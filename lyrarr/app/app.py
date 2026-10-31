import hashlib
import hmac
import logging
import os
import threading
import time
from collections import defaultdict, deque

from flask import Flask, g, jsonify, request, session

# Largest accepted request body. Uploads are cover images and lyrics files;
# the biggest JSON body is a settings/profiles backup restore (a few KB).
# 50 MB leaves room for very large original-size cover art.
MAX_CONTENT_LENGTH = 50 * 1024 * 1024

# Each open /api/events stream pins one of waitress' 50 worker threads.
MAX_SSE_STREAMS_PER_IP = 10
MAX_SSE_STREAMS_TOTAL = 25

# Failed logins (form login or wrong Basic credentials) allowed per IP.
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SECONDS = 15 * 60

UNSAFE_METHODS = frozenset(['POST', 'PUT', 'PATCH', 'DELETE'])


class RateLimiter:
    """Simple in-memory per-IP sliding window rate limiter."""

    def __init__(self, max_requests=120, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_prune = time.time()

    def _expire(self, ip, now):
        hits = self._requests.get(ip)
        if hits is None:
            return None
        cutoff = now - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def _prune(self, now):
        """Drop IPs with no hits inside the window so the table can't grow forever."""
        if now - self._last_prune < self.window:
            return
        self._last_prune = now
        for ip in list(self._requests):
            if not self._expire(ip, now):
                del self._requests[ip]

    def is_allowed(self, ip):
        """Count a request for ip; returns (allowed, remaining, retry_after)."""
        now = time.time()
        with self._lock:
            self._prune(now)
            hits = self._expire(ip, now) or self._requests[ip]
            if len(hits) >= self.max_requests:
                return False, 0, hits[0] + self.window - now
            hits.append(now)
            return True, self.max_requests - len(hits), 0

    def remaining(self, ip):
        with self._lock:
            hits = self._expire(ip, time.time())
            return max(0, self.max_requests - len(hits or ()))

    def retry_after(self, ip):
        """Seconds until ip may try again (0 when it isn't over the limit)."""
        now = time.time()
        with self._lock:
            hits = self._expire(ip, now)
            if not hits or len(hits) < self.max_requests:
                return 0
            return hits[0] + self.window - now

    def record(self, ip):
        """Count a hit without checking (e.g. a failed login)."""
        now = time.time()
        with self._lock:
            self._prune(now)
            self._requests[ip].append(now)

    def reset(self, ip):
        with self._lock:
            self._requests.pop(ip, None)

    def __len__(self):
        return len(self._requests)


class StreamLimiter:
    """Counts concurrent long-lived streams per IP and overall."""

    def __init__(self, per_ip, total):
        self.per_ip = per_ip
        self.total = total
        self._open = defaultdict(int)
        self._lock = threading.Lock()

    def acquire(self, ip):
        with self._lock:
            if self._open[ip] >= self.per_ip or sum(self._open.values()) >= self.total:
                if not self._open[ip]:
                    del self._open[ip]
                return False
            self._open[ip] += 1
            return True

    def release(self, ip):
        with self._lock:
            self._open[ip] -= 1
            if self._open[ip] <= 0:
                del self._open[ip]


def _check_credentials(username, password):
    """Check if username/password match the configured auth credentials.

    The password is stored as a werkzeug hash; check_password_hash is itself
    constant-time. A legacy plaintext value (from before hashing) is still
    accepted via constant-time compare for backward compatibility — it gets
    upgraded to a hash the next time the password is saved.
    """
    from werkzeug.security import check_password_hash

    from lyrarr.app.config import _is_password_hash, settings
    conf_user = getattr(settings.auth, 'username', '') or ''
    conf_pass = getattr(settings.auth, 'password', '') or ''
    if not conf_user or not conf_pass:
        return False
    if not isinstance(username, str) or not isinstance(password, str):
        return False

    user_match = hmac.compare_digest(username.encode('utf-8'), conf_user.encode('utf-8'))
    if _is_password_hash(conf_pass):
        pass_match = check_password_hash(conf_pass, password)
    else:
        pass_match = hmac.compare_digest(password.encode('utf-8'), conf_pass.encode('utf-8'))
    return user_match and pass_match


def _credentials_fingerprint():
    """Opaque tag of the current username + password (hash).

    Stored in the session at login; a session whose tag no longer matches was
    issued for old credentials and is rejected, so changing the password logs
    out every other session. Keyed with the secret key because Flask session
    cookies are signed, not encrypted — the tag must not reveal the hash.
    """
    from lyrarr.app.config import settings
    conf_user = getattr(settings.auth, 'username', '') or ''
    conf_pass = getattr(settings.auth, 'password', '') or ''
    key = str(settings.general.flask_secret_key).encode('utf-8')
    msg = f'{conf_user}\0{conf_pass}'.encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def _session_valid():
    if not session.get('authenticated'):
        return False
    tag = session.get('cred', '')
    return isinstance(tag, str) and hmac.compare_digest(tag, _credentials_fingerprint())


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


def _client_ip():
    return request.remote_addr or '127.0.0.1'


# API paths (relative to the API prefix) that never require auth.
# NOTE: /webhook/lidarr is deliberately NOT exempt. When auth is enabled it
# must be authenticated (via the X-API-KEY header or Basic auth, both handled in
# check_authentication) so an unauthenticated caller can't spam it to force
# repeated Lidarr syncs. Configure the Lidarr Webhook connection to send the API
# key (or Basic credentials). When no auth is configured it stays open, matching
# the rest of the app.
# /events is NOT exempt either: EventSource sends session cookies (and cached
# Basic credentials) automatically, so authenticated browsers stream fine,
# while anonymous clients can't watch library activity.
AUTH_EXEMPT_API_PATHS = frozenset([
    '/auth/login',
    '/auth/status',
    '/auth/logout',
])


def create_app():
    app = Flask(__name__)

    from lyrarr.app.config import normalize_base_url, settings
    app.config['SECRET_KEY'] = settings.general.flask_secret_key
    app.config['JSON_SORT_KEYS'] = False
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # Secure cookies only when explicitly served over HTTPS — forcing it would
    # break logins for plain-http LAN setups.
    if os.environ.get('LYRARR_HTTPS', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        app.config['SESSION_COOKIE_SECURE'] = True
    app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

    # The API blueprint is mounted at <base_url>/api (server.py); the gate
    # below must protect exactly that prefix. Plain /api is covered too, for
    # routes registered without the base_url.
    base = normalize_base_url(settings.general.base_url)
    api_prefix = base + '/api'
    api_prefixes = (api_prefix, '/api') if base else ('/api',)
    events_paths = frozenset(p + '/events' for p in api_prefixes)
    static_paths = frozenset([base + '/', base + '/favicon.ico', base + '/manifest.json',
                              '/', '/favicon.ico', '/manifest.json'])
    static_prefixes = tuple(sorted({base + '/assets/', '/assets/'}))

    def api_subpath(path):
        """Path below the API prefix ('/system/status'), or None for non-API paths."""
        for prefix in api_prefixes:
            if path == prefix:
                return '/'
            if path.startswith(prefix + '/'):
                return path[len(prefix):]
        return None

    if not _get_auth_type():
        logging.warning(
            "Authentication is DISABLED: anyone who can reach this Lyrarr instance can change its "
            "settings and files. Enable Settings > Authentication if it is reachable beyond a trusted network.")

    rate_limiter = RateLimiter()
    login_limiter = RateLimiter(max_requests=LOGIN_MAX_FAILURES, window_seconds=LOGIN_WINDOW_SECONDS)
    sse_limiter = StreamLimiter(MAX_SSE_STREAMS_PER_IP, MAX_SSE_STREAMS_TOTAL)

    def too_many_logins(ip):
        wait = login_limiter.retry_after(ip)
        resp = jsonify({'message': 'Too many failed login attempts. Try again later.', 'authenticated': False})
        resp.status_code = 429
        resp.headers['Retry-After'] = str(int(wait) + 1)
        return resp

    # ---------- Auth endpoints ----------

    @app.route(api_prefix + '/auth/status')
    def auth_status():
        """Return current auth config and session status."""
        auth_type = _get_auth_type()
        authenticated = False

        if not auth_type:
            # No auth configured — always authenticated
            authenticated = True
        elif auth_type == 'form':
            authenticated = _session_valid()
        elif auth_type == 'basic':
            authenticated = True  # Basic auth is checked per-request

        return jsonify({
            'authType': auth_type,
            'authenticated': authenticated,
            'username': session.get('username', '') if authenticated else '',
        })

    @app.route(api_prefix + '/auth/login', methods=['POST'])
    def auth_login():
        """Authenticate user and create session."""
        ip = _client_ip()
        if login_limiter.retry_after(ip):
            return too_many_logins(ip)

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        username = data.get('username', '')
        password = data.get('password', '')

        if _check_credentials(username, password):
            login_limiter.reset(ip)
            session.clear()
            session['authenticated'] = True
            session['username'] = username
            session['cred'] = _credentials_fingerprint()
            session.permanent = True
            return jsonify({'message': 'Login successful', 'authenticated': True})

        login_limiter.record(ip)
        return jsonify({'message': 'Invalid username or password', 'authenticated': False}), 401

    @app.route(api_prefix + '/auth/logout', methods=['POST'])
    def auth_logout():
        """Clear the session."""
        session.clear()
        return jsonify({'message': 'Logged out', 'authenticated': False})

    # ---------- Error responses ----------

    @app.errorhandler(413)
    def request_too_large(_error):
        limit = app.config.get('MAX_CONTENT_LENGTH') or MAX_CONTENT_LENGTH
        return jsonify({'message': f'Request too large (limit {limit / (1024 * 1024):g} MB)'}), 413

    # ---------- Request guards ----------

    @app.before_request
    def check_request_size():
        """Refuse oversized bodies up front (werkzeug also enforces the limit
        lazily for chunked bodies)."""
        limit = app.config.get('MAX_CONTENT_LENGTH')
        if limit and request.content_length is not None and request.content_length > limit:
            return request_too_large(None)

    @app.before_request
    def check_csrf():
        """Refuse cross-site form posts to the API.

        State-changing requests must carry something a plain HTML form (or
        other "simple" cross-origin request) can't: a valid X-API-KEY, a JSON
        content type, or X-Requested-With (the frontend always sends it).
        Applies whether or not auth is enabled.
        """
        if request.method not in UNSAFE_METHODS or api_subpath(request.path) is None:
            return
        if request.mimetype == 'application/json':
            return
        if request.headers.get('X-Requested-With', '').lower() == 'xmlhttprequest':
            return
        if _is_api_key_valid(request.headers.get('X-API-KEY', '')):
            return
        return jsonify({'message': 'Cross-site request refused: send JSON or the X-Requested-With header'}), 403

    @app.before_request
    def check_authentication():
        """Enforce authentication on all requests based on auth.type setting."""
        path = request.path
        subpath = api_subpath(path)

        # Login/status/logout are always reachable (see AUTH_EXEMPT_API_PATHS).
        # The Lidarr webhook is intentionally NOT here — it honors auth below.
        if subpath in AUTH_EXEMPT_API_PATHS:
            return

        # Static files (frontend assets) don't need auth
        if subpath is None and (path.startswith(static_prefixes) or path in static_paths):
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
            # Everything under the API prefix needs a session or API key
            if subpath is not None:
                if not _session_valid():
                    if session.get('authenticated'):
                        # Issued before a password change — drop it.
                        session.clear()
                    return jsonify({'message': 'Unauthorized — login required'}), 401
            else:
                # SPA routes — if not authenticated, the frontend will redirect to login
                # We serve the SPA regardless and let React handle the redirect
                pass

        elif auth_type == 'basic':
            auth = request.authorization
            ip = _client_ip()
            if auth and login_limiter.retry_after(ip):
                return too_many_logins(ip)
            if not auth or not _check_credentials(auth.username, auth.password):
                if auth:
                    login_limiter.record(ip)
                return (
                    jsonify({'message': 'Unauthorized'}),
                    401,
                    {'WWW-Authenticate': 'Basic realm="Lyrarr"'}
                )

    @app.before_request
    def rate_limit_check():
        """Apply rate limiting to API requests; cap concurrent SSE streams."""
        path = request.path
        if api_subpath(path) is None:
            return

        ip = _client_ip()
        if path in events_paths:
            # Streams are long-lived, so they're capped by concurrency instead
            # of request rate. The slot is released when the response closes.
            if not sse_limiter.acquire(ip):
                return jsonify({'message': 'Too many open event streams'}), 429
            g.sse_slot_ip = ip
            return

        allowed, remaining, retry_after = rate_limiter.is_allowed(ip)

        if not allowed:
            resp = jsonify({'message': 'Rate limit exceeded. Try again later.'})
            resp.status_code = 429
            resp.headers['X-RateLimit-Remaining'] = '0'
            resp.headers['Retry-After'] = str(int(retry_after) + 1)
            return resp

    @app.after_request
    def add_rate_limit_headers(response):
        """Add rate limit headers to API responses; hand SSE slots to the response."""
        slot_ip = g.pop('sse_slot_ip', None)
        if slot_ip is not None:
            # The stream outlives the request context: release on close.
            response.call_on_close(lambda: sse_limiter.release(slot_ip))
            return response

        if api_subpath(request.path) is not None and request.path not in events_paths:
            ip = _client_ip()
            response.headers['X-RateLimit-Limit'] = str(rate_limiter.max_requests)
            response.headers['X-RateLimit-Remaining'] = str(rate_limiter.remaining(ip))
        return response

    @app.teardown_request
    def release_unclaimed_sse_slot(_exc):
        # The request failed before after_request handed the slot over.
        slot_ip = g.pop('sse_slot_ip', None)
        if slot_ip is not None:
            sse_limiter.release(slot_ip)

    app.extensions['lyrarr_limiters'] = {
        'rate': rate_limiter, 'login': login_limiter, 'sse': sse_limiter,
    }
    return app
