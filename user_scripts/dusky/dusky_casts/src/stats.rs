//! Phone-compatible account statistics. Only cumulative seconds are transmitted.
use crate::{
    api::Api,
    db::{Db, now},
    wire::{
        api::{StatsRequest, StatsResponse},
        sync::{Int32Value, Int64Value, StringValue, SyncUserDevice},
    },
};
use anyhow::{Context, Result};
use chrono::TimeZone;

pub fn total_ms(db: &Db) -> Result<i64> {
    let recorded: i64 = db.conn.query_row(
        "SELECT coalesce(sum(milliseconds),0) FROM listening_days",
        [],
        |r| r.get(0),
    )?;
    Ok(recorded.saturating_add(db.number("stats_restored_ms")?.max(0)))
}

pub struct Snapshot {
    pub record: SyncUserDevice,
    pub seconds: i64,
}
pub fn snapshot(db: &Db) -> Result<Option<Snapshot>> {
    let seconds = total_ms(db)? / 1000;
    if seconds < 1 || seconds <= db.number("stats_ack_seconds")? {
        return Ok(None);
    }
    let mut started = db.number("stats_started_at")?;
    if started == 0 {
        // Migrate the first recorded calendar day; older releases did not keep
        // an exact start instant. New installations begin on the first listen.
        let day: Option<String> =
            db.conn
                .query_row("SELECT min(day) FROM listening_days", [], |r| r.get(0))?;
        started = day
            .and_then(|day| chrono::NaiveDate::parse_from_str(&day, "%Y-%m-%d").ok())
            .and_then(|day| day.and_hms_opt(0, 0, 0))
            .and_then(|day| chrono::Local.from_local_datetime(&day).earliest())
            .map(|day| day.timestamp())
            .unwrap_or(now() / 1000);
        db.set("stats_started_at", &started.to_string())?;
    }
    Ok(Some(Snapshot {
        seconds,
        record: SyncUserDevice {
            device_id: Some(StringValue {
                value: db.get("sync_device_id")?,
            }),
            device_type: Some(Int32Value { value: 1 }), // existing mobile protocol, as on the phone
            times_started_at: Some(Int64Value { value: started }),
            time_listened: Some(Int64Value { value: seconds }),
            // No fabricated silence/speed/skip savings. Absent fields leave
            // those server counters untouched.
            ..Default::default()
        },
    }))
}

pub fn acknowledge(db: &Db, snapshot: &Snapshot) -> Result<()> {
    // Called inside the same transaction as the episode cursor. New playback
    // during the request remains above this watermark for the next sync.
    let seconds = db.number("stats_ack_seconds")?.max(snapshot.seconds);
    db.set("stats_ack_seconds", &seconds.to_string())
}

pub fn apply_device(db: &Db, record: &SyncUserDevice) -> Result<()> {
    // The phone treats device responses as scoped to the requesting device;
    // optional identity wrappers may be absent. Ignore explicitly foreign IDs.
    if let Some(id) = &record.device_id
        && !id.value.is_empty()
        && id.value != db.get("sync_device_id")?
    {
        return Ok(());
    }
    if let Some(remote) = &record.time_listened {
        // Like StatsManager's remote restoration, preserve a higher cumulative
        // counter for this installation. Never put other devices into this counter.
        let missing = remote
            .value
            .max(0)
            .saturating_mul(1000)
            .saturating_sub(total_ms(db)?);
        if missing > 0 {
            db.set(
                "stats_restored_ms",
                &db.number("stats_restored_ms")?
                    .saturating_add(missing)
                    .to_string(),
            )?;
        }
    }
    Ok(())
}

pub fn refresh_summary(db: &mut Db, api: &Api) -> Result<()> {
    let request = StatsRequest {
        device_id: db.get("sync_device_id")?,
        device_type: 1,
        ..Default::default()
    };
    // RetrieveStatsTask excludes this device. Add the live PC counter in the UI
    // exactly once, instead of combining it with an inclusive account response.
    let response: StatsResponse = api
        .post("user/stats/summary", &request)?
        .context("No listening summary returned")?;
    anyhow::ensure!(response.time_listened >= 0, "Invalid listening summary");
    let tx = db.conn.transaction()?;
    let token: String = tx.query_row(
        "SELECT coalesce((SELECT value FROM settings WHERE key='account_token'),'')",
        [],
        |r| r.get(0),
    )?;
    anyhow::ensure!(
        token == api.token,
        "Account changed while fetching listening summary"
    );
    for (key, value) in [
        ("stats_other_seconds", response.time_listened.to_string()),
        ("stats_summary_at", (now() / 1000).to_string()),
    ] {
        tx.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",rusqlite::params![key,value])?;
    }
    tx.commit()?;
    Ok(())
}
