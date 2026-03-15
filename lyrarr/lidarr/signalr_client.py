# coding=utf-8

import logging
import threading

from lyrarr.app.config import settings
from lyrarr.lidarr.sync import update_artists

logger = logging.getLogger(__name__)


class LidarrSignalRClient:
    """SignalR client for real-time updates from Lidarr."""

    def __init__(self):
        self._connection = None
        self._connected = False

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

    def _on_close(self):
        self._connected = False
        logger.info("Lidarr SignalR disconnected")

    def _on_error(self, error):
        logger.error(f"Lidarr SignalR error: {error}")

    def _on_message(self, data):
        """Handle incoming SignalR messages from Lidarr."""
        if not data:
            return

        try:
            message = data[0] if isinstance(data, list) else data
            name = message.get('name', '')

            if name in ['artist', 'album', 'track']:
                logger.info(f"Lidarr SignalR event: {name} updated, triggering sync")
                if settings.lidarr.sync_on_live:
                    sync_thread = threading.Thread(target=update_artists, daemon=True)
                    sync_thread.start()

        except Exception as e:
            logger.error(f"Error processing Lidarr SignalR message: {e}")

    def stop(self):
        if self._connection:
            self._connection.stop()
            self._connected = False


lidarr_signalr_client = LidarrSignalRClient()
