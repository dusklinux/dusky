#!/usr/bin/env bash
# Flat / [section] key-value engine. See README.md for grammar and contract.
# Uses the shared helpers from frontend/core_types.sh; no terminal/UI calls.
# shellcheck disable=SC2034 # Engine result fields are consumed by the frontend.

declare -ga ENGINE_DEPENDENCIES=(realpath mktemp flock stat chmod chown mv rm awk mkdir sha256sum)
declare -g KV_SIGNATURE="" KV_LOCK_TARGET="" KV_TMPFILE="" KV_TMPMODE=""
declare -ga KV_TEMP_PATHS=()

kv_register_temp() {
    local path=$1
    [[ -n $path ]] && KV_TEMP_PATHS+=("$path")
}

kv_forget_temp() {
    local path=$1 kept=() item
    for item in "${KV_TEMP_PATHS[@]}"; do
        [[ $item == "$path" ]] || kept+=("$item")
    done
    KV_TEMP_PATHS=("${kept[@]}")
}

kv_remove_temp() {
    local path=$1
    [[ -n $path && -e $path ]] && rm -f -- "$path" 2>/dev/null || :
    kv_forget_temp "$path"
}

kv_file_signature() {
    local path=$1
    LC_ALL=C stat -Lc '%d:%i:%s:%y:%z:%a:%u:%g' -- "$path" 2>/dev/null
}

kv_release_lock_fd() {
    local fd=${1:-}
    if [[ $fd =~ ^[0-9]+$ ]]; then
        flock -u "$fd" 2>/dev/null || :
        { exec {fd}>&-; } 2>/dev/null || :
    fi
}

engine_init() {
    local config_path=$1
    ENGINE_MESSAGE=""
    [[ -n $config_path ]] || { ENGINE_MESSAGE="Config path is empty."; return 1; }
    path_dirname "$config_path"
    mkdir -p -- "$REPLY" 2>/dev/null || {
        ENGINE_MESSAGE="Unable to create config directory."; return 1;
    }
    # Opening an existing config must not change its timestamp.
    if [[ ! -e $config_path ]]; then
        ( set -o noclobber; : > "$config_path" ) 2>/dev/null || {
            ENGINE_MESSAGE="Unable to create config file."; return 1;
        }
    fi
    ENGINE_TARGET=$(realpath -e -- "$config_path" 2>/dev/null) || {
        ENGINE_MESSAGE="Unable to resolve config path."; return 1;
    }
    [[ -f $ENGINE_TARGET && -r $ENGINE_TARGET ]] || {
        ENGINE_MESSAGE="Config must be a readable regular file."; return 1;
    }
    local lock_dir="${XDG_RUNTIME_DIR:-/tmp}/dusky_tui_locks_${UID}" digest
    mkdir -p -- "$lock_dir" 2>/dev/null || {
        ENGINE_MESSAGE="Unable to create config lock directory."; return 1;
    }
    digest=$(printf '%s' "$ENGINE_TARGET" | sha256sum) || return 1
    KV_LOCK_TARGET="${lock_dir}/${digest%% *}.lock"
}

kv_create_tmpfile_for_target() {
    local target=$1 target_dir
    if [[ -n ${KV_TMPFILE:-} ]]; then
        kv_remove_temp "$KV_TMPFILE"
    fi
    KV_TMPFILE=""
    KV_TMPMODE=""

    path_dirname "$target"; target_dir=$REPLY

    if ! KV_TMPFILE=$(mktemp --tmpdir="$target_dir" ".dusky.tmp.XXXXXXXXXX" 2>/dev/null); then
        KV_TMPFILE=""
        KV_TMPMODE=""
        return 1
    fi
    KV_TMPMODE="atomic"
    kv_register_temp "$KV_TMPFILE"
    return 0
}

kv_commit_tmpfile_to_target() {
    local target=$1
    [[ -n ${KV_TMPFILE:-} && -f $KV_TMPFILE && ${KV_TMPMODE:-} == atomic ]] || return 1
    [[ -e $target && -f $target ]] || return 1

    chown --reference="$target" -- "$KV_TMPFILE" 2>/dev/null || return 1
    chmod --reference="$target" -- "$KV_TMPFILE" 2>/dev/null || return 1
    mv -fT --no-copy -- "$KV_TMPFILE" "$target" || return 1

    kv_forget_temp "$KV_TMPFILE"
    KV_TMPFILE=""
    KV_TMPMODE=""
    return 0
}

engine_load() {
    ENGINE_MESSAGE=""
    local target_path=${ENGINE_TARGET:-}
    local current_scope="" k v line before after
    local -A parsed_cache=()
    KV_SIGNATURE=""

    if [[ -z $target_path || ! -f $target_path || ! -r $target_path ]]; then
        ENGINE_MESSAGE="Config is missing or unreadable."
        return 1
    fi

    before=$(kv_file_signature "$target_path") || { ENGINE_MESSAGE="Unable to inspect config."; return 1; }
    while IFS= read -r line || [[ -n $line ]]; do
        trim_spaces "$line"; line=$REPLY
        [[ -z $line || $line == \#* || $line == \;* ]] && continue

        if [[ $line =~ ^\[(.*)\]$ ]]; then
            current_scope="${BASH_REMATCH[1]}"
            trim_spaces "$current_scope"; current_scope=$REPLY
        elif [[ $line =~ ^([^=[:space:]]+)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
            k="${BASH_REMATCH[1]}"
            v="${BASH_REMATCH[2]}"
            trim_spaces "$k"; k=$REPLY
            trim_spaces "$v"; v=$REPLY
            if [[ $v == \"*\" || $v == \'*\' ]]; then
                v="${v:1:-1}"
            fi
            parsed_cache["${k}|${current_scope}"]=$v
        elif [[ $line =~ ^([^=[:space:]]+)[[:space:]]+(.*)$ ]]; then
            k=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}
            trim_spaces "$v"; v=$REPLY
            if [[ $v == \"*\" || $v == \'*\' ]]; then v="${v:1:-1}"; fi
            parsed_cache["${k}|${current_scope}"]=$v
        fi
    done < "$target_path" || { ENGINE_MESSAGE="Unable to read config."; return 1; }
    if ! after=$(kv_file_signature "$target_path") || [[ $before != "$after" ]]; then
        ENGINE_MESSAGE="Config changed while being read; retry."
        return 1
    fi
    # Publish only a complete, stable read. Failed reloads retain every view.
    ENGINE_STATE=()
    for k in "${!parsed_cache[@]}"; do ENGINE_STATE["$k"]=${parsed_cache["$k"]}; done
    KV_SIGNATURE=$after
    return 0
}

engine_write() {
    ENGINE_MESSAGE=""
    local target_key=$1 new_val=$2 target_scope=${3:-} operation=${4:-set}
    local cache_key lock_fd="" before after encoded
    ENGINE_CHANGED=0
    trim_spaces "$target_scope"; target_scope=$REPLY
    cache_key="${target_key}|${target_scope}"
    if [[ $target_scope == *[$'\n\r'\[\]\|]* || $target_key == [\#\;\[]* ]]; then
        ENGINE_MESSAGE="Invalid scope or key."; return 1
    fi
    if [[ $operation != set && $operation != delete ]] ||
       [[ $target_key == *[[:space:]=\|]* || -z $target_key ||
          $new_val == *$'\n'* || $new_val == *$'\r'* ]]; then
        ENGINE_MESSAGE="Invalid key, operation, or multiline value."
        return 1
    fi
    if [[ -z $ENGINE_TARGET || -z $KV_LOCK_TARGET ]]; then
        ENGINE_MESSAGE="Config path is not initialized."; return 1
    fi
    if ! { exec {lock_fd}>>"$KV_LOCK_TARGET"; } 2>/dev/null; then
        ENGINE_MESSAGE="Unable to open config lock."; return 1
    fi
    if ! flock -x -n "$lock_fd" 2>/dev/null; then
        kv_release_lock_fd "$lock_fd"
        ENGINE_MESSAGE="Config file is locked by another process."; return 1
    fi
    # Validate the cache under the lock. Reload only after an external change;
    # unchanged large files need one streaming AWK mutation, no Bash reparse.
    if ! before=$(kv_file_signature "$ENGINE_TARGET"); then
        kv_release_lock_fd "$lock_fd"
        ENGINE_MESSAGE="Config is missing or unreadable."; return 1
    fi
    if [[ $before != "$KV_SIGNATURE" ]]; then
        if ! engine_load; then kv_release_lock_fd "$lock_fd"; return 1; fi
        before=$KV_SIGNATURE
    fi
    # Optional compare-and-swap protects relative edits, including stale no-ops.
    # Arguments 5/6 are the expected presence (0/1) and raw cached value.
    # Argument 7 is the schema type, for writers that need typed serialization.
    # This literal key-value writer does not need it. Empty argument 5 omits CAS.
    if [[ -n ${5-} ]]; then
        local actual_present=0
        [[ ${ENGINE_STATE[$cache_key]+present} ]] && actual_present=1
        if [[ $actual_present != "$5" || ${ENGINE_STATE[$cache_key]-} != "${6-}" ]]; then
            kv_release_lock_fd "$lock_fd"
            ENGINE_MESSAGE="Setting changed externally; refreshed. Retry the adjustment."
            return 1
        fi
    fi
    if { [[ $operation == delete && ! ${ENGINE_STATE[$cache_key]+present} ]]; } ||
       { [[ $operation == set && ${ENGINE_STATE[$cache_key]+present} &&
            ${ENGINE_STATE[$cache_key]} == "$new_val" ]]; }; then
        kv_release_lock_fd "$lock_fd"; return 0
    fi
    if [[ ! -w $ENGINE_TARGET ]] || ! kv_create_tmpfile_for_target "$ENGINE_TARGET"; then
        kv_release_lock_fd "$lock_fd"
        ENGINE_MESSAGE="Atomic save unavailable; check file and directory permissions."; return 1
    fi
    encoded=$new_val
    # Preserve whitespace and literal outer quotes in the documented grammar.
    if [[ $new_val == [[:space:]]* || $new_val == *[[:space:]] ||
          $new_val == \"*\" || $new_val == \'*\' ]]; then encoded="\"${new_val}\""; fi
    # ENVIRON preserves literal backslashes, unlike awk -v string assignments.
    if ! DUSKY_SCOPE="$target_scope" DUSKY_KEY="$target_key" DUSKY_VALUE="$encoded" \
         DUSKY_OPERATION="$operation" awk '
        BEGIN {
            scope = ENVIRON["DUSKY_SCOPE"]; key = ENVIRON["DUSKY_KEY"]
            val = ENVIRON["DUSKY_VALUE"]; deleting = ENVIRON["DUSKY_OPERATION"] == "delete"
            in_scope = (scope == ""); seen_scope = in_scope; found = 0
        }
        {
            if (NR == 1) eol = ($0 ~ /\r$/ ? "\r" : "")
            line = $0; sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+$/, "", line)
        }
        line ~ /^\[.*\]$/ {
            if (in_scope && !found && !deleting) { print key "=" val eol; found = 1 }
            sec = substr(line, 2, length(line) - 2)
            sub(/^[[:space:]]+/, "", sec); sub(/[[:space:]]+$/, "", sec)
            in_scope = (sec == scope); if (in_scope) seen_scope = 1
            print; next
        }
        {
            if (in_scope && line !~ /^[#;]/) {
                k = line; sub(/[=[:space:]].*$/, "", k)
                if (k == key && line ~ /[=[:space:]]/) {
                    if (!found && !deleting) {
                        indent = $0; sub(/[^[:space:]].*$/, "", indent)
                        rest = substr($0, length(indent) + length(key) + 1)
                        sub(/\r$/, "", rest)
                        # Keep the existing assignment operator and its spacing.
                        if (match(rest, /^[[:space:]]*=[[:space:]]*/)) sep = substr(rest, 1, RLENGTH)
                        else if (match(rest, /^[[:space:]]+/)) sep = substr(rest, 1, RLENGTH)
                        else sep = "="
                        print indent key sep val eol; found = 1
                    }
                    next
                }
            }
            print
        }
        END {
            if (!found && !deleting) {
                if (!seen_scope) print eol "\n[" scope "]" eol
                print key "=" val eol
            }
        }
    ' "$ENGINE_TARGET" > "$KV_TMPFILE" 2>/dev/null; then
        kv_remove_temp "$KV_TMPFILE"; kv_release_lock_fd "$lock_fd"
        ENGINE_MESSAGE="Failed to modify configuration."; return 1
    fi
    # Catch non-cooperating writers during staging; flock coordinates this engine.
    if ! after=$(kv_file_signature "$ENGINE_TARGET") || [[ $before != "$after" ]]; then
        kv_remove_temp "$KV_TMPFILE"; kv_release_lock_fd "$lock_fd"
        engine_load || :
        ENGINE_MESSAGE="Config changed during save; retry the edit."; return 1
    fi
    if ! kv_commit_tmpfile_to_target "$ENGINE_TARGET"; then
        kv_remove_temp "$KV_TMPFILE"; kv_release_lock_fd "$lock_fd"
        ENGINE_MESSAGE="Atomic save failed."; return 1
    fi
    if [[ $operation == delete ]]; then
        unset 'ENGINE_STATE[$cache_key]'
    else
        ENGINE_STATE["$cache_key"]=$new_val
    fi
    KV_SIGNATURE=$(kv_file_signature "$ENGINE_TARGET") || KV_SIGNATURE=""
    kv_release_lock_fd "$lock_fd"
    ENGINE_CHANGED=1
    return 0
}

engine_cleanup() {
    local path
    for path in "${KV_TEMP_PATHS[@]}"; do
        [[ -n $path && -e $path ]] && rm -f -- "$path" 2>/dev/null || :
    done
    KV_TEMP_PATHS=()
    KV_TMPFILE=""; KV_TMPMODE=""
}
