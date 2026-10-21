# Establishing the control environment

Read [README.md](README.md) first. This is the known working setup for this
rootless iOS 16 phone. It documents earlier live procedures; the notes review
itself did not install/start a server or interrupt the user's capture.

## 1. Keep the three connections distinct

| Purpose | Host endpoint | Phone endpoint / owner |
|---|---|---|
| USB SSH | 127.0.0.1:2222 | iproxy → phone SSH 22 |
| Optional Frida UI control | 127.0.0.1:27047 | SSH local forward → phone Frida 27042 |
| USB capture / preview | FTP 2240 + passive 2241–2248; HTTP 8765 | Receiver owns SSH reverse forwards to phone loopback |

An active SSH connection does not imply Frida or USB recording is ready.
Inspect existing listeners/processes first, e.g. host `ss -ltnp`, `pgrep -af iproxy`,
`systemctl --user status duskie-usb`. Do not replace a service owned by another
session. Read-only `./iphone-control ps` is a useful first phone check.

USB SSH is preferred. If absent, leave `iproxy -s 127.0.0.1 2222:22` running in
its own terminal (syntax checked against installed help). Pairing/trust,
libusbmuxd and phone jailbreak SSH must already work. With multiple devices,
discover the correct UDID and use iproxy's `--udid`; never reuse a saved identifier.
Wi-Fi SSH is an alternative when the current phone address is known; old `.111`
and `.226` addresses in transcripts are not reliable defaults.

## 2. Authentication and root privilege

Preferred noninteractive account is `mobile`, using the existing host SSH key.
CLI SSH/scp uses BatchMode; test interactively if keys are not ready:

```sh
ssh -tt -p 2222 mobile@127.0.0.1
# In that device shell, only when root is needed:
sudo -s
```

The owner-provided password remains in the existing local [unlock.md](unlock.md)
and session instructions; the SSH password is not an iPhone UI passcode. Direct
root password login was owner-verified earlier, but the main control work used
mobile + sudo. Do not change passwd/master.passwd or SSH keys just to get a probe
working. Enter passwords at the prompt, never in argv or committed scripts.
When running through an agent's PTY tool, use **tty=true** for the interactive
session; `ssh -tt` alone does not make closed tool stdin usable. Keep the root
terminal open for signing and the foreground server.

## 3. Host client and offline preparation

The CLI uses Python 3.15 syntax. From this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'frida==17.2.17'
.venv/bin/python ./iphone-control doctor
```

This was actually prepared on 2026-10-21. The default system interpreter still
need not have Frida. Invoke `.venv/bin/python ./iphone-control ...` for Frida
commands. A former Python 3.14 environment cannot parse this CLI. Do not combine
site-packages from different environments or replace another session's runtime.

For offline use, download the matching wheel ahead of time with
`.venv/bin/python -m pip download 'frida==17.2.17' -d .local/wheels`, then install
with `--no-index --find-links .local/wheels`. Save the device package and this
folder too; `/mnt/zram1` build artifacts are not durable prerequisites.

Frida 17 no longer embeds the optional ObjC bridge in plain GumJS. The bundled
raw runtime avoids that dependency. A random old script using `ObjC.classes`
may require a separately bundled frida-objc-bridge; don't assume a REPL's injected
helpers also exist in Python create_script. [Frida JavaScript API](https://frida.re/docs/javascript-api/).

## 4. Matching device package

First inspect `command -v frida-server`, its `--version`/`--help` if present,
and current processes. Reuse a known working server. No server was installed at
the 2026-10-21 check; that was deliberate diagnostic cleanup, not broken SSH.

The main probes used client/server **17.2.17**, rootless architecture
**iphoneos-arm64**. Exact upstream artifact:

- [Official Frida 17.2.17 release](https://github.com/frida/frida/releases/tag/17.2.17)
- [frida_17.2.17_iphoneos-arm64.deb](https://github.com/frida/frida/releases/download/17.2.17/frida_17.2.17_iphoneos-arm64.deb)
- SHA-256: `865959c9613246231cbb8f2f1dc11bedf0ff0c54f13e993620bbec9ca9ffc96d`

The existing `/mnt/zram1/duskie/build/review/frida.deb` matched that digest during
this review. It is a convenience cache, not guaranteed future storage. On this host `dpkg-deb` is absent; GNU ar 2.47 and tar 1.35 are present. From
this control directory, use a dedicated working directory:

```sh
mkdir -p .local/frida-17.2.17/payload
cd .local/frida-17.2.17
# Copy the verified cache as frida.deb, or download the upstream asset:
curl --fail --location --output frida.deb \
  https://github.com/frida/frida/releases/download/17.2.17/frida_17.2.17_iphoneos-arm64.deb
# Compare sha256sum frida.deb to the exact digest above before extracting.
ar x frida.deb data.tar.xz
tar -xJf data.tar.xz -C payload
cp payload/var/jb/usr/sbin/frida-server frida-server.original
cp payload/var/jb/usr/lib/frida/frida-agent.dylib frida-agent.dylib
```

The cached artifact's member names and extraction were checked locally during
this review. On hosts with dpkg-deb, `dpkg-deb -x PACKAGE DESTINATION` is another
extraction method; it is not available by default here. Relevant payloads are `var/jb/usr/sbin/frida-server` and
`var/jb/usr/lib/frida/frida-agent.dylib`. Use a private staging directory, not an
active capture directory. An ordinary package install may set up a persistent
service; manual staging below avoids assuming such an install is required.

## 5. Rootless agent lookup and signing: observed workaround

Try the matching unmodified server/layout first when appropriate. Apply this
phone-specific workaround **only for the observed agent-path lookup failure**.
A missing agent means the server was reachable but injection could not load its
agent; changing Camera hooks does not repair that.

The original server contained two references to
`/usr/lib/frida/frida-agent.dylib`. The main session replaced those bytes in a
**copy** with `/var/jb/var/tmp/df-agent.dylib`, padded with NUL to preserve binary
size. Reproducible host patch, from a directory containing an extracted server:

```python
from pathlib import Path
original = Path('frida-server.original').read_bytes()
old = b'/usr/lib/frida/frida-agent.dylib'
new = b'/var/jb/var/tmp/df-agent.dylib'
assert len(new) <= len(old)
assert original.count(old) == 2, 'Different binary: inspect rather than patch blindly'
Path('frida-server.patched').write_bytes(original.replace(old, new.ljust(len(old), b'\0')))
```

Stage the original and patched server under a directory you own on the phone,
for example `/var/mobile/Library/Duskie/ControlTools`. Keep the matching agent
unchanged. From the directory containing your three files:

```sh
ssh -p 2222 mobile@127.0.0.1 'mkdir -p /var/mobile/Library/Duskie/ControlTools'
scp -P 2222 frida-server.original frida-server.patched frida-agent.dylib \
  mobile@127.0.0.1:/var/mobile/Library/Duskie/ControlTools/
```

Then open the root shell from step 2 and `cd` to that phone directory. Before signing the patched server, use the phone's existing ldid to
extract the **original server's** entitlements, then sign with that exact file:

```sh
# Device root shell; cd to your staging directory first.
/var/jb/usr/bin/ldid -e frida-server.original > frida.entitlements
# Confirm the extraction is nonempty and is a plist before continuing.
/var/jb/usr/bin/ldid -S"$PWD/frida.entitlements" frida-server.patched
chmod 755 frida-server.patched
```

**Plain `ldid -S frida-server.patched` was killed on this phone.** Preserve the
privileged entitlements; do not copy those privileges onto unrelated helpers.
Replacing bytes invalidates the original server signature. Copying the matching
agent without modification preserves its original signature.

Place that agent at the path the **current attach error actually requests**.
Earlier runs encountered both `/var/jb/var/tmp/df-agent.dylib` and
`/var/jb/var/jb/var/tmp/df-agent.dylib` because of rootless prefix translation.
The correct action was to create the requested parent directory and copy the
matching agent there as root, then retry the attach. Do not infer the final path
just from the patched literal, create broad symlink fixes, or assume old copies
remain. Record each owned deployed path for cleanup.

## 6. Start and connect

In the dedicated root device terminal, when no other server owns the port:

```sh
./frida-server.patched -l 127.0.0.1:27042
```

Keep it foreground. An ad-hoc background process may die when its SSH session
ends. In a second Linux terminal, keep the forward running:

```sh
ssh -N -T -p 2222 -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:27047:127.0.0.1:27042 mobile@127.0.0.1
```

In a third terminal, from this control directory:

```sh
.venv/bin/python ./iphone-control ps
.venv/bin/python ./iphone-control status
.venv/bin/python ./iphone-control --process SpringBoard methods SBWiFiManager --matching wiFi
```

Start read-only. Fresh PID resolution and successful attach are separate from a
responsive app main queue. Serialize probes. A suspended background app may not
service UIKit work promptly: foreground it only if that won't interrupt capture.
The CLI's timeout bounds RPC wait, not all attach/teardown operations. A timed-out
action may still have run; inspect state before repeating it.

Stop only your foreground server/forward when done. Detach and stop owned Frida
before a respring; reconnect SSH and rediscover PIDs afterward. Restore temporary
settings, retain queued recordings, and remove only the diagnostic paths you
recorded. Follow README's Safe Mode procedure rather than a normal reboot.
