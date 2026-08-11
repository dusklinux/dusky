import { createComputed, onCleanup } from "ags"
import Astal from "gi://Astal?version=4.0"
import Gdk from "gi://Gdk?version=4.0"
import Gtk from "gi://Gtk?version=4.0"
import type { PanelId } from "../lib/popupState"
import { activePanel, enterPanel, leavePanel } from "../lib/popupState"
import { motionClass } from "../lib/motionState"
import { themeMode } from "../lib/themeState"

type PopupWindowProps = {
  id: PanelId
  gdkmonitor: Gdk.Monitor
  anchor: Astal.WindowAnchor
  marginTop?: number
  marginLeft?: number
  marginRight?: number
  child: JSX.Element
  windowRef?: (window: Astal.Window | null) => void
  enabled?: () => boolean
}

export default function PopupWindow({
  id,
  gdkmonitor,
  anchor,
  marginTop = 42,
  marginLeft = 8,
  marginRight = 8,
  child,
  windowRef,
  enabled,
}: PopupWindowProps) {
  let win: Astal.Window
  const rootClass = createComputed(() => {
    const themeClass = themeMode() === "light" ? "theme-light" : "theme-dark"
    return `adaptive-glass-popup-window ${themeClass} ${motionClass()}`
  })
  const frameClass = createComputed(() =>
    activePanel() === id
      ? `popup-window-frame popup-${id} popup-open`
      : `popup-window-frame popup-${id}`,
  )
  const windowVisible = createComputed(() => (enabled?.() ?? true) && activePanel() === id)

  onCleanup(() => {
    windowRef?.(null)
    win?.destroy()
  })

  return (
    <window
      $={(self) => {
        win = self
        windowRef?.(self)
      }}
      name={`adaptive-glass-popup-${id}-${gdkmonitor.connector}`}
      namespace="dusky-adaptive-glass-popup"
      class={rootClass}
      gdkmonitor={gdkmonitor}
      visible={windowVisible}
      anchor={anchor}
      exclusivity={Astal.Exclusivity.IGNORE}
      layer={Astal.Layer.OVERLAY}
      keymode={Astal.Keymode.NONE}
      marginTop={marginTop}
      marginLeft={marginLeft}
      marginRight={marginRight}
    >
      <box class={frameClass}>
        <Gtk.EventControllerMotion
          onEnter={() => enterPanel(id)}
          onLeave={() => leavePanel(id)}
        />
        {child}
      </box>
    </window>
  )
}
