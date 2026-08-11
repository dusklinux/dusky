import Gio from "gi://Gio"
import GLib from "gi://GLib"
import { createState } from "ags"
import { readFile } from "ags/file"

export type AdaptiveMotionStyle = "soft-magnetic" | "precise-futuristic"

export const DEFAULT_MOTION_STYLE: AdaptiveMotionStyle = "soft-magnetic"

const HOME = GLib.get_home_dir()
const STATE_DIR = `${HOME}/.config/dusky/settings/ags`
const STATE_FILE = "adaptive-glass-motion"
const STATE_PATH = `${STATE_DIR}/${STATE_FILE}`

const VALID_MOTION_STYLES = new Set<AdaptiveMotionStyle>([
  "soft-magnetic",
  "precise-futuristic",
])

let motionMonitor: Gio.FileMonitor | null = null

const WORKSPACE_TIMINGS = {
  "soft-magnetic": {
    interactionReleaseDelayMs: 140,
    snapDelayMs: 205,
    snapPulseMs: 560,
  },
  "precise-futuristic": {
    interactionReleaseDelayMs: 85,
    snapDelayMs: 125,
    snapPulseMs: 320,
  },
} as const

function isMotionStyle(value: string): value is AdaptiveMotionStyle {
  return VALID_MOTION_STYLES.has(value as AdaptiveMotionStyle)
}

function loadMotionStyle(): AdaptiveMotionStyle {
  try {
    if (GLib.file_test(STATE_PATH, GLib.FileTest.EXISTS)) {
      const saved = readFile(STATE_PATH).trim()
      if (isMotionStyle(saved)) return saved
    }
  } catch (error) {
    console.error("Adaptive Glass: could not read saved motion style", error)
  }
  return DEFAULT_MOTION_STYLE
}

export const [motionStyle, setMotionStyle] = createState<AdaptiveMotionStyle>(loadMotionStyle())
export const motionClass = motionStyle((style) => `motion-${style}`)

export function getWorkspaceMotionTiming() {
  return WORKSPACE_TIMINGS[motionStyle()]
}

function handleMotionFileChanged(
  _monitor: Gio.FileMonitor,
  file: Gio.File,
  otherFile: Gio.File | null,
  eventType: Gio.FileMonitorEvent,
) {
  const changedName = file?.get_basename()
  const otherName = otherFile?.get_basename()
  if (STATE_FILE !== changedName && STATE_FILE !== otherName) return

  const handledEvents = new Set<Gio.FileMonitorEvent>([
    Gio.FileMonitorEvent.CHANGED,
    Gio.FileMonitorEvent.CHANGES_DONE_HINT,
    Gio.FileMonitorEvent.CREATED,
    Gio.FileMonitorEvent.DELETED,
  ])

  const movedIn = (Gio.FileMonitorEvent as any).MOVED_IN
  const renamed = (Gio.FileMonitorEvent as any).RENAMED
  if (movedIn !== undefined) handledEvents.add(movedIn)
  if (renamed !== undefined) handledEvents.add(renamed)

  if (!handledEvents.has(eventType)) return
  setMotionStyle(loadMotionStyle())
}

function startMotionMonitor() {
  try {
    GLib.mkdir_with_parents(STATE_DIR, 0o755)
    const dir = Gio.File.new_for_path(STATE_DIR)
    motionMonitor = dir.monitor_directory(Gio.FileMonitorFlags.NONE, null)
    motionMonitor.connect("changed", handleMotionFileChanged)
  } catch (error) {
    console.error("Adaptive Glass: could not monitor motion settings", error)
  }
}

startMotionMonitor()
