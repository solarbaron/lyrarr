# coding=utf-8

"""Allow running lyrarr as a module: python -m lyrarr"""

# Import main module which performs all initialization
# (logging, database, scheduler, signalr)
import lyrarr.main  # noqa

# Start the web server
from lyrarr.app.server import webserver
webserver.start()
