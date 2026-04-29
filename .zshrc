# =============================================================================
# ~/.zshrc - Zsh Configuration
#
# This configuration is structured for clarity and performance.
# Sections are ordered logically:
# 1. Environment Variables & Path
# 2. History Configuration
# 3. Completion System
# 4. Keybindings (Vi-Mode)
# 5. Aliases and Functions
# 6. Plugin & Prompt Initialization
# 7. Auto login INTO UWSM HYPRLAND WITH TTY1
# =============================================================================

# Exit early if not interactive
[[ -o interactive ]] || return

# -----------------------------------------------------------------------------
# [1] ENVIRONMENT VARIABLES & PATH
# -----------------------------------------------------------------------------
# Set core applications and configure the system's search path for executables.
# These are fundamental for defining your work environment.

# Set the default terminal emulator.
export TERMINAL='kitty'
# Set the default web browser.
#export BROWSER='firefox'

# Set the default editor (Critical for TTY/SSH/Yazi)
export EDITOR='nvim'
export VISUAL='nvim'

# --- Compilation Optimization ---
# 1. Parallelism: Use ALL available processing units.
#    $(nproc) dynamically counts cores on any machine this runs on.
export MAKEFLAGS="-j$(nproc)"

# --- Pyenv (Python Version Management) ---
# Initializes pyenv to manage multiple Python versions.

##    export PYENV_ROOT="$HOME/.pyenv"
##    export PATH="$PYENV_ROOT/bin:$PATH"
##    if command -v pyenv 1>/dev/null 2>&1; then
##      eval "$(pyenv init --path)"
##      eval "$(pyenv init -)"
##    fi

# Configure the path where Zsh looks for commands.
# Uncomment and modify if you have local binaries (e.g., in ~/.local/bin).
# export PATH="$HOME/.local/bin:$PATH"

# -----------------------------------------------------------------------------
# [2] HISTORY CONFIGURATION
# -----------------------------------------------------------------------------
# Configure how Zsh records and manages your command history. Robust history
# settings are crucial for an efficient workflow.

# Set the number of history lines to keep in memory during the session.
HISTSIZE=50000
# Set the number of history lines to save in the history file (~/.zsh_history).
SAVEHIST=25000
# Specify the location of the history file.
HISTFILE=~/.zsh_history

# Use `setopt` to fine-tune history behavior.
setopt APPEND_HISTORY          # Append new history entries instead of overwriting.
setopt INC_APPEND_HISTORY      # Write history to file immediately after command execution.
setopt SHARE_HISTORY           # Share history between all concurrent shell sessions.
setopt HIST_EXPIRE_DUPS_FIRST  # When trimming history, delete duplicates first.
setopt HIST_IGNORE_DUPS        # Don't record an entry that was just recorded again.
setopt HIST_IGNORE_SPACE       # Ignore commands starting with space.
setopt HIST_VERIFY             # Expand history (!!) into the buffer, don't run immediately.

# -----------------------------------------------------------------------------
# [3] COMPLETION SYSTEM
# -----------------------------------------------------------------------------

setopt EXTENDED_GLOB        # Enable extended globbing features (e.g., `^` for negation).

# Optimized initialization: Only regenerate cache once every 24 hours.
autoload -Uz compinit
local zcompdump="${ZDOTDIR:-$HOME}/.zcompdump"
local dump_cache=($zcompdump(#qN.mh-24)) # Array expansion forces glob evaluation without subshells

if (( ${#dump_cache} )); then
  compinit -C  # Trust the fresh cache, skip checks (FAST)
else
  compinit     # Cache is old or missing, regenerate it (SLOW)
  # Explicitly touch the file to reset the timer
  touch "$zcompdump"
fi

# Style the completion menu.
# ':completion:*' is a pattern that applies to all completion widgets.
zstyle ':completion:*' menu select                 # Enable menu selection on the first Tab press.
zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}" # Colorize the completion menu using LS_COLORS.
zstyle ':completion:*:descriptions' format '%B%d%b'  # Format descriptions for clarity (bold).
zstyle ':completion:*' group-name ''               # Group completions by type without showing group names.
zstyle ':completion:*' matcher-list 'm:{a-zA-Z}={A-Za-z}' # Case-insensitive matching.

# -----------------------------------------------------------------------------
# [4] KEYBINDINGS & SHELL OPTIONS
# -----------------------------------------------------------------------------
# Define keybindings and enable various shell options for a better user experience.

# --- Vi Mode Keybindings ---
# Enables the use of Vim-like keybindings in the shell for modal editing.
bindkey -v
# Set the timeout for ambiguous key sequences (in centiseconds).
# 1 = 10ms, making the transition to normal mode in Vi mode practically instantaneous.
export KEYTIMEOUT=1

# --- Neovim Integration ---
# Press 'v' in normal mode to edit the current command in Neovim.
autoload -U edit-command-line
zle -N edit-command-line
bindkey -M vicmd v edit-command-line

# --- Search History with Up/Down ---
# If you type "git" and press Up, it finds the last "git" command.
autoload -U history-search-end
zle -N history-beginning-search-backward-end history-search-end
zle -N history-beginning-search-forward-end history-search-end
bindkey "${terminfo[kcuu1]:-^[[A}" history-beginning-search-backward-end
bindkey "${terminfo[kcud1]:-^[[B}" history-beginning-search-forward-end

# --- General Shell Options (`setopt`) ---
setopt INTERACTIVE_COMMENTS # Allow comments (like this one) in an interactive shell.
setopt GLOB_DOTS            # Include dotfiles (e.g., .config) in globbing results.
setopt NO_CASE_GLOB         # Perform case-insensitive globbing.
setopt AUTO_PUSHD           # Automatically push directories onto the directory stack.
setopt PUSHD_IGNORE_DUPS    # Don't push duplicate directories onto the stack.


# -----------------------------------------------------------------------------
# [5] ALIASES & FUNCTIONS
# -----------------------------------------------------------------------------
# Define shortcuts (aliases) and small scripts (functions) to reduce typing
# and streamline common tasks.

# --- Aliases ---

# alias ls='ls --color=auto' # Always use color for `ls`.
# alias la='ls -A'           # List all entries except for . and ..
# alias ll='ls -alF'         # List all files in long format.
# alias l='ls -CF'           # List entries by columns.

# Safety First
alias cp='cp -iv'
alias mv='mv -iv'
alias rm='rm -I'
alias ln='ln -v'

alias disk_usage='sudo btrfs filesystem usage /' # The TRUTH about BTRFS space
alias df='df -hT'                                # Show filesystem types

# VNC iphone daemon.
alias iphone_vnc='~/user_scripts/networking/iphone_vnc.sh'

# wifi security
alias wifi_security='~/user_scripts/networking/ax201_wifi_testing.sh'

#Theme Switcher
alias darkmode='~/user_scripts/theme_matugen/matugen_config.sh --mode dark'
alias lightmode='~/user_scripts/theme_matugen/matugen_config.sh --mode light'

#submit logs 
alias sendlogs='~/user_scripts/arch_setup_scripts/send_logs.sh --auto'

# update dusky
alias update_dusky='~/user_scripts/update_dusky/update_dusky.sh'

# update dusky reset
alias dusky_force_sync_github='~/user_scripts/update_dusky/dusky_force_sync_github.sh'

# Check if eza is installed
if command -v eza >/dev/null; then
    alias ls='eza --icons --group-directories-first'
    alias ll='eza --icons --group-directories-first -l --git'
    alias la='eza --icons --group-directories-first -la --git'
    alias lt='eza --icons --group-directories-first --tree --level=2'
else
    # Fallback to standard ls if eza is missing
    alias ls='ls --color=auto'
    alias ll='ls -lh'
    alias la='ls -A'
fi

alias diff='delta --side-by-side'
alias grep='grep --color=auto'
alias egrep='egrep --color=auto'
alias fgrep='fgrep --color=auto'

#alias cat='bat'

#alias for using gdu instead of ncdu
alias ncdu='gdu'

#alias for disk io realtime.
alias io_drives='~/user_scripts/drives/io_monitor.sh'

# 1. Base Bare Repo Alias
# (Defined first for logical clarity, though strictly not required by Zsh)
alias git_dusky='/usr/bin/git --git-dir=$HOME/dusky/ --work-tree=$HOME'

# 2. Add List Alias (FIXED with Subshell)
# The ( ) runs this specific command inside $HOME so the paths match,
# but it DOES NOT change your actual terminal directory.
alias git_dusky_add_list='(cd $HOME && git_dusky add --pathspec-from-file=.git_dusky_list)'

# 3. Alias for discarding all local changes (both staged and unstaged) and revert the state of tracked files to exactly match the last commit (HEAD), this is a destructive operation. (DANGER ZONE)
alias git_dusky_restore='echo "git --git-dir=$HOME/dusky/ --work-tree=$HOME reset --hard HEAD" && git_dusky reset --hard HEAD'

# 4. Delta/Diff Alias
alias gitdelta='git_dusky_add_list && git_dusky diff HEAD'

# 5. Lazygit Bare Repo Alias
alias lazygit_dusky='lazygit --git-dir=$HOME/dusky/ --work-tree=$HOME'

# unlock block_devices
alias unlock='$HOME/user_scripts/drives/drive_manager.sh unlock'

# lock block_devices
alias lock='$HOME/user_scripts/drives/drive_manager.sh lock'


# Battery stats

batstat() {
    # Isolate shell options for predictable execution
    emulate -L zsh
    
    # Strictly scope all variables at the function level
    local bat="" target="$1" live_mode=0 d dev_type
    local cap stat curr volt power
    float watts=0.0

    # Parse arguments cleanly
    if [[ "$1" == "live" ]]; then
        live_mode=1
        target=""
    elif [[ "$2" == "live" ]]; then
        live_mode=1
    fi

    # Hardware Detection: Target specific battery or find the first one natively
    if [[ -n "$target" && -d "/sys/class/power_supply/$target" ]]; then
        bat="/sys/class/power_supply/$target"
    else
        # (N) prevents failure if the directory is completely empty
        for d in /sys/class/power_supply/*(N); do
            if [[ -f "$d/type" ]]; then
                read -r dev_type < "$d/type" 2>/dev/null
                if [[ "$dev_type" == "Battery" ]]; then
                    bat="$d"
                    break
                fi
            fi
        done
    fi

    if [[ -z "$bat" ]]; then
        printf "Error: No battery detected in /sys/class/power_supply/\n" >&2
        return 1
    fi

    # Core Logic: True Zero-Fork
    _get_bat_stats() {
        read -r cap < "$bat/capacity" 2>/dev/null
        read -r stat < "$bat/status" 2>/dev/null

        # Fallbacks for empty reads
        cap=${cap:-"N/A"}
        stat=${stat:-"Unknown"}

        # Native Zsh floating-point arithmetic (zero subshells)
        if [[ -f "$bat/power_now" ]]; then
            read -r power < "$bat/power_now" 2>/dev/null
            (( watts = ${power:-0} / 1000000.0 ))
        elif [[ -f "$bat/current_now" && -f "$bat/voltage_now" ]]; then
            read -r curr < "$bat/current_now" 2>/dev/null
            read -r volt < "$bat/voltage_now" 2>/dev/null
            (( watts = (${curr:-0} * ${volt:-0}) / 1000000000000.0 ))
        fi
        
        printf "Capacity: %s%% | Power Draw: %.2f W (%s)" "$cap" "$watts" "$stat"
    }

    # Execution Engine
    if (( live_mode )); then
        printf "\e[?25l" # Hide cursor
        
        # Zsh native 'always' block guarantees cursor restoration on Ctrl+C
        {
            while true; do
                printf "\r\e[K"
                _get_bat_stats
                sleep 1
            done
        } always {
            printf "\e[?25h\n" # Restore cursor and drop a clean newline
        }
    else
        _get_bat_stats
        printf "\n"
    fi
}


# Weather query via wttr.in
# Usage: wthr [location]
# use with "-s" flag to only get one line.
wthr() {
    # Check if the first argument is '-s' (short)
    if [[ "$1" == "-s" ]]; then
        shift # Remove the -s from arguments
        local location="${(j:+:)@}"
        curl "wttr.in/${location}?format=%c+%t"
    else
        local location="${(j:+:)@}"
        curl "wttr.in/${location}"
    fi
}


# for troubleshoting scripts
source ~/.config/zshrc/logs
source ~/.config/zshrc/logs_old

# share zram1 directory with waydroid at pictures point inside waydroid
# Function to remount Waydroid pictures to ZRAM
waydroid_bind() {
    local target="$HOME/.local/share/waydroid/data/media/0/Pictures"
    local source="/mnt/zram1"

    # 1. Attempt to unmount recursively.
    # 2>/dev/null silences the error if it's not mounted.
    # || true ensures the script doesn't abort if you have 'set -e' active or strict chaining.
    sudo umount -R "$target" 2>/dev/null || true

    # 2. Perform the bind mount
    # We check if the source exists first to avoid mounting nothing.
    if [[ -d "$source" ]]; then
        sudo mount --bind "$source" "$target"
        echo "Successfully bound $source to Waydroid Pictures."
    else
        echo "Error: Source $source does not exist."
        return 1
    fi
}

# ===
# use `command sudo nvim ...` to escape the funtion if you ever dont want sudoedit to be used.
# ===
# sudo edit nvim sudoedit
# Function to intercept 'sudo nvim' and convert it to 'sudoedit'
sudo() {
    # Check if we are trying to run nvim
    if [[ "$1" == "nvim" ]]; then
        shift # Remove 'nvim'
        
        # Check if there are actually files to edit
        if [[ $# -eq 0 ]]; then
            echo "Error: sudoedit requires a filename."
            return 1
        fi
        
        # Pass the filenames to sudoedit
        command sudoedit "$@"
    else
        # Run standard sudo for everything else
        command sudo "$@"
    fi
}

# YAZI
#change the current working directory when exiting Yazi

function y() {
    local tmp="$(mktemp -t "yazi-cwd.XXXXXX")" cwd
    yazi "$@" --cwd-file="$tmp"
    if cwd="$(cat -- "$tmp")" && [ -n "$cwd" ] && [ "$cwd" != "$PWD" ]; then
        builtin cd -- "$cwd"
    fi
    rm -f -- "$tmp"
}

# --- sysbench benchmark ---
alias run_sysbench='~/user_scripts/performance/sysbench_benchmark.sh'

# --- nvidia vfio bind/unbind ---
alias nvidia_bind='~/user_scripts/nvidia_passthrough/nvidia_vfio_bind_unbind.sh --bind'
alias nvidia_unbind='~/user_scripts/nvidia_passthrough/nvidia_vfio_bind_unbind.sh --unbind'

#-- LM- Studio--
llm() {
    /mnt/media/Documents/do_not_delete_linux/appimages/LM-Studio*(om[1]) "$@"
}
# The (om[1]) glob qualifier picks the most recently modified file (newest first)

# --- Functions ---
# Creates a directory and changes into it.
mkcd() {
  mkdir -p "$1" && cd "$1"
}

# --- Windows 10 KVM Manager ---
# HOW TO USE 
# Start VM: win start
# Open Looking Glass: win view
# Do both (One-click gaming): win launch
# Kill it: win kill

win() {
    local vm="win10"
    local shm_file="/dev/shm/looking-glass"
    local lg_cmd="looking-glass-client -f ${shm_file} -m KEY_F6"

    # Helper for colored output
    local p_info() { echo -e "\e[34m[WIN10]\e[0m $1"; }
    local p_err()  { echo -e "\e[31m[ERROR]\e[0m $1"; }

    case "$1" in
        start)
            p_info "Starting VM..."
            sudo virsh start "$vm"
            ;;
        stop|shutdown)
            p_info "Sending shutdown signal..."
            sudo virsh shutdown "$vm"
            ;;
        kill|destroy)
            p_info "Forcefully destroying VM..."
            sudo virsh destroy "$vm"
            ;;
        reboot)
            p_info "Rebooting VM..."
            sudo virsh reboot "$vm"
            ;;
        view|lg|show)
            if [ -f "$shm_file" ]; then
                p_info "Launching Looking Glass..."
                eval "$lg_cmd"
            else
                p_err "Looking Glass SHM file not found. Is the VM running?"
            fi
            ;;
        # --- Advanced Options ---
        launch|play)
            # Starts VM and waits for Looking Glass to be ready
            p_info "Two birds one stone: Starting VM and waiting for Looking Glass..."
            sudo virsh start "$vm" 2>/dev/null
            
            p_info "Waiting for Shared Memory..."
            # Efficient bash wait loop (timeout after 30s)
            local timeout=30
            while [ ! -f "$shm_file" ] && [ $timeout -gt 0 ]; do
                sleep 1
                ((timeout--))
            done

            if [ -f "$shm_file" ]; then
                p_info "Ready! Launching Client..."
                eval "$lg_cmd"
            else
                p_err "Timed out waiting for VM graphics."
            fi
            ;;
        status)
            sudo virsh domstate "$vm"
            ;;
        edit)
            sudo virsh edit "$vm"
            ;;
        *)
            echo "Usage: win {start|shutdown|destroy|reboot|view|launch|status|edit}"
            ;;
    esac
}

# --- Auto-Completion for 'win' ---
# This makes hitting 'tab' show your options
_win_completion() {
    local -a commands
    commands=('start' 'shutdown' 'destroy' 'reboot' 'view' 'launch' 'status' 'edit')
    _describe 'command' commands
}
compdef _win_completion win


# =============================================================================
# Pacman/Expac Utility: Unified Package Querying (Platinum Edition)
# Usage: pkg <command> [count]
# =============================================================================

# DRY Header Helper — defined once at source time, not re-created on every pkg call.
# Args: $1 = title string, $2 = count integer
_pkg_header() {
    print -P "\n%F{blue}::%f %B${1}%b (Top ${2})"
    print -P "%F{238}------------------------------------------------------------%f"
}

pkg() {
    # 1. Pre-flight Check: Ultra-fast Zsh native dependency validation
    if (( ! $+commands[expac] )); then
        print -u2 -P "%F{red}✖ Error:%f 'expac' is not installed. Run: sudo pacman -S expac"
        return 1
    fi

    # 2. Scope & Defaults
    local cmd="${1:-help}"
    local num="${2:-20}"

    # 3. Strict Validation: Must be a POSITIVE integer (no zeros, no letters)
    if [[ ! "$num" =~ ^[1-9][0-9]*$ ]]; then
        print -u2 -P "%F{red}✖ Error:%f Count must be a positive integer (1 or higher)."
        return 1
    fi

    # 4. Core Execution Pipeline
    case "${cmd:l}" in
        hogs|size|all)
            _pkg_header "Largest Packages (Including Dependencies)" "$num"
            expac '%m|%n' | sort -rn | head -n "$num" | numfmt --to=iec-i --suffix=B --field=1 --delimiter='|' | column -t -s '|'
            ;;

        explicit|user)
            _pkg_header "Largest Explicitly Installed Packages" "$num"
            pacman -Qeq | expac '%m|%n' - | sort -rn | head -n "$num" | numfmt --to=iec-i --suffix=B --field=1 --delimiter='|' | column -t -s '|'
            ;;

        new|recent|latest)
            _pkg_header "Recently Installed Packages" "$num"
            expac --timefmt='%Y-%m-%d %T' '%l|%n' | sort -r | head -n "$num" | column -t -s '|'
            ;;

        old|ancient)
            _pkg_header "Oldest Installed Packages" "$num"
            expac --timefmt='%Y-%m-%d %T' '%l|%n' | sort | head -n "$num" | column -t -s '|'
            ;;

        help|*)
            print -P "\n%F{green}Usage:%f pkg <command> [count (default: 20)]\n"
            print -P "%BCommands:%b"
            print "  hogs, size, all          - List largest packages (overall)"
            print "  explicit, user           - List largest explicitly installed packages"
            print "  new, recent, latest      - List recently installed (newest first)"
            print "  old, ancient             - List oldest installed (oldest first)\n"
            print -P "%BExamples:%b"
            print "  pkg size 10        # Top 10 largest packages overall"
            print "  pkg explicit 50    # Top 50 largest packages you explicitly installed\n"

            [[ "${cmd:l}" == "help" ]] && return 0 || return 1
            ;;
    esac

    # 5. Trailing newline for prompt breathing room
    print ""
}

# Native Zsh tab-completion — proper named function, unambiguously correct.
_pkg() {
    _arguments \
        "1:command:(hogs size all explicit user new recent latest old ancient help)" \
        "2:count: "
}
compdef _pkg pkg

# -----------------------------------------------------------------------------
# [6] PLUGINS & PROMPT INITIALIZATION
# -----------------------------------------------------------------------------
# Self-Healing Cache:
# 1. Checks if the static init file exists.
# 2. Checks if the binary (starship/fzf) has been updated (is newer than the cache).
# 3. Regenerates the cache automatically if needed.

# --- Starship Prompt ---
# Define paths
_starship_cache="$HOME/.starship-init.zsh"
_starship_bin="$(command -v starship)"

# Only proceed if starship is actually installed
if [[ -n "$_starship_bin" ]]; then
  if [[ ! -f "$_starship_cache" || "$_starship_bin" -nt "$_starship_cache" ]]; then
    starship init zsh --print-full-init >! "$_starship_cache"
  fi
  source "$_starship_cache"
fi

# --- Fuzzy Finder (fzf) ---
_fzf_cache="$HOME/.fzf-init.zsh"
_fzf_bin="$(command -v fzf)"

if [[ -n "$_fzf_bin" ]];
then
  # Check if fzf supports the --zsh flag
if $_fzf_bin --zsh > /dev/null 2>&1; then
      if [[ ! -f "$_fzf_cache" || "$_fzf_bin" -nt "$_fzf_cache" ]]; then
        $_fzf_bin --zsh >! "$_fzf_cache"
      fi
      source "$_fzf_cache"
  else
      # Fallback for older fzf versions
      if [[ -f ~/.fzf.zsh ]]; then
          source ~/.fzf.zsh
      fi
  fi
fi


# -- Zoxide (Cached) --
_zoxide_cache="$HOME/.zoxide-init.zsh"
_zoxide_bin="$(command -v zoxide)"

if [[ -n "$_zoxide_bin" ]]; then
  if [[ ! -f "$_zoxide_cache" || "$_zoxide_bin" -nt "$_zoxide_cache" ]]; then
    "$_zoxide_bin" init zsh >! "$_zoxide_cache"
  fi
  source "$_zoxide_cache"
fi
unset _zoxide_cache _zoxide_bin


# --- Autosuggestions ---
if [ -f /usr/share/zsh/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh ]; then
    # Config MUST be set before sourcing
    ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=60'
    source /usr/share/zsh/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh
fi

# --- Syntax Highlighting (Must be last) ---
if [[ -f "/usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh" ]]; then
  source "/usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh"
fi



# Cleanup variables to keep environment clean
unset _starship_cache _starship_bin _fzf_cache _fzf_bin

# -----------------------------------------------------------------------------
# [7] Auto login INTO UWSM HYPRLAND WITH TTY1
# -----------------------------------------------------------------------------

# Check if we are on tty1 and no display server is running
# Using native $TTY and $WAYLAND_DISPLAY variables avoids the overhead of spawning $(tty) subshells
if [[ -z "$DISPLAY" && -z "$WAYLAND_DISPLAY" && "$TTY" == "/dev/tty1" ]]; then
  if uwsm check may-start; then
    exec uwsm start hyprland.desktop
  fi
fi

# =============================================================================
# End of ~/.zshrc
# =============================================================================
