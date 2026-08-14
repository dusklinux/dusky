import Gio from "gi://Gio"
import GLib from "gi://GLib"
import { createState } from "ags"
import { readFile } from "ags/file"

export const DEFAULT_CLOCK_24H_ENABLED = false

const HOME = GLib.get_home_dir()
const STATE_DIR = `${HOME}/.config/dusky/settings/ags`
const STATE_FILE = "clock-24h"
const STATE_PATH = `${STATE_DIR}/${STATE_FILE}`

const TRUE_VALUES = new Set(["true", "yes", "1", "on", "enabled"])
const FALSE_VALUES = new Set(["false", "no", "0", "off", "disabled"])

let clock24hMonitor: Gio.FileMonitor | null = null

function parseClock24hValue(raw: string) {
  const value = raw.trim().toLowerCase()
  if (TRUE_VALUES.has(value)) return true
  if (FALSE_VALUES.has(value)) return false
  return DEFAULT_CLOCK_24H_ENABLED
}

function loadClock24hEnabled() {
  try {
    if (GLib.file_test(STATE_PATH, GLib.FileTest.EXISTS)) {
      return parseClock24hValue(readFile(STATE_PATH))
    }
  } catch (error) {
    console.error("Adaptive Glass: could not read 24-hour clock setting", error)
  }
  return DEFAULT_CLOCK_24H_ENABLED
}

export const [clock24hEnabled, setClock24hEnabled] = createState(loadClock24hEnabled())

function handleClock24hFileChanged(
  _monitor: Gio.FileMonitor,
  _file: Gio.File,
  _otherFile: Gio.File | null,
  eventType: Gio.FileMonitorEvent,
) {
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
  setClock24hEnabled(loadClock24hEnabled())
}

function startClock24hMonitor() {
  try {
    GLib.mkdir_with_parents(STATE_DIR, 0o755)
    const file = Gio.File.new_for_path(STATE_PATH)
    clock24hMonitor = file.monitor_file(Gio.FileMonitorFlags.NONE, null)
    clock24hMonitor.connect("changed", handleClock24hFileChanged)
  } catch (error) {
    console.error("Adaptive Glass: could not monitor 24-hour clock setting", error)
  }
}

startClock24hMonitor()
