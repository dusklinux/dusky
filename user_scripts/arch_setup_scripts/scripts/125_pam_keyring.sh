#!/usr/bin/env bash
#d: Set up GNOME Keyring for console login

set -euo pipefail

# Both installers share the same editor and preserve the existing PAM stack.
SCRIPT_PATH=${BASH_SOURCE[0]}
if [[ $SCRIPT_PATH == */* ]]; then
    SCRIPT_DIR=$(cd -- "${SCRIPT_PATH%/*}" && pwd -P)
else
    SCRIPT_DIR=$PWD
fi
exec python3 "$SCRIPT_DIR/127_pam_keyring_greetd.py" --login-only "$@"
