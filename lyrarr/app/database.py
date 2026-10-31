
import atexit
import json
import logging
import os
from datetime import datetime

from sqlalchemy import (  # noqa W0611
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    delete,
    func,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import close_all_sessions, mapped_column, scoped_session, sessionmaker
from sqlalchemy.pool import NullPool

from .get_args import args

logger = logging.getLogger(__name__)

url = f'sqlite:///{os.path.join(args.config_dir, "db", "lyrarr.db")}'
logger.debug(f"Connecting to SQLite database: {url}")
engine = create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")

from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # WAL lets readers and a writer coexist instead of blocking each other,
    # which matters because the web server (many threads), the scheduler, and
    # the SignalR/webhook sync threads all hit SQLite concurrently.
    cursor.execute("PRAGMA journal_mode=WAL")
    # Wait up to 5s for a lock instead of immediately raising "database is locked".
    cursor.execute("PRAGMA busy_timeout=5000")
    # NORMAL is safe under WAL and much faster than the default FULL fsync.
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


session_factory = sessionmaker(bind=engine)
database = scoped_session(session_factory)


def close_database():
    close_all_sessions()
    engine.dispose()


@atexit.register
def _stop_worker_threads():
    database.remove()


# SIGTERM/SIGINT are handled by the web server (lyrarr/app/server.py), which
# stops the scheduler and HTTP server and then calls close_database().

Base = declarative_base()
metadata = Base.metadata


def _serialize_value(v):
    """Convert non-JSON-serializable values to strings."""
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def parse_json_column(value):
    """Parse a JSON text column; None for NULL/empty or invalid JSON (never raises)."""
    if value is None or value == '':
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def dump_json_column(value):
    """Serialise a value for a JSON text column (None stays NULL)."""
    if value is None:
        return None
    return json.dumps(value, default=str, ensure_ascii=False)


def _row_to_dict(row, json_columns=()):
    out = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        out[column.name] = parse_json_column(value) if column.name in json_columns else _serialize_value(value)
    return out


# ---------------------------------------------------------------------------
# Shared constants (PLAN §1.1.3)
# ---------------------------------------------------------------------------

HISTORY_ACTION_DOWNLOAD = 1
HISTORY_ACTION_UPGRADE = 2
HISTORY_ACTION_DELETE = 3
HISTORY_ACTION_MANUAL = 4
HISTORY_ACTION_PUBLISH = 5
HISTORY_ACTION_CLASSIFY = 6
HISTORY_ACTION_AUDIT_REPAIR = 7

LYRICS_STATUSES = ('missing', 'available', 'manual', 'blacklisted', 'instrumental')
LYRICS_SOURCES = ('auto', 'manual', 'translated', 'generated')

# Providers that pre-1.7 manual saves recorded with history action 1.
MANUAL_PROVIDER_LABELS = ('editor', 'upload', 'restored', 'manual', 'batch-sync-generate')


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
    lyrics_selection_mode = mapped_column(Text, default='best_score')  # 'best_score' | 'prefer_synced' | 'prefer_plain'
    auto_detect_language = mapped_column(Boolean, default=True)
    auto_translate = mapped_column(Text, default='off')  # 'off' | 'dual' | 'replace'
    translate_target_lang = mapped_column(Text, default='en')
    translate_only_foreign = mapped_column(Boolean, default=True)
    score_threshold = mapped_column(Integer, default=0)  # minimum match score 0-100, 0 = accept all
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
    language_override = mapped_column(Text, nullable=True)  # per-artist language override (ISO 639-1)
    translate_target_override = mapped_column(Text, nullable=True)  # per-artist translation target
    profileId = mapped_column(Integer, ForeignKey('table_profiles.id', ondelete='SET NULL'), nullable=True)
    created_at_timestamp = mapped_column(DateTime)
    updated_at_timestamp = mapped_column(DateTime)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


class TableAlbums(Base):
    __tablename__ = 'table_albums'

    lidarrAlbumId = mapped_column(Integer, primary_key=True)
    mbId = mapped_column(Text)  # MusicBrainz Release Group ID
    artistId = mapped_column(Integer, ForeignKey('table_artists.lidarrArtistId', ondelete='CASCADE'), index=True)
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
    cover_retry_count = mapped_column(Integer, default=0)  # failed cover searches (backoff step)
    cover_retry_after = mapped_column(DateTime, nullable=True)  # next eligible cover search
    created_at_timestamp = mapped_column(DateTime)
    updated_at_timestamp = mapped_column(DateTime)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


class TableTracks(Base):
    __tablename__ = 'table_tracks'

    lidarrTrackId = mapped_column(Integer, primary_key=True)
    mbId = mapped_column(Text)  # MusicBrainz Recording ID
    albumId = mapped_column(Integer, ForeignKey('table_albums.lidarrAlbumId', ondelete='CASCADE'), index=True)
    artistId = mapped_column(Integer, ForeignKey('table_artists.lidarrArtistId', ondelete='CASCADE'), index=True)
    title = mapped_column(Text, nullable=False)
    trackNumber = mapped_column(Integer)
    discNumber = mapped_column(Integer, default=1)
    duration = mapped_column(Integer)  # in seconds (sync converts Lidarr's ms)
    path = mapped_column(Text)
    hasLyrics = mapped_column(Boolean, default=False)  # whether synced lyrics exist
    lyrics_status = mapped_column(Text, default='missing', index=True)  # missing, available, manual, blacklisted, instrumental
    detected_language = mapped_column(Text, nullable=True)  # ISO 639-1 code (e.g. 'en', 'ja')
    is_synced = mapped_column(Boolean, default=False)  # whether current lyrics file is synced LRC
    lyrics_retry_count = mapped_column(Integer, default=0)  # number of failed lyrics search attempts
    lyrics_retry_after = mapped_column(DateTime, nullable=True)  # next eligible retry time (exponential backoff)
    # Who wrote the current lyrics file: 'auto' (scheduled downloader), 'manual'
    # (user pick/upload/edit/restore), 'translated', 'generated' (Whisper).
    # NULL for files written before this column existed. Automatic passes only
    # replace 'auto'/NULL lyrics so user-curated files are never clobbered.
    lyrics_source = mapped_column(Text, nullable=True)
    # --- instrumental policy (PLAN §1.1.1, §2) ---
    # 'title' | 'consensus' | 'user'; NULL on an instrumental row = legacy/unknown.
    instrumental_reason = mapped_column(Text, nullable=True)
    # JSON list of votes; also set on 'missing' rows as the "likely instrumental" hint.
    instrumental_evidence = mapped_column(Text, nullable=True)
    instrumental_checked_at = mapped_column(DateTime, nullable=True)
    # User decision: 'never' (has vocals) | 'always' (is instrumental) | NULL.
    instrumental_override = mapped_column(Text, nullable=True)
    # JSON {"why","reason","actor","at","evidence_count"} of a flag removed by a non-user clear.
    instrumental_prev = mapped_column(Text, nullable=True)
    # 'detected' | 'manual' | 'translated'; NULL is treated as 'detected'.
    language_source = mapped_column(Text, nullable=True)
    # Per-track credited artist (NULL = album artist).
    track_artist = mapped_column(Text, nullable=True)
    # --- provenance of the current lyrics file ---
    lyrics_provider = mapped_column(Text, nullable=True)
    lyrics_source_id = mapped_column(Text, nullable=True)
    lyrics_match_score = mapped_column(Float, nullable=True)
    lyrics_match_json = mapped_column(Text, nullable=True)
    lyrics_content_hash = mapped_column(Text, nullable=True)
    # Replace mode (§2.8): re-search an 'available' row, keep the file if nothing better.
    lyrics_replace_requested = mapped_column(DateTime, nullable=True)
    # --- backoff (§3.6) ---
    lyrics_transient_count = mapped_column(Integer, default=0)
    lyrics_cooldown_count = mapped_column(Integer, default=0)
    upgrade_retry_count = mapped_column(Integer, default=0)
    upgrade_retry_after = mapped_column(DateTime, nullable=True)
    # Written by every 1.7+ backoff write; NULL with a non-trivial backoff = older version.
    lyrics_retry_set_at = mapped_column(DateTime, nullable=True)
    upgrade_retry_set_at = mapped_column(DateTime, nullable=True)
    created_at_timestamp = mapped_column(DateTime)
    updated_at_timestamp = mapped_column(DateTime)

    JSON_COLUMNS = ('instrumental_evidence', 'instrumental_prev', 'lyrics_match_json')

    def to_dict(self):
        return _row_to_dict(self, self.JSON_COLUMNS)


class TableHistory(Base):
    __tablename__ = 'table_history'

    id = mapped_column(Integer, primary_key=True)
    # 1=download, 2=upgrade, 3=delete, 4=manual, 5=publish, 6=classify, 7=audit repair
    action = mapped_column(Integer, nullable=False)
    description = mapped_column(Text, nullable=False)
    metadata_type = mapped_column(Text)  # 'cover' or 'lyrics'
    provider = mapped_column(Text)
    lidarrAlbumId = mapped_column(Integer, ForeignKey('table_albums.lidarrAlbumId', ondelete='CASCADE'), index=True)
    lidarrTrackId = mapped_column(Integer, ForeignKey('table_tracks.lidarrTrackId', ondelete='CASCADE'), index=True)
    lidarrArtistId = mapped_column(Integer, ForeignKey('table_artists.lidarrArtistId', ondelete='CASCADE'), index=True)
    timestamp = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)
    metadata_path = mapped_column(Text)
    details = mapped_column(Text, nullable=True)  # JSON: classification / audit evidence

    def to_dict(self):
        return _row_to_dict(self, ('details',))


class TableBlacklist(Base):
    __tablename__ = 'table_blacklist'

    id = mapped_column(Integer, primary_key=True)
    metadata_type = mapped_column(Text)  # 'cover' or 'lyrics'
    provider = mapped_column(Text)
    # Normalized content hash of a rejected lyrics result (timestamps/metadata
    # stripped, lowercased). Lets the auto-downloader skip a specific wrong match
    # on re-runs regardless of which provider returns it.
    content_hash = mapped_column(Text)
    lidarrAlbumId = mapped_column(Integer, ForeignKey('table_albums.lidarrAlbumId', ondelete='CASCADE'), index=True)
    lidarrTrackId = mapped_column(Integer, ForeignKey('table_tracks.lidarrTrackId', ondelete='CASCADE'), index=True)
    timestamp = mapped_column(DateTime, default=datetime.now)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


class TableLyricsVersions(Base):
    """Store previous lyrics versions in-app instead of leaving extra files in music dirs."""
    __tablename__ = 'table_lyrics_versions'

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    lidarrTrackId = mapped_column(Integer, ForeignKey('table_tracks.lidarrTrackId', ondelete='CASCADE'), index=True)
    content = mapped_column(Text, nullable=False)
    lyrics_type = mapped_column(Text, nullable=False)  # 'synced' or 'plain'
    provider = mapped_column(Text)
    translated_from = mapped_column(Text, nullable=True)  # source language if this is a translation
    # Who replaced/removed this version ('lrclib', 'editor', 'batch-delete', 'audit:F-6', ...).
    # From 1.7 `provider` is the ORIGIN of the archived text; rows with
    # replaced_by NULL are older and their provider is the replacer.
    replaced_by = mapped_column(Text, nullable=True)
    timestamp = mapped_column(DateTime, default=datetime.now)

    def to_dict(self):
        return {column.name: _serialize_value(getattr(self, column.name)) for column in self.__table__.columns}


# ---------------------------------------------------------------------------
# Audit & repair, data migrations, library roots, tombstones (PLAN §1.1.2)
# ---------------------------------------------------------------------------

class TableAuditRuns(Base):
    __tablename__ = 'table_audit_runs'

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode = mapped_column(Text)  # 'dry_run' | 'apply'
    trigger = mapped_column(Text)  # 'user' | 'scheduled' | 'bootstrap' | 'legacy_endpoint'
    status = mapped_column(Text)  # 'running' | 'done' | 'failed' | 'cancelled' | 'aborted'
    parent_run_id = mapped_column(Integer, ForeignKey('table_audit_runs.id', ondelete='SET NULL'), nullable=True)
    options = mapped_column(Text)
    library = mapped_column(Text)
    totals = mapped_column(Text)
    checks = mapped_column(Text)
    progress = mapped_column(Text)
    error = mapped_column(Text, nullable=True)
    started_at = mapped_column(DateTime)
    finished_at = mapped_column(DateTime)

    JSON_COLUMNS = ('options', 'library', 'totals', 'checks', 'progress')

    def to_dict(self):
        return _row_to_dict(self, self.JSON_COLUMNS)


class TableAuditIssues(Base):
    __tablename__ = 'table_audit_issues'
    __table_args__ = (Index('ix_audit_issue_run_check', 'run_id', 'check_id'),)

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id = mapped_column(Integer, ForeignKey('table_audit_runs.id', ondelete='CASCADE'), index=True)
    check_id = mapped_column(Text, index=True)
    entity_type = mapped_column(Text)  # 'track'|'album'|'artist'|'file'|'profile'|'version'|'db'
    entity_id = mapped_column(Integer, nullable=True)
    entity_key = mapped_column(Text)
    path = mapped_column(Text, nullable=True)
    severity = mapped_column(Text)  # 'critical'|'high'|'medium'|'low'|'info'
    title = mapped_column(Text)
    details = mapped_column(Text)
    fix_action = mapped_column(Text, nullable=True)
    fix_mode = mapped_column(Text)  # 'auto' | 'review' | 'report'
    status = mapped_column(Text)  # 'open'|'fixed'|'failed'|'skipped'|'ignored'|'pending'
    message = mapped_column(Text, nullable=True)
    lidarrTrackId = mapped_column(Integer, nullable=True)
    lidarrAlbumId = mapped_column(Integer, nullable=True)
    lidarrArtistId = mapped_column(Integer, nullable=True)
    created_at = mapped_column(DateTime)
    resolved_at = mapped_column(DateTime)

    def to_dict(self):
        return _row_to_dict(self, ('details',))


class TableAuditPins(Base):
    """Evidence (archived versions / history rows) kept while its issue is open."""
    __tablename__ = 'table_audit_pins'
    __table_args__ = (Index('ix_audit_pin_kind_ref', 'kind', 'ref_id'),)

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id = mapped_column(Integer, ForeignKey('table_audit_issues.id', ondelete='CASCADE'), index=True)
    kind = mapped_column(Text)  # 'version' | 'history'
    ref_id = mapped_column(Integer)

    def to_dict(self):
        return _row_to_dict(self)


class TableAuditIgnores(Base):
    __tablename__ = 'table_audit_ignores'
    __table_args__ = (UniqueConstraint('check_id', 'entity_key', name='uq_audit_ignore_check_entity'),)

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    check_id = mapped_column(Text)
    entity_key = mapped_column(Text)
    created_at = mapped_column(DateTime, default=datetime.now)

    def to_dict(self):
        return _row_to_dict(self)


class TableDataMigrations(Base):
    __tablename__ = 'table_data_migrations'

    id = mapped_column(Text, primary_key=True)
    applied_at = mapped_column(DateTime)
    result = mapped_column(Text)  # JSON

    def to_dict(self):
        return _row_to_dict(self, ('result',))


class TableLibraryRoots(Base):
    """Library root folders (Lidarr's /rootfolder, or derived artist parents)."""
    __tablename__ = 'table_library_roots'

    path = mapped_column(Text, primary_key=True)  # os.path.normpath, no trailing slash
    source = mapped_column(Text)  # 'lidarr' | 'derived'
    updated_at = mapped_column(DateTime)

    def to_dict(self):
        return _row_to_dict(self)


class TableTrackTombstones(Base):
    """User state of track rows removed by sync, restored on re-import (30 days)."""
    __tablename__ = 'table_track_tombstones'

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    lidarrAlbumId = mapped_column(Integer, index=True)
    mbId = mapped_column(Text, nullable=True, index=True)
    path_stem = mapped_column(Text, index=True)
    lidarrTrackId = mapped_column(Integer)
    state = mapped_column(Text)  # JSON; never restored as status 'instrumental'
    removed_at = mapped_column(DateTime)

    def to_dict(self):
        return _row_to_dict(self, ('state',))


def legacy_non_download_filter():
    """True unless the history row is a pre-1.7 publish or manual save that was
    recorded with a download action (NULL-safe on description and provider)."""
    return and_(
        or_(TableHistory.description.is_(None), ~TableHistory.description.like('Published lyrics%')),
        or_(
            TableHistory.provider.is_(None),
            and_(
                TableHistory.provider.notin_(MANUAL_PROVIDER_LABELS),
                ~TableHistory.provider.like('batch-translate-%'),
            ),
        ),
    )


# Columns that were migrated from Text ('True'/'False') to Boolean (1/0)
_BOOL_MIGRATION_COLUMNS = {
    'table_profiles': ['is_default', 'download_covers', 'download_lyrics',
                       'prefer_synced_lyrics', 'overwrite_existing', 'embed_cover_art'],
    'table_artists': ['monitored'],
    'table_albums': ['monitored', 'override_prefer_synced',
                     'override_download_covers', 'override_download_lyrics'],
    'table_tracks': ['hasLyrics'],
}


def _rebuild_text_bool_columns():
    """Rebuild Boolean columns that legacy schemas declared as TEXT.

    Databases created by old lyrarr versions declared boolean columns as TEXT
    (e.g. `download_covers TEXT`). SQLite's TEXT affinity converts every
    written integer to a string, so saving download_covers=False stores the
    string '0' — which Python reads back as truthy. That made disabled
    profile toggles appear permanently enabled, and broke `monitored == True`
    SQL filters ('1' = 1 is false in SQLite when types differ).

    Value-level fixes can't help: the affinity re-corrupts every subsequent
    write. The column itself must be rebuilt with a real BOOLEAN type.
    """
    import sqlite3 as _sqlite3

    from sqlalchemy import inspect as sa_inspect

    if _sqlite3.sqlite_version_info < (3, 35, 0):  # DROP COLUMN support
        logger.warning(
            "SQLite %s is too old to rebuild legacy TEXT boolean columns "
            "(3.35+ needed); boolean settings may not save correctly",
            _sqlite3.sqlite_version,
        )
        return

    inspector = sa_inspect(engine)
    for table_name, columns in _BOOL_MIGRATION_COLUMNS.items():
        if not inspector.has_table(table_name):
            continue
        declared = {c['name']: str(c['type']).upper() for c in inspector.get_columns(table_name)}
        for col_name in columns:
            tmp = f"{col_name}__boolfix"
            col_type = declared.get(col_name)
            leftover = tmp in declared
            if not leftover and (col_type is None or 'BOOL' in col_type):
                continue
            try:
                _rebuild_bool_column(table_name, col_name, col_type, leftover)
                if col_type is not None and 'BOOL' not in col_type:
                    logger.info(
                        f"Migration: rebuilt {table_name}.{col_name} as BOOLEAN (was {col_type})"
                    )
            except Exception as e:
                logger.error(f"Migration: failed to rebuild {table_name}.{col_name}: {e}")


def _rebuild_bool_column(table_name, col_name, col_type, leftover):
    """Rebuild one column as BOOLEAN in a single explicit transaction.

    The engine runs in AUTOCOMMIT mode, so `engine.begin()` would not make the
    ADD/UPDATE/DROP/RENAME sequence atomic; SQLite DDL is transactional, so an
    explicit BEGIN on the raw connection does. A `<col>__boolfix` column left
    by an older, non-atomic run is cleaned up first (or, if the original
    column is already gone, renamed into place).
    """
    tmp = f"{col_name}__boolfix"
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            if leftover:
                if col_type is None:
                    conn.exec_driver_sql(f'ALTER TABLE {table_name} RENAME COLUMN {tmp} TO {col_name}')
                    conn.exec_driver_sql('COMMIT')
                    logger.info(f"Migration: restored {table_name}.{col_name} from an interrupted rebuild")
                    return
                conn.exec_driver_sql(f'ALTER TABLE {table_name} DROP COLUMN {tmp}')
                if 'BOOL' in col_type:
                    conn.exec_driver_sql('COMMIT')
                    logger.info(f"Migration: dropped leftover column {table_name}.{tmp}")
                    return
            conn.exec_driver_sql(f'ALTER TABLE {table_name} ADD COLUMN {tmp} BOOLEAN')
            # NULL stays NULL (override_* columns use it for "no override").
            conn.exec_driver_sql(
                f"UPDATE {table_name} SET {tmp} = CASE "
                f"WHEN {col_name} IS NULL THEN NULL "
                f"WHEN lower(CAST({col_name} AS TEXT)) IN ('0', 'false', '', 'no', 'off', 'none') THEN 0 "
                f"ELSE 1 END"
            )
            conn.exec_driver_sql(f'ALTER TABLE {table_name} DROP COLUMN {col_name}')
            conn.exec_driver_sql(f'ALTER TABLE {table_name} RENAME COLUMN {tmp} TO {col_name}')
            conn.exec_driver_sql('COMMIT')
        except Exception:
            try:
                conn.exec_driver_sql('ROLLBACK')
            except Exception:
                pass
            raise


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


def _alembic_stamp_head():
    """Stamp the database with the current Alembic head, failing soft.

    The Alembic config is built in code (pointing at lyrarr/migrations) rather
    than read from alembic.ini, which isn't shipped in the Docker image; the
    `configure_logger` attribute tells migrations/env.py to leave the app's
    logging configuration alone.
    """
    try:
        from alembic import command as alembic_cmd
        from alembic.config import Config as AlembicConfig

        alembic_cfg = AlembicConfig()
        alembic_cfg.set_main_option(
            'script_location', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations')
        )
        alembic_cfg.attributes['configure_logger'] = False
        alembic_cmd.stamp(alembic_cfg, 'head')
        logger.info("Alembic: stamped database with current head revision")
    except Exception as e:
        logger.warning(f"Alembic stamp skipped: {e}")


def init_db():
    metadata.create_all(engine)

    # Auto-migrate: add any missing columns to existing tables
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text
    inspector = sa_inspect(engine)
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing_cols = {c['name'] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing_cols:
                col_type = col.type.compile(engine.dialect)
                # Only emit a literal DEFAULT for scalar (non-callable) defaults.
                # A callable default like datetime.now would render as a Python
                # repr and produce invalid SQL; columns with such defaults are
                # simply added without a DEFAULT (existing rows get NULL, and the
                # ORM applies the default on subsequent writes).
                default_clause = ''
                default_arg = getattr(col.default, 'arg', None) if col.default is not None else None
                if default_arg is not None and not callable(default_arg):
                    default_clause = f" DEFAULT {default_arg!r}"
                with engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}{default_clause}'
                    ))
                logger.info(f"Migration: added column {col.name} to {table.name}")

    # Rebuild legacy TEXT-typed boolean columns, then migrate any remaining
    # text 'True'/'False' values in correctly-typed columns.
    _rebuild_text_bool_columns()
    _migrate_bool_columns()

    # create_all() only creates indexes together with new tables; add any
    # model-declared index that an existing database is missing.
    for table in metadata.sorted_tables:
        for index in table.indexes:
            try:
                index.create(bind=engine, checkfirst=True)
            except Exception as e:
                logger.warning(f"Migration: could not create index {index.name}: {e}")

    # Stamp with Alembic 'head' if alembic_version table doesn't exist yet
    # This marks existing databases as up-to-date for future Alembic migrations
    from sqlalchemy import inspect as sa_inspect2
    if not sa_inspect2(engine).has_table('alembic_version'):
        _alembic_stamp_head()

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
                lyrics_selection_mode='best_score',
                auto_detect_language=True,
                auto_translate='off',
                translate_target_lang='en',
                translate_only_foreign=True,
                score_threshold=0,
                cover_format='jpg',
                overwrite_existing=False,
                embed_cover_art=False,
                created_at_timestamp=datetime.now(),
                updated_at_timestamp=datetime.now(),
            )
        )

    # One-shot pure-SQL data fixes (PLAN §1.1.4).
    run_data_migrations()


# ---------------------------------------------------------------------------
# Data migrations (PLAN §1.1.4)
#
# Each registered id runs exactly once and is recorded in table_data_migrations
# together with its result. They are pure SQL (no filesystem, no network):
# anything that needs the disk runs in the audit bootstrap instead.
# ---------------------------------------------------------------------------

def _dm_app_version(conn):
    try:
        from lyrarr import __version__ as version
    except Exception:
        version = None
    return {'version': version, 'installed_at': datetime.now().isoformat()}


def _dm_stale_lyrics_source(conn):
    n = conn.execute(text(
        "UPDATE table_tracks SET lyrics_source = NULL "
        "WHERE (lyrics_status IS NULL OR lyrics_status != 'available') AND lyrics_source IS NOT NULL"
    )).rowcount
    return {'rows': n}


def _dm_has_lyrics_invariant(conn):
    has = conn.execute(text(
        "UPDATE table_tracks SET hasLyrics = CASE WHEN lyrics_status IN ('available','manual') THEN 1 ELSE 0 END "
        "WHERE hasLyrics IS NULL OR hasLyrics != (CASE WHEN lyrics_status IN ('available','manual') THEN 1 ELSE 0 END)"
    )).rowcount
    terminal = conn.execute(text(
        "UPDATE table_tracks SET is_synced = 0, detected_language = NULL "
        "WHERE lyrics_status IN ('blacklisted','instrumental') "
        "AND (is_synced IS NULL OR is_synced != 0 OR detected_language IS NOT NULL)"
    )).rowcount
    return {'hasLyrics': has, 'terminal': terminal}


def _dm_language_source(conn):
    n = conn.execute(text(
        "UPDATE table_tracks SET language_source = 'translated' "
        "WHERE lyrics_source = 'translated' AND (language_source IS NULL OR language_source != 'translated')"
    )).rowcount
    return {'rows': n}


def _dm_instrumental_title_reason(conn):
    from lyrarr.metadata.lyrics_text import instrumental_title_tier

    rows = conn.execute(text(
        "SELECT lidarrTrackId, title FROM table_tracks "
        "WHERE lyrics_status = 'instrumental' AND instrumental_reason IS NULL"
    )).all()
    evidence = json.dumps([{'source': 'title', 'kind': 'title'}])
    ids = [r[0] for r in rows if instrumental_title_tier(r[1]) == 'strong']
    for track_id in ids:
        conn.execute(
            text("UPDATE table_tracks SET instrumental_reason = 'title', instrumental_evidence = :ev "
                 "WHERE lidarrTrackId = :id AND instrumental_reason IS NULL"),
            {'ev': evidence, 'id': track_id},
        )
    return {'rows': len(ids), 'legacy_unknown': len(rows) - len(ids)}


def _dm_upgrade_backoff_split(conn):
    # The pre-1.7 upgrade pass deferred available rows through lyrics_retry_*;
    # move that backoff to its own columns (kept, not reset). *_set_at stay
    # NULL: that marks the backoff as written by an older version.
    n = conn.execute(text(
        "UPDATE table_tracks SET "
        "upgrade_retry_count = COALESCE(lyrics_retry_count, 0), "
        "upgrade_retry_after = lyrics_retry_after, "
        "upgrade_retry_set_at = NULL, "
        "lyrics_retry_count = 0, lyrics_retry_after = NULL, lyrics_retry_set_at = NULL "
        "WHERE lyrics_status IN ('available','manual') "
        "AND (COALESCE(lyrics_retry_count, 0) > 0 OR lyrics_retry_after IS NOT NULL)"
    )).rowcount
    return {'rows': n}


DATA_MIGRATIONS = (
    ('2026-10-app-version-1.7.0', _dm_app_version),
    ('2026-10-stale-lyrics-source', _dm_stale_lyrics_source),
    ('2026-10-hasLyrics-invariant', _dm_has_lyrics_invariant),
    ('2026-10-language-source', _dm_language_source),
    ('2026-10-instrumental-title-reason', _dm_instrumental_title_reason),
    ('2026-10-upgrade-backoff-split', _dm_upgrade_backoff_split),
)


def run_data_migrations():
    """Run every registered data migration that has not been applied yet.

    Each one runs in its own transaction together with its bookkeeping row,
    so a failure leaves neither a half-applied change nor a record (it is
    retried on the next start). Idempotent; never touches the filesystem.
    Returns the ids applied by this call.
    """
    applied_now = []
    try:
        with engine.connect() as conn:
            done = set(conn.execute(text('SELECT id FROM table_data_migrations')).scalars().all())
    except Exception as e:
        logger.error(f"Data migrations skipped: {e}")
        return applied_now

    for mig_id, fn in DATA_MIGRATIONS:
        if mig_id in done:
            continue
        with engine.connect() as conn:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            try:
                # Re-check inside the write lock (two processes starting at once).
                if conn.execute(text('SELECT 1 FROM table_data_migrations WHERE id = :id'),
                                {'id': mig_id}).first():
                    conn.exec_driver_sql('ROLLBACK')
                    continue
                result = fn(conn)
                conn.execute(
                    text('INSERT INTO table_data_migrations (id, applied_at, result) VALUES (:id, :at, :r)'),
                    {'id': mig_id, 'at': datetime.now(), 'r': json.dumps(result, default=str)},
                )
                conn.exec_driver_sql('COMMIT')
                applied_now.append(mig_id)
                logger.info(f"Data migration {mig_id} applied: {result}")
            except Exception as e:
                try:
                    conn.exec_driver_sql('ROLLBACK')
                except Exception:
                    pass
                logger.error(f"Data migration {mig_id} failed (will retry on next start): {e}")
    return applied_now


def data_migration_applied(mig_id):
    """applied_at of a recorded data migration, or None."""
    row = database.execute(
        select(TableDataMigrations).where(TableDataMigrations.id == mig_id)
    ).scalars().first()
    return row.applied_at if row else None


def record_data_migration(mig_id, result=None):
    """Record a migration id (used by the audit bootstrap: 'audit-bootstrap-v1')."""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    database.execute(
        sqlite_insert(TableDataMigrations)
        .values(id=mig_id, applied_at=datetime.now(), result=dump_json_column(result))
        .on_conflict_do_nothing(index_elements=['id'])
    )
