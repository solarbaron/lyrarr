
"""
Cross-provider lyrics result merging and duplicate detection.

After collecting results from all providers, this module:
  1. Detects duplicate content across providers (content hashing)
  2. Creates composite results (e.g. synced from LRCLIB + plain from Genius)
  3. Boosts confidence for results with multi-provider agreement
"""

import hashlib
import logging
import re
from collections import defaultdict
from difflib import SequenceMatcher

from lyrarr.metadata.lrc_repair import LRC_ENHANCED_TIMESTAMP, LRC_META_TAG, LRC_TIMESTAMP

logger = logging.getLogger(__name__)

# LRC timestamps / metadata tags: the shared definitions ([mm:ss] without a
# fraction and unknown [tool:..] tags included), so a synced and a plain copy
# hash the same everywhere. Enhanced word timings (<mm:ss.xx>) are stripped too.
_LRC_TS = LRC_TIMESTAMP
_LRC_META = LRC_META_TAG
_LRC_WORD_TS = LRC_ENHANCED_TIMESTAMP

# The pre-2.0 definitions: blacklist rows written by older versions hold
# hashes computed with these, so legacy_content_hash() keeps them matchable.
_LEGACY_LRC_TS = re.compile(r'\[\d{1,2}:\d{2}[.:]\d{2,3}\]\s*')
_LEGACY_LRC_META = re.compile(r'^\[(?:ar|ti|al|au|length|by|offset|re|ve|id|la):.*\]$', re.IGNORECASE)

# Keys copied into a merged result's `merged_members` (no lyrics, no nesting,
# so search results stay JSON-serialisable).
_MEMBER_KEYS = ('_provider', 'provider', 'score', 'match_details', 'duration', 'strategy',
                'source_id', 'album_name', 'track_name', 'artist_name')

# Confidence boost per agreeing provider
AGREEMENT_BOOST = 0.05


def strip_lrc_timestamps(text):
    """Plain-text rendering of synced lyrics: drop timestamps and LRC metadata tags.

    Used when a plain copy is needed but only synced content exists (e.g.
    publishing both forms to LRCLIB).
    """
    lines = []
    for line in (text or '').lstrip('\ufeff').splitlines():
        if _LRC_META.match(line.strip()):
            continue
        lines.append(_strip_line_timing(line).rstrip())
    return '\n'.join(lines).strip()


def _strip_line_timing(line):
    return _LRC_WORD_TS.sub('', _LRC_TS.sub('', line))


def _member(r):
    """Lightweight copy of a result for `merged_members` (score before any agreement boost)."""
    member = {k: r.get(k) for k in _MEMBER_KEYS if k in r}
    if '_raw_score' in r:
        member['score'] = r['_raw_score']
    return member


def result_members(result):
    """Every provider result a (merged) result stands for.

    merge_provider_results() records them in `merged_members`; an unmerged
    result stands for itself. Callers that count per-provider evidence (e.g.
    instrumental votes) check each member on its own.
    """
    members = result.get('merged_members')
    if members:
        return members
    return [_member(result)]


def merge_provider_results(results, selection_mode='best_score'):
    """Merge and de-duplicate results from multiple providers.

    This function:
    1. Hashes each result's lyrics content (normalized)
    2. Groups results by content hash to find duplicates
    3. Boosts scores for results with multi-provider agreement
    4. Creates composite results when one provider has synced and another
       has matching plain lyrics
    5. Returns the enhanced result set sorted by composite quality

    Args:
        results: List of result dicts from providers (each with _provider key)
        selection_mode: 'best_score', 'prefer_synced', or 'prefer_plain'

    Returns:
        Enhanced and de-duplicated results list
    """
    if not results or len(results) <= 1:
        return results

    # Step 1: Compute content hashes for all results
    for r in results:
        # Normalize provider key: use _provider, fall back to provider
        if '_provider' not in r and 'provider' in r:
            r['_provider'] = r['provider']

        r['_raw_score'] = r.get('score')
        synced = r.get('synced_lyrics', '') or ''
        plain = r.get('plain_lyrics', '') or ''

        r['_synced_hash'] = _content_hash(synced) if synced.strip() else None
        r['_plain_hash'] = _content_hash(plain) if plain.strip() else None
        r['_content_hash'] = r['_synced_hash'] or r['_plain_hash']

    # Step 2: Group by content hash to find duplicates
    hash_groups = defaultdict(list)
    for r in results:
        if r['_content_hash']:
            hash_groups[r['_content_hash']].append(r)

    # Step 3: Mark agreement count and boost scores. Boost every member of a
    # group *before* picking its representative, so the pick compares like
    # with like (boosting one member at a time favoured whichever came first —
    # a plain copy could beat the synced copy of the same lyrics).
    for group in hash_groups.values():
        providers_agree = len(set(g.get('_provider', '') for g in group))
        for g in group:
            g['providers_agree'] = providers_agree
            if providers_agree > 1:
                boost = AGREEMENT_BOOST * (providers_agree - 1)
                g['score'] = min(1.0, (g.get('score', 0) or 0) + boost)

    seen_hashes = set()
    enhanced = []

    for r in results:
        h = r['_content_hash']
        if h and h in hash_groups:
            # Only keep the best-scoring entry per hash (de-duplicate)
            if h not in seen_hashes:
                seen_hashes.add(h)
                group = hash_groups[h]
                best = max(group, key=lambda x: (x.get('score', 0), 1 if x.get('synced_lyrics') else 0))
                if best['providers_agree'] > 1:
                    agreeing_names = sorted(set(g.get('_provider', '?') for g in group))
                    best['agreement_providers'] = agreeing_names
                best['merged_members'] = [_member(g) for g in group]
                enhanced.append(best)
            # else: skip duplicates
        else:
            r['providers_agree'] = 1
            r['merged_members'] = [_member(r)]
            enhanced.append(r)

    # Step 4: Create composite results (synced from one + plain from another)
    composites = _create_composites(results)
    for comp in composites:
        # Only add if not duplicating an existing result
        already = any(
            r.get('_synced_hash') == comp.get('_synced_hash') and
            r.get('_plain_hash') == comp.get('_plain_hash')
            for r in enhanced
        )
        if not already:
            enhanced.append(comp)

    # Clean up internal hash keys before returning. '_provider' stays: the
    # download worker attributes the saved lyrics (history, logs) to it.
    for r in list(enhanced) + list(results):
        r.pop('_synced_hash', None)
        r.pop('_plain_hash', None)
        r.pop('_content_hash', None)
        r.pop('_raw_score', None)

    return enhanced


def _content_hash(text):
    """Hash lyrics content after normalizing for comparison.

    Strips LRC timestamps, metadata tags, normalizes whitespace,
    and lowercases text to produce a content-only hash.
    """
    if not text:
        return None

    return _hash_lines(text, _LRC_TS, _LRC_META, word_ts=True)


def legacy_content_hash(text):
    """The content hash as computed before 2.0 (narrower timestamp/meta
    patterns, no enhanced-tag stripping). Blacklist rows written by older
    versions hold these, so blacklist matching checks both."""
    if not text:
        return None
    return _hash_lines(text, _LEGACY_LRC_TS, _LEGACY_LRC_META, word_ts=False)


def _hash_lines(text, ts_re, meta_re, *, word_ts):
    lines = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip LRC metadata tags
        if meta_re.match(stripped):
            continue
        # Remove LRC timestamps
        cleaned = ts_re.sub('', stripped)
        if word_ts:
            cleaned = _LRC_WORD_TS.sub('', cleaned)
        cleaned = cleaned.strip().lower()
        if cleaned:
            lines.append(cleaned)

    if not lines:
        return None

    content = '\n'.join(lines)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]


def lyrics_similarity(a, b):
    """Similarity ratio (0..1) of two lyrics texts after normalisation
    (timestamps, tags and case ignored). 0.0 when either side is empty."""
    if not a or not b:
        return 0.0
    norm_a = _normalize_for_comparison(a)
    norm_b = _normalize_for_comparison(b)
    if not norm_a or not norm_b:
        return 0.0
    return SequenceMatcher(None, norm_a, norm_b).ratio()


def _lyrics_are_equivalent(a, b, threshold=None):
    """How equivalent two lyrics texts are.

    threshold None → the similarity ratio (0..1, see lyrics_similarity);
    a threshold → bool (ratio >= threshold).
    """
    ratio = lyrics_similarity(a, b)
    if threshold is None:
        return ratio
    return ratio >= threshold


def _normalize_for_comparison(text):
    """Normalize lyrics text for comparison (remove timestamps, lowercase, normalize whitespace)."""
    lines = []
    for line in text.lstrip('\ufeff').strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _LRC_META.match(stripped):
            continue
        cleaned = _strip_line_timing(stripped).strip().lower()
        if cleaned:
            lines.append(cleaned)
    return '\n'.join(lines)


def _create_composites(results):
    """Create composite results by combining synced and plain from different providers.

    If one provider has synced lyrics and another has plain lyrics for the same track
    (determined by high content similarity), create a composite with both.
    """
    composites = []

    synced_results = [r for r in results if r.get('synced_lyrics') and not r.get('plain_lyrics')]
    plain_results = [r for r in results if r.get('plain_lyrics') and not r.get('synced_lyrics')]

    for synced in synced_results:
        for plain in plain_results:
            # Check if they're from different providers
            if synced.get('_provider') == plain.get('_provider'):
                continue

            # Check if the content matches (synced text without timestamps ≈ plain text)
            synced_text = _normalize_for_comparison(synced.get('synced_lyrics', ''))
            plain_text = _normalize_for_comparison(plain.get('plain_lyrics', ''))

            if _lyrics_are_equivalent(synced_text, plain_text, threshold=0.8):
                # Create composite
                composite_score = max(synced.get('score', 0), plain.get('score', 0))
                composite = {
                    'synced_lyrics': synced.get('synced_lyrics'),
                    'plain_lyrics': plain.get('plain_lyrics'),
                    'synced_preview': synced.get('synced_preview', (synced.get('synced_lyrics') or '')[:300]),
                    'plain_preview': plain.get('plain_preview', (plain.get('plain_lyrics') or '')[:300]),
                    'provider': f"{synced.get('_provider')}+{plain.get('_provider')}",
                    '_provider': f"{synced.get('_provider')}+{plain.get('_provider')}",
                    'score': min(1.0, composite_score + AGREEMENT_BOOST),
                    'track_name': synced.get('track_name') or plain.get('track_name'),
                    'artist_name': synced.get('artist_name') or plain.get('artist_name'),
                    'duration': synced.get('duration') or plain.get('duration'),
                    'match_details': synced.get('match_details') or plain.get('match_details'),
                    'providers_agree': 2,
                    'is_composite': True,
                    'strategy': synced.get('strategy') or plain.get('strategy'),
                    'source_id': synced.get('source_id') or plain.get('source_id'),
                    'album_name': synced.get('album_name') or plain.get('album_name'),
                    'merged_members': [_member(synced), _member(plain)],
                    '_synced_hash': synced.get('_synced_hash'),
                    '_plain_hash': plain.get('_plain_hash'),
                }
                composites.append(composite)

    return composites
