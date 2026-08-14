#!/usr/bin/env -S ags run
import { For, createBinding } from "ags"
import app from "ags/gtk4/app"
import Gio from "gi://Gio"
import GLib from "gi://GLib"
import { readFile } from "ags/file"
import fallback from "./styles/fallback.css"
import shellStyle from "./style.css"
import PopupWindows from "./components/PopupWindows"
import { clock24hEnabled } from "./lib/clockState"
import { featureAccessors } from "./lib/featureState"
import { motionStyle } from "./lib/motionState"

const home = GLib.get_home_dir()
const matugenDir = `${home}/.config/matugen/generated`
const matugenPath = `${matugenDir}/waybar-colors.css`
let matugenMonitor: Gio.FileMonitor | null = null
let matugenReloadSource = 0

function loadMatugenCss() {
  try {
    if (GLib.file_test(matugenPath, GLib.FileTest.EXISTS)) {
      return readFile(matugenPath)
    }
  } catch (error) {
    console.error("Adaptive Glass: could not load Matugen palette", error)
  }
  return ""
}

function composeCss() {
  return `${fallback}\n${loadMatugenCss()}\n${shellStyle}`
}

function scheduleMatugenReload() {
  if (matugenReloadSource !== 0) GLib.source_remove(matugenReloadSource)

  matugenReloadSource = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 90, () => {
    app.apply_css(composeCss(), true)
    matugenReloadSource = 0
    return GLib.SOURCE_REMOVE
  })
}

function startMatugenMonitor() {
  try {
    if (!GLib.file_test(matugenDir, GLib.FileTest.IS_DIR)) return

    const dir = Gio.File.new_for_path(matugenDir)
    matugenMonitor = dir.monitor_directory(Gio.FileMonitorFlags.NONE, null)
    matugenMonitor.connect("changed", (_monitor, file, otherFile) => {
      const changedName = file?.get_basename()
      const otherName = otherFile?.get_basename()
      if (changedName !== "waybar-colors.css" && otherName !== "waybar-colors.css") return
      scheduleMatugenReload()
    })
  } catch (error) {
    console.error("Adaptive Glass: could not monitor Matugen palette", error)
  }
}

function preferenceState() {
  return {
    motion: motionStyle(),
    "clock-24h": clock24hEnabled(),
    features: {
      "workspace-preview": featureAccessors["workspace-preview"](),
      "media-island": featureAccessors["media-island"](),
      "weather": featureAccessors.weather(),
      "notifications": featureAccessors.notifications(),
    },
  }
}

startMatugenMonitor()

app.start({
  instanceName: "dusky-adaptive-glass",
  css: composeCss(),
  gtkTheme: "Adwaita",
  requestHandler(argv, response) {
    const [command] = argv

    if (command === "state") {
      response(JSON.stringify(preferenceState()))
      return
    }

    response("usage: ags request -i dusky-adaptive-glass state")
  },
  main() {
    const monitors = createBinding(app, "monitors")

    return (
      <For each={monitors}>
        {(monitor) => <PopupWindows gdkmonitor={monitor} />}
      </For>
    )
  },
})
