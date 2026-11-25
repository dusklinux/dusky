use dusky_casts::{db::Db, local};
use std::{io::Write, path::Path};

fn wave(path: &Path) {
    let samples = 8000u32;
    let mut f = std::fs::File::create(path).unwrap();
    f.write_all(b"RIFF").unwrap();
    f.write_all(&(samples * 2 + 36).to_le_bytes()).unwrap();
    f.write_all(b"WAVEfmt ").unwrap();
    f.write_all(&16u32.to_le_bytes()).unwrap();
    f.write_all(&1u16.to_le_bytes()).unwrap();
    f.write_all(&1u16.to_le_bytes()).unwrap();
    f.write_all(&8000u32.to_le_bytes()).unwrap();
    f.write_all(&16000u32.to_le_bytes()).unwrap();
    f.write_all(&2u16.to_le_bytes()).unwrap();
    f.write_all(&16u16.to_le_bytes()).unwrap();
    f.write_all(b"data").unwrap();
    f.write_all(&(samples * 2).to_le_bytes()).unwrap();
    for i in 0..samples {
        f.write_all(&((i % 128) as i16 * 100).to_le_bytes())
            .unwrap();
    }
}

#[test]
fn recursive_mpv_scan_cache_progress_and_library_switch() {
    use std::os::unix::{ffi::OsStringExt, fs::symlink};
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("Audio with spaces # 音");
    std::fs::create_dir_all(root.join("nested")).unwrap();
    wave(&root.join("UPPER.WAV"));
    wave(&root.join("nested/extensionless"));
    wave(&root.join(std::ffi::OsString::from_vec(b"nonutf-\xff.wav".to_vec())));
    std::fs::write(root.join("notes.txt"), "not audio").unwrap();
    symlink(&root, root.join("nested/cycle")).unwrap();
    let mut db = Db::open(dir.path().join("db/library.db")).unwrap();
    let scanned = local::scan(&mut db, root.to_str().unwrap()).unwrap();
    assert_eq!(scanned.audio, 3);
    assert_eq!(scanned.probed, 4);
    assert_eq!(scanned.skipped, 1);
    let episodes = db.episodes("Local files", "", 0, 60, 0).unwrap();
    assert_eq!(episodes.len(), 3);
    assert_eq!(
        db.episodes("Local files", "nested/", 0, 60, 0)
            .unwrap()
            .len(),
        1
    );
    assert!(episodes.iter().all(|e| e.is_local() && e.duration == 1));
    let id = episodes[0].id;
    db.change(id, "starred", 1).unwrap();
    db.change(id, "playing_status", 1).unwrap();
    db.progress(id, 0.5, 1.0).unwrap();
    let cached = local::scan(&mut db, root.to_str().unwrap()).unwrap();
    assert_eq!(cached.probed, 0);
    assert_eq!(db.episode(id).unwrap().position, 500);
    assert!(db.episode(id).unwrap().starred);
    // Changed files are re-probed without replacing their saved state.
    std::fs::OpenOptions::new()
        .append(true)
        .open(root.join("UPPER.WAV"))
        .unwrap()
        .write_all(&[0, 0])
        .unwrap();
    assert_eq!(
        local::scan(&mut db, root.to_str().unwrap()).unwrap().probed,
        1
    );
    let configured = db.get("local_library_path").unwrap();
    assert!(local::scan(&mut db, "/a-missing-dusky_casts-fixture-directory").is_err());
    assert_eq!(db.get("local_library_path").unwrap(), configured);
    assert_eq!(db.episodes("Local files", "", 0, 60, 0).unwrap().len(), 3);
    db.edit_queue(id, 3).unwrap();
    let other = dir.path().join("Other");
    std::fs::create_dir(&other).unwrap();
    local::scan(&mut db, other.to_str().unwrap()).unwrap();
    assert!(db.episodes("Local files", "", 0, 60, 0).unwrap().is_empty());
    assert!(db.queue_for(true).unwrap().is_empty());
    local::scan(&mut db, root.to_str().unwrap()).unwrap();
    assert_eq!(db.episode(id).unwrap().position, 500);
    std::fs::remove_file(root.join("nested/extensionless")).unwrap();
    local::scan(&mut db, root.to_str().unwrap()).unwrap();
    assert_eq!(db.episodes("Local files", "", 0, 60, 0).unwrap().len(), 2);
}

#[test]
fn local_queue_is_durable_and_never_creates_account_actions() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("audio");
    std::fs::create_dir(&root).unwrap();
    wave(&root.join("a.wav"));
    wave(&root.join("b.wav"));
    let path = dir.path().join("library.db");
    let mut db = Db::open(&path).unwrap();
    local::scan(&mut db, root.to_str().unwrap()).unwrap();
    let episodes = db.episodes("Local files", "", 0, 60, 0).unwrap();
    let (a, b) = (episodes[0].id, episodes[1].id);
    db.edit_queue(a, 3).unwrap();
    db.edit_queue(b, 2).unwrap();
    assert_eq!(db.queue_for(true).unwrap(), [b, a]);
    db.reorder_queue(&[a, b]).unwrap();
    db.edit_queue(a, 1).unwrap();
    assert_eq!(db.queue_for(true).unwrap(), [a, b]);
    assert!(db.queue().unwrap().is_empty());
    assert_eq!(
        db.conn
            .query_row("SELECT count(*) FROM queue_actions", [], |r| r
                .get::<_, i64>(0))
            .unwrap(),
        0
    );
    drop(db);
    let mut db = Db::open(path).unwrap();
    assert_eq!(db.queue_for(true).unwrap(), [a, b]);
    db.edit_queue(a, 4).unwrap();
    assert_eq!(db.queue_for(true).unwrap(), [b]);
}

#[test]
fn local_filename_and_path_sorts_are_natural_and_apply_to_queue() {
    let dir = tempfile::tempdir().unwrap();
    let mut db = Db::open(dir.path().join("library.db")).unwrap();
    db.conn.execute(
        "INSERT INTO podcasts(uuid,title,feed_url,added_at,subscribed) VALUES ('local-library','Local audio','local://library',0,0)",
        [],
    ).unwrap();
    let podcast: i64 = db
        .conn
        .query_row(
            "SELECT id FROM podcasts WHERE uuid='local-library'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    let paths = ["1180_1.m4a", "10.m4a", "2.m4a", "1180.m4a", "old/1.m4a"];
    let mut ids = Vec::new();
    for (index, path) in paths.iter().enumerate() {
        let url = format!("file:///fixture/{index}");
        db.conn.execute(
            "INSERT INTO episodes(uuid,podcast_id,title,description,audio_url,file_type,duration,added_at) VALUES (?1,?2,?3,?4,?5,'audio/local',60,0)",
            rusqlite::params![format!("local:fixture-{index}"), podcast, path, path, url],
        ).unwrap();
        let id: i64 = db
            .conn
            .query_row("SELECT last_insert_rowid()", [], |row| row.get(0))
            .unwrap();
        db.conn.execute(
            "INSERT INTO local_files(path,episode_id,size,modified,available) VALUES (?1,?2,1,'1',1)",
            rusqlite::params![url, id],
        ).unwrap();
        ids.push(id);
    }

    db.set("local_sort", "filename").unwrap();
    db.set("local_sort_asc", "1").unwrap();
    let descriptions = |db: &Db| {
        db.episodes("Local files", "", 0, 60, 0)
            .unwrap()
            .into_iter()
            .map(|episode| episode.description)
            .collect::<Vec<_>>()
    };
    assert_eq!(
        descriptions(&db),
        ["old/1.m4a", "2.m4a", "10.m4a", "1180.m4a", "1180_1.m4a"]
    );

    db.set("local_sort", "path").unwrap();
    assert_eq!(
        descriptions(&db),
        ["2.m4a", "10.m4a", "1180.m4a", "1180_1.m4a", "old/1.m4a"]
    );
    db.set_local_queue(&ids).unwrap();
    db.sort_local_queue().unwrap();
    assert_eq!(
        db.queue_for(true).unwrap(),
        descriptions(&db)
            .iter()
            .map(|description| {
                db.conn
                    .query_row(
                        "SELECT id FROM episodes WHERE description=?1",
                        [description],
                        |row| row.get::<_, i64>(0),
                    )
                    .unwrap()
            })
            .collect::<Vec<_>>()
    );
}

#[test]
#[ignore = "explicit 1,000-file mpv filesystem stress check"]
fn large_local_library_scan_and_cache() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("audio");
    std::fs::create_dir(&root).unwrap();
    let source = dir.path().join("source.wav");
    wave(&source);
    for index in 0..1000 {
        std::fs::hard_link(&source, root.join(format!("Track {index:04}"))).unwrap();
    }
    let mut db = Db::open(dir.path().join("library.db")).unwrap();
    let begin = std::time::Instant::now();
    let scan = local::scan(&mut db, root.to_str().unwrap()).unwrap();
    println!("First 1,000-file scan: {:?}", begin.elapsed());
    assert_eq!(scan.audio, 1000);
    assert_eq!(scan.probed, 1000);
    let begin = std::time::Instant::now();
    let cached = local::scan(&mut db, root.to_str().unwrap()).unwrap();
    println!("Cached 1,000-file scan: {:?}", begin.elapsed());
    assert_eq!(cached.audio, 1000);
    assert_eq!(cached.probed, 0);
    let mut seen = std::collections::HashSet::new();
    for page in 0..17 {
        for e in db.episodes("Local files", "", 0, 60, page * 60).unwrap() {
            assert!(seen.insert(e.id));
        }
    }
    assert_eq!(seen.len(), 1000);
    assert_eq!(
        db.conn
            .query_row("PRAGMA integrity_check", [], |r| r.get::<_, String>(0))
            .unwrap(),
        "ok"
    );
}

#[test]
fn ftp_stats_discovers_ios_manifest_in_ancestor_and_converges_export() {
    let dir = tempfile::tempdir().unwrap();
    let ftp_root = dir.path().join("ftp_root");
    let leaf = ftp_root.join("sub/episodes");
    std::fs::create_dir_all(&leaf).unwrap();
    wave(&leaf.join("ep01.wav"));

    let mut db = Db::open(dir.path().join("data/library.db")).unwrap();
    local::scan(&mut db, leaf.to_str().unwrap()).unwrap();
    db.conn
        .execute(
            "INSERT INTO listening_days(day,milliseconds) VALUES ('2026-11-20', 600000)",
            [],
        )
        .unwrap();

    let ios_manifest = serde_json::json!({
        "device_id": "iphone-ancestor-test",
        "device_name": "iPhone",
        "updated_at": 1763600000,
        "days_seconds": { "2026-11-20": 900.0 }
    });
    let ios_file = ftp_root.join(dusky_casts::listening::IOS_STATS_FILENAME);
    std::fs::write(&ios_file, serde_json::to_vec(&ios_manifest).unwrap()).unwrap();

    let report = dusky_casts::listening::sync_local_stats_files_report(&mut db).unwrap();
    assert_eq!(report.imported_days, 1);
    assert_eq!(report.updated_days, 1);
    assert!(report.pc_exported);
    assert!(report.ios_sources.iter().any(|p| p == &ios_file));
    assert!(
        ftp_root
            .join(dusky_casts::listening::PC_STATS_FILENAME)
            .is_file()
    );
    assert_eq!(
        db.get("discovered_ftp_stats_dir").unwrap(),
        ftp_root.canonicalize().unwrap().to_str().unwrap()
    );
    assert_eq!(
        db.ios_listening_days_all().unwrap(),
        vec![("2026-11-20".to_owned(), 900_000)]
    );
    assert_eq!(dusky_casts::stats::total_ms(&db).unwrap(), 600_000);
}

#[test]
fn ftp_stats_dir_override_works_while_locked_drive_is_skipped() {
    let dir = tempfile::tempdir().unwrap();
    let override_dir = dir.path().join("override_ftp");
    let locked_root = dir.path().join("locked_mount");
    let locked_leaf = locked_root.join("episodes");
    std::fs::create_dir_all(&override_dir).unwrap();
    std::fs::create_dir_all(&locked_leaf).unwrap();

    let mut db = Db::open(dir.path().join("data/library.db")).unwrap();
    db.set("ftp_stats_dir", override_dir.to_str().unwrap())
        .unwrap();
    db.set("local_library_path", locked_leaf.to_str().unwrap())
        .unwrap();
    db.set("local_library_dev", "999999999").unwrap();
    db.conn
        .execute(
            "INSERT INTO podcasts(uuid,title,feed_url,added_at,subscribed) VALUES ('local-library','Local audio','local://library',0,0)",
            [],
        )
        .unwrap();
    let missing_url = reqwest::Url::from_file_path(locked_leaf.join("missing.wav"))
        .unwrap()
        .to_string();
    db.conn
        .execute(
            "INSERT INTO episodes(uuid,podcast_id,title,audio_url,added_at) SELECT 'local:locked',id,'Locked',?,0 FROM podcasts WHERE uuid='local-library'",
            [&missing_url],
        )
        .unwrap();
    let ep_id = db.conn.last_insert_rowid();
    db.conn
        .execute(
            "INSERT INTO local_files(path,episode_id,size,modified,available) VALUES (?,?,1024,'1',1)",
            rusqlite::params![missing_url, ep_id],
        )
        .unwrap();
    db.conn
        .execute(
            "INSERT INTO listening_days(day,milliseconds) VALUES ('2026-11-21', 300000)",
            [],
        )
        .unwrap();

    std::fs::write(
        override_dir.join(dusky_casts::listening::IOS_STATS_FILENAME),
        serde_json::to_vec(&serde_json::json!({
            "days_seconds": { "2026-11-21": 450.0 }
        }))
        .unwrap(),
    )
    .unwrap();
    std::fs::write(
        locked_root.join(dusky_casts::listening::IOS_STATS_FILENAME),
        serde_json::to_vec(&serde_json::json!({
            "days_seconds": { "2026-11-22": 999.0 }
        }))
        .unwrap(),
    )
    .unwrap();

    let report = dusky_casts::listening::sync_local_stats_files_report(&mut db).unwrap();
    assert_eq!(report.imported_days, 1);
    assert!(
        override_dir
            .join(dusky_casts::listening::PC_STATS_FILENAME)
            .is_file()
    );
    assert!(
        !locked_leaf
            .join(dusky_casts::listening::PC_STATS_FILENAME)
            .is_file()
    );
    assert_eq!(
        db.ios_listening_days_all().unwrap(),
        vec![("2026-11-21".to_owned(), 450_000)]
    );
}

#[test]
fn local_stats_sync_ingests_legacy_ios_and_exports_primary_pc_only() {
    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("dusky_casts/library.db");
    let ftp_dir = dir.path().join("ftp_sync");
    std::fs::create_dir_all(&ftp_dir).unwrap();

    let mut db = Db::open(&db_path).unwrap();
    db.set("ftp_stats_dir", ftp_dir.to_string_lossy().as_ref())
        .unwrap();

    // Plant a legacy iPhone manifest written by the un-updated iOS app.
    let legacy_ios_path = ftp_dir.join(dusky_casts::listening::LEGACY_IOS_STATS_FILENAME);
    let legacy_payload = serde_json::json!({
        "device_id": "iphone-legacy-uuid",
        "device_name": "iPhone",
        "updated_at": 1_764_000_000_i64,
        "days_seconds": {
            "2026-11-24": 1800.0
        }
    });
    std::fs::write(
        &legacy_ios_path,
        serde_json::to_vec_pretty(&legacy_payload).unwrap(),
    )
    .unwrap();

    let report = dusky_casts::listening::sync_local_stats_files_report(&mut db).unwrap();
    assert_eq!(report.imported_days, 1);
    assert!(report.ios_sources.iter().any(|p| p == &legacy_ios_path));

    let pc_primary = ftp_dir.join(dusky_casts::listening::PC_STATS_FILENAME);
    let pc_legacy = ftp_dir.join(".duskcasts-stats-pc.json");
    assert!(
        pc_primary.is_file(),
        "expected primary .dusky_casts-stats-pc.json export"
    );
    assert!(
        !pc_legacy.exists(),
        "must not write legacy .duskcasts-stats-pc.json"
    );

    let exported: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&pc_primary).unwrap()).unwrap();
    assert_eq!(exported["device_name"], "Dusky Casts PC");
}
