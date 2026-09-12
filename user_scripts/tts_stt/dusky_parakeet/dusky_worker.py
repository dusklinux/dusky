#!/usr/bin/env python3
"""Dusky STT worker (hardware-agnostic, exec-isolated).

Runs under .venv-worker. Provider choice follows config hardware:
  nvidia: CUDAExecutionProvider (strict, profile-verified) + CPU fallback
  amd:    tries MIGraphX/ROCM if available, else CPU (reliable)
  cpu:    CPUExecutionProvider only

Sealed memfds are re-validated on receipt (size + seals + F_SEAL_EXEC=0x0020).
Oversized replies return via a second sealed memfd (never truncated).
Exits on idle timeout so discrete GPUs can reach D3cold (process exit is the
only guaranteed CUDA teardown).
"""

import argparse
import ctypes
import fcntl
import importlib.metadata
import json
import mmap
import os
import selectors
import shutil
import socket
import stat
import struct
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

MIN_PYTHON = (3, 14, 6)
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
MAX_PACKET = 65536
MAX_INLINE = 57344

if sys.version_info < MIN_PYTHON:
    raise SystemExit("Worker requires CPython 3.14.6+")
_gil = getattr(sys, "_is_gil_enabled", None)
if _gil is None or not _gil():
    raise SystemExit("Worker requires GIL-enabled CPython")

type JsonObject = dict[str, Any]

# Kernel ABI values (Python 3.14 does not expose F_SEAL_EXEC / MFD_NOEXEC_SEAL).
F_SEAL_EXEC = 0x0020
REQUIRED_SEALS = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
if not hasattr(os, "MFD_NOEXEC_SEAL"):
    os.MFD_NOEXEC_SEAL = 0x0008  # type: ignore[attr-defined]

CUDA_PRELOAD_ORDER = (
    "libnvJitLink.so.13", "libcudart.so.13", "libnvrtc-builtins.so.13", "libnvrtc.so.13",
    "libcublasLt.so.13", "libcublas.so.13", "libcufft.so.12", "libcurand.so.10",
    "libcudnn_graph.so.9", "libcudnn_engines_precompiled.so.9", "libcudnn_ops.so.9",
    "libcudnn_adv.so.9", "libcudnn_cnn.so.9", "libcudnn.so.9",
)
OPTIONAL_CUDNN = frozenset({
    "libcudnn_graph.so.9", "libcudnn_engines_precompiled.so.9",
    "libcudnn_ops.so.9", "libcudnn_adv.so.9", "libcudnn_cnn.so.9",
})


def fail(msg: str, code: int = 2) -> None:
    sys.stderr.write(f"dusky-worker: {msg}\n")
    sys.stderr.flush()
    raise SystemExit(code)


def assert_worker_namespace(hardware: str) -> None:
    owners = sorted(set(importlib.metadata.packages_distributions().get("onnxruntime", [])))
    expected = ["onnxruntime-gpu"] if hardware == "nvidia" else ["onnxruntime"]
    # AMD paths: the plain CPU wheel ("onnxruntime") and AMD's ROCm wheels
    # ("onnxruntime-rocm", from repo.radeon.com) both export the
    # "onnxruntime" module -- accept either on amd, never on nvidia/cpu.
    if hardware == "amd" and owners in (["onnxruntime"], ["onnxruntime-rocm"], ["onnxruntime-migraphx"]):
        return
    if owners != expected:
        fail(f"Worker ORT namespace must be {expected}, found {owners}")


def preload_cuda13() -> None:
    dists = ("nvidia-cuda-runtime", "nvidia-cublas", "nvidia-cudnn-cu13",
             "nvidia-cuda-nvrtc", "nvidia-cufft", "nvidia-curand", "nvidia-nvjitlink")
    resolved: dict[str, Path] = {}
    for d in dists:
        try:
            dist = importlib.metadata.distribution(d)
        except importlib.metadata.PackageNotFoundError:
            continue
        for f in dist.files or ():
            p = Path(dist.locate_file(f)).resolve()
            if p.is_file() and ".so" in p.name:
                resolved.setdefault(p.name, p)
                # also index by SONAME prefix (libfoo.so.13.0.88 -> libfoo.so.13)
                for soname in CUDA_PRELOAD_ORDER:
                    if p.name == soname or p.name.startswith(soname + "."):
                        resolved.setdefault(soname, p)
    for soname in CUDA_PRELOAD_ORDER:
        match = resolved.get(soname)
        if not match:
            if soname in OPTIONAL_CUDNN:
                continue
            fail(f"Missing CUDA 13 object {soname}; run 'uv pip check' in .venv-worker")
        try:
            ctypes.CDLL(str(match), mode=ctypes.RTLD_GLOBAL | os.RTLD_NOW)
        except OSError:
            if soname not in OPTIONAL_CUDNN:
                fail(f"Cannot preload {soname} from {match}")
    try:
        ctypes.CDLL("libcuda.so.1", mode=ctypes.RTLD_GLOBAL | os.RTLD_NOW)
    except OSError:
        fail("libcuda.so.1 missing; install nvidia-utils >= 580")


class AsrEngine:
    def __init__(self, config: JsonObject, *, profiling: bool = False, profile_dir: Path | None = None) -> None:
        import numpy as _np
        import onnxruntime as _ort
        import onnx_asr
        self.np = _np
        self.ort = _ort
        hardware = str(config.get("hardware", "cpu"))
        opts = _ort.SessionOptions()
        opts.execution_mode = _ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = _ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # Variable-length audio (partial tail chunks, 0.25-15 s phrases):
        # pre-planned mem patterns never hit, so they only cost VRAM.
        opts.enable_mem_pattern = False
        if hardware == "cpu":
            # Throughput path: file transcription is embarrassingly parallel
            # across ORT intra-op threads; single-thread would 4-8x slowdown.
            opts.intra_op_num_threads = max(1, os.cpu_count() or 4)
            opts.inter_op_num_threads = 1
        else:
            # Low-VRAM path (2GB): one inference at a time minimizes arena peak.
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        if profiling:
            opts.enable_profiling = True
            # Direct ALL profiler dumps (one per InferenceSession inside
            # onnx_asr, including ones we never discover) into a temp dir.
            # Default prefix would scatter onnxruntime_profile__*.json into
            # the caller's cwd (source tree / APP_DIR) -- the exact stray
            # files reported. Temp + rmtree in self_test keeps this clean
            # and also respects the ReadOnlyPaths sandbox at runtime.
            if profile_dir is not None:
                opts.profile_file_prefix = str(profile_dir / "dusky_worker_profile")
        available = _ort.get_available_providers()
        if hardware == "nvidia":
            if "CUDAExecutionProvider" not in available:
                fail(f"CUDAExecutionProvider missing; available={available}")
            limit_mb = max(512, int(config.get("gpu_mem_limit_mb", 4096)))
            providers: list[Any] = [(("CUDAExecutionProvider"), {
                "device_id": 0, "arena_extend_strategy": "kSameAsRequested",
                "gpu_mem_limit": limit_mb * 1024 * 1024,
                "cudnn_conv_algo_search": "HEURISTIC",
                # 2GB-VRAM spike killers: clamp cudnn workspace (default max
                # can transiently cost GBs on first Run; useless for int8
                # Gemm/Attention anyway) and use one unified stream instead
                # of per-thread streams + graph pools (variable-T audio).
                "cudnn_conv_use_max_workspace": "0",
                "use_ep_level_unified_stream": "1",
                "enable_cuda_graph": "0",
                "use_tf32": True, "do_copy_in_default_stream": True}), "CPUExecutionProvider"]
        elif hardware == "amd":
            # Opportunistic: use MIGraphX/ROCM only if the installed wheel provides them.
            prefs = [ep for ep in ("MIGraphXExecutionProvider", "ROCMExecutionProvider") if ep in available]
            providers = prefs + ["CPUExecutionProvider"] if "CPUExecutionProvider" in available else prefs
            if not providers:
                fail(f"No usable EP; available={available}")
        else:
            if "CPUExecutionProvider" not in available:
                fail(f"CPUExecutionProvider missing; available={available}")
            providers = ["CPUExecutionProvider"]
        model_dir = Path(str(config["model_dir"])).expanduser()
        if not model_dir.is_dir():
            fail(f"model_dir missing: {model_dir}")
        q = config.get("quantization")
        if q in ("", "none", "fp32", "None"):
            q = None
        if q not in (None, "int8", "fp16"):
            fail("quantization must be null|int8|fp16")
        self.model = onnx_asr.load_model(str(config.get("model", "nemo-parakeet-tdt-0.6b-v2")),
                                         str(model_dir), quantization=q, sess_options=opts,
                                         providers=providers,
                                         preprocessor_config={"max_concurrent_workers": 1, "use_conv_preprocessors": True})
        self.sessions = self._discover()
        if not self.sessions:
            fail("No InferenceSession found in onnx-asr model")
        if hardware == "nvidia" and not any(s.get_providers()[:1] == ["CUDAExecutionProvider"] for s in self.sessions.values()):
            fail(f"All sessions fell back to CPU: {[s.get_providers() for s in self.sessions.values()]}")

    def _discover(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        seen: set[int] = set()
        def walk(node: Any, path: str, depth: int) -> None:
            if depth > 6 or id(node) in seen:
                return
            seen.add(id(node))
            if isinstance(node, self.ort.InferenceSession):
                found[path] = node
                return
            d = getattr(node, "__dict__", None)
            if not isinstance(d, dict):
                return
            for k, v in d.items():
                if not k.startswith("__"):
                    walk(v, path + "." + k, depth + 1)
        walk(self.model, "model", 0)
        return found

    def recognize(self, pcm_f32: Any) -> str:
        res = self.model.recognize(pcm_f32, sample_rate=SAMPLE_RATE)
        if isinstance(res, list):
            res = " ".join(str(x) for x in res)
        return str(res or "").strip()


def validate_memfd(fd: int, samples: int) -> None:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("Descriptor is not a regular file")
    # Same-uid peer is trusted for content but not for size: cap the mmap
    # before touching it (legit max is a 20 s file chunk = 320k samples).
    if samples <= 0 or samples > 480000:
        raise ValueError(f"Samples out of range: {samples}")
    expected = samples * BYTES_PER_SAMPLE
    if st.st_size != expected:
        raise ValueError(f"Size mismatch: {st.st_size} != {expected}")
    seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
    if (seals & REQUIRED_SEALS) != REQUIRED_SEALS:
        raise ValueError(f"Incomplete seals: {hex(seals)}")
    if not (seals & F_SEAL_EXEC):
        raise ValueError("Memfd missing F_SEAL_EXEC (needs MFD_NOEXEC_SEAL)")


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
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, REQUIRED_SEALS)
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


def run_worker(fd: int, config_path: Path) -> int:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if cfg.get("schema_version") != 2:
        fail("config schema_version must be 2")
    hardware = str(cfg.get("hardware", "cpu"))
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
    if hardware == "nvidia":
        preload_cuda13()
    assert_worker_namespace(hardware)
    engine = AsrEngine(cfg)
    send_response(sock, {"ok": True, "event": "ready", "hardware": hardware,
                         "providers": {k: list(v.get_providers()) for k, v in engine.sessions.items()}})
    timeout = max(5.0, float(cfg.get("idle_timeout_seconds", 90.0)))
    # Warm-resident service mode: the daemon sets DUSKY_WORKER_NO_IDLE_EXIT
    # so the model stays loaded for instant dictation (no D3cold in this
    # mode by design; use `dusky_trigger --unload` or disable the service
    # to free the GPU).
    no_idle_exit = os.environ.get("DUSKY_WORKER_NO_IDLE_EXIT") == "1"
    if no_idle_exit:
        sys.stderr.write("dusky-worker: warm mode, idle exit disabled\n")
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
                validate_memfd(audio_fd, samples)
                with mmap.mmap(audio_fd, samples * BYTES_PER_SAMPLE, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ) as m:
                    pcm_view = engine.np.frombuffer(m, dtype="<i2", count=samples)
                    f32 = pcm_view.astype(engine.np.float32) * (1.0 / 32768.0)
                    # Release the mmap export BEFORE the with-block closes it:
                    # mmap.close() raises BufferError if any exporter is alive,
                    # which deterministically failed every request.
                    del pcm_view
                t0 = time.monotonic()
                text = engine.recognize(f32)
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


def self_test(config_path: Path) -> int:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    hardware = str(cfg.get("hardware", "cpu"))
    if hardware == "nvidia":
        preload_cuda13()
    assert_worker_namespace(hardware)
    # All profiler dumps go here; removed in finally even on failure.
    prof_dir = Path(tempfile.mkdtemp(prefix="dusky-prof-"))
    try:
        engine = AsrEngine(cfg, profiling=(hardware == "nvidia"), profile_dir=prof_dir)
        wav = (0.3 * engine.np.sin(2 * engine.np.pi * 220.0 *
               engine.np.linspace(0.0, 3.0, 48000, dtype=engine.np.float32))).astype(engine.np.float32)
        t0 = time.monotonic()
        engine.recognize(wav)
        latency = round((time.monotonic() - t0) * 1000, 1)
        cuda_nodes = 0
        if hardware == "nvidia":
            for s in engine.sessions.values():
                try:
                    prof = s.end_profiling()
                except Exception:
                    prof = None
                if prof and Path(prof).exists():
                    try:
                        events = json.loads(Path(prof).read_text())
                        cuda_nodes += sum(1 for e in (events if isinstance(events, list) else [])
                                          if isinstance(e, dict) and (e.get("args") or {}).get("provider") == "CUDAExecutionProvider")
                    except (OSError, json.JSONDecodeError):
                        pass
                    try:
                        Path(prof).unlink()
                    except OSError:
                        pass
            report = {"ok": cuda_nodes > 0, "hardware": hardware, "cuda_nodes": cuda_nodes, "latency_ms": latency}
            print(json.dumps(report))
            return 0 if cuda_nodes > 0 else 3
        print(json.dumps({"ok": True, "hardware": hardware, "latency_ms": latency,
                          "providers": [s.get_providers() for s in engine.sessions.values()]}))
        return 0
    finally:
        shutil.rmtree(prof_dir, ignore_errors=True)
        # Belt-and-braces: undiscovered aux sessions (preprocessors) may
        # still use the default prefix and land in cwd. Sweep them so the
        # source tree / APP_DIR never accumulates strays again.
        for stale in Path.cwd().glob("onnxruntime_profile__*.json"):
            try:
                stale.unlink()
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--fd", type=int, default=-1)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test(args.config)
    if args.fd < 0:
        fail("--fd required outside --self-test")
    return run_worker(args.fd, args.config)


if __name__ == "__main__":
    sys.exit(main())
