//! Queries and verified writes for TOML controls. Called on executor workers.
use super::cmd::{atomic_write_text, run_command, run_shell};
use crate::config::{ActionConfig, ItemConfig, ValueConfig};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Duration,
};

pub fn item_key(item: &ItemConfig) -> String {
    if item.properties.key.is_empty() {
        item.properties.title.clone()
    } else {
        item.properties.key.clone()
    }
}

pub fn service_key(scope: &str, unit: &str) -> String {
    format!("{}:{unit}", if scope == "user" { "user" } else { "system" })
}

pub fn settings_dir() -> PathBuf {
    std::env::var_os("XDG_CONFIG_HOME")
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config")
        })
        .join("dusky/settings")
}

pub fn parse_bool(raw: &str) -> Option<bool> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "enabled" | "yes" | "true" | "1" | "on" | "active" | "set" | "running" | "open"
        | "high" => Some(true),
        "unavailable" | "" => None,
        _ => Some(false),
    }
}

#[derive(Debug, Clone)]
pub struct ServiceStatus {
    pub load: String,
    pub active: String,
    pub startup: String,
}
impl ServiceStatus {
    pub fn enabled(&self) -> bool {
        self.active == "active"
    }
    pub fn actionable(&self) -> bool {
        self.load == "loaded"
            && matches!(self.active.as_str(), "active" | "inactive" | "failed")
            && !matches!(
                self.startup.as_str(),
                "static" | "masked" | "masked-runtime"
            )
    }
    pub fn description(&self) -> String {
        format!(
            "Runtime: {} • Startup: {}",
            self.active,
            self.startup.replace('-', " ")
        )
    }
}

#[derive(Debug, Clone, Default)]
pub struct Snapshot {
    pub toggles: Vec<(String, Option<bool>)>,
    pub services: HashMap<String, ServiceStatus>,
    pub selections: Vec<(String, Option<String>)>,
    pub options: Vec<(String, Vec<String>)>,
    pub sliders: Vec<(String, f32)>,
    pub entries: Vec<(String, String)>,
    pub labels: Vec<(String, String)>,
}

pub fn query(items: &[ItemConfig]) -> Snapshot {
    let mut result = Snapshot::default();
    for item in items {
        let p = &item.properties;
        let key = item_key(item);
        if item.item_type == "label" {
            result.labels.push((key.clone(), label_value(item.value.as_ref()).unwrap_or_else(|| "N/A".into())));
        }
        if matches!(item.item_type.as_str(), "entry" | "secret")
            && let Some(raw) = scalar_value(item) {
            result.entries.push((key.clone(), raw));
        }
        if item.item_type == "selection" {
            let options = if p.options_command.is_empty() {
                Some(p.options.clone())
            } else {
                run_shell(&p.options_command, Duration::from_secs(5), true)
                    .filter(|out| out.status)
                    .map(|out| out.stdout.lines().map(str::trim).filter(|s| !s.is_empty())
                        .map(str::to_string).collect::<Vec<_>>())
            };
            if let Some(options) = options {
                if !p.options_command.is_empty() {
                    result.options.push((key.clone(), options.clone()));
                }
                if !p.value_command.is_empty() || !p.key.is_empty() {
                    let value = scalar_value(item).and_then(|raw| {
                        let mapped = p.options_map.iter().find(|(k, _)| k.eq_ignore_ascii_case(&raw))
                            .map(|(_, v)| v.as_str()).unwrap_or(&raw);
                        options.iter().find(|s| s.eq_ignore_ascii_case(mapped)).cloned()
                    });
                    result.selections.push((key.clone(), value));
                }
            }
        }
        if matches!(item.item_type.as_str(), "slider" | "spin")
            && !matches!(
                key.as_str(),
                "Volume" | "Microphone" | "Brightness" | "Night Light"
            )
            && let Some(raw) = scalar_value(item)
            && let Ok(value) = raw.parse::<f32>()
            && value.is_finite()
        {
            result.sliders.push((key.clone(), value));
        }
        if matches!(item.item_type.as_str(), "toggle" | "toggle_card") {
            let value = if !p.state_command.is_empty() {
                run_shell(&p.state_command, Duration::from_secs(4), true)
                    .filter(|out| out.status)
                    .and_then(|out| parse_bool(&out.stdout))
            } else if !p.key.is_empty() {
                match std::fs::read_to_string(settings_dir().join(&p.key)) {
                    Ok(raw) => parse_bool(&raw),
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => Some(false),
                    Err(_) => None,
                }
            } else {
                Some(false)
            };
            result
                .toggles
                .push((item_key(item), value.map(|v| v ^ p.key_inverse)));
        }
    }
    // One query per scope, deduplicated; Id makes output independent of unit order.
    for scope in ["system", "user"] {
        let mut units: Vec<String> = items
            .iter()
            .filter(|i| {
                !i.properties.service.is_empty()
                    && (i.properties.scope == "user") == (scope == "user")
            })
            .map(|i| i.properties.service.clone())
            .collect();
        units.sort();
        units.dedup();
        if units.is_empty() {
            continue;
        }
        let mut argv = vec!["systemctl".into()];
        if scope == "user" {
            argv.push("--user".into());
        }
        argv.extend([
            "show".into(),
            "--no-pager".into(),
            "--property=Id,Names,LoadState,ActiveState,UnitFileState".into(),
            "--".into(),
        ]);
        argv.extend(units.clone());
        if let Some(out) = run_command(&argv, Duration::from_secs(4), true) {
            result.services.extend(parse_services(scope, &out.stdout));
        }
        for unit in units {
            result
                .services
                .entry(service_key(scope, &unit))
                .or_insert_with(|| ServiceStatus {
                    load: "unavailable".into(),
                    active: "unknown".into(),
                    startup: "unknown".into(),
                });
        }
    }
    result
}

fn scalar_value(item: &ItemConfig) -> Option<String> {
    let p = &item.properties;
    if !p.value_command.is_empty() {
        run_shell(&p.value_command, Duration::from_secs(4), true)
            .filter(|out| out.status).map(|out| out.stdout.trim().to_string())
    } else if !p.key.is_empty() {
        std::fs::read_to_string(settings_dir().join(&p.key)).ok()
            .map(|raw| raw.trim().to_string())
    } else { None }
}

/// Persist only after a finite action succeeds. Launches are confirmed by readback.
pub fn set_scalar(item: &ItemConfig, value: &str, action: Option<&ActionConfig>) -> Result<(), String> {
    if let Some(action) = action { run_action(action)?; }
    if item.properties.persistence == "app" && !item.properties.key.is_empty() {
        let raw = if item.item_type == "selection" {
            item.properties.options_map.iter().filter(|(_, v)| v.as_str() == value)
                .map(|(k, _)| k.as_str()).min().unwrap_or(value)
        } else { value };
        if !atomic_write_text(&settings_dir().join(&item.properties.key), raw) {
            return Err("Could not save setting".into());
        }
    }
    Ok(())
}

pub fn is_launch(action: &ActionConfig) -> bool {
    matches!(action, ActionConfig::Exec { terminal: true, .. } | ActionConfig::Argv { terminal: true, .. })
        || matches!(action, ActionConfig::Exec { mode, .. } | ActionConfig::Argv { mode, .. } if mode == "launch")
}

fn label_value(value: Option<&ValueConfig>) -> Option<String> {
    match value? {
        ValueConfig::Static { text } => Some(text.clone()),
        ValueConfig::Exec { command } => run_shell(command, Duration::from_secs(4), true)
            .filter(|out| out.status).map(|out| out.stdout.trim().to_string()),
        ValueConfig::File { path } => std::fs::read_to_string(expand_arg(path))
            .ok().map(|raw| raw.trim().to_string()),
        ValueConfig::System { key } => match key.as_str() {
            "kernel_version" => std::fs::read_to_string("/proc/sys/kernel/osrelease")
                .ok().map(|raw| raw.trim().to_string()),
            "cpu_model" => std::fs::read_to_string("/proc/cpuinfo").ok()?
                .lines().find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    (name.trim() == "model name").then(|| value.trim().split(" @").next().unwrap_or_default().to_string())
                }),
            "memory_total" | "memory_used" => {
                let raw = std::fs::read_to_string("/proc/meminfo").ok()?;
                let field = |name: &str| raw.lines().find_map(|line| {
                    let (key, value) = line.split_once(':')?;
                    (key == name).then(|| value.split_whitespace().next()?.parse::<u64>().ok()).flatten()
                });
                let total = field("MemTotal")?;
                let kb = if key == "memory_used" { total.saturating_sub(field("MemAvailable")?) } else { total };
                Some(format!("{:.1} GB", kb as f64 / 1_048_576.0))
            }
            _ => None,
        },
    }
}

fn parse_services(scope: &str, raw: &str) -> HashMap<String, ServiceStatus> {
    let mut result = HashMap::new();
    for block in raw.split("\n\n") {
        let fields: HashMap<&str, &str> = block.lines().filter_map(|s| s.split_once('=')).collect();
        let (Some(id), Some(load), Some(active), Some(startup)) = (
            fields.get("Id"),
            fields.get("LoadState"),
            fields.get("ActiveState"),
            fields.get("UnitFileState"),
        ) else {
            continue;
        };
        let status = ServiceStatus {
            load: load.to_string(),
            active: active.to_string(),
            startup: startup.to_string(),
        };
        // systemctl returns a canonical Id even when the requested unit is an alias.
        for name in std::iter::once(*id).chain(
            fields
                .get("Names")
                .into_iter()
                .flat_map(|names| names.split_whitespace()),
        ) {
            result.insert(service_key(scope, name), status.clone());
        }
    }
    result
}

pub fn service_argv(scope: &str, unit: &str, enabled: bool) -> Vec<String> {
    let mut argv = if scope == "user" {
        vec!["systemctl".into(), "--user".into()]
    } else {
        vec!["pkexec".into(), "systemctl".into()]
    };
    argv.extend([
        if enabled { "enable" } else { "disable" }.into(),
        "--now".into(),
        "--".into(),
        unit.into(),
    ]);
    argv
}

pub fn set_service(scope: &str, unit: &str, enabled: bool) -> Result<(), String> {
    checked_run(&service_argv(scope, unit, enabled), Duration::from_secs(45))
}

fn expand_arg(raw: &str) -> String {
    let home = std::env::var("HOME").unwrap_or_default();
    if let Some(rest) = raw
        .strip_prefix("~/")
        .or_else(|| raw.strip_prefix("$HOME/"))
    {
        Path::new(&home).join(rest).to_string_lossy().into_owned()
    } else {
        raw.to_string()
    }
}

pub fn action_argv(action: &ActionConfig) -> Vec<String> {
    let (mut argv, terminal, root) = match action {
        ActionConfig::Exec {
            command,
            argv,
            expanded_argv,
            terminal,
            requires_root,
            ..
        } => {
            let args = if argv.is_empty() {
                // Expand the caller's HOME before pkexec changes the environment.
                let home = std::env::var("HOME").unwrap_or_default();
                let command = if *requires_root {
                    expand_shell_home(command, &home)
                } else {
                    // Leave ordinary shell variables to Bash. Only normalize
                    // a leading executable path, as the GTK launcher did.
                    normalize_shell_command(command, &home)
                };
                vec!["bash".into(), "-c".into(), command]
            } else {
                if *expanded_argv { argv.clone() }
                else { argv.iter().map(|a| expand_arg(a)).collect() }
            };
            (args, *terminal, *requires_root)
        }
        ActionConfig::Argv {
            argv,
            expanded_argv,
            terminal,
            requires_root,
            ..
        } => (
            if *expanded_argv { argv.clone() } else { argv.iter().map(|a| expand_arg(a)).collect() },
            *terminal,
            *requires_root,
        ),
        ActionConfig::Redirect { .. } | ActionConfig::Reload => return vec![],
    };
    if root && argv.first().is_some_and(|a| a != "pkexec") {
        argv.insert(0, "pkexec".into());
    }
    if terminal {
        argv.splice(0..0, ["kitty".into(), "--".into()]);
    }
    argv
}

pub fn with_value(action: &ActionConfig, value: &str) -> ActionConfig {
    let mut action = action.clone();
    match &mut action {
        ActionConfig::Exec { command, argv, expanded_argv, .. } => {
            *command = replace_shell_value(command, value);
            for arg in argv {
                let raw = if *expanded_argv { arg.clone() } else { expand_arg(arg) };
                *arg = replace_argv_value(&raw, value);
            }
            *expanded_argv = true;
        }
        ActionConfig::Argv { argv, expanded_argv, .. } => {
            for arg in argv {
                let raw = if *expanded_argv { arg.clone() } else { expand_arg(arg) };
                *arg = replace_argv_value(&raw, value);
            }
            *expanded_argv = true;
        }
        _ => (),
    }
    action
}

fn normalize_shell_command(raw: &str, home: &str) -> String {
    let raw = raw.trim();
    if let Some(rest) = raw.strip_prefix("$HOME/").or_else(|| raw.strip_prefix("~/")) {
        format!("{}/{rest}", shell_literal(home, None))
    } else { raw.into() }
}

fn value_token_length(raw: &str) -> Option<usize> {
    ["{value}", "${VALUE}", "$VALUE"].into_iter()
        .find(|token| raw.starts_with(token) && (*token != "$VALUE" || raw[token.len()..]
            .chars().next().is_none_or(|c| !c.is_alphanumeric() && c != '_')))
        .map(str::len)
}

fn replace_argv_value(raw: &str, value: &str) -> String {
    let mut result = String::new();
    let mut rest = raw;
    while !rest.is_empty() {
        if let Some(length) = value_token_length(rest) {
            result.push_str(value);
            rest = &rest[length..];
        } else {
            let c = rest.chars().next().unwrap();
            result.push(c);
            rest = &rest[c.len_utf8()..];
        }
    }
    result
}

fn shell_literal(value: &str, quote: Option<char>) -> String {
    match quote {
        Some('"') => value.replace('\\', "\\\\").replace('"', "\\\"")
            .replace('$', "\\$").replace('`', "\\`"),
        Some('\'') => value.replace('\'', "'\\''"),
        _ => format!("'{}'", value.replace('\'', "'\\''")),
    }
}

fn replace_shell_value(raw: &str, value: &str) -> String {
    let mut output = String::new();
    let mut rest = raw;
    let mut quote = None;
    while !rest.is_empty() {
        if let Some(length) = value_token_length(rest)
        {
            output.push_str(&shell_literal(value, quote));
            rest = &rest[length..];
            continue;
        }
        let c = rest.chars().next().unwrap();
        rest = &rest[c.len_utf8()..];
        output.push(c);
        if c == '\\' && quote != Some('\'') {
            if let Some(next) = rest.chars().next() { output.push(next); rest = &rest[next.len_utf8()..]; }
        } else if Some(c) == quote { quote = None; }
        else if quote.is_none() && matches!(c, '\'' | '"') { quote = Some(c); }
    }
    output
}

fn expand_shell_home(raw: &str, home: &str) -> String {
    let mut output = String::new();
    let mut rest = raw;
    let mut quote = None;
    while !rest.is_empty() {
        let token = if quote == Some('\'') {
            None
        } else if rest.starts_with("${HOME}") {
            Some(7)
        } else if rest.starts_with("$HOME")
            && rest[5..]
                .chars()
                .next()
                .is_none_or(|c| !c.is_alphanumeric() && c != '_')
        {
            Some(5)
        } else if rest.starts_with("~/") && quote.is_none()
            && output.chars().last().is_none_or(|c| c.is_whitespace() || "=;|&(".contains(c)) {
            Some(1)
        } else {
            None
        };
        if let Some(length) = token {
            output.push_str(&shell_literal(home, quote));
            rest = &rest[length..];
            continue;
        }
        let c = rest.chars().next().unwrap();
        rest = &rest[c.len_utf8()..];
        output.push(c);
        if c == '\\' && quote != Some('\'') {
            if let Some(next) = rest.chars().next() {
                output.push(next);
                rest = &rest[next.len_utf8()..];
            }
        } else if Some(c) == quote {
            quote = None;
        } else if quote.is_none() && matches!(c, '\'' | '"') {
            quote = Some(c);
        }
    }
    output
}

pub fn launch(action: &ActionConfig) -> Result<(), String> {
    let args = action_argv(action);
    if args.is_empty() {
        return Ok(());
    }
    let mut proc = Command::new("systemd-run");
    proc.args([
        "--user",
        "--scope",
        "--collect",
        "--quiet",
        "--expand-environment=no",
        "--",
    ])
    .args(args)
    .stdin(Stdio::null())
    .stdout(Stdio::null())
    .stderr(Stdio::null());
    use std::os::unix::process::CommandExt;
    proc.process_group(0);
    let mut child = proc.spawn().map_err(|e| e.to_string())?;
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(())
}

pub fn run_action(action: &ActionConfig) -> Result<(), String> {
    let (terminal, mode, timeout) = match action {
        ActionConfig::Exec {
            terminal,
            mode,
            timeout,
            ..
        }
        | ActionConfig::Argv {
            terminal,
            mode,
            timeout,
            ..
        } => (*terminal, mode.as_str(), timeout.unwrap_or(90)),
        ActionConfig::Redirect { .. } | ActionConfig::Reload => return Ok(()),
    };
    if terminal || mode == "launch" {
        launch(action)
    } else {
        checked_run(&action_argv(action), Duration::from_secs(timeout))
    }
}

fn checked_run(argv: &[String], timeout: Duration) -> Result<(), String> {
    let out = run_command(argv, timeout, true).ok_or("Command could not start or timed out")?;
    if out.status {
        Ok(())
    } else {
        let message = if out.stderr.trim().is_empty() {
            out.stdout.trim()
        } else {
            out.stderr.trim()
        };
        Err(if message.is_empty() {
            format!("Command exited with {}", out.code)
        } else {
            message.chars().take(240).collect()
        })
    }
}

pub fn set_toggle(
    item: &ItemConfig,
    enabled: bool,
    action: Option<&ActionConfig>,
) -> Result<(), String> {
    if let Some(action) = action {
        run_action(action)?;
    }
    if item.properties.persistence == "app" && !item.properties.key.is_empty() {
        let value = enabled ^ item.properties.key_inverse;
        let raw = if item.properties.save_as_int {
            if value { "1" } else { "0" }
        } else if value {
            "true"
        } else {
            "false"
        };
        if !atomic_write_text(&settings_dir().join(&item.properties.key), raw) {
            return Err("Could not save setting".into());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dynamic_options_and_scalar_files_follow_external_changes() {
        let path = std::env::temp_dir().join(format!("dusky-selection-{}", std::process::id()));
        let mut item: ItemConfig = toml::from_str("type='selection'\n[properties]\ntitle='Fixture'\npersistence='app'\noptions_command=\"printf 'RAM\\nDisk\\n'\"\n[properties.options_map]\nfalse='RAM'\ntrue='Disk'").unwrap();
        item.properties.key = path.to_string_lossy().into_owned();
        let missing = query(&[item.clone()]);
        assert_eq!(missing.options[0].1, ["RAM", "Disk"]);
        assert_eq!(missing.selections[0].1, None);
        set_scalar(&item, "Disk", None).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "true");
        assert_eq!(query(&[item.clone()]).selections[0].1.as_deref(), Some("Disk"));
        std::fs::write(&path, "FALSE\n").unwrap();
        assert_eq!(query(&[item.clone()]).selections[0].1.as_deref(), Some("RAM"));
        let failure: ActionConfig = toml::from_str("type='exec'\ncommand='exit 7'").unwrap();
        assert!(set_scalar(&item, "Disk", Some(&failure)).is_err());
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "FALSE\n");
        std::fs::write(&path, "unexpected").unwrap();
        assert_eq!(query(&[item.clone()]).selections[0].1, None);
        item.properties.options_command = "exit 1".into();
        let failed = query(&[item]);
        assert!(failed.options.is_empty(), "failed discovery must retain the previous list");
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn substitutions_preserve_values_in_each_shell_quote_context() {
        let value = "6 GB's $HOME/\\path \"quoted\"; $(exit 7) `literal`";
        for template in ["printf %s {value}", "printf %s \"{value}\"", "printf %s '{value}'", "printf %s $VALUE"] {
            let action = ActionConfig::Exec { command: template.into(), argv: vec![], expanded_argv: false, terminal: false,
                requires_root: false, mode: String::new(), timeout: None };
            let action = with_value(&action, value);
            let output = run_command(&action_argv(&action), Duration::from_secs(2), true).unwrap();
            assert!(output.status, "{}", output.stderr);
            assert_eq!(output.stdout, value, "{template}");
        }
        assert_eq!(expand_shell_home("printf %s '$HOME/literal'", "/tmp/other"), "printf %s '$HOME/literal'");
        assert_eq!(replace_shell_value("printf %s $VALUE_SUFFIX", value), "printf %s $VALUE_SUFFIX");
    }

    #[test]
    fn substituted_argv_values_stay_literal_and_shell_home_remains_dynamic() {
        for kind in ["argv", "exec"] {
            let action: ActionConfig = toml::from_str(&format!("type='{kind}'\nargv=['printf','%s','{{value}}']")).unwrap();
            for value in ["~/literal", "$HOME/literal", "$VALUE", "${VALUE}", "{value}", "two words'\\quoted"] {
                let substituted = with_value(&action, value);
                let output = run_command(&action_argv(&substituted), Duration::from_secs(2), true).unwrap();
                assert!(output.status);
                assert_eq!(output.stdout, value);
            }
        }
        let action: ActionConfig = toml::from_str("type='exec'\ncommand='HOME=/tmp/dusky-command-local; printf %s \"$HOME\"'").unwrap();
        let output = run_command(&action_argv(&action), Duration::from_secs(2), true).unwrap();
        assert_eq!(output.stdout, "/tmp/dusky-command-local");
        assert_eq!(normalize_shell_command("$HOME/script --flag", "/tmp/two words"), "'/tmp/two words'/script --flag");
    }

    #[test]
    fn entries_and_numeric_settings_persist_and_read_back() {
        let path = std::env::temp_dir().join(format!("dusky-scalar-{}", std::process::id()));
        for kind in ["entry", "slider"] {
            let mut item: ItemConfig = toml::from_str(&format!("type='{kind}'\n[properties]\ntitle='Scalar'\npersistence='app'")).unwrap();
            item.properties.key = path.to_string_lossy().into_owned();
            set_scalar(&item, "0.25", None).unwrap();
            let snapshot = query(&[item]);
            if kind == "entry" { assert_eq!(snapshot.entries[0].1, "0.25"); }
            else { assert_eq!(snapshot.sliders[0].1, 0.25); }
            std::fs::remove_file(&path).unwrap();
        }
    }
    #[test]
    fn configured_label_sources_report_values_and_query_failures() {
        let path = std::env::temp_dir().join(format!("dusky-label-{}", std::process::id()));
        std::fs::write(&path, "fixture version\n").unwrap();
        let file: ItemConfig = toml::from_str(&format!("type='label'\n[properties]\ntitle='Version'\n[value]\ntype='file'\npath='{}'", path.display())).unwrap();
        let exec: ItemConfig = toml::from_str("type='label'\n[properties]\ntitle='Status'\n[value]\ntype='exec'\ncommand='printf actual'").unwrap();
        let failed: ItemConfig = toml::from_str("type='label'\n[properties]\ntitle='Absent'\n[value]\ntype='exec'\ncommand='exit 1'").unwrap();
        let labels: HashMap<_, _> = query(&[file, exec, failed]).labels.into_iter().collect();
        std::fs::remove_file(path).unwrap();
        assert_eq!(labels["Version"], "fixture version");
        assert_eq!(labels["Status"], "actual");
        assert_eq!(labels["Absent"], "N/A");
        assert_eq!(label_value(Some(&ValueConfig::Static { text: "literal".into() })), Some("literal".into()));
        for key in ["cpu_model", "kernel_version", "memory_total", "memory_used"] {
            let value = label_value(Some(&ValueConfig::System { key: key.into() })).unwrap();
            assert!(!value.is_empty(), "{key}");
        }
    }

    #[test]
    fn structured_actions_preserve_arguments_and_shell_programs() {
        let action: ActionConfig =
            toml::from_str("type='argv'\nargv=['printf','<%s>','Dusky Updater','two words']")
                .unwrap();
        let out = run_command(&action_argv(&action), Duration::from_secs(1), true).unwrap();
        assert_eq!(out.stdout, "<Dusky Updater><two words>");
        let action: ActionConfig =
            toml::from_str("type='argv'\nargv=['bash','-c','printf %s \"$1\"','fixture','a b']")
                .unwrap();
        let out = run_command(&action_argv(&action), Duration::from_secs(1), true).unwrap();
        assert_eq!(out.stdout, "a b");
    }
    #[test]
    fn service_queries_distinguish_scope_runtime_and_startup() {
        let raw = "Id=demo.service\nNames=demo.service alias.service\nLoadState=loaded\nActiveState=active\nUnitFileState=enabled\n\nId=absent.service\nLoadState=not-found\nActiveState=inactive\nUnitFileState=\n";
        let states = parse_services("user", raw);
        assert!(states["user:demo.service"].enabled());
        assert!(states["user:alias.service"].enabled());
        assert!(states["user:demo.service"].actionable());
        assert!(!states["user:absent.service"].actionable());
        assert_eq!(
            service_argv("user", "demo.service", true),
            [
                "systemctl",
                "--user",
                "enable",
                "--now",
                "--",
                "demo.service"
            ]
        );
        assert_eq!(
            service_argv("system", "demo.service", false),
            [
                "pkexec",
                "systemctl",
                "disable",
                "--now",
                "--",
                "demo.service"
            ]
        );
    }
    #[test]
    fn toggle_action_failure_is_reported() {
        let action: ActionConfig =
            toml::from_str("type='exec'\ncommand='printf failed >&2; exit 1'").unwrap();
        assert_eq!(run_action(&action), Err("failed".into()));
        assert_eq!(parse_bool("True\n"), Some(true));
        assert_eq!(parse_bool("off"), Some(false));
        assert_eq!(parse_bool("unavailable"), None);
    }

    #[test]
    fn entry_values_are_single_arguments_and_retain_action_options() {
        let action: ActionConfig =
            toml::from_str("type='exec'\ncommand='printf %s {value}'\ntimeout=2").unwrap();
        let action = with_value(&action, "6 GB's worth; literal");
        let out = run_command(&action_argv(&action), Duration::from_secs(1), true).unwrap();
        assert_eq!(out.stdout, "6 GB's worth; literal");
        assert!(matches!(
            action,
            ActionConfig::Exec {
                timeout: Some(2),
                ..
            }
        ));
    }

    #[test]
    fn caller_paths_keep_shell_quoting_with_spaces_and_apostrophes() {
        let home = "/tmp/a user's folder";
        for raw in [
            "printf '%s' \"$HOME/file\"",
            "printf '%s' $HOME/file",
            "printf '%s' ~/file",
        ] {
            let out =
                run_shell(&expand_shell_home(raw, home), Duration::from_secs(1), true).unwrap();
            assert!(out.status, "{}", out.stderr);
            assert_eq!(out.stdout, format!("{home}/file"));
        }
    }

    #[test]
    fn app_persistence_changes_only_after_a_successful_action() {
        let path =
            std::env::temp_dir().join(format!("dusky-toggle-fixture-{}", std::process::id()));
        let mut item: ItemConfig =
            toml::from_str("type='toggle'\n[properties]\ntitle='Fixture'\npersistence='app'")
                .unwrap();
        item.properties.key = path.to_string_lossy().into_owned();
        let failure: ActionConfig = toml::from_str("type='exec'\ncommand='exit 1'").unwrap();
        set_toggle(&item, false, None).unwrap();
        assert!(set_toggle(&item, true, Some(&failure)).is_err());
        assert_eq!(query(&[item.clone()]).toggles[0].1, Some(false));
        set_toggle(&item, true, None).unwrap();
        assert_eq!(query(&[item]).toggles[0].1, Some(true));
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    #[ignore = "requires a live systemd user manager; uses and removes an isolated fixture unit"]
    fn isolated_user_service_enable_start_disable_stop() {
        let runtime =
            PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").expect("user runtime required"));
        let unit = format!("dusky-center-fixture-{}.service", std::process::id());
        let path = runtime.join("systemd/user").join(&unit);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        struct Cleanup {
            path: PathBuf,
            unit: String,
        }
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = set_service("user", &self.unit, false);
                let _ = std::fs::remove_file(&self.path);
                let _ = checked_run(
                    &["systemctl".into(), "--user".into(), "daemon-reload".into()],
                    Duration::from_secs(4),
                );
            }
        }
        let _cleanup = Cleanup {
            path: path.clone(),
            unit: unit.clone(),
        };
        std::fs::write(path, "[Unit]\nDescription=Dusky Center isolated test\n[Service]\nExecStart=/usr/bin/sleep infinity\n[Install]\nWantedBy=default.target\n").unwrap();
        checked_run(
            &["systemctl".into(), "--user".into(), "daemon-reload".into()],
            Duration::from_secs(4),
        )
        .unwrap();
        let mut item: ItemConfig =
            toml::from_str("type='service'\n[properties]\nscope='user'").unwrap();
        item.properties.service = unit.clone();
        let key = service_key("user", &unit);
        set_service("user", &unit, true).unwrap();
        let snapshot = query(&[item.clone()]);
        assert!(snapshot.services[&key].enabled());
        assert!(snapshot.services[&key].startup.starts_with("enabled"));
        set_service("user", &unit, false).unwrap();
        let snapshot = query(&[item]);
        assert!(!snapshot.services[&key].enabled());
        assert!(!snapshot.services[&key].startup.starts_with("enabled"));
    }
}
