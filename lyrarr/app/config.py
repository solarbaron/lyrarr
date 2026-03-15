# coding=utf-8

import os
import logging

import yaml
from dynaconf import Dynaconf, Validator as OriginalValidator
from dynaconf.loaders.yaml_loader import write
from dynaconf.validator import ValidationError
from dynaconf.utils.functional import empty
from binascii import hexlify
from ipaddress import ip_address
from shutil import move
from types import MappingProxyType

from .get_args import args

NoneType = type(None)


def base_url_slash_cleaner(uri):
    while "//" in uri:
        uri = uri.replace("//", "/")
    return uri


def validate_ip_address(ip_string):
    if ip_string == '*':
        return True
    try:
        ip_address(ip_string)
        return True
    except ValueError:
        return False


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
    Validator('metadata.covers.providers', must_exist=True, default=['musicbrainz', 'fanart'], is_type_of=list),
    Validator('metadata.covers.preferred_size', must_exist=True, default='large', is_type_of=str,
              is_in=['small', 'large', 'original']),
    Validator('metadata.covers.overwrite_existing', must_exist=True, default=False, is_type_of=bool),
    Validator('metadata.covers.embed_in_files', must_exist=True, default=False, is_type_of=bool),
    Validator('metadata.covers.save_as_folder_art', must_exist=True, default=True, is_type_of=bool),
    Validator('metadata.covers.folder_art_filename', must_exist=True, default='cover', is_type_of=str),

    # metadata > lyrics section
    Validator('metadata.lyrics.enabled', must_exist=True, default=True, is_type_of=bool),
    Validator('metadata.lyrics.providers', must_exist=True, default=['lrclib', 'genius'], is_type_of=list),
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

    # notifications section
    Validator('notifications.enabled', must_exist=True, default=False, is_type_of=bool),
    Validator('notifications.discord_webhook', must_exist=True, default='', is_type_of=str),
    Validator('notifications.telegram_bot_token', must_exist=True, default='', is_type_of=str),
    Validator('notifications.telegram_chat_id', must_exist=True, default='', is_type_of=str, cast=str),

    # auth section
    Validator('auth.api_key', must_exist=True, default='', is_type_of=str),

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
]


config_yaml_file = os.path.join(args.config_dir, 'config', 'config.yaml')

if not os.path.exists(config_yaml_file):
    if not os.path.isdir(os.path.dirname(config_yaml_file)):
        os.makedirs(os.path.dirname(config_yaml_file))
    open(config_yaml_file, mode='w').close()

if os.path.exists(config_yaml_file):
    os.environ['LYRARR_CONFIGURED'] = '1'

settings = Dynaconf(
    settings_file=config_yaml_file,
    core_loaders=['YAML'],
    apply_default_on_none=True,
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
        if hasattr(current_validator_details, 'default') and current_validator_details.default is not empty:
            old_value = settings.get(current_validator_details.names[0], 'undefined')
            settings[current_validator_details.names[0]] = current_validator_details.default
            logging.warning(f"VALIDATOR RESET: {current_validator_details.names[0]} from '{old_value}' to '{current_validator_details.default}'")
        else:
            logging.critical(f"Value for {current_validator_details.names[0]} doesn't pass validation and there's no "
                             f"default value. Lyrarr won't work until it's been fixed.")
            raise SystemExit(4)


def write_config():
    if settings.as_dict() == Dynaconf(
        settings_file=config_yaml_file,
        core_loaders=['YAML']
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


base_url = settings.general.base_url.rstrip('/')

# Make sure to get rid of double slashes in base_url
settings.general.base_url = base_url_slash_cleaner(uri=settings.general.base_url)
settings.lidarr.base_url = base_url_slash_cleaner(uri=settings.lidarr.base_url)

# Save updated settings to file
write_config()

ignore_keys = ['flask_secret_key']

array_keys = ['providers', 'exclude', 'path_mappings']

empty_values = ['', 'None', 'null', 'undefined', None, []]

str_keys = ['password']


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
                if subv in empty_values and subk in array_keys:
                    settings_to_return[k][subk] = []
                else:
                    settings_to_return[k][subk] = subv
    return settings_to_return


def save_settings(settings_items):
    update_schedule = False
    lidarr_changed = False

    for key, value in settings_items:
        settings_keys = key.split('-')

        # Make sure that text based form values aren't passed as list
        if isinstance(value, list) and len(value) == 1 and settings_keys[-1] not in array_keys:
            value = value[0]
            if value in empty_values and value != '':
                value = None

        # Try to cast string as integer
        if isinstance(value, str) and settings_keys[-1] not in str_keys:
            try:
                value = int(value)
            except ValueError:
                pass

        if value == 'true':
            value = True
        elif value == 'false':
            value = False

        if key in ['settings-general-base_url', 'settings-lidarr-base_url']:
            value = base_url_slash_cleaner(value)

        if key in ['settings-general-use_lidarr', 'settings-lidarr-ip', 'settings-lidarr-port',
                   'settings-lidarr-base_url', 'settings-lidarr-ssl', 'settings-lidarr-apikey']:
            lidarr_changed = True

        if key in ['update_schedule', 'settings-general-use_lidarr',
                   'settings-lidarr-sync_interval', 'settings-lidarr-full_update',
                   'settings-lidarr-full_update_day', 'settings-lidarr-full_update_hour',
                   'settings-backup-frequency', 'settings-backup-day', 'settings-backup-hour']:
            update_schedule = True

        # Apply the setting
        settings_keys_path = '.'.join(settings_keys[1:]) if settings_keys[0] == 'settings' else '.'.join(settings_keys)
        settings[settings_keys_path] = value

    write_config()

    return {
        'update_schedule': update_schedule,
        'lidarr_changed': lidarr_changed,
    }
