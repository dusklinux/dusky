import Gtk from "gi://Gtk?version=4.0"
import { createPoll } from "ags/time"
import { execAsync } from "ags/process"
import PanelTrigger from "./PanelTrigger"
import { runNightControls, runWallpaper } from "../lib/dusky"
import { themeMode, setAdaptiveTheme } from "../lib/themeState"

function readBrightness(previous: number): Promise<number> {
  return execAsync(["brightnessctl", "-m"])
    .then((output) => {
      const match = output.match(/,(\d+)%/)
      if (!match) return previous
      const value = Number(match[1]) / 100
      return Number.isFinite(value) ? Math.min(1, Math.max(0.1, value)) : previous
    })
    .catch(() => previous)
}

function setBrightness(value: number) {
  const percent = Math.max(10, Math.min(100, Math.round(value * 100)))
  execAsync(["brightnessctl", "set", `${percent}%`]).catch((error) => {
    console.error("Adaptive Glass: could not set brightness", error)
  })
}

export function DisplayPanel() {
  const level = createPoll(0.5, 1000, (previous) => readBrightness(previous))
  const percent = level((value) => `${Math.round(value * 100)}%`)

  return (
    <box class="control-panel display-panel" orientation={Gtk.Orientation.VERTICAL} spacing={7}>
      <label class="display-kicker" xalign={0} label="DISPLAY" />

      <box class="display-brightness-card" orientation={Gtk.Orientation.VERTICAL} spacing={5}>
        <box class="display-brightness-meta" spacing={7}>
          <label class="display-brightness-icon" label="󰃠" />
          <label class="display-brightness-label" xalign={0} hexpand label="Brightness" />
          <label class="display-brightness-percent" label={percent} />
        </box>
        <box class="display-brightness-row">
          <slider
            class="display-thick-slider"
            hexpand
            widthRequest={220}
            value={level}
            onChangeValue={({ value }) => setBrightness(value)}
          />
        </box>
      </box>

      <box class="display-theme-row" spacing={8}>
        <box orientation={Gtk.Orientation.VERTICAL} hexpand>
          <label class="display-row-title" xalign={0} label="Theme" />
          <label
            class="display-row-subtitle"
            xalign={0}
            label={themeMode((mode) => mode === "light" ? "Light" : "Dark")}
          />
        </box>
        <box class="display-theme-switch-wrap" spacing={5} valign={Gtk.Align.CENTER}>
          <label
            class={themeMode((mode) => `display-theme-mode-icon sun ${mode === "light" ? "active" : ""}`)}
            label="󰖨"
          />
          <Gtk.Switch
            class="display-theme-switch"
            valign={Gtk.Align.CENTER}
            active={themeMode((mode) => mode === "dark")}
            tooltipText={themeMode((mode) => mode === "dark" ? "Switch to light mode" : "Switch to dark mode")}
            onStateSet={(_self, state) => {
              setAdaptiveTheme(state ? "dark" : "light")
              return false
            }}
          />
          <label
            class={themeMode((mode) => `display-theme-mode-icon moon ${mode === "dark" ? "active" : ""}`)}
            label="󰖔"
          />
        </box>
      </box>

      <button class="display-action-row wallpaper-row" onClicked={() => runWallpaper()}>
        <box spacing={8}>
          <label class="display-row-icon" label="󰸉" />
          <box orientation={Gtk.Orientation.VERTICAL} hexpand>
            <label class="display-row-title" xalign={0} label="Wallpaper" />
            <label class="display-row-subtitle" xalign={0} label="Choose background" />
          </box>
          <label class="display-row-chevron" label="›" />
        </box>
      </button>

      <button class="display-action-row night-row" onClicked={() => runNightControls()}>
        <box spacing={8}>
          <label class="display-row-icon night" label="󰖔" />
          <box orientation={Gtk.Orientation.VERTICAL} hexpand>
            <label class="display-row-title" xalign={0} label="Night controls" />
            <label class="display-row-subtitle" xalign={0} label="Shaders & warmth" />
          </box>
          <label class="display-row-chevron" label="›" />
        </box>
      </button>
    </box>
  )
}

export default function DisplayControl() {
  return <PanelTrigger panel="display" class="control-leader display-leader" child={<label label="󰃠" />} />
}
