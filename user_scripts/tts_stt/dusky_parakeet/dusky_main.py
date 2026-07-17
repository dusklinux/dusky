#!/usr/bin/env python3
"""
Dusky Main v8.1 BLEEDING - D3-cold safe, torch-free, secure FIFO, fixed realtime
- Never imports torch/onnxruntime at top level (D3-cold safe)
- ONNX-only Silero VAD with RMS fallback, robust download URLs
- Secure XDG_RUNTIME_DIR 0700, FIFO 0600, O_RDWR|O_NONBLOCK, TOCTOU protected
- Worker spawn context, daemon=False, proper D3-cold cleanup
- Realtime typing: Word-level LCP suffix window, hallucination blocklist, VAD gate
- ffmpeg soxr high quality resampling
"""
import os
import sys
import sysconfig
import time
import signal
import threading
import subprocess
import shutil
import json
import logging
import tempfile
from pathlib import Path
import stat
import select

if sys.version_info < (3, 14, 6):
    print(f"Need 3.14.6+", file=sys.stderr)
    sys.exit(1)
if sysconfig.get_config_var("Py_GIL_DISABLED") == 1:
    print("Need GIL build", file=sys.stderr)
    sys.exit(1)
try:
    if not sys._is_gil_enabled():
        print("Need GIL enabled", file=sys.stderr)
        sys.exit(1)
except AttributeError:
    pass

# D3-cold safety: never init CUDA in main daemon
os.environ.setdefault("PYTORCH_NVML_BASED_CUDA_CHECK", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

logger = logging.getLogger("dusky_main")
logger.setLevel(logging.INFO)
try:
    from rich.logging import RichHandler
    logger.handlers.clear()
    logger.addHandler(RichHandler(rich_tracebacks=False, show_time=False))
except Exception:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(ch)

try:
    import numpy as np
    import soundfile as sf
    import sounddevice as sd
    HAS_SD = True
except ImportError as e:
    HAS_SD = False
    import numpy as np
    import soundfile as sf
    logger.warning(f"sounddevice missing: {e}")

# Force spawn early - required for CUDA workers
import multiprocessing
try:
    multiprocessing.set_start_method("spawn", force=True)
except RuntimeError:
    pass

def get_runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        p_base = Path(base)
        try:
            st = p_base.stat()
            if st.st_uid != os.getuid():
                raise ValueError("bad owner")
            if stat.S_IMODE(st.st_mode) != 0o700:
                try:
                    p_base.chmod(0o700)
                except Exception:
                    pass
            runtime = p_base / "dusky_stt"
            runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                runtime.chmod(0o700)
            except Exception:
                pass
            return runtime
        except Exception as e:
            logger.warning(f"XDG_RUNTIME_DIR invalid {e}, using secure tmp")
    secure_dir = Path(tempfile.mkdtemp(prefix=f"dusky_stt-{os.getuid()}-", dir="/tmp"))
    try:
        secure_dir.chmod(0o700)
    except Exception:
        pass
    logger.warning(f"Using fallback runtime dir {secure_dir}")
    return secure_dir

RUNTIME_DIR = get_runtime_dir()
FIFO_PATH = RUNTIME_DIR / "fifo"
PID_FILE = RUNTIME_DIR / "pid"
READY_FILE = RUNTIME_DIR / "ready"
RECORD_PID_FILE = RUNTIME_DIR / "recording"
TRANSCRIPT_DIR = Path.home() / "Transcripts" / "DuskySTT"
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = Path.home() / "contained_apps" / "uv" / "dusky_stt_v2" / "install_config.json"
MODEL_CACHE_DIR = Path.home() / "contained_apps" / "uv" / "dusky_stt_v2" / "models"
MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

BLOCKLIST = {
    "thank you", "thanks for watching", "thank you for watching",
    "subtitle by", "amara.org", "please subscribe", ""
}

def is_hallucination(text: str) -> bool:
    t = text.lower().strip()
    if len(t) < 2:
        return True
    for b in BLOCKLIST:
        if b and b in t:
            return True
    return False

def rms_energy(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))

def notify(t, m, critical=False):
    if not shutil.which("notify-send"):
        return
    cmd = ["notify-send", "-a", "Dusky STT", "-t", "4000"]
    if critical:
        cmd += ["-u", "critical"]
    cmd += [t, m[:400]]
    try:
        subprocess.run(cmd, check=False, timeout=2)
    except Exception:
        pass

def type_into_focused(text: str):
    if not text:
        return
    if shutil.which("wtype"):
        try:
            result = subprocess.run(["wtype", text], check=False, timeout=5, capture_output=True, text=True)
            if result.returncode == 0:
                return
            logger.warning(f"wtype failed {result.returncode}: {result.stderr}")
        except Exception as e:
            logger.warning(f"wtype exception {e}")
    if shutil.which("ydotool"):
        try:
            subprocess.run(["ydotool", "type", text], check=False, timeout=5)
            return
        except Exception:
            pass
    if shutil.which("wl-copy"):
        try:
            subprocess.run(["wl-copy"], input=text.encode(), check=True, timeout=2)
        except Exception:
            pass

def secure_write_pid(path: Path, content: str):
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Failed secure pid write to {path}: {e}")

class VADProcessor:
    def __init__(self, sr=16000):
        self.sr = sr
        self.model_path = MODEL_CACHE_DIR / "silero_vad.onnx"
        self.session = None
        self._load_onnx_model()

    def _load_onnx_model(self):
        if not self.model_path.exists():
            try:
                import importlib.util
                spec = importlib.util.find_spec("silero_vad")
                if spec and spec.submodule_search_locations:
                    for loc in spec.submodule_search_locations:
                        cand = Path(loc) / "files" / "silero_vad.onnx"
                        if cand.exists():
                            shutil.copy(cand, self.model_path)
                            logger.info(f"Copied silero VAD from {cand}")
                            break
            except Exception:
                pass

        if not self.model_path.exists():
            urls = [
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
                "https://huggingface.co/onnx-community/silero-vad/resolve/main/onnx/model.onnx",
                "https://raw.githubusercontent.com/snakers4/silero-vad/master/files/silero_vad.onnx",
            ]
            for url in urls:
                try:
                    import urllib.request
                    logger.info(f"Trying VAD download {url}")
                    urllib.request.urlretrieve(url, str(self.model_path))
                    logger.info(f"Downloaded VAD to {self.model_path}")
                    break
                except Exception as e:
                    logger.warning(f"VAD download failed {url}: {e}")
                    continue
            else:
                self.model_path = None
                return

        if self.model_path and self.model_path.exists():
            try:
                import onnxruntime as rt
                self.session = rt.InferenceSession(str(self.model_path), providers=['CPUExecutionProvider'])
                logger.info(f"Loaded ONNX VAD {self.model_path}")
            except Exception as e:
                logger.warning(f"Failed to load ONNX VAD {e}")
                self.session = None

    def is_speech(self, audio: np.ndarray) -> bool:
        if audio.size == 0:
            return False
        if self.session:
            try:
                chunk = audio[-512:].astype(np.float32) if len(audio) >= 512 else np.pad(audio, (512-len(audio),0)).astype(np.float32)
                state = np.zeros((2,1,128), dtype=np.float32)
                sr = np.array([16000], dtype=np.int64)
                inp = chunk.reshape(1, -1)
                try:
                    out = self.session.run(None, {"input": inp, "state": state, "sr": sr})
                    prob = float(out[0][0][0]) if len(out[0].shape) > 1 else float(out[0][0])
                    return prob > 0.45
                except Exception:
                    out = self.session.run(None, {"input": inp})
                    prob = float(out[0][0]) if hasattr(out[0], '__len__') else float(out[0])
                    return prob > 0.45
            except Exception:
                pass
        return rms_energy(audio) > 0.015

    def get_segments(self, audio: np.ndarray, max_sec=25):
        sr = self.sr
        max_samples = max_sec * sr
        if self.session is None:
            overlap = int(0.5 * sr)
            step = max_samples - overlap
            segs = []
            for start in range(0, len(audio), step):
                end = min(start + max_samples, len(audio))
                segs.append((start, end))
                if end == len(audio):
                    break
            return segs

        try:
            window = 512
            speech_probs = []
            for i in range(0, len(audio), window):
                chunk = audio[i:i+window]
                if len(chunk) < window:
                    chunk = np.pad(chunk, (0, window - len(chunk)))
                speech_probs.append(1 if self.is_speech(chunk) else 0)

            segments = []
            in_speech = False
            start_idx = 0
            min_speech_samples = int(0.25 * sr)
            min_silence_samples = int(0.8 * sr)
            silence_counter = 0
            speech_counter = 0

            for idx, prob in enumerate(speech_probs):
                sample_pos = idx * window
                if prob == 1:
                    if not in_speech:
                        start_idx = sample_pos
                        in_speech = True
                        speech_counter = window
                    else:
                        speech_counter += window
                    silence_counter = 0
                else:
                    if in_speech:
                        silence_counter += window
                        if silence_counter >= min_silence_samples:
                            end_idx = sample_pos - silence_counter
                            if speech_counter >= min_speech_samples:
                                pad = int(0.2 * sr)
                                s = max(0, start_idx - pad)
                                e = min(len(audio), end_idx + pad)
                                if e - s > max_samples:
                                    for sub in range(s, e, max_samples - int(0.5*sr)):
                                        sub_e = min(sub + max_samples, e)
                                        segments.append((sub, sub_e))
                                        if sub_e == e:
                                            break
                                else:
                                    segments.append((s, e))
                            in_speech = False
                            speech_counter = 0
                            silence_counter = 0

            if in_speech and speech_counter >= min_speech_samples:
                pad = int(0.2 * sr)
                s = max(0, start_idx - pad)
                e = len(audio)
                if e - s > max_samples:
                    for sub in range(s, e, max_samples - int(0.5*sr)):
                        sub_e = min(sub + max_samples, e)
                        segments.append((sub, sub_e))
                        if sub_e == e:
                            break
                else:
                    segments.append((s, e))

            return segments if segments else [(0, len(audio))]
        except Exception as e:
            logger.warning(f"VAD failed {e}, using simple chunking")
            overlap = int(0.5 * sr)
            step = max_samples - overlap
            segs = []
            for start in range(0, len(audio), step):
                end = min(start + max_samples, len(audio))
                segs.append((start, end))
                if end == len(audio):
                    break
            return segs

class WorkerManager:
    def __init__(self, config: dict):
        self.config = config
        self.task_q = None
        self.result_q = None
        self.proc = None
        self.lock = threading.Lock()

    def ensure(self):
        with self.lock:
            if self.proc and self.proc.is_alive():
                return
            if self.proc:
                try:
                    self.proc.terminate()
                    self.proc.join(timeout=2)
                except Exception:
                    pass
            ctx = multiprocessing.get_context("spawn")
            self.task_q = ctx.Queue()
            self.result_q = ctx.Queue()
            from dusky_worker import worker_main
            # daemon=False is absolutely required for clean CUDA env tracking
            self.proc = ctx.Process(target=worker_main, args=(self.task_q, self.result_q, self.config), daemon=False)
            self.proc.start()
            logger.info(f"Worker started PID {self.proc.pid}")

    def submit(self, audio: np.ndarray, index: int, start_sec: float):
        self.ensure()
        try:
            self.task_q.put({"type": "audio", "audio": audio, "index": index, "start_sec": start_sec}, timeout=2)
        except Exception as e:
            logger.error(f"Submit failed {e}")

    def get_all(self):
        res = []
        try:
            while self.result_q and not self.result_q.empty():
                res.append(self.result_q.get_nowait())
        except Exception:
            pass
        return res

    def stop(self):
        with self.lock:
            if self.task_q:
                try:
                    self.task_q.put({"type": "stop"}, timeout=1)
                except Exception:
                    pass
            if self.proc:
                try:
                    self.proc.join(timeout=3)
                    if self.proc.is_alive():
                        self.proc.terminate()
                except Exception:
                    pass
            self.proc = None

def get_new_suffix(last_model_text: str, new_text: str) -> str:
    """Find the portion of new_text that hasn't been typed yet by comparing
    it to the last model text for the current active buffer.
    """
    def clean(w: str) -> str:
        return w.lower().strip(".,?!:;\"'()-")
    
    last_words = last_model_text.strip().split()
    new_words = new_text.strip().split()
    
    if not last_words:
        return new_text.strip()
    if not new_words:
        return ""
        
    last_clean = [clean(w) for w in last_words]
    new_clean = [clean(w) for w in new_words]
    
    best_new_end = 0
    best_score = (0, 0)  # (closeness_to_end_of_last, match_length)
    
    for j in range(len(new_clean)):
        for i in range(len(last_clean)):
            if last_clean[i] != new_clean[j]:
                continue
            k = 0
            while (i + k < len(last_clean) and 
                   j + k < len(new_clean) and 
                   last_clean[i + k] == new_clean[j + k]):
                k += 1
            if k >= 1:
                closeness = i + k
                score = (closeness, k)
                if score > best_score:
                    best_score = score
                    best_new_end = j + k
                    
    # If the match is close to the end of last_clean, or is a solid match (length >= 2)
    if best_score[0] >= len(last_clean) - 2 or best_score[1] >= 2:
        return " ".join(new_words[best_new_end:])
        
    # If no overlap is found, they are disjoint, so type the full text
    return new_text.strip()

class DuskyDaemon:
    def __init__(self, config: dict):
        self.config = config
        self.running = True
        self.recording = False
        self.realtime = config.get("realtime", True)
        self.chunk_seconds = config.get("chunk_seconds", 25)
        self.realtime_chunk = config.get("realtime_chunk", 2.0)
        self.vad = VADProcessor(sr=16000)
        self.worker = WorkerManager(config)
        self.audio_q = None
        self.acc_chunks = []
        self.typed_text = ""
        self.lock = threading.Lock()
        self.record_thread = None
        self.transcribe_thread = None
        self.stop_event = threading.Event()

    def start_recording(self, realtime=False):
        with self.lock:
            if self.recording:
                return
            self.recording = True
            self.realtime = realtime
            self.acc_chunks = []
            self.typed_text = ""
            self.stop_event.clear()
            self.submitted_count = 0
            self.processed_count = 0
            self.last_model_text = ""
            self.last_chunk_idx = -1
            import queue
            self.audio_q = queue.Queue()

        self.record_thread = threading.Thread(target=self._record_loop, daemon=True)
        self.transcribe_thread = threading.Thread(target=self._transcribe_loop, daemon=True)
        self.record_thread.start()
        self.transcribe_thread.start()
        
        mode = "REALTIME typing" if realtime else "push-to-talk"
        logger.info(f"Recording started {mode}")
        notify("Listening...", f"{mode} - speak now" if realtime else "Speak now")

    def _record_loop(self):
        sr = 16000
        try:
            # Request stereo and mix to mono dynamically to resolve PipeWire mapping anomalies
            with sd.InputStream(samplerate=sr, channels=2, blocksize=2048, dtype='float32') as stream:
                while self.recording and self.running and not self.stop_event.is_set():
                    data, overflow = stream.read(2048)
                    if overflow:
                        logger.warning("Audio input overflow detected")
                    mono = data.mean(axis=1).astype(np.float32)
                    self.audio_q.put(mono)
        except Exception as e:
            logger.error(f"Audio stream error: {e}")
            self.recording = False

    def _transcribe_loop(self):
        sr = 16000
        rolling_buffer = np.array([], dtype=np.float32)
        chunk_idx = 0
        silence_samples = 0
        max_silence = int(1.5 * sr) 
        
        last_process_time = time.time()
        chunk_sec = self.realtime_chunk if self.realtime else self.chunk_seconds

        while self.running and (self.recording or not self.audio_q.empty() or (self.processed_count < self.submitted_count and self.worker.proc and self.worker.proc.is_alive())):
            try:
                import queue
                try:
                    data = self.audio_q.get(timeout=0.1)
                except queue.Empty:
                    data = None

                if data is not None:
                    rolling_buffer = np.concatenate([rolling_buffer, data]) if rolling_buffer.size else data
                    
                    if self.realtime:
                        if not self.vad.is_speech(data):
                            silence_samples += len(data)
                        else:
                            silence_samples = 0

                # Check if it's time to process the window
                current_time = time.time()
                should_process = False
                if self.realtime:
                    should_process = (current_time - last_process_time >= chunk_sec or not self.recording)
                else:
                    should_process = not self.recording

                if rolling_buffer.size > 0 and should_process:
                    last_process_time = current_time
                    
                    # Realtime uses rolling window; Offline shifts forward
                    if self.realtime:
                        # Truncate rolling buffer to prevent VRAM ballooning and OOM
                        max_buf_samples = int(15 * sr)
                        if rolling_buffer.size > max_buf_samples:
                            rolling_buffer = rolling_buffer[-max_buf_samples:]
                            
                        # VAD Gate check: only evaluate if speech was observed in this phrase window
                        if silence_samples < len(rolling_buffer):
                            self.worker.submit(rolling_buffer.copy(), chunk_idx, chunk_idx * chunk_sec)
                            self.submitted_count += 1
                        
                        # Flush rolling window on explicit long silence boundaries to avoid phrase limits
                        if silence_samples >= max_silence and self.recording:
                            rolling_buffer = np.array([], dtype=np.float32)
                            self.last_model_text = ""
                            # Clear audio queue to discard any backlog from before the pause
                            try:
                                while not self.audio_q.empty():
                                    self.audio_q.get_nowait()
                            except Exception:
                                pass
                            chunk_idx += 1
                            silence_samples = 0
                    else:
                        # Offline mode: transcribe the entire accumulated buffer as a single block at the end
                        self.worker.submit(rolling_buffer.copy(), chunk_idx, 0.0)
                        self.submitted_count += 1
                        rolling_buffer = np.array([], dtype=np.float32)
                        chunk_idx += 1

                # Evaluate internal queue responses from our worker context
                all_results = self.worker.get_all()
                for res in all_results:
                    self.processed_count += 1
                
                valid_results = [r for r in all_results if r.get("text", "").strip() and not is_hallucination(r.get("text", "").strip())]
                if self.realtime:
                    # Discard any stale results from older chunk indices
                    valid_results = [r for r in valid_results if r.get("index", 0) >= getattr(self, "last_chunk_idx", -1)]
                
                if valid_results:
                    if self.realtime:
                        # Group valid results by index and keep the latest for each index
                        latest_by_idx = {}
                        for res in valid_results:
                            idx = res.get("index", 0)
                            latest_by_idx[idx] = res
                            
                        # Process them in sorted order of index
                        for idx in sorted(latest_by_idx.keys()):
                            res = latest_by_idx[idx]
                            text = res.get("text", "").strip()
                            
                            # Reset last_model_text when boundary index changes (pause flush)
                            if idx != getattr(self, "last_chunk_idx", -1):
                                self.last_model_text = ""
                                self.last_chunk_idx = idx
                                
                            suffix = get_new_suffix(self.last_model_text, text)
                            if suffix:
                                if self.typed_text and not self.typed_text.endswith(" "):
                                    suffix = " " + suffix
                                type_into_focused(suffix)
                                self.typed_text += suffix
                                self.last_model_text = text
                            
                            self.acc_chunks.append({"index": idx, "text": text, "start_sec": res.get("start_sec", 0)})
                    else:
                        for res in valid_results:
                            text = res.get("text", "").strip()
                            idx = res.get("index", 0)
                            self.acc_chunks.append({"index": idx, "text": text, "start_sec": res.get("start_sec", 0)})

            except Exception as e:
                logger.error(f"Transcribe thread engine crash: {e}")
                time.sleep(0.1)

    def stop_recording(self) -> str:
        # Sleep briefly to allow physical modifier keys (like SUPER) to be released
        # before the final transcript suffix is typed on screen.
        time.sleep(0.4)
        with self.lock:
            if not self.recording:
                logger.info(f"stop_recording: already stopped, returning empty")
                return ""
            self.recording = False
            self.stop_event.set()

        if self.record_thread:
            self.record_thread.join(timeout=2)
        if self.transcribe_thread:
            self.transcribe_thread.join(timeout=120)

        # Catch trailing fragments
        trailing = 0
        for res in self.worker.get_all():
            if res.get("text") and not is_hallucination(res["text"]):
                self.acc_chunks.append({"index": res.get("index", 0), "text": res["text"], "start_sec": res.get("start_sec", 0)})
                trailing += 1
        
        logger.info(f"stop_recording: realtime={self.realtime} acc_chunks={len(self.acc_chunks)} trailing={trailing} typed_text_len={len(self.typed_text)} submitted={self.submitted_count} processed={self.processed_count}")

        if self.realtime:
            # Since self.typed_text is no longer cleared on silence pauses, it contains
            # the complete, non-repeated typed text of the entire session.
            full_text = self.typed_text.strip()
        else:
            self.acc_chunks = sorted(self.acc_chunks, key=lambda x: x["index"])
            
            # Eliminate structural duplicate rolling records from unified context array
            seen_texts = set()
            unique_parts = []
            for c in self.acc_chunks:
                txt = c["text"].strip()
                if txt and txt.lower() not in seen_texts:
                    unique_parts.append(txt)
                    seen_texts.add(txt.lower())
                    
            full_text = " ".join(unique_parts).strip()

        if not full_text:
            logger.info(f"stop_recording: no text to save")
            notify("No speech", "No clear text captured")
            return ""

        ts = int(time.time())
        out_path = TRANSCRIPT_DIR / f"{'realtime' if self.realtime else 'live'}_{ts}.txt"
        out_path.write_text(full_text, encoding="utf-8")
        try:
            out_path.chmod(0o600)
        except Exception:
            pass

        out_choice = self.config.get("transcript_output", "both")
        if out_choice in ("clipboard", "both", "realtime-both"):
            if shutil.which("wl-copy"):
                try:
                    subprocess.run(["wl-copy"], input=full_text.encode(), check=True, timeout=5)
                except Exception:
                    pass
            notify("Complete", full_text[:200])

        logger.info(f"Saved transcript to {out_path}")
        return full_text

    def transcribe_file(self, filepath: str):
        path = Path(filepath).expanduser().resolve()
        if not path.exists():
            logger.error(f"Target path does not exist: {path}")
            return ""
        logger.info(f"File transcription target: {path}")
        notify("Transcribing", f"{path.name}")

        tmp_wav = RUNTIME_DIR / f"transcode_{int(time.time())}_{path.stem}.wav"
        try:
            # Enforce pristine high fidelity soxr resampling filters 
            cmd = ["ffmpeg", "-y", "-i", str(path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-af", "aresample=resampler=soxr:precision=28", str(tmp_wav)]
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)

            data, sr = sf.read(str(tmp_wav), dtype='float32', always_2d=False)
            if data.ndim > 1:
                data = data.mean(axis=1)

            segments = self.vad.get_segments(data, max_sec=self.chunk_seconds)
            if not segments:
                notify("No speech", f"No speech elements discovered in {path.name}")
                return ""

            incremental = TRANSCRIPT_DIR / f"{path.stem}_incremental.txt"
            incremental.write_text("", encoding="utf-8")
            full_parts = []

            self.worker.ensure()

            for idx, (s, e) in enumerate(segments):
                chunk = data[s:e].astype(np.float32)
                self.worker.submit(chunk, idx, s / 16000)
                
                waited = 0.0
                result_text = ""
                while waited < 60.0:
                    for res in self.worker.get_all():
                        if res.get("index") == idx:
                            result_text = res.get("text", "").strip()
                            break
                    if result_text:
                        break
                    time.sleep(0.1)
                    waited += 0.1
                    
                if result_text and not is_hallucination(result_text):
                    full_parts.append(result_text)
                    with open(incremental, "a", encoding="utf-8") as f:
                        f.write(result_text + "\n")

            full_text = " ".join(full_parts).strip()
            if full_text:
                final_path = TRANSCRIPT_DIR / f"{path.stem}_{int(time.time())}.txt"
                final_path.write_text(full_text, encoding="utf-8")
                if shutil.which("wl-copy"):
                    try:
                        subprocess.run(["wl-copy"], input=full_text.encode(), check=True, timeout=5)
                    except Exception:
                        pass
                notify("Complete", f"{path.name} finished processing.")
            return full_text
        finally:
            try:
                tmp_wav.unlink(missing_ok=True)
            except Exception:
                pass

    def fifo_loop(self):
        if FIFO_PATH.exists() or FIFO_PATH.is_symlink():
            try:
                st = os.lstat(FIFO_PATH)
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISFIFO(st.st_mode):
                    FIFO_PATH.unlink()
            except Exception:
                FIFO_PATH.unlink(missing_ok=True)

        if not FIFO_PATH.exists():
            old_umask = os.umask(0o077)
            try:
                os.mkfifo(FIFO_PATH, mode=0o600)
            finally:
                os.umask(old_umask)

        # O_RDWR avoids blocking conditions when execution triggers interface
        fd = os.open(FIFO_PATH, os.O_RDWR | os.O_NONBLOCK)
        poll = select.poll()
        poll.register(fd, select.POLLIN)
        logger.info(f"Secure IPC pipeline online at {FIFO_PATH}")

        while self.running:
            if not poll.poll(500):
                continue
            try:
                data = b""
                while True:
                    try:
                        chunk = os.read(fd, 4096)
                        if not chunk:
                            break
                        data += chunk
                    except BlockingIOError:
                        break
                if not data:
                    continue
                for line in data.decode('utf-8', errors='ignore').splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    logger.info(f"IPC input command: {line}")
                    if line == "START":
                        self.start_recording(realtime=False)
                    elif line == "START_REALTIME":
                        self.start_recording(realtime=True)
                    elif line == "STOP":
                        self.stop_recording()
                    elif line.startswith("FILE:"):
                        fpath = line[5:].strip()
                        threading.Thread(target=self.transcribe_file, args=(fpath,), daemon=True).start()
            except Exception as e:
                logger.error(f"IPC handling error: {e}")
                time.sleep(0.5)
        try:
            os.close(fd)
        except Exception:
            pass

    def start(self):
        def handle_sig(s, f):
            self.running = False
        signal.signal(signal.SIGTERM, handle_sig)
        signal.signal(signal.SIGINT, handle_sig)

        secure_write_pid(PID_FILE, str(os.getpid()))
        try:
            PID_FILE.chmod(0o600)
        except Exception:
            pass
            
        READY_FILE.touch()
        try:
            READY_FILE.chmod(0o600)
        except Exception:
            pass
            
        logger.info(f"Dusky Daemon v8.1 execution active loop on process: {os.getpid()}")
        threading.Thread(target=self.fifo_loop, daemon=True).start()

        try:
            while self.running:
                time.sleep(1)
        finally:
            self.worker.stop()
            for p in (FIFO_PATH, PID_FILE, READY_FILE, RECORD_PID_FILE):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {"model": "nemo-parakeet-tdt-0.6b-v2", "quantization": "int8", "chunk_seconds": 25, "enable_vad": True, "transcript_output": "realtime-both", "realtime": True, "realtime_chunk": 1.2, "idle_timeout": 30}

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--file", type=str)
    args = parser.parse_args()

    cfg = load_config()
    daemon = DuskyDaemon(cfg)

    if args.file:
        print(daemon.transcribe_file(args.file))
        return
    if args.daemon:
        daemon.start()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
