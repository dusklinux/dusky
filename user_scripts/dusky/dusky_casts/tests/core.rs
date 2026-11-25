use dusky_casts::{
    api::Api,
    db::{Db, now},
    library, sync,
    wire::sync::*,
};
use prost::Message;
use serde_json::json;
use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

struct Fixture {
    _dir: tempfile::TempDir,
    db: Db,
}
impl Fixture {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let mut db = Db::open(dir.path().join("library.db")).unwrap();
        library::import_cache(&mut db,"pod",&json!({"uuid":"pod","title":"Test podcast","episodes":[{"uuid":"a","title":"Alpha","url":"https://example.invalid/a.mp3","published":"2026-10-01T00:00:00Z","duration":100},{"uuid":"b","title":"Beta","url":"https://example.invalid/b.mp3","duration":200}]})).unwrap();
        db.set("account_token", "test-token").unwrap();
        db.set("rust_import_complete", "1").unwrap();
        Self { _dir: dir, db }
    }
    fn id(&self, uuid: &str) -> i64 {
        self.db
            .conn
            .query_row("SELECT id FROM episodes WHERE uuid=?", [uuid], |r| r.get(0))
            .unwrap()
    }
}
struct Response {
    status: u16,
    body: Vec<u8>,
}
impl Response {
    fn proto<M: Message>(v: M) -> Self {
        Self {
            status: 200,
            body: v.encode_to_vec(),
        }
    }
}
struct Mock {
    base: String,
    stop: Arc<AtomicBool>,
    join: Option<thread::JoinHandle<()>>,
}
impl Mock {
    fn new(handler: impl Fn(&str, &[u8]) -> Response + Send + 'static) -> Self {
        Self::all_requests(move |path, body| {
            if path == "/user/stats/summary" {
                Response::proto(dusky_casts::wire::api::StatsResponse::default())
            } else {
                handler(path, body)
            }
        })
    }
    fn all_requests(handler: impl Fn(&str, &[u8]) -> Response + Send + 'static) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}/", listener.local_addr().unwrap());
        listener.set_nonblocking(true).unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let flag = stop.clone();
        let join = thread::spawn(move || {
            while !flag.load(Ordering::Relaxed) {
                let (mut stream, _) = match listener.accept() {
                    Ok(v) => v,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    Err(e) => panic!("{e}"),
                };
                stream
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let (path, body) = read_request(&mut stream);
                let response = handler(&path, &body);
                write!(
                    stream,
                    "HTTP/1.1 {} OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    response.status,
                    response.body.len()
                )
                .unwrap();
                stream.write_all(&response.body).unwrap();
            }
        });
        Self {
            base,
            stop,
            join: Some(join),
        }
    }
    fn api(&self) -> Api {
        let mut api = Api::new("test-token".into()).unwrap();
        api.base = self.base.clone();
        api.cache = self.base.clone();
        api
    }
}
impl Drop for Mock {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.join.take().unwrap().join().unwrap();
    }
}
fn read_request(stream: &mut TcpStream) -> (String, Vec<u8>) {
    let mut header = Vec::new();
    let mut b = [0];
    while !header.ends_with(b"\r\n\r\n") {
        stream.read_exact(&mut b).unwrap();
        header.push(b[0]);
        assert!(header.len() < 10000)
    }
    let header = String::from_utf8(header).unwrap();
    let length = header
        .lines()
        .find_map(|l| {
            l.to_ascii_lowercase()
                .strip_prefix("content-length:")
                .map(|s| s.trim().parse::<usize>().unwrap())
        })
        .unwrap_or(0);
    let path = header.split_whitespace().nth(1).unwrap().into();
    let mut body = vec![0; length];
    stream.read_exact(&mut body).unwrap();
    (path, body)
}
fn empty_queue() -> Response {
    Response::proto(UpNextResponse {
        server_modified: 100,
        ..Default::default()
    })
}
fn remote_position(uuid: &str, value: i64, stamp: i64) -> SyncUserEpisode {
    SyncUserEpisode {
        uuid: uuid.into(),
        podcast_uuid: "pod".into(),
        played_up_to: Some(Int64Value { value }),
        played_up_to_modified: Some(Int64Value { value: stamp }),
        ..Default::default()
    }
}
fn record(e: SyncUserEpisode) -> Record {
    Record {
        record: Some(record::Record::Episode(e)),
    }
}

#[test]
fn wrapper_zero_presence_and_units_match_phone_wire() {
    // Field 9 = played_up_to wrapper; field 10 = modified wrapper. An empty
    // wrapper carries an explicit zero rather than an absent update.
    let r = sync::episode_record(
        &json!({"uuid":"a","podcast_uuid":"pod","position":1999,"played_up_to_modified":7,"starred":0,"starred_modified":8}),
    );
    assert_eq!(r.played_up_to.unwrap().value, 1);
    assert!(!r.starred.unwrap().value);
    let zero = SyncUserEpisode {
        played_up_to: Some(Int64Value { value: 0 }),
        ..Default::default()
    };
    assert_eq!(zero.encode_to_vec(), vec![0x4a, 0]);
    assert_eq!(record(zero).encode_to_vec(), vec![0x12, 2, 0x4a, 0]);
    let decoded = SyncUserEpisode::decode([0x4a, 2, 8, 42, 0x52, 2, 8, 7].as_slice()).unwrap();
    assert_eq!(decoded.played_up_to.unwrap().value, 42);
    assert_eq!(decoded.played_up_to_modified.unwrap().value, 7);
}
#[test]
fn per_field_merge_false_zero_and_active_playback() {
    let mut f = Fixture::new();
    let id = f.id("a");
    f.db.change(id, "position", 99000).unwrap();
    f.db.change(id, "starred", 1).unwrap();
    let stamp = now() + 10000;
    let mut r = remote_position("a", 0, stamp);
    r.starred = Some(BoolValue { value: false });
    r.starred_modified = Some(Int64Value { value: stamp });
    f.db.active(Some(id)).unwrap();
    sync::apply_episode(&f.db, &r).unwrap();
    assert_eq!(f.db.episode(id).unwrap().position, 99000);
    assert!(!f.db.episode(id).unwrap().starred);
    f.db.active(None).unwrap();
    sync::apply_episode(&f.db, &r).unwrap();
    assert_eq!(f.db.episode(id).unwrap().position, 0);
    sync::apply_episode(&f.db, &remote_position("a", 55, stamp - 1)).unwrap();
    assert_eq!(f.db.episode(id).unwrap().position, 0);
}
#[test]
fn failed_and_304_requests_keep_outbox_and_cursor() {
    for status in [503, 304, 200] {
        let mut f = Fixture::new();
        let id = f.id("a");
        f.db.change(id, "position", 5000).unwrap();
        f.db.set("sync_cursor", "42").unwrap();
        let mock = Mock::new(move |path, _| {
            if path == "/user/sync/update" {
                Response {
                    status,
                    body: vec![],
                }
            } else {
                empty_queue()
            }
        });
        let result = sync::run(&mut f.db, &mock.api());
        assert!(result.is_err());
        assert_eq!(f.db.number("sync_cursor").unwrap(), 42);
        assert_eq!(
            f.db.rows("SELECT needs_sync FROM episodes WHERE uuid='a'")
                .unwrap()[0]["needs_sync"],
            1
        );
    }
}
#[test]
fn edit_during_network_request_is_not_acknowledged_or_overwritten() {
    let mut f = Fixture::new();
    let id = f.id("a");
    f.db.change(id, "position", 5000).unwrap();
    let path = f.db.path.clone();
    let mock = Mock::new(move |url, bytes| {
        if url == "/user/sync/update" {
            let request = SyncUpdateRequest::decode(bytes).unwrap();
            assert_eq!(request.records.len(), 1);
            let mut other = Db::open(&path).unwrap();
            other.change(id, "position", 77000).unwrap();
            Response::proto(SyncUpdateResponse {
                last_modified: 100,
                records: vec![record(remote_position("a", 6, 1))],
            })
        } else {
            empty_queue()
        }
    });
    sync::run(&mut f.db, &mock.api()).unwrap();
    assert_eq!(f.db.episode(id).unwrap().position, 77000);
    assert_eq!(
        f.db.rows("SELECT needs_sync FROM episodes WHERE uuid='a'")
            .unwrap()[0]["needs_sync"],
        1
    );
}
#[test]
fn response_and_cursor_are_atomic_on_database_failure() {
    let mut f = Fixture::new();
    recorded_listening(&f.db, 12000);
    let id = f.id("a");
    f.db.change(id, "position", 5000).unwrap();
    f.db.conn.execute_batch("CREATE TRIGGER fail_folder BEFORE INSERT ON folders BEGIN SELECT RAISE(ABORT,'fixture failure'); END;").unwrap();
    let mock = Mock::new(|url, _| {
        if url == "/user/sync/update" {
            Response::proto(SyncUpdateResponse {
                last_modified: 100,
                records: vec![
                    record(remote_position("a", 10, now() + 10000)),
                    Record {
                        record: Some(record::Record::Folder(SyncUserFolder {
                            folder_uuid: "f".into(),
                            ..Default::default()
                        })),
                    },
                ],
            })
        } else {
            empty_queue()
        }
    });
    assert!(sync::run(&mut f.db, &mock.api()).is_err());
    assert_eq!(f.db.number("sync_cursor").unwrap(), 0);
    assert_eq!(f.db.number("stats_ack_seconds").unwrap(), 0);
    assert_eq!(
        dusky_casts::stats::snapshot(&f.db)
            .unwrap()
            .unwrap()
            .seconds,
        12
    );
    assert_eq!(f.db.episode(id).unwrap().position, 5000);
    assert_eq!(
        f.db.rows("SELECT needs_sync FROM episodes WHERE uuid='a'")
            .unwrap()[0]["needs_sync"],
        1
    );
}
fn queue_response() -> Response {
    Response::proto(UpNextResponse {
        server_modified: 100,
        episodes: vec![up_next_response::EpisodeResponse {
            uuid: "a".into(),
            podcast: "pod".into(),
            title: "Alpha".into(),
            url: "https://example.invalid/a.mp3".into(),
            ..Default::default()
        }],
        ..Default::default()
    })
}
#[test]
fn first_queue_sync_pulls_then_merges_instead_of_replacing_phone_queue() {
    let mut f = Fixture::new();
    f.db.edit_queue(f.id("b"), 3).unwrap();
    let mock = Mock::new(|url, bytes| {
        assert_eq!(url, "/up_next/sync");
        let request = UpNextSyncRequest::decode(bytes).unwrap();
        assert!(request.up_next.unwrap().changes.is_empty());
        queue_response()
    });
    sync::sync_queue(&mut f.db, &mock.api()).unwrap();
    assert_eq!(f.db.queue().unwrap(), vec![f.id("a"), f.id("b")]);
    assert_eq!(
        f.db.rows("SELECT action FROM queue_actions").unwrap()[0]["action"],
        3
    );
}
#[test]
fn queue_edits_during_request_keep_order_and_replay() {
    let mut f = Fixture::new();
    f.db.set("rust_queue_initialized", "1").unwrap();
    let b = f.id("b");
    let path = f.db.path.clone();
    let mock = Mock::new(move |_, _| {
        let mut other = Db::open(&path).unwrap();
        other.edit_queue(b, 2).unwrap();
        queue_response()
    });
    sync::sync_queue(&mut f.db, &mock.api()).unwrap();
    assert_eq!(f.db.queue().unwrap(), vec![b]);
    assert_eq!(f.db.rows("SELECT * FROM queue_actions").unwrap().len(), 1);
    assert_eq!(f.db.number("upnext_cursor").unwrap(), 0);
}
#[test]
fn deferred_unknown_episode_merges_partial_fields_and_survives_reopen() {
    let f = Fixture::new();
    let mut star = remote_position("old", 50, 10);
    star.starred = Some(BoolValue { value: true });
    star.starred_modified = Some(Int64Value { value: 10 });
    assert!(!sync::apply_episode(&f.db, &star).unwrap());
    sync::apply_episode(&f.db, &remote_position("old", 80, 20)).unwrap();
    let mut other = Db::open(&f.db.path).unwrap();
    let bytes: Vec<u8> = other
        .conn
        .query_row(
            "SELECT record FROM remote_episodes WHERE uuid='old'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let r = SyncUserEpisode::decode(bytes.as_slice()).unwrap();
    assert!(r.starred.as_ref().unwrap().value);
    assert_eq!(r.played_up_to.as_ref().unwrap().value, 80);
    library::import_cache(&mut other,"pod",&json!({"uuid":"pod","episodes":[{"uuid":"old","title":"Historical","url":"https://example.invalid/old"}]})).unwrap();
    assert!(sync::apply_episode(&other, &r).unwrap());
    let rows = other
        .rows("SELECT position,starred FROM episodes WHERE uuid='old'")
        .unwrap();
    assert_eq!(rows[0]["position"], 80000);
    assert_eq!(rows[0]["starred"], 1);
}
#[test]
fn stress_large_library_bounded_paging_and_timestamp_conflicts() {
    let mut f = Fixture::new();
    let start = Instant::now();
    let tx = f.db.conn.transaction().unwrap();
    {
        let mut insert=tx.prepare("INSERT INTO episodes(uuid,podcast_id,title,audio_url,published_at,added_at) VALUES (?,1,?,'https://example.invalid/audio',?,1)").unwrap();
        for index in 0..25000 {
            insert
                .execute(rusqlite::params![
                    format!("stress-{index}"),
                    format!("Episode {index}"),
                    index
                ])
                .unwrap();
        }
    }
    tx.commit().unwrap();
    for page in 0..100 {
        assert_eq!(
            f.db.episodes("All episodes", "", 0, 60, page * 60)
                .unwrap()
                .len(),
            60
        );
    }
    let id = f.id("a");
    let mut largest = 0;
    let mut expected = 0;
    for i in 0..10000 {
        let stamp = (i * 7919) % 10007 + 1;
        let position = i % 100;
        sync::apply_episode(&f.db, &remote_position("a", position, stamp)).unwrap();
        if stamp > largest {
            largest = stamp;
            expected = position * 1000
        }
        assert_eq!(f.db.episode(id).unwrap().position, expected);
    }
    assert_eq!(
        f.db.conn
            .query_row("PRAGMA integrity_check", [], |r| r.get::<_, String>(0))
            .unwrap(),
        "ok"
    );
    eprintln!(
        "stress: 25,002 episodes, 100 page queries, 10,000 conflict merges in {:?}",
        start.elapsed()
    );
}
#[test]
fn smart_playlist_filters_and_manual_membership() {
    let mut f = Fixture::new();
    let a = f.id("a");
    f.db.change(a, "starred", 1).unwrap();
    f.db.conn.execute("INSERT INTO playlists(uuid,title,all_podcasts,starred,manual) VALUES ('smart','Starred',1,1,0)",[]).unwrap();
    let id = f.db.conn.last_insert_rowid();
    let view = f.db.playlist_view(id, "", 60, 0).unwrap();
    assert_eq!(view.len(), 1);
    assert_eq!(view[0].id, a);
    f.db.conn
        .execute(
            "INSERT INTO playlists(uuid,title,manual,sort_type) VALUES ('manual','Manual',1,4)",
            [],
        )
        .unwrap();
    let id = f.db.conn.last_insert_rowid();
    f.db.conn
        .execute(
            "INSERT INTO playlist_episodes(playlist_id,episode_uuid,position) VALUES (?,'b',0)",
            [id],
        )
        .unwrap();
    assert_eq!(f.db.playlist_view(id, "", 60, 0).unwrap()[0].uuid, "b");
}
#[test]
fn saving_paused_or_completed_position_does_not_reset_played_status() {
    let mut f = Fixture::new();
    let a = f.id("a");
    f.db.change(a, "playing_status", 2).unwrap();
    f.db.progress(a, 100.0, 100.0).unwrap();
    assert_eq!(f.db.episode(a).unwrap().playing_status, 2);
    f.db.conn
        .execute("UPDATE episodes SET needs_sync=0 WHERE id=?", [a])
        .unwrap();
    f.db.progress(a, 100.0, 100.0).unwrap();
    assert_eq!(
        f.db.rows("SELECT needs_sync FROM episodes WHERE uuid='a'")
            .unwrap()[0]["needs_sync"],
        0
    );
}

#[test]
fn login_and_subscription_import_use_phone_messages_without_embedded_identity() {
    use dusky_casts::wire::api as wire;
    let mock = Mock::new(|path, bytes| {
        match path {
        "/user/login"=>{
            let r=wire::UserLoginRequest::decode(bytes).unwrap();assert_eq!(r.email,"fixture@example.invalid");assert_eq!(r.password,"fixture-password");assert_eq!(r.scope,"mobile");
            Response::proto(wire::UserLoginResponse{token:"test-token".into(),uuid:"fixture-account".into(),email:r.email})
        },
        "/user/podcast/list"=>{let r=wire::UserPodcastListRequest::decode(bytes).unwrap();assert_eq!(r.m,"mobile");Response::proto(wire::UserPodcastListResponse{podcasts:vec![wire::UserPodcastResponse{uuid:"new-show".into(),title:"New show".into(),..Default::default()}],folders:vec![]})},
        "/mobile/podcast/full/new-show"=>Response{status:200,body:serde_json::to_vec(&json!({"podcast":{"uuid":"new-show","title":"New show","episodes":[{"uuid":"new-episode","title":"Newest","url":"https://example.invalid/new","duration":123}]}})).unwrap()},
        _=>panic!("Unexpected endpoint {path}"),
    }
    });
    let api = mock.api();
    let account = api
        .login("fixture@example.invalid".into(), "fixture-password".into())
        .unwrap();
    assert_eq!(account.uuid, "fixture-account");
    let mut f = Fixture::new();
    assert_eq!(library::import_account(&mut f.db, &api).unwrap(), 1);
    let rows=f.db.rows("SELECT e.uuid,p.uuid AS podcast_uuid FROM episodes e JOIN podcasts p ON e.podcast_id=p.id WHERE e.uuid='new-episode'").unwrap();
    assert_eq!(rows[0]["podcast_uuid"], "new-show");
    assert_eq!(f.db.get("rust_import_complete").unwrap(), "1");
}

#[test]
fn rss_import_keeps_local_identity_and_preserves_playback_on_refresh() {
    let rss=br#"<?xml version="1.0"?><rss version="2.0"><channel><title>Fixture show</title><description>Test</description><link>https://example.invalid</link><item><title>Episode</title><guid>stable-id</guid><enclosure url="https://example.invalid/episode.mp3" length="100" type="audio/mpeg"/></item></channel></rss>"#;
    let mock = Mock::new(move |_, _| Response {
        status: 200,
        body: rss.to_vec(),
    });
    let mut f = Fixture::new();
    let url = format!("{}feed.xml", mock.base);
    library::add_feed(&mut f.db, &mock.api(), &url).unwrap();
    let rows=f.db.rows("SELECT e.id,e.uuid FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE p.title='Fixture show'").unwrap();
    assert_eq!(rows.len(), 1);
    let id = rows[0]["id"].as_i64().unwrap();
    f.db.change(id, "position", 42000).unwrap();
    library::add_feed(&mut f.db, &mock.api(), &url).unwrap();
    assert_eq!(f.db.episode(id).unwrap().position, 42000);
    assert_eq!(
        f.db.rows("SELECT needs_sync FROM podcasts WHERE title='Fixture show'")
            .unwrap()[0]["needs_sync"],
        0
    );
}

#[test]
fn download_atomic_publish_progress_and_cancellation_preserve_existing_file() {
    let data = vec![42u8; 200000];
    let mock = Mock::new(move |_, _| Response {
        status: 200,
        body: data.clone(),
    });
    let f = Fixture::new();
    let id = f.id("a");
    f.db.conn
        .execute(
            "UPDATE episodes SET audio_url=? WHERE id=?",
            rusqlite::params![format!("{}episode", mock.base), id],
        )
        .unwrap();
    let root = tempfile::tempdir().unwrap();
    let control = library::DownloadControl::default();
    library::download_to(&f.db, &mock.api(), id, &control, root.path()).unwrap();
    assert_eq!(control.bytes.load(Ordering::Relaxed), 200000);
    assert_eq!(control.total.load(Ordering::Relaxed), 200000);
    let path = f.db.episode(id).unwrap().downloaded_path;
    assert_eq!(std::fs::read(&path).unwrap(), vec![42; 200000]);
    control.cancel.store(true, Ordering::Relaxed);
    assert!(library::download_to(&f.db, &mock.api(), id, &control, root.path()).is_err());
    assert_eq!(std::fs::read(&path).unwrap(), vec![42; 200000]);
    assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
}

#[test]
fn synced_manual_playlist_retains_unknown_metadata_and_folder_deletion() {
    let f = Fixture::new();
    let p = SyncUserPlaylist {
        uuid: "server-record".into(),
        original_uuid: "manual-list".into(),
        title: Some(StringValue {
            value: "Phone list".into(),
        }),
        manual: Some(BoolValue { value: true }),
        episode_order: vec!["unfetched".into()],
        episodes: vec![SyncPlaylistEpisode {
            episode: "unfetched".into(),
            podcast: "pod".into(),
            title: Some(StringValue {
                value: "Historical entry".into(),
            }),
            url: Some(StringValue {
                value: "https://example.invalid/historical".into(),
            }),
            ..Default::default()
        }],
        ..Default::default()
    };
    sync::apply_record(
        &f.db,
        &Record {
            record: Some(record::Record::Playlist(p)),
        },
    )
    .unwrap();
    let rows = f.db.rows("SELECT * FROM playlist_episodes").unwrap();
    assert_eq!(rows[0]["title"], "Historical entry");
    assert_eq!(rows[0]["episode_uuid"], "unfetched");
    let folder = SyncUserFolder {
        folder_uuid: "folder".into(),
        name: "From phone".into(),
        ..Default::default()
    };
    sync::apply_record(
        &f.db,
        &Record {
            record: Some(record::Record::Folder(folder.clone())),
        },
    )
    .unwrap();
    sync::apply_record(
        &f.db,
        &Record {
            record: Some(record::Record::Folder(SyncUserFolder {
                is_deleted: true,
                ..folder
            })),
        },
    )
    .unwrap();
    assert_eq!(
        f.db.rows("SELECT is_deleted FROM folders").unwrap()[0]["is_deleted"],
        1
    );
}

#[test]
fn bootstrap_uses_snapshots_and_watermark_preserving_offline_edits() {
    use dusky_casts::wire::{api as a, bootstrap as full};
    let mut f = Fixture::new();
    f.db.set("rust_import_complete", "").unwrap();
    let id = f.id("a");
    f.db.change(id, "position", 77000).unwrap();
    let mock = Mock::new(|path, bytes| match path {
        "/user/last_sync_at" => {
            assert!(bytes.is_empty());
            Response::proto(a::UserLastSyncAtResponse {
                last_sync_at_ms: 123000,
                ..Default::default()
            })
        }
        "/user/podcast/list" => Response::proto(a::UserPodcastListResponse::default()),
        "/user/podcast/episodes" => {
            let req = full::UuidRequest::decode(bytes).unwrap();
            assert_eq!(req.uuid, "pod");
            assert_eq!(req.m, "mobile");
            Response::proto(full::SyncEpisodesResponse {
                episodes: vec![
                    full::EpisodeSyncResponse {
                        uuid: "a".into(),
                        played_up_to: 5,
                        starred: true,
                        duration: 100,
                        ..Default::default()
                    },
                    full::EpisodeSyncResponse {
                        uuid: "b".into(),
                        played_up_to: 17,
                        playing_status: 1,
                        duration: 200,
                        ..Default::default()
                    },
                ],
            })
        }
        "/user/playlist/list" => Response::proto(full::UserPlaylistListResponse {
            playlists: vec![full::PlaylistSyncResponse {
                uuid: "list".into(),
                title: "Snapshot list".into(),
                manual: Some(BoolValue { value: true }),
                episode_order: vec!["b".into()],
                ..Default::default()
            }],
        }),
        _ => panic!("Unexpected endpoint {path}"),
    });
    sync::bootstrap(&mut f.db, &mock.api()).unwrap();
    assert_eq!(f.db.number("sync_cursor").unwrap(), 123000);
    assert_eq!(f.db.get("rust_import_complete").unwrap(), "1");
    let a = f.db.episode(id).unwrap();
    assert_eq!(a.position, 77000);
    assert!(a.starred);
    assert_eq!(f.db.episode(f.id("b")).unwrap().position, 17000);
    assert_eq!(
        f.db.rows("SELECT title FROM playlists").unwrap()[0]["title"],
        "Snapshot list"
    );
}

#[test]
fn interrupted_bootstrap_never_advances_cursor_or_marks_import_complete() {
    use dusky_casts::wire::{api as a, bootstrap as full};
    let mut f = Fixture::new();
    f.db.set("rust_import_complete", "").unwrap();
    f.db.set("sync_cursor", "42").unwrap();
    let mock = Mock::new(|path, _| match path {
        "/user/last_sync_at" => Response::proto(a::UserLastSyncAtResponse {
            last_sync_at_ms: 123000,
            ..Default::default()
        }),
        "/user/podcast/list" => Response::proto(a::UserPodcastListResponse::default()),
        "/user/podcast/episodes" => Response::proto(full::SyncEpisodesResponse::default()),
        "/user/playlist/list" => Response {
            status: 503,
            body: vec![],
        },
        _ => panic!("Unexpected endpoint {path}"),
    });
    assert!(sync::bootstrap(&mut f.db, &mock.api()).is_err());
    assert_eq!(f.db.number("sync_cursor").unwrap(), 42);
    assert_eq!(f.db.get("rust_import_complete").unwrap(), "");
}

#[test]
fn outbox_batches_follow_phone_limit_and_acknowledge_only_submitted_rows() {
    let mut f = Fixture::new();
    f.db.set("rust_queue_initialized", "1").unwrap();
    let tx = f.db.conn.transaction().unwrap();
    {
        let mut insert=tx.prepare("INSERT INTO episodes(uuid,podcast_id,title,added_at,needs_sync,position,played_up_to_modified) VALUES (?,1,'Bulk',1,1,42000,1)").unwrap();
        for i in 0..2001 {
            insert.execute([format!("batch-{i}")]).unwrap();
        }
    }
    tx.commit().unwrap();
    let calls = Arc::new(std::sync::Mutex::new(Vec::new()));
    let received = calls.clone();
    let mock = Mock::new(move |path, bytes| {
        if path == "/user/sync/update" {
            let req = SyncUpdateRequest::decode(bytes).unwrap();
            received.lock().unwrap().push(req.records.len());
            Response::proto(SyncUpdateResponse {
                last_modified: 123,
                ..Default::default()
            })
        } else {
            empty_queue()
        }
    });
    sync::run(&mut f.db, &mock.api()).unwrap();
    assert_eq!(
        f.db.rows("SELECT count(*) AS n FROM episodes WHERE needs_sync=1")
            .unwrap()[0]["n"],
        1
    );
    sync::run(&mut f.db, &mock.api()).unwrap();
    assert_eq!(*calls.lock().unwrap(), vec![2000, 1]);
    assert_eq!(
        f.db.rows("SELECT count(*) AS n FROM episodes WHERE needs_sync=1")
            .unwrap()[0]["n"],
        0
    );
}

#[test]
fn unsubscribe_logs_queue_and_playlist_changes_and_cannot_be_undone_by_cache() {
    let mut f = Fixture::new();
    let a = f.id("a");
    f.db.edit_queue(a, 3).unwrap();
    f.db.conn
        .execute(
            "INSERT INTO playlists(uuid,title,manual) VALUES ('list','Manual',1)",
            [],
        )
        .unwrap();
    let list = f.db.conn.last_insert_rowid();
    f.db.conn
        .execute(
            "INSERT INTO playlist_episodes(playlist_id,episode_uuid,position) VALUES (?,'a',0)",
            [list],
        )
        .unwrap();
    library::unsubscribe(&mut f.db, 1).unwrap();
    assert!(f.db.queue().unwrap().is_empty());
    assert!(
        f.db.rows("SELECT * FROM playlist_episodes")
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        f.db.rows("SELECT needs_sync FROM playlists").unwrap()[0]["needs_sync"],
        1
    );
    assert!(
        f.db.rows("SELECT action FROM queue_actions WHERE action=4")
            .unwrap()
            .len()
            == 1
    );
    library::import_cache_with_subscription(
        &mut f.db,
        "pod",
        &json!({"uuid":"pod","title":"Stale response","episodes":[]}),
        false,
    )
    .unwrap();
    assert!(f.db.rows("SELECT * FROM podcasts").unwrap().is_empty());
}

#[test]
fn queue_only_show_stays_unsubscribed_and_uses_returned_position() {
    let mut f = Fixture::new();
    let mock = Mock::new(|path, _| match path {
        "/up_next/sync" => Response::proto(UpNextResponse {
            server_modified: 100,
            episodes: vec![up_next_response::EpisodeResponse {
                uuid: "queued".into(),
                podcast: "other".into(),
                title: "Queued only".into(),
                url: "https://example.invalid/q".into(),
                ..Default::default()
            }],
            episode_sync: vec![up_next_response::EpisodeSyncResponse {
                uuid: "queued".into(),
                played_up_to: Some(Int64Value { value: 42 }),
                duration: Some(Int64Value { value: 300 }),
            }],
        }),
        "/mobile/podcast/full/other" => Response {
            status: 200,
            body: serde_json::to_vec(
                &json!({"uuid":"other","title":"Unsubscribed show","episodes":[]}),
            )
            .unwrap(),
        },
        _ => panic!("Unexpected endpoint {path}"),
    });
    sync::sync_queue(&mut f.db, &mock.api()).unwrap();
    let p =
        f.db.rows("SELECT subscribed,needs_sync FROM podcasts WHERE uuid='other'")
            .unwrap();
    assert_eq!(p[0]["subscribed"], 0);
    assert_eq!(p[0]["needs_sync"], 0);
    let e = f.db.episode(f.db.queue().unwrap()[0]).unwrap();
    assert_eq!(e.position, 42000);
    assert_eq!(e.duration, 300);
}

#[test]
fn first_rust_refresh_preserves_python_rss_ids_downloads_and_progress() {
    let rss=br#"<rss version="2.0"><channel><title>Legacy show</title><link>https://example.invalid</link><description>Test</description><item><title>Refreshed title</title><guid>guid-from-rss</guid><enclosure url="https://example.invalid/old.mp3" type="audio/mpeg" length="10"/></item></channel></rss>"#;
    let mock = Mock::new(move |_, _| Response {
        status: 200,
        body: rss.to_vec(),
    });
    let mut f = Fixture::new();
    let feed = format!("{}feed.xml", mock.base);
    f.db.conn.execute("INSERT INTO podcasts(uuid,title,feed_url,added_at) VALUES ('legacy-show','Legacy',?,1)",[&feed]).unwrap();
    let pid = f.db.conn.last_insert_rowid();
    f.db.conn.execute("INSERT INTO episodes(uuid,podcast_id,title,audio_url,position,downloaded_path,added_at) VALUES ('legacy-sha1-identity',?,'Original','https://example.invalid/old.mp3',42000,'/fixture/download.media',1)",[pid]).unwrap();
    let id = f.db.conn.last_insert_rowid();
    library::add_feed(&mut f.db, &mock.api(), &feed).unwrap();
    let episode = f.db.episode(id).unwrap();
    assert_eq!(episode.uuid, "legacy-sha1-identity");
    assert_eq!(episode.position, 42000);
    assert_eq!(episode.downloaded_path, "/fixture/download.media");
    assert_eq!(episode.title, "Refreshed title");
    assert_eq!(
        f.db.rows(&format!(
            "SELECT count(*) AS n FROM episodes WHERE podcast_id={pid}"
        ))
        .unwrap()[0]["n"],
        1
    );
}

#[test]
fn first_queue_pull_without_local_additions_does_not_log_a_replacement() {
    let mut f = Fixture::new();
    let mock = Mock::new(|_, _| queue_response());
    sync::sync_queue(&mut f.db, &mock.api()).unwrap();
    assert_eq!(f.db.queue().unwrap(), vec![f.id("a")]);
    assert!(f.db.rows("SELECT * FROM queue_actions").unwrap().is_empty());
}
#[test]
fn playing_an_episode_moves_it_to_queue_head_and_records_play_now() {
    let mut f = Fixture::new();
    let a = f.id("a");
    let b = f.id("b");
    f.db.edit_queue(a, 3).unwrap();
    f.db.edit_queue(b, 3).unwrap();
    f.db.edit_queue(b, 1).unwrap();
    assert_eq!(f.db.queue().unwrap(), vec![b, a]);
    assert_eq!(
        f.db.rows("SELECT action FROM queue_actions ORDER BY id DESC LIMIT 1")
            .unwrap()[0]["action"],
        1
    );
}
#[test]
fn artwork_fetch_resizes_caches_and_rejects_invalid_image_without_partial_output() {
    let mut bytes = std::io::Cursor::new(Vec::new());
    image::DynamicImage::new_rgb8(1000, 500)
        .write_to(&mut bytes, image::ImageFormat::Png)
        .unwrap();
    let bytes = bytes.into_inner();
    let count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let requests = count.clone();
    let mock = Mock::new(move |path, _| {
        requests.fetch_add(1, Ordering::Relaxed);
        Response {
            status: 200,
            body: if path == "/cover" {
                bytes.clone()
            } else {
                b"not an image".to_vec()
            },
        }
    });
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("cover.png");
    library::cache_artwork_at(&mock.api(), &format!("{}cover", mock.base), &path).unwrap();
    let image = image::open(&path).unwrap();
    assert_eq!((image.width(), image.height()), (420, 210));
    library::cache_artwork_at(&mock.api(), &format!("{}cover", mock.base), &path).unwrap();
    let bad = dir.path().join("bad.png");
    assert!(library::cache_artwork_at(&mock.api(), &format!("{}bad", mock.base), &bad).is_err());
    assert!(!bad.exists());
    assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
    assert_eq!(count.load(Ordering::Relaxed), 2);
}

#[test]
fn phone_queue_sync_leaves_local_files_and_queue_untouched() {
    let mut f = Fixture::new();
    f.db.conn.execute("INSERT INTO podcasts(uuid,title,feed_url,added_at,subscribed) VALUES ('local-library','Local audio','local://library',0,0)",[]).unwrap();
    f.db.conn.execute("INSERT INTO episodes(uuid,podcast_id,title,audio_url,added_at) SELECT 'local:test',id,'Local','file:///tmp/local.flac',0 FROM podcasts WHERE uuid='local-library'",[]).unwrap();
    let local = f.id("local:test");
    f.db.conn.execute("INSERT INTO local_files(path,episode_id,size,modified,available) VALUES ('file:///tmp/local.flac',?,1,'1',1)",[local]).unwrap();
    f.db.edit_queue(local, 3).unwrap();
    f.db.progress(local, 12.0, 100.0).unwrap();
    let mock = Mock::new(|path, body| {
        assert_eq!(path, "/up_next/sync");
        let req = UpNextSyncRequest::decode(body).unwrap();
        assert!(req.up_next.unwrap().changes.is_empty());
        empty_queue()
    });
    sync::sync_queue(&mut f.db, &mock.api()).unwrap();
    sync::sync_queue(&mut f.db, &mock.api()).unwrap();
    assert_eq!(f.db.queue_for(true).unwrap(), [local]);
    assert_eq!(f.db.episode(local).unwrap().position, 12000);
    assert_eq!(f.db.episodes("Local files", "", 0, 60, 0).unwrap().len(), 1);
    assert!(f.db.queue().unwrap().is_empty());
}

fn recorded_listening(db: &Db, milliseconds: i64) {
    db.conn.execute("INSERT INTO listening_days(day,milliseconds) VALUES ('2026-10-26',?) ON CONFLICT(day) DO UPDATE SET milliseconds=milliseconds+excluded.milliseconds",[milliseconds]).unwrap();
}

#[test]
fn device_statistics_wire_matches_phone_and_preserves_fractional_seconds() {
    let f = Fixture::new();
    f.db.set("sync_device_id", "pc").unwrap();
    f.db.set("stats_started_at", "10").unwrap();
    recorded_listening(&f.db, 12500);
    let snapshot = dusky_casts::stats::snapshot(&f.db).unwrap().unwrap();
    let record = Record {
        record: Some(record::Record::Device(snapshot.record.clone())),
    };
    // api.pb.swift: Record.device=4, wrappers deviceID=1/type=2/start=3/listened=8.
    assert_eq!(
        record.encode_to_vec(),
        [
            0x22, 0x12, 0x0a, 0x04, 0x0a, 0x02, b'p', b'c', 0x12, 0x02, 0x08, 0x01, 0x1a, 0x02,
            0x08, 0x0a, 0x42, 0x02, 0x08, 0x0c
        ]
    );
    assert_eq!(snapshot.seconds, 12);
    dusky_casts::stats::acknowledge(&f.db, &snapshot).unwrap();
    assert!(dusky_casts::stats::snapshot(&f.db).unwrap().is_none());
    recorded_listening(&f.db, 500);
    assert_eq!(
        dusky_casts::stats::snapshot(&f.db)
            .unwrap()
            .unwrap()
            .seconds,
        13
    );
}

#[test]
fn local_audio_sends_only_cumulative_seconds_with_no_file_registration() {
    use dusky_casts::wire::api::{StatsRequest, StatsResponse};
    let mut f = Fixture::new();
    f.db.set("sync_device_id", "pc-statistics").unwrap();
    f.db.conn.execute("INSERT INTO podcasts(uuid,title,feed_url,added_at,subscribed) VALUES ('local-library','Local audio','local://library',0,0)",[]).unwrap();
    f.db.conn.execute("INSERT INTO episodes(uuid,podcast_id,title,audio_url,added_at) SELECT 'local:private',id,'Private filename','file:///private/example.wav',0 FROM podcasts WHERE uuid='local-library'",[]).unwrap();
    f.db.progress(f.id("local:private"), 10.0, 100.0).unwrap();
    recorded_listening(&f.db, 14500);
    let requests = Arc::new(AtomicUsize::new(0));
    let counter = requests.clone();
    let mock = Mock::all_requests(move |path, body| match path {
        "/user/sync/update" => {
            let req = SyncUpdateRequest::decode(body).unwrap();
            let index = counter.fetch_add(1, Ordering::Relaxed);
            if index == 0 {
                assert_eq!(req.records.len(), 1);
                let Some(record::Record::Device(device)) = &req.records[0].record else {
                    panic!("Expected device statistics");
                };
                assert_eq!(device.device_id.as_ref().unwrap().value, "pc-statistics");
                assert_eq!(device.time_listened.as_ref().unwrap().value, 14);
            } else {
                assert!(req.records.is_empty());
            }
            assert!(!String::from_utf8_lossy(body).contains("private"));
            Response::proto(SyncUpdateResponse {
                last_modified: 100,
                records: vec![],
            })
        }
        "/up_next/sync" => empty_queue(),
        "/user/stats/summary" => {
            let req = StatsRequest::decode(body).unwrap();
            assert_eq!(req.device_id, "pc-statistics");
            assert_eq!(req.time_listened, 0);
            Response::proto(StatsResponse {
                time_listened: 100,
                ..Default::default()
            })
        }
        other => panic!("Unexpected endpoint, including file upload: {other}"),
    });
    sync::run(&mut f.db, &mock.api()).unwrap();
    sync::run(&mut f.db, &mock.api()).unwrap();
    assert_eq!(requests.load(Ordering::Relaxed), 2);
    assert_eq!(f.db.number("stats_ack_seconds").unwrap(), 14);
    assert_eq!(f.db.number("stats_other_seconds").unwrap(), 100);
    assert_eq!(dusky_casts::stats::total_ms(&f.db).unwrap(), 14500);
}

#[test]
fn statistics_acknowledges_only_the_snapshot_and_retries_failed_requests() {
    let mut f = Fixture::new();
    recorded_listening(&f.db, 10500);
    let path = f.db.path.clone();
    let mock = Mock::new(move |endpoint, body| match endpoint {
        "/user/sync/update" => {
            let req = SyncUpdateRequest::decode(body).unwrap();
            let Some(record::Record::Device(device)) = &req.records[0].record else {
                panic!("Missing stats");
            };
            assert_eq!(device.time_listened.as_ref().unwrap().value, 10);
            let concurrent = Db::open(&path).unwrap();
            recorded_listening(&concurrent, 1000);
            Response::proto(SyncUpdateResponse {
                last_modified: 100,
                records: vec![],
            })
        }
        "/up_next/sync" => empty_queue(),
        other => panic!("Unexpected {other}"),
    });
    sync::run(&mut f.db, &mock.api()).unwrap();
    assert_eq!(f.db.number("stats_ack_seconds").unwrap(), 10);
    assert_eq!(
        dusky_casts::stats::snapshot(&f.db)
            .unwrap()
            .unwrap()
            .seconds,
        11
    );
    for status in [500, 304, 200] {
        let failed = Mock::new(move |endpoint, _| match endpoint {
            "/user/sync/update" => Response {
                status,
                body: vec![],
            },
            "/up_next/sync" => empty_queue(),
            other => panic!("Unexpected {other}"),
        });
        assert!(sync::run(&mut f.db, &failed.api()).is_err());
        assert_eq!(f.db.number("stats_ack_seconds").unwrap(), 10);
    }
    let accepted = Mock::new(|endpoint, body| match endpoint {
        "/user/sync/update" => {
            let req = SyncUpdateRequest::decode(body).unwrap();
            let Some(record::Record::Device(d)) = &req.records[0].record else {
                panic!("Missing stats");
            };
            assert_eq!(d.time_listened.as_ref().unwrap().value, 11);
            Response::proto(SyncUpdateResponse {
                last_modified: 101,
                records: vec![],
            })
        }
        "/up_next/sync" => empty_queue(),
        other => panic!("Unexpected {other}"),
    });
    sync::run(&mut f.db, &accepted.api()).unwrap();
    assert_eq!(f.db.number("stats_ack_seconds").unwrap(), 11);
}

#[test]
fn device_restore_and_exclusive_summary_never_duplicate_other_devices() {
    use dusky_casts::wire::api::{StatsRequest, StatsResponse};
    let mut f = Fixture::new();
    recorded_listening(&f.db, 10000);
    let own = f.db.get("sync_device_id").unwrap();
    let mut remote = SyncUserDevice {
        device_id: Some(StringValue {
            value: "another-phone".into(),
        }),
        time_listened: Some(Int64Value { value: 15 }),
        ..Default::default()
    };
    dusky_casts::stats::apply_device(&f.db, &remote).unwrap();
    assert_eq!(dusky_casts::stats::total_ms(&f.db).unwrap(), 10000);
    remote.device_id = Some(StringValue { value: own.clone() });
    dusky_casts::stats::apply_device(&f.db, &remote).unwrap();
    dusky_casts::stats::apply_device(&f.db, &remote).unwrap();
    recorded_listening(&f.db, 1000);
    assert_eq!(dusky_casts::stats::total_ms(&f.db).unwrap(), 16000);
    // As on the phone, scoped device responses can omit the identity wrapper.
    remote.device_id = None;
    remote.time_listened = Some(Int64Value { value: 16 });
    dusky_casts::stats::apply_device(&f.db, &remote).unwrap();
    assert_eq!(dusky_casts::stats::total_ms(&f.db).unwrap(), 16000);
    let mock = Mock::all_requests(move |endpoint, body| {
        assert_eq!(endpoint, "/user/stats/summary");
        let req = StatsRequest::decode(body).unwrap();
        assert_eq!(req.device_id, own);
        Response::proto(StatsResponse {
            time_listened: 30,
            ..Default::default()
        })
    });
    dusky_casts::stats::refresh_summary(&mut f.db, &mock.api()).unwrap();
    let account = dusky_casts::stats::total_ms(&f.db).unwrap() / 1000
        + f.db.number("stats_other_seconds").unwrap();
    assert_eq!(account, 46);
    assert_eq!(
        dusky_casts::stats::snapshot(&f.db)
            .unwrap()
            .unwrap()
            .seconds,
        16
    );
    assert_eq!(
        f.db.conn
            .query_row("SELECT sum(milliseconds) FROM listening_days", [], |r| r
                .get::<_, i64>(0))
            .unwrap(),
        11000
    );
}

#[test]
fn failed_or_wrong_account_summary_preserves_the_cached_total() {
    let mut f = Fixture::new();
    f.db.set("stats_other_seconds", "123").unwrap();
    f.db.set("stats_summary_at", "1").unwrap();
    for status in [500, 304] {
        let mock = Mock::all_requests(move |_, _| Response {
            status,
            body: vec![],
        });
        assert!(dusky_casts::stats::refresh_summary(&mut f.db, &mock.api()).is_err());
        assert_eq!(f.db.number("stats_other_seconds").unwrap(), 123);
        assert_eq!(f.db.number("stats_summary_at").unwrap(), 1);
    }
    let path = f.db.path.clone();
    let mock = Mock::all_requests(move |_, _| {
        Db::open(&path)
            .unwrap()
            .set("account_token", "changed-account")
            .unwrap();
        Response::proto(dusky_casts::wire::api::StatsResponse {
            time_listened: 999,
            ..Default::default()
        })
    });
    assert!(dusky_casts::stats::refresh_summary(&mut f.db, &mock.api()).is_err());
    assert_eq!(f.db.number("stats_other_seconds").unwrap(), 123);
}

#[test]
fn summary_failure_does_not_undo_acknowledged_statistics() {
    let mut f = Fixture::new();
    recorded_listening(&f.db, 8000);
    f.db.set("stats_other_seconds", "123").unwrap();
    f.db.set("stats_summary_at", "1").unwrap();
    let mock = Mock::all_requests(|endpoint, _| match endpoint {
        "/user/sync/update" => Response::proto(SyncUpdateResponse {
            last_modified: 100,
            records: vec![],
        }),
        "/up_next/sync" => empty_queue(),
        "/user/stats/summary" => Response {
            status: 500,
            body: vec![],
        },
        other => panic!("Unexpected {other}"),
    });
    sync::run(&mut f.db, &mock.api()).unwrap();
    assert_eq!(f.db.number("stats_ack_seconds").unwrap(), 8);
    assert_eq!(f.db.number("sync_cursor").unwrap(), 100);
    assert_eq!(f.db.number("stats_other_seconds").unwrap(), 123);
    assert!(f.db.get("stats_error").unwrap().contains("HTTP 500"));
    assert!(dusky_casts::stats::snapshot(&f.db).unwrap().is_none());
}

#[test]
fn sql_view_stats_matches_played_in_progress_active_and_queue_semantics() {
    let mut f = Fixture::new();
    let a = f.id("a");
    let b = f.id("b");
    f.db.conn
        .execute(
            "INSERT INTO episodes(uuid,podcast_id,title,audio_url,duration,added_at) VALUES ('c',1,'Gamma','https://example.invalid/c.mp3',300,1)",
            [],
        )
        .unwrap();
    f.db.change(a, "playing_status", 1).unwrap();
    f.db.change(a, "position", 40_000).unwrap();
    f.db.change(b, "playing_status", 2).unwrap();
    f.db.change(b, "position", 50_000).unwrap();

    let (count, total, active) = f.db.view_stats("All episodes", "", 0, None).unwrap();
    assert_eq!(count, 3);
    assert!((total - 360.0).abs() < 1e-6);
    assert!(active.is_none());

    let (count, total, active) = f.db.view_stats("All episodes", "", 0, Some(a)).unwrap();
    assert_eq!(count, 3);
    assert!((total - 360.0).abs() < 1e-6);
    assert!((active.unwrap() - 60.0).abs() < 1e-6);

    let (count, total, active) = f.db.view_stats("All episodes", "", 0, Some(b)).unwrap();
    assert_eq!(count, 3);
    assert!((total - 560.0).abs() < 1e-6);
    assert!((active.unwrap() - 200.0).abs() < 1e-6);

    f.db.edit_queue_batch(&[a, b], 3).unwrap();
    let (q_count, q_total, q_active) = f.db.view_stats("Up Next", "", 0, Some(a)).unwrap();
    assert_eq!(q_count, 2);
    assert!((q_total - 260.0).abs() < 1e-6);
    assert!((q_active.unwrap() - 60.0).abs() < 1e-6);
}

#[test]
fn change_batch_and_episodes_by_ids_preserve_order_and_mark_dirty() {
    let mut f = Fixture::new();
    let a = f.id("a");
    let b = f.id("b");
    f.db.conn
        .execute(
            "INSERT INTO episodes(uuid,podcast_id,title,audio_url,duration,added_at) VALUES ('c',1,'Gamma','https://example.invalid/c.mp3',300,1)",
            [],
        )
        .unwrap();
    let c = f.id("c");
    f.db.conn
        .execute("UPDATE episodes SET needs_sync=0", [])
        .unwrap();

    f.db.change_batch(&[b, a], "starred", 1).unwrap();
    assert!(f.db.episode(a).unwrap().starred);
    assert!(f.db.episode(b).unwrap().starred);
    assert!(!f.db.episode(c).unwrap().starred);
    let dirty: i64 =
        f.db.conn
            .query_row(
                "SELECT count(*) FROM episodes WHERE needs_sync=1",
                [],
                |r| r.get(0),
            )
            .unwrap();
    assert_eq!(dirty, 2);

    let fetched = f.db.episodes_by_ids(&[c, a, b]).unwrap();
    let ids: Vec<i64> = fetched.iter().map(|e| e.id).collect();
    assert_eq!(ids, vec![c, a, b]);
}

#[test]
fn user_version_4_migration_adds_podcast_columns_and_indexes() {
    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("legacy_v3.db");
    {
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "CREATE TABLE podcasts (
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
                http_etag TEXT DEFAULT '',
                http_modified TEXT DEFAULT ''
            );
            CREATE TABLE episodes (
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
            CREATE TABLE queue (id INTEGER PRIMARY KEY, episode_id INTEGER UNIQUE NOT NULL, position INTEGER NOT NULL);
            CREATE TABLE local_queue (episode_id INTEGER PRIMARY KEY, position INTEGER NOT NULL);
            CREATE TABLE local_files (
                path TEXT PRIMARY KEY,
                episode_id INTEGER UNIQUE,
                size INTEGER NOT NULL,
                modified TEXT NOT NULL,
                available INTEGER NOT NULL DEFAULT 0,
                parent_dir TEXT NOT NULL DEFAULT '',
                file_name TEXT NOT NULL DEFAULT '',
                leading_num INTEGER,
                mtime_secs INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE playlist_episodes (
                id INTEGER PRIMARY KEY,
                playlist_id INTEGER NOT NULL,
                episode_uuid TEXT NOT NULL,
                podcast_uuid TEXT DEFAULT '',
                title TEXT DEFAULT '',
                audio_url TEXT DEFAULT '',
                published_at INTEGER DEFAULT 0,
                position INTEGER NOT NULL,
                added_at INTEGER DEFAULT 0
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE listening_days (day TEXT PRIMARY KEY, milliseconds INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE ios_listening_days (day TEXT PRIMARY KEY, milliseconds INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL DEFAULT 0);
            PRAGMA user_version = 3;",
        )
        .unwrap();
    }

    let db = Db::open(&db_path).unwrap();
    let version: i32 = db
        .conn
        .query_row("PRAGMA user_version", [], |r| r.get(0))
        .unwrap();
    assert_eq!(version, 4);
    let cols: Vec<String> = db
        .conn
        .prepare("PRAGMA table_info(podcasts)")
        .unwrap()
        .query_map([], |r| r.get(1))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    assert!(cols.iter().any(|c| c == "subscribed"));
    assert!(cols.iter().any(|c| c == "episodes_sort"));
    assert!(cols.iter().any(|c| c == "show_archived"));
    let starred_idx: bool = db
        .conn
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_episodes_starred')",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let downloaded_idx: bool = db
        .conn
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_episodes_downloaded')",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!(starred_idx);
    assert!(downloaded_idx);
}

#[test]
fn sort_up_next_reorders_queue_preserving_active_head_and_logging_sync_action() {
    use dusky_casts::db::UpNextSort;
    let mut f = Fixture::new();
    let a = f.id("a");
    let b = f.id("b");
    f.db.conn
        .execute(
            "INSERT INTO episodes(uuid,podcast_id,title,audio_url,published_at,duration,added_at) VALUES ('c',1,'Gamma','https://example.invalid/c.mp3',1791158400,50,1)",
            [],
        )
        .unwrap();
    f.db.conn
        .execute(
            "INSERT INTO episodes(uuid,podcast_id,title,audio_url,published_at,duration,playing_status,position,added_at) VALUES ('d',1,'Delta','https://example.invalid/d.mp3',1790985600,400,1,380000,1)",
            [],
        )
        .unwrap();
    let c = f.id("c");
    let d = f.id("d");

    f.db.edit_queue_batch(&[a, b, c, d], 3).unwrap();
    f.db.active(Some(a)).unwrap();
    f.db.sort_up_next(UpNextSort::Newest).unwrap();
    assert_eq!(f.db.queue().unwrap(), vec![a, c, d, b]);
    let last_action: i64 =
        f.db.conn
            .query_row(
                "SELECT action FROM queue_actions ORDER BY id DESC LIMIT 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
    assert_eq!(last_action, 5);

    f.db.active(None).unwrap();
    f.db.sort_up_next(UpNextSort::Shortest).unwrap();
    assert_eq!(f.db.queue().unwrap(), vec![d, c, a, b]);

    f.db.sort_up_next(UpNextSort::Longest).unwrap();
    assert_eq!(f.db.queue().unwrap(), vec![b, a, c, d]);

    f.db.sort_up_next(UpNextSort::Oldest).unwrap();
    assert_eq!(f.db.queue().unwrap(), vec![a, d, c, b]);
}

#[test]
fn podcast_summaries_and_per_podcast_filters_and_sort() {
    let mut f = Fixture::new();
    let a = f.id("a");
    let b = f.id("b");
    f.db.conn
        .execute(
            "INSERT INTO episodes(uuid,podcast_id,title,audio_url,published_at,duration,archived,added_at) VALUES ('c',1,'Archived Ep','https://example.invalid/c.mp3',1790000000,50,1,1)",
            [],
        )
        .unwrap();
    let c = f.id("c");
    f.db.change(a, "playing_status", 2).unwrap();

    let summaries = f.db.podcast_summaries().unwrap();
    assert_eq!(summaries.len(), 1);
    let pid = summaries[0].id;
    assert_eq!(summaries[0].episode_count, 3);
    assert_eq!(summaries[0].archived_count, 1);
    assert_eq!(summaries[0].unplayed_count, 1);

    f.db.set_podcast_sort(pid, 2).unwrap();
    f.db.set_podcast_show_archived(pid, true).unwrap();
    let updated = f.db.podcast_by_id(pid).unwrap().unwrap();
    assert_eq!(updated.episodes_sort, 2);
    assert!(updated.show_archived);

    assert_eq!(
        f.db.podcast_episode_ids(pid, "All", "", 2).unwrap(),
        vec![c, a, b]
    );
    assert_eq!(
        f.db.podcast_episode_ids(pid, "Unplayed", "", 0).unwrap(),
        vec![b]
    );
    assert_eq!(
        f.db.podcast_episode_ids(pid, "Archived", "", 0).unwrap(),
        vec![c]
    );
}
