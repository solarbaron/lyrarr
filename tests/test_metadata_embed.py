"""Regression tests for cover embedding on real (tiny, hand-built) audio files.

Covers: album folders with glob metacharacters ("[FLAC]"), Opus files (were
skipped) and Ogg containers opened by their real type, multi-disc albums via
track paths, front-cover-only replacement that keeps other pictures, and the
no-overwrite mode that leaves an existing front cover alone.
"""

import base64
import io
import struct

import mutagen
import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC
from mutagen.mp3 import MP3
from mutagen.ogg import OggPage

from lyrarr.metadata.embed import embed_cover_in_files, read_embedded_artist, read_embedded_lyrics

JPEG = b'\xff\xd8\xff\xe0' + b'jpegdata' * 8
PNG = b'\x89PNG\r\n\x1a\n' + b'pngdata' * 8


def _flac():
    # fLaC + a lone (last) STREAMINFO block: 44.1 kHz, stereo, 16-bit, 0 samples
    info = struct.pack('>HH', 4096, 4096) + b'\x00' * 6
    info += ((44100 << 44) | (1 << 41) | (15 << 36)).to_bytes(8, 'big') + b'\x00' * 16
    return b'fLaC' + b'\x80' + len(info).to_bytes(3, 'big') + info


def _mp3():
    return (b'\xff\xfb\x90\x00' + b'\x00' * 413) * 10  # MPEG1 Layer III frames


def _opus():
    head = b'OpusHead' + bytes([1, 2]) + struct.pack('<HIhB', 312, 48000, 0, 0)
    tags = b'OpusTags' + struct.pack('<I', 6) + b'lyrarr' + struct.pack('<I', 0)
    out = io.BytesIO()
    for seq, packet, pos in ((0, head, 0), (1, tags, 0), (2, b'\xf8\xff\xfe', 960)):
        page = OggPage()
        page.serial, page.sequence, page.packets, page.position = 1, seq, [packet], pos
        page.first, page.last = seq == 0, seq == 2
        out.write(page.write())
    return out.getvalue()


@pytest.fixture
def album(tmp_path):
    folder = tmp_path / 'Artist' / 'Album (2011) [FLAC]'
    folder.mkdir(parents=True)
    files = {
        'flac': folder / '01 - One.flac',
        'mp3': folder / '02 - Two.mp3',
        'opus': folder / '03 - Three.opus',
    }
    files['flac'].write_bytes(_flac())
    files['mp3'].write_bytes(_mp3())
    files['opus'].write_bytes(_opus())
    return folder, files


def _ogg_pictures(path):
    audio = mutagen.File(path)
    return [Picture(base64.b64decode(v)) for v in audio.tags.get('metadata_block_picture', [])]


def test_bracketed_folder_and_all_formats(album):
    folder, files = album
    assert embed_cover_in_files(str(folder), PNG, 'jpg') == 3

    flac = FLAC(files['flac'])
    assert [(p.type, p.mime, p.data) for p in flac.pictures] == [(3, 'image/png', PNG)]
    apic = MP3(files['mp3']).tags.getall('APIC')
    assert [(a.type, a.mime, a.data) for a in apic] == [(3, 'image/png', PNG)]
    pics = _ogg_pictures(files['opus'])
    assert [(p.type, p.mime) for p in pics] == [(3, 'image/png')]


def test_multi_disc_via_track_paths(tmp_path):
    disc1 = tmp_path / 'Album' / 'CD1'
    disc2 = tmp_path / 'Album' / 'CD2'
    disc1.mkdir(parents=True)
    disc2.mkdir(parents=True)
    t1, t2 = disc1 / '01.flac', disc2 / '01.flac'
    t1.write_bytes(_flac())
    t2.write_bytes(_flac())
    n = embed_cover_in_files(str(disc1), JPEG, track_paths=[str(t1), str(t2), str(tmp_path / 'gone.flac')])
    assert n == 2
    assert FLAC(t2).pictures[0].data == JPEG


def test_keeps_other_pictures_and_existing_front_without_overwrite(album):
    folder, files = album
    flac = FLAC(files['flac'])
    back = Picture()
    back.type, back.mime, back.data = 4, 'image/jpeg', b'\xff\xd8\xffback'
    flac.add_picture(back)
    flac.save()
    mp3 = MP3(files['mp3'])
    mp3.add_tags()
    mp3.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='old', data=b'\xff\xd8\xffold'))
    mp3.save()

    # No overwrite: the FLAC (no front yet) gets one, the MP3's front is kept.
    embed_cover_in_files(str(folder), JPEG, track_paths=[str(files['flac']), str(files['mp3'])], overwrite=False)
    assert sorted(p.type for p in FLAC(files['flac']).pictures) == [3, 4]
    assert MP3(files['mp3']).tags.getall('APIC')[0].data == b'\xff\xd8\xffold'

    # Overwrite: only the front cover is replaced; the back cover survives.
    embed_cover_in_files(str(folder), PNG, track_paths=[str(files['flac']), str(files['mp3'])], overwrite=True)
    pics = {p.type: p.data for p in FLAC(files['flac']).pictures}
    assert pics == {3: PNG, 4: b'\xff\xd8\xffback'}
    assert [a.data for a in MP3(files['mp3']).tags.getall('APIC')] == [PNG]


# ---------------------------------------------------------------------------
# Reading embedded lyrics / artist (synthetic text only)
# ---------------------------------------------------------------------------

LYRICS = "first placeholder line\nsecond placeholder line\nla la placeholder"


def _m4a():
    def box(t, data):
        return struct.pack('>I', 8 + len(data)) + t + data
    ftyp = box(b'ftyp', b'M4A \x00\x00\x00\x00M4A mp42isom')
    mvhd = box(b'mvhd', b'\x00' * 12 + struct.pack('>II', 1000, 0) + b'\x00\x01\x00\x00\x01\x00'
               + b'\x00' * 70 + struct.pack('>I', 2))
    return ftyp + box(b'moov', mvhd)


class TestReadEmbedded:
    def test_mp3_uslt_and_artist(self, tmp_path):
        from mutagen.id3 import TPE1, USLT
        path = tmp_path / 'a.mp3'
        path.write_bytes(_mp3())
        mp3 = MP3(path)
        mp3.add_tags()
        mp3.tags.add(USLT(encoding=3, lang='eng', desc='', text=LYRICS))
        mp3.tags.add(TPE1(encoding=3, text=['Guest Singer']))
        mp3.save()
        e = read_embedded_lyrics(str(path))
        assert e.text == LYRICS and e.synced is False and e.source_tag == 'USLT'
        assert read_embedded_artist(str(path)) == 'Guest Singer'

    def test_mp3_sylt_as_lrc(self, tmp_path):
        from mutagen.id3 import SYLT
        path = tmp_path / 'b.mp3'
        path.write_bytes(_mp3())
        mp3 = MP3(path)
        mp3.add_tags()
        mp3.tags.add(SYLT(encoding=3, lang='eng', format=2, type=1, desc='',
                          text=[('first placeholder line', 1500), ('second placeholder line', 61230)]))
        mp3.save()
        e = read_embedded_lyrics(str(path))
        assert e.source_tag == 'SYLT' and e.synced is True
        assert e.text.splitlines() == ['[00:01.50] first placeholder line', '[01:01.23] second placeholder line']

    @pytest.mark.parametrize('key', ['LYRICS', 'UNSYNCEDLYRICS', 'lyrics'])
    def test_flac_vorbis_lyrics(self, tmp_path, key):
        path = tmp_path / 'c.flac'
        path.write_bytes(_flac())
        flac = FLAC(path)
        flac[key] = LYRICS
        flac['ARTIST'] = 'Tag Artist'
        flac.save()
        e = read_embedded_lyrics(str(path))
        assert e.text == LYRICS and e.source_tag.upper() == key.upper()
        assert read_embedded_artist(str(path)) == 'Tag Artist'

    def test_flac_synced_lyrics_detected(self, tmp_path):
        path = tmp_path / 'd.flac'
        path.write_bytes(_flac())
        flac = FLAC(path)
        flac['LYRICS'] = "[00:01.00]first placeholder line\n[00:04.00]second placeholder line"
        flac.save()
        assert read_embedded_lyrics(str(path)).synced is True

    def test_m4a_lyr(self, tmp_path):
        from mutagen.mp4 import MP4
        path = tmp_path / 'e.m4a'
        path.write_bytes(_m4a())
        m4a = MP4(path)
        m4a.add_tags()
        m4a.tags['\xa9lyr'] = [LYRICS]
        m4a.tags['\xa9ART'] = ['M4A Artist']
        m4a.save()
        e = read_embedded_lyrics(str(path))
        assert e.text == LYRICS and e.source_tag == '\xa9lyr'
        assert read_embedded_artist(str(path)) == 'M4A Artist'

    def test_opus_lyrics(self, album):
        _folder, files = album
        audio = mutagen.File(files['opus'])
        audio['LYRICS'] = LYRICS
        audio.save()
        assert read_embedded_lyrics(str(files['opus'])).text == LYRICS

    def test_no_tags_missing_and_garbage_never_raise(self, tmp_path):
        plain = tmp_path / 'f.flac'
        plain.write_bytes(_flac())
        assert read_embedded_lyrics(str(plain)) is None
        assert read_embedded_lyrics(str(tmp_path / 'missing.mp3')) is None
        junk = tmp_path / 'junk.mp3'
        junk.write_bytes(b'not audio at all')
        assert read_embedded_lyrics(str(junk)) is None
        assert read_embedded_artist(str(junk)) is None
        assert read_embedded_lyrics(None) is None
