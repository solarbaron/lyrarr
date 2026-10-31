-- Legacy lyrarr database schema (v1.0 era), captured once with
-- `sqlite3 data/db/lyrarr.db .schema` for tests/test_schema_upgrade.py.
-- TEXT-typed booleans, no lyrics_source / retry / instrumental columns.
-- Data rows are synthetic.

CREATE TABLE alembic_version (
	version_num VARCHAR(32) NOT NULL, 
	CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

CREATE TABLE system (
	id INTEGER NOT NULL, 
	configured TEXT, 
	updated TEXT, 
	PRIMARY KEY (id)
);

CREATE TABLE table_albums (
	"lidarrAlbumId" INTEGER NOT NULL, 
	"mbId" TEXT, 
	"artistId" INTEGER, 
	title TEXT NOT NULL, 
	year INTEGER, 
	path TEXT, 
	monitored TEXT, 
	overview TEXT, 
	cover TEXT, 
	genres TEXT, 
	"albumType" TEXT, 
	cover_status TEXT, 
	lyrics_status TEXT, 
	missing_covers TEXT, 
	missing_lyrics TEXT, 
	"profileId" INTEGER, 
	created_at_timestamp DATETIME, 
	updated_at_timestamp DATETIME, override_cover_format TEXT, override_prefer_synced TEXT, override_download_covers TEXT, override_download_lyrics TEXT, 
	PRIMARY KEY ("lidarrAlbumId"), 
	FOREIGN KEY("artistId") REFERENCES table_artists ("lidarrArtistId") ON DELETE CASCADE, 
	FOREIGN KEY("profileId") REFERENCES table_profiles (id) ON DELETE SET NULL
);

CREATE TABLE table_artists (
	"lidarrArtistId" INTEGER NOT NULL, 
	"mbId" TEXT, 
	name TEXT NOT NULL, 
	"sortName" TEXT, 
	path TEXT NOT NULL, 
	monitored TEXT, 
	overview TEXT, 
	fanart TEXT, 
	poster TEXT, 
	tags TEXT, 
	metadata_status TEXT, 
	"profileId" INTEGER, 
	created_at_timestamp DATETIME, 
	updated_at_timestamp DATETIME, 
	PRIMARY KEY ("lidarrArtistId"), 
	FOREIGN KEY("profileId") REFERENCES table_profiles (id) ON DELETE SET NULL
);

CREATE TABLE table_blacklist (
	id INTEGER NOT NULL, 
	metadata_type TEXT, 
	provider TEXT, 
	"lidarrAlbumId" INTEGER, 
	"lidarrTrackId" INTEGER, 
	timestamp DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY("lidarrAlbumId") REFERENCES table_albums ("lidarrAlbumId") ON DELETE CASCADE, 
	FOREIGN KEY("lidarrTrackId") REFERENCES table_tracks ("lidarrTrackId") ON DELETE CASCADE
);

CREATE TABLE table_history (
	id INTEGER NOT NULL, 
	action INTEGER NOT NULL, 
	description TEXT NOT NULL, 
	metadata_type TEXT, 
	provider TEXT, 
	"lidarrAlbumId" INTEGER, 
	"lidarrTrackId" INTEGER, 
	"lidarrArtistId" INTEGER, 
	timestamp DATETIME NOT NULL, 
	metadata_path TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY("lidarrAlbumId") REFERENCES table_albums ("lidarrAlbumId") ON DELETE CASCADE, 
	FOREIGN KEY("lidarrTrackId") REFERENCES table_tracks ("lidarrTrackId") ON DELETE CASCADE, 
	FOREIGN KEY("lidarrArtistId") REFERENCES table_artists ("lidarrArtistId") ON DELETE CASCADE
);

CREATE TABLE table_profiles (
	id INTEGER NOT NULL, 
	name TEXT NOT NULL, 
	is_default TEXT, 
	download_covers TEXT, 
	download_lyrics TEXT, 
	cover_providers TEXT, 
	lyrics_providers TEXT, 
	prefer_synced_lyrics TEXT, 
	cover_format TEXT, 
	overwrite_existing TEXT, 
	created_at_timestamp DATETIME, 
	updated_at_timestamp DATETIME, embed_cover_art TEXT DEFAULT 'False', 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE table_tracks (
	"lidarrTrackId" INTEGER NOT NULL, 
	"mbId" TEXT, 
	"albumId" INTEGER, 
	"artistId" INTEGER, 
	title TEXT NOT NULL, 
	"trackNumber" INTEGER, 
	"discNumber" INTEGER, 
	duration INTEGER, 
	path TEXT, 
	"hasLyrics" TEXT, 
	lyrics_status TEXT, 
	created_at_timestamp DATETIME, 
	updated_at_timestamp DATETIME, 
	PRIMARY KEY ("lidarrTrackId"), 
	FOREIGN KEY("albumId") REFERENCES table_albums ("lidarrAlbumId") ON DELETE CASCADE, 
	FOREIGN KEY("artistId") REFERENCES table_artists ("lidarrArtistId") ON DELETE CASCADE
);

-- Columns that later versions (<= 1.6) added through init_db's auto-add loop.
ALTER TABLE table_tracks ADD COLUMN detected_language TEXT;
ALTER TABLE table_tracks ADD COLUMN is_synced BOOLEAN DEFAULT 0;
ALTER TABLE table_tracks ADD COLUMN lyrics_retry_count INTEGER DEFAULT 0;
ALTER TABLE table_tracks ADD COLUMN lyrics_retry_after DATETIME;
ALTER TABLE table_tracks ADD COLUMN lyrics_source TEXT;

INSERT INTO table_profiles (id, name, is_default, download_covers, download_lyrics, cover_providers, lyrics_providers,
    prefer_synced_lyrics, cover_format, overwrite_existing) VALUES
  (1, 'Default', 'True', 'True', 'True', '["musicbrainz"]', '["lrclib"]', 'True', 'jpg', 'False'),
  (2, 'Second', '1', 'False', 'True', '["musicbrainz"]', '["lrclib"]', 'False', 'png', '0');

INSERT INTO table_artists ("lidarrArtistId", name, path, monitored) VALUES (1, 'Synthetic Artist', '/music/Synthetic Artist', 'True');
INSERT INTO table_albums ("lidarrAlbumId", "artistId", title, path, monitored, cover_status, lyrics_status)
  VALUES (10, 1, 'Synthetic Album', '/music/Synthetic Artist/Synthetic Album', 'True', 'missing', 'unknown');

INSERT INTO table_tracks ("lidarrTrackId", "albumId", "artistId", title, duration, path, "hasLyrics", lyrics_status,
    is_synced, detected_language, lyrics_retry_count, lyrics_retry_after, lyrics_source) VALUES
  -- instrumental, STRONG title: gets reason 'title'
  (1, 10, 1, 'Placeholder Song (Instrumental)', 245, '/music/Synthetic Artist/Synthetic Album/01.flac', 'False', 'instrumental', 1, 'en', 0, NULL, NULL),
  -- instrumental, generic title: stays legacy/unknown (reason NULL)
  (2, 10, 1, 'Interlude', 130, '/music/Synthetic Artist/Synthetic Album/02.flac', 'False', 'instrumental', 0, NULL, 0, NULL, NULL),
  -- missing with a stale 'auto' source
  (3, 10, 1, 'Song Three', 200, '/music/Synthetic Artist/Synthetic Album/03.flac', 'False', 'missing', 0, NULL, 4, '2026-11-20 00:00:00', 'auto'),
  -- available with a legacy upgrade-pass backoff in lyrics_retry_*
  (4, 10, 1, 'Song Four', 210, '/music/Synthetic Artist/Synthetic Album/04.flac', 'True', 'available', 0, 'en', 2, '2026-11-05 12:00:00', 'auto'),
  -- available but hasLyrics stored as text 'False'
  (5, 10, 1, 'Song Five', 220, '/music/Synthetic Artist/Synthetic Album/05.flac', 'False', 'available', 0, 'en', 0, NULL, NULL),
  -- blacklisted with leftover flags
  (6, 10, 1, 'Song Six', 230, '/music/Synthetic Artist/Synthetic Album/06.flac', 'True', 'blacklisted', 1, 'en', 0, NULL, NULL),
  -- translated
  (7, 10, 1, 'Song Seven', 240, '/music/Synthetic Artist/Synthetic Album/07.flac', 'True', 'available', 0, 'de', 0, NULL, 'translated');

INSERT INTO table_history (id, action, description, metadata_type, provider, "lidarrTrackId", timestamp)
  VALUES (1, 1, 'Downloaded lyrics for Song Four', 'lyrics', 'lrclib', 4, '2026-09-01 10:00:00');
