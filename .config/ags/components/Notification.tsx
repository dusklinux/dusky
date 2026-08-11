import GLib from "gi://GLib"
import Gdk from "gi://Gdk?version=4.0"
import Gtk from "gi://Gtk?version=4.0"
import { createPoll } from "ags/time"
import { clearNotifications, runNotificationCenter, toggleDnd } from "../lib/dusky"

export default function Notification() {
  const home = GLib.get_home_dir()
  const status = createPoll(
    "󰂚 0",
    2000,
    ["bash", "-lc", `"${home}/user_scripts/waybar/mako.sh" --horizontal 2>/dev/null | jq -r '.text // "󰂚 0"' 2>/dev/null || printf '󰂚 0'`],
  )

  return (
    <button
      class="notification-card"
      tooltipText="LMB notifications · MMB clear · RMB DND"
      onClicked={() => runNotificationCenter()}
    >
      <Gtk.GestureClick button={Gdk.BUTTON_MIDDLE} onPressed={() => clearNotifications()} />
      <Gtk.GestureClick button={Gdk.BUTTON_SECONDARY} onPressed={() => toggleDnd()} />
      <label label={status((text) => text.trim())} />
    </button>
  )
}
