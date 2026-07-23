# =============================================================================
# ~/.zshrc - Zsh Configuration (Modular Architecture)
#
# Sections are ordered specifically to respect Zsh initialization sequences:
# 1. Environment Variables & Path
# 2. History Configuration
# 3. Completion System (Must precede plugins)
# 4. Keybindings & Shell Options
# 5. Aliases & Core Functions
# 6. External Modules (Modular Sourcing)
# 7. Prompt & Tool Initialization
# 8. Plugins (Syntax Highlighting MUST be last)
# 9. TTY Auto-Login
# =============================================================================

# Exit early if not interactive (prevents breaking SCP/SFTP/rsync)
[[ -o interactive ]] || return

# -----------------------------------------------------------------------------
# [3] COMPLETION SYSTEM
# -----------------------------------------------------------------------------
setopt EXTENDED_GLOB

# 1. zstyle configurations MUST be declared before compinit
if [[ -z "$LS_COLORS" ]] && command -v dircolors >/dev/null; then
  eval "$(dircolors -b)"
fi

zstyle ':completion:*' menu select
zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"
zstyle ':completion:*:descriptions' format '%B%F{yellow}%d%f%b'
zstyle ':completion:*' group-name ''
zstyle ':completion:*' matcher-list 'm:{a-zA-Z}={A-Za-z}' 'r:|=*' 'l:|=* r:|=*'

local cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/zsh"
[[ -d "$cache_dir" ]] || mkdir -p "$cache_dir"
zstyle ':completion:*' use-cache on
zstyle ':completion:*' cache-path "$cache_dir/zcompcache"

# 2. Optimized initialization: Regenerate compdump cache once every 24 hours
autoload -Uz compinit
local zcompdump="${ZDOTDIR:-$HOME}/.zcompdump"
local dump_cache=($zcompdump(#qN.mh-24))

if (( ${#dump_cache} )); then
  compinit -C
else
  compinit
  touch "$zcompdump"
fi
unset cache_dir zcompdump dump_cache

# -----------------------------------------------------------------------------
# [1] ENVIRONMENT VARIABLES & PATH
# -----------------------------------------------------------------------------
export TERMINAL='kitty'
export EDITOR='nvim'
export VISUAL='nvim'

# Compilation Optimization: Moved to ~/.config/pacman/makepkg.conf

# Clipboard DB path - dynamic, set by 390_clipboard_persistance.sh toggle
[ -f "$HOME/.config/dusky/settings/cliphist_db_env" ] && source "$HOME/.config/dusky/settings/cliphist_db_env"

# --- Clipboard DB path auto-reload (live RAM/disk switch) ---
# Ensures old shells pick up new CLIPHIST_DB_PATH after running 390_clipboard_persistance.py
# in another terminal, without needing reboot or manual source.
__clip_db_env_file="$HOME/.config/dusky/settings/cliphist_db_env"
__clip_db_env_mtime=0
__reload_clip_env() {
  [[ -f "$__clip_db_env_file" ]] || return 0
  local mtime
  zmodload zsh/stat 2>/dev/null
  mtime=$(zstat +mtime "$__clip_db_env_file" 2>/dev/null || stat -c %Y "$__clip_db_env_file" 2>/dev/null || echo 0)
  if (( mtime != __clip_db_env_mtime )); then
    # shellcheck source=/dev/null
    source "$__clip_db_env_file" 2>/dev/null && __clip_db_env_mtime=$mtime
  fi
}
autoload -Uz add-zsh-hook 2>/dev/null && add-zsh-hook precmd __reload_clip_env

# Configure PATH (Uncomment to enable local binaries)
# export PATH="$HOME/.local/bin:$PATH"

# -----------------------------------------------------------------------------
# [2] HISTORY CONFIGURATION
# -----------------------------------------------------------------------------
HISTSIZE=50000
SAVEHIST=25000
HISTFILE="$HOME/.zsh_history"

setopt SHARE_HISTORY           # Share history between all concurrent shell sessions.
setopt HIST_EXPIRE_DUPS_FIRST  # When trimming history, delete duplicates first.
setopt HIST_IGNORE_ALL_DUPS    # Delete old recorded entry if new entry is a duplicate.
setopt HIST_IGNORE_SPACE       # Ignore commands starting with space.
setopt HIST_VERIFY             # Expand history (!!) into the buffer, don't run immediately.
setopt HIST_FCNTL_LOCK         # Better locking for concurrent shells sharing history

# -----------------------------------------------------------------------------
# [4] KEYBINDINGS & SHELL OPTIONS
# -----------------------------------------------------------------------------

# --- General Options ---
setopt INTERACTIVE_COMMENTS # Allow comments (#) in an interactive shell.
setopt GLOB_DOTS            # Include dotfiles (e.g., .config) in globbing results.
setopt NO_CASE_GLOB         # Perform case-insensitive globbing.
setopt AUTO_PUSHD           # Automatically push directories onto the directory stack.
setopt PUSHD_IGNORE_DUPS    # Don't push duplicate directories onto the stack.

# --- Vi Mode Keybindings ---
bindkey -v
KEYTIMEOUT=1 # 10ms transition delay (instant mode switching)

# --- Neovim Integration ---
# Press 'v' in normal mode to edit the current command string in Neovim
autoload -Uz edit-command-line
zle -N edit-command-line
bindkey -M vicmd v edit-command-line

# --- History Search with Up/Down Arrows ---
autoload -U history-search-end
zle -N history-beginning-search-backward-end history-search-end
zle -N history-beginning-search-forward-end history-search-end

# Bind Arrow Keys across both vi insert and command modes
for keymap in viins vicmd; do
  bindkey -M "$keymap" "${terminfo[kcuu1]:-^[[A}" history-beginning-search-backward-end
  bindkey -M "$keymap" "^[[A" history-beginning-search-backward-end
  bindkey -M "$keymap" "${terminfo[kcud1]:-^[[B}" history-beginning-search-forward-end
  bindkey -M "$keymap" "^[[B" history-beginning-search-forward-end
done

# -----------------------------------------------------------------------------
# [5] ALIASES & FUNCTIONS (Main Core)
# -----------------------------------------------------------------------------
# Core Safety
alias cp='cp -iv'
alias mv='mv -iv'
alias rm='rm -I'
alias ln='ln -v'

# Filesystem & IO
alias df='df -hT'
alias disk_usage='sudo btrfs filesystem usage /'
alias ncdu='gdu'
alias unlock="$HOME/user_scripts/drives/drive_manager/drive_manager.py unlock"
alias lock="$HOME/user_scripts/drives/drive_manager/drive_manager.py lock"
alias io_drives="$HOME/user_scripts/drives/dusky_disk_monitor_io.py"

# Searching & Differencing
alias diff='delta --side-by-side'
alias grep='grep --color=auto'
alias dusky_replace='python3 ~/user_scripts/tools/sed/dusky_replace.py'

# System & Development Scripts
alias tui='python ~/user_scripts/dusky_tui/python/main/main.py'
alias sendlogs="$HOME/user_scripts/arch_setup_scripts/send_logs.sh --auto"
alias update_dusky="$HOME/user_scripts/update_dusky/update_dusky.sh"
alias dusky_force_sync_github="$HOME/user_scripts/update_dusky/dusky_force_sync_github.sh"
alias darkmode="$HOME/user_scripts/theme_matugen/theme_ctl.sh set --mode dark"
alias lightmode="$HOME/user_scripts/theme_matugen/theme_ctl.sh set --mode light"
alias run_sysbench="$HOME/user_scripts/performance/sysbench_benchmark.py"

# Memory Optimization
alias mem_optimize='sudo systemctl start dusky_boot_mem_reclaim.service'

# Networking
alias iphone_vnc="$HOME/user_scripts/networking/iphone_vnc.sh"
alias wifi_security="$HOME/user_scripts/networking/airmon_ng.sh"

# Eza Integration (Replaces standard ls)
if command -v eza >/dev/null; then
    alias ls='eza --icons --group-directories-first'
    alias ll='eza --icons --group-directories-first -l --git'
    alias la='eza --icons --group-directories-first -la --git'
    alias lt='eza --icons --group-directories-first --tree --level=2'
else
    alias ls='ls --color=auto'
    alias ll='ls -lh'
    alias la='ls -A'
fi

# Yazi Wrapper (Changes directory upon exiting Yazi)
function y() {
    local tmp="$(mktemp -t "yazi-cwd.XXXXXX")" cwd
    yazi "$@" --cwd-file="$tmp"
    if cwd="$(cat -- "$tmp")" && [ -n "$cwd" ] && [ "$cwd" != "$PWD" ] && [ -d "$cwd" ]; then
        builtin cd -- "$cwd"
    fi
    rm -f -- "$tmp"
}

# Sudo Wrapper (Automatically routes 'sudo nvim' to 'sudoedit')
sudo() {
    if [[ "$1" == "nvim" ]]; then
        shift
        if [[ $# -eq 0 ]]; then
            echo "Error: sudoedit requires a filename."
            return 1
        fi
        command sudoedit "$@"
    else
        command sudo "$@"
    fi
}

# Utility Function: Make directory and immediately CD into it
mkcd() {
  mkdir -p -- "$1" && cd -- "$1"
}

# -----------------------------------------------------------------------------
# [6] EXTERNAL MODULES (Modular Sourcing)
# -----------------------------------------------------------------------------
# Array-based sourcing loop. 
# To add a new module, simply place the file in ~/.config/zshrc/ and add its name to this list.
local conf_dir="$HOME/.config/zshrc"
local -a my_modules=(
    batstat git kvm lmstudio logs logs_old mon_info
    pkg pkg_search res_mon vfio waydroid win10 wthr cmd_atlas
    sshfile scripts neovim_delta core gemini stt_dusky
)

for mod in "${my_modules[@]}"; do
    [[ -f "$conf_dir/$mod" ]] && source "$conf_dir/$mod"
done

unset conf_dir my_modules mod

# -----------------------------------------------------------------------------
# [7] PROMPT & TOOL INITIALIZATION
# -----------------------------------------------------------------------------
# Self-Healing Caches: Checks if binaries were updated and regenerates config.

# --- Starship Prompt ---
_starship_cache="$HOME/.starship-init.zsh"
_starship_bin="$(command -v starship)"
if [[ -n "$_starship_bin" ]]; then
  if [[ ! -f "$_starship_cache" || "$_starship_bin" -nt "$_starship_cache" || "$HOME/.config/starship.toml" -nt "$_starship_cache" ]]; then
    "$_starship_bin" init zsh --print-full-init >! "$_starship_cache"
  fi
  source "$_starship_cache"
fi

# --- Fuzzy Finder (fzf) ---
_fzf_cache="$HOME/.fzf-init.zsh"
_fzf_bin="$(command -v fzf)"
if [[ -n "$_fzf_bin" ]]; then
  if "$_fzf_bin" --zsh >/dev/null 2>&1; then
    if [[ ! -f "$_fzf_cache" || "$_fzf_bin" -nt "$_fzf_cache" ]]; then
      "$_fzf_bin" --zsh >! "$_fzf_cache"
    fi
    source "$_fzf_cache"
  fi
fi

# --- Zoxide ---
_zoxide_cache="$HOME/.zoxide-init.zsh"
_zoxide_bin="$(command -v zoxide)"
if [[ -n "$_zoxide_bin" ]]; then
  if [[ ! -f "$_zoxide_cache" || "$_zoxide_bin" -nt "$_zoxide_cache" ]]; then
    "$_zoxide_bin" init zsh --cmd cd >! "$_zoxide_cache"
  fi
  source "$_zoxide_cache"
fi

# Cleanup
unset _starship_cache _starship_bin _fzf_cache _fzf_bin _zoxide_cache _zoxide_bin

# -----------------------------------------------------------------------------
# [8] PLUGINS (Execution Order Critical)
# -----------------------------------------------------------------------------
# Autosuggestions MUST be sourced before Syntax Highlighting.

# --- Zsh Autosuggestions ---
if [[ -f /usr/share/zsh/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh ]]; then
    ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=60' # Dimmed grey matching
    source /usr/share/zsh/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh
fi

# --- Zsh Syntax Highlighting ---
# This MUST be the absolute last thing sourced in the file.
if [[ -f /usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]]; then
  source /usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
fi

# -----------------------------------------------------------------------------
# [9] TTY AUTO-LOGIN (Hyprland)
# -----------------------------------------------------------------------------
# Native variable check avoids expensive $(tty) subshells
if [[ -z "$DISPLAY" && -z "$WAYLAND_DISPLAY" && "$TTY" == "/dev/tty1" ]]; then
  exec start-hyprland
fi

# =============================================================================
# End of ~/.zshrc
# =============================================================================
