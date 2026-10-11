use std::{collections::HashMap, time::Duration};
use zbus::{connection::Builder, proxy::Proxy, zvariant::Value};
/// Notifications never block advertisement processing.
pub struct NotificationClient {
    proxy: Option<Proxy<'static>>,
}
impl NotificationClient {
    pub async fn new(enabled: bool) -> Self {
        let proxy = if enabled {
            match Builder::session() {
                Ok(b) => match b.method_timeout(Duration::from_secs(2)).build().await {
                    Ok(conn) => Proxy::new(
                        &conn,
                        "org.freedesktop.Notifications",
                        "/org/freedesktop/Notifications",
                        "org.freedesktop.Notifications",
                    )
                    .await
                    .ok(),
                    Err(_) => None,
                },
                Err(_) => None,
            }
        } else {
            None
        };
        Self { proxy }
    }
    pub fn notify(&self, summary: &str, body: &str, icon: &str) {
        let Some(proxy) = self.proxy.clone() else {
            return;
        };
        let (summary, body, icon) = (summary.to_owned(), body.to_owned(), icon.to_owned());
        tokio::spawn(async move {
            let hints: HashMap<&str, Value<'_>> = HashMap::new();
            let actions: Vec<String> = Vec::new();
            if let Err(e) = proxy
                .call::<_, _, u32>(
                    "Notify",
                    &(
                        "Dusky Proximity",
                        0u32,
                        icon,
                        summary,
                        body,
                        actions,
                        hints,
                        3000i32,
                    ),
                )
                .await
            {
                eprintln!("[notification] {e}");
            }
        });
    }
}
