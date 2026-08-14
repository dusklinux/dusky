import Gdk from "gi://Gdk?version=4.0"
import Gtk from "gi://Gtk?version=4.0"
import { runAppLauncher, runLauncher, runQuickPanel } from "../lib/dusky"

export default function Launcher() {
  return (
    <button
      class="launcher-card"
      tooltipText="LMB Control Center · MMB Quick Panel · RMB Applications"
      onClicked={() => runLauncher()}
    >
      <Gtk.GestureClick button={Gdk.BUTTON_MIDDLE} onPressed={() => runQuickPanel()} />
      <Gtk.GestureClick button={Gdk.BUTTON_SECONDARY} onPressed={() => runAppLauncher()} />
      <label class="launcher-glyph" label="󰣇" />
    </button>
  )
}
