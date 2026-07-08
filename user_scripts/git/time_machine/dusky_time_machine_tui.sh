#!/usr/bin/env bash
# =============================================================================
# Dusky Git Time Machine (Platinum Edition - Architecture v9.2 - Ephemeral Zenith)
# Environment: Bash 5.3+, FZF 0.73+, Arch Linux
# Mechanisms: Self-Relocating Ephemeral RAM Execution, Unit Separator (\x1f) indexing,
#             Calculated Byte-Parity Column Alignment, Dynamic ANSI Stripping,
#             Automated Stash-and-Pop Safety Protocols, No-Ellipsis Truncation.
# =============================================================================


# =============================================================================
# 0. The Grandfather Paradox Bypass: Ephemeral Self-Relocation
# =============================================================================
_dusky_volatile_shift() {
    local -r current_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)/$(basename "${BASH_SOURCE[0]}")"
    local target_dir="/tmp"
    local -r zram_dir="/mnt/zram1"

    # Prioritize ZRAM, fallback to /tmp. Includes a strict write-test.
    if [[ -d "$zram_dir" && -w "$zram_dir" ]]; then
        if touch "${zram_dir}/.dusky_write_test" 2>/dev/null; then
            target_dir="$zram_dir"
            rm -f "${zram_dir}/.dusky_write_test"
        fi
    fi

    # If we are already running inside the volatile zone, bypass relocation and proceed.
    if [[ "$current_path" == "${target_dir}/_dusky_tm_run_"* ]]; then
        return 0
    fi

    # We are in the volatile timeline (Git work tree). Clone to RAM and jump.
    local -r volatile_path="${target_dir}/_dusky_tm_run_${$}_${RANDOM}.sh"
    
    cp "$current_path" "$volatile_path"
    chmod +x "$volatile_path"
    
    # 'exec' replaces the current Bash process entirely. The script in the Git tree
    # is abandoned, and execution seamlessly continues from the volatile RAM copy.
    exec "$volatile_path" "$@"
}

# Execute the temporal shift before ANY Git variables are set or logic is parsed.
_dusky_volatile_shift "$@"

# =============================================================================
# 1. Global Git Bare Repository Overrides & State Management
# =============================================================================
export GIT_DIR="$HOME/dusky/"
export GIT_WORK_TREE="$HOME"

# Prevent interactive prompts from hijacking FZF's subshells
export GIT_PAGER=cat
export GIT_TERMINAL_PROMPT=0
export GIT_OPTIONAL_LOCKS=0

# Lock the session PID to ensure stash state files never collide
export DUSKY_SESSION_ID="$$"

# Guarantee UTF-8 character width mapping for Awk length() calculations
export LC_ALL=en_US.UTF-8

# Purge global FZF options to ensure pristine rendering
unset FZF_DEFAULT_OPTS

# CRITICAL SAFETY: Unified Janitorial Trap
_dusky_cleanup() {
    # 1. Stash Pop Safety (Protects user's uncommitted work)
    if [[ -f "/tmp/dusky_time_machine_stash_${DUSKY_SESSION_ID}" ]]; then
        _dusky_git_return >/dev/null 2>&1
    fi
    rm -f "/tmp/dusky_time_machine_stash_${DUSKY_SESSION_ID}" 2>/dev/null || true

    # 2. Ephemeral Instance Self-Destruct (Cleans up the RAM clone)
    local -r current_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)/$(basename "${BASH_SOURCE[0]}")"
    if [[ "$current_path" == *"/_dusky_tm_run_"* ]]; then
        rm -f "$current_path" 2>/dev/null || true
    fi
}
# Trap EXIT fires reliably in Bash 5+ for normal exits, Ctrl+C (SIGINT), and errors.
trap _dusky_cleanup EXIT

# =============================================================================
# 2. Native Bash Functions for FZF Execution Payloads
# =============================================================================

_dusky_git_help() {
    clear
    printf "\n\n  \033[1;38;5;81m󰏖 Dusky Time Machine - Keyboard Shortcuts\033[0m\n"
    printf "  \033[38;5;238m──────────────────────────────────────────────\033[0m\n"
    printf "  \033[1;33m[ENTER]\033[0m          Time Travel (Force Checkout selected commit)\n"
    printf "  \033[1;33m[DOUBLE-CLICK]\033[0m   Time Travel via Mouse\n"
    printf "  \033[1;33m[CTRL-R]\033[0m         Return to Present (Force Checkout default branch)\n"
    printf "  \033[1;33m[CTRL-W]\033[0m         Wipe Changes (Hard Reset to current HEAD)\n"
    printf "  \033[1;33m[ALT-C]\033[0m          Copy current Commit Hash to Clipboard\n"
    printf "  \033[1;33m[F1 / CTRL-O]\033[0m    Show this Help Menu\n"
    printf "  \033[1;33m[ESC]\033[0m            Exit Time Machine\n\n"
    printf "  \033[38;5;242mPress any key to return...\033[0m"
    
    # Read keypress and instantly drain the input buffer of any lingering escape sequences
    read -rsn1 < /dev/tty
    while read -rsn1 -t 0.01 < /dev/tty; do :; done
}
export -f _dusky_git_help

_dusky_git_list() {
    # The --all flag keeps "future" commits visible while in the past.
    # We now fetch BOTH %cd (Date) and %ar (Relative Time)
    git log --all --graph --color=always \
        --format="%x1f%h%x1f%cd%x1f%an%x1f%ar%x1f%C(auto)%d%x1f%s" \
        --date=format:"%m/%d" | \
    awk -v FS=$'\x1f' '
        # Function to completely strip ANSI color codes for true-length math
        function vlen(s) {
            c = s
            gsub(/\033\[[0-9;]*[a-zA-Z]/, "", c)
            return length(c)
        }
        
        {
            if (NF == 1) {
                # Pure graph connection line handling
                graph = $1
                pad_len = 63 - vlen(graph)
                if (pad_len < 0) pad_len = 0
                pad = sprintf("%*s", pad_len, "")
                
                # Leaves the right-side columns open to prevent spreadsheet-grid look
                printf "\x1f \033[38;5;242m      \033[0m \033[38;5;238m│\033[0m %s%s \033[38;5;238m│\033[0m\n", graph, pad
            } else {
                # Commit data extraction mapped to accurate fields
                graph = $1
                hash = $2
                date = $3
                author = $4
                time_str = $5
                refs = $6
                msg = $7
                
                # Strip " ago" from the relative time for a cleaner UI
                gsub(/ ago/, "", time_str)
                if (length(time_str) > 12) time_str = substr(time_str, 1, 12)
                
                if (length(author) > 7) author = substr(author, 1, 7)
                gsub(/\|/, "│", msg)
                if (length(refs) > 0) refs = refs " "
                
                # Math: Calculate available space inside the 55-character boundary
                base_vlen = vlen(graph) + vlen(refs)
                max_msg = 63 - base_vlen
                if (max_msg < 1) max_msg = 1 
                
                # Truncate message BEFORE applying any color codes to prevent ANSI breaking
                if (length(msg) > max_msg) {
                    msg = substr(msg, 1, max_msg)
                }
                
                # Assemble the formatted middle block
                mid = graph refs "\033[38;5;253m" msg "\033[0m"
                mid_vlen = base_vlen + length(msg)
                
                # Dynamic Padding to hit EXACTLY 55 characters
                pad_len = 63 - mid_vlen
                if (pad_len < 0) pad_len = 0
                pad = sprintf("%*s", pad_len, "")
                
                # Final Printout (Green Date | Grey Borders | Coral Author | Cyan Time)
                printf "%s\x1f \033[1;38;5;114m%-6s\033[0m \033[38;5;238m│\033[0m %s%s \033[38;5;238m│\033[0m \033[1;38;5;203m%-7s\033[0m \033[38;5;238m│\033[0m \033[1;38;5;81m%-12s\033[0m\n", hash, date, mid, pad, author, time_str
            }
        }
    '
}
export -f _dusky_git_list

_dusky_git_preview() {
    local -r hash="$1"
    
    # Intercept pure graph lines and show a stylized ghost pane
    if [[ -z "$hash" || "$hash" == " " ]]; then
        printf "\n\n  \033[1;38;5;242m╭────────────────────────────────────────╮\033[0m"
        printf "\n  \033[1;38;5;242m│\033[0m \033[3;38;5;238mGraph connection line. No commit here.\033[0m \033[1;38;5;242m│\033[0m"
        printf "\n  \033[1;38;5;242m╰────────────────────────────────────────╯\033[0m\n"
        exit 0
    fi

    # Fallback safety if delta is ever uninstalled
    if command -v delta >/dev/null 2>&1; then
        git show "$hash" | delta --side-by-side --width="${FZF_PREVIEW_COLUMNS:-120}" --paging=never
    else
        git show --color=always "$hash"
    fi
}
export -f _dusky_git_preview

_dusky_git_checkout() {
    # SENSORY DEPRIVATION: Kill I/O streams so FZF never freezes on background errors
    exec < /dev/null > /dev/null 2>&1
    local -r hash="$1"
    [[ -z "$hash" ]] && exit 0
    
    # STASH SHIELD: Only stash ONCE when leaving the present.
    if [[ ! -f "/tmp/dusky_time_machine_stash_${DUSKY_SESSION_ID}" ]]; then
        if ! git diff-index --quiet HEAD --; then
            # Secure stash (Tracked files only, protects bare-repo Home Directory)
            if git stash push --quiet -m "DUSKY_AUTO_STASH_${DUSKY_SESSION_ID}"; then
                echo "STASHED" > "/tmp/dusky_time_machine_stash_${DUSKY_SESSION_ID}"
            else
                # CRITICAL FAIL-SAFE: If stash fails (e.g. index lock), abort immediately. 
                # Refuse to time-travel rather than risk wiping the user's current changes.
                exit 1
            fi
        else
            echo "CLEAN" > "/tmp/dusky_time_machine_stash_${DUSKY_SESSION_ID}"
        fi
    fi
    
    # Force checkout allows jumping cleanly regardless of detached head states
    git checkout --force "$hash" -- || true
}
export -f _dusky_git_checkout

_dusky_git_return() {
    exec < /dev/null > /dev/null 2>&1
    local main_branch
    main_branch=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
    
    # Detached HEAD fallback detection logic
    if [[ -z "$main_branch" ]]; then
        for b in main master; do
            if git show-ref --verify --quiet "refs/heads/$b"; then
                main_branch="$b"
                break
            fi
        done
    fi
    
    # Guard to prevent returning to void if somehow no branches exist
    if [[ -n "$main_branch" ]]; then
        git checkout --force "$main_branch" -- || true
        
        # RESTORE SHIELD: Auto-pop exactly what was stashed in this specific session
        if [[ -f "/tmp/dusky_time_machine_stash_${DUSKY_SESSION_ID}" ]]; then
            local status
            status=$(cat "/tmp/dusky_time_machine_stash_${DUSKY_SESSION_ID}")
            if [[ "$status" == "STASHED" ]]; then
                # Safe pop: If merge conflict occurs, aborts silently and keeps stash safe
                git stash pop --quiet || true
            fi
            rm -f "/tmp/dusky_time_machine_stash_${DUSKY_SESSION_ID}"
        fi
    fi
}
export -f _dusky_git_return

_dusky_git_restore() {
    exec < /dev/null > /dev/null 2>&1
    git reset --hard HEAD || true
}
export -f _dusky_git_restore

_dusky_git_copy() {
    exec < /dev/null > /dev/null 2>&1
    local -r hash="$1"
    [[ -z "$hash" ]] && exit 0
    if command -v wl-copy >/dev/null 2>&1; then
        printf "%s" "$hash" | wl-copy || true
    fi
}
export -f _dusky_git_copy

# =============================================================================
# 3. Main Engine Execution
# =============================================================================

main() {
    if ! command -v fzf >/dev/null 2>&1; then
        printf "\n\e[31m✖ Error:\e[0m 'fzf' is not installed.\n\n" >&2
        exit 1
    fi

    # Mathematically Aligned Header (6 | 55 | 15 | 12)
    local -r visual_header=$(printf " \033[1;37m%-6s\033[0m \033[38;5;238m│\033[0m \033[1;37m%-63s\033[0m \033[38;5;238m│\033[0m \033[1;37m%-7s\033[0m \033[38;5;238m│\033[0m \033[1;37m%-12s\033[0m" "DATE" "GRAPH / REFS / MESSAGE" "AUTHOR" "TIME AGO")

    # Determine current HEAD and find its line number in the git log list
    local -a fzf_args=()
    local current_head
    current_head=$(git rev-parse --short HEAD 2>/dev/null || true)
    if [[ -n "$current_head" ]]; then
        local line_num
        line_num=$(_dusky_git_list | grep -n "^${current_head}" | head -n 1 | cut -d: -f1)
        if [[ -n "$line_num" ]]; then
            fzf_args+=("--bind=start:pos(${line_num})")
        fi
    fi

    # Launch FZF subprocess mapping
    _dusky_git_list | fzf --ansi \
        --sync \
        "${fzf_args[@]}" \
        --with-shell="bash -c" \
        --delimiter=$'\x1f' \
        --with-nth=2 \
        --tiebreak=index \
        --no-sort \
        --no-hscroll \
        --ellipsis='' \
        --prompt=" :: Time Machine ❯ " \
        --pointer=">" \
        --marker="✓" \
        --layout=reverse \
        --border=rounded \
        --border-label=" 󰏖 Dusky Time Machine [F1 / Ctrl-O: Help] " \
        --border-label-pos=3 \
        --info=hidden \
        --header="$visual_header" \
        --header-first \
        --bind="enter:execute-silent(_dusky_git_checkout {1})+transform-prompt( [ -n \"{1}\" ] && echo \" :: Traveled to {1} ❯ \" || echo \" :: Time Machine ❯ \" )+reload-sync(_dusky_git_list)" \
        --bind="double-click:execute-silent(_dusky_git_checkout {1})+transform-prompt( [ -n \"{1}\" ] && echo \" :: Traveled to {1} ❯ \" || echo \" :: Time Machine ❯ \" )+reload-sync(_dusky_git_list)" \
        --bind="ctrl-r:execute-silent(_dusky_git_return)+change-prompt( :: Returned to Present ❯ )+reload-sync(_dusky_git_list)" \
        --bind="ctrl-w:execute-silent(_dusky_git_restore)+change-prompt( :: Restored (Hard Reset) ❯ )+reload-sync(_dusky_git_list)" \
        --bind="alt-c:execute-silent(_dusky_git_copy {1})+transform-prompt( [ -n \"{1}\" ] && echo \" :: Copied {1} ❯ \" || echo \" :: Time Machine ❯ \" )" \
        --bind="f1:execute(_dusky_git_help)" \
        --bind="ctrl-o:execute(_dusky_git_help)" \
        --color="bg+:#1e1e2e,bg:#11111b,spinner:#f5e0dc" \
        --color="fg:#cdd6f4,fg+:#cdd6f4,header:#89b4fa,info:#cba6f7" \
        --color="pointer:#a6e3a1,marker:#f5e0dc,prompt:#cba6f7" \
        --color="hl:#f38ba8,hl+:#f38ba8,border:#585b70,label:#89b4fa" \
        --preview="_dusky_git_preview {1}" \
        --preview-window="right,65%,border-left,wrap"

    # Clean Exit Payload
    clear
    printf "\e[1;32m✔ Disengaged Time Machine.\e[0m (Current HEAD: \e[33m%s\e[0m)\n" "$(git rev-parse --short HEAD 2>/dev/null)"
}

main "$@"
