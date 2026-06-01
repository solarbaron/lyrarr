
"""
Webhook endpoints for receiving notifications from Lidarr.
Alternative to SignalR for environments where direct network access isn't available.

Configure a Lidarr webhook (Settings → Connect → Webhook) pointing to:
    POST http://lyrarr-host:port/api/webhook/lidarr

If authentication is enabled in Lyrarr, this endpoint is NOT exempt — the
request must carry valid credentials, otherwise anyone could POST to it to
force repeated Lidarr syncs. Either add an "X-Api-Key" header with your Lyrarr
API key to the Lidarr webhook connection, or set the webhook's Basic auth
username/password to your Lyrarr credentials. With no auth configured, it stays
open, matching the rest of the app.
"""

import logging

from flask import request
from flask_restx import Namespace, Resource

from lyrarr.app.event_handler import event_stream
from lyrarr.lidarr.sync import request_artist_sync, request_sync

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

        # Trigger sync for relevant events, scoped to the affected artist when
        # the payload identifies one (Lidarr includes an `artist` object).
        sync_events = {'Download', 'ArtistAdd', 'Rename', 'Retag', 'TrackRetag', 'AlbumDelete'}
        if event_type in sync_events:
            artist_id = (data.get('artist') or {}).get('id')
            if artist_id:
                logger.info(f"Webhook {event_type}: scoped sync for artist {artist_id}")
                request_artist_sync(artist_id)
            else:
                logger.info(f"Webhook {event_type}: full sync (no artist id in payload)")
                request_sync()
            return {'message': f'Sync triggered for {event_type}'}

        if event_type == 'ArtistDelete':
            # Re-sync the artist; sync_artist removes it locally if it's gone.
            artist_id = (data.get('artist') or {}).get('id')
            if artist_id:
                request_artist_sync(artist_id)
                return {'message': 'Artist delete processed'}
            request_sync()
            return {'message': 'Sync triggered for ArtistDelete'}

        if event_type == 'Test':
            return {'message': 'Webhook test received successfully'}

        return {'message': f'Received {event_type}'}

    def get(self):
        """Health check for webhook endpoint."""
        return {'status': 'ok', 'message': 'Lidarr webhook endpoint is active'}
