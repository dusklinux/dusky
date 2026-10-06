#!/usr/bin/env python3
"""Configurable Linux resource terminator. Requires Python 3.15+, systemd 262+."""
import argparse
import os
import re
import select
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Immutable schema lookup; these are configuration conventions, not shell code.
SECTIONS = frozendict(processes=("process", "comm"), scripts=("process", "script"),
                      user_units=("user", ""), system_units=("system", ""))
STOPPED = frozenset({"inactive", "failed", "not-found"})
UNIT_SUFFIXES = (".service", ".socket", ".timer", ".path", ".scope", ".target",
                 ".mount", ".automount", ".slice", ".swap", ".device")


class Error(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Target:
    name: str
    kind: str
    default: bool = True
    match: str = ""
    value: str | tuple[str, ...] = ""
    units: tuple[str, ...] = ()
    uid: int | None = None
    sig: int = signal.SIGTERM
    timeout: float = 2.0
    force: bool = True


@dataclass(frozen=True, slots=True)
class Settings:
    kill_timeout: float = 1.0
    unit_timeout: float = 45.0
    shell_after: bool = True


@dataclass(frozen=True, slots=True)
class Process:
    pid: int
    start: int
    uid: int
    comm: str
    argv: tuple[str, ...]
    exe: str
    cwd: str


def keys(table: dict, allowed: set[str], where: str) -> None:
    if not isinstance(table, dict):
        raise Error(f"{where}: expected a table")
    if unknown := table.keys() - allowed:
        raise Error(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")


def string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise Error(f"{where}: expected a nonempty string without control characters")
    return value


def boolean(value: object, where: str) -> bool:
    if type(value) is not bool:
        raise Error(f"{where}: expected true or false")
    return value


def seconds(value: object, where: str) -> float:
    if type(value) not in (int, float) or not 0 <= value <= 3600:
        raise Error(f"{where}: expected finite seconds between 0 and 3600")
    return float(value)


def uid_value(value: object) -> int | None:
    if value == "current":
        return os.getuid()
    if value == "all":
        return None
    if type(value) is int and value >= 0:
        return value
    raise Error('uid: expected "current", "all", or a nonnegative numeric UID')


def unit_name(value: object) -> str:
    name = string(value, "unit")
    if name.startswith("-") or any(c in name for c in "/ \t*?[]"):
        raise Error(f"unit: expected an exact unit name: {name!r}")
    return name if name.endswith(UNIT_SUFFIXES) else name + ".service"


def config(path: Path) -> tuple[Settings, list[Target]]:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    keys(raw, {"settings", "targets", *SECTIONS}, "configuration")
    opts = raw.get("settings", {})
    keys(opts, {"timeout", "kill_timeout", "unit_timeout", "force", "uid", "shell_after"}, "settings")
    timeout = seconds(opts.get("timeout", 2), "settings.timeout")
    force = boolean(opts.get("force", True), "settings.force")
    uid_value(opts.get("uid", "current"))
    settings = Settings(seconds(opts.get("kill_timeout", 1), "settings.kill_timeout"),
                        seconds(opts.get("unit_timeout", 45), "settings.unit_timeout"),
                        boolean(opts.get("shell_after", True), "settings.shell_after"))
    rows = []
    for section, (kind, mode) in SECTIONS.items():
        table = raw.get(section, {})
        keys(table, {"default", "optional"}, section)
        for group in ("default", "optional"):
            values = table.get(group, [])
            if not isinstance(values, list):
                raise Error(f"{section}.{group}: expected an array")
            for value in values:
                rows.append(dict(name=value, kind=kind, default=group == "default",
                                 **({"match": mode, "value": value} if mode else {"units": [value]})))
    extra = raw.get("targets", [])
    if not isinstance(extra, list):
        raise Error("targets: expected [[targets]] tables")
    rows.extend(extra)
    targets = []
    seen = set()
    for number, row in enumerate(rows, 1):
        where = f"target {number}"
        keys(row, {"name", "kind", "default", "match", "value", "units", "uid", "signal", "timeout", "force"}, where)
        kind = row.get("kind")
        if kind not in ("process", "user", "system"):
            raise Error(f"{where}: kind must be process, user, or system")
        name = string(row.get("name"), f"{where}.name")
        default = boolean(row.get("default", True), f"{where}.default")
        if kind != "process":
            if row.keys() & {"match", "value", "uid", "signal", "timeout", "force"}:
                raise Error(f"{where}: process options are invalid on a unit target")
            units = row.get("units", [name])
            if not isinstance(units, list) or not units:
                raise Error(f"{where}.units: expected a nonempty array")
            target = Target(name, kind, default, units=tuple(dict.fromkeys(map(unit_name, units))))
        else:
            if "units" in row:
                raise Error(f"{where}: units are invalid on a process target")
            mode = row.get("match", "comm")
            if mode not in ("comm", "exe", "script", "argv"):
                raise Error(f"{where}.match: expected comm, exe, script, or argv")
            value = row.get("value", name)
            if mode == "argv":
                if not isinstance(value, list) or not value:
                    raise Error(f"{where}.value: argv requires a nonempty array of exact arguments")
                if any(not isinstance(v, str) or "\0" in v for v in value):
                    raise Error(f"{where}.value: arguments must be strings without NUL bytes")
                value = tuple(value)
            else:
                value = string(value, f"{where}.value")
                if mode == "comm" and ("/" in value or len(os.fsencode(value)) > 15):
                    raise Error(f"{where}: comm is a basename of at most 15 bytes; use exe/script/argv")
                if mode == "exe" and not value.startswith(("/", "~/")):
                    raise Error(f"{where}: exe requires an absolute path or ~/ path")
                if mode in ("exe", "script") and "/" in value:
                    value = str(Path(value).expanduser().resolve())
            sig_name = string(row.get("signal", "TERM"), f"{where}.signal")
            sig = getattr(signal, "SIG" + sig_name.removeprefix("SIG"), None)
            if not isinstance(sig, signal.Signals) or sig in (signal.SIGSTOP, signal.SIGCONT):
                raise Error(f"{where}.signal: expected a termination signal name, e.g. TERM, INT, KILL")
            target = Target(name, kind, default, mode, value, uid=uid_value(row.get("uid", opts.get("uid", "current"))),
                            sig=sig, timeout=seconds(row.get("timeout", timeout), f"{where}.timeout"),
                            force=boolean(row.get("force", force), f"{where}.force"))
        identity = (target.kind, target.name)
        if identity in seen:
            raise Error(f"{where}: duplicate target name within {target.kind}: {target.name}")
        seen.add(identity)
        targets.append(target)
    return settings, targets


def stat_fields(pid: int) -> list[str]:
    # comm may contain spaces and ')' characters. Fields after its final ')' are stable.
    return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="surrogateescape").rsplit(")", 1)[1].split()


def ancestors() -> set[int]:
    excluded = {1}
    pid = os.getpid()
    while pid > 1 and pid not in excluded:
        excluded.add(pid)
        try:
            pid = int(stat_fields(pid)[1])
        except (FileNotFoundError, ProcessLookupError):
            break
    return excluded


def processes() -> list[Process]:
    result = []
    excluded = ancestors()
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdecimal() or (pid := int(entry.name)) in excluded:
                continue
            base = Path(entry.path)
            try:
                fields = stat_fields(pid)
                if fields[0] in ("Z", "X"):
                    continue
                start = int(fields[19])
                uid = base.stat().st_uid
                comm = os.fsdecode((base / "comm").read_bytes().removesuffix(b"\n"))
                cmdline = (base / "cmdline").read_bytes()
                argv = tuple(os.fsdecode(v) for v in cmdline.removesuffix(b"\0").split(b"\0")) if cmdline else ()
                try:
                    exe = os.readlink(base / "exe")
                    cwd = os.readlink(base / "cwd")
                except PermissionError:
                    exe = cwd = ""
                if int(stat_fields(pid)[19]) == start:
                    result.append(Process(pid, start, uid, comm, argv, exe, cwd))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue  # Normal exit/visibility races during procfs traversal.
    return result


def script_arg(proc: Process) -> str:
    if not proc.argv:
        return ""
    interpreter = Path(proc.exe).name
    python = re.fullmatch(r"(?:python|pypy)\d*(?:\.\d+)*", interpreter)
    shell = interpreter in ("bash", "sh", "dash", "zsh")
    if not python and not shell:
        return ""  # Use argv matching for other interpreters/launchers.
    args = iter(proc.argv[1:])
    for arg in args:
        if arg == "--" or (interpreter == "zsh" and arg == "-b"):
            return next(args, "")
        if arg == "-":
            return ""  # stdin is not a script file.
        if arg.startswith("--"):
            if arg in ("--check-hash-based-pycs", "--rcfile", "--init-file"):
                next(args, None)
            continue
        if arg.startswith("-") or (shell and arg.startswith("+")):
            # Python short options can be bundled; W/X consume the remainder
            # or the next argument. c/m select code/module, not a file.
            for index, option in enumerate(arg[1:], 1):
                if (python and option in "cm") or (shell and arg[0] == "-" and option in "cs"):
                    return ""
                if (python and option in "WX") or (shell and option in "oO"):
                    if index == len(arg) - 1:
                        next(args, None)
                    break
            continue
        return arg
    return ""


def matches(target: Target, proc: Process) -> bool:
    if target.uid is not None and proc.uid != target.uid:
        return False
    match target.match:
        case "comm":
            return proc.comm == target.value
        case "exe":
            return proc.exe.removesuffix(" (deleted)") == target.value
        case "argv":
            needle = target.value
            return any(proc.argv[i:i + len(needle)] == needle for i in range(len(proc.argv) - len(needle) + 1))
        case "script":
            arg = script_arg(proc)
            if not arg:
                return False
            if "/" not in target.value:
                return Path(arg).name == target.value
            path = Path(arg)
            if not path.is_absolute():
                if not proc.cwd:
                    return False
                path = Path(proc.cwd) / path
            return str(path.resolve()) == target.value
    return False


def command(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=os.environ | {"LC_ALL": "C", "SYSTEMD_COLORS": "0"})
    except subprocess.TimeoutExpired as exc:
        raise Error(f"Command timed out after {timeout:g}s: {' '.join(args)}; stop jobs may still be running") from exc
    except OSError as exc:
        raise Error(f"Cannot execute {args[0]}: {exc}") from exc


def unit_states(kind: str, names: list[str], timeout: float) -> dict[str, str]:
    if not names:
        return {}
    # Exact names only. show is systemd's documented machine-readable interface.
    result = command(["systemctl", *(["--user"] if kind == "user" else []), "--no-pager", "show",
                      "--property=Id,LoadState,ActiveState", "--", *names], timeout)
    blocks = [dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
              for block in result.stdout.strip().split("\n\n") if block]
    if len(blocks) != len(names) or any("ActiveState" not in block or "LoadState" not in block for block in blocks):
        raise Error(f"Cannot query {kind} manager: {result.stderr.strip() or 'incomplete systemctl response'}")
    return {name: ("not-found" if block["LoadState"] == "not-found" else block["ActiveState"]) for name, block in zip(names, blocks, strict=True)}


def discover(targets: list[Target], settings: Settings) -> dict[Target, list[Process]]:
    snapshot = processes() if any(t.kind == "process" for t in targets) else []
    states = {}
    for kind in ("user", "system"):
        names = list(dict.fromkeys(u for t in targets if t.kind == kind for u in t.units))
        states[kind] = unit_states(kind, names, settings.unit_timeout)
    candidates = {}
    for target in targets:
        if target.kind == "process":
            hits = [p for p in snapshot if matches(target, p)]
            if hits:
                candidates[target] = hits
        elif any(states[target.kind][u] not in STOPPED for u in target.units):
            candidates[target] = []
    return candidates


def stop_units(kind: str, targets: list[Target], settings: Settings, auto: bool) -> dict[Target, str]:
    names = list(dict.fromkeys(u for t in targets for u in t.units))
    try:
        before = unit_states(kind, names, settings.unit_timeout)
    except Error as exc:
        return dict.fromkeys(targets, str(exc))
    # Missing optional group members are normal on differing installations.
    present = [u for u in names if before[u] != "not-found"]
    if not any(before[u] not in STOPPED for u in present):
        return dict.fromkeys(targets, "")
    args = ["systemctl", *(["--user"] if kind == "user" else []), "--no-pager", "--no-ask-password", "stop", "--", *present]
    if kind == "system" and os.geteuid() != 0:
        args = ["sudo", *(["-n"] if auto else []), "--", *args]
    error = ""
    try:
        result = command(args, settings.unit_timeout)
        if result.returncode:
            error = result.stderr.strip() or f"systemctl exited {result.returncode}"
        states = unit_states(kind, names, settings.unit_timeout)
    except Error as exc:
        return dict.fromkeys(targets, str(exc))
    # A failed stop command is reported even if units happened to be inactive.
    return {t: error or ("still active: " + ", ".join(u for u in t.units if states[u] not in STOPPED)
                        if any(states[u] not in STOPPED for u in t.units) else "") for t in targets}


def stop_processes(targets: list[Target], settings: Settings) -> dict[Target, str]:
    # Refresh after service stops and selection. Keep one pidfd per process even if
    # several selectors overlap. Recheck start time after opening to reject reuse.
    snapshot = processes()
    errors = dict.fromkeys(targets, "")
    handles = {}
    owners = {}
    poller = select.poll()
    try:
        for target in targets:
            for proc in snapshot:
                if not matches(target, proc):
                    continue
                if proc.pid in handles:
                    owners[proc.pid].append(target)
                    continue
                fd = None
                try:
                    fd = os.pidfd_open(proc.pid)
                    if int(stat_fields(proc.pid)[19]) != proc.start:
                        os.close(fd)
                        continue
                    handles[proc.pid] = fd
                    owners[proc.pid] = [target]
                    poller.register(fd, select.POLLIN)
                except (FileNotFoundError, ProcessLookupError):
                    if fd is not None:
                        os.close(fd)
                except OSError as exc:
                    if fd is not None:
                        os.close(fd)
                    errors[target] = f"PID {proc.pid}: {exc}"
        pending = {}
        now = time.monotonic()
        for pid, fd in handles.items():
            group = owners[pid]
            # Overlapping selectors must not silently override signal policy.
            policies = {(t.sig, t.timeout, t.force) for t in group}
            if len(policies) != 1:
                for t in group:
                    errors[t] = f"PID {pid}: overlapping selectors have conflicting termination policies"
                poller.unregister(fd)
                continue
            sig, timeout, force = policies.pop()
            try:
                signal.pidfd_send_signal(fd, sig)
                pending[fd] = (pid, now + (settings.kill_timeout if sig == signal.SIGKILL else timeout),
                               force and sig != signal.SIGKILL)
            except ProcessLookupError:
                poller.unregister(fd)
            except OSError as exc:
                poller.unregister(fd)
                for t in group:
                    errors[t] = f"PID {pid}: {exc}"
        while pending:
            deadline = min(deadline for _, deadline, _ in pending.values())
            for fd, event in poller.poll(max(0, int((deadline - time.monotonic()) * 1000 + 1))):
                if event & (select.POLLIN | select.POLLHUP):
                    pending.pop(fd, None)  # Includes exited but unreaped zombies.
                    poller.unregister(fd)
            now = time.monotonic()
            for fd, (pid, deadline, force) in list(pending.items()):
                if now < deadline:
                    continue
                if force:
                    try:
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                        pending[fd] = (pid, now + settings.kill_timeout, False)
                        continue
                    except ProcessLookupError:
                        pass
                    except OSError as exc:
                        for t in owners[pid]:
                            errors[t] = f"PID {pid}: {exc}"
                else:
                    for t in owners[pid]:
                        errors[t] = f"PID {pid}: did not exit before deadline"
                pending.pop(fd)
                poller.unregister(fd)
    finally:
        for fd in handles.values():
            os.close(fd)
    # Replacements are deliberately never hunted indefinitely. Report respawns.
    remaining = processes()
    for target in targets:
        if hits := [str(p.pid) for p in remaining if matches(target, p)]:
            errors[target] = errors[target] or "still running or respawned: PID " + ", ".join(hits)
    return errors


def choose(candidates: dict[Target, list[Process]]) -> list[Target]:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise Error("Interactive selection requires a terminal; use --auto or --list")
    # gum preselects labels, not values. Feed selected labels via stdin with
    # positional options so commas in target names do not pass through CSV.
    labels = {str(i): t for i, t in enumerate(candidates, 1)}
    displays = {key: f"[{key}] {t.name} ({t.kind})" for key, t in labels.items()}
    options = [f"{displays[key]}\t{key}" for key in labels]
    env = os.environ.copy()
    env.pop("GUM_CHOOSE_SELECTED", None)
    result = subprocess.run(["gum", "choose", "--no-limit", "--height=15", "--label-delimiter=\t",
                             "--input-delimiter=\n", "--output-delimiter=\n",
                             "--header=Select resources to stop (SPACE: toggle, ENTER: confirm)", "--", *options],
                            input="\n".join(displays[key] for key, t in labels.items() if t.default),
                            text=True, encoding="utf-8", stdout=subprocess.PIPE, env=env)
    if result.returncode in (1, 130):
        print("Cancelled.")
        return []
    if result.returncode:
        raise Error(f"gum choose exited {result.returncode}")
    try:
        return [labels[key] for key in result.stdout.splitlines() if key]
    except KeyError as exc:
        raise Error(f"Unexpected selection from gum: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("list.toml"))
    parser.add_argument("--auto", action="store_true", help="stop active default targets without a picker; sudo is noninteractive")
    parser.add_argument("--dry-run", action="store_true", help="show selection without stopping anything (uses defaults with --auto)")
    parser.add_argument("--list", action="store_true", help="list all active configured targets without stopping anything")
    parser.add_argument("--check-config", action="store_true", help="validate configuration without accessing processes or managers")
    parser.add_argument("--no-shell", action="store_true", help="exit after reporting instead of opening an interactive shell")
    args = parser.parse_args()
    settings, targets = config(args.config.expanduser())
    if args.check_config:
        print(f"Configuration OK: {len(targets)} targets.")
        return 0
    if args.auto and not args.list:
        targets = [t for t in targets if t.default]
    candidates = discover(targets, settings)
    if args.list:
        for target, hits in candidates.items():
            print(f"{'default' if target.default else 'optional'}: {target.name} ({target.kind})"
                  + (" PID " + ", ".join(str(p.pid) for p in hits) if hits else " " + ", ".join(target.units)))
        return 0
    if not candidates:
        print("All configured targets are inactive.")
        return 0
    selected = list(candidates) if args.auto else choose(candidates)
    if args.dry_run:
        for target in selected:
            print(f"Would stop: {target.name} ({target.kind})")
        return 0
    if not selected:
        return 0
    results = {}
    # Stop manager-owned units first so Restart= does not fight process signals.
    for kind in ("user", "system"):
        group = [t for t in selected if t.kind == kind]
        if group:
            results.update(stop_units(kind, group, settings, args.auto))
    group = [t for t in selected if t.kind == "process"]
    if group:
        results.update(stop_processes(group, settings))
    for target in selected:
        print(f"FAILED: {target.name} ({target.kind}): {results[target]}" if results[target]
              else f"Stopped: {target.name} ({target.kind})")
    failed = any(results.values())
    if settings.shell_after and not args.auto and not args.no_shell and sys.stdin.isatty() and sys.stdout.isatty():
        print("Session active. Type 'exit' to close.", flush=True)
        subprocess.run([os.environ.get("SHELL") or "/bin/bash"])
    return int(failed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Error, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        sys.exit(130)
