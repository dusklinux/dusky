import sys
import os
import logging
import torch
import gc

# --- Configuration ---
MODEL_NAME = "nvidia/parakeet-tdt-0.6b-v2"

def configure_silence():
    """
    Configures NeMo and PyTorch logging to be as quiet as possible.
    """
    # Suppress NeMo Logging
    from nemo.utils import logging as nemo_logging
    nemo_logging.setLevel(logging.ERROR)
    
    # Suppress PyTorch/Lightning warnings
    import warnings
    warnings.filterwarnings("ignore")
    logging.getLogger("pytorch_lightning").setLevel(logging.ERROR)

def load_optimized_model():
    """
    Loads the model safely onto 4GB VRAM hardware.
    1. Download/Load to CPU (System RAM) first.
    2. Convert to FP16 (Half Precision).
    3. Move to GPU.
    """
    import nemo.collections.asr as nemo_asr

    try:
        # Step 1: Load to System RAM (CPU)
        # This triggers the download if the model is not cached.
        model = nemo_asr.models.ASRModel.from_pretrained(
            model_name=MODEL_NAME,
            map_location=torch.device("cpu")
        )

        # Step 2: Convert to Half Precision (FP16)
        # Essential for RTX 3050 Ti (4GB)
        if hasattr(model, 'half'):
            model = model.half()

        # Step 3: Clean up System Memory
        gc.collect()
        torch.cuda.empty_cache()

        # Step 4: Move to GPU
        if torch.cuda.is_available():
            model = model.cuda()
            # Lock the model in eval mode
            model.eval()
        else:
            print("Warning: CUDA not found, running on CPU.", file=sys.stderr)

        return model

    except Exception as e:
        print(f"Error loading model: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    # Ensure we have an argument
    if len(sys.argv) < 2:
        print("Usage: python transcribe_parakeet.py <path_to_wav>", file=sys.stderr)
        sys.exit(1)

    audio_file_path = sys.argv[1]

    if not os.path.exists(audio_file_path):
        print(f"Error: Audio file not found: {audio_file_path}", file=sys.stderr)
        sys.exit(1)

    configure_silence()
    
    # Load model
    asr_model = load_optimized_model()

    # Transcribe using inference_mode for maximum efficiency
    try:
        with torch.inference_mode(): 
            # verbose=False prevents the progress bar
            output = asr_model.transcribe([audio_file_path], verbose=False)
        
        if output and isinstance(output, list) and len(output) > 0:
            # Handle potential TDT output differences
            text = output[0].text.strip() if hasattr(output[0], 'text') else str(output[0]).strip()
            print(text)
        else:
            # Print nothing to stdout if silence
            pass

    except Exception as e:
        print(f"Transcription error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
