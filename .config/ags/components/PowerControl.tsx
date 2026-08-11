import Gtk from "gi://Gtk?version=4.0"
import AstalBattery from "gi://AstalBattery"
import AstalPowerProfiles from "gi://AstalPowerProfiles"
import { createBinding, createComputed, createState } from "ags"
import PanelTrigger from "./PanelTrigger"
import {
  lockSession,
  logoutSession,
  restartSession,
  shutdownSession,
  softRebootSession,
  suspendSession,
} from "../lib/dusky"
import { caffeineState, toggleCaffeine } from "../lib/powerState"

type ConfirmAction = "soft-reboot" | "restart" | "shutdown" | null

type ProfileName = "power-saver" | "balanced" | "performance"

const PROFILE_ORDER: ProfileName[] = ["power-saver", "balanced", "performance"]
const PROFILE_LABELS: Record<ProfileName, string> = {
  "power-saver": "Saver",
  balanced: "Balanced",
  performance: "Boost",
}

export function formatBatteryTime(seconds: number, charging = false) {
  const value = Math.max(0, Number(seconds) || 0)
  if (value < 60) return charging ? "Estimating charge time" : "Estimating runtime"

  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  const duration = hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`
  return charging ? `${duration} to full` : `${duration} remaining`
}

function availableProfileNames(powerProfiles: any): ProfileName[] {
  try {
    const profiles = powerProfiles.get_profiles() ?? []
    const names = profiles
      .map((item: any) => typeof item === "string" ? item : String(item?.profile ?? ""))
      .filter((name: string): name is ProfileName => PROFILE_ORDER.includes(name as ProfileName))
    const active = String(powerProfiles.activeProfile ?? "") as ProfileName
    const all = new Set<ProfileName>(names)
    if (PROFILE_ORDER.includes(active)) all.add(active)
    return PROFILE_ORDER.filter((profile) => all.has(profile))
  } catch (error) {
    console.error("Adaptive Glass: could not enumerate power profiles", error)
    const active = String(powerProfiles.activeProfile ?? "balanced") as ProfileName
    return PROFILE_ORDER.includes(active) ? [active] : ["balanced"]
  }
}

function PowerCommandTile({
  icon,
  label,
  tone = "neutral",
  onClicked,
}: {
  icon: string
  label: string
  tone?: "neutral" | "session" | "warning" | "danger"
  onClicked: () => void
}) {
  return (
    <button
      class={`power-command-tile ${tone}`}
      tooltipText={label}
      hexpand
      onClicked={onClicked}
    >
      <box orientation={Gtk.Orientation.VERTICAL} spacing={4} halign={Gtk.Align.CENTER} valign={Gtk.Align.CENTER}>
        <label class="power-command-icon" label={icon} />
        <label class="power-command-label" label={label} />
      </box>
    </button>
  )
}

export function PowerPanel() {
  const battery = AstalBattery.get_default()
  const powerProfiles = AstalPowerProfiles.get_default()
  const [confirmAction, setConfirmAction] = createState<ConfirmAction>(null)

  const percentage = createBinding(battery, "percentage")
  const charging = createBinding(battery, "charging")
  const timeToEmpty = createBinding(battery, "timeToEmpty")
  const timeToFull = createBinding(battery, "timeToFull")
  const isPresent = createBinding(battery, "isPresent")
  const batteryIcon = createBinding(battery, "batteryIconName")
  const activeProfile = createBinding(powerProfiles, "activeProfile")
  const availableProfiles = availableProfileNames(powerProfiles)

  const batteryPercent = percentage((value) => `${Math.round(Number(value) * 100)}%`)
  const batteryStatus = createComputed(() => {
    const percent = Number(percentage())
    if (charging()) return percent >= 0.995 ? "Fully charged" : "Charging"
    return "Discharging"
  })
  const batteryTime = createComputed(() =>
    formatBatteryTime(charging() ? Number(timeToFull()) : Number(timeToEmpty()), Boolean(charging()))
  )

  const setProfile = (profile: ProfileName) => {
    if (String(powerProfiles.activeProfile) === profile) return
    try {
      powerProfiles.set_active_profile(profile)
    } catch (error) {
      console.error(`Adaptive Glass: could not switch power profile to ${profile}`, error)
    }
  }

  const caffeineOn = caffeineState((state) => state.trim() === "on")
  const showingActions = confirmAction((action) => action === null)
  const showingConfirm = confirmAction((action) => action !== null)
  const confirmTitle = confirmAction((action) => {
    if (action === "soft-reboot") return "Soft reboot this system?"
    if (action === "restart") return "Reboot this computer?"
    return "Power off this computer?"
  })
  const confirmCopy = confirmAction((action) => {
    if (action === "soft-reboot") return "Apps will close while Linux stays powered on."
    if (action === "restart") return "Open apps will be closed."
    return "Finish your work before powering off."
  })
  const confirmButton = confirmAction((action) => {
    if (action === "soft-reboot") return "Soft reboot"
    if (action === "restart") return "Reboot"
    return "Power off"
  })

  const confirmPendingAction = () => {
    const action = confirmAction()
    setConfirmAction(null)
    if (action === "soft-reboot") softRebootSession()
    if (action === "restart") restartSession()
    if (action === "shutdown") shutdownSession()
  }

  return (
    <box class="control-panel power-panel" orientation={Gtk.Orientation.VERTICAL} spacing={8}>
      <label class="power-kicker" xalign={0} label="POWER" />

      <box class="power-battery-card" visible={isPresent} spacing={10}>
        <box class="power-battery-icon-wrap" valign={Gtk.Align.CENTER}>
          <image class="power-battery-icon" pixelSize={27} iconName={batteryIcon} />
        </box>
        <box orientation={Gtk.Orientation.VERTICAL} hexpand valign={Gtk.Align.CENTER} spacing={1}>
          <box spacing={8}>
            <label class="power-battery-percent" xalign={0} hexpand label={batteryPercent} />
            <label class="power-battery-time" label={batteryTime} />
          </box>
          <label class="power-battery-status" xalign={0} label={batteryStatus} />
        </box>
      </box>

      <box class="power-profile-section" orientation={Gtk.Orientation.VERTICAL} spacing={5}>
        <label class="power-section-label" xalign={0} label="POWER MODE" />
        <box class="power-profile-segments" homogeneous spacing={0}>
          {availableProfiles.map((profile) => (
            <button
              class={activeProfile((active) => `power-profile-segment ${String(active) === profile ? "active" : ""}`)}
              sensitive={availableProfiles.length > 1}
              tooltipText={`Switch to ${PROFILE_LABELS[profile]} mode`}
              onClicked={() => setProfile(profile)}
            >
              <label label={PROFILE_LABELS[profile]} />
            </button>
          ))}
        </box>
      </box>

      <box
        class={caffeineState((state) => `power-caffeine-row ${state.trim() === "on" ? "active" : ""}`)}
        spacing={9}
      >
        <label class="power-caffeine-icon" label="󰅶" />
        <box orientation={Gtk.Orientation.VERTICAL} hexpand valign={Gtk.Align.CENTER}>
          <label class="power-session-title" xalign={0} label="Caffeine" />
          <label
            class="power-session-subtitle"
            xalign={0}
            label={caffeineState((state) => state.trim() === "on" ? "Stay awake" : "Idle protection active")}
          />
        </box>
        <Gtk.Switch
          class="power-caffeine-switch"
          valign={Gtk.Align.CENTER}
          active={caffeineOn}
          tooltipText={caffeineState((state) => state.trim() === "on" ? "Allow idle protection again" : "Keep the computer awake")}
          onStateSet={(_self, state) => {
            if (Boolean(caffeineOn()) !== state) toggleCaffeine()
            return false
          }}
        />
      </box>

      <box class="power-command-deck" visible={showingActions} orientation={Gtk.Orientation.VERTICAL} spacing={5}>
        <box class="power-command-row" homogeneous spacing={5}>
          <PowerCommandTile icon="󰌾" label="Lock" onClicked={() => lockSession()} />
          <PowerCommandTile icon="󰤄" label="Sleep" onClicked={() => suspendSession()} />
          <PowerCommandTile icon="󰍃" label="Logout" tone="session" onClicked={() => logoutSession()} />
        </box>
        <box class="power-command-row" homogeneous spacing={5}>
          <PowerCommandTile icon="󰑓" label="Soft reboot" tone="warning" onClicked={() => setConfirmAction("soft-reboot")} />
          <PowerCommandTile icon="󰜉" label="Reboot" tone="warning" onClicked={() => setConfirmAction("restart")} />
          <PowerCommandTile icon="󰐥" label="Power off" tone="danger" onClicked={() => setConfirmAction("shutdown")} />
        </box>
      </box>

      <box class="power-confirm" visible={showingConfirm} orientation={Gtk.Orientation.VERTICAL} spacing={8}>
        <box spacing={9}>
          <label class="power-confirm-icon" label="󰀦" />
          <box orientation={Gtk.Orientation.VERTICAL} hexpand>
            <label class="power-confirm-title" xalign={0} label={confirmTitle} />
            <label class="power-confirm-copy" xalign={0} label={confirmCopy} />
          </box>
        </box>
        <box class="power-confirm-actions" spacing={6} halign={Gtk.Align.END}>
          <button class="power-confirm-button cancel" onClicked={() => setConfirmAction(null)}>
            <label label="Cancel" />
          </button>
          <button class="power-confirm-button danger" onClicked={confirmPendingAction}>
            <label label={confirmButton} />
          </button>
        </box>
      </box>
    </box>
  )
}

export default function PowerControl() {
  return (
    <PanelTrigger
      panel="power"
      class="control-leader power-leader"
      child={
        <box class="power-trigger-content" spacing={2}>
          <label label="󰐥" />
          <label class="power-caffeine-dot bar-dot" visible={caffeineState((state) => state.trim() === "on")} label="•" />
        </box>
      }
    />
  )
}
