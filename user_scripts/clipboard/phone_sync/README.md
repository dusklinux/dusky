# Optional iPhone clipboard sharing

Text clipboard sharing between Hyprland/Wayland and rootless iOS 16 Duskie
0.2.9+. This is a separate service; installing normal Dusky clipboard history
does **not** enable or install phone sharing.

## Install

Requires Python 3.15+, systemd user services, OpenSSH and wl-clipboard 2.3+.
The iPhone needs Duskie 0.2.9+ and SSH access as `mobile`.
Use Duskie 0.2.26+ for reliable reconnection when an old Wi-Fi SSH session
remains half-open: the newest connection replaces it, keeping one active peer.

Create an SSH key if necessary, then run:

```sh
python3 setup.py install --host YOUR_PHONE_WIFI_ADDRESS --pair
```

`--pair` uses `ssh-copy-id` and requests the phone password interactively. The
service stores the identity **path**, never the password or private key.
For another key use `--identity /path/to/key`. Running without `--pair` uses
an already authorized key. Verify the SSH host fingerprint when first connecting.

Setup enables **Settings → Duskie → Phone clipboard sharing → Phone clipboard
sync**, creates `dusky/phone_sync.json` under your XDG configuration directory,
and enables `dusky_phone_sync.service` for your user’s graphical session.
The service uses the script directory from which setup was run; keep it installed.

### USB

An existing `iproxy 2222:22` tunnel can be the sole connection:

```sh
python3 setup.py install --host 127.0.0.1 --port 2222 --pair
```

Or add `--usb-port 2222` to a Wi-Fi installation for fallback to that existing
tunnel. This service does not start, stop, or replace your iproxy process.
If USB uses a different SSH host-key entry, connect once as `mobile` through
that endpoint to verify its fingerprint before relying on automatic fallback.

## Behavior

- New UTF-8 text copies, up to 16 KiB, are shared in both directions.
- Images, empty selections, history deletions and pin changes are not shared.
- Regular clipboard only; PRIMARY selection is separate.
- New incoming iPhone text is published with `wl-copy`; your existing clipboard
  daemon records it normally. No direct cliphist database access is needed.
- Duskie receives Linux text in the actual iOS pasteboard; its existing history
  capture records the incoming copy when history is enabled.
- Sharing works independently of the clipboard-history switch on iOS.
- No old clipboard is replayed on startup or reconnect. Copies made while
  disconnected remain local. Rapid Linux copies waiting for transmission collapse
  to the latest clipboard value.
- Startup takes one size-bounded text snapshot before watching for changes. This
  also preserves the first new text copy when the initial selection is an image.
- Both sides suppress echoes; Linux-to-phone updates require acknowledgment.
- The service waits for events, with reconnect backoff and SSH liveness checks.
  It logs connection state and byte counts, never clipboard contents.
- App-marked sensitive text follows your existing daemon’s capture behavior and
  is shared too. Turn sync off when you do not want copies shared.

## Stop / remove

```sh
systemctl --user stop dusky_phone_sync.service
python3 setup.py uninstall
```

Uninstall disables/removes only this service and its configuration and attempts
to turn off phone sharing. If the phone is offline, turn that switch off manually
later. It preserves SSH keys, existing clipboard scripts, services, pins and history.
Turning the phone switch off closes its listener and disconnects an active session.

```sh
python3 setup.py status
journalctl --user -u dusky_phone_sync.service -n 30 --no-pager
python3 test_phone_sync.py
```

The isolated tests never use the live clipboard, phone, history or services.

For the slower stress checks (real fixture subprocesses and Unix sockets):

```sh
python3 -X dev -W error test_phone_sync_stress.py
```

These also leave your real clipboard and phone untouched. See
[STRESS-TEST.md](STRESS-TEST.md) for the exercised loads and verification limits.

## Protocol

An SSH stdio stream runs `/var/jb/usr/libexec/duskie-clipboard --stream` as
`mobile`, connecting to a private SpringBoard Unix socket. There is no new
public network listener or iPhone launch daemon. Frames consist of a 4-byte
big-endian length followed by UTF-8 JSON, with a 128-KiB frame limit:

- `hello`: protocol `version: 1`, `maxBytes: 16384`.
- `copy`: UUID `id` and nonempty `text`.
- `ack`: the applied copy’s `id`.

The clipboard API pattern was examined in ioscpy; this implementation uses
Duskie’s existing copy hooks and an independent protocol. It does not import
ioscpy’s screen streaming, polling timers or paste-alert suppression hooks.
