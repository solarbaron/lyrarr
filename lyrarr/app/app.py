# coding=utf-8

import time
from collections import defaultdict
from flask import Flask, request, jsonify


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


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'lyrarr-secret-key'
    app.config['JSON_SORT_KEYS'] = False

    rate_limiter = RateLimiter()

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

