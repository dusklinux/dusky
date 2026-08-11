import Launcher from "./Launcher"
import Workspaces from "./Workspaces"
import Weather from "./Weather"
import Notification from "./Notification"
import { notificationsEnabled, weatherEnabled } from "../lib/featureState"

export default function LeftCluster() {
  return (
    <box class="left-cluster" spacing={6}>
      <Launcher />
      <Workspaces />
      <box visible={weatherEnabled}>
        <Weather />
      </box>
      <box visible={notificationsEnabled}>
        <Notification />
      </box>
    </box>
  )
}
