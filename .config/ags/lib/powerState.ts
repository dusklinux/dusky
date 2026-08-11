import { createPoll } from "ags/time"
import { toggleIdle } from "./dusky"

// Caffeine is the inverse of Hypridle: when Hypridle is absent the machine is
// intentionally being kept awake. Polling keeps the UI honest even if the
// process is changed outside Adaptive Glass.
export const caffeineState = createPoll(
  "off",
  1200,
  [
    "bash",
    "-lc",
    "if pgrep -x hypridle >/dev/null 2>&1; then printf off; else printf on; fi",
  ],
)

export function toggleCaffeine() {
  return toggleIdle()
}
