import GLib from "gi://GLib"
import Gtk from "gi://Gtk?version=4.0"
import { createPoll } from "ags/time"
import PanelTrigger from "./PanelTrigger"
import { openClocks } from "../lib/dusky"

type ClockFrame = {
  previous: string
  current: string
  tick: number
}

type ClockSlot = {
  previous: string
  current: string
  changed: boolean
  tick: number
}

function formatClockTime() {
  return GLib.DateTime.new_now_local().format("%I:%M") ?? ""
}

function clockReelValueClass(char: string) {
  return /^[0-9]$/.test(char) ? `clock-reel-value-${char}` : "clock-reel-value-empty"
}

function clockSlotClass(slot: ClockSlot) {
  return [
    "clock-reel-digit",
    slot.changed ? "changed" : "stable",
    slot.tick % 2 === 0 ? "clock-reel-tick-even" : "clock-reel-tick-odd",
    clockReelValueClass(slot.current),
  ].join(" ")
}

function ClockReelDigit({ slot }: { slot: any }) {
  return (
    <overlay class={slot((value: ClockSlot) => clockSlotClass(value))}>
      <label class="clock-reel-new clock-reel-digit-face" label={slot((value: ClockSlot) => value.current)} />
      <label
        $type="overlay"
        class="clock-reel-old clock-reel-digit-face"
        canTarget={false}
        halign={Gtk.Align.CENTER}
        valign={Gtk.Align.CENTER}
        label={slot((value: ClockSlot) => value.previous)}
      />
    </overlay>
  )
}

export function CalendarPanel() {
  const date = createPoll("", 60000, () => GLib.DateTime.new_now_local().format("%A, %B %d, %Y") ?? "")
  let calendar: Gtk.Calendar

  const goToday = () => {
    calendar.select_day(GLib.DateTime.new_now_local())
  }

  return (
    <box class="calendar-panel" orientation={Gtk.Orientation.VERTICAL} spacing={10}>
      <box class="calendar-date-header" orientation={Gtk.Orientation.VERTICAL}>
        <label class="calendar-date-label" halign={Gtk.Align.CENTER} label={date} />
      </box>

      <Gtk.Calendar
        $={(self) => calendar = self}
        class="calendar-widget"
        showHeading
        showDayNames
        showWeekNumbers={false}
      />

      <box class="calendar-footer" homogeneous spacing={6}>
        <button class="calendar-footer-action today-action" onClicked={goToday}>
          <box class="calendar-footer-action-content" spacing={6} halign={Gtk.Align.CENTER}>
            <box class="calendar-footer-icon-shell" halign={Gtk.Align.CENTER} valign={Gtk.Align.CENTER}>
              <image class="calendar-footer-icon today-icon" pixelSize={14} iconName="x-office-calendar-symbolic" />
            </box>
            <label class="calendar-footer-action-label" label="Today" />
          </box>
        </button>
        <button class="calendar-footer-action clocks-action" onClicked={() => openClocks()}>
          <box class="calendar-footer-action-content" spacing={6} halign={Gtk.Align.CENTER}>
            <box class="calendar-footer-icon-shell" halign={Gtk.Align.CENTER} valign={Gtk.Align.CENTER}>
              <image class="calendar-footer-icon clocks-icon" pixelSize={14} iconName="preferences-system-time-symbolic" />
            </box>
            <label class="calendar-footer-action-label" label="Clocks" />
          </box>
        </button>
      </box>
    </box>
  )
}

export default function ClockCard() {
  const initialTime = formatClockTime()
  let previousTime = initialTime
  let clockReelTick = 0
  const time = createPoll<ClockFrame>({ previous: initialTime, current: initialTime, tick: clockReelTick }, 1000, () => {
    const current = formatClockTime()
    if (current !== previousTime) clockReelTick = (clockReelTick + 1) % 2
    const frame = { previous: previousTime, current, tick: clockReelTick }
    previousTime = current
    return frame
  })
  const meridiem = createPoll("", 1000, () => GLib.DateTime.new_now_local().format("%p") ?? "")
  const timeSlots = Array.from({ length: 5 }, (_, index) => time((frame) => {
    const previous = frame.previous[index] ?? " "
    const current = frame.current[index] ?? " "
    return { previous, current, changed: previous !== current, tick: frame.tick }
  }))

  return (
    <PanelTrigger
      panel="calendar"
      class="clock-card"
      child={
        <overlay class="clock-card-stage">
          <box class="clock-card-content" spacing={6} valign={Gtk.Align.CENTER}>
            <box class="clock-reel" spacing={1} valign={Gtk.Align.CENTER}>
              {timeSlots.map((slot, index) =>
                index === 2
                  ? <label class="clock-reel-separator" label={slot((value: ClockSlot) => value.current)} />
                  : <ClockReelDigit slot={slot} />
              )}
            </box>
            <Gtk.Separator class="clock-divider" orientation={Gtk.Orientation.VERTICAL} />
            <label class="clock-meridiem" label={meridiem} />
          </box>
          <box
            $type="overlay"
            class="clock-edge-glow left"
            canTarget={false}
            halign={Gtk.Align.START}
            valign={Gtk.Align.END}
          />
          <box
            $type="overlay"
            class="clock-edge-glow right"
            canTarget={false}
            halign={Gtk.Align.END}
            valign={Gtk.Align.END}
          />
        </overlay>
      }
    />
  )
}
