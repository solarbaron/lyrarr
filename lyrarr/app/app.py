# coding=utf-8

from flask import Flask, request, jsonify


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'lyrarr-secret-key'
    app.config['JSON_SORT_KEYS'] = False

    @app.before_request
    def check_api_key():
        """Enforce API key auth on /api/* routes if configured."""
        if not request.path.startswith('/api'):
            return
        # Skip SSE endpoint (needs open connection)
        if request.path == '/api/events':
            return
        try:
            from lyrarr.app.config import settings
            api_key = settings.auth.api_key
            if api_key:
                provided = request.headers.get('X-API-KEY', '')
                if provided != api_key:
                    return jsonify({'message': 'Unauthorized — invalid or missing API key'}), 401
        except Exception:
            pass

    return app
