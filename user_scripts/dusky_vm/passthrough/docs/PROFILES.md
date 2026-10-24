# VM setup profiles

`000_dusky_vm.py` replaces `000_dusky_vm.sh`. Run it as your normal user with
Python 3.15+. Individual setup scripts handle their own sudo prompts. The runner
uses the Python standard library and the `profile/` TOML files beside it.

From the `passthrough/` project root:

```sh
./000_dusky_vm.py --linux
./000_dusky_vm.py --windows
```

With no selection, an interactive terminal offers a numbered profile menu;
Enter selects the first profile, Linux. Noninteractive invocation requires an
explicit selection. These select the same profile:

```sh
./000_dusky_vm.py --profile linux
./000_dusky_vm.py --profile 'Arch Linux'
./000_dusky_vm.py --profile 01_arch_linux.toml
./000_dusky_vm.py --profile 1
```

Names, filenames, stems and unique substrings follow the reference installation
orchestrator's selection rules. Ambiguous matches fail. An explicit TOML path
also works. Script/search-directory paths are relative to the runner, so the
profiles work when the launcher is invoked from another working directory.

## Included sequences

| Profile | Resumable host setup | Runs each invocation |
|---|---|---|
| Linux | `05 --skip-virtio`, `10`, `20 --nat` | `30` Linux deployment |
| Windows 11 | `05 --virtio`, `07`, `10`, `15`, `20`, `windows/25` | `30 --windows` |

Linux uses its deployment script's storage selection, virtual GPU and VirtioFS
configuration. Its NAT step preserves an existing `default` network and creates
one on a fresh host. Windows retains the earlier interactive VFIO, networking
and Looking Glass sequence. Its guest modernization remains a separate task.
Review the Windows profile before running its hardware-specific setup.

Package staging and availability are handled by the existing `05` script;
the runner does not change its repository/AUR or offline behavior.

## Preview, resume and forward VM arguments

```sh
./000_dusky_vm.py --list
./000_dusky_vm.py --linux --dry-run
./000_dusky_vm.py --windows --dry-run
./000_dusky_vm.py --linux --setup-only
./000_dusky_vm.py --linux --deploy-only
./000_dusky_vm.py --linux --manual
```

The full selected manifest is validated before any task executes. `--dry-run`
prints commands and recorded completion without executing children, invoking
sudo or creating state/log files. Child argument validation happens when the
child is run; the runner validates task files, profile fields and interpreters.

Arguments after `--` go only to the deployment task:

```sh
./000_dusky_vm.py --linux -- --iso /mnt/zram1/dusky_10_26.iso \
    --storage /mnt/zram1 --name dusky-linux-next

# Current development machine's temporary distro-tool Python override:
./000_dusky_vm.py --linux --deploy-only -- --tool-python /usr/bin/python3

./000_dusky_vm.py --linux --deploy-only -- --help
./000_dusky_vm.py --windows --deploy-only -- --help
```

The profiles share the same runner and existing setup scripts. Each guest
workflow retains its own options; consult its help before forwarding flags.
Use a new VM name when creating another Linux VM. Existing domains can use
the deployment script's `--configure`, `--ssh` and other management options
through `--deploy-only --`.

Successful host tasks receive small atomic completion receipts under
`$XDG_STATE_HOME/dusky-vm/setup` (default `~/.local/state/dusky-vm/setup`). A receipt
identifies the profile path, script path/content, arguments, mode and duplicate
occurrence. Failed, interrupted and manually skipped tasks stay pending. Setup
progress is separate from the old Bash launcher's global `.install_state` file;
that old file is left in place and is not imported into either profile.

The deployment task has `always = true`: successful host setup is skipped on
later invocations, while VM creation/management remains available every time.
Changed setup script contents or profile task arguments invalidate that task's
receipt. To deliberately rerun setup:

```sh
./000_dusky_vm.py --linux --reset           # Clear this profile's receipts; exit.
./000_dusky_vm.py --linux --reset-and-run   # Clear receipts, then run.
```

`--state-dir PATH` overrides receipt storage, including a RAM-backed directory
when persistent progress is unwanted. Per-run command/status reports go under
`$XDG_RUNTIME_DIR/dusky-vm/setup` in RAM. Interactive output stays in the terminal;
the report does not capture child terminal output or password input.

There is no runner execution lock. Receipts are separate files published by
atomic rename, so concurrent runs cannot overwrite an unrelated task's receipt.
Concurrent runs can execute the same pending setup task twice; leaf tools keep
their own package-manager and VM-disk coordination. A failed task stops the
sequence with its exit code. Ctrl-C and termination stop the active child group;
successful earlier tasks remain recorded.

## Editing a profile

The supported format is a small subset of the reference orchestrator's task
tables, with `forward_args` added for the VM launcher:

```toml
[profile]
name = "Arch Linux"
description = "Linux VM setup"

[search_dirs]
dirs = ["."]

[sequence]
tasks = [
    { script = "05_virtio_iso.py", args = ["--skip-virtio"], mode = "U" },
    { script = "30_kvm_vm_deploy.py", always = true, forward_args = true },
]
```

`mode` defaults to `U`; `S` adds sudo. `args` is an array of literal arguments,
including arguments containing spaces. At most one task can receive forwarded
arguments, and that task must use `always = true`. Unknown fields and invalid
types are errors. Duplicate filenames across search directories need an explicit
script path. Python tasks use the runner's interpreter; Bash tasks use installed
Bash. No setup modules are imported into the runner.

The reference's Git updates, SQLite state store, prompt auto-answering, password
helpers and Textual dashboard are outside this runner's scope. TOML parsing uses
[Python 3.15 tomllib](https://docs.python.org/3.15/library/tomllib.html), and child
execution uses documented [subprocess process groups](https://docs.python.org/3.15/library/subprocess.html#popen-constructor).

Run isolated regressions with scratch files in RAM:

```sh
TMPDIR=/mnt/zram1 python3 -B -m unittest discover -s tests -v
```
