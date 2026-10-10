#!/usr/bin/env bash
# Discover CPU sensors once, then stream the hottest reading to Waybar.
# Sysfs numbering is deliberately not persisted across boots.
set -u
shopt -s nullglob

interval=${1:-3}
critical=${2:-80}
[[ $interval =~ ^[1-9][0-9]*$ && $critical =~ ^[1-9][0-9]*$ ]] || exit 1
declare -a sensors=()

discover_sensors() {
    sensors=()
    local directory name input label sensor_count
    for directory in /sys/class/hwmon/hwmon*; do
        { IFS= read -r name < "$directory/name"; } 2>/dev/null || continue
        case $name in
            coretemp|k10temp|zenpower)
                sensor_count=${#sensors[@]}
                for input in "$directory"/temp*_input; do
                    label=''
                    { IFS= read -r label < "${input%_input}_label"; } 2>/dev/null || true
                    case $label in
                        'Package id '*|Tctl|Tdie) sensors+=("$input") ;;
                    esac
                done
                # Drivers without labels expose their primary CPU reading here.
                if ((${#sensors[@]} == sensor_count)) && [[ -r $directory/temp1_input ]]; then
                    sensors+=("$directory/temp1_input")
                fi
                ;;
        esac
    done
    ((${#sensors[@]})) && return
    for directory in /sys/class/thermal/thermal_zone*; do
        { IFS= read -r name < "$directory/type"; } 2>/dev/null || continue
        case ${name,,} in
            *cpu*|*soc*|*pkg*|*package*)
                [[ -r $directory/temp ]] && sensors+=("$directory/temp") ;;
        esac
    done
}

discover_sensors
while :; do
    hottest=''
    for input in "${sensors[@]}"; do
        raw=''
        if { IFS= read -r raw < "$input"; } 2>/dev/null && [[ $raw =~ ^-?[0-9]+$ ]]; then
            value=$((10#${raw#-}))
            [[ $raw == -* ]] && value=$((-value))
            if [[ -z $hottest ]] || ((value > hottest)); then hottest=$value; fi
        fi
    done
    if [[ -n $hottest ]]; then
        celsius=$((hottest / 1000))
        fahrenheit=$((hottest * 9 / 5000 + 32))
        percentage=$((celsius * 100 / critical))
        ((percentage < 0)) && percentage=0
        ((percentage > 100)) && percentage=100
        class=normal
        ((hottest >= critical * 1000)) && class=critical
        printf '{"text":"%d","tooltip":"CPU temperature: %d°C / %d°F","percentage":%d,"class":"%s","alt":"%s"}\n' \
            "$celsius" "$celsius" "$fahrenheit" "$percentage" "$class" "$class"
    else
        printf '{"text":"","tooltip":"CPU temperature unavailable","class":"unavailable"}\n'
        discover_sensors
    fi
    sleep "$interval"
done
