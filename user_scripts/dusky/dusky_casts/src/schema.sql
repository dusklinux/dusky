CREATE TABLE IF NOT EXISTS podcasts (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    author TEXT DEFAULT '',
    description TEXT DEFAULT '',
    artwork_url TEXT DEFAULT '',
    feed_url TEXT UNIQUE NOT NULL,
    added_at INTEGER NOT NULL,
    last_refreshed INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    folder_uuid TEXT DEFAULT '',
    needs_sync INTEGER DEFAULT 0,
    subscribed INTEGER DEFAULT 1,
    http_etag TEXT DEFAULT '',
    http_modified TEXT DEFAULT '',
    episodes_sort INTEGER DEFAULT 0,
    show_archived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE NOT NULL,
    podcast_id INTEGER NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    audio_url TEXT DEFAULT '',
    file_type TEXT DEFAULT '',
    published_at INTEGER DEFAULT 0,
    duration INTEGER DEFAULT 0,
    size INTEGER DEFAULT 0,
    playing_status INTEGER DEFAULT 0,
    position INTEGER DEFAULT 0,
    starred INTEGER DEFAULT 0,
    downloaded_path TEXT DEFAULT '',
    download_state INTEGER DEFAULT 0,
    added_at INTEGER NOT NULL,
    playing_status_modified INTEGER DEFAULT 0,
    played_up_to_modified INTEGER DEFAULT 0,
    starred_modified INTEGER DEFAULT 0,
    duration_modified INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    archived_modified INTEGER DEFAULT 0,
    needs_sync INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_episodes_podcast ON episodes(podcast_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(playing_status);
CREATE INDEX IF NOT EXISTS idx_episodes_uuid ON episodes(uuid);
CREATE INDEX IF NOT EXISTS idx_episodes_published ON episodes(published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_pending ON episodes(id) WHERE needs_sync=1;
CREATE INDEX IF NOT EXISTS idx_episodes_audio ON episodes(podcast_id, audio_url);
CREATE INDEX IF NOT EXISTS idx_episodes_podcast_archived_pub ON episodes(podcast_id, archived, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_archived_pub ON episodes(archived, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_status_archived ON episodes(playing_status, archived);
CREATE INDEX IF NOT EXISTS idx_episodes_starred ON episodes(published_at DESC, id DESC) WHERE starred=1;
CREATE INDEX IF NOT EXISTS idx_episodes_downloaded ON episodes(published_at DESC, id DESC) WHERE downloaded_path!='';

CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY,
    episode_id INTEGER UNIQUE NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_position ON queue(position, id);

CREATE TABLE IF NOT EXISTS queue_actions (
    id INTEGER PRIMARY KEY,
    action INTEGER NOT NULL,
    episode_uuid TEXT DEFAULT '',
    episode_uuids TEXT DEFAULT '',
    modified INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_actions_modified ON queue_actions(modified);

-- Account podcasts are removed locally straight away.  The tombstone is kept
-- until the deletion has been accepted by the sync API, so an offline removal
-- is not lost on the next launch.
CREATE TABLE IF NOT EXISTS podcast_tombstones (
    uuid TEXT PRIMARY KEY,
    modified INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    color INTEGER DEFAULT 0,
    sort_position INTEGER DEFAULT 0,
    podcasts_sort_type INTEGER DEFAULT 0,
    added_at INTEGER DEFAULT 0,
    needs_sync INTEGER DEFAULT 0,
    local_modified INTEGER DEFAULT 0,
    is_deleted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE NOT NULL,
    original_uuid TEXT DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    is_deleted INTEGER DEFAULT 0,
    all_podcasts INTEGER DEFAULT 1,
    podcast_uuids TEXT DEFAULT '',
    episode_uuids TEXT DEFAULT '',
    audio_video INTEGER DEFAULT 0,
    not_downloaded INTEGER DEFAULT 1,
    downloaded INTEGER DEFAULT 1,
    downloading INTEGER DEFAULT 1,
    finished INTEGER DEFAULT 1,
    partially_played INTEGER DEFAULT 1,
    unplayed INTEGER DEFAULT 1,
    starred INTEGER DEFAULT 0,
    manual INTEGER DEFAULT 0,
    sort_position INTEGER DEFAULT 0,
    sort_type INTEGER DEFAULT 0,
    icon_id INTEGER DEFAULT 0,
    filter_hours INTEGER DEFAULT 0,
    filter_duration INTEGER DEFAULT 0,
    longer_than INTEGER DEFAULT 0,
    shorter_than INTEGER DEFAULT 0,
    show_archived INTEGER DEFAULT 0,
    needs_sync INTEGER DEFAULT 0,
    local_modified INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlist_episodes (
    id INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    episode_uuid TEXT NOT NULL,
    podcast_uuid TEXT DEFAULT '',
    title TEXT DEFAULT '',
    audio_url TEXT DEFAULT '',
    published_at INTEGER DEFAULT 0,
    position INTEGER NOT NULL,
    added_at INTEGER DEFAULT 0,
    UNIQUE(playlist_id, episode_uuid)
);
CREATE INDEX IF NOT EXISTS idx_playlist_episodes ON playlist_episodes(playlist_id, position);
CREATE INDEX IF NOT EXISTS idx_playlist_episodes_uuid ON playlist_episodes(episode_uuid);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS remote_episodes (
    uuid TEXT PRIMARY KEY,
    record BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS rust_runtime (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Actual elapsed listening on this desktop; independent of episode seek positions.
CREATE TABLE IF NOT EXISTS listening_days (
    day TEXT PRIMARY KEY,
    milliseconds INTEGER NOT NULL DEFAULT 0 CHECK(milliseconds >= 0)
);

-- Companion iPhone daily listening synced via FTP/local link; kept separate from
-- listening_days so Pocket Casts cloud reporting never double-counts mobile time.
CREATE TABLE IF NOT EXISTS ios_listening_days (
    day TEXT PRIMARY KEY,
    milliseconds INTEGER NOT NULL DEFAULT 0 CHECK(milliseconds >= 0),
    updated_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS local_files (
    path TEXT PRIMARY KEY,
    episode_id INTEGER UNIQUE REFERENCES episodes(id) ON DELETE SET NULL,
    size INTEGER NOT NULL,
    modified TEXT NOT NULL,
    available INTEGER NOT NULL DEFAULT 0,
    parent_dir TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL DEFAULT '',
    leading_num INTEGER,
    mtime_secs INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_local_files_available_ep ON local_files(available, episode_id);
CREATE INDEX IF NOT EXISTS idx_local_files_ep ON local_files(episode_id) WHERE episode_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_local_files_sort_filename ON local_files(
    available,
    (CASE WHEN leading_num IS NOT NULL THEN 0 ELSE 1 END) ASC,
    leading_num ASC,
    file_name COLLATE NOCASE ASC,
    path ASC
);
CREATE INDEX IF NOT EXISTS idx_local_files_sort_path ON local_files(
    available,
    parent_dir COLLATE NOCASE ASC,
    (CASE WHEN leading_num IS NOT NULL THEN 0 ELSE 1 END) ASC,
    leading_num ASC,
    file_name COLLATE NOCASE ASC,
    path ASC
);
CREATE INDEX IF NOT EXISTS idx_local_files_sort_mtime ON local_files(available, mtime_secs ASC, path ASC);

CREATE TABLE IF NOT EXISTS local_queue (
    episode_id INTEGER PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_local_queue_position ON local_queue(position, episode_id);
