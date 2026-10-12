//! Four blocking workers, owned by this invocation. Keep subprocess waits out
//! of Iced's stream executor so one slow device cannot stall unrelated tasks.

use iced_futures::futures::{channel::oneshot, executor::ThreadPool};
use iced_runtime::Task;
use std::sync::OnceLock;

fn pool() -> Result<&'static ThreadPool, std::io::Error> {
    static POOL: OnceLock<Result<ThreadPool, String>> = OnceLock::new();
    POOL.get_or_init(|| {
        ThreadPool::builder()
            .pool_size(4)
            .name_prefix("tray-query")
            .create()
            .map_err(|error| error.to_string())
    })
    .as_ref()
    .map_err(|error| std::io::Error::other(error.clone()))
}

pub fn initialize() -> Result<(), std::io::Error> {
    pool().map(|_| ())
}

fn submit<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> oneshot::Receiver<T> {
    let (sender, receiver) = oneshot::channel();
    pool()
        .expect("blocking worker pool initialized")
        .spawn_ok(async move {
            // Dropped tasks need no queued work; an already running command retains
            // its timeout and Linux parent-death cleanup when the tray exits.
            if !sender.is_canceled() {
                let _ = sender.send(work());
            }
        });
    receiver
}

/// Start read-only boot queries now, overlapping graphics initialization.
pub fn start<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> Task<T> {
    let receiver = submit(work);
    Task::future(async move { receiver.await.expect("blocking worker completed") })
}

/// Defer actions until Iced runs the returned task.
pub fn perform<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> Task<T> {
    Task::future(async move { submit(work).await.expect("blocking worker completed") })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Condvar, Mutex, mpsc};
    use std::time::Duration;

    #[test]
    fn four_blocked_jobs_run_concurrently_and_fifth_waits() {
        let gate = Arc::new((Mutex::new(false), Condvar::new()));
        let (started, observed) = mpsc::channel();
        let jobs: Vec<_> = (0..4)
            .map(|id| {
                let gate = gate.clone();
                let started = started.clone();
                submit(move || {
                    started.send(id).unwrap();
                    let (lock, wake) = &*gate;
                    let _guard = wake
                        .wait_while(lock.lock().unwrap(), |open| !*open)
                        .unwrap();
                    id
                })
            })
            .collect();
        let all_started = (0..4).all(|_| observed.recv_timeout(Duration::from_secs(2)).is_ok());
        let (canceled_sender, canceled_receiver) = mpsc::channel();
        drop(submit(move || canceled_sender.send(()).unwrap()));
        let fifth = submit(move || started.send(4).unwrap());
        let fifth_waited = observed.recv_timeout(Duration::from_millis(50)).is_err();
        // Always release the fixture, including when a concurrency assertion fails.
        *gate.0.lock().unwrap() = true;
        gate.1.notify_all();
        iced_futures::futures::executor::block_on(async {
            for (id, job) in jobs.into_iter().enumerate() {
                assert_eq!(job.await.unwrap(), id);
            }
            fifth.await.unwrap();
        });
        assert!(
            all_started,
            "four blocking jobs must make independent progress"
        );
        assert!(
            fifth_waited,
            "blocking concurrency must stay bounded to four"
        );
        assert_eq!(observed.recv_timeout(Duration::from_secs(2)).unwrap(), 4);
        assert!(
            matches!(
                canceled_receiver.recv_timeout(Duration::from_secs(2)),
                Err(mpsc::RecvTimeoutError::Disconnected)
            ),
            "a discarded queued job must not run"
        );
    }
}
