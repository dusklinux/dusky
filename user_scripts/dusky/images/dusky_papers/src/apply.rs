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
        let mut cmd = Command::new("timeout");
        cmd.args(["-k", "2s", "120s"])
            .arg(theme_ctl_path)
            .arg("set");
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
    } else if !regen {
        // Wallpaper-only mode can still work without the theme controller.
        let mut cmd = Command::new("timeout");
        cmd.args(["-k", "2s", "15s", "awww", "img"]).arg(image_path);
        let output = cmd
            .output()
            .map_err(|e| format!("Failed to run awww: {e}"))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(format!("awww img failed: {stderr}"));
        }
    } else {
        return Err(format!(
            "Theme controller is missing: {}",
            theme_ctl_path.display()
        ));
    }

    Ok(())
}
