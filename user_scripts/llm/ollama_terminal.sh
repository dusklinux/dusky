#!/usr/bin/env bash
# ==============================================================================
#  Ollama Terminal Chat (v4.2 - Auto-Install Enabled)
#  Description: Hardened chat loop with reliable streaming and proper cleanup.
# ==============================================================================

# --- 1. Strict Mode ---
set -euo pipefail

# --- 2. Configuration ---
readonly DEFAULT_MODEL_CONFIG="qwen3.5:0.8b"
readonly OLLAMA_URL="http://localhost:11434"
readonly CONFIG_DIR="${HOME}/.config/ollama_dusky/ollama-terminal-chat"
readonly STATE_FILE="${CONFIG_DIR}/last_model"
readonly MAX_CLIPBOARD_LEN=4000

# --- 3. Formatting (ANSI) ---
readonly BOLD=$'\033[1m'
readonly DIM=$'\033[2m'
readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly BLUE=$'\033[0;34m'
readonly YELLOW=$'\033[0;33m'
readonly CYAN=$'\033[0;36m'
readonly RESET=$'\033[0m'

# --- 4. Runtime State ---
HISTORY_FILE=""
PAYLOAD_FILE=""
MODEL=""

# --- 5. Cleanup (Trap-Safe) ---
cleanup() {
    [[ -n "${HISTORY_FILE:-}" && -f "$HISTORY_FILE" ]] && rm -f "$HISTORY_FILE"
    [[ -n "${PAYLOAD_FILE:-}" && -f "$PAYLOAD_FILE" ]] && rm -f "$PAYLOAD_FILE"
    # Restore cursor visibility
    printf '\033[?25h' 2>/dev/null || true
}
trap cleanup EXIT INT TERM HUP

# --- 6. Core Utility Functions ---
log_info() { printf '%b[INFO]%b %s\n' "$BLUE" "$RESET" "$1"; }
log_warn() { printf '%b[WARN]%b %s\n' "$YELLOW" "$RESET" "$1" >&2; }
log_err()  { printf '%b[ERROR]%b %s\n' "$RED" "$RESET" "$1" >&2; }

die() {
    log_err "$1"
    exit "${2:-1}"
}

# --- 7. Dependency & Service Checks ---
check_dependencies() {
    local -a missing=()
    local cmd
    
    # Check for all required tools
    for cmd in jq curl ollama; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done

    # If any are missing, attempt auto-install via pacman
    if (( ${#missing[@]} > 0 )); then
        log_info "Missing dependencies detected: ${missing[*]}. Attempting auto-install..."
        
        # Sudo will prompt user for password if necessary
        if sudo pacman -S --needed "${missing[@]}"; then
            log_info "Dependencies installed successfully."
        else
            die "Failed to auto-install dependencies. Please run: sudo pacman -S ${missing[*]}"
        fi
    fi
}

ensure_ollama_running() {
    # Use systemctl for reliable service state check
    if ! systemctl is-active --quiet ollama.service; then
        log_info "Starting Ollama service..."
        if ! sudo systemctl start ollama.service; then
            die "Failed to start ollama.service. Check 'systemctl status ollama.service'."
        fi

        # Wait for API endpoint to become responsive
        local retries=0
        log_info "Waiting for Ollama API..."
        while ! curl -sf --max-time 2 -o /dev/null "${OLLAMA_URL}/api/tags"; do
            sleep 1
            retries=$((retries + 1))  # Safe increment, avoids ((retries++)) exit code bug
            if (( retries >= 15 )); then
                die "Ollama API at ${OLLAMA_URL} did not respond within 15 seconds."
            fi
        done
        log_info "Ollama service is ready."
    fi
}

# --- 8. Model Management ---
get_models() {
    curl -sf --max-time 5 "${OLLAMA_URL}/api/tags" | jq -r '.models[].name // empty' | sort
}

model_exists() {
    local target="$1"
    local model
    while IFS= read -r model; do
        [[ "$model" == "$target" ]] && return 0
    done < <(get_models)
    return 1
}

save_state() {
    printf '%s\n' "$1" > "$STATE_FILE"
}

select_model() {
    local selected=""
    local -a models_array

    mapfile -t models_array < <(get_models)

    if (( ${#models_array[@]} == 0 )); then
        log_err "No models available. Pull one with: ollama pull ${DEFAULT_MODEL_CONFIG}"
        return 1
    fi

    if command -v fzf &>/dev/null; then
        # fzf returns non-zero on cancel; allow it
        selected=$(printf '%s\n' "${models_array[@]}" | fzf --height=40% --layout=reverse --border --prompt="Model: ") || true
    else
        PS3="Select model (number): "
        select opt in "${models_array[@]}"; do
            [[ -n "${opt:-}" ]] && { selected="$opt"; break; }
        done
    fi

    if [[ -n "$selected" ]]; then
        MODEL="$selected"
        save_state "$MODEL"
        printf '%b>> Switched to model: %s%b\n' "$GREEN" "$MODEL" "$RESET"
        return 0
    fi
    return 1
}

# --- 9. Conversation History ---
update_history() {
    local role="$1"
    local content="$2"

    # Skip effectively empty content
    [[ -z "${content//[[:space:]]/}" ]] && return 0

    local temp_json
    temp_json=$(mktemp) || { log_warn "mktemp failed in update_history"; return 1; }

    if jq --arg r "$role" --arg c "$content" \
          '. += [{role: $r, content: $c}]' "$HISTORY_FILE" > "$temp_json" 2>/dev/null; then
        mv -f "$temp_json" "$HISTORY_FILE"
    else
        rm -f "$temp_json"
        log_warn "Failed to update conversation history."
        return 1
    fi
}

# --- 10. UI ---
print_header() {
    clear
    printf '%b========================================%b\n' "$CYAN" "$RESET"
    printf ' %bOllama Terminal Chat%b %b(v4.2)%b\n' "$BOLD" "$RESET" "$DIM" "$RESET"
    printf ' %bModel:%b %s\n' "$BLUE" "$RESET" "$MODEL"
    printf ' %bCommands:%b /model, /clear, /exit, /help\n' "$DIM" "$RESET"
    printf '%b========================================%b\n\n' "$CYAN" "$RESET"
}

print_help() {
    printf '%bAvailable Commands:%b\n' "$BOLD" "$RESET"
    printf '  /model   - Switch to a different model\n'
    printf '  /clear   - Clear conversation history\n'
    printf '  /exit    - Exit the chat (or Ctrl+D)\n'
    printf '  /help    - Show this message\n\n'
}

# ==============================================================================
# --- MAIN EXECUTION ---
# ==============================================================================

# Pre-flight checks
check_dependencies
ensure_ollama_running
[[ -d "$CONFIG_DIR" ]] || mkdir -p "$CONFIG_DIR"

# Initialize temp files (registered for cleanup via trap)
HISTORY_FILE=$(mktemp --tmpdir ollama_hist.XXXXXX.json)
PAYLOAD_FILE=$(mktemp --tmpdir ollama_payload.XXXXXX.json)
printf '%s\n' "[]" > "$HISTORY_FILE"

# Load persisted model or use default
if [[ -f "$STATE_FILE" && -s "$STATE_FILE" ]]; then
    MODEL=$(<"$STATE_FILE")
else
    MODEL="$DEFAULT_MODEL_CONFIG"
fi

# Parse command-line arguments
while (( $# > 0 )); do
    case "$1" in
        -m|--model)
            [[ -z "${2:-}" ]] && die "Option $1 requires a model name."
            MODEL="$2"
            shift 2
            ;;
        -s|--select)
            select_model || true
            shift
            ;;
        -h|--help)
            printf 'Usage: %s [OPTIONS]\n\n' "${0##*/}"
            printf 'Options:\n'
            printf '  -m, --model <name>  Use specified model\n'
            printf '  -s, --select        Interactively select a model\n'
            printf '  -h, --help          Show this help\n'
            exit 0
            ;;
        *)
            log_warn "Unknown option: $1"
            shift
            ;;
    esac
done

# Ensure selected model is available locally
if ! model_exists "$MODEL"; then
    log_info "Model '${MODEL}' not found locally. Pulling..."
    if ! ollama pull "$MODEL"; then
        die "Failed to pull model '${MODEL}'."
    fi
fi

print_header

# ==============================================================================
# --- MAIN CHAT LOOP ---
# ==============================================================================
while true; do
    user_input=""

    # 1. Acquire user input (Manual entry only)
    printf '%bYou:%b ' "$BOLD" "$RESET"
    if ! IFS= read -e -r user_input; then
        # EOF (Ctrl+D)
        printf '\n'
        break
    fi

    # 2. Handle slash commands
    case "$user_input" in
        /exit|/quit|/q)
            break
            ;;
        /clear)
            printf '%s\n' "[]" > "$HISTORY_FILE"
            print_header
            continue
            ;;
        /model)
            if select_model; then
                print_header
            fi
            continue
            ;;
        /help|/h|\?)
            print_help
            continue
            ;;
        "")
            continue
            ;;
    esac

    # 3. Append user message to history
    update_history "user" "$user_input"

    # 4. Build API payload
    jq -n \
       --arg model "$MODEL" \
       --slurpfile msgs "$HISTORY_FILE" \
       '{model: $model, messages: $msgs[0], stream: true}' > "$PAYLOAD_FILE"

    printf '\n%b%bAI:%b ' "$CYAN" "$BOLD" "$RESET"

    # 5. Stream response (unbuffered)
    full_response=""
    stream_error=""

    while IFS= read -r line; do
        # Skip empty keep-alive lines
        [[ -z "$line" ]] && continue

        # Fast path: extract content using here-string (no subshell for echo)
        chunk=$(jq -j '.message.content // empty' <<< "$line" 2>/dev/null) || true

        if [[ -n "$chunk" ]]; then
            printf '%s' "$chunk"
            full_response+="$chunk"
        else
            # Slow path: check for error (only if no content)
            api_err=$(jq -r '.error // empty' <<< "$line" 2>/dev/null) || true
            if [[ -n "$api_err" ]]; then
                stream_error="$api_err"
                break
            fi
        fi
    done < <(curl -sS -N --fail-with-body \
                  -X POST "${OLLAMA_URL}/api/chat" \
                  -H "Content-Type: application/json" \
                  -d @"$PAYLOAD_FILE" 2>&1)

    printf '\n'

    # 6. Handle errors
    if [[ -n "$stream_error" ]]; then
        printf '%b[API Error: %s]%b\n' "$RED" "$stream_error" "$RESET"
    fi

    # 7. Append assistant response to history
    if [[ -n "$full_response" ]]; then
        update_history "assistant" "$full_response"
    fi

    printf '\n'
done

printf '%b👋 Goodbye!%b\n' "$DIM" "$RESET"
exit 0
