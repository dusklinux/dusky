import GLib from "gi://GLib"
import { createState } from "ags"
import { getWorkspaceMotionTiming } from "./motionState"

// Historical defaults retained for existing regression contracts. Runtime
// scheduling now reads the active motion style at the moment a timer is set.
export const INTERACTION_RELEASE_DELAY_MS = 120
export const SNAP_DELAY_MS = 190
export const SNAP_PULSE_MS = 510

export const [workspaceInteractionId, setWorkspaceInteractionId] = createState<number | null>(null)
export const [workspaceSnapId, setWorkspaceSnapId] = createState<number | null>(null)

let railInside = false
let previewInside = false
let releaseTimer = 0
let snapDelayTimer = 0
let snapClearTimer = 0

function cancelRelease() {
  if (!releaseTimer) return
  GLib.Source.remove(releaseTimer)
  releaseTimer = 0
}

function cancelSnapTimers(clearState = true) {
  if (snapDelayTimer) {
    GLib.Source.remove(snapDelayTimer)
    snapDelayTimer = 0
  }
  if (snapClearTimer) {
    GLib.Source.remove(snapClearTimer)
    snapClearTimer = 0
  }
  if (clearState) setWorkspaceSnapId(null)
}

function scheduleSnap(id: number) {
  cancelSnapTimers()
  const timing = getWorkspaceMotionTiming()
  snapDelayTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, timing.snapDelayMs, () => {
    snapDelayTimer = 0
    if (workspaceInteractionId() !== id) return GLib.SOURCE_REMOVE

    setWorkspaceSnapId(id)
    snapClearTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, timing.snapPulseMs, () => {
      snapClearTimer = 0
      setWorkspaceSnapId(null)
      return GLib.SOURCE_REMOVE
    })
    return GLib.SOURCE_REMOVE
  })
}

function scheduleRelease() {
  cancelRelease()
  const timing = getWorkspaceMotionTiming()
  releaseTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, timing.interactionReleaseDelayMs, () => {
    releaseTimer = 0
    if (railInside || previewInside) return GLib.SOURCE_REMOVE
    setWorkspaceInteractionId(null)
    cancelSnapTimers()
    return GLib.SOURCE_REMOVE
  })
}

export function claimWorkspaceInteraction(id: number) {
  cancelRelease()
  setWorkspaceInteractionId(id)
  scheduleSnap(id)
}

export function enterWorkspaceRail() {
  railInside = true
  cancelRelease()
}

export function leaveWorkspaceRail() {
  railInside = false
  scheduleRelease()
}

export function enterWorkspacePreview() {
  previewInside = true
  cancelRelease()
}

export function leaveWorkspacePreview() {
  previewInside = false
  scheduleRelease()
}

export function clearWorkspaceInteraction() {
  cancelRelease()
  cancelSnapTimers()
  railInside = false
  previewInside = false
  setWorkspaceInteractionId(null)
}
