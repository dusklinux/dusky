#!/usr/bin/env python
import os
import sys
import re
import signal
import onnxruntime as ort
from kokoro_onnx import Kokoro
import numpy as np

# --- Configuration ---
MODEL_PATH = os.path.expanduser("~/contained_apps/uv/kokoro_gpu/kokoro-v1.0.fp16-gpu.onnx")
VOICES_PATH = os.path.expanduser("~/contained_apps/uv/kokoro_gpu/voices-v1.0.bin")
VOICE_NAME = "af_heart"
LANG_CODE = "en-us"
ALLOWED_CHARS = r"a-zA-Z0-9\s\.\,\!\?\;\:\'\-\%"

# --- Signal Handling ---
# This ensures the script exits cleanly immediately upon receiving a kill signal,
# allowing the pipeline to close and ffmpeg to write the WAV header for partial files.
def signal_handler(sig, frame):
    os._exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def initialize_kokoro():
    if not os.path.exists(MODEL_PATH) or not os.path.exists(VOICES_PATH):
        sys.stderr.write(f"FATAL: Model files not found at {MODEL_PATH}\n")
        os._exit(1)

    try:
        sess_options = ort.SessionOptions()
        sess_options.log_severity_level = 3
        kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
        gpu_sess = ort.InferenceSession(
            MODEL_PATH,
            sess_options=sess_options,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        kokoro.sess = gpu_sess
        return kokoro
    except Exception as e:
        sys.stderr.write(f"FATAL: Failed to initialize Kokoro/CUDA: {e}\n")
        os._exit(1)

def clean_text(text):
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'http[s]?://\S+', 'Link', text)
    cleaning_pattern = f"[^{ALLOWED_CHARS}]"
    text = re.sub(cleaning_pattern, ' ', text)
    text = ' '.join(text.split())
    return text.strip()

def smart_split(text):
    pattern = r'(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bJr)(?<!\bSr)(?<!\bProf)(?<!\bVol)(?<!\bNo)(?<!\bVs)(?<!\bEtc)\s*([.?!;:]+)\s+'
    chunks = re.split(pattern, text)
    sentences = []
    if len(chunks) == 1: return chunks
    for i in range(0, len(chunks) - 1, 2):
        if chunks[i].strip():
            sentences.append(f"{chunks[i].strip()}{chunks[i+1].strip()}")
    if len(chunks) % 2 != 0 and chunks[-1].strip():
        sentences.append(chunks[-1].strip())
    return sentences

def main():
    try:
        full_text = sys.stdin.read().strip()
        if not full_text: os._exit(0)

        cleaned_text = clean_text(full_text)
        if not cleaned_text: os._exit(0)

        kokoro = initialize_kokoro()
        sentences = smart_split(cleaned_text)

        for sentence in sentences:
            audio, sr = kokoro.create(sentence, voice=VOICE_NAME, speed=1.0, lang=LANG_CODE)
            if audio is not None and len(audio) > 0:
                if audio.dtype != np.float32:
                    audio = audio.astype(np.float32)
                try:
                    sys.stdout.buffer.write(audio.tobytes())
                    sys.stdout.buffer.flush()
                except (BrokenPipeError, IOError):
                    os._exit(0)
    except Exception:
        os._exit(1)
    os._exit(0)

if __name__ == "__main__":
    main()
