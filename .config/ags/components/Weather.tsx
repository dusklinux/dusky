import GLib from "gi://GLib"
import { createPoll } from "ags/time"

export default function Weather() {
  const home = GLib.get_home_dir()
  const weather = createPoll(
    "󰖐 --°C",
    300000,
    ["bash", "-lc", `python3 "${home}/user_scripts/waybar/weather.py" 2>/dev/null | jq -r 'if type=="object" then (.text // .alt // "󰖐 --°C") else . end' 2>/dev/null || printf '󰖐 --°C'`],
  )

  return (
    <button class="weather-card" tooltipText="Weather">
      <label label={weather((text) => text.trim())} />
    </button>
  )
}
