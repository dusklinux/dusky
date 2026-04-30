#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky TUI Engine - Lua/Hyprland Refactor
# Target: current Arch Linux, Wayland, Hyprland 0.55+ Lua config, UWSM sessions
# -----------------------------------------------------------------------------

set -Eeuo pipefail
shopt -s extglob

# =============================================================================
# USER CONFIGURATION
# =============================================================================

: "${XDG_CONFIG_HOME:=${HOME}/.config}"
declare CONFIG_FILE="${DUSKY_CONFIG_FILE:-${XDG_CONFIG_HOME}/hypr/hyprland.lua}"
declare -r APP_TITLE="Hyprland Lua Config Editor"
declare -r APP_VERSION="v5.0.0-lua"

# Parser limits for untrusted config evaluation.
declare -ri LUA_TIMEOUT_SECONDS=4
declare -ri LUA_KILL_AFTER_SECONDS=1
declare -ri LUA_MEMORY_KB=262144

# Dimensions & layout.
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ADJUST_THRESHOLD=38
declare -ri ITEM_PADDING=32

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

declare -ra TABS=("General" "Input" "Display" "Misc")

register_items() {
    # Hyprland 0.55 Lua layout: hl.config({ category = { key = value } })
    register 0 "Enable Logs"    'logs_enabled|bool|general|||'          "true"
    register 0 "Timeout (ms)"   'timeout|int|general|0|1000|50'         "100"

    register 1 "Sensitivity"    'sensitivity|float|input|-1.0|1.0|0.1'  "0.0"
    register 1 "Accel Profile"  'accel_profile|cycle|input|flat,adaptive,custom||' "adaptive"

    register 2 "Border Size"    'border_size|int|general|0|10|1'        "2"
    register 2 "Blur Enabled"   'enabled|bool|decoration/blur|||'       "true"

    register 3 "Advanced Settings" 'advanced_settings|menu||||'         ""
    register_child "advanced_settings" "Touchpad Enable" 'enabled|bool|input/touchpad|||'                  "true"
    register_child "advanced_settings" "Scroll Factor"   'scroll_factor|float|input/touchpad|0.1|5.0|0.1' "1.0"
    register_child "advanced_settings" "Tap to Click"    'tap-to-click|bool|input/touchpad|||'            "true"

    register 3 "Shadow Color"   'color|cycle|decoration/shadow|0xee1a1a1a,0xff000000||' "0xee1a1a1a"

    register 3 "Custom Path (Text Entry)"   'demo_text|action||||' ""
    register 3 "Select Theme (Picker)"      'demo_picker|action||||' ""
    register 3 "Restart Systemd (Sudo)"     'demo_sudo|action||||' ""
}

action_demo_text() {
    local input=""
    prompt_line_input "Enter a custom file path:" input
    if [[ -n $input ]]; then
        set_status "You typed: $input"
    else
        clear_status
    fi
}

action_demo_picker() {
    PICKER_TITLE="Select a Workspace Theme"
    PICKER_ITEMS=("Catppuccin Mocha" "Nord" "Dracula" "Gruvbox" "Tokyo Night")
    PICKER_HINTS=("Warm & Pastel" "Arctic Cold" "Vampire Dark" "Retro Groove" "Neon Lights")
    PICKER_CALLBACK="picker_cb_demo_theme"
    PICKER_SELECTED=0
    PICKER_SCROLL=0

    PARENT_ROW=$SELECTED_ROW
    PARENT_SCROLL=$SCROLL_OFFSET
    CURRENT_VIEW=2
    clear_status
}

picker_cb_demo_theme() {
    local selected=$1
    set_status "Selected Theme: $selected"
}

action_demo_sudo() {
    if ! sudo -n true 2>/dev/null; then
        acquire_sudo || return 0
    fi
    set_status "Sudo acquired. Service restart simulated."
}

post_write_action() {
    if command -v hyprctl >/dev/null 2>&1; then
        hyprctl reload >/dev/null 2>&1 || :
    fi
}

# =============================================================================
# CONSTANTS AND STATE
# =============================================================================

declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
declare -r H_LINE="${_h_line_buf// /-}"
unset _h_line_buf

# ANSI constants.
declare -r C_RESET=$'\033[0m'
declare -r C_CYAN=$'\033[1;36m'
declare -r C_GREEN=$'\033[1;32m'
declare -r C_MAGENTA=$'\033[1;35m'
declare -r C_RED=$'\033[1;31m'
declare -r C_YELLOW=$'\033[1;33m'
declare -r C_WHITE=$'\033[1;37m'
declare -r C_GREY=$'\033[1;30m'
declare -r C_INVERSE=$'\033[7m'
declare -r CLR_EOL=$'\033[K'
declare -r CLR_EOS=$'\033[J'
declare -r CLR_SCREEN=$'\033[2J'
declare -r CURSOR_HOME=$'\033[H'
declare -r CURSOR_HIDE=$'\033[?25l'
declare -r CURSOR_SHOW=$'\033[?25h'
declare -r ALT_SCREEN_ON=$'\033[?1049h'
declare -r ALT_SCREEN_OFF=$'\033[?1049l'
declare -r MOUSE_ON=$'\033[?1000h\033[?1006h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1006l'

declare -r ESC_READ_TIMEOUT=0.08
declare -r READ_LOOP_TIMEOUT=0.25
declare -r UNSET_MARKER='<unset>'

declare -i SELECTED_ROW=0 CURRENT_TAB=0 SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0
declare ORIGINAL_STTY=""
declare -i TUI_STARTED=0

declare -a TAB_SAVED_ROW=()
declare -a TAB_SAVED_SCROLL=()
for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    TAB_SAVED_ROW+=("0")
    TAB_SAVED_SCROLL+=("0")
done
unset _ti

declare -i CURRENT_VIEW=0
declare CURRENT_MENU_ID=""
declare -i PARENT_ROW=0 PARENT_SCROLL=0
declare -gi RESIZE_PENDING=0

declare PICKER_TITLE=""
declare -a PICKER_ITEMS=()
declare -a PICKER_HINTS=()
declare PICKER_CALLBACK=""
declare -i PICKER_SELECTED=0 PICKER_SCROLL=0

declare -i SUDO_AUTHENTICATED=0

declare _TMPFILE=""
declare _TMPMODE=""
declare -a _TEMP_PATHS=()
declare WRITE_TARGET=""
declare LOCK_TARGET=""
declare LUA_BIN=""

declare -i TERM_ROWS=0 TERM_COLS=0
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 5 ))

declare -gi LAST_WRITE_CHANGED=0
declare STATUS_MESSAGE=""
declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

declare -A ITEM_MAP=()
declare -A VALUE_CACHE=()
declare -A CONFIG_CACHE=()
declare -A DEFAULTS=()
declare -a CONFIG_SOURCE_FILES=()

for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    declare -ga "TAB_ITEMS_${_ti}=()"
done
unset _ti

# =============================================================================
# SYSTEM HELPERS
# =============================================================================

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

set_status() { declare -g STATUS_MESSAGE=$1; }
clear_status() { declare -g STATUS_MESSAGE=""; }

register_temp() {
    local path=$1
    [[ -n $path ]] && _TEMP_PATHS+=("$path")
}

forget_temp() {
    local path=$1 kept=() item
    for item in "${_TEMP_PATHS[@]:-}"; do
        [[ $item == "$path" ]] || kept+=("$item")
    done
    _TEMP_PATHS=("${kept[@]:-}")
}

remove_temp() {
    local path=$1
    [[ -n $path && -e $path ]] && rm -f -- "$path" 2>/dev/null || :
    forget_temp "$path"
}

cleanup() {
    local path
    if (( TUI_STARTED )); then
        printf '%s%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" "$ALT_SCREEN_OFF" 2>/dev/null || :
    else
        printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    fi

    if [[ -n ${ORIGINAL_STTY:-} ]]; then
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi

    for path in "${_TEMP_PATHS[@]:-}"; do
        [[ -n $path && -e $path ]] && rm -f -- "$path" 2>/dev/null || :
    done
    _TEMP_PATHS=()
    _TMPFILE=""
    _TMPMODE=""
    printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

path_dirname() {
    local path=$1
    if [[ $path == */* ]]; then
        REPLY=${path%/*}
        [[ -n $REPLY ]] || REPLY=/
    else
        REPLY=.
    fi
}

path_basename() {
    local path=$1
    REPLY=${path##*/}
}

find_lua() {
    local candidate found
    for candidate in lua lua5.4 lua54; do
        found=$(type -P "$candidate" 2>/dev/null || true)
        if [[ -n $found ]]; then
            LUA_BIN=$found
            return 0
        fi
    done
    return 1
}

resolve_write_target() {
    WRITE_TARGET=$(realpath -e -- "$CONFIG_FILE")
    LOCK_TARGET="${WRITE_TARGET}.lock"
}

create_tmpfile_for_target() {
    local target=$1 target_dir target_base
    path_dirname "$target"; target_dir=$REPLY
    path_basename "$target"; target_base=$REPLY

    if ! _TMPFILE=$(mktemp --tmpdir="$target_dir" ".${target_base}.tmp.XXXXXXXXXX" 2>/dev/null); then
        _TMPFILE=""
        _TMPMODE=""
        return 1
    fi
    _TMPMODE="atomic"
    register_temp "$_TMPFILE"
    return 0
}

commit_tmpfile_to_target() {
    local target=$1 target_dir
    [[ -n ${_TMPFILE:-} && -f $_TMPFILE && ${_TMPMODE:-} == atomic ]] || return 1

    chown --reference="$target" -- "$_TMPFILE" 2>/dev/null || :
    chmod --reference="$target" -- "$_TMPFILE" 2>/dev/null || return 1
    sync -f -- "$_TMPFILE" 2>/dev/null || :
    mv -f -- "$_TMPFILE" "$target" || return 1
    path_dirname "$target"; target_dir=$REPLY
    sync -f -- "$target_dir" 2>/dev/null || :

    forget_temp "$_TMPFILE"
    _TMPFILE=""
    _TMPMODE=""
    return 0
}

update_terminal_size() {
    local size
    if size=$(stty size < /dev/tty 2>/dev/null); then
        TERM_ROWS=${size%% *}
        TERM_COLS=${size##* }
    else
        TERM_ROWS=0
        TERM_COLS=0
    fi
}

terminal_size_ok() {
    (( TERM_COLS >= MIN_TERM_COLS && TERM_ROWS >= MIN_TERM_ROWS ))
}

draw_small_terminal_notice() {
    printf '%s%s' "$CURSOR_HOME" "$CLR_SCREEN"
    printf '%sTerminal too small%s\n' "$C_RED" "$C_RESET"
    printf '%sNeed at least:%s %d cols x %d rows\n' "$C_YELLOW" "$C_RESET" "$MIN_TERM_COLS" "$MIN_TERM_ROWS"
    printf '%sCurrent size:%s %d cols x %d rows\n' "$C_WHITE" "$C_RESET" "$TERM_COLS" "$TERM_ROWS"
    printf '%sResize the terminal, then continue. Press q to quit.%s%s' "$C_CYAN" "$C_RESET" "$CLR_EOS"
}

get_active_context() {
    if (( CURRENT_VIEW == 0 )); then
        REPLY_CTX=${CURRENT_TAB}
        REPLY_REF="TAB_ITEMS_${CURRENT_TAB}"
    else
        REPLY_CTX=${CURRENT_MENU_ID}
        REPLY_REF="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
    fi
}

strip_ansi() {
    local v=$1
    v=${v//$'\033'\[*([0-9;:?<=>])@([@A-Z[\\\]^_\`a-z\{\|\}~])/}
    REPLY=$v
}

trim_spaces() {
    local v=$1
    v=${v#"${v%%[![:space:]]*}"}
    v=${v%"${v##*[![:space:]]}"}
    REPLY=$v
}

join_scope_key() {
    local scope=$1 key=$2
    if [[ -n $scope ]]; then
        REPLY="${key}|${scope}"
    else
        REPLY="${key}|"
    fi
}

normalize_target() {
    local key=$1 scope=$2 prefix leaf
    TARGET_KEY=$key
    TARGET_SCOPE=$scope

    join_scope_key "$scope" "$key"
    if [[ -n ${CONFIG_CACHE[$REPLY]+_} || $key != *.* ]]; then
        return 0
    fi

    prefix=${key%.*}
    leaf=${key##*.}
    prefix=${prefix//./\/}
    if [[ -n $scope ]]; then
        TARGET_SCOPE="${scope}/${prefix}"
    else
        TARGET_SCOPE=$prefix
    fi
    TARGET_KEY=$leaf
}

# =============================================================================
# REGISTRATION
# =============================================================================

validate_cycle_options() {
    local label=$1 options=$2 opt
    local -a opts=()
    IFS=',' read -r -a opts <<< "$options"
    if (( ${#opts[@]} == 0 )); then
        log_err "Register Error: Cycle '$label' has no options."
        exit 1
    fi
    for opt in "${opts[@]}"; do
        if [[ -z $opt || $opt == *$'\n'* || $opt == *'}'* ]]; then
            log_err "Register Error: Cycle '$label' contains unsafe option: '$opt'"
            exit 1
        fi
    done
}

validate_item_config() {
    local label=$1 key=$2 type=$3 block=$4 min=$5
    if [[ -z $label || $label == *$'\n'* ]]; then
        log_err "Register Error: Invalid label."
        exit 1
    fi
    if [[ -z $key ]]; then
        log_err "Register Error: Missing key for '$label'."
        exit 1
    fi
    case $type in
        bool|int|float|cycle|menu|action) ;;
        *) log_err "Invalid type for '$label': $type"; exit 1 ;;
    esac
    if [[ -n $block && ! $block =~ ^[a-zA-Z0-9_.:-]+(/[a-zA-Z0-9_.:-]+)*$ ]]; then
        log_err "Register Error: Invalid block path for '$label': $block"
        exit 1
    fi
    if [[ $type == cycle ]]; then
        validate_cycle_options "$label" "$min"
    fi
    if [[ $type == action && ! $key =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        log_err "Register Error: Action key '$key' is not a safe function suffix."
        exit 1
    fi
}

register() {
    local -i tab_idx=$1
    local label=$2 config=$3 default_val=${4:-}
    local key type block min max step
    IFS='|' read -r key type block min max step <<< "$config"

    if (( tab_idx < 0 || tab_idx >= TAB_COUNT )); then
        log_err "Register Error: Tab index out of range for '$label': $tab_idx"
        exit 1
    fi
    validate_item_config "$label" "$key" "$type" "$block" "$min"

    if [[ -n ${ITEM_MAP["${tab_idx}::${label}"]+_} ]]; then
        log_err "Register Error: Duplicate label in tab $tab_idx: $label"
        exit 1
    fi
    if [[ $type == menu && ! $key =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        log_err "Register Error: Menu ID '$key' contains invalid characters."
        exit 1
    fi

    ITEM_MAP["${tab_idx}::${label}"]=$config
    [[ -n $default_val ]] && DEFAULTS["${tab_idx}::${label}"]=$default_val

    local -n _reg_tab_ref="TAB_ITEMS_${tab_idx}"
    _reg_tab_ref+=("$label")

    if [[ $type == menu ]]; then
        declare -ga "SUBMENU_ITEMS_${key}=()"
    fi
}

register_child() {
    local parent_id=$1 label=$2 config=$3 default_val=${4:-}
    local key type block min max step
    IFS='|' read -r key type block min max step <<< "$config"

    if [[ ! $parent_id =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        log_err "Register Error: Menu ID '$parent_id' contains invalid characters."
        exit 1
    fi
    if ! declare -p "SUBMENU_ITEMS_${parent_id}" >/dev/null 2>&1; then
        log_err "Register Error: register_child called for unknown menu '$parent_id'."
        exit 1
    fi
    validate_item_config "$label" "$key" "$type" "$block" "$min"
    if [[ $type == menu ]]; then
        log_err "Register Error: Nested menus are not supported for '$label'."
        exit 1
    fi
    if [[ -n ${ITEM_MAP["${parent_id}::${label}"]+_} ]]; then
        log_err "Register Error: Duplicate label in menu '$parent_id': $label"
        exit 1
    fi

    ITEM_MAP["${parent_id}::${label}"]=$config
    [[ -n $default_val ]] && DEFAULTS["${parent_id}::${label}"]=$default_val

    local -n _child_ref="SUBMENU_ITEMS_${parent_id}"
    _child_ref+=("$label")
}

# =============================================================================
# LUA CONFIG CACHE
# =============================================================================

populate_config_cache() {
    local config_file=${CONFIG_FILE-}
    local tmp_proto tmp_err err_msg part state=0
    local tag="" field_a="" field_b="" field_c="" field_d="" cache_key=""
    local -A new_cache=()
    local -A seen_file=()
    local -a new_files=()

    if [[ -z $config_file || ! -f $config_file || ! -r $config_file ]]; then
        log_err "Config file missing or unreadable: ${config_file:-<unset>}"
        return 1
    fi
    [[ -n $LUA_BIN ]] || find_lua || { log_err "Lua interpreter not found"; return 1; }

    tmp_proto=$(mktemp) || { log_err "Failed to create parser IPC file"; return 1; }
    register_temp "$tmp_proto"
    tmp_err=$(mktemp) || { remove_temp "$tmp_proto"; log_err "Failed to create parser error file"; return 1; }
    register_temp "$tmp_err"

    if ! (
        ulimit -v "$LUA_MEMORY_KB" 2>/dev/null || :
        LC_ALL=C timeout --kill-after="${LUA_KILL_AFTER_SECONDS}s" "${LUA_TIMEOUT_SECONDS}s" \
            "$LUA_BIN" - "$WRITE_TARGET" "$tmp_proto" > /dev/null 2>"$tmp_err" <<'LUA'
local main_path = assert(arg[1], "missing config path")
local proto_path = assert(arg[2], "missing protocol path")

local host_io = io
local host_os = os
local out, open_err = host_io.open(proto_path, "wb")
if not out then
    host_io.stderr:write(tostring(open_err), "\n")
    host_os.exit(1)
end

local function dirname(path)
    local d = path:match("^(.*)/[^/]*$")
    if d == nil or d == "" then return "." end
    return d
end

local config_dir = dirname(main_path)
local loaded_files = {}
local loaded_file_seen = {}
local package_loaded = {}
local loading = {}
local config_root = {}
local reserved = {}

local function record_file(path)
    if not loaded_file_seen[path] then
        loaded_file_seen[path] = true
        loaded_files[#loaded_files + 1] = path
    end
end
record_file(main_path)

local function shallow_copy(src)
    local dst = {}
    for k, v in pairs(src) do dst[k] = v end
    return dst
end

local function safe_tostring(v)
    local ok, s = pcall(tostring, v)
    return ok and s or "<unprintable>"
end

local function has_nul(s)
    return type(s) == "string" and s:find("\0", 1, true) ~= nil
end

local function scalar_to_string(v)
    local t = type(v)
    if t == "string" then
        if has_nul(v) then error("NUL bytes not supported in string values", 0) end
        return v
    elseif t == "number" then
        if v ~= v or v == math.huge or v == -math.huge then error("non-finite numbers not supported", 0) end
        return tostring(v)
    elseif t == "boolean" then
        return v and "true" or "false"
    end
    error("unsupported value type: " .. t, 0)
end

local function deep_merge(dst, src, active, depth)
    if type(src) ~= "table" then return dst end
    active = active or {}
    depth = depth or 0
    if depth > 256 then error("config table nesting too deep while merging", 0) end
    if active[src] then return dst end
    active[src] = true
    for k, v in pairs(src) do
        if type(k) == "string" and k ~= "" and not has_nul(k) then
            if type(v) == "table" then
                if type(dst[k]) ~= "table" then dst[k] = {} end
                deep_merge(dst[k], v, active, depth + 1)
            elseif type(v) == "string" or type(v) == "number" or type(v) == "boolean" then
                dst[k] = v
            end
        end
    end
    active[src] = nil
    return dst
end

local inert_proxy
local proxy_mt = {
    __index = function(_, _) return inert_proxy end,
    __newindex = function(_, _, _) end,
    __call = function(_, ...) return inert_proxy end,
    __tostring = function() return "" end,
    __concat = function(a, b) return tostring(a) .. tostring(b) end,
}
inert_proxy = setmetatable({}, proxy_mt)

local hl = setmetatable({}, {
    __index = function(_, _) return inert_proxy end,
    __newindex = function(_, _, _) end,
})
rawset(hl, "config", function(tbl)
    if type(tbl) == "table" then deep_merge(config_root, tbl) end
    return inert_proxy
end)

local function normalize_module_name(name)
    if type(name) ~= "string" or name == "" or has_nul(name) then return nil end
    if name:sub(1, 1) == "/" or name:find("%.%.", 1, true) then return nil end
    return (name:gsub("%.", "/"))
end

local function path_is_allowed(path)
    if type(path) ~= "string" or path == "" or path:find("\0", 1, true) then return false end
    if path:find("%.%.", 1, true) then return false end
    if path:sub(1, 1) == "/" then
        return path == config_dir or path:sub(1, #config_dir + 1) == config_dir .. "/"
    end
    return true
end

local function file_exists(path)
    local f = host_io.open(path, "rb")
    if f then f:close(); return true end
    return false
end

local function read_file(path)
    local f, err = host_io.open(path, "rb")
    if not f then return nil, err end
    local data = f:read("*a")
    f:close()
    if has_nul(data) then return nil, "NUL bytes are not supported in Lua source" end
    return data
end

local function candidate_paths(modname)
    local norm = normalize_module_name(modname)
    if not norm then return {} end
    return {
        config_dir .. "/" .. norm .. ".lua",
        config_dir .. "/" .. norm .. "/init.lua",
    }
end

local env
local safe_package = { loaded = package_loaded, path = config_dir .. "/?.lua;" .. config_dir .. "/?/init.lua" }

local function load_text_as_chunk(text, chunkname)
    local chunk, err = load(text, "@" .. chunkname, "t", env)
    if not chunk then error(err, 0) end
    return chunk
end

local function safe_dofile(path)
    if not path_is_allowed(path) then error("dofile path outside config tree", 0) end
    if path:sub(1, 1) ~= "/" then path = config_dir .. "/" .. path end
    local text, err = read_file(path)
    if not text then error(tostring(err), 0) end
    record_file(path)
    return load_text_as_chunk(text, path)()
end

local function safe_loadfile(path, mode, custom_env)
    if mode ~= nil and mode ~= "t" then return nil, "binary chunks disabled" end
    if not path_is_allowed(path) then return nil, "path outside config tree" end
    if path:sub(1, 1) ~= "/" then path = config_dir .. "/" .. path end
    local text, err = read_file(path)
    if not text then return nil, err end
    record_file(path)
    return load(text, "@" .. path, "t", custom_env or env)
end

local function safe_load(chunk, chunkname, mode, custom_env)
    if type(chunk) ~= "string" then return nil, "only string chunks are supported" end
    if has_nul(chunk) then return nil, "NUL bytes are not supported in load()" end
    if mode ~= nil and mode ~= "t" then return nil, "binary chunks disabled" end
    return load(chunk, chunkname or "=(load)", "t", custom_env or env)
end

local function safe_require(name)
    if package_loaded[name] ~= nil then return package_loaded[name] end
    if loading[name] then return package_loaded[name] or inert_proxy end

    local paths = candidate_paths(name)
    local selected
    for _, p in ipairs(paths) do
        if file_exists(p) then selected = p; break end
    end
    if not selected then error("module not found in config directory: " .. tostring(name), 0) end

    loading[name] = true
    package_loaded[name] = inert_proxy
    local text, err = read_file(selected)
    if not text then error(tostring(err), 0) end
    record_file(selected)
    local chunk = load_text_as_chunk(text, selected)
    local result = chunk()
    if type(result) == "table" then deep_merge(config_root, result) end
    if result == nil then result = true end
    package_loaded[name] = result
    loading[name] = nil
    return result
end

local safe_os = {
    clock = os.clock,
    date = os.date,
    difftime = os.difftime,
    time = os.time,
    getenv = function(_) return nil end,
    execute = function() return nil, "sandbox", 1 end,
    exit = function() error("os.exit disabled in parser sandbox", 0) end,
    remove = function() return nil, "sandbox" end,
    rename = function() return nil, "sandbox" end,
    setlocale = function() return nil, "sandbox" end,
    tmpname = function() return nil, "sandbox" end,
}

local safe_io = {
    open = function(path, mode)
        mode = mode or "r"
        if mode ~= "r" and mode ~= "rb" then return nil, "sandbox" end
        if not path_is_allowed(path) then return nil, "path outside config tree" end
        if path:sub(1, 1) ~= "/" then path = config_dir .. "/" .. path end
        return host_io.open(path, mode)
    end,
    read = function() return nil end,
    type = host_io.type,
    write = function(...) return true end,
    flush = function() return true end,
    popen = function() return nil, "sandbox" end,
    tmpfile = function() return nil, "sandbox" end,
}

local safe_coroutine = shallow_copy(coroutine)
safe_coroutine.create = nil
safe_coroutine.wrap = nil
safe_coroutine.resume = nil
safe_coroutine.yield = nil

local safe_string = shallow_copy(string)
local safe_table = shallow_copy(table)
local safe_math = shallow_copy(math)

env = {
    _VERSION = _VERSION,
    assert = assert, error = error, ipairs = ipairs, next = next, pairs = pairs,
    pcall = pcall, rawequal = rawequal, rawget = rawget, rawlen = rawlen,
    rawset = rawset, select = select, tonumber = tonumber, tostring = tostring,
    type = type, xpcall = xpcall,
    math = safe_math, string = safe_string, table = safe_table,
    coroutine = safe_coroutine,
    os = safe_os, io = safe_io,
    package = safe_package,
    require = safe_require, dofile = safe_dofile, loadfile = safe_loadfile, load = safe_load,
    print = function(...) end,
    warn = function(...) end,
    hl = hl,
}
if utf8 then env.utf8 = shallow_copy(utf8) end
if bit32 then env.bit32 = shallow_copy(bit32) end
env._G = env
for k in pairs(env) do reserved[k] = true end

local hook_interval = 100000
local hook_steps = 0
local hook_limit = 50000000
debug.sethook(function()
    hook_steps = hook_steps + hook_interval
    if hook_steps > hook_limit then error("config evaluation exceeded instruction limit", 0) end
end, "", hook_interval)

local function evaluate_main()
    local text, err = read_file(main_path)
    if not text then error(tostring(err), 0) end
    local chunk = load_text_as_chunk(text, main_path)
    local result = chunk()
    if type(result) == "table" then deep_merge(config_root, result) end

    for k, v in pairs(env) do
        if not reserved[k] and type(v) == "table" then
            deep_merge(config_root, v)
        end
    end
end

local function valid_key(k)
    return type(k) == "string" and k ~= "" and not k:find("\0", 1, true)
        and not k:find("|", 1, true) and not k:find("/", 1, true)
end

local scope = {}
local active = {}
local function scope_text(depth)
    if depth == 0 then return "" end
    return table.concat(scope, "/", 1, depth)
end

local function walk(t, depth)
    if depth > 512 then error("table nesting too deep", 0) end
    if active[t] then return end
    active[t] = true
    local keys = {}
    for k in pairs(t) do
        if valid_key(k) then keys[#keys + 1] = k end
    end
    table.sort(keys)
    for _, k in ipairs(keys) do
        local v = rawget(t, k)
        if type(v) == "table" then
            scope[depth + 1] = k
            walk(v, depth + 1)
            scope[depth + 1] = nil
        else
            local ok, str_val = pcall(scalar_to_string, v)
            if ok then
                out:write("V", "\0", k, "\0", scope_text(depth), "\0", str_val, "\0", "", "\0")
            end
        end
    end
    active[t] = nil
end

local ok, err = xpcall(function()
    evaluate_main()
    for _, p in ipairs(loaded_files) do
        out:write("F", "\0", p, "\0", "", "\0", "", "\0", "", "\0")
    end
    walk(config_root, 0)
end, function(msg) return type(msg) == "string" and msg or safe_tostring(msg) end)

debug.sethook()
out:close()

if not ok then
    host_io.stderr:write(tostring(err), "\n")
    host_os.exit(1)
end
LUA
    ); then
        err_msg=$(<"$tmp_err")
        [[ -n $err_msg ]] || err_msg="unknown Lua parser error"
        log_err "Parser failed on $config_file: $err_msg"
        remove_temp "$tmp_proto"
        remove_temp "$tmp_err"
        return 1
    fi

    local proto_fd
    if ! exec {proto_fd}<"$tmp_proto"; then
        remove_temp "$tmp_proto"
        remove_temp "$tmp_err"
        log_err "Failed to open parser output file"
        return 1
    fi

    while IFS= read -r -d '' part <&"$proto_fd"; do
        case $state in
            0) tag=$part; state=1 ;;
            1) field_a=$part; state=2 ;;
            2) field_b=$part; state=3 ;;
            3) field_c=$part; state=4 ;;
            4)
                field_d=$part
                case $tag in
                    V)
                        cache_key="${field_a}|${field_b}"
                        new_cache["$cache_key"]=$field_c
                        ;;
                    F)
                        if [[ -n $field_a ]]; then
                            local canon_file
                            if canon_file=$(realpath -e -- "$field_a" 2>/dev/null); then
                                if [[ -z ${seen_file[$canon_file]+_} ]]; then
                                    seen_file[$canon_file]=1
                                    new_files+=("$canon_file")
                                fi
                            fi
                        fi
                        ;;
                    *)
                        exec {proto_fd}<&-
                        remove_temp "$tmp_proto"
                        remove_temp "$tmp_err"
                        log_err "Internal parser emitted an unknown record tag."
                        return 1
                        ;;
                esac
                state=0
                ;;
        esac
    done
    exec {proto_fd}<&-

    remove_temp "$tmp_proto"
    remove_temp "$tmp_err"

    if (( state != 0 )); then
        log_err "Internal parser output was truncated."
        return 1
    fi

    CONFIG_CACHE=()
    local k
    for k in "${!new_cache[@]}"; do
        CONFIG_CACHE["$k"]=${new_cache[$k]}
    done
    CONFIG_SOURCE_FILES=("${new_files[@]:-$WRITE_TARGET}")
}

# =============================================================================
# LUA MUTATOR
# =============================================================================

run_lua_mutator_for_file() {
    local src_file=$1 target_key=$2 target_scope=$3 val_file=$4
    LC_ALL=C "$LUA_BIN" - "$src_file" "$target_key" "$target_scope" "$val_file" <<'LUA'
local src_path = assert(arg[1], "missing source")
local target_key = assert(arg[2], "missing key")
local target_scope = assert(arg[3], "missing scope")
local val_path = assert(arg[4], "missing value file")

local function read_file(path)
    local f, err = io.open(path, "rb")
    if not f then io.stderr:write(tostring(err), "\n"); os.exit(4) end
    local s = f:read("*a")
    f:close()
    if s:find("\0", 1, true) then io.stderr:write("NUL bytes not supported\n"); os.exit(4) end
    return s
end

local text = read_file(src_path)
local new_value = read_file(val_path)

local len = #text
local tokens = {}
local pos = 1

local function is_alpha(c) return c:match("^[A-Za-z_]$") ~= nil end
local function is_alnum(c) return c:match("^[A-Za-z0-9_]$") ~= nil end
local function is_space(c) return c == " " or c == "\t" or c == "\r" or c == "\n" or c == "\v" or c == "\f" end
local function add(tp, val, s, e) tokens[#tokens + 1] = { type = tp, val = val, s = s, e = e } end

local function long_bracket_end_at(p)
    if text:sub(p, p) ~= "[" then return nil end
    local q = p + 1
    while q <= len and text:sub(q, q) == "=" do q = q + 1 end
    if text:sub(q, q) ~= "[" then return nil end
    local eqs = text:sub(p + 1, q - 1)
    local close = "]" .. eqs .. "]"
    local found = text:find(close, q + 1, true)
    if found then return found + #close - 1 end
    return len
end

while pos <= len do
    local c = text:sub(pos, pos)
    if is_space(c) then
        pos = pos + 1
    elseif c == "-" and text:sub(pos + 1, pos + 1) == "-" then
        pos = pos + 2
        local lb_end = long_bracket_end_at(pos)
        if lb_end then
            pos = lb_end + 1
        else
            local nl = text:find("\n", pos, true)
            if nl then pos = nl + 1 else pos = len + 1 end
        end
    elseif c == "'" or c == '"' then
        local quote = c
        local s = pos
        pos = pos + 1
        while pos <= len do
            local ch = text:sub(pos, pos)
            if ch == "\\" then
                pos = pos + 2
            elseif ch == quote then
                pos = pos + 1
                break
            else
                pos = pos + 1
            end
        end
        add("STRING", text:sub(s, pos - 1), s, pos - 1)
    elseif c == "[" then
        local lb_end = long_bracket_end_at(pos)
        if lb_end then
            add("STRING", text:sub(pos, lb_end), pos, lb_end)
            pos = lb_end + 1
        else
            add("LBRACK", c, pos, pos)
            pos = pos + 1
        end
    elseif is_alpha(c) then
        local s = pos
        pos = pos + 1
        while pos <= len and is_alnum(text:sub(pos, pos)) do pos = pos + 1 end
        add("IDENT", text:sub(s, pos - 1), s, pos - 1)
    elseif c:match("^[0-9]$") or (c == "." and text:sub(pos + 1, pos + 1):match("^[0-9]$")) then
        local s = pos
        pos = pos + 1
        while pos <= len and text:sub(pos, pos):match("^[A-Za-z0-9_%.%+%-]$") do pos = pos + 1 end
        add("NUMBER", text:sub(s, pos - 1), s, pos - 1)
    else
        local map = {
            ["{"] = "LBRACE", ["}"] = "RBRACE", ["("] = "LPAREN", [")"] = "RPAREN",
            ["["] = "LBRACK", ["]"] = "RBRACK", ["="] = "EQUALS", [","] = "COMMA",
            [";"] = "SEMI", ["."] = "DOT", [":"] = "COLON",
        }
        add(map[c] or "OTHER", c, pos, pos)
        pos = pos + 1
    end
end

local function unquote_string(raw)
    local first = raw:sub(1, 1)
    if first == "[" then
        local open_end = raw:find("%[", 2)
        local close_start = raw:match("()]=*]$")
        if not open_end or not close_start then return raw end
        local body = raw:sub(open_end + 1, close_start - 1)
        if body:sub(1, 1) == "\n" then body = body:sub(2) end
        return body
    end
    local out = {}
    local i = 2
    local last = #raw - 1
    while i <= last do
        local ch = raw:sub(i, i)
        if ch == "\\" and i < last then
            local nx = raw:sub(i + 1, i + 1)
            if nx == "n" then out[#out + 1] = "\n"
            elseif nx == "r" then out[#out + 1] = "\r"
            elseif nx == "t" then out[#out + 1] = "\t"
            elseif nx == "b" then out[#out + 1] = "\b"
            elseif nx == "f" then out[#out + 1] = "\f"
            elseif nx == "v" then out[#out + 1] = "\v"
            elseif nx == "\\" or nx == "'" or nx == '"' then out[#out + 1] = nx
            else out[#out + 1] = "\\" .. nx end
            i = i + 2
        else
            out[#out + 1] = ch
            i = i + 1
        end
    end
    return table.concat(out)
end

local function trim(s)
    return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function is_lua_number_literal(raw)
    raw = trim(raw)
    return raw:match("^[+-]?%d+%.?%d*$")
        or raw:match("^[+-]?%d+%.?%d*[eE][+-]?%d+$")
        or raw:match("^[+-]?%.%d+$")
        or raw:match("^[+-]?%.%d+[eE][+-]?%d+$")
        or raw:match("^[+-]?0[xX][%da-fA-F]+$")
end

local function format_short_string(value, quote)
    value = value:gsub("\\", "\\\\")
    value = value:gsub("\n", "\\n")
    value = value:gsub("\r", "\\r")
    value = value:gsub("\t", "\\t")
    value = value:gsub("\b", "\\b")
    value = value:gsub("\f", "\\f")
    value = value:gsub("\v", "\\v")
    local pat = quote == '"' and '"' or "'"
    value = value:gsub(pat, "\\" .. pat)
    return quote .. value .. quote
end

local function format_long_string(value, old_raw)
    local eqs = old_raw:match("^%[(=*)%[") or ""
    local open = "[" .. eqs .. "["
    local close = "]" .. eqs .. "]"
    while value:find(close, 1, true) do
        eqs = eqs .. "="
        open = "[" .. eqs .. "["
        close = "]" .. eqs .. "]"
    end
    local body = value
    if body:sub(1, 1) == "\n" then body = "\n" .. body end
    return open .. body .. close
end

local function classify_raw(raw)
    local t = trim(raw)
    if t == "true" or t == "false" then return "bool" end
    if t:match("^%[=*%[") or t:match("^['\"]") then return "string" end
    if is_lua_number_literal(t) then return "number" end
    return "expr"
end

local function format_replacement(old_raw)
    local kind = classify_raw(old_raw)
    if kind == "bool" then
        if new_value == "true" or new_value == "false" then return new_value end
        return new_value == "0" and "false" or "true"
    elseif kind == "number" then
        if not is_lua_number_literal(new_value) then error("new value is not a Lua number literal") end
        return new_value
    elseif kind == "string" then
        local t = trim(old_raw)
        if t:sub(1, 1) == "[" then return format_long_string(new_value, t) end
        return format_short_string(new_value, t:sub(1, 1))
    end
    error("target value is an expression; refusing to rewrite custom logic")
end

local matches = {}

local function scope_string(parts)
    return table.concat(parts, "/")
end

local parse_table

local function find_rhs_end(i)
    local j = i
    local depth = 0
    local fn_depth = 0
    local rhs_end = i
    while j <= #tokens do
        local tp = tokens[j].type
        local val = tokens[j].val
        if tp == "IDENT" and val == "function" then fn_depth = fn_depth + 1
        elseif tp == "IDENT" and val == "end" and fn_depth > 0 then fn_depth = fn_depth - 1
        elseif fn_depth == 0 then
            if tp == "LBRACE" or tp == "LPAREN" or tp == "LBRACK" then depth = depth + 1
            elseif tp == "RBRACE" then
                if depth == 0 then break end
                depth = depth - 1
            elseif tp == "RPAREN" or tp == "RBRACK" then
                if depth == 0 then break end
                depth = depth - 1
            elseif depth == 0 and (tp == "COMMA" or tp == "SEMI") then
                break
            end
        end
        rhs_end = j
        j = j + 1
    end
    return rhs_end, j
end

local function key_at(i)
    local tok = tokens[i]
    if not tok then return nil, i end
    if tok.type == "IDENT" and tokens[i + 1] and tokens[i + 1].type == "EQUALS" then
        return tok.val, i + 2
    end
    if tok.type == "LBRACK" and tokens[i + 1] and tokens[i + 1].type == "STRING" and tokens[i + 2]
        and tokens[i + 2].type == "RBRACK" and tokens[i + 3] and tokens[i + 3].type == "EQUALS" then
        return unquote_string(tokens[i + 1].val), i + 4
    end
    return nil, i
end

parse_table = function(i, scope_parts)
    if not tokens[i] or tokens[i].type ~= "LBRACE" then return i end
    i = i + 1
    while i <= #tokens do
        if tokens[i].type == "RBRACE" then return i + 1 end
        if tokens[i].type == "COMMA" or tokens[i].type == "SEMI" then i = i + 1 goto continue end

        local key, rhs = key_at(i)
        if key then
            local rhs_end, next_i = find_rhs_end(rhs)
            if tokens[rhs] and tokens[rhs].type == "LBRACE" then
                scope_parts[#scope_parts + 1] = key
                parse_table(rhs, scope_parts)
                scope_parts[#scope_parts] = nil
            else
                local curr_scope = scope_string(scope_parts)
                if key == target_key and curr_scope == target_scope then
                    local raw = text:sub(tokens[rhs].s, tokens[rhs_end].e)
                    matches[#matches + 1] = { s = tokens[rhs].s, e = tokens[rhs_end].e, raw = raw }
                end
            end
            i = next_i
        else
            local _, next_i = find_rhs_end(i)
            if next_i <= i then next_i = i + 1 end
            i = next_i
        end
        ::continue::
    end
    return i
end

local function is_hl_config_call(i)
    return tokens[i] and tokens[i].type == "IDENT" and tokens[i].val == "hl"
        and tokens[i + 1] and tokens[i + 1].type == "DOT"
        and tokens[i + 2] and tokens[i + 2].type == "IDENT" and tokens[i + 2].val == "config"
        and tokens[i + 3] and tokens[i + 3].type == "LPAREN"
end

local i = 1
while i <= #tokens do
    if is_hl_config_call(i) then
        local arg = i + 4
        if tokens[arg] and tokens[arg].type == "LBRACE" then
            parse_table(arg, {})
        end
        i = arg + 1
    else
        i = i + 1
    end
end

if #matches == 0 then os.exit(1) end
if #matches > 1 then os.exit(2) end

local m = matches[1]
local ok, repl_or_err = pcall(format_replacement, m.raw)
if not ok then
    io.stderr:write(tostring(repl_or_err), "\n")
    os.exit(3)
end
local new_text = text:sub(1, m.s - 1) .. repl_or_err .. text:sub(m.e + 1)
io.write(new_text)
os.exit(0)
LUA
}

write_value_to_file() {
    local requested_key=$1 new_val=$2 requested_scope=${3:-}
    local target_key target_scope cache_key current_val
    local lock_fd val_file scratch src status match_count=0 matched_src="" matched_scratch="" err_file
    local -a scratch_files=()

    LAST_WRITE_CHANGED=0

    if [[ ! -f $WRITE_TARGET || ! -r $WRITE_TARGET ]]; then
        set_status "Config file missing or unreadable."
        return 1
    fi

    normalize_target "$requested_key" "$requested_scope"
    target_key=$TARGET_KEY
    target_scope=$TARGET_SCOPE
    cache_key="${target_key}|${target_scope}"
    current_val=${CONFIG_CACHE[$cache_key]:-}

    exec {lock_fd}>>"$LOCK_TARGET"
    if ! flock -n "$lock_fd"; then
        exec {lock_fd}>&-
        set_status "Config file is locked by another process."
        return 1
    fi

    if ! populate_config_cache; then
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        set_status "Lua config parse failed; refusing to write."
        return 1
    fi

    normalize_target "$requested_key" "$requested_scope"
    target_key=$TARGET_KEY
    target_scope=$TARGET_SCOPE
    cache_key="${target_key}|${target_scope}"
    current_val=${CONFIG_CACHE[$cache_key]:-}

    if [[ -n ${CONFIG_CACHE[$cache_key]+_} && $current_val == "$new_val" ]]; then
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        return 0
    fi

    err_file=$(mktemp) || {
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        set_status "Failed to create error transfer file."
        return 1
    }
    register_temp "$err_file"

    val_file=$(mktemp) || {
        remove_temp "$err_file"
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        set_status "Failed to create value transfer file."
        return 1
    }
    register_temp "$val_file"
    printf '%s' "$new_val" > "$val_file"

    for src in "${CONFIG_SOURCE_FILES[@]:-$WRITE_TARGET}"; do
        [[ -f $src && -r $src ]] || continue
        scratch=$(mktemp) || continue
        register_temp "$scratch"
        if run_lua_mutator_for_file "$src" "$target_key" "$target_scope" "$val_file" > "$scratch" 2>"$err_file"; then
            match_count=$(( match_count + 1 ))
            matched_src=$src
            matched_scratch=$scratch
            scratch_files+=("$scratch")
        else
            status=$?
            case $status in
                1)
                    remove_temp "$scratch"
                    ;;
                2)
                    remove_temp "$scratch"
                    local sf
                    for sf in "${scratch_files[@]:-}"; do remove_temp "$sf"; done
                    remove_temp "$val_file"
                    remove_temp "$err_file"
                    flock -u "$lock_fd" || :
                    exec {lock_fd}>&-
                    set_status "Ambiguous duplicate keys in $src. Refusing to write."
                    return 1
                    ;;
                3)
                    remove_temp "$scratch"
                    local sf
                    for sf in "${scratch_files[@]:-}"; do remove_temp "$sf"; done
                    remove_temp "$val_file"
                    remove_temp "$err_file"
                    flock -u "$lock_fd" || :
                    exec {lock_fd}>&-
                    set_status "Target is computed/custom logic; refusing to overwrite."
                    return 1
                    ;;
                *)
                    remove_temp "$scratch"
                    local sf
                    for sf in "${scratch_files[@]:-}"; do remove_temp "$sf"; done
                    remove_temp "$val_file"
                    remove_temp "$err_file"
                    flock -u "$lock_fd" || :
                    exec {lock_fd}>&-
                    set_status "Lua mutator failed while parsing $src."
                    return 1
                    ;;
            esac
        fi
    done
    remove_temp "$err_file"
    remove_temp "$val_file"

    if (( match_count == 0 )); then
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        set_status "Key not found in literal hl.config table."
        return 1
    fi
    if (( match_count > 1 )); then
        local sf
        for sf in "${scratch_files[@]:-}"; do remove_temp "$sf"; done
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        set_status "Ambiguous key appears in multiple config files."
        return 1
    fi

    if [[ ! -w $matched_src ]]; then
        remove_temp "$matched_scratch"
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        set_status "Config source is not writable."
        return 1
    fi
    if [[ ! -s $matched_scratch ]]; then
        remove_temp "$matched_scratch"
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        set_status "Refusing empty write."
        return 1
    fi

    if ! create_tmpfile_for_target "$matched_src"; then
        remove_temp "$matched_scratch"
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        set_status "Atomic save unavailable."
        return 1
    fi

    if ! cat -- "$matched_scratch" > "$_TMPFILE"; then
        remove_temp "$matched_scratch"
        remove_temp "$_TMPFILE"
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        set_status "Failed to stage atomic write."
        return 1
    fi
    remove_temp "$matched_scratch"

    if ! commit_tmpfile_to_target "$matched_src"; then
        remove_temp "$_TMPFILE"
        flock -u "$lock_fd" || :
        exec {lock_fd}>&-
        set_status "Atomic save failed."
        return 1
    fi

    flock -u "$lock_fd" || :
    exec {lock_fd}>&-

    CONFIG_CACHE["$cache_key"]=$new_val
    LAST_WRITE_CHANGED=1
    return 0
}

# =============================================================================
# VALUE ENGINE
# =============================================================================

cycle_display_value() {
    local value=$1 options=$2 opt opt_dec
    local -a opts=()
    REPLY=$value
    IFS=',' read -r -a opts <<< "$options"
    for opt in "${opts[@]}"; do
        [[ $opt == "$value" ]] && { REPLY=$opt; return 0; }
    done
    if [[ $value =~ ^[0-9]+$ ]]; then
        for opt in "${opts[@]}"; do
            if [[ $opt =~ ^0[xX]([0-9a-fA-F]+)$ ]]; then
                opt_dec=$(( 16#${BASH_REMATCH[1]} ))
                [[ $value == "$opt_dec" ]] && { REPLY=$opt; return 0; }
            fi
        done
    fi
    return 0
}

load_active_values() {
    local REPLY_REF REPLY_CTX item key type block min cache_key norm_key norm_scope value
    get_active_context
    local -n _lav_items_ref="$REPLY_REF"

    for item in "${_lav_items_ref[@]}"; do
        IFS='|' read -r key type block min _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        normalize_target "$key" "$block"
        norm_key=$TARGET_KEY
        norm_scope=$TARGET_SCOPE
        cache_key="${norm_key}|${norm_scope}"
        if [[ -n ${CONFIG_CACHE[$cache_key]+_} ]]; then
            value=${CONFIG_CACHE[$cache_key]}
            if [[ $type == cycle ]]; then
                cycle_display_value "$value" "$min"
                value=$REPLY
            fi
            VALUE_CACHE["${REPLY_CTX}::${item}"]=$value
        else
            VALUE_CACHE["${REPLY_CTX}::${item}"]=$UNSET_MARKER
        fi
    done
}

calc_float() {
    local current=$1 direction=$2 step=$3 min=$4 max=$5
    LC_ALL=C "$LUA_BIN" - "$current" "$direction" "$step" "$min" "$max" <<'LUA'
local c = tonumber(arg[1]) or 0
local dir = tonumber(arg[2]) or 0
local step = tonumber(arg[3]) or 0.1
local mn = arg[4] ~= "" and tonumber(arg[4]) or nil
local mx = arg[5] ~= "" and tonumber(arg[5]) or nil
local v = c + dir * step
if mn and v < mn then v = mn end
if mx and v > mx then v = mx end
if v == 0 then v = 0 end
local s = string.format("%.6f", v):gsub("0+$", ""):gsub("%.$", "")
if s == "-0" then s = "0" end
io.write(s)
LUA
}

modify_value() {
    local label=$1
    local -i direction=$2
    local REPLY_REF REPLY_CTX key type block min max step current new_val
    get_active_context
    local -n _items_ref="$REPLY_REF"
    IFS='|' read -r key type block min max step <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    current=${VALUE_CACHE["${REPLY_CTX}::${label}"]:-}

    if [[ $current == "$UNSET_MARKER" || -z $current ]]; then
        current=${DEFAULTS["${REPLY_CTX}::${label}"]:-}
        [[ -z $current ]] && current=${min:-0}
    fi

    case $type in
        int)
            [[ $current =~ ^-?[0-9]+$ ]] || current=${min:-0}
            local sign= unsigned int_val int_step min_i max_i
            unsigned=${current#-}
            int_val=$(( 10#${unsigned:-0} ))
            [[ $current == -* ]] && int_val=$(( -int_val ))
            int_step=${step:-1}
            int_val=$(( int_val + direction * int_step ))
            if [[ -n $min ]]; then
                unsigned=${min#-}; min_i=$(( 10#${unsigned:-0} )); [[ $min == -* ]] && min_i=$(( -min_i ))
                (( int_val < min_i )) && int_val=$min_i
            fi
            if [[ -n $max ]]; then
                unsigned=${max#-}; max_i=$(( 10#${unsigned:-0} )); [[ $max == -* ]] && max_i=$(( -max_i ))
                (( int_val > max_i )) && int_val=$max_i
            fi
            new_val=$int_val
            ;;
        float)
            [[ $current =~ ^-?([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]] || current=${min:-0.0}
            new_val=$(calc_float "$current" "$direction" "${step:-0.1}" "$min" "$max")
            ;;
        bool)
            [[ $current == true ]] && new_val=false || new_val=true
            ;;
        cycle)
            local -a opts=()
            local -i count idx=0 i
            IFS=',' read -r -a opts <<< "$min"
            count=${#opts[@]}
            (( count == 0 )) && return 0
            for (( i = 0; i < count; i++ )); do
                if [[ ${opts[i]} == "$current" ]]; then idx=$i; break; fi
            done
            idx=$(( (idx + direction + count) % count ))
            new_val=${opts[idx]}
            ;;
        menu|action) return 0 ;;
        *) return 0 ;;
    esac

    if write_value_to_file "$key" "$new_val" "$block"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]=$new_val
        clear_status
        (( LAST_WRITE_CHANGED )) && post_write_action
    fi
}

set_absolute_value() {
    local label=$1 new_val=$2
    local REPLY_REF REPLY_CTX key type block
    get_active_context
    IFS='|' read -r key type block _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    if write_value_to_file "$key" "$new_val" "$block"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]=$new_val
        return 0
    fi
    return 1
}

reset_defaults() {
    local REPLY_REF REPLY_CTX item def_val type
    local -i any_written=0 any_failed=0
    get_active_context
    local -n _rd_items_ref="$REPLY_REF"

    for item in "${_rd_items_ref[@]}"; do
        IFS='|' read -r _ type _ _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        case $type in menu|action) continue ;; esac
        def_val=${DEFAULTS["${REPLY_CTX}::${item}"]:-}
        if [[ -n $def_val ]]; then
            if set_absolute_value "$item" "$def_val"; then
                (( LAST_WRITE_CHANGED )) && any_written=1
            else
                any_failed=1
            fi
        fi
    done

    (( any_written )) && post_write_action
    (( any_failed )) && set_status "Some defaults were not written." || clear_status
    return 0
}

# =============================================================================
# LINE INPUT AND SUDO
# =============================================================================

acquire_sudo() {
    if sudo -n true 2>/dev/null; then
        SUDO_AUTHENTICATED=1
        return 0
    fi

    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    [[ -n ${ORIGINAL_STTY:-} ]] && stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || :

    printf '%s%s' "$CLR_SCREEN" "$CURSOR_HOME"
    printf '\n  %s+------------------------------------------------+%s\n' "$C_MAGENTA" "$C_RESET"
    printf '  %s|%s  System operation requires administrator access  %s|%s\n' "$C_MAGENTA" "$C_YELLOW" "$C_MAGENTA" "$C_RESET"
    printf '  %s+------------------------------------------------+%s\n\n' "$C_MAGENTA" "$C_RESET"

    local -i result=0
    sudo -v 2>/dev/null || result=$?

    stty -icanon -echo min 0 time 0 < /dev/tty 2>/dev/null || :
    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"

    if (( result == 0 )); then
        SUDO_AUTHENTICATED=1
        set_status "Authentication successful."
        return 0
    fi
    set_status "Authentication failed or cancelled."
    return 1
}

prompt_line_input() {
    local prompt_text=$1 __result_var=$2 input="" prompt_row
    printf '%s%s' "$MOUSE_OFF" "$CURSOR_SHOW"
    stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || :

    prompt_row=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 6 ))
    (( prompt_row > TERM_ROWS - 1 )) && prompt_row=$(( TERM_ROWS - 1 ))
    printf '\033[%d;1H%s' "$prompt_row" "$CLR_EOS"
    printf '%s%s%s ' "$C_YELLOW" "$prompt_text" "$C_RESET"

    IFS= read -r input < /dev/tty || input=""

    stty -icanon -echo min 0 time 0 < /dev/tty 2>/dev/null || :
    printf '%s%s%s%s' "$CURSOR_HIDE" "$MOUSE_ON" "$CLR_SCREEN" "$CURSOR_HOME"

    trim_spaces "$input"
    printf -v "$__result_var" '%s' "$REPLY"
}

# =============================================================================
# RENDERING
# =============================================================================

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0; _vis_start=0; _vis_end=0; return
    fi
    (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
    (( SELECTED_ROW >= count )) && SELECTED_ROW=$(( count - 1 ))
    (( SELECTED_ROW < SCROLL_OFFSET )) && SCROLL_OFFSET=$SELECTED_ROW
    (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )) && SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
    local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
    (( max_scroll < 0 )) && max_scroll=0
    (( SCROLL_OFFSET > max_scroll )) && SCROLL_OFFSET=$max_scroll
    _vis_start=$SCROLL_OFFSET
    _vis_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    (( _vis_end > count )) && _vis_end=$count
}

render_scroll_indicator() {
    local -n _buf=$1
    local position=$2
    local -i count=$3 boundary=$4
    if [[ $position == above ]]; then
        if (( SCROLL_OFFSET > 0 )); then _buf+="${C_GREY}    ^ (more above)${CLR_EOL}${C_RESET}"$'\n'; else _buf+="${CLR_EOL}"$'\n'; fi
    else
        if (( count > MAX_DISPLAY_ROWS )); then
            local position_info="[$(( SELECTED_ROW + 1 ))/${count}]"
            if (( boundary < count )); then _buf+="${C_GREY}    v (more below) ${position_info}${CLR_EOL}${C_RESET}"$'\n'; else _buf+="${C_GREY}                   ${position_info}${CLR_EOL}${C_RESET}"$'\n'; fi
        else
            _buf+="${CLR_EOL}"$'\n'
        fi
    fi
}

render_item_list() {
    local -n _buf=$1
    local -n _items=$2
    local ctx=$3
    local -i vs=$4 ve=$5 ri
    local item val display type config padded_item max_len

    for (( ri = vs; ri < ve; ri++ )); do
        item=${_items[ri]}
        val=${VALUE_CACHE["${ctx}::${item}"]:-$UNSET_MARKER}
        config=${ITEM_MAP["${ctx}::${item}"]}
        IFS='|' read -r _ type _ _ _ _ <<< "$config"
        case $type in
            menu) display="${C_YELLOW}[+] Open Menu ...${C_RESET}" ;;
            action) display="${C_GREEN}> press Enter${C_RESET}" ;;
            *)
                case $val in
                    true) display="${C_GREEN}ON${C_RESET}" ;;
                    false) display="${C_RED}OFF${C_RESET}" ;;
                    "$UNSET_MARKER") display="${C_YELLOW}! UNSET${C_RESET}" ;;
                    *) display="${C_WHITE}${val}${C_RESET}" ;;
                esac
                ;;
        esac
        max_len=$(( ITEM_PADDING - 1 ))
        if (( ${#item} > ITEM_PADDING )); then
            printf -v padded_item "%-${max_len}s~" "${item:0:max_len}"
        else
            printf -v padded_item "%-${ITEM_PADDING}s" "$item"
        fi
        if (( ri == SELECTED_ROW )); then
            _buf+="${C_CYAN} > ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            _buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    local -i rows_rendered=$(( ve - vs ))
    for (( ri = rows_rendered; ri < MAX_DISPLAY_ROWS; ri++ )); do _buf+="${CLR_EOL}"$'\n'; done
}

draw_main_view() {
    local buf="" pad_buf="" tab_line name display_name item_var
    local -i i current_col=3 zone_start count left_pad right_pad vis_len _vis_start _vis_end

    buf+="${CURSOR_HOME}${C_MAGENTA}+${H_LINE}+${C_RESET}${CLR_EOL}"$'\n'
    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    vis_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 )); (( left_pad < 0 )) && left_pad=0
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad )); (( right_pad < 0 )) && right_pad=0
    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}|${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}|${C_RESET}${CLR_EOL}"$'\n'

    (( TAB_SCROLL_START > CURRENT_TAB )) && TAB_SCROLL_START=$CURRENT_TAB
    (( TAB_SCROLL_START < 0 )) && TAB_SCROLL_START=0
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))
    LEFT_ARROW_ZONE=""; RIGHT_ARROW_ZONE=""

    while true; do
        tab_line="${C_MAGENTA}| "
        current_col=3
        TAB_ZONES=()
        local -i used_len=0
        if (( TAB_SCROLL_START > 0 )); then
            tab_line+="${C_YELLOW}<${C_RESET} "
            LEFT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
        else
            tab_line+="  "
        fi
        used_len=$(( used_len + 2 )); current_col=$(( current_col + 2 ))

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            name=${TABS[i]}; display_name=$name
            local -i tab_name_len=${#name} chunk_len=$(( tab_name_len + 4 )) reserve=0
            (( i < TAB_COUNT - 1 )) && reserve=2
            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i < CURRENT_TAB || (i == CURRENT_TAB && TAB_SCROLL_START < CURRENT_TAB) )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 )); continue 2
                fi
                if (( i == CURRENT_TAB )); then
                    local -i avail_label=$(( max_tab_width - used_len - reserve - 4 ))
                    (( avail_label < 1 )) && avail_label=1
                    if (( tab_name_len > avail_label )); then
                        if (( avail_label == 1 )); then display_name="~"; else display_name="${name:0:avail_label-1}~"; fi
                        tab_name_len=${#display_name}; chunk_len=$(( tab_name_len + 4 ))
                    fi
                    zone_start=$current_col
                    tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}| "
                    TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
                    used_len=$(( used_len + chunk_len )); current_col=$(( current_col + chunk_len ))
                    if (( i < TAB_COUNT - 1 )); then
                        tab_line+="${C_YELLOW}> ${C_RESET}"
                        RIGHT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
                        used_len=$(( used_len + 2 ))
                    fi
                    break
                fi
                tab_line+="${C_YELLOW}> ${C_RESET}"
                RIGHT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
                used_len=$(( used_len + 2 ))
                break
            fi
            zone_start=$current_col
            if (( i == CURRENT_TAB )); then tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}| "; else tab_line+="${C_GREY} ${display_name} ${C_MAGENTA}| "; fi
            TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
            used_len=$(( used_len + chunk_len )); current_col=$(( current_col + chunk_len ))
        done
        local -i pad=$(( BOX_INNER_WIDTH - used_len - 1 ))
        if (( pad > 0 )); then printf -v pad_buf '%*s' "$pad" ''; tab_line+="$pad_buf"; fi
        tab_line+="${C_MAGENTA}|${C_RESET}"
        break
    done

    buf+="${tab_line}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}+${H_LINE}+${C_RESET}${CLR_EOL}"$'\n'

    item_var="TAB_ITEMS_${CURRENT_TAB}"
    local -n _draw_items_ref="$item_var"
    count=${#_draw_items_ref[@]}
    compute_scroll_window "$count"
    render_scroll_indicator buf above "$count" "$_vis_start"
    render_item_list buf _draw_items_ref "${CURRENT_TAB}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf below "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Tab] Category  [r] Reset  [Left/Right h/l] Adjust  [Enter] Action  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n $STATUS_MESSAGE ]]; then buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"; else buf+="${C_CYAN} File: ${C_WHITE}${WRITE_TARGET}${C_RESET}${CLR_EOL}${CLR_EOS}"; fi
    printf '%s' "$buf"
}

draw_detail_view() {
    local buf="" pad_buf="" items_var breadcrumb title sub
    local -i count pad_needed left_pad right_pad vis_len _vis_start _vis_end
    buf+="${CURSOR_HOME}${C_MAGENTA}+${H_LINE}+${C_RESET}${CLR_EOL}"$'\n'
    title=" DETAIL VIEW "; sub=" ${CURRENT_MENU_ID} "
    strip_ansi "$title"; local -i t_len=${#REPLY}; strip_ansi "$sub"; local -i s_len=${#REPLY}
    vis_len=$(( t_len + s_len )); left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 )); (( left_pad < 0 )) && left_pad=0
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad )); (( right_pad < 0 )) && right_pad=0
    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}|${pad_buf}${C_YELLOW}${title}${C_GREY}${sub}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}|${C_RESET}${CLR_EOL}"$'\n'
    breadcrumb=" < Back to ${TABS[CURRENT_TAB]}"
    strip_ansi "$breadcrumb"; local -i b_len=${#REPLY}; pad_needed=$(( BOX_INNER_WIDTH - b_len )); (( pad_needed < 0 )) && pad_needed=0
    printf -v pad_buf '%*s' "$pad_needed" ''
    buf+="${C_MAGENTA}|${C_CYAN}${breadcrumb}${C_RESET}${pad_buf}${C_MAGENTA}|${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}+${H_LINE}+${C_RESET}${CLR_EOL}"$'\n'

    items_var="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
    local -n _detail_items_ref="$items_var"
    count=${#_detail_items_ref[@]}
    compute_scroll_window "$count"
    render_scroll_indicator buf above "$count" "$_vis_start"
    render_item_list buf _detail_items_ref "${CURRENT_MENU_ID}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf below "$count" "$_vis_end"
    buf+=$'\n'"${C_CYAN} [Esc/Sh+Tab] Back  [r] Reset  [Left/Right h/l] Adjust  [Enter] Toggle  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n $STATUS_MESSAGE ]]; then buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"; else buf+="${C_CYAN} Submenu: ${C_WHITE}${CURRENT_MENU_ID}${C_RESET}${CLR_EOL}${CLR_EOS}"; fi
    printf '%s' "$buf"
}

draw_picker_view() {
    local buf="" pad_buf="" title sub breadcrumb item hint padded hint_trim
    local -i left_pad right_pad vis_len pad_needed count i vstart vend rows_rendered max_len
    buf+="${CURSOR_HOME}${C_MAGENTA}+${H_LINE}+${C_RESET}${CLR_EOL}"$'\n'
    title=" PICKER "; sub=" ${PICKER_TITLE} "
    strip_ansi "$title"; local -i t_len=${#REPLY}; strip_ansi "$sub"; local -i s_len=${#REPLY}
    vis_len=$(( t_len + s_len )); left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 )); (( left_pad < 0 )) && left_pad=0
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad )); (( right_pad < 0 )) && right_pad=0
    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}|${pad_buf}${C_YELLOW}${title}${C_GREY}${sub}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}|${C_RESET}${CLR_EOL}"$'\n'
    breadcrumb=" < Esc to cancel"; strip_ansi "$breadcrumb"; local -i b_len=${#REPLY}; pad_needed=$(( BOX_INNER_WIDTH - b_len )); (( pad_needed < 0 )) && pad_needed=0
    printf -v pad_buf '%*s' "$pad_needed" ''
    buf+="${C_MAGENTA}|${C_CYAN}${breadcrumb}${C_RESET}${pad_buf}${C_MAGENTA}|${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}+${H_LINE}+${C_RESET}${CLR_EOL}"$'\n'

    count=${#PICKER_ITEMS[@]}
    if (( count == 0 )); then
        PICKER_SELECTED=0; PICKER_SCROLL=0
    else
        (( PICKER_SELECTED < 0 )) && PICKER_SELECTED=0
        (( PICKER_SELECTED >= count )) && PICKER_SELECTED=$(( count - 1 ))
        (( PICKER_SELECTED < PICKER_SCROLL )) && PICKER_SCROLL=$PICKER_SELECTED
        (( PICKER_SELECTED >= PICKER_SCROLL + MAX_DISPLAY_ROWS )) && PICKER_SCROLL=$(( PICKER_SELECTED - MAX_DISPLAY_ROWS + 1 ))
        local -i max_scroll=$(( count - MAX_DISPLAY_ROWS )); (( max_scroll < 0 )) && max_scroll=0; (( PICKER_SCROLL > max_scroll )) && PICKER_SCROLL=$max_scroll
    fi
    vstart=$PICKER_SCROLL; vend=$(( PICKER_SCROLL + MAX_DISPLAY_ROWS )); (( vend > count )) && vend=$count
    (( PICKER_SCROLL > 0 )) && buf+="${C_GREY}    ^ (more above)${CLR_EOL}${C_RESET}"$'\n' || buf+="${CLR_EOL}"$'\n'
    max_len=$(( ITEM_PADDING - 1 ))
    for (( i = vstart; i < vend; i++ )); do
        item=${PICKER_ITEMS[i]}; hint=${PICKER_HINTS[i]:-}
        if (( ${#item} > ITEM_PADDING )); then printf -v padded "%-${max_len}s~" "${item:0:max_len}"; else printf -v padded "%-${ITEM_PADDING}s" "$item"; fi
        hint_trim=$hint; (( ${#hint_trim} > 32 )) && hint_trim="${hint_trim:0:31}~"
        if (( i == PICKER_SELECTED )); then buf+="${C_CYAN} > ${C_INVERSE}${padded}${C_RESET} ${C_GREY}${hint_trim}${C_RESET}${CLR_EOL}"$'\n'; else buf+="    ${padded} ${C_GREY}${hint_trim}${C_RESET}${CLR_EOL}"$'\n'; fi
    done
    rows_rendered=$(( vend - vstart )); for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do buf+="${CLR_EOL}"$'\n'; done
    if (( count > MAX_DISPLAY_ROWS )); then
        local pos_info="[$(( PICKER_SELECTED + 1 ))/${count}]"
        (( vend < count )) && buf+="${C_GREY}    v (more below) ${pos_info}${CLR_EOL}${C_RESET}"$'\n' || buf+="${C_GREY}                   ${pos_info}${CLR_EOL}${C_RESET}"$'\n'
    else
        buf+="${CLR_EOL}"$'\n'
    fi
    buf+=$'\n'"${C_CYAN} [Up/Down j/k] Navigate  [Enter] Select  [Esc] Cancel  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n $STATUS_MESSAGE ]]; then buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"; elif (( count == 0 )); then buf+="${C_CYAN} ${C_YELLOW}(no items - press Esc to go back)${C_RESET}${CLR_EOL}${CLR_EOS}"; else buf+="${C_CYAN} ${count} item(s)${C_RESET}${CLR_EOL}${CLR_EOS}"; fi
    printf '%s' "$buf"
}

draw_ui() {
    update_terminal_size
    if ! terminal_size_ok; then draw_small_terminal_notice; return; fi
    case $CURRENT_VIEW in
        0) draw_main_view ;;
        1) draw_detail_view ;;
        2) draw_picker_view ;;
    esac
}

# =============================================================================
# NAVIGATION AND INPUT
# =============================================================================

exit_picker() {
    CURRENT_VIEW=0
    SELECTED_ROW=$PARENT_ROW
    SCROLL_OFFSET=$PARENT_SCROLL
    PICKER_ITEMS=(); PICKER_HINTS=(); PICKER_TITLE=""; PICKER_CALLBACK=""
    load_active_values
}

picker_navigate() {
    local -i dir=$1 count=${#PICKER_ITEMS[@]}
    (( count == 0 )) && return 0
    PICKER_SELECTED=$(( (PICKER_SELECTED + dir + count) % count ))
}

picker_confirm() {
    local -i count=${#PICKER_ITEMS[@]}
    (( count == 0 )) && { exit_picker; return; }
    local chosen=${PICKER_ITEMS[PICKER_SELECTED]} cb=$PICKER_CALLBACK
    exit_picker
    [[ -n $cb && $(type -t "$cb") == function ]] && "$cb" "$chosen"
}

navigate() {
    local -i dir=$1 count
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _nav_items_ref="$REPLY_REF"
    count=${#_nav_items_ref[@]}
    (( count == 0 )) && return 0
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
    clear_status
}

navigate_page() {
    local -i dir=$1 count
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _items_ref="$REPLY_REF"
    count=${#_items_ref[@]}
    (( count == 0 )) && return 0
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    (( SELECTED_ROW < 0 )) && SELECTED_ROW=0
    (( SELECTED_ROW >= count )) && SELECTED_ROW=$(( count - 1 ))
    clear_status
}

navigate_end() {
    local -i target=$1 count
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _items_ref="$REPLY_REF"
    count=${#_items_ref[@]}
    (( count == 0 )) && return 0
    (( target == 0 )) && SELECTED_ROW=0 || SELECTED_ROW=$(( count - 1 ))
    clear_status
}

adjust() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX label type
    get_active_context
    local -n _items_ref="$REPLY_REF"
    (( ${#_items_ref[@]} == 0 )) && return 0
    label=${_items_ref[SELECTED_ROW]}
    IFS='|' read -r _ type _ _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    [[ $type == action ]] && return 0
    modify_value "$label" "$dir"
}

switch_tab() {
    local -i dir=${1:-1}
    TAB_SAVED_ROW[CURRENT_TAB]=$SELECTED_ROW
    TAB_SAVED_SCROLL[CURRENT_TAB]=$SCROLL_OFFSET
    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
    SELECTED_ROW=${TAB_SAVED_ROW[CURRENT_TAB]:-0}
    SCROLL_OFFSET=${TAB_SAVED_SCROLL[CURRENT_TAB]:-0}
    load_active_values
    clear_status
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        TAB_SAVED_ROW[CURRENT_TAB]=$SELECTED_ROW
        TAB_SAVED_SCROLL[CURRENT_TAB]=$SCROLL_OFFSET
        CURRENT_TAB=$idx
        SELECTED_ROW=${TAB_SAVED_ROW[CURRENT_TAB]:-0}
        SCROLL_OFFSET=${TAB_SAVED_SCROLL[CURRENT_TAB]:-0}
        load_active_values
        clear_status
    fi
}

activate_item() {
    local REPLY_REF REPLY_CTX item config key type
    get_active_context
    local -n _act_ref="$REPLY_REF"
    (( ${#_act_ref[@]} == 0 )) && return 1
    item=${_act_ref[SELECTED_ROW]}
    config=${ITEM_MAP["${REPLY_CTX}::${item}"]}
    IFS='|' read -r key type _ _ _ _ <<< "$config"
    case $type in
        menu)
            PARENT_ROW=$SELECTED_ROW; PARENT_SCROLL=$SCROLL_OFFSET
            CURRENT_MENU_ID=$key; CURRENT_VIEW=1; SELECTED_ROW=0; SCROLL_OFFSET=0
            load_active_values
            return 0
            ;;
        action)
            if [[ $(type -t "action_${key}") == function ]]; then
                "action_${key}"
                load_active_values
            else
                set_status "No handler defined for action: $key"
            fi
            return 0
            ;;
    esac
    return 1
}

go_back() {
    CURRENT_VIEW=0
    SELECTED_ROW=$PARENT_ROW
    SCROLL_OFFSET=$PARENT_SCROLL
    load_active_values
    clear_status
}

handle_mouse() {
    local input=$1 body terminator field1 field2 field3 zone
    local -i button x y i start end effective_start clicked_idx count
    body=${input#'[<'}
    [[ $body == "$input" ]] && return 0
    terminator=${body: -1}
    [[ $terminator != M && $terminator != m ]] && return 0
    body=${body%[Mm]}
    IFS=';' read -r field1 field2 field3 <<< "$body"
    [[ $field1 =~ ^[0-9]+$ && $field2 =~ ^[0-9]+$ && $field3 =~ ^[0-9]+$ ]] || return 0
    button=$field1; x=$field2; y=$field3
    (( button == 64 )) && { navigate -1; return 0; }
    (( button == 65 )) && { navigate 1; return 0; }
    [[ $terminator != M ]] && return 0

    if (( y == TAB_ROW )); then
        if (( CURRENT_VIEW == 0 )); then
            if [[ -n $LEFT_ARROW_ZONE ]]; then start=${LEFT_ARROW_ZONE%%:*}; end=${LEFT_ARROW_ZONE##*:}; (( x >= start && x <= end )) && { switch_tab -1; return 0; }; fi
            if [[ -n $RIGHT_ARROW_ZONE ]]; then start=${RIGHT_ARROW_ZONE%%:*}; end=${RIGHT_ARROW_ZONE##*:}; (( x >= start && x <= end )) && { switch_tab 1; return 0; }; fi
            for (( i = 0; i < ${#TAB_ZONES[@]}; i++ )); do
                zone=${TAB_ZONES[i]}; start=${zone%%:*}; end=${zone##*:}
                (( x >= start && x <= end )) && { set_tab "$(( i + TAB_SCROLL_START ))"; return 0; }
            done
        else
            (( button == 0 )) && go_back
            return 0
        fi
    fi

    effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))
        local target_var_name
        (( CURRENT_VIEW == 0 )) && target_var_name="TAB_ITEMS_${CURRENT_TAB}" || target_var_name="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
        local -n _mouse_items_ref="$target_var_name"
        count=${#_mouse_items_ref[@]}
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            if (( x > ADJUST_THRESHOLD )); then
                (( button == 0 )) && { activate_item || adjust 1; }
                (( button == 2 )) && adjust -1
            fi
        fi
    fi
    return 0
}

handle_mouse_picker() {
    local input=$1 body terminator field1 field2 field3
    local -i button x y effective_start clicked_idx count
    body=${input#'[<'}
    [[ $body == "$input" ]] && return 0
    terminator=${body: -1}
    [[ $terminator != M && $terminator != m ]] && return 0
    body=${body%[Mm]}
    IFS=';' read -r field1 field2 field3 <<< "$body"
    [[ $field1 =~ ^[0-9]+$ && $field2 =~ ^[0-9]+$ && $field3 =~ ^[0-9]+$ ]] || return 0
    button=$field1; x=$field2; y=$field3
    (( button == 64 )) && { picker_navigate -1; return 0; }
    (( button == 65 )) && { picker_navigate 1; return 0; }
    [[ $terminator != M ]] && return 0
    effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        clicked_idx=$(( y - effective_start + PICKER_SCROLL ))
        count=${#PICKER_ITEMS[@]}
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            PICKER_SELECTED=$clicked_idx
            (( button == 0 )) && picker_confirm
        fi
    fi
    return 0
}

read_escape_seq() {
    local -n _esc_out=$1
    _esc_out=""
    local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; then return 1; fi
    _esc_out+=$char
    if [[ $char == '[' || $char == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
            _esc_out+=$char
            [[ $char =~ [a-zA-Z~] ]] && break
        done
    fi
    return 0
}

handle_key_main() {
    local key=$1
    case $key in
        '[Z') switch_tab -1; return ;;
        '[A'|'OA') navigate -1; return ;;
        '[B'|'OB') navigate 1; return ;;
        '[C'|'OC') adjust 1; return ;;
        '[D'|'OD') adjust -1; return ;;
        '[5~') navigate_page -1; return ;;
        '[6~') navigate_page 1; return ;;
        '[H'|'[1~') navigate_end 0; return ;;
        '[F'|'[4~') navigate_end 1; return ;;
        '['*'<'*[Mm]) handle_mouse "$key"; return ;;
    esac
    case $key in
        k|K) navigate -1 ;;
        j|J) navigate 1 ;;
        l|L) adjust 1 ;;
        h|H) adjust -1 ;;
        g) navigate_end 0 ;;
        G) navigate_end 1 ;;
        $'\t') switch_tab 1 ;;
        r|R) reset_defaults ;;
        ''|$'\n') activate_item || adjust 1 ;;
        $'\x7f'|$'\x08'|$'\e\n') adjust -1 ;;
        q|Q|$'\x03') exit 0 ;;
    esac
}

handle_key_detail() {
    local key=$1
    case $key in
        '[A'|'OA') navigate -1; return ;;
        '[B'|'OB') navigate 1; return ;;
        '[C'|'OC') adjust 1; return ;;
        '[D'|'OD') adjust -1; return ;;
        '[5~') navigate_page -1; return ;;
        '[6~') navigate_page 1; return ;;
        '[H'|'[1~') navigate_end 0; return ;;
        '[F'|'[4~') navigate_end 1; return ;;
        '[Z') go_back; return ;;
        '['*'<'*[Mm]) handle_mouse "$key"; return ;;
    esac
    case $key in
        ESC) go_back ;;
        k|K) navigate -1 ;;
        j|J) navigate 1 ;;
        l|L) adjust 1 ;;
        h|H) adjust -1 ;;
        g) navigate_end 0 ;;
        G) navigate_end 1 ;;
        r|R) reset_defaults ;;
        ''|$'\n') activate_item || adjust 1 ;;
        $'\x7f'|$'\x08'|$'\e\n') adjust -1 ;;
        q|Q|$'\x03') exit 0 ;;
    esac
}

handle_key_picker() {
    local key=$1
    case $key in
        '[A'|'OA') picker_navigate -1; return ;;
        '[B'|'OB') picker_navigate 1; return ;;
        '[5~') picker_navigate -$MAX_DISPLAY_ROWS; return ;;
        '[6~') picker_navigate $MAX_DISPLAY_ROWS; return ;;
        '[H'|'[1~') PICKER_SELECTED=0; return ;;
        '[F'|'[4~') PICKER_SELECTED=$(( ${#PICKER_ITEMS[@]} - 1 )); return ;;
        '['*'<'*[Mm]) handle_mouse_picker "$key"; return ;;
    esac
    case $key in
        ESC) exit_picker ;;
        k|K) picker_navigate -1 ;;
        j|J) picker_navigate 1 ;;
        g) PICKER_SELECTED=0 ;;
        G) PICKER_SELECTED=$(( ${#PICKER_ITEMS[@]} - 1 )) ;;
        ''|$'\n') picker_confirm ;;
        q|Q|$'\x03') exit 0 ;;
    esac
}

handle_input_router() {
    local key=$1 escape_seq=""
    if [[ $key == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key=$escape_seq
            [[ $key == "" || $key == $'\n' ]] && key=$'\e\n'
        else
            key=ESC
        fi
    fi
    if ! terminal_size_ok; then
        case $key in q|Q|$'\x03') exit 0 ;; esac
        return 0
    fi
    case $CURRENT_VIEW in
        0) handle_key_main "$key" ;;
        1) handle_key_detail "$key" ;;
        2) handle_key_picker "$key" ;;
    esac
}

# =============================================================================
# ENTRYPOINT
# =============================================================================

parse_args() {
    while (($#)); do
        case $1 in
            --config)
                shift
                [[ $# -gt 0 ]] || { log_err "--config requires a path"; exit 2; }
                CONFIG_FILE=$1
                ;;
            --help|-h)
                printf 'Usage: %s [--config /path/to/hyprland.lua]\n' "${0##*/}"
                exit 0
                ;;
            *)
                log_err "Unknown argument: $1"
                exit 2
                ;;
        esac
        shift
    done
}

main() {
    parse_args "$@"

    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5.0+ required"; exit 1; fi
    if [[ ! -t 0 || ! -t 1 ]]; then log_err "Interactive TTY stdin/stdout required"; exit 1; fi
    if [[ ! -f $CONFIG_FILE ]]; then log_err "Config not found: $CONFIG_FILE"; exit 1; fi

    local dep
    for dep in realpath mktemp timeout flock sync cat chmod chown mv rm stty sudo; do
        command -v "$dep" >/dev/null 2>&1 || { log_err "Missing dependency: $dep"; exit 1; }
    done
    find_lua || { log_err "Lua interpreter not found"; exit 1; }

    resolve_write_target
    if [[ ! -w $WRITE_TARGET ]]; then log_err "Config not writable: $CONFIG_FILE"; exit 1; fi

    register_items
    populate_config_cache || exit 1

    ORIGINAL_STTY=$(stty -g < /dev/tty 2>/dev/null) || ORIGINAL_STTY=""
    if [[ -z $ORIGINAL_STTY ]]; then log_err "Failed to read terminal settings. A controlling TTY is required."; exit 1; fi
    stty -icanon -echo min 0 time 0 < /dev/tty 2>/dev/null || { log_err "Failed to configure terminal raw input."; exit 1; }

    TUI_STARTED=1
    printf '%s%s%s%s%s' "$ALT_SCREEN_ON" "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    load_active_values
    trap 'RESIZE_PENDING=1' WINCH

    local key
    while true; do
        draw_ui
        if IFS= read -rsn1 -t "$READ_LOOP_TIMEOUT" key; then
            (( RESIZE_PENDING )) && RESIZE_PENDING=0
            handle_input_router "$key"
        else
            if (( RESIZE_PENDING )); then RESIZE_PENDING=0; fi
        fi
    done
}

main "$@"
