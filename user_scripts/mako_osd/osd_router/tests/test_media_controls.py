#!/usr/bin/env python3
"""Real MPV IPC + stale MPRIS regressions on a private bus, never desktop players.

Run with Python 3.15+, PyGObject, MPV, jq and dbus-run-session installed.
"""
import importlib.util
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor

lazy from gi.repository import Gio, GLib

ROOT = Path(__file__).resolve().parents[1]
ROUTER = Path(os.environ.get("OSD_TEST_ROUTER", ROOT / "osd_router.sh"))
HELPER = ROOT / "media_ipc.py"


def stale_player(path):
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    xml = '''<node><interface name="org.mpris.MediaPlayer2.Player">
      <method name="Play"/><method name="Pause"/>
      <property name="PlaybackStatus" type="s" access="read"/>
      <property name="Metadata" type="a{sv}" access="read"/>
      <property name="CanControl" type="b" access="read"/>
      <property name="CanPlay" type="b" access="read"/>
      <property name="CanPause" type="b" access="read"/>
      <property name="CanGoNext" type="b" access="read"/>
      <property name="CanGoPrevious" type="b" access="read"/>
    </interface></node>'''

    def get(_connection, _sender, _path, _interface, name):
        if name == "PlaybackStatus":
            state = path.with_suffix(".state")
            return GLib.Variant("s", state.read_text() if state.exists() else "Paused")
        if name == "Metadata":
            return GLib.Variant("a{sv}", {"xesam:title": GLib.Variant("s", "Stale session")})
        return GLib.Variant("b", True)

    def command(_connection, _sender, _path, _interface, name, _args, invocation):
        with path.open("a", encoding="utf-8") as log:
            log.write(name + "\n")
        state = path.with_suffix(".state")
        if state.exists():
            state.write_text("Playing" if name == "Play" else "Paused")
        invocation.return_value(None)  # Models the live stale browser session.

    node = Gio.DBusNodeInfo.new_for_xml(xml)
    connection.register_object("/org/mpris/MediaPlayer2", node.interfaces[0], command, get, None)
    connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                         "RequestName", GLib.Variant("(su)", ("org.mpris.MediaPlayer2.stale", 0)),
                         None, Gio.DBusCallFlags.NONE, 1000, None)
    print(connection.get_unique_name(), flush=True)
    GLib.MainLoop().run()


def run_tests():
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] != os.environ["TEST_DESKTOP_BUS"]
    spec = importlib.util.spec_from_file_location("media_ipc", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    processes = []
    with tempfile.TemporaryDirectory(prefix="osd-media-") as directory:
        root = Path(directory)
        binary = root / "bin"
        binary.mkdir()
        media = root / "Silent λ audio.wav"
        with wave.open(str(media), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(bytes(8000 * 2 * 120))
        environment = os.environ | {
            "XDG_RUNTIME_DIR": str(root), "TEST_ROOT": str(root),
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        }
        # Gate /proc discovery and every IPC command to this test's PIDs. A
        # private D-Bus alone would not isolate MPV sockets on the desktop.
        gate = root / "gate.py"
        gate.write_text('''import json,os,pathlib,subprocess,sys
allowed=json.loads((pathlib.Path(os.environ["TEST_ROOT"])/"pids").read_text())
if sys.argv[2]=="list":
 data=json.loads(subprocess.check_output([sys.executable]+sys.argv[1:]))
 print(json.dumps([p for p in data if p["pid"] in allowed]))
else:
 assert int(sys.argv[3].split(":")[1]) in allowed
 raise SystemExit(subprocess.call([sys.executable]+sys.argv[1:]))
''', encoding="utf-8")
        for name, body in {
            "python3": f'exec "{sys.executable}" "{gate}" "$@"',
            "hyprctl": 'cat "$TEST_ROOT/windows"',
            "notify-send": 'touch "$TEST_ROOT/notification-sent"',
        }.items():
            executable = binary / name
            executable.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8")
            executable.chmod(0o755)
        clients = []
        try:
            stale = subprocess.Popen([sys.executable, __file__, "--stale", str(root / "calls")],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            processes.append(stale)
            with selectors.DefaultSelector() as selector:
                selector.register(stale.stdout, selectors.EVENT_READ)
                assert selector.select(5), "MPRIS fixture failed to start"
            stale_owner = stale.stdout.readline().strip()
            assert stale_owner.startswith(":"), stale.stderr.read()
            identities = []
            for index in range(2):
                address = root / f"generic socket λ {index}.sock"
                command = ["mpv", "--no-config", "--load-scripts=no", "--idle=yes", "--ao=null", "--vo=null",
                           "--force-window=no", "--no-terminal", f"--input-ipc-server={address}"]
                if index:
                    command.append("--pause")
                command += [str(media), str(media)]
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)
                for _ in range(100):
                    if address.is_socket():
                        break
                    assert process.poll() is None, "MPV exited during startup"
                    time.sleep(0.02)
                _, started, _ = helper.process(process.pid)
                identity = f"mpv:{process.pid}:{started}"
                identities.append(identity)
                clients.append(helper.Client(identity))
            (root / "pids").write_text(json.dumps([p.pid for p in processes[1:]]))
            (root / "windows").write_text(json.dumps([
                {"pid": p.pid, "mapped": True, "hidden": False, "focusHistoryID": i + 1}
                for i, p in enumerate(processes)
            ]))

            def state(index):
                return clients[index].snapshot()["data"][0]["PlaybackStatus"]["data"]

            def run(action="--play-pause", success=True):
                result = subprocess.run([str(ROUTER), action], env=environment,
                                        capture_output=True, text=True, timeout=20)
                assert (result.returncode == 0) == success, result.stderr
                assert not result.stdout, result.stdout
                assert not (root / "notification-sent").exists(), "Media notification was sent"
                return result

            (root / "osd_media_player.txt").write_text(stale_owner)
            run()
            assert state(0) == "Paused" and state(1) == "Paused"
            run()
            assert state(0) == "Playing", {
                "first": state(0), "second": state(1),
                "context": (root / "osd_media_context.json").read_text(),
                "identities": identities,
            }
            assert not (root / "calls").exists()
            print("PASS stale paused MPRIS cannot steal active embedded-MPV controls; no popups", flush=True)

            # Already-playing background source must not steal resume.
            clients[1].control("Play")
            run()  # Newly playing second instance wins.
            assert state(0) == "Playing" and state(1) == "Paused"
            run()  # Resume second while the first still plays.
            assert state(0) == state(1) == "Playing"
            assert (root / "osd_media_player.txt").read_text().strip() == identities[1]
            print("PASS new playback supersedes old selection; existing playback preserves pause/resume", flush=True)

            (root / "windows").write_text(json.dumps([
                {"pid": processes[1].pid, "mapped": True, "hidden": False, "focusHistoryID": 0}
            ]))
            run()
            assert state(0) == "Paused" and state(1) == "Playing"
            run()
            print("PASS focused playing window switches the target", flush=True)

            (root / "windows").write_text("[]")
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: run(), range(21)))
            assert state(0) == "Paused" and state(1) == "Playing"
            print("PASS 21 concurrent IPC toggles keep correct target and final parity", flush=True)

            run("--next")
            assert clients[0].get("playlist-pos") == 1
            run("--prev")
            assert clients[0].get("playlist-pos") == 0
            run("--play")
            run("--pause")
            assert state(0) == "Paused"
            run("--next")
            run("--stop")
            assert state(0) == "Stopped"
            run("--play")
            assert state(0) == "Playing"
            assert clients[0].get("playlist-pos") == 1
            run("--pause")
            print("PASS IPC next, previous, stop/resume, explicit play/pause and paths containing spaces/Unicode", flush=True)

            # No-op failure is quiet, and no other player receives a retry.
            clients[1].control("Pause")
            (root / "osd_media_player.txt").write_text(stale_owner)
            result = run(success=False)
            assert "Did Not Play" in result.stderr
            assert state(0) == state(1) == "Paused"
            print("PASS nonresponsive MPRIS fails via stderr only; no redirected command or popup", flush=True)

            selector = ROOT.parents[1] / "audio/mpris_toggle/mpris_playback_selection.sh"
            subprocess.run(["bash", "-c", '''source "$1"
HAS_MPRIS=1
_update_players
for player in "${players[@]}"; do
    [[ ${player_owners[$player]} == "$2" ]] || continue
    ids=(0); apps=(test); backends=("mpris:$player")
    _perform_action toggle 0
    exit $?
done
exit 1''', "selector", str(selector), identities[0]],
                           env=environment, check=True, capture_output=True, text=True, timeout=5)
            assert state(0) == "Playing"
            run()
            assert state(0) == "Paused"
            print("PASS selector and global key share IPC discovery and selected-player context", flush=True)

            (root / "pids").write_text("[]")  # Exercise MPRIS without any IPC candidates.
            (root / "calls.state").write_text("Playing")
            (root / "osd_media_player.txt").write_text(stale_owner)
            run()
            assert (root / "calls.state").read_text() == "Paused"
            run()
            assert (root / "calls.state").read_text() == "Playing"
            print("PASS ordinary MPRIS-only playback still toggles without notifications", flush=True)

            (root / "windows").write_text("Couldn't connect to old Hyprland socket")
            run()
            assert (root / "calls.state").read_text() == "Paused"
            (root / "windows").unlink()  # Also test a nonzero hyprctl exit status.
            run()
            assert (root / "calls.state").read_text() == "Playing"
            print("PASS stale/unavailable Hyprland focus cannot block media controls", flush=True)
            try:
                helper.Client(identities[0] + "0")
            except ValueError:
                pass
            else:
                raise AssertionError("Stale PID identity accepted")
            print("PASS process start-time identity rejects PID reuse", flush=True)
        finally:
            for client in clients:
                client.connection.close()
            for process in processes:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                for pipe in (process.stdout, process.stderr):
                    if pipe:
                        pipe.close()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--stale"]:
        stale_player(Path(sys.argv[2]))
    elif sys.argv[1:] == ["--private"]:
        run_tests()
    else:
        subprocess.run(["dbus-run-session", "--", sys.executable, __file__, "--private"],
                       env=os.environ | {"TEST_DESKTOP_BUS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")},
                       check=True)
