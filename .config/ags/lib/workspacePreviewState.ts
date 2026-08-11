import GLib from "gi://GLib"
import Hyprland from "gi://AstalHyprland"
import AstalApps from "gi://AstalApps"
import { createComputed, createState } from "ags"
import { execAsync } from "ags/process"
import { closePanels } from "./popupState"
import { workspacePreviewEnabled } from "./featureState"
import { clearWorkspaceInteraction } from "./workspaceInteractionState"
import { focusWindow, focusWorkspace } from "./dusky"

export type PreviewClient = {
  address: string
  className: string
  title: string
  iconName: string
  width: number
  height: number
  focusHistory: number
}

export const PREVIEW_PAGE_SIZE = 8
const hyprland = Hyprland.get_default()
const apps = new AstalApps.Apps()
const runtimeRoot = `${GLib.get_user_runtime_dir()}/dusky-adaptive-glass/previews`
const captureScript = `${GLib.get_user_config_dir()}/ags/scripts/capture_window_preview.sh`

export const [previewWorkspaceLocalId, setPreviewWorkspaceLocalId] = createState(1)
export const [previewClients, setPreviewClients] = createState<PreviewClient[]>([])
export const [previewPage, setPreviewPage] = createState(0)
export const previewPageCount = createComputed(() =>
  Math.max(1, Math.ceil(previewClients().length / PREVIEW_PAGE_SIZE)),
)
export const previewPageClients = createComputed(() => {
  const items = previewClients()
  const page = Math.min(previewPage(), Math.max(0, Math.ceil(items.length / PREVIEW_PAGE_SIZE) - 1))
  const start = page * PREVIEW_PAGE_SIZE
  return items.slice(start, start + PREVIEW_PAGE_SIZE)
})
export const [selectedAddress, setSelectedAddress] = createState<string | null>(null)
export const [selectedClient, setSelectedClient] = createState<PreviewClient | null>(null)
export const [previewPath, setPreviewPath] = createState<string | null>(null)
export const [capturing, setCapturing] = createState(false)
export const [captureError, setCaptureError] = createState<string | null>(null)

let captureGeneration = 0
let lastPreviewPath: string | null = null

type WorkspacePreviewWindow = {
  set_default_size: (width: number, height: number) => void
  queue_resize?: () => void
}

let workspacePreviewWindow: WorkspacePreviewWindow | null = null
let popupSizeSource = 0

export function setWorkspacePreviewWindow(window: WorkspacePreviewWindow | null) {
  workspacePreviewWindow = window
  if (!window && popupSizeSource) {
    GLib.Source.remove(popupSizeSource)
    popupSizeSource = 0
  }
}

export function workspacePopupSizeForCount(count: number) {
  if (count <= 0) return null
  if (count === 1) return { width: 308, height: 230 }
  if (count <= 4) return { width: 356, height: 320 }
  return { width: 356, height: 365 }
}

export function requestWorkspacePopupSize(count: number) {
  if (popupSizeSource) {
    GLib.Source.remove(popupSizeSource)
    popupSizeSource = 0
  }

  const size = workspacePopupSizeForCount(count)
  if (!size) return

  popupSizeSource = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
    popupSizeSource = 0
    workspacePreviewWindow?.set_default_size(size.width, size.height)
    workspacePreviewWindow?.queue_resize?.()
    return GLib.SOURCE_REMOVE
  })
}

function targetWorkspace(localId: number) {
  const monitors = Array.from(hyprland.get_monitors?.() ?? hyprland.monitors ?? []) as any[]
  monitors.sort((a, b) => a.x - b.x || a.y - b.y)
  const focusedMonitor = hyprland.get_focused_monitor?.() ?? hyprland.focusedMonitor
  const monitorIndex = Math.max(0, monitors.findIndex((monitor) => monitor.id === focusedMonitor?.id))
  return monitorIndex * 10 + localId
}

function resolveApp(className: string) {
  const exact = apps.exact_query(className)?.[0]
  if (exact) return exact
  return apps.fuzzy_query(className)?.[0]
}

function snapshotClients(localId: number): PreviewClient[] {
  const targetId = targetWorkspace(localId)
  const allClients = Array.from(hyprland.get_clients?.() ?? hyprland.clients ?? []) as any[]

  return allClients
    .filter((client) => client.mapped !== false && !client.hidden && client.workspace?.id === targetId)
    .sort((a, b) => (a.focusHistoryId ?? a.focus_history_id ?? 9999) - (b.focusHistoryId ?? b.focus_history_id ?? 9999))
    .map((client) => {
      const rawClass = client.class || client.initialClass || client.initial_class || "Application"
      const app = resolveApp(rawClass)
      return {
        address: String(client.address || "").replace(/^0x/, ""),
        className: app?.name || rawClass,
        title: client.title || client.initialTitle || client.initial_title || "Untitled window",
        iconName: app?.iconName || app?.icon_name || "application-x-executable-symbolic",
        width: Math.max(1, Number(client.width || 900)),
        height: Math.max(1, Number(client.height || 600)),
        focusHistory: Number(client.focusHistoryId ?? client.focus_history_id ?? 9999),
      }
    })
}

function removeOldPreview(nextPath: string | null) {
  if (!lastPreviewPath || lastPreviewPath === nextPath) return
  try {
    if (GLib.file_test(lastPreviewPath, GLib.FileTest.EXISTS)) GLib.unlink(lastPreviewPath)
  } catch (_) {
    // Runtime thumbnails are best-effort cache files.
  }
}

async function captureClient(client: PreviewClient) {
  const generation = ++captureGeneration
  setCapturing(true)
  setCaptureError(null)
  // Never leave the previous app's screenshot visible after row selection.
  // The selected app fallback is shown immediately until its fresh capture lands.
  setPreviewPath(null)
  GLib.mkdir_with_parents(runtimeRoot, 0o700)

  const safeAddress = client.address.replace(/[^a-zA-Z0-9_-]/g, "") || "window"
  const destination = `${runtimeRoot}/preview-${generation}-${safeAddress}.png`

  try {
    await execAsync(["bash", captureScript, client.address, destination])
    if (generation !== captureGeneration) {
      try { GLib.unlink(destination) } catch (_) {}
      return
    }

    if (!GLib.file_test(destination, GLib.FileTest.EXISTS)) throw new Error("capture produced no image")
    removeOldPreview(destination)
    lastPreviewPath = destination
    setPreviewPath(destination)
    setCaptureError(null)
  } catch (error) {
    if (generation !== captureGeneration) return
    removeOldPreview(null)
    lastPreviewPath = null
    setPreviewPath(null)
    setCaptureError("Real preview unavailable")
    console.error(`Adaptive Glass: could not capture window ${client.address}`, error)
  } finally {
    if (generation === captureGeneration) setCapturing(false)
  }
}

function chooseClient(client: PreviewClient | null, recapture = true) {
  setSelectedClient(client)
  setSelectedAddress(client?.address ?? null)
  if (!client) {
    ++captureGeneration
    setCapturing(false)
    setPreviewPath(null)
    setCaptureError(null)
    return
  }
  if (recapture) void captureClient(client)
}

export function openWorkspacePreview(localId: number) {
  if (!workspacePreviewEnabled()) {
    closeWorkspacePreview()
    return 0
  }

  setPreviewWorkspaceLocalId(localId)
  const items = snapshotClients(localId)
  setPreviewClients(items)
  setPreviewPage(0)
  chooseClient(items[0] ?? null, true)
  requestWorkspacePopupSize(items.length)
  return items.length
}

function selectPage(page: number) {
  const items = previewClients.get()
  const maxPage = Math.max(0, Math.ceil(items.length / PREVIEW_PAGE_SIZE) - 1)
  const nextPage = Math.min(maxPage, Math.max(0, page))
  if (previewPage.get() === nextPage) return
  setPreviewPage(nextPage)
  const start = nextPage * PREVIEW_PAGE_SIZE
  chooseClient(items.slice(start, start + PREVIEW_PAGE_SIZE)[0] ?? null, true)
}

export function previousPreviewPage() {
  selectPage(previewPage.get() - 1)
}

export function nextPreviewPage() {
  selectPage(previewPage.get() + 1)
}

export function closeWorkspacePreview() {
  ++captureGeneration
  setCapturing(false)
  setCaptureError(null)
  setPreviewPath(null)
  setPreviewClients([])
  setPreviewPage(0)
  setSelectedAddress(null)
  setSelectedClient(null)
  removeOldPreview(null)
  lastPreviewPath = null
}

export function selectPreviewClient(address: string) {
  if (selectedAddress.get() === address) return
  const client = previewClients.get().find((item) => item.address === address) ?? null
  chooseClient(client, true)
}

export async function activatePreviewClient(localId: number, address: string) {
  closePanels()
  try {
    await focusWorkspace(localId)
    await focusWindow(address)
  } finally {
    clearWorkspaceInteraction()
  }
}
