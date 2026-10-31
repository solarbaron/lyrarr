
import logging
import os
from binascii import hexlify
from ipaddress import ip_address
from shutil import move
from types import MappingProxyType

from dynaconf import Dynaconf, validator_conditions
from dynaconf import Validator as OriginalValidator
from dynaconf.loaders.yaml_loader import write
from dynaconf.utils.functional import empty
from dynaconf.validator import ValidationError

from .get_args import args

NoneType = type(None)


def base_url_slash_cleaner(uri):
    while "//" in uri:
        uri = uri.replace("//", "/")
    return uri


def normalize_base_url(uri):
    """Canonical form of general.base_url: '' or '/prefix' (leading slash, no
    trailing slash). The API blueprint and the auth gate are mounted under it,
    so both must agree on the exact spelling."""
    uri = str(uri or '').strip()
    return base_url_slash_cleaner('/' + uri).rstrip('/')


# ---------- Dynaconf injection guards ----------
# Dynaconf evaluates string values starting with '@' (@jinja, @format, @int,
# @merge, @del, ...) and honours special keys such as dynaconf_include /
# dynaconf_merge / *_for_dynaconf, both when loading config.yaml and when a
# value is set at runtime. @jinja/@format and dynaconf_include amount to code
# execution (CVE-2026-33154), so no such value may reach the settings object.

def is_reserved_settings_key(key):
    """Keys Dynaconf treats specially (dynaconf_include, dynaconf_merge, ...)."""
    return 'dynaconf' in str(key).lower()


def _is_merge_marker(value):
    """List entries like 'dynaconf_merge' switch Dynaconf into merge mode."""
    return isinstance(value, str) and value.strip().lower().startswith('dynaconf_')


def find_unsafe_value(value, path=''):
    """Return the path of the first Dynaconf token / reserved key inside
    ``value`` (recursively), or None when the value is safe to store."""
    if isinstance(value, str):
        return (path or '<value>') if value.lstrip().startswith('@') else None
    if isinstance(value, dict):
        for k, v in value.items():
            sub = f'{path}.{k}' if path else str(k)
            if is_reserved_settings_key(k):
                return sub
            found = find_unsafe_value(v, sub)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            if _is_merge_marker(v):
                return f'{path}[{i}]'
            found = find_unsafe_value(v, f'{path}[{i}]')
            if found:
                return found
    return None


def _strip_unsafe(data, path=''):
    """Copy of ``data`` with Dynaconf tokens and reserved keys removed.
    Returns (clean_data, removed_paths)."""
    removed = []
    if isinstance(data, dict):
        clean = {}
        for k, v in data.items():
            sub = f'{path}.{k}' if path else str(k)
            if is_reserved_settings_key(k) or (isinstance(v, str) and v.lstrip().startswith('@')):
                removed.append(sub)
                continue
            clean[k], sub_removed = _strip_unsafe(v, sub)
            removed.extend(sub_removed)
        return clean, removed
    if isinstance(data, list):
        clean = []
        for i, v in enumerate(data):
            if _is_merge_marker(v) or (isinstance(v, str) and v.lstrip().startswith('@')):
                removed.append(f'{path}[{i}]')
                continue
            item, sub_removed = _strip_unsafe(v, f'{path}[{i}]')
            clean.append(item)
            removed.extend(sub_removed)
        return clean, removed
    return data, removed


def _sanitize_config_file(path):
    """Drop Dynaconf tokens/reserved keys from config.yaml before Dynaconf
    reads it (a tampered file or an old backup restore could have planted
    them). Removed keys fall back to their validator defaults."""
    try:
        import yaml
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except Exception as error:
        logging.debug(f"Could not pre-scan {path}: {error}")
        return
    if not isinstance(data, dict):
        return
    clean, removed = _strip_unsafe(data)
    if not removed:
        return
    logging.warning(f"Removed unsafe entries from {path} (Dynaconf tokens are not allowed): {', '.join(removed)}")
    try:
        write(settings_path=path + '.tmp', settings_data=clean, merge=False)
        move(path + '.tmp', path)
    except Exception as error:
        logging.exception(f"Could not rewrite sanitized settings file: {error}")


def validate_ip_address(ip_string):
    if ip_string == '*':
        return True
    try:
        ip_address(ip_string)
        return True
    except ValueError:
        return False


def is_plain_filename(value):
    """A bare file name (no directories, no traversal) — used where a setting
    names a file written next to the music (folder art)."""
    return (isinstance(value, str) and value.strip() not in ('', '.', '..')
            and not any(c in value for c in '/\\\0'))


class Validator(OriginalValidator):
    default_messages = MappingProxyType(
        {
            "must_exist_true": "{name} is required",
            "must_exist_false": "{name} cannot exists",
            "condition": "{name} invalid for {function}({value})",
            "operations": "{name} must {operation} {op_value} but it is {value}",
            "combined": "combined validators failed {errors}",
        }
    )


validators = [
    # general section
    Validator('general.flask_secret_key', must_exist=True, default=hexlify(os.urandom(16)).decode(),
              is_type_of=str),
    Validator('general.ip', must_exist=True, default='*', is_type_of=str, condition=validate_ip_address),
    Validator('general.port', must_exist=True, default=6868, is_type_of=int, gte=1, lte=65535),
    Validator('general.base_url', must_exist=True, default='', is_type_of=str),
    Validator('general.instance_name', must_exist=True, default='Lyrarr', is_type_of=str,
              apply_default_on_none=True),
    Validator('general.debug', must_exist=True, default=False, is_type_of=bool),
    Validator('general.page_size', must_exist=True, default=25, is_type_of=int,
              is_in=[25, 50, 100, 250, 500, 1000]),
    Validator('general.theme', must_exist=True, default='auto', is_type_of=str,
              is_in=['auto', 'light', 'dark']),
    Validator('general.use_lidarr', must_exist=True, default=False, is_type_of=bool),
    Validator('general.path_mappings', must_exist=True, default=[], is_type_of=list),
    Validator('general.default_profile_id', must_exist=True, default=None, is_type_of=(NoneType, int)),
    # Days of download/upload history to keep; 0 keeps everything forever.
    Validator('general.history_retention_days', must_exist=True, default=90, is_type_of=int, gte=0),

    # auth section
    Validator('auth.apikey', must_exist=True, default=hexlify(os.urandom(16)).decode(), is_type_of=str),
    Validator('auth.type', must_exist=True, default=None, is_type_of=(NoneType, str),
              is_in=[None, 'basic', 'form']),
    Validator('auth.username', must_exist=True, default='', is_type_of=str, cast=str),
    Validator('auth.password', must_exist=True, default='', is_type_of=str, cast=str),

    # lidarr section
    Validator('lidarr.ip', must_exist=True, default='127.0.0.1', is_type_of=str),
    Validator('lidarr.port', must_exist=True, default=8686, is_type_of=int, gte=1, lte=65535),
    Validator('lidarr.base_url', must_exist=True, default='/', is_type_of=str),
    Validator('lidarr.ssl', must_exist=True, default=False, is_type_of=bool),
    Validator('lidarr.verify_ssl', must_exist=True, default=False, is_type_of=bool),
    Validator('lidarr.apikey', must_exist=True, default='', is_type_of=str),
    Validator('lidarr.http_timeout', must_exist=True, default=60, is_type_of=int,
              is_in=[60, 120, 180, 240, 300, 600]),
    Validator('lidarr.full_update', must_exist=True, default='Daily', is_type_of=str,
              is_in=['Manually', 'Daily', 'Weekly']),
    Validator('lidarr.full_update_day', must_exist=True, default=6, is_type_of=int, gte=0, lte=6),
    Validator('lidarr.full_update_hour', must_exist=True, default=4, is_type_of=int, gte=0, lte=23),
    Validator('lidarr.only_monitored', must_exist=True, default=False, is_type_of=bool),
    Validator('lidarr.sync_on_live', must_exist=True, default=True, is_type_of=bool),
    Validator('lidarr.sync_interval', must_exist=True, default=60, is_type_of=int,
              is_in=[15, 60, 180, 360, 720, 1440, 10080]),

    # metadata > covers section
    Validator('metadata.covers.enabled', must_exist=True, default=True, is_type_of=bool),
    Validator('metadata.covers.providers', must_exist=True, default=['musicbrainz', 'deezer', 'itunes', 'fanart', 'theaudiodb'], is_type_of=list),
    Validator('metadata.covers.preferred_size', must_exist=True, default='large', is_type_of=str,
              is_in=['small', 'large', 'original']),
    Validator('metadata.covers.overwrite_existing', must_exist=True, default=False, is_type_of=bool),
    Validator('metadata.covers.embed_in_files', must_exist=True, default=False, is_type_of=bool),
    Validator('metadata.covers.save_as_folder_art', must_exist=True, default=True, is_type_of=bool),
    Validator('metadata.covers.folder_art_filename', must_exist=True, default='cover', is_type_of=str,
              condition=is_plain_filename),

    # metadata > lyrics section
    Validator('metadata.lyrics.enabled', must_exist=True, default=True, is_type_of=bool),
    Validator('metadata.lyrics.providers', must_exist=True, default=['lrclib', 'musixmatch', 'netease', 'genius'], is_type_of=list),
    Validator('metadata.lyrics.prefer_synced', must_exist=True, default=True, is_type_of=bool),
    Validator('metadata.lyrics.overwrite_existing', must_exist=True, default=False, is_type_of=bool),
    Validator('metadata.lyrics.file_format', must_exist=True, default='lrc', is_type_of=str,
              is_in=['lrc', 'txt']),
    Validator('metadata.lyrics.save_alongside_track', must_exist=True, default=True, is_type_of=bool),

    # metadata > whisper section (for synced lyrics generation)
    Validator('metadata.whisper.model', must_exist=True, default='base', is_type_of=str,
              is_in=['tiny', 'base', 'small', 'medium', 'large-v3']),
    Validator('metadata.whisper.device', must_exist=True, default='cpu', is_type_of=str,
              is_in=['cpu', 'cuda', 'auto']),
    Validator('metadata.whisper.compute_type', must_exist=True, default='int8', is_type_of=str,
              is_in=['int8', 'float16', 'float32']),

    # fanart.tv section
    Validator('fanart.apikey', must_exist=True, default='', is_type_of=str),

    # genius section
    Validator('genius.apikey', must_exist=True, default='', is_type_of=str),

    # lrclib section — instance is overridable for self-hosted LRCLIB servers
    Validator('lrclib.instance', must_exist=True, default='https://lrclib.net', is_type_of=str),

    # translation section
    Validator('translation.engine', must_exist=True, default='google', is_type_of=str,
              is_in=['google', 'deepl']),
    Validator('translation.deepl_api_key', must_exist=True, default='', is_type_of=str),

    # musixmatch section
    Validator('musixmatch.apikey', must_exist=True, default='', is_type_of=str),

    # theaudiodb section
    Validator('theaudiodb.apikey', must_exist=True, default='2', is_type_of=str, cast=str),

    # notifications section
    Validator('notifications.enabled', must_exist=True, default=False, is_type_of=bool),
    Validator('notifications.discord_webhook', must_exist=True, default='', is_type_of=str),
    Validator('notifications.telegram_bot_token', must_exist=True, default='', is_type_of=str),
    Validator('notifications.telegram_chat_id', must_exist=True, default='', is_type_of=str, cast=str),


    # backup section
    Validator('backup.folder', must_exist=True, default=os.path.join(args.config_dir, 'backup'),
              is_type_of=str),
    Validator('backup.retention', must_exist=True, default=31, is_type_of=int, gte=0),
    Validator('backup.frequency', must_exist=True, default='Weekly', is_type_of=str,
              is_in=['Manually', 'Daily', 'Weekly']),
    Validator('backup.day', must_exist=True, default=6, is_type_of=int, gte=0, lte=6),
    Validator('backup.hour', must_exist=True, default=3, is_type_of=int, gte=0, lte=23),

    # log section
    Validator('log.include_filter', must_exist=True, default='', is_type_of=str, cast=str),
    Validator('log.exclude_filter', must_exist=True, default='', is_type_of=str, cast=str),
    Validator('log.ignore_case', must_exist=True, default=False, is_type_of=bool),
    Validator('log.use_regex', must_exist=True, default=False, is_type_of=bool),

    # proxy section
    Validator('proxy.type', must_exist=True, default=None, is_type_of=(NoneType, str),
              is_in=[None, 'socks5', 'socks5h', 'http']),
    Validator('proxy.url', must_exist=True, default='', is_type_of=str),
    Validator('proxy.port', must_exist=True, default='', is_type_of=(str, int)),
    Validator('proxy.username', must_exist=True, default='', is_type_of=str, cast=str),
    Validator('proxy.password', must_exist=True, default='', is_type_of=str, cast=str),
    Validator('proxy.exclude', must_exist=True, default=["localhost", "127.0.0.1"], is_type_of=list),

    # analytics section
    Validator('analytics.enabled', must_exist=True, default=True, is_type_of=bool),

    # audit section (Audit & Repair, PLAN §1.7 / §4.5)
    Validator('audit.schedule', must_exist=True, default='off', is_type_of=str,
              is_in=['off', 'daily', 'weekly']),
    Validator('audit.schedule_day', must_exist=True, default=6, is_type_of=int, gte=0, lte=6),
    Validator('audit.schedule_hour', must_exist=True, default=3, is_type_of=int, gte=0, lte=23),
    Validator('audit.scheduled_mode', must_exist=True, default='dry_run', is_type_of=str,
              is_in=['dry_run', 'apply_safe']),
    # One-shot repair of state left by older versions, after an upgrade.
    Validator('audit.auto_fix_on_upgrade', must_exist=True, default=True, is_type_of=bool),
    Validator('audit.scheduled_include_file_scan', must_exist=True, default=True, is_type_of=bool),
    Validator('audit.network_max_tracks', must_exist=True, default=200, is_type_of=int, gte=1, lte=5000),
    Validator('audit.report_retention', must_exist=True, default=10, is_type_of=int, gte=1, lte=100),
]


config_yaml_file = os.path.join(args.config_dir, 'config', 'config.yaml')

if not os.path.exists(config_yaml_file):
    if not os.path.isdir(os.path.dirname(config_yaml_file)):
        os.makedirs(os.path.dirname(config_yaml_file))
    open(config_yaml_file, mode='w').close()

if os.path.exists(config_yaml_file):
    os.environ['LYRARR_CONFIGURED'] = '1'

_sanitize_config_file(config_yaml_file)

settings = Dynaconf(
    settings_file=config_yaml_file,
    core_loaders=['YAML'],
    # No global apply_default_on_none: when it's on, every validator whose
    # default is None (auth.type, proxy.type, general.default_profile_id)
    # re-applies that default with TOML parsing of its WHOLE section, turning
    # numeric-looking strings ('123' instance name, digit-only API keys) into
    # ints that then fail validation and get reset. A null value for a typed
    # setting still gets its default via the reset loop below.
    # Never evaluate '@' tokens (@jinja/@format/@int/...) in config values —
    # YAML is already typed, so nothing legitimate depends on them.
    auto_cast=False,
)

settings.validators.register(*validators)

failed_validator = True
while failed_validator:
    try:
        settings.validators.validate_all()
        failed_validator = False
    except ValidationError as e:
        current_validator_details = e.details[0][0]
        logging.error(f"Validator failed for {current_validator_details.names[0]}: {e}")
        name = current_validator_details.names[0]
        old_value = settings.get(name, 'undefined')
        if (getattr(current_validator_details, 'is_type_of', None) is str
                and isinstance(old_value, (int, float)) and not isinstance(old_value, bool)):
            # A numeric-looking string that Dynaconf TOML-parsed into a number
            # (see above): restore the string instead of discarding it.
            settings[name] = str(old_value)
            logging.warning(f"VALIDATOR REPAIR: {name} restored as a string")
        elif hasattr(current_validator_details, 'default') and current_validator_details.default is not empty:
            settings[name] = current_validator_details.default
            logging.warning(f"VALIDATOR RESET: {name} from '{old_value}' to '{current_validator_details.default}'")
        else:
            logging.critical(f"Value for {name} doesn't pass validation and there's no "
                             f"default value. Lyrarr won't work until it's been fixed.")
            raise SystemExit(4) from None


def write_config():
    if settings.as_dict() == Dynaconf(
        settings_file=config_yaml_file,
        core_loaders=['YAML'],
        auto_cast=False,
    ).as_dict():
        logging.debug("Nothing changed when comparing to config file. Skipping write to file.")
    else:
        try:
            write(settings_path=config_yaml_file + '.tmp',
                  settings_data={k.lower(): v for k, v in settings.as_dict().items()},
                  merge=False)
        except Exception as error:
            logging.exception(f"Exception raised while trying to save temporary settings file: {error}")
        else:
            try:
                move(config_yaml_file + '.tmp', config_yaml_file)
            except Exception as error:
                logging.exception(f"Exception raised while trying to overwrite settings file: {error}")


# Canonical '' or '/prefix' — server.py mounts the API under it and the auth
# gate in app.py protects exactly that prefix.
base_url = normalize_base_url(settings.general.base_url)
settings.general.base_url = base_url
settings.lidarr.base_url = base_url_slash_cleaner(uri=settings.lidarr.base_url)

# Save updated settings to file
write_config()

ignore_keys = ['flask_secret_key']

# Subkeys whose values are never sent to the client (login secrets). The UI
# treats a blank field as "leave unchanged".
redact_keys = ['password']

array_keys = ['providers', 'exclude', 'path_mappings']

empty_values = ['', 'None', 'null', 'undefined', None, []]

# Subkeys that must stay strings — never coerce to int on save. Without this,
# a numeric-looking value like the TheAudioDB test key "2" would be cast to the
# int 2 and fail its str validator (and a purely-numeric API key would be
# corrupted, dropping any leading zeros).
str_keys = ['password', 'apikey', 'username', 'telegram_chat_id', 'telegram_bot_token',
            'deepl_api_key', 'instance', 'engine']


def _is_password_hash(value):
    """Whether a stored value is already a werkzeug password hash (not plaintext)."""
    return isinstance(value, str) and value.startswith(('pbkdf2:', 'scrypt:', 'argon2'))


def _lowercase_keys(d):
    """Recursively lowercase all dictionary keys."""
    result = {}
    for k, v in d.items():
        key = k.lower()
        if isinstance(v, dict):
            result[key] = _lowercase_keys(v)
        else:
            result[key] = v
    return result


def get_settings():
    settings_to_return = {}
    for k, v in settings.as_dict().items():
        if isinstance(v, dict):
            k = k.lower()
            settings_to_return[k] = {}
            lowered = _lowercase_keys(v)
            for subk, subv in lowered.items():
                if subk in ignore_keys:
                    continue
                if subk in redact_keys:
                    # Never expose login secrets to the client; blank means
                    # "unchanged" when the form is saved back.
                    settings_to_return[k][subk] = ''
                elif subv in empty_values and subk in array_keys:
                    settings_to_return[k][subk] = []
                else:
                    settings_to_return[k][subk] = subv
    return settings_to_return


# Known settings schema: dotted path -> Validator (type and default).
_schema = {v.names[0].lower(): v for v in validators}

# Settings whose blank value in a submitted form / restored backup means "keep
# the stored value" (they are redacted or easy to leave empty by accident).
secret_keys = ['password', 'apikey', 'deepl_api_key', 'telegram_bot_token', 'discord_webhook']

_lidarr_connection_paths = {
    'general.use_lidarr', 'lidarr.ip', 'lidarr.port', 'lidarr.base_url', 'lidarr.ssl',
    'lidarr.verify_ssl', 'lidarr.apikey'}
_schedule_paths = {
    'general.use_lidarr', 'lidarr.sync_interval', 'lidarr.full_update', 'lidarr.full_update_day',
    'lidarr.full_update_hour', 'backup.frequency', 'backup.day', 'backup.hour',
    'audit.schedule', 'audit.schedule_day', 'audit.schedule_hour', 'audit.scheduled_mode',
    'audit.scheduled_include_file_scan'}


class SettingsError(ValueError):
    """A submitted/restored setting was rejected; nothing was applied."""


def _expected_types(path):
    validator = _schema.get(path)
    types = getattr(validator, 'operations', {}).get('is_type_of') if validator else None
    if types is None:
        return ()
    return types if isinstance(types, tuple) else (types,)


def _validate_value(path, value):
    """Run the setting's validator checks on a prospective value without
    touching the live settings (Validator.validate() mutates them). Returns
    the (cast) value or raises SettingsError."""
    validator = _schema[path]
    value = validator.cast(value)
    types = _expected_types(path)
    ok = not (isinstance(value, bool) and types and bool not in types)
    for op_name, op_value in getattr(validator, 'operations', {}).items():
        op = getattr(validator_conditions, op_name, None)
        if not ok or op is None:
            continue
        try:
            ok = bool(op(value, op_value))
        except TypeError:
            ok = False
    if ok and validator.condition is not None:
        try:
            ok = bool(validator.condition(value))
        except Exception:
            ok = False
    if not ok:
        raise SettingsError(f'Invalid value for {path}: {value!r}')
    return value


def _flatten_settings(key, value):
    """Yield (dotted path, value) leaves. The UI posts whole sections, so a
    value can be a dict of subkeys (e.g. settings-metadata-covers)."""
    parts = str(key).split('-')
    if parts[0] == 'settings' and len(parts) > 1:
        parts = parts[1:]
    path = '.'.join(parts).lower()
    yield from _flatten_path(path, value)


def _flatten_path(path, value):
    if isinstance(value, dict) and path not in _schema:
        for subk, subv in value.items():
            yield from _flatten_path(f'{path}.{str(subk).lower()}', subv)
    else:
        yield path, value


def _coerce(path, value):
    """Coerce a submitted value to the setting's declared type, or raise
    SettingsError. Mirrors the old form handling ('true'/'false', numeric
    strings) but only casts to a type the validator actually accepts."""
    leaf = path.rsplit('.', 1)[-1]
    types = _expected_types(path)

    # Make sure that text based form values aren't passed as list
    if isinstance(value, list) and len(value) == 1 and leaf not in array_keys:
        value = value[0]
        if value in empty_values and value != '':
            value = None

    if isinstance(value, str):
        if value in ('true', 'false') and bool in types:
            value = value == 'true'
        elif int in types and leaf not in str_keys:
            if value.strip() == '':
                if NoneType in types:
                    value = None
                elif str not in types:
                    raise SettingsError(f'{path} must be a number')
            else:
                try:
                    value = int(value)
                except ValueError:
                    if str not in types:
                        raise SettingsError(f'{path} must be a number') from None
        elif value in ('None', 'null', '') and NoneType in types:
            value = None
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and types == (str,):
        # A JSON number for a string field (chat id, numeric API key, ...)
        value = str(value)
    return value


def save_settings(settings_items, *, unknown='error', keep_blank_secrets=False):
    """Validate and apply settings, then persist them.

    settings_items: iterable of (key, value); keys are 'settings-section-sub'
    (or 'section-sub') and values may be whole sub-dicts. Only keys of the
    known schema are accepted, Dynaconf tokens ('@...', dynaconf_* keys) are
    refused, values are coerced to the declared type and checked against their
    validators before anything is applied; on any error nothing is changed and
    SettingsError is raised.

    unknown: 'error' rejects unknown keys (unless re-submitting the stored
    value unchanged), 'skip' ignores them (backup restore across versions).
    keep_blank_secrets: treat blank secrets (API keys, tokens) as "keep the
    stored value" — exported backups redact some of them.
    """
    update_schedule = False
    lidarr_changed = False
    changes = {}
    skipped = []

    for key, raw_value in settings_items:
        if key == 'update_schedule':
            update_schedule = True
            continue
        if is_reserved_settings_key(key):
            raise SettingsError(f'Setting {key} is not allowed')

        for path, value in _flatten_settings(key, raw_value):
            if is_reserved_settings_key(path):
                raise SettingsError(f'Setting {path} is not allowed')
            bad = find_unsafe_value(value, path)
            if bad:
                raise SettingsError(f"Invalid value for {bad}: values may not start with '@'")

            leaf = path.rsplit('.', 1)[-1]
            if leaf in ignore_keys:
                continue

            try:
                old_value = settings[path]
            except (KeyError, AttributeError):
                old_value = None

            if path not in _schema:
                # The UI round-trips every key it received, including stale
                # ones still sitting in config.yaml; those are left alone.
                if unknown == 'skip' or (old_value is not None and old_value == value):
                    skipped.append(path)
                    continue
                raise SettingsError(f'Unknown setting {path}')

            value = _coerce(path, value)

            # Password fields: a blank submission means "keep the stored value"
            # (the client never receives the real value). The auth password is
            # stored hashed; hash any new plaintext before saving.
            if leaf == 'password':
                if value in (None, '', 'None'):
                    continue
                if path == 'auth.password' and not _is_password_hash(value):
                    from werkzeug.security import generate_password_hash
                    value = generate_password_hash(value)
            elif keep_blank_secrets and leaf in secret_keys and value in (None, '') and old_value:
                continue

            if path == 'general.base_url':
                value = normalize_base_url(value)
            elif path == 'lidarr.base_url':
                value = base_url_slash_cleaner(value) if isinstance(value, str) else value

            value = _validate_value(path, value)

            # The change flags below only fire when a value actually changed —
            # the frontend posts the whole form, not just edited fields.
            if old_value == value:
                continue
            changes[path] = (old_value, value)

    if changes:
        if any(path.startswith('auth.') for path in changes):
            _check_auth_consistency(changes)
        for path, (_old, value) in changes.items():
            settings[path] = value

        lidarr_changed = bool(_lidarr_connection_paths & changes.keys())
        update_schedule = update_schedule or bool(_schedule_paths & changes.keys())
        write_config()

    return {
        'update_schedule': update_schedule,
        'lidarr_changed': lidarr_changed,
        'changed': sorted(changes),
        'skipped': skipped,
    }


def _check_auth_consistency(changes):
    """Refuse to enable login auth without credentials (instant lockout)."""
    def prospective(path):
        return changes[path][1] if path in changes else settings.get(path)
    if prospective('auth.type') and not (prospective('auth.username') and prospective('auth.password')):
        raise SettingsError('Set a username and password before enabling authentication')
