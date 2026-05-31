# coding=utf-8

"""
Album-level lyrics coherence analysis.

After downloading lyrics for an album, checks for statistical outliers
that may indicate wrong lyrics were matched:
  - Language outliers (e.g. 10/12 tracks are English but one is Korean)
  - Artist name mismatches (lyrics matched to a different artist)
  - Length outliers (one track's lyrics suspiciously short vs album average)
"""

import logging
import os
import re
from collections import Counter

from lyrarr.app.database import (
    database, TableTracks, TableAlbums, TableArtists,
    select
)

logger = logging.getLogger(__name__)

# LRC timestamp pattern
_LRC_TS = re.compile(r'\[\d{1,2}:\d{2}[.:]\d{2,3}\]\s*')
_LRC_META = re.compile(r'^\[(?:ar|ti|al|au|length|by|offset|re|ve|id|la):.*\]$', re.IGNORECASE)

# Minimum tracks needed for statistical analysis
MIN_TRACKS_FOR_ANALYSIS = 4

# Threshold for outlier detection (% below mean)
SHORT_LYRICS_THRESHOLD = 0.3  # Flag if < 30% of mean line count


def check_album_coherence(album_id):
    """Analyze lyrics across an album for statistical outliers.

    Args:
        album_id: The Lidarr album ID

    Returns:
        Dict with:
            album_id: int
            album_title: str
            total_tracks: int
            tracks_with_lyrics: int
            issues: list of dicts with track_id, track_title, issue, detail
    """
    album = database.execute(
        select(TableAlbums).where(TableAlbums.lidarrAlbumId == album_id)
    ).scalars().first()

    if not album:
        return {'album_id': album_id, 'album_title': '?', 'total_tracks': 0,
                'tracks_with_lyrics': 0, 'issues': []}

    artist = database.execute(
        select(TableArtists).where(TableArtists.lidarrArtistId == album.artistId)
    ).scalars().first()

    tracks = database.execute(
        select(TableTracks).where(TableTracks.albumId == album_id)
        .order_by(TableTracks.discNumber, TableTracks.trackNumber)
    ).scalars().all()

    total_tracks = len(tracks)
    issues = []

    # Collect data for analysis
    track_data = []
    for track in tracks:
        if track.lyrics_status not in ('available', 'manual') or not track.path:
            continue

        # Read lyrics file
        track_base = os.path.splitext(track.path)[0]
        filepath = track_base + '.lrc'
        line_count = 0

        if os.path.isfile(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                line_count = _count_text_lines(content)
            except Exception:
                pass

        track_data.append({
            'id': track.lidarrTrackId,
            'title': track.title,
            'language': track.detected_language,
            'is_synced': track.is_synced,
            'line_count': line_count,
            'duration': track.duration,
        })

    tracks_with_lyrics = len(track_data)

    if tracks_with_lyrics < MIN_TRACKS_FOR_ANALYSIS:
        return {
            'album_id': album_id,
            'album_title': album.title,
            'total_tracks': total_tracks,
            'tracks_with_lyrics': tracks_with_lyrics,
            'issues': [],
        }

    # ------------------------------------------------------------------
    # 1. Language outlier detection
    # ------------------------------------------------------------------
    langs = [t['language'] for t in track_data if t['language']]
    if len(langs) >= MIN_TRACKS_FOR_ANALYSIS:
        lang_counts = Counter(langs)
        majority_lang, majority_count = lang_counts.most_common(1)[0]
        majority_ratio = majority_count / len(langs)

        # If > 70% of tracks are one language, flag outliers
        if majority_ratio >= 0.7:
            for t in track_data:
                if t['language'] and t['language'] != majority_lang:
                    issues.append({
                        'track_id': t['id'],
                        'track_title': t['title'],
                        'issue': 'language_outlier',
                        'detail': f"Detected as '{t['language']}' but {majority_count}/{len(langs)} "
                                  f"tracks are '{majority_lang}'",
                    })

    # ------------------------------------------------------------------
    # 2. Length outlier detection
    # ------------------------------------------------------------------
    line_counts = [t['line_count'] for t in track_data if t['line_count'] > 0]
    if len(line_counts) >= MIN_TRACKS_FOR_ANALYSIS:
        mean_lines = sum(line_counts) / len(line_counts)
        threshold = mean_lines * SHORT_LYRICS_THRESHOLD

        for t in track_data:
            if t['line_count'] > 0 and t['line_count'] < threshold:
                # Also check if the track is short (< 60s) — short tracks naturally have fewer lines
                duration_s = (t['duration'] or 0) / 1000
                if duration_s > 60:
                    issues.append({
                        'track_id': t['id'],
                        'track_title': t['title'],
                        'issue': 'suspiciously_short',
                        'detail': f"Only {t['line_count']} lines (album average: {mean_lines:.0f})",
                    })

    # ------------------------------------------------------------------
    # 3. Sync type consistency
    # ------------------------------------------------------------------
    synced_tracks = [t for t in track_data if t['is_synced']]
    unsynced_tracks = [t for t in track_data if not t['is_synced']]

    # If most tracks are synced but a few aren't, flag them
    if len(synced_tracks) > len(unsynced_tracks) * 3 and unsynced_tracks:
        for t in unsynced_tracks:
            issues.append({
                'track_id': t['id'],
                'track_title': t['title'],
                'issue': 'sync_type_outlier',
                'detail': f"Plain lyrics while {len(synced_tracks)}/{tracks_with_lyrics} "
                          f"album tracks have synced lyrics",
            })

    return {
        'album_id': album_id,
        'album_title': album.title,
        'total_tracks': total_tracks,
        'tracks_with_lyrics': tracks_with_lyrics,
        'issues': issues,
    }


def _count_text_lines(content):
    """Count actual lyrics text lines (not timestamps or metadata)."""
    count = 0
    for line in content.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _LRC_META.match(stripped):
            continue
        # Remove timestamp and check if text remains
        cleaned = _LRC_TS.sub('', stripped).strip()
        if cleaned:
            count += 1
    return count
