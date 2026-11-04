#!/usr/bin/env bash
# Build and check dusky-center exclusively on tmpfs (RAM) to prevent disk write amplification.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly PROJECT_DIR
mode="${1:---check}"

CARGO_TARGET_DIR="$(realpath -m -- "${CARGO_TARGET_DIR:-/tmp/dusky-center-target-$UID}")"
export CARGO_TARGET_DIR

case "$CARGO_TARGET_DIR/" in
    "$PROJECT_DIR/"*) printf 'Build output must stay outside the project: %s\n' "$CARGO_TARGET_DIR" >&2; exit 1 ;;
esac

# Verify the existing ancestor before creating output to guarantee tmpfs.
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
    --check) exec cargo check --locked ;;
    --test) exec cargo test --locked ;;
    --clippy) exec cargo clippy --locked --all-targets -- -D warnings ;;
    --release) exec cargo build --release --locked ;;
    *) printf 'Usage: %s [--check|--test|--clippy|--release]\n' "$0" >&2; exit 2 ;;
esac
