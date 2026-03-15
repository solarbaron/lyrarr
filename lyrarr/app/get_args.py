# coding=utf-8

import argparse
import os


def get_args():
    parser = argparse.ArgumentParser(description='Lyrarr - Lidarr Companion for Music Metadata')

    parser.add_argument('--config', dest='config_dir', default=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data'),
                        help='Directory to store configuration and data files')
    parser.add_argument('--port', dest='port', default=None, type=int,
                        help='Port to listen on (overrides config)')
    parser.add_argument('--no-update', dest='no_update', action='store_true', default=False,
                        help='Disable auto-update')
    parser.add_argument('--no-signalr', dest='no_signalr', action='store_true', default=False,
                        help='Disable SignalR client for Lidarr')
    parser.add_argument('--debug', dest='debug', action='store_true', default=False,
                        help='Enable debug logging')
    parser.add_argument('--no-tasks', dest='no_tasks', action='store_true', default=False,
                        help='Disable all scheduled tasks')
    parser.add_argument('--create-db-revision', dest='create_db_revision', action='store_true', default=False,
                        help='Create a new database revision for migration')

    return parser.parse_args()


args = get_args()

# Ensure config directory exists
os.makedirs(args.config_dir, exist_ok=True)
os.makedirs(os.path.join(args.config_dir, 'config'), exist_ok=True)
os.makedirs(os.path.join(args.config_dir, 'db'), exist_ok=True)
os.makedirs(os.path.join(args.config_dir, 'log'), exist_ok=True)
os.makedirs(os.path.join(args.config_dir, 'backup'), exist_ok=True)
