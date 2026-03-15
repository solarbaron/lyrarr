# coding=utf-8

"""
Webhook endpoints for receiving notifications from Lidarr.
Alternative to SignalR for environments where direct network access isn't available.

Configure a Lidarr webhook (Settings → Connect → Webhook) pointing to:
    POST http://lyrarr-host:port/api/webhook/lidarr
"""

import logging
import threading

from flask import request
from flask_restx import Namespace, Resource

from lyrarr.lidarr.sync import update_artists
from lyrarr.app.event_handler import event_stream

logger = logging.getLogger(__name__)

api_ns_webhook = Namespace('webhook', description='Webhook receivers')


@api_ns_webhook.route('/webhook/lidarr')
class LidarrWebhook(Resource):
    def post(self):
        """Receive Lidarr webhook notification.

        Lidarr sends webhooks for events like:
        - Grab: Album/track grabbed for download
        - Download: Import completed
        - Rename: Files renamed
        - ArtistAdd: New artist added
        - ArtistDelete: Artist removed
        - AlbumDelete: Album removed
        - Retag: Files retagged
        - HealthIssue: Health check issue
        - ApplicationUpdate: Lidarr updated
        - Test: Test notification
        """
        data = request.get_json(silent=True) or {}
        event_type = data.get('eventType', 'unknown')

        logger.info(f"Lidarr webhook received: {event_type}")

        event_stream(type='lidarr_webhook', payload={
            'event': event_type,
            'message': f'Lidarr webhook: {event_type}',
        })

        # Trigger sync for relevant events
        sync_events = {'Download', 'ArtistAdd', 'Rename', 'Retag'}
        if event_type in sync_events:
            logger.info(f"Webhook {event_type}: triggering sync")
            sync_thread = threading.Thread(target=update_artists, daemon=True)
            sync_thread.start()
            return {'message': f'Sync triggered for {event_type}'}

        if event_type == 'Test':
            return {'message': 'Webhook test received successfully'}

        return {'message': f'Received {event_type}'}

    def get(self):
        """Health check for webhook endpoint."""
        return {'status': 'ok', 'message': 'Lidarr webhook endpoint is active'}
