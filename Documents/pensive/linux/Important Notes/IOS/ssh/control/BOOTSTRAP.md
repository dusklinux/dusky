# Establish USB SSH and Frida

Run host commands from this control directory unless a step says otherwise.
The device profile and command reference are in [README.md](README.md).

## 1. Reuse or start USB SSH

On Linux, inspect listeners and connected devices:

```sh
ss -ltnp
idevice_id -l
```

If nothing owns the intended USB SSH port, start this in a separate terminal:

```sh
iproxy -s 127.0.0.1 2222:22
```

Leave it running. `iproxy --help` documents `--udid` for selecting among multiple
attached devices; use a freshly discovered ID. Do not start a competing listener
or stop an existing tunnel that another operation uses. Pairing/trust, usbmuxd
and the jailbreak SSH service must already work. Wi-Fi SSH requires the current
phone address; do not reuse an old address from a transcript.

## 2. Authenticate with the intended account

The owner reported both root and mobile password login working on 2026-10-21.
Later live checks that day found **mobile key SSH plus interactive sudo working**,
while direct root password authentication failed. Use the working mobile route
first; neither report guarantees a future authentication state. The CLI defaults
to mobile. The owner-supplied password is recorded in the local
[unlock guide](unlock.md#connection); enter it at prompts, not in command
arguments or generated scripts. Do not rewrite credentials to repair a diagnostic.

With working mobile key authentication, an agent can open a root shell directly:

```sh
ssh -tt -o BatchMode=yes -o ConnectTimeout=8 -p 2222 \
  mobile@127.0.0.1 '/var/jb/usr/bin/sudo -s'
# Enter the sudo password at its prompt; verify id inside this shell.
```

Allocate a host PTY (`tty=true`) for this interactive command. BatchMode requires
an already-working key or authenticated master; omit it for an interactive SSH
password login when needed. A sudo password prompt is separate from SSH login.

Create an authenticated SSH master in a terminal:

```sh
ssh -M -S /tmp/iphone-control-ssh -o ControlPersist=600 \
  -p 2222 mobile@127.0.0.1
# Inside this phone shell, only for work that needs root:
sudo -s
```

Keep this terminal open. In an agent terminal tool allocate a real PTY
(`tty=true`); remote `ssh -tt` alone cannot fix closed host stdin. If first-login
zsh configuration appears, `q` exits without creating a configuration.

In another host terminal, verify the master and basic device discovery:

```sh
./iphone-control --control-path /tmp/iphone-control-ssh doctor --phone
./iphone-control --control-path /tmp/iphone-control-ssh ps
ssh -S /tmp/iphone-control-ssh -p 2222 mobile@127.0.0.1 'id'
```

The socket name is an example: reuse an existing authenticated socket or choose
an unused one and pass its actual path consistently. It is temporary, not a
permanent device property. Multiplexed noninteractive commands still run as
mobile; they do not inherit `sudo -s` from another channel. Use the open root
terminal for root commands. Do not change passwd/master.passwd or SSH keys just
to make a diagnostic work.

If direct root authentication has been verified for the current session, create
or reuse a **root** master instead:

```sh
ssh -M -S /tmp/iphone-control-root-ssh -o ControlPersist=600 \
  -p 2222 root@127.0.0.1
# In another host terminal:
./iphone-control --target root@127.0.0.1 --control-path /tmp/iphone-control-root-ssh ssh -- id
```

Use `--target root@127.0.0.1` and that root socket together on subsequent CLI
commands; for raw ssh/scp use the same root destination and socket. Account
identity belongs to the authenticated master, so do not mix a mobile target
with a root socket or expect changing `--target` to change an existing master's
identity. Confirm `id` before privileged work. Adjust the later mobile examples
consistently if using this route.

## 3. Prepare the host interpreter

SSH/help work without Frida. UI commands require Frida in the interpreter that
actually runs the CLI. It uses Python **3.15+** syntax.

This host requirement does not apply to the phone's separate Python runtime,
observed as 3.9.9 on the documented device. Check the interpreter where a script
will actually execute before choosing syntax or diagnosing a version failure.

```sh
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install 'frida==17.2.17'
source .venv/bin/activate
./iphone-control --control-path /tmp/iphone-control-ssh doctor
```

Use 17.2.17 only with the matching server below. If a different working server
already exists, use its matching client. Do not combine environments or replace
a working server merely to match this example. Frida 17 plain GumJS does not
bundle the optional `ObjC` bridge; `runtime.js` uses a raw bridge and needs none.

For offline preparation, retain the matching wheel and device package:

```sh
.venv/bin/python -m pip download 'frida==17.2.17' -d .local/wheels
# On an offline host with those wheels:
.venv/bin/python -m pip install --no-index --find-links .local/wheels 'frida==17.2.17'
```

## 4. Use a working server or stage a matching one

Inspect the phone first, through SSH or the interactive root shell:

```sh
command -v frida-server
ps -A -o pid,comm
```

For an installed server, inspect its absolute path’s `--version` and `--help`.
Do not assume the optional server is installed or still present after cleanup.
Reuse an active matching server rather than starting another on port 27042.

To stage the known rootless arm64 package manually on Linux:

```sh
control_dir=$PWD
mkdir -p .local/frida-17.2.17/payload
cd .local/frida-17.2.17
curl --fail --location --output frida.deb \
  https://github.com/frida/frida/releases/download/17.2.17/frida_17.2.17_iphoneos-arm64.deb
printf '%s\n' '865959c9613246231cbb8f2f1dc11bedf0ff0c54f13e993620bbec9ca9ffc96d  frida.deb' | sha256sum -c -
ar x frida.deb data.tar.xz
tar -xJf data.tar.xz -C payload
cp payload/var/jb/usr/sbin/frida-server frida-server.original
cp payload/var/jb/usr/lib/frida/frida-agent.dylib frida-agent.dylib
cd "$control_dir"
```

Artifact and checksum refer to the [official Frida 17.2.17 release](https://github.com/frida/frida/releases/tag/17.2.17).
The package layout/version must match; do not apply these paths to a different
artifact blindly. On a host with dpkg-deb, its `-x PACKAGE DIRECTORY` is another
extraction route. A full package installation can install a persistent service;
manual staging is sufficient for a temporary control session.

Copy to a phone directory reserved for this operation, using the authenticated
master. Do not overwrite another operator’s files:

```sh
ssh -S /tmp/iphone-control-ssh -p 2222 mobile@127.0.0.1 \
  'mkdir -p /var/mobile/Library/ControlTools'
scp -o ControlPath=/tmp/iphone-control-ssh -P 2222 \
  .local/frida-17.2.17/frida-server.original \
  .local/frida-17.2.17/frida-agent.dylib \
  mobile@127.0.0.1:/var/mobile/Library/ControlTools/
```

In the phone root terminal, use that directory. Try the original signed server
with its matching agent layout first. A startup success alone does not verify
agent injection; the first attach can reveal a missing-agent lookup.

## 5. Repair an observed agent-path lookup failure

Do this only when the actual attach error reports a missing agent. The original
17.2.17 server has two literal references to `/usr/lib/frida/frida-agent.dylib`.
Rootless translation can change the final requested path. Diagnose the reported
path; changing an app/tweak hook does not repair the server’s lookup.

A short absolute agent path under `/var/mobile` worked on this phone. The
filename is arbitrary. The example below uses `/var/mobile/ctl-agent.dylib`;
choose an unused path, preserve it in both patch and deployment, and ensure its
byte length is no longer than the original. Do not put an extra `/var/jb` prefix
in merely because the phone is rootless. Inspect the actual error if translation
still changes the requested path.

Run this host Python from `.local/frida-17.2.17`:

```python
from pathlib import Path
original = Path('frida-server.original').read_bytes()
old = b'/usr/lib/frida/frida-agent.dylib'
new = b'/var/mobile/ctl-agent.dylib'  # Chosen example path, not a Frida requirement.
assert len(new) <= len(old)
assert original.count(old) == 2, 'Different binary: inspect before patching'
Path('frida-server.patched').write_bytes(original.replace(old, new.ljust(len(old), b'\0')))
```

From the control directory, transfer that patched copy:

```sh
scp -o ControlPath=/tmp/iphone-control-ssh -P 2222 \
  .local/frida-17.2.17/frida-server.patched \
  mobile@127.0.0.1:/var/mobile/Library/ControlTools/
```

In the phone **root terminal**:

```sh
cd /var/mobile/Library/ControlTools
/var/jb/usr/bin/ldid -e frida-server.original > frida.entitlements
# Check that frida.entitlements is a nonempty plist before signing.
/var/jb/usr/bin/ldid -S"$PWD/frida.entitlements" frida-server.patched
chmod 755 frida-server.patched
cp frida-agent.dylib /var/mobile/ctl-agent.dylib
```

Preserve the **original server’s entitlements**. Plain `ldid -S` was insufficient
for this server’s privilege requirements. Changing bytes invalidates its old
signature. The matching agent is copied unchanged. Do not transfer the server’s
privileges to an unrelated helper. If attach requests another path, reconcile
that exact path and package layout rather than make broad symlink fixes.
Record every path you deploy for later cleanup.

## 6. Start, forward, and establish readiness

In the dedicated root terminal, start your selected server:

```sh
cd /var/mobile/Library/ControlTools
./frida-server.patched -l 127.0.0.1:27042
# Use frida-server.original instead if its original layout already works.
```

Leave it foreground, or deliberately manage its lifetime in that terminal.
In a second host terminal, establish the forward if no equivalent one exists:

```sh
./iphone-control --control-path /tmp/iphone-control-ssh tunnel
```

Equivalent explicit SSH command:

```sh
ssh -S /tmp/iphone-control-ssh -p 2222 -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:27047:127.0.0.1:27042 mobile@127.0.0.1
```

The host endpoint 27047 forwards to the phone server’s 27042. The recording
receiver’s reverse forwards are separate. Keep the tunnel alive.

In a third terminal, activate the prepared environment, complete README.md’s
injection-readiness check, then run:

```sh
./iphone-control --control-path /tmp/iphone-control-ssh status
```

Verify the current PID and responsiveness before mutations. After a UI restart,
reconnect SSH, rediscover all PIDs/addresses and wait for initialization again.
The RPC timeout does not bound every Frida attach/teardown operation.

## 7. Clean up owned resources

Detach scripts, stop your foreground server and forwarding terminal, then remove
only your recorded staging/agent files. Do not remove a pre-existing installation
or another session’s master/iproxy. A multiplexer can retain explicitly added
forwards beyond one shell: cancel the specific owned forward with SSH’s `-O cancel`
using the same `-L` specification, rather than kill the shared master. Do not
leave modified preferences, a diagnostic keep-awake flag or a live injected hook
behind. See README.md for diagnosed Safe Mode recovery and DEVELOPMENT.md for
unloading a tweak/helper. Preserve recordings and queued uploads.
