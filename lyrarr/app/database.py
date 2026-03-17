# coding=utf-8

import atexit
import logging
import os
import signal

from datetime import datetime

from sqlalchemy import create_engine, DateTime, ForeignKey, Integer, Text, Boolean, func, text
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
    is_default = mapped_column(Boolean, default=False)
    download_covers = mapped_column(Boolean, default=True)
    download_lyrics = mapped_column(Boolean, default=True)
    cover_providers = mapped_column(Text, default='["musicbrainz","deezer","itunes","fanart","theaudiodb"]')
    lyrics_providers = mapped_column(Text, default='["lrclib","musixmatch","netease","genius"]')
    prefer_synced_lyrics = mapped_column(Boolean, default=True)
    cover_format = mapped_column(Text, default='jpg')
    overwrite_existing = mapped_column(Boolean, default=False)
    embed_cover_art = mapped_column(Boolean, default=False)
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
    monitored = mapped_column(Boolean, default=False)
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
    monitored = mapped_column(Boolean, default=False)
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
    override_prefer_synced = mapped_column(Boolean, nullable=True)
    override_download_covers = mapped_column(Boolean, nullable=True)
    override_download_lyrics = mapped_column(Boolean, nullable=True)
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
    hasLyrics = mapped_column(Boolean, default=False)  # whether synced lyrics exist
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


class TableLyricsVersions(Base):
    """Store previous lyrics versions in-app instead of leaving extra files in music dirs."""
    __tablename__ = 'table_lyrics_versions'

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    lidarrTrackId = mapped_column(Integer, ForeignKey('table_tracks.lidarrTrackId', ondelete='CASCADE'))
    content = mapped_column(Text, nullable=False)
    lyrics_type = mapped_column(Text, nullable=False)  # 'synced' or 'plain'
    provider = mapped_column(Text)
    timestamp = mapped_column(DateTime, default=datetime.now)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


# Columns that were migrated from Text ('True'/'False') to Boolean (1/0)
_BOOL_MIGRATION_COLUMNS = {
    'table_profiles': ['is_default', 'download_covers', 'download_lyrics',
                       'prefer_synced_lyrics', 'overwrite_existing', 'embed_cover_art'],
    'table_artists': ['monitored'],
    'table_albums': ['monitored', 'override_prefer_synced',
                     'override_download_covers', 'override_download_lyrics'],
    'table_tracks': ['hasLyrics'],
}


def _migrate_bool_columns():
    """Migrate Text 'True'/'False' values to integer 1/0 for Boolean columns."""
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(engine)

    for table_name, columns in _BOOL_MIGRATION_COLUMNS.items():
        if not inspector.has_table(table_name):
            continue
        for col_name in columns:
            # Check if any rows still have text 'True' or 'False'
            with engine.begin() as conn:
                result = conn.execute(text(
                    f"SELECT COUNT(*) FROM {table_name} WHERE typeof({col_name}) = 'text' "
                    f"AND ({col_name} = 'True' OR {col_name} = 'False')"
                )).scalar()
                if result and result > 0:
                    conn.execute(text(
                        f"UPDATE {table_name} SET {col_name} = CASE "
                        f"WHEN {col_name} = 'True' THEN 1 "
                        f"WHEN {col_name} = 'False' THEN 0 "
                        f"ELSE {col_name} END"
                    ))
                    logger.info(f"Migration: converted {result} rows in {table_name}.{col_name} "
                                f"from text to boolean")


def init_db():
    metadata.create_all(engine)

    # Auto-migrate: add any missing columns to existing tables
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(engine)
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing_cols = {c['name'] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing_cols:
                col_type = col.type.compile(engine.dialect)
                default_clause = ''
                if col.default is not None:
                    default_clause = f" DEFAULT {col.default.arg!r}"
                with engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}{default_clause}'
                    ))
                logger.info(f"Migration: added column {col.name} to {table.name}")

    # Migrate text booleans to real booleans
    _migrate_bool_columns()

    # Stamp with Alembic 'head' if alembic_version table doesn't exist yet
    # This marks existing databases as up-to-date for future Alembic migrations
    from sqlalchemy import inspect as sa_inspect2
    inspector2 = sa_inspect2(engine)
    if not inspector2.has_table('alembic_version'):
        try:
            from alembic.config import Config as AlembicConfig
            from alembic import command as alembic_cmd
            import os as _os
            alembic_ini = _os.path.join(_os.path.dirname(__file__), '..', '..', 'alembic.ini')
            if _os.path.exists(alembic_ini):
                alembic_cfg = AlembicConfig(alembic_ini)
                alembic_cmd.stamp(alembic_cfg, 'head')
                logger.info("Alembic: stamped database with current head revision")
        except Exception as e:
            logger.debug(f"Alembic stamp skipped: {e}")

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
                is_default=True,
                download_covers=True,
                download_lyrics=True,
                cover_providers='["musicbrainz","deezer","itunes","fanart","theaudiodb"]',
                lyrics_providers='["lrclib","musixmatch","netease","genius"]',
                prefer_synced_lyrics=True,
                cover_format='jpg',
                overwrite_existing=False,
                embed_cover_art=False,
                created_at_timestamp=datetime.now(),
                updated_at_timestamp=datetime.now(),
            )
        )
