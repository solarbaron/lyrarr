"""Table-driven tests for the pure lyrics text helpers (lyrarr/metadata/lyrics_text.py).

Covers the PLAN §1.2 title-tier table (strong / weak / None), the §7.2 title
negatives and positives (incl. the `inst.` forms), generic titles, version
tags, credit / placeholder / marker / junk lines, content classification and
write normalisation. All lyrics text here is synthetic.
"""

import pytest

from lyrarr.metadata.lyrics_text import (
    classify_lyrics_text,
    instrumental_qualifier_kind,
    instrumental_title_tier,
    is_credit_line,
    is_explicit_instrumental_marker_line,
    is_generic_title,
    is_junk_line,
    is_placeholder_line,
    is_substantive_lyrics,
    matches_legacy_title_rule,
    needs_album_context,
    normalize_text_for_write,
    real_lyric_lines,
    strip_credit_blocks,
    strip_instrumental_qualifier,
    version_tags,
)

STRONG = [
    'Lose Yourself (Instrumental)',
    'Song (Off Vocal)',
    'Song (Inst.)',
    'Song [inst.]',
    'Song (Inst. Version)',
    'Song [Instrumental Version]',
    'Song (Karaoke Version)',
    'Bite the Pain (1996 demo, instrumental)',
    'Defensive Personalities (studio Instrumentals)',
    '(Basic instrumental studio Tracks)',
    'So Far to Go (7" edit instrumental)',
    'A Screw (Holy Money) (mix, no lead vocal)',
    'Showcase of the Immortals (Ramirez mix instrumental)',
    'Song - Instrumental',
    'Song – Instrumental Version',
    'Song (TV Size Instrumental)',
    'Song (伴奏)',
    'Song (Instrumental) [Remastered 2009]',
    'Song (Without Vocals)',
    'Song Instrumental Version',
]

WEAK = [
    'Song (Instrumental Intro)',
    'Song (with Instrumental Outro)',
    'Song (Vocal & Instrumental Mix)',
    'Song (Instrumental Break)',
    'Song (Instrumental Reprise)',
    'Song - Instrumental Bridge',
    'Instrumental',
    'instrumental',
    'Instrumental V1',
    'Instrumental 2',
    'Untitled Instrumental',
]

NONE = [
    'The Instrumental',
    'My Instrumental',
    'Rap Over My Instrumental',
    'Mostly Instrumental',
    'I Am Not Instrumental',
    'Instrumental Version of Myself',
    'Semi-Instrumental',
    'Lupe Fiasco - Food & Liquor - 05 - The Instrumental',
    'Instrumental (feat. X)',
    'Instrumental (Live from the Queen Elizabeth Hall)',
    'For Free? (interlude)',
    'Intro',
    'Song (Piano Vocals)',
    'Artist - Rap Over My Instrumental',
    'Song',
    '',
    None,
]


class TestTitleTier:
    @pytest.mark.parametrize('title', STRONG)
    def test_strong(self, title):
        assert instrumental_title_tier(title) == 'strong'

    @pytest.mark.parametrize('title', WEAK)
    def test_weak(self, title):
        assert instrumental_title_tier(title) == 'weak'

    @pytest.mark.parametrize('title', NONE)
    def test_none(self, title):
        assert instrumental_title_tier(title) is None

    @pytest.mark.parametrize('title,kind', [
        ('Song (Karaoke Version)', 'karaoke'),
        ('Song (Off Vocal)', 'offvocal'),
        ('A Screw (Holy Money) (mix, no lead vocal)', 'offvocal'),
        ('Lose Yourself (Instrumental)', 'instrumental'),
        ('Song (伴奏)', 'instrumental'),
        ('Instrumental 2', 'instrumental'),
        ('Song', None),
    ])
    def test_qualifier_kind(self, title, kind):
        assert instrumental_qualifier_kind(title) == kind

    @pytest.mark.parametrize('title,stripped', [
        ('Lose Yourself (Instrumental)', 'Lose Yourself'),
        ('Song - Instrumental', 'Song'),
        ('Song – Instrumental Version', 'Song'),
        ('A Screw (Holy Money) (mix, no lead vocal)', 'A Screw (Holy Money)'),
        ('Song Instrumental Version', 'Song'),
        # Not STRONG → unchanged.
        ('Song (Instrumental Intro)', 'Song (Instrumental Intro)'),
        ('The Instrumental', 'The Instrumental'),
    ])
    def test_strip_qualifier(self, title, stripped):
        assert strip_instrumental_qualifier(title) == stripped

    def test_legacy_rule_is_broader(self):
        # Titles the pre-1.7 regex flagged that the new tiers never call STRONG.
        for title in ('The Instrumental', 'Mostly Instrumental', 'Rap Over My Instrumental',
                      'Song (Instrumental Intro)'):
            assert matches_legacy_title_rule(title)
            assert instrumental_title_tier(title) != 'strong'
        assert not matches_legacy_title_rule('Song')
        assert not matches_legacy_title_rule(None)


class TestGenericTitles:
    @pytest.mark.parametrize('title', ['Intro', 'Outro', 'Interlude', 'Skit #3', 'Untitled 03', '04 - Intro',
                                       'Reprise', 'Track 12', 'Hidden Track', '07', ''])
    def test_generic(self, title):
        assert is_generic_title(title) is True
        assert needs_album_context(title) is True

    @pytest.mark.parametrize('title', ['Intro to Love', 'Outro (feat. X)', 'The Interlude Song', 'Song'])
    def test_not_generic(self, title):
        assert is_generic_title(title) is False

    def test_needs_album_context_via_version_tag(self):
        assert needs_album_context('For Free? (interlude)')
        assert needs_album_context('Song (Reprise)')
        assert not needs_album_context('Lose Yourself (Instrumental)')
        assert not needs_album_context('Intro to Love')

    @pytest.mark.parametrize('title,tags', [
        ('Song (Reprise)', {'reprise'}),
        ('For Free? (interlude)', {'interlude'}),
        ('Song (Instrumental Intro)', {'instrumental', 'intro'}),
        ('Song (Karaoke Version)', {'karaoke'}),
        ('Song (A Cappella)', {'acapella'}),
        ('Skit #3', {'skit'}),
        ('Outro (feat. X)', {'outro'}),
        ('Intro to Love', set()),
        ('The Instrumental', set()),
        ('Song', set()),
    ])
    def test_version_tags(self, title, tags):
        assert version_tags(title) == frozenset(tags)


class TestLines:
    @pytest.mark.parametrize('line', ['作词 : 某人', '[00:01.00]作曲：某人', 'Composer: Someone', '词: 某人',
                                      '指挥家 : 某人', '[00:02.00]大提琴：某人', '录音助理: 某人', '母带工程师 : 某人'])
    def test_credit_lines(self, line):
        assert is_credit_line(line)

    @pytest.mark.parametrize('line', ['first line of the verse', 'Composer of dreams', ''])
    def test_not_credit_lines(self, line):
        assert not is_credit_line(line)

    @pytest.mark.parametrize('line', [
        'Lyrics not available', 'No lyrics found.', '[00:01.00]Lyrics coming soon',
        'Lyrics for this song have yet to be released. Please check back once the song has been released.',
        "We don't have the lyrics for this song yet",
        'Unfortunately, we are not licensed to display the full lyrics for this song at the moment.',
        '纯音乐，请欣赏', '此歌曲为没有填词的纯音乐，请您欣赏',
    ])
    def test_placeholder_lines(self, line):
        assert is_placeholder_line(line)

    @pytest.mark.parametrize('line', ['first line of the verse', 'no lyrics here today my friend'])
    def test_not_placeholder_lines(self, line):
        assert not is_placeholder_line(line)

    @pytest.mark.parametrize('line', ['Instrumental', '[Instrumental]', '(instrumental)', '♪ Instrumental ♪',
                                      '[00:00.00] Instrumental', '[au: instrumental]', '[au:instrumental]',
                                      '纯音乐，请欣赏', '此歌曲为没有填词的纯音乐，请您欣赏'])
    def test_marker_lines(self, line):
        assert is_explicit_instrumental_marker_line(line)

    @pytest.mark.parametrize('line', ['[Intro]', 'Outro', '[Interlude]', 'Skit', 'Instrumental break here',
                                      'The instrumental', 'first line of the verse'])
    def test_not_marker_lines(self, line):
        assert not is_explicit_instrumental_marker_line(line)

    @pytest.mark.parametrize('line', ['https://example.com/lyrics', 'www.example.com', 'Lyrics by example',
                                      'Downloaded from somewhere', '♪ ♪ ♪', '...'])
    def test_junk_lines(self, line):
        assert is_junk_line(line)

    def test_real_lyric_lines(self):
        text = ('\ufeff[ar: Someone]\n[ti: Song]\n[00:00.00]作词 : 某人\n[Verse 1]\n'
                '[00:10.00]first line of the verse\n[00:12.00]<00:12.50>la la placeholder\n'
                '[00:20.00]\n[00:30.00]Instrumental\nhttps://example.com\n')
        assert real_lyric_lines(text) == ['first line of the verse', 'la la placeholder']

    def test_strip_credit_blocks_keeps_body_order(self):
        text = ('[00:00.00]作词 : A\n[00:01.00]作曲 : B\n[00:05.00]first line of the verse\n'
                '[00:07.00]Composer: not a credit block here\n[00:09.00]second line\n[00:20.00]制作人 : C')
        out = strip_credit_blocks(text)
        assert out == ('[00:05.00]first line of the verse\n[00:07.00]Composer: not a credit block here\n'
                       '[00:09.00]second line')


class TestClassify:
    @pytest.mark.parametrize('text,kind', [
        ('[au: instrumental]\n', 'marker'),
        ('[Instrumental]', 'marker'),
        ('[00:00.00] Instrumental\n', 'marker'),
        ('♪ Instrumental ♪', 'marker'),
        ('[ar: x]\n[ti: y]\n', 'empty'),
        ('', 'empty'),
        ('   \n\n', 'empty'),
        (None, 'empty'),
        ('Lyrics not available', 'placeholder'),
        ('[00:01.00]作词 : X\n[00:02.00]作曲 : Y', 'credits_only'),
        # NetEase "pure music" text: explicit marker beats the placeholder reading.
        ('[00:00.00]作词 : X\n[00:01.00]作曲 : Y\n[00:02.00]纯音乐，请欣赏', 'marker'),
        ('此歌曲为没有填词的纯音乐，请您欣赏', 'marker'),
        ('first line of the verse', 'lyrics'),
        ('[Intro]\nfirst line of the verse\n[Instrumental]\nsecond line here', 'lyrics'),
        ('[00:10.00]la la placeholder', 'lyrics'),
    ])
    def test_classify(self, text, kind):
        assert classify_lyrics_text(text) == kind

    def test_substantive(self):
        assert is_substantive_lyrics('one line\ntwo line\nthree line\nfour line')
        assert not is_substantive_lyrics('one line\nONE LINE\ntwo line\nthree line')
        assert not is_substantive_lyrics('[au: instrumental]')
        assert is_substantive_lyrics('one\ntwo', min_distinct=2)


class TestNormalizeForWrite:
    @pytest.mark.parametrize('raw,expected', [
        ('\ufeffa\r\nb\r\n\r\n', 'a\nb\n'),
        ('a\rb', 'a\nb\n'),
        ('a\n\n\n', 'a\n'),
        ('a', 'a\n'),
        ('', ''),
        ('  \n ', ''),
        (None, ''),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_text_for_write(raw) == expected
