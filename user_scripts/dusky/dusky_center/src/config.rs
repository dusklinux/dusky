//! Configuration schema and parser for Dusky Center.
//!
//! Deserializes `dusky_config.toml` verbatim with serde.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct LoadedConfig {
    pub config: AppConfig,
    pub path: PathBuf,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AppConfig {
    pub pages: Vec<PageConfig>,
}

impl AppConfig {
    /// Load configuration from the specified path.
    pub fn load_from_path(path: &Path) -> Result<Self, String> {
        let content = fs::read_to_string(path)
            .map_err(|e| format!("Failed to read {}: {e}", path.display()))?;
        let config: Self = toml::from_str(&content)
            .map_err(|e| format!("Failed to parse TOML in {}: {e}", path.display()))?;
        config.validate().map_err(|e| format!("Invalid configuration in {}: {e}", path.display()))?;
        Ok(config)
    }

    pub fn validate(&self) -> Result<(), String> {
        use std::collections::HashSet;
        if self.pages.is_empty() {
            return Err("Configuration requires at least one page".into());
        }
        let mut ids = HashSet::new();
        for page in &self.pages {
            if page.id.trim().is_empty() || page.title.trim().is_empty() {
                return Err("Pages require a nonempty id and title".into());
            }
            if !ids.insert(page.id.as_str()) { return Err(format!("Duplicate page id: {}", page.id)); }
        }
        fn action(action: &ActionConfig, ids: &HashSet<&str>) -> Result<(), String> {
            match action {
                ActionConfig::Exec { command, argv, mode, timeout, .. } => {
                    if command.trim().is_empty() == argv.is_empty() {
                        return Err("Specify exactly one nonempty command or argv".into());
                    }
                    if argv.first().is_some_and(|s| s.is_empty()) { return Err("argv requires an executable".into()); }
                    if !matches!(mode.as_str(), "" | "apply" | "launch") || *timeout == Some(0) {
                        return Err("Invalid action mode or timeout".into());
                    }
                }
                ActionConfig::Argv { argv, mode, timeout, .. } => {
                    if argv.first().is_none_or(|s| s.is_empty()) { return Err("argv requires an executable".into()); }
                    if !matches!(mode.as_str(), "" | "apply" | "launch") || *timeout == Some(0) {
                        return Err("Invalid action mode or timeout".into());
                    }
                }
                ActionConfig::Redirect { page } if !ids.contains(page.as_str()) => {
                    return Err(format!("Unknown redirect page: {page}"));
                }
                _ => (),
            }
            Ok(())
        }
        fn item(item: &ItemConfig, ids: &HashSet<&str>) -> Result<(), String> {
            let p = &item.properties;
            if p.interval == Some(0) { return Err("Interval must be positive".into()); }
            if !matches!(p.persistence.as_str(), "" | "app" | "action") { return Err("Invalid persistence mode".into()); }
            if matches!(item.item_type.as_str(), "slider" | "spin") {
                let (min, max, step) = (p.min.unwrap_or(0.0), p.max.unwrap_or(100.0), p.step.unwrap_or(1.0));
                let default = p.default.unwrap_or(min);
                if [min, max, step, default].iter().any(|v| !(*v as f32).is_finite())
                    || min as f32 >= max as f32 || step as f32 <= 0.0 || !(min..=max).contains(&default) {
                    return Err("Numeric controls require finite min < max, step > 0 and a default in range".into());
                }
            }
            for value in [&item.on_press, &item.on_action].into_iter().flatten() { action(value, ids)?; }
            if let Some(pair) = &item.on_toggle { action(&pair.enabled, ids)?; action(&pair.disabled, ids)?; }
            if let Some(change) = &item.on_change {
                match change {
                    ChangeAction::Direct(value) => action(value, ids)?,
                    ChangeAction::Map(map) => { for value in map.values() { action(value, ids)?; } }
                }
            }
            for button in &p.buttons { if let Some(value) = &button.on_press { action(value, ids)?; } }
            for child in &item.items { check_item(child, ids)?; }
            for section in &item.layout { for child in &section.items { check_item(child, ids)?; } }
            if let Some(template) = &item.item_template { check_item(template, ids)?; }
            Ok(())
        }
        fn check_item(value: &ItemConfig, ids: &HashSet<&str>) -> Result<(), String> {
            item(value, ids).map_err(|e| format!("{}: {e}", value.properties.title))
        }
        for page in &self.pages {
            for section in &page.layout { for value in &section.items { check_item(value, &ids)?; } }
        }
        Ok(())
    }

    /// Resolve configuration using an explicit `--config` path or the canonical fallback chain.
    pub fn load_resolved(override_path: Option<&Path>) -> Result<LoadedConfig, String> {
        let xdg_config_home = std::env::var_os("XDG_CONFIG_HOME");
        let home = std::env::var_os("HOME");
        let exe_path = std::env::current_exe().ok();
        let exe_dir = exe_path.as_deref().and_then(Path::parent);
        let candidates = candidate_paths(
            xdg_config_home.as_deref(),
            home.as_deref(),
            exe_dir,
        );
        Self::load_resolved_with(override_path, &candidates)
    }

    /// Resolve editable configuration at runtime using the canonical search chain.
    pub fn load() -> Result<Self, String> {
        Self::load_resolved(None).map(|loaded| loaded.config)
    }

    fn load_resolved_with(
        override_path: Option<&Path>,
        candidates: &[PathBuf],
    ) -> Result<LoadedConfig, String> {
        if let Some(path) = override_path {
            let config = Self::load_from_path(path)?;
            return Ok(LoadedConfig {
                config,
                path: path.to_path_buf(),
            });
        }

        for candidate in candidates {
            // Commit to the first present entry (including broken symlinks) so a broken
            // or malformed canonical config fails fast instead of silently loading a fallback.
            if fs::symlink_metadata(candidate).is_ok() {
                let config = Self::load_from_path(candidate)?;
                return Ok(LoadedConfig {
                    config,
                    path: candidate.clone(),
                });
            }
        }

        Err(format!(
            "Could not find dusky_config.toml in any search location: {candidates:?}"
        ))
    }
}

fn absolute_env_path(value: Option<&OsStr>) -> Option<PathBuf> {
    let raw = value.filter(|v| !v.is_empty())?;
    let path = PathBuf::from(raw);
    path.is_absolute().then_some(path)
}

fn push_unique(paths: &mut Vec<PathBuf>, candidate: PathBuf) {
    if !paths.contains(&candidate) {
        paths.push(candidate);
    }
}

fn candidate_paths(
    xdg_config_home: Option<&OsStr>,
    home: Option<&OsStr>,
    exe_dir: Option<&Path>,
) -> Vec<PathBuf> {
    let mut candidates = Vec::with_capacity(4);
    let abs_home = absolute_env_path(home);

    if let Some(xdg) = absolute_env_path(xdg_config_home) {
        push_unique(&mut candidates, xdg.join("dusky/dusky_config.toml"));
    } else if let Some(ref home_dir) = abs_home {
        push_unique(&mut candidates, home_dir.join(".config/dusky/dusky_config.toml"));
    }

    if let Some(ref home_dir) = abs_home {
        push_unique(
            &mut candidates,
            home_dir.join("user_scripts/dusky/dusky_center/dusky_config.toml"),
        );
    }

    push_unique(
        &mut candidates,
        PathBuf::from("/usr/share/dusky_center/dusky_config.toml"),
    );

    if let Some(dir) = exe_dir.filter(|p| p.is_absolute()) {
        push_unique(&mut candidates, dir.join("dusky_config.toml"));
    }

    candidates
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct PageConfig {
    pub id: String,
    pub title: String,
    #[serde(default)]
    pub icon: String,
    #[serde(default)]
    pub layout: Vec<SectionConfig>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SectionConfig {
    #[serde(rename = "type")]
    pub section_type: String,
    #[serde(default)]
    pub properties: ItemProperties,
    #[serde(default)]
    pub items: Vec<ItemConfig>,
}

#[derive(Debug, Clone, Deserialize, Serialize, Default)]
pub struct ItemProperties {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub icon: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub key: String,
    #[serde(default)]
    pub key_inverse: bool,
    #[serde(default)]
    pub save_as_int: bool,
    #[serde(default)]
    pub style: String,
    #[serde(default)]
    pub button_text: String,
    #[serde(default)]
    pub button_text_file: String,
    #[serde(default)]
    pub badge_file: String,
    #[serde(default)]
    pub button_text_map: HashMap<String, String>,
    #[serde(default)]
    pub style_map: HashMap<String, String>,
    #[serde(default)]
    pub options_map: HashMap<String, String>,
    #[serde(default)]
    pub options: Vec<String>,
    #[serde(default)]
    pub min: Option<f64>,
    #[serde(default)]
    pub max: Option<f64>,
    #[serde(default)]
    pub step: Option<f64>,
    #[serde(default)]
    pub default: Option<f64>,
    #[serde(default)]
    pub state_command: String,
    #[serde(default)]
    pub value_command: String,
    #[serde(default)]
    pub options_command: String,
    #[serde(default)]
    pub interval: Option<u64>,
    #[serde(default)]
    pub persistence: String,
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub glob: String,
    #[serde(default)]
    pub recursive: bool,
    #[serde(default)]
    pub sort: String,
    #[serde(default)]
    pub service: String,
    #[serde(default)]
    pub scope: String,
    #[serde(default)]
    pub toast: String,
    #[serde(default)]
    pub debounce: bool,
    #[serde(default)]
    pub compact: bool,
    #[serde(default)]
    pub center_title: bool,
    #[serde(default)]
    pub show_label: bool,
    #[serde(default)]
    pub buttons: Vec<ButtonProperty>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ButtonProperty {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub icon: String,
    #[serde(default)]
    pub style: String,
    pub on_press: Option<ActionConfig>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ToggleActionPair {
    pub enabled: ActionConfig,
    pub disabled: ActionConfig,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(untagged)]
pub enum ChangeAction {
    Direct(ActionConfig),
    Map(HashMap<String, ActionConfig>),
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ItemConfig {
    #[serde(rename = "type")]
    pub item_type: String,
    #[serde(default)]
    pub properties: ItemProperties,
    #[serde(default)]
    pub on_press: Option<ActionConfig>,
    #[serde(default)]
    pub on_toggle: Option<ToggleActionPair>,
    #[serde(default)]
    pub on_change: Option<ChangeAction>,
    #[serde(default)]
    pub on_action: Option<ActionConfig>,
    #[serde(default)]
    pub value: Option<ValueConfig>,
    #[serde(default)]
    pub items: Vec<ItemConfig>,
    #[serde(default)]
    pub layout: Vec<SectionConfig>,
    #[serde(default)]
    pub item_template: Option<Box<ItemConfig>>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "type")]
pub enum ActionConfig {
    #[serde(rename = "reload")]
    Reload,
    #[serde(rename = "exec")]
    Exec {
        #[serde(default)]
        command: String,
        #[serde(default)]
        argv: Vec<String>,
        // Configured paths are expanded before inserting literal submitted values.
        #[serde(skip)]
        expanded_argv: bool,
        #[serde(default)]
        terminal: bool,
        #[serde(default)]
        requires_root: bool,
        #[serde(default)]
        mode: String,
        #[serde(default)]
        timeout: Option<u64>,
    },
    #[serde(rename = "argv")]
    Argv {
        #[serde(default)]
        argv: Vec<String>,
        // Configured paths are expanded before inserting literal submitted values.
        #[serde(skip)]
        expanded_argv: bool,
        #[serde(default)]
        terminal: bool,
        #[serde(default)]
        requires_root: bool,
        #[serde(default)]
        mode: String,
        #[serde(default)]
        timeout: Option<u64>,
    },
    #[serde(rename = "redirect")]
    Redirect {
        page: String,
    },
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "type")]
pub enum ValueConfig {
    #[serde(rename = "exec")]
    Exec {
        #[serde(default)]
        command: String,
    },
    #[serde(rename = "file")]
    File {
        #[serde(default)]
        path: String,
    },
    #[serde(rename = "system")]
    System {
        #[serde(default)]
        key: String,
    },
    #[serde(rename = "static")]
    Static {
        #[serde(default)]
        text: String,
    },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn semantic_validation_rejects_broken_reload_inputs() {
        for (tail, expected) in [
            ("[[pages.layout.items]]\ntype='slider'\n[pages.layout.items.properties]\nmin=100\nmax=1", "Numeric"),
            ("[[pages.layout.items]]\ntype='button'\n[pages.layout.items.on_press]\ntype='exec'", "command or argv"),
            ("[[pages.layout.items]]\ntype='button'\n[pages.layout.items.on_press]\ntype='redirect'\npage='absent'", "Unknown redirect"),
            ("[[pages]]\nid='home'\ntitle='Duplicate'", "Duplicate page"),
        ] {
            let config: AppConfig = toml::from_str(&format!("[[pages]]\nid='home'\ntitle='Home'\n[[pages.layout]]\ntype='section'\n{tail}")).unwrap();
            assert!(config.validate().unwrap_err().contains(expected));
        }
        let empty = AppConfig { pages: vec![] };
        assert!(empty.validate().unwrap_err().contains("at least one page"));

        let config = AppConfig::load_from_path(&PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("dusky_config.toml")).unwrap();
        config.validate().unwrap();
    }

    #[test]
    fn candidate_paths_follow_canonical_order_and_ignore_relative_env() {
        let standard = candidate_paths(
            Some(OsStr::new("/xdg/cfg")),
            Some(OsStr::new("/home/tester")),
            Some(Path::new("/opt/dusky/bin")),
        );
        assert_eq!(
            standard,
            vec![
                PathBuf::from("/xdg/cfg/dusky/dusky_config.toml"),
                PathBuf::from("/home/tester/user_scripts/dusky/dusky_center/dusky_config.toml"),
                PathBuf::from("/usr/share/dusky_center/dusky_config.toml"),
                PathBuf::from("/opt/dusky/bin/dusky_config.toml"),
            ]
        );
        assert!(!standard.contains(&PathBuf::from("dusky_config.toml")));

        let relative_xdg = candidate_paths(
            Some(OsStr::new("relative/config")),
            Some(OsStr::new("/home/tester")),
            Some(Path::new("/usr/share/dusky_center")),
        );
        assert_eq!(
            relative_xdg,
            vec![
                PathBuf::from("/home/tester/.config/dusky/dusky_config.toml"),
                PathBuf::from("/home/tester/user_scripts/dusky/dusky_center/dusky_config.toml"),
                PathBuf::from("/usr/share/dusky_center/dusky_config.toml"),
            ]
        );

        let relative_home = candidate_paths(None, Some(OsStr::new("relative/home")), None);
        assert_eq!(
            relative_home,
            vec![PathBuf::from("/usr/share/dusky_center/dusky_config.toml")]
        );
    }

    #[test]
    fn load_resolved_prioritizes_override_and_xdg_and_fails_fast_on_broken_canonical() {
        let root = std::env::temp_dir().join(format!("dusky-config-res-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let xdg = root.join("xdg/dusky/dusky_config.toml");
        let repo = root.join("repo/dusky_config.toml");
        let custom = root.join("custom.toml");
        fs::create_dir_all(xdg.parent().unwrap()).unwrap();
        fs::create_dir_all(repo.parent().unwrap()).unwrap();

        fs::write(&repo, "[[pages]]\nid='repo'\ntitle='Repo'\n").unwrap();
        fs::write(&custom, "[[pages]]\nid='custom'\ntitle='Custom'\n").unwrap();

        let candidates = vec![xdg.clone(), repo.clone()];

        // Falls back to repo when XDG is absent.
        let from_repo = AppConfig::load_resolved_with(None, &candidates).unwrap();
        assert_eq!(from_repo.path, repo);
        assert_eq!(from_repo.config.pages[0].id, "repo");

        // XDG takes precedence over repo when present.
        fs::write(&xdg, "[[pages]]\nid='xdg'\ntitle='XDG'\n").unwrap();
        let from_xdg = AppConfig::load_resolved_with(None, &candidates).unwrap();
        assert_eq!(from_xdg.path, xdg);
        assert_eq!(from_xdg.config.pages[0].id, "xdg");

        // Explicit --config overrides XDG and fails fast when missing.
        let from_custom = AppConfig::load_resolved_with(Some(&custom), &candidates).unwrap();
        assert_eq!(from_custom.path, custom);
        assert_eq!(from_custom.config.pages[0].id, "custom");
        let missing = root.join("missing.toml");
        assert!(AppConfig::load_resolved_with(Some(&missing), &candidates).is_err());

        // Malformed XDG fails fast instead of silently falling through to repo.
        fs::write(&xdg, "pages = [").unwrap();
        assert!(
            AppConfig::load_resolved_with(None, &candidates)
                .unwrap_err()
                .contains(&xdg.display().to_string())
        );

        // Broken symlink at XDG fails fast instead of silently falling through to repo.
        fs::remove_file(&xdg).unwrap();
        std::os::unix::fs::symlink(root.join("dangling.toml"), &xdg).unwrap();
        assert!(
            AppConfig::load_resolved_with(None, &candidates)
                .unwrap_err()
                .contains(&xdg.display().to_string())
        );

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn test_load_full_config() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("dusky_config.toml");
        assert!(path.is_file(), "dusky_config.toml must exist");

        let config = AppConfig::load_from_path(&path).expect("Full config must parse cleanly");
        assert_eq!(config.pages.len(), 18, "Must contain all 18 configured pages");

        let home = &config.pages[0];
        assert_eq!(home.id, "home");
        assert_eq!(home.title, "Home");
        assert!(!home.layout.is_empty());

        fn count_items(items: &[ItemConfig]) -> usize {
            let mut count = items.len();
            for item in items {
                count += count_items(&item.items);
                for section in &item.layout {
                    count += count_items(&section.items);
                }
                if let Some(template) = &item.item_template {
                    count += 1 + count_items(&template.items);
                }
            }
            count
        }

        let total_items: usize = config
            .pages
            .iter()
            .flat_map(|p| &p.layout)
            .map(|s| count_items(&s.items))
            .sum();

        assert!(total_items >= 250, "Must have deserialized >= 250 total items, got {total_items}");
        println!("Successfully parsed {} pages with {} total items!", config.pages.len(), total_items);
    }
}
