use std::path::Path;
use std::process::Command;

pub fn apply_wallpaper(
    image_path: &Path,
    theme_ctl_path: &Path,
    regen: bool,
) -> Result<(), String> {
    if !image_path.exists() {
        return Err(format!("Image does not exist: {}", image_path.display()));
    }

    if theme_ctl_path.exists() {
        let mut cmd = Command::new(theme_ctl_path);
        cmd.arg("set");
        if !regen {
            cmd.arg("--no-regen");
        }
        cmd.arg(image_path);

        let output = cmd
            .output()
            .map_err(|e| format!("Failed to execute theme_ctl.sh: {e}"))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(format!("theme_ctl.sh failed: {stderr}"));
        }
    } else {
        // Fallback directly to awww img
        let mut cmd = Command::new("awww");
        cmd.arg("img").arg(image_path);
        let output = cmd
            .output()
            .map_err(|e| format!("Failed to run awww: {e}"))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(format!("awww img failed: {stderr}"));
        }
    }

    Ok(())
}

pub fn notify_wallpaper(name: &str) {
    let _ = Command::new("notify-send")
        .args([
            "-a",
            "dusky-wallpaper",
            "-i",
            "preferences-desktop-wallpaper",
            "-t",
            "1800",
            "Wallpaper Applied",
            name,
        ])
        .spawn();
}
