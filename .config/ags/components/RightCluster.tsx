import MediaCard from "./MediaCard"
import NetworkControl from "./NetworkControl"
import AudioControl from "./AudioControl"
import DisplayControl from "./DisplayControl"
import Battery from "./Battery"
import PowerControl from "./PowerControl"
import OptionalFeature from "./OptionalFeature"
import { mediaIslandEnabled } from "../lib/featureState"

export default function RightCluster() {
  return (
    <box class="right-cluster" spacing={6}>
      <OptionalFeature enabled={mediaIslandEnabled} render={() => <MediaCard />} />
      <NetworkControl />
      <AudioControl />
      <DisplayControl />
      <Battery />
      <PowerControl />
    </box>
  )
}
