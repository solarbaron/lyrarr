
"""
Backup and restore endpoints for profiles and settings.
"""

import json

from flask import Response, request
from flask_restx import Namespace, Resource

from lyrarr.app.config import SettingsError, save_settings, settings
from lyrarr.app.database import TableProfiles, database, select

api_ns_backup = Namespace('backup', description='Backup and restore')

# Sensitive subkeys removed from exported settings so a shared/stored backup
# file doesn't leak login secrets. (API keys are kept so a restore is useful.)
_SENSITIVE_SUBKEYS = {'flask_secret_key', 'password'}


def _redact_settings(settings_data):
    """Strip login secrets (flask secret key, passwords) from settings sections."""
    redacted = {}
    for section, values in settings_data.items():
        if isinstance(values, dict):
            redacted[section] = {
                k: v for k, v in values.items()
                if k.lower() not in _SENSITIVE_SUBKEYS
            }
        else:
            redacted[section] = values
    return redacted


@api_ns_backup.route('/system/backup')
class BackupExport(Resource):
    def get(self):
        """Export profiles and settings as JSON."""
        # Gather profiles
        profiles = database.execute(select(TableProfiles)).scalars().all()
        profiles_data = [p.to_dict() for p in profiles]

        # Gather settings (dynaconf to dict)
        settings_data = {}
        for section in ['general', 'lidarr', 'fanart', 'genius', 'notifications', 'auth', 'metadata']:
            try:
                section_obj = getattr(settings, section, None)
                if section_obj:
                    settings_data[section] = dict(section_obj)
            except Exception:
                pass

        backup = {
            'version': 1,
            'profiles': profiles_data,
            'settings': _redact_settings(settings_data),
        }

        data = json.dumps(backup, indent=2, default=str)
        return Response(
            data,
            mimetype='application/json',
            headers={'Content-Disposition': 'attachment; filename=lyrarr-backup.json'}
        )


@api_ns_backup.route('/system/restore')
class BackupRestore(Resource):
    def post(self):
        """Restore profiles and settings from JSON."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or 'version' not in data:
            return {'message': 'Invalid backup file'}, 400
        if not isinstance(data.get('settings', {}), dict) or not isinstance(data.get('profiles', []), list) \
                or not all(isinstance(p, dict) for p in data.get('profiles', [])):
            return {'message': 'Invalid backup file'}, 400

        restored_profiles = 0
        restored_settings = 0

        # Restore settings first: it validates everything and applies nothing
        # on error, so a bad/tampered file is refused before profiles change.
        # Merged key-by-key into the live settings (then persisted), so the
        # next settings save can't revert it. Exports redact the password and
        # the flask secret key — blank secrets keep the current value instead
        # of wiping it (which would lock the user out). Unknown keys from
        # other versions are skipped; Dynaconf '@' tokens are refused.
        notes = ''
        if data.get('settings'):
            sections = dict(data['settings'])
            auth = sections.get('auth')
            if isinstance(auth, dict) and auth.get('type') and not auth.get('password') \
                    and not getattr(settings.auth, 'password', ''):
                # The export has no password and none is set here: enabling
                # auth would lock everyone out, so leave auth off for now.
                sections['auth'] = {k: v for k, v in auth.items() if k != 'type'}
                notes = ' (authentication left disabled: set a password in Settings to enable it)'
            items = list(sections.items())
            try:
                result = save_settings(items, unknown='skip', keep_blank_secrets=True)
            except SettingsError as e:
                return {'message': f'Backup settings rejected: {e}'}, 400
            restored_settings = len(items)

            from lyrarr.api.system import apply_settings_side_effects
            apply_settings_side_effects(result)

        # Restore profiles
        if data.get('profiles'):
            from datetime import datetime

            from sqlalchemy.dialects.sqlite import insert

            from lyrarr.app.database import update

            for p in data['profiles']:
                existing = database.execute(
                    select(TableProfiles).where(TableProfiles.name == p.get('name'))
                ).scalars().first()

                values = {
                    'name': p.get('name', 'Unnamed'),
                    'download_covers': p.get('download_covers', True),
                    'download_lyrics': p.get('download_lyrics', True),
                    'cover_providers': p.get('cover_providers', '["musicbrainz","deezer","itunes","fanart","theaudiodb"]'),
                    'lyrics_providers': p.get('lyrics_providers', '["lrclib","musixmatch","netease","genius"]'),
                    'prefer_synced_lyrics': p.get('prefer_synced_lyrics', True),
                    'lyrics_selection_mode': p.get('lyrics_selection_mode', 'best_score'),
                    'auto_detect_language': p.get('auto_detect_language', True),
                    'auto_translate': p.get('auto_translate', 'off'),
                    'translate_target_lang': p.get('translate_target_lang', 'en'),
                    'translate_only_foreign': p.get('translate_only_foreign', True),
                    'score_threshold': p.get('score_threshold', 0),
                    'cover_format': p.get('cover_format', 'jpg'),
                    'overwrite_existing': p.get('overwrite_existing', False),
                    'embed_cover_art': p.get('embed_cover_art', False),
                    'is_default': p.get('is_default', False),
                    'updated_at_timestamp': datetime.now(),
                }

                if existing:
                    database.execute(
                        update(TableProfiles)
                        .where(TableProfiles.id == existing.id)
                        .values(**values)
                    )
                else:
                    values['created_at_timestamp'] = datetime.now()
                    database.execute(insert(TableProfiles).values(**values))

                restored_profiles += 1

        return {
            'message': f'Restored {restored_profiles} profiles and {restored_settings} setting sections{notes}',
        }


def run_scheduled_backup():
    """Standalone backup function for scheduler — saves a backup to disk and cleans old ones."""
    import logging
    import os
    from datetime import datetime, timedelta

    logger = logging.getLogger(__name__)

    try:
        backup_dir = settings.backup.folder
        retention_days = settings.backup.retention  # how many days to keep backups
        os.makedirs(backup_dir, exist_ok=True)

        # Build backup data
        profiles = database.execute(select(TableProfiles)).scalars().all()
        profiles_data = [p.to_dict() for p in profiles]

        settings_data = {}
        for section in ['general', 'lidarr', 'fanart', 'genius', 'notifications', 'auth', 'metadata',
                        'musixmatch', 'theaudiodb', 'backup']:
            try:
                section_obj = getattr(settings, section, None)
                if section_obj:
                    settings_data[section] = dict(section_obj)
            except Exception:
                pass

        backup = {
            'version': 1,
            'timestamp': datetime.now().isoformat(),
            'profiles': profiles_data,
            'settings': _redact_settings(settings_data),
        }

        # Write backup file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'lyrarr-backup-{timestamp}.json'
        filepath = os.path.join(backup_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(backup, f, indent=2, default=str)

        logger.info(f"Backup saved: {filepath}")

        # Clean old backups
        if retention_days > 0:
            cutoff = datetime.now() - timedelta(days=retention_days)
            for fname in os.listdir(backup_dir):
                if fname.startswith('lyrarr-backup-') and fname.endswith('.json'):
                    fpath = os.path.join(backup_dir, fname)
                    fstat = os.stat(fpath)
                    file_time = datetime.fromtimestamp(fstat.st_mtime)
                    if file_time < cutoff:
                        os.remove(fpath)
                        logger.info(f"Removed old backup: {fname}")

    except Exception as e:
        logger.error(f"Scheduled backup failed: {e}")
