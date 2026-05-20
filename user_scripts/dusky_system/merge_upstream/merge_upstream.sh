#!/usr/bin/env bash
set -euo pipefail

GIT_DIR="${HOME}/dusky"
WORK_TREE="${HOME}"
UPSTREAM_REMOTE="dusky-upstream"
UPSTREAM_URL="https://github.com/dusklinux/dusky"
BRANCH="main"

C_RESET='\033[0m'
C_INFO='\033[1;34m'
C_SUCCESS='\033[1;32m'
C_WARN='\033[1;33m'
C_ERROR='\033[1;31m'

log_info() { printf "${C_INFO}[INFO]${C_RESET} %s\n" "$1"; }
log_warn() { printf "${C_WARN}[WARN]${C_RESET} %s\n" "$1"; }
log_success() { printf "${C_SUCCESS}[OK]${C_RESET} %s\n" "$1"; }
log_error() { printf "${C_ERROR}[ERR]${C_RESET} %s\n" "$1"; }

if ! command -v git &>/dev/null; then
    log_error "git is not installed."
    exit 1
fi

if [ ! -d "$GIT_DIR" ]; then
    log_error "Bare repo not found at $GIT_DIR. Have you deployed the dotfiles?"
    exit 1
fi

GIT="git --git-dir=$GIT_DIR --work-tree=$WORK_TREE"

if ! $GIT remote get-url "$UPSTREAM_REMOTE" &>/dev/null; then
    log_info "Adding upstream remote: $UPSTREAM_URL"
    $GIT remote add -f "$UPSTREAM_REMOTE" "$UPSTREAM_URL"
    log_success "Upstream remote added."
else
    log_info "Upstream remote already exists, fetching..."
    $GIT fetch "$UPSTREAM_REMOTE" "$BRANCH"
fi

BEHIND=$($GIT rev-list --count HEAD.."$UPSTREAM_REMOTE/$BRANCH" 2>/dev/null || echo "0")
AHEAD=$($GIT rev-list --count "$UPSTREAM_REMOTE/$BRANCH"..HEAD 2>/dev/null || echo "0")

echo ""
log_info "Fork status relative to upstream:"
echo "  Behind: $BEHIND commits"
echo "  Ahead:  $AHEAD commits"
echo ""

if [ "$BEHIND" -eq 0 ]; then
    log_success "Your fork is up to date with upstream."
    exit 0
fi

log_warn "Your fork is $BEHIND commit(s) behind upstream."
echo ""
echo "Do you want to merge upstream changes into your fork?"
echo "  1) Merge and commit (auto-commit with merge message)"
echo "  2) Show diff first (preview changes)"
echo "  3) Cancel"
read -rp "Choose [1-3]: " choice

case "$choice" in
    1)
        log_info "Merging upstream/$BRANCH..."
        if $GIT merge --no-edit "$UPSTREAM_REMOTE/$BRANCH"; then
            log_success "Merge successful! Your fork now includes upstream changes."
            log_warn "Run the ORCHESTRA or update script to sync new configs."
        else
            log_error "Merge conflict detected. Resolve conflicts, then run:"
            echo "  git --git-dir=\$HOME/dusky --work-tree=\$HOME merge --continue"
            echo "  Or to abort: git --git-dir=\$HOME/dusky --work-tree=\$HOME merge --abort"
        fi
        ;;
    2)
        $GIT diff HEAD.."$UPSTREAM_REMOTE/$BRANCH" | less
        echo ""
        read -rp "Proceed with merge? [y/N]: " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            log_info "Merging upstream/$BRANCH..."
            if $GIT merge --no-edit "$UPSTREAM_REMOTE/$BRANCH"; then
                log_success "Merge successful!"
            else
                log_error "Merge conflict detected. See above."
            fi
        else
            log_info "Cancelled."
        fi
        ;;
    *)
        log_info "Cancelled."
        exit 0
        ;;
esac
