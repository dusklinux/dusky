#!/usr/bin/env python3
"""
Parakeet Transcription Script
Version: 2.0 (Bug-fixed & Optimized)

Optimized for low VRAM (4GB) GPUs like RTX 3050 Ti.

Fixes:
  - model.eval() now called unconditionally (was only called for CUDA)
  - More specific warning filters (not blanket ignore)
  - Added pathlib for robust path handling
  - Separated transcription logic for clarity
  - Type hints for better maintainability
"""

import sys
import os
import logging
import warnings
from pathlib import Path
from typing import Optional

import torch
import gc

# --- Configuration ---
MODEL_NAME = "nvidia/parakeet-tdt-0.6b-v2"


def configure_silence() -> None:
    """
    Configures NeMo and PyTorch logging to be as quiet as possible.
    Must be called BEFORE importing nemo modules for full effect.
    """
    # Filter specific warning categories (not blanket ignore)
    warnings.filterwarnings("ignore", category=UserWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    
    # Suppress common noisy loggers
    for logger_name in [
        "pytorch_lightning",
        "nemo_logger", 
        "nemo",
        "transformers",
        "torch",
    ]:
        logging.getLogger(logger_name).setLevel(logging.ERROR)
    
    # NeMo-specific logging (import here to apply settings first)
    try:
        from nemo.utils import logging as nemo_logging
        nemo_logging.setLevel(logging.ERROR)
    except ImportError:
        pass  # Will fail later with better error message


def load_optimized_model():
    """
    Loads the ASR model with optimizations for limited VRAM.
    
    Strategy:
        1. Load to CPU (System RAM) first
        2. Convert to FP16 (Half Precision) 
        3. Set eval mode (CRITICAL: must happen regardless of device!)
        4. Clean up memory
        5. Move to GPU if available
    
    Returns:
        Loaded ASR model ready for inference
    """
    try:
        import nemo.collections.asr as nemo_asr
    except ImportError as e:
        print(f"Error: NeMo ASR not installed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        # Step 1: Load to System RAM (CPU)
        # This triggers download if model is not cached
        print("Loading model to CPU...", file=sys.stderr)
        model = nemo_asr.models.ASRModel.from_pretrained(
            model_name=MODEL_NAME,
            map_location=torch.device("cpu")
        )

        # Step 2: Convert to Half Precision (FP16)
        # Essential for 4GB VRAM cards
        model = model.half()

        # Step 3: Set eval mode UNCONDITIONALLY
        # CRITICAL FIX: This was previously only called for CUDA!
        # Eval mode disables dropout, uses running stats for batchnorm, etc.
        model.eval()

        # Step 4: Clean up memory before GPU transfer
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # Step 5: Move to GPU if available
        if torch.cuda.is_available():
            print("Moving model to GPU...", file=sys.stderr)
            model = model.cuda()
        else:
            print("Warning: CUDA not available, running on CPU (will be slow)", file=sys.stderr)

        return model

    except Exception as e:
        print(f"Error loading model: {e}", file=sys.stderr)
        sys.exit(1)


def transcribe_audio(model, audio_path: Path) -> Optional[str]:
    """
    Transcribe a single audio file.
    
    Args:
        model: Loaded ASR model
        audio_path: Path to audio file
        
    Returns:
        Transcribed text, or None if no speech detected
    """
    try:
        with torch.inference_mode():
            # verbose=False suppresses progress bar
            output = model.transcribe([str(audio_path)], verbose=False)
        
        if not output or not isinstance(output, list) or len(output) == 0:
            return None
        
        result = output[0]
        
        # Handle different NeMo output formats
        if hasattr(result, 'text'):
            text = result.text
        elif hasattr(result, 'hypothesis'):
            text = result.hypothesis
        else:
            text = str(result)
        
        text = text.strip() if text else None
        return text if text else None

    except torch.cuda.OutOfMemoryError:
        print("Error: GPU out of memory. Try a shorter audio file.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Transcription error: {e}", file=sys.stderr)
        sys.exit(1)


def validate_audio_file(audio_path: Path) -> None:
    """Validate the audio file exists and is readable."""
    if not audio_path.exists():
        print(f"Error: Audio file not found: {audio_path}", file=sys.stderr)
        sys.exit(1)
    
    if not audio_path.is_file():
        print(f"Error: Not a file: {audio_path}", file=sys.stderr)
        sys.exit(1)
    
    if audio_path.stat().st_size == 0:
        print(f"Error: Audio file is empty: {audio_path}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Main entry point."""
    # Argument validation
    if len(sys.argv) < 2:
        print("Usage: python transcribe_parakeet.py <path_to_wav>", file=sys.stderr)
        sys.exit(1)

    audio_path = Path(sys.argv[1]).resolve()
    validate_audio_file(audio_path)

    # Configure logging BEFORE heavy imports
    configure_silence()
    
    # Load model (this is the slow part - downloads on first run)
    asr_model = load_optimized_model()

    # Transcribe
    text = transcribe_audio(asr_model, audio_path)
    
    # Output: only print to stdout if we have text
    # Bash script reads stdout for the transcription result
    if text:
        print(text)
    # else: silence = no output to stdout


if __name__ == "__main__":
    main()
