#!/usr/bin/env python
"""
Kokoro TTS - Stream synthesized speech to stdout as raw f32le PCM.
"""
import os
import re
import signal
import sys
from typing import Optional

import numpy as np
import onnxruntime as ort

# Late import to avoid loading at module level
Kokoro = None

# ==============================================================================
# Configuration
# ==============================================================================
MODEL_PATH = os.path.expanduser("~/contained_apps/uv/kokoro_gpu/kokoro-v1.0.fp16-gpu.onnx")
VOICES_PATH = os.path.expanduser("~/contained_apps/uv/kokoro_gpu/voices-v1.0.bin")
VOICE_NAME = "af_heart"
LANG_CODE = "en-us"
EXPECTED_SAMPLE_RATE = 24000

# Character whitelist for text cleaning (properly escaped for regex char class)
ALLOWED_CHARS = r"a-zA-Z0-9\s.,!?;:'%\-"

# Pre-compiled regex patterns for performance
RE_MARKDOWN_LINK = re.compile(r'\[([^\]]+)\]\([^)]+\)')
RE_URL = re.compile(r'https?://\S+', re.IGNORECASE)
RE_CLEAN = re.compile(f"[^{ALLOWED_CHARS}]")
RE_SENTENCE_SPLIT = re.compile(
    r'(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bJr)(?<!\bSr)'
    r'(?<!\bProf)(?<!\bVol)(?<!\bNo)(?<!\bVs)(?<!\bEtc)'
    r'\s*([.?!;:]+)\s+'
)


def fatal(message: str) -> None:
    """Print fatal error and exit immediately."""
    sys.stderr.write(f"FATAL: {message}\n")
    sys.stderr.flush()
    os._exit(1)


def setup_signal_handlers() -> None:
    """
    Install signal handlers for graceful pipeline shutdown.
    Uses os._exit() to immediately terminate without cleanup,
    allowing upstream pipeline components to receive SIGPIPE.
    """
    def handler(sig: int, frame) -> None:
        os._exit(0)
    
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    # Handle broken pipe from downstream
    signal.signal(signal.SIGPIPE, handler) if hasattr(signal, 'SIGPIPE') else None


def initialize_kokoro():
    """
    Initialize Kokoro with GPU acceleration.
    Avoids double-loading by configuring session options before model load.
    """
    global Kokoro
    from kokoro_onnx import Kokoro as KokoroClass
    Kokoro = KokoroClass

    if not os.path.isfile(MODEL_PATH):
        fatal(f"Model not found: {MODEL_PATH}")
    if not os.path.isfile(VOICES_PATH):
        fatal(f"Voices file not found: {VOICES_PATH}")

    try:
        # Configure ONNX Runtime session options
        sess_options = ort.SessionOptions()
        sess_options.log_severity_level = 3  # Error only
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        
        # Create session with GPU priority, CPU fallback
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        
        # Initialize Kokoro - this loads model with default session
        kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
        
        # Replace with optimized GPU session
        gpu_session = ort.InferenceSession(
            MODEL_PATH,
            sess_options=sess_options,
            providers=providers
        )
        kokoro.sess = gpu_session
        
        return kokoro

    except Exception as e:
        fatal(f"Failed to initialize Kokoro/ONNX: {e}")


def clean_text(text: str) -> str:
    """
    Sanitize input text for TTS processing.
    - Removes markdown links (keeps link text)
    - Replaces URLs with 'Link'
    - Strips non-whitelisted characters
    - Normalizes whitespace
    """
    # Extract link text from markdown
    text = RE_MARKDOWN_LINK.sub(r'\1', text)
    
    # Replace URLs
    text = RE_URL.sub('Link', text)
    
    # Remove disallowed characters
    text = RE_CLEAN.sub(' ', text)
    
    # Normalize whitespace
    return ' '.join(text.split())


def smart_split(text: str) -> list[str]:
    """
    Split text into sentences while preserving abbreviations.
    Handles: Mr., Mrs., Ms., Dr., Jr., Sr., Prof., Vol., No., Vs., Etc.
    """
    if not text:
        return []
    
    chunks = RE_SENTENCE_SPLIT.split(text)
    
    if len(chunks) == 1:
        return [text.strip()] if text.strip() else []
    
    sentences = []
    
    # Reassemble sentences with their punctuation
    for i in range(0, len(chunks) - 1, 2):
        sentence = chunks[i].strip()
        punctuation = chunks[i + 1].strip() if i + 1 < len(chunks) else ''
        if sentence:
            sentences.append(f"{sentence}{punctuation}")
    
    # Handle trailing text without punctuation
    if len(chunks) % 2 != 0:
        trailing = chunks[-1].strip()
        if trailing:
            sentences.append(trailing)
    
    return sentences


def stream_audio(kokoro, sentences: list[str]) -> None:
    """
    Generate and stream audio for each sentence.
    Writes raw float32 PCM to stdout.
    """
    stdout_buffer = sys.stdout.buffer
    
    for sentence in sentences:
        if not sentence:
            continue
        
        try:
            audio, sample_rate = kokoro.create(
                sentence, 
                voice=VOICE_NAME, 
                speed=1.0, 
                lang=LANG_CODE
            )
        except Exception as e:
            sys.stderr.write(f"WARNING: Failed to synthesize: {e}\n")
            continue
        
        if audio is None or len(audio) == 0:
            continue
        
        # Validate sample rate matches expected
        if sample_rate != EXPECTED_SAMPLE_RATE:
            sys.stderr.write(
                f"WARNING: Sample rate mismatch: got {sample_rate}, "
                f"expected {EXPECTED_SAMPLE_RATE}\n"
            )
        
        # Ensure float32 format
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        
        try:
            stdout_buffer.write(audio.tobytes())
            stdout_buffer.flush()
        except (BrokenPipeError, IOError):
            # Downstream closed, exit gracefully
            os._exit(0)


def main() -> int:
    """Main entry point."""
    setup_signal_handlers()
    
    # Read all input
    try:
        full_text = sys.stdin.read()
    except KeyboardInterrupt:
        return 0
    
    full_text = full_text.strip()
    if not full_text:
        return 0
    
    # Clean and validate
    cleaned_text = clean_text(full_text)
    if not cleaned_text:
        sys.stderr.write("WARNING: No valid text after cleaning\n")
        return 0
    
    # Initialize TTS engine
    kokoro = initialize_kokoro()
    
    # Split into sentences
    sentences = smart_split(cleaned_text)
    if not sentences:
        sys.stderr.write("WARNING: No sentences to process\n")
        return 0
    
    # Stream audio
    stream_audio(kokoro, sentences)
    
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        sys.stderr.write(f"FATAL: Unhandled exception: {e}\n")
        os._exit(1)
