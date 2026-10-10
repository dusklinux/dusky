#!/usr/bin/env bash
# Synchronize Papirus folder colors asynchronously, without rebuilding caches twice.
# Systemd workers log to the user journal; fallback workers log beside the lock.

set -euo pipefail

worker=false
if [[ ${1:-} == --worker ]]; then
    worker=true
    shift
fi

if (( $# != 1 )) || [[ ! $1 =~ ^[a-z][a-z0-9-]*$ ]]; then
    printf 'Usage: %s <color_name>\n' "${0##*/}" >&2
    exit 1
fi
target_color=$1

if ! command -v papirus-folders >/dev/null 2>&1; then
    printf '[matugen] papirus-folders not installed\n' >&2
    exit 0
fi

# Match papirus-folders' theme search order, then pass the resolved path so sudo
# cannot accidentally select a different theme from root's home directory.
IFS=: read -r -a data_dirs <<< "${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
icon_dirs=("$HOME/.icons" "${XDG_DATA_HOME:-$HOME/.local/share}/icons")
for data_dir in "${data_dirs[@]}"; do
    [[ -n $data_dir ]] && icon_dirs+=("${data_dir%/}/icons")
done
theme_dir=
for icon_dir in "${icon_dirs[@]}"; do
    if [[ -f $icon_dir/Papirus-Dark/index.theme ]]; then
        theme_dir=$icon_dir/Papirus-Dark
        break
    fi
done
if [[ -z $theme_dir ]]; then
    printf '[matugen] Papirus-Dark icon theme not installed\n' >&2
    exit 1
fi

color_is_current() {
    local size icon name places
    # Reject an obviously different color before checking specialized icons.
    for size in 22x22 24x24 32x32 48x48 64x64; do
        [[ $theme_dir/$size/places/folder.svg -ef $theme_dir/$size/places/folder-$target_color.svg ]] || return 1
    done
    # A failed recolor can leave specialized folders or user icons out of sync.
    # Check exactly the regular color files that papirus-folders would apply.
    for size in 22x22 24x24 32x32 48x48 64x64; do
        places=$theme_dir/$size/places
        for icon in "$places/"{folder,user}"-$target_color"{,-*}.svg; do
            [[ -f $icon && ! -L $icon ]] || continue
            name=${icon##*/}
            [[ $places/${name/-$target_color/} -ef $icon ]] || return 1
        done
    done
}

lock_file=${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/matugen-papirus-folders-$UID.lock
request_file=$lock_file.request
exec {lock_fd}>"$lock_file"

if "$worker"; then
    # Recheck after locking: a preceding worker may have applied this color.
    flock "$lock_fd"
    # Every queued worker applies the latest request, regardless of launch order.
    IFS= read -r target_color < "$request_file"
    color_is_current && exit 0
    if ! sudo -n papirus-folders -C "$target_color" --theme "$theme_dir"; then
        printf '[matugen] papirus-folders failed; retaining current icon theme\n' >&2
        exit 1
    fi
    if command -v gsettings >/dev/null 2>&1; then
        gsettings set org.gnome.desktop.interface icon-theme Adwaita || :
        gsettings set org.gnome.desktop.interface icon-theme Papirus-Dark
    fi
    exit 0
fi

# Publish changed requests before the fast path so pending workers see them too.
# Identical requests need no write; rename keeps changed requests atomic.
requested_color=
if ! { [[ -r $request_file ]] && IFS= read -r requested_color < "$request_file" && [[ $requested_color == "$target_color" ]]; }; then
    request_tmp=$request_file.$$
    trap '[[ -z ${request_tmp:-} ]] || rm -f -- "$request_tmp"' EXIT
    printf '%s\n' "$target_color" > "$request_tmp"
    mv --force --no-target-directory -- "$request_tmp" "$request_file"
    request_tmp=
fi

# Never skip a request while another worker is changing the icons.
if flock --nonblocking "$lock_fd"; then
    color_is_current && exit 0
fi
exec {lock_fd}>&-

script_path=${BASH_SOURCE[0]}
[[ $script_path == /* ]] || script_path=$PWD/$script_path

if command -v systemd-run >/dev/null 2>&1 && [[ -n ${XDG_RUNTIME_DIR:-} ]]; then
    # Transient services otherwise inherit the manager's environment, which may
    # differ from Matugen's. Disable systemd's expansion of literal dollar signs.
    environment=()
    for name in HOME PATH XDG_RUNTIME_DIR XDG_DATA_HOME XDG_DATA_DIRS XDG_CONFIG_HOME DBUS_SESSION_BUS_ADDRESS; do
        [[ -v $name ]] && environment+=("--setenv=$name=${!name}")
    done
    if systemd-run --user --slice=background.slice --collect --quiet \
        --service-type=exec --expand-environment=no "${environment[@]}" \
        "$BASH" "$script_path" --worker "$target_color"; then
        exit 0
    fi
    printf '[matugen] systemd launch failed; using background worker\n' >&2
fi

(
    trap '' HUP
    exec "$BASH" "$script_path" --worker "$target_color"
) </dev/null >"$lock_file.log" 2>&1 &
