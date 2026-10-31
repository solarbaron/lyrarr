"""Regression tests for provider HTTP handling (requests mocked — no network).

Covers: the shared safe cover downloader (host allow-lists re-checked per
redirect, byte cap, magic-number check), fanart.tv v3 album-cover parsing,
Deezer/iTunes result parsing + album verification, Musixmatch/NetEase
transient-error and malformed-body handling, NetEase credit stripping and
pure-music hints, Musixmatch search parameters/fallback/hints, and Genius
search_all scoring. All lyric text is synthetic.
"""

import sys
import tempfile

if 'lyrarr.app.get_args' not in sys.modules:
    # Provider modules import app config — point it at a throwaway dir, never data/.
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-metadata-')]
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

import pytest  # noqa: E402
import requests  # noqa: E402

from lyrarr.metadata import cover_fetch  # noqa: E402
from lyrarr.metadata.cover_fetch import cover_result_matches, safe_download, sniff_image  # noqa: E402
from lyrarr.metadata.provider_utils import (  # noqa: E402
    ProviderTransientError,
    begin_search,
    search_had_transient_error,
)

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


class FakeResponse:
    def __init__(self, status=200, body=b'', headers=None, json_data=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self._json = json_data
        self.closed = False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def json(self):
        if self._json is None:
            raise ValueError('no json')
        return self._json

    def close(self):
        self.closed = True


def _route(monkeypatch, module, table):
    """Patch module.requests.get/post to answer from {url_prefix: response}."""
    calls = []

    def fake(url, *args, **kwargs):
        calls.append(url)
        for prefix, resp in table.items():
            if url.startswith(prefix):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f'unexpected URL {url}')

    monkeypatch.setattr(module.requests, 'get', fake)
    monkeypatch.setattr(module.requests, 'post', fake)
    return calls


class TestSafeDownload:
    def test_allowed_host_jpeg(self, monkeypatch):
        _route(monkeypatch, cover_fetch, {'https://is1-ssl.mzstatic.com/': FakeResponse(body=JPEG)})
        assert safe_download('https://is1-ssl.mzstatic.com/a/100x100bb.jpg', 'itunes') == JPEG

    def test_disallowed_host_not_fetched(self, monkeypatch):
        calls = _route(monkeypatch, cover_fetch, {})
        assert safe_download('http://169.254.169.254/latest/meta-data', 'deezer') is None
        assert safe_download('https://evil.example/cover.jpg', 'itunes') is None
        assert safe_download('file:///etc/passwd', 'musicbrainz') is None
        assert safe_download('https://mzstatic.com.evil.example/x.jpg', 'itunes') is None
        assert safe_download('https://cdns-images.dzcdn.net:8080/x.jpg', 'deezer') is None
        assert calls == []

    def test_redirect_rechecked_each_hop(self, monkeypatch):
        calls = _route(monkeypatch, cover_fetch, {
            'https://coverartarchive.org/': FakeResponse(302, headers={'Location': 'https://archive.org/download/x.jpg'}),
            'https://archive.org/': FakeResponse(302, headers={'Location': 'https://ia800.us.archive.org/x.jpg'}),
            'https://ia800.us.archive.org/': FakeResponse(body=PNG),
        })
        assert safe_download('https://coverartarchive.org/release/1/front', 'musicbrainz') == PNG
        assert len(calls) == 3

    def test_redirect_to_disallowed_host(self, monkeypatch):
        calls = _route(monkeypatch, cover_fetch, {
            'https://coverartarchive.org/': FakeResponse(302, headers={'Location': 'http://127.0.0.1:8686/api'}),
        })
        assert safe_download('https://coverartarchive.org/release/1/front', 'musicbrainz') is None
        assert calls == ['https://coverartarchive.org/release/1/front']

    def test_byte_cap(self, monkeypatch):
        _route(monkeypatch, cover_fetch, {'https://assets.fanart.tv/': FakeResponse(body=JPEG * 100)})
        assert safe_download('https://assets.fanart.tv/x.jpg', 'fanart', max_bytes=100) is None

    def test_declared_length_cap(self, monkeypatch):
        resp = FakeResponse(body=JPEG, headers={'Content-Length': str(10 ** 9)})
        _route(monkeypatch, cover_fetch, {'https://assets.fanart.tv/': resp})
        assert safe_download('https://assets.fanart.tv/x.jpg', 'fanart') is None

    def test_non_image_rejected(self, monkeypatch):
        _route(monkeypatch, cover_fetch, {'https://www.theaudiodb.com/': FakeResponse(body=b'<html>hi</html>')})
        assert safe_download('https://www.theaudiodb.com/x.jpg', 'theaudiodb') is None

    def test_sniff(self):
        assert sniff_image(JPEG) == ('jpg', 'image/jpeg')
        assert sniff_image(PNG) == ('png', 'image/png')
        assert sniff_image(b'RIFF\x00\x00\x00\x00WEBPVP8 ') == ('webp', 'image/webp')
        assert sniff_image(b'GIF89a....') == ('gif', 'image/gif')
        assert sniff_image(b'nope') is None


class TestCoverResultMatches:
    def test_mbid_results_pass(self):
        assert cover_result_matches({'url': 'x'}, 'Adele', '25')

    def test_requires_album_and_artist(self):
        assert cover_result_matches({'title': 'Abbey Road (Remastered)', 'artist': 'The Beatles'},
                                    'The Beatles', 'Abbey Road')
        assert not cover_result_matches({'title': 'Let It Be', 'artist': 'The Beatles'},
                                        'The Beatles', 'Abbey Road')
        assert not cover_result_matches({'title': 'Abbey Road', 'artist': 'Some Tribute Band'},
                                        'The Beatles', 'Abbey Road')


FANART_DOC = {
    'name': 'Adele',
    'mbid_id': 'artist-mbid',
    'artistthumb': [{'id': '1', 'url': 'https://assets.fanart.tv/fanart/music/x/artistthumb/a.jpg', 'likes': '9'}],
    'artistbackground': [{'id': '2', 'url': 'https://assets.fanart.tv/fanart/music/x/bg.jpg', 'likes': '5'}],
    'albums': {
        'rg-21': {
            'albumcover': [
                {'id': '3', 'url': 'https://assets.fanart.tv/fanart/music/x/albumcover/21-a.jpg', 'likes': '1'},
                {'id': '4', 'url': 'https://assets.fanart.tv/fanart/music/x/albumcover/21-b.jpg', 'likes': '7'},
            ],
            'cdart': [{'id': '5', 'url': 'https://assets.fanart.tv/fanart/music/x/cdart/21.png', 'likes': '2'}],
        },
        'rg-25': {
            'albumcover': [{'id': '6', 'url': 'https://assets.fanart.tv/fanart/music/x/albumcover/25.jpg'}],
        },
    },
}


class TestFanart:
    @pytest.fixture
    def provider(self, monkeypatch):
        from lyrarr.metadata.covers import fanart
        monkeypatch.setattr(fanart.FanartCoverProvider, '_api_key', property(lambda self: 'key'))
        return fanart, fanart.FanartCoverProvider()

    def test_only_requested_release_group_covers(self, provider, monkeypatch):
        module, p = provider
        _route(monkeypatch, module, {'https://webservice.fanart.tv/v3/music/albums/rg-21':
                                     FakeResponse(json_data=FANART_DOC)})
        results = p.search(mb_artist_id='artist-mbid', mb_album_id='rg-21')
        assert [r['url'].rsplit('/', 1)[1] for r in results] == ['21-b.jpg', '21-a.jpg']
        assert all(r['type'] == 'front' for r in results)

    def test_artist_only_returns_nothing(self, provider, monkeypatch):
        module, p = provider
        calls = _route(monkeypatch, module, {})
        assert p.search(mb_artist_id='artist-mbid') == []
        assert calls == []

    def test_falls_back_to_artist_document(self, provider, monkeypatch):
        module, p = provider
        _route(monkeypatch, module, {
            'https://webservice.fanart.tv/v3/music/albums/': FakeResponse(404),
            'https://webservice.fanart.tv/v3/music/artist-mbid': FakeResponse(json_data=FANART_DOC),
        })
        results = p.search(mb_artist_id='artist-mbid', mb_album_id='rg-25')
        assert [r['url'].rsplit('/', 1)[1] for r in results] == ['25.jpg']


DEEZER_JSON = {'data': [
    {'title': '25', 'artist': {'name': 'Adele'},
     'cover_xl': 'https://e-cdns-images.dzcdn.net/images/cover/abc/1000x1000-000000-80-0-0.jpg',
     'cover_big': 'https://e-cdns-images.dzcdn.net/images/cover/abc/500x500.jpg',
     'cover_small': 'https://e-cdns-images.dzcdn.net/images/cover/abc/56x56.jpg'},
    {'title': '25 (Karaoke Hits)', 'artist': {'name': 'Karaoke Crew'},
     'cover_xl': 'https://e-cdns-images.dzcdn.net/images/cover/def/1000x1000.jpg'},
]}

ITUNES_JSON = {'resultCount': 1, 'results': [
    {'collectionName': '21', 'artistName': 'Adele',
     'artworkUrl100': 'https://is1-ssl.mzstatic.com/image/thumb/Music/v4/x/source/100x100bb.jpg'},
]}


class TestNameSearchCovers:
    def test_deezer_parsing_and_verification(self, monkeypatch):
        from lyrarr.metadata.covers import deezer
        _route(monkeypatch, deezer, {'https://api.deezer.com/search/album': FakeResponse(json_data=DEEZER_JSON)})
        results = deezer.DeezerCoverProvider().search(artist_name='Adele', album_name='25')
        assert results[0]['url'].endswith('1000x1000-000000-80-0-0.jpg')
        assert [cover_result_matches(r, 'Adele', '25') for r in results] == [True, False]
        assert all(cover_fetch.host_allowed(r['url'], 'deezer') for r in results)

    def test_itunes_parsing(self, monkeypatch):
        from lyrarr.metadata.covers import itunes
        _route(monkeypatch, itunes, {'https://itunes.apple.com/search': FakeResponse(json_data=ITUNES_JSON)})
        results = itunes.ITunesCoverProvider().search(artist_name='Adele', album_name='21')
        assert results[0]['url'].endswith('1200x1200bb.jpg')
        assert results[0]['title'] == '21' and results[0]['artist'] == 'Adele'
        assert cover_fetch.host_allowed(results[0]['url'], 'itunes')

    def test_deezer_rate_limit_is_transient(self, monkeypatch):
        from lyrarr.metadata.covers import deezer
        _route(monkeypatch, deezer, {'https://api.deezer.com/': FakeResponse(429)})
        begin_search()
        assert deezer.DeezerCoverProvider().search(artist_name='A', album_name='B') == []
        assert search_had_transient_error() is True


class TestMusixmatch:
    @pytest.fixture
    def mxm(self, monkeypatch):
        from lyrarr.metadata.lyrics import musixmatch
        monkeypatch.setattr(musixmatch.MusixmatchProvider, '_api_key', property(lambda self: 'k'))
        return musixmatch

    def _env(self, status, body):
        return FakeResponse(json_data={'message': {'header': {'status_code': status}, 'body': body}})

    def test_empty_body_string_is_not_a_crash(self, mxm, monkeypatch):
        _route(monkeypatch, mxm, {'https://api.musixmatch.com/': self._env(404, '')})
        assert mxm.MusixmatchProvider().search(track_name='Hello', artist_name='Adele', duration=295) == []

    def test_timeout_is_transient(self, mxm, monkeypatch):
        _route(monkeypatch, mxm, {'https://api.musixmatch.com/': requests.exceptions.Timeout('slow')})
        begin_search()
        with pytest.raises(ProviderTransientError):
            mxm.MusixmatchProvider().search(track_name='Hello', artist_name='Adele')
        assert search_had_transient_error() is True

    def test_quota_is_transient(self, mxm, monkeypatch):
        _route(monkeypatch, mxm, {'https://api.musixmatch.com/': self._env(402, '')})
        with pytest.raises(ProviderTransientError):
            mxm.MusixmatchProvider().search(track_name='Hello', artist_name='Adele')

    def test_free_tier_excerpt_flagged_and_footer_stripped(self, mxm, monkeypatch):
        track = {'track_list': [{'track': {'track_id': 1, 'track_name': 'Hello', 'artist_name': 'Adele',
                                           'track_length': 295}}]}
        lyrics = {'lyrics': {'lyrics_body': "first line of the verse\nsecond line of the verse\n...\n\n"
                                            "******* This Lyrics is NOT for Commercial use *******\n(1409)"}}
        _route(monkeypatch, mxm, {
            'https://api.musixmatch.com/ws/1.1/track.search': self._env(200, track),
            'https://api.musixmatch.com/ws/1.1/track.lyrics.get': self._env(200, lyrics),
            'https://api.musixmatch.com/ws/1.1/track.subtitle.get': self._env(401, ''),
        })
        [r] = mxm.MusixmatchProvider().search(track_name='Hello', artist_name='Adele', duration=295)
        assert r['plain_truncated'] is True
        assert 'Commercial' not in r['plain_lyrics'] and not r['plain_lyrics'].endswith('...')
        assert r['synced_lyrics'] is None
        assert r['match_details']['duration_diff'] == 0


class TestNetEase:
    def test_timeout_is_transient_not_not_found(self, monkeypatch):
        from lyrarr.metadata.lyrics import netease
        _route(monkeypatch, netease, {'https://music.163.com/': requests.exceptions.ConnectTimeout('x')})
        begin_search()
        with pytest.raises(ProviderTransientError):
            netease.NetEaseProvider().search(track_name='Hello', artist_name='Adele')
        assert search_had_transient_error() is True

    def test_throttle_code_is_transient(self, monkeypatch):
        from lyrarr.metadata.lyrics import netease
        _route(monkeypatch, netease, {'https://music.163.com/': FakeResponse(json_data={'code': -460})})
        with pytest.raises(ProviderTransientError):
            netease.NetEaseProvider().search(track_name='Hello', artist_name='Adele')

    def test_null_result_is_no_match(self, monkeypatch):
        from lyrarr.metadata.lyrics import netease
        _route(monkeypatch, netease, {'https://music.163.com/': FakeResponse(json_data={'code': 200, 'result': None})})
        assert netease.NetEaseProvider().search(track_name='Hello', artist_name='Adele') == []


# ---------------------------------------------------------------------------
# NetEase: credit blocks, pure-music hints, strategy continuation
# ---------------------------------------------------------------------------

def _ne_lrc(head, body, tail=()):
    lines = [f"[00:{i:02d}.000] {role} : Person {chr(65 + i)}" for i, role in enumerate(head)]
    lines += [f"[{10 + i // 6:02d}:{(i * 7) % 60:02d}.{i:02d}0]{text}" for i, text in enumerate(body)]
    lines += [f"[04:{30 + i:02d}.000] {role} : Person {chr(80 + i % 10)}" for i, role in enumerate(tail)]
    return "\n".join(lines) + "\n"


# Shapes of live NetEase answers (scratchpad ne_1426503 / ne_17177324 /
# ne_186016): same flags, same credit roles, synthetic names and lyrics.
NE_PURE_MUSIC = {
    'pureMusic': True, 'code': 200,
    'lrc': {'version': 1, 'lyric': _ne_lrc(
        ['作曲', '制作人', '指挥家'], ['纯音乐，请欣赏'],
        ['音频助理', '混音师', '贝斯', '中提琴', '附加编程', '吉他', '合成器', '长号', '监制', '大提琴',
         '录音', '混音助理', '大号', '母带工程师'])},
}
NE_EN_CREDITS = {
    'code': 200,
    'lrc': {'version': 80, 'lyric': _ne_lrc(
        ['作词', '作曲', '制作人'],
        [f"placeholder english line {i}" for i in range(12)])},
}
NE_ZH_CREDITS = {
    'code': 200,
    'lrc': {'version': 70, 'lyric': _ne_lrc(
        ['作词', '作曲', '编曲', '制作人', '和声编写', '吉他', '贝斯', '鼓', '录音工程', '混音', '录音助理'],
        [f"占位歌词第{i}行" for i in range(12)])},
}


def _ne_song(sid, name='Song', artist='Band', duration_ms=200000, album='Album'):
    return {'id': sid, 'name': name, 'artists': [{'name': artist}], 'duration': duration_ms,
            'album': {'name': album}}


class NetEaseFake:
    def __init__(self, searches, lyrics):
        self.searches = list(searches)   # one songs-list per search request, in order
        self.lyrics = lyrics             # id -> lyric JSON
        self.queries = []
        self.lyric_ids = []

    def post(self, url, data=None, **kw):
        self.queries.append(data['s'])
        songs = self.searches.pop(0) if self.searches else []
        return FakeResponse(json_data={'code': 200, 'result': {'songs': songs}})

    def get(self, url, params=None, **kw):
        self.lyric_ids.append(params['id'])
        return FakeResponse(json_data=self.lyrics.get(params['id'], {'code': 200}))


@pytest.fixture
def netease_fake(monkeypatch):
    from lyrarr.metadata.lyrics import netease

    def install(searches, lyrics):
        fake = NetEaseFake(searches, lyrics)
        monkeypatch.setattr(netease.requests, 'post', fake.post)
        monkeypatch.setattr(netease.requests, 'get', fake.get)
        return fake
    return netease, install


class TestNetEaseContent:
    def test_pure_music_gives_hint_not_lyrics(self, netease_fake):
        netease, install = netease_fake
        install([[_ne_song(1426503, duration_ms=201000)]], {1426503: NE_PURE_MUSIC})
        [r] = netease.NetEaseProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert r['synced_lyrics'] is None and r['plain_lyrics'] is None
        assert 'instrumental' not in r
        hint = r['instrumental_hint']
        assert hint['provider'] == 'netease' and hint['kind'] == 'pure_music'
        assert hint['duration_diff'] == 1 and hint['score'] >= 0.5
        assert hint['matched_title'] == 'Song' and hint['matched_album'] == 'Album'
        assert r['strategy'] == 'search' and r['source_id'] == 1426503

    def test_credits_stripped_english(self, netease_fake):
        netease, install = netease_fake
        install([[_ne_song(17177324)]], {17177324: NE_EN_CREDITS})
        [r] = netease.NetEaseProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert ' : ' not in r['synced_lyrics'] and ' : ' not in r['plain_lyrics']
        assert r['plain_lyrics'].splitlines()[0] == 'placeholder english line 0'
        assert r['synced_lyrics'].splitlines()[0].startswith('[10:00.000]')
        assert 'instrumental_hint' not in r

    def test_credits_stripped_chinese_extended_roles(self, netease_fake):
        netease, install = netease_fake
        install([[_ne_song(186016)]], {186016: NE_ZH_CREDITS})
        [r] = netease.NetEaseProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert ' : ' not in r['synced_lyrics']
        assert len(r['plain_lyrics'].splitlines()) == 12
        assert r['plain_lyrics'].splitlines()[0] == '占位歌词第0行'

    def test_credit_strip_keeps_middle_lines(self):
        from lyrarr.metadata.lyrics.netease import strip_netease_credits
        text = "[00:00.00] 作词 : A\n[00:10.00]first line\n[00:20.00]Producer: middle line\n[00:30.00]end"
        assert "[00:20.00]Producer: middle line" in strip_netease_credits(text)

    def test_nolyric_flag_hints(self, netease_fake):
        netease, install = netease_fake
        install([[_ne_song(5)]], {5: {'code': 200, 'nolyric': True}})
        [r] = netease.NetEaseProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert r['instrumental_hint']['kind'] == 'pure_music'

    def test_uncollected_is_not_a_hint(self, netease_fake):
        netease, install = netease_fake
        install([[_ne_song(6)]], {6: {'code': 200, 'uncollected': True}})
        assert netease.NetEaseProvider().search(track_name='Song', artist_name='Band', duration=200) == []

    def test_no_hint_for_instrumental_titled_match(self, netease_fake):
        netease, install = netease_fake
        install([[_ne_song(7, name='Song (伴奏)')]] * 4, {7: NE_PURE_MUSIC})
        assert netease.NetEaseProvider().search(track_name='Song', artist_name='Band', duration=200) == []

    def test_no_hint_for_gated_match(self, netease_fake):
        netease, install = netease_fake
        install([[_ne_song(8, artist='Somebody Else')]] * 4, {8: NE_PURE_MUSIC})
        provider = netease.NetEaseProvider()
        assert provider.search(track_name='Song', artist_name='Band', duration=200) == []
        # Manual search (strict=False) shows the gated candidate, still without a hint.
        install([[_ne_song(8, artist='Somebody Else')]] * 4, {8: NE_EN_CREDITS})
        [r] = provider.search(track_name='Song', artist_name='Band', duration=200, strict=False)
        assert r['match_details']['gate'] == 'artist_mismatch'

    def test_hint_conflicting_with_lyric_lines_is_dropped(self, netease_fake):
        netease, install = netease_fake
        data = dict(NE_EN_CREDITS, pureMusic=True)
        install([[_ne_song(9)]], {9: data})
        [r] = netease.NetEaseProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert 'instrumental_hint' not in r and r['plain_lyrics']


class TestNetEaseStrategies:
    def test_unacceptable_first_strategy_tries_next(self, netease_fake):
        netease, install = netease_fake
        fake = install([[_ne_song(1, artist='Other Act')], [_ne_song(2)]], {2: NE_EN_CREDITS})
        [r] = netease.NetEaseProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert r['source_id'] == 2
        assert len(fake.queries) == 2 and fake.lyric_ids == [2]

    def test_stops_at_first_acceptable(self, netease_fake):
        netease, install = netease_fake
        fake = install([[_ne_song(3)], [_ne_song(4)]], {3: NE_EN_CREDITS})
        [r] = netease.NetEaseProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert r['source_id'] == 3 and len(fake.queries) == 1

    def test_alt_artist_names(self, netease_fake):
        netease, install = netease_fake
        install([[_ne_song(10, artist='Guest Singer')]], {10: NE_EN_CREDITS})
        [r] = netease.NetEaseProvider().search(track_name='Song', artist_name='Album Artist', duration=200,
                                               alt_artist_names=('Guest Singer',))
        assert r['match_details']['gate'] is None


# ---------------------------------------------------------------------------
# Musixmatch: params, fallback, strict, hints
# ---------------------------------------------------------------------------

class MxmFake:
    def __init__(self, searches, lyrics=None, subtitles=None):
        self.searches = list(searches)
        self.lyrics = lyrics or {}
        self.subtitles = subtitles or {}
        self.calls = []

    @staticmethod
    def env(status, body):
        return FakeResponse(json_data={'message': {'header': {'status_code': status}, 'body': body}})

    def __call__(self, url, params=None, **kw):
        endpoint = url.rsplit('/', 1)[-1]
        self.calls.append((endpoint, dict(params or {})))
        if endpoint == 'track.search':
            tracks = self.searches.pop(0) if self.searches else []
            return self.env(200, {'track_list': [{'track': t} for t in tracks]})
        if endpoint == 'track.lyrics.get':
            body = self.lyrics.get(params['track_id'])
            return self.env(200, {'lyrics': {'lyrics_body': body}}) if body else self.env(404, '')
        body = self.subtitles.get(params['track_id'])
        return self.env(200, {'subtitle': {'subtitle_body': body}}) if body else self.env(404, '')

    def endpoints(self):
        return [e for e, _ in self.calls]


def _mxm_track(tid, name='Song', artist='Band', length=200, album='Album', instrumental=0):
    return {'track_id': tid, 'track_name': name, 'artist_name': artist, 'track_length': length,
            'album_name': album, 'instrumental': instrumental, 'has_lyrics': 1}


@pytest.fixture
def mxm_fake(monkeypatch):
    from lyrarr.metadata.lyrics import musixmatch
    monkeypatch.setattr(musixmatch.MusixmatchProvider, '_api_key', property(lambda self: 'k'))

    def install(*args, **kwargs):
        fake = MxmFake(*args, **kwargs)
        monkeypatch.setattr(musixmatch.requests, 'get', fake)
        return fake
    return musixmatch, install


class TestMusixmatchSearch:
    def test_search_params(self, mxm_fake):
        mxm, install = mxm_fake
        fake = install([[_mxm_track(1)]], lyrics={1: 'first line of the verse\nsecond line'})
        mxm.MusixmatchProvider().search(track_name='Song (Remastered 2011)', artist_name='Band',
                                        album_name='Album', duration=200)
        params = fake.calls[0][1]
        assert params['f_has_lyrics'] == 1
        assert 'q_album' not in params and 'f_track_length' not in params
        assert params['q_track'] == 'Song' and params['q_artist'] == 'Band'

    def test_result_shape(self, mxm_fake):
        mxm, install = mxm_fake
        install([[_mxm_track(11)]], lyrics={11: 'first line of the verse\nsecond line'})
        [r] = mxm.MusixmatchProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert r['strategy'] == 'search' and r['source_id'] == 11
        assert r['album_name'] == 'Album' and r['duration'] == 200
        assert r['match_details']['duration_diff'] == 0

    def test_title_only_fallback_when_best_is_gated(self, mxm_fake):
        mxm, install = mxm_fake
        fake = install([[_mxm_track(1, artist='Other Act')], [_mxm_track(2)]],
                       lyrics={2: 'first line of the verse\nsecond line'})
        [r] = mxm.MusixmatchProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert r['source_id'] == 2
        assert 'q_artist' not in fake.calls[1][1]

    def test_strict_skips_lyrics_calls_for_gated(self, mxm_fake):
        mxm, install = mxm_fake
        fake = install([[_mxm_track(1, artist='Other Act')], [_mxm_track(1, artist='Other Act')]],
                       lyrics={1: 'first line of the verse\nsecond line'})
        assert mxm.MusixmatchProvider().search(track_name='Song', artist_name='Band', duration=200) == []
        assert 'track.lyrics.get' not in fake.endpoints()
        assert 'track.subtitle.get' not in fake.endpoints()

    def test_manual_search_fetches_gated(self, mxm_fake):
        mxm, install = mxm_fake
        install([[_mxm_track(1, artist='Other Act')], [_mxm_track(1, artist='Other Act')]],
                lyrics={1: 'first line of the verse\nsecond line'})
        [r] = mxm.MusixmatchProvider().search(track_name='Song', artist_name='Band', duration=200, strict=False)
        assert r['match_details']['gate'] == 'artist_mismatch'

    def test_instrumental_flag_gives_hint(self, mxm_fake):
        mxm, install = mxm_fake
        fake = install([[_mxm_track(3, length=202, instrumental=1)]])
        [r] = mxm.MusixmatchProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert r['plain_lyrics'] is None and r['synced_lyrics'] is None
        assert 'instrumental' not in r
        assert r['instrumental_hint']['kind'] == 'mxm_instrumental'
        assert r['instrumental_hint']['duration_diff'] == 2
        assert 'track.lyrics.get' not in fake.endpoints()

    def test_no_hint_for_instrumental_titled_track(self, mxm_fake):
        mxm, install = mxm_fake
        install([[_mxm_track(4, name='Song (Karaoke Version)', instrumental=1)]] * 2)
        assert mxm.MusixmatchProvider().search(track_name='Song', artist_name='Band', duration=200) == []


# ---------------------------------------------------------------------------
# Genius: excluded terms, search_all scoring, cleanup
# ---------------------------------------------------------------------------

class GeniusFake:
    def __init__(self, sections, pages):
        self.sections = sections
        self.pages = pages
        self.excluded_terms = ['tracklist', 'credits', '(instrumental)']
        self.fetched = []
        self.queries = []

    def search_all(self, term, per_page=None, page=None):
        self.queries.append(term)
        return {'sections': self.sections}

    def lyrics(self, song_id=None, song_url=None, remove_section_headers=False):
        self.fetched.append(song_id)
        return self.pages.get(song_id)


def _g_hit(gid, title='Song', artist='Band', instrumental=False):
    return {'type': 'song', 'result': {'id': gid, 'title': title, 'artist_names': artist,
                                       'primary_artist': {'name': artist}, 'url': f'https://genius.com/{gid}',
                                       'lyrics_state': 'complete', 'instrumental': instrumental}}


GENIUS_PAGE = "Song Lyrics\n[Verse 1]\nfirst line of the verse\nsecond line &amp; more\nthird line here\n12Embed"


@pytest.fixture
def genius_mod(monkeypatch):
    from lyrarr.metadata.lyrics import genius
    monkeypatch.setattr(genius.GeniusProvider, '_api_key', property(lambda self: 'k'))
    return genius


class TestGenius:
    def test_excluded_terms_keep_defaults(self, genius_mod):
        client = genius_mod.GeniusProvider()._get_client()
        assert 'tracklist' in client.excluded_terms
        assert '(Remix)' in client.excluded_terms and '(Live)' in client.excluded_terms

    def test_scores_all_hits_fetches_best_only(self, genius_mod, monkeypatch):
        fake = GeniusFake(
            [{'type': 'top_hit', 'hits': [_g_hit(1, title='Other Song')]},
             {'type': 'song', 'hits': [_g_hit(1, title='Other Song'), _g_hit(2, artist='Band Tribute Act'),
                                       _g_hit(3)]}],
            {1: GENIUS_PAGE, 2: GENIUS_PAGE, 3: GENIUS_PAGE})
        monkeypatch.setattr(genius_mod.GeniusProvider, '_get_client', lambda self: fake)
        [r] = genius_mod.GeniusProvider().search(track_name='Song', artist_name='Band', duration=200)
        assert r['source_id'] == 3 and fake.fetched == [3]
        assert r['strategy'] == 'search' and r['duration'] is None and r['album_name'] is None
        assert r['match_details']['gate'] is None
        assert '&amp;' not in r['plain_lyrics'] and 'second line & more' in r['plain_lyrics']
        assert 'Embed' not in r['plain_lyrics']

    def test_next_hit_when_best_page_empty(self, genius_mod, monkeypatch):
        fake = GeniusFake([{'type': 'song', 'hits': [_g_hit(3), _g_hit(4, artist='Band feat. Guest')]}],
                          {3: '', 4: GENIUS_PAGE})
        monkeypatch.setattr(genius_mod.GeniusProvider, '_get_client', lambda self: fake)
        [r] = genius_mod.GeniusProvider().search(track_name='Song', artist_name='Band')
        assert r['source_id'] == 4 and fake.fetched == [3, 4]

    def test_only_gated_hits_nothing_fetched(self, genius_mod, monkeypatch):
        fake = GeniusFake([{'type': 'song', 'hits': [_g_hit(5, title='Different Words'),
                                                     _g_hit(6, artist='Someone Else')]}],
                          {5: GENIUS_PAGE, 6: GENIUS_PAGE})
        monkeypatch.setattr(genius_mod.GeniusProvider, '_get_client', lambda self: fake)
        assert genius_mod.GeniusProvider().search(track_name='Song', artist_name='Band') == []
        assert fake.fetched == []

    def test_instrumental_flag_ignored(self, genius_mod, monkeypatch):
        # Genius' flag is neither a hint nor a reason to drop real lyrics.
        fake = GeniusFake([{'type': 'song', 'hits': [_g_hit(7, instrumental=True)]}], {7: GENIUS_PAGE})
        monkeypatch.setattr(genius_mod.GeniusProvider, '_get_client', lambda self: fake)
        [r] = genius_mod.GeniusProvider().search(track_name='Song', artist_name='Band')
        assert 'instrumental' not in r and 'instrumental_hint' not in r

    def test_excluded_term_skipped_unless_in_query(self, genius_mod, monkeypatch):
        fake = GeniusFake([{'type': 'song', 'hits': [_g_hit(8, title='Song (Credits)')]}], {8: GENIUS_PAGE})
        monkeypatch.setattr(genius_mod.GeniusProvider, '_get_client', lambda self: fake)
        assert genius_mod.GeniusProvider().search(track_name='Song', artist_name='Band') == []
