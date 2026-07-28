# Contributing to Dusky

Thanks for being here. Dusky is a large, opinionated Arch + Hyprland desktop built over
many months, and it is very much still growing. Bug reports, scripts, docs fixes and
typo corrections are all genuinely welcome.

This guide is longer than most because Dusky has one genuinely unusual property: **it is
deployed with a bare git repository straight into `$HOME`.** That makes "how do I edit
this safely" a real question, and getting it wrong can scatter files across your home
directory. Read the [Development setup](#development-setup) section before you start.

---

## Table of contents

- [Ways to help](#ways-to-help)
- [Before you open an issue](#before-you-open-an-issue)
- [Development setup](#development-setup)
- [Testing your change](#testing-your-change)
- [Repository layout](#repository-layout)
- [Shell script conventions](#shell-script-conventions)
- [Python conventions](#python-conventions)
- [Adding a new setup subscript](#adding-a-new-setup-subscript)
- [Adding a new config file](#adding-a-new-config-file)
- [Line endings](#line-endings)
- [Commits and pull requests](#commits-and-pull-requests)
- [Design principles](#design-principles)
- [Licensing](#licensing)

---

## Ways to help

You do not need to write shell to be useful here.

| Contribution | Notes |
|---|---|
| **Report a bug** | Use the [bug report form](https://github.com/dusklinux/dusky/issues/new?template=bug_report.yml). Hardware details matter enormously — see below. |
| **Fix a typo or broken doc** | Always welcome, no issue needed. The README and the `Documents/` vault both have rough edges. |
| **Test on hardware nobody has** | AMD-only laptops, hybrid AMD+NVIDIA, unusual displays. Reports of "this worked fine" are also useful data. |
| **Write a script** | See the conventions below. Small, single-purpose, invoked on demand. |
| **Improve the Obsidian vault** | `Documents/pensive/` is a real knowledge base people actually read. |
| **Answer questions on Discord** | Genuinely the highest-leverage thing, and it is where most support happens. |

---

## Before you open an issue

Roughly nine out of ten Dusky problems trace back to one of three things: **GPU vendor**,
**filesystem**, or **a partially completed `ORCHESTRA.sh` run**. Two steps solve most of
them before an issue is ever needed:

1. **Re-run the orchestrator.** It is idempotent by design and safe to run repeatedly:
   ```bash
   ~/user_scripts/arch_setup_scripts/ORCHESTRA.sh
   ```
2. **Run the failing subscript on its own.** Subscripts live in
   `~/user_scripts/arch_setup_scripts/scripts/` and each one is independently runnable.
   Running just the failing one usually surfaces a much clearer error than the full run.

If it still fails, open an issue with the subscript name and its full output.

---

## Development setup

**Do not develop inside the bare repo.** The bare clone described in the README is a
*deployment* mechanism — it checks tracked files directly into your home directory. It is
the right way to *install* Dusky and the wrong way to *edit* it.

### Recommended: normal clone, separate from your live config

```bash
git clone https://github.com/dusklinux/dusky.git ~/src/dusky
cd ~/src/dusky
git checkout -b my-change
```

Edit here, commit here, open the PR from here. Your running desktop stays untouched until
you deliberately test the change.

### If you are already running Dusky via the bare repo

Your live system *is* a checkout, so you can iterate in place and push from it — but be
deliberate about it, because `git checkout -f` against `$HOME` will overwrite local files
without asking.

```bash
# Convenience alias — add to your shell rc if you do this often.
alias dgit='git --git-dir=$HOME/dusky/ --work-tree=$HOME'

dgit status                 # see what you have actually changed
dgit diff                   # review before committing
dgit checkout -b my-change
```

> [!WARNING]
> `dgit checkout -f` and `dgit reset --hard` operate on your entire home directory.
> Take a snapshot first. If you installed via the standard route you already have Snapper
> and BTRFS, so this costs nothing:
> ```bash
> sudo snapper -c root create -d "before dusky edits"
> ```

---

## Testing your change

There is no way to unit-test "does this desktop feel right", so testing is mostly manual.
What matters is that you test *something* and say so in the PR.

**For scripts:**

```bash
bash -n path/to/script.sh          # syntax check — must pass, CI enforces it
shellcheck path/to/script.sh       # must be clean, CI enforces it on changed files
./path/to/script.sh                # actually run it
./path/to/script.sh                # run it AGAIN — it must be safe to re-run
```

That second run is not optional. Every script in Dusky is expected to be idempotent
because `ORCHESTRA.sh` re-runs the whole set and users are told re-running is safe.

**For setup subscripts**, test in a VM or against a fresh snapshot rather than your daily
driver. A GNOME Boxes or `virt-manager` Arch VM is enough for most of them; the
`Documents/pensive/linux/Important Notes/KVM/` notes cover setting one up.

**For the Python test suite** — `user_scripts/networking/network_throttle/` has real
pytest coverage:

```bash
cd user_scripts/networking/network_throttle
python -m pytest tests/ -v
```

**In your PR, state what you tested on:** GPU vendor, laptop or desktop, filesystem.
"Tested on AMD desktop, BTRFS" tells a reviewer far more than "works for me".

---

## Repository layout

Everything tracked here lands relative to `$HOME`.

```
.config/                     application configs (hypr, waybar, rofi, matugen, nvim, …)
├── dusky/version            current Dusky version string
├── hypr/                    Hyprland config, keybinds, animations, shaders
├── firefox_extentions/      bundled extensions incl. ai_bridge and dusky_sites
└── sddm/                    login theme (derived from SilentSDDM)

user_scripts/                all executable logic — the heart of the project
├── arch_setup_scripts/
│   ├── ORCHESTRA.sh         the conductor: runs ~80 subscripts in order
│   └── scripts/             the subscripts themselves, NNN_name.sh|py
├── arch_iso_scripts/        offline Dusky ISO build pipeline
├── tools/                   general-purpose utilities (largest directory)
├── dusky_tui/               terminal UIs for configuration
├── dusky_system/            core desktop behaviours
├── networking/              tailscale, ssh, wireguard, throttling, wifi tooling
├── drives/                  drive manager, health, formatting, BTRFS helpers
├── tts_stt/                 whisper / parakeet / kokoro speech pipelines
├── hypr/ waybar/ rofi/      per-component helper scripts
├── theme_matugen/           colour generation and template application
└── performance/ audio/ battery/ gaming/ …

Documents/pensive/           Obsidian knowledge vault — Arch, BTRFS, KVM, GPU passthrough
Pictures/readme_assets/      screenshots used by the README
.git_dusky_list              which paths a user's personal backup repo will track
```

---

## Shell script conventions

461 shell scripts already exist. Match them.

**Required:**

```bash
#!/usr/bin/env bash
set -euo pipefail
```

- `#!/usr/bin/env bash`, not `#!/bin/bash` — portability across Arch derivatives.
- `set -euo pipefail` at the top. Around half the tree predates this rule; new scripts
  are expected to have it, and adding it to a script you are already touching is welcome.
- **4-space indent**, spaces not tabs.
- **Quote every expansion**: `"$var"`, `"${arr[@]}"`, `"$(cmd)"`. Unquoted paths break the
  moment a user has a space in a filename.
- **`shellcheck`-clean.** CI runs ShellCheck at `--severity=error` on every shell file your
  PR touches, and that is a hard gate. A separate advisory job reports warnings across the
  whole repo without blocking — clearing warnings in files you touch is appreciated but
  not required.
- **Idempotent.** Guard before you act:
  ```bash
  command -v foo >/dev/null 2>&1 || paru -S --needed --noconfirm foo
  grep -q 'my_setting' "$conf" || printf 'my_setting=1\n' >> "$conf"
  ```
- **Never hardcode `/home/username`.** Use `$HOME` or `"${XDG_CONFIG_HOME:-$HOME/.config}"`.
- **Ask before destroying.** Anything that formats, deletes, or overwrites user data
  prompts first, and says exactly what it will touch.
- **Use `notify-send` for user-facing feedback** on scripts bound to a keybind — the user
  has no terminal open to read stdout.
- **`sudo` at the narrowest scope possible.** Do not run a whole script as root when three
  lines need it. Never re-exec the script as root implicitly.

---

## Python conventions

212 Python files, standard-library-first.

- Python 3, 4-space indent, `snake_case`.
- **Prefer the standard library.** A script that needs `pip install` becomes a support
  burden on a rolling-release distro. If you need a third-party package it must be
  available in the Arch repos or AUR, and the dependency must be installed by a setup
  subscript rather than assumed.
- Must pass `python -m py_compile` (CI enforces this on all Python files).
- Guard entry points with `if __name__ == "__main__":`.
- Keep TUIs responsive — no blocking calls on the render path.

---

## Adding a new setup subscript

Subscripts live in `user_scripts/arch_setup_scripts/scripts/` and are named
`NNN_short_description.sh` (or `.py`). The number sets execution order.

1. **Pick a number that reflects the dependency order.** Something that needs the network
   must sort after `003_network_connect.sh`; something that themes an app must sort after
   the app is installed. Leave gaps — use `145_`, not `144.5_`.
2. **Make it re-runnable.** Detect the already-done state and exit cleanly.
3. **Make it survivable.** If your subscript fails, the rest of the install should still
   complete. Do not `exit 1` out of the whole orchestrator for an optional feature.
4. **Be quiet on success, loud on failure.** Users watch ~80 of these scroll past.
5. **Test it twice** — fresh, then again immediately.

---

## Adding a new config file

Two steps, and the second one is easy to forget:

1. Add the file under `.config/` (or wherever it belongs relative to `$HOME`).
2. **Add its path to `.git_dusky_list`.** That file drives the personal-backup feature
   described in the README — anything not listed there will not be backed up by users who
   set up their own Dusky backup repo. A new config that is not in this list silently
   fails to persist for them.

---

## Line endings

All text files in this repo are LF. This is enforced by `.gitattributes`, and CI fails
any PR that introduces CRLF.

This is not stylistic. A shell script that reaches an Arch machine with CRLF endings dies
at exec time with `/usr/bin/env: 'bash\r': No such file or directory`, which is a
confusing failure to debug from the user side.

If you are on Windows or WSL, this is handled for you by `.gitattributes` — but verify:

```bash
file user_scripts/tools/some_script.sh     # must NOT say "with CRLF line terminators"
```

---

## Commits and pull requests

**Commits:** short, lowercase, present tense, describing the change. Match what is already
in the log — `fps limiter`, `gaming packages`, `sideload ios`. A prefix helps when the
scope is not obvious from the subject:

```
fix: waybar colours not regenerating after wallpaper change
feat: add fps limiter toggle to gaming menu
docs: correct drive_manager path in README
```

Conventional-commit prefixes are encouraged but not enforced.

**Pull requests:**

- One logical change per PR. A typo fix and a new feature should be two PRs.
- Fill in the PR template — especially the hardware you tested on.
- Include a screenshot or short recording for anything visual. This is a ricing project;
  reviewers need to see it.
- Do not commit generated artifacts. Anything listed in `.gitignore`
  (`matugen.kdl`, `foot-colors.ini`, `active.lua`, `libwaylandgrab.so`, `__pycache__/`)
  is generated at runtime and must stay out of the repo.
- Do not commit personal data — hostnames, SSH keys, Tailscale auth keys, WireGuard
  private keys, drive UUIDs, wallpapers you did not create.
- Draft PRs are welcome for work in progress.

Review is by the maintainer and can take a little while — Dusky is maintained alongside a
life. A ping on Discord after a week is completely fine.

---

## Design principles

Understanding these will save you a rejected PR.

1. **Lightweight is the point.** Idle RAM sits near 900 MB and disk near 5 GB, fully
   configured. Quickshell and similar heavyweight shells are *deliberately* not used;
   features are TUI- and script-based instead. A proposal adding a large always-running
   daemon needs to justify its footprint.
2. **Invoked, not resident.** Prefer a script that runs on a keybind over a process that
   runs forever.
3. **Idempotent everywhere.** Users are explicitly told re-running the installer is safe.
   That promise has to hold.
4. **Fail soft.** One broken component must not take the desktop with it.
5. **Auto-detect hardware, but leave the override.** Scripts detect Intel/AMD/NVIDIA, and
   users must still be able to set the variable by hand when detection is wrong — real
   hardware is messier than any detection heuristic.
6. **Matugen is the single source of colour.** New themed components read from Matugen
   templates rather than hardcoding a palette.

---

## Licensing

Dusky is [MIT licensed](../LICENSE). By submitting a pull request you agree that your
contribution is licensed under the same terms. There is no CLA.

If you are contributing code adapted from another project, say so in the PR and keep its
copyright notice intact — as Dusky does for
[SilentSDDM](https://github.com/uiriansan/SilentSDDM) and
[MatugenFox](https://github.com/Ubaidullah-Web-Dev/MatugenFox).

---

By participating you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).
