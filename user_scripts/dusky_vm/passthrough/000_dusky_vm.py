#!/usr/bin/env python3
"""Run the Linux or Windows VM setup profile with normal terminal interaction.

Adapted from arch_setup_scripts/orchestrator.py: TOML task tables, profile
selection, manifest validation, content-aware progress and foreground execution.
No Git integration, TUI framework, password storage or execution lock.
"""

import argparse
import datetime
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import tomllib

SCRIPT_DIR = Path(__file__).resolve().parent
PROFILES_DIR = SCRIPT_DIR / "profile"

# The reference orchestrator's foreground launcher, without its askpass branch.
# The child claims the terminal before a prompt can read from a background group.
CHILD_LAUNCHER = """
import os, signal, sys
if sys.stdin.isatty():
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    os.tcsetpgrp(0, os.getpgrp())
    signal.signal(signal.SIGTTOU, signal.SIG_DFL)
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
"""


@dataclass(frozen=True, slots=True)
class Task:
    script: Path
    args: tuple[str, ...]
    mode: str
    always: bool
    forward_args: bool
    key: str
    prompt: str = ""

    def command(self, extra: list[str]) -> list[str]:
        if self.script.suffix == ".py":
            interpreter = [sys.executable]
        elif self.script.suffix == ".sh":
            bash = shutil.which("bash")
            if not bash:
                raise ValueError(f"Bash is required for {self.script}")
            interpreter = [bash]
        elif os.access(self.script, os.X_OK):
            interpreter = []
        else:
            raise ValueError(f"Unsupported non-executable task: {self.script}")
        prefix = ["sudo", "--"] if self.mode == "S" else []
        return [*prefix, *interpreter, str(self.script), *self.args,
                *(extra if self.forward_args else [])]


@dataclass(frozen=True, slots=True)
class Profile:
    filepath: Path
    name: str
    description: str
    tasks: tuple[Task, ...]

    @property
    def state_name(self) -> str:
        ident = hashlib.blake2b(os.fsencode(self.filepath), digest_size=8).hexdigest()
        return f"{self.filepath.stem}-{ident}"


def resolve_home(value: str, base: Path = SCRIPT_DIR) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def check_keys(table: dict, allowed: set[str], context: str) -> None:
    unknown = table.keys() - allowed
    if unknown:
        raise ValueError(f"{context}: unknown fields: {', '.join(sorted(unknown))}")


def load_profile(filepath: Path) -> Profile:
    with filepath.open("rb") as stream:
        data = tomllib.load(stream)
    check_keys(data, {"profile", "search_dirs", "sequence"}, str(filepath))
    for section in ("profile", "search_dirs", "sequence"):
        if not isinstance(data.get(section, {}), dict):
            raise ValueError(f"{filepath}: [{section}] must be a table")
    info = data.get("profile", {})
    check_keys(info, {"name", "description"}, "profile")
    name = info.get("name", filepath.stem)
    description = info.get("description", "")
    if not isinstance(name, str) or not name.strip() or not isinstance(description, str):
        raise ValueError(f"{filepath}: invalid profile name/description")
    search = data.get("search_dirs", {})
    check_keys(search, {"dirs"}, "search_dirs")
    dirs = search.get("dirs", ["."])
    if not isinstance(dirs, list) or any(not isinstance(d, str) or not d for d in dirs):
        raise ValueError(f"{filepath}: search_dirs.dirs must contain directory strings")
    search_dirs = tuple(dict.fromkeys(resolve_home(d) for d in dirs))
    sequence = data.get("sequence", {})
    check_keys(sequence, {"tasks"}, "sequence")
    tables = sequence.get("tasks", [])
    if not isinstance(tables, list) or not tables:
        raise ValueError(f"{filepath}: sequence.tasks must be a nonempty array of tables")
    tasks = []
    occurrences: dict[tuple, int] = {}
    for index, table in enumerate(tables, 1):
        context = f"{filepath.name}, task {index}"
        if not isinstance(table, dict):
            raise ValueError(f"{context}: expected a table")
        check_keys(table, {"script", "args", "mode", "always", "forward_args", "prompt"}, context)
        prompt = table.get("prompt", "")
        if not isinstance(prompt, str):
            raise ValueError(f"{context}: prompt must be a string")
        script = table.get("script")
        args = table.get("args", [])
        mode = table.get("mode", "U")
        if not isinstance(script, str) or not script or "\0" in script:
            raise ValueError(f"{context}: script must be a nonempty path")
        if not isinstance(args, list) or any(not isinstance(a, str) or "\0" in a for a in args):
            raise ValueError(f"{context}: args must be an array of strings")
        if mode not in ("U", "S"):
            raise ValueError(f"{context}: mode must be U or S")
        for key in ("always", "forward_args"):
            if not isinstance(table.get(key, False), bool):
                raise ValueError(f"{context}: {key} must be a boolean")
        if table.get("forward_args") and not table.get("always"):
            raise ValueError(f"{context}: forward_args requires always = true")
        candidates = ([resolve_home(script)] if "/" in script else
                      [d / script for d in search_dirs])
        matches = list(dict.fromkeys(p.resolve() for p in candidates if p.is_file()))
        if len(matches) != 1:
            raise ValueError(f"{context}: expected one readable match for {script}, found {len(matches)}")
        path = matches[0]
        checksum = hashlib.blake2b(path.read_bytes(), digest_size=16).hexdigest()
        identity = (mode, str(path), tuple(args))
        occurrence = occurrences.get(identity, 0)
        occurrences[identity] = occurrence + 1
        fields = [identity, checksum, occurrence, table.get("always", False), table.get("forward_args", False)]
        if prompt:
            fields.append(prompt)
        material = json.dumps(fields).encode()
        key = hashlib.blake2b(material, digest_size=16).hexdigest()
        task = Task(path, tuple(args), mode, table.get("always", False),
                    table.get("forward_args", False), key, prompt)
        task.command([])  # Validate every interpreter before executing any task.
        tasks.append(task)
    if sum(task.forward_args for task in tasks) > 1:
        raise ValueError(f"{filepath}: only one task may receive forwarded arguments")
    return Profile(filepath.resolve(), name.strip(), description, tuple(tasks))


def select_profile(paths: list[Path], query: str) -> Path:
    # Same number/exact/unique-substring selection as the reference orchestrator.
    if query.isdecimal() and 1 <= int(query) <= len(paths):
        return paths[int(query) - 1]
    if "/" in query:
        path = resolve_home(query, Path.cwd())
        if path.is_file():
            return path
        raise ValueError(f"Profile does not exist: {path}")
    names = []
    for path in paths:
        with path.open("rb") as stream:
            info = tomllib.load(stream).get("profile", {})
        if not isinstance(info, dict):
            raise ValueError(f"{path}: [profile] must be a table")
        names.append((path, (path.name.casefold(), path.stem.casefold(),
                             str(info.get("name", path.stem)).casefold())))
    key = query.casefold()
    exact = [p for p, labels in names if key in labels]
    matches = exact or [p for p, labels in names if any(key in label for label in labels)]
    if len(matches) != 1:
        raise ValueError(f"Profile {query!r}: expected one match, found {len(matches)}")
    return matches[0]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".vm-progress-", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Interrupted(Exception):
    def __init__(self, signum: int):
        self.signum = signum


def execute(command: list[str], cwd: Path, env: dict[str, str]) -> int:
    tty = sys.stdin.fileno() if sys.stdin.isatty() else None
    old_group = os.tcgetpgrp(tty) if tty is not None else None
    old_attributes = termios.tcgetattr(tty) if tty is not None else None
    proc = None
    try:
        proc = subprocess.Popen([sys.executable, "-c", CHILD_LAUNCHER, *command],
                                cwd=cwd, env=env, process_group=0)
        result = proc.wait()
        return 128 - result if result < 0 else result
    finally:
        if proc is not None and proc.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        if tty is not None:
            previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            try:
                os.tcsetpgrp(tty, old_group)
                termios.tcsetattr(tty, termios.TCSANOW, old_attributes)
            finally:
                signal.signal(signal.SIGTTOU, previous)


def run_profile(profile: Profile, tasks: tuple[Task, ...], state: Path,
                extra: list[str], dry_run: bool, manual: bool) -> int:
    receipts = state / profile.state_name
    plan = []
    for task in tasks:
        receipt = receipts / f"{task.key}.json"
        done = receipt.is_file() and not task.always
        plan.append((task, receipt, done, task.command(extra)))
    print(f"Profile: {profile.name}\n{profile.description}\nSetup progress: {receipts}", flush=True)
    for index, (task, _receipt, done, command) in enumerate(plan, 1):
        label = "done" if done else ("always" if task.always else "pending")
        print(f"  {index}. [{task.mode}, {label}] {shlex.join(command)}", flush=True)
    if dry_run:
        print("Validated; no scripts executed or files written.")
        return 0
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    report_path = runtime / "dusky-vm/setup" / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json"
    report = {"profile": str(profile.filepath), "tasks": []}
    env = os.environ | {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
                        "DUSKY_VM_PROFILE": str(profile.filepath)}
    result = 0
    print(f"Run report (RAM): {report_path}", flush=True)
    try:
        for task, receipt, done, command in plan:
            if done:
                continue
            if task.prompt:
                while True:
                    try:
                        answer = input(task.prompt + " [y/N]: ").strip().casefold()
                    except EOFError:
                        raise ValueError(f"{task.script.name} needs an interactive choice.") from None
                    if answer in {"", "n", "no", "y", "yes"}:
                        break
                    print("Enter y or n.")
                if answer not in {"y", "yes"}:
                    report["tasks"].append({"command": command, "status": "declined"})
                    if not task.always:
                        write_json(receipt, {"command": command, "selected": False})
                    continue
            if manual:
                answer = input(f"Run {task.script.name}? [Enter=run / s=skip / q=quit]: ").strip().casefold()
                if answer in {"q", "quit"}:
                    return 130
                if answer in {"s", "skip"}:
                    report["tasks"].append({"command": command, "status": "skipped"})
                    continue
                if answer:
                    raise ValueError("Expected Enter, s or q")
            entry = {"command": command, "status": "interrupted"}
            report["tasks"].append(entry)
            print(f"\n>>> {shlex.join(command)}", flush=True)
            result = execute(command, task.script.parent, env)
            entry.update(status="completed" if result == 0 else "failed", exit_code=result)
            if result:
                print(f"Stopped: {task.script.name} exited with {result}; rerun to resume.", file=sys.stderr)
                return result
            if not task.always:
                write_json(receipt, {"command": command, "completed": datetime.datetime.now(datetime.UTC).isoformat()})
        print("Profile finished.", flush=True)
        return result
    finally:
        write_json(report_path, report)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    separator = argv.index("--") if "--" in argv else len(argv)
    options, extra = argv[:separator], argv[separator + 1:]
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
        epilog="VM arguments follow --, e.g. --linux --deploy-only -- --iso /path/dusky.iso")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--profile", "-p", help="profile name, filename, number or TOML path")
    selection.add_argument("--linux", action="store_const", const="linux", dest="profile")
    selection.add_argument("--windows", action="store_const", const="windows", dest="profile")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="list profiles")
    action.add_argument("--dry-run", action="store_true", help="validate and preview without writes or execution")
    action.add_argument("--reset", action="store_true", help="clear selected profile's progress and exit")
    action.add_argument("--reset-and-run", action="store_true", help="clear progress, then run")
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument("--setup-only", action="store_true", help="run resumable host setup tasks only")
    phase.add_argument("--deploy-only", action="store_true", help="run always tasks, bypass host setup")
    parser.add_argument("--manual", action="store_true", help="prompt before each pending task")
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get(
        "XDG_STATE_HOME", Path.home() / ".local/state")) / "dusky-vm/setup",
        help="setup receipts directory; default XDG_STATE_HOME/dusky-vm/setup")
    args = parser.parse_args(options)
    paths = sorted(PROFILES_DIR.glob("*.toml"))
    try:
        if sys.version_info < (3, 15):
            raise ValueError("Python 3.15+ is required.")
        if args.list:
            if extra:
                raise ValueError("--list does not accept VM arguments")
            for index, path in enumerate(paths, 1):
                with path.open("rb") as stream:
                    info = tomllib.load(stream).get("profile", {})
                if not isinstance(info, dict):
                    raise ValueError(f"{path}: [profile] must be a table")
                print(f"{index}. {path.stem}: {info.get('name', path.stem)} — {info.get('description', '')}")
            return 0
        if not args.profile:
            if not sys.stdin.isatty():
                raise ValueError("Select --linux, --windows or --profile NAME.")
            for index, path in enumerate(paths, 1):
                print(f"{index}. {path.stem}")
            args.profile = input("VM profile [1]: ").strip() or "1"
        profile = load_profile(select_profile(paths, args.profile))
        tasks = tuple(t for t in profile.tasks if not (
            (args.setup_only and t.always) or (args.deploy_only and not t.always)))
        if extra and (args.reset or not any(t.forward_args for t in tasks)):
            raise ValueError("This selection has no task accepting VM arguments after --.")
        if not args.dry_run and os.geteuid() == 0:
            raise ValueError("Run as your normal user; individual tasks handle sudo.")
        if args.manual and not sys.stdin.isatty():
            raise ValueError("--manual requires an interactive terminal")
        state = args.state_dir.expanduser().resolve()
        if args.reset or args.reset_and_run:
            receipts = state / profile.state_name
            if receipts.is_dir():
                for receipt in receipts.glob("*.json"):
                    receipt.unlink()
            print(f"Reset setup progress for {profile.name}.")
            if args.reset:
                return 0
        return run_profile(profile, tasks, state, extra, args.dry_run, args.manual)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Interrupted as exc:
        return 128 + exc.signum
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted; completed setup tasks remain recorded.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    def interrupted(signum, _frame):
        raise Interrupted(signum)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    raise SystemExit(main())
