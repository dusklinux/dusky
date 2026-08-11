import app from "ags/gtk4/app"
import { This } from "ags"
import Astal from "gi://Astal?version=4.0"
import Gdk from "gi://Gdk?version=4.0"
import PopupWindow from "./PopupWindow"
import Bar from "./Bar"
import { CalendarPanel } from "./ClockCard"
import { NetworkPanel } from "./NetworkControl"
import { AudioPanel } from "./AudioControl"
import { DisplayPanel } from "./DisplayControl"
import { PowerPanel } from "./PowerControl"
import WorkspacePreview from "./WorkspacePreview"
import MediaPanel from "./MediaPanel"
import { mediaIslandEnabled, workspacePreviewEnabled } from "../lib/featureState"
import { setWorkspacePreviewWindow } from "../lib/workspacePreviewState"

export default function PopupWindows({ gdkmonitor }: { gdkmonitor: Gdk.Monitor }) {
  const { TOP, LEFT, RIGHT } = Astal.WindowAnchor

  return (
    <This this={app}>
      <Bar gdkmonitor={gdkmonitor} />
      <PopupWindow
        id="workspace"
        gdkmonitor={gdkmonitor}
        anchor={TOP | LEFT}
        marginTop={42}
        marginLeft={6}
        child={<WorkspacePreview />}
        windowRef={setWorkspacePreviewWindow}
        enabled={workspacePreviewEnabled}
      />
      <PopupWindow id="calendar" gdkmonitor={gdkmonitor} anchor={TOP} marginTop={42} child={<CalendarPanel />} />
      <PopupWindow id="media" gdkmonitor={gdkmonitor} anchor={TOP | RIGHT} marginTop={42} marginRight={188} child={<MediaPanel />} enabled={mediaIslandEnabled} />
      <PopupWindow id="network" gdkmonitor={gdkmonitor} anchor={TOP | RIGHT} marginTop={42} marginRight={10} child={<NetworkPanel />} />
      <PopupWindow id="audio" gdkmonitor={gdkmonitor} anchor={TOP | RIGHT} marginTop={42} marginRight={10} child={<AudioPanel />} />
      <PopupWindow id="display" gdkmonitor={gdkmonitor} anchor={TOP | RIGHT} marginTop={42} marginRight={10} child={<DisplayPanel />} />
      <PopupWindow id="power" gdkmonitor={gdkmonitor} anchor={TOP | RIGHT} marginTop={42} marginRight={10} child={<PowerPanel />} />
    </This>
  )
}
