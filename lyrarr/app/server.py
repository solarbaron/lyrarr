
import errno
import json
import logging
import os
import re
import signal
import sys
import threading
import warnings

from flask import Response, abort, jsonify, redirect, request, send_from_directory
from waitress.server import create_server
from werkzeug.security import safe_join

from lyrarr.api import api_bp
from lyrarr.api.events import events_bp
from lyrarr.literals import EXIT_INTERRUPT, EXIT_NORMAL, EXIT_PORT_ALREADY_IN_USE_ERROR
from lyrarr.utilities.central import restart_lyrarr, stop_lyrarr

from .app import create_app
from .config import base_url, settings
from .database import close_database
from .get_args import args

# Resolve the frontend build directory
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend')

# Everything lives under general.base_url ('' or '/prefix'): the SPA, its
# assets, the API and the SSE stream.
api_prefix = base_url + '/api'

app = create_app()
app.register_blueprint(api_bp, url_prefix=api_prefix)
app.register_blueprint(events_bp, url_prefix=api_prefix)


def _is_api_path(path):
    return any(path == p or path.startswith(p + '/') for p in {api_prefix, '/api'})


@app.errorhandler(404)
def _not_found(error):
    # API clients get JSON, never the SPA's HTML.
    if _is_api_path(request.path):
        return jsonify({'message': 'Not found'}), 404
    return error


_index_cache = {'mtime': None, 'html': None}


def _render_index():
    """index.html with the base URL injected and root-relative asset links prefixed.

    The frontend reads window.__LYRARR_BASE_URL__ to prefix API/SSE calls and its
    router basename.
    """
    index_path = os.path.join(frontend_dir, 'index.html')
    try:
        mtime = os.path.getmtime(index_path)
    except OSError:
        abort(404)
    if _index_cache['mtime'] != mtime:
        with open(index_path, encoding='utf-8') as f:
            html = f.read()
        value = json.dumps(base_url).replace('<', '\\u003c').replace('>', '\\u003e')
        script = f'<script>window.__LYRARR_BASE_URL__={value}</script>'
        if base_url:
            # src="/assets/x.js" → src="/prefix/assets/x.js" (protocol-relative "//" untouched)
            html = re.sub(r'''((?:src|href)=["'])/(?!/)''', lambda m: f'{m.group(1)}{base_url}/', html)
        html, count = re.subn(r'<head[^>]*>', lambda m: m.group(0) + script, html, count=1)
        if not count:
            html = script + html
        _index_cache.update(mtime=mtime, html=html)
    resp = Response(_index_cache['html'], mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


# Serve frontend static files
@app.route(base_url + '/')
def serve_index():
    return _render_index()


if base_url:
    @app.route('/')
    def redirect_to_base_url():
        return redirect(base_url + '/')


@app.route(base_url + '/assets/<path:filename>')
def serve_assets(filename):
    return send_from_directory(os.path.join(frontend_dir, 'assets'), filename)


# Catch-all for SPA client-side routing (any path that isn't the API)
@app.route(base_url + '/<path:path>')
def serve_spa(path):
    if path == 'api' or path.startswith('api/'):
        return jsonify({'message': 'Not found'}), 404
    if path == 'index.html':
        return _render_index()
    # If the file exists in the frontend dir, serve it directly. safe_join
    # rejects traversal ('..', absolute paths) before anything touches the disk.
    file_path = safe_join(frontend_dir, path)
    if file_path and os.path.isfile(file_path):
        return send_from_directory(frontend_dir, path)
    # Otherwise serve index.html for client-side routing
    return _render_index()


def _stop_background_services():
    """Stop the scheduler and the Lidarr SignalR client if they were started."""
    sched_mod = sys.modules.get('lyrarr.app.scheduler')
    if sched_mod is not None:
        try:
            sched_mod.scheduler.aps_scheduler.shutdown(wait=False)
        except Exception as e:
            logging.debug(f"Scheduler shutdown: {e}")
    signalr_mod = sys.modules.get('lyrarr.lidarr.signalr_client')
    if signalr_mod is not None:
        try:
            signalr_mod.lidarr_signalr_client.stop()
        except Exception as e:
            logging.debug(f"SignalR shutdown: {e}")


class Server:
    def __init__(self):
        warnings.simplefilter("ignore", DeprecationWarning)
        warnings.filterwarnings('ignore', message='Unverified HTTPS request')
        warnings.simplefilter("ignore", BrokenPipeError)

        self.server = None
        self.connected = False
        self.address = str(settings.general.ip)
        self.port = int(args.port) if args.port else int(settings.general.port)
        self.interrupted = False
        self.exit_status = EXIT_NORMAL

    def _bind(self):
        while not self.connected:
            self.configure_server()

    def configure_server(self):
        try:
            self.server = create_server(app,
                                        host=self.address,
                                        port=self.port,
                                        threads=50)
            self.connected = True
        except OSError as error:
            if error.errno == errno.EADDRNOTAVAIL:
                logging.exception("LYRARR cannot bind to specified IP, trying with 0.0.0.0")
                self.address = '0.0.0.0'
                self.connected = False
            elif error.errno == errno.EADDRINUSE:
                if self.port != 6868:
                    logging.exception("LYRARR cannot bind to specified TCP port, trying with default (6868)")
                    self.port = 6868
                    self.connected = False
                else:
                    logging.exception("LYRARR cannot bind to default TCP port (6868) because it's already in use, "
                                      "exiting...")
                    self.shutdown(EXIT_PORT_ALREADY_IN_USE_ERROR)
            elif error.errno in [errno.ENOLINK, errno.EAFNOSUPPORT]:
                logging.exception("LYRARR cannot bind to IPv6 (*), trying with 0.0.0.0")
                self.address = '0.0.0.0'
                self.connected = False
            else:
                logging.exception("LYRARR cannot start because of unhandled exception.")
                self.shutdown()

    def interrupt_handler(self, signum, frame):
        """SIGTERM (docker stop) / SIGINT: leave the server loop and shut down cleanly."""
        if self.interrupted:
            # Second signal while shutting down: stop waiting.
            os._exit(EXIT_INTERRUPT)
        self.interrupted = True
        self.exit_status = EXIT_NORMAL
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = str(signum)
        logging.info(f"Received {name}, shutting down...")
        # End open SSE streams so waitress' worker threads can exit promptly.
        # (In a thread: the handler must not block on locks the interrupted
        # main thread might hold.)
        from .event_handler import close_all_streams
        threading.Thread(target=close_all_streams, daemon=True).start()
        # waitress' run() catches SystemExit, stops its task threads and returns.
        raise SystemExit(EXIT_NORMAL)

    def start(self):
        self._bind()
        signal.signal(signal.SIGINT, self.interrupt_handler)
        signal.signal(signal.SIGTERM, self.interrupt_handler)
        try:
            self.server.print_listen("LYRARR is started and waiting for requests on: http://{}:{}")
            self.server.run()
        except (KeyboardInterrupt, SystemExit):
            self.interrupted = True
        except OSError as error:
            if error.errno != errno.EBADF:
                logging.exception("Web server stopped unexpectedly")
        except Exception:
            logging.exception("Web server stopped unexpectedly")
        self.shutdown(self.exit_status)

    def close_all(self):
        _stop_background_services()
        if self.server:
            print("Closing webserver...")
            try:
                self.server.close()
            except Exception as e:
                logging.debug(f"Web server close: {e}")
        print("Closing database...")
        close_database()

    def shutdown(self, status=EXIT_NORMAL):
        self.close_all()
        stop_lyrarr(status)

    def restart(self):
        self.close_all()
        restart_lyrarr()


webserver = Server()
