import MediaCard from "./MediaCard"
import NetworkControl from "./NetworkControl"
import AudioControl from "./AudioControl"
import DisplayControl from "./DisplayControl"
import Battery from "./Battery"
import PowerControl from "./PowerControl"
import { mediaIslandEnabled } from "../lib/featureState"

export default function RightCluster() {
  return (
    <box class="right-cluster" spacing={6}>
      <box visible={mediaIslandEnabled}>
        <MediaCard />
      </box>
      <NetworkControl />
      <AudioControl />
      <DisplayControl />
      <Battery />
      <PowerControl />
    </box>
  )
}
