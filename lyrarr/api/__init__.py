# coding=utf-8

from flask import Blueprint
from flask_restx import Api

from .artists import api_ns_artists
from .albums import api_ns_albums
from .tracks import api_ns_tracks
from .metadata import api_ns_metadata
from .history import api_ns_history
from .wanted import api_ns_wanted
from .system import api_ns_system
from .profiles import api_ns_profiles
from .search import api_ns_search
from .dashboard import api_ns_dashboard
from .backup import api_ns_backup
from .webhook import api_ns_webhook

api_ns_list = [
    api_ns_artists,
    api_ns_albums,
    api_ns_tracks,
    api_ns_metadata,
    api_ns_history,
    api_ns_wanted,
    api_ns_system,
    api_ns_profiles,
    api_ns_search,
    api_ns_dashboard,
    api_ns_backup,
    api_ns_webhook,
]

authorizations = {
    'apikey': {
        'type': 'apiKey',
        'in': 'header',
        'name': 'X-API-KEY'
    }
}

api_bp = Blueprint('api', __name__, url_prefix='/api')

api = Api(api_bp, authorizations=authorizations, security='apikey',
          validate=True, title='Lyrarr API', version='1.0',
          description='Lyrarr - Lidarr Companion for Music Metadata')

for api_ns in api_ns_list:
    if isinstance(api_ns, list):
        for item in api_ns:
            api.add_namespace(item, "/")
    else:
        api.add_namespace(api_ns, "/")
