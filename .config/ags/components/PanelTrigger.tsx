import Gtk from "gi://Gtk?version=4.0"
import type { PanelId } from "../lib/popupState"
import { hoverPanel, leaveTrigger, togglePin } from "../lib/popupState"

type PanelTriggerProps = {
  panel: PanelId
  class?: any
  child: JSX.Element
}

export default function PanelTrigger({ panel, class: className = "", child }: PanelTriggerProps) {
  return (
    <button
      class={className as any}
      onClicked={() => togglePin(panel)}
    >
      <Gtk.EventControllerMotion
        onEnter={() => hoverPanel(panel)}
        onLeave={() => leaveTrigger(panel)}
      />
      {child}
    </button>
  )
}
