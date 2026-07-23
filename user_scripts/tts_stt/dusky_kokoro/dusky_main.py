#!/usr/bin/env python3
import os
import time
import signal
import threading
import queue
import argparse
import select
import gc
import subprocess
import soundfile as sf
import re
import hashlib
import numpy as np
import traceback
import shutil
import sys
import uuid
import fcntl
import unicodedata
import base64
from pathlib import Path
import logging

# ==============================================================================
# KOKORO V1.0 VOICE ROSTER
# ==============================================================================
# 🇺🇸 American (en-us) : af_heart, af_alloy, af_aoede, af_bella, af_jessica, af_kore,
#                       af_nicole, af_nova, af_river, af_sarah, af_sky, am_adam,
#                       am_echo, am_eric, am_fenrir, am_liam, am_michael, am_onyx,
#                       am_puck, am_santa
# 🇬🇧 British (en-gb)  : bf_alice, bf_emma, bf_isabella, bf_lily, bm_daniel,
#                       bm_fable, bm_george, bm_lewis
# 🇯🇵 Japanese (ja)    : jf_alpha, jf_gongitsune, jf_nezumi, jf_tebukuro, jm_kumo
# 🇨🇳 Mandarin (zh)    : zf_xiaobei, zf_xiaoni, zf_xiaoxiao, zf_xiaoyi, zm_yunjian,
#                       zm_yunxi, zm_yunxia, zm_yunyang
# 🇪🇸 Spanish (es)     : ef_dora, em_alex, em_santa
# 🇫🇷 French (fr-fr)   : ff_siwis
# 🇮🇳 Hindi (hi)       : hf_alpha, hf_beta, hm_omega, hm_psi
# 🇮🇹 Italian (it)     : if_sara, im_nicola
# 🇧🇷 Portuguese(pt-br): pf_dora, pm_alex, pm_santa

# ==============================================================================
# VERSION & CONFIGURATION
# ==============================================================================
VERSION = "4.9 (Infinite Cache Fix + Precision Toggle)"

ZRAM_MOUNT = Path("/mnt/zram1")
AUDIO_OUTPUT_DIR = ZRAM_MOUNT / "kokoro_audio"
FIFO_PATH = Path("/tmp/dusky_kokoro.fifo")
PID_FILE = Path("/tmp/dusky_kokoro.pid")
READY_FILE = Path("/tmp/dusky_kokoro.ready")
LOCK_FILE = Path("/tmp/dusky_kokoro.lock")

# --- VOICE SETUP ---
# Handled safely by Dusky TUI. Do not use multi-line dicts here.
BLEND_VOICES = True
VOICE_1 = "af_heart"
VOICE_1_WEIGHT = 0.4
VOICE_2 = "af_bella"

if BLEND_VOICES:
    # Uses round() to prevent Python floating point errors (e.g. 0.79999999)
    VOICE_SETUP = {VOICE_1: VOICE_1_WEIGHT, VOICE_2: round(1.0 - VOICE_1_WEIGHT, 2)}
else:
    VOICE_SETUP = VOICE_1

# --- MODEL PRECISION ---
# Options: "f32" (Highest quality, ~310MB), "fp16" (Excellent quality, ~169MB), "int8" (Low quality/Fast, ~88MB)
MODEL_PRECISION = "fp16"

SPEED = 1.0
MPV_SPEED = 1.0  # MPV playback speed control
SAMPLE_RATE = 24000
STRIP_SPECIAL_CHARS = True  # Set to False to allow special characters like brackets and emojis

MAX_BATCH_LEN = 2000
IDLE_TIMEOUT = 10.0
DEDUP_WINDOW = 2.0
QUEUE_SIZE = 5

# ------------------------------------------------------------------------------
# Streaming safety knobs
# ------------------------------------------------------------------------------
# None = never kill mpv just because writes are temporarily blocked.
# Backpressure is normal when playback is slower than generation.
MPV_WRITE_STALL_TIMEOUT = None

# ==============================================================================
# LOGGING
# ==============================================================================
logger = logging.getLogger("dusky_daemon")
logger.setLevel(logging.INFO)

c_handler = logging.StreamHandler()
c_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(threadName)s - %(levelname)s - %(message)s"
))
logger.addHandler(c_handler)


def setup_debug_logging(filepath):
    f_handler = logging.FileHandler(filepath, mode="w")
    f_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(threadName)s - %(levelname)s - %(funcName)s - %(message)s"
    ))
    f_handler.setLevel(logging.DEBUG)
    logger.addHandler(f_handler)
    logger.setLevel(logging.DEBUG)
    logger.info(f"Debug logging enabled to: {filepath}")


def custom_excepthook(args):
    thread_name = args.thread.name if args.thread else "unknown (GC'd)"
    logger.critical(
        f"UNCAUGHT EXCEPTION in thread {thread_name}: {args.exc_value}"
    )
    traceback.print_tb(args.exc_traceback)


threading.excepthook = custom_excepthook

# ==============================================================================
# TEXT PROCESSING & UTILITIES
# ==============================================================================
RE_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
RE_URL = re.compile(r"https?://\S+", re.IGNORECASE)
RE_CLEAN = re.compile(r"[^a-zA-Z0-9\s.,!?;:'%\-]")
RE_SENTENCE_SPLIT = re.compile(
    r"(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bJr)(?<!\bSr)"
    r"(?<!\bProf)(?<!\bVol)(?<!\bNo)(?<!\bVs)(?<!\bEtc)"
    r"\s*([.?!;:]+)\s+"
)

ALLOWED_PUNCTUATION = frozenset({
    ".", ",", "!", "?", ";", ":", "'", "%", "-",
    "’", "“", "”", "–", "—", "…",
    "。", "，", "！", "？", "；", "：", "、", "％",
})


def clean_text(text):
    text = RE_MARKDOWN_LINK.sub(r"\1", text)
    text = RE_URL.sub("Link", text)
    
    # --- Structural Preservation ---
    # Convert double newlines (paragraphs) to sentence boundaries
    text = re.sub(r'\n\s*\n', '. ', text)
    # Convert Markdown list items (-, *, 1.) to sentence boundaries
    text = re.sub(r'\n\s*[-*]\s+', '. ', text)
    text = re.sub(r'\n\s*\d+\.\s+', '. ', text)
    # Convert remaining single newlines (hard wraps) to spaces
    text = text.replace('\n', ' ')

    if STRIP_SPECIAL_CHARS:
        text = "".join(
            ch if (
                ch.isspace()
                or ch in ALLOWED_PUNCTUATION
                or unicodedata.category(ch)[0] in {"L", "M", "N"}
            ) else " "
            for ch in text
        )
    return " ".join(text.split())


def smart_split(text):
    if not text:
        return []
    chunks = RE_SENTENCE_SPLIT.split(text)
    sentences = []
    
    if len(chunks) == 1:
        sentences = [text.strip()] if text.strip() else []
    else:
        for i in range(0, len(chunks) - 1, 2):
            sentence = chunks[i].strip()
            punctuation = chunks[i + 1].strip() if i + 1 < len(chunks) else ""
            if sentence:
                sentences.append(f"{sentence}{punctuation}")
        if len(chunks) % 2 != 0:
            trailing = chunks[-1].strip()
            if trailing:
                sentences.append(trailing)

    # --- Hard Fallback: Prevent ONNX 510-Token Index Crash ---
    MAX_CHARS = 400
    safe_sentences = []
    for sent in sentences:
        while len(sent) > MAX_CHARS:
            split_idx = sent.rfind(" ", 0, MAX_CHARS)
            if split_idx == -1:
                split_idx = MAX_CHARS
            safe_sentences.append(sent[:split_idx].strip())
            sent = sent[split_idx:].strip()
        if sent:
            safe_sentences.append(sent)
            
    return safe_sentences


def generate_filename_slug(text):
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", text)
    words = clean.split()
    if not words:
        return "audio"
    return "_".join(words[:5]).lower()


def get_next_index(directory):
    max_idx = 0
    if not directory.exists():
        return 1
    for f in directory.glob("*.wav"):
        try:
            parts = f.name.split("_")
            if parts and parts[0].isdigit():
                idx = int(parts[0])
                if idx > max_idx:
                    max_idx = idx
        except Exception:
            pass
    return max_idx + 1


def get_lang_from_prefix(voice_name):
    """Dynamically routes the phonemizer based on the voice's geographic prefix."""
    prefix = voice_name[0].lower()
    lang_map = {
        "a": "en-us",
        "b": "en-gb",
        "j": "ja",
        "z": "zh",
        "e": "es",
        "f": "fr-fr",
        "h": "hi",
        "i": "it",
        "p": "pt-br",
    }
    return lang_map.get(prefix, "en-us")


# ==============================================================================
# HARDWARE ENFORCER (UNIVERSAL - FIXED ROCM)
# ==============================================================================
import onnxruntime as rt

_available = rt.get_available_providers()
logger.info(f"ONNX Runtime initialized. Detected Providers: {_available}")


class PatchedInferenceSession(rt.InferenceSession):
    def __init__(self, path_or_bytes, sess_options=None, providers=None, **kwargs):
        if sess_options is None:
            sess_options = rt.SessionOptions()

        dynamic_providers = []
        available_set = set(rt.get_available_providers())
        is_gpu = False

        if "CUDAExecutionProvider" in available_set:
            logger.info("Configuring for NVIDIA CUDA...")
            is_gpu = True
            cuda_options = {
                "device_id": 0,
                "arena_extend_strategy": "kSameAsRequested",
                "gpu_mem_limit": 3 * 1024 * 1024 * 1024,  # 3GB VRAM limit
                "cudnn_conv_algo_search": "HEURISTIC",
                "do_copy_in_default_stream": True,
            }
            dynamic_providers.append(("CUDAExecutionProvider", cuda_options))

        elif "ROCmExecutionProvider" in available_set:
            logger.info("Configuring for AMD ROCm...")
            is_gpu = True
            rocm_options = {
                "device_id": 0,
                "arena_extend_strategy": "kSameAsRequested",
                "gpu_mem_limit": 3 * 1024 * 1024 * 1024,  # 3GB VRAM limit
                "do_copy_in_default_stream": True,
            }
            dynamic_providers.append(("ROCmExecutionProvider", rocm_options))

        dynamic_providers.append("CPUExecutionProvider")

        if is_gpu:
            sess_options.enable_mem_pattern = False
            sess_options.enable_cpu_mem_arena = False
        else:
            logger.info("CPU Mode: Enabling memory arena for performance.")
            sess_options.enable_mem_pattern = True
            sess_options.enable_cpu_mem_arena = True

        sess_options.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
        logger.info(f"Active Provider Stack: {dynamic_providers}")

        super().__init__(
            path_or_bytes, sess_options, providers=dynamic_providers, **kwargs
        )


rt.InferenceSession = PatchedInferenceSession
from kokoro_onnx import Kokoro

# ==============================================================================
# THREAD 1: MPV STREAMER (STREAM ID ARCHITECTURE)
# ==============================================================================
class AudioPlaybackThread(threading.Thread):
    def __init__(self, audio_queue, stop_event):
        super().__init__(name="MPV-Thread")
        self.audio_queue = audio_queue
        self.stop_event = stop_event
        self.active = True
        self.daemon = True
        self._mpv_process = None
        self._lock = threading.Lock()
        self._current_stream_id = None

        if not shutil.which("mpv"):
            logger.critical("MPV executable not found in PATH!")

    def _kill_process(self, proc):
        if proc is None:
            return
        try:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
            else:
                proc.wait()
        except Exception:
            pass

    def _spawn_mpv(self):
        cmd = [
            "mpv",
            "--no-terminal",
            "--force-window",
            "--title=Kokoro TTS",
            "--x11-name=kokoro",
            "--wayland-app-id=kokoro",
            "--geometry=400x100",
            "--keep-open=no",
            f"--speed={MPV_SPEED}",
            "--demuxer=rawaudio",
            f"--demuxer-rawaudio-rate={SAMPLE_RATE}",
            "--demuxer-rawaudio-channels=1",
            "--demuxer-rawaudio-format=float",
            "--cache=yes",
            "--cache-secs=36000",          # Allow up to 10 hours of cache in time
            "--demuxer-max-bytes=1024M",   # Allow up to ~3 hours of instant raw audio memory
            "-",
        ]

        mpv_env = os.environ.copy()
        mpv_env.pop("LD_LIBRARY_PATH", None)

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stderr=sys.stderr,
                stdout=subprocess.DEVNULL,
                env=mpv_env,
                start_new_session=False,
                close_fds=True,
                bufsize=0,
            )
            os.set_blocking(proc.stdin.fileno(), False)
            logger.info(f"MPV started (PID: {proc.pid})")
            return proc
        except Exception as e:
            if proc is not None:
                self._kill_process(proc)
            logger.error(f"Failed to start MPV: {e}")
            return None

    def _prepare_mpv_for_chunk(self, chunk_stream_id):
        with self._lock:
            proc = self._mpv_process
            is_alive = (proc is not None and proc.poll() is None)

            if chunk_stream_id == self._current_stream_id:
                if is_alive:
                    return proc
                logger.info("MPV closed mid-stream (User Kill). Halting.")
                self._mpv_process = None
                self.stop_event.set()
                return None

            if is_alive:
                logger.info("New stream ID. Restarting MPV.")
                self._kill_process(proc)

            logger.info(f"Starting new stream ({chunk_stream_id[:8]}...). Spawning MPV.")
            new_proc = self._spawn_mpv()
            self._mpv_process = new_proc
            self._current_stream_id = chunk_stream_id
            return new_proc

    def _finish_stream(self):
        with self._lock:
            self._current_stream_id = None
            proc = self._mpv_process
            self._mpv_process = None
        if proc:
            logger.info(f"Closing MPV stdin (PID: {proc.pid}). Playback finishing.")
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            threading.Thread(
                target=self._reap_process,
                args=(proc,),
                name="MPV-Reaper",
                daemon=True,
            ).start()

    def _reap_process(self, proc):
        try:
            proc.wait(timeout=600)
            logger.debug(f"MPV (PID: {proc.pid}) exited after playback.")
        except subprocess.TimeoutExpired:
            self._kill_process(proc)
        except Exception:
            pass

    def _timed_write(self, proc, data, idle_timeout=None):
        """
        Write raw audio to mpv stdin.
        """
        try:
            fd = proc.stdin.fileno()
        except Exception:
            return False

        view = memoryview(data)
        last_success = time.monotonic()

        while view:
            if self.stop_event.is_set():
                return False

            if proc.poll() is not None:
                return False

            if idle_timeout is not None and (time.monotonic() - last_success > idle_timeout):
                logger.error(f"MPV pipe stalled for {idle_timeout}s. Killing.")
                self._kill_process(proc)
                return False

            try:
                _, wlist, _ = select.select([], [fd], [], 0.2)
            except (ValueError, OSError):
                return False

            if not wlist:
                continue

            try:
                written = os.write(fd, view)
                if written > 0:
                    view = view[written:]
                    last_success = time.monotonic()
            except BlockingIOError:
                continue
            except (BrokenPipeError, OSError):
                return False

        return True

    def run(self):
        try:
            while self.active:
                try:
                    item = self.audio_queue.get(timeout=0.2)
                except queue.Empty:
                    with self._lock:
                        if self._mpv_process and self._mpv_process.poll() is not None:
                            logger.debug("Cleaning up dead MPV handle (Idle).")
                            self._mpv_process = None
                    continue

                if item is None:
                    self._finish_stream()
                    self.audio_queue.task_done()
                    continue

                if self.stop_event.is_set():
                    self.audio_queue.task_done()
                    continue

                samples, _, stream_id = item
                if samples.dtype != np.float32:
                    samples = samples.astype(np.float32)
                raw_bytes = samples.tobytes()

                try:
                    proc = self._prepare_mpv_for_chunk(stream_id)
                    if not proc:
                        self.audio_queue.task_done()
                        self._drain_queue()
                        continue
                    if not self._timed_write(proc, raw_bytes, idle_timeout=MPV_WRITE_STALL_TIMEOUT):
                        raise BrokenPipeError("Write failed")
                except (BrokenPipeError, OSError):
                    logger.warning("MPV Connection Broken. Stopping.")
                    with self._lock:
                        dead_proc = self._mpv_process
                        self._mpv_process = None
                        self._current_stream_id = None
                    self._kill_process(dead_proc)
                    self.stop_event.set()
                    self.audio_queue.task_done()
                    self._drain_queue()
                    continue
                except Exception as e:
                    logger.error(f"Playback Error: {e}")

                self.audio_queue.task_done()
        finally:
            self.cleanup()

    def _drain_queue(self):
        while True:
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except queue.Empty:
                break

    def cleanup(self):
        self.active = False
        with self._lock:
            proc = self._mpv_process
            self._mpv_process = None
            self._current_stream_id = None
        self._kill_process(proc)


# ==============================================================================
# THREAD 2: FIFO READER
# ==============================================================================
class FifoReader(threading.Thread):
    def __init__(self, text_queue, fifo_path):
        super().__init__(name="FIFO-Thread")
        self.text_queue = text_queue
        self.fifo_path = fifo_path
        self.active = True
        self.daemon = True
        self.last_hash = None
        self.last_time = 0.0

    def _submit_text(self, text):
        text = text.strip()
        if not text:
            return

        if text.startswith("B64:"):
            try:
                text = base64.b64decode(text[4:]).decode("utf-8", errors="ignore").strip()
            except Exception as e:
                logger.error(f"Base64 decode failed: {e}")
                return

        h = hashlib.md5(text.encode("utf-8")).hexdigest()
        now = time.monotonic()
        if self.last_hash == h and (now - self.last_time) < DEDUP_WINDOW:
            logger.info("Skipping duplicate.")
            return

        self.last_hash = h
        self.last_time = now

        while self.active:
            try:
                self.text_queue.put(text, timeout=0.5)
                return
            except queue.Full:
                continue

    def _flush_lines(self, buffer, *, final=False):
        while True:
            newline_index = buffer.find(b"\n")
            if newline_index < 0:
                break
            raw = bytes(buffer[:newline_index])
            del buffer[:newline_index + 1]
            self._submit_text(raw.decode("utf-8", errors="ignore"))

        if final and buffer:
            raw = bytes(buffer)
            buffer.clear()
            self._submit_text(raw.decode("utf-8", errors="ignore"))

    def run(self):
        if not self.fifo_path.exists():
            os.mkfifo(self.fifo_path)

        while self.active:
            try:
                fd = os.open(self.fifo_path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                time.sleep(0.5)
                continue

            buffer = bytearray()
            poll = select.poll()
            poll.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)

            try:
                while self.active:
                    events = poll.poll(500)
                    if not events:
                        continue

                    reopen = False

                    for _, event in events:
                        if event & select.POLLERR:
                            reopen = True
                            break

                        if event & (select.POLLIN | select.POLLHUP):
                            while True:
                                try:
                                    chunk = os.read(fd, 65536)
                                except BlockingIOError:
                                    break

                                if not chunk:
                                    reopen = True
                                    break

                                buffer.extend(chunk)
                                self._flush_lines(buffer)

                            if reopen:
                                break

                    if reopen:
                        self._flush_lines(buffer, final=True)
                        break
            except OSError:
                time.sleep(0.5)
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

            if self.active:
                time.sleep(0.1)


# ==============================================================================
# DAEMON CORE
# ==============================================================================
class DuskyDaemon:
    def __init__(self, debug_file=None):
        self.running = True
        if debug_file:
            setup_debug_logging(debug_file)
        logger.info(f"Dusky Daemon {VERSION} Initializing...")
        self.audio_queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.text_queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.stop_event = threading.Event()
        self.playback = AudioPlaybackThread(self.audio_queue, self.stop_event)
        self.fifo_reader = FifoReader(self.text_queue, FIFO_PATH)
        env_dir = Path(__file__).parent
        self.kokoro = None

        # --- Dynamic Model Precision Routing ---
        precision = MODEL_PRECISION.lower()
        if precision == "fp16":
            model_filename = "kokoro-v1.0.fp16.onnx"
        elif precision == "int8":
            model_filename = "kokoro-v1.0.int8.onnx"
        else:  # Defaults to standard f32
            model_filename = "kokoro-v1.0.onnx"

        self.model_path = str(env_dir / f"models/{model_filename}")
        self.voices_path = str(env_dir / "models/voices-v1.0.bin")
        self.last_used = 0
        self._lock_fd = None
        self._voice_input = None
        self._lang_code = "en-us"
        self._voice_resolved = False

    def get_model(self):
        self.last_used = time.time()
        if self.kokoro is None:
            logger.info("Loading Kokoro...")
            self.kokoro = Kokoro(self.model_path, self.voices_path)
        return self.kokoro

    def check_idle(self):
        if self.kokoro and (time.time() - self.last_used > IDLE_TIMEOUT):
            logger.info("Idle timeout. Cleaning VRAM.")
            del self.kokoro
            self.kokoro = None
            gc.collect()

    def _should_stop(self):
        return not self.running or self.stop_event.is_set()

    def _acquire_instance_lock(self):
        self._lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise RuntimeError("Another Dusky daemon instance is already running.")

        os.ftruncate(self._lock_fd, 0)
        os.write(self._lock_fd, f"{os.getpid()}\n".encode())
        os.fsync(self._lock_fd)

    def _release_instance_lock(self):
        if self._lock_fd is None:
            return

        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass

        try:
            os.close(self._lock_fd)
        except OSError:
            pass

        self._lock_fd = None

    def _ensure_output_dir(self):
        try:
            AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            logger.warning(f"Cannot create audio output dir: {e}")
            return False

    def _resolve_voice_input(self):
        if self._voice_resolved:
            return self._voice_input, self._lang_code

        if isinstance(VOICE_SETUP, str):
            self._voice_input = VOICE_SETUP
            self._lang_code = get_lang_from_prefix(VOICE_SETUP)

        elif isinstance(VOICE_SETUP, dict):
            first_voice = next(iter(VOICE_SETUP))
            self._lang_code = get_lang_from_prefix(first_voice)

            try:
                voice_vectors = np.load(self.voices_path, allow_pickle=False)
                try:
                    blended = None
                    for v_name, weight in VOICE_SETUP.items():
                        vector = voice_vectors[v_name] * weight
                        blended = vector if blended is None else blended + vector
                    self._voice_input = blended
                finally:
                    close = getattr(voice_vectors, "close", None)
                    if close is not None:
                        close()
            except Exception as e:
                logger.error(
                    f"Failed to blend voices mathematically: {e}. Falling back to single voice."
                )
                self._voice_input = first_voice

        else:
            raise TypeError("VOICE_SETUP must be a string or a dict of weights.")

        self._voice_resolved = True
        return self._voice_input, self._lang_code

    def _setup_fifo(self):
        if FIFO_PATH.exists():
            if not FIFO_PATH.is_fifo():
                logger.warning(f"Non-FIFO file at {FIFO_PATH}, removing.")
                FIFO_PATH.unlink()
        if not FIFO_PATH.exists():
            os.mkfifo(FIFO_PATH)
        logger.debug("FIFO created.")

    def generate(self, text):
        stream_opened = False
        wav_file = None
        wav_path = None
        file_written = False
        file_sr = None

        try:
            model = self.get_model()
            slug = generate_filename_slug(text)
            sentences = smart_split(text)
            if not sentences:
                return

            logger.info(f"Generating: '{slug}' ({len(sentences)} sentences)")
            current_stream_id = str(uuid.uuid4())
            voice_input, lang_code = self._resolve_voice_input()

            if self._ensure_output_dir():
                idx = get_next_index(AUDIO_OUTPUT_DIR)
                wav_path = AUDIO_OUTPUT_DIR / f"{idx}_{slug}.wav"

            for i, sentence in enumerate(sentences):
                if self._should_stop():
                    break

                self.last_used = time.time()
                logger.debug(f"  Sentence {i + 1}/{len(sentences)}: {sentence[:60]}...")
                audio, sr = model.create(sentence, voice=voice_input, speed=SPEED, lang=lang_code)
                self.last_used = time.time()

                if audio is None:
                    continue

                audio = np.asarray(audio, dtype=np.float32)

                if wav_path is not None:
                    try:
                        if wav_file is None:
                            file_sr = sr
                            wav_file = sf.SoundFile(
                                str(wav_path),
                                mode="w",
                                samplerate=sr,
                                channels=1,
                                subtype="FLOAT",
                                format="WAV",
                            )
                        elif sr != file_sr:
                            raise RuntimeError(
                                f"Inconsistent sample rate across sentences: {file_sr} -> {sr}"
                            )

                        wav_file.write(audio)
                        file_written = True
                    except Exception as e:
                        logger.error(f"Failed to save WAV: {e}")
                        if wav_file is not None:
                            try:
                                wav_file.close()
                            except Exception:
                                pass
                        if wav_path is not None:
                            try:
                                wav_path.unlink(missing_ok=True)
                            except OSError:
                                pass
                        wav_file = None
                        wav_path = None

                while not self._should_stop():
                    try:
                        self.audio_queue.put((audio, sr, current_stream_id), timeout=0.2)
                        stream_opened = True
                        break
                    except queue.Full:
                        continue

            self.last_used = time.time()

        except Exception as e:
            logger.error(f"Generation Error: {e}")
            self.kokoro = None

        finally:
            if wav_file is not None:
                try:
                    wav_file.close()
                except Exception as e:
                    logger.error(f"Failed to close WAV: {e}")
                else:
                    if file_written and wav_path is not None:
                        logger.info(f"Saved: {wav_path.name}")

            if stream_opened:
                sentinel_sent = False
                while not self._should_stop():
                    if not self.playback.is_alive():
                        logger.warning("Playback thread is not running; could not finalize stream cleanly.")
                        break
                    try:
                        self.audio_queue.put(None, timeout=0.2)
                        sentinel_sent = True
                        break
                    except queue.Full:
                        continue

                if not sentinel_sent and not self._should_stop():
                    logger.warning("Could not send end-of-stream sentinel.")

    def start(self):
        signal.signal(signal.SIGTERM, lambda s, f: self.stop())
        signal.signal(signal.SIGINT, lambda s, f: self.stop())

        try:
            self._acquire_instance_lock()
            PID_FILE.write_text(str(os.getpid()))
            self._setup_fifo()
            self.playback.start()
            self.fifo_reader.start()
            READY_FILE.touch()
            logger.info(f"Daemon Ready (PID: {os.getpid()})")

            while self.running:
                try:
                    text = self.text_queue.get(timeout=0.5)
                    self.stop_event.clear()
                    clean = clean_text(text)
                    if clean:
                        self.generate(clean)

                    if self.stop_event.is_set():
                        logger.info("User interrupted playback. Flushing...")
                        drained = 0

                        while not self.text_queue.empty():
                            try:
                                self.text_queue.get_nowait()
                                self.text_queue.task_done()
                                drained += 1
                            except queue.Empty:
                                break

                        time.sleep(1.0)

                        while not self.text_queue.empty():
                            try:
                                self.text_queue.get_nowait()
                                self.text_queue.task_done()
                                drained += 1
                            except queue.Empty:
                                break

                        if drained > 0:
                            logger.info(f"Flushed {drained} items.")

                    self.text_queue.task_done()

                except queue.Empty:
                    self.check_idle()

        except RuntimeError as e:
            logger.critical(str(e))
        finally:
            self.cleanup()

    def stop(self):
        self.running = False
        self.stop_event.set()

    def cleanup(self):
        logger.info("Shutting down...")
        self.running = False
        self.stop_event.set()
        self.fifo_reader.active = False
        self.playback.cleanup()

        for p in (FIFO_PATH, PID_FILE, READY_FILE):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

        self._release_instance_lock()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--debug-file", help="Path to write debug log")
    args = parser.parse_args()

    log_level = os.environ.get("DUSKY_LOG_LEVEL", args.log_level).upper()
    if hasattr(logging, log_level):
        logger.setLevel(getattr(logging, log_level))

    debug_path = args.debug_file or os.environ.get("DUSKY_LOG_FILE")

    if args.daemon:
        DuskyDaemon(debug_path).start()
    else:
        print("Run with --daemon")
