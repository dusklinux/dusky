import GLib from "gi://GLib"
import { createPoll } from "ags/time"

export type NetworkSessionSample = {
  interfaceName: string
  rxRate: number
  txRate: number
  rxTotal: number
  txTotal: number
  sinceEpoch: number
}

type PersistedNetworkSession = NetworkSessionSample & {
  lastRx: number
  lastTx: number
  lastWallMs: number
}

const decoder = new TextDecoder()
const runtimeDir = GLib.get_user_runtime_dir() || "/tmp"
const statePath = `${runtimeDir}/dusky-adaptive-glass-network-session.json`

function readText(path: string): string {
  try {
    const [ok, contents] = GLib.file_get_contents(path)
    if (!ok) return ""
    return decoder.decode(contents).trim()
  } catch (_) {
    return ""
  }
}

function readNumber(path: string): number {
  const value = Number(readText(path))
  return Number.isFinite(value) && value >= 0 ? value : 0
}

function activeDefaultInterface(): string {
  const route = readText("/proc/net/route")
  if (!route) return ""

  for (const line of route.split("\n").slice(1)) {
    const columns = line.trim().split(/\s+/)
    if (columns.length < 4) continue
    const [iface, destination, , flagsHex] = columns
    const flags = Number.parseInt(flagsHex, 16)
    if (iface && iface !== "lo" && destination === "00000000" && Number.isFinite(flags) && (flags & 0x1) === 0x1) {
      return iface
    }
  }

  return ""
}

function interfaceCounters(interfaceName: string): { rx: number; tx: number } | null {
  if (!interfaceName || !/^[A-Za-z0-9_.:-]+$/.test(interfaceName)) return null
  const base = `/sys/class/net/${interfaceName}/statistics`
  const rx = readNumber(`${base}/rx_bytes`)
  const tx = readNumber(`${base}/tx_bytes`)
  return { rx, tx }
}

function currentWallMs(): number {
  return GLib.get_real_time() / 1000
}

function estimatedBootEpoch(): number {
  const nowSeconds = Math.floor(GLib.get_real_time() / 1_000_000)
  const uptimeSeconds = Number(readText("/proc/uptime").split(/\s+/)[0] || "0")
  if (!Number.isFinite(uptimeSeconds) || uptimeSeconds <= 0) return nowSeconds
  return Math.max(0, nowSeconds - Math.floor(uptimeSeconds))
}

function loadPersistedState(): PersistedNetworkSession | null {
  const raw = readText(statePath)
  if (!raw) return null

  try {
    const parsed = JSON.parse(raw) as Partial<PersistedNetworkSession>
    const required = [parsed.rxTotal, parsed.txTotal, parsed.sinceEpoch, parsed.lastRx, parsed.lastTx, parsed.lastWallMs]
    if (!required.every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0)) return null

    return {
      interfaceName: typeof parsed.interfaceName === "string" ? parsed.interfaceName : "",
      rxRate: 0,
      txRate: 0,
      rxTotal: parsed.rxTotal!,
      txTotal: parsed.txTotal!,
      sinceEpoch: parsed.sinceEpoch!,
      lastRx: parsed.lastRx!,
      lastTx: parsed.lastTx!,
      lastWallMs: parsed.lastWallMs!,
    }
  } catch (_) {
    return null
  }
}

function saveState(state: PersistedNetworkSession) {
  try {
    GLib.file_set_contents(statePath, JSON.stringify(state))
  } catch (_) {
    // Usage data is helpful but must never be able to crash the shell.
  }
}

function createInitialState(): PersistedNetworkSession {
  const interfaceName = activeDefaultInterface()
  const counters = interfaceCounters(interfaceName)
  const rx = counters?.rx ?? 0
  const tx = counters?.tx ?? 0

  const initial: PersistedNetworkSession = {
    interfaceName,
    rxRate: 0,
    txRate: 0,
    rxTotal: rx,
    txTotal: tx,
    sinceEpoch: estimatedBootEpoch(),
    lastRx: rx,
    lastTx: tx,
    lastWallMs: currentWallMs(),
  }

  saveState(initial)
  return initial
}

let state = loadPersistedState() ?? createInitialState()

function sampleNetworkSession(): NetworkSessionSample {
  const nowMs = currentWallMs()
  const interfaceName = activeDefaultInterface()
  const counters = interfaceCounters(interfaceName)

  if (!interfaceName || !counters) {
    state = {
      ...state,
      interfaceName: "",
      rxRate: 0,
      txRate: 0,
      lastRx: 0,
      lastTx: 0,
      lastWallMs: nowMs,
    }
    saveState(state)
    return state
  }

  if (
    state.interfaceName !== interfaceName ||
    counters.rx < state.lastRx ||
    counters.tx < state.lastTx
  ) {
    state = {
      ...state,
      interfaceName,
      rxRate: 0,
      txRate: 0,
      lastRx: counters.rx,
      lastTx: counters.tx,
      lastWallMs: nowMs,
    }
    saveState(state)
    return state
  }

  const elapsedSeconds = Math.max((nowMs - state.lastWallMs) / 1000, 0.001)
  const rxDelta = Math.max(0, counters.rx - state.lastRx)
  const txDelta = Math.max(0, counters.tx - state.lastTx)

  state = {
    ...state,
    interfaceName,
    rxRate: rxDelta / elapsedSeconds,
    txRate: txDelta / elapsedSeconds,
    rxTotal: state.rxTotal + rxDelta,
    txTotal: state.txTotal + txDelta,
    lastRx: counters.rx,
    lastTx: counters.tx,
    lastWallMs: nowMs,
  }

  saveState(state)
  return state
}

export function formatBytes(bytes: number): string {
  const value = Math.max(0, Number.isFinite(bytes) ? bytes : 0)
  const units = ["B", "KB", "MB", "GB", "TB"]
  let size = value
  let unit = 0

  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }

  const digits = unit === 0 ? 0 : size >= 100 ? 0 : size >= 10 ? 1 : 2
  return `${size.toFixed(digits)} ${units[unit]}`
}

export function formatRate(bytesPerSecond: number): string {
  return `${formatBytes(bytesPerSecond)}/s`
}

export function formatSince(epochSeconds: number): string {
  try {
    const date = GLib.DateTime.new_from_unix_local(Math.floor(epochSeconds))
    const formatted = date.format("%I:%M %p") ?? ""
    return formatted.replace(/^0/, "")
  } catch (_) {
    return "—"
  }
}

const initialSample = sampleNetworkSession()
export const networkSession = createPoll(initialSample, 1000, sampleNetworkSession)
