#!/bin/bash

# 1. Define the exact state file location safely
STATE_FILE="$HOME/.config/dusky/settings/reboot_post_lua"

# 2. State Check: Silent exit if already processed
if [[ -f "$STATE_FILE" ]]; then
    exit 0
fi

# 3. Action Functions
perform_reboot() {
    # Ensure directory exists, then create state file
    mkdir -p "$(dirname "$STATE_FILE")"
    touch "$STATE_FILE"
    
    echo -e "\n\e[1;32mInitiating system reboot...\e[0m\n"
    systemctl reboot
    exit 0
}

cancel_reboot() {
    echo -e "\n\e[1;33mReboot cancelled. Please reboot manually later to apply Lua changes.\e[0m\n"
    exit 0
}

# 4. Visual Notification: Massive, eye-catching terminal banner
# Uses Red Background (\e[41m) and Bright White Text (\e[97m) for maximum contrast
echo -e "\n\n"
echo -e "\e[1;97;41m======================================================================\e[0m"
echo -e "\e[1;97;41m|                                                                    |\e[0m"
echo -e "\e[1;97;41m|               LUA CHANGES REQUIRE A RESTART                        |\e[0m"
echo -e "\e[1;97;41m|                                                                    |\e[0m"
echo -e "\e[1;97;41m======================================================================\e[0m"
echo -e "\n\e[1;93mThe system will automatically reboot in \e[1;91m2 MINUTES\e[1;93m if there is no response.\e[0m\n"

# 5. Flush the standard input buffer
# This catches any accidental stray keystrokes the user might have typed 
# while previous scripts were running, preventing accidental skips.
read -t 0.1 -n 10000 discard_input 2>/dev/null || true

# 6. Prompt User (120-second timeout)
# The prompt string itself is colored Cyan to stand out.
if read -t 120 -p $'\e[1;36mWould you like to reboot now? [Y/n]: \e[0m' choice; then
    # The user responded before the timeout
    case "$choice" in
        [Nn]* )
            cancel_reboot
            ;;
        * )
            # Catch-all: Y, y, or an empty 'Enter' defaults to rebooting
            perform_reboot
            ;;
    esac
else
    # The read command timed out OR failed
    exit_status=$?
    
    if [[ $exit_status -gt 128 ]]; then
        # Exit code > 128 strictly means a timeout occurred
        echo -e "\n\n\e[1;91m*** TIMEOUT REACHED: No response received. Auto-rebooting... ***\e[0m"
        perform_reboot
    else
        # Edge case: read failed because standard input was closed (e.g., non-interactive shell)
        echo -e "\n\n\e[1;31mWarning: Unable to read user input (stdin closed). Proceeding with required auto-reboot...\e[0m"
        perform_reboot
    fi
fi
