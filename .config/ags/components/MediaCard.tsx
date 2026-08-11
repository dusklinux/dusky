import Gtk from "gi://Gtk?version=4.0"
import Pango from "gi://Pango"
import AstalMpris from "gi://AstalMpris"
import AstalApps from "gi://AstalApps"
import { With, createBinding } from "ags"
import MarqueeText from "./MarqueeText"
import { additionalPlayingPlayers, mediaSnapshot } from "../lib/mediaState"
import { hoverPanel, leaveTrigger, togglePin } from "../lib/popupState"

function sourceIconName(player: any, apps: AstalApps.Apps) {
  try {
    const [sourceApp] = apps.exact_query(player.entry)
    return sourceApp?.iconName ?? "audio-x-generic-symbolic"
  } catch {
    return "audio-x-generic-symbolic"
  }
}

function Transport({ player }: { player: any }) {
  const playing = createBinding(player, "playbackStatus")((status) => status === AstalMpris.PlaybackStatus.PLAYING)
  return (
    <box class="media-controls" spacing={2} valign={Gtk.Align.CENTER}>
      <button class="media-control" tooltipText="Previous" visible={createBinding(player, "canGoPrevious")} onClicked={() => player.previous()}>
        <label label="󰒮" />
      </button>
      <button class="media-control play" tooltipText="Play / Pause" visible={createBinding(player, "canControl")} onClicked={() => player.play_pause()}>
        <label label={playing((value) => value ? "󰏤" : "󰐊")} />
      </button>
      <button class="media-control" tooltipText="Next" visible={createBinding(player, "canGoNext")} onClicked={() => player.next()}>
        <label label="󰒭" />
      </button>
    </box>
  )
}

export default function MediaCard() {
  const apps = new AstalApps.Apps()

  return (
    <box class="media-slot">
      <With value={mediaSnapshot}>
        {(snapshot) => {
          const player = snapshot.primary
          if (!player) return <box visible={false} />

          const alternatives = additionalPlayingPlayers(snapshot)
          const popupAvailable = alternatives.length > 0
          const artist = createBinding(player, "artist")
          const coverArt = createBinding(player, "coverArt")
          const sourceIcon = sourceIconName(player, apps)

          return (
            <box class={`media-card ${popupAvailable ? "has-alternatives" : "solo"}`} spacing={4}>
              <Gtk.EventControllerMotion
                onEnter={() => popupAvailable && hoverPanel("media")}
                onLeave={() => popupAvailable && leaveTrigger("media")}
              />

              <button
                class="media-main-hit"
                tooltipText={popupAvailable ? "Other playing media" : "Media"}
                onClicked={() => {
                  if (popupAvailable) togglePin("media")
                }}
              >
                <box spacing={7}>
                  <box class="media-art-frame" overflow={Gtk.Overflow.HIDDEN}>
                    <image class="media-art" pixelSize={24} file={coverArt} visible={coverArt(Boolean)} />
                    <image class="media-source-icon" pixelSize={16} iconName={sourceIcon} visible={coverArt((path) => !path)} />
                  </box>
                  <box class="media-copy" orientation={Gtk.Orientation.VERTICAL} valign={Gtk.Align.CENTER}>
                    <MarqueeText text={() => player.title ?? "Unknown track"} width={30} />
                    <label
                      class="media-artist"
                      xalign={0}
                      maxWidthChars={22}
                      ellipsize={Pango.EllipsizeMode.END}
                      label={artist((value) => value || player.identity || "Media")}
                    />
                  </box>
                </box>
              </button>

              <Transport player={player} />
            </box>
          )
        }}
      </With>
    </box>
  )
}
