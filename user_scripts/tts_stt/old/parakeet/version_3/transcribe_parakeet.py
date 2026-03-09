#!/usr/bin/env python3
"""
Parakeet Transcription Script
Version: 3.0 (Audited & Hardened)

Optimized for low VRAM (4GB) GPUs.

Exit Codes:
    0: Success
    1: General/Unknown error
    2: Invalid arguments or file not found
    3: Model loading failure
    4: GPU Out of Memory
    5: Transcription error
"""

from __future__ import annotations

import gc
import logging
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import torch

if TYPE_CHECKING:
    # Avoid importing heavy NeMo modules at top level for faster startup on errors
    from nemo.collections.asr.models import ASRModel

# --- Configuration ---
MODEL_NAME = "nvidia/parakeet-tdt-0.6b-v2"

# --- Exit Codes ---
EXIT_OK = 0
EXIT_GENERAL_ERROR = 1
EXIT_INVALID_INPUT = 2
EXIT_MODEL_LOAD_FAILURE = 3
EXIT_OOM = 4
EXIT_TRANSCRIPTION_ERROR = 5


def log_status(message: str) -> None:
    """Print a status message to stderr, flushed immediately."""
    print(message, file=sys.stderr, flush=True)


def configure_logging() -> None:
    """
    Silence noisy libraries. MUST be called BEFORE importing NeMo.
    """
    warnings.filterwarnings("ignore", category=UserWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    noisy_loggers = (
        "pytorch_lightning",
        "nemo_logger",
        "nemo",
        "transformers",
        "torch",
    )
    for name in noisy_loggers:
        logging.getLogger(name).setLevel(logging.ERROR)

    try:
        from nemo.utils import logging as nemo_logging
        nemo_logging.setLevel(logging.ERROR)
    except ImportError:
        pass  # Will fail with a clearer message in load_model


def load_model() -> ASRModel:
    """
    Load the ASR model with VRAM optimizations.

    Strategy:
        1. Load to CPU first (uses system RAM).
        2. Convert to FP16 (half precision).
        3. Enable eval mode (disables dropout, etc.).
        4. Garbage collect before GPU transfer.
        5. Move to GPU if available.

    Returns:
        The loaded ASR model, ready for inference.
    """
    try:
        import nemo.collections.asr as nemo_asr
    except ImportError as e:
        log_status(f"Fatal: NeMo ASR library not installed. {e}")
        sys.exit(EXIT_MODEL_LOAD_FAILURE)

    try:
        log_status("Loading model to CPU...")
        model = nemo_asr.models.ASRModel.from_pretrained(
            model_name=MODEL_NAME,
            map_location=torch.device("cpu"),
        )

        model = model.half()  # FP16 for VRAM savings
        model.eval()          # Inference mode (CRITICAL)

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            log_status("Moving model to GPU...")
            model = model.cuda()
        else:
            log_status("Warning: CUDA unavailable. Running on CPU (slow).")

        return model

    except Exception as e:
        log_status(f"Fatal: Failed to load model. {e}")
        sys.exit(EXIT_MODEL_LOAD_FAILURE)


def transcribe(model: ASRModel, audio_path: Path) -> Optional[str]:
    """
    Run transcription on a single audio file.

    Args:
        model: A loaded NeMo ASR model.
        audio_path: Path to the .wav file.

    Returns:
        The transcribed text, or None if no speech was detected.
    """
    try:
        with torch.inference_mode():
            # NeMo's transcribe expects a list of paths as strings
            output = model.transcribe([str(audio_path)], verbose=False)

        if not output or not isinstance(output, list) or len(output) == 0:
            return None

        result = output[0]

        # Handle varying NeMo output formats across versions
        if hasattr(result, "text"):
            text = result.text
        elif hasattr(result, "hypothesis"):
            text = result.hypothesis
        else:
            text = str(result)

        return text.strip() if text else None

    except torch.cuda.OutOfMemoryError:
        log_status("Fatal: GPU ran out of memory. Try a shorter recording.")
        sys.exit(EXIT_OOM)
    except Exception as e:
        log_status(f"Fatal: Transcription failed. {e}")
        sys.exit(EXIT_TRANSCRIPTION_ERROR)


def validate_input(audio_path: Path) -> None:
    """Validate the input file before processing."""
    if not audio_path.exists():
        log_status(f"Error: File not found: {audio_path}")
        sys.exit(EXIT_INVALID_INPUT)
    if not audio_path.is_file():
        log_status(f"Error: Not a regular file: {audio_path}")
        sys.exit(EXIT_INVALID_INPUT)
    if audio_path.stat().st_size == 0:
        log_status(f"Error: File is empty: {audio_path}")
        sys.exit(EXIT_INVALID_INPUT)


def main() -> None:
    """Main entry point."""
    if len(sys.argv) < 2:
        log_status("Usage: python transcribe_parakeet.py <path_to_audio.wav>")
        sys.exit(EXIT_INVALID_INPUT)

    audio_path = Path(sys.argv[1]).resolve()
    validate_input(audio_path)

    # Configure logging BEFORE heavy imports
    configure_logging()

    asr_model = load_model()
    text = transcribe(asr_model, audio_path)

    # Output ONLY the transcription to stdout. Bash script reads this.
    if text:
        print(text)

    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
