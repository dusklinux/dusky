#!/usr/bin/env -S ags run
import { For, createBinding } from "ags"
import app from "ags/gtk4/app"
import GLib from "gi://GLib"
import { readFile } from "ags/file"
import fallback from "./styles/fallback.css"
import shellStyle from "./style.css"
import PopupWindows from "./components/PopupWindows"

const home = GLib.get_home_dir()
const matugenPath = `${home}/.config/matugen/generated/waybar-colors.css`
let matugen = ""

try {
  if (GLib.file_test(matugenPath, GLib.FileTest.EXISTS)) {
    matugen = readFile(matugenPath)
  }
} catch (error) {
  console.error("Adaptive Glass: could not load Matugen palette", error)
}

app.start({
  instanceName: "dusky-adaptive-glass",
  css: `${fallback}\n${matugen}\n${shellStyle}`,
  gtkTheme: "Adwaita",
  main() {
    const monitors = createBinding(app, "monitors")

    return (
      <For each={monitors}>
        {(monitor) => <PopupWindows gdkmonitor={monitor} />}
      </For>
    )
  },
})
