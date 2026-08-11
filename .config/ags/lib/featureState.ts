import Gio from "gi://Gio"
import GLib from "gi://GLib"
import { createState } from "ags"
import { readFile } from "ags/file"

export type AdaptiveFeatureKey =
  | "workspace-preview"
  | "media-island"
  | "weather"
  | "notifications"

export const DEFAULT_FEATURE_ENABLED = true

const HOME = GLib.get_home_dir()
const FEATURE_DIR = `${HOME}/.config/dusky/settings/ags/features`
const FEATURE_KEYS: AdaptiveFeatureKey[] = [
  "workspace-preview",
  "media-island",
  "weather",
  "notifications",
]

const TRUE_VALUES = new Set(["true", "yes", "1", "on", "enabled"])
const FALSE_VALUES = new Set(["false", "no", "0", "off", "disabled"])

function featurePath(key: AdaptiveFeatureKey) {
  return `${FEATURE_DIR}/${key}`
}

function parseFeatureValue(raw: string) {
  const value = raw.trim().toLowerCase()
  if (TRUE_VALUES.has(value)) return true
  if (FALSE_VALUES.has(value)) return false
  return DEFAULT_FEATURE_ENABLED
}

function loadFeature(key: AdaptiveFeatureKey) {
  const path = featurePath(key)
  try {
    if (GLib.file_test(path, GLib.FileTest.EXISTS)) {
      return parseFeatureValue(readFile(path))
    }
  } catch (error) {
    console.error(`Adaptive Glass: could not read feature setting ${key}`, error)
  }
  return DEFAULT_FEATURE_ENABLED
}

export const [workspacePreviewEnabled, setWorkspacePreviewEnabled] = createState(
  loadFeature("workspace-preview"),
)
export const [mediaIslandEnabled, setMediaIslandEnabled] = createState(loadFeature("media-island"))
export const [weatherEnabled, setWeatherEnabled] = createState(loadFeature("weather"))
export const [notificationsEnabled, setNotificationsEnabled] = createState(
  loadFeature("notifications"),
)

export const featureAccessors = {
  "workspace-preview": workspacePreviewEnabled,
  "media-island": mediaIslandEnabled,
  weather: weatherEnabled,
  notifications: notificationsEnabled,
} as const

const featureSetters = {
  "workspace-preview": setWorkspacePreviewEnabled,
  "media-island": setMediaIslandEnabled,
  weather: setWeatherEnabled,
  notifications: setNotificationsEnabled,
} as const

function updateFeature(key: AdaptiveFeatureKey) {
  featureSetters[key](loadFeature(key))
}

function handleFeatureFileChanged(
  _monitor: Gio.FileMonitor,
  file: Gio.File,
  otherFile: Gio.File | null,
  eventType: Gio.FileMonitorEvent,
) {
  const changedName = file?.get_basename()
  const otherName = otherFile?.get_basename()
  const key = FEATURE_KEYS.find((candidate) =>
    candidate === changedName || candidate === otherName,
  )
  if (!key) return

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
  updateFeature(key)
}

function startFeatureMonitor() {
  try {
    GLib.mkdir_with_parents(FEATURE_DIR, 0o755)
    const dir = Gio.File.new_for_path(FEATURE_DIR)
    const monitor = dir.monitor_directory(Gio.FileMonitorFlags.NONE, null)
    monitor.connect("changed", handleFeatureFileChanged)
  } catch (error) {
    console.error("Adaptive Glass: could not monitor feature settings", error)
  }
}

startFeatureMonitor()
