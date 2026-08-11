import GLib from "gi://GLib"
import Gtk from "gi://Gtk?version=4.0"
import { createPoll } from "ags/time"
import PanelTrigger from "./PanelTrigger"
import { openClocks } from "../lib/dusky"

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
  const time = createPoll("", 1000, () => GLib.DateTime.new_now_local().format("%I:%M") ?? "")
  const meridiem = createPoll("", 1000, () => GLib.DateTime.new_now_local().format("%p") ?? "")

  return (
    <PanelTrigger
      panel="calendar"
      class="clock-card"
      child={
        <box class="clock-card-content" spacing={6} valign={Gtk.Align.CENTER}>
          <box class="clock-accent-dot" valign={Gtk.Align.CENTER} />
          <label class="clock-time" label={time} />
          <Gtk.Separator class="clock-divider" orientation={Gtk.Orientation.VERTICAL} />
          <label class="clock-meridiem" label={meridiem} />
        </box>
      }
    />
  )
}
