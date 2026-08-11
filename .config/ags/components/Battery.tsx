import AstalBattery from "gi://AstalBattery"
import Gtk from "gi://Gtk?version=4.0"
import { createBinding, createComputed } from "ags"

function batteryLevelClass(percentage: number, charging: boolean) {
  const value = Number(percentage) || 0
  const classes = ["battery-level"]

  if (charging) classes.push("charging")
  if (value <= 0.03) classes.push("battery-level-empty")
  else if (value <= 0.12) classes.push("battery-level-critical")
  else if (value <= 0.22) classes.push("battery-level-warning")
  else if (value >= 0.82) classes.push("battery-level-full")
  else classes.push("battery-level-good")

  return classes.join(" ")
}

export default function Battery() {
  const battery = AstalBattery.get_default()
  const percentage = createBinding(battery, "percentage")
  const charging = createBinding(battery, "charging")
  const percent = percentage((value) => `${Math.round(Number(value) * 100)}%`)
  const levelClass = createComputed(() => batteryLevelClass(Number(percentage()), Boolean(charging())))
  const cardClass = levelClass((value) => `battery-card ${value}`)
  const warningVisible = levelClass((value) =>
    value.includes("battery-level-warning") ||
    value.includes("battery-level-critical") ||
    value.includes("battery-level-empty")
  )

  return (
    <box class={cardClass} visible={createBinding(battery, "isPresent")} spacing={0}>
      <overlay class="battery-shell">
        <box class="battery-shell-base" />
        <box
          $type="overlay"
          class="battery-fill"
          canTarget={false}
          halign={Gtk.Align.START}
          valign={Gtk.Align.CENTER}
        />
        <label
          $type="overlay"
          class="battery-percent"
          canTarget={false}
          halign={Gtk.Align.CENTER}
          valign={Gtk.Align.CENTER}
          label={percent}
        />
        <box
          $type="overlay"
          class="battery-cap"
          canTarget={false}
          halign={Gtk.Align.END}
          valign={Gtk.Align.CENTER}
        />
        <label
          $type="overlay"
          class="battery-warning-sign"
          canTarget={false}
          halign={Gtk.Align.END}
          valign={Gtk.Align.CENTER}
          visible={warningVisible}
          label="!"
        />
      </overlay>
    </box>
  )
}
