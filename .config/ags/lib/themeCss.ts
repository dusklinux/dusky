import app from "ags/gtk4/app"
import GLib from "gi://GLib"
import { readFile } from "ags/file"
import fallback from "../styles/fallback.css"
import shellStyle from "../style.css"

const HOME = GLib.get_home_dir()
const MATUGEN_PATH = `${HOME}/.config/matugen/generated/waybar-colors.css`

function readMatugenPalette() {
  try {
    if (GLib.file_test(MATUGEN_PATH, GLib.FileTest.EXISTS)) {
      return readFile(MATUGEN_PATH)
    }
  } catch (error) {
    console.error("Adaptive Glass: could not reload Matugen palette", error)
  }
  return ""
}

export function reloadAdaptiveCss() {
  try {
    const matugen = readMatugenPalette()
    app.reset_css()
    app.apply_css(`${fallback}\n${matugen}\n${shellStyle}`)
  } catch (error) {
    console.error("Adaptive Glass: could not reload theme CSS", error)
  }
}
