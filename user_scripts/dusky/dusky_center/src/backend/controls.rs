//! Queries and verified writes for TOML controls. Called on executor workers.
use super::cmd::{CmdOutput, atomic_write_text, run_command, run_shell, spawn_detached};
use crate::config::{ActionConfig, ItemConfig, ValueConfig};
use std::{
    collections::{HashMap, HashSet},
    path::{Path, PathBuf},
    sync::{
        Mutex,
        atomic::{AtomicUsize, Ordering},
    },
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
    pub file_texts: Vec<(String, Option<String>)>,
}

enum QueryTask {
    Shell { command: String, timeout: Duration },
    Systemctl { argv: Vec<String> },
}

fn run_query_task(task: &QueryTask) -> Option<CmdOutput> {
    match task {
        QueryTask::Shell { command, timeout } => run_shell(command, *timeout, true),
        QueryTask::Systemctl { argv } => run_command(argv, Duration::from_secs(4), true),
    }
}

fn execute_query_tasks(tasks: &[QueryTask]) -> Vec<Option<CmdOutput>> {
    if tasks.is_empty() {
        return Vec::new();
    }
    if tasks.len() == 1 {
        return vec![run_query_task(&tasks[0])];
    }
    let worker_count = tasks.len().min(4);
    let next = AtomicUsize::new(0);
    let slots: Vec<Mutex<Option<CmdOutput>>> = (0..tasks.len()).map(|_| Mutex::new(None)).collect();
    let run_loop = || loop {
        let idx = next.fetch_add(1, Ordering::Relaxed);
        if idx >= tasks.len() {
            break;
        }
        *slots[idx].lock().unwrap() = run_query_task(&tasks[idx]);
    };
    std::thread::scope(|scope| {
        for _ in 1..worker_count {
            scope.spawn(run_loop);
        }
        run_loop();
    });
    slots
        .into_iter()
        .map(|slot| slot.into_inner().unwrap())
        .collect()
}

pub fn query(items: &[ItemConfig]) -> Snapshot {
    let mut result = Snapshot::default();
    let mut tasks: Vec<QueryTask> = Vec::new();
    let mut shell_indices: HashMap<String, usize> = HashMap::new();
    let mut seen_file_texts: HashSet<&str> = HashSet::new();

    let mut enqueue_shell = |command: &str, timeout: Duration| {
        if let Some(&idx) = shell_indices.get(command) {
            if let QueryTask::Shell { timeout: existing, .. } = &mut tasks[idx]
                && timeout > *existing
            {
                *existing = timeout;
            }
            return;
        }
        let idx = tasks.len();
        shell_indices.insert(command.to_string(), idx);
        tasks.push(QueryTask::Shell {
            command: command.to_string(),
            timeout,
        });
    };

    // Pass 1: Read deduplicated button_text_file entries locally and collect all unique shell commands.
    for item in items {
        let p = &item.properties;
        let key = item_key(item);
        if !p.button_text_file.is_empty() && seen_file_texts.insert(p.button_text_file.as_str()) {
            let text = std::fs::read_to_string(expand_arg(&p.button_text_file))
                .ok()
                .map(|raw| raw.trim().to_string());
            result.file_texts.push((p.button_text_file.clone(), text));
        }
        if item.item_type == "label"
            && let Some(ValueConfig::Exec { command }) = item.value.as_ref()
        {
            enqueue_shell(command, Duration::from_secs(4));
        }
        if matches!(item.item_type.as_str(), "entry" | "secret") && !p.value_command.is_empty() {
            enqueue_shell(&p.value_command, Duration::from_secs(4));
        }
        if item.item_type == "selection" {
            if !p.options_command.is_empty() {
                enqueue_shell(&p.options_command, Duration::from_secs(5));
            }
            if !p.value_command.is_empty() {
                enqueue_shell(&p.value_command, Duration::from_secs(4));
            }
        }
        if matches!(item.item_type.as_str(), "slider" | "spin")
            && !matches!(
                key.as_str(),
                "Volume" | "Microphone" | "Brightness" | "Night Light"
            )
            && !p.value_command.is_empty()
        {
            enqueue_shell(&p.value_command, Duration::from_secs(4));
        }
        if matches!(item.item_type.as_str(), "toggle" | "toggle_card") && !p.state_command.is_empty() {
            enqueue_shell(&p.state_command, Duration::from_secs(4));
        }
    }

    // One query per scope, deduplicated; Id makes output independent of unit order.
    let mut service_batches: Vec<(&'static str, Vec<String>, usize)> = Vec::new();
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
        let task_idx = tasks.len();
        tasks.push(QueryTask::Systemctl { argv });
        service_batches.push((scope, units, task_idx));
    }

    // Pass 2: Execute all unique external commands concurrently (bounded to 4 threads).
    let outputs = execute_query_tasks(&tasks);

    let shell_output = |command: &str| -> Option<&CmdOutput> {
        shell_indices
            .get(command)
            .and_then(|&idx| outputs[idx].as_ref())
    };

    // Pass 3: Assemble snapshot from completed outputs and local settings/procfs reads.
    for item in items {
        let p = &item.properties;
        let key = item_key(item);
        if item.item_type == "label" {
            let value = label_value_with(item.value.as_ref(), &shell_output)
                .unwrap_or_else(|| "N/A".into());
            result.labels.push((key.clone(), value));
        }
        if matches!(item.item_type.as_str(), "entry" | "secret")
            && let Some(raw) = scalar_value_with(item, &shell_output)
        {
            result.entries.push((key.clone(), raw));
        }
        if item.item_type == "selection" {
            let options = if p.options_command.is_empty() {
                Some(p.options.clone())
            } else {
                shell_output(&p.options_command)
                    .filter(|out| out.status)
                    .map(|out| {
                        out.stdout
                            .lines()
                            .map(str::trim)
                            .filter(|s| !s.is_empty())
                            .map(str::to_string)
                            .collect::<Vec<_>>()
                    })
            };
            if let Some(options) = options {
                if !p.options_command.is_empty() {
                    result.options.push((key.clone(), options.clone()));
                }
                if !p.value_command.is_empty() || !p.key.is_empty() {
                    let value = scalar_value_with(item, &shell_output).and_then(|raw| {
                        let mapped = p
                            .options_map
                            .iter()
                            .find(|(k, _)| k.eq_ignore_ascii_case(&raw))
                            .map(|(_, v)| v.as_str())
                            .unwrap_or(&raw);
                        options
                            .iter()
                            .find(|s| s.eq_ignore_ascii_case(mapped))
                            .cloned()
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
            && let Some(raw) = scalar_value_with(item, &shell_output)
            && let Ok(value) = raw.parse::<f32>()
            && value.is_finite()
        {
            result.sliders.push((key.clone(), value));
        }
        if matches!(item.item_type.as_str(), "toggle" | "toggle_card") {
            let value = if !p.state_command.is_empty() {
                shell_output(&p.state_command)
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

    for (scope, units, task_idx) in service_batches {
        if let Some(out) = outputs[task_idx].as_ref() {
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

fn scalar_value_with<'a>(
    item: &ItemConfig,
    shell_output: &impl Fn(&str) -> Option<&'a CmdOutput>,
) -> Option<String> {
    let p = &item.properties;
    if !p.value_command.is_empty() {
        shell_output(&p.value_command)
            .filter(|out| out.status)
            .map(|out| out.stdout.trim().to_string())
    } else if !p.key.is_empty() {
        std::fs::read_to_string(settings_dir().join(&p.key))
            .ok()
            .map(|raw| raw.trim().to_string())
    } else {
        None
    }
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

/// Buttons/cards with `mode = "apply"` or non-terminal `requires_root` (when not `mode = "launch"`)
/// run as bounded observed commands so errors surface and controls refresh on completion.
pub fn is_observed_button_action(action: &ActionConfig) -> bool {
    match action {
        ActionConfig::Exec {
            terminal,
            requires_root,
            mode,
            ..
        }
        | ActionConfig::Argv {
            terminal,
            requires_root,
            mode,
            ..
        } => mode == "apply" || (*requires_root && !*terminal && mode != "launch"),
        ActionConfig::Redirect { .. } | ActionConfig::Reload => false,
    }
}

fn label_value_with<'a>(
    value: Option<&ValueConfig>,
    shell_output: &impl Fn(&str) -> Option<&'a CmdOutput>,
) -> Option<String> {
    match value? {
        ValueConfig::Static { text } => Some(text.clone()),
        ValueConfig::Exec { command } => shell_output(command)
            .filter(|out| out.status)
            .map(|out| out.stdout.trim().to_string()),
        ValueConfig::File { path } => std::fs::read_to_string(expand_arg(path))
            .ok()
            .map(|raw| raw.trim().to_string()),
        ValueConfig::System { key } => match key.as_str() {
            "kernel_version" => std::fs::read_to_string("/proc/sys/kernel/osrelease")
                .ok()
                .map(|raw| raw.trim().to_string()),
            "cpu_model" => std::fs::read_to_string("/proc/cpuinfo")
                .ok()?
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    (name.trim() == "model name").then(|| {
                        value
                            .trim()
                            .split(" @")
                            .next()
                            .unwrap_or_default()
                            .to_string()
                    })
                }),
            "memory_total" | "memory_used" => {
                let raw = std::fs::read_to_string("/proc/meminfo").ok()?;
                let field = |name: &str| {
                    raw.lines().find_map(|line| {
                        let (key, value) = line.split_once(':')?;
                        (key == name)
                            .then(|| value.split_whitespace().next()?.parse::<u64>().ok())
                            .flatten()
                    })
                };
                let total = field("MemTotal")?;
                let kb = if key == "memory_used" {
                    total.saturating_sub(field("MemAvailable")?)
                } else {
                    total
                };
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

/// Parse a simple command string directly into `argv` without invoking `bash -c`,
/// following strict Bash quoting, tilde, `$HOME`/`${HOME}`, assignment, and builtin rules.
/// Returns `None` whenever shell interpretation may be needed.
pub(crate) fn try_parse_direct_argv(raw: &str, home: &str) -> Option<Vec<String>> {
    let mut args = Vec::new();
    let mut current = String::new();
    let mut in_token = false;
    let mut rest = raw;

    while !rest.is_empty() {
        let c = rest.chars().next()?;
        match c {
            ' ' | '\t' => {
                if in_token {
                    args.push(std::mem::take(&mut current));
                    in_token = false;
                }
                rest = &rest[c.len_utf8()..];
            }
            '\n' | '\r'
            | '|' | '&' | ';' | '(' | ')' | '<' | '>' | '`' | '*' | '?' | '[' | ']' | '{' | '}'
            | '#' | '\\' | '!' => return None,
            '\'' => {
                in_token = true;
                rest = &rest[1..];
                let end = rest.find('\'')?;
                current.push_str(&rest[..end]);
                rest = &rest[end + 1..];
            }
            '"' => {
                in_token = true;
                rest = &rest[1..];
                let mut closed = false;
                while !rest.is_empty() {
                    let q = rest.chars().next()?;
                    match q {
                        '"' => {
                            rest = &rest[1..];
                            closed = true;
                            break;
                        }
                        '\\' | '`' => return None,
                        '$' => {
                            let len = home_var_length(rest)?;
                            if home.is_empty() {
                                return None;
                            }
                            current.push_str(home);
                            rest = &rest[len..];
                        }
                        _ => {
                            current.push(q);
                            rest = &rest[q.len_utf8()..];
                        }
                    }
                }
                if !closed {
                    return None;
                }
            }
            '$' => {
                let len = home_var_length(rest)?;
                if home.is_empty()
                    || home
                        .chars()
                        .any(|ch| ch.is_whitespace() || matches!(ch, '*' | '?' | '['))
                {
                    return None;
                }
                current.push_str(home);
                in_token = true;
                rest = &rest[len..];
            }
            '~' => {
                let after = &rest[1..];
                let boundary = after
                    .chars()
                    .next()
                    .is_none_or(|n| matches!(n, '/' | ' ' | '\t'));
                if !in_token {
                    if !boundary || home.is_empty() {
                        return None;
                    }
                    current.push_str(home);
                    in_token = true;
                } else if current.ends_with('=') {
                    return None;
                } else {
                    current.push('~');
                }
                rest = after;
            }
            _ => {
                current.push(c);
                in_token = true;
                rest = &rest[c.len_utf8()..];
            }
        }
    }

    if in_token {
        args.push(current);
    }
    let first = args.first()?;
    if first.is_empty() {
        return None;
    }
    if let Some((name, _)) = first.split_once('=')
        && !name.is_empty()
        && name
            .chars()
            .next()
            .is_some_and(|ch| ch.is_ascii_alphabetic() || ch == '_')
        && name.chars().all(|ch| ch.is_ascii_alphanumeric() || ch == '_')
    {
        return None;
    }
    if matches!(
        first.as_str(),
        "exit"
            | "exec"
            | "cd"
            | "export"
            | "unset"
            | "set"
            | "source"
            | "."
            | "if"
            | "then"
            | "else"
            | "elif"
            | "fi"
            | "for"
            | "while"
            | "until"
            | "do"
            | "done"
            | "case"
            | "esac"
            | "read"
            | "eval"
            | "builtin"
            | "command"
            | "type"
            | "alias"
            | "unalias"
            | "local"
            | "return"
            | "shift"
            | "trap"
            | "umask"
            | "ulimit"
            | "wait"
            | "jobs"
            | "bg"
            | "fg"
            | "kill"
            | "echo"
            | "printf"
            | "test"
            | "["
            | "[["
            | "true"
            | "false"
            | "hash"
            | "pwd"
            | "times"
            | "readonly"
            | "declare"
            | "typeset"
            | "let"
    ) {
        return None;
    }
    Some(args)
}

fn home_var_length(rest: &str) -> Option<usize> {
    if rest.starts_with("${HOME}") {
        Some(7)
    } else if rest.starts_with("$HOME")
        && rest[5..]
            .chars()
            .next()
            .is_none_or(|c| !c.is_alphanumeric() && c != '_')
    {
        Some(5)
    } else {
        None
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
                if let Some(direct) = try_parse_direct_argv(command, &home) {
                    direct
                } else {
                    let command = if *requires_root {
                        expand_shell_home(command, &home)
                    } else {
                        // Leave ordinary shell variables to Bash. Only normalize
                        // a leading executable path, as the GTK launcher did.
                        normalize_shell_command(command, &home)
                    };
                    vec!["bash".into(), "-c".into(), command]
                }
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
    spawn_detached(&args)
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
        let no_shell = |_: &str| None;
        assert_eq!(
            label_value_with(Some(&ValueConfig::Static { text: "literal".into() }), &no_shell),
            Some("literal".into())
        );
        for key in ["cpu_model", "kernel_version", "memory_total", "memory_used"] {
            let value =
                label_value_with(Some(&ValueConfig::System { key: key.into() }), &no_shell).unwrap();
            assert!(!value.is_empty(), "{key}");
        }
    }

    #[test]
    fn direct_argv_parser_respects_bash_quoting_and_falls_back_on_shell_syntax() {
        assert_eq!(
            try_parse_direct_argv("wpctl set-volume @DEFAULT_AUDIO_SINK@ 50%", "/home/dusk"),
            Some(vec![
                "wpctl".into(),
                "set-volume".into(),
                "@DEFAULT_AUDIO_SINK@".into(),
                "50%".into(),
            ])
        );
        assert_eq!(
            try_parse_direct_argv(
                "~/scripts/tool.py \"$HOME/file\" '${HOME}/lit' '~/lit' \"~/lit\" ~ '' \"\"",
                "/home/dusk",
            ),
            Some(vec![
                "/home/dusk/scripts/tool.py".into(),
                "/home/dusk/file".into(),
                "${HOME}/lit".into(),
                "~/lit".into(),
                "~/lit".into(),
                "/home/dusk".into(),
                String::new(),
                String::new(),
            ])
        );
        // Double-quoted $HOME with spaces in HOME stays one argument; unquoted $HOME falls back.
        assert_eq!(
            try_parse_direct_argv("kitty \"$HOME/a\"", "/home/a b"),
            Some(vec!["kitty".into(), "/home/a b/a".into()])
        );
        assert_eq!(try_parse_direct_argv("kitty $HOME/a", "/home/a b"), None);

        for complex in [
            "",
            "   ",
            "kitty \"$HOME_OTHER/a\"",
            "kitty $HOME_OTHER/a",
            "kitty ~root/file",
            "FOO=1 kitty",
            "kitty a | cat",
            "kitty a && kitty b",
            "kitty \"$(id)\"",
            "kitty \"a\\\"b\"",
            "kitty 'unclosed",
            "printf '%s' ok",
            "test -f /etc/hosts",
            "exit 1",
        ] {
            assert_eq!(try_parse_direct_argv(complex, "/home/dusk"), None, "{complex}");
        }
    }

    #[test]
    fn parallel_query_deduplicates_commands_and_reports_deleted_button_text_files() {
        let root = std::env::temp_dir().join(format!("dusky-par-query-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let count_file = root.join("calls");
        let text_file = root.join("badge.txt");
        let missing_file = root.join("missing.txt");
        std::fs::write(&text_file, "3\n").unwrap();

        let dup_cmd = format!(
            "/usr/bin/bash -c 'printf x >> \"{}\"; /usr/bin/sleep 0.18; printf yes'",
            count_file.display()
        );
        let items: Vec<ItemConfig> = vec![
            toml::from_str(&format!(
                "type='toggle'\n[properties]\ntitle='Dup1'\nstate_command='{}'",
                dup_cmd.replace('\'', "''")
            ))
            .unwrap_or_else(|_| {
                let mut i: ItemConfig = toml::from_str("type='toggle'\n[properties]\ntitle='Dup1'").unwrap();
                i.properties.state_command = dup_cmd.clone();
                i
            }),
            {
                let mut i: ItemConfig = toml::from_str("type='toggle'\n[properties]\ntitle='Dup2'").unwrap();
                i.properties.state_command = dup_cmd;
                i
            },
            {
                let mut i: ItemConfig = toml::from_str("type='label'\n[properties]\ntitle='Slow1'").unwrap();
                i.value = Some(ValueConfig::Exec {
                    command: "/usr/bin/sleep 0.18 && printf one".into(),
                });
                i
            },
            {
                let mut i: ItemConfig = toml::from_str("type='label'\n[properties]\ntitle='Slow2'").unwrap();
                i.value = Some(ValueConfig::Exec {
                    command: "/usr/bin/sleep 0.18 && printf two".into(),
                });
                i
            },
            {
                let mut i: ItemConfig = toml::from_str("type='label'\n[properties]\ntitle='Slow3'").unwrap();
                i.value = Some(ValueConfig::Exec {
                    command: "/usr/bin/sleep 0.18 && printf three".into(),
                });
                i
            },
            {
                let mut i: ItemConfig = toml::from_str("type='grid_card'\n[properties]\ntitle='Card1'").unwrap();
                i.properties.button_text_file = text_file.to_string_lossy().into_owned();
                i
            },
            {
                let mut i: ItemConfig = toml::from_str("type='button'\n[properties]\ntitle='BtnDup'").unwrap();
                i.properties.button_text_file = text_file.to_string_lossy().into_owned();
                i
            },
            {
                let mut i: ItemConfig = toml::from_str("type='button'\n[properties]\ntitle='BtnMissing'").unwrap();
                i.properties.button_text_file = missing_file.to_string_lossy().into_owned();
                i
            },
        ];

        let start = std::time::Instant::now();
        let snap = query(&items);
        let elapsed = start.elapsed();

        assert!(
            elapsed < Duration::from_millis(550),
            "4 unique 180 ms commands should run in parallel, took {elapsed:?}"
        );
        assert_eq!(
            std::fs::read_to_string(&count_file).unwrap(),
            "x",
            "identical commands in one snapshot must execute once"
        );
        let toggles: HashMap<_, _> = snap.toggles.into_iter().collect();
        assert_eq!(toggles["Dup1"], Some(true));
        assert_eq!(toggles["Dup2"], Some(true));
        let labels: HashMap<_, _> = snap.labels.into_iter().collect();
        assert_eq!(labels["Slow1"], "one");
        assert_eq!(labels["Slow2"], "two");
        assert_eq!(labels["Slow3"], "three");
        assert_eq!(snap.file_texts.len(), 2, "duplicate button_text_file paths must be deduplicated");
        let files: HashMap<_, _> = snap.file_texts.into_iter().collect();
        assert_eq!(files[&text_file.to_string_lossy().into_owned()].as_deref(), Some("3"));
        assert_eq!(files[&missing_file.to_string_lossy().into_owned()], None);

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn observed_button_action_classification() {
        let normal: ActionConfig = toml::from_str("type='exec'\ncommand='kitty'").unwrap();
        let apply: ActionConfig = toml::from_str("type='exec'\ncommand='true'\nmode='apply'").unwrap();
        let root_apply: ActionConfig = toml::from_str("type='exec'\ncommand='true'\nrequires_root=true").unwrap();
        let root_term: ActionConfig = toml::from_str("type='exec'\ncommand='true'\nrequires_root=true\nterminal=true").unwrap();
        let root_launch: ActionConfig = toml::from_str("type='exec'\ncommand='true'\nrequires_root=true\nmode='launch'").unwrap();
        assert!(!is_observed_button_action(&normal));
        assert!(is_observed_button_action(&apply));
        assert!(is_observed_button_action(&root_apply));
        assert!(!is_observed_button_action(&root_term));
        assert!(!is_observed_button_action(&root_launch));
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
        let unit = format!("dusky_center-fixture-{}.service", std::process::id());
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
