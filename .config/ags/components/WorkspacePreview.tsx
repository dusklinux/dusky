import Gio from "gi://Gio"
import Gtk from "gi://Gtk?version=4.0"
import Pango from "gi://Pango"
import { For, createComputed } from "ags"
import { closePanels } from "../lib/popupState"
import { clearWorkspaceInteraction, enterWorkspacePreview, leaveWorkspacePreview } from "../lib/workspaceInteractionState"
import {
  captureError,
  capturing,
  previewClients,
  previewPage,
  previewPageClients,
  previewPageCount,
  previewPath,
  previewWorkspaceLocalId,
  selectedAddress,
  selectedClient,
  selectPreviewClient,
  previousPreviewPage,
  nextPreviewPage,
  activatePreviewClient,
  type PreviewClient,
} from "../lib/workspacePreviewState"

function ClientIcon({ client, size = 22 }: { client: PreviewClient, size?: number }) {
  return client.iconName.startsWith("/")
    ? <image class="workspace-window-icon" pixelSize={size} file={client.iconName} />
    : <image class="workspace-window-icon" pixelSize={size} iconName={client.iconName || "application-x-executable-symbolic"} />
}

function HeroAppIcon() {
  return (
    <box class="workspace-preview-fallback-icon-wrap">
      <image
        class="workspace-preview-fallback-icon"
        pixelSize={30}
        visible={selectedClient((client) => Boolean(client?.iconName?.startsWith("/")))}
        file={selectedClient((client) => client?.iconName?.startsWith("/") ? client.iconName : "")}
      />
      <image
        class="workspace-preview-fallback-icon"
        pixelSize={30}
        visible={selectedClient((client) => Boolean(client && !client.iconName?.startsWith("/")))}
        iconName={selectedClient((client) => client && !client.iconName?.startsWith("/")
          ? (client.iconName || "application-x-executable-symbolic")
          : "application-x-executable-symbolic")}
      />
    </box>
  )
}

function IdentityAppIcon() {
  return (
    <box class="workspace-preview-identity-icon-shell" halign={Gtk.Align.CENTER} valign={Gtk.Align.CENTER}>
      <image
        class="workspace-preview-identity-icon"
        pixelSize={22}
        visible={selectedClient((client) => Boolean(client?.iconName?.startsWith("/")))}
        file={selectedClient((client) => client?.iconName?.startsWith("/") ? client.iconName : "")}
      />
      <image
        class="workspace-preview-identity-icon"
        pixelSize={22}
        visible={selectedClient((client) => Boolean(client && !client.iconName?.startsWith("/")))}
        iconName={selectedClient((client) => client && !client.iconName?.startsWith("/")
          ? (client.iconName || "application-x-executable-symbolic")
          : "application-x-executable-symbolic")}
      />
    </box>
  )
}

function PreviewStage() {
  return (
    <box class="workspace-preview-hero" orientation={Gtk.Orientation.VERTICAL}>
      <box class="workspace-preview-hero-viewport" orientation={Gtk.Orientation.VERTICAL}>
        <Gtk.Picture
          class="workspace-preview-picture"
          widthRequest={previewClients((items) => items.length === 1 ? 276 : 320)}
          heightRequest={previewClients((items) => items.length === 1 ? 138 : 160)}
          hexpand={true}
          vexpand={true}
          halign={Gtk.Align.FILL}
          valign={Gtk.Align.FILL}
          canShrink={true}
          contentFit={Gtk.ContentFit.CONTAIN}
          visible={previewPath((path) => Boolean(path))}
          file={previewPath((path) => path ? Gio.File.new_for_path(path) : null)}
        />

        <box
          class="workspace-preview-fallback"
          orientation={Gtk.Orientation.VERTICAL}
          spacing={8}
          halign={Gtk.Align.CENTER}
          valign={Gtk.Align.CENTER}
          visible={previewPath((path) => !path)}
        >
          <HeroAppIcon />
          <label
            class="workspace-preview-fallback-name"
            label={selectedClient((client) => client?.className ?? "No window selected")}
          />
          <label
            class="workspace-preview-fallback-copy"
            label={capturing((value) => value ? "Refreshing preview…" : "Preview unavailable")}
          />
        </box>
      </box>

      <label
        class="workspace-preview-status error"
        visible={captureError((value) => Boolean(value))}
        label={captureError((value) => value ?? "")}
      />
    </box>
  )
}

function SelectedWindowIdentity() {
  return (
    <box
      class="workspace-preview-identity"
      spacing={8}
      visible={selectedClient((client) => Boolean(client))}
    >
      <IdentityAppIcon />
      <box class="workspace-preview-identity-copy" orientation={Gtk.Orientation.VERTICAL} spacing={1} hexpand>
        <label
          class="workspace-preview-identity-app"
          xalign={0}
          maxWidthChars={40}
          ellipsize={Pango.EllipsizeMode.END}
          label={selectedClient((client) => client?.className ?? "Application")}
        />
        <label
          class="workspace-preview-identity-title"
          xalign={0}
          maxWidthChars={44}
          ellipsize={Pango.EllipsizeMode.END}
          label={selectedClient((client) => client?.title ?? "Untitled window")}
        />
      </box>
    </box>
  )
}

function WindowTile({ client }: { client: PreviewClient }) {
  return (
    <button
      class={selectedAddress((address) => address === client.address ? "workspace-window-tile selected" : "workspace-window-tile")}
      onClicked={() => void activatePreviewClient(previewWorkspaceLocalId.get(), client.address)}
    >
      <Gtk.EventControllerMotion onEnter={() => selectPreviewClient(client.address)} />
      <box class="workspace-window-tile-icon-shell" halign={Gtk.Align.CENTER} valign={Gtk.Align.CENTER}>
        <ClientIcon client={client} size={24} />
      </box>
    </button>
  )
}

export default function WorkspacePreview() {
  const pageLabel = createComputed(() => `${previewPage() + 1} / ${previewPageCount()}`)
  const canGoNext = createComputed(() => previewPage() + 1 < previewPageCount())

  return (
    <box
      class={previewClients((items) => items.length === 0
        ? "workspace-navigator empty-state"
        : items.length === 1
          ? "workspace-navigator single-state"
          : "workspace-navigator multi-state")}
      orientation={Gtk.Orientation.VERTICAL}
      spacing={6}
    >
      <Gtk.EventControllerMotion
        onEnter={() => enterWorkspacePreview()}
        onLeave={() => leaveWorkspacePreview()}
      />
      <box class="workspace-preview-header" spacing={10}>
        <box
          class="workspace-preview-header-icon"
          halign={Gtk.Align.CENTER}
          valign={Gtk.Align.CENTER}
        >
          <box
            class="workspace-preview-grid-glyph"
            orientation={Gtk.Orientation.VERTICAL}
            spacing={2}
            halign={Gtk.Align.CENTER}
            valign={Gtk.Align.CENTER}
          >
            <box spacing={2} halign={Gtk.Align.CENTER}>
              <box class="workspace-preview-grid-cell" />
              <box class="workspace-preview-grid-cell" />
            </box>
            <box spacing={2} halign={Gtk.Align.CENTER}>
              <box class="workspace-preview-grid-cell" />
              <box class="workspace-preview-grid-cell" />
            </box>
          </box>
        </box>
        <box class="workspace-preview-header-copy" spacing={8} hexpand valign={Gtk.Align.CENTER}>
          <label
            class="workspace-preview-header-title"
            xalign={0}
            label={previewWorkspaceLocalId((id) => `Workspace ${id}`)}
          />
          <label
            class="workspace-preview-header-subtitle"
            xalign={0}
            label={previewClients((items) => items.length === 1 ? "1 window" : `${items.length} windows`)}
          />
        </box>
        <button class="workspace-preview-close" valign={Gtk.Align.CENTER} onClicked={() => { closePanels(); clearWorkspaceInteraction() }}>
          <label label="󰅖" />
        </button>
      </box>

      <box
        class={previewClients((items) => items.length === 1
          ? "workspace-preview-content single-window"
          : "workspace-preview-content multi-window")}
        orientation={Gtk.Orientation.VERTICAL}
        spacing={5}
        visible={previewClients((items) => items.length > 0)}
      >
        <PreviewStage />
        <SelectedWindowIdentity />

        <box
          class="workspace-window-list-wrap"
          orientation={Gtk.Orientation.VERTICAL}
          spacing={4}
          visible={previewClients((items) => items.length > 1)}
        >
          <box class="workspace-window-list-heading" spacing={8}>
            <label class="workspace-window-list-title" xalign={0} hexpand label="WINDOWS" />
            <box
              class="workspace-window-pager"
              spacing={4}
              valign={Gtk.Align.CENTER}
              visible={previewPageCount((count) => count > 1)}
            >
              <button
                class="workspace-window-page-button"
                sensitive={previewPage((page) => page > 0)}
                onClicked={previousPreviewPage}
              >
                <label label="‹" />
              </button>
              <label class="workspace-window-page-indicator" label={pageLabel} />
              <button
                class="workspace-window-page-button"
                sensitive={canGoNext}
                onClicked={nextPreviewPage}
              >
                <label label="›" />
              </button>
            </box>
          </box>
          <box class="workspace-window-grid" orientation={Gtk.Orientation.VERTICAL} spacing={5}>
            <box
              class="workspace-window-grid-row"
              spacing={5}
              homogeneous={true}
              hexpand={true}
              halign={Gtk.Align.FILL}
            >
              <For each={previewPageClients((items) => items.slice(0, 4))}>
                {(client) => <WindowTile client={client} />}
              </For>
            </box>
            <box
              class="workspace-window-grid-row"
              spacing={5}
              homogeneous={true}
              hexpand={true}
              halign={Gtk.Align.FILL}
              visible={previewPageClients((items) => items.length > 4)}
            >
              <For each={previewPageClients((items) => items.slice(4, 8))}>
                {(client) => <WindowTile client={client} />}
              </For>
            </box>
          </box>
        </box>
      </box>
    </box>
  )
}
