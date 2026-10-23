//! Keep blocking capture/export jobs off Iced's timer and event executor.
use iced::futures::{channel::oneshot, executor::ThreadPool};
use std::sync::OnceLock;

fn pool() -> std::io::Result<&'static ThreadPool> {
    static POOL: OnceLock<std::result::Result<ThreadPool, String>> = OnceLock::new();
    POOL.get_or_init(|| {
        ThreadPool::builder()
            .pool_size(2)
            .name_prefix("snap-job")
            .create()
            .map_err(|e| e.to_string())
    })
    .as_ref()
    .map_err(|e| std::io::Error::other(e.clone()))
}

pub fn initialize() -> std::io::Result<()> {
    pool().map(|_| ())
}

pub fn perform<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> iced::Task<T> {
    iced::Task::future(async move {
        let (send, receive) = oneshot::channel();
        pool()
            .expect("blocking pool initialized")
            .spawn_ok(async move {
                if !send.is_canceled() {
                    let _ = send.send(work());
                }
            });
        receive.await.expect("blocking job completed")
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use iced::Executor as _;
    use std::{
        sync::{Arc, Barrier, mpsc},
        time::Duration,
    };

    #[test]
    fn timers_progress_while_both_blocking_workers_wait() {
        initialize().unwrap();
        let gate = Arc::new(Barrier::new(3));
        let (send, receive) = mpsc::channel();
        for _ in 0..2 {
            let gate = gate.clone();
            let send = send.clone();
            pool().unwrap().spawn_ok(async move {
                send.send(()).unwrap();
                gate.wait();
            });
        }
        let started = (0..2).all(|_| receive.recv_timeout(Duration::from_secs(2)).is_ok());
        let executor = crate::ui::BackgroundExecutor::new().unwrap();
        let (timer_send, timer_receive) = mpsc::channel();
        executor.spawn(async move {
            futures_timer::Delay::new(Duration::from_millis(20)).await;
            let _ = timer_send.send(());
        });
        let timer = timer_receive.recv_timeout(Duration::from_millis(500));
        gate.wait();
        assert!(started);
        assert!(timer.is_ok(), "Iced timer stalled behind blocking jobs");
    }
}
