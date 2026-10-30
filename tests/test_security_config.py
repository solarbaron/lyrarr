"""Security regression tests for settings handling (lyrarr/app/config.py).

Dynaconf evaluates '@jinja'/'@format'/... string values and dynaconf_* keys,
so nothing user-supplied may reach the settings object unchecked.
"""

import copy
import os
import sys

import pytest

REPO_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')


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
    return config


@pytest.fixture(autouse=True)
def restore_settings(cfg):
    snapshot = {path: copy.deepcopy(cfg.settings.get(path)) for path in cfg._schema}
    yield
    for path, value in snapshot.items():
        if cfg.settings.get(path) != value:
            cfg.settings[path] = value
    cfg.write_config()


JINJA = "@jinja {{ cycler.__init__.__globals__.os.popen('echo pwned').read() }}"


@pytest.mark.parametrize('value', [
    JINJA,
    '@format {this.general.flask_secret_key}',
    '   @jinja {{ 7 * 7 }}',
    '@merge [1]',
])
def test_save_rejects_dynaconf_tokens(cfg, value):
    before = cfg.settings.general.instance_name
    with pytest.raises(cfg.SettingsError):
        cfg.save_settings([('settings-general-instance_name', value)])
    assert cfg.settings.general.instance_name == before


def test_save_rejects_tokens_inside_lists_and_dicts(cfg):
    with pytest.raises(cfg.SettingsError):
        cfg.save_settings([('settings-metadata-lyrics-providers', ['lrclib', '@jinja {{ 1 }}'])])
    with pytest.raises(cfg.SettingsError):
        cfg.save_settings([('settings-metadata-lyrics-providers', ['dynaconf_merge', 'lrclib'])])
    with pytest.raises(cfg.SettingsError):
        cfg.save_settings([('settings-metadata-covers', {'folder_art_filename': '@format {env}'})])


@pytest.mark.parametrize('key', [
    'settings-dynaconf_include',
    'settings-general-dynaconf_merge',
    'dynaconf_include',
    'settings-general-includes_for_dynaconf',
])
def test_save_rejects_reserved_keys(cfg, key):
    with pytest.raises(cfg.SettingsError):
        cfg.save_settings([(key, ['/tmp/evil.py'])])


def test_save_rejects_unknown_keys_but_tolerates_round_trip(cfg):
    with pytest.raises(cfg.SettingsError):
        cfg.save_settings([('settings-general-not_a_setting', 'x')])
    # A stale key still in config.yaml is posted back unchanged by the UI.
    cfg.settings['general.legacy_thing'] = 'old'
    result = cfg.save_settings([('settings-general-legacy_thing', 'old')])
    assert 'general.legacy_thing' in result['skipped']


def test_live_settings_do_not_evaluate_tokens(cfg):
    # Defence in depth: even if a token slipped through, it stays a literal.
    cfg.settings['general.instance_name'] = '@jinja {{ 7 * 7 }}'
    assert cfg.settings.general.instance_name == '@jinja {{ 7 * 7 }}'


def test_blank_port_is_rejected_not_stored(cfg):
    port = cfg.settings.general.port
    with pytest.raises(cfg.SettingsError):
        cfg.save_settings([('settings-general-port', '')])
    assert cfg.settings.general.port == port


def test_numeric_strings_coerced_by_declared_type(cfg):
    cfg.save_settings([('settings-general-port', '7001'), ('settings-general-instance_name', '123'),
                       ('settings-general-debug', 'false'), ('settings-general-default_profile_id', '')])
    assert cfg.settings.general.port == 7001
    assert cfg.settings.general.instance_name == '123'
    assert cfg.settings.general.debug is False
    assert cfg.settings.general.default_profile_id is None


def test_invalid_value_rolls_back_whole_batch(cfg):
    name = cfg.settings.general.instance_name
    page_size = cfg.settings.general.page_size
    with pytest.raises(cfg.SettingsError):
        cfg.save_settings([('settings-general-instance_name', 'Renamed'), ('settings-general-page_size', 33)])
    assert cfg.settings.general.instance_name == name
    assert cfg.settings.general.page_size == page_size


def test_nested_section_values_are_applied(cfg):
    cfg.save_settings([('settings-metadata-covers', {'folder_art_filename': 'folder', 'enabled': 'true'})])
    assert cfg.settings.metadata.covers.folder_art_filename == 'folder'
    assert cfg.settings.metadata.covers.enabled is True


def test_enabling_auth_without_credentials_is_refused(cfg):
    cfg.settings['auth.username'] = ''
    cfg.settings['auth.password'] = ''
    with pytest.raises(cfg.SettingsError):
        cfg.save_settings([('settings-auth-type', 'form')])
    assert cfg.settings.auth.type is None


def test_flask_secret_key_is_read_only(cfg):
    key = cfg.settings.general.flask_secret_key
    cfg.save_settings([('settings-general-flask_secret_key', '')])
    assert cfg.settings.general.flask_secret_key == key


@pytest.mark.parametrize('raw,expected', [
    ('', ''), ('/', ''), ('lyrarr', '/lyrarr'), ('/lyrarr/', '/lyrarr'), ('//a//b/', '/a/b'), (None, ''),
])
def test_normalize_base_url(cfg, raw, expected):
    assert cfg.normalize_base_url(raw) == expected


def test_save_normalizes_base_url(cfg):
    cfg.save_settings([('settings-general-base_url', 'lyrarr/')])
    assert cfg.settings.general.base_url == '/lyrarr'


def test_sanitize_config_file_strips_planted_tokens(cfg, tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text(
        "general:\n"
        "  instance_name: '@jinja {{ 7 * 7 }}'\n"
        "  port: 6868\n"
        "dynaconf_include:\n"
        "  - /tmp/evil.py\n"
        "metadata:\n"
        "  lyrics:\n"
        "    providers: [dynaconf_merge, lrclib, '@format {x}']\n"
    )
    cfg._sanitize_config_file(str(path))
    import yaml
    data = yaml.safe_load(path.read_text())
    assert data == {'general': {'port': 6868}, 'metadata': {'lyrics': {'providers': ['lrclib']}}}


def test_sanitize_config_file_leaves_clean_file_untouched(cfg, tmp_path):
    path = tmp_path / 'config.yaml'
    original = "general:\n  instance_name: 'My @ home'\n"
    path.write_text(original)
    cfg._sanitize_config_file(str(path))
    assert path.read_text() == original


@pytest.mark.parametrize('name', ['../../etc/cron.d/x', '/abs/cover', 'a\\b', '', '..'])
def test_folder_art_filename_must_be_plain(cfg, name):
    with pytest.raises(cfg.SettingsError):
        cfg.save_settings([('settings-metadata-covers-folder_art_filename', name)])
    assert cfg.settings.metadata.covers.folder_art_filename == 'cover'
