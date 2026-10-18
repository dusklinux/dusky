#!/usr/bin/env bash

# Discover system batteries by sysfs type, including devices not named BAT*.
# Report each pack separately rather than inventing an unweighted aggregate.
output=''
for battery in /sys/class/power_supply/*; do
    { IFS= read -r type < "$battery/type"; } 2>/dev/null || continue
    [[ $type == Battery ]] || continue
    if { IFS= read -r scope < "$battery/scope"; } 2>/dev/null; then
        [[ $scope == Device ]] && continue
    fi
    if { IFS= read -r present < "$battery/present"; } 2>/dev/null; then
        [[ $present == 0 ]] && continue
    fi
    { IFS= read -r capacity < "$battery/capacity"; } 2>/dev/null || continue
    [[ $capacity =~ ^[0-9]{1,3}$ ]] || continue
    (( 10#$capacity <= 100 )) || continue
    { IFS= read -r status < "$battery/status"; } 2>/dev/null || continue

    case $status in
        Charging)       text="⚡ $capacity%" ;;
        Full)           text='Full' ;;
        'Not charging') text=" $capacity%" ;;
        *)              text="$capacity%" ;;
    esac
    output+="${output:+  ·  }$text"
done

[[ -z $output ]] || printf '%s\n' "$output"
exit 0
