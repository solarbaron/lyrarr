# coding=utf-8

"""
Backup and restore endpoints for profiles and settings.
"""

import json
from flask import request, Response
from flask_restx import Namespace, Resource
from lyrarr.app.database import database, TableProfiles, select
from lyrarr.app.config import settings

api_ns_backup = Namespace('backup', description='Backup and restore')


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
            'settings': settings_data,
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
        data = request.get_json()
        if not data or 'version' not in data:
            return {'message': 'Invalid backup file'}, 400

        restored_profiles = 0
        restored_settings = 0

        # Restore profiles
        if 'profiles' in data:
            from sqlalchemy.dialects.sqlite import insert
            from lyrarr.app.database import update
            from datetime import datetime

            for p in data['profiles']:
                existing = database.execute(
                    select(TableProfiles).where(TableProfiles.name == p.get('name'))
                ).scalars().first()

                values = {
                    'name': p.get('name', 'Unnamed'),
                    'download_covers': p.get('download_covers', 'True'),
                    'download_lyrics': p.get('download_lyrics', 'True'),
                    'cover_providers': p.get('cover_providers', '["musicbrainz","fanart"]'),
                    'lyrics_providers': p.get('lyrics_providers', '["lrclib","genius"]'),
                    'prefer_synced_lyrics': p.get('prefer_synced_lyrics', 'True'),
                    'cover_format': p.get('cover_format', 'jpg'),
                    'overwrite_existing': p.get('overwrite_existing', 'False'),
                    'embed_cover_art': p.get('embed_cover_art', 'False'),
                    'is_default': p.get('is_default', 'False'),
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

        # Restore settings (write to config file)
        if 'settings' in data:
            import os
            import yaml
            from lyrarr.app.get_args import args

            config_path = os.path.join(args.config_dir, 'config', 'config.yaml')
            try:
                with open(config_path, 'r') as f:
                    current_config = yaml.safe_load(f) or {}
            except Exception:
                current_config = {}

            for section, values in data['settings'].items():
                current_config[section] = values
                restored_settings += 1

            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w') as f:
                yaml.dump(current_config, f, default_flow_style=False)

        return {
            'message': f'Restored {restored_profiles} profiles and {restored_settings} setting sections',
        }
