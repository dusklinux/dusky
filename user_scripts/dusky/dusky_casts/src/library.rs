use crate::{
    api::Api,
    db::{Db, n, now, s},
};
use anyhow::{Context, Result, bail};
use rusqlite::{OptionalExtension, params};
use serde_json::Value;
use std::{
    collections::VecDeque,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Mutex,
};

const MAX_FETCH_WORKERS: usize = 4;

fn fetch_parallel<T, R, F>(items: Vec<T>, f: F) -> Vec<(T, R)>
where
    T: Send,
    R: Send,
    F: Fn(&T) -> R + Sync,
{
    let workers = items.len().min(MAX_FETCH_WORKERS);
    if workers <= 1 {
        return items
            .into_iter()
            .map(|item| {
                let res = f(&item);
                (item, res)
            })
            .collect();
    }
    let queue: Mutex<VecDeque<(usize, T)>> = Mutex::new(items.into_iter().enumerate().collect());
    let out: Mutex<Vec<(usize, T, R)>> = Mutex::new(Vec::new());
    std::thread::scope(|scope| {
        for _ in 0..workers {
            scope.spawn(|| {
                loop {
                    let next = queue.lock().unwrap_or_else(|e| e.into_inner()).pop_front();
                    let Some((idx, item)) = next else { break };
                    let res = f(&item);
                    out.lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .push((idx, item, res));
                }
            });
        }
    });
    let mut results = out.into_inner().unwrap_or_else(|e| e.into_inner());
    results.sort_unstable_by_key(|(idx, _, _)| *idx);
    results
        .into_iter()
        .map(|(_, item, res)| (item, res))
        .collect()
}

pub fn import_cache(db: &mut Db, podcast_id: &str, payload: &Value) -> Result<()> {
    import_cache_with_subscription(db, podcast_id, payload, true)
}
pub fn import_cache_with_subscription(
    db: &mut Db,
    podcast_id: &str,
    payload: &Value,
    subscribed: bool,
) -> Result<()> {
    let p = payload.get("podcast").unwrap_or(payload);
    let id = p.get("uuid").and_then(Value::as_str).unwrap_or(podcast_id);
    if id.is_empty() {
        bail!("Podcast has no UUID")
    }
    let tx = db.conn.transaction()?;
    let deleted: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM podcast_tombstones WHERE uuid=?)",
        [id],
        |r| r.get(0),
    )?;
    if deleted {
        return Ok(());
    }
    tx.execute("INSERT INTO podcasts(uuid,title,author,description,feed_url,added_at,artwork_url,subscribed) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET title=CASE WHEN excluded.title!='' THEN excluded.title ELSE podcasts.title END,author=excluded.author,description=excluded.description,artwork_url=excluded.artwork_url,last_refreshed=excluded.added_at",params![id,s(p,"title"),s(p,"author"),s(p,"description"),format!("pcast://{id}"),now()/1000,format!("https://static.pocketcasts.com/discover/images/420/{id}.jpg"),subscribed])?;
    let local_id: i64 = tx.query_row("SELECT id FROM podcasts WHERE uuid=?", [id], |r| r.get(0))?;
    if let Some(episodes) = p["episodes"].as_array() {
        let mut stmt=tx.prepare("INSERT INTO episodes(uuid,podcast_id,title,description,audio_url,file_type,published_at,duration,size,added_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET title=excluded.title,description=excluded.description,audio_url=excluded.audio_url,file_type=excluded.file_type,published_at=excluded.published_at,size=excluded.size,duration=CASE WHEN episodes.duration_modified=0 THEN excluded.duration ELSE episodes.duration END")?;
        for e in episodes {
            if s(e, "uuid").is_empty() || s(e, "url").is_empty() {
                continue;
            }
            stmt.execute(params![
                s(e, "uuid"),
                local_id,
                s(e, "title"),
                s(e, "description"),
                s(e, "url"),
                s(e, "file_type"),
                epoch(&e["published"]),
                number(&e["duration"]),
                number(&e["file_size"]),
                now() / 1000
            ])?;
        }
    }
    tx.commit()?;
    Ok(())
}
fn number(v: &Value) -> i64 {
    v.as_i64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .unwrap_or(0)
}
fn epoch(v: &Value) -> i64 {
    if let Some(text) = v.as_str()
        && let Ok(d) = chrono::DateTime::parse_from_rfc3339(text)
    {
        return d.timestamp();
    }
    let n = number(v);
    if n > 10_000_000_000 { n / 1000 } else { n }
}
pub fn import_account(db: &mut Db, api: &Api) -> Result<usize> {
    use crate::wire::api::*;
    let list: UserPodcastListResponse = api
        .post(
            "user/podcast/list",
            &UserPodcastListRequest {
                v: env!("CARGO_PKG_VERSION").into(),
                m: "mobile".into(),
            },
        )?
        .context("No subscription list returned")?;
    let total = list.podcasts.len();
    let mut to_fetch = Vec::with_capacity(total);
    for p in list.podcasts {
        let deleted: bool = db.conn.query_row(
            "SELECT EXISTS(SELECT 1 FROM podcast_tombstones WHERE uuid=?)",
            [&p.uuid],
            |r| r.get(0),
        )?;
        if !deleted {
            to_fetch.push(p);
        }
    }
    // Fetch podcast payloads concurrently, then commit sequentially on SQLite.
    // Any failure remains retryable on restart without discarding fetched shows.
    let fetched = fetch_parallel(to_fetch, |p| {
        api.podcast(&p.uuid)
            .with_context(|| format!("Importing {}", p.title))
    });
    let mut first_error = None;
    for (p, result) in fetched {
        match result {
            Ok(data) => {
                import_cache(db, &p.uuid, &data)?;
                db.conn.execute(
                    "UPDATE podcasts SET folder_uuid=?,sort_order=?,subscribed=1 WHERE uuid=? AND needs_sync=0",
                    params![
                        p.folder_uuid.map(|v| v.value).unwrap_or_default(),
                        p.sort_position.map(|v| v.value).unwrap_or(0),
                        p.uuid
                    ],
                )?;
            }
            Err(e) => {
                if first_error.is_none() {
                    first_error = Some(e);
                }
            }
        }
    }
    if let Some(err) = first_error {
        return Err(err);
    }
    for f in list.folders {
        db.conn.execute("INSERT INTO folders(uuid,name,color,sort_position,podcasts_sort_type,added_at) VALUES (?,?,?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET name=excluded.name,color=excluded.color,sort_position=excluded.sort_position,podcasts_sort_type=excluded.podcasts_sort_type WHERE folders.needs_sync=0",params![f.folder_uuid,f.name,f.color,f.sort_position,f.podcasts_sort_type,f.date_added.map(|v|v.seconds).unwrap_or(0)])?;
    }
    Ok(total)
}

struct FetchedFeed {
    url: String,
    etag: String,
    modified: String,
    feed: feed_rs::model::Feed,
}

enum RefreshTarget {
    Cache {
        uuid: String,
        title: String,
    },
    Feed {
        title: String,
        feed_url: String,
        etag: String,
        modified: String,
    },
}

enum RefreshPayload {
    Cache(Value),
    Feed(Option<Box<FetchedFeed>>),
}

fn fetch_feed(api: &Api, url: &str, etag: &str, modified: &str) -> Result<Option<FetchedFeed>> {
    let parsed_url = reqwest::Url::parse(url).context("Enter an RSS or Atom feed URL")?;
    if !matches!(parsed_url.scheme(), "http" | "https") {
        bail!("Use an HTTP or HTTPS feed URL")
    }
    let mut req = api.http.get(parsed_url.clone());
    if !etag.is_empty() {
        req = req.header("If-None-Match", etag);
    }
    if !modified.is_empty() {
        req = req.header("If-Modified-Since", modified);
    }
    let response = req.send()?;
    if response.status().as_u16() == 304 {
        return Ok(None);
    }
    let response = response.error_for_status()?;
    let next_etag = response
        .headers()
        .get("etag")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let next_modified = response
        .headers()
        .get("last-modified")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let final_url = response.url().clone();
    let mut bytes = Vec::new();
    response
        .take(32 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > 32 * 1024 * 1024 {
        bail!("Feed exceeds 32 MiB")
    }
    let feed = feed_rs::parser::Builder::new()
        .base_uri(Some(final_url.as_str()))
        .build()
        .parse(bytes.as_slice())?;
    Ok(Some(FetchedFeed {
        url: parsed_url.to_string(),
        etag: next_etag,
        modified: next_modified,
        feed,
    }))
}

fn commit_feed(db: &mut Db, fetched: FetchedFeed) -> Result<()> {
    let FetchedFeed {
        url,
        etag,
        modified,
        feed,
    } = fetched;
    let uuid = uuid::Uuid::new_v5(&uuid::Uuid::NAMESPACE_URL, url.as_bytes()).to_string();
    let tx = db.conn.transaction()?;
    tx.execute("INSERT INTO podcasts(uuid,title,feed_url,author,description,artwork_url,added_at,http_etag,http_modified) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(feed_url) DO UPDATE SET title=excluded.title,author=excluded.author,description=excluded.description,http_etag=excluded.http_etag,http_modified=excluded.http_modified,last_refreshed=excluded.added_at",params![uuid,feed.title.map(|t|t.content).unwrap_or_else(||url.clone()),url,feed.authors.iter().map(|p|p.name.as_deref().unwrap_or("")).collect::<Vec<_>>().join(", "),feed.description.map(|t|t.content).unwrap_or_default(),feed.logo.or(feed.icon).map(|i|i.uri).unwrap_or_default(),now()/1000,etag,modified])?;
    let pid: i64 = tx.query_row("SELECT id FROM podcasts WHERE feed_url=?", [&url], |r| {
        r.get(0)
    })?;
    for e in feed.entries {
        let media = e.media.iter().flat_map(|m| m.content.iter()).find(|m| {
            m.url.is_some()
                && m.content_type
                    .as_ref()
                    .is_none_or(|mime| !mime.to_string().starts_with("image/"))
        });
        let Some(media) = media else { continue };
        let audio = media.url.as_ref().unwrap().to_string();
        let identity = if e.id.is_empty() {
            audio.as_str()
        } else {
            &e.id
        };
        // Local RSS identities never masquerade as Pocket Casts server UUIDs.
        let generated =
            uuid::Uuid::new_v5(&uuid::Uuid::parse_str(&uuid)?, identity.as_bytes()).to_string();
        // Preserve existing enclosure identities, including the Python client's
        // SHA-1 keys. A parser change must not duplicate downloaded/progress rows.
        let eid = tx
            .query_row(
                "SELECT uuid FROM episodes WHERE podcast_id=? AND audio_url=?",
                params![pid, audio],
                |r| r.get::<_, String>(0),
            )
            .optional()?
            .unwrap_or(generated);
        tx.execute("INSERT INTO episodes(uuid,podcast_id,title,description,audio_url,published_at,duration,added_at,file_type) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET title=excluded.title,description=excluded.description,audio_url=excluded.audio_url,file_type=excluded.file_type",params![eid,pid,e.title.map(|t|t.content).unwrap_or_else(||"Untitled".into()),e.content.and_then(|c|c.body).or(e.summary.map(|s|s.content)).unwrap_or_default(),audio,e.published.map(|d|d.timestamp()).unwrap_or(0),media.duration.map(|d|d.as_secs() as i64).unwrap_or(0),now()/1000,media.content_type.as_ref().map(|m|m.to_string()).unwrap_or_default()])?;
    }
    tx.commit()?;
    Ok(())
}

pub fn refresh(db: &mut Db, api: &Api) -> Result<usize> {
    let podcasts = db.rows(
        "SELECT uuid,title,feed_url,http_etag,http_modified FROM podcasts WHERE subscribed=1",
    )?;
    let targets: Vec<RefreshTarget> = podcasts
        .into_iter()
        .map(|p| {
            let feed_url = s(&p, "feed_url").to_owned();
            let title = s(&p, "title").to_owned();
            if feed_url.starts_with("pcast://") {
                RefreshTarget::Cache {
                    uuid: s(&p, "uuid").to_owned(),
                    title,
                }
            } else {
                RefreshTarget::Feed {
                    title,
                    feed_url,
                    etag: s(&p, "http_etag").to_owned(),
                    modified: s(&p, "http_modified").to_owned(),
                }
            }
        })
        .collect();
    let fetched = fetch_parallel(targets, |target| match target {
        RefreshTarget::Cache { uuid, .. } => api.podcast(uuid).map(RefreshPayload::Cache),
        RefreshTarget::Feed {
            feed_url,
            etag,
            modified,
            ..
        } => fetch_feed(api, feed_url, etag, modified)
            .map(|feed| RefreshPayload::Feed(feed.map(Box::new))),
    });
    let mut count = 0;
    let mut errors = Vec::new();
    for (target, result) in fetched {
        let title = match &target {
            RefreshTarget::Cache { title, .. } | RefreshTarget::Feed { title, .. } => title.clone(),
        };
        let committed = result.and_then(|payload| match (target, payload) {
            (RefreshTarget::Cache { uuid, .. }, RefreshPayload::Cache(v)) => {
                import_cache(db, &uuid, &v)
            }
            (RefreshTarget::Feed { .. }, RefreshPayload::Feed(Some(feed))) => {
                commit_feed(db, *feed)
            }
            (RefreshTarget::Feed { .. }, RefreshPayload::Feed(None)) => Ok(()),
            _ => unreachable!(),
        });
        match committed {
            Ok(()) => count += 1,
            Err(e) => errors.push(format!("{title}: {e}")),
        }
    }
    if !errors.is_empty() {
        bail!("Refreshed {count}; {}", errors.join("; "))
    }
    Ok(count)
}

pub fn refresh_one(db: &mut Db, api: &Api, podcast_id: i64) -> Result<()> {
    let (uuid, feed_url, etag, modified): (String, String, String, String) = db.conn.query_row(
        "SELECT uuid,feed_url,http_etag,http_modified FROM podcasts WHERE id=?",
        [podcast_id],
        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
    )?;
    if feed_url.starts_with("pcast://") {
        let data = api.podcast(&uuid)?;
        import_cache(db, &uuid, &data)
    } else if let Some(feed) = fetch_feed(api, &feed_url, &etag, &modified)? {
        commit_feed(db, feed)
    } else {
        Ok(())
    }
}

pub fn add_feed(db: &mut Db, api: &Api, url: &str) -> Result<()> {
    let parsed_url = reqwest::Url::parse(url).context("Enter an RSS or Atom feed URL")?;
    if !matches!(parsed_url.scheme(), "http" | "https") {
        bail!("Use an HTTP or HTTPS feed URL")
    }
    let (etag, modified) = db
        .conn
        .query_row(
            "SELECT http_etag,http_modified FROM podcasts WHERE feed_url=?",
            [parsed_url.as_str()],
            |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
        )
        .optional()?
        .unwrap_or_default();
    if let Some(fetched) = fetch_feed(api, parsed_url.as_str(), &etag, &modified)? {
        commit_feed(db, fetched)?;
    }
    Ok(())
}

#[derive(Default)]
pub struct DownloadControl {
    pub cancel: std::sync::atomic::AtomicBool,
    pub bytes: std::sync::atomic::AtomicU64,
    pub total: std::sync::atomic::AtomicU64,
}
pub fn download(db: &Db, api: &Api, id: i64) -> Result<()> {
    download_with(db, api, id, &DownloadControl::default())
}
pub fn download_with(db: &Db, api: &Api, id: i64, control: &DownloadControl) -> Result<()> {
    let root = std::env::var_os("XDG_DOWNLOAD_DIR")
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| crate::db::xdg("HOME", "Downloads").join("Downloads"));
    let root = root.join("Dusky Casts");
    download_to(db, api, id, control, &root)
}
pub fn download_to(
    db: &Db,
    api: &Api,
    id: i64,
    control: &DownloadControl,
    root: &Path,
) -> Result<()> {
    use std::sync::atomic::Ordering::Relaxed;
    let ep = db.episode(id)?;
    anyhow::ensure!(!ep.is_local(), "Local files already live on disk");
    std::fs::create_dir_all(root)?;
    let mut temp = tempfile::Builder::new()
        .prefix(".dusky_casts-")
        .suffix(".part")
        .tempfile_in(root)?;
    let mut response = api
        .http
        .get(&ep.audio_url)
        .timeout(std::time::Duration::from_secs(6 * 3600))
        .send()?
        .error_for_status()?;
    let expected = response.content_length();
    control.total.store(expected.unwrap_or(0), Relaxed);
    let mut size = 0;
    let mut buffer = [0u8; 64 * 1024];
    loop {
        anyhow::ensure!(!control.cancel.load(Relaxed), "Download cancelled");
        let read = response.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        temp.write_all(&buffer[..read])?;
        size += read as u64;
        control.bytes.store(size, Relaxed);
    }
    if size == 0 || expected.is_some_and(|n| n != size) {
        bail!("Incomplete download")
    }
    temp.flush()?;
    let target = root.join(format!("{}.media", ep.uuid));
    temp.persist(&target)?;
    db.conn.execute(
        "UPDATE episodes SET downloaded_path=?,download_state=3 WHERE id=?",
        params![target.to_string_lossy(), id],
    )?;
    Ok(())
}

pub fn unsubscribe(db: &mut Db, id: i64) -> Result<()> {
    let tx = db.conn.transaction()?;
    let (uuid, feed): (String, String) =
        tx.query_row("SELECT uuid,feed_url FROM podcasts WHERE id=?", [id], |r| {
            Ok((r.get(0)?, r.get(1)?))
        })?;
    if feed.starts_with("pcast://") {
        tx.execute("INSERT INTO podcast_tombstones VALUES (?,?) ON CONFLICT(uuid) DO UPDATE SET modified=excluded.modified",params![uuid,now()])?;
    }
    let queued = tx
        .prepare(
            "SELECT e.uuid FROM queue q JOIN episodes e ON q.episode_id=e.id WHERE e.podcast_id=?",
        )?
        .query_map([id], |r| r.get::<_, String>(0))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    for episode in queued {
        tx.execute(
            "INSERT INTO queue_actions(action,episode_uuid,modified) VALUES (4,?,?)",
            params![episode, now()],
        )?;
    }
    let downloaded: Vec<String> = tx
        .prepare("SELECT downloaded_path FROM episodes WHERE podcast_id=? AND downloaded_path!=''")?
        .query_map([id], |r| r.get::<_, String>(0))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    tx.execute("UPDATE playlists SET needs_sync=1,local_modified=max(?,local_modified+1) WHERE id IN (SELECT pe.playlist_id FROM playlist_episodes pe JOIN episodes e ON e.uuid=pe.episode_uuid WHERE e.podcast_id=?)",params![now(),id])?;
    tx.execute("DELETE FROM playlist_episodes WHERE episode_uuid IN (SELECT uuid FROM episodes WHERE podcast_id=?)",[id])?;
    tx.execute("DELETE FROM podcasts WHERE id=?", [id])?;
    tx.commit()?;
    for path in downloaded {
        let _ = std::fs::remove_file(path);
    }
    Ok(())
}

pub fn search(api: &Api, query: &str) -> Result<Vec<Value>> {
    let url = reqwest::Url::parse_with_params(
        "https://itunes.apple.com/search",
        &[
            ("term", query),
            ("media", "podcast"),
            ("entity", "podcast"),
            ("limit", "30"),
        ],
    )?;
    let v = api.json_get(url.as_str(), false)?;
    Ok(v["results"].as_array().cloned().unwrap_or_default())
}

pub fn subscribe_search(db: &mut Db, api: &Api, result: &Value) -> Result<()> {
    if !api.token.is_empty() {
        let base = std::env::var("DUSKY_CASTS_REFRESH_BASE")
            .unwrap_or_else(|_| "https://refresh.pocketcasts.com/".into());
        let v:Value=api.http.post(format!("{}/podcasts/show",base.trim_end_matches('/'))).json(&serde_json::json!({"id":n(result,"collectionId"),"dt":"1","v":"1.7","m":"Linux","av":"2","l":"en","c":"US"})).send()?.error_for_status()?.json()?;
        let id = v
            .pointer("/result/podcast/uuid")
            .and_then(Value::as_str)
            .context("Directory did not return a podcast UUID")?;
        let payload = api.podcast(id)?;
        db.conn
            .execute("DELETE FROM podcast_tombstones WHERE uuid=?", [id])?;
        import_cache(db, id, &payload)?;
        db.conn.execute(
            "UPDATE podcasts SET needs_sync=1,subscribed=1 WHERE uuid=?",
            [id],
        )?;
        return Ok(());
    }
    add_feed(db, api, s(result, "feedUrl"))
}

pub fn artwork_path(uuid: &str) -> PathBuf {
    crate::db::xdg("XDG_CACHE_HOME", ".cache")
        .join("dusky_casts/artwork")
        .join(format!(
            "{}.png",
            uuid::Uuid::new_v5(&uuid::Uuid::NAMESPACE_URL, uuid.as_bytes())
        ))
}
pub fn cache_artwork(api: &Api, uuid: &str, url: &str) -> Result<()> {
    let path = artwork_path(uuid);
    cache_artwork_at(api, url, &path)
}
pub fn cache_artwork_at(api: &Api, url: &str, path: &Path) -> Result<()> {
    if path.is_file() {
        return Ok(());
    }
    let mut bytes = Vec::new();
    api.http
        .get(url)
        .timeout(std::time::Duration::from_secs(15))
        .send()?
        .error_for_status()?
        .take(10 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)?;
    anyhow::ensure!(bytes.len() <= 10 * 1024 * 1024, "Artwork exceeds 10 MiB");
    let mut reader = image::ImageReader::new(std::io::Cursor::new(bytes)).with_guessed_format()?;
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(8192);
    limits.max_image_height = Some(8192);
    limits.max_alloc = Some(128 * 1024 * 1024);
    reader.limits(limits);
    let image = reader.decode()?.thumbnail(420, 420);
    let root = path.parent().expect("Artwork has parent");
    std::fs::create_dir_all(root)?;
    let mut temp = tempfile::NamedTempFile::new_in(root)?;
    image.write_to(&mut temp, image::ImageFormat::Png)?;
    temp.persist(path)?;
    Ok(())
}

pub fn decode_artwork_rgba(uuid: &str, max_dim: u32) -> Option<(u32, u32, Vec<u8>)> {
    decode_artwork_rgba_at(&artwork_path(uuid), max_dim)
}

pub fn decode_artwork_rgba_at(path: &Path, max_dim: u32) -> Option<(u32, u32, Vec<u8>)> {
    if !path.is_file() {
        return None;
    }
    let mut reader = image::ImageReader::open(path)
        .ok()?
        .with_guessed_format()
        .ok()?;
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(8192);
    limits.max_image_height = Some(8192);
    limits.max_alloc = Some(64 * 1024 * 1024);
    reader.limits(limits);
    let decoded = reader.decode().ok()?;
    let image = if max_dim > 0 {
        decoded.thumbnail(max_dim, max_dim)
    } else {
        decoded
    };
    let rgba = image.to_rgba8();
    Some((rgba.width(), rgba.height(), rgba.into_raw()))
}
