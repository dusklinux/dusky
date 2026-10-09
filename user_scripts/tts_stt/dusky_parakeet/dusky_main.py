#!/usr/bin/env python3
"""Clipboard-only English dictation. Capture first; transcribe after stop."""

import argparse
import collections
import fcntl
import json
import logging
import math
import mmap
import os
import selectors
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import tempfile
import html
from datetime import datetime
from pathlib import Path
from typing import Any

MIN_PYTHON = (3, 15)
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
MAX_PACKET = 65536

if sys.version_info < MIN_PYTHON:
    raise SystemExit("Dusky STT requires CPython 3.15+")
_gil = getattr(sys, "_is_gil_enabled", None)
if _gil is None or not _gil():
    raise SystemExit("Dusky STT requires GIL-enabled CPython")

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["ORT_DISABLE_TELEMETRY"] = "1"  # Avoid background uploads/device-ID writes.

lazy import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s dusky[%(process)d]: %(message)s")
LOG = logging.getLogger("dusky")

APP_DIR = Path(os.environ.get("DUSKY_APP_DIR", Path(__file__).resolve().parent))
CONFIG_PATH = Path(os.environ.get("DUSKY_CONFIG", APP_DIR / "config.json"))

type JsonObject = dict[str, Any]

CUDA_TOKENS = ("libcuda.so", "libcudart.so", "libcublas", "libcudnn", "libnvrtc", "onnxruntime_providers_cuda")
NO_IDLE_EXIT_ENV = "DUSKY_WORKER_NO_IDLE_EXIT"


def cuda_maps() -> list[str]:
    try:
        text = Path("/proc/self/maps").read_text(encoding="utf-8", errors="replace").casefold()
    except OSError:
        return []
    return sorted({tok for tok in CUDA_TOKENS if tok in text})


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as h:
            h.write(content)
            h.flush()
            os.fsync(h.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        tmp.unlink(missing_ok=True)


class WorkerManager:
    def __init__(self, config: JsonObject, config_path: Path | None = None) -> None:
        self.config = config
        self.config_path = config_path or APP_DIR / "config.json"
        self.progress = 0.0
        self._cv = threading.Condition(threading.RLock())
        self._selection_lock = threading.RLock()
        self.ready = False
        self._resident_requested = False
        self._proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._gen = 0
        self._spawns = 0
        self._inflight: dict[str, int] = {}
        self._results: dict[str, JsonObject] = {}
        self._discarded: set[str] = set()
        self._intentional_exits: set[int] = set()
        self.backend = "cpu"
        self.gpu = None
        self.fallback_reason = ""
        self._select_backend()

    def _select_backend(self) -> None:
        preference = self.config.get("backend", "cpu")
        if preference not in ("auto", "cpu", "nvidia"):
            raise ValueError("backend must be auto, cpu or nvidia")
        self.backend, self.gpu, self.fallback_reason = "cpu", None, ""
        if preference == "cpu":
            return
        if not self.config.get("parakeet") or not (APP_DIR / ".venv-gpu/bin/python").is_file():
            self.fallback_reason = "Parakeet GPU backend is not installed"
            return
        # Do not wake/probe NVIDIA before the microphone and UI can start.
        self.backend = "nvidia"

    def _resolve_gpu(self) -> None:
        from dusky_hardware import detect_nvidia, select_gpu
        with self._selection_lock:
            if self.backend == "nvidia" and self.gpu is None:
                gpu = select_gpu(detect_nvidia(), self.config["parakeet"].get("gpu_device"))
                with self._cv:
                    if gpu:
                        self.gpu = gpu
                    else:
                        self._fallback_cpu("No supported NVIDIA GPU/driver is available")

    def _fallback_cpu(self, reason: str) -> None:
        self.backend = "cpu"
        self.fallback_reason = reason
        LOG.warning("Parakeet unavailable; falling back to Moonshine CPU: %s", reason)

    def set_backend(self, preference: str) -> None:
        with self._selection_lock:
            self.stop()
            self.config["backend"] = preference
            self._select_backend()

    @property
    def pid(self) -> int | None:
        with self._cv:
            return self._proc.pid if self._proc and self._proc.poll() is None else None

    def _spawn_locked(self) -> None:
        if self._proc and self._proc.poll() is None and self._sock:
            return
        # Reap a dead predecessor before overwriting (else zombie Popen +
        # leaked socketpair fd); the old reader thread already closes its sock.
        if self._proc is not None and self._proc.poll() is not None:
            try:
                self._proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._proc.kill()
                except OSError:
                    pass
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._proc = None
            self._sock = None
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
        for s in (parent, child):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            except OSError:
                pass
        child.set_inheritable(True)
        env = dict(os.environ)
        if self.backend == "nvidia":
            env["CUDA_VISIBLE_DEVICES"] = self.gpu["uuid"]
        else:
            env["CUDA_VISIBLE_DEVICES"] = "-1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["HF_HUB_OFFLINE"] = "1"
        # The daemon owns residency. An idle timer must not unload the model
        # during a long recording; on-demand cleanup happens at session end.
        env[NO_IDLE_EXIT_ENV] = "1"
        worker_py = APP_DIR / (".venv-gpu/bin/python" if self.backend == "nvidia" else
                              str(self.config.get("worker_python", ".venv/bin/python")))
        worker_script = APP_DIR / str(self.config.get("worker_script", "dusky_worker.py"))
        cfg = self.config_path
        try:
            proc = subprocess.Popen([str(worker_py), str(worker_script), "--config", str(cfg),
                                     "--fd", str(child.fileno()), "--backend", self.backend],
                                    cwd=APP_DIR, env=env, close_fds=True, pass_fds=(child.fileno(),))
        except BaseException as exc:
            parent.close()
            if isinstance(exc, OSError) and self.backend == "nvidia":
                self._fallback_cpu(f"GPU worker could not start: {exc}")
            raise
        finally:
            child.close()
        self._gen += 1
        self._spawns += 1
        self._proc = proc
        self._sock = parent
        self.ready = False
        threading.Thread(target=self._reader_loop, args=(self._gen, proc, parent, self.backend),
                         name=f"dusky-worker-{self._gen}", daemon=True).start()
        LOG.info("Spawned worker PID=%d gen=%d backend=%s", proc.pid, self._gen, self.backend)

    def _fail_generation(self, gen: int, reason: str) -> None:
        with self._cv:
            for req_id, g in list(self._inflight.items()):
                if g == gen and req_id not in self._results:
                    if req_id in self._discarded:
                        self._discarded.discard(req_id)
                    else:
                        self._results[req_id] = {"ok": False, "request_id": req_id, "error": reason}
                    # Free the slot: without this two worker crashes pin
                    # len(_inflight) == limit forever and the next
                    # retries otherwise wait forever for a queue slot.
                    self._inflight.pop(req_id, None)
            self._cv.notify_all()

    def _reader_loop(self, gen: int, proc: subprocess.Popen[bytes], sock: socket.socket, backend: str = "cpu") -> None:
        ready = False
        fallback = False
        try:
            while True:
                fds: list[int] = []
                try:
                    payload, ancdata, flags, _ = sock.recvmsg(MAX_PACKET, socket.CMSG_SPACE(4 * 8))
                except OSError as exc:
                    LOG.debug("Worker recv failed: %s", exc)
                    break
                for level, ctype, data in ancdata:
                    if level == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
                        n = len(data) // struct.calcsize("i")
                        fds.extend(struct.unpack(f"{n}i", data[:n * struct.calcsize("i")]))
                if flags & getattr(socket, "MSG_CTRUNC", 0x20) or flags & getattr(socket, "MSG_TRUNC", 0x20):
                    for fd in fds:
                        os.close(fd)
                    LOG.warning("Worker packet truncated; discarding generation %d", gen)
                    break
                if len(fds) > 1:
                    for fd in fds:
                        os.close(fd)
                    LOG.warning("Worker sent >1 fd; discarding")
                    continue
                if not payload:
                    for fd in fds:
                        os.close(fd)
                    break
                try:
                    resp = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    for fd in fds:
                        os.close(fd)
                    continue
                if resp.get("payload") == "memfd" and fds:
                    fd = fds[0]
                    try:
                        sz = os.fstat(fd).st_size
                        with mmap.mmap(fd, sz, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ) as m:
                            resp.update(json.loads(m.read().decode("utf-8")))
                    except (OSError, ValueError, json.JSONDecodeError) as exc:
                        LOG.warning("Bad worker memfd reply: %s", exc)
                    finally:
                        for fd in fds:
                            os.close(fd)
                else:
                    for fd in fds:
                        os.close(fd)
                    if resp.get("payload") == "memfd":
                        resp = {"ok": False, "request_id": resp.get("request_id"),
                                "error": "worker memfd reply arrived without fd"}
                req_id = resp.get("request_id")
                with self._cv:
                    if resp.get("event") == "ready":
                        ready = resp.get("ok") is True
                        if gen == self._gen:
                            self.ready = ready
                            self._cv.notify_all()
                        if not ready and backend == "nvidia" and gen == self._gen:
                            self._fallback_cpu(str(resp.get("error", "GPU initialization failed")))
                            fallback = True
                            proc.kill()
                            break
                        continue
                    if resp.get("event") == "progress":
                        if self._inflight.get(req_id) == gen:
                            self.progress = float(resp["fraction"])
                        continue
                    if resp.get("ok") is False and backend == "nvidia" and gen == self._gen:
                        self._fallback_cpu(str(resp.get("error", "GPU inference failed")))
                        fallback = True
                        proc.kill()
                        break
                    self._inflight.pop(req_id, None)
                    if req_id in self._discarded:
                        self._discarded.discard(req_id)
                    elif req_id:
                        self._results[req_id] = resp
                    self._cv.notify_all()
        except Exception as exc:
            LOG.debug("Worker channel closed: %s", exc)
        finally:
            # Reap before releasing pending requests: retries must not overlap
            # a retiring CUDA context or reuse its channel.
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            with self._cv:
                if gen == self._gen:
                    pending = any(g == gen and req not in self._discarded for req, g in self._inflight.items())
                    if backend == "nvidia" and gen not in self._intentional_exits and (pending or not ready):
                        if not fallback:
                            self._fallback_cpu("GPU worker exited unexpectedly")
                        fallback = True
                    self._proc = None
                    self._sock = None
                    self.ready = False
                self._intentional_exits.discard(gen)
                self._fail_generation(gen, "worker exited")
            sock.close()
            with self._cv:
                if fallback and self._resident_requested:
                    try:
                        self._spawn_locked()
                    except Exception as exc:
                        LOG.warning("CPU fallback prewarm failed: %s", exc)

    def submit_fd(self, fd: int, samples: int, meta: JsonObject) -> str:
        self._resolve_gpu()
        deadline = time.monotonic() + float(self.config.get("finalize_timeout_seconds", 120.0))
        with self._cv:
            limit = 1
            while len(self._inflight) >= limit:
                if time.monotonic() >= deadline:
                    if self._proc is not None and self._proc.poll() is None:
                        try:
                            self._proc.kill()
                        except OSError:
                            pass
                    raise TimeoutError("Worker request queue did not drain")
                self._cv.wait(0.1)
            self._spawn_locked()
            self._resident_requested = True
            assert self._sock is not None
            req_id = uuid.uuid4().hex
            self.progress = 0.0
            try:
                self._inflight[req_id] = self._gen
                self._sock.sendmsg([json.dumps({"op": "recognize", "request_id": req_id,
                                                "samples": samples, "encoding": "s16le", **meta}).encode()],
                                   [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))])
            except OSError:
                self._inflight.pop(req_id, None)
                # Dead-socket race (worker idle-exited before the reader
                # reaped it): drop the stale handles so the retry respawns.
                try:
                    self._sock.close()
                except OSError:
                    pass
                if self._proc is not None and self._proc.poll() is None:
                    try:
                        self._proc.kill()
                    except OSError:
                        pass
                self._proc = None
                self._sock = None
                self._cv.notify_all()
                raise
            return req_id

    def wait_result(self, req_id: str, timeout: float, stop: threading.Event | None = None) -> JsonObject | None:
        deadline = time.monotonic() + timeout
        with self._cv:
            while req_id not in self._results:
                if stop is not None and stop.is_set():
                    self._intentional_exits.add(self._gen)
                    self._discarded.add(req_id)
                    if len(self._discarded) > 128:
                        self._discarded.pop()
                    if self._proc is not None and self._proc.poll() is None:
                        self._proc.kill()
                    return None
                rem = deadline - time.monotonic()
                if rem <= 0:
                    if self.backend == "nvidia":
                        self._fallback_cpu("GPU request timed out")
                    self._discarded.add(req_id)
                    if len(self._discarded) > 128:
                        self._discarded.pop()
                    # A timeout must retire the stuck generation. Otherwise
                    # retries can queue indefinitely behind the same request.
                    if self._proc is not None and self._proc.poll() is None:
                        LOG.warning("Worker request timed out; restarting worker.")
                        try:
                            self._proc.kill()
                        except OSError:
                            pass
                    return None
                self._cv.wait(min(rem, 0.2))
            self._inflight.pop(req_id, None)
            return self._results.pop(req_id)

    def prewarm(self) -> None:
        """Load in the background at recording start."""
        with self._selection_lock:
            with self._cv:
                retiring = self._proc is not None and self._gen in self._intentional_exits
            if retiring:
                # Cancellation kills asynchronously. Do not mistake that
                # still-live process for the resident replacement.
                self._stop_worker()
            self._resolve_gpu()
            with self._cv:
                self._resident_requested = True
                self._spawn_locked()

    def stop(self) -> None:
        with self._selection_lock:
            self._stop_worker()

    def _stop_worker(self) -> None:
        with self._cv:
            self._resident_requested = False
            sock = self._sock
            proc = self._proc
            if proc is not None:
                self._intentional_exits.add(self._gen)
        if sock:
            try:
                sock.sendmsg([b'{"op":"shutdown"}'])
            except OSError:
                pass
        if proc:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    pass
        with self._cv:
            if self._proc is proc:
                self._proc = None
                self._sock = None
                self.ready = False
        if sock:
            sock.close()


def decode_file_to_pcm(path: Path, chunk_seconds: float) -> "collections.abc.Iterator[np.ndarray]":
    """Stream-decode any ffmpeg-readable file to 16k mono s16 chunks.

    Generator: yields one chunk at a time so a 2-hour podcast (~230 MB PCM)
    never materializes fully in the daemon (constant ~1 MB RSS instead of
    ~460 MB transient). Raises on ffmpeg failure.
    """
    per_samples = max(1, int(chunk_seconds * SAMPLE_RATE))
    per_bytes = per_samples * BYTES_PER_SAMPLE
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
           "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000",
           "-f", "s16le", "-c:a", "pcm_s16le", "-"]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout and proc.stderr
    errs: collections.deque[bytes] = collections.deque(maxlen=4)
    def drain() -> None:
        try:
            while block := proc.stderr.read(4096):
                errs.append(block)
        except OSError:
            pass
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    exhausted = False
    try:
        carry = b""
        while True:
            buf = proc.stdout.read(max(per_bytes - len(carry), 4096))
            if not buf:
                break
            carry += buf
            while len(carry) >= per_bytes:
                # Segmentation belongs to the selected backend. These pieces
                # are joined losslessly in the recording spool.
                piece, carry = carry[:per_bytes], carry[per_bytes:]
                yield np.frombuffer(piece, dtype="<i2").copy()
        if carry:
            # Odd trailing byte cannot form a sample; drop it.
            carry = carry[:len(carry) & ~1]
            if carry:
                yield np.frombuffer(carry, dtype="<i2").copy()
        exhausted = True
    finally:
        try:
            if not exhausted and proc.poll() is None:
                proc.kill()
        except OSError:
            pass
        t.join(timeout=10)
        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            rc = proc.wait(timeout=30)
        proc.stdout.close()
        t.join(timeout=1)
        proc.stderr.close()
        if exhausted and rc != 0:
            raise RuntimeError(f"ffmpeg failed ({rc}): {b''.join(errs)[-1000:].decode(errors='replace')}")


class RecordingSession:
    def __init__(self, daemon: "DuskyDaemon") -> None:
        self.daemon = daemon
        self.config = daemon.config
        self.session_id = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.paused = threading.Event()
        self.ready = threading.Event()
        self.errors: list[str] = []
        self.transcript_path: str | None = None
        self._indicator: subprocess.Popen | None = None
        self.samples = 0
        self.level = 0.0
        self.processing_started = 0.0
        self.dropped_samples = 0
        self.state_dir = Path(self.config["state_dir"]).expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _notify(self, title: str, text: str) -> None:
        if self.config.get("notifications", True):
            try:
                subprocess.run(["notify-send", "-a", "Dusky STT", "-t", "3500", "--",
                                title, html.escape(text[:220])], check=True, timeout=5)
            except (OSError, subprocess.SubprocessError) as exc:
                LOG.warning("Notification failed: %s", exc)

    def _publish(self, text: str, *, complete: bool = True) -> str:
        out_dir = self.state_dir / "transcripts"
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"capture-{datetime.now():%Y%m%d-%H%M%S}-{self.session_id}.txt"
        atomic_write_text(target, text + "\n")
        self.transcript_path = str(target)
        if not text:
            self._notify("Nothing transcribed" if complete else "Transcription failed",
                         "No speech detected." if complete else "; ".join(self.errors))
            return text  # Preserve the existing clipboard on silence/failure.
        try:
            subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8"],
                           input=text.encode(), check=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            self.errors.append(f"Clipboard failed; transcript saved to {target}")
            LOG.error("Clipboard failed: %s", exc)
            self._notify("Transcript saved; clipboard failed", str(target))
            return text
        self._notify("Copied transcription" if complete else "Copied partial transcription", text)
        return text

    def _capture_loop(self, spool) -> None:
        # Native PipeWire capture avoids PortAudio's slow ALSA device scan.
        command = ["pw-record", "--raw", "--rate", str(SAMPLE_RATE),
                   "--channels", "1", "--format", "s16", "--latency", "20ms"]
        target = self.config.get("capture_target")
        if target:
            command.extend(["--target", str(target)])
        command.append("-")
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
            assert proc.stdout is not None
            fd = proc.stdout.fileno()
            os.set_blocking(fd, False)
            selector = selectors.DefaultSelector()
            selector.register(fd, selectors.EVENT_READ)
            pending = b""
            startup_deadline = time.monotonic() + 10
            stopping = False
            stop_deadline = 0.0
            try:
                while True:
                    if self.stop_event.is_set() and not stopping:
                        stopping = True
                        stop_deadline = time.monotonic() + 3
                        proc.terminate()
                    if not selector.select(timeout=0.02):
                        if stopping and time.monotonic() >= stop_deadline:
                            raise TimeoutError("PipeWire capture did not stop")
                        if not self.ready.is_set() and time.monotonic() >= startup_deadline:
                            raise TimeoutError("PipeWire microphone produced no audio for 10 seconds")
                        continue
                    try:
                        raw = os.read(fd, 4096)
                    except BlockingIOError:
                        continue
                    if not raw:
                        break
                    # Pipes need not split on sample boundaries.
                    raw = pending + raw
                    length = len(raw) & ~1
                    pending = raw[length:]
                    raw = raw[:length]
                    if not raw:
                        continue
                    self.ready.set()
                    if self.paused.is_set():
                        self.level = 0.0
                        continue
                    # Save before importing NumPy for the first meter update.
                    spool.write(raw)
                    self.samples += len(raw) // BYTES_PER_SAMPLE
                    pcm = np.frombuffer(raw, dtype="<i2")
                    self.level = min(1.0, float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) / 6000)
                rc = proc.wait(timeout=3)
                if not stopping:
                    errors.seek(0)
                    detail = errors.read(2000).decode(errors="replace").strip()
                    raise RuntimeError(f"PipeWire capture ended unexpectedly ({rc}): {detail}")
                if pending:
                    raise RuntimeError("PipeWire capture ended with an incomplete sample")
            finally:
                selector.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                proc.stdout.close()

    def _recognize(self, spool, cancel: threading.Event) -> str:
        spool.flush()
        self.processing_started = time.monotonic()
        if not self.samples:
            return ""
        timeout = max(float(self.config.get("finalize_timeout_seconds", 120)), self.samples / SAMPLE_RATE * 2)
        res = None
        for attempt in (1, 2):
            if cancel.is_set():
                raise RuntimeError("Transcription cancelled")
            try:
                req = self.daemon.worker.submit_fd(spool.fileno(), self.samples, {})
                res = self.daemon.worker.wait_result(req, timeout, stop=cancel)
            except (OSError, TimeoutError) as exc:
                LOG.warning("Worker submit attempt %d failed: %s", attempt, exc)
                res = None
            if res and res.get("ok") is True:
                LOG.info("Recognized %.2fs audio in %.1fms", self.samples / SAMPLE_RATE, res.get("latency_ms", 0))
                return str(res.get("text", "")).strip()
            LOG.warning("Recognition attempt %d failed: %s", attempt, res)
        raise RuntimeError("Transcription cancelled" if cancel.is_set() else f"Recognition failed: {res}")

    def run(self) -> str:
        # Disk spool bounds RAM independently of recording duration. The file is
        # unlinked automatically, including errors; caches are never touched.
        with tempfile.TemporaryFile(dir=self.state_dir, prefix="audio-", mode="w+b") as spool:
            try:
                self._capture_loop(spool)
            except Exception as exc:
                self.errors.append(f"Capture failed: {exc}")
            finally:
                self.ready.set()
                self.level = 0.0
                with self.daemon._lock:
                    self.daemon.state = "finalizing"
            try:
                text = self._recognize(spool, self.cancel_event)
            except Exception as exc:
                self.errors.append(str(exc))
                self._publish("", complete=False)
                raise
            result = self._publish(text, complete=not self.errors)
            if self.errors:
                raise RuntimeError("; ".join(self.errors))
            return result

    def run_file(self, path: Path) -> str:
        with tempfile.TemporaryFile(dir=self.state_dir, prefix="audio-", mode="w+b") as spool:
            for chunk in decode_file_to_pcm(path, 20):
                if self.stop_event.is_set():
                    raise RuntimeError("Transcription cancelled")
                spool.write(chunk.tobytes())
                self.samples += chunk.size
            text = self._recognize(spool, self.stop_event)
            result = self._publish(text)
            if self.errors:
                raise RuntimeError("; ".join(self.errors))
            return result


def _kill_indicator(sess: RecordingSession) -> None:
    """Close and reap the pill after completion, failure or recorder shutdown."""
    proc = sess._indicator
    sess._indicator = None
    if proc is not None:
        try:
            proc.terminate()
        except OSError:
            pass
        def reap() -> None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        threading.Thread(target=reap, daemon=True).start()


class DuskyDaemon:
    def __init__(self, config_path: Path) -> None:
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        if self.config.get("schema_version") != 3:
            raise RuntimeError("config schema_version must be 3")
        if int(self.config.get("max_inflight_requests", 2)) < 1:
            raise RuntimeError("max_inflight_requests must be positive")
        value = float(self.config.get("finalize_timeout_seconds", 120.0))
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError("finalize_timeout_seconds must be finite and positive")
        self.worker = WorkerManager(self.config, config_path)
        self.state = "idle"
        self._lock = threading.RLock()
        self._session: RecordingSession | None = None
        self._file_session: RecordingSession | None = None
        self._stop = threading.Event()
        self._start_time = time.monotonic()
        rt = os.environ.get("XDG_RUNTIME_DIR")
        if not rt:
            raise RuntimeError("XDG_RUNTIME_DIR unset")
        self.control_path = Path(rt) / "dusky-stt" / "control.sock"
        self.control_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._instance_lock = open(self.control_path.parent / "instance.lock", "a+b")
        try:
            fcntl.flock(self._instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._instance_lock.close()
            raise RuntimeError("A recording process is already running")
        self._listener = self._bind_socket()

    def _bind_socket(self) -> socket.socket:
        self.control_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.control_path.parent, 0o700)
        self.control_path.unlink(missing_ok=True)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
        old = os.umask(0o177)
        try:
            s.bind(str(self.control_path))
        finally:
            os.umask(old)
        os.chmod(self.control_path, 0o600)
        s.listen(16)
        s.setblocking(False)
        return s

    def status(self) -> JsonObject:
        with self._lock:
            sess = self._session
            state = self.state
            if sess is not None and sess.stop_event.is_set() and state in ("recording", "transcribing"):
                state = "finalizing"
            return {"ok": True, "state": state, "pid": os.getpid(), "worker_pid": self.worker.pid,
                    "hardware": self.worker.backend,
                    "backend_preference": self.config.get("backend", "cpu"),
                    "available_backends": ["cpu", "nvidia"] if self.config.get("parakeet") else ["cpu"],
                    "fallback_reason": self.worker.fallback_reason,
                    "worker_ready": self.worker.ready,
                    "capture_ready": bool(sess and sess.ready.is_set()),
                    "session": sess.session_id[:8] if sess is not None else None,
                    "paused": bool(sess.paused.is_set()) if sess is not None else False,
                    "uptime_seconds": round(time.monotonic() - self._start_time, 1),
                    "rss_kib": self._rss(), "cuda_maps": cuda_maps(),
                    "dropped_samples": sess.dropped_samples if sess else 0,
                    "recorded_seconds": sess.samples / SAMPLE_RATE if sess else 0,
                    "level": sess.level if sess else 0,
                    "progress": self.worker.progress if sess and sess.processing_started else 0,
                    "processing_seconds": round(time.monotonic() - sess.processing_started, 1)
                        if sess and sess.processing_started else 0}

    @staticmethod
    def _rss() -> int:
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
        except OSError:
            pass
        return 0

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())
        sel = selectors.DefaultSelector()
        sel.register(self._listener, selectors.EVENT_READ)
        try:
            while not self._stop.is_set():
                timeout = 0.1
                for key, _ in sel.select(timeout=timeout):
                    if key.fileobj is self._listener:
                        try:
                            conn, _ = self._listener.accept()
                        except (BlockingIOError, OSError):
                            continue
                        threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()
                with self._lock:
                    # A trigger can die after launch but before delivering its
                    # command. Do not leave that unused recorder resident.
                    if self.state == "idle" and time.monotonic() - self._start_time >= 10:
                        self._maybe_self_stop()
        finally:
            sel.close()
            self._listener.close()
            with self._lock:
                if self._session:
                    self._session.cancel_event.set()
                    self._session.stop_event.set()
                    _kill_indicator(self._session)
            if getattr(self, "_session_thread", None):
                self._session_thread.join(timeout=5)
            self.worker.stop()
            self.control_path.unlink(missing_ok=True)
            self._instance_lock.close()
        return 0

    def _handle_conn(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(5)
                cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                _, uid, _ = struct.unpack("3i", cred)
                if uid != os.getuid():
                    return
                # recvmsg (not recv): SEQPACKET truncation is only visible
                # via MSG_TRUNC, otherwise a crafted oversize datagram could
                # truncate to still-valid JSON and mis-execute.
                data, _, flags, _ = conn.recvmsg(MAX_PACKET)
                if flags & getattr(socket, "MSG_TRUNC", 0x20):
                    return
                if not data:
                    return
                req = json.loads(data.decode("utf-8"))
                if not isinstance(req, dict):
                    conn.sendmsg([b'{"ok":false,"error":"request must be an object"}'])
                    return
            except (OSError, ValueError):
                return
            cmd = req.get("command")
            owner = req.get("session")
            if owner and cmd != "status":
                with self._lock:
                    if self._session is None or self._session.session_id != owner:
                        conn.sendmsg([b'{"ok":false,"error":"session ended"}'])
                        return
            resp: JsonObject = {"ok": False, "error": f"unknown command {cmd!r}"}
            retire = False
            try:
                with self._lock:
                    if self._stop.is_set():
                        resp = {"ok": False, "error": "recording process stopping", "retry": True}
                    elif cmd == "shutdown":
                        self.state = "stopping"
                        retire = True
                        resp = {"ok": True, "event": "stopping"}
                    elif cmd == "status":
                        resp = self.status()
                    elif cmd == "backend":
                        preference = req.get("backend")
                        if self.state != "idle":
                            resp = {"ok": False, "error": "busy", "state": self.state}
                        elif preference not in ("auto", "cpu", "nvidia"):
                            resp = {"ok": False, "error": "backend must be auto, cpu or nvidia"}
                        elif preference == "nvidia" and not self.config.get("parakeet"):
                            resp = {"ok": False, "error": "Run the installer with --backend auto or --backend nvidia first"}
                        else:
                            self.worker.set_backend(preference)
                            atomic_write_text(self.worker.config_path, json.dumps(self.config, indent=2) + "\n")
                            resp = self.status()
                            retire = True
                    elif cmd in ("start", "toggle"):
                        if self.state == "idle":
                            self._session = RecordingSession(self)
                            # Publish state under the lock so --status never
                            # reports stale idle after start was acked recording.
                            self.state = "recording"
                            self._session_thread = threading.Thread(target=self._run_session, args=(self._session, False, None), daemon=True)
                            self._session_thread.start()
                            resp = {"ok": True, "state": "recording", "job": self._session.session_id}
                        elif cmd == "toggle" and self._session:
                            if self._session.stop_event.is_set():
                                resp = {"ok": False, "error": "Transcription in progress", "state": "finalizing"}
                            else:
                                self._session.stop_event.set()
                                resp = {"ok": True, "state": "finalizing", "job": self._session.session_id}
                        else:
                            resp = {"ok": False, "error": "already recording", "state": self.state}
                    elif cmd == "stop":
                        if self._session:
                            if self.state == "finalizing":
                                self._session.cancel_event.set()
                            self._session.stop_event.set()
                            resp = {"ok": True, "state": "finalizing", "job": self._session.session_id}
                        else:
                            resp = {"ok": False, "error": "not recording", "state": self.state}
                    elif cmd == "pause":
                        sess = self._session
                        if sess is not None and self.state == "recording" and not sess.stop_event.is_set():
                            if sess.paused.is_set():
                                sess.paused.clear()
                                resp = {"ok": True, "event": "resumed", "state": self.state}
                            else:
                                sess.paused.set()
                                resp = {"ok": True, "event": "paused", "state": self.state}
                        else:
                            resp = {"ok": False, "error": "not recording", "state": self.state}
                    elif cmd == "unload":
                        if self.state == "idle":
                            self.worker.stop()
                            resp = {"ok": True, "event": "unloaded", "worker_pid": None}
                            retire = True
                        else:
                            resp = {"ok": False, "error": "busy", "state": self.state}
                    elif cmd == "file":
                        if self.state == "idle":
                            try:
                                p = Path(str(req.get("path", ""))).expanduser()
                                if not p.is_file():
                                    resp = {"ok": False, "error": f"file not found: {p}"}
                                else:
                                    self._session = RecordingSession(self)
                                    self.state = "transcribing"
                                    self._session_thread = threading.Thread(target=self._run_session, args=(self._session, True, p), daemon=True)
                                    self._session_thread.start()
                                    resp = {"ok": True, "state": "transcribing", "job": self._session.session_id}
                            except (OSError, ValueError) as exc:
                                resp = {"ok": False, "error": str(exc)}
                        else:
                            resp = {"ok": False, "error": "busy", "state": self.state}
            except Exception as exc:
                LOG.error("Command %s failed: %s", cmd, exc)
                resp = {"ok": False, "error": str(exc)}
            try:
                # Single datagram: sendall could split an oversize reply into
                # N datagrams of which the client reads only the first.
                raw = json.dumps(resp).encode()
                if len(raw) > MAX_PACKET:
                    raw = json.dumps({"ok": False, "error": "response too large"}).encode()
                conn.sendmsg([raw])
            except OSError:
                pass
            finally:
                if retire:
                    with self._lock:
                        if self.state == "stopping":
                            self._stop.set()
                        else:
                            self._maybe_self_stop()

    def _prewarm_worker(self) -> None:
        if self._stop.is_set():
            return
        try:
            self.worker.prewarm()
        except Exception as exc:
            LOG.warning("Worker prewarm failed (on-demand respawn still works): %s", exc)

    def _maybe_self_stop(self) -> None:
        """Exit after a completed job; the next hotkey launches a fresh process."""
        with self._lock:
            if self.state == "idle" and self._session is None:
                self.state = "stopping"
                self._stop.set()

    def _run_session(self, sess: RecordingSession, is_file: bool, path: Path | None) -> None:
        self.state = "transcribing" if is_file else "recording"
        indicator: subprocess.Popen | None = None
        error: str | None = None
        if not is_file:
            with self._lock:
                if not sess.stop_event.is_set():
                    sess._indicator = self._spawn_indicator(sess)
        def prepare_worker() -> None:
            if not is_file:
                sess.ready.wait()
                with self._lock:
                    if self.state != "recording" or sess.stop_event.is_set():
                        return
            self._prewarm_worker()
        preload = threading.Thread(target=prepare_worker, daemon=True, name="dusky-session-preload")
        preload.start()
        try:
            sess.run_file(path) if (is_file and path) else sess.run()
        except Exception as exc:
            error = str(exc)
            LOG.error("Session failed: %s", exc)
            if not sess.transcript_path:
                sess._notify("Transcription failed", str(exc))
        finally:
            # A delayed hardware probe must not spawn a worker after cleanup.
            sess.ready.set()
            preload.join()
            # Keep processing feedback visible until publication completes.
            indicator = sess._indicator
            _kill_indicator(sess)
            if indicator is not None:
                try:
                    indicator.wait(timeout=2.0)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        indicator.kill()
                    except OSError:
                        pass
            try:
                self.worker.stop()
            except Exception as exc:
                LOG.warning("Worker release after session failed: %s", exc)
            with self._lock:
                self.state = "idle"
                self._session = None
                # A visible completed job means the next take can start.
                # Publishing before cleanup let --wait return while still busy.
                try:
                    results = Path(str(self.config.get("state_dir", "~/.local/state/dusky-stt"))).expanduser() / "jobs"
                    results.mkdir(parents=True, exist_ok=True)
                    atomic_write_text(results / f"{sess.session_id}.json", json.dumps({
                        "ok": error is None and not (sess.stop_event.is_set() if is_file else sess.cancel_event.is_set()), "job": sess.session_id,
                        "event": "error" if error else "transcribed",
                        "path": sess.transcript_path, "error": error,
                    }) + "\n")
                except OSError as exc:
                    LOG.error("Cannot save job result: %s", exc)
                self._maybe_self_stop()

    @staticmethod
    def _spawn_indicator(sess: RecordingSession) -> "subprocess.Popen[bytes] | None":
        """Show the on-screen recording pill (best effort, never fatal)."""
        try:
            script = APP_DIR / "dusky-rec-indicator"
            if not script.is_file():
                return None
            return subprocess.Popen([str(script), sess.session_id],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    start_new_session=True)
        except OSError as exc:
            LOG.warning("Recording indicator unavailable: %s", exc)
            return None


def main() -> int:
    global APP_DIR, CONFIG_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=CONFIG_PATH)
    ap.add_argument("--check-cpu-isolation", action="store_true")
    args = ap.parse_args()
    CONFIG_PATH = args.config
    APP_DIR = Path(os.environ.get("DUSKY_APP_DIR", args.config.parent if args.config.name == "config.json" else APP_DIR))
    if args.check_cpu_isolation:
        assert not cuda_maps(), "Unexpected CUDA libraries in the capture process"
        print(json.dumps({"ok": True, "isolation": "clean", "cuda_maps": cuda_maps()}))
        return 0
    return DuskyDaemon(CONFIG_PATH).run()


if __name__ == "__main__":
    sys.exit(main())
