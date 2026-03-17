# coding=utf-8

from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.database import (
    database, TableAlbums, TableTracks, TableArtists,
    select
)
from lyrarr.metadata.manager import (
    cover_providers, lyrics_providers,
    save_cover_art, save_lyrics
)

api_ns_metadata = Namespace('metadata', description='Metadata search and download')


@api_ns_metadata.route('/metadata/covers/search/<int:album_id>')
class CoverSearch(Resource):
    def get(self, album_id):
        """Search for cover art for an album across all providers."""
        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
        ).scalars().first()
        if not album:
            return {'message': 'Album not found'}, 404

        artist = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == album.artistId)
        ).scalars().first()

        results = []
        for name, provider in cover_providers.items():
            try:
                if name == 'musicbrainz' and album.mbId:
                    hits = provider.search(mb_release_group_id=album.mbId)
                elif name == 'fanart' and artist and artist.mbId:
                    hits = provider.search(mb_artist_id=artist.mbId)
                else:
                    hits = []
                for h in hits:
                    h['provider'] = name
                results.extend(hits)
            except Exception as e:
                pass

        return {'results': results, 'albumId': album_id}


@api_ns_metadata.route('/metadata/covers/download/<int:album_id>')
class CoverDownload(Resource):
    def post(self, album_id):
        """Download a specific cover art image and save it."""
        data = request.get_json() or {}
        url = data.get('url')
        provider_name = data.get('provider', 'musicbrainz')

        if not url:
            return {'message': 'url is required'}, 400

        provider = cover_providers.get(provider_name)
        if not provider:
            return {'message': 'Invalid provider'}, 400

        image_data = provider.download(url)
        if not image_data:
            return {'message': 'Failed to download image'}, 500

        success = save_cover_art(album_id, image_data, provider_name)
        if success:
            return {'message': 'Cover art saved successfully'}
        return {'message': 'Failed to save cover art'}, 500


@api_ns_metadata.route('/metadata/lyrics/search/<int:track_id>')
class LyricsSearch(Resource):
    def get(self, track_id):
        """Search for lyrics for a track across all providers."""
        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track:
            return {'message': 'Track not found'}, 404

        artist = database.execute(
            select(TableArtists).where(TableArtists.lidarrArtistId == track.artistId)
        ).scalars().first()

        album = database.execute(
            select(TableAlbums).where(TableAlbums.lidarrAlbumId == track.albumId)
        ).scalars().first()

        results = []
        for name, provider in lyrics_providers.items():
            try:
                hits = provider.search(
                    track_name=track.title,
                    artist_name=artist.name if artist else None,
                    album_name=album.title if album else None,
                    duration=track.duration,
                )
                for h in hits:
                    h['provider'] = name
                    # Truncate lyrics for preview (first 200 chars)
                    if h.get('synced_lyrics'):
                        h['synced_preview'] = h['synced_lyrics'][:300]
                    if h.get('plain_lyrics'):
                        h['plain_preview'] = h['plain_lyrics'][:300]
                results.extend(hits)
            except Exception as e:
                pass

        # Sort by score
        results.sort(key=lambda x: x.get('score', 0), reverse=True)
        return {'results': results, 'trackId': track_id}


@api_ns_metadata.route('/metadata/lyrics/download/<int:track_id>')
class LyricsDownload(Resource):
    def post(self, track_id):
        """Download/save specific lyrics for a track."""
        data = request.get_json() or {}
        lyrics_data = {
            'synced_lyrics': data.get('synced_lyrics'),
            'plain_lyrics': data.get('plain_lyrics'),
        }
        provider_name = data.get('provider', 'lrclib')

        if not lyrics_data['synced_lyrics'] and not lyrics_data['plain_lyrics']:
            return {'message': 'synced_lyrics or plain_lyrics is required'}, 400

        success = save_lyrics(track_id, lyrics_data, provider_name)
        if success:
            return {'message': 'Lyrics saved successfully'}
        return {'message': 'Failed to save lyrics'}, 500


@api_ns_metadata.route('/metadata/lyrics/versions/<int:track_id>')
class LyricsVersions(Resource):
    def get(self, track_id):
        """List previous lyrics versions stored in-app."""
        from lyrarr.app.database import TableLyricsVersions
        versions = database.execute(
            select(TableLyricsVersions)
            .where(TableLyricsVersions.lidarrTrackId == track_id)
            .order_by(TableLyricsVersions.timestamp.desc())
        ).scalars().all()
        return {'versions': [v.to_dict() for v in versions], 'trackId': track_id}

    def post(self, track_id):
        """Restore a previous lyrics version by ID."""
        data = request.get_json() or {}
        version_id = data.get('versionId')
        if not version_id:
            return {'message': 'versionId is required'}, 400

        from lyrarr.app.database import TableLyricsVersions
        version = database.execute(
            select(TableLyricsVersions).where(TableLyricsVersions.id == version_id)
        ).scalars().first()
        if not version or version.lidarrTrackId != track_id:
            return {'message': 'Version not found'}, 404

        lyrics_data = {}
        if version.lyrics_type == 'synced':
            lyrics_data['synced_lyrics'] = version.content
        else:
            lyrics_data['plain_lyrics'] = version.content

        success = save_lyrics(track_id, lyrics_data, 'restored')
        if success:
            return {'message': 'Lyrics version restored'}
        return {'message': 'Failed to restore'}, 500

@api_ns_metadata.route('/metadata/lyrics/read/<int:track_id>')
class LyricsRead(Resource):
    def get(self, track_id):
        """Read existing lyrics content from disk for a track."""
        import os

        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track or not track.path:
            return {'message': 'Track not found or has no path'}, 404

        track_base = os.path.splitext(track.path)[0]
        content = None
        lyrics_type = None

        # Try .lrc first, then .txt
        for ext, ltype in [('.lrc', 'synced'), ('.txt', 'plain')]:
            filepath = track_base + ext
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    lyrics_type = ltype
                    break
                except Exception:
                    pass

        return {
            'trackId': track_id,
            'content': content,
            'type': lyrics_type,
            'has_lyrics': content is not None,
        }


@api_ns_metadata.route('/metadata/lyrics/upload/<int:track_id>')
class LyricsUpload(Resource):
    def post(self, track_id):
        """Upload a lyrics file (.lrc or .txt) for a track."""
        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track:
            return {'message': 'Track not found'}, 404

        if 'file' not in request.files:
            return {'message': 'No file provided'}, 400

        file = request.files['file']
        if not file.filename:
            return {'message': 'No file selected'}, 400

        allowed = {'lrc', 'txt'}
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in allowed:
            return {'message': f'Invalid format. Allowed: .lrc, .txt'}, 400

        content = file.read().decode('utf-8', errors='replace')

        # Determine if synced or plain
        is_synced = ext == 'lrc' or '[' in content[:50]
        lyrics_data = {}
        if is_synced:
            lyrics_data['synced_lyrics'] = content
        else:
            lyrics_data['plain_lyrics'] = content

        success = save_lyrics(track_id, lyrics_data, 'upload')
        if success:
            return {'message': f'Lyrics uploaded for track'}
        return {'message': 'Failed to save lyrics'}, 500


@api_ns_metadata.route('/metadata/lyrics/translate/<int:track_id>')
class LyricsTranslate(Resource):
    def post(self, track_id):
        """Translate lyrics to a target language.

        Body: { content: str, targetLang: str, mode: "replace" | "dual" }
        """
        data = request.get_json() or {}
        content = data.get('content', '')
        target_lang = data.get('targetLang', 'en')
        mode = data.get('mode', 'replace')  # 'replace' or 'dual'

        if not content.strip():
            return {'message': 'No lyrics content provided'}, 400

        try:
            # Use deep-translator (more reliable, lightweight)
            from deep_translator import GoogleTranslator
            translator = GoogleTranslator(source='auto', target=target_lang)

            # Split into lines, translate non-timestamp lines
            lines = content.split('\n')
            translated_lines = []
            original_lines = []

            for line in lines:
                stripped = line.strip()
                # Skip LRC timestamp lines' timestamps but translate text
                if stripped.startswith('[') and ']' in stripped:
                    bracket_end = stripped.index(']') + 1
                    tag = stripped[:bracket_end]
                    text = stripped[bracket_end:].strip()

                    if text and not tag.startswith('[ti:') and not tag.startswith('[ar:') and not tag.startswith('[al:'):
                        try:
                            translated = translator.translate(text)
                        except Exception:
                            translated = text
                        translated_lines.append(f"{tag} {translated}")
                        original_lines.append(stripped)
                    else:
                        translated_lines.append(stripped)
                        original_lines.append(stripped)
                elif stripped:
                    try:
                        translated = translator.translate(stripped)
                    except Exception:
                        translated = stripped
                    translated_lines.append(translated)
                    original_lines.append(stripped)
                else:
                    translated_lines.append('')
                    original_lines.append('')

            translated_content = '\n'.join(translated_lines)

            if mode == 'dual':
                # Build dual display: original line + translated line interleaved
                dual_lines = []
                for orig, trans in zip(original_lines, translated_lines):
                    if orig.strip():
                        dual_lines.append(orig)
                        if trans.strip() != orig.strip():
                            dual_lines.append(f"  → {trans.strip() if not trans.strip().startswith('[') else trans.strip().split(']', 1)[-1].strip()}")
                    else:
                        dual_lines.append('')
                translated_content = '\n'.join(dual_lines)

            return {
                'translated': translated_content,
                'targetLang': target_lang,
                'mode': mode,
            }

        except ImportError:
            return {'message': 'Translation requires deep-translator package. Install with: pip install deep-translator'}, 500
        except Exception as e:
            return {'message': f'Translation failed: {str(e)}'}, 500


@api_ns_metadata.route('/metadata/lyrics/sync-generate/<int:track_id>')
class LyricsSyncGenerate(Resource):
    def post(self, track_id):
        """Generate synced lyrics from plain lyrics by aligning with audio.

        Uses faster-whisper to transcribe the audio, then aligns the existing
        plain lyrics lines to the transcription timestamps using fuzzy matching.

        Body: { content: str, model?: str }
        model override is optional; defaults to settings.metadata.whisper.model
        """
        import os
        from difflib import SequenceMatcher
        from lyrarr.app.config import settings

        data = request.get_json() or {}
        plain_lyrics = data.get('content', '').strip()

        # Read from config, allow per-request override
        model_size = data.get('model') or settings.metadata.whisper.model
        device = settings.metadata.whisper.device
        compute_type = settings.metadata.whisper.compute_type

        if not plain_lyrics:
            return {'message': 'No lyrics content provided'}, 400

        # Get track audio path
        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track or not track.path:
            return {'message': 'Track not found or has no audio file'}, 404

        if not os.path.isfile(track.path):
            return {'message': f'Audio file not found: {track.path}'}, 404

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return {
                'message': 'Sync generation requires faster-whisper. '
                           'Install with: pip install faster-whisper'
            }, 500

        try:
            # Load model (cached after first use)
            model = WhisperModel(model_size, device=device, compute_type=compute_type)

            # Transcribe with word-level timestamps
            segments, info = model.transcribe(
                track.path,
                word_timestamps=True,
                language=None,  # auto-detect
            )

            # Collect all segments with timestamps
            transcribed_segments = []
            for segment in segments:
                transcribed_segments.append({
                    'start': segment.start,
                    'end': segment.end,
                    'text': segment.text.strip(),
                })

            if not transcribed_segments:
                return {'message': 'Could not transcribe any audio from the track'}, 400

            # Parse plain lyrics into lines (skip empty)
            lyric_lines = [line.strip() for line in plain_lyrics.split('\n') if line.strip()]

            if not lyric_lines:
                return {'message': 'No lyrics lines to align'}, 400

            # Align lyrics lines to transcription segments
            # Strategy: for each lyrics line, find the best matching transcribed segment
            # using fuzzy string matching, then use that segment's timestamp
            lrc_lines = []
            used_segments = set()

            for lyric_line in lyric_lines:
                best_score = 0
                best_idx = -1
                lyric_lower = lyric_line.lower()

                for idx, seg in enumerate(transcribed_segments):
                    if idx in used_segments:
                        continue
                    score = SequenceMatcher(None, lyric_lower, seg['text'].lower()).ratio()
                    if score > best_score:
                        best_score = score
                        best_idx = idx

                if best_idx >= 0 and best_score > 0.3:
                    seg = transcribed_segments[best_idx]
                    used_segments.add(best_idx)
                    # Format timestamp as [mm:ss.xx]
                    minutes = int(seg['start'] // 60)
                    seconds = seg['start'] % 60
                    lrc_lines.append(f"[{minutes:02d}:{seconds:05.2f}] {lyric_line}")
                else:
                    # No good match — estimate from position ratio
                    if transcribed_segments:
                        total_duration = transcribed_segments[-1]['end']
                        ratio = lyric_lines.index(lyric_line) / max(len(lyric_lines), 1)
                        estimated_time = ratio * total_duration
                        minutes = int(estimated_time // 60)
                        seconds = estimated_time % 60
                        lrc_lines.append(f"[{minutes:02d}:{seconds:05.2f}] {lyric_line}")

            # Sort by timestamp
            lrc_lines.sort()

            synced_content = '\n'.join(lrc_lines)

            return {
                'synced': synced_content,
                'segments': len(transcribed_segments),
                'matched': len(used_segments),
                'total_lines': len(lyric_lines),
                'language': info.language if hasattr(info, 'language') else 'unknown',
            }

        except Exception as e:
            return {'message': f'Sync generation failed: {str(e)}'}, 500


@api_ns_metadata.route('/metadata/batch-download')
class BatchDownload(Resource):
    def post(self):
        """Trigger metadata downloads for specific albums/artists in background."""
        from threading import Thread
        from lyrarr.metadata.download_worker import download_missing_covers, download_missing_lyrics
        from lyrarr.app.event_handler import event_stream

        data = request.get_json() or {}
        album_ids = data.get('albumIds', [])
        artist_ids = data.get('artistIds', [])
        dtype = data.get('type', 'all')  # 'covers', 'lyrics', 'all'

        if not album_ids and not artist_ids:
            return {'message': 'albumIds or artistIds required'}, 400

        # If artist IDs provided, resolve to album IDs
        if artist_ids:
            albums = database.execute(
                select(TableAlbums).where(TableAlbums.artistId.in_(artist_ids))
            ).scalars().all()
            album_ids = list(set(album_ids + [a.lidarrAlbumId for a in albums]))

        count = len(album_ids)

        def _run():
            try:
                event_stream(type='download_start', payload={
                    'message': f'Batch download started for {count} album(s)',
                    'total_covers': count if dtype in ('covers', 'all') else 0,
                    'total_lyrics': count if dtype in ('lyrics', 'all') else 0,
                })

                covers = 0
                lyrics = 0

                if dtype in ('covers', 'all'):
                    covers = download_missing_covers(album_ids=album_ids) or 0
                if dtype in ('lyrics', 'all'):
                    lyrics = download_missing_lyrics(album_ids=album_ids) or 0

                event_stream(type='download_complete', payload={
                    'covers': covers, 'lyrics': lyrics,
                    'message': f'Batch: {covers} covers, {lyrics} lyrics downloaded',
                })
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Batch download error: {e}")

        thread = Thread(target=_run, daemon=True)
        thread.start()

        return {'message': f'Batch download started for {count} album(s)', 'albumCount': count}


@api_ns_metadata.route('/metadata/lyrics/batch-redetect')
class LyricsBatchRedetect(Resource):
    def post(self):
        """Re-detect language and synced status for all existing lyrics files."""
        import os
        from lyrarr.app.database import update
        from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics
        from threading import Thread

        def _run():
            import logging
            logger = logging.getLogger(__name__)
            tracks = database.execute(
                select(TableTracks).where(TableTracks.lyrics_status == 'available')
            ).scalars().all()

            updated = 0
            for track in tracks:
                if not track.path:
                    continue
                track_base = os.path.splitext(track.path)[0]
                content = None
                synced = False

                for ext in ['.lrc', '.txt']:
                    fpath = track_base + ext
                    if os.path.isfile(fpath):
                        try:
                            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                            synced = ext == '.lrc'
                        except Exception:
                            pass
                        break

                if content:
                    lang = detect_language(content)
                    database.execute(
                        update(TableTracks)
                        .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                        .values(detected_language=lang, is_synced=synced)
                    )
                    updated += 1

            logger.info(f"Batch re-detect complete: {updated}/{len(tracks)} tracks updated")

        thread = Thread(target=_run, daemon=True)
        thread.start()

        return {'message': 'Batch language re-detection started'}


@api_ns_metadata.route('/metadata/lyrics/language-stats')
class LyricsLanguageStats(Resource):
    def get(self):
        """Get language distribution, synced/plain breakdown, and provider stats."""
        from lyrarr.app.database import func, TableHistory

        # Language distribution
        lang_rows = database.execute(
            select(TableTracks.detected_language, func.count())
            .where(TableTracks.lyrics_status == 'available')
            .group_by(TableTracks.detected_language)
        ).all()
        languages = {(row[0] or 'unknown'): row[1] for row in lang_rows}

        # Synced vs plain
        total_available = database.execute(
            select(func.count()).select_from(TableTracks)
            .where(TableTracks.lyrics_status == 'available')
        ).scalar() or 0
        total_synced = database.execute(
            select(func.count()).select_from(TableTracks)
            .where(TableTracks.lyrics_status == 'available')
            .where(TableTracks.is_synced == True)
        ).scalar() or 0
        total_plain = total_available - total_synced

        # Provider distribution (from history)
        provider_rows = database.execute(
            select(TableHistory.provider, func.count())
            .where(TableHistory.metadata_type == 'lyrics')
            .group_by(TableHistory.provider)
        ).all()
        providers = {(row[0] or 'unknown'): row[1] for row in provider_rows}

        # Total tracks
        total_tracks = database.execute(
            select(func.count()).select_from(TableTracks)
        ).scalar() or 0
        total_missing = database.execute(
            select(func.count()).select_from(TableTracks)
            .where(TableTracks.lyrics_status == 'missing')
        ).scalar() or 0

        return {
            'languages': languages,
            'synced': total_synced,
            'plain': total_plain,
            'total_available': total_available,
            'total_tracks': total_tracks,
            'total_missing': total_missing,
            'providers': providers,
        }
