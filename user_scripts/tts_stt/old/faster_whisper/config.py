import sys
import argparse
from faster_whisper import WhisperModel

def main():
    # --- 1. Parse Arguments ---
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_path", help="Absolute path to the wav file")
    args = parser.parse_args()

    audio_file_path = args.audio_path

    # --- 2. Model Configuration ---
    # If distil-small continues to fail, change this to 'base.en' or 'small.en'
    model_size = "distil-small.en" 
    device_type = "cpu"
    compute_type = "int8" 

    try:
        print(f"Loading model '{model_size}'...", file=sys.stderr)
        model = WhisperModel(model_size, device=device_type, compute_type=compute_type)
        
        print(f"Transcribing: {audio_file_path}", file=sys.stderr)

        # --- 3. Transcription (Robust Settings) ---
        segments, info = model.transcribe(
            audio_file_path, 
            beam_size=5,                  # Higher beam size reduces weird loops
            language="en",
            vad_filter=True,              # Aggressively cut out silence
            vad_parameters=dict(min_silence_duration_ms=500), # Tweak silence detection
            condition_on_previous_text=False, # CRITICAL: Prevents loop propagation
            repetition_penalty=1.2,       # Penalizes the model for saying the same thing twice
            no_repeat_ngram_size=3        # Hard blocks repeating 3-word phrases
        )

        # --- 4. Process and Output ---
        transcribed_parts = []
        for segment in segments:
            text = segment.text.strip()
            # Filter out tiny hallucinations (e.g. single symbols)
            if len(text) > 1 or text.isalnum(): 
                transcribed_parts.append(text)
                print(f"  [{segment.start:.2f}s -> {segment.end:.2f}s] {text}", file=sys.stderr)

        final_text = " ".join(transcribed_parts).strip()
        print(final_text)

    except Exception as e:
        print(f"\nFatal Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
