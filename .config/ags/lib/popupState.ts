import GLib from "gi://GLib"
import { createState } from "ags"

export type PanelId = "workspace" | "calendar" | "network" | "audio" | "display" | "power" | "media"

export const CLOSE_DELAY_MS = 80

export const [activePanel, setActivePanel] = createState<PanelId | null>(null)
export const [pinnedPanel, setPinnedPanel] = createState<PanelId | null>(null)

let closeTimer = 0

function cancelClose() {
  if (!closeTimer) return
  GLib.Source.remove(closeTimer)
  closeTimer = 0
}

function scheduleClose(id: PanelId) {
  cancelClose()
  if (pinnedPanel.get()) return

  closeTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, CLOSE_DELAY_MS, () => {
    closeTimer = 0
    if (!pinnedPanel.get() && activePanel.get() === id) setActivePanel(null)
    return GLib.SOURCE_REMOVE
  })
}

export function hoverPanel(id: PanelId) {
  cancelClose()
  const pinned = pinnedPanel.get()
  if (pinned && pinned !== id) return
  setActivePanel(id)
}

export function leaveTrigger(id: PanelId) {
  if (activePanel.get() === id) scheduleClose(id)
}

export function enterPanel(id: PanelId) {
  if (activePanel.get() !== id) return
  cancelClose()
}

export function leavePanel(id: PanelId) {
  if (activePanel.get() === id) scheduleClose(id)
}

export function togglePin(id: PanelId) {
  cancelClose()
  if (pinnedPanel.get() === id) {
    setPinnedPanel(null)
    setActivePanel(null)
    return
  }

  setPinnedPanel(id)
  setActivePanel(id)
}

export function closePanel(id: PanelId) {
  if (pinnedPanel.get() !== id && activePanel.get() !== id) return
  cancelClose()
  if (pinnedPanel.get() === id) setPinnedPanel(null)
  if (activePanel.get() === id) setActivePanel(null)
}

export function closePanels() {
  cancelClose()
  setPinnedPanel(null)
  setActivePanel(null)
}
