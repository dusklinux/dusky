//! Configuration schema and parser for Dusky Center.
//!
//! Deserializes `dusky_config.toml` verbatim with serde.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

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

    /// Resolve editable configuration at runtime, including relocated installations.
    pub fn load() -> Result<Self, String> {
        let mut candidates = vec![
            PathBuf::from("dusky_config.toml"),
            dirs_fallback().join("dusky_config.toml"),
        ];
        if let Some(home) = std::env::var_os("HOME").filter(|home| !home.is_empty()) {
            candidates.push(PathBuf::from(home)
                .join("user_scripts/dusky_system/dusky_center/dusky_config.toml"));
        }
        if let Ok(executable) = std::env::current_exe()
            && let Some(parent) = executable.parent()
        {
            candidates.push(parent.join("dusky_config.toml"));
        }

        candidates.push(PathBuf::from("/usr/share/dusky-center/dusky_config.toml"));

        for candidate in &candidates {
            if candidate.is_file() {
                return Self::load_from_path(candidate);
            }
        }

        Err(format!(
            "Could not find dusky_config.toml in any search location: {:?}",
            candidates
        ))
    }
}

fn dirs_fallback() -> PathBuf {
    if let Ok(config_home) = std::env::var("XDG_CONFIG_HOME")
        && !config_home.is_empty()
    {
        return PathBuf::from(config_home).join("dusky");
    }
    if let Ok(home) = std::env::var("HOME") {
        return PathBuf::from(home).join(".config/dusky");
    }
    PathBuf::from("/tmp")
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
        let config = AppConfig::load_from_path(&PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("dusky_config.toml")).unwrap();
        config.validate().unwrap();
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
