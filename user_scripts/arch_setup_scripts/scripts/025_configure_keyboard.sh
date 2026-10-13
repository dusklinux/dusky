#!/usr/bin/env bash
#d: Detect the system keyboard layout and variant and apply them to Hyprland

set -euo pipefail

readonly TARGET_CONF="${HOME}/.config/hypr/edit_here/source/input.lua"
readonly DEFAULT_LAYOUT="us"
readonly KEYMAP_TABLE="/usr/share/systemd/kbd-model-map"

if [[ -t 2 && ${TERM:-dumb} != dumb ]]; then
    readonly GREEN=$'\033[0;32m' BLUE=$'\033[0;34m'
    readonly YELLOW=$'\033[0;33m' RED=$'\033[0;31m' NC=$'\033[0m'
else
    readonly GREEN="" BLUE="" YELLOW="" RED="" NC=""
fi

_temp_file=""
layout=""
variant=""

cleanup() {
    if [[ -n $_temp_file ]]; then
        rm -f -- "$_temp_file"
    fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

log_info() { printf '%s[INFO]%s %s\n' "$BLUE" "$NC" "$*" >&2; }
log_ok()   { printf '%s[OK]%s %s\n' "$GREEN" "$NC" "$*" >&2; }
log_warn() { printf '%s[WARN]%s %s\n' "$YELLOW" "$NC" "$*" >&2; }
log_err()  { printf '%s[ERROR]%s %s\n' "$RED" "$NC" "$*" >&2; }
die() { log_err "$*"; exit 1; }

# XKB names are used by Wayland too; querying localectl's X11 fields does not
# require Xorg or XWayland. Keep variants aligned with their layout list.
detect_layout() {
    log_info "Detecting system keyboard configuration..."
    local status vc_map="" line
    if ! status=$(LC_ALL=C SYSTEMD_COLORS=0 localectl --no-pager --full status); then
        die "Cannot query the system keyboard configuration; config left unchanged."
    fi
    while IFS= read -r line; do
        if [[ $line =~ ^[[:blank:]]*X11[[:blank:]]+Layout:[[:blank:]]*([^[:space:]]+) ]]; then
            layout=${BASH_REMATCH[1]}
        elif [[ $line =~ ^[[:blank:]]*X11[[:blank:]]+Variant:[[:blank:]]*([^[:space:]]+) ]]; then
            variant=${BASH_REMATCH[1]}
        elif [[ $line =~ ^[[:blank:]]*VC[[:blank:]]+Keymap:[[:blank:]]*([^[:space:]]+) ]]; then
            vc_map=${BASH_REMATCH[1]}
        fi
    done <<< "$status"
    [[ -n $layout || -n $vc_map ]] ||
        die "No keyboard fields in localectl output; config left unchanged."
    case $variant in '(unset)'|'n/a') variant="" ;; esac
    case $layout in ''|'(unset)'|'n/a') layout="" ;; *) return ;; esac
    variant=""
    case $vc_map in
        ''|'(unset)'|'n/a')
            log_warn "No system keymap configured. Using '$DEFAULT_LAYOUT'."
            layout=$DEFAULT_LAYOUT
            return
            ;;
    esac

    # Read systemd's packaged conversion data instead of guessing two letters.
    # Whitespace separates columns; '-' denotes an empty variant. On duplicate
    # keymaps, use the first row in the table.
    local console xkb _model mapped_variant _rest
    while read -r console xkb _model mapped_variant _rest; do
        if [[ $console == "$vc_map" ]]; then
            layout=$xkb
            [[ $mapped_variant == - ]] || variant=$mapped_variant
            # systemd 262 puts phonetic on the US group in this row. The
            # installed XKB data defines it on the Bulgarian group instead.
            if [[ $vc_map == bg_pho-utf8 && $layout == bg,us && $variant == ,phonetic ]]; then
                variant=phonetic,
            fi
            return
        fi
    done < "$KEYMAP_TABLE"

    # Colemak is a common console keymap missing from systemd's table.
    if [[ $vc_map == colemak ]]; then
        layout=us
        variant=colemak
    elif [[ $vc_map =~ ^[a-zA-Z0-9_-]+$ ]] &&
         xkbcli compile-keymap --layout "$vc_map" --variant "" --options "" --test 2>/dev/null; then
        layout=$vc_map
    else
        log_warn "No XKB mapping for console keymap '$vc_map'. Using '$DEFAULT_LAYOUT'."
        layout=$DEFAULT_LAYOUT
    fi
}

apply_config() {
    [[ $layout =~ ^[a-zA-Z0-9_-]+(,[a-zA-Z0-9_-]+)*$ ]] || die "Invalid layout: '$layout'."
    [[ $variant =~ ^[a-zA-Z0-9_,+-]*$ ]] || die "Invalid variant: '$variant'."
    # libxkbcommon can log an error and still succeed after discarding excess
    # variants. Check their group count before relying on compilation status.
    local layout_names=${layout//,/} variant_names=${variant//,/}
    (( ${#variant} - ${#variant_names} <= ${#layout} - ${#layout_names} )) ||
        die "More variants than layouts; config left unchanged."
    if ! xkbcli compile-keymap --layout "$layout" --variant "$variant" --options "" --test; then
        die "XKB cannot compile layout '$layout' with variant '$variant'; config left unchanged."
    fi
    [[ -f $TARGET_CONF ]] || die "Config not found: $TARGET_CONF"
    local target target_fd
    target=$(realpath -e -- "$TARGET_CONF")
    # Pin the source inode: another run may replace its pathname while this
    # run reads the configuration or preserves its attributes.
    exec {target_fd}< "$target"

    # Edit standalone quoted fields in the shipped Lua template. Keep spacing,
    # quote style, commas, comments, and original line endings.
    local -a lines
    mapfile -u "$target_fd" lines
    local key_re='^([[:blank:]]*)(kb_layout|kb_variant)[[:blank:]]*='
    local value_re="^([[:blank:]]*(kb_layout|kb_variant)[[:blank:]]*=[[:blank:]]*)([\"'])([^\"']*)"
    local suffix_re=$'^[[:blank:]]*,?[[:blank:]]*(--[^\r\n]*)?\r?\n?$'
    local comment_re='^[[:blank:]]*--\[(=*)\['
    local long_bracket_re='\[=*\['
    # Recognize short strings (including escaped quotes) and comment starts.
    # Incomplete short strings and long strings are outside this editor's scope.
    local token_re="--|\"(\\\\.|[^\"\\\\])*\"|'(\\\\.|[^'\\\\])*'|[\"']"
    local comment_tail_re='^[[:space:]]*(--.*)?$'
    local index line key prefix quote suffix value block_end="" code rest token
    local layout_count=0 variant_count=0 changed=0
    for index in "${!lines[@]}"; do
        line=${lines[index]}
        # Most lines are unrelated input settings; avoid regex work on them.
        if [[ -z $block_end && $line != *kb_layout* && $line != *kb_variant* &&
              $line != *'[['* && $line != *'[='* && $line != *\"* && $line != *\'* ]]; then
            continue
        fi
        # Ignore standalone Lua long comments, including --[=[ ... ]=].
        if [[ -n $block_end ]]; then
            [[ $line == *"$block_end"* ]] || continue
            line=${line#*"$block_end"}
            block_end=""
            [[ $line =~ $comment_tail_re ]] || die "End long comments on their own line; config left unchanged."
            continue
        fi
        if [[ $line =~ $comment_re ]]; then
            block_end="]${BASH_REMATCH[1]}]"
            line=${line#*"[${BASH_REMATCH[1]}["}
            if [[ $line == *"$block_end"* ]]; then
                line=${line#*"$block_end"}
                block_end=""
                [[ $line =~ $comment_tail_re ]] || die "End long comments on their own line; config left unchanged."
            fi
            continue
        fi
        # Strip short strings and ordinary comments before checking for long
        # brackets. This accepts quoted values such as "[[Auto]]" and rejects
        # long strings regardless of where they appear in an expression.
        code=""
        rest=$line
        while [[ $rest =~ $token_re ]]; do
            token=${BASH_REMATCH[0]}
            code+=${rest%%"$token"*}
            rest=${rest#*"$token"}
            case $token in
                --)
                    [[ ! $rest =~ ^\[=*\[ ]] || die "Put long comments on their own line; config left unchanged."
                    rest=""
                    break
                    ;;
                \"|\') die "Unsupported multiline or incomplete Lua string; config left unchanged." ;;
                *) code+=" " ;;
            esac
        done
        code+=$rest
        [[ ! $code =~ $long_bracket_re ]] ||
            die "Unsupported long-bracket syntax; config left unchanged."
        [[ $line =~ $key_re ]] || continue
        key=${BASH_REMATCH[2]}
        [[ $line =~ $value_re ]] || die "Unsupported Lua value for '$key'; config left unchanged."
        prefix=${BASH_REMATCH[1]}
        quote=${BASH_REMATCH[3]}
        value=${BASH_REMATCH[4]}
        suffix=${line:${#BASH_REMATCH[0]}}
        [[ $suffix == "$quote"* ]] || die "Mismatched quotes for '$key'; config left unchanged."
        suffix=${suffix:1}
        [[ $suffix =~ $suffix_re ]] || die "Unsupported Lua expression for '$key'; config left unchanged."
        if [[ $key == kb_layout ]]; then
            ((layout_count += 1))
            [[ $value == "$layout" ]] && continue
            value=$layout
        else
            ((variant_count += 1))
            [[ $value == "$variant" ]] && continue
            value=$variant
        fi
        lines[index]="$prefix$quote$value$quote$suffix"
        changed=1
    done
    (( layout_count == 1 && variant_count == 1 )) ||
        die "Expected exactly one active kb_layout and kb_variant field; config left unchanged."
    [[ -z $block_end ]] || die "Unterminated Lua comment; config left unchanged."
    if (( ! changed )); then
        exec {target_fd}<&-
        log_ok "Keyboard configuration already matches the system."
        return
    fi

    _temp_file=$(mktemp -- "${target%/*}/.tmp.input.XXXXXX")
    printf '%s' "${lines[@]}" > "$_temp_file"
    # Copy attributes only: preserve ownership, permissions (including ACLs),
    # and extended attributes without overwriting the new data or timestamp.
    cp --attributes-only --preserve=mode,ownership,xattr -- "/proc/self/fd/$target_fd" "$_temp_file"
    exec {target_fd}<&-
    mv -fT --no-copy -- "$_temp_file" "$target"
    _temp_file=""
    log_ok "Updated kb_layout = '$layout', kb_variant = '$variant' in $TARGET_CONF."
}

main() {
    (( EUID != 0 )) || die 'Run as the target user, without sudo.'
    detect_layout
    apply_config
}

main
