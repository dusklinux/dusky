import os
import time
import signal
import threading
import queue
import argparse
import select
import gc
import subprocess
import traceback
import sys
import shutil
from pathlib import Path
import logging
import warnings

# Suppress the known multiprocessing resource_tracker warning triggered by Silero VAD
warnings.filterwarnings("ignore", category=UserWarning, module="multiprocessing.resource_tracker")

# ==============================================================================
# VERSION & CONFIGURATION
# ==============================================================================
VERSION = "1.2 (Dusky STT - Explicit Caching & Wayland Optimized)"

ZRAM_MOUNT = Path("/tmp") # Using /tmp for audio buffering
FIFO_PATH = Path("/tmp/dusky_stt.fifo")
PID_FILE = Path("/tmp/dusky_stt.pid")
READY_FILE = Path("/tmp/dusky_stt.ready")

DEFAULT_MODEL = "distil-large-v3"
IDLE_TIMEOUT = 10.0  # Time before model is unloaded from VRAM
QUEUE_SIZE = 5

# ==============================================================================
# LOGGING
# ==============================================================================
logger = logging.getLogger("dusky_stt_daemon")
logger.setLevel(logging.INFO)

c_handler = logging.StreamHandler()
c_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'
))
logger.addHandler(c_handler)

def setup_debug_logging(filepath):
    f_handler = logging.FileHandler(filepath, mode='w')
    f_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(threadName)s - %(levelname)s - %(funcName)s - %(message)s'
    ))
    f_handler.setLevel(logging.DEBUG)
    logger.addHandler(f_handler)
    logger.setLevel(logging.DEBUG)
    logger.info(f"Debug logging enabled to: {filepath}")

def custom_excepthook(args):
    thread_name = args.thread.name if args.thread else "unknown (GC'd)"
    logger.critical(f"UNCAUGHT EXCEPTION in thread {thread_name}: {args.exc_value}")
    traceback.print_tb(args.exc_traceback)

threading.excepthook = custom_excepthook

# ==============================================================================
# HARDWARE VERIFICATION
# ==============================================================================
def verify_cuda_environment():
    """Forces CTranslate2 to report if it actually sees the CUDA backend."""
    try:
        import ctranslate2
        cuda_supported = ctranslate2.get_supported_compute_types("cuda")
        if cuda_supported:
            logger.info(f"[HARDWARE] CTranslate2 CUDA Backend OK. Supported precision: {cuda_supported}")
            return True
        else:
            logger.warning("[HARDWARE] CTranslate2 loaded, but CUDA backend returned no supported types!")
            return False
    except Exception as e:
        logger.warning(f"[HARDWARE] CTranslate2 CUDA check failed: {e}")
        return False

# ==============================================================================
# WAYLAND TEXT INJECTION
# ==============================================================================
def inject_text_wayland(text):
    """Puts text in clipboard and types it using available Wayland tools."""
    if not text:
        return
    
    # 1. Copy to clipboard
    if shutil.which("wl-copy"):
        try:
            subprocess.run(["wl-copy"], input=text.encode('utf-8'), check=True)
            logger.debug("Text copied to Wayland clipboard.")
        except Exception as e:
            logger.error(f"Failed to wl-copy: {e}")
    else:
        logger.warning("wl-copy not found. Cannot copy to clipboard.")

    # 2. Type it out natively
    if shutil.which("wtype"):
        try:
            subprocess.run(["wtype", text], check=True)
            logger.debug("Text injected via wtype.")
        except Exception as e:
            logger.error(f"Failed to wtype: {e}")
    elif shutil.which("ydotool"):
        try:
            subprocess.run(["ydotool", "type", text], check=True)
            logger.debug("Text injected via ydotool.")
        except Exception as e:
            logger.error(f"Failed to ydotool: {e}")
    else:
        logger.info("Auto-typing tools (wtype/ydotool) not found. Text ready in clipboard.")

# ==============================================================================
# THREAD 1: FIFO READER
# ==============================================================================
class FifoReader(threading.Thread):
    def __init__(self, task_queue, fifo_path):
        super().__init__(name="FIFO-Thread")
        self.task_queue = task_queue
        self.fifo_path = fifo_path
        self.active = True
        self.daemon = True
        self.fd = None 

    def run(self):
        if self.fd is not None:
            fd = self.fd
        else:
            if not self.fifo_path.exists(): os.mkfifo(self.fifo_path)
            fd = os.open(self.fifo_path, os.O_RDWR | os.O_NONBLOCK)

        poll = select.poll()
        poll.register(fd, select.POLLIN)

        while self.active:
            if not poll.poll(500): continue
            try:
                data = b""
                while True:
                    try:
                        chunk = os.read(fd, 65536)
                        if not chunk: break
                        data += chunk
                    except BlockingIOError: break
                if not data: continue
                
                # Payload format expected: "/path/to/audio.wav|model_name"
                payload = data.decode('utf-8', errors='ignore').strip()
                if not payload: continue
                
                parts = payload.split('|')
                audio_path = parts[0].strip()
                model_name = parts[1].strip() if len(parts) > 1 else DEFAULT_MODEL
                
                if Path(audio_path).exists():
                    self.task_queue.put((audio_path, model_name))
                else:
                    logger.warning(f"Received invalid audio path: {audio_path}")
                    
            except OSError: time.sleep(1)
        os.close(fd)

# ==============================================================================
# DAEMON CORE & VRAM MANAGER
# ==============================================================================
class DuskySTTDaemon:
    def __init__(self, debug_file=None, device_mode="cpu"):
        self.running = True
        self.device_mode = device_mode
        if debug_file: setup_debug_logging(debug_file)
        
        logger.info(f"Dusky STT Daemon {VERSION} Initializing [Mode: {self.device_mode.upper()}]...")
        if self.device_mode == "nvidia":
            verify_cuda_environment()
        
        self.task_queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.stop_event = threading.Event()
        self.fifo_reader = FifoReader(self.task_queue, FIFO_PATH)
        
        self.whisper_model = None
        self.current_model_name = None
        self.last_used = 0

    def get_model(self, model_name):
        self.last_used = time.time()
        
        if self.whisper_model is not None and self.current_model_name == model_name:
            return self.whisper_model

        if self.whisper_model is not None:
            logger.info(f"Unloading model {self.current_model_name} for swap...")
            del self.whisper_model
            self.whisper_model = None
            gc.collect()

        logger.info(f"Loading Faster-Whisper '{model_name}' into VRAM...")
        from faster_whisper import WhisperModel
        
        # Point the library directly to our isolated containment directory
        model_dir = Path(__file__).parent / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        
        # Hardware specific precision loading
        if self.device_mode == "nvidia":
            compute_type = "int8_float16" # Optimal for modern NVIDIA GPUs
            device = "cuda"
        else:
            compute_type = "int8"
            device = "cpu"

        try:
            self.whisper_model = WhisperModel(
                model_name, 
                device=device, 
                compute_type=compute_type,
                download_root=str(model_dir) # Strict Sandbox Integrity
            )
            self.current_model_name = model_name
            logger.info(f"Model '{model_name}' successfully loaded into VRAM.")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

        return self.whisper_model

    def check_idle(self):
        if self.whisper_model and (time.time() - self.last_used > IDLE_TIMEOUT):
            logger.info("Idle timeout. Cleaning VRAM.")
            del self.whisper_model
            self.whisper_model = None
            self.current_model_name = None
            gc.collect()

    def _setup_fifo(self):
        if FIFO_PATH.exists():
            if not FIFO_PATH.is_fifo():
                logger.warning(f"Non-FIFO file at {FIFO_PATH}, removing.")
                FIFO_PATH.unlink()
        if not FIFO_PATH.exists(): os.mkfifo(FIFO_PATH)
        fd = os.open(FIFO_PATH, os.O_RDWR | os.O_NONBLOCK)
        self.fifo_reader.fd = fd
        logger.debug("FIFO created and opened.")

    def transcribe(self, audio_path, model_name):
        try:
            model = self.get_model(model_name)
            logger.info(f"Transcribing {audio_path}...")
            
            # Silero VAD runs here (via ONNX) to strip silence, then CTranslate2 handles the rest via GPU
            segments, info = model.transcribe(audio_path, beam_size=5, language="en")
            text = " ".join([segment.text for segment in segments]).strip()
            
            logger.info(f"Transcription complete: '{text}'")
            inject_text_wayland(text)
            
            # Clean up the temp audio file
            Path(audio_path).unlink(missing_ok=True)
            
        except Exception as e:
            logger.error(f"Transcription Error: {e}")
            self.whisper_model = None # Force reload on next attempt

    def start(self):
        signal.signal(signal.SIGTERM, lambda s, f: self.stop())
        signal.signal(signal.SIGINT, lambda s, f: self.stop())
        PID_FILE.write_text(str(os.getpid()))
        
        self._setup_fifo()
        self.fifo_reader.start()
        READY_FILE.touch()
        logger.info(f"Daemon Ready (PID: {os.getpid()})")
        
        try:
            while self.running:
                try:
                    audio_path, model_name = self.task_queue.get(timeout=0.5)
                    self.transcribe(audio_path, model_name)
                    self.task_queue.task_done()
                except queue.Empty:
                    self.check_idle()
        finally:
            self.cleanup()

    def stop(self):
        self.running = False

    def cleanup(self):
        logger.info("Shutting down...")
        self.running = False
        self.fifo_reader.active = False
        for p in (FIFO_PATH, PID_FILE, READY_FILE):
            try: p.unlink(missing_ok=True)
            except Exception: pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--mode", default="cpu", choices=["cpu", "nvidia"])
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--debug-file", help="Path to write debug log")
    args = parser.parse_args()
    
    log_level = os.environ.get("DUSKY_STT_LOG_LEVEL", args.log_level).upper()
    if hasattr(logging, log_level): logger.setLevel(getattr(logging, log_level))
    debug_path = args.debug_file or os.environ.get("DUSKY_STT_LOG_FILE")
    
    if args.daemon: DuskySTTDaemon(debug_path, args.mode).start()
    else: print("Run with --daemon")
