import sys
import os
from faster_whisper import WhisperModel

def find_latest_audio_file(audio_dir):
    """
    Scans a directory for files named like 'N_mic.wav', finds the one with the
    highest number N, and returns its full path.

    - All status/log messages are printed to stderr.
    - Returns the full path on success, None on failure.
    """
    latest_file_name = None
    highest_index = -1

    try:
        if not os.path.isdir(audio_dir):
            raise FileNotFoundError(f"The specified directory does not exist: {audio_dir}")

        candidate_files = [f for f in os.listdir(audio_dir) if f.endswith("_mic.wav") and f.split('_')[0].isdigit()]

        if not candidate_files:
            raise FileNotFoundError(f"No correctly formatted audio files (e.g., '1_mic.wav') were found in {audio_dir}")

        for filename in candidate_files:
            try:
                index_part = int(filename.split('_')[0])
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
    Main function to find the latest audio file and transcribe it.
    - All status/log messages are printed to stderr.
    - The final, complete transcription text is printed to stdout.
    """
    # --- 1. Dynamic Audio File Selection ---
    # This logic is restored as per your request.
    audio_dir = "/mnt/zram1/mic/"
    audio_file_path = find_latest_audio_file(audio_dir)

    if not audio_file_path:
        sys.exit(1)

    print(f"Processing audio file: {audio_file_path}", file=sys.stderr)

    # --- 2. Model Configuration ---
    model_size = "small.en"
    device_type = "cpu"
    compute_type = "int8"

    # --- 3. Transcription ---
    try:
        print(f"Loading faster-whisper model '{model_size}'...", file=sys.stderr)
        model = WhisperModel(model_size, device=device_type, compute_type=compute_type)
        print("Model loaded successfully.", file=sys.stderr)

        print(f"Starting transcription...", file=sys.stderr)
        segments, info = model.transcribe(audio_file_path, beam_size=5)
        print(f"Detected language '{info.language}' with probability {info.language_probability}", file=sys.stderr)
        
        # --- 4. Process and Output ---
        transcribed_parts = []
        for segment in segments:
            transcribed_parts.append(segment.text.strip())
            print(f"  Segment: [{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text.strip()}", file=sys.stderr)

        final_text = " ".join(transcribed_parts).strip()
        
        # The final, clean text is printed to STANDARD OUTPUT.
        print(final_text)
        
        print("\nTranscription complete.", file=sys.stderr)

    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
