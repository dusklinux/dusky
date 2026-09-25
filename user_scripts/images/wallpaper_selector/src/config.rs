use std::path::PathBuf;
use std::{fs, io};

#[derive(serde::Deserialize, serde::Serialize)]
#[serde(default)]
pub struct Preferences {
    pub animate_carousel: bool,
}

impl Default for Preferences {
    fn default() -> Self {
        Self {
            animate_carousel: true,
        }
    }
}

impl Preferences {
    pub fn load(path: &std::path::Path) -> Self {
        fs::read(path)
            .ok()
            .and_then(|data| serde_json::from_slice(&data).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, path: &std::path::Path) -> io::Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let temporary = path.with_extension(format!("tmp.{}", std::process::id()));
        let data = serde_json::to_vec_pretty(self).map_err(io::Error::other)?;
        fs::write(&temporary, data)?;
        if let Err(error) = fs::rename(&temporary, path) {
            let _ = fs::remove_file(&temporary);
            return Err(error);
        }
        Ok(())
    }
}

#[derive(Clone)]
#[allow(dead_code)]
pub struct Config {
    pub home: PathBuf,
    pub wallpaper_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub thumb_dir: PathBuf,
    pub theme_dir: PathBuf,
    pub fav_file: PathBuf,
    pub fav_state_file: PathBuf,
    pub track_dark: PathBuf,
    pub track_light: PathBuf,
    pub theme_ctl: PathBuf,
    pub preferences_file: PathBuf,
}

impl Config {
    pub fn load() -> Self {
        let home = std::env::var_os("HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .or_else(std::env::home_dir)
            .expect("Could not determine the user's home directory");

        let wallpaper_dir = home.join("Pictures/wallpapers");
        let cache_dir = home.join(".cache/dusky_images/wallpaper_selector_rust");
        let thumb_dir = cache_dir.join("thumbs");
        let theme_dir = home.join(".config/dusky/settings/dusky_theme");
        let fav_file = theme_dir.join("wal_fav_list");
        let fav_state_file = theme_dir.join("current_fav");
        let track_dark = theme_dir.join("dark_wal");
        let track_light = theme_dir.join("light_wal");
        let theme_ctl = home.join("user_scripts/theme_matugen/theme_ctl.sh");
        let preferences_file =
            home.join(".config/dusky/settings/wallpaper_selector_rust/preferences.json");

        Self {
            home,
            wallpaper_dir,
            cache_dir,
            thumb_dir,
            theme_dir,
            fav_file,
            fav_state_file,
            track_dark,
            track_light,
            theme_ctl,
            preferences_file,
        }
    }
}
