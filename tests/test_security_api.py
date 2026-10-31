"""Security regression tests for the HTTP layer: CSRF, auth gate (incl.
base_url), session invalidation, login throttling, body/stream limits, the
Lidarr connection test, backup restore and notifier log redaction."""

import copy
import json
import logging
import os
import sys

import pytest
from werkzeug.security import generate_password_hash

REPO_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
XRW = {'X-Requested-With': 'XMLHttpRequest'}


@pytest.fixture(scope='module')
def cfg(tmp_path_factory):
    # lyrarr.app.config parses argv and loads config.yaml at import time; point
    # it at a throwaway dir unless another test module already imported it.
    if 'lyrarr.app.config' not in sys.modules:
        saved = sys.argv
        sys.argv = [saved[0], '--config', str(tmp_path_factory.mktemp('lyrarr')), '--no-tasks', '--no-signalr']
        try:
            import lyrarr.app.config  # noqa: F401
        finally:
            sys.argv = saved
    from lyrarr.app import config
    from lyrarr.app.get_args import args
    if os.path.abspath(args.config_dir) == os.path.abspath(REPO_DATA):
        pytest.skip('config was loaded from the repo data/ dir; refusing to modify it')
    from lyrarr.app.database import init_db
    init_db()
    return config


@pytest.fixture(autouse=True)
def restore_settings(cfg):
    snapshot = {path: copy.deepcopy(cfg.settings.get(path)) for path in cfg._schema}
    yield
    for path, value in snapshot.items():
        if cfg.settings.get(path) != value:
            cfg.settings[path] = value
    cfg.write_config()


def make_app(cfg, with_api=False):
    """A fresh app (fresh limiters) with the real guards, optionally the real
    API, and a trivial /_probe endpoint under the API prefix."""
    from lyrarr.app.app import create_app
    app = create_app()
    app.testing = True
    prefix = cfg.normalize_base_url(cfg.settings.general.base_url) + '/api'
    if with_api:
        from lyrarr.api import api_bp
        app.register_blueprint(api_bp, url_prefix=prefix)

    @app.route(prefix + '/_probe', methods=['GET', 'POST', 'DELETE'])
    def probe():
        return {'ok': True}
    return app


def enable_auth(cfg, auth_type='form', password='secret'):
    cfg.settings['auth.username'] = 'admin'
    cfg.settings['auth.password'] = generate_password_hash(password)
    cfg.settings['auth.type'] = auth_type


def login(client, password='secret', prefix=''):
    return client.post(prefix + '/api/auth/login', json={'username': 'admin', 'password': password})


# ---------- CSRF ----------

@pytest.mark.parametrize('path', [
    '/api/albums/1/upload-cover',
    '/api/metadata/lyrics/upload/1',
    '/api/metadata/lyrics/validate-lrc/1',
    '/api/metadata/lyrics/publish/1',
    '/api/system/sync',
    '/api/metadata/lyrics/import-sidecar',
    '/api/metadata/lyrics/audit',
    '/api/system/test/notification',
    '/api/webhook/lidarr',
    '/api/system/settings',
    '/api/audit/runs',
    '/api/audit/runs/1/apply',
    '/api/audit/runs/1/cancel',
    '/api/audit/ignore',
    '/api/audit/unignore',
    '/api/tracks/instrumental',
])
def test_csrf_form_posts_refused(cfg, path):
    client = make_app(cfg, with_api=True).test_client()
    resp = client.post(path, data='a=b', content_type='application/x-www-form-urlencoded')
    assert resp.status_code == 403
    resp = client.post(path, data='a=b', content_type='text/plain')
    assert resp.status_code == 403


def test_csrf_allows_json_xrw_and_api_key(cfg):
    client = make_app(cfg).test_client()
    assert client.post('/api/_probe', json={}).status_code == 200
    assert client.post('/api/_probe', data='a=b', headers=XRW).status_code == 200
    assert client.delete('/api/_probe', headers=XRW).status_code == 200
    key = {'X-API-KEY': cfg.settings.auth.apikey}
    assert client.post('/api/_probe', data='a=b', headers=key).status_code == 200
    assert client.post('/api/_probe', data='a=b', headers={'X-API-KEY': 'wrong'}).status_code == 403
    assert client.delete('/api/_probe').status_code == 403
    assert client.get('/api/_probe').status_code == 200


def test_csrf_multipart_needs_header(cfg):
    client = make_app(cfg).test_client()
    data = {'file': (__import__('io').BytesIO(b'x'), 'a.lrc')}
    assert client.post('/api/_probe', data=data, content_type='multipart/form-data').status_code == 403
    data = {'file': (__import__('io').BytesIO(b'x'), 'a.lrc')}
    resp = client.post('/api/_probe', data=data, content_type='multipart/form-data', headers=XRW)
    assert resp.status_code == 200


# ---------- system endpoints ----------

def test_tasks_post_without_body_is_400(cfg):
    client = make_app(cfg, with_api=True).test_client()
    assert client.post('/api/system/tasks', headers=XRW).status_code == 400
    assert client.post('/api/system/tasks', json=[1]).status_code == 400


def test_settings_post_rejects_jinja(cfg):
    from lyrarr.app.get_args import args
    client = make_app(cfg, with_api=True).test_client()
    resp = client.post('/api/system/settings', json={'settings-general-instance_name': '@jinja {{ 7 * 7 }}'})
    assert resp.status_code == 400
    assert cfg.settings.general.instance_name != '49'
    with open(os.path.join(args.config_dir, 'config', 'config.yaml')) as f:
        assert '@jinja' not in f.read()


def test_settings_post_blank_port_is_400(cfg):
    client = make_app(cfg, with_api=True).test_client()
    port = cfg.settings.general.port
    resp = client.post('/api/system/settings', json={'settings-general-port': ''})
    assert resp.status_code == 400
    assert cfg.settings.general.port == port


# ---------- auth gate ----------

def test_base_url_api_requires_auth(cfg):
    cfg.settings['general.base_url'] = '/lyrarr'
    enable_auth(cfg)
    client = make_app(cfg).test_client()
    assert client.get('/lyrarr/api/_probe').status_code == 401
    assert client.get('/api/_probe').status_code == 401
    status = client.get('/lyrarr/api/auth/status')
    assert status.status_code == 200 and status.json['authenticated'] is False
    assert client.get('/lyrarr/assets/app.js').status_code == 404  # not 401
    assert client.get('/lyrarr/settings').status_code != 401  # SPA route
    assert login(client, prefix='/lyrarr').status_code == 200
    assert client.get('/lyrarr/api/_probe').status_code == 200


def test_basic_auth_gates_everything_but_static(cfg):
    enable_auth(cfg, 'basic')
    client = make_app(cfg).test_client()
    assert client.get('/api/_probe').status_code == 401
    assert client.get('/api/_probe', auth=('admin', 'secret')).status_code == 200
    assert client.get('/api/auth/status').status_code == 200


def test_session_invalidated_by_password_change(cfg):
    enable_auth(cfg)
    client = make_app(cfg).test_client()
    assert login(client).status_code == 200
    assert client.get('/api/_probe').status_code == 200
    cfg.settings['auth.password'] = generate_password_hash('changed')
    assert client.get('/api/_probe').status_code == 401
    assert client.get('/api/auth/status').json['authenticated'] is False
    assert login(client, 'changed').status_code == 200
    assert client.get('/api/_probe').status_code == 200


def test_session_without_fingerprint_rejected(cfg):
    enable_auth(cfg)
    client = make_app(cfg).test_client()
    with client.session_transaction() as sess:
        sess['authenticated'] = True  # e.g. a pre-upgrade session
    assert client.get('/api/_probe').status_code == 401


def test_login_bruteforce_throttled(cfg):
    from lyrarr.app.app import LOGIN_MAX_FAILURES
    enable_auth(cfg)
    client = make_app(cfg).test_client()
    for _ in range(LOGIN_MAX_FAILURES):
        assert login(client, 'wrong').status_code == 401
    resp = login(client)
    assert resp.status_code == 429
    assert int(resp.headers['Retry-After']) > 0
    # Another IP is unaffected.
    other = client.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'},
                        environ_base={'REMOTE_ADDR': '10.9.9.9'})
    assert other.status_code == 200


def test_basic_auth_bruteforce_throttled(cfg):
    from lyrarr.app.app import LOGIN_MAX_FAILURES
    enable_auth(cfg, 'basic')
    client = make_app(cfg).test_client()
    for _ in range(LOGIN_MAX_FAILURES):
        assert client.get('/api/_probe', auth=('admin', 'nope')).status_code == 401
    assert client.get('/api/_probe', auth=('admin', 'secret')).status_code == 429


def test_rate_limiter_prunes_idle_ips(monkeypatch):
    from lyrarr.app import app as app_module
    now = [1000.0]
    monkeypatch.setattr(app_module.time, 'time', lambda: now[0])
    limiter = app_module.RateLimiter(max_requests=2, window_seconds=60)
    for i in range(50):
        limiter.is_allowed(f'10.0.0.{i}')
    assert len(limiter) == 50
    assert limiter.is_allowed('a')[0] and limiter.is_allowed('a')[0]
    assert not limiter.is_allowed('a')[0]
    now[0] += 61
    assert limiter.is_allowed('b')[0]
    assert len(limiter) == 1


# ---------- limits ----------

def test_request_body_limit_returns_json_413(cfg):
    from lyrarr.app.app import MAX_CONTENT_LENGTH
    app = make_app(cfg)
    assert app.config['MAX_CONTENT_LENGTH'] == MAX_CONTENT_LENGTH
    app.config['MAX_CONTENT_LENGTH'] = 100
    resp = app.test_client().post('/api/_probe', json={'x': 'y' * 500})
    assert resp.status_code == 413
    assert 'too large' in resp.json['message']


def test_sse_streams_capped_per_ip(cfg):
    from flask import Response
    app = make_app(cfg)

    @app.route('/api/events')
    def events():
        def gen():
            while True:
                yield ': keepalive\n\n'
        return Response(gen(), mimetype='text/event-stream')

    app.extensions['lyrarr_limiters']['sse'].per_ip = 2
    client = app.test_client()
    first = client.get('/api/events', buffered=False)
    second = client.get('/api/events', buffered=False)
    assert first.status_code == second.status_code == 200
    assert client.get('/api/events', buffered=False).status_code == 429
    first.close()
    third = client.get('/api/events', buffered=False)
    assert third.status_code == 200
    second.close()
    third.close()


# ---------- Lidarr connection test (SSRF) ----------

class _Resp:
    status_code = 200

    def json(self):
        return {'version': '2.0'}


@pytest.fixture
def lidarr_calls(cfg, monkeypatch):
    import requests
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Resp()
    monkeypatch.setattr(requests, 'get', fake_get)
    cfg.settings['lidarr.ip'] = '10.0.0.5'
    cfg.settings['lidarr.port'] = 8686
    cfg.settings['lidarr.base_url'] = '/'
    cfg.settings['lidarr.ssl'] = False
    cfg.settings['lidarr.apikey'] = 'STOREDKEY'
    return calls


def test_lidarr_test_does_not_leak_stored_key(cfg, lidarr_calls):
    client = make_app(cfg, with_api=True).test_client()
    for body in ({'ip': 'evil.example'}, {'port': 9999}, {'ssl': True}, {'base_url': '/x'},
                 {'ip': 'evil.example', 'apikey': ''}):
        assert client.post('/api/system/test/lidarr', json=body).status_code == 400, body
    assert lidarr_calls == []


def test_lidarr_test_uses_stored_key_for_stored_target(cfg, lidarr_calls):
    client = make_app(cfg, with_api=True).test_client()
    assert client.post('/api/system/test/lidarr', json={}).status_code == 200
    url, kwargs = lidarr_calls[-1]
    assert url == 'http://10.0.0.5:8686/api/v1/system/status'
    assert kwargs['headers']['X-Api-Key'] == 'STOREDKEY'
    assert kwargs['allow_redirects'] is False
    resp = client.post('/api/system/test/lidarr', json={'ip': 'other.lan', 'apikey': 'GIVEN'})
    assert resp.status_code == 200
    assert lidarr_calls[-1][0].startswith('http://other.lan:8686/')
    assert lidarr_calls[-1][1]['headers']['X-Api-Key'] == 'GIVEN'


@pytest.mark.parametrize('ip', ['http://evil', 'evil/x', 'user@evil', 'evil host', ''])
def test_lidarr_test_rejects_bad_host(cfg, lidarr_calls, ip):
    client = make_app(cfg, with_api=True).test_client()
    assert client.post('/api/system/test/lidarr', json={'ip': ip, 'apikey': 'k'}).status_code == 400
    assert lidarr_calls == []


# ---------- backup restore ----------

def test_restore_own_export_keeps_secrets_and_applies_live(cfg):
    from lyrarr.app.get_args import args
    enable_auth(cfg)
    password_hash = cfg.settings.auth.password
    secret_key = cfg.settings.general.flask_secret_key
    client = make_app(cfg, with_api=True).test_client()
    login(client)

    backup = json.loads(client.get('/api/system/backup').data)
    assert 'password' not in backup['settings']['auth']
    backup['settings']['general']['instance_name'] = 'Restored Name'
    resp = client.post('/api/system/restore', json=backup)
    assert resp.status_code == 200, resp.json

    assert cfg.settings.general.instance_name == 'Restored Name'
    assert cfg.settings.auth.password == password_hash
    assert cfg.settings.general.flask_secret_key == secret_key
    with open(os.path.join(args.config_dir, 'config', 'config.yaml')) as f:
        assert 'Restored Name' in f.read()
    # Still logged in, and a later settings save doesn't revert the restore.
    assert client.post('/api/system/settings', json={'settings-general-theme': 'dark'}).status_code == 200
    assert cfg.settings.general.instance_name == 'Restored Name'


def test_restore_rejects_dynaconf_tokens(cfg):
    from lyrarr.app.get_args import args
    client = make_app(cfg, with_api=True).test_client()
    name = cfg.settings.general.instance_name
    for settings_data in ({'general': {'instance_name': '@jinja {{ 7 * 7 }}'}},
                          {'dynaconf_include': ['/tmp/evil.py']},
                          {'general': {'dynaconf_merge': True}}):
        resp = client.post('/api/system/restore', json={'version': 1, 'settings': settings_data})
        assert resp.status_code == 400, settings_data
    assert cfg.settings.general.instance_name == name
    with open(os.path.join(args.config_dir, 'config', 'config.yaml')) as f:
        text = f.read()
    assert '@jinja' not in text and 'dynaconf' not in text


def test_restore_skips_unknown_keys_and_validates(cfg):
    client = make_app(cfg, with_api=True).test_client()
    resp = client.post('/api/system/restore', json={'version': 1, 'settings': {
        'general': {'old_removed_option': 1, 'theme': 'dark'}}})
    assert resp.status_code == 200
    assert cfg.settings.general.theme == 'dark'
    port = cfg.settings.general.port
    resp = client.post('/api/system/restore', json={'version': 1, 'settings': {'general': {'port': 'abc'}}})
    assert resp.status_code == 400
    assert cfg.settings.general.port == port


def test_restore_does_not_enable_auth_without_password(cfg):
    cfg.settings['auth.password'] = ''
    cfg.settings['auth.type'] = None
    client = make_app(cfg, with_api=True).test_client()
    resp = client.post('/api/system/restore', json={'version': 1, 'settings': {
        'auth': {'type': 'form', 'username': 'admin'}}})
    assert resp.status_code == 200
    assert cfg.settings.auth.type is None


def test_restore_profiles_still_work(cfg):
    from lyrarr.app.database import TableProfiles, database, select
    client = make_app(cfg, with_api=True).test_client()
    resp = client.post('/api/system/restore', json={'version': 1, 'profiles': [{'name': 'SecTestProfile'}]})
    assert resp.status_code == 200
    row = database.execute(select(TableProfiles).where(TableProfiles.name == 'SecTestProfile')).scalars().first()
    assert row is not None


# ---------- notifier ----------

def test_notifier_errors_do_not_log_tokens(cfg, monkeypatch, caplog):
    import requests

    from lyrarr.app import notifier

    def boom(url, **kwargs):
        raise requests.exceptions.ConnectionError(f"Max retries exceeded with url: {url}")
    monkeypatch.setattr(requests, 'post', boom)
    with caplog.at_level(logging.ERROR, logger='lyrarr.app.notifier'):
        notifier._send_telegram('123456:SECRET-TOKEN_x', '42', 't', 'm')
        notifier._send_discord('https://discord.com/api/webhooks/987/WEBHOOKSECRET', 't', 'm')
    assert 'SECRET-TOKEN' not in caplog.text
    assert 'WEBHOOKSECRET' not in caplog.text
    assert 'Telegram notification error' in caplog.text


def test_redact_patterns():
    from lyrarr.app.notifier import _redact
    assert _redact('https://api.telegram.org/bot1:abc-DEF/sendMessage') == 'https://api.telegram.org/bot***/sendMessage'
    assert _redact('https://discord.com/api/webhooks/1/tok.en') == 'https://discord.com/api/webhooks/1/***'
