"""Pure text helpers for lyrics content and track titles.

No database, no I/O: importable anywhere (providers, worker, sync, audit,
tests). Shared vocabulary for the instrumental policy (PLAN §1.2 / §2):

* Title tiers: ``instrumental_title_tier()`` says whether a title *itself*
  declares an instrumental version. ``'strong'`` is the only tier that may
  classify a track on its own; ``'weak'`` is one non-anchor vote; ``None``
  means the title says nothing. A vocal track must never be STRONG, so the
  rules err towards ``None``/``'weak'`` (``The Instrumental``,
  ``Song (Instrumental Intro)``, ``Rap Over My Instrumental`` ...).
* Content classes: ``classify_lyrics_text()`` separates real lyric lines from
  explicit instrumental markers (``[au: instrumental]``), provider
  placeholders ("lyrics not available"), credit-only blocks and empty files.
"""

import re
import unicodedata
from typing import Literal

# ---------------------------------------------------------------------------
# Title tiers
# ---------------------------------------------------------------------------

# Latin alternatives need word boundaries ("Piano Vocals" must not match
# "no vocals"); the CJK ones are matched as-is.
QUALIFIER = r"""
    (?:
        \b(?:
            instrumentals?\b
          | inst\.(?=[\s,;/&)\]）】]|$)
          | karaoke\b
          | off[\s-]?vocals?\b
          | no(?:\s+lead)?\s+vocals?\b
          | without\s+vocals?\b
          | backing[\s-]?tracks?\b
          | minus[\s-]?one\b
        )
      | 伴奏 | 纯音乐版 | オフボーカル | インスト(?:ゥルメンタル)?
    )
"""
_QUALIFIER_RE = re.compile(QUALIFIER, re.IGNORECASE | re.VERBOSE)

POST_WORDS = frozenset({
    'version', 'ver', 'ver.', 'mix', 'edit', 'take', 'demo', 'studio', 'track', 'tracks',
    'remaster', 'remastered', 'mono', 'stereo', 'only', 'cut', 'dub', 'tv', 'size',
})
# Years (1996), '96, bare numbers, v2.
_POST_TOKEN_RE = re.compile(r"(?:\d+|'\d{2}|v\d+)", re.IGNORECASE)

DEMOTE_WORDS = frozenset({
    'vocal', 'vocals', 'vox', 'intro', 'outro', 'break', 'bridge', 'section', 'solo',
    'interlude', 'part', 'pt', 'pt.', 'reprise', 'verse', 'chorus', 'acapella', 'hook', 'skit',
})
_DEMOTE_MULTI_RE = re.compile(r'\ba\s+cap+el+a\b', re.IGNORECASE)

# Words allowed before the qualifier in a DASH segment. The bracket rule leaves
# the text before the qualifier free ("(Ramirez mix instrumental)"), but an
# unbracketed dash segment is often a phrase ("- Rap Over My Instrumental"), so
# it is only STRONG when the leading words are plain version descriptors.
_DASH_PRE_WORDS = POST_WORDS | frozenset({
    'live', 'album', 'original', 'official', 'extended', 'radio', 'single', 'full', 'short',
    'long', 'alternate', 'alt', 'acoustic', 'piano', 'orchestral', 'string', 'strings',
    'guitar', 'synth', 'clean', 'explicit', 'new', 'old', 'alternative', 'bonus',
})

_TOKEN_SPLIT_RE = re.compile(r"[\s,;/&\-–—:+\"“”'’]+")

# (...) / [...] groups (innermost), plus full-width （…） and 【…】.
_BRACKET_RE = re.compile(r'\(([^()]*)\)|\[([^\[\]]*)\]|（([^（）]*)）|【([^【】]*)】')

# A dash preceded by whitespace; only the LAST segment is examined.
_DASH_RE = re.compile(r'\s+[-–—]+\s*')
_DASH_ARTICLES = frozenset({'the', 'my', 'your', 'a', 'an', 'our', 'his', 'her', 'their'})

_TERMINAL_RE = re.compile(r'\b(?:instrumental|karaoke)\s+(?:version|mix|edit|take)\s*$', re.IGNORECASE)
_WEAK_WHOLE_RE = re.compile(
    r'^(?:untitled\s+)?instrumentals?(?:\s+(?:v?\d+|[ivx]+|#\d+|no\.?\s*\d+))?$', re.IGNORECASE)

_KARAOKE_RE = re.compile(r'karaoke', re.IGNORECASE)
_OFFVOCAL_RE = re.compile(r'off[\s-]?vocal|no(?:\s+lead)?\s+vocal|without\s+vocal|オフボーカル', re.IGNORECASE)

_TIER_RANK = {None: 0, 'weak': 1, 'strong': 2}

# Bracket groups that are artist credits, never version tags.
_CREDIT_BRACKET_RE = re.compile(
    r'\s*(?:feat\.?|ft\.?|featuring|prod\.?|produced\s+by)(?=\s|$)', re.IGNORECASE)
# A dash segment that is only a track number ("02", "#02", "Track 02").
_TRACKNO_SEGMENT_RE = re.compile(r'(?:track\s*)?#?\d{1,3}', re.IGNORECASE)

Tier = Literal['strong', 'weak']


def _tokens(text):
    return [t for t in _TOKEN_SPLIT_RE.split(text.casefold()) if t]


def _is_post_token(tok):
    return tok in POST_WORDS or bool(_POST_TOKEN_RE.fullmatch(tok))


def _has_demote(text_outside):
    if _DEMOTE_MULTI_RE.search(text_outside):
        return True
    return any(t in DEMOTE_WORDS or t.rstrip('.') in DEMOTE_WORDS for t in _tokens(text_outside))


def _qualifier_kind(q_text):
    if _KARAOKE_RE.search(q_text):
        return 'karaoke'
    if _OFFVOCAL_RE.search(q_text):
        return 'offvocal'
    return 'instrumental'


def _segment_tier(seg, *, dash=False):
    """Tier of one bracket content / dash segment, plus the qualifier kind."""
    m = _QUALIFIER_RE.search(seg)
    if not m:
        return None, None
    kind = _qualifier_kind(m.group(0))
    before, after = seg[:m.start()], seg[m.end():]
    if _has_demote(before + ' ' + after):
        return 'weak', kind
    if not all(_is_post_token(t) for t in _tokens(after)):
        return 'weak', kind
    if dash and not all(t in _DASH_PRE_WORDS or _is_post_token(t) for t in _tokens(before)):
        # "Artist - Rap Over My Instrumental": a phrase, not a version tag.
        return None, None
    return 'strong', kind


def _normalise_title(title):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', title or '')).strip()


def _analyse(title):
    """Return (tier, kind, strip_span) for the best qualifier in the title.

    strip_span is the (start, end) character range to cut for
    strip_instrumental_qualifier (only meaningful for a STRONG result).
    """
    t = _normalise_title(title)
    if not t:
        return None, None, None
    best = (None, None, None)

    def consider(tier, kind, span):
        nonlocal best
        if tier and _TIER_RANK[tier] > _TIER_RANK[best[0]]:
            best = (tier, kind, span)

    # 1. Bracket rule. Credit groups ("feat. X", "prod. by X") name people,
    # not versions: an act called "Instrumental" must not make a title STRONG.
    for m in _BRACKET_RE.finditer(t):
        content = next(g for g in m.groups() if g is not None)
        if _CREDIT_BRACKET_RE.match(content):
            continue
        tier, kind = _segment_tier(content)
        consider(tier, kind, m.span())

    # 2. Dash rule, last segment only.
    dashes = list(_DASH_RE.finditer(t))
    whole = t
    if dashes:
        last = dashes[-1]
        seg = t[last.end():]
        prev = t[dashes[-2].end():last.start()] if len(dashes) > 1 else t[:last.start()]
        words = _tokens(seg)
        if _TRACKNO_SEGMENT_RE.fullmatch(prev.strip()):
            # Lidarr duplicate-row form "Artist - Album - 02 - Title": after a
            # bare track number the last segment IS the title, not a version
            # tag on a song before it — judge it as a whole title (rule 4).
            whole = seg.strip()
        elif words and words[0] not in _DASH_ARTICLES:
            tier, kind = _segment_tier(seg, dash=True)
            consider(tier, kind, (last.start(), len(t)))

    # 3. Terminal unbracketed "Instrumental Version".
    m = _TERMINAL_RE.search(t)
    if m:
        consider('strong', _qualifier_kind(m.group(0)), m.span())

    # 4. The whole title is "Instrumental" (+ a number).
    if _WEAK_WHOLE_RE.match(whole.casefold()):
        consider('weak', 'instrumental', None)

    return best


def instrumental_title_tier(title: str | None) -> Tier | None:
    """'strong' / 'weak' / None — see PLAN §1.2 for the rules and examples."""
    return _analyse(title)[0]


def instrumental_qualifier_kind(title) -> Literal['instrumental', 'karaoke', 'offvocal'] | None:
    """Kind of the qualifier behind the title's STRONG/WEAK tier (None if no tier)."""
    tier, kind, _span = _analyse(title)
    return kind if tier else None


def strip_instrumental_qualifier(title) -> str:
    """Remove the STRONG bracket/dash segment (query title for override 'never').

    Titles that are not STRONG are returned unchanged (whitespace-normalised).
    """
    t = _normalise_title(title)
    tier, _kind, span = _analyse(t)
    if tier != 'strong' or not span:
        return t
    out = (t[:span[0]] + ' ' + t[span[1]:]).strip()
    out = re.sub(r'\s+', ' ', out)
    out = re.sub(r'[\s\-–—:,;]+$', '', out).strip()
    return out or t


# The pre-2.0 broad title regex (validation.py), kept for audit diffing only:
# rows it flagged that the new tiers no longer call STRONG are legacy mistakes.
_LEGACY_TITLE_RE = re.compile(
    r'[\(\[]\s*(?:instrumental|karaoke|off[\s-]?vocals?|no[\s-]?vocals?|'
    r'backing[\s-]?track)\b[^\)\]]*[\)\]]'
    r'|\b(?:instrumental|karaoke)\s+(?:version|mix|edit|take)\b'
    r'|[-–—]\s*instrumental\s*$'
    r'|\binstrumental\s*$',
    re.IGNORECASE
)


def matches_legacy_title_rule(title) -> bool:
    """Whether the OLD (pre-2.0) instrumental title regex matches."""
    return bool(title) and bool(_LEGACY_TITLE_RE.search(title))


# ---------------------------------------------------------------------------
# Generic / context-dependent titles (§2.7)
# ---------------------------------------------------------------------------

_GENERIC_WORDS = (
    r'(?:intro|outro|interlude|interludio|intermission|skit|prelude|postlude|prologue|epilogue|'
    r'overture|reprise|untitled|segue|transition|interstitial|hidden\s+track|bonus\s+track|'
    r'track|opening|closing|theme)'
)
_GENERIC_RE = re.compile(
    r'^' + _GENERIC_WORDS
    + r'(?:\s*(?:/|&|\+|and)\s*' + _GENERIC_WORDS + r')?'   # "Intro / Outro"
    + r'(?:\s+skit)?'                                         # "Intro Skit"
    + r'(?:[\s#.\-]*(?:\d+|[ivx]+))?$',
    re.IGNORECASE,
)
# "04 - Intro", "04. Intro", "04 Intro".
_TRACKNO_PREFIX_RE = re.compile(r'^\s*\d{1,3}(?:\s*[-.)_]\s*|\s+)')
# A trailing " - Remastered 2011" / " - Live" / " - 2009 Remaster" segment.
_DASH_VERSION_WORDS = POST_WORDS | _DASH_PRE_WORDS | frozenset({
    'remastered', 'remaster', 'remix', 'version', 'deluxe', 'edition', 'anniversary', 'reissue',
    'session', 'sessions', 'recording', 'take', 'rehearsal',
})
_FEAT_RE = re.compile(r'\b(?:feat\.?|ft\.?|featuring|with)\b', re.IGNORECASE)

_VERSION_TAG_WORDS = {
    'intro': 'intro', 'outro': 'outro', 'interlude': 'interlude', 'skit': 'skit',
    'reprise': 'reprise', 'acapella': 'acapella', 'acappella': 'acapella',
}
_ACAPELLA_RE = re.compile(r'\ba\s*cap+el+a\b', re.IGNORECASE)
_CONTEXT_TAGS = frozenset({'intro', 'outro', 'interlude', 'skit', 'reprise'})


def _strip_noise_brackets(title):
    """Drop bracket groups except featured-artist credits, which make a
    title specific ("Outro (feat. X)" is not generic)."""
    def repl(m):
        content = next(g for g in m.groups() if g is not None)
        return m.group(0) if _FEAT_RE.search(content) else ' '
    return _BRACKET_RE.sub(repl, title)


def _strip_dash_version_suffix(t):
    """Drop a last dash segment made only of version words ("- Remastered 2011")."""
    dashes = list(_DASH_RE.finditer(t))
    if not dashes:
        return t
    last = dashes[-1]
    words = [w.rstrip('.') for w in _tokens(t[last.end():])]
    if words and all(w in _DASH_VERSION_WORDS or _is_post_token(w) for w in words):
        return t[:last.start()]
    return t


def _generic_core(title):
    t = _normalise_title(title)
    t = _TRACKNO_PREFIX_RE.sub('', t)
    t = _strip_noise_brackets(t)
    t = _strip_dash_version_suffix(re.sub(r'\s+', ' ', t).strip())
    return re.sub(r'\s+', ' ', t).strip().strip('.-–—:,;!?').strip()


def is_generic_title(title) -> bool:
    """Intro / Outro / Skit #3 / Untitled 03 / '04 - Intro' / digits only (§2.7)."""
    core = _generic_core(title)
    if not core or core.isdigit():
        return True
    return bool(_GENERIC_RE.match(core))


def version_tags(raw_title) -> frozenset[str]:
    """Content-changing tags carried by the RAW title.

    A tag counts when it appears inside a bracket group, in the last
    ` - ` segment, or as the whole (generic) title — not as part of a phrase
    ("Intro to Love" has none). Tags: intro, outro, interlude, skit, reprise,
    instrumental, karaoke, acapella.
    """
    t = _normalise_title(raw_title)
    if not t:
        return frozenset()
    segments = [next(g for g in m.groups() if g is not None) for m in _BRACKET_RE.finditer(t)]
    dashes = list(_DASH_RE.finditer(t))
    if dashes:
        segments.append(t[dashes[-1].end():])
    core = _generic_core(t)
    bare = re.sub(r'\s+', ' ', _BRACKET_RE.sub(' ', core)).strip()  # "Outro (feat. X)" → "Outro"
    for c in (core, bare):
        if _GENERIC_RE.match(c) or _WEAK_WHOLE_RE.match(c.casefold()):
            segments.append(c)

    tags = set()
    for seg in segments:
        for tok in _tokens(seg):
            tag = _VERSION_TAG_WORDS.get(tok.rstrip('.'))
            if tag:
                tags.add(tag)
        if _ACAPELLA_RE.search(seg):
            tags.add('acapella')
        q = _QUALIFIER_RE.search(seg)
        if q:
            tags.add('karaoke' if _qualifier_kind(q.group(0)) == 'karaoke' else 'instrumental')
    return frozenset(tags)


def needs_album_context(title) -> bool:
    """Titles whose lyrics only make sense per album (§2.7)."""
    return is_generic_title(title) or bool(version_tags(title) & _CONTEXT_TAGS)


def is_bare_instrumental_title(title) -> bool:
    """The whole title is "Instrumental" (+ a number) — the WEAK rule-4 form."""
    core = _generic_core(title)
    return bool(core) and bool(_WEAK_WHOLE_RE.match(core.casefold()))


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------

_TS = r'\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]'
_LRC_TS_RE = re.compile(_TS + r'\s*')
_ENHANCED_TS_RE = re.compile(r'<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>\s*')
_LEADING_TS_RE = re.compile(r'^\s*(?:' + _TS + r'\s*)+')
_BRACKET_LINE_RE = re.compile(r'^\s*\[[^\]]*\]\s*$')  # [ar: x], [Verse 1], [Chorus: X]

_CREDIT_RE = re.compile(
    r'^\s*(?:\[[\d:.]+\])?\s*(?:作词|作曲|编曲|制作人|监制|混音|母带|录音|和声|吉他|贝斯|鼓|键盘|弦乐|出品|发行|'
    r'OP|SP|音频助理|主人声|词|曲|Lyricist|Composer|Arranger|Producer|'
    # More NetEase roles (WS-B's netease.strip_netease_credits superset, explicit words only).
    r'指挥家|指挥|混音师|混音助理|混音工程|和声编写|和声编排|弦乐编写|录音工程|录音助理|录音师|母带工程师|'
    r'合成器|附加编程|编程|中提琴|大提琴|小提琴|长号|大号|小号|钢琴|人声|配唱|统筹|策划|企划|制作)'
    r'\s*[:：]\s*\S',
    re.IGNORECASE,
)

_PLACEHOLDER_PHRASES = (
    r'lyrics?\s+(?:not\s+(?:available|found)|unavailable|coming\s+soon)',
    r'no\s+lyrics\s+(?:found|available)',
    r'lyrics\s+will\s+be\s+available\s+soon',
    r'lyrics\s+for\s+this\s+song\s+have\s+yet\s+to\s+be\s+(?:released|transcribed)',
    r'please\s+check\s+back\s+once\s+the\s+song\s+has\s+been\s+released',
    r"we\s+don['’]?t\s+have\s+(?:the\s+)?lyrics(?:\s+(?:for|to)\s+this\s+song)?(?:\s+yet)?",
    r'unfortunately,?\s+we\s+are\s+not\s+licensed\s+to\s+display\s+(?:the\s+)?(?:full\s+)?lyrics'
    r'(?:\s+for\s+this\s+song)?(?:\s+at\s+the\s+moment)?',
    r'纯音乐[，,]?\s*请欣赏',
    r'此歌曲为没有填词的纯音乐(?:[，,]?\s*请您?欣赏)?',
)
_PLACEHOLDER_ONE = r'(?:' + '|'.join(_PLACEHOLDER_PHRASES) + r')'
_PLACEHOLDER_RE = re.compile(
    r'^\s*' + _PLACEHOLDER_ONE + r'(?:\s*[.!。！]*\s*' + _PLACEHOLDER_ONE + r')*\s*[.!。！]*\s*$',
    re.IGNORECASE,
)

_MARKER_RE = re.compile(
    r'^\s*(?:(?:' + _TS + r'|<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>)\s*)*'
    r'(?:'
    r'[\[(♪♫]?\s*instrumentals?\s*[\])♪♫]?'
    r'|\[\s*au\s*:\s*instrumental\s*\]'
    r'|纯音乐[，,]\s*请欣赏'
    r'|此歌曲为没有填词的纯音乐[，,]\s*请您欣赏'
    r')\s*[.!。！]?\s*$',
    re.IGNORECASE,
)

_URL_ONLY_RE = re.compile(r'^\s*(?:https?://|www\.)\S+\s*$', re.IGNORECASE)
_JUNK_PREFIX_RE = re.compile(r'^\s*(?:lyrics\s+by\b|downloaded\s+from\b)', re.IGNORECASE)


def _strip_ts(line):
    line = _LRC_TS_RE.sub('', line)
    line = _ENHANCED_TS_RE.sub('', line)
    return line.strip()


def is_credit_line(line: str) -> bool:
    """NetEase-style credit lines (作词 : X, Composer: Y)."""
    return bool(line) and bool(_CREDIT_RE.match(_LEADING_TS_RE.sub('', line)))


def is_placeholder_line(line) -> bool:
    """Provider "no lyrics here" placeholders (not instrumental evidence)."""
    if not line:
        return False
    return bool(_PLACEHOLDER_RE.match(_strip_ts(line)))


def is_explicit_instrumental_marker_line(line) -> bool:
    """STRICT explicit instrumental markers: 'Instrumental', '[Instrumental]',
    '♪ Instrumental ♪', '[au: instrumental]', '纯音乐，请欣赏' (timestamps
    allowed). Never Intro/Outro/Interlude/Skit headers."""
    return bool(line) and bool(_MARKER_RE.match(line.lstrip('\ufeff')))


def is_junk_line(line) -> bool:
    """URL-only lines, 'lyrics by …' / 'downloaded from …' / 'www.' lines, and
    lines without a single letter or digit."""
    if not line:
        return False
    s = _strip_ts(line)
    if not s:
        return False
    if _URL_ONLY_RE.match(s) or _JUNK_PREFIX_RE.match(s) or 'www.' in s.casefold():
        return True
    return not any(ch.isalnum() for ch in s)


def _lines(text):
    if not text:
        return []
    text = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    return text.split('\n')


def strip_credit_blocks(text: str) -> str:
    """Remove the LEADING and TRAILING contiguous credit blocks only.

    A block may interleave blank/meta/timestamp-only lines with the credits;
    those are kept, as is everything between the blocks (timestamps and order
    untouched).
    """
    if not text:
        return text or ''
    lines = _lines(text)

    def neutral(line):
        s = _strip_ts(line)
        return not s or bool(_BRACKET_LINE_RE.match(s))

    def block_end(seq):
        """Index after the leading run made of credits + neutral lines that
        contains at least one credit line; 0 when there is none."""
        end = 0
        for i, line in enumerate(seq):
            if is_credit_line(line):
                end = i + 1
            elif not neutral(line):
                break
        return end

    head = block_end(lines)
    tail = block_end(list(reversed(lines[head:])))
    keep = []
    n = len(lines)
    for i, line in enumerate(lines):
        in_head = i < head
        in_tail = i >= n - tail
        if (in_head or in_tail) and is_credit_line(line):
            continue
        keep.append(line)
    out = '\n'.join(keep)
    return out.strip('\n') if out.strip() else ''


def real_lyric_lines(text) -> list[str]:
    """Lines that are actual lyrics: timestamps, meta tags, section headers,
    credits, placeholders, explicit markers and junk removed."""
    out = []
    for raw in _lines(text):
        if not raw.strip():
            continue
        if is_explicit_instrumental_marker_line(raw):
            continue
        s = _strip_ts(raw)
        if not s:
            continue
        if _BRACKET_LINE_RE.match(s):
            continue
        if is_credit_line(s) or is_placeholder_line(s) or is_junk_line(s):
            continue
        out.append(s)
    return out


ContentClass = Literal['lyrics', 'marker', 'placeholder', 'credits_only', 'empty']


def classify_lyrics_text(text) -> ContentClass:
    """'lyrics' iff any real lyric line; else 'marker' / 'placeholder' /
    'credits_only' / 'empty' (first that applies)."""
    if not text or not text.strip():
        return 'empty'
    if real_lyric_lines(text):
        return 'lyrics'
    lines = [ln for ln in _lines(text) if ln.strip()]
    if any(is_explicit_instrumental_marker_line(ln) for ln in lines):
        return 'marker'
    if any(is_placeholder_line(ln) for ln in lines):
        return 'placeholder'
    if any(is_credit_line(ln) for ln in lines):
        return 'credits_only'
    return 'empty'


def is_substantive_lyrics(text, min_distinct=4) -> bool:
    """Real lyrics with at least `min_distinct` distinct lines (casefolded)."""
    lines = real_lyric_lines(text)
    if not lines:
        return False
    return len({ln.casefold() for ln in lines}) >= min_distinct


def normalize_text_for_write(text) -> str:
    """Strip a leading BOM, normalise newlines to LF, end with exactly one LF.

    Returns '' for empty/whitespace-only input.
    """
    if not text:
        return ''
    t = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    t = t.rstrip()
    t = t.lstrip('\n')
    if not t.strip():
        return ''
    return t + '\n'
