import sys
import os

# --- The "Big Hammer" Solution ---
# The NeMo toolkit is very verbose and prints its setup logs directly to
# standard output (stdout), which contaminates our desired transcription text.
#
# This solution solves the problem by temporarily redirecting the entire
# stdout stream to a null device (a "black hole") during the noisy
# import and model loading process. This effectively silences NeMo.
#
# 1. Save the original stdout.
# 2. Point stdout to os.devnull.
# 3. Import and load the model (all its logs are sent to the black hole).
# 4. Restore the original stdout.
# 5. Print the final transcription (this goes to the clean, original stdout).

# Temporarily silence stdout by redirecting it to a null device
original_stdout = sys.stdout
sys.stdout = open(os.devnull, 'w')

# Now, import the noisy library. All its startup messages are discarded.
import nemo.collections.asr as nemo_asr

# Restore stdout so the rest of our script can print normally
sys.stdout.close()
sys.stdout = original_stdout
# -----------------------------------


def find_latest_audio_file(audio_dir):
    """
    Scans a directory for files named like 'N.wav', finds the one with the
    highest number N, and returns its full path.
    """
    latest_file_name = None
    highest_index = -1

    try:
        if not os.path.isdir(audio_dir):
            raise FileNotFoundError(f"The specified directory does not exist: {audio_dir}")

        candidate_files = [f for f in os.listdir(audio_dir) if f.endswith(".wav") and f.split('.')[0].isdigit()]

        if not candidate_files:
            raise FileNotFoundError(f"No correctly formatted audio files (e.g., '1.wav') were found in {audio_dir}")

        for filename in candidate_files:
            try:
                index_part = int(filename.split('.')[0])
                if index_part > highest_index:
                    highest_index = index_part
                    latest_file_name = filename
            except (ValueError, IndexError):
                print(f"Notice: Bypassing malformed filename: {filename}", file=sys.stderr)
                continue

        if latest_file_name is None:
            raise FileNotFoundError("A valid, latest audio file could not be determined from the candidates.")

        return os.path.join(audio_dir, latest_file_name)

    except FileNotFoundError as e:
        print(f"Fatal Error: {e}", file=sys.stderr)
        return None

def main():
    """
    Main function to find the latest audio file and transcribe it using Parakeet.
    """
    audio_dir = "/mnt/zram1/mic/"
    audio_file_path = find_latest_audio_file(audio_dir)

    if not audio_file_path:
        sys.exit(1)

    print(f"Processing audio file: {audio_file_path}", file=sys.stderr)

    model_name = "nvidia/parakeet-tdt-0.6b-v2"

    try:
        print(f"Loading Nemo ASR model '{model_name}'...", file=sys.stderr)
        
        # --- Silence the model loading process ---
        sys.stdout = open(os.devnull, 'w')
        asr_model = nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
        sys.stdout.close()
        sys.stdout = original_stdout
        # ----------------------------------------
        
        print("Model loaded successfully.", file=sys.stderr)

        print(f"Starting transcription...", file=sys.stderr)
        # The `verbose=False` flag here prevents the transcription progress bar
        output = asr_model.transcribe([audio_file_path], verbose=False)
        
        if output and isinstance(output, list) and hasattr(output[0], 'text'):
            final_text = output[0].text.strip()
            # This print() is the ONLY ONE that goes to stdout and will be copied
            print(final_text)
            print(f"  Transcription result: {final_text}", file=sys.stderr)
        else:
            print("Warning: Transcription produced no text.", file=sys.stderr)
            print("")

        print("\nTranscription complete.", file=sys.stderr)

    except Exception as e:
        # Ensure stdout is restored even if an error occurs
        sys.stdout = original_stdout
        print(f"\nAn unexpected error occurred during transcription: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
