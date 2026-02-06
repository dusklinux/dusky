# Dusky Update Helper Scripts

These scripts help you manage local changes to tracked files when running dusky system updates.

## Problem

When you run the dusky update, it does a `git reset --hard` to get the latest upstream changes. This can overwrite your local customizations. While the update script stashes your changes, it's not always clear:
- What you've changed locally
- What changed upstream
- Whether you need to merge changes

## Solution

These helper scripts give you visibility and control over the update process.

## Usage

### 1. Before Update: `pre_update_check.sh`

Run this **BEFORE** running the dusky update:

```bash
~/user_scripts/update_dusky/pre_update_check.sh
```

**What it does:**
- Shows all your local changes to tracked files
- Categorizes them (Hyprland configs, scripts, desktop files, etc.)
- Optionally shows detailed diffs
- **Creates a timestamped backup** of all your changes
- Saves a full diff patch for reference

**Output:**
- Backup directory: `~/Documents/dusky_update_backups/YYYYMMDD_HHMMSS/`
- Modified files list
- Full diff patch

### 2. Run Dusky Update

```bash
~/user_scripts/update_dusky/update_dusky.sh
```

The update will proceed as normal, stashing and applying changes.

### 3. After Update: `post_update_merge.sh`

Run this **AFTER** the update completes:

```bash
~/user_scripts/update_dusky/post_update_merge.sh
```

**What it does:**
- Compares your backed-up files with the current versions
- Detects which files changed upstream
- Identifies potential conflicts (both you and upstream modified the same file)
- **Interactive conflict resolution** for each conflicting file

**Options for each conflict:**
1. Keep current version (from update)
2. Restore your backed-up version
3. Open 3-way merge editor (vimdiff)
4. Skip and decide later

## Example Workflow

```bash
# 1. Check what you've changed
~/user_scripts/update_dusky/pre_update_check.sh

# Review the output, see your changes

# 2. Run the update
~/user_scripts/update_dusky/update_dusky.sh

# 3. Merge your changes back
~/user_scripts/update_dusky/post_update_merge.sh

# Review conflicts and choose how to resolve them
```

## File Categories

The scripts categorize your changes into:
- **Hyprland Config** - Files in `~/.config/hypr/`
- **Other Config** - Other files in `~/.config/`
- **User Scripts** - Files in `~/user_scripts/`
- **Desktop Files** - Files in `~/.local/share/applications/`
- **Other** - Everything else

## Backup Location

Backups are stored in:
```
~/Documents/dusky_update_backups/YYYYMMDD_HHMMSS/
```

Each backup contains:
- All modified files (full copies)
- `modified_files.txt` - List of files that were modified
- `full_diff.patch` - Complete diff of all changes
- `metadata.sh` - Timestamp and file count info

## Manual Recovery

You can always manually restore files from the backup:

```bash
# List backups
ls -lt ~/Documents/dusky_update_backups/

# Compare a specific file
diff -u ~/Documents/dusky_update_backups/YYYYMMDD_HHMMSS/path/to/file ~/path/to/file

# Restore a file
cp ~/Documents/dusky_update_backups/YYYYMMDD_HHMMSS/path/to/file ~/path/to/file
```

## Tips

- Run `pre_update_check.sh` every time before updating
- Keep old backups - they're timestamped so you can track history
- Use the detailed diff option to review your changes before updating
- For complex merges, option 3 (vimdiff) gives you full control

## Requirements

- `git` (already required for dusky)
- `diff` (standard on all Linux systems)
- `vimdiff` (optional, for 3-way merge - install with `pacman -S vim`)
