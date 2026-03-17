# coding=utf-8

import time
import hmac
import secrets
from collections import defaultdict
from flask import Flask, request, jsonify, session
from functools import wraps


class RateLimiter:
    """Simple in-memory per-IP sliding window rate limiter."""

    def __init__(self, max_requests=120, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests = defaultdict(list)

    def is_allowed(self, ip):
        now = time.time()
        cutoff = now - self.window
        # Remove expired entries
        self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]

        if len(self._requests[ip]) >= self.max_requests:
            return False, self.max_requests - len(self._requests[ip]), cutoff + self.window - now

        self._requests[ip].append(now)
        return True, self.max_requests - len(self._requests[ip]), 0


def _check_credentials(username, password):
    """Check if username/password match the configured auth credentials.
    Uses constant-time comparison to prevent timing attacks."""
    from lyrarr.app.config import settings
    conf_user = getattr(settings.auth, 'username', '') or ''
    conf_pass = getattr(settings.auth, 'password', '') or ''
    if not conf_user or not conf_pass:
        return False
    user_match = hmac.compare_digest(username.encode('utf-8'), conf_user.encode('utf-8'))
    pass_match = hmac.compare_digest(password.encode('utf-8'), conf_pass.encode('utf-8'))
    return user_match and pass_match


def _is_api_key_valid(provided_key):
    """Check if provided API key matches configured auth.apikey.
    Uses constant-time comparison to prevent timing attacks."""
    from lyrarr.app.config import settings
    api_key = getattr(settings.auth, 'apikey', '') or ''
    if not api_key or not provided_key:
        return False
    return hmac.compare_digest(provided_key.encode('utf-8'), api_key.encode('utf-8'))


def _get_auth_type():
    """Get the configured auth type (None, 'form', 'basic')."""
    from lyrarr.app.config import settings
    return getattr(settings.auth, 'type', None)


# Paths that should never require auth
AUTH_EXEMPT_PATHS = frozenset([
    '/api/auth/login',
    '/api/auth/status',
    '/api/auth/logout',
    '/api/events',
    '/api/webhook/lidarr',
])


def create_app():
    app = Flask(__name__)

    from lyrarr.app.config import settings
    app.config['SECRET_KEY'] = settings.general.flask_secret_key
    app.config['JSON_SORT_KEYS'] = False
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    rate_limiter = RateLimiter()

    # ---------- Auth endpoints ----------

    @app.route('/api/auth/status')
    def auth_status():
        """Return current auth config and session status."""
        auth_type = _get_auth_type()
        authenticated = False

        if not auth_type:
            # No auth configured — always authenticated
            authenticated = True
        elif auth_type == 'form':
            authenticated = session.get('authenticated', False)
        elif auth_type == 'basic':
            authenticated = True  # Basic auth is checked per-request

        return jsonify({
            'authType': auth_type,
            'authenticated': authenticated,
            'username': session.get('username', ''),
        })

    @app.route('/api/auth/login', methods=['POST'])
    def auth_login():
        """Authenticate user and create session."""
        data = request.get_json() or {}
        username = data.get('username', '')
        password = data.get('password', '')

        if _check_credentials(username, password):
            session['authenticated'] = True
            session['username'] = username
            session.permanent = True
            return jsonify({'message': 'Login successful', 'authenticated': True})

        return jsonify({'message': 'Invalid username or password', 'authenticated': False}), 401

    @app.route('/api/auth/logout', methods=['POST'])
    def auth_logout():
        """Clear the session."""
        session.clear()
        return jsonify({'message': 'Logged out', 'authenticated': False})

    # ---------- Auth middleware ----------

    @app.before_request
    def check_authentication():
        """Enforce authentication on all requests based on auth.type setting."""
        path = request.path

        # Auth endpoints and webhooks are always accessible
        if path in AUTH_EXEMPT_PATHS:
            return

        # Static files (frontend assets) don't need auth
        if path.startswith('/assets/') or path in ('/', '/favicon.ico', '/manifest.json'):
            return

        auth_type = _get_auth_type()

        # No auth configured — allow everything
        if not auth_type:
            return

        # API key header always works as a bypass (for external integrations)
        api_key = request.headers.get('X-API-KEY', '')
        if api_key and _is_api_key_valid(api_key):
            return

        if auth_type == 'form':
            # API routes need session or API key
            if request.path.startswith('/api'):
                if not session.get('authenticated'):
                    return jsonify({'message': 'Unauthorized — login required'}), 401
            else:
                # SPA routes — if not authenticated, the frontend will redirect to login
                # We serve the SPA regardless and let React handle the redirect
                pass

        elif auth_type == 'basic':
            auth = request.authorization
            if not auth or not _check_credentials(auth.username, auth.password):
                return (
                    jsonify({'message': 'Unauthorized'}),
                    401,
                    {'WWW-Authenticate': 'Basic realm="Lyrarr"'}
                )

    @app.before_request
    def rate_limit_check():
        """Apply rate limiting to API requests."""
        if not request.path.startswith('/api'):
            return
        if request.path == '/api/events':
            return

        ip = request.remote_addr or '127.0.0.1'
        allowed, remaining, retry_after = rate_limiter.is_allowed(ip)

        if not allowed:
            resp = jsonify({'message': 'Rate limit exceeded. Try again later.'})
            resp.status_code = 429
            resp.headers['X-RateLimit-Remaining'] = '0'
            resp.headers['Retry-After'] = str(int(retry_after) + 1)
            return resp

    @app.after_request
    def add_rate_limit_headers(response):
        """Add rate limit headers to API responses."""
        if request.path.startswith('/api') and request.path != '/api/events':
            ip = request.remote_addr or '127.0.0.1'
            now = time.time()
            cutoff = now - rate_limiter.window
            recent = [t for t in rate_limiter._requests.get(ip, []) if t > cutoff]
            remaining = max(0, rate_limiter.max_requests - len(recent))
            response.headers['X-RateLimit-Limit'] = str(rate_limiter.max_requests)
            response.headers['X-RateLimit-Remaining'] = str(remaining)
        return response

    return app

