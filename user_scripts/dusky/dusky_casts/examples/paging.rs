//! Focused before/after measurement of the global episode-order index.
use dusky_casts::db::Db;
use std::time::{Duration, Instant};
fn measure(db: &Db) -> Duration {
    let start = Instant::now();
    for page in 0..100 {
        let rows = db.episodes("All episodes", "", 0, 60, page * 60).unwrap();
        assert_eq!(rows.len(), 60);
        std::hint::black_box(rows);
    }
    start.elapsed()
}
fn main() -> anyhow::Result<()> {
    let dir = tempfile::tempdir()?;
    let mut db = Db::open(dir.path().join("library.db"))?;
    db.conn.execute("INSERT INTO podcasts(uuid,title,feed_url,added_at) VALUES ('bench','Benchmark','local://bench',1)",[])?;
    let tx = db.conn.transaction()?;
    {
        let mut insert=tx.prepare("INSERT INTO episodes(uuid,podcast_id,title,published_at,added_at) VALUES (?,1,'Benchmark episode',?,1)")?;
        for i in 0..50000 {
            insert.execute(rusqlite::params![format!("bench-{i}"), i])?;
        }
    }
    tx.commit()?;
    db.conn.execute("DROP INDEX idx_episodes_published", [])?;
    let _ = measure(&db);
    let mut before = (0..3).map(|_| measure(&db)).collect::<Vec<_>>();
    before.sort();
    db.conn.execute(
        "CREATE INDEX idx_episodes_published ON episodes(published_at DESC,id DESC)",
        [],
    )?;
    let _ = measure(&db);
    let mut after = (0..3).map(|_| measure(&db)).collect::<Vec<_>>();
    after.sort();
    println!(
        "50,000 episodes, 100 queries of 60 rows, median of three warm runs: without index {:?}; with index {:?}",
        before[1], after[1]
    );
    Ok(())
}
