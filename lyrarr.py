# coding=utf-8

import os
import platform
import signal
import subprocess
import sys
import time

from lyrarr.app.get_args import args
from lyrarr.literals import (
    EXIT_PYTHON_UPGRADE_NEEDED, EXIT_NORMAL, EXIT_INTERRUPT,
    FILE_RESTART, FILE_STOP, ENV_RESTARTFILE, ENV_STOPFILE
)

# always flush print statements
sys.stdout.reconfigure(line_buffering=True)


def exit_program(status_code):
    print(f'Lyrarr exited with status code {status_code}.')
    raise SystemExit(status_code)


def check_python_version():
    python_version = platform.python_version_tuple()
    minimum_py3_tuple = (3, 13, 0)
    minimum_py3_str = ".".join(str(i) for i in minimum_py3_tuple)

    major, minor = int(python_version[0]), int(python_version[1])

    if major < minimum_py3_tuple[0] or (major == minimum_py3_tuple[0] and minor < minimum_py3_tuple[1]):
        print("Python " + minimum_py3_str + " or greater required. "
              "Current version is " + platform.python_version() + ". Please upgrade Python.")
        exit_program(EXIT_PYTHON_UPGRADE_NEEDED)


def get_python_path():
    if sys.platform == "darwin":
        python_bundle_path = os.path.join(sys.base_exec_prefix, "Resources", "Python.app", "Contents", "MacOS", "Python")
        if os.path.exists(python_bundle_path):
            import tempfile
            python_path = os.path.join(tempfile.mkdtemp(), "python")
            os.symlink(python_bundle_path, python_path)
            return python_path
    return sys.executable


check_python_version()

dir_name = os.path.dirname(__file__)


def start_lyrarr():
    script = [get_python_path(), "-u", os.path.normcase(os.path.join(dir_name, 'lyrarr', 'main.py'))] + sys.argv[1:]
    ep = subprocess.Popen(script, stdout=None, stderr=None, stdin=subprocess.DEVNULL, env=os.environ)
    print(f"Lyrarr starting child process with PID {ep.pid}...")
    return ep


def terminate_child():
    global child_process
    print(f"Terminating child process with PID {child_process.pid}")
    if child_process.poll() is None:
        child_process.terminate()
    child_process.wait()


def get_stop_status_code(input_file):
    try:
        with open(input_file, 'r') as file:
            line = file.readline()
            try:
                status_code = int(line)
            except (ValueError, TypeError):
                status_code = EXIT_NORMAL
            file.close()
    except Exception:
        status_code = EXIT_NORMAL
    return status_code


def check_status():
    global child_process
    if os.path.exists(stop_file):
        status_code = get_stop_status_code(stop_file)
        try:
            print("Deleting stop file...")
            os.remove(stop_file)
        except Exception:
            print('Unable to delete stop file.')
        finally:
            terminate_child()
            exit_program(status_code)

    if os.path.exists(restart_file):
        try:
            print("Deleting restart file...")
            os.remove(restart_file)
        except Exception:
            print('Unable to delete restart file.')
        finally:
            terminate_child()
            print("Lyrarr is restarting...")
            child_process = start_lyrarr()


def is_process_running(pid):
    commands = {
        "win": ["tasklist", "/FI", f"PID eq {pid}"],
        "linux": ["ps", "-eo", "pid"],
        "darwin": ["ps", "-ax", "-o", "pid"]
    }
    for key in commands:
        if sys.platform.startswith(key):
            result = subprocess.run(commands[key], capture_output=True, text=True)
            return str(pid) in result.stdout.split()
    print("Unsupported OS")
    return False


def interrupt_handler(signum, frame):
    global interrupted
    if not interrupted:
        interrupted = True
        print('Handling keyboard interrupt...')
    else:
        if not is_process_running(child_process.pid):
            raise SystemExit(EXIT_INTERRUPT)


if __name__ == '__main__':
    interrupted = False
    signal.signal(signal.SIGINT, interrupt_handler)
    restart_file = os.path.join(args.config_dir, FILE_RESTART)
    stop_file = os.path.join(args.config_dir, FILE_STOP)
    os.environ[ENV_STOPFILE] = stop_file
    os.environ[ENV_RESTARTFILE] = restart_file

    # Cleanup leftover files
    try:
        os.remove(restart_file)
    except FileNotFoundError:
        pass

    try:
        os.remove(stop_file)
    except FileNotFoundError:
        pass

    # Initial start of main lyrarr process
    child_process = start_lyrarr()

    # Keep the script running forever until stop is requested
    while True:
        check_status()
        try:
            time.sleep(5)
        except (KeyboardInterrupt, SystemExit, ChildProcessError):
            print('Lyrarr exited main script file via keyboard interrupt.')
            exit_program(EXIT_INTERRUPT)
