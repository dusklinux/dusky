import { createPoll } from "ags/time"

function windowText(text: string, width: number, step: number) {
  const clean = (text || "Unknown track").trim()
  if (clean.length <= width) return clean
  const gap = "      "
  const cycle = clean + gap
  const start = step % cycle.length
  return (cycle + cycle).slice(start, start + width)
}

export default function MarqueeText({ text, width = 25 }: { text: () => string, width?: number }) {
  let step = 0
  let previous = ""

  const label = createPoll("", 260, () => {
    const current = text() || "Unknown track"
    if (current !== previous) {
      previous = current
      step = 0
    }
    const output = windowText(current, width, step)
    if (current.length > width) step += 1
    return output
  })

  return <label class="media-title" xalign={0} label={label} />
}
