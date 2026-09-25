use std::path::PathBuf;

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
        }
    }
}
