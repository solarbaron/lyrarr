
import logging
import os
import sys


def _exit_now(status_code):
    """Terminate the process after cleanup has run.

    A plain SystemExit would make the interpreter join every non-daemon thread
    first (e.g. the scheduler's executor running a long download), so `docker
    stop` would hit its timeout and SIGKILL. Callers have already stopped the
    scheduler/web server and closed the database.
    """
    try:
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(status_code)


def stop_lyrarr(status_code=0):
    stop_file = os.environ.get('LYRARR_STOPFILE')
    if stop_file:
        # Running under the lyrarr.py launcher: tell it the exit status.
        try:
            with open(stop_file, 'w') as f:
                f.write(str(status_code))
        except OSError as e:
            print(f"Could not write stop file: {e}")
    _exit_now(status_code)


def restart_lyrarr():
    restart_file = os.environ.get('LYRARR_RESTARTFILE')
    if restart_file:
        # The launcher terminates this process and starts a new one.
        with open(restart_file, 'w') as f:
            f.write('')
    else:
        _exit_now(0)
