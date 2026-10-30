
import logging
import threading
import time

from lyrarr.app.config import settings
from lyrarr.app.event_handler import event_stream
from lyrarr.lidarr.sync import request_artist_sync, request_sync

logger = logging.getLogger(__name__)

# Supervisor timings (seconds). signalrcore's own reconnect gives up after a
# few attempts and a failed initial start is never retried, so a watchdog
# thread owns the connection's lifetime and rebuilds it with backoff.
_CHECK_INTERVAL = 15
_OPEN_TIMEOUT = 15
_BACKOFF_MIN = 10
_BACKOFF_MAX = 300

_SYNC_COMMANDS = frozenset({
    'RefreshArtist', 'RescanArtist', 'ArtistSearch', 'RefreshAlbum', 'AlbumSearch',
    'DownloadedAlbumsScan', 'RescanFolders', 'RenameArtist', 'RenameFiles', 'RetagArtist',
    'RetagFiles', 'MoveArtist', 'BulkMoveArtist', 'DeleteArtist',
})


def _hub_url():
    """Lidarr (Servarr) serves its SignalR hub at {url base}/signalr/messages."""
    protocol = 'https' if settings.lidarr.ssl else 'http'
    base = f"{protocol}://{settings.lidarr.ip}:{settings.lidarr.port}"
    return base + (settings.lidarr.base_url or '').rstrip('/') + '/signalr/messages'


def parse_message(data):
    """Normalize a Lidarr SignalR 'receiveMessage' payload.

    Servarr sends `[{"name": "album", "body": {"action": "updated",
    "resource": {...}}}]`; older assumptions had `action`/`resource` on the
    message itself. Accepts both. Returns (name, action, resource) or None.
    """
    message = data[0] if isinstance(data, (list, tuple)) and data else data
    if not isinstance(message, dict):
        return None
    name = str(message.get('name') or '').lower()
    body = message.get('body') if isinstance(message.get('body'), dict) else {}
    action = str(message.get('action') or body.get('action') or '').lower()
    resource = body.get('resource')
    if not isinstance(resource, dict):
        resource = message.get('resource')
    if not isinstance(resource, dict):
        resource = body
    return name, action, resource


class LidarrSignalRClient:
    """SignalR client for real-time updates from Lidarr."""

    def __init__(self):
        self._connection = None
        self._connected = False
        self._lock = threading.Lock()
        self._stop_event = None
        self._supervisor = None

    @property
    def is_connected(self):
        conn = self._connection
        transport = getattr(conn, 'transport', None) if conn is not None else None
        check = getattr(transport, 'is_connected', None)
        if callable(check):
            # The transport sits in "reconnecting" (without firing on_close)
            # after signalrcore's own reconnect gives up — trust its state.
            try:
                return bool(check())
            except Exception:
                pass
        return self._connected

    def start(self):
        """Start the supervised SignalR connection to Lidarr (non-blocking)."""
        from lyrarr.app.get_args import args
        if getattr(args, 'no_signalr', False):
            return
        if not settings.general.use_lidarr or not settings.lidarr.apikey:
            return
        with self._lock:
            if self._supervisor is not None and self._supervisor.is_alive() \
                    and self._stop_event is not None and not self._stop_event.is_set():
                return
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._supervisor = threading.Thread(
                target=self._supervise, args=(stop_event,), name='lidarr-signalr', daemon=True
            )
            self._supervisor.start()
        logger.info("Lidarr SignalR supervisor started")

    def stop(self):
        with self._lock:
            if self._stop_event is not None:
                self._stop_event.set()
        self._teardown()

    def _supervise(self, stop_event):
        backoff = _BACKOFF_MIN
        while not stop_event.is_set():
            if not settings.general.use_lidarr or not settings.lidarr.apikey:
                self._teardown()
                stop_event.wait(_CHECK_INTERVAL)
                continue
            if self.is_connected:
                backoff = _BACKOFF_MIN
                stop_event.wait(_CHECK_INTERVAL)
                continue
            self._teardown()
            if self._open(stop_event):
                backoff = _BACKOFF_MIN
                continue
            if stop_event.is_set():
                break
            logger.warning(f"Lidarr SignalR not connected; retrying in {backoff}s")
            stop_event.wait(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
        self._teardown()

    def _open(self, stop_event):
        """Build and start a fresh connection; True once the hub handshake is done."""
        hub_url = _hub_url()
        try:
            from signalrcore.hub_connection_builder import HubConnectionBuilder

            connection = HubConnectionBuilder() \
                .with_url(hub_url, options={
                    "headers": {"X-Api-Key": settings.lidarr.apikey},
                    "verify_ssl": bool(getattr(settings.lidarr, 'verify_ssl', False)),
                }) \
                .with_automatic_reconnect({
                    "type": "raw",
                    "keep_alive_interval": 10,
                    "reconnect_interval": 5,
                    "max_attempts": 5,
                }) \
                .build()

            connection.on_open(self._on_open)
            connection.on_close(self._on_close)
            connection.on_error(self._on_error)
            connection.on("receiveMessage", self._on_message)

            self._connection = connection
            connection.start()
        except Exception as e:
            logger.error(f"Failed to start Lidarr SignalR client ({hub_url}): {e}")
            return False

        deadline = time.monotonic() + _OPEN_TIMEOUT
        while time.monotonic() < deadline and not stop_event.is_set():
            if self.is_connected:
                logger.info(f"Lidarr SignalR connection established ({hub_url})")
                return True
            stop_event.wait(0.5)
        return self.is_connected

    def _teardown(self):
        conn = self._connection
        self._connection = None
        self._connected = False
        if conn is None:
            return
        try:
            conn.stop()
        except Exception as e:
            logger.debug(f"Error stopping SignalR connection: {e}")

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
        - track/trackfile: Track file was imported/deleted
        - command: Lidarr command completed (e.g., RefreshArtist)
        """
        if not data:
            return

        try:
            parsed = parse_message(data)
            if parsed is None:
                return
            name, action, resource = parsed

            logger.debug(f"Lidarr SignalR event: {name} (action={action})")

            event_stream(type='lidarr_event', payload={
                'event': name, 'action': action,
                'message': f'Lidarr: {name} {action}',
            })

            if not settings.lidarr.sync_on_live:
                return

            if name in ('artist', 'album', 'track', 'trackfile'):
                _trigger_sync(name, action, resource)

            elif name == 'command':
                cmd_name = resource.get('name') or resource.get('commandName') or ''
                status = str(resource.get('status') or 'completed').lower()
                if cmd_name in _SYNC_COMMANDS and status == 'completed':
                    cmd_body = resource.get('body') if isinstance(resource.get('body'), dict) else {}
                    artist_ids = list(cmd_body.get('artistIds') or [])
                    if cmd_body.get('artistId'):
                        artist_ids.append(cmd_body['artistId'])
                    logger.info(f"Lidarr command completed: {cmd_name}, triggering sync")
                    if artist_ids:
                        for artist_id in artist_ids:
                            request_artist_sync(artist_id)
                    else:
                        request_sync()

        except Exception as e:
            logger.error(f"Error processing Lidarr SignalR message: {e}")


def _resolve_artist_id(event_name, resource):
    """Find the Lidarr artist id an artist/album/track/trackfile event refers to."""
    if event_name == 'artist':
        return resource.get('id') or resource.get('artistId')

    artist_id = resource.get('artistId') or (resource.get('artist') or {}).get('id')
    if artist_id:
        return artist_id

    from lyrarr.app.database import TableAlbums, TableTracks, database, select

    # Delete events only carry the resource id — resolve it from local rows.
    if event_name == 'trackfile' and resource.get('id'):
        artist_id = database.execute(
            select(TableTracks.artistId).where(TableTracks.lidarrTrackId == resource['id'])
        ).scalar()
        if artist_id:
            return artist_id

    album_id = resource.get('albumId') or (resource.get('id') if event_name == 'album' else None)
    if not album_id:
        return None
    artist_id = database.execute(
        select(TableAlbums.artistId).where(TableAlbums.lidarrAlbumId == album_id)
    ).scalar()
    if artist_id:
        return artist_id
    try:
        from lyrarr.lidarr.api_client import lidarr_api
        album = lidarr_api.get_album(album_id)
        if album:
            return album.get('artistId')
    except Exception:
        pass
    return None


def _trigger_sync(event_name, action, resource):
    """Trigger a sync scoped to the affected artist when possible.

    artist/album/track events all carry enough to resolve an artist id, so we
    sync just that artist instead of the whole library. Anything we can't scope
    falls back to a full sync.
    """
    try:
        artist_id = _resolve_artist_id(event_name, resource or {})
    except Exception as e:
        logger.debug(f"Could not resolve artist for {event_name} event: {e}")
        artist_id = None

    if artist_id:
        logger.info(f"Scoped sync for {event_name} {action} → artist {artist_id}")
        request_artist_sync(artist_id)
    else:
        logger.info(f"Full sync for {event_name} {action} (no artist id in event)")
        request_sync()


lidarr_signalr_client = LidarrSignalRClient()
