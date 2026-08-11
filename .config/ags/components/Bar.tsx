import Astal from "gi://Astal?version=4.0"
import Gdk from "gi://Gdk?version=4.0"
import Gtk from "gi://Gtk?version=4.0"
import { createComputed, onCleanup } from "ags"
import { motionClass } from "../lib/motionState"
import { themeMode } from "../lib/themeState"
import LeftCluster from "./LeftCluster"
import ClockCard from "./ClockCard"
import RightCluster from "./RightCluster"

export default function Bar({ gdkmonitor }: { gdkmonitor: Gdk.Monitor }) {
  let win: Astal.Window
  const { TOP, LEFT, RIGHT } = Astal.WindowAnchor
  const rootClass = createComputed(() => {
    const themeClass = themeMode() === "light" ? "theme-light" : "theme-dark"
    return `adaptive-glass-root ${themeClass} ${motionClass()}`
  })

  onCleanup(() => win?.destroy())

  return (
    <window
      $={(self) => (win = self)}
      visible
      namespace="dusky-adaptive-glass"
      class={rootClass}
      name={`adaptive-glass-${gdkmonitor.connector}`}
      gdkmonitor={gdkmonitor}
      exclusivity={Astal.Exclusivity.EXCLUSIVE}
      anchor={TOP | LEFT | RIGHT}
    >
      <centerbox class="bar-shell" orientation={Gtk.Orientation.HORIZONTAL}>
        <box $type="start" class="bar-zone bar-left" valign={Gtk.Align.CENTER}>
          <LeftCluster />
        </box>
        <box $type="center" class="bar-zone bar-center" valign={Gtk.Align.CENTER}>
          <ClockCard />
        </box>
        <box $type="end" class="bar-zone bar-right" valign={Gtk.Align.CENTER}>
          <RightCluster />
        </box>
      </centerbox>
    </window>
  )
}
