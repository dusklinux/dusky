#!/usr/bin/env python3
"""Isolated Moonshine CPU / Parakeet CUDA worker; one final transcript."""

import argparse
import fcntl
import json
import os
import select
import selectors
import socket
import stat
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

MIN_PYTHON = (3, 14)
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
# Upstream's supported switch: otherwise each session creates a large spinning
# thread pool. One inference thread is portable and markedly faster here.
os.environ["MOONSHINE_ORT_SINGLE_THREAD"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
import numpy as np
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
MAX_PACKET = 65536
MAX_INLINE = 57344

if sys.version_info < MIN_PYTHON:
    raise SystemExit("Worker requires CPython 3.14+ (CPU backend requires 3.15+)")
_gil = getattr(sys, "_is_gil_enabled", None)
if _gil is None or not _gil():
    raise SystemExit("Worker requires GIL-enabled CPython")

type JsonObject = dict[str, Any]

# Linux UAPI values: UV's managed build omits the sealing constants even
# though the target kernel supports them. Verified against linux/fcntl.h.
F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
REQUIRED_SEALS = 0x0001 | 0x0002 | 0x0004 | 0x0008
# The verified CPython 3.15 build omits this Linux ABI constant.
if not hasattr(os, "MFD_NOEXEC_SEAL"):
    os.MFD_NOEXEC_SEAL = 0x0008  # type: ignore[attr-defined]

def fail(msg: str, code: int = 2) -> None:
    sys.stderr.write(f"dusky-worker: {msg}\n")
    sys.stderr.flush()
    raise SystemExit(code)


class AsrEngine:
    hardware = "cpu"
    def __init__(self, config: JsonObject) -> None:
        if sys.version_info < (3, 15):
            raise RuntimeError("Moonshine CPU worker requires CPython 3.15+")
        from moonshine_voice import Transcriber, ModelArch, TranscriptEventListener
        self.listener_base = TranscriptEventListener
        if config.get("model") not in ("small_streaming", "medium_streaming"):
            raise ValueError("model must be small_streaming or medium_streaming (English)")
        self.transcriber = Transcriber(
            model_path=str(Path(config["model_dir"]).expanduser()),
            model_arch=getattr(ModelArch, config["model"].upper()),
            options={"ort_providers": "CPU", "decode_incomplete_lines": "false",
                     "return_audio_data": "false"})

    def recognize_fd(self, fd: int, samples: int, on_progress=None) -> str:
        texts: list[str] = []
        errors: list[str] = []
        class Listener(self.listener_base):
            def on_line_completed(self, event):
                if event.line.text.strip():
                    texts.append(event.line.text.strip())
            def on_error(self, event):
                # Moonshine catches listener exceptions, including at Stop.
                errors.append(str(event.error))
        # Bound input copies and retain native segmentation across blocks.
        # Fresh stream per job prevents hypothesis/audio state leaking between
        # recordings. return_audio_data=false releases completed VAD audio.
        with self.transcriber.create_stream(update_interval=5.0) as stream:
            stream.add_listener(Listener())
            stream.start()
            offset = 0
            while offset < samples * BYTES_PER_SAMPLE:
                raw = os.pread(fd, min(SAMPLE_RATE * BYTES_PER_SAMPLE * 5, samples * BYTES_PER_SAMPLE - offset), offset)
                if not raw or len(raw) % BYTES_PER_SAMPLE:
                    raise ValueError("Audio spool ended before the declared sample count")
                pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
                stream.add_audio(pcm, SAMPLE_RATE)
                offset += len(raw)
                if on_progress is not None:
                    on_progress(min(0.99, offset / (samples * BYTES_PER_SAMPLE)))
            # In 0.1.5, decode_incomplete_lines=false can leave the final VAD
            # segment empty at Stop. A second of zero input settles its 0.5s
            # detection window and completes recorded speech before finalizing.
            silence = np.zeros(SAMPLE_RATE // 2, dtype=np.float32)
            stream.add_audio(silence, SAMPLE_RATE)
            stream.add_audio(silence, SAMPLE_RATE)
            stream.stop()
            if errors:
                raise RuntimeError("Moonshine stream failed: " + "; ".join(errors))
        return " ".join(texts)

    def close(self) -> None:
        self.transcriber.close()


def quiet_chunk_boundary(pcm: np.ndarray) -> int:
    """Prefer 120 ms of quiet near the end of a bounded Parakeet block."""
    frame = 320
    start = max(0, pcm.size - 3 * SAMPLE_RATE)
    tail = pcm[start:].astype(np.float32)
    frames = tail.size // frame
    if frames < 6:
        return pcm.size
    power = np.mean(tail[:frames * frame].reshape(-1, frame) ** 2, axis=1)
    quiet = np.flatnonzero(np.convolve(power, np.ones(6) / 6, mode="valid") < 300 ** 2)
    return start + (int(quiet[-1]) + 3) * frame if quiet.size else pcm.size


def preload_cuda13() -> None:
    """Load the GPU environment's libraries before ORT imports libcudart."""
    import ctypes
    import importlib.metadata
    order = ("libnvJitLink.so.13", "libcudart.so.13", "libnvrtc-builtins.so.13", "libnvrtc.so.13",
        "libcublasLt.so.13", "libcublas.so.13", "libcufft.so.12", "libcurand.so.10",
        "libcudnn_graph.so.9", "libcudnn_engines_precompiled.so.9", "libcudnn_ops.so.9",
        "libcudnn_adv.so.9", "libcudnn_cnn.so.9", "libcudnn.so.9")
    optional = frozenset(order[8:-1])
    resolved = {}
    for name in ("nvidia-cuda-runtime", "nvidia-cublas", "nvidia-cudnn-cu13",
                 "nvidia-cuda-nvrtc", "nvidia-cufft", "nvidia-curand", "nvidia-nvjitlink"):
        distribution = importlib.metadata.distribution(name)
        for item in distribution.files or ():
            for soname in order:
                if item.name == soname or item.name.startswith(soname + "."):
                    resolved.setdefault(soname, distribution.locate_file(item))
    for soname in order:
        path = resolved.get(soname)
        if path is None:
            if soname in optional:
                continue
            raise RuntimeError(f"GPU runtime library missing: {soname}")
        try:
            ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL | os.RTLD_NOW)
        except OSError:
            if soname not in optional:
                raise
    ctypes.CDLL("libcuda.so.1", mode=ctypes.RTLD_GLOBAL | os.RTLD_NOW)


class ParakeetEngine:
    hardware = "nvidia"

    def __init__(self, config: JsonObject, *, profile_dir: Path | None = None) -> None:
        preload_cuda13()
        import onnxruntime as ort
        import onnx_asr
        cfg = config["parakeet"]
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_mem_pattern = False
        options.intra_op_num_threads = min(8, os.process_cpu_count() or 1)
        options.inter_op_num_threads = 1
        # Multiple model sessions otherwise spin competing CPU thread pools.
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.log_severity_level = 3
        if profile_dir is not None:
            options.enable_profiling = True
            options.profile_file_prefix = str(profile_dir / "parakeet")
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError("ONNX Runtime has no CUDA execution provider")
        # The daemon maps its discovered GPU UUID to logical device zero.
        providers = [("CUDAExecutionProvider", {
            "device_id": 0, "arena_extend_strategy": "kSameAsRequested",
            # Per-session arena, not a total VRAM cap. Clamp old configs too.
            "gpu_mem_limit": min(512, max(1, int(cfg.get("gpu_mem_limit_mb", 512)))) * 1024 * 1024,
            "cudnn_conv_algo_search": "HEURISTIC", "cudnn_conv_use_max_workspace": "0",
            "use_ep_level_unified_stream": "1", "enable_cuda_graph": "0",
            "do_copy_in_default_stream": "1"}), "CPUExecutionProvider"]
        self.model = onnx_asr.load_model(cfg["model"], str(Path(cfg["model_dir"]).expanduser()),
            quantization="int8", sess_options=options, providers=providers,
            preprocessor_config={"max_concurrent_workers": 1, "use_conv_preprocessors": True})
        self.sessions = []
        seen = set()
        def discover(node, depth=0):
            if depth > 6 or id(node) in seen:
                return
            seen.add(id(node))
            if isinstance(node, ort.InferenceSession):
                self.sessions.append(node)
            else:
                for value in getattr(node, "__dict__", {}).values():
                    discover(value, depth + 1)
        discover(self.model)
        if not any(session.get_providers()[0] == "CUDAExecutionProvider" for session in self.sessions):
            raise RuntimeError("Parakeet sessions silently fell back to CPU")
        # Prime kernels and bounded-block allocations while capture continues.
        # Session construction alone leaves first-inference setup until Stop.
        self.model.recognize(np.zeros(20 * SAMPLE_RATE, dtype=np.float32), sample_rate=SAMPLE_RATE)

    def recognize_fd(self, fd: int, samples: int, on_progress=None) -> str:
        texts = []
        offset = 0
        while offset < samples:
            raw = os.pread(fd, min(20 * SAMPLE_RATE, samples - offset) * BYTES_PER_SAMPLE,
                           offset * BYTES_PER_SAMPLE)
            if not raw or len(raw) % BYTES_PER_SAMPLE:
                raise ValueError("Audio spool ended before the declared sample count")
            pcm = np.frombuffer(raw, dtype="<i2")
            length = quiet_chunk_boundary(pcm) if offset + pcm.size < samples else pcm.size
            text = self.model.recognize(pcm[:length].astype(np.float32) / 32768, sample_rate=SAMPLE_RATE)
            if text:
                texts.append(str(text).strip())
            offset += length
            if on_progress is not None:
                on_progress(min(.99, offset / samples))
        return " ".join(texts)

    def cuda_nodes(self) -> int:
        nodes = 0
        for session in self.sessions:
            path = session.end_profiling()
            if path:
                events = json.loads(Path(path).read_text())
                nodes += sum(event.get("args", {}).get("provider") == "CUDAExecutionProvider" for event in events)
        return nodes

    def close(self) -> None:
        self.sessions.clear()
        self.model = None


def create_engine(config: JsonObject, backend: str, *, profile_dir: Path | None = None):
    return ParakeetEngine(config, profile_dir=profile_dir) if backend == "nvidia" else AsrEngine(config)


def validate_audio_fd(fd: int, samples: int) -> None:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("Descriptor is not a regular file")
    if samples <= 0 or st.st_size != samples * BYTES_PER_SAMPLE:
        raise ValueError("Audio spool size does not match samples")


def sealed_response(payload: JsonObject) -> tuple[bytes, int | None]:
    raw = json.dumps(payload, ensure_ascii=False).encode()
    if len(raw) <= MAX_INLINE:
        return raw, None
    fd = os.memfd_create("dusky-resp", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING | os.MFD_NOEXEC_SEAL)
    try:
        os.ftruncate(fd, len(raw))
        view = memoryview(raw)
        off = 0
        while off < len(raw):
            off += os.pwrite(fd, view[off:], off)
        fcntl.fcntl(fd, F_ADD_SEALS, REQUIRED_SEALS)
        stub = {k: v for k, v in payload.items() if k != "text"}
        stub["payload"] = "memfd"
        return json.dumps(stub).encode(), fd
    except BaseException:
        os.close(fd)
        raise


def send_response(sock: socket.socket, payload: JsonObject) -> None:
    raw, fd = sealed_response(payload)
    if fd is None:
        sock.sendmsg([raw])
    else:
        try:
            sock.sendmsg([raw], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))])
        finally:
            os.close(fd)


def recv_request(sock: socket.socket) -> tuple[JsonObject | None, int | None]:
    fds: list[int] = []
    try:
        payload, ancdata, flags, _ = sock.recvmsg(MAX_PACKET, socket.CMSG_SPACE(4 * 8))
    except OSError:
        return None, None
    for lvl, ct, data in ancdata:
        if lvl == socket.SOL_SOCKET and ct == socket.SCM_RIGHTS:
            n = len(data) // struct.calcsize("i")
            fds.extend(struct.unpack(f"{n}i", data[:n * struct.calcsize("i")]))
    if flags & getattr(socket, "MSG_CTRUNC", 0x20) or flags & getattr(socket, "MSG_TRUNC", 0x20):
        for fd in fds:
            os.close(fd)
        raise ValueError("Packet truncated (MSG_TRUNC/CTRUNC)")
    if len(fds) > 1:
        for fd in fds:
            os.close(fd)
        raise ValueError("At most one fd per packet")
    if not payload:
        for fd in fds:
            os.close(fd)
        return None, None
    try:
        header = json.loads(payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        for fd in fds:
            os.close(fd)
        raise ValueError(f"Bad header: {exc}") from exc
    if not isinstance(header, dict):
        for fd in fds:
            os.close(fd)
        raise ValueError("Header must be an object")
    return header, (fds[0] if fds else None)


def exit_on_disconnect(fd: int) -> None:
    """Release native models even if the recorder dies during native inference."""
    poller = select.poll()
    poller.register(fd, select.POLLHUP | select.POLLRDHUP)
    for _, flags in poller.poll():
        if flags & (select.POLLHUP | select.POLLRDHUP):
            os._exit(0)


def run_worker(fd: int, config_path: Path, backend: str = "cpu") -> int:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if cfg.get("schema_version") != 3:
        fail("config schema_version must be 3")
    sock = socket.socket(fileno=fd)
    if sock.family != socket.AF_UNIX or sock.type != socket.SOCK_SEQPACKET:
        fail("Inherited fd is not AF_UNIX SOCK_SEQPACKET")
    try:
        cred = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        peer_pid, peer_uid, _ = struct.unpack("3i", cred)
        if peer_uid != os.getuid() or peer_pid != os.getppid():
            fail("Peer verification failed")
    except OSError as exc:
        fail(f"SO_PEERCRED failed: {exc}")
    # A temporary recorder has no systemd cgroup to reap an orphaned worker.
    threading.Thread(target=exit_on_disconnect, args=(fd,), daemon=True,
                     name="dusky-parent-channel").start()
    try:
        engine = create_engine(cfg, backend)
    except Exception as exc:
        send_response(sock, {"ok": False, "event": "ready", "hardware": backend, "error": str(exc)})
        sock.close()
        return 2
    send_response(sock, {"ok": True, "event": "ready", "hardware": engine.hardware})
    timeout = max(5.0, float(cfg.get("idle_timeout_seconds", 90.0)))
    # The daemon owns residency, including long on-demand recordings. Direct
    # worker users retain the idle timeout unless they set this environment.
    no_idle_exit = os.environ.get("DUSKY_WORKER_NO_IDLE_EXIT") == "1"
    if no_idle_exit:
        sys.stderr.write("dusky-worker: daemon-managed residency, idle exit disabled\n")
    sel = selectors.DefaultSelector()
    sel.register(sock, selectors.EVENT_READ)
    deadline = None if no_idle_exit else time.monotonic() + timeout
    try:
        while True:
            if deadline is None:
                if not sel.select(timeout=1.0):
                    continue
            else:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    return 0
                if not sel.select(timeout=min(rem, 1.0)):
                    continue
            try:
                req, audio_fd = recv_request(sock)
            except ValueError as exc:
                send_response(sock, {"ok": False, "error": str(exc)})
                continue
            if req is None:
                return 0
            if deadline is not None:
                deadline = time.monotonic() + timeout
            op = req.get("op", "recognize")
            if op == "shutdown":
                if audio_fd is not None:
                    os.close(audio_fd)
                send_response(sock, {"ok": True, "request_id": req.get("request_id")})
                return 0
            if op != "recognize":
                if audio_fd is not None:
                    os.close(audio_fd)
                send_response(sock, {"ok": False, "request_id": req.get("request_id"), "error": f"unknown op {op!r}"})
                continue
            if audio_fd is None:
                send_response(sock, {"ok": False, "request_id": req.get("request_id"), "error": "missing audio fd"})
                continue
            try:
                samples = int(req.get("samples", 0))
                if req.get("encoding") != "s16le" or samples <= 0:
                    raise ValueError("Bad encoding/samples")
                validate_audio_fd(audio_fd, samples)
                t0 = time.monotonic()
                text = engine.recognize_fd(audio_fd, samples, lambda fraction: send_response(sock, {
                    "event": "progress", "request_id": req.get("request_id"), "fraction": fraction}))
                send_response(sock, {"ok": True, "request_id": req.get("request_id"), "text": text,
                                     "latency_ms": round((time.monotonic() - t0) * 1000, 1)})
            except Exception as exc:
                send_response(sock, {"ok": False, "request_id": req.get("request_id"),
                                     "error": f"{type(exc).__name__}: {exc}"})
            finally:
                os.close(audio_fd)
    finally:
        sel.close()
        sock.close()
        engine.close()


def self_test(config_path: Path, backend: str = "cpu") -> int:
    import tempfile
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if backend == "nvidia":
        from dusky_hardware import detect_nvidia, select_gpu
        gpu = select_gpu(detect_nvidia(), cfg["parakeet"].get("gpu_device"))
        if gpu is None:
            raise RuntimeError("No supported NVIDIA GPU is available for the smoke test")
        os.environ["CUDA_VISIBLE_DEVICES"] = gpu["uuid"]
    profile_dir = tempfile.TemporaryDirectory(prefix="dusky-gpu-profile-")
    engine = None
    try:
        engine = create_engine(cfg, backend, profile_dir=Path(profile_dir.name) if backend == "nvidia" else None)
        with tempfile.TemporaryFile() as spool:
            samples = (20 if backend == "nvidia" else 1) * SAMPLE_RATE
            spool.write(bytes(samples * BYTES_PER_SAMPLE))
            spool.flush()
            start = time.monotonic()
            text = engine.recognize_fd(spool.fileno(), samples)
            if text:
                raise RuntimeError(f"Silent audio produced text: {text!r}")
        cuda_nodes = engine.cuda_nodes() if backend == "nvidia" else 0
        if backend == "nvidia" and not cuda_nodes:
            raise RuntimeError("GPU smoke test executed no CUDA nodes")
        print(json.dumps({"ok": True, "hardware": backend, "cuda_nodes": cuda_nodes,
                          "model": cfg["parakeet"]["model"] if backend == "nvidia" else cfg["model"],
                          "latency_ms": round((time.monotonic() - start) * 1000, 1)}))
        return 0
    finally:
        if engine is not None:
            engine.close()
        profile_dir.cleanup()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--fd", type=int, default=-1)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--backend", choices=("cpu", "nvidia"), default="cpu")
    args = ap.parse_args()
    if args.self_test:
        return self_test(args.config, args.backend)
    if args.fd < 0:
        fail("--fd required outside --self-test")
    return run_worker(args.fd, args.config, args.backend)


if __name__ == "__main__":
    sys.exit(main())
