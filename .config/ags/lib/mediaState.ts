import AstalMpris from "gi://AstalMpris"
import { createBinding } from "ags"
import { createPoll } from "ags/time"

export type MediaPlayback = "playing" | "paused" | "stopped"

export type MediaSnapshot = {
  primary: any | null
  ordered: any[]
  signature: string
}

const mpris = AstalMpris.get_default()
const mprisPlayers = createBinding(mpris, "players")
const lastPlaybackState = new Map<string, MediaPlayback>()
const lastPlayedRank = new Map<string, number>()
let playbackRank = 0

function playerKey(player: any) {
  try {
    return String(player.busName || player.get_bus_name?.() || player.entry || player.identity || "media")
  } catch {
    return String(player.entry || player.identity || "media")
  }
}

export function normalizePlaybackStatus(status: any): MediaPlayback {
  if (status === AstalMpris.PlaybackStatus.PLAYING) return "playing"
  if (status === AstalMpris.PlaybackStatus.PAUSED) return "paused"
  const value = String(status ?? "").toLowerCase()
  if (value.includes("play")) return "playing"
  if (value.includes("pause")) return "paused"
  return "stopped"
}

function playbackOf(player: any) {
  return normalizePlaybackStatus(player?.playbackStatus)
}

function playerIsAvailable(player: any) {
  try {
    return player?.available !== false
  } catch {
    return true
  }
}

function recencyOf(player: any) {
  return lastPlayedRank.get(playerKey(player)) ?? 0
}

export function sortByRecency(players: any[]) {
  return [...players].sort((a, b) => recencyOf(b) - recencyOf(a))
}

export function selectPrimaryPlayer(players: any[]) {
  const visible = players.filter((player) => playerIsAvailable(player) && playbackOf(player) !== "stopped")
  const playing = visible.filter((player) => playbackOf(player) === "playing")
  if (playing.length > 0) return sortByRecency(playing)[0]

  const paused = visible.filter((player) => playbackOf(player) === "paused")
  return sortByRecency(paused)[0] ?? null
}

function snapshotPlayers(players: any[], previousSnapshot: MediaSnapshot): MediaSnapshot {
  const presentKeys = new Set<string>()

  for (const player of players) {
    const key = playerKey(player)
    presentKeys.add(key)
    const playback = playbackOf(player)
    const previous = lastPlaybackState.get(key)
    if (playback === "playing" && previous !== "playing") {
      lastPlayedRank.set(key, ++playbackRank)
    } else if (!lastPlayedRank.has(key)) {
      lastPlayedRank.set(key, 0)
    }
    lastPlaybackState.set(key, playback)
  }

  for (const key of [...lastPlaybackState.keys()]) {
    if (!presentKeys.has(key)) {
      lastPlaybackState.delete(key)
      lastPlayedRank.delete(key)
    }
  }

  const visible = players.filter((player) => playerIsAvailable(player) && playbackOf(player) !== "stopped")
  const primary = selectPrimaryPlayer(visible)
  const playing = sortByRecency(visible.filter((player) => player !== primary && playbackOf(player) === "playing"))
  const paused = sortByRecency(visible.filter((player) => player !== primary && playbackOf(player) === "paused"))
  const ordered = primary ? [primary, ...playing, ...paused] : [...playing, ...paused]
  const signature = ordered
    .map((player) => `${playerKey(player)}:${playbackOf(player)}:${recencyOf(player)}`)
    .join("|")

  const sameObjects = ordered.length === previousSnapshot.ordered.length
    && ordered.every((player, index) => previousSnapshot.ordered[index] === player)

  if (signature === previousSnapshot.signature && sameObjects && previousSnapshot.primary === primary) {
    return previousSnapshot
  }

  return { primary, ordered, signature }
}

const EMPTY_SNAPSHOT: MediaSnapshot = { primary: null, ordered: [], signature: "" }

export const mediaSnapshot = createPoll<MediaSnapshot>(EMPTY_SNAPSHOT, 250, (previous) => {
  let players: any[] = []
  try {
    players = mprisPlayers() ?? []
  } catch (error) {
    console.error("Adaptive Glass: could not read MPRIS players", error)
  }
  return snapshotPlayers(players, previous)
})

export function additionalPlayingPlayers(snapshot: MediaSnapshot) {
  return snapshot.ordered.filter((player) =>
    player !== snapshot.primary && playbackOf(player) === "playing"
  )
}

export const primaryPlayer = mediaSnapshot((snapshot) => snapshot.primary)
export const orderedPlayers = mediaSnapshot((snapshot) => snapshot.ordered)
export const mediaPopupAvailable = mediaSnapshot((snapshot) => additionalPlayingPlayers(snapshot).length > 0)
