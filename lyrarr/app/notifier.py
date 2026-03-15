# coding=utf-8

"""
Notification system: Discord webhooks and Telegram bot messages.
"""

import logging
import requests

from lyrarr.app.config import settings

logger = logging.getLogger(__name__)


def send_notification(title, message, metadata_type='info'):
    """Send a notification to all configured channels."""
    enabled = getattr(settings, 'notifications', None)
    if not enabled:
        return

    # Try to check if notifications are enabled via settings
    try:
        if not settings.notifications.enabled:
            return
    except AttributeError:
        return

    # Discord
    try:
        webhook_url = settings.notifications.discord_webhook
        if webhook_url:
            _send_discord(webhook_url, title, message, metadata_type)
    except AttributeError:
        pass

    # Telegram
    try:
        bot_token = settings.notifications.telegram_bot_token
        chat_id = settings.notifications.telegram_chat_id
        if bot_token and chat_id:
            _send_telegram(bot_token, chat_id, title, message)
    except AttributeError:
        pass


def _send_discord(webhook_url, title, message, metadata_type='info'):
    """Send a Discord webhook notification with an embed."""
    color_map = {
        'cover': 0x8b3dff,  # Purple
        'lyrics': 0x6a1bfa,  # Blue-purple
        'summary': 0x22c55e,  # Green
        'error': 0xef4444,  # Red
        'info': 0x6366f1,  # Indigo
    }

    payload = {
        'embeds': [{
            'title': title,
            'description': message,
            'color': color_map.get(metadata_type, 0x6366f1),
            'footer': {'text': 'Lyrarr'},
        }]
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            logger.debug(f"Discord notification sent: {title}")
        else:
            logger.warning(f"Discord webhook returned {resp.status_code}")
    except Exception as e:
        logger.error(f"Discord notification error: {e}")


def _send_telegram(bot_token, chat_id, title, message):
    """Send a Telegram notification."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    text = f"*{title}*\n{message}"

    try:
        resp = requests.post(url, json={
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'Markdown'
        }, timeout=10)
        if resp.status_code == 200:
            logger.debug(f"Telegram notification sent: {title}")
        else:
            logger.warning(f"Telegram API returned {resp.status_code}")
    except Exception as e:
        logger.error(f"Telegram notification error: {e}")


def test_notification():
    """Send a test notification to all configured channels."""
    send_notification(
        title='Lyrarr Test',
        message='This is a test notification from Lyrarr. If you see this, notifications are working!',
        metadata_type='info',
    )
    return True
