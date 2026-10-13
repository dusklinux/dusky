#!/usr/bin/env python3
#d: Set up bookmarks for the file manager side pane
"""Append GTK and KDE bookmarks without replacing existing user entries.

Run with a single writer: atomic replacement does not merge concurrent edits
from another setup process or a file manager saving its bookmarks.
"""

import os
import stat
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True, kw_only=True)
class Bookmark:
    path: str
    name: str | None = None
    icon: str = "folder"
    create: bool = True


type ResolvedBookmarks = list[tuple[Path, Bookmark]]

BOOKMARK_NAMESPACE = "http://www.freedesktop.org/standards/desktop-bookmarks"
ET.register_namespace("bookmark", BOOKMARK_NAMESPACE)
ET.register_namespace("mime", "http://www.freedesktop.org/standards/shared-mime-info")


def xdg_directory(variable: str, default: Path) -> Path:
    """Ignore empty or relative XDG values as required by the specification."""
    value = os.environ.get(variable, "")
    return Path(value) if Path(value).is_absolute() else default


def configured_bookmarks(config_home: Path) -> list[Bookmark]:
    # Add or remove entries here. Only create directories owned by this setup;
    # system mount points are supplied by the corresponding mount setup script.
    return [
        Bookmark(path="~/Documents", name="Documents", icon="folder-documents"),
        Bookmark(path="~/Downloads", name="Downloads", icon="folder-downloads"),
        Bookmark(path="~/Pictures", name="Pictures", icon="folder-pictures"),
        Bookmark(path="/mnt/zram1", name="ZRAM Disk", icon="drive-harddisk", create=False),
        Bookmark(path="~/user_scripts", name="Dusky Scripts", icon="text-x-script"),
        Bookmark(path="/mnt", name="Mounts", icon="drive-harddisk", create=False),
        Bookmark(path=str(config_home), name="Config", icon="folder-system"),
    ]


def prepare_bookmarks(bookmarks: list[Bookmark]) -> tuple[ResolvedBookmarks, bool]:
    """Validate directories once for both backends; report required failures."""
    resolved: ResolvedBookmarks = []
    failed = False
    for bookmark in bookmarks:
        try:
            if bookmark.name and any(character in bookmark.name for character in "\r\n\x00"):
                raise ValueError("bookmark labels must be a single line without NUL")
            path = Path(bookmark.path).expanduser()
            if bookmark.create:
                path.mkdir(parents=True, exist_ok=True)
            if not stat.S_ISDIR(path.stat().st_mode):
                raise NotADirectoryError(f"{path} is not a directory")
            resolved.append((path.resolve(strict=True), bookmark))
        except FileNotFoundError as error:
            if bookmark.create:
                print(f"Error preparing {bookmark.path}: {error}", file=sys.stderr)
                failed = True
            else:
                print(f"Skipping unavailable directory: {bookmark.path}")
        except (OSError, ValueError, RuntimeError) as error:
            print(f"Error preparing {bookmark.path}: {error}", file=sys.stderr)
            failed = True
    return resolved, failed


def atomic_write(path: Path, content: str) -> None:
    """Replace through a unique sibling temporary file, preserving file modes."""
    # Follow an existing symlink so that the configured link itself survives.
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = None
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent,
        prefix=f".{path.name}.", delete_on_close=False,
    ) as stream:
        stream.write(content)
        stream.flush()
        if mode is not None:
            os.fchmod(stream.fileno(), mode)
        stream.close()
        Path(stream.name).replace(path)


def update_gtk_bookmarks(bookmarks: ResolvedBookmarks, destination: Path) -> int:
    """Append missing URIs while retaining existing GTK lines and labels."""
    try:
        with destination.open(encoding="utf-8", newline="") as stream:
            existing = stream.read()
    except FileNotFoundError:
        existing = ""
    uris = {line.partition(" ")[0] for line in existing.split("\n") if line}
    additions: list[str] = []
    for path, bookmark in bookmarks:
        uri = path.as_uri()
        if uri in uris:
            continue
        additions.append(f"{uri} {bookmark.name}" if bookmark.name else uri)
        uris.add(uri)
    if additions:
        separator = "\n" if existing and not existing.endswith("\n") else ""
        atomic_write(destination, existing + separator + "\n".join(additions) + "\n")
    return len(additions)


def update_kde_bookmarks(bookmarks: ResolvedBookmarks, destination: Path) -> int:
    """Append valid XBEL entries; malformed existing XML is left untouched."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    try:
        tree = ET.parse(destination, parser=parser)
        root = tree.getroot()
    except FileNotFoundError:
        root = ET.Element("xbel", version="1.0")
    if root.tag != "xbel":
        raise ValueError(f"{destination}: expected an xbel root element")

    uris = {element.get("href") for element in root.iter("bookmark")}
    added = 0
    for path, bookmark in bookmarks:
        uri = path.as_uri()
        if uri in uris:
            continue
        element = ET.SubElement(root, "bookmark", href=uri)
        ET.SubElement(element, "title").text = bookmark.name or path.name or str(path)
        info = ET.SubElement(element, "info")
        metadata = ET.SubElement(info, "metadata", owner="http://freedesktop.org")
        ET.SubElement(metadata, f"{{{BOOKMARK_NAMESPACE}}}icon", name=bookmark.icon)
        metadata = ET.SubElement(info, "metadata", owner="http://www.kde.org")
        ET.SubElement(metadata, "isSystemItem").text = "false"
        uris.add(uri)
        added += 1
    if added:
        # Retain existing metadata text and whitespace rather than reindenting it.
        content = ET.tostring(root, encoding="unicode")
        # ElementTree serialization permits characters forbidden by XML 1.0.
        # Check the complete output before replacing any existing user data.
        ET.fromstring(content)
        atomic_write(
            destination,
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xbel>\n' + content + "\n",
        )
    return added


def main() -> int:
    home = Path.home()
    config_home = xdg_directory("XDG_CONFIG_HOME", home / ".config")
    data_home = xdg_directory("XDG_DATA_HOME", home / ".local" / "share")
    bookmarks, failed = prepare_bookmarks(configured_bookmarks(config_home))
    for name, update, destination in (
        ("GTK (Thunar)", update_gtk_bookmarks, config_home / "gtk-3.0" / "bookmarks"),
        ("KDE (Dolphin)", update_kde_bookmarks, data_home / "user-places.xbel"),
    ):
        try:
            added = update(bookmarks, destination)
        except (OSError, UnicodeError, ValueError, ET.ParseError) as error:
            print(f"{name} error: {error}", file=sys.stderr)
            failed = True
        else:
            print(f"{name}: added {added} entries" if added else f"{name}: already configured")
    if failed:
        print("Bookmark setup finished with errors.", file=sys.stderr)
        return 1
    print("File manager bookmarks configured successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
