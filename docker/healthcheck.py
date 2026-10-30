"""Docker HEALTHCHECK: is the Lyrarr web server answering?

Reads the port and URL base from /config/config/config.yaml (both can be
changed in Settings) and requests <base>/api/auth/status, which never needs
authentication. Exit 0 = healthy, 1 = unhealthy.
"""
import sys
import urllib.request

CONFIG = '/config/config/config.yaml'

port, base = 6868, ''
try:
    import yaml

    with open(CONFIG) as f:
        general = (yaml.safe_load(f) or {}).get('general') or {}
    port = int(general.get('port') or port)
    base = str(general.get('base_url') or '').strip().strip('/')
    base = f'/{base}' if base else ''
except Exception:
    pass  # first start (no config yet) or unreadable: use the defaults

try:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}{base}/api/auth/status', timeout=5) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
