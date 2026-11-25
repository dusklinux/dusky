use crate::{
    api::Api,
    db::{Db, n, now, s},
    library, stats,
    wire::sync::*,
};
use anyhow::Result;
use prost::Message;
use rusqlite::{OptionalExtension, params};
use serde_json::Value;
use std::collections::HashSet;

const HOME_FOLDER: &str = "973df93c-e4dc-41fb-879e-0c7b532ebb70";
const USER_PODCAST: &str = "da7aba5e-f11e-f11e-f11e-da7aba5ef11e";
fn wrap(record: record::Record) -> Record {
    Record {
        record: Some(record),
    }
}
fn i64v(value: i64) -> Option<Int64Value> {
    Some(Int64Value { value })
}
fn i32v(value: i32) -> Option<Int32Value> {
    Some(Int32Value { value })
}
fn boolv(value: bool) -> Option<BoolValue> {
    Some(BoolValue { value })
}
fn strv(value: &str) -> Option<StringValue> {
    Some(StringValue {
        value: value.into(),
    })
}

pub fn episode_record(e: &Value) -> SyncUserEpisode {
    let mut r = SyncUserEpisode {
        uuid: s(e, "uuid").into(),
        podcast_uuid: s(e, "podcast_uuid").into(),
        ..Default::default()
    };
    if n(e, "playing_status_modified") > 0 {
        r.playing_status = i32v(n(e, "playing_status") as i32);
        r.playing_status_modified = i64v(n(e, "playing_status_modified"));
    }
    if n(e, "played_up_to_modified") > 0 {
        r.played_up_to = i64v(n(e, "position").max(0) / 1000);
        r.played_up_to_modified = i64v(n(e, "played_up_to_modified"));
    }
    if n(e, "starred_modified") > 0 {
        r.starred = boolv(n(e, "starred") != 0);
        r.starred_modified = i64v(n(e, "starred_modified"));
    }
    if n(e, "duration_modified") > 0 {
        r.duration = i64v(n(e, "duration").max(0));
        r.duration_modified = i64v(n(e, "duration_modified"));
    }
    if n(e, "archived_modified") > 0 {
        r.is_deleted = boolv(n(e, "archived") != 0);
        r.is_deleted_modified = i64v(n(e, "archived_modified"));
    }
    r
}

// This runs inside the response transaction, reading current timestamps rather
// than the request snapshot. Edits during the HTTP request therefore win.
pub fn apply_episode(db: &Db, r: &SyncUserEpisode) -> Result<bool> {
    let id: Option<i64> = db
        .conn
        .query_row("SELECT id FROM episodes WHERE uuid=?", [&r.uuid], |row| {
            row.get(0)
        })
        .optional()?;
    let Some(id) = id else {
        // Keep unknown historical records durably; advancing the cursor cannot
        // lose them when cache retrieval fails or an episode arrives later.
        let previous: Option<Vec<u8>> = db
            .conn
            .query_row(
                "SELECT record FROM remote_episodes WHERE uuid=?",
                [&r.uuid],
                |row| row.get(0),
            )
            .optional()?;
        let mut merged = previous
            .map(|bytes| SyncUserEpisode::decode(bytes.as_slice()))
            .transpose()?
            .unwrap_or_else(|| r.clone());
        macro_rules! merge {($($value:ident : $time:ident),*)=>{$(if r.$value.is_some() && r.$time.as_ref().map(|t|t.value).unwrap_or(0)>=merged.$time.as_ref().map(|t|t.value).unwrap_or(0){merged.$value=r.$value.clone();merged.$time=r.$time.clone();})*}}
        merge!(playing_status:playing_status_modified,played_up_to:played_up_to_modified,starred:starred_modified,duration:duration_modified,is_deleted:is_deleted_modified);
        if merged.podcast_uuid.is_empty() {
            merged.podcast_uuid = r.podcast_uuid.clone();
        }
        db.conn.execute("INSERT INTO remote_episodes VALUES (?,?) ON CONFLICT(uuid) DO UPDATE SET record=excluded.record",params![r.uuid,merged.encode_to_vec()])?;
        return Ok(false);
    };
    let active: i64 = db
        .conn
        .query_row(
            "SELECT coalesce((SELECT value FROM rust_runtime WHERE key='active'),'0')",
            [],
            |r| r.get::<_, String>(0),
        )?
        .parse()
        .unwrap_or(0);
    let fields = [
        (
            "playing_status",
            "playing_status_modified",
            r.playing_status.as_ref().map(|v| v.value as i64),
            &r.playing_status_modified,
        ),
        (
            "position",
            "played_up_to_modified",
            r.played_up_to
                .as_ref()
                .map(|v| v.value.max(0).saturating_mul(1000)),
            &r.played_up_to_modified,
        ),
        (
            "duration",
            "duration_modified",
            r.duration.as_ref().map(|v| v.value.max(0)),
            &r.duration_modified,
        ),
        (
            "starred",
            "starred_modified",
            r.starred.as_ref().map(|v| v.value as i64),
            &r.starred_modified,
        ),
        (
            "archived",
            "archived_modified",
            r.is_deleted.as_ref().map(|v| v.value as i64),
            &r.is_deleted_modified,
        ),
    ];
    for (field, modified, value, time) in fields {
        if id == active && field != "starred" {
            continue;
        }
        if let Some(value) = value {
            let stamp = time.as_ref().map_or(0, |t| {
                if (1_000_000_000..100_000_000_000).contains(&t.value) {
                    t.value.saturating_mul(1000)
                } else {
                    t.value
                }
            });
            if stamp > 0 {
                db.conn.execute(
                    &format!(
                        "UPDATE episodes SET {field}=?,{modified}=? WHERE id=? AND {modified}<?"
                    ),
                    params![value, stamp, id, stamp],
                )?;
            } else {
                db.conn.execute(
                    &format!("UPDATE episodes SET {field}=? WHERE id=? AND needs_sync=0"),
                    params![value, id],
                )?;
            }
        }
    }
    db.conn
        .execute("DELETE FROM remote_episodes WHERE uuid=?", [&r.uuid])?;
    Ok(true)
}

fn playlist_record(db: &Db, p: &Value) -> Result<SyncUserPlaylist> {
    let mut r = SyncUserPlaylist {
        uuid: s(p, "uuid").into(),
        original_uuid: if s(p, "original_uuid").is_empty() {
            s(p, "uuid")
        } else {
            s(p, "original_uuid")
        }
        .into(),
        ..Default::default()
    };
    macro_rules! strs {($($f:ident),*)=>{$(r.$f=strv(s(p,stringify!($f)));)*}}
    macro_rules! ints {($($f:ident),*)=>{$(r.$f=i32v(n(p,stringify!($f)) as i32);)*}}
    macro_rules! bools {($($f:ident),*)=>{$(r.$f=boolv(n(p,stringify!($f))!=0);)*}}
    strs!(title, podcast_uuids, episode_uuids);
    ints!(
        audio_video,
        sort_position,
        sort_type,
        icon_id,
        filter_hours,
        longer_than,
        shorter_than
    );
    bools!(
        is_deleted,
        all_podcasts,
        not_downloaded,
        downloaded,
        downloading,
        finished,
        partially_played,
        unplayed,
        starred,
        manual,
        filter_duration,
        show_archived
    );
    if n(p, "manual") != 0 {
        let mut stmt = db.conn.prepare(
            "SELECT episode_uuid,podcast_uuid,title,audio_url,added_at,published_at FROM playlist_episodes WHERE playlist_id=? ORDER BY position",
        )?;
        let rows = stmt.query_map([n(p, "id")], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, i64>(4)?,
                row.get::<_, i64>(5)?,
            ))
        })?;
        for row in rows {
            let (ep_uuid, pod_uuid, title, url, added, published) = row?;
            r.episode_order.push(ep_uuid.clone());
            r.episodes.push(SyncPlaylistEpisode {
                episode: ep_uuid,
                podcast: pod_uuid,
                title: strv(&title),
                url: strv(&url),
                added: i64v(added),
                published: Some(Timestamp {
                    seconds: published,
                    nanos: 0,
                }),
                ..Default::default()
            });
        }
    }
    Ok(r)
}

fn apply_playlist(db: &Db, r: &SyncUserPlaylist) -> Result<()> {
    let id = if r.original_uuid.is_empty() {
        &r.uuid
    } else {
        &r.original_uuid
    };
    if id.is_empty() {
        return Ok(());
    }
    db.conn.execute(
        "INSERT OR IGNORE INTO playlists(uuid,original_uuid) VALUES (?,?)",
        params![id, id],
    )?;
    macro_rules! fields {($($f:ident),*)=>{$(if let Some(v)=&r.$f {db.conn.execute(concat!("UPDATE playlists SET ",stringify!($f),"=? WHERE uuid=?"),params![v.value,id])?;})*}}
    fields!(
        title,
        podcast_uuids,
        episode_uuids,
        audio_video,
        sort_position,
        sort_type,
        icon_id,
        filter_hours,
        longer_than,
        shorter_than,
        is_deleted,
        all_podcasts,
        not_downloaded,
        downloaded,
        downloading,
        finished,
        partially_played,
        unplayed,
        starred,
        manual,
        filter_duration,
        show_archived
    );
    if let Some(manual) = &r.manual {
        let pid: i64 = db
            .conn
            .query_row("SELECT id FROM playlists WHERE uuid=?", [id], |r| r.get(0))?;
        db.conn
            .execute("DELETE FROM playlist_episodes WHERE playlist_id=?", [pid])?;
        if manual.value {
            let order = if r.episode_order.is_empty() {
                r.episodes.iter().map(|e| e.episode.clone()).collect()
            } else {
                r.episode_order.clone()
            };
            for (index, uuid) in order.iter().enumerate() {
                let e = r.episodes.iter().find(|e| &e.episode == uuid);
                db.conn.execute("INSERT OR IGNORE INTO playlist_episodes(playlist_id,episode_uuid,podcast_uuid,title,audio_url,published_at,position,added_at) VALUES (?,?,?,?,?,?,?,?)",params![pid,uuid,e.map(|e|e.podcast.as_str()).unwrap_or(""),e.and_then(|e|e.title.as_ref()).map(|v|v.value.as_str()).unwrap_or(""),e.and_then(|e|e.url.as_ref()).map(|v|v.value.as_str()).unwrap_or(""),e.and_then(|e|e.published.as_ref()).map(|v|v.seconds).unwrap_or(0),index as i64,e.and_then(|e|e.added.as_ref()).map(|v|v.value).unwrap_or(0)])?;
            }
        }
    }
    Ok(())
}

struct Snapshot {
    stats: Option<stats::Snapshot>,
    episodes: Vec<Value>,
    podcasts: Vec<Value>,
    folders: Vec<Value>,
    playlists: Vec<Value>,
    deleted: Vec<Value>,
}
impl Snapshot {
    fn take(db: &Db) -> Result<Self> {
        Ok(Self{
        stats: stats::snapshot(db)?,
        episodes:db.rows("SELECT e.*,p.uuid AS podcast_uuid FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.needs_sync=1 AND p.feed_url LIKE 'pcast://%' ORDER BY e.id LIMIT 2000")?,
        podcasts:db.rows("SELECT * FROM podcasts WHERE needs_sync=1 AND feed_url LIKE 'pcast://%'")?,
        folders:db.rows("SELECT * FROM folders WHERE needs_sync=1")?,
        playlists:db.rows("SELECT * FROM playlists WHERE needs_sync=1")?,deleted:db.rows("SELECT * FROM podcast_tombstones")?})
    }
    fn records(&self, db: &Db) -> Result<Vec<Record>> {
        let mut records: Vec<Record> = self
            .episodes
            .iter()
            .map(|e| wrap(record::Record::Episode(episode_record(e))))
            .collect();
        if let Some(stats) = &self.stats {
            records.push(wrap(record::Record::Device(stats.record.clone())));
        }
        for p in &self.podcasts {
            records.push(wrap(record::Record::Podcast(SyncUserPodcast {
                uuid: s(p, "uuid").into(),
                is_deleted: boolv(false),
                subscribed: boolv(true),
                folder_uuid: strv(if s(p, "folder_uuid").is_empty() {
                    HOME_FOLDER
                } else {
                    s(p, "folder_uuid")
                }),
                sort_position: i32v(n(p, "sort_order") as i32),
                date_added: Some(Timestamp {
                    seconds: n(p, "added_at"),
                    nanos: 0,
                }),
            })));
        }
        for p in &self.deleted {
            records.push(wrap(record::Record::Podcast(SyncUserPodcast {
                uuid: s(p, "uuid").into(),
                is_deleted: boolv(true),
                subscribed: boolv(false),
                ..Default::default()
            })));
        }
        for p in &self.playlists {
            records.push(wrap(record::Record::Playlist(playlist_record(db, p)?)));
        }
        for f in &self.folders {
            records.push(wrap(record::Record::Folder(SyncUserFolder {
                folder_uuid: s(f, "uuid").into(),
                is_deleted: n(f, "is_deleted") != 0,
                name: s(f, "name").into(),
                color: n(f, "color") as i32,
                sort_position: n(f, "sort_position") as i32,
                podcasts_sort_type: n(f, "podcasts_sort_type") as i32,
                date_added: Some(Timestamp {
                    seconds: n(f, "added_at"),
                    nanos: 0,
                }),
            })));
        }
        Ok(records)
    }
    fn acknowledge(&self, db: &Db) -> Result<()> {
        if let Some(stats) = &self.stats {
            stats::acknowledge(db, stats)?;
        }
        for e in &self.episodes {
            db.conn.execute("UPDATE episodes SET needs_sync=0 WHERE uuid=? AND playing_status_modified=? AND played_up_to_modified=? AND starred_modified=? AND duration_modified=? AND archived_modified=?",params![s(e,"uuid"),n(e,"playing_status_modified"),n(e,"played_up_to_modified"),n(e,"starred_modified"),n(e,"duration_modified"),n(e,"archived_modified")])?;
        }
        for (table, rows) in [("folders", &self.folders), ("playlists", &self.playlists)] {
            for r in rows {
                db.conn.execute(
                    &format!("UPDATE {table} SET needs_sync=0 WHERE uuid=? AND local_modified=?"),
                    params![s(r, "uuid"), n(r, "local_modified")],
                )?;
            }
        }
        for p in &self.podcasts {
            db.conn.execute(
                "UPDATE podcasts SET needs_sync=0 WHERE uuid=? AND folder_uuid=? AND sort_order=?",
                params![s(p, "uuid"), s(p, "folder_uuid"), n(p, "sort_order")],
            )?;
        }
        for d in &self.deleted {
            db.conn.execute(
                "DELETE FROM podcast_tombstones WHERE uuid=? AND modified=?",
                params![s(d, "uuid"), n(d, "modified")],
            )?;
        }
        Ok(())
    }
}

pub fn run(db: &mut Db, api: &Api) -> Result<()> {
    if api.token.is_empty() {
        return Ok(());
    }
    if db.get("rust_import_complete")? != "1" {
        bootstrap(db, api)?;
    }
    // Deferred records are independent of the cursor, including failed old
    // episode fetches from a previous run.
    let snap = Snapshot::take(db)?;
    let req = SyncUpdateRequest {
        device_utc_time_ms: now(),
        last_modified: db.number("sync_cursor")?,
        country: String::new(),
        device_id: db.get("sync_device_id")?,
        records: snap.records(db)?,
        device_type: i32v(1),
    };
    let result: Result<Option<SyncUpdateResponse>> = api.post("user/sync/update", &req);
    let records_result=result.and_then(|response| {
        if let Some(response)=response {
            anyhow::ensure!(response.last_modified>0 && response.last_modified>=req.last_modified,"Sync returned an empty or regressing cursor; local edits retained");
            // Hydrate subscribed shows referenced by newly received records
            // before the cursor transaction; failures leave a durable record.
            for r in &response.records {
                if let Some(record::Record::Podcast(p)) = &r.record {
                    if p.uuid.is_empty() || p.uuid == USER_PODCAST {
                        continue;
                    }
                    let deleted = p.is_deleted.as_ref().is_some_and(|v| v.value)
                        || p.subscribed.as_ref().is_some_and(|v| !v.value);
                    let exists: bool = db.conn.query_row(
                        "SELECT EXISTS(SELECT 1 FROM podcasts WHERE uuid=? UNION SELECT 1 FROM podcast_tombstones WHERE uuid=?)",
                        params![p.uuid, p.uuid],
                        |r| r.get(0),
                    )?;
                    if !deleted
                        && !exists
                        && let Ok(payload) = api.podcast(&p.uuid)
                    {
                        let _ = library::import_cache(db, &p.uuid, &payload);
                    }
                }
            }
            db.conn.execute_batch("BEGIN IMMEDIATE")?;
            let applied=(||->Result<()> {
                ensure_account(db,api)?;
                // Acknowledge before merging so a newer remote timestamp cannot
                // prevent acknowledgement of the exact submitted local edit.
                snap.acknowledge(db)?;
                for r in &response.records {apply_record(db,r)?;}
                if response.last_modified>=req.last_modified {db.set("sync_cursor",&response.last_modified.to_string())?;}
                Ok(())
            })();
            finish_transaction(db,applied)?;
        } else {
            anyhow::ensure!(req.records.is_empty(),"Server did not acknowledge pending records; retrying later");
        }
        Ok(())
    });
    // The queue endpoint is independent; it can succeed during a record error.
    let queue_result = sync_queue(db, api);
    records_result?;
    queue_result?;
    retry_deferred(db, api)?;
    let summary = stats::refresh_summary(db, api);
    db.set(
        "stats_error",
        &summary.err().map(|e| format!("{e:#}")).unwrap_or_default(),
    )?;
    db.set("last_sync_time", &(now() / 1000).to_string())?;
    Ok(())
}

pub fn bootstrap(db: &mut Db, api: &Api) -> Result<()> {
    use crate::wire::{api::UserLastSyncAtResponse, bootstrap as full};
    // Capture the server watermark BEFORE fetching snapshots. Changes made by
    // the phone during import are then replayed by incremental sync afterwards.
    let time: UserLastSyncAtResponse = api
        .post("user/last_sync_at", &full::EmptyRequest {})?
        .ok_or_else(|| anyhow::anyhow!("Missing account sync watermark"))?;
    let watermark = if time.last_sync_at_ms > 0 {
        time.last_sync_at_ms
    } else {
        chrono::DateTime::parse_from_rfc3339(&time.last_sync_at)?.timestamp_millis()
    };
    anyhow::ensure!(watermark > 0, "Invalid account sync watermark");
    library::import_account(db, api)?;
    let podcasts = db.rows("SELECT uuid FROM podcasts WHERE feed_url LIKE 'pcast://%'")?;
    for p in podcasts {
        let uuid = s(&p, "uuid");
        let response: full::SyncEpisodesResponse = api
            .post(
                "user/podcast/episodes",
                &full::UuidRequest {
                    m: "mobile".into(),
                    uuid: uuid.into(),
                    ..Default::default()
                },
            )?
            .ok_or_else(|| anyhow::anyhow!("Missing episode snapshot"))?;
        db.conn.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<()> {
            ensure_account(db, api)?;
            for e in response.episodes {
                let mut r = SyncUserEpisode {
                    uuid: e.uuid,
                    podcast_uuid: uuid.into(),
                    playing_status: i32v(e.playing_status),
                    playing_status_modified: i64v(watermark),
                    played_up_to: i64v(e.played_up_to.max(0) as i64),
                    played_up_to_modified: i64v(watermark),
                    starred: boolv(e.starred),
                    starred_modified: i64v(watermark),
                    duration: i64v(e.duration.max(0) as i64),
                    duration_modified: i64v(watermark),
                    is_deleted: boolv(e.is_deleted),
                    is_deleted_modified: i64v(watermark),
                };
                // Snapshot rows do not carry per-field modification dates.
                // Preserve dirty local fields, including edits made mid-import.
                let local:Option<(i64,i64,i64,i64,i64)>=db.conn.query_row("SELECT playing_status_modified,played_up_to_modified,starred_modified,duration_modified,archived_modified FROM episodes WHERE uuid=? AND needs_sync=1",[&r.uuid],|row|Ok((row.get(0)?,row.get(1)?,row.get(2)?,row.get(3)?,row.get(4)?))).optional()?;
                if let Some((status, position, star, duration, archive)) = local {
                    if status > 0 {
                        r.playing_status = None;
                        r.playing_status_modified = None;
                    }
                    if position > 0 {
                        r.played_up_to = None;
                        r.played_up_to_modified = None;
                    }
                    if star > 0 {
                        r.starred = None;
                        r.starred_modified = None;
                    }
                    if duration > 0 {
                        r.duration = None;
                        r.duration_modified = None;
                    }
                    if archive > 0 {
                        r.is_deleted = None;
                        r.is_deleted_modified = None;
                    }
                }
                apply_episode(db, &r)?;
            }
            Ok(())
        })();
        finish_transaction(db, result)?;
    }
    let response: full::UserPlaylistListResponse = api
        .post(
            "user/playlist/list",
            &full::UserPlaylistListRequest {
                m: "mobile".into(),
                ..Default::default()
            },
        )?
        .ok_or_else(|| anyhow::anyhow!("Missing playlist snapshot"))?;
    db.conn.execute_batch("BEGIN IMMEDIATE")?;
    let result = (|| -> Result<()> {
        ensure_account(db, api)?;
        for p in response.playlists {
            let r = SyncUserPlaylist {
                uuid: p.uuid,
                original_uuid: p.original_uuid,
                title: strv(&p.title),
                is_deleted: p.is_deleted,
                audio_video: p.audio_video,
                not_downloaded: p.not_downloaded,
                downloaded: p.downloaded,
                downloading: p.downloading,
                finished: p.finished,
                partially_played: p.partially_played,
                unplayed: p.unplayed,
                starred: p.starred,
                manual: p.manual,
                sort_position: p.sort_position,
                sort_type: p.sort_type,
                icon_id: p.icon_id,
                all_podcasts: p.all_podcasts,
                filter_hours: p.filter_hours,
                podcast_uuids: strv(&p.podcast_uuids),
                episode_uuids: strv(&p.episode_uuids),
                filter_duration: p.filter_duration,
                longer_than: p.longer_than,
                shorter_than: p.shorter_than,
                episode_order: p.episode_order,
                episodes: p.episodes,
                show_archived: p.show_archived,
            };
            apply_record(db, &wrap(record::Record::Playlist(r)))?;
        }
        db.set("sync_cursor", &watermark.to_string())?;
        db.set("rust_import_complete", "1")?;
        Ok(())
    })();
    finish_transaction(db, result)
}
fn ensure_account(db: &Db, api: &Api) -> Result<()> {
    anyhow::ensure!(
        db.get("account_token")? == api.token,
        "Account changed during sync"
    );
    Ok(())
}
fn finish_transaction(db: &Db, result: Result<()>) -> Result<()> {
    match result {
        Ok(()) => {
            db.conn.execute_batch("COMMIT")?;
            Ok(())
        }
        Err(e) => {
            let _ = db.conn.execute_batch("ROLLBACK");
            Err(e)
        }
    }
}
pub fn apply_record(db: &Db, r: &Record) -> Result<()> {
    match &r.record {
        Some(record::Record::Device(d)) => {
            stats::apply_device(db, d)?;
            let own_id = db.get("sync_device_id")?;
            if let (Some(dev_id), Some(secs)) = (&d.device_id, &d.time_listened)
                && !dev_id.value.is_empty()
                && dev_id.value != own_id
                && secs.value > db.number("stats_other_seconds").unwrap_or(0)
            {
                db.set("stats_other_seconds", &secs.value.to_string())?;
                if db.number("stats_summary_at").unwrap_or(0) == 0 {
                    db.set("stats_summary_at", &(now() / 1000).to_string())?;
                }
            }
        }
        Some(record::Record::Episode(e)) => {
            apply_episode(db, e)?;
        }
        Some(record::Record::Playlist(p)) => {
            let uuid = if p.original_uuid.is_empty() {
                &p.uuid
            } else {
                &p.original_uuid
            };
            if !dirty(db, "playlists", uuid)? {
                apply_playlist(db, p)?;
            }
        }
        Some(record::Record::Folder(f)) => {
            if !dirty(db, "folders", &f.folder_uuid)? {
                db.conn.execute("INSERT INTO folders(uuid,name,color,sort_position,podcasts_sort_type,added_at,is_deleted) VALUES (?,?,?,?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET name=excluded.name,color=excluded.color,sort_position=excluded.sort_position,podcasts_sort_type=excluded.podcasts_sort_type,is_deleted=excluded.is_deleted",params![f.folder_uuid,f.name,f.color,f.sort_position,f.podcasts_sort_type,f.date_added.as_ref().map(|t|t.seconds).unwrap_or(0),f.is_deleted])?;
            }
        }
        Some(record::Record::Podcast(p)) => {
            if dirty(db, "podcasts", &p.uuid)? {
                return Ok(());
            }
            let tombstone: bool = db.conn.query_row(
                "SELECT EXISTS(SELECT 1 FROM podcast_tombstones WHERE uuid=?)",
                [&p.uuid],
                |r| r.get(0),
            )?;
            if tombstone {
                return Ok(());
            }
            if p.is_deleted.as_ref().is_some_and(|v| v.value)
                || p.subscribed.as_ref().is_some_and(|v| !v.value)
            {
                db.conn
                    .execute("DELETE FROM podcasts WHERE uuid=?", [&p.uuid])?;
            } else {
                if p.subscribed.as_ref().is_some_and(|v| v.value) {
                    db.conn
                        .execute("UPDATE podcasts SET subscribed=1 WHERE uuid=?", [&p.uuid])?;
                }
                if let Some(f) = &p.folder_uuid {
                    db.conn.execute(
                        "UPDATE podcasts SET folder_uuid=? WHERE uuid=?",
                        params![f.value, p.uuid],
                    )?;
                }
                if let Some(v) = &p.sort_position {
                    db.conn.execute(
                        "UPDATE podcasts SET sort_order=? WHERE uuid=?",
                        params![v.value, p.uuid],
                    )?;
                }
            }
        }
        None => {}
    }
    Ok(())
}
fn dirty(db: &Db, table: &str, uuid: &str) -> Result<bool> {
    Ok(db
        .conn
        .query_row(
            &format!("SELECT needs_sync!=0 FROM {table} WHERE uuid=?"),
            [uuid],
            |r| r.get(0),
        )
        .optional()?
        .unwrap_or(false))
}

fn retry_deferred(db: &mut Db, api: &Api) -> Result<()> {
    let pending = db
        .conn
        .prepare("SELECT record FROM remote_episodes ORDER BY rowid LIMIT 5")?
        .query_map([], |r| r.get::<_, Vec<u8>>(0))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let started = std::time::Instant::now();
    for bytes in pending {
        if started.elapsed() >= std::time::Duration::from_secs(10) {
            break;
        }
        let r = SyncUserEpisode::decode(bytes.as_slice())?;
        if r.podcast_uuid.is_empty() || r.podcast_uuid == USER_PODCAST {
            db.conn
                .execute("DELETE FROM remote_episodes WHERE uuid=?", [&r.uuid])?;
            continue;
        }
        if apply_episode(db, &r)? {
            continue;
        }
        let exists: bool = db.conn.query_row(
            "SELECT EXISTS(SELECT 1 FROM podcasts WHERE uuid=?)",
            [&r.podcast_uuid],
            |row| row.get(0),
        )?;
        if exists
            && let Ok(payload) = api.historical(&r.podcast_uuid, &r.uuid)
            && library::import_cache_with_subscription(db, &r.podcast_uuid, &payload, false).is_ok()
        {
            apply_episode(db, &r)?;
        }
        // Rotate failed historical entries so one unavailable episode cannot
        // permanently starve all later records.
        db.conn.execute("UPDATE remote_episodes SET rowid=(SELECT coalesce(max(rowid),0)+1 FROM remote_episodes) WHERE uuid=?",[&r.uuid])?;
    }
    Ok(())
}

fn queue_change(db: &Db, r: &Value) -> Result<up_next_changes::Change> {
    let mut c = up_next_changes::Change {
        uuid: s(r, "episode_uuid").into(),
        action: n(r, "action") as i32,
        modified: n(r, "modified"),
        ..Default::default()
    };
    let ids: Vec<&str> = if c.action == 5 {
        s(r, "episode_uuids")
            .split(',')
            .filter(|s| !s.is_empty())
            .collect()
    } else {
        vec![s(r, "episode_uuid")]
    };
    for uuid in ids {
        let id: Option<i64> = db
            .conn
            .query_row("SELECT id FROM episodes WHERE uuid=?", [uuid], |r| r.get(0))
            .optional()?;
        if let Some(id) = id {
            let e = db.episode(id)?;
            let published = Some(Timestamp {
                seconds: e.published_at,
                nanos: 0,
            });
            if c.action == 5 {
                c.episodes.push(UpNextEpisodeRequest {
                    uuid: e.uuid,
                    title: e.title,
                    url: e.audio_url,
                    podcast: e.podcast_uuid,
                    published,
                });
            } else {
                c.title = e.title;
                c.url = e.audio_url;
                c.podcast = e.podcast_uuid;
                c.published = published;
            }
        }
    }
    Ok(c)
}
pub fn sync_queue(db: &mut Db, api: &Api) -> Result<()> {
    let cursor = db.number("upnext_cursor")?;
    let first = db.get("rust_queue_initialized")? != "1";
    let actions = db.rows("SELECT * FROM queue_actions ORDER BY id")?;
    let watermark = actions.last().map(|r| n(r, "id")).unwrap_or(0);
    let changes = if first {
        Vec::new()
    } else {
        actions
            .iter()
            .map(|r| queue_change(db, r))
            .collect::<Result<Vec<_>>>()?
    };
    let req = UpNextSyncRequest {
        device_time: now(),
        version: "2".into(),
        model: "Linux".into(),
        device_id: db.get("sync_device_id")?,
        up_next: Some(UpNextChanges {
            server_modified: if first { 0 } else { cursor },
            changes,
            order: vec![],
        }),
        ..Default::default()
    };
    let response: Option<UpNextResponse> = api.post("up_next/sync", &req)?;
    if let Some(response) = response {
        anyhow::ensure!(
            response.server_modified > 0 && response.server_modified >= cursor,
            "Up Next returned an empty or regressing cursor; local queue retained"
        );
        // The queue supplies enough metadata to keep even historical episodes
        // playable, so remote entries are never dropped for being unknown.
        for e in &response.episodes {
            if e.podcast.is_empty() || e.podcast == USER_PODCAST {
                continue;
            }
            let exists: bool = db.conn.query_row(
                "SELECT EXISTS(SELECT 1 FROM podcasts WHERE uuid=?)",
                [&e.podcast],
                |r| r.get(0),
            )?;
            if !exists && let Ok(payload) = api.podcast(&e.podcast) {
                let _ = library::import_cache_with_subscription(db, &e.podcast, &payload, false);
            }
            let pid: Option<i64> = db
                .conn
                .query_row("SELECT id FROM podcasts WHERE uuid=?", [&e.podcast], |r| {
                    r.get(0)
                })
                .optional()?;
            if let Some(pid) = pid {
                db.conn.execute("INSERT OR IGNORE INTO episodes(uuid,podcast_id,title,audio_url,published_at,added_at) VALUES (?,?,?,?,?,?)",params![e.uuid,pid,e.title,e.url,e.published.as_ref().map(|v|v.seconds).unwrap_or(0),now()/1000])?;
            }
        }
        db.conn.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| -> Result<()> {
            ensure_account(db, api)?;
            for episode in &response.episode_sync {
                if let Some(position) = &episode.played_up_to {
                    let pos_ms = position.value.max(0).saturating_mul(1000);
                    db.conn.execute(
                        "UPDATE episodes SET position=?,playing_status=CASE WHEN ?>0 AND playing_status=0 THEN 1 ELSE playing_status END WHERE uuid=? AND needs_sync=0 AND id!=coalesce((SELECT value FROM rust_runtime WHERE key='active'),0)",
                        params![pos_ms, pos_ms, episode.uuid],
                    )?;
                }
                if let Some(duration) = &episode.duration {
                    db.conn.execute(
                        "UPDATE episodes SET duration=? WHERE uuid=? AND needs_sync=0 AND id!=coalesce((SELECT value FROM rust_runtime WHERE key='active'),0)",
                        params![duration.value.max(0), episode.uuid],
                    )?;
                }
            }
            let current_watermark: i64 =
                db.conn
                    .query_row("SELECT coalesce(max(id),0) FROM queue_actions", [], |r| {
                        r.get(0)
                    })?;
            // New queue edits retain their local order until replayed next run.
            if current_watermark == watermark {
                let before = db.queue()?;
                let mut ids = Vec::new();
                let mut extras = Vec::new();
                let mut seen = HashSet::new();
                for e in &response.episodes {
                    if let Some(id) = db
                        .conn
                        .query_row("SELECT id FROM episodes WHERE uuid=?", [&e.uuid], |r| {
                            r.get::<_, i64>(0)
                        })
                        .optional()?
                        && seen.insert(id)
                    {
                        ids.push(id)
                    }
                }
                if first {
                    for id in before {
                        if seen.insert(id) {
                            ids.push(id);
                            extras.push(id)
                        }
                    }
                }
                db.conn.execute("DELETE FROM queue", [])?;
                for (index, id) in ids.iter().enumerate() {
                    db.conn.execute(
                        "INSERT INTO queue(episode_id,position) VALUES (?,?)",
                        params![id, index as i64],
                    )?;
                }
                db.conn
                    .execute("DELETE FROM queue_actions WHERE id<=?", [watermark])?;
                for id in extras {
                    // Append only local additions. Replacing an unchanged
                    // remote list could overwrite phone edits made after pull.
                    let uuid = db.episode(id)?.uuid;
                    db.conn.execute(
                        "INSERT INTO queue_actions(action,episode_uuid,modified) VALUES (3,?,?)",
                        params![uuid, now()],
                    )?;
                }
                db.set("upnext_cursor", &response.server_modified.to_string())?;
                db.set("rust_queue_initialized", "1")?;
            } else if !first {
                db.conn
                    .execute("DELETE FROM queue_actions WHERE id<=?", [watermark])?;
            }
            Ok(())
        })();
        finish_transaction(db, result)?;
    } else {
        anyhow::ensure!(
            !first && req.up_next.as_ref().is_none_or(|q| q.changes.is_empty()),
            "Server did not acknowledge Up Next; retrying later"
        );
    }
    Ok(())
}
