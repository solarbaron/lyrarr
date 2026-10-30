
"""
Embed cover art into audio files using mutagen.
Supports MP3 (ID3 APIC), FLAC (Picture), M4A/MP4 (covr) and Ogg
(Vorbis, Opus, FLAC-in-Ogg via METADATA_BLOCK_PICTURE).

Only the FRONT cover is touched: other embedded pictures (back cover, artist,
booklet scans) are kept, and an existing front cover is only replaced when
overwrite is requested.
"""

import base64
import logging
import os

from lyrarr.metadata.cover_fetch import sniff_image

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = ('.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.mp4', '.aac', '.wma')

_FRONT_COVER = 3  # ID3/FLAC picture type "Cover (front)"


def _get_mime_type(image_format):
    """Get MIME type from image format string."""
    fmt = (image_format or '').lower()
    if fmt in ('jpg', 'jpeg'):
        return 'image/jpeg'
    elif fmt == 'png':
        return 'image/png'
    elif fmt == 'webp':
        return 'image/webp'
    elif fmt == 'gif':
        return 'image/gif'
    return 'image/jpeg'


def _audio_files(album_path, track_paths):
    """Audio files to embed into: the tracks' own files when known (multi-disc
    albums live in several folders), else the files directly in album_path.

    os.scandir rather than glob, so folder names with glob metacharacters
    ("Album (2011) [FLAC]") work.
    """
    if track_paths:
        files = [p for p in track_paths if p and os.path.isfile(p)
                 and os.path.splitext(p)[1].lower() in AUDIO_EXTENSIONS]
        return sorted(set(files))

    if not album_path or not os.path.isdir(album_path):
        logger.warning(f"Album path not found: {album_path}")
        return []
    files = []
    with os.scandir(album_path) as it:
        for entry in it:
            if entry.is_file() and os.path.splitext(entry.name)[1].lower() in AUDIO_EXTENSIONS:
                files.append(entry.path)
    return sorted(files)


def embed_cover_in_files(album_path, image_data, image_format=None, *, track_paths=None, overwrite=True):
    """
    Embed cover art as the front cover of an album's audio files.

    Args:
        album_path: Path to the album directory (used when track_paths is not given)
        image_data: Raw image bytes
        image_format: Fallback image format (jpg, png, ...) — the real type is
            sniffed from the bytes when possible
        track_paths: The album's track file paths; preferred over scanning
            album_path so every disc folder is covered
        overwrite: Replace an existing embedded front cover. When False, files
            that already have one are left alone.

    Returns:
        Number of files successfully updated
    """
    if not image_data:
        logger.warning("No image data provided for embedding")
        return 0

    sniffed = sniff_image(image_data)
    mime_type = sniffed[1] if sniffed else _get_mime_type(image_format)
    updated = 0

    for filepath in _audio_files(album_path, track_paths):
        try:
            updated += _embed_file(filepath, image_data, mime_type, overwrite)
        except Exception as e:
            logger.error(f"Error embedding cover in '{os.path.basename(filepath)}': {e}")

    if updated:
        logger.info(f"Embedded cover art in {updated} files for {album_path}")
    return updated


def _embed_file(filepath, image_data, mime_type, overwrite):
    """Embed into one file, dispatching on the actual container (not the extension)."""
    import mutagen
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4

    audio = mutagen.File(filepath)
    if audio is None:
        logger.debug(f"Unrecognised audio file, not embedding: {os.path.basename(filepath)}")
        return 0

    if isinstance(audio, MP3):
        return _embed_id3(audio, filepath, image_data, mime_type, overwrite)
    if isinstance(audio, FLAC):
        return _embed_flac(audio, filepath, image_data, mime_type, overwrite)
    if isinstance(audio, MP4):
        return _embed_m4a(audio, filepath, image_data, mime_type, overwrite)

    from mutagen.ogg import OggFileType
    if isinstance(audio, OggFileType):
        return _embed_ogg(audio, filepath, image_data, mime_type, overwrite)

    logger.debug(f"Unsupported format for embedding: {type(audio).__name__} ({os.path.basename(filepath)})")
    return 0


def _embed_id3(audio, filepath, image_data, mime_type, overwrite):
    """Embed the front cover in an MP3 file's ID3 tag (APIC type 3)."""
    from mutagen.id3 import APIC

    if audio.tags is None:
        audio.add_tags()

    fronts = [k for k, frame in audio.tags.items() if k.startswith('APIC') and frame.type == _FRONT_COVER]
    if fronts and not overwrite:
        logger.debug(f"MP3 already has a front cover, keeping it: {os.path.basename(filepath)}")
        return 0
    for key in fronts:
        del audio.tags[key]

    audio.tags.add(
        APIC(
            encoding=3,  # UTF-8
            mime=mime_type,
            type=_FRONT_COVER,
            desc='Cover',
            data=image_data,
        )
    )
    audio.save()
    logger.debug(f"Embedded MP3 cover: {os.path.basename(filepath)}")
    return 1


def _new_picture(image_data, mime_type):
    from mutagen.flac import Picture

    pic = Picture()
    pic.type = _FRONT_COVER
    pic.mime = mime_type
    pic.desc = 'Cover'
    pic.data = image_data
    return pic


def _embed_flac(audio, filepath, image_data, mime_type, overwrite):
    """Embed the front cover in a FLAC file, keeping its other pictures."""
    pictures = list(audio.pictures)
    if any(p.type == _FRONT_COVER for p in pictures) and not overwrite:
        logger.debug(f"FLAC already has a front cover, keeping it: {os.path.basename(filepath)}")
        return 0

    audio.clear_pictures()
    for pic in pictures:
        if pic.type != _FRONT_COVER:
            audio.add_picture(pic)
    audio.add_picture(_new_picture(image_data, mime_type))
    audio.save()
    logger.debug(f"Embedded FLAC cover: {os.path.basename(filepath)}")
    return 1


def _embed_m4a(audio, filepath, image_data, mime_type, overwrite):
    """Embed the cover in an M4A/MP4 file (covr[0] is the front cover)."""
    from mutagen.mp4 import MP4Cover

    if audio.tags is None:
        audio.add_tags()
    existing = list(audio.tags.get('covr', []))
    if existing and not overwrite:
        logger.debug(f"M4A already has a cover, keeping it: {os.path.basename(filepath)}")
        return 0

    # MP4 covers only support JPEG/PNG flags.
    img_format = MP4Cover.FORMAT_PNG if mime_type == 'image/png' else MP4Cover.FORMAT_JPEG
    audio.tags['covr'] = [MP4Cover(image_data, imageformat=img_format)] + existing[1:]
    audio.save()
    logger.debug(f"Embedded M4A cover: {os.path.basename(filepath)}")
    return 1


def _embed_ogg(audio, filepath, image_data, mime_type, overwrite):
    """Embed the front cover in an Ogg file (Vorbis, Opus, FLAC-in-Ogg).

    Vorbis comments carry pictures as base64 FLAC picture blocks in
    METADATA_BLOCK_PICTURE; non-front pictures are kept.
    """
    from mutagen.flac import Picture

    if audio.tags is None:
        audio.add_tags()

    kept = []
    has_front = False
    for value in audio.tags.get('metadata_block_picture', []):
        try:
            pic = Picture(base64.b64decode(value))
        except Exception:
            kept.append(value)  # unparseable — leave it as it was
            continue
        if pic.type == _FRONT_COVER:
            has_front = True
        else:
            kept.append(value)
    if has_front and not overwrite:
        logger.debug(f"Ogg file already has a front cover, keeping it: {os.path.basename(filepath)}")
        return 0

    new_pic = base64.b64encode(_new_picture(image_data, mime_type).write()).decode('ascii')
    audio.tags['metadata_block_picture'] = kept + [new_pic]
    audio.save()
    logger.debug(f"Embedded Ogg cover: {os.path.basename(filepath)}")
    return 1
