import Gtk from "gi://Gtk?version=4.0"
import AstalWp from "gi://AstalWp"
import { For, createBinding } from "ags"
import PanelTrigger from "./PanelTrigger"
import { runAudioMixer } from "../lib/dusky"

function OutputRow({ speaker }: { speaker: AstalWp.Endpoint }) {
  const volume = createBinding(speaker, "volume")
  const percent = volume((value) => `${Math.round(value * 100)}%`)
  const description = createBinding(speaker, "description")((value) => value || speaker.name || "Speakers")

  return (
    <box class="audio-channel-row output-channel" orientation={Gtk.Orientation.VERTICAL} spacing={4}>
      <label class="audio-section-kicker" xalign={0} label="OUTPUT" />
      <label class="audio-device-label" xalign={0} ellipsize={3} maxWidthChars={30} label={description} />

      <box class="audio-control-line" spacing={8}>
        <button
          class={createBinding(speaker, "mute")((muted) => muted ? "audio-channel-icon muted" : "audio-channel-icon")}
          tooltipText={createBinding(speaker, "mute")((muted) => muted ? "Unmute output" : "Mute output")}
          valign={Gtk.Align.CENTER}
          onClicked={() => speaker.set_mute(!speaker.mute)}
        >
          <image pixelSize={19} iconName={createBinding(speaker, "volumeIcon")} />
        </button>

        <slider
          class="audio-thick-slider output-slider"
          hexpand
          valign={Gtk.Align.CENTER}
          value={volume}
          onChangeValue={({ value }) => speaker.set_volume(value)}
        />

        <label class="audio-channel-percent" valign={Gtk.Align.CENTER} label={percent} />
      </box>
    </box>
  )
}

function InputRow({ microphone, inUse }: { microphone: AstalWp.Endpoint; inUse: any }) {
  const volume = createBinding(microphone, "volume")
  const percent = volume((value) => `${Math.round(value * 100)}%`)
  const description = createBinding(microphone, "description")((value) => value || microphone.name || "Microphone")
  const micIcon = createBinding(microphone, "mute")((muted) =>
    muted ? "microphone-sensitivity-muted-symbolic" : "audio-input-microphone-symbolic"
  )

  return (
    <box class="audio-channel-row input-channel" orientation={Gtk.Orientation.VERTICAL} spacing={4}>
      <box spacing={6}>
        <label class="audio-section-kicker" xalign={0} hexpand label="INPUT" />
        <label class="audio-mic-in-use" visible={inUse} label="• IN USE" />
      </box>
      <label class="audio-device-label" xalign={0} ellipsize={3} maxWidthChars={30} label={description} />

      <box class="audio-control-line" spacing={8}>
        <button
          class={createBinding(microphone, "mute")((muted) => muted ? "audio-channel-icon mic muted" : "audio-channel-icon mic")}
          tooltipText={createBinding(microphone, "mute")((muted) => muted ? "Unmute microphone" : "Mute microphone")}
          valign={Gtk.Align.CENTER}
          onClicked={() => microphone.set_mute(!microphone.mute)}
        >
          <image pixelSize={19} iconName={micIcon} />
        </button>

        <slider
          class="audio-thick-slider input-slider"
          hexpand
          valign={Gtk.Align.CENTER}
          value={volume}
          onChangeValue={({ value }) => microphone.set_volume(value)}
        />

        <label class="audio-channel-percent" valign={Gtk.Align.CENTER} label={percent} />
      </box>
    </box>
  )
}

export function AudioPanel() {
  const wp = AstalWp.get_default()!
  const audio = wp.audio
  const defaultSpeaker = createBinding(audio, "defaultSpeaker")
  const defaultMicrophone = createBinding(audio, "defaultMicrophone")
  const recorders = createBinding(audio, "recorders")

  return (
    <box class="control-panel audio-panel" orientation={Gtk.Orientation.VERTICAL} spacing={6}>
      <For each={defaultSpeaker((speaker) => speaker ? [speaker] : [])}>
        {(speaker) => <OutputRow speaker={speaker} />}
      </For>

      <For each={defaultMicrophone((microphone) => microphone ? [microphone] : [])}>
        {(microphone) => (
          <InputRow
            microphone={microphone}
            inUse={recorders((items) => items.length > 0)}
          />
        )}
      </For>

      <button class="audio-mixer-action" onClicked={() => runAudioMixer()}>
        <box spacing={7}>
          <image pixelSize={14} iconName="multimedia-volume-control-symbolic" />
          <label xalign={0} hexpand label="Mixer" />
          <label class="audio-mixer-chevron" label="›" />
        </box>
      </button>
    </box>
  )
}

export default function AudioControl() {
  const wp = AstalWp.get_default()!
  const speaker = wp.defaultSpeaker
  return (
    <PanelTrigger
      panel="audio"
      class="control-leader audio-leader"
      child={<image iconName={createBinding(speaker, "volumeIcon")} />}
    />
  )
}
