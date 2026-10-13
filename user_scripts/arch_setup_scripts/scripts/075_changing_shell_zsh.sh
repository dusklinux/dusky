#!/usr/bin/env bash
#d: Switch the default shell to Zsh

set -euo pipefail

# Each message resets its own color; leave signals at their default dispositions.
BLUE='' GREEN='' RESET='' RED='' ERR_RESET=''
if [[ -t 1 ]]; then
  BLUE=$'\e[34m' GREEN=$'\e[32m' RESET=$'\e[0m'
fi
if [[ -t 2 ]]; then
  RED=$'\e[31m' ERR_RESET=$'\e[0m'
fi

log_info() { printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$1"; }
log_success() { printf '%s[SUCCESS]%s %s\n' "$GREEN" "$RESET" "$1"; }
log_err() { printf '%s[ERROR]%s %s\n' "$RED" "$ERR_RESET" "$1" >&2; }

# Resolve the account through NSS, not the potentially stale USER variable.
# Only a root invocation uses sudo's original user; direct root runs target root.
TARGET_KEY=$EUID
if (( EUID == 0 )) && [[ -n ${SUDO_USER:-} ]]; then
  TARGET_KEY=$SUDO_USER
fi

if ! PASSWD_ENTRY=$(getent -- passwd "$TARGET_KEY"); then
  log_err "Cannot resolve user account: $TARGET_KEY"
  exit 1
fi
IFS=: read -r TARGET_USER _ _ _ _ _ CURRENT_SHELL <<< "$PASSWD_ENTRY"
if [[ -z $TARGET_USER ]]; then
  log_err "Empty passwd entry for: $TARGET_KEY"
  exit 1
fi

if ! TARGET_PATH=$(command -v zsh); then
  log_err "Zsh is not installed."
  printf '%s\n' '       Run: sudo pacman -S zsh to install it.' >&2
  exit 1
fi
if [[ $TARGET_PATH != /* || ! -f $TARGET_PATH || ! -x $TARGET_PATH ]]; then
  log_err "Zsh must resolve to an absolute executable file: $TARGET_PATH"
  exit 1
fi

if ! grep -Fxq -- "$TARGET_PATH" /etc/shells; then
  log_err "Cannot confirm '$TARGET_PATH' is listed in /etc/shells."
  exit 1
fi

# /bin/zsh and /usr/bin/zsh may refer to the same executable.
if [[ $CURRENT_SHELL == "$TARGET_PATH" || $CURRENT_SHELL -ef $TARGET_PATH ]]; then
  log_success "User '$TARGET_USER' is already using Zsh ($CURRENT_SHELL)."
  exit 0
fi

log_info "Current shell: $CURRENT_SHELL"
log_info "Target shell:  $TARGET_PATH"
log_info "Targeting user: $TARGET_USER"

# Non-root callers authenticate through chsh's normal PAM prompt.
if ! chsh --shell "$TARGET_PATH" -- "$TARGET_USER"; then
  log_err "Failed to change shell for '$TARGET_USER'."
  exit 1
fi

# Confirm the saved account entry before reporting success.
if ! PASSWD_ENTRY=$(getent -- passwd "$TARGET_USER"); then
  log_err "Cannot verify the saved shell for '$TARGET_USER'."
  exit 1
fi
IFS=: read -r _ _ _ _ _ _ CURRENT_SHELL <<< "$PASSWD_ENTRY"
if [[ $CURRENT_SHELL != "$TARGET_PATH" && ! $CURRENT_SHELL -ef $TARGET_PATH ]]; then
  log_err "Saved shell verification failed for '$TARGET_USER': $CURRENT_SHELL"
  exit 1
fi

log_success "Shell changed successfully for '$TARGET_USER'. Log out and back in to use Zsh."
