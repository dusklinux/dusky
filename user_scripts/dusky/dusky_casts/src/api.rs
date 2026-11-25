use anyhow::{Context, Result, bail};
use prost::Message;
use reqwest::blocking::Client;
use serde_json::Value;
use std::{io::Read, time::Duration};
static HTTP: std::sync::OnceLock<Client> = std::sync::OnceLock::new();

#[derive(Clone)]
pub struct Api {
    pub http: Client,
    pub base: String,
    pub cache: String,
    pub token: String,
}
impl Api {
    pub fn new(token: String) -> Result<Self> {
        let http = if let Some(client) = HTTP.get() {
            client.clone()
        } else {
            let client = Client::builder()
                .connect_timeout(Duration::from_secs(10))
                .timeout(Duration::from_secs(45))
                .user_agent(concat!(
                    "DuskyCasts/",
                    env!("CARGO_PKG_VERSION"),
                    " (Linux)"
                ))
                .build()?;
            let _ = HTTP.set(client);
            HTTP.get().expect("HTTP pool initialized").clone()
        };
        Ok(Self {
            http,
            base: std::env::var("DUSKY_CASTS_API_BASE")
                .unwrap_or("https://api.pocketcasts.com/".into()),
            cache: std::env::var("DUSKY_CASTS_CACHE_BASE")
                .unwrap_or("https://cache.pocketcasts.com/".into()),
            token,
        })
    }
    pub fn post<Q: Message, R: Message + Default>(
        &self,
        path: &str,
        request: &Q,
    ) -> Result<Option<R>> {
        let mut req = self
            .http
            .post(format!(
                "{}{path}",
                self.base.trim_end_matches('/').to_owned() + "/"
            ))
            .header("Content-Type", "application/octet-stream")
            .header("Accept", "application/octet-stream")
            .body(request.encode_to_vec());
        if !self.token.is_empty() {
            req = req.bearer_auth(&self.token);
        }
        let response = req
            .send()
            .with_context(|| format!("{path}: connection failed"))?;
        let status = response.status();
        if status.as_u16() == 304 {
            return Ok(None);
        }
        if !status.is_success() {
            // Do not include arbitrary response bodies: proxies may echo credentials.
            bail!(
                "{path}: HTTP {}{}",
                status.as_u16(),
                if matches!(status.as_u16(), 401 | 403) {
                    "; sign in again"
                } else {
                    ""
                }
            );
        }
        let mut bytes = Vec::new();
        response
            .take(64 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() > 64 * 1024 * 1024 {
            bail!("{path}: response exceeds 64 MiB")
        }
        Ok(Some(R::decode(bytes.as_slice()).with_context(|| {
            format!("{path}: invalid protobuf response")
        })?))
    }
    pub fn login(
        &self,
        email: String,
        password: String,
    ) -> Result<crate::wire::api::UserLoginResponse> {
        use crate::wire::api::*;
        let request = UserLoginRequest {
            email,
            password,
            scope: "mobile".into(),
            v: env!("CARGO_PKG_VERSION").into(),
            m: "Linux".into(),
            av: "2".into(),
            f: "0".into(),
            l: "en".into(),
            ..Default::default()
        };
        let mut response: UserLoginResponse = self
            .post("user/login", &request)?
            .context("Login returned no account")?;
        if response.token.is_empty() {
            bail!("Login returned no token")
        }
        if response.email.is_empty() {
            response.email = request.email;
        }
        Ok(response)
    }
    pub fn json_get(&self, url: &str, authenticated: bool) -> Result<Value> {
        self.json_get_timeout(url, authenticated, Duration::from_secs(45))
    }
    fn json_get_timeout(&self, url: &str, authenticated: bool, timeout: Duration) -> Result<Value> {
        let mut req = self.http.get(url).timeout(timeout);
        if authenticated {
            req = req.bearer_auth(&self.token)
        }
        let mut bytes = Vec::new();
        req.send()?
            .error_for_status()?
            .take(64 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        anyhow::ensure!(
            bytes.len() <= 64 * 1024 * 1024,
            "JSON response exceeds 64 MiB"
        );
        Ok(serde_json::from_slice(&bytes)?)
    }
    pub fn podcast(&self, id: &str) -> Result<Value> {
        self.json_get_timeout(
            &format!(
                "{}/mobile/podcast/full/{}",
                self.cache.trim_end_matches('/'),
                segment(id)
            ),
            true,
            Duration::from_secs(45),
        )
    }
    pub fn historical(&self, podcast: &str, episode: &str) -> Result<Value> {
        self.json_get_timeout(
            &format!(
                "{}/mobile/podcast/findbyepisode/{}/{}",
                self.cache.trim_end_matches('/'),
                segment(podcast),
                segment(episode)
            ),
            true,
            Duration::from_secs(5),
        )
    }
}
fn segment(s: &str) -> String {
    reqwest::Url::parse("https://cache.invalid/")
        .map(|mut u| {
            u.path_segments_mut().unwrap().push(s);
            u.path().trim_start_matches('/').to_owned()
        })
        .unwrap()
}
