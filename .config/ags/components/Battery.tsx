import AstalBattery from "gi://AstalBattery"
import { createBinding, createComputed } from "ags"

function batteryLevelClass(percentage: number, charging: boolean) {
  const value = Number(percentage) || 0
  const classes = ["battery-level"]

  if (charging) classes.push("charging")
  if (value <= 0.12) classes.push("battery-level-critical")
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
    value.includes("battery-level-warning") || value.includes("battery-level-critical")
  )

  return (
    <box class={cardClass} visible={createBinding(battery, "isPresent")} spacing={5}>
      <image class="battery-icon" iconName={createBinding(battery, "iconName")} />
      <label class="battery-percent" label={percent} />
      <label class="battery-warning-sign" visible={warningVisible} label="!" />
    </box>
  )
}
