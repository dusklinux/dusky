import GLib from "gi://GLib"
import { createState } from "ags"
import { readFile } from "ags/file"
import { execAsync } from "ags/process"
import { runTheme } from "./dusky"
import { reloadAdaptiveCss } from "./themeCss"

export type AdaptiveThemeMode = "light" | "dark"

const HOME = GLib.get_home_dir()
const STATE_DIR = `${HOME}/.config/dusky/settings/ags`
const STATE_PATH = `${STATE_DIR}/adaptive-glass-theme`

function loadThemeMode(): AdaptiveThemeMode {
  try {
    if (GLib.file_test(STATE_PATH, GLib.FileTest.EXISTS)) {
      const saved = readFile(STATE_PATH).trim()
      if (saved === "light" || saved === "dark") return saved
    }
  } catch (error) {
    console.error("Adaptive Glass: could not read saved theme mode", error)
  }
  return "dark"
}

export const [themeMode, setThemeMode] = createState<AdaptiveThemeMode>(loadThemeMode())

function persistThemeMode(mode: AdaptiveThemeMode) {
  execAsync([
    "bash",
    "-lc",
    `mkdir -p "${STATE_DIR}" && printf '%s\\n' "${mode}" > "${STATE_PATH}"`,
  ]).catch((error) => {
    console.error("Adaptive Glass: could not persist theme mode", error)
  })
}

export function setAdaptiveTheme(mode: AdaptiveThemeMode) {
  setThemeMode(mode)
  persistThemeMode(mode)

  // Keep Dusky/Matugen as the system-theme backend. Once it has updated its
  // generated palette, re-apply the AGS stylesheet so new wallpaper/theme
  // accents can be picked up without restarting the shell.
  runTheme(mode)
    .then(() => reloadAdaptiveCss())
    .catch((error) => console.error("Adaptive Glass: theme update failed", error))
}
