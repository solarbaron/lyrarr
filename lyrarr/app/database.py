# coding=utf-8

import atexit
import logging
import os
import signal

from datetime import datetime

from sqlalchemy import create_engine, DateTime, ForeignKey, Integer, Text, func, text
from sqlalchemy import update, delete, select, func  # noqa W0611
from sqlalchemy.orm import scoped_session, sessionmaker, mapped_column, close_all_sessions
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.pool import NullPool

from .config import settings
from .get_args import args

logger = logging.getLogger(__name__)

url = f'sqlite:///{os.path.join(args.config_dir, "db", "lyrarr.db")}'
logger.debug(f"Connecting to SQLite database: {url}")
engine = create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")

from sqlalchemy.engine import Engine
from sqlalchemy import event


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


session_factory = sessionmaker(bind=engine)
database = scoped_session(session_factory)


def close_database():
    close_all_sessions()
    engine.dispose()


@atexit.register
def _stop_worker_threads():
    database.remove()


signal.signal(signal.SIGTERM, lambda signal_no, frame: close_database())

Base = declarative_base()
metadata = Base.metadata


def _serialize_value(v):
    """Convert non-JSON-serializable values to strings."""
    if isinstance(v, datetime):
        return v.isoformat()
    return v


class System(Base):
    __tablename__ = 'system'

    id = mapped_column(Integer, primary_key=True)
    configured = mapped_column(Text)
    updated = mapped_column(Text)


class TableProfiles(Base):
    __tablename__ = 'table_profiles'

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    name = mapped_column(Text, nullable=False, unique=True)
    is_default = mapped_column(Text, default='False')
    download_covers = mapped_column(Text, default='True')
    download_lyrics = mapped_column(Text, default='True')
    cover_providers = mapped_column(Text, default='["musicbrainz","fanart"]')
    lyrics_providers = mapped_column(Text, default='["lrclib","genius"]')
    prefer_synced_lyrics = mapped_column(Text, default='True')
    cover_format = mapped_column(Text, default='jpg')
    overwrite_existing = mapped_column(Text, default='False')
    embed_cover_art = mapped_column(Text, default='False')
    created_at_timestamp = mapped_column(DateTime)
    updated_at_timestamp = mapped_column(DateTime)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


class TableArtists(Base):
    __tablename__ = 'table_artists'

    lidarrArtistId = mapped_column(Integer, primary_key=True)
    mbId = mapped_column(Text)  # MusicBrainz Artist ID
    name = mapped_column(Text, nullable=False)
    sortName = mapped_column(Text)
    path = mapped_column(Text, nullable=False)
    monitored = mapped_column(Text)
    overview = mapped_column(Text)
    fanart = mapped_column(Text)
    poster = mapped_column(Text)
    tags = mapped_column(Text)
    metadata_status = mapped_column(Text, default='unknown')
    profileId = mapped_column(Integer, ForeignKey('table_profiles.id', ondelete='SET NULL'), nullable=True)
    created_at_timestamp = mapped_column(DateTime)
    updated_at_timestamp = mapped_column(DateTime)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


class TableAlbums(Base):
    __tablename__ = 'table_albums'

    lidarrAlbumId = mapped_column(Integer, primary_key=True)
    mbId = mapped_column(Text)  # MusicBrainz Release Group ID
    artistId = mapped_column(Integer, ForeignKey('table_artists.lidarrArtistId', ondelete='CASCADE'))
    title = mapped_column(Text, nullable=False)
    year = mapped_column(Integer)
    path = mapped_column(Text)
    monitored = mapped_column(Text)
    overview = mapped_column(Text)
    cover = mapped_column(Text)  # URL from Lidarr
    genres = mapped_column(Text)
    albumType = mapped_column(Text)
    cover_status = mapped_column(Text, default='missing')  # missing, available, manual
    lyrics_status = mapped_column(Text, default='unknown')  # unknown, partial, complete
    missing_covers = mapped_column(Text)
    missing_lyrics = mapped_column(Text)
    profileId = mapped_column(Integer, ForeignKey('table_profiles.id', ondelete='SET NULL'), nullable=True)
    override_cover_format = mapped_column(Text, nullable=True)
    override_prefer_synced = mapped_column(Text, nullable=True)
    override_download_covers = mapped_column(Text, nullable=True)
    override_download_lyrics = mapped_column(Text, nullable=True)
    created_at_timestamp = mapped_column(DateTime)
    updated_at_timestamp = mapped_column(DateTime)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


class TableTracks(Base):
    __tablename__ = 'table_tracks'

    lidarrTrackId = mapped_column(Integer, primary_key=True)
    mbId = mapped_column(Text)  # MusicBrainz Recording ID
    albumId = mapped_column(Integer, ForeignKey('table_albums.lidarrAlbumId', ondelete='CASCADE'))
    artistId = mapped_column(Integer, ForeignKey('table_artists.lidarrArtistId', ondelete='CASCADE'))
    title = mapped_column(Text, nullable=False)
    trackNumber = mapped_column(Integer)
    discNumber = mapped_column(Integer, default=1)
    duration = mapped_column(Integer)  # in milliseconds
    path = mapped_column(Text)
    hasLyrics = mapped_column(Text, default='False')  # whether synced lyrics exist
    lyrics_status = mapped_column(Text, default='missing')  # missing, available, manual, blacklisted
    created_at_timestamp = mapped_column(DateTime)
    updated_at_timestamp = mapped_column(DateTime)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


class TableHistory(Base):
    __tablename__ = 'table_history'

    id = mapped_column(Integer, primary_key=True)
    action = mapped_column(Integer, nullable=False)  # 1=download, 2=upgrade, 3=delete, 4=manual
    description = mapped_column(Text, nullable=False)
    metadata_type = mapped_column(Text)  # 'cover' or 'lyrics'
    provider = mapped_column(Text)
    lidarrAlbumId = mapped_column(Integer, ForeignKey('table_albums.lidarrAlbumId', ondelete='CASCADE'))
    lidarrTrackId = mapped_column(Integer, ForeignKey('table_tracks.lidarrTrackId', ondelete='CASCADE'))
    lidarrArtistId = mapped_column(Integer, ForeignKey('table_artists.lidarrArtistId', ondelete='CASCADE'))
    timestamp = mapped_column(DateTime, nullable=False, default=datetime.now)
    metadata_path = mapped_column(Text)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


class TableBlacklist(Base):
    __tablename__ = 'table_blacklist'

    id = mapped_column(Integer, primary_key=True)
    metadata_type = mapped_column(Text)  # 'cover' or 'lyrics'
    provider = mapped_column(Text)
    lidarrAlbumId = mapped_column(Integer, ForeignKey('table_albums.lidarrAlbumId', ondelete='CASCADE'))
    lidarrTrackId = mapped_column(Integer, ForeignKey('table_tracks.lidarrTrackId', ondelete='CASCADE'))
    timestamp = mapped_column(DateTime, default=datetime.now)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


def init_db():
    metadata.create_all(engine)

    # Add the system table single row if it doesn't exist
    if not database.execute(select(System)).first():
        from sqlalchemy.dialects.sqlite import insert
        database.execute(
            insert(System).values(configured='0', updated='0')
        )

    # Seed a default profile if none exists
    if not database.execute(select(TableProfiles)).first():
        from sqlalchemy.dialects.sqlite import insert
        database.execute(
            insert(TableProfiles).values(
                name='Default',
                is_default='True',
                download_covers='True',
                download_lyrics='True',
                cover_providers='["musicbrainz","fanart"]',
                lyrics_providers='["lrclib","genius"]',
                prefer_synced_lyrics='True',
                cover_format='jpg',
                overwrite_existing='False',
                embed_cover_art='False',
                created_at_timestamp=datetime.now(),
                updated_at_timestamp=datetime.now(),
            )
        )
