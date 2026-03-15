# coding=utf-8

"""
Embed cover art into audio files using mutagen.
Supports MP3 (ID3 APIC), FLAC (Picture), and M4A (covr).
"""

import logging
import os
import glob

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = ('.mp3', '.flac', '.ogg', '.m4a', '.mp4', '.aac', '.wma')


def _get_mime_type(image_format):
    """Get MIME type from image format string."""
    fmt = image_format.lower()
    if fmt in ('jpg', 'jpeg'):
        return 'image/jpeg'
    elif fmt == 'png':
        return 'image/png'
    elif fmt == 'webp':
        return 'image/webp'
    return 'image/jpeg'


def embed_cover_in_files(album_path, image_data, image_format='jpg'):
    """
    Embed cover art into all audio files in the given album directory.
    
    Args:
        album_path: Path to the album directory
        image_data: Raw image bytes
        image_format: Image format string (jpg, png, etc.)
    
    Returns:
        Number of files successfully updated
    """
    if not album_path or not os.path.isdir(album_path):
        logger.warning(f"Album path not found: {album_path}")
        return 0

    if not image_data:
        logger.warning("No image data provided for embedding")
        return 0

    mime_type = _get_mime_type(image_format)
    updated = 0

    # Find all audio files in album directory
    audio_files = []
    for ext in AUDIO_EXTENSIONS:
        audio_files.extend(glob.glob(os.path.join(album_path, f'*{ext}')))
        audio_files.extend(glob.glob(os.path.join(album_path, f'*{ext.upper()}')))

    for filepath in sorted(set(audio_files)):
        try:
            ext = os.path.splitext(filepath)[1].lower()

            if ext == '.mp3':
                updated += _embed_mp3(filepath, image_data, mime_type)
            elif ext == '.flac':
                updated += _embed_flac(filepath, image_data, mime_type)
            elif ext in ('.m4a', '.mp4', '.aac'):
                updated += _embed_m4a(filepath, image_data, image_format)
            elif ext == '.ogg':
                updated += _embed_ogg(filepath, image_data, mime_type)
            else:
                logger.debug(f"Unsupported format for embedding: {ext}")

        except Exception as e:
            logger.error(f"Error embedding cover in '{os.path.basename(filepath)}': {e}")

    if updated:
        logger.info(f"Embedded cover art in {updated} files in {album_path}")
    return updated


def _embed_mp3(filepath, image_data, mime_type):
    """Embed cover art in MP3 file using ID3 APIC tag."""
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, APIC, ID3NoHeaderError

    try:
        audio = MP3(filepath, ID3=ID3)
    except ID3NoHeaderError:
        audio = MP3(filepath)
        audio.add_tags()

    # Remove existing cover art
    audio.tags.delall('APIC')

    # Add new cover art
    audio.tags.add(
        APIC(
            encoding=3,  # UTF-8
            mime=mime_type,
            type=3,  # Front cover
            desc='Cover',
            data=image_data,
        )
    )
    audio.save()
    logger.debug(f"Embedded MP3 cover: {os.path.basename(filepath)}")
    return 1


def _embed_flac(filepath, image_data, mime_type):
    """Embed cover art in FLAC file."""
    from mutagen.flac import FLAC, Picture

    audio = FLAC(filepath)

    # Clear existing pictures
    audio.clear_pictures()

    # Add new picture
    pic = Picture()
    pic.type = 3  # Front cover
    pic.mime = mime_type
    pic.desc = 'Cover'
    pic.data = image_data
    audio.add_picture(pic)
    audio.save()
    logger.debug(f"Embedded FLAC cover: {os.path.basename(filepath)}")
    return 1


def _embed_m4a(filepath, image_data, image_format):
    """Embed cover art in M4A/MP4 file."""
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(filepath)

    fmt = image_format.lower()
    if fmt in ('png',):
        img_format = MP4Cover.FORMAT_PNG
    else:
        img_format = MP4Cover.FORMAT_JPEG

    audio['covr'] = [MP4Cover(image_data, imageformat=img_format)]
    audio.save()
    logger.debug(f"Embedded M4A cover: {os.path.basename(filepath)}")
    return 1


def _embed_ogg(filepath, image_data, mime_type):
    """Embed cover art in OGG Vorbis file."""
    import base64
    from mutagen.oggvorbis import OggVorbis
    from mutagen.flac import Picture

    audio = OggVorbis(filepath)

    pic = Picture()
    pic.type = 3
    pic.mime = mime_type
    pic.desc = 'Cover'
    pic.data = image_data

    # OGG uses base64-encoded FLAC picture block in metadata
    audio['metadata_block_picture'] = [base64.b64encode(pic.write()).decode('ascii')]
    audio.save()
    logger.debug(f"Embedded OGG cover: {os.path.basename(filepath)}")
    return 1
