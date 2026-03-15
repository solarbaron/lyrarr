# coding=utf-8

import signal
import warnings
import logging
import errno

from lyrarr.literals import EXIT_INTERRUPT, EXIT_NORMAL, EXIT_PORT_ALREADY_IN_USE_ERROR
from lyrarr.utilities.central import restart_lyrarr, stop_lyrarr

from waitress.server import create_server
from time import sleep

import os
from flask import send_from_directory

from lyrarr.api import api_bp
from lyrarr.api.events import events_bp
from .app import create_app
from .get_args import args
from .config import settings, base_url
from .database import close_database

# Resolve the frontend build directory
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend')

app = create_app()
app.register_blueprint(api_bp, url_prefix=base_url.rstrip('/') + '/api')
app.register_blueprint(events_bp)


# Serve frontend static files
@app.route('/')
def serve_index():
    return send_from_directory(frontend_dir, 'index.html')


@app.route('/assets/<path:filename>')
def serve_assets(filename):
    return send_from_directory(os.path.join(frontend_dir, 'assets'), filename)


# Catch-all for SPA client-side routing (any path that isn't /api)
@app.route('/<path:path>')
def serve_spa(path):
    # If the file exists in the frontend dir, serve it directly
    file_path = os.path.join(frontend_dir, path)
    if os.path.isfile(file_path):
        return send_from_directory(frontend_dir, path)
    # Otherwise serve index.html for client-side routing
    return send_from_directory(frontend_dir, 'index.html')


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

        while not self.connected:
            sleep(0.1)
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
        if not self.interrupted:
            self.interrupted = True
            self.shutdown(EXIT_INTERRUPT)

    def start(self):
        self.server.print_listen("LYRARR is started and waiting for requests on: http://{}:{}")
        signal.signal(signal.SIGINT, self.interrupt_handler)
        try:
            self.server.run()
        except (KeyboardInterrupt, SystemExit):
            self.shutdown()
        except OSError as error:
            if error.errno == 9:
                self.server.close()
            else:
                pass
        except Exception:
            pass

    def close_all(self):
        print("Closing database...")
        close_database()
        if self.server:
            print("Closing webserver...")
            self.server.close()

    def shutdown(self, status=EXIT_NORMAL):
        self.close_all()
        stop_lyrarr(status)

    def restart(self):
        self.close_all()
        restart_lyrarr()


webserver = Server()
