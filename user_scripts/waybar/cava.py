#!/usr/bin/env python3
"""
Cava Manager for Waybar
A simplified script that runs a single cava instance and outputs formatted data for waybar

Waybar Configuration Example:
{
    "custom/cava": {
        "format": "{}",
        "return-type": "json",
        "exec": "/path/to/cava.py waybar",
        "restart-interval": 1,
        "config": {
            "bar": "▁▂▃▄▅▆▇█",
            "bar-array": ["<span color='#ff0000'>█</span>", "<span color='#00ff00'>█</span>"],
            "width": 16,
            "stb": 0,
            "bars": 16,
            "range": 15
        }
    }
}

Config Options:
- bar: Unicode characters for bars (default: "▁▂▃▄▅▆▇█")
- bar-array: Array of strings for each bar level (overrides bar)
- width: Number of bars to display (default: auto from bar length)
- stb: Standby mode (0=hide, 1=blank, 2=full, 3=low, string=custom)
- bars: Number of cava bars to generate (default: width)
- range: ASCII range for cava (default: 15)

Command line args override config values.
"""

import socket
import subprocess
import os
import sys
import threading
import time
import argparse
import signal
import atexit
import json
from pathlib import Path


class CavaDataParser:
    """Handle cava data parsing and formatting for waybar"""

    @staticmethod
    def format_data(line, bar_chars="▁▂▃▄▅▆▇█", width=None, standby_mode=""):
        """Format cava data with custom bar characters"""
        line = line.strip()
        if not line:
            return CavaDataParser._handle_standby_mode(standby_mode, bar_chars, width)

        try:
            values = [int(x) for x in line.split(";") if x.isdigit()]
        except ValueError:
            return CavaDataParser._handle_standby_mode(standby_mode, bar_chars, width)

        if not values or all(v == 0 for v in values):
            return CavaDataParser._handle_standby_mode(standby_mode, bar_chars, width)

        if not width:
            width = len(values)

        if len(values) != width:
            expanded_values = []
            for i in range(width):
                original_pos = (i * (len(values) - 1)) / (width - 1) if width > 1 else 0
                left_idx = int(original_pos)
                right_idx = min(left_idx + 1, len(values) - 1)

                if left_idx == right_idx:
                    expanded_values.append(values[left_idx])
                else:
                    fraction = original_pos - left_idx
                    interpolated = (
                        values[left_idx]
                        + (values[right_idx] - values[left_idx]) * fraction
                    )
                    expanded_values.append(int(round(interpolated)))

            values = expanded_values

        bar_length = len(bar_chars)
        result = ""

        for value in values:
            if value >= bar_length:
                char_index = bar_length - 1
            else:
                char_index = value
            result += bar_chars[char_index]

        return result

    @staticmethod
    def _handle_standby_mode(standby_mode, bar_chars, width):
        """Handle standby mode when no audio activity"""
        if isinstance(standby_mode, str):
            return standby_mode
        elif standby_mode == 0:
            return ""
        elif standby_mode == 1:
            return "‎ "
        elif standby_mode == 2:
            full_char = bar_chars[-1]
            return full_char * (width or len(bar_chars))
        elif standby_mode == 3:
            low_char = bar_chars[0]
            return low_char * (width or len(bar_chars))
        else:
            return str(standby_mode)


class CavaServer:
    """Cava server that manages the cava process and broadcasts to waybar clients"""

    def __init__(self):
        self.runtime_dir = os.getenv(
            "XDG_RUNTIME_DIR", os.path.join("/run/user", str(os.getuid()))
        )
        self.socket_file = os.path.join(self.runtime_dir, "hyde", "cava.sock")
        self.pid_file = os.path.join(self.runtime_dir, "hyde", "cava.pid")
        self.temp_dir = Path(os.path.join(self.runtime_dir, "hyde"))
        self.config_file = self.temp_dir / "cava.manager.conf"

        self.clients = []
        self.clients_lock = threading.Lock()
        self.cava_process = None
        self.server_socket = None
        self.cleanup_registered = False
        self.successfully_started = False
        self.should_shutdown = False

    def cleanup(self):
        """Cleanup function called on exit"""
        if not (
            self.successfully_started and (self.server_socket or self.cava_process)
        ):
            return

        if self.cava_process and self.cava_process.poll() is None:
            self.cava_process.terminate()
            try:
                self.cava_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.cava_process.kill()

        with self.clients_lock:
            for client_socket in self.clients[:]:
                try:
                    client_socket.close()
                except Exception:
                    pass
            self.clients.clear()

        if self.server_socket:
            self.server_socket.close()

        if os.path.exists(self.socket_file):
            owns_pid_file = False
            if os.path.exists(self.pid_file):
                try:
                    with open(self.pid_file, "r") as f:
                        pid = int(f.read().strip())
                    owns_pid_file = pid == os.getpid()
                except (ValueError, IOError, FileNotFoundError):
                    pass

            if owns_pid_file or not os.path.exists(self.pid_file):
                os.remove(self.socket_file)

        if os.path.exists(self.pid_file):
            try:
                with open(self.pid_file, "r") as f:
                    pid = int(f.read().strip())
                if pid == os.getpid():
                    os.remove(self.pid_file)
            except (ValueError, IOError, FileNotFoundError):
                pass

    def _write_pid_file(self):
        """Write PID file to prevent multiple managers"""
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        with open(self.pid_file, "w") as f:
            f.write(str(os.getpid()))

    def _quick_check_running(self):
        """Quick check if manager is running without acquiring locks"""
        if os.path.exists(self.socket_file):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as test_socket:
                    test_socket.settimeout(0.5)
                    test_socket.connect(self.socket_file)
                return True
            except (ConnectionRefusedError, FileNotFoundError, OSError, socket.timeout):
                try:
                    os.remove(self.socket_file)
                except FileNotFoundError:
                    pass

        if os.path.exists(self.pid_file):
            try:
                with open(self.pid_file, "r") as f:
                    pid = int(f.read().strip())

                try:
                    os.kill(pid, 0)
                    return True
                except OSError:
                    try:
                        os.remove(self.pid_file)
                    except FileNotFoundError:
                        pass
            except (ValueError, IOError):
                try:
                    os.remove(self.pid_file)
                except FileNotFoundError:
                    pass

        return False

    def _broadcast_data(self, data):
        """Broadcast data to all connected clients"""
        with self.clients_lock:
            disconnected_clients = []
            for client_socket in self.clients:
                try:
                    client_socket.sendall(data)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    disconnected_clients.append(client_socket)

            for client in disconnected_clients:
                try:
                    client.close()
                except Exception:
                    pass
                if client in self.clients:
                    self.clients.remove(client)

            if not self.clients and not self.should_shutdown:
                self.should_shutdown = True
                if self.cava_process and self.cava_process.poll() is None:
                    try:
                        self.cava_process.terminate()
                    except Exception:
                        pass

    def _handle_client_connections(self):
        """Handle incoming client connections"""
        while not self.should_shutdown:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(
                    target=self._client_command_listener, args=(conn,), daemon=True
                ).start()
                with self.clients_lock:
                    self.clients.append(conn)
            except OSError:
                break

    def _client_command_listener(self, conn):
        """Listen for commands from waybar client"""
        try:
            conn.settimeout(0.1)
            while True:
                try:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    if b"\n" in chunk:
                        line = chunk.split(b"\n", 1)[0]
                        if line.strip() == b"CMD:RELOAD":
                            self._reload_cava_process()
                except socket.timeout:
                    break
        except Exception:
            pass

    def _reload_cava_process(self):
        """Restart cava process"""
        if self.cava_process and self.cava_process.poll() is None:
            self.cava_process.terminate()
            try:
                self.cava_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.cava_process.kill()

        self._create_cava_config()
        try:
            self.cava_process = subprocess.Popen(
                ["cava", "-p", str(self.config_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            print("Error: cava not found. Please install cava.", file=sys.stderr)

    def _create_cava_config(self, bars=16, range_val=15, channels="stereo", reverse=0):
        """Create cava configuration file"""
        # Override with environment variables if set
        bars = int(os.getenv("CAVA_BARS", bars))
        range_val = int(os.getenv("CAVA_RANGE", range_val))
        channels = os.getenv("CAVA_CHANNELS", channels)
        reverse = os.getenv("CAVA_REVERSE", reverse)
        try:
            reverse = int(reverse)
        except Exception:
            reverse = 1 if str(reverse).lower() in ("true", "yes", "on") else 0

        self.temp_dir.mkdir(parents=True, exist_ok=True)

        config_content = f"""[general]
bars = {bars}
sleep_timer = 1

[input]
method = pulse
source = auto

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = {range_val}
channels = {channels}
reverse = {reverse}
"""

        with open(self.config_file, "w") as f:
            f.write(config_content)

    def start(self, bars=16, range_val=15, channels="stereo", reverse=0):
        """Start the cava server"""
        self.shutdown_event = threading.Event()
        threads = []
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                self.server_socket.bind(self.socket_file)
                self.server_socket.listen(10)
            except OSError as e:
                if e.errno == 98:  # Address already in use
                    print("Error: Cava manager is already running")
                    self.server_socket.close()
                    sys.exit(1)
                elif e.errno == 2:  # No such file or directory
                    os.makedirs(os.path.dirname(self.socket_file), exist_ok=True)
                    try:
                        self.server_socket.bind(self.socket_file)
                        self.server_socket.listen(10)
                    except OSError as e2:
                        print(
                            "Error: Cava manager is already running"
                            if e2.errno == 98
                            else f"Error: Could not bind to socket: {e2}"
                        )
                        self.server_socket.close()
                        sys.exit(1)
                else:
                    print(f"Error: Could not bind to socket: {e}")
                    self.server_socket.close()
                    sys.exit(1)

            if not self.cleanup_registered:
                atexit.register(self.cleanup)
                self.cleanup_registered = True
                self.successfully_started = True

            self._write_pid_file()
            self._create_cava_config(bars, range_val, channels, reverse)

            try:
                self.cava_process = subprocess.Popen(
                    ["cava", "-p", str(self.config_file)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except FileNotFoundError:
                print("Error: cava not found. Please install cava.", file=sys.stderr)
                sys.exit(1)

            def read_cava_output():
                import select

                while not self.shutdown_event.is_set():
                    if self.cava_process.stdout:
                        rlist, _, _ = select.select(
                            [self.cava_process.stdout], [], [], 0.2
                        )
                        if rlist:
                            line = self.cava_process.stdout.readline()
                            if not line or self.shutdown_event.is_set():
                                break
                            if line.strip():
                                self._broadcast_data(line.encode("utf-8"))

            def handle_client_connections():
                while not self.shutdown_event.is_set():
                    try:
                        self.server_socket.settimeout(0.2)
                        conn, addr = self.server_socket.accept()
                        threading.Thread(
                            target=self._client_command_listener,
                            args=(conn,),
                            daemon=True,
                        ).start()
                        with self.clients_lock:
                            self.clients.append(conn)
                    except socket.timeout:
                        continue
                    except OSError:
                        break

            threads.append(
                threading.Thread(target=handle_client_connections, daemon=True)
            )
            threads.append(threading.Thread(target=read_cava_output, daemon=True))
            for t in threads:
                t.start()

            def shutdown_handler(signum=None, frame=None):
                self.shutdown_event.set()
                for t in threads:
                    t.join(timeout=2)
                if self.cava_process and self.cava_process.poll() is None:
                    try:
                        self.cava_process.terminate()
                        self.cava_process.wait(timeout=2)
                    except Exception:
                        try:
                            self.cava_process.kill()
                        except Exception:
                            pass
                self.cleanup()
                os._exit(0)

            signal.signal(signal.SIGTERM, shutdown_handler)
            signal.signal(signal.SIGINT, shutdown_handler)
            try:
                while not self.shutdown_event.is_set():
                    time.sleep(0.2)
            except KeyboardInterrupt:
                shutdown_handler()

        except Exception as e:
            print(f"Error starting manager: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            self.cleanup()
            os._exit(0)

    def is_running(self):
        """Check if the server is running"""
        return self._quick_check_running()

    def start_in_background(self, bars=16, range_val=15):
        """Start the manager in background and return immediately"""
        if self.is_running():
            return True

        script_path = os.path.abspath(__file__)
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    script_path,
                    "manager",
                    "--bars",
                    str(bars),
                    "--range",
                    str(range_val),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            max_wait = 5
            start_time = time.time()
            while time.time() - start_time < max_wait:
                if self.is_running():
                    return True
                time.sleep(0.1)

            return False
        except Exception as e:
            print(f"Failed to start manager in background: {e}", file=sys.stderr)
            return False


class CavaWaybarClient:
    """Cava client for waybar that connects to the server and outputs JSON"""

    def __init__(self):
        self.runtime_dir = os.getenv(
            "XDG_RUNTIME_DIR", os.path.join("/run/user", str(os.getuid()))
        )
        self.socket_file = os.path.join(self.runtime_dir, "hyde", "cava.sock")
        self.parser = CavaDataParser()
        self.config = self._read_waybar_config()

    def _read_waybar_config(self):
        """Read configuration from waybar stdin input"""
        config = {}
        try:
            # Waybar sends initial config on stdin
            import select

            if select.select([sys.stdin], [], [], 0.1)[0]:
                line = sys.stdin.readline()
                if line:
                    import json

                    try:
                        waybar_input = json.loads(line)
                        config = waybar_input.get("config", {})
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
        return config

    def _get_config_value(self, key, default):
        """Get config value from waybar config, then args, then default"""
        if (
            hasattr(self, "args")
            and hasattr(self.args, key)
            and getattr(self.args, key) is not None
        ):
            return getattr(self.args, key)
        return self.config.get(key, default)

    def _auto_start_manager_if_needed(self, bars=16, range_val=15):
        """Automatically start manager if not running"""
        server = CavaServer()
        if not server.is_running():
            if server.start_in_background(bars, range_val):
                return True
            else:
                return False
        return True

    def start(self, args=None, timeout=10):
        """Start the cava waybar client"""
        self.args = args

        # Get configuration from waybar config, then args, then defaults
        bar_chars = self._get_config_value("bar", "▁▂▃▄▅▆▇█")
        if self._get_config_value("bar-array", None):
            bar_chars = self._get_config_value("bar-array", None)

        width = self._get_config_value("width", None)
        if width is None:
            width = len(bar_chars) if bar_chars else 8

        standby_mode = self._get_config_value("stb", 0)
        if isinstance(standby_mode, str) and standby_mode.isdigit():
            standby_mode = int(standby_mode)

        bars = self._get_config_value("bars", width)
        range_val = self._get_config_value("range", 15)

        if not self._auto_start_manager_if_needed(bars, range_val):
            print("Error: Could not start cava manager", file=sys.stderr)
            sys.exit(1)

        start_time = time.time()
        while not os.path.exists(self.socket_file):
            if time.time() - start_time > timeout:
                print(
                    "Error: Cava manager not accessible after timeout", file=sys.stderr
                )
                sys.exit(1)
            time.sleep(0.1)

        client_socket = None
        try:
            client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client_socket.connect(self.socket_file)

            standby_output = self.parser._handle_standby_mode(
                standby_mode, bar_chars, width
            )
            if standby_output:
                output = {
                    "text": standby_output,
                    "tooltip": "Cava audio visualizer - standby mode",
                }
                print(json.dumps(output), flush=True)

            buffer = ""
            while True:
                data = client_socket.recv(1024)
                if not data:
                    break

                decoded_data = data.decode("utf-8")
                if decoded_data.strip():
                    buffer += decoded_data

                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.strip():
                            formatted = self.parser.format_data(
                                line, bar_chars, width, standby_mode
                            )
                            if formatted:
                                output = {
                                    "text": formatted,
                                    "tooltip": "Cava audio visualizer - active",
                                }
                                print(json.dumps(output), flush=True)

        except (ConnectionRefusedError, FileNotFoundError):
            print("Error: Cannot connect to cava manager", file=sys.stderr)
            sys.exit(1)
        except KeyboardInterrupt:
            pass
        finally:
            if client_socket:
                try:
                    client_socket.close()
                except Exception:
                    pass


class CavaReloadClient:
    """Minimal client to send reload command to the server"""

    def __init__(self):
        self.runtime_dir = os.getenv(
            "XDG_RUNTIME_DIR", os.path.join("/run/user", str(os.getuid()))
        )
        self.socket_file = os.path.join(self.runtime_dir, "hyde", "cava.sock")

    def reload(self):
        if not os.path.exists(self.socket_file):
            print("Cava manager is not running.")
            sys.exit(1)
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self.socket_file)
            s.sendall(b"CMD:RELOAD\n")
            s.close()
            print("Reload command sent.")
        except Exception as e:
            print(f"Failed to send reload command: {e}")
            sys.exit(1)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Cava Manager for Waybar")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Manager command
    manager_parser = subparsers.add_parser("manager", help="Start cava manager")
    manager_parser.add_argument("--bars", type=int, default=16, help="Number of bars")
    manager_parser.add_argument("--range", type=int, default=15, help="ASCII range")
    manager_parser.add_argument(
        "--channels",
        choices=["mono", "stereo"],
        default="stereo",
        help="Audio channels",
    )
    manager_parser.add_argument(
        "--reverse", type=int, choices=[0, 1], default=0, help="Reverse frequency order"
    )

    # Waybar client command
    waybar_parser = subparsers.add_parser("waybar", help="Waybar client")
    waybar_parser.add_argument("--bar", default="▁▂▃▄▅▆▇█", help="Bar characters")
    waybar_parser.add_argument("--bar-array", nargs="+", help="Bar characters as array")
    waybar_parser.add_argument("--width", type=int, help="Bar width")
    waybar_parser.add_argument("--stb", default=0, help="Standby mode (0-3 or string)")
    waybar_parser.add_argument(
        "--json", action="store_true", help="Output JSON format for waybar tooltips"
    )

    # Status command
    subparsers.add_parser("status", help="Check manager status")

    # Reload command
    subparsers.add_parser("reload", help="Reload cava manager")

    args = parser.parse_args()

    if args.command == "manager":
        server = CavaServer()
        if server.is_running():
            print("Cava manager is already running")
            sys.exit(0)
        server.start(args.bars, args.range, args.channels, args.reverse)

    elif args.command == "waybar":
        bar_chars = args.bar_array if args.bar_array else args.bar
        width = (
            args.width
            if args.width is not None
            else (len(bar_chars) if bar_chars else 8)
        )

        client = CavaWaybarClient()
        client.start(args)

    elif args.command == "status":
        server = CavaServer()
        if server.is_running():
            print("Cava manager is running")
            sys.exit(0)
        else:
            print("Cava manager is not running")
            sys.exit(1)

    elif args.command == "reload":
        CavaReloadClient().reload()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
