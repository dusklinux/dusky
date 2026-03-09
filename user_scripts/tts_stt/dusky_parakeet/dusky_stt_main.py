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
import shutil
from pathlib import Path
import logging

# ==============================================================================
# CONFIGURATION & MODEL SELECTION
# ==============================================================================
VERSION = "3.3 (English V2 + Arena Flush + MIGraphX AMD Support)"

STT_MODEL_NAME = "nemo-parakeet-tdt-0.6b-v2"  
QUANTIZATION = "int8" 

ZRAM_MOUNT = Path("/mnt/zram1")
AUDIO_DIR = ZRAM_MOUNT if ZRAM_MOUNT.is_dir() else Path("/tmp/dusky_stt_audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

FIFO_PATH = Path("/tmp/dusky_stt.fifo")
PID_FILE = Path("/tmp/dusky_stt.pid")
READY_FILE = Path("/tmp/dusky_stt.ready")

IDLE_TIMEOUT = 10.0  
QUEUE_SIZE = 5

logger = logging.getLogger("dusky_stt")
logger.setLevel(logging.INFO)
c_handler = logging.StreamHandler()
c_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(c_handler)

def custom_excepthook(args):
    logger.critical(f"UNCAUGHT EXCEPTION: {args.exc_value}")
    traceback.print_tb(args.exc_traceback)

threading.excepthook = custom_excepthook

def notify(title, message, critical=False):
    if not shutil.which("notify-send"):
        logger.error(f"notify-send missing. Cannot display: {title} - {message}")
        return
    cmd = ["notify-send", "-a", "Parakeet STT", "-t", "3000"]
    if critical:
        cmd.extend(["-u", "critical"])
    cmd.extend([title, message])
    try:
        subprocess.run(cmd, check=False)
    except Exception as e:
        logger.error(f"Failed to execute notify-send: {e}")

# ==============================================================================
# HARDWARE ENFORCER
# ==============================================================================
import onnxruntime as rt

class PatchedInferenceSession(rt.InferenceSession):
    def __init__(self, path_or_bytes, sess_options=None, providers=None, **kwargs):
        if sess_options is None:
            sess_options = rt.SessionOptions()
        
        p_names = []
        p_opts = []
        available_set = set(rt.get_available_providers())
        is_gpu = False

        if 'CUDAExecutionProvider' in available_set:
            is_gpu = True
            p_names.append('CUDAExecutionProvider')
            p_opts.append({
                'device_id': 0,
                'arena_extend_strategy': 'kSameAsRequested',
                'gpu_mem_limit': 6 * 1024 * 1024 * 1024,
                'cudnn_conv_algo_search': 'HEURISTIC',
                'do_copy_in_default_stream': True,
            })
        elif 'MIGraphXExecutionProvider' in available_set:
            is_gpu = True
            p_names.append('MIGraphXExecutionProvider')
            p_opts.append({
                'device_id': 0,
            })
        elif 'ROCmExecutionProvider' in available_set:
            is_gpu = True
            p_names.append('ROCmExecutionProvider')
            p_opts.append({
                'device_id': 0,
                'arena_extend_strategy': 'kSameAsRequested',
                'gpu_mem_limit': 3 * 1024 * 1024 * 1024,
                'do_copy_in_default_stream': True,
            })

        p_names.append('CPUExecutionProvider')
        p_opts.append({})

        if is_gpu:
            sess_options.enable_mem_pattern = False
            sess_options.enable_cpu_mem_arena = False
        else:
            sess_options.enable_mem_pattern = True
            sess_options.enable_cpu_mem_arena = True

        sess_options.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
        kwargs.pop('provider_options', None)
        
        super().__init__(path_or_bytes, sess_options, providers=p_names, provider_options=p_opts, **kwargs)

rt.InferenceSession = PatchedInferenceSession
import onnx_asr 

# ==============================================================================
# THREAD: FIFO READER
# ==============================================================================
class FifoReader(threading.Thread):
    def __init__(self, path_queue, fifo_path):
        super().__init__(name="FIFO")
        self.path_queue = path_queue
        self.fifo_path = fifo_path
        self.active = True
        self.daemon = True

    def run(self):
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
                        chunk = os.read(fd, 4096)
                        if not chunk: break
                        data += chunk
                    except BlockingIOError: break
                
                if data:
                    for path_str in data.decode('utf-8', errors='ignore').splitlines():
                        clean_path = path_str.strip()
                        if clean_path:
                            self.path_queue.put(clean_path)
            except OSError: time.sleep(1)
        os.close(fd)

# ==============================================================================
# DAEMON CORE
# ==============================================================================
class DuskySTTDaemon:
    def __init__(self):
        self.running = True
        logger.info(f"Dusky STT Daemon {VERSION} Initializing...")
        self.path_queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.fifo_reader = FifoReader(self.path_queue, FIFO_PATH)
        self.model = None
        self.last_used = 0

    def get_model(self):
        self.last_used = time.time()
        if self.model is None:
            logger.info(f"Loading {STT_MODEL_NAME} (Quantization: {QUANTIZATION}) into VRAM...")
            self.model = onnx_asr.load_model(STT_MODEL_NAME, quantization=QUANTIZATION)
        return self.model

    def check_idle(self):
        if self.model and (time.time() - self.last_used > IDLE_TIMEOUT):
            logger.info(f"Idle timeout ({IDLE_TIMEOUT}s). Unloading model to free VRAM.")
            del self.model
            self.model = None
            gc.collect()

    def transcribe(self, filepath):
        logger.info(f"Transcribing: {filepath}")
        
        try:
            model = self.get_model()
            res = model.recognize(filepath)
            text = (res[0] if isinstance(res, list) else res).strip()
            
            del res
            gc.collect()
            
        except Exception as e:
            logger.error(f"Inference Error: {e}")
            self.model = None 
            notify("Transcription Failed", str(e), critical=True)
            return

        if not text:
            logger.warning("No speech detected.")
            return
            
        logger.info(f"Success: {text[:50]}...")
        
        try:
            if not shutil.which("wl-copy"): raise FileNotFoundError("wl-copy not found in PATH")
            subprocess.run(["wl-copy"], input=text.encode('utf-8'), check=True)
            notify("Transcription Complete", text)
        except Exception as e:
            logger.error(f"Wayland Output Error: {e}")
            notify("Output Failed (wl-copy)", str(e), critical=True)

    def start(self):
        signal.signal(signal.SIGTERM, lambda s, f: self.stop())
        signal.signal(signal.SIGINT, lambda s, f: self.stop())
        
        PID_FILE.write_text(str(os.getpid()))
        if FIFO_PATH.exists() and not FIFO_PATH.is_fifo(): FIFO_PATH.unlink()
        
        self.fifo_reader.start()
        READY_FILE.touch()
        logger.info(f"Daemon Ready (PID: {os.getpid()})")
        
        try:
            while self.running:
                try:
                    filepath = self.path_queue.get(timeout=1.0)
                    if Path(filepath).exists():
                        self.transcribe(filepath)
                    self.path_queue.task_done()
                except queue.Empty: 
                    self.check_idle()
        finally: self.cleanup()

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
    args = parser.parse_args()
    if args.daemon: DuskySTTDaemon().start()
    else: print("Run with --daemon")
