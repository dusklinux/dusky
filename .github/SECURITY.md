# Security Policy

Dusky installs a desktop environment, configures system services, and runs scripts with
`sudo`. That makes its security posture a real concern rather than a formality, so this
policy is specific rather than boilerplate.

---

## Supported versions

Dusky is a rolling configuration tracking Arch Linux, which is itself rolling. There are
no maintained release branches.

| Version | Supported |
|---|---|
| Latest `main` | ✅ Yes |
| Anything older | ❌ No — pull and re-run `ORCHESTRA.sh` |

Check what you are on with `cat ~/.config/dusky/version`.

---

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Use GitHub's private reporting, which goes only to the maintainer:

👉 **[Report a vulnerability privately](https://github.com/dusklinux/dusky/security/advisories/new)**

If that is unavailable to you, DM the maintainer on
[Discord](https://discord.gg/Nv2a7yTBQS) and ask for a private channel. Do not post
details in a public Discord channel.

**Please include:**

- What the issue is and what an attacker gains from it
- The specific file and line, if you have it
- Reproduction steps
- Your hardware/config if it is environment-specific

**What to expect:** this is a solo-maintained hobby project, not a company with an
on-call rotation. Realistically expect an initial response within about a week. You will
be credited in the fix unless you prefer otherwise. Please give a reasonable window for a
fix before disclosing publicly.

---

## What counts as a vulnerability here

Dusky is configuration and scripts, so the interesting classes are narrower than for an
application. In scope:

- **Privilege escalation** — a script that lets an unprivileged local user gain root
  beyond what the user already intended to authorise.
- **Unsafe `sudo` usage** — running more as root than necessary, writing root-owned files
  to user-writable paths, `sudo` on a path an attacker can influence.
- **Command injection** — unquoted expansion of filenames, network responses, clipboard
  contents, or window titles into a shell command.
- **Insecure temporary files** — predictable paths in `/tmp` that allow symlink attacks.
- **Credential exposure** — anything writing SSH keys, WireGuard private keys, Tailscale
  auth keys, or API tokens to world-readable locations, or logging them.
- **Unintended network exposure** — a setup script binding a service to `0.0.0.0` when it
  should be loopback or Tailscale-only, or opening firewall rules more broadly than stated.
- **Insecure downloads** — fetching and executing anything over plain HTTP, or piping an
  unverified remote payload into a shell.
- **Committed secrets** — any real key, token, or password found in this repo's history.

Out of scope:

- Vulnerabilities in upstream packages (Hyprland, Waybar, Rofi, the kernel). Report those
  upstream — though a heads-up is appreciated if Dusky's defaults make one materially worse.
- "Running the installer requires trusting the installer." That is inherent to the tool.
- The bare-repo install overwriting files in `$HOME`. This is documented, intended
  behaviour — see the warning in the README.
- Missing hardening you would like to see. That is a feature request, and a welcome one.

---

## Security-relevant things you should know as a user

These are not bugs. They are properties of what Dusky does, and you should understand
them before installing.

### The installer runs with elevated privileges

`ORCHESTRA.sh` orchestrates ~80 subscripts that install packages, enable systemd services,
and modify system configuration. Read what you run. Every subscript in
`~/user_scripts/arch_setup_scripts/scripts/` is a plain, individually readable file, and
that is deliberate.

### The install overwrites files in your home directory

```bash
git --git-dir=$HOME/dusky/ --work-tree=$HOME checkout -f
```

This force-checks-out tracked files into `$HOME`, overwriting existing ones without
prompting. Back up your existing configs first.

### AUR packages are built from source

Dusky uses `paru` to build several AUR packages with CPU-native flags. AUR packages are
user-submitted build scripts. This is normal Arch practice and carries the normal Arch
trust model.

### The wireless security tooling is for networks you own

`user_scripts/networking/airmon_ng.sh` and `airmon_ng_gpu.sh` wrap wireless auditing
tools. They exist for testing your own access points.

> Testing wireless networks you do not own or lack written authorisation to test is a
> criminal offence in most jurisdictions. The maintainer accepts no responsibility for
> misuse. If you do not have explicit authorisation for the network you are pointing this
> at, do not run it.

### Networking features change your exposure

The OpenSSH, FTP, WireGuard, Tailscale, VNC, and Cloudflare WARP helpers all alter what
your machine exposes to a network. Understand each before enabling it, particularly on
untrusted networks. Prefer Tailscale-scoped access over publicly-bound services.

### The offline ISO is distributed outside GitHub

The Dusky ISO is hosted on Google Drive. It is not signed or checksummed by any automated
release process. If you require verifiable provenance, build the ISO yourself from
`user_scripts/arch_iso_scripts/` rather than downloading the prebuilt image.

### Do not commit your own secrets

If you set up the personal-backup feature described in the README, `.git_dusky_list`
controls what gets pushed to *your* repository. Review it before pointing it at a public
remote — SSH keys, WireGuard configs, and drive UUIDs should not end up there.
