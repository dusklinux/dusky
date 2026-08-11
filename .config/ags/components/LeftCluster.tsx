import Launcher from "./Launcher"
import Workspaces from "./Workspaces"
import Weather from "./Weather"
import Notification from "./Notification"
import OptionalFeature from "./OptionalFeature"
import { notificationsEnabled, weatherEnabled } from "../lib/featureState"

export default function LeftCluster() {
  return (
    <box class="left-cluster" spacing={6}>
      <Launcher />
      <Workspaces />
      <OptionalFeature enabled={weatherEnabled} render={() => <Weather />} />
      <OptionalFeature enabled={notificationsEnabled} render={() => <Notification />} />
    </box>
  )
}
