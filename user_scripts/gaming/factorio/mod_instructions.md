# Factorio Mod Installation Guide (Manual Method)

## Overview
This guide covers installing mods manually for the jc141 (Linux portable) version of Factorio, including how to patch version incompatibilities.

## Where Mods Live

- **Game data**: Inside the Factorio portable directory (e.g. `/home/new/Downloads/Factorio-jc141`)
- **User data**: `~/.factorio/` (set via `use-system-read-write-data-directories=true` in the launcher)
- **Mods folder**: `~/.factorio/mods/`

A mod must be placed in its own subdirectory named `<mod-name>_<version>` (e.g. `afraid-of-the-dark_3.0.0`).

## Step-by-Step

### 1. Download the Mod

Clone the source repository (bare repo recommended to avoid leaving a working tree):

```bash
git clone --bare <repo-url> <mod-name>.git
# Example:
git clone --bare https://github.com/RedRafe/afraid-of-the-dark.git afraid-of-the-dark.git
```

### 2. Extract into the Mods Folder

Use `git --work-tree` to check out the files into the properly named mod directory:

```bash
cd /path/to/<mod-name>.git
git --work-tree=/home/new/.factorio/mods/<mod-name>_<version> checkout HEAD -- .
```

For the Bright Universe mod:

```bash
cd /home/new/Downloads/Factorio-jc141/afraid-of-the-dark.git
git --work-tree=/home/new/.factorio/mods/afraid-of-the-dark_3.0.0 checkout HEAD -- .
```

### 3. Check Version Compatibility

Open `info.json` inside the mod directory. Look for two fields:

```json
"factorio_version": "2.1",
"dependencies": ["base >= 2.1.0"]
```

If the required version is higher than your Factorio version, you can **downgrade them**:

```json
"factorio_version": "2.0",
"dependencies": ["base >= 2.0.0"]
```

> **Warning**: This only works for mods that don't use version-specific APIs. Pure visual/LUT mods are safe. If the mod uses new API features, it will crash — revert the change if so.

### 4. Enable the Mod

Edit `~/.factorio/mods/mod-list.json`. Add (or find and set) the mod entry:

```json
{"name": "afraid-of-the-dark", "enabled": true}
```

### 5. Launch and Configure

1. Launch Factorio.
2. Go to **Settings → Mod Settings → Startup** (or **Startup mod settings** on the main menu).
3. Configure each planet-specific setting:
   - `aotd_nauvis` → `always_day`
   - `aotd_vulcanus` → `always_day`
   - `aotd_fulgora` → `always_day`
   - `aotd_gleba` → `always_day`
   - `aotd_aquilo` → `always_day`
4. Load or start a save. Accept any mod-change prompts.

## Achievement Safety

- Mods that only alter **visuals** (LUTs, sprites, colors) without changing game mechanics, recipes, technologies, or the day/night cycle **do not disable achievements**.
- Always check the mod description for statements like: *"Brighten players' surroundings, without changing the day/night cycle of planets and their power production stats."*
- When in doubt, the game will show a warning icon next to the save if achievements are disabled.

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `Incompatible Factorio version` | Mod requires newer Factorio | Patch `info.json` (step 3) |
| `Dependency ... is not satisfied` | `base` version mismatch | Patch `info.json` (step 3) |
| Mod not showing in list | Wrong folder name | Must be `<mod-name>_<version>` exactly |
| Mod shows but can't enable | Missing dependencies | Check `info.json` `dependencies` |
| Crash on load after patching | Mod uses APIs from newer version | Revert `info.json`, find older mod version |
