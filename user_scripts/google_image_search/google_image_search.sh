#!/usr/bin/env bash
# Capture a Wayland screen region and search it with Google Lens.
set -euo pipefail

# true: upload to uguu.se; false: copy locally and paste into Lens with Ctrl+V.
readonly USE_UPLOAD_SERVICE="${USE_UPLOAD_SERVICE:-true}"

notify() {
    # A missing notification daemon must not interrupt capture or browser launch.
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -a "Google Lens" "$1" "$2" || true
    fi
}

die() {
    printf 'Google Lens: %s\n' "$1" >&2
    notify "Error" "$1"
    exit 1
}

open_url() {
    # xdg-open may remain attached to the browser; report failures asynchronously.
    (
        dusky-run xdg-open "$1" || die "Failed to open Google Lens in the browser."
    ) </dev/null &
    disown "$!" || true
}

case "$USE_UPLOAD_SERVICE" in
    true) dependencies=(grim slurp dusky-run xdg-open mktemp rm curl jq) ;;
    false) dependencies=(grim slurp dusky-run xdg-open mktemp rm wl-copy) ;;
    *) die "USE_UPLOAD_SERVICE must be true or false." ;;
esac

# Dependencies belong in the ISO/install process, not an interactive keybind.
for dependency in "${dependencies[@]}"; do
    command -v "$dependency" >/dev/null 2>&1 || die "Missing command: $dependency"
done

tmp_file=$(mktemp --tmpdir lens-XXXXXXXXXX.png) || die "Failed to create a temporary image."
trap 'rm -f -- "$tmp_file"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

printf 'Select a screen region...\n'
# Reuse the temporary file for diagnostics until capture overwrites it.
selection_status=0
geometry=$(slurp -f '%x,%y %wx%h' </dev/null 2> "$tmp_file") || selection_status=$?
selection_error=$(< "$tmp_file")
# slurp 1.5 reports both cancellation and errors with status 1.
if [[ $selection_status == 1 && $selection_error == 'selection cancelled' ]]; then
    printf 'Selection cancelled.\n'
    exit 0
fi
[[ -z $selection_error ]] || printf '%s\n' "$selection_error" >&2
(( selection_status == 0 )) || die "Region selection failed (status $selection_status)."

# Output coordinates may be negative; dimensions must be positive.
if [[ ! $geometry =~ ^-?[0-9]+,-?[0-9]+\ 0*[1-9][0-9]*x0*[1-9][0-9]*$ ]]; then
    die "Invalid selection geometry received."
fi

# Capture first so a failed screenshot cannot replace the clipboard contents.
grim -t png -g "$geometry" "$tmp_file" || die "Failed to capture the selected region."
[[ -s $tmp_file ]] || die "The screenshot is empty."

if [[ $USE_UPLOAD_SERVICE == true ]]; then
    notify "Uploading..." "Sending the screenshot to uguu.se."
    # Read the image from stdin so TMPDIR names cannot affect multipart syntax.
    if ! response=$(curl --silent --show-error --fail \
        --connect-timeout 10 --max-time 60 \
        --form 'files[]=@-;filename=screenshot.png;type=image/png' \
        'https://uguu.se/upload' < "$tmp_file"); then
        die "Image upload failed."
    fi

    if ! encoded_url=$(jq --slurp --exit-status --raw-output '
        select(length == 1) | .[0]
        | select(.success == true)
        | .files[0].url
        | select(type == "string")
        | select(test("^https?://[^/?#[:space:]]+([/?#][^[:space:]]*)?$"))
        | @uri
    ' <<< "$response"); then
        die "The upload service returned an invalid or unsuccessful response."
    fi

    open_url "https://lens.google.com/uploadbyurl?url=${encoded_url}"
else
    wl-copy --type image/png < "$tmp_file" || die "Failed to copy the screenshot."
    notify "Ready" "Screenshot copied. Paste (Ctrl+V) in the browser."
    open_url 'https://lens.google.com/'
fi
