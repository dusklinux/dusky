import AstalBattery from "gi://AstalBattery"
import { createBinding } from "ags"

export default function Battery() {
  const battery = AstalBattery.get_default()
  const percent = createBinding(battery, "percentage")((value) => `${Math.round(value * 100)}%`)

  return (
    <box class="battery-card" visible={createBinding(battery, "isPresent")} spacing={5}>
      <image iconName={createBinding(battery, "iconName")} />
      <label label={percent} />
    </box>
  )
}
