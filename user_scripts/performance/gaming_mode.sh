#!/bin/bash

BACKUP_DIR="${HOME}/.config/hypr/gaming_backups"
STATE_FILE="${BACKUP_DIR}/gaming_state"

HYPRCTL=$(command -v hyprctl || echo "/usr/bin/hyprctl")

mkdir -p "$BACKUP_DIR"

if ! command -v hyprctl >/dev/null 2>&1; then
    notify-send -u critical "Gaming Mode: hyprctl not found"
    exit 1
fi

HL_EVAL() {
    $HYPRCTL eval "$1" 2>&1
}

save_state() {
    cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor > "${BACKUP_DIR}/cpu_governor" 2>/dev/null || echo "powersave" > "${BACKUP_DIR}/cpu_governor"
    cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference > "${BACKUP_DIR}/epp" 2>/dev/null || echo "balance_performance" > "${BACKUP_DIR}/epp"
    cat /proc/sys/vm/swappiness > "${BACKUP_DIR}/swappiness" 2>/dev/null || echo "100" > "${BACKUP_DIR}/swappiness"

    echo "MODE=on" > "$STATE_FILE"
}

restore_state() {
    [ ! -f "$STATE_FILE" ] && notify-send -u critical "Gaming Mode: No backup found to restore" && return 1

    local gov epp swp
    gov=$(cat "${BACKUP_DIR}/cpu_governor" 2>/dev/null) || true
    epp=$(cat "${BACKUP_DIR}/epp" 2>/dev/null) || true
    swp=$(cat "${BACKUP_DIR}/swappiness" 2>/dev/null) || true

    [ -n "$gov" ] && echo "$gov" | sudo -n tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null 2>&1 || true
    [ -n "$epp" ] && echo "$epp" | sudo -n tee /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference >/dev/null 2>&1 || true
    [ -n "$swp" ] && sudo -n sysctl -w vm.swappiness="$swp" >/dev/null 2>&1 || true

    nvidia-smi -pm 0 2>/dev/null || true
    systemctl --user stop gamemoded 2>/dev/null || true

    echo "MODE=off" > "$STATE_FILE"
}

gaming_on() {
    save_state

    HL_EVAL "hl.config({decoration={blur={enabled=false},drop_shadow=false},animations={enabled=false}})"

    echo "performance" | sudo -n tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null 2>&1 || true
    echo "performance" | sudo -n tee /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference >/dev/null 2>&1 || true
    sudo -n sysctl -w vm.swappiness=10 >/dev/null 2>&1 || true

    nvidia-smi -pm 1 2>/dev/null || true
    systemctl --user start gamemoded 2>/dev/null || true

    notify-send -u critical -t 2000 "Gaming Mode ON" "Blur/Shadows/Animations disabled\nCPU: performance"
}

gaming_off() {
    restore_state

    notify-send -u low -t 2000 "Gaming Mode OFF" "Restoring original config..."

    $HYPRCTL reload config-only

    notify-send -u low -t 2000 "Gaming Mode OFF" "Settings restored"
}

case "${1:-toggle}" in
    on|enable)
        gaming_on
        ;;
    off|disable)
        gaming_off
        ;;
    toggle|*)
        if [ -f "$STATE_FILE" ] && grep -q "^MODE=on" "$STATE_FILE" 2>/dev/null; then
            gaming_off
        else
            gaming_on
        fi
        ;;
esac
