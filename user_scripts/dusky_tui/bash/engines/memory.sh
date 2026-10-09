#!/usr/bin/env bash
# Example alternative engine for prototyping schemas. Nothing is persisted.
# shellcheck disable=SC2034 # Result fields are consumed by the frontend.

declare -ga ENGINE_DEPENDENCIES=()

engine_init() {
    ENGINE_TARGET="memory (session only)"
    ENGINE_MESSAGE=""
    ENGINE_CHANGED=0
    ENGINE_STATE=()
}

engine_load() { ENGINE_MESSAGE=""; }

engine_write() {
    local key=$1 value=$2 scope=${3:-} operation=${4:-set}
    local cache_key="${key}|${scope}" present=0
    ENGINE_MESSAGE=""; ENGINE_CHANGED=0
    [[ ${ENGINE_STATE[$cache_key]+present} ]] && present=1
    # Argument 7 carries the schema type. This engine stores all values literally.
    if [[ -n ${5-} ]] &&
       [[ $present != "$5" || ${ENGINE_STATE[$cache_key]-} != "${6-}" ]]; then
        ENGINE_MESSAGE="Setting changed; retry the adjustment."
        return 1
    fi
    case $operation in
        set)
            if (( present )) && [[ ${ENGINE_STATE[$cache_key]} == "$value" ]]; then return 0; fi
            ENGINE_STATE["$cache_key"]=$value
            ;;
        delete)
            if (( !present )); then return 0; fi
            unset 'ENGINE_STATE[$cache_key]'
            ;;
        *) ENGINE_MESSAGE="Unknown write operation: $operation"; return 1 ;;
    esac
    ENGINE_CHANGED=1
}

engine_cleanup() { :; }
