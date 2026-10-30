"""Regression tests for provider HTTP handling (requests mocked — no network).

Covers: the shared safe cover downloader (host allow-lists re-checked per
redirect, byte cap, magic-number check), fanart.tv v3 album-cover parsing,
Deezer/iTunes result parsing + album verification, and Musixmatch/NetEase
transient-error and malformed-body handling.
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
        lyrics = {'lyrics': {'lyrics_body': "Hello, it's me\nI was wondering\n...\n\n"
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
