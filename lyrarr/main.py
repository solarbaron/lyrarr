# coding=utf-8

import os
import sys
import logging

# Add the parent directory to the Python path so we can import lyrarr modules
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lyrarr.app.logger import configure_logging
from lyrarr.app.get_args import args
from lyrarr.app.config import settings

# Configure logging first
configure_logging(debug=settings.general.debug)

from lyrarr.app.database import init_db, database, System, update, select
from lyrarr.app.server import webserver

logging.info("Lyrarr starting up...")

# Initialize the database
init_db()

# Set the configured state
database.execute(
    update(System)
    .values(configured=os.environ.get('LYRARR_CONFIGURED', '0'))
)

# Start the scheduler
from lyrarr.app.scheduler import scheduler  # noqa
logging.info("Scheduler started")

# Start SignalR client if enabled
if not args.no_signalr and settings.general.use_lidarr:
    from threading import Thread
    from lyrarr.lidarr.signalr_client import lidarr_signalr_client

    signalr_thread = Thread(target=lidarr_signalr_client.start)
    signalr_thread.daemon = True
    signalr_thread.start()
    logging.info("Lidarr SignalR client started")

if __name__ == "__main__":
    webserver.start()
