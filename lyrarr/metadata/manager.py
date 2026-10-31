
import logging
import os
from datetime import datetime

from lyrarr.app.config import settings
from lyrarr.app.database import TableAlbums, TableHistory, TableProfiles, TableTracks, database, select, update
from lyrarr.metadata.embed import embed_cover_in_files
from lyrarr.metadata.registry import cover_providers, lyrics_providers

logger = logging.getLogger(__name__)


def search_cover_art(album):
    """Search for cover art for an album using configured providers."""
    if not settings.metadata.covers.enabled:
        return []

    results = []
    enabled_providers = settings.metadata.covers.providers

    for provider_name in enabled_providers:
        provider = cover_providers.get(provider_name)
        if not provider:
            continue

        try:
            # Build kwargs based on what this provider accepts
            kwargs = {}
            if provider_name == 'musicbrainz':
                kwargs['mb_release_group_id'] = album.get('mbId')
            elif provider_name == 'fanart':
                kwargs['mb_artist_id'] = album.get('artistMbId')
                kwargs['mb_album_id'] = album.get('mbId')
            else:
                # Deezer, iTunes, TheAudioDB use artist + album name search
                kwargs['artist_name'] = album.get('artistName')
                kwargs['album_name'] = album.get('title')

            provider_results = provider.search(**kwargs)
            results.extend(provider_results)
        except Exception as e:
            logger.error(f"Cover art search error ({provider_name}): {e}")

    return results


def search_lyrics(track):
    """Search for lyrics for a track using configured providers."""
    if not settings.metadata.lyrics.enabled:
        return []

    results = []
    enabled_providers = settings.metadata.lyrics.providers

    for provider_name in enabled_providers:
        provider = lyrics_providers.get(provider_name)
        if not provider:
            continue

        try:
            provider_results = provider.search(
                track_name=track.get('title'),
                artist_name=track.get('artistName'),
                album_name=track.get('albumTitle'),
                duration=track.get('duration'),
                mb_recording_id=track.get('mbId'),
            )
            results.extend(provider_results)
        except Exception as e:
            logger.error(f"Lyrics search error ({provider_name}): {e}")

    # Sort by score (highest first)
    results.sort(key=lambda x: x.get('score', 0), reverse=True)

    # If prefer_synced is enabled, prioritize results with synced lyrics
    if settings.metadata.lyrics.prefer_synced:
        synced = [r for r in results if r.get('synced_lyrics')]
        plain = [r for r in results if not r.get('synced_lyrics')]
        results = synced + plain

    return results


def album_track_paths(album_id):
    """File paths of an album's tracks (for embedding into every disc folder)."""
    rows = database.execute(
        select(TableTracks.path).where(TableTracks.albumId == album_id)
    ).scalars().all()
    return [p for p in rows if p]


def save_cover_art(album_id, image_data, provider_name):
    """Save cover art for an album (manual pick — replaces the embedded front cover).

    Returns (ok, reason). reason is a cover_fetch.COVER_REASONS key
    ('no_path', 'album_dir_missing', 'artist_root'), 'not_found',
    'not_image' or 'write_failed'. The album folder must already exist: no
    directory is ever created (an unmounted library must not grow ghost dirs).
    """
    from lyrarr.metadata.cover_fetch import album_dir_writable, sniff_image
    from lyrarr.metadata.lyrics_store import atomic_write_bytes

    album = database.execute(
        select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
    ).scalars().first()
    if not album:
        logger.error(f"Album {album_id} not found")
        return False, 'not_found'

    ok, reason = album_dir_writable(album)
    if not ok:
        logger.warning(f"Not saving cover art for album {album_id}: {reason} ({album.path})")
        return False, reason

    sniffed = sniff_image(image_data)
    if not sniffed:
        logger.error(f"Cover art for album {album_id} is not a JPEG/PNG/WebP/GIF image")
        return False, 'not_image'

    filename = settings.metadata.covers.folder_art_filename
    # Extension from the actual bytes, so a PNG isn't saved as .jpg
    filepath = os.path.join(album.path, f"{filename}.{sniffed[0]}")
    try:
        atomic_write_bytes(filepath, image_data)
    except OSError as e:
        logger.error(f"Error saving cover art for album {album_id}: {e}")
        return False, 'write_failed'

    database.execute(
        update(TableAlbums)
        .where(TableAlbums.lidarrAlbumId == album_id)
        .values(cover_status='available', updated_at_timestamp=datetime.now())
    )
    from sqlalchemy.dialects.sqlite import insert
    database.execute(
        insert(TableHistory).values(
            action=1,
            description=f"Downloaded cover art for {album.title}",
            metadata_type='cover',
            provider=provider_name,
            lidarrAlbumId=album_id,
            lidarrArtistId=album.artistId,
            timestamp=datetime.now(),
            metadata_path=filepath,
        )
    )
    logger.info(f"Saved cover art for album '{album.title}' to {filepath}")

    # Embed in audio files if profile says so
    try:
        profile = None
        if album.profileId:
            profile = database.execute(
                select(TableProfiles).where(TableProfiles.id == album.profileId)
            ).scalars().first()
        if not profile:
            profile = database.execute(
                select(TableProfiles).where(TableProfiles.is_default == True)
            ).scalars().first()
        if profile and profile.embed_cover_art:
            embed_cover_in_files(album.path, image_data, sniffed[0],
                                 track_paths=album_track_paths(album_id), overwrite=True)
    except Exception as e:
        logger.error(f"Error embedding cover art for album {album_id}: {e}")

    return True, None


def save_lyrics(track_id, lyrics_data, provider_name, source='manual', **persist_kw):
    """Save lyrics to disk as .lrc for a track (manual/interactive path).

    Thin wrapper over the shared persist path so manual saves, uploads, and the
    editor behave identically to the scheduled downloader (archiving, status,
    history, language detection). Returns the lyrics_store.PersistResult
    (truthy on success; `reason`/`message` say why it failed). The history
    action is derived by persist (4 for manual/translated/generated), and a
    manual save marks the track "has vocals" there (rule M).

    persist_kw: extra persist_lyrics_result() options for derived saves
    (archive_translated_from, detected_language); never history_action.
    """
    from lyrarr.metadata.lyrics_store import PersistResult, persist_lyrics_result

    track = database.execute(
        select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
    ).scalars().first()
    if not track:
        logger.error(f"Track {track_id} not found")
        return PersistResult(False, 'not_found', 'Track not found.', None)

    # Prefer synced over plain
    content = (lyrics_data or {}).get('synced_lyrics') or (lyrics_data or {}).get('plain_lyrics')
    if not content or not content.strip():
        return PersistResult(False, 'empty', 'No lyrics content to save.', None)

    persist_kw.pop('history_action', None)
    persist_kw.setdefault('detect_lang', not persist_kw.get('detected_language'))
    result = persist_lyrics_result(track, content, provider_name, source=source, **persist_kw)
    if not result:
        logger.warning(f"Could not save lyrics for track {track_id}: {result.reason} ({result.message})")
        return result

    info = result.info or {}
    logger.info(
        f"Saved lyrics for track '{track.title}' to {info.get('filepath')} "
        f"(lang={info.get('detected_language')}, synced={info.get('is_synced')})"
    )
    return result


class _DeepLTranslator:
    """Minimal DeepL v2 client with the same interface deep-translator exposes.

    deep-translator's DeeplTranslator always sends a source_lang, which breaks
    DeepL's native auto-detection — omitting it entirely is the documented way
    to auto-detect, so lyrics in any language translate correctly.
    """

    # DeepL wants regional codes for a few targets; everything else is the
    # uppercased ISO 639-1 code.
    _TARGET_MAP = {'en': 'EN-US', 'pt': 'PT-PT', 'zh-cn': 'ZH', 'zh': 'ZH'}

    def __init__(self, api_key, target_lang):
        self._api_key = api_key
        self._target = self._TARGET_MAP.get(target_lang.lower(), target_lang.upper())
        # DeepL free-tier keys carry an ':fx' suffix and use a separate host.
        host = 'api-free.deepl.com' if api_key.endswith(':fx') else 'api.deepl.com'
        self._url = f'https://{host}/v2/translate'

    def translate_batch(self, texts):
        import requests
        response = requests.post(
            self._url,
            data={'auth_key': self._api_key, 'text': texts, 'target_lang': self._target},
            timeout=30,
        )
        response.raise_for_status()
        return [t.get('text', '') for t in response.json().get('translations', [])]

    def translate(self, text):
        batch = self.translate_batch([text])
        return batch[0] if batch else text


def get_translator(target_lang):
    """Translator for the configured engine: DeepL when selected and keyed, else Google."""
    from lyrarr.app.config import settings

    engine = getattr(getattr(settings, 'translation', None), 'engine', 'google')
    api_key = getattr(getattr(settings, 'translation', None), 'deepl_api_key', '')
    if engine == 'deepl' and api_key:
        return _DeepLTranslator(api_key, target_lang)
    if engine == 'deepl':
        logger.warning("DeepL engine selected but no API key configured — using Google")

    from deep_translator import GoogleTranslator
    return GoogleTranslator(source='auto', target=target_lang)


def translate_lyrics_content(content, target_lang, mode='replace'):
    """Translate lyrics text to a target language.

    Args:
        content: Lyrics text (may include LRC timestamps)
        target_lang: Target ISO 639-1 language code
        mode: 'replace' (replace original) or 'dual' (interleave original + translation)

    Returns:
        Translated content string, or None when any translator call failed
        or the result is identical to the input (nothing was translated).
    """
    import re

    if not content or not content.strip():
        return None

    try:
        translator = get_translator(target_lang)

        from lyrarr.metadata.lrc_repair import LRC_META_TAG

        # One or more leading timestamps (compressed LRC shares a line).
        lrc_ts = re.compile(r'((?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\])+)\s*(.*)')

        # Parse each line into (tag, text). tag is the LRC timestamp ('' for
        # plain lyrics); text is the lyric to translate ('' for blank lines).
        parsed = []   # list of (tag, text)
        for line in content.split('\n'):
            stripped = line.strip()
            m = lrc_ts.match(stripped)
            if LRC_META_TAG.match(stripped):
                parsed.append((stripped, ''))  # [ar:..]/[ti:..] tag — keep, don't translate
            elif m:
                parsed.append((m.group(1), m.group(2)))
            elif stripped:
                parsed.append(('', stripped))
            else:
                parsed.append((None, None))  # blank line

        # Translate the *unique* non-empty texts in one batched pass. Lyrics
        # repeat heavily (choruses), so de-duping avoids re-translating the same
        # line and turns hundreds of per-line requests into a handful.
        unique_texts = list({text for tag, text in parsed if text})
        translations, failed = _translate_texts(translator, unique_texts)
        if failed:
            # A partial translation would be saved as if it were complete.
            logger.warning(f"Translation failed for {failed}/{len(unique_texts)} line(s) — not saving")
            return None
        if unique_texts and all(translations.get(t, t) == t for t in unique_texts):
            logger.info("Translation returned the original text — nothing to save")
            return None

        def _render(text):
            return translations.get(text, text) if text else text

        original_lines = []
        translated_lines = []
        for tag, text in parsed:
            if tag is None:  # blank line
                original_lines.append('')
                translated_lines.append('')
            elif tag:        # timestamped line
                orig = f"{tag} {text}" if text else tag
                trans = f"{tag} {_render(text)}" if text else tag
                original_lines.append(orig)
                translated_lines.append(trans)
            else:            # plain line
                original_lines.append(text)
                translated_lines.append(_render(text))

        if mode == 'dual':
            dual = []
            for orig, trans in zip(original_lines, translated_lines):
                if orig.strip():
                    dual.append(orig)
                    if trans != orig:
                        dual.append(trans)
                else:
                    dual.append('')
            out = '\n'.join(dual)
        else:
            out = '\n'.join(translated_lines)
        return None if out.strip() == content.strip() else out

    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return None


def _translate_texts(translator, texts):
    """Translate a list of strings. Returns ({original: translation}, failed_count).

    Uses the translator's batch API when it has one and falls back to
    per-item translation. A line whose call raised keeps its original text
    and is counted in failed_count, so callers can refuse to save a partial
    "translation".
    """
    result = {}
    if not texts:
        return result, 0

    batch_fn = getattr(translator, 'translate_batch', None)
    if callable(batch_fn):
        try:
            batch = batch_fn(texts)
            if batch and len(batch) == len(texts):
                for original, translated in zip(texts, batch):
                    result[original] = translated or original
                return result, 0
        except Exception as e:
            logger.debug(f"Batch translation unavailable, falling back per-line: {e}")

    failed = 0
    for text in texts:
        try:
            result[text] = translator.translate(text) or text
        except Exception as e:
            logger.debug(f"Translating a line failed: {e}")
            result[text] = text
            failed += 1
    return result, failed
