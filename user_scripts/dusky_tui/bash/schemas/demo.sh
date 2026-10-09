#!/usr/bin/env bash
# Example application schema and callbacks. Edit copies for future applications.
# shellcheck disable=SC2034 # Schema and picker fields are consumed by the frontend.
declare CONFIG_FILE="${DUSKY_CONFIG_FILE:-${XDG_CONFIG_HOME:-${HOME}/.config}/dusky_tui/demo.conf}"
declare APP_TITLE="Dusky Bash TUI Demo" APP_VERSION="v1.0"
declare -a TABS=("General" "Network" "Display" "System")

register_items() {
    # Generic Config Layout: register tab_idx "Label" 'key|type|scope|min|max|step' "default"
    # Note: 'scope' corresponds to [Section] in INI files, leave blank for global scope.
    # Omit the default argument to make Reset remove a setting; "" is an empty value.
    # int: decimal integers up to 18 digits, step defaults to 1.
    # float: finite decimal/exponent values, step defaults to 0.1.
    # cycle: comma-separated options in the min field; bool: true/false/yes/no/on/off/1/0.
    # action: define action_KEY(); menu: register before its register_child() entries.
    # Menus have one level. Reset All applies to the current tab or submenu only.
    register 0 "Enable Service"   'service_enabled|bool||||'              "true"
    register 0 "Timeout (ms)"     'timeout|int||0|1000|50'                "100"
    register 0 "Log Prefix"       'log_prefix|string||||'                 "myapp_"

    register 1 "Hostname"         'hostname|action||||'                   ""
    register 1 "Protocol"         'protocol|cycle|network|tcp,udp,icmp||' "tcp"
    
    register 2 "Border Size"      'border_size|int|display|0|10|1'        "2"
    register 2 "Blur Enabled"     'blur_enabled|bool|display|||'          "true"

    register 3 "Advanced Settings" 'advanced_settings|menu||||'           ""
    register_child "advanced_settings" "Notifications"    'notifications|bool|system|||' "false"
    register_child "advanced_settings" "Max Retries"      'max_retries|int|system|1|10|1' "3"

    register 3 "Shadow Color"     'color|cycle|decoration|0xee1a1a1a,0xff000000||' "0xee1a1a1a"

    register 3 "Custom Path"      'demo_text|action||||' ""
    register 3 "Select Theme"     'demo_picker|action||||' ""
    register 3 "Demo Service Action" 'demo_service|action||||' ""
}

action_hostname() {
    local user_input=""
    prompt_line_input "Enter new hostname:" user_input || return 0
    if [[ -n $user_input ]]; then
        set_status "Hostname set to: $user_input"
    else
        clear_status
    fi
}

action_demo_text() {
    local user_input=""
    prompt_line_input "Enter a custom file path:" user_input || return 0
    if [[ -n $user_input ]]; then
        set_status "You typed: $user_input"
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

    PICKER_PARENT_VIEW=$CURRENT_VIEW
    PICKER_PARENT_ROW=$SELECTED_ROW
    PICKER_PARENT_SCROLL=$SCROLL_OFFSET
    CURRENT_VIEW=2
    clear_status
}

picker_cb_demo_theme() {
    local selected=$1
    set_status "Selected Theme: $selected"
}

action_demo_service() {
    set_status "Service restart simulated. Replace this callback for your application."
}

post_write_action() {
    # Called after changed saves, once after Reset All. Report hook failures here.
    # systemctl reload my-daemon.service >/dev/null 2>&1 || set_status "Reload failed."
    :
}
