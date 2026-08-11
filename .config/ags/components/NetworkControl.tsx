import Gtk from "gi://Gtk?version=4.0"
import AstalNetwork from "gi://AstalNetwork"
import AstalBluetooth from "gi://AstalBluetooth"
import { createBinding, createComputed } from "ags"
import { createPoll } from "ags/time"
import PanelTrigger from "./PanelTrigger"
import { runBluetoothManager, runNetworkManager } from "../lib/dusky"
import { formatBytes, formatRate, formatSince, networkSession } from "../lib/networkSession"

function ConnectionHeader({ network }: { network: any }) {
  const ssid = createBinding(network, "wifi", "ssid")((value) => value || "Not connected")
  const signal = createBinding(network, "wifi", "activeAccessPoint", "strength")((value) => Number(value ?? 0))
  const icon = createBinding(network, "wifi", "iconName")((value) => value || "network-wireless-offline-symbolic")
  const status = signal((value) => value > 0 ? `Connected · Signal ${value}%` : "Wi-Fi available")

  return (
    <box class="network-connection-header" spacing={12}>
      <image class="network-connection-icon" pixelSize={28} iconName={icon} />
      <box class="network-connection-copy" orientation={Gtk.Orientation.VERTICAL} valign={Gtk.Align.CENTER} hexpand>
        <label class="network-connection-name" xalign={0} label={ssid} />
        <label class="network-connection-status" xalign={0} label={status} />
      </box>
    </box>
  )
}

function LiveTraffic() {
  return (
    <box class="network-section" orientation={Gtk.Orientation.VERTICAL} spacing={3}>
      <label class="network-section-kicker" xalign={0} label="LIVE TRAFFIC" />
      <box class="network-live-grid">
        <box class="network-live-metric download" orientation={Gtk.Orientation.VERTICAL} spacing={2} hexpand>
          <box class="network-live-value-row" spacing={6} halign={Gtk.Align.CENTER}>
            <image class="network-transfer-icon download" pixelSize={14} iconName="go-down-symbolic" />
            <label class="network-live-value" label={networkSession((sample) => formatRate(sample.rxRate))} />
          </box>
          <label class="network-live-label" label="Download" />
        </box>
        <Gtk.Separator class="network-metric-divider" orientation={Gtk.Orientation.VERTICAL} />
        <box class="network-live-metric upload" orientation={Gtk.Orientation.VERTICAL} spacing={2} hexpand>
          <box class="network-live-value-row" spacing={6} halign={Gtk.Align.CENTER}>
            <image class="network-transfer-icon upload" pixelSize={14} iconName="go-up-symbolic" />
            <label class="network-live-value" label={networkSession((sample) => formatRate(sample.txRate))} />
          </box>
          <label class="network-live-label" label="Upload" />
        </box>
      </box>
    </box>
  )
}

function SessionData() {
  return (
    <box class="network-section" orientation={Gtk.Orientation.VERTICAL} spacing={3}>
      <label class="network-section-kicker" xalign={0} label="SESSION DATA" />
      <box class="network-session-card" orientation={Gtk.Orientation.VERTICAL} spacing={4}>
        <label
          class="network-session-total"
          label={networkSession((sample) => `${formatBytes(sample.rxTotal + sample.txTotal)} used`)}
        />
        <box class="network-session-breakdown">
          <box class="network-session-stat" spacing={5} halign={Gtk.Align.CENTER} hexpand>
            <image class="network-transfer-icon download" pixelSize={13} iconName="go-down-symbolic" />
            <label label={networkSession((sample) => formatBytes(sample.rxTotal))} />
          </box>
          <Gtk.Separator class="network-metric-divider" orientation={Gtk.Orientation.VERTICAL} />
          <box class="network-session-stat" spacing={5} halign={Gtk.Align.CENTER} hexpand>
            <image class="network-transfer-icon upload" pixelSize={13} iconName="go-up-symbolic" />
            <label label={networkSession((sample) => formatBytes(sample.txTotal))} />
          </box>
        </box>
        <label
          class="network-session-since"
          label={networkSession((sample) => `Since ${formatSince(sample.sinceEpoch)}`)}
        />
      </box>
    </box>
  )
}

function QuickAction({
  iconName,
  title,
  subtitle,
  actionLabel,
  onClicked,
}: {
  iconName: any
  title: string
  subtitle?: any
  actionLabel: string
  onClicked: () => void
}) {
  return (
    <button class="network-quick-action" onClicked={onClicked}>
      <box spacing={10}>
        <image class="network-quick-icon" pixelSize={19} iconName={iconName} />
        <box class="network-quick-copy" orientation={Gtk.Orientation.VERTICAL} valign={Gtk.Align.CENTER} hexpand>
          <label class="network-quick-title" xalign={0} label={title} />
          {subtitle && <label class="network-quick-subtitle" xalign={0} label={subtitle as any} />}
        </box>
        <label class="network-quick-action-label" label={actionLabel} />
        <image class="network-quick-chevron" pixelSize={13} iconName="go-next-symbolic" />
      </box>
    </button>
  )
}

export function NetworkPanel() {
  const network = AstalNetwork.get_default()
  const bluetooth = AstalBluetooth.get_default()

  const btPowered = createPoll("Off", 1800, [
    "bash",
    "-lc",
    `bluetoothctl show 2>/dev/null | awk -F': ' '/Powered:/ {print $2; exit}' | grep -qx yes && printf On || printf Off`,
  ])
  const connectedCount = createPoll(0, 1500, () =>
    bluetooth.get_devices().filter((device: any) => Boolean(device.connected)).length,
  )
  const btSummary = createComputed(() => {
    if (btPowered() !== "On") return "Off"
    const count = connectedCount()
    if (count === 0) return "On"
    return count === 1 ? "1 connected" : `${count} connected`
  })
  const wifiActionIcon = createBinding(network, "wifi", "iconName")((icon) => icon || "network-wireless-offline-symbolic")
  const btActionIcon = createComputed(() => btPowered() === "On" ? "bluetooth-active-symbolic" : "bluetooth-disabled-symbolic")

  return (
    <box class="network-dashboard" orientation={Gtk.Orientation.VERTICAL} spacing={6}>
      <ConnectionHeader network={network} />
      <Gtk.Separator class="network-divider" />
      <LiveTraffic />
      <SessionData />
      <box class="network-actions" orientation={Gtk.Orientation.VERTICAL} spacing={4}>
        <QuickAction
          iconName={wifiActionIcon}
          title="Wi-Fi"
          actionLabel="Manage"
          onClicked={() => runNetworkManager()}
        />
        <QuickAction
          iconName={btActionIcon}
          title="Bluetooth"
          subtitle={btSummary as any}
          actionLabel="Devices"
          onClicked={() => runBluetoothManager()}
        />
      </box>
    </box>
  )
}

export default function NetworkControl() {
  const network = AstalNetwork.get_default()
  return (
    <PanelTrigger
      panel="network"
      class="control-leader network-leader"
      child={<image iconName={createBinding(network, "wifi", "iconName")((icon) => icon || "network-wireless-offline-symbolic")} />}
    />
  )
}
