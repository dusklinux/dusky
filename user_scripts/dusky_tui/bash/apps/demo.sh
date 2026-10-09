#!/usr/bin/env bash
set -Eeuo pipefail

# Resolve relative to this launcher; independent of username and working directory.
BASH_TUI_ROOT=${BASH_SOURCE[0]}
if [[ $BASH_TUI_ROOT == */* ]]; then BASH_TUI_ROOT=${BASH_TUI_ROOT%/*}; else BASH_TUI_ROOT=.; fi
BASH_TUI_ROOT=$(cd -- "$BASH_TUI_ROOT/.." && pwd -P)
# shellcheck source=../frontend/ui.sh
source "$BASH_TUI_ROOT/frontend/ui.sh"
# shellcheck source=../engines/kv.sh
source "$BASH_TUI_ROOT/engines/${DUSKY_DEMO_ENGINE:-kv}.sh"
# shellcheck source=../schemas/demo.sh
source "$BASH_TUI_ROOT/schemas/demo.sh"

tui_main "$@"
