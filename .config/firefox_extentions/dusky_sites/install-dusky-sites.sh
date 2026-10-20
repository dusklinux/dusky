#!/usr/bin/env bash
# install-dusky-sites.sh — install the native host + manifest, verify toolchain, build the xpi, smoke-test the wire.
# usage: ./install-dusky-sites.sh [repo-root]   (repo-root contains extension/ and dusky_sites_host.py)
set -euo pipefail

SCRIPT_PATH="$(realpath -- "${BASH_SOURCE[0]}")"
SRC="$(realpath -- "${1:-${SCRIPT_PATH%/*}}")"
HOST_DST="$HOME/.local/share/dusky-sites/dusky_sites_host.py"
NM_DIR="$HOME/.mozilla/native-messaging-hosts"
EXT_ID="dusky_sites@dusky.com"

echo "== toolchain"

command -v python3 >/dev/null || {
  echo "python3 is required" >&2
  exit 1
}

command -v firefox >/dev/null || {
  echo "firefox is required" >&2
  exit 1
}

command -v zip >/dev/null || {
  echo "zip is required (pacman -S zip)" >&2
  exit 1
}

python3 -c '
import sys
assert sys.version_info >= (3, 15), sys.version
print("python", sys.version.split()[0])
'

ff_ver="$(firefox --version)"
printf '%s\n' "$ff_ver" >&2

ff_major="$(
  sed -En \
    's/.*Firefox ([0-9]+).*/\1/p' \
    <<<"$ff_ver"
)"

if [[ ! "$ff_major" =~ ^[0-9]+$ ]] || (( ff_major < 158 )); then
  echo "Firefox >= 158 required" >&2
  exit 1
fi
if command -v node >/dev/null; then
  node --check "$SRC/extension/background.js"
  node --check "$SRC/extension/content.js"
  node --check "$SRC/extension/defaults.js"
  echo "js syntax ok"
else
  echo "node not found: skipping --check (pacman -S nodejs)"
fi
python3 -m json.tool "$SRC/extension/manifest.json" >/dev/null && echo "manifest json ok"

echo "== host"
install -Dm755 "$SRC/dusky_sites_host.py" "$HOST_DST"
python3 -m py_compile "$HOST_DST" && echo "py_compile ok"
install -d "$NM_DIR"
python3 - "$HOST_DST" "$NM_DIR/dusky_sites.json" "$EXT_ID" <<'PYMANIFEST'
import json
from pathlib import Path
import sys
host, manifest, extension = sys.argv[1:]
Path(manifest).write_text(json.dumps({
    "name": "dusky_sites",
    "description": "Dusky Sites Native Messaging Host",
    "path": host,
    "type": "stdio",
    "allowed_extensions": [extension],
}, indent=2) + "\n", encoding="utf-8")
PYMANIFEST
echo "native manifest → $NM_DIR/dusky_sites.json"
install -d "$HOME/.config/dusky/settings/dusky_sites" "$HOME/.config/dusky_sites"

echo "== wire smoke test (HELLO → HELLO_ACK, FETCH_NOW → MATUGEN_UPDATE, delta path, EOF → exit 0)"
python3 - "$HOST_DST" "$NM_DIR/dusky_sites.json" "$EXT_ID" <<'PY'
import atexit, json, os, select, struct, subprocess, sys, time
host, manifest, ext = sys.argv[1:4]
p = subprocess.Popen([host, manifest, ext], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def cleanup():
    if p.poll() is None:
        p.kill()
        p.wait()
    p.stdin.close()
    p.stdout.close()
atexit.register(cleanup)
def send(m):
    b = json.dumps(m).encode(); p.stdin.write(struct.pack('=I', len(b)) + b); p.stdin.flush()
def read_exact(size, deadline):
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        assert remaining > 0 and select.select([p.stdout], [], [], remaining)[0], 'host response timeout'
        part = os.read(p.stdout.fileno(), size - len(result))
        assert part, 'host closed early'
        result.extend(part)
    return result

def recv(want):
    deadline = time.monotonic() + 10
    chunks = {}
    while True:
        h = read_exact(4, deadline)
        m = json.loads(read_exact(struct.unpack('=I', h)[0], deadline))
        if m.get('type') == 'CHUNK':
            parts = chunks.setdefault(m['id'], {})
            parts[m['seq']] = m['part']
            if len(parts) < m['total']:
                continue
            m = json.loads(''.join(parts[i] for i in range(m['total'])))
        if m.get('type') == want:
            return m
send({'type': 'HELLO', 'wire': 3, 'extension': ext, 'known': {}})
ack = recv('HELLO_ACK'); assert ack['wire'] >= 3, ack
t0 = time.perf_counter(); send({'type': 'FETCH_NOW', 'known': {}}); m = recv('MATUGEN_UPDATE'); dt = (time.perf_counter() - t0) * 1000
d = m["data"]

print(
    f"MATUGEN_UPDATE in {dt:.1f} ms: "
    f"{len(d['colors'])} colours, "
    f"{len(d.get('websites', {}))} site keys, "
    f"status={d['status']}"
)

assert (
    isinstance(
        d.get("colorsRev"),
        str
    ) and
    d["colorsRev"]
), "missing canonical colorsRev"

assert (
    isinstance(
        d.get("websitesRev"),
        str
    ) and
    d["websitesRev"]
), "missing canonical websitesRev"

assert (
    "websites" in d
), "first frame must carry the rule map"

send({
    "type": "FETCH_NOW",
    "known": {
        "websitesRev":
            d["websitesRev"]
    }
})

m2 = recv(
    "MATUGEN_UPDATE"
)

assert (
    m2["data"].get("websitesRev") ==
    d["websitesRev"]
), "websitesRev changed unexpectedly"

assert (
    "websites" not in
    m2["data"]
), "delta path failed: websites re-sent although known"

print(
    "delta ok: second FETCH_NOW omitted websites"
)
send({'type': 'PING', 'at': 1}); assert recv('PONG')['at'] == 1
p.stdin.close(); code = p.wait(timeout=5); assert code == 0, code; print("exit", code)
PY

echo "== xpi"
( cd "$SRC/extension" && rm -f ../dusky_sites-unsigned.zip && zip -qr -X ../dusky_sites-unsigned.zip manifest.json background.js content.js defaults.js icons )
echo "built unsigned signing input: $SRC/dusky_sites-unsigned.zip"
echo "Submit this archive to Mozilla for signing; preserve xpi/dusky_sites.xpi until a new signed release is available."
echo "load via about:debugging#/runtime/this-firefox → Load Temporary Add-on → $SRC/extension/manifest.json"
