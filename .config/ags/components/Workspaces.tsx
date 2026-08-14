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

const WORKSPACE_VISIBLE_SLOTS = [1, 2, 3, 4, 5] as const

function normalizeWorkspaceId(id: number) {
  const value = Number(id)
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 1
}

function workspaceIdForSlot(slot: number, active: () => number) {
  if (slot === 5) return active() > 5 ? active() : slot
  return slot
}

function workspaceAccentId(id: number) {
  return ((normalizeWorkspaceId(id) - 1) % 10) + 1
}

export default function Workspaces() {
  const hyprland = Hyprland.get_default()
  const active = createBinding(hyprland, "focusedWorkspace", "id")((id) => normalizeWorkspaceId(id))

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
        {WORKSPACE_VISIBLE_SLOTS.map((slot) => {
          const id = createComputed(() => workspaceIdForSlot(slot, active))
          const isActive = createComputed(() => active() === id())
          const isInactive = createComputed(() => active() !== id())
          const buttonClass = createComputed(() => {
            const currentId = id()
            const classes = [
              "workspace-button",
              `workspace-id-${id()}`,
              `workspace-accent-${workspaceAccentId(id())}`,
            ]
            const interactionId = workspaceInteractionId()
            const expanded = interactionId === currentId || (interactionId === null && active() === currentId)
            if (active() === currentId) classes.push("active")
            if (workspaceInteractionId() === currentId) classes.push("hovered")
            if (expanded) classes.push("expanded")
            return classes.join(" ")
          })
          const snapShellClass = createComputed(() =>
            workspaceSnapId() === id()
              ? "workspace-magnetic-shell snapping"
              : "workspace-magnetic-shell",
          )

          return (
            <button
              class={buttonClass}
              onClicked={() => {
                closePanels()
                void focusWorkspace(id())
              }}
            >
              <Gtk.EventControllerMotion
                onEnter={() => {
                  claimWorkspaceInteraction(id())
                  if (active() === id()) {
                    closeWorkspacePreview()
                    closePanel("workspace")
                    return
                  }
                  if (!workspacePreviewEnabled()) {
                    closeWorkspacePreview()
                    closePanel("workspace")
                    return
                  }
                  const windowCount = openWorkspacePreview(id())
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
                  <label class="workspace-number" visible={isInactive} label={id((value) => `${value}`)} />
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
