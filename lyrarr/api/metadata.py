
from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.database import TableAlbums, TableArtists, TableTracks, database, func, select
from lyrarr.metadata.manager import cover_providers, lyrics_providers, save_cover_art, save_lyrics
from lyrarr.metadata.merge import merge_provider_results

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
                elif name in ('deezer', 'itunes', 'theaudiodb'):
                    hits = provider.search(
                        artist_name=artist.name if artist else None,
                        album_name=album.title,
                    )
                else:
                    hits = []
                for h in hits:
                    h['provider'] = name
                results.extend(hits)
            except Exception:
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
        """Search for lyrics for a track across all providers.

        Optional query param:
            custom_query: Override automatic title/artist with a manual search string
        """
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

        # Custom query override: user manually entered a search string
        custom_query = request.args.get('custom_query', '').strip()

        results = []
        for name, provider in lyrics_providers.items():
            try:
                if custom_query:
                    # Use custom query as track name, no artist filter
                    hits = provider.search(
                        track_name=custom_query,
                        artist_name=None,
                        album_name=None,
                        duration=track.duration,
                    )
                else:
                    hits = provider.search(
                        track_name=track.title,
                        artist_name=artist.name if artist else None,
                        album_name=album.title if album else None,
                        duration=track.duration,
                        mb_recording_id=track.mbId if track.mbId else None,
                    )
                for h in hits:
                    h['provider'] = name
                    h['_provider'] = name  # Used by merge_provider_results()
                    # Truncate lyrics for preview (first 300 chars)
                    if h.get('synced_lyrics'):
                        h['synced_preview'] = h['synced_lyrics'][:300]
                    if h.get('plain_lyrics'):
                        h['plain_preview'] = h['plain_lyrics'][:300]

                    # match_details is now computed inside each provider
                    # via LyricsProvider.score_result() — no duplicate scoring needed

                results.extend(hits)
            except Exception:
                pass

        # Merge and de-duplicate cross-provider results
        results = merge_provider_results(results)

        # Hide results the user has blacklisted for this track.
        from lyrarr.metadata.lyrics_store import get_blacklisted_hashes, result_is_blacklisted
        blacklisted = get_blacklisted_hashes(track_id)
        if blacklisted:
            results = [r for r in results if not result_is_blacklisted(r, blacklisted)]

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


@api_ns_metadata.route('/metadata/lyrics/blacklist/<int:track_id>')
class LyricsBlacklist(Resource):
    def get(self, track_id):
        """List blacklisted lyrics entries for a track."""
        from lyrarr.app.database import TableBlacklist
        rows = database.execute(
            select(TableBlacklist).where(
                TableBlacklist.lidarrTrackId == track_id,
                TableBlacklist.metadata_type == 'lyrics',
            )
        ).scalars().all()
        return {'trackId': track_id, 'blacklist': [r.to_dict() for r in rows]}

    def post(self, track_id):
        """Blacklist a specific lyrics result so it's never auto-selected again.

        Body: { content?, synced_lyrics?, plain_lyrics?, provider?, rescan? }
        If no content is given, blacklists the currently saved .lrc file.
        rescan (default true): remove the file and re-queue the track so the
        downloader picks a different match on the next run.
        """
        import os
        from datetime import datetime

        from lyrarr.app.database import update
        from lyrarr.metadata.lyrics_store import blacklist_content

        data = request.get_json() or {}
        provider = data.get('provider')
        rescan = data.get('rescan', True)

        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track:
            return {'message': 'Track not found'}, 404

        content = (
            data.get('content')
            or data.get('synced_lyrics')
            or data.get('plain_lyrics')
        )
        # Fall back to the currently saved lyrics file
        if not content and track.path:
            fpath = os.path.splitext(track.path)[0] + '.lrc'
            if os.path.isfile(fpath):
                with open(fpath, encoding='utf-8', errors='ignore') as f:
                    content = f.read()

        if not content or not content.strip():
            return {'message': 'No lyrics content to blacklist'}, 400

        if not blacklist_content(track_id, content, provider):
            return {'message': 'Could not blacklist (empty content)'}, 400

        did_rescan = bool(rescan and track.path)
        if did_rescan:
            fpath = os.path.splitext(track.path)[0] + '.lrc'
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId == track_id)
                .values(
                    lyrics_status='missing',
                    hasLyrics=False,
                    is_synced=False,
                    lyrics_retry_count=0,
                    lyrics_retry_after=None,
                    updated_at_timestamp=datetime.now(),
                )
            )

        return {'message': 'Lyrics result blacklisted', 'rescan': did_rescan}

    def delete(self, track_id):
        """Clear all blacklisted lyrics entries for a track."""
        from lyrarr.metadata.lyrics_store import clear_blacklist
        count = clear_blacklist(track_id)
        return {'message': f'Cleared {count} blacklist entries', 'count': count}


@api_ns_metadata.route('/metadata/lyrics/upgrade')
class LyricsUpgrade(Resource):
    def post(self):
        """Re-search plain-lyrics tracks for a synced upgrade (background).

        Body: { trackIds?, albumIds?, artistIds?, all? }. With no scope (or
        all=true), every track that currently has unsynced lyrics is checked.
        """
        from threading import Thread

        from lyrarr.app.event_handler import event_stream
        from lyrarr.metadata.download_worker import downloads_in_progress, run_lyrics_upgrade

        # Advisory check — same reasoning as the batch-download endpoint.
        if downloads_in_progress():
            return {'message': 'Another download run is in progress — try again when it finishes'}, 409

        data = request.get_json() or {}
        track_ids = data.get('trackIds') or None
        album_ids = data.get('albumIds', [])
        artist_ids = data.get('artistIds', [])
        scope_all = data.get('all', False)

        if artist_ids:
            albums = database.execute(
                select(TableAlbums).where(TableAlbums.artistId.in_(artist_ids))
            ).scalars().all()
            album_ids = list(set(album_ids + [a.lidarrAlbumId for a in albums]))

        scoped_albums = None if (scope_all or track_ids) else (album_ids or None)

        def _run():
            try:
                result = run_lyrics_upgrade(album_ids=scoped_albums, track_ids=track_ids, source='manual')
                if result.get('skipped'):
                    event_stream(type='download_complete', payload={
                        'covers': 0, 'lyrics': 0,
                        'message': 'Another run is already in progress',
                    })
                else:
                    event_stream(type='download_complete', payload={
                        'covers': 0, 'lyrics': result['upgraded'],
                        'message': f"Upgraded {result['upgraded']} track(s) to synced lyrics",
                    })
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Lyrics upgrade error: {e}")
            finally:
                database.remove()

        Thread(target=_run, daemon=True).start()
        return {'message': 'Lyrics upgrade started'}


@api_ns_metadata.route('/metadata/lyrics/reset-retry')
class LyricsResetRetry(Resource):
    def post(self):
        """Reset retry backoff state for tracks so they are re-checked immediately.

        Body: { trackIds: [1,2,3] }  OR  { all: true }
        """
        from datetime import datetime

        from lyrarr.app.database import update

        data = request.get_json() or {}
        track_ids = data.get('trackIds', [])
        reset_all = data.get('all', False)

        if reset_all:
            count = database.execute(
                select(func.count()).select_from(TableTracks)
                .where(TableTracks.lyrics_retry_count > 0)
            ).scalar() or 0
            database.execute(
                update(TableTracks)
                .where(TableTracks.lyrics_retry_count > 0)
                .values(lyrics_retry_count=0, lyrics_retry_after=None,
                        updated_at_timestamp=datetime.now())
            )
            return {'message': f'Reset retry state for {count} tracks', 'count': count}
        elif track_ids:
            database.execute(
                update(TableTracks)
                .where(TableTracks.lidarrTrackId.in_(track_ids))
                .values(lyrics_retry_count=0, lyrics_retry_after=None,
                        lyrics_status='missing',
                        updated_at_timestamp=datetime.now())
            )
            return {'message': f'Reset retry state for {len(track_ids)} tracks', 'count': len(track_ids)}
        return {'message': 'trackIds or all=true required'}, 400


@api_ns_metadata.route('/metadata/lyrics/coherence/<int:album_id>')
class LyricsCoherence(Resource):
    def get(self, album_id):
        """Run album-level lyrics coherence check."""
        from lyrarr.metadata.coherence import check_album_coherence
        return check_album_coherence(album_id)


@api_ns_metadata.route('/metadata/lyrics/validate-lrc/<int:track_id>')
class LyricsValidateLrc(Resource):
    def get(self, track_id):
        """Validate LRC timestamps for a track's lyrics."""
        import os

        from lyrarr.metadata.lrc_repair import validate_lrc

        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track or not track.path:
            return {'message': 'Track not found'}, 404

        track_base = os.path.splitext(track.path)[0]
        filepath = track_base + '.lrc'
        if not os.path.isfile(filepath):
            return {'message': 'No lyrics file found'}, 404

        with open(filepath, encoding='utf-8', errors='ignore') as f:
            content = f.read()

        return validate_lrc(content)

    def post(self, track_id):
        """Validate and repair LRC timestamps, saving the repaired version."""
        import os

        from lyrarr.metadata.lrc_repair import repair_lrc, validate_lrc

        track = database.execute(
            select(TableTracks).where(TableTracks.lidarrTrackId == track_id)
        ).scalars().first()
        if not track or not track.path:
            return {'message': 'Track not found'}, 404

        track_base = os.path.splitext(track.path)[0]
        filepath = track_base + '.lrc'
        if not os.path.isfile(filepath):
            return {'message': 'No lyrics file found'}, 404

        with open(filepath, encoding='utf-8', errors='ignore') as f:
            content = f.read()

        validation = validate_lrc(content)
        if validation.get('valid'):
            return {'message': 'LRC is already valid', 'validation': validation}

        repaired = repair_lrc(content)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(repaired)

        return {
            'message': 'LRC repaired and saved',
            'before': validation,
            'after': validate_lrc(repaired),
        }


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

        # Read .lrc file
        filepath = track_base + '.lrc'
        if os.path.exists(filepath):
            try:
                with open(filepath, encoding='utf-8') as f:
                    content = f.read()
                from lyrarr.metadata.language_detect import is_synced_lyrics
                lyrics_type = 'synced' if is_synced_lyrics(content) else 'plain'
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
        """Upload a lyrics file (.lrc) for a track."""
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

        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext != 'lrc':
            return {'message': 'Invalid format. Only .lrc files are accepted'}, 400

        from lyrarr.metadata.language_detect import is_synced_lyrics

        content = file.read().decode('utf-8', errors='replace')

        # Determine if synced or plain from content, not extension
        is_synced = is_synced_lyrics(content)
        lyrics_data = {}
        if is_synced:
            lyrics_data['synced_lyrics'] = content
        else:
            lyrics_data['plain_lyrics'] = content

        success = save_lyrics(track_id, lyrics_data, 'upload')
        if success:
            return {'message': 'Lyrics uploaded for track'}
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

            for line_idx, lyric_line in enumerate(lyric_lines):
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
                        ratio = line_idx / max(len(lyric_lines), 1)
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

        from lyrarr.app.event_handler import event_stream
        from lyrarr.metadata.download_worker import run_downloads

        data = request.get_json() or {}
        album_ids = data.get('albumIds', [])
        artist_ids = data.get('artistIds', [])
        dtype = data.get('type', 'all')  # 'covers', 'lyrics', 'all'

        if not album_ids and not artist_ids:
            return {'message': 'albumIds or artistIds required'}, 400

        # Advisory check so the user gets told up front instead of a silent
        # skip buried in the activity feed. run_downloads' lock stays the
        # authoritative guard against the race.
        from lyrarr.metadata.download_worker import downloads_in_progress
        if downloads_in_progress():
            return {'message': 'Another download run is in progress — try again when it finishes'}, 409

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

                # run_downloads holds the shared lock, so this manual batch can't
                # run concurrently with the scheduled job (or another batch) and
                # double-process the same tracks.
                result = run_downloads(
                    album_ids=album_ids or None,
                    do_covers=dtype in ('covers', 'all'),
                    do_lyrics=dtype in ('lyrics', 'all'),
                    source='manual batch',
                    # The user explicitly picked these albums/artists — retry
                    # backed-off tracks now instead of silently skipping them.
                    ignore_backoff=True,
                )

                if result.get('skipped'):
                    event_stream(type='download_complete', payload={
                        'covers': 0, 'lyrics': 0,
                        'message': 'Another download run is already in progress',
                    })
                else:
                    event_stream(type='download_complete', payload={
                        'covers': result['covers'], 'lyrics': result['lyrics'],
                        'message': f"Batch: {result['covers']} covers, {result['lyrics']} lyrics downloaded",
                    })
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Batch download error: {e}")
            finally:
                database.remove()  # Clean up scoped session for this thread

        thread = Thread(target=_run, daemon=True)
        thread.start()

        return {'message': f'Batch download started for {count} album(s)', 'albumCount': count}


@api_ns_metadata.route('/metadata/lyrics/batch-translate')
class BatchTranslate(Resource):
    def post(self):
        """Translate lyrics in bulk for tracks with no detected_language.

        Body: { albumIds?: int[], artistIds?: int[], targetLang?: str }
        Only processes tracks where lyrics_status='available' AND detected_language IS NULL.
        Runs in a background thread.
        """
        from threading import Thread

        from lyrarr.app.database import update
        from lyrarr.app.event_handler import event_stream

        data = request.get_json() or {}
        album_ids = data.get('albumIds', [])
        artist_ids = data.get('artistIds', [])
        target_lang = data.get('targetLang', 'en')

        if not album_ids and not artist_ids:
            return {'message': 'albumIds or artistIds required'}, 400

        # Resolve artist → album IDs
        if artist_ids:
            albums = database.execute(
                select(TableAlbums).where(TableAlbums.artistId.in_(artist_ids))
            ).scalars().all()
            album_ids = list(set(album_ids + [a.lidarrAlbumId for a in albums]))

        # Find eligible tracks: available lyrics but no detected language
        tracks = database.execute(
            select(TableTracks).where(
                TableTracks.lidarrAlbumId.in_(album_ids),
                TableTracks.lyrics_status == 'available',
                TableTracks.detected_language.is_(None),
            )
        ).scalars().all()

        track_list = [(t.lidarrTrackId, t.path, t.title) for t in tracks]
        count = len(track_list)

        if count == 0:
            return {'message': 'No eligible tracks found (all tracks already have detected language)'}, 200

        def _run():
            import logging
            import os
            log = logging.getLogger(__name__)
            translated = 0
            failed = 0

            try:
                from deep_translator import GoogleTranslator

                from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics

                event_stream(type='batch_translate_start', payload={
                    'message': f'Translating lyrics for {count} track(s)',
                    'total': count,
                })

                for track_id, track_path, track_title in track_list:
                    try:
                        if not track_path:
                            continue

                        # Read current lyrics file
                        track_base = os.path.splitext(track_path)[0]
                        lyrics_path = track_base + '.lrc'
                        content = None
                        if os.path.isfile(lyrics_path):
                            with open(lyrics_path, encoding='utf-8', errors='ignore') as f:
                                content = f.read()

                        if not content or not content.strip():
                            continue

                        # Detect language first
                        lang = detect_language(content)
                        if lang:
                            # Language detected — update DB and skip translation
                            database.execute(
                                update(TableTracks)
                                .where(TableTracks.lidarrTrackId == track_id)
                                .values(detected_language=lang)
                            )
                            continue

                        # Translate line by line
                        translator = GoogleTranslator(source='auto', target=target_lang)
                        lines = content.split('\n')
                        translated_lines = []

                        for line in lines:
                            stripped = line.strip()
                            if not stripped:
                                translated_lines.append('')
                                continue

                            # Handle LRC timestamp lines
                            if stripped.startswith('[') and ']' in stripped:
                                bracket_end = stripped.index(']') + 1
                                tag = stripped[:bracket_end]
                                text = stripped[bracket_end:].strip()

                                if text and not tag.startswith('[ti:') and not tag.startswith('[ar:') and not tag.startswith('[al:'):
                                    try:
                                        t = translator.translate(text)
                                        translated_lines.append(f"{tag} {t}")
                                    except Exception:
                                        translated_lines.append(stripped)
                                else:
                                    translated_lines.append(stripped)
                            else:
                                try:
                                    t = translator.translate(stripped)
                                    translated_lines.append(t)
                                except Exception:
                                    translated_lines.append(stripped)

                        translated_content = '\n'.join(translated_lines)

                        # Save translated lyrics using save_lyrics for versioning
                        is_synced = is_synced_lyrics(translated_content)
                        lyrics_data = {
                            'synced_lyrics': translated_content if is_synced else None,
                            'plain_lyrics': None if is_synced else translated_content,
                        }
                        save_lyrics(track_id, lyrics_data, f'batch-translate-{target_lang}')

                        # Update language in DB
                        database.execute(
                            update(TableTracks)
                            .where(TableTracks.lidarrTrackId == track_id)
                            .values(detected_language=target_lang)
                        )

                        translated += 1
                        log.debug(f"Batch translate: translated '{track_title}' → {target_lang}")

                    except Exception as e:
                        failed += 1
                        log.warning(f"Batch translate failed for '{track_title}': {e}")

                event_stream(type='batch_translate_complete', payload={
                    'translated': translated,
                    'failed': failed,
                    'message': f'Batch translate: {translated} translated, {failed} failed',
                })

            except ImportError:
                log.error("Batch translate requires deep-translator. Install with: pip install deep-translator")
            except Exception as e:
                log.error(f"Batch translate error: {e}")
            finally:
                database.remove()

        thread = Thread(target=_run, daemon=True)
        thread.start()

        return {'message': f'Batch translate started for {count} track(s)', 'trackCount': count}


@api_ns_metadata.route('/metadata/lyrics/batch-sync-generate')
class BatchSyncGenerate(Resource):
    def post(self):
        """Generate synced LRC in bulk for tracks with untimed lyrics.

        Body: { albumIds?: int[], artistIds?: int[], trackIds?: int[] }
        Only processes tracks where lyrics_status='available' AND is_synced=False.
        Uses Whisper to transcribe audio and align with existing plain lyrics.
        Runs in a background thread.
        """
        from threading import Thread

        from lyrarr.app.config import settings
        from lyrarr.app.database import update
        from lyrarr.app.event_handler import event_stream

        data = request.get_json() or {}
        album_ids = data.get('albumIds', [])
        artist_ids = data.get('artistIds', [])
        track_ids = data.get('trackIds', [])

        if not album_ids and not artist_ids and not track_ids:
            return {'message': 'albumIds, artistIds, or trackIds required'}, 400

        if track_ids:
            # Direct track selection
            tracks = database.execute(
                select(TableTracks).where(
                    TableTracks.lidarrTrackId.in_(track_ids),
                    TableTracks.lyrics_status == 'available',
                    TableTracks.is_synced == False,
                )
            ).scalars().all()
        else:
            # Resolve artist → album IDs
            if artist_ids:
                albums = database.execute(
                    select(TableAlbums).where(TableAlbums.artistId.in_(artist_ids))
                ).scalars().all()
                album_ids = list(set(album_ids + [a.lidarrAlbumId for a in albums]))

            # Find eligible tracks: available lyrics but not synced
            tracks = database.execute(
                select(TableTracks).where(
                    TableTracks.lidarrAlbumId.in_(album_ids),
                    TableTracks.lyrics_status == 'available',
                    TableTracks.is_synced == False,
                )
            ).scalars().all()

        track_list = [(t.lidarrTrackId, t.path, t.title) for t in tracks]
        count = len(track_list)

        if count == 0:
            return {'message': 'No eligible tracks found (all available lyrics are already synced)'}, 200

        model_size = settings.metadata.whisper.model
        device = settings.metadata.whisper.device
        compute_type = settings.metadata.whisper.compute_type

        def _run():
            import logging
            import os
            from difflib import SequenceMatcher
            log = logging.getLogger(__name__)
            synced = 0
            failed = 0

            try:
                from faster_whisper import WhisperModel

                event_stream(type='batch_sync_start', payload={
                    'message': f'Generating synced lyrics for {count} track(s)',
                    'total': count,
                })

                # Load model once for the entire batch
                model = WhisperModel(model_size, device=device, compute_type=compute_type)

                for track_id, track_path, track_title in track_list:
                    try:
                        if not track_path or not os.path.isfile(track_path):
                            continue

                        # Read current lyrics file
                        track_base = os.path.splitext(track_path)[0]
                        content = None
                        lyrics_path = track_base + '.lrc'
                        if os.path.isfile(lyrics_path):
                            with open(lyrics_path, encoding='utf-8', errors='ignore') as f:
                                content = f.read()

                        if not content or not content.strip():
                            continue

                        # Parse lyrics into non-empty lines
                        lyric_lines = [line.strip() for line in content.split('\n') if line.strip()]
                        if not lyric_lines:
                            continue

                        # Transcribe audio
                        segments_iter, info = model.transcribe(
                            track_path,
                            word_timestamps=True,
                            language=None,
                        )

                        transcribed_segments = []
                        for segment in segments_iter:
                            transcribed_segments.append({
                                'start': segment.start,
                                'end': segment.end,
                                'text': segment.text.strip(),
                            })

                        if not transcribed_segments:
                            log.debug(f"Batch sync: no transcription for '{track_title}'")
                            continue

                        # Align lyrics to transcription using fuzzy matching
                        lrc_lines = []
                        used_segments = set()

                        for line_idx, lyric_line in enumerate(lyric_lines):
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
                                minutes = int(seg['start'] // 60)
                                seconds = seg['start'] % 60
                                lrc_lines.append(f"[{minutes:02d}:{seconds:05.2f}] {lyric_line}")
                            else:
                                # Estimate from position
                                if transcribed_segments:
                                    total_duration = transcribed_segments[-1]['end']
                                    ratio = line_idx / max(len(lyric_lines), 1)
                                    estimated_time = ratio * total_duration
                                    minutes = int(estimated_time // 60)
                                    seconds = estimated_time % 60
                                    lrc_lines.append(f"[{minutes:02d}:{seconds:05.2f}] {lyric_line}")

                        lrc_lines.sort()
                        synced_content = '\n'.join(lrc_lines)

                        # Save synced lyrics
                        lyrics_data = {'synced_lyrics': synced_content, 'plain_lyrics': None}
                        save_lyrics(track_id, lyrics_data, 'batch-sync-generate')

                        # Update DB
                        database.execute(
                            update(TableTracks)
                            .where(TableTracks.lidarrTrackId == track_id)
                            .values(is_synced=True)
                        )

                        synced += 1
                        log.debug(f"Batch sync: generated timing for '{track_title}'")

                    except Exception as e:
                        failed += 1
                        log.warning(f"Batch sync failed for '{track_title}': {e}")

                event_stream(type='batch_sync_complete', payload={
                    'synced': synced,
                    'failed': failed,
                    'message': f'Batch sync: {synced} synced, {failed} failed',
                })

            except ImportError:
                log.error("Batch sync requires faster-whisper. Install with: pip install faster-whisper")
            except Exception as e:
                log.error(f"Batch sync error: {e}")
            finally:
                database.remove()

        thread = Thread(target=_run, daemon=True)
        thread.start()

        return {'message': f'Batch sync generation started for {count} track(s)', 'trackCount': count}


@api_ns_metadata.route('/metadata/lyrics/batch-redetect')
class LyricsBatchRedetect(Resource):
    def post(self):
        """Re-detect language and synced status for lyrics files.

        Body (optional): { trackIds?: int[] }
        If trackIds provided, only processes those tracks. Otherwise processes all available.
        """
        import os
        from threading import Thread

        from lyrarr.app.database import update
        from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics

        data = request.get_json() or {}
        track_ids = data.get('trackIds', [])

        def _run():
            import logging
            logger = logging.getLogger(__name__)
            try:
                query = select(TableTracks).where(TableTracks.lyrics_status == 'available')
                if track_ids:
                    query = query.where(TableTracks.lidarrTrackId.in_(track_ids))
                tracks = database.execute(query).scalars().all()

                updated = 0
                for track in tracks:
                    if not track.path:
                        continue
                    track_base = os.path.splitext(track.path)[0]
                    content = None
                    synced = False

                    fpath = track_base + '.lrc'
                    if os.path.isfile(fpath):
                        try:
                            with open(fpath, encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                            synced = is_synced_lyrics(content)
                        except Exception:
                            pass

                    if content:
                        lang = detect_language(content)
                        database.execute(
                            update(TableTracks)
                            .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                            .values(detected_language=lang, is_synced=synced)
                        )
                        updated += 1

                logger.info(f"Batch re-detect complete: {updated}/{len(tracks)} tracks updated")
            finally:
                database.remove()  # Clean up scoped session for this thread

        thread = Thread(target=_run, daemon=True)
        thread.start()

        return {'message': 'Batch language re-detection started'}


@api_ns_metadata.route('/metadata/lyrics/language-stats')
class LyricsLanguageStats(Resource):
    def get(self):
        """Get language distribution, synced/plain breakdown, and provider stats."""
        from lyrarr.app.database import TableHistory, func

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


@api_ns_metadata.route('/metadata/lyrics/import-sidecar')
class LyricsSidecarImport(Resource):
    def post(self):
        """Scan for existing .lrc sidecar files on disk and import into DB.

        For each track with lyrics_status='missing', checks if a .lrc
        file already exists alongside the audio file. If found, updates the DB
        with detected language and synced status.
        """
        from threading import Thread

        from lyrarr.app.database import database as db_session
        from lyrarr.app.database import update
        from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics

        def _run():
            import logging
            import os
            log = logging.getLogger(__name__)

            try:
                tracks = db_session.execute(
                    select(TableTracks).where(
                        TableTracks.lyrics_status.in_(['missing', 'unknown'])
                    )
                ).scalars().all()

                imported = 0
                for track in tracks:
                    if not track.path:
                        continue
                    track_base = os.path.splitext(track.path)[0]

                    fpath = track_base + '.lrc'
                    if os.path.isfile(fpath):
                        try:
                            with open(fpath, encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                            if content.strip():
                                lang = detect_language(content)
                                synced = is_synced_lyrics(content)
                                db_session.execute(
                                    update(TableTracks)
                                    .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                                    .values(
                                        lyrics_status='available',
                                        hasLyrics=True,
                                        detected_language=lang,
                                        is_synced=synced,
                                    )
                                )
                                imported += 1
                        except Exception as e:
                            log.warning(f"Failed to import sidecar for track {track.lidarrTrackId}: {e}")

                log.info(f"Sidecar import complete: {imported}/{len(tracks)} tracks updated")
            finally:
                db_session.remove()  # Clean up scoped session for this thread

        thread = Thread(target=_run, daemon=True)
        thread.start()
        return {'message': 'Sidecar import started — scanning for existing lyrics files'}


@api_ns_metadata.route('/metadata/lyrics/audit')
class LyricsAudit(Resource):
    def post(self):
        """Comprehensive lyrics state audit.

        Checks ALL tracks and reconciles DB state with what's on disk:
        - If .lrc exists but DB says 'missing' → set to 'available'
        - If DB says 'available' but no .lrc found → set to 'missing'
        - Re-detects language and sync status for all available tracks
        Runs in a background thread with SSE progress events.
        """
        from threading import Thread

        from lyrarr.app.database import database as db_session
        from lyrarr.app.database import update
        from lyrarr.app.event_handler import event_stream
        from lyrarr.metadata.language_detect import detect_language, is_synced_lyrics

        def _run():
            import logging
            import os
            log = logging.getLogger(__name__)

            try:
                tracks = db_session.execute(
                    select(TableTracks).where(
                        TableTracks.lyrics_status.notin_(['blacklisted'])
                    )
                ).scalars().all()

                total = len(tracks)
                fixed_to_available = 0
                fixed_to_missing = 0
                updated_metadata = 0

                event_stream(type='lyrics_audit_start', payload={
                    'message': f'Auditing lyrics state for {total} tracks...',
                    'total': total,
                })

                for i, track in enumerate(tracks):
                    if not track.path:
                        continue

                    track_base = os.path.splitext(track.path)[0]
                    lrc_path = track_base + '.lrc'
                    file_exists = os.path.isfile(lrc_path)

                    if file_exists:
                        # File exists — ensure DB is correct
                        try:
                            with open(lrc_path, encoding='utf-8', errors='ignore') as f:
                                content = f.read()

                            if content.strip():
                                lang = detect_language(content)
                                synced = is_synced_lyrics(content)

                                updates = {
                                    'detected_language': lang,
                                    'is_synced': synced,
                                    'hasLyrics': True,
                                }

                                if track.lyrics_status != 'available':
                                    updates['lyrics_status'] = 'available'
                                    fixed_to_available += 1

                                # Only count as metadata update if something changed
                                if (track.detected_language != lang or
                                    track.is_synced != synced or
                                    track.lyrics_status != 'available'):
                                    db_session.execute(
                                        update(TableTracks)
                                        .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                                        .values(**updates)
                                    )
                                    updated_metadata += 1
                            else:
                                # Empty file — treat as missing
                                if track.lyrics_status == 'available':
                                    db_session.execute(
                                        update(TableTracks)
                                        .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                                        .values(
                                            lyrics_status='missing',
                                            hasLyrics=False,
                                            detected_language=None,
                                            is_synced=False,
                                        )
                                    )
                                    fixed_to_missing += 1
                        except Exception as e:
                            log.warning(f"Audit error for track {track.lidarrTrackId}: {e}")
                    else:
                        # No file on disk — ensure DB reflects missing
                        if track.lyrics_status == 'available':
                            db_session.execute(
                                update(TableTracks)
                                .where(TableTracks.lidarrTrackId == track.lidarrTrackId)
                                .values(
                                    lyrics_status='missing',
                                    hasLyrics=False,
                                    detected_language=None,
                                    is_synced=False,
                                )
                            )
                            fixed_to_missing += 1

                    # Progress event every 50 tracks
                    if (i + 1) % 50 == 0 or (i + 1) == total:
                        event_stream(type='lyrics_audit_progress', payload={
                            'current': i + 1,
                            'total': total,
                            'message': f'Audited {i + 1}/{total} tracks...',
                        })

                summary = (
                    f"Lyrics audit complete: {total} tracks checked. "
                    f"{fixed_to_available} fixed to available, "
                    f"{fixed_to_missing} fixed to missing, "
                    f"{updated_metadata} metadata updated."
                )
                log.info(summary)
                event_stream(type='lyrics_audit_complete', payload={
                    'message': summary,
                    'fixed_to_available': fixed_to_available,
                    'fixed_to_missing': fixed_to_missing,
                    'updated_metadata': updated_metadata,
                })

            except Exception as e:
                log.error(f"Lyrics audit error: {e}")
                event_stream(type='lyrics_audit_error', payload={'message': str(e)})
            finally:
                db_session.remove()

        thread = Thread(target=_run, daemon=True)
        thread.start()
        return {'message': 'Lyrics state audit started — checking all tracks against disk'}


@api_ns_metadata.route('/metadata/providers/health')
class ProviderHealth(Resource):
    def get(self):
        """Get health stats for all metadata providers."""
        from lyrarr.metadata.provider_utils import health_tracker
        from lyrarr.metadata.registry import cover_providers, lyrics_providers

        stats = health_tracker.get_stats()

        # Include all known providers even if they have no stats yet
        all_providers = {}
        for name in cover_providers:
            entry = stats.get(name, {
                'successes': 0, 'failures': 0, 'consecutive_failures': 0,
                'last_failure': None, 'disabled_until': None, 'available': True,
            })
            entry['type'] = 'cover'
            all_providers[name] = entry

        for name in lyrics_providers:
            if name in all_providers:
                all_providers[name]['type'] = 'both'
            else:
                entry = stats.get(name, {
                    'successes': 0, 'failures': 0, 'consecutive_failures': 0,
                    'last_failure': None, 'disabled_until': None, 'available': True,
                })
                entry['type'] = 'lyrics'
                all_providers[name] = entry

        return {'providers': all_providers}

    def post(self):
        """Reset provider health stats."""
        from lyrarr.metadata.provider_utils import health_tracker
        data = request.get_json() or {}
        provider_name = data.get('provider')
        health_tracker.reset(provider_name)
        return {'message': f'Health stats reset for {"all providers" if not provider_name else provider_name}'}
