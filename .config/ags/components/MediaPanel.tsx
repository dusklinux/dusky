import Gtk from "gi://Gtk?version=4.0"
import Pango from "gi://Pango"
import AstalMpris from "gi://AstalMpris"
import AstalApps from "gi://AstalApps"
import { With, createBinding } from "ags"
import { createPoll } from "ags/time"
import { additionalPlayingPlayers, mediaSnapshot } from "../lib/mediaState"
import { closePanel } from "../lib/popupState"

function sourceIconName(player: any, apps: AstalApps.Apps) {
  try {
    const [sourceApp] = apps.exact_query(player.entry)
    return sourceApp?.iconName ?? "audio-x-generic-symbolic"
  } catch {
    return "audio-x-generic-symbolic"
  }
}

function MediaTransport({ player }: { player: any }) {
  const playing = createBinding(player, "playbackStatus")((status) => status === AstalMpris.PlaybackStatus.PLAYING)
  return (
    <box class="media-popup-controls compact" spacing={3} valign={Gtk.Align.CENTER}>
      <button class="media-popup-control" tooltipText="Previous" visible={createBinding(player, "canGoPrevious")} onClicked={() => player.previous()}>
        <label label="󰒮" />
      </button>
      <button class="media-popup-control play" tooltipText="Play / Pause" visible={createBinding(player, "canControl")} onClicked={() => player.play_pause()}>
        <label label={playing((value) => value ? "󰏤" : "󰐊")} />
      </button>
      <button class="media-popup-control" tooltipText="Next" visible={createBinding(player, "canGoNext")} onClicked={() => player.next()}>
        <label label="󰒭" />
      </button>
    </box>
  )
}

function MediaSourceRow({ player, apps }: { player: any, apps: AstalApps.Apps }) {
  const title = createBinding(player, "title")
  const artist = createBinding(player, "artist")
  const coverArt = createBinding(player, "coverArt")
  const sourceIcon = sourceIconName(player, apps)

  return (
    <box class="media-source-row playing" spacing={8}>
      <box class="media-source-row-art" overflow={Gtk.Overflow.HIDDEN}>
        <image pixelSize={34} file={coverArt} visible={coverArt(Boolean)} />
        <image pixelSize={20} iconName={sourceIcon} visible={coverArt((path) => !path)} />
      </box>
      <box orientation={Gtk.Orientation.VERTICAL} hexpand valign={Gtk.Align.CENTER}>
        <label class="media-source-row-title" xalign={0} maxWidthChars={28} ellipsize={Pango.EllipsizeMode.END} label={title((value) => value || player.identity || "Media")} />
        <label class="media-source-row-meta" xalign={0} maxWidthChars={28} ellipsize={Pango.EllipsizeMode.END} label={artist((value) => value || player.identity || "Media")} />
      </box>
      <MediaTransport player={player} />
    </box>
  )
}

export default function MediaPanel() {
  const apps = new AstalApps.Apps()
  const popupAvailable = createPoll(false, 250, () => {
    const alternatives = additionalPlayingPlayers(mediaSnapshot())
    if (alternatives.length === 0) closePanel("media")
    return alternatives.length > 0
  })

  return (
    <box class="control-panel media-popup-panel" orientation={Gtk.Orientation.VERTICAL} spacing={6} visible={popupAvailable}>
      <With value={mediaSnapshot}>
        {(snapshot) => {
          const alternatives = additionalPlayingPlayers(snapshot)
          if (alternatives.length === 0) return <box visible={false} />

          return (
            <box orientation={Gtk.Orientation.VERTICAL} spacing={5}>
              <label class="media-popup-section" xalign={0} label="OTHER MEDIA" />
              <box class="media-source-list" orientation={Gtk.Orientation.VERTICAL} spacing={4}>
                {alternatives.map((player) => <MediaSourceRow player={player} apps={apps} />)}
              </box>
            </box>
          )
        }}
      </With>
    </box>
  )
}
