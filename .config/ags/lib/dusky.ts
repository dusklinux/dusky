import GLib from "gi://GLib"
import { execAsync } from "ags/process"

const HOME = GLib.get_home_dir()
const workspaceScript = `${HOME}/user_scripts/hypr/multi_monitor_workspace.sh`

async function runShell(command: string) {
  try {
    await execAsync(["bash", "-lc", command])
  } catch (error) {
    console.error(`Adaptive Glass command failed: ${command}`, error)
    try {
      await execAsync(["notify-send", "Adaptive Glass", "A Dusky action failed. Check the AGS terminal log."])
    } catch (_) {
      // Keep the shell alive even if notifications are unavailable.
    }
  }
}


export async function focusWorkspace(id: number) {
  if (!Number.isInteger(id) || id < 1 || id > 10) return

  try {
    await execAsync(["bash", workspaceScript, "workspace", String(id)])
  } catch (error) {
    console.error(`Adaptive Glass workspace switch failed: ${id}`, error)
    try {
      await execAsync(["notify-send", "Adaptive Glass", `Could not switch to workspace ${id}. Check the AGS terminal log.`])
    } catch (_) {
      // Keep the shell alive even if notifications are unavailable.
    }
  }
}

export async function focusWindow(address: string) {
  const clean = String(address || "").replace(/^0x/, "").replace(/[^a-fA-F0-9]/g, "")
  if (!clean) return

  try {
    await execAsync(["hyprctl", "dispatch", `hl.dsp.focus({ window = "address:0x${clean}" })`])
  } catch (error) {
    console.error(`Adaptive Glass exact window focus failed: 0x${clean}`, error)
  }
}

export function runLauncher() {
  return runShell(`python3 "${HOME}/user_scripts/dusky_system/control_center/dusky_control_center.py"`)
}

export function runAppLauncher() {
  return runShell("dusky-run rofi -show drun")
}

export function runQuickPanel() {
  return runShell("gdbus call --session --dest org.dusky.quickpanal --object-path /org/dusky/quickpanal --method org.freedesktop.Application.Activate '{}'")
}

export function runNotificationCenter() {
  return runShell(`"${HOME}/user_scripts/rofi/rofi_mako.sh"`)
}

export function toggleDnd() {
  return runShell("makoctl mode -t do-not-disturb")
}

export function clearNotifications() {
  return runShell(`"${HOME}/user_scripts/waybar/mako.sh" --clear`)
}

export function runNetworkManager() {
  const main = `"${HOME}/user_scripts/dusky_tui/python/main/main.py"`
  const network = `"${HOME}/user_scripts/network_manager/tui_dusky_network.py"`
  return runShell(`if command -v foot >/dev/null 2>&1; then
    foot --app-id=dusky_tui python ${main} ${network}
  elif command -v kitty >/dev/null 2>&1; then
    kitty --class dusky_tui -e python ${main} ${network}
  elif command -v wezterm >/dev/null 2>&1; then
    wezterm start --class dusky_tui -- python ${main} ${network}
  else
    notify-send "Adaptive Glass" "No supported terminal found for Dusky Network."
  fi`)
}

export function runBluetoothManager() {
  return runShell("dusky-run blueman-manager")
}

export function runAudioMixer() {
  return runShell(`if command -v pavucontrol >/dev/null 2>&1; then
    dusky-run pavucontrol
  elif command -v pwvucontrol >/dev/null 2>&1; then
    dusky-run pwvucontrol
  else
    dusky-run python3 "${HOME}/user_scripts/dusky_system/quickpanal/dusky_quickpanal.py"
  fi`)
}

export function runCava() {
  return runShell("dusky-run kitty --class cava -e cava")
}

export function runTheme(mode: "dark" | "light" = "dark") {
  return runShell(`"${HOME}/user_scripts/theme_matugen/theme_ctl.sh" set --mode ${mode}`)
}

export function runWallpaper() {
  return runShell(`dusky-run "${HOME}/user_scripts/images/wallpaper_selector.py"`)
}

export function runNightControls() {
  return runShell(`dusky-run "${HOME}/user_scripts/rofi/shader_menu.sh"`)
}

export function runPowerMenu() {
  return runShell(`dusky-run "${HOME}/user_scripts/wlogout/wlogout_scale.sh"`)
}

export function toggleIdle() {
  return runShell(`dusky-run "${HOME}/user_scripts/waybar/toggle_hypridle.sh"`)
}

export function lockSession() {
  return runShell("hyprlock")
}

export function suspendSession() {
  return runShell("systemctl suspend")
}

export function logoutSession() {
  return runShell(`dusky-run "${HOME}/user_scripts/wlogout/dusky_session.sh" logout`)
}

export function softRebootSession() {
  return runShell(`dusky-run "${HOME}/user_scripts/wlogout/dusky_session.sh" soft-reboot`)
}

export function restartSession() {
  return runShell(`dusky-run "${HOME}/user_scripts/wlogout/dusky_session.sh" reboot`)
}

export function shutdownSession() {
  return runShell(`dusky-run "${HOME}/user_scripts/wlogout/dusky_session.sh" poweroff`)
}

export function openClocks() {
  return runShell("dusky-run gnome-clocks")
}

export function openTerminalCalendar() {
  return runShell("dusky-run kitty --class peaclock -e peaclock")
}
