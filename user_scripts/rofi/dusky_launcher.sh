#!/usr/bin/env bash
# ~/user_scripts/rofi/dusky_launcher.sh
# Unified All-in-One Launcher & Data Provider

# ==============================================================================
# 1. DATA PROVIDER MODE (Populates the "Dusky" tab)
# This executes ONLY when Rofi calls the script back looking for data.
# ==============================================================================
if [[ "$1" == "--rofi-mode" ]]; then
    # ROFI_RETV state: 0 = Initial load, 1 = User selected an item
    if [[ -z "$ROFI_RETV" || "$ROFI_RETV" -eq 0 ]]; then
        
        # Tell Rofi this script provides Pango markup so Combi mode does not escape the tags
        echo -en "\0markup-rows\x1ftrue\n"
        
        # Pure Bash globbing
        shopt -s nullglob nocaseglob
        for file in ~/.local/share/applications/*dusky*.desktop /usr/share/applications/*dusky*.desktop; do
            
            name="" desc="" icon=""
            
            # Native, zero-fork line-by-line reading. Bypasses ALL regex quirks.
            while IFS= read -r line || [[ -n "$line" ]]; do
                # Strip trailing carriage returns natively
                line="${line%$'\r'}" 
                
                # Use native string prefix matching to extract values perfectly
                if [[ "$line" == Name=* && -z "$name" ]]; then
                    name="${line#Name=}"
                elif [[ "$line" == GenericName=* && -z "$desc" ]]; then
                    desc="${line#GenericName=}"
                elif [[ "$line" == Icon=* && -z "$icon" ]]; then
                    icon="${line#Icon=}"
                fi
            done < "$file"
            
            # Escape XML entities natively so the Pango parser does not prematurely terminate strings
            name="${name//&/&amp;}"
            name="${name//</&lt;}"
            desc="${desc//&/&amp;}"
            desc="${desc//</&lt;}"
            
            # Format text: "Name (Description)" using Pango markup
            if [[ -n "$desc" ]]; then
                display_text="${name} <span alpha='60%'><i>(${desc})</i></span>"
            else
                display_text="${name}"
            fi
            
            # Use printf instead of echo -e to absolutely prevent unintended escape sequence evaluation
            printf "%s\0icon\x1f%s\x1finfo\x1f%s\n" "$display_text" "$icon" "$file"
        done
        shopt -u nullglob nocaseglob

    elif [[ "$ROFI_RETV" -eq 1 ]]; then
        # The user hit enter. Extract the hidden file path from ROFI_INFO
        if [[ -n "$ROFI_INFO" && -f "$ROFI_INFO" ]]; then
            
            # Read the file line-by-line natively to find the Exec command
            exec_cmd=""
            while IFS= read -r line || [[ -n "$line" ]]; do
                line="${line%$'\r'}"
                if [[ "$line" == Exec=* ]]; then
                    exec_cmd="${line#Exec=}"
                    break
                fi
            done < "$ROFI_INFO"
            
            # Clean standard XDG execution flags natively using parameter expansion globbing
            exec_cmd="${exec_cmd// %[a-zA-Z]/}"
            
            # Execute cleanly and detach entirely from the script lifecycle
            bash -c "$exec_cmd" >/dev/null 2>&1 &
            disown
        fi
    fi
    exit 0
fi

# ==============================================================================
# 2. UI LAUNCHER MODE
# ==============================================================================

# Get absolute path to this script so Rofi knows exactly what to call back
SCRIPT_PATH="$(realpath "$0")"

# Dynamic UI Injection (Leaves config.rasi absolutely pristine)
# entry { max-history: 200; } enforces a strict FIFO rolling buffer for text inputs
THEME_INJECTION='
mainbox { 
    children: [ inputbar, mode-switcher, message, listview ]; 
}
mode-switcher { 
    orientation: horizontal; 
    spacing: 10px; 
    background-color: transparent; 
}
button { 
    padding: 8px 12px; 
    border-radius: 8px; 
    background-color: @var-input-bg; 
    text-color: @var-text-def; 
    cursor: pointer; 
}
button selected { 
    background-color: @var-active-bg; 
    text-color: @var-text-active; 
}
listview { 
    fixed-height: false; 
}
entry {
    max-history: 200;
}
element-text {
    markup: true;
}
'

# Execute Rofi entirely natively to allow internal memory to multiply history correctly
# Removed ALL custom cache overrides so Rofi uses ~/.cache/rofi atomically
rofi -show combi \
     -modes "drun,combi,Dusky:${SCRIPT_PATH} --rofi-mode" \
     -combi-modes "drun,Dusky" \
     -combi-hide-mode-prefix true \
     -display-drun "󰀻 Apps" \
     -display-combi "󰜉 All" \
     -display-Dusky "󰒓 Dusky" \
     -drun-match-fields "name,generic,exec,categories,keywords" \
     -tokenize \
     -matching fuzzy \
     -no-sort \
     -no-disable-history \
     -max-history-size 200 \
     -no-fixed-num-lines \
     -markup \
     -theme-str "$THEME_INJECTION"
