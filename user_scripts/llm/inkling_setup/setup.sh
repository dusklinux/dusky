#!/usr/bin/env bash
set -euo pipefail

# Refuse root execution so source/build files stay owned by the regular user.
if [[ ${EUID} -eq 0 ]]; then
  echo "ERROR: Run this script as your normal user, not as root or via sudo." >&2
  exit 1
fi

# === Environment-Overridable Configuration ===
MODEL_DIR="${INKLING_MODEL_DIR:-/mnt/media/downloads/llm/models/unsloth/Inkling-2-Mini-GGUF}"
BASE_MODEL="${INKLING_BASE_MODEL:-${MODEL_DIR}/Inkling-2-Mini-UD-Q4_K_M.gguf}"
LORA_MODEL="${INKLING_LORA_MODEL:-${MODEL_DIR}/Inkling-2-Mini-Unbound-v2.1-LoRA-r16.gguf}"
SRC_DIR="${INKLING_SRC_DIR:-${HOME}/src/llama.cpp}"
LAUNCHER="${INKLING_LAUNCHER:-/usr/local/bin/inkling-server}"
REPO_URL="${INKLING_REPO_URL:-https://github.com/ggml-org/llama.cpp.git}"

# === Cleanup Trap State ===
TMP_LAUNCHER=""

cleanup() {
  if [[ -n "$TMP_LAUNCHER" && -f "$TMP_LAUNCHER" ]]; then
    rm -f "$TMP_LAUNCHER"
  fi
}
trap cleanup EXIT INT TERM

# === Helper Functions ===
verify_gguf_file() {
  local path="$1"
  local label="$2"

  if [[ ! -r "$path" ]]; then
    if [[ "$path" == /mnt/media || "$path" == /mnt/media/* ]] && ! mountpoint -q /mnt/media; then
      echo "ERROR: ${label} not accessible because /mnt/media is not mounted. Unlock/mount the media drive first." >&2
    else
      echo "ERROR: ${label} not found or not readable:${path}" >&2
    fi
    return 1
  fi

  if [[ ! -s "$path" ]]; then
    echo "ERROR: ${label} is empty (0 bytes):${path}" >&2
    return 1
  fi

  local magic
  magic="$(LC_ALL=C od -An -tx1 -N4 -- "$path" | tr -d '[:space:]')"
  if [[ "$magic" != "47475546" ]]; then
    echo "ERROR: ${label} does not have a valid GGUF header (got 0x${magic:-empty}):${path}" >&2
    return 1
  fi
}

detect_cuda_archs() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    return 0
  fi

  local raw
  raw="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null || true)"
  awk '
    {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0)
      if ($0 ~ /^[0-9]+\.[0-9]+$/) {
        gsub(/\./, "", $0)
        if (!seen[$0]++) {
          out = (out == "" ? $0 : out ";" $0)
        }
      }
    }
    END { if (out != "") print out }
  ' <<< "$raw"
}

sync_repo() {
  mkdir -p "$(dirname "$SRC_DIR")"
  if [[ -d "$SRC_DIR" ]] \
    && git -C "$SRC_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    && [[ "$(git -C "$SRC_DIR" rev-parse --show-toplevel 2>/dev/null || true)" == "$(cd "$SRC_DIR" && pwd -P)" ]]; then
    if git -C "$SRC_DIR" remote set-url origin "$REPO_URL" 2>/dev/null \
      && git -C "$SRC_DIR" fetch --depth 1 --no-tags origin master \
      && git -C "$SRC_DIR" checkout -B master FETCH_HEAD \
      && git -C "$SRC_DIR" reset --hard FETCH_HEAD \
      && git -C "$SRC_DIR" clean -fdx; then
      return 0
    fi
    echo "WARNING: Existing checkout at ${SRC_DIR} could not be updated cleanly; re-cloning." >&2
  fi

  rm -rf "$SRC_DIR"
  git clone --depth 1 --single-branch --branch master --no-tags "$REPO_URL" "$SRC_DIR"
}

# === 1. Preflight Model Verification ===
verify_gguf_file "$BASE_MODEL" "Base model"

if [[ -e "$LORA_MODEL" ]]; then
  verify_gguf_file "$LORA_MODEL" "LoRA adapter"
else
  echo "WARNING: Optional LoRA not found at ${LORA_MODEL}; launcher will attach it automatically if added later." >&2
fi

# === 2. Capture GPU Compute Capability Before Rolling Upgrade ===
CUDA_ARCHS="${INKLING_CUDA_ARCHS:-$(detect_cuda_archs)}"

# === 3. Stage Portable System Launcher ===
TMP_LAUNCHER="$(mktemp)"
cat <<'LAUNCHER_EOF' > "$TMP_LAUNCHER"
#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="${INKLING_MODEL_DIR:-/mnt/media/downloads/llm/models/unsloth/Inkling-2-Mini-GGUF}"
BASE_MODEL="${INKLING_BASE_MODEL:-${MODEL_DIR}/Inkling-2-Mini-UD-Q4_K_M.gguf}"
LORA_MODEL="${INKLING_LORA_MODEL:-${MODEL_DIR}/Inkling-2-Mini-Unbound-v2.1-LoRA-r16.gguf}"
SRC_DIR="${INKLING_SRC_DIR:-${HOME}/src/llama.cpp}"
SERVER_BIN="${INKLING_SERVER_BIN:-${SRC_DIR}/build/bin/llama-server}"
DEFAULT_OT='(blk\.([0-5]|[7-9]|1[0-9]|2[0-3])\.ffn_.*exps|token_embd)=CPU'
OT_EXPR="${INKLING_OT:-$DEFAULT_OT}"

verify_gguf_file() {
  local path="$1"
  local label="$2"

  if [[ ! -r "$path" ]]; then
    if [[ "$path" == /mnt/media || "$path" == /mnt/media/* ]] && ! mountpoint -q /mnt/media; then
      echo "ERROR: ${label} not accessible because /mnt/media is not mounted. Unlock/mount the media drive first." >&2
    else
      echo "ERROR: ${label} not found or not readable:${path}" >&2
    fi
    return 1
  fi

  if [[ ! -s "$path" ]]; then
    echo "ERROR: ${label} is empty (0 bytes):${path}" >&2
    return 1
  fi

  local magic
  magic="$(LC_ALL=C od -An -tx1 -N4 -- "$path" | tr -d '[:space:]')"
  if [[ "$magic" != "47475546" ]]; then
    echo "ERROR: ${label} does not have a valid GGUF header (got 0x${magic:-empty}):${path}" >&2
    return 1
  fi
}

expand_cpu_list() {
  local spec="${1//[[:space:]]/}"
  local IFS=','
  local part start end i
  for part in $spec; do
    if [[ "$part" == *-* ]]; then
      start="${part%-*}"
      end="${part#*-}"
      for ((i = start; i <= end; i++)); do
        printf '%d\n' "$i"
      done
    elif [[ -n "$part" ]]; then
      printf '%d\n' "$part"
    fi
  done
}

build_hex_mask() {
  local -A present=()
  local cpu max_cpu=-1
  for cpu in "$@"; do
    present["$cpu"]=1
    if (( cpu > max_cpu )); then
      max_cpu="$cpu"
    fi
  done

  if (( max_cpu < 0 )); then
    return 1
  fi

  local top_nibble=$(( max_cpu / 4 ))
  local hex="" n base val bit
  local -r digits="0123456789ABCDEF"

  for ((n = top_nibble; n >= 0; n--)); do
    base=$(( n * 4 ))
    val=0
    for ((bit = 0; bit < 4; bit++)); do
      if [[ -n "${present[$((base + bit))]+x}" ]]; then
        val=$(( val | (1 << bit) ))
      fi
    done
    hex+="${digits:val:1}"
  done

  printf '0x%s\n' "$hex"
}

if [[ ! -x "$SERVER_BIN" ]]; then
  echo "ERROR: llama-server binary not found or not executable: ${SERVER_BIN}" >&2
  exit 1
fi

verify_gguf_file "$BASE_MODEL" "Base model"

ARGS=(
  -m "$BASE_MODEL"
)

if [[ -e "$LORA_MODEL" ]]; then
  verify_gguf_file "$LORA_MODEL" "LoRA adapter"
  ARGS+=(--lora "$LORA_MODEL")
else
  echo "WARNING: LoRA not found at ${LORA_MODEL}; launching base model without --lora." >&2
fi

HAS_CUSTOM_OT=0
for arg in "$@"; do
  case "$arg" in
    -ot|--override-tensor|-ot=*|--override-tensor=*)
      HAS_CUSTOM_OT=1
      break
      ;;
  esac
done

ARGS+=(
  -ngl 99
)
if [[ $HAS_CUSTOM_OT -eq 0 && -n "$OT_EXPR" ]]; then
  ARGS+=(-ot "$OT_EXPR")
fi
ARGS+=(
  --no-op-offload
  --load-mode none
  --fit off
  -fa on
  -ctk q8_0 -ctv q8_0
  -c 32768
  -b 512 -ub 64
)

if [[ -r /sys/devices/system/cpu/online ]]; then
  ONLINE_SPEC="$(< /sys/devices/system/cpu/online)"
  mapfile -t ONLINE_CPUS < <(expand_cpu_list "$ONLINE_SPEC")

  declare -A ONLINE_SET=()
  for cpu in "${ONLINE_CPUS[@]}"; do
    ONLINE_SET["$cpu"]=1
  done

  PHYS_CPUS=()
  for cpu in "${ONLINE_CPUS[@]}"; do
    topo_file="/sys/devices/system/cpu/cpu${cpu}/topology/core_cpus_list"
    if [[ -r "$topo_file" ]]; then
      sib_spec="$(< "$topo_file")"
      min_sib="$cpu"
      while IFS= read -r sib; do
        if [[ -n "${ONLINE_SET[$sib]+x}" ]] && (( sib < min_sib )); then
          min_sib="$sib"
        fi
      done < <(expand_cpu_list "$sib_spec")
      if (( cpu == min_sib )); then
        PHYS_CPUS+=("$cpu")
      fi
    else
      PHYS_CPUS+=("$cpu")
    fi
  done

  DECODE_CPUS=()
  if [[ -r /sys/devices/cpu_core/cpus ]]; then
    pcore_spec="$(< /sys/devices/cpu_core/cpus)"
    while IFS= read -r cpu; do
      if [[ -n "${ONLINE_SET[$cpu]+x}" ]]; then
        DECODE_CPUS+=("$cpu")
      fi
    done < <(expand_cpu_list "$pcore_spec")
  fi

  if (( ${#DECODE_CPUS[@]} == 0 )); then
    DECODE_CPUS=("${PHYS_CPUS[@]}")
  fi

  DECODE_THREADS="${#DECODE_CPUS[@]}"
  DECODE_MASK="$(build_hex_mask "${DECODE_CPUS[@]}")"
  PREFILL_THREADS="${#PHYS_CPUS[@]}"
  PREFILL_MASK="$(build_hex_mask "${PHYS_CPUS[@]}")"

  ARGS+=(
    -t "$DECODE_THREADS" -C "$DECODE_MASK" --cpu-strict 1
    -tb "$PREFILL_THREADS" -Cb "$PREFILL_MASK" --cpu-strict-batch 1
  )
else
  FALLBACK_THREADS="$(nproc)"
  ARGS+=(
    -t "$FALLBACK_THREADS"
    -tb "$FALLBACK_THREADS"
  )
fi

ARGS+=(
  -np 1
  --jinja
  --chat-template-kwargs '{"reasoning_effort":"medium"}'
  --temp 0.6 --top-p 0.95
  --host 127.0.0.1 --port 8080
)

exec "$SERVER_BIN" "${ARGS[@]}" "$@"
LAUNCHER_EOF

# === 4. Single Privileged Phase: Arch Rolling Upgrade & Atomic Launcher Install ===
sudo sh -eu -c '
  pacman -Syu --needed --noconfirm base-devel clang openmp cmake git ninja cuda
  install -d -m 0755 "$1"
  install -m 0755 -o root -g root "$2" "$3"
' _ "$(dirname "$LAUNCHER")" "$TMP_LAUNCHER" "$LAUNCHER"
rm -f "$TMP_LAUNCHER"
TMP_LAUNCHER=""

# === 5. Safely Source Arch CUDA Environment ===
if [[ -r /etc/profile.d/cuda.sh ]]; then
  set +euo pipefail
  _defined_append_path=0
  if ! declare -F append_path >/dev/null 2>&1; then
    _defined_append_path=1
    append_path() {
      case ":$PATH:" in
        *:"$1":*) ;;
        *) PATH="${PATH:+$PATH:}$1" ;;
      esac
    }
  fi
  # shellcheck disable=SC1091
  source /etc/profile.d/cuda.sh
  if [[ $_defined_append_path -eq 1 ]]; then
    unset -f append_path
  fi
  unset _defined_append_path
  set -euo pipefail
fi

if command -v nvcc >/dev/null 2>&1; then
  NVCC_BIN="$(command -v nvcc)"
elif [[ -x /opt/cuda/bin/nvcc ]]; then
  NVCC_BIN="/opt/cuda/bin/nvcc"
else
  echo "ERROR: nvcc not found after installing cuda." >&2
  exit 1
fi

CUDA_HOST_CXX="${NVCC_CCBIN:-/usr/bin/g++}"
if [[ "$CUDA_HOST_CXX" != */* ]]; then
  CUDA_HOST_CXX="$(command -v "$CUDA_HOST_CXX" || true)"
fi
if [[ -z "$CUDA_HOST_CXX" || ! -x "$CUDA_HOST_CXX" ]]; then
  echo "ERROR: Resolved CUDA host compiler is not executable: ${CUDA_HOST_CXX:-empty}" >&2
  exit 1
fi
export CUDAHOSTCXX="$CUDA_HOST_CXX"

if [[ -z "$CUDA_ARCHS" ]]; then
  CUDA_ARCHS="$(detect_cuda_archs)"
fi
if [[ -z "$CUDA_ARCHS" ]]; then
  echo "ERROR: Unable to detect an NVIDIA GPU compute capability via nvidia-smi." >&2
  echo "       Ensure the NVIDIA kernel driver is installed and loaded (reboot if newly installed/upgraded), or set INKLING_CUDA_ARCHS." >&2
  exit 1
fi

# === 6. Self-Healing Shallow Sync of Mainline llama.cpp ===
sync_repo

# === 7. Clean Native Build (Clang + OpenMP + CUDA, No Compiler Caches) ===
export CCACHE_DISABLE=1
rm -rf "${SRC_DIR}/build"

cmake -S "$SRC_DIR" -B "${SRC_DIR}/build" -G Ninja \
  -DCMAKE_C_COMPILER=/usr/bin/clang \
  -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
  -DCMAKE_CUDA_COMPILER="$NVCC_BIN" \
  -DCMAKE_CUDA_HOST_COMPILER="$CUDA_HOST_CXX" \
  -DCMAKE_C_COMPILER_LAUNCHER="" \
  -DCMAKE_CXX_COMPILER_LAUNCHER="" \
  -DCMAKE_CUDA_COMPILER_LAUNCHER="" \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCHS" \
  -DGGML_NATIVE=ON \
  -DGGML_OPENMP=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_TOOLS=ON \
  -DLLAMA_BUILD_SERVER=ON \
  -DCMAKE_BUILD_TYPE=Release

cmake --build "${SRC_DIR}/build" -j "$(nproc)" --target llama-server llama-bench

# === 8. Post-Build Linkage & Guarded CUDA Device Verification ===
SERVER_BIN="${SRC_DIR}/build/bin/llama-server"
BENCH_BIN="${SRC_DIR}/build/bin/llama-bench"

echo "=== Verifying built binaries ==="
"$SERVER_BIN" --version

DEVICES_OUT="$("$SERVER_BIN" --list-devices 2>&1 || true)"
echo "$DEVICES_OUT"

echo "=== Setup complete ==="
echo "Git commit : $(git -C "$SRC_DIR" rev-parse --short HEAD)"
echo "CUDA archs : ${CUDA_ARCHS}"
echo "CUDA host  : ${CUDA_HOST_CXX}"
echo "Server bin : ${SERVER_BIN}"
echo "Bench bin  : ${BENCH_BIN}"
echo "Launcher   : ${LAUNCHER}"
echo "Server URL : http://127.0.0.1:8080"

if grep -Eq 'CUDA[0-9]+:' <<< "$DEVICES_OUT"; then
  echo "GPU status : CUDA device ready. Start anytime with: inkling-server"
else
  echo "WARNING: Build succeeded, but no CUDA device was enumerated right now." >&2
  echo "         If pacman upgraded the NVIDIA driver or the kernel module is not loaded yet, reboot and then run: inkling-server" >&2
fi
