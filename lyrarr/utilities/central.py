# coding=utf-8

import os


def stop_lyrarr(status_code=0):
    stop_file = os.environ.get('LYRARR_STOPFILE')
    if stop_file:
        with open(stop_file, 'w') as f:
            f.write(str(status_code))
    else:
        raise SystemExit(status_code)


def restart_lyrarr():
    restart_file = os.environ.get('LYRARR_RESTARTFILE')
    if restart_file:
        with open(restart_file, 'w') as f:
            f.write('')
    else:
        raise SystemExit(0)
