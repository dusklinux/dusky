import Gtk from "gi://Gtk?version=4.0"
import Hyprland from "gi://AstalHyprland"
import { createBinding, createComputed } from "ags"
import { closePanel, closePanels, hoverPanel, leaveTrigger } from "../lib/popupState"
import { workspacePreviewEnabled } from "../lib/featureState"
import { closeWorkspacePreview, openWorkspacePreview } from "../lib/workspacePreviewState"
import {
  claimWorkspaceInteraction,
  enterWorkspaceRail,
  leaveWorkspaceRail,
  workspaceInteractionId,
  workspaceSnapId,
} from "../lib/workspaceInteractionState"
import { focusWorkspace } from "../lib/dusky"

export default function Workspaces() {
  const hyprland = Hyprland.get_default()
  const active = createBinding(hyprland, "focusedWorkspace", "id")((id) =>
    id > 0 ? ((id - 1) % 10) + 1 : 1,
  )
  const workspaces = Array.from({ length: 10 }, (_, index) => index + 1)

  return (
    <box class="workspace-switcher">
      <box class="workspace-deck" spacing={3}>
        <Gtk.EventControllerMotion
          onEnter={enterWorkspaceRail}
          onLeave={() => {
            leaveWorkspaceRail()
            leaveTrigger("workspace")
          }}
        />
        {workspaces.map((id) => {
          const isActive = createComputed(() => active() === id)
          const isInactive = createComputed(() => active() !== id)
          const buttonClass = createComputed(() => {
            const classes = ["workspace-button", `workspace-id-${id}`]
            const interactionId = workspaceInteractionId()
            const expanded = interactionId === id || (interactionId === null && active() === id)
            if (active() === id) classes.push("active")
            if (workspaceInteractionId() === id) classes.push("hovered")
            if (expanded) classes.push("expanded")
            return classes.join(" ")
          })
          const snapShellClass = createComputed(() =>
            workspaceSnapId() === id
              ? "workspace-magnetic-shell snapping"
              : "workspace-magnetic-shell",
          )

          return (
            <button
              class={buttonClass}
              onClicked={() => {
                closePanels()
                void focusWorkspace(id)
              }}
            >
              <Gtk.EventControllerMotion
                onEnter={() => {
                  claimWorkspaceInteraction(id)
                  if (active() === id) {
                    closeWorkspacePreview()
                    closePanel("workspace")
                    return
                  }
                  if (!workspacePreviewEnabled()) {
                    closeWorkspacePreview()
                    closePanel("workspace")
                    return
                  }
                  const windowCount = openWorkspacePreview(id)
                  if (windowCount === 0) {
                    closePanel("workspace")
                    return
                  }
                  hoverPanel("workspace")
                }}
              />
              <overlay class="workspace-button-overlay">
                <box
                  class="workspace-button-content"
                  halign={Gtk.Align.CENTER}
                  valign={Gtk.Align.CENTER}
                >
                  <label class="workspace-number" visible={isInactive} label={`${id}`} />
                  <label class="workspace-pacman" visible={isActive} label="󰮯" />
                </box>
                <box
                  $type="overlay"
                  class={snapShellClass}
                  canTarget={false}
                  halign={Gtk.Align.CENTER}
                  valign={Gtk.Align.CENTER}
                />
              </overlay>
            </button>
          )
        })}
      </box>
    </box>
  )
}
