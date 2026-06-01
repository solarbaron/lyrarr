# coding=utf-8

import logging

from lyrarr.app.config import settings
from lyrarr.lidarr.sync import request_artist_sync, request_sync
from lyrarr.app.event_handler import event_stream

logger = logging.getLogger(__name__)


class LidarrSignalRClient:
    """SignalR client for real-time updates from Lidarr."""

    def __init__(self):
        self._connection = None
        self._connected = False

    @property
    def is_connected(self):
        return self._connected

    def start(self):
        """Start the SignalR connection to Lidarr."""
        if not settings.general.use_lidarr or not settings.lidarr.apikey:
            return

        protocol = 'https' if settings.lidarr.ssl else 'http'
        base_url = f"{protocol}://{settings.lidarr.ip}:{settings.lidarr.port}{settings.lidarr.base_url}"
        hub_url = f"{base_url.rstrip('/')}/signalr"

        try:
            from signalrcore.hub_connection_builder import HubConnectionBuilder

            self._connection = HubConnectionBuilder() \
                .with_url(hub_url, options={
                    "headers": {"X-Api-Key": settings.lidarr.apikey},
                    "verify_ssl": False,
                }) \
                .with_automatic_reconnect({
                    "type": "interval",
                    "keep_alive_interval": 10,
                    "intervals": [1, 3, 5, 6, 7, 87, 3]
                }) \
                .build()

            self._connection.on_open(self._on_open)
            self._connection.on_close(self._on_close)
            self._connection.on_error(self._on_error)

            # Listen for relevant events
            self._connection.on("receiveMessage", self._on_message)

            self._connection.start()
            logger.info("Lidarr SignalR connection started")

        except Exception as e:
            logger.error(f"Failed to start Lidarr SignalR client: {e}")

    def _on_open(self):
        self._connected = True
        logger.info("Lidarr SignalR connected")
        event_stream(type='signalr', payload={'status': 'connected'})

    def _on_close(self):
        self._connected = False
        logger.info("Lidarr SignalR disconnected")
        event_stream(type='signalr', payload={'status': 'disconnected'})

    def _on_error(self, error):
        logger.error(f"Lidarr SignalR error: {error}")

    def _on_message(self, data):
        """Handle incoming SignalR messages from Lidarr.

        Supported events:
        - artist: Artist was added/updated/deleted in Lidarr
        - album: Album was added/updated/deleted
        - track: Track file was imported/deleted
        - command: Lidarr command completed (e.g., RefreshArtist)
        """
        if not data:
            return

        try:
            message = data[0] if isinstance(data, list) else data
            name = message.get('name', '')
            action = message.get('action', '')
            body = message.get('body', {}) or message.get('resource', {}) or {}

            logger.info(f"Lidarr SignalR event: {name} (action={action})")

            event_stream(type='lidarr_event', payload={
                'event': name, 'action': action,
                'message': f'Lidarr: {name} {action}',
            })

            if name in ('artist', 'album', 'track'):
                if settings.lidarr.sync_on_live:
                    _trigger_sync(name, action, body)

            elif name == 'command':
                # Lidarr command completed (e.g., RefreshArtist, RescanArtist)
                cmd_name = body.get('name', '')
                if cmd_name in ('RefreshArtist', 'RescanArtist', 'ArtistSearch'):
                    logger.info(f"Lidarr command completed: {cmd_name}, triggering sync")
                    if settings.lidarr.sync_on_live:
                        request_sync()

        except Exception as e:
            logger.error(f"Error processing Lidarr SignalR message: {e}")

    def stop(self):
        if self._connection:
            self._connection.stop()
            self._connected = False


def _trigger_sync(event_name, action, body):
    """Trigger a sync scoped to the affected artist when possible.

    artist/album/track events all carry enough to resolve an artist id, so we
    sync just that artist instead of the whole library. Anything we can't scope
    falls back to a full sync.
    """
    artist_id = None
    if event_name == 'artist':
        artist_id = body.get('id') or body.get('artistId')
    elif event_name in ('album', 'track'):
        artist_id = body.get('artistId')
        # Track/album events sometimes nest the artist object instead.
        if not artist_id:
            artist_id = (body.get('artist') or {}).get('id')

    if artist_id:
        logger.info(f"Scoped sync for {event_name} {action} → artist {artist_id}")
        request_artist_sync(artist_id)
    else:
        logger.info(f"Full sync for {event_name} {action} (no artist id in event)")
        request_sync()


lidarr_signalr_client = LidarrSignalRClient()
