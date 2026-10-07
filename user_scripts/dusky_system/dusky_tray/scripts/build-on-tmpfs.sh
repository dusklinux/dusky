#!/usr/bin/env bash
# Build offline on verified RAM storage; copy only the runnable executable here.
set -euo pipefail
readonly PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
mode="${1:---release}"
case "$mode" in
    --check|--test|--clippy) ;;
    --release|--install) profile=release; build_args=(--release) ;;
    --dev) profile=debug; build_args=() ;;
    *) printf 'Usage: %s [--release|--dev|--check|--test|--clippy|--install]\n' "$0" >&2; exit 2 ;;
esac
CARGO_TARGET_DIR="$(realpath -m -- "${CARGO_TARGET_DIR:-/tmp/dusky-tray-target-$UID}")"
export CARGO_TARGET_DIR
case "$CARGO_TARGET_DIR/" in
    "$PROJECT_DIR/"*) printf 'Build output must stay outside the project: %s\n' "$CARGO_TARGET_DIR" >&2; exit 1 ;;
esac
# /tmp is not universally RAM-backed. Verify the existing ancestor before
# creating output, including explicitly supplied target directories.
probe_dir="$CARGO_TARGET_DIR"
while [[ ! -e "$probe_dir" ]]; do probe_dir="$(dirname -- "$probe_dir")"; done
if [[ "$(findmnt -n -o FSTYPE -T "$probe_dir")" != tmpfs ]]; then
    printf 'Build output requires tmpfs: %s. Set CARGO_TARGET_DIR to a tmpfs path.\n' "$CARGO_TARGET_DIR" >&2
    exit 1
fi
export CARGO_BUILD_BUILD_DIR="$CARGO_TARGET_DIR"
export TMPDIR="$CARGO_TARGET_DIR/tmp"
mkdir -p -- "$TMPDIR"
cd -- "$PROJECT_DIR"
case "$mode" in
    --check) exec cargo check --locked --offline ;;
    --test) exec cargo test --locked --offline ;;
    --clippy) exec cargo clippy --locked --offline --all-targets -- -D warnings ;;
esac
cargo build --locked --offline "${build_args[@]}"
mkdir -p -- "$PROJECT_DIR/bin"
install -m 0755 -- "$CARGO_TARGET_DIR/$profile/dusky-tray" "$PROJECT_DIR/bin/dusky-tray"
printf 'Built: %s/bin/dusky-tray\n' "$PROJECT_DIR"
if [[ "$mode" == --install ]]; then
    mkdir -p -- "$HOME/.local/bin"
    install -m 0755 -- "$PROJECT_DIR/bin/dusky-tray" "$HOME/.local/bin/dusky-tray"
fi
