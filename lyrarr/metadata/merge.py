# coding=utf-8

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

logger = logging.getLogger(__name__)

# LRC timestamp pattern for stripping
_LRC_TS = re.compile(r'\[\d{1,2}:\d{2}[.:]\d{2,3}\]\s*')

# LRC metadata tag pattern
_LRC_META = re.compile(r'^\[(?:ar|ti|al|au|length|by|offset|re|ve|id|la):.*\]$', re.IGNORECASE)

# Confidence boost per agreeing provider
AGREEMENT_BOOST = 0.05


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

    # Step 3: Mark agreement count and boost scores
    seen_hashes = set()
    enhanced = []

    for r in results:
        h = r['_content_hash']
        if h and h in hash_groups:
            group = hash_groups[h]
            providers_agree = len(set(g.get('_provider', '') for g in group))
            r['providers_agree'] = providers_agree

            # Boost score if multiple providers agree
            if providers_agree > 1:
                original_score = r.get('score', 0)
                boost = AGREEMENT_BOOST * (providers_agree - 1)
                r['score'] = min(1.0, original_score + boost)

            # Only keep the best-scoring entry per hash (de-duplicate)
            if h not in seen_hashes:
                seen_hashes.add(h)
                # Pick the best entry from the group
                best = max(group, key=lambda x: (x.get('score', 0), 1 if x.get('synced_lyrics') else 0))
                best['providers_agree'] = providers_agree
                if providers_agree > 1:
                    agreeing_names = sorted(set(g.get('_provider', '?') for g in group))
                    best['agreement_providers'] = agreeing_names
                enhanced.append(best)
            # else: skip duplicates
        else:
            r['providers_agree'] = 1
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

    # Clean up internal keys before returning
    for r in enhanced:
        r.pop('_synced_hash', None)
        r.pop('_plain_hash', None)
        r.pop('_content_hash', None)
        r.pop('_provider', None)

    return enhanced


def _content_hash(text):
    """Hash lyrics content after normalizing for comparison.

    Strips LRC timestamps, metadata tags, normalizes whitespace,
    and lowercases text to produce a content-only hash.
    """
    if not text:
        return None

    lines = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip LRC metadata tags
        if _LRC_META.match(stripped):
            continue
        # Remove LRC timestamps
        cleaned = _LRC_TS.sub('', stripped).strip().lower()
        if cleaned:
            lines.append(cleaned)

    if not lines:
        return None

    content = '\n'.join(lines)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]


def _lyrics_are_equivalent(a, b, threshold=0.9):
    """Check if two lyrics texts are essentially the same content.

    Uses SequenceMatcher after normalizing both texts.
    """
    if not a or not b:
        return False

    # Normalize both
    norm_a = _normalize_for_comparison(a)
    norm_b = _normalize_for_comparison(b)

    if not norm_a or not norm_b:
        return False

    return SequenceMatcher(None, norm_a, norm_b).ratio() >= threshold


def _normalize_for_comparison(text):
    """Normalize lyrics text for comparison (remove timestamps, lowercase, normalize whitespace)."""
    lines = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _LRC_META.match(stripped):
            continue
        cleaned = _LRC_TS.sub('', stripped).strip().lower()
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
                    '_synced_hash': synced.get('_synced_hash'),
                    '_plain_hash': plain.get('_plain_hash'),
                }
                composites.append(composite)

    return composites
