"""Tests for lyrarr/metadata/lyrics_store.py (shared persistence and state helpers).

Pure helpers (content_hash, result_is_blacklisted, pick_best_synced, sidecar
decoding) plus the DB-backed helpers against a throwaway SQLite database and
temp music dirs: persist_lyrics_result (no_audio, CAS, lossy files, rules M
and T, history action, provenance), archive attribution / pins / dedupe,
set/clear_instrumental, replace requests, content-hash backfill, blacklist
hash variants, atomic writes and reconcile_track_lyrics (TestReconcile moved
here from tests/test_metadata_worker.py). All lyrics text is synthetic.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

if 'lyrarr.app.get_args' not in sys.modules:
    _saved_argv = sys.argv
    sys.argv = ['lyrarr', '--config', tempfile.mkdtemp(prefix='lyrarr-test-lyrics-store-')]
    try:
        import lyrarr.app.get_args  # noqa: F401
    finally:
        sys.argv = _saved_argv

from lyrarr.app.get_args import args as _args  # noqa: E402

_REPO_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
if os.path.abspath(_args.config_dir) == os.path.abspath(_REPO_DATA):
    pytest.skip('config was loaded from the repo data/ dir', allow_module_level=True)

from sqlalchemy import insert  # noqa: E402

from lyrarr.app.database import (  # noqa: E402
    TableAlbums,
    TableArtists,
    TableAuditIssues,
    TableAuditPins,
    TableAuditRuns,
    TableBlacklist,
    TableHistory,
    TableLibraryRoots,
    TableLyricsVersions,
    TableTracks,
    TableTrackTombstones,
    database,
    delete,
    init_db,
    select,
    update,
)
from lyrarr.metadata import lyrics_store  # noqa: E402
from lyrarr.metadata.lyrics_store import (  # noqa: E402
    PERSIST_REASONS,
    content_hash,
    pick_best_synced,
    result_is_blacklisted,
)

init_db()

PLAIN = "first line\nsecond line\nthird line"
SYNCED = "[00:01.00]first line\n[00:05.00]second line\n[00:09.00]third line"
VERSE = "\n".join([
    "first line of the verse",
    "second line of the verse",
    "la la placeholder chorus",
    "another line that rhymes",
    "closing words of the song",
])
OTHER = VERSE.replace("verse", "stanza").replace("rhymes", "scans")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_synced_and_plain_hash_equal(self):
        # The same lyrics in synced and plain form must hash identically so a
        # blacklist entry catches both representations.
        assert content_hash(PLAIN) == content_hash(SYNCED)

    def test_case_and_edge_whitespace_insensitive(self):
        # Lowercased and per-line leading/trailing whitespace stripped (internal
        # spacing is intentionally preserved).
        assert content_hash("Hello World") == content_hash("  hello world  ")

    def test_bom_ignored(self):
        assert content_hash('\ufeff' + PLAIN) == content_hash(PLAIN)

    def test_empty_is_none(self):
        assert content_hash("") is None
        assert content_hash(None) is None
        assert content_hash("   ") is None


class TestResultIsBlacklisted:
    def test_matches_plain(self):
        bl = {content_hash(PLAIN)}
        assert result_is_blacklisted({"plain_lyrics": PLAIN}, bl) is True

    def test_matches_synced_against_plain_hash(self):
        # Blacklisted from a plain copy; a synced result of the same song matches.
        bl = {content_hash(PLAIN)}
        assert result_is_blacklisted({"synced_lyrics": SYNCED}, bl) is True

    def test_non_match_passes(self):
        bl = {content_hash(PLAIN)}
        other = {"plain_lyrics": "completely different words here\nand more\nlines"}
        assert result_is_blacklisted(other, bl) is False

    def test_empty_blacklist_never_matches(self):
        assert result_is_blacklisted({"plain_lyrics": PLAIN}, set()) is False

    def test_result_without_content(self):
        bl = {content_hash(PLAIN)}
        assert result_is_blacklisted({}, bl) is False

    def test_matches_repaired_hash(self):
        # Blacklisted after lyrarr repaired a compressed LRC: the raw provider
        # copy must still be recognised.
        raw = '[00:12.00][01:20.00]la la placeholder\n[00:15.00]first line of the verse'
        from lyrarr.metadata.lrc_repair import repair_lrc
        repaired_hash = content_hash(repair_lrc(raw))
        assert repaired_hash != content_hash(raw)
        assert result_is_blacklisted({'synced_lyrics': raw}, {repaired_hash}) is True

    def test_matches_legacy_hash(self, monkeypatch):
        from lyrarr.metadata import merge
        monkeypatch.setattr(merge, 'legacy_content_hash', lambda text: 'legacy-' + str(len(text)), raising=False)
        assert result_is_blacklisted({'plain_lyrics': PLAIN}, {'legacy-' + str(len(PLAIN))}) is True


class TestPickBestSynced:
    def test_picks_highest_scoring_synced(self):
        results = [
            {"synced_lyrics": SYNCED, "score": 0.6},
            {"synced_lyrics": SYNCED, "score": 0.9},
            {"synced_lyrics": SYNCED, "score": 0.7},
        ]
        assert pick_best_synced(results)["score"] == 0.9

    def test_ignores_plain_only_results(self):
        results = [
            {"plain_lyrics": PLAIN, "score": 0.95},
            {"synced_lyrics": SYNCED, "score": 0.5},
        ]
        best = pick_best_synced(results)
        assert best is not None
        assert best.get("synced_lyrics") == SYNCED

    def test_none_when_no_synced(self):
        results = [{"plain_lyrics": PLAIN, "score": 0.9}]
        assert pick_best_synced(results) is None

    def test_respects_min_score(self):
        results = [{"synced_lyrics": SYNCED, "score": 0.4}]
        assert pick_best_synced(results, min_score=0.5) is None
        assert pick_best_synced(results, min_score=0.3) is not None

    def test_empty(self):
        assert pick_best_synced([]) is None


class TestSidecarDecoding:
    def test_utf8_bom(self, tmp_path):
        p = tmp_path / 'a.lrc'
        p.write_bytes(('\ufeff' + 'la la placeholder \u00fc').encode())
        sr = lyrics_store.read_sidecar(str(p))
        assert sr.text == 'la la placeholder \u00fc' and sr.lossless and sr.had_bom and sr.encoding == 'utf-8-sig'

    @pytest.mark.parametrize('enc', ['utf-16-le', 'utf-16-be'])
    def test_utf16_with_bom(self, tmp_path, enc):
        import codecs
        bom = codecs.BOM_UTF16_LE if enc.endswith('le') else codecs.BOM_UTF16_BE
        p = tmp_path / 'a.lrc'
        p.write_bytes(bom + 'first line of the verse\nテスト'.encode(enc))
        sr = lyrics_store.read_sidecar(str(p))
        assert sr.text == 'first line of the verse\nテスト' and sr.lossless and sr.had_bom

    def test_cp1252_is_lossy_not_guessed(self, tmp_path):
        p = tmp_path / 'a.lrc'
        p.write_bytes('café placeholder'.encode('cp1252'))
        sr = lyrics_store.read_sidecar(str(p))
        assert sr.lossless is False and '�' in sr.text

    def test_missing_file(self, tmp_path):
        assert lyrics_store.read_sidecar(str(tmp_path / 'nope.lrc')) is None


class TestAtomicWrite:
    def test_write_and_replace(self, tmp_path):
        p = tmp_path / 'a.lrc'
        p.write_text('old words')
        lyrics_store.atomic_write_text(str(p), 'new words\n')
        assert p.read_text() == 'new words\n'
        assert not any(n.endswith('.lyrarr-tmp') for n in os.listdir(tmp_path))

    def test_failure_keeps_original(self, tmp_path, monkeypatch):
        p = tmp_path / 'a.lrc'
        p.write_text('old words')

        def boom(src, dst):
            raise OSError('disk full')
        monkeypatch.setattr(lyrics_store.os, 'replace', boom)
        with pytest.raises(OSError):
            lyrics_store.atomic_write_text(str(p), 'new words')
        assert p.read_text() == 'old words'
        assert not any(n.endswith('.lyrarr-tmp') for n in os.listdir(tmp_path))

    def test_missing_dir_is_not_created(self, tmp_path):
        target = tmp_path / 'gone' / 'a.lrc'
        with pytest.raises(OSError):
            lyrics_store.atomic_write_text(str(target), 'x')
        assert not (tmp_path / 'gone').exists()


# ---------------------------------------------------------------------------
# DB-backed helpers
# ---------------------------------------------------------------------------

_TABLES = (TableAuditPins, TableAuditIssues, TableAuditRuns, TableBlacklist, TableLyricsVersions, TableHistory,
           TableTrackTombstones, TableTracks, TableAlbums, TableArtists, TableLibraryRoots)


def _clean():
    for table in _TABLES:
        database.execute(delete(table))


@pytest.fixture
def env(tmp_path, monkeypatch):
    _clean()
    database.execute(insert(TableArtists).values(lidarrArtistId=1, name='Artist', path=str(tmp_path)))
    album_dir = tmp_path / 'Album [FLAC]'
    album_dir.mkdir()
    database.execute(insert(TableAlbums).values(
        lidarrAlbumId=10, artistId=1, title='Album', path=str(album_dir), cover_status='missing'))
    audio = album_dir / '01 Song.flac'
    audio.write_bytes(b'audio')
    database.execute(insert(TableTracks).values(
        lidarrTrackId=100, albumId=10, artistId=1, title='Song', duration=200, path=str(audio),
        lyrics_status='missing', lyrics_retry_count=0))
    monkeypatch.setattr(lyrics_store, 'detect_language', lambda text: 'en')  # langdetect is optional
    events = []
    import lyrarr.app.event_handler as eh
    monkeypatch.setattr(eh, 'event_stream', lambda type, payload=None: events.append((type, payload)))
    yield {'audio': audio, 'album_dir': album_dir, 'lrc': str(audio)[:-5] + '.lrc', 'events': events,
           'tmp': tmp_path}
    _clean()
    database.remove()


def _track():
    database.remove()
    return database.execute(select(TableTracks).where(TableTracks.lidarrTrackId == 100)).scalars().first()


def _set(**values):
    database.execute(update(TableTracks).where(TableTracks.lidarrTrackId == 100).values(**values))


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _history(action=None):
    q = select(TableHistory).order_by(TableHistory.id)
    if action is not None:
        q = q.where(TableHistory.action == action)
    return database.execute(q).scalars().all()


def _versions():
    return database.execute(
        select(TableLyricsVersions).order_by(TableLyricsVersions.timestamp, TableLyricsVersions.id)
    ).scalars().all()


class TestReconcile:
    # Moved unchanged from tests/test_metadata_worker.py (PLAN §1.10).
    def test_mount_down_keeps_available(self, env):
        database.execute(update(TableTracks).values(lyrics_status='available'))
        os.remove(env['audio'])  # audio gone too: mount down / file moved
        assert lyrics_store.reconcile_track_lyrics(_track()) is False
        assert _track().lyrics_status == 'available'

    def test_lrc_deleted_with_audio_present(self, env):
        database.execute(update(TableTracks).values(lyrics_status='available'))
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        assert _track().lyrics_status == 'missing'


class TestReconcileRules:
    def test_missing_clears_source_and_replace_request(self, env):
        _set(lyrics_status='available', lyrics_source='auto', lyrics_replace_requested=datetime.now(),
             lyrics_retry_count=3)
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        t = _track()
        assert t.lyrics_status == 'missing' and t.lyrics_source is None
        assert t.lyrics_replace_requested is None and t.lyrics_retry_count == 0
        assert t.lyrics_retry_set_at is not None

    def test_marker_file_is_not_lyrics(self, env):
        open(env['lrc'], 'w').write('[au: instrumental]\n')
        _set(lyrics_status='available')
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        assert _track().lyrics_status == 'missing'
        # ...and a missing row with a marker file stays missing.
        assert lyrics_store.reconcile_track_lyrics(_track()) is False

    def test_appeared_file_adopted_user_source_kept(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='missing', lyrics_source='manual')
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        t = _track()
        assert t.lyrics_status == 'available' and t.lyrics_source == 'manual' and t.is_synced is False

    def test_appeared_file_unknown_origin(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='missing', lyrics_source='auto')  # stale: not what lyrarr wrote
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        assert _track().lyrics_source is None

    def test_instrumental_with_real_lyrics_adopted(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='instrumental', instrumental_reason='consensus', title='Interlude')
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        t = _track()
        assert t.lyrics_status == 'available' and t.instrumental_reason is None
        assert _history(6)[-1].description.startswith('Instrumental flag cleared (sidecar-lyrics)')

    def test_instrumental_strong_title_stays(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='instrumental', instrumental_reason='title', title='Song (Instrumental)')
        assert lyrics_store.reconcile_track_lyrics(_track()) is False
        assert _track().lyrics_status == 'instrumental'

    def test_instrumental_override_always_stays(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='instrumental', instrumental_reason='user', instrumental_override='always')
        assert lyrics_store.reconcile_track_lyrics(_track()) is False

    def test_available_strong_title_gets_conflict_entry(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='available', title='Song (Instrumental)', lyrics_source=None)
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        ev = _track().to_dict()['instrumental_evidence']
        assert ev[0]['veto'] == 'sidecar' and ev[0]['source'] == 'title'
        assert lyrics_store.reconcile_track_lyrics(_track()) is False  # idempotent

    def test_blacklisted_untouched(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='blacklisted')
        assert lyrics_store.reconcile_track_lyrics(_track()) is False


# Synthetic Cyrillic placeholder text saved in a legacy codepage: strict UTF-8
# decoding replaces every letter with U+FFFD, which alone would classify 'empty'.
CP1251_LRC = '[00:01.00]первая строка куплета\n[00:05.00]вторая строка куплета\n'.encode('cp1251')


class TestReconcileUnknownContent:
    def test_sidecar_kind(self, env):
        assert lyrics_store.read_sidecar_kind(env['lrc']) == (None, None)
        with open(env['lrc'], 'wb') as f:
            f.write(CP1251_LRC)
        kind, sr = lyrics_store.read_sidecar_kind(env['lrc'])
        assert kind == lyrics_store.SIDECAR_UNKNOWN and sr is not None and not sr.lossless
        assert lyrics_store.sidecar_present(kind)
        with open(env['lrc'], 'w') as f:
            f.write('')
        assert lyrics_store.read_sidecar_kind(env['lrc'])[0] == 'empty'

    def test_lossy_manual_file_stays_available(self, env):
        with open(env['lrc'], 'wb') as f:
            f.write(CP1251_LRC)
        _set(lyrics_status='available', hasLyrics=True, lyrics_source='manual', lyrics_retry_count=2,
             detected_language='ru', language_source='detected')
        for _ in range(2):  # every sync / maintenance / album view
            assert lyrics_store.reconcile_track_lyrics(_track()) is False
        t = _track()
        assert t.lyrics_status == 'available' and t.lyrics_source == 'manual' and t.hasLyrics
        assert t.lyrics_retry_count == 2 and t.detected_language == 'ru'
        with open(env['lrc'], 'rb') as f:
            assert f.read() == CP1251_LRC

    def test_unreadable_file_stays_available(self, env, monkeypatch):
        with open(env['lrc'], 'w') as f:
            f.write(VERSE)
        _set(lyrics_status='available', lyrics_source='auto')
        monkeypatch.setattr(lyrics_store, 'read_sidecar', lambda path: None)  # EACCES / I/O error
        assert lyrics_store.reconcile_track_lyrics(_track()) is False
        assert _track().lyrics_status == 'available' and _track().lyrics_source == 'auto'

    def test_lossy_file_adopted_on_missing_row_without_language(self, env):
        with open(env['lrc'], 'wb') as f:
            f.write(CP1251_LRC)
        _set(lyrics_status='missing', detected_language=None)
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        t = _track()
        assert t.lyrics_status == 'available' and t.hasLyrics and t.is_synced
        assert t.lyrics_source is None and t.detected_language is None

    def test_lossy_file_never_lifts_instrumental(self, env):
        with open(env['lrc'], 'wb') as f:
            f.write(CP1251_LRC)
        _set(lyrics_status='instrumental', instrumental_reason='consensus', title='Interlude')
        assert lyrics_store.reconcile_track_lyrics(_track()) is False
        assert _track().lyrics_status == 'instrumental'
        assert lyrics_store.adopt_sidecar(_track()) is False
        assert _track().lyrics_status == 'instrumental'


class TestReconcileProvenance:
    _PROV = dict(lyrics_provider='lrclib', lyrics_source_id='4242', lyrics_match_score=0.9,
                 lyrics_match_json='{"strategy": "exact_album"}')

    def _assert_cleared(self, t):
        assert t.lyrics_provider is None and t.lyrics_source_id is None
        assert t.lyrics_match_score is None and t.lyrics_match_json is None
        assert t.lyrics_content_hash is None

    def test_deleted_then_user_file_has_no_stale_provenance(self, env):
        with open(env['lrc'], 'w') as f:
            f.write(VERSE)
        _set(lyrics_status='available', lyrics_source='auto',
             lyrics_content_hash=lyrics_store.content_hash(VERSE), **self._PROV)
        os.remove(env['lrc'])
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        t = _track()
        assert t.lyrics_status == 'missing'
        self._assert_cleared(t)
        with open(env['lrc'], 'w') as f:
            f.write(OTHER)  # the user drops in their own file
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        t = _track()
        assert t.lyrics_status == 'available' and t.lyrics_source is None
        self._assert_cleared(t)

    def test_adopt_clears_provenance_of_other_content(self, env):
        # Legacy row: went missing without the provenance being cleared.
        with open(env['lrc'], 'w') as f:
            f.write(OTHER)
        _set(lyrics_status='missing', lyrics_source='auto',
             lyrics_content_hash=lyrics_store.content_hash(VERSE), **self._PROV)
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        t = _track()
        assert t.lyrics_status == 'available' and t.lyrics_source is None
        self._assert_cleared(t)

    def test_adopt_keeps_hash_verified_provenance(self, env):
        with open(env['lrc'], 'w') as f:
            f.write(VERSE)
        h = lyrics_store.content_hash(VERSE)
        _set(lyrics_status='missing', lyrics_source='auto', lyrics_content_hash=h, **self._PROV)
        assert lyrics_store.reconcile_track_lyrics(_track()) is True
        t = _track()
        assert t.lyrics_status == 'available' and t.lyrics_source == 'auto'
        assert t.lyrics_provider == 'lrclib' and t.lyrics_source_id == '4242'
        assert t.lyrics_match_score == 0.9 and t.lyrics_content_hash == h

    def test_clear_instrumental_drops_provenance(self, env):
        _set(lyrics_status='instrumental', instrumental_reason='consensus', **self._PROV)
        assert lyrics_store.clear_instrumental(100, why='test', actor='user') is True
        t = _track()
        assert t.lyrics_status == 'missing'
        self._assert_cleared(t)


class TestPersist:
    def test_writes_status_provenance_and_history(self, env):
        r = lyrics_store.persist_lyrics_result(
            _track(), SYNCED, 'lrclib', match={'source_id': 42, 'score': 0.91, 'strategy': 'exact_album',
                                                'title_score': 100})
        assert r and r.info['is_synced'] is True
        t = _track()
        assert t.lyrics_status == 'available' and t.hasLyrics and t.lyrics_source == 'auto'
        assert t.lyrics_provider == 'lrclib' and t.lyrics_source_id == '42' and t.lyrics_match_score == 0.91
        assert t.to_dict()['lyrics_match_json'] == {'strategy': 'exact_album', 'title_score': 100}
        assert t.lyrics_content_hash == content_hash(SYNCED)
        assert t.language_source == 'detected' and t.detected_language == 'en'
        assert _read(env['lrc']) == SYNCED + '\n'
        h = _history()
        assert [x.action for x in h] == [1] and h[0].provider == 'lrclib'

    def test_resets_retry_and_counters(self, env):
        _set(lyrics_retry_count=4, lyrics_retry_after=datetime.now() + timedelta(days=3),
             lyrics_transient_count=2, lyrics_cooldown_count=1, lyrics_replace_requested=datetime.now())
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'p1')
        t = _track()
        assert (t.lyrics_retry_count, t.lyrics_retry_after, t.lyrics_transient_count, t.lyrics_cooldown_count) \
            == (0, None, 0, 0)
        assert t.lyrics_retry_set_at is not None and t.lyrics_replace_requested is None

    def test_no_audio_refused_and_no_directory(self, env):
        gone = env['tmp'] / 'Unmounted' / '01 Song.flac'
        _set(path=str(gone))
        r = lyrics_store.persist_lyrics_result(_track(), VERSE, 'p1')
        assert not r and r.reason == 'no_audio'
        assert str(gone) in r.message
        assert not (env['tmp'] / 'Unmounted').exists()
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'p1') is None

    def test_not_found_and_empty(self, env):
        assert lyrics_store.persist_lyrics_result(None, VERSE, 'p1').reason == 'not_found'
        assert lyrics_store.persist_lyrics_result(999, VERSE, 'p1').reason == 'not_found'
        r = lyrics_store.persist_lyrics_result(_track(), ' \n ', 'p1')
        assert r.reason == 'empty' and r.message == PERSIST_REASONS['empty']
        assert not os.path.exists(env['lrc'])

    def test_cas_row_changed(self, env):
        snapshot = _track()
        _set(lyrics_status='blacklisted')  # the user skipped it mid-run
        r = lyrics_store.persist_lyrics_result(snapshot, VERSE, 'p1', expect_status=('missing',))
        assert r.reason == 'row_changed'
        assert not os.path.exists(env['lrc']) and _track().lyrics_status == 'blacklisted'

    def test_cas_expect_sources_null(self, env):
        open(env['lrc'], 'w').write(OTHER)
        _set(lyrics_status='available', lyrics_source=None)
        r = lyrics_store.persist_lyrics_result(_track(), VERSE, 'p1', expect_status=('available',),
                                               expect_sources=(None, 'auto'))
        assert r
        _set(lyrics_source='manual')
        r = lyrics_store.persist_lyrics_result(_track(), OTHER, 'p1', expect_status=('available',),
                                               expect_sources=(None, 'auto'))
        assert r.reason == 'row_changed' and 'first line of the verse' in _read(env['lrc'])

    @pytest.mark.parametrize('source,action', [('auto', 1), (None, 1), ('manual', 4), ('translated', 4),
                                                ('generated', 4)])
    def test_history_action_derived(self, env, source, action):
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'p1', source=source)
        assert _history()[-1].action == action

    def test_explicit_history_action(self, env):
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'p1', history_action=2)
        assert _history()[-1].action == 2

    def test_lossy_existing_auto_refused_manual_keeps_orig(self, env):
        raw = 'café placeholder words'.encode('cp1252')
        with open(env['lrc'], 'wb') as f:
            f.write(raw)
        _set(lyrics_status='available')
        r = lyrics_store.persist_lyrics_result(_track(), VERSE, 'p1', source='auto')
        assert r.reason == 'lossy_existing'
        with open(env['lrc'], 'rb') as f:
            assert f.read() == raw
        r = lyrics_store.persist_lyrics_result(_track(), VERSE, 'editor', source='manual')
        assert r
        with open(env['lrc'] + '.orig', 'rb') as f:
            assert f.read() == raw
        assert 'first line of the verse' in _read(env['lrc'])

    def test_translated_sets_language_source(self, env):
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'p1', source='translated', detected_language='de')
        t = _track()
        assert t.language_source == 'translated' and t.detected_language == 'de'


class TestRuleM:
    @pytest.mark.parametrize('state', [
        {'lyrics_status': 'instrumental', 'instrumental_reason': 'consensus'},
        {'instrumental_evidence': json.dumps([{'source': 'lrclib'}])},
        {'instrumental_prev': json.dumps({'why': 'legacy-unverified'})},
        {'instrumental_override': 'always'},
        {'title': 'Song (Instrumental)'},
        {'title': 'Instrumental 2'},
    ])
    def test_manual_save_sets_never(self, env, state):
        _set(**state)
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'editor', source='manual')
        t = _track()
        assert t.instrumental_override == 'never' and t.instrumental_prev is None
        details = _history()[-1].to_dict()['details']
        assert details == {'override_set': 'never', 'why': 'manual-save'}
        # A manual save never leaves a title-conflict entry.
        assert t.instrumental_evidence is None

    def test_plain_title_without_signals_untouched(self, env):
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'editor', source='manual')
        assert _track().instrumental_override is None

    def test_translated_untouched(self, env):
        _set(instrumental_prev=json.dumps({'why': 'legacy-unverified'}))
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'batch-translate-de', source='translated')
        t = _track()
        assert t.instrumental_override is None and t.instrumental_prev is not None


class TestRuleT:
    def test_strong_title_auto_save_records_conflict(self, env):
        _set(title='Song (Instrumental)')
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'embedded', source=None)
        t = _track()
        ev = t.to_dict()['instrumental_evidence']
        assert t.lyrics_status == 'available' and t.instrumental_reason is None
        assert ev[0]['veto'] == 'saved_lyrics' and ev[0]['provider'] == 'embedded'

    def test_strong_title_override_never_no_conflict(self, env):
        _set(title='Song (Instrumental)', instrumental_override='never')
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'p1')
        assert _track().instrumental_evidence is None

    def test_plain_title_clears_hint(self, env):
        _set(instrumental_evidence=json.dumps([{'source': 'netease'}]))
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'p1')
        assert _track().instrumental_evidence is None


class TestArchive:
    def test_attribution_origin_and_replacer(self, env):
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'lrclib')
        assert lyrics_store.persist_lyrics(_track(), OTHER, 'editor', source='manual')
        v = _versions()
        assert len(v) == 1 and v[0].provider == 'lrclib' and v[0].replaced_by == 'editor'
        assert v[0].content == VERSE

    def test_attribution_falls_back_to_history_then_unknown(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='available', lyrics_provider=None)
        database.execute(insert(TableHistory).values(
            action=1, description='Downloaded lyrics for Song', metadata_type='lyrics', provider='netease',
            lidarrTrackId=100, timestamp=datetime.now() - timedelta(days=1)))
        assert lyrics_store.persist_lyrics(_track(), OTHER, 'p1')
        assert _versions()[-1].provider == 'netease'

        database.execute(delete(TableHistory))
        _set(lyrics_provider=None)
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'p2')
        assert _versions()[-1].provider == 'unknown'

    def test_identical_resave_adds_no_version(self, env):
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'p1')
        assert lyrics_store.persist_lyrics(_track(), VERSE, 'p1')
        assert _versions() == []

    def test_pinned_version_survives_six_writes(self, env):
        assert lyrics_store.persist_lyrics(_track(), 'pinned evidence words\nsecond pinned line', 'p0')
        assert lyrics_store.persist_lyrics(_track(), 'write number 0\nmore words', 'p1')
        pinned = _versions()[0]
        database.execute(insert(TableAuditRuns).values(id=1, mode='dry_run', trigger='user', status='done'))
        database.execute(insert(TableAuditIssues).values(id=1, run_id=1, check_id='history.curated_superseded',
                                                         entity_key='track:100', status='open'))
        database.execute(insert(TableAuditPins).values(issue_id=1, kind='version', ref_id=pinned.id))
        for i in range(1, 7):
            assert lyrics_store.persist_lyrics(_track(), f'write number {i}\nmore words', 'p1')
        ids = [v.id for v in _versions()]
        assert pinned.id in ids
        assert len(ids) == lyrics_store._MAX_VERSIONS_PER_TRACK + 1
        assert lyrics_store.pinned_version_ids(100) == {pinned.id}
        # Once the issue is resolved the pin no longer protects it.
        database.execute(update(TableAuditIssues).values(status='fixed'))
        assert lyrics_store.pinned_version_ids(100) == set()

    def test_archive_and_remove_with_blacklist(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='available', lyrics_provider='genius')
        assert lyrics_store.archive_and_remove(_track(), 'audit:I-6', blacklist_reason='I-6')
        assert not os.path.exists(env['lrc'])
        v = _versions()[-1]
        assert v.provider == 'genius' and v.replaced_by == 'audit:I-6'
        bl = database.execute(select(TableBlacklist)).scalars().all()
        assert [(b.provider, b.content_hash) for b in bl] == [('audit:I-6', content_hash(VERSE))]
        assert lyrics_store.text_is_blacklisted(100, VERSE)

    def test_archive_failure_keeps_file(self, env, monkeypatch):
        open(env['lrc'], 'w').write(VERSE)
        monkeypatch.setattr(lyrics_store, '_archive_existing', lambda *a, **k: False)
        assert lyrics_store.archive_and_remove(_track(), 'x') is False
        assert os.path.exists(env['lrc'])
        r = lyrics_store.persist_lyrics_result(_track(), OTHER, 'p1')
        assert r.reason == 'archive_failed' and _read(env['lrc']) == VERSE


class TestInstrumentalHelpers:
    def test_set_instrumental_writes_reason_evidence_history(self, env):
        votes = [{'source': 'lrclib', 'anchor': True}, {'source': 'netease', 'anchor': True}]
        _set(lyrics_retry_count=2, lyrics_transient_count=1)
        assert lyrics_store.set_instrumental(100, 'consensus', votes)
        t = _track()
        assert t.lyrics_status == 'instrumental' and t.instrumental_reason == 'consensus'
        assert t.to_dict()['instrumental_evidence'] == votes
        assert t.instrumental_checked_at is not None and t.instrumental_override is None
        assert t.lyrics_retry_count == 0 and t.lyrics_transient_count == 0 and t.lyrics_retry_set_at
        h = _history(6)
        assert len(h) == 1 and h[0].to_dict()['details']['reason'] == 'consensus'
        assert [e[0] for e in env['events']] == ['instrumental_classified']
        assert env['events'][0][1]['action'] == 'flagged'

    def test_set_instrumental_cas_and_silent(self, env):
        _set(lyrics_status='available')
        assert lyrics_store.set_instrumental(100, 'title', [], emit_event=False) is False
        assert lyrics_store.set_instrumental(100, 'user', [], expect_status=('available',), emit_event=False)
        t = _track()
        assert t.instrumental_override == 'always' and env['events'] == []
        assert len(_history(6)) == 1

    @pytest.mark.parametrize('actor', ['bootstrap', 'audit'])
    def test_clear_by_non_user_records_prev(self, env, actor):
        _set(lyrics_status='instrumental', instrumental_reason=None,
             instrumental_evidence=json.dumps([{'source': 'x'}]), instrumental_override='never')
        retry = datetime.now() + timedelta(hours=5)
        assert lyrics_store.clear_instrumental(100, why='legacy-unverified', actor=actor, retry_after=retry,
                                               emit_event=False)
        t = _track()
        assert t.lyrics_status == 'missing' and t.instrumental_reason is None and t.instrumental_evidence is None
        assert t.instrumental_override == 'never'  # untouched when not passed
        prev = t.to_dict()['instrumental_prev']
        assert prev['why'] == 'legacy-unverified' and prev['actor'] == actor and prev['evidence_count'] == 1
        assert abs((t.lyrics_retry_after - retry).total_seconds()) < 1
        assert env['events'] == [] and _history(6)[-1].description.startswith('Instrumental flag cleared')

    def test_clear_by_user_nulls_prev_and_writes_override(self, env):
        _set(lyrics_status='instrumental', instrumental_reason='title',
             instrumental_prev=json.dumps({'why': 'old'}))
        assert lyrics_store.clear_instrumental(100, why='user-not-instrumental', actor='user', override='never')
        t = _track()
        assert t.instrumental_prev is None and t.instrumental_override == 'never'
        assert env['events'][-1][1]['action'] == 'cleared'

    def test_clear_override_none_is_written(self, env):
        _set(lyrics_status='instrumental', instrumental_override='always')
        assert lyrics_store.clear_instrumental(100, why='x', actor='user', override=None)
        assert _track().instrumental_override is None

    def test_hint_and_dismiss(self, env):
        assert lyrics_store.record_instrumental_hint(100, [{'source': 'lrclib'}], emit_event=False)
        t = _track()
        assert t.lyrics_status == 'missing' and t.to_dict()['instrumental_evidence'] == [{'source': 'lrclib'}]
        assert _history(6)[-1].description.startswith('Likely instrumental (1 vote)')
        _set(instrumental_prev=json.dumps({'why': 'legacy-unverified'}))
        assert lyrics_store.dismiss_cleared([100, 555]) == 1
        assert _track().instrumental_prev is None

    def test_adopt_sidecar(self, env):
        open(env['lrc'], 'w').write('[00:01.00]first line of the verse\n[00:03.00]second line')
        _set(lyrics_status='missing', lyrics_retry_count=3)
        assert lyrics_store.adopt_sidecar(_track())
        t = _track()
        assert t.lyrics_status == 'available' and t.is_synced and t.lyrics_retry_count == 0

    def test_adopt_sidecar_refuses(self, env):
        open(env['lrc'], 'w').write('[au: instrumental]')
        assert lyrics_store.adopt_sidecar(_track()) is False
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='blacklisted')
        assert lyrics_store.adopt_sidecar(_track()) is False
        _set(lyrics_status='instrumental', instrumental_override='always')
        assert lyrics_store.adopt_sidecar(_track()) is False

    def test_adopt_strong_title_conflict(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(title='Song (Off Vocal)', lyrics_source='auto')
        assert lyrics_store.adopt_sidecar(_track(), keep_source=False)
        t = _track()
        assert t.lyrics_source is None
        assert t.to_dict()['instrumental_evidence'][0]['veto'] == 'sidecar'


class TestReplaceRequests:
    def test_request_replace_stores_retry_after(self, env):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='available', lyrics_source='auto', lyrics_retry_count=2)
        later = datetime.now() + timedelta(hours=7)
        assert lyrics_store.request_replace(100, 'M-1', retry_after=later)
        t = _track()
        assert t.lyrics_replace_requested is not None and t.lyrics_status == 'available'
        assert abs((t.lyrics_retry_after - later).total_seconds()) < 1 and t.lyrics_retry_count == 0
        assert t.lyrics_retry_set_at is not None
        assert _history(7)[-1].to_dict()['details']['reason'] == 'M-1'
        lyrics_store.clear_replace_request(100, result='kept', details={'reason': 'nothing better'})
        assert _track().lyrics_replace_requested is None
        assert _history(7)[-1].to_dict()['details'] == {'replace_result': 'kept', 'reason': 'nothing better'}

    @pytest.mark.parametrize('state', [{'lyrics_status': 'missing'},
                                       {'lyrics_status': 'available', 'lyrics_source': 'manual'}])
    def test_request_replace_refused(self, env, state):
        _set(**state)
        assert lyrics_store.request_replace(100, 'M-1') is False

    def test_stagger_until(self):
        now = datetime.now()
        for n, hours in ((1, 6), (150, 12), (5000, 72)):
            t = lyrics_store.stagger_until(n, now=now)
            assert now < t <= now + timedelta(hours=hours)


class TestBackfillContentHash:
    def _auto_file(self, env, mtime_offset=0):
        open(env['lrc'], 'w').write(VERSE)
        _set(lyrics_status='available', lyrics_source='auto', lyrics_content_hash=None)
        if mtime_offset:
            st = os.stat(env['lrc'])
            os.utime(env['lrc'], (st.st_atime, st.st_mtime + mtime_offset))

    def test_set_when_history_matches(self, env):
        self._auto_file(env)
        database.execute(insert(TableHistory).values(
            action=1, description='d', metadata_type='lyrics', provider='p1', lidarrTrackId=100,
            timestamp=datetime.now(), metadata_path=env['lrc']))
        assert lyrics_store.backfill_content_hash(_track()) == 'set'
        assert _track().lyrics_content_hash == content_hash(VERSE)
        assert lyrics_store.backfill_content_hash(_track()) == 'unchanged'

    def test_downgraded_without_history(self, env):
        self._auto_file(env)
        assert lyrics_store.backfill_content_hash(_track()) == 'downgraded'
        assert _track().lyrics_source is None

    def test_downgraded_when_file_newer_than_history(self, env):
        self._auto_file(env, mtime_offset=3600)
        database.execute(insert(TableHistory).values(
            action=1, description='d', metadata_type='lyrics', provider='p1', lidarrTrackId=100,
            timestamp=datetime.now(), metadata_path=env['lrc']))
        assert lyrics_store.backfill_content_hash(_track()) == 'downgraded'


class TestBlacklistHelpers:
    def test_text_is_blacklisted_via_repaired_hash(self, env):
        raw = '[00:12.00][01:20.00]la la placeholder\n[00:15.00]first line of the verse'
        from lyrarr.metadata.lrc_repair import repair_lrc
        lyrics_store.blacklist_content(100, repair_lrc(raw), reason='F-5')
        assert lyrics_store.text_is_blacklisted(100, raw)
        assert database.execute(select(TableBlacklist.provider)).scalar() == 'audit:F-5'
        assert not lyrics_store.text_is_blacklisted(100, OTHER)

    def test_blacklist_content_keeps_provider_argument(self, env):
        assert lyrics_store.blacklist_content(100, VERSE, 'lrclib')
        assert database.execute(select(TableBlacklist.provider)).scalar() == 'lrclib'

    def test_text_is_blacklisted_via_legacy_hash(self, env, monkeypatch):
        from lyrarr.metadata import merge
        monkeypatch.setattr(merge, 'legacy_content_hash', lambda text: 'legacy-hash', raising=False)
        database.execute(insert(TableBlacklist).values(metadata_type='lyrics', content_hash='legacy-hash',
                                                       lidarrTrackId=100))
        assert lyrics_store.text_is_blacklisted(100, VERSE)
