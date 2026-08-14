import GLib from "gi://GLib"
import Gtk from "gi://Gtk?version=4.0"
import { createPoll } from "ags/time"
import PanelTrigger from "./PanelTrigger"
import { clock24hEnabled } from "../lib/clockState"
import { openClocks } from "../lib/dusky"

type ClockFrame = {
  previous: string
  current: string
  tick: number
  hourChanged: boolean
}

type ClockSlot = {
  previous: string
  current: string
  changed: boolean
  tick: number
}

function formatClockTime(use24h: boolean, time = GLib.DateTime.new_now_local()) {
  return time.format(use24h ? "%H:%M" : "%I:%M") ?? ""
}

function hourKey(time = GLib.DateTime.new_now_local()) {
  return time.format("%H") ?? ""
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
  const initialNow = GLib.DateTime.new_now_local()
  const initialTime = formatClockTime(clock24hEnabled(), initialNow)
  let previousTime = initialTime
  let previousHour = hourKey(initialNow)
  let clockReelTick = 0
  const time = createPoll<ClockFrame>({ previous: initialTime, current: initialTime, tick: clockReelTick, hourChanged: false }, 1000, () => {
    const now = GLib.DateTime.new_now_local()
    const current = formatClockTime(clock24hEnabled(), now)
    const currentHour = hourKey(now)
    const hourChanged = currentHour !== previousHour
    if (current !== previousTime) clockReelTick = (clockReelTick + 1) % 2
    const frame = { previous: previousTime, current, tick: clockReelTick, hourChanged }
    previousTime = current
    previousHour = currentHour
    return frame
  })
  const meridiem = createPoll("", 1000, () => GLib.DateTime.new_now_local().format("%p") ?? "")
  const meridiemVisible = clock24hEnabled((enabled) => !enabled)
  const clockCardClass = clock24hEnabled((enabled) => `clock-card ${enabled ? "clock-mode-24h" : "clock-mode-12h"}`)
  const hourLineClass = time((frame) => frame.hourChanged ? "clock-hour-line hour-changed" : "clock-hour-line")
  const timeSlots = Array.from({ length: 5 }, (_, index) => time((frame) => {
    const previous = frame.previous[index] ?? " "
    const current = frame.current[index] ?? " "
    return { previous, current, changed: previous !== current, tick: frame.tick }
  }))

  return (
    <PanelTrigger
      panel="calendar"
      class={clockCardClass}
      child={
        <overlay class="clock-card-stage">
          <box class="clock-card-content" spacing={0} halign={Gtk.Align.CENTER} valign={Gtk.Align.CENTER}>
            <box class="clock-reel" spacing={1} valign={Gtk.Align.CENTER}>
              {timeSlots.map((slot, index) =>
                index === 2
                  ? <label class="clock-reel-separator" label={slot((value: ClockSlot) => value.current)} />
                  : <ClockReelDigit slot={slot} />
              )}
            </box>
          </box>
          <label
            $type="overlay"
            class="clock-meridiem"
            visible={meridiemVisible}
            canTarget={false}
            halign={Gtk.Align.END}
            valign={Gtk.Align.CENTER}
            label={meridiem}
          />
          <box
            $type="overlay"
            class={hourLineClass}
            canTarget={false}
            halign={Gtk.Align.CENTER}
            valign={Gtk.Align.START}
          />
        </overlay>
      }
    />
  )
}
