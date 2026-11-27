//! Select the discovered compositor GPU before any worker, D-Bus, or IPC threads start.
//! Explicit user GPU/offload preferences always take precedence.

use std::{env, path::Path, process::Command, sync::OnceLock};

static AUTOMATIC_ENV: OnceLock<Vec<&'static str>> = OnceLock::new();

fn read_card_vendor_driver(drm: &Path, card_name: &str) -> Option<(String, String, String)> {
    let sys_base = drm.join(card_name).join("device");
    let vendor_path = sys_base.join("vendor");
    let vendor = std::fs::read_to_string(&vendor_path)
        .ok()?
        .trim()
        .to_ascii_lowercase();

    let driver = std::fs::read_link(sys_base.join("driver"))
        .ok()
        .and_then(|p| p.file_name().map(|f| f.to_string_lossy().to_string()))
        .unwrap_or_default()
        .to_ascii_lowercase();

    let pci_device = std::fs::canonicalize(&sys_base)
        .ok()?
        .file_name()?
        .to_string_lossy()
        .into_owned();

    Some((vendor, driver, pci_device))
}

fn detect_primary_gpu_vendor(
    drm: &Path,
    aq_devices: Option<&str>,
) -> Option<(String, String, String)> {
    // 1. Check AQ_DRM_DEVICES (set by Hyprland via gpu.lua, ordered with primary card first).
    if let Some(aq_devices) = aq_devices
        && let Some(first) = aq_devices.split(':').next()
    {
        let p = Path::new(first.trim());
        if let Ok(real) = std::fs::canonicalize(p)
            && let Some(name) = real.file_name().and_then(|s| s.to_str())
            && is_card_name(name)
            && let Some(pair) = read_card_vendor_driver(drm, name)
        {
            return Some(pair);
        }
    }

    // Without a valid compositor hint, filter only a single-card system.
    // Firmware boot_vga and directory order cannot identify Hyprland's GPU.
    let mut cards = std::fs::read_dir(drm)
        .ok()?
        .filter_map(Result::ok)
        .map(|entry| entry.file_name())
        .filter(|name| name.to_str().is_some_and(is_card_name));
    let card = cards.next()?;
    if cards.next().is_some() {
        return None;
    }
    read_card_vendor_driver(drm, card.to_str()?)
}

fn is_card_name(name: &str) -> bool {
    name.strip_prefix("card")
        .is_some_and(|index| !index.is_empty() && index.bytes().all(|b| b.is_ascii_digit()))
}

/// Must be called on the main thread strictly before spawning IPC, D-Bus, or worker threads.
pub fn optimize_environment() {
    AUTOMATIC_ENV.get_or_init(configure_environment);
}

/// Child commands (`hyprctl`, `pw-dump`, `wpctl`) inherit the session's GPU preferences,
/// not automatic graphics tuning applied solely for Dusky Bluetooth's own compositor.
pub fn inherit_session_environment(command: &mut Command) {
    if let Some(keys) = AUTOMATIC_ENV.get() {
        for key in keys {
            command.env_remove(key);
        }
    }
}

fn configure_environment() -> Vec<&'static str> {
    let mut keys = Vec::with_capacity(5);
    let backend_overridden = env::var_os("WGPU_BACKEND").is_some();
    let power_overridden = env::var_os("WGPU_POWER_PREF").is_some();

    if !power_overridden {
        unsafe { env::set_var("WGPU_POWER_PREF", "low") };
        keys.push("WGPU_POWER_PREF");
    }

    // Respect explicit GPU/offload selection, including legacy Vulkan variables.
    if power_overridden
        || [
            "WGPU_ADAPTER_NAME",
            "VK_DRIVER_FILES",
            "VK_ICD_FILENAMES",
            "VK_ADD_DRIVER_FILES",
            "VK_LOADER_DRIVERS_SELECT",
            "VK_LOADER_DRIVERS_DISABLE",
            "VK_LOADER_DEVICE_SELECT",
            "DRI_PRIME",
            "MESA_VK_DEVICE_SELECT",
            "MESA_LOADER_DRIVER_OVERRIDE",
            "__NV_PRIME_RENDER_OFFLOAD",
            "__NV_PRIME_RENDER_OFFLOAD_PROVIDER",
            "__EGL_VENDOR_LIBRARY_FILENAMES",
            "__EGL_VENDOR_LIBRARY_DIRS",
        ]
        .iter()
        .any(|name| env::var_os(name).is_some())
    {
        return keys;
    }

    let aq_devices = env::var("AQ_DRM_DEVICES").ok();
    let Some((vendor, driver, pci_device)) =
        detect_primary_gpu_vendor(Path::new("/sys/class/drm"), aq_devices.as_deref())
    else {
        return keys;
    };

    let candidates = driver_candidates(&vendor, &driver);
    if candidates.is_empty() {
        return keys;
    }

    // Mesa selects this PCI GPU for EGL and prioritizes it for Vulkan.
    if matches!(vendor.as_str(), "0x8086" | "0x1002" | "0x1af4") {
        if matches!(vendor.as_str(), "0x8086" | "0x1002")
            && pci_device.split([':', '.']).count() == 4
            && pci_device
                .chars()
                .all(|c| c.is_ascii_hexdigit() || c == ':' || c == '.')
        {
            let prime = format!("pci-{}", pci_device.replace([':', '.'], "_"));
            unsafe { env::set_var("DRI_PRIME", prime) };
            keys.push("DRI_PRIME");
        }
        let mesa_egl = "/usr/share/glvnd/egl_vendor.d/50_mesa.json";
        if env::var_os("__EGL_VENDOR_LIBRARY_FILENAMES").is_none() && Path::new(mesa_egl).is_file()
        {
            unsafe { env::set_var("__EGL_VENDOR_LIBRARY_FILENAMES", mesa_egl) };
            keys.push("__EGL_VENDOR_LIBRARY_FILENAMES");
        }
    }

    // Restrict Vulkan loader to installed ICDs matching the primary GPU.
    // If no matching Vulkan ICD is installed, fall back to Wayland EGL directly
    // instead of letting the loader probe unrelated discrete drivers.
    let drivers: Vec<_> = candidates
        .iter()
        .copied()
        .filter(|candidate| Path::new(candidate).is_file())
        .collect();
    if !drivers.is_empty() {
        unsafe { env::set_var("VK_DRIVER_FILES", drivers.join(":")) };
        keys.push("VK_DRIVER_FILES");
    } else if !backend_overridden {
        unsafe { env::set_var("WGPU_BACKEND", "gl") };
        keys.push("WGPU_BACKEND");
    }

    keys
}

fn driver_candidates(vendor: &str, driver: &str) -> &'static [&'static str] {
    match vendor {
        // Intel (Iris Xe, UHD, Arc)
        "0x8086" => &[
            "/usr/share/vulkan/icd.d/intel_icd.x86_64.json",
            "/usr/share/vulkan/icd.d/intel_icd.json",
            "/usr/share/vulkan/icd.d/intel_hasvk_icd.x86_64.json",
            "/usr/share/vulkan/icd.d/intel_hasvk_icd.json",
        ],
        // AMD (Radeon, Ryzen iGPU, Radeon dGPU)
        "0x1002" => &[
            "/usr/share/vulkan/icd.d/radeon_icd.x86_64.json",
            "/usr/share/vulkan/icd.d/radeon_icd.json",
        ],
        // NVIDIA (Desktop discrete GPU or single-GPU system)
        "0x10de" => {
            if matches!(driver, "nouveau" | "nova" | "nova_drm" | "nova_core") {
                &[
                    "/usr/share/vulkan/icd.d/nouveau_icd.x86_64.json",
                    "/usr/share/vulkan/icd.d/nouveau_icd.json",
                ]
            } else if matches!(driver, "nvidia" | "nvidia_drm") {
                &[
                    "/usr/share/vulkan/icd.d/nvidia_icd.x86_64.json",
                    "/usr/share/vulkan/icd.d/nvidia_icd.json",
                ]
            } else {
                &[]
            }
        }
        // VirtIO's Venus Vulkan driver is optional; VirGL-only guests use EGL.
        "0x1af4" => &[
            "/usr/share/vulkan/icd.d/virtio_icd.x86_64.json",
            "/usr/share/vulkan/icd.d/virtio_icd.json",
        ],
        // Other unknown/virtual drivers use Vulkan-first compositor fallback.
        _ => &[],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEST_DIR_SEQ: AtomicU64 = AtomicU64::new(0);

    fn unique_test_dir(tag: &str) -> PathBuf {
        let seq = TEST_DIR_SEQ.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir().join(format!(
            "dusky_bluetooth-{tag}-{}-{seq}",
            std::process::id()
        ))
    }

    #[test]
    fn bluetooth_gpu_discovery_respects_compositor_order_and_rejects_ambiguity() {
        let root = unique_test_dir("gpu");
        let _ = std::fs::remove_dir_all(&root);
        for (card, vendor, driver) in [("card2", "0x8086", "i915"), ("card10", "0x10de", "nvidia")]
        {
            let device = root.join(card).join("device");
            std::fs::create_dir_all(&device).unwrap();
            std::fs::write(device.join("vendor"), vendor).unwrap();
            std::os::unix::fs::symlink(root.join(driver), device.join("driver")).unwrap();
        }
        assert!(detect_primary_gpu_vendor(&root, None).is_none());
        std::fs::write(root.join("card10/device/boot_vga"), "1").unwrap();
        assert!(detect_primary_gpu_vendor(&root, None).is_none());

        let ordered = format!(
            "{}:{}",
            root.join("card2").display(),
            root.join("card10").display()
        );
        assert_eq!(
            detect_primary_gpu_vendor(&root, Some(&ordered)).unwrap().0,
            "0x8086"
        );
        assert!(detect_primary_gpu_vendor(&root, Some("/missing")).is_none());

        std::fs::remove_dir_all(root.join("card10")).unwrap();
        assert_eq!(detect_primary_gpu_vendor(&root, None).unwrap().0, "0x8086");

        std::fs::remove_dir_all(&root).unwrap();
        assert!(detect_primary_gpu_vendor(&root, None).is_none());
    }

    #[test]
    fn bluetooth_driver_candidates_cover_vendor_and_nvidia_driver_variants() {
        let intel = driver_candidates("0x8086", "i915");
        assert!(intel.iter().any(|p| p.contains("intel_icd")));
        assert!(intel.iter().any(|p| p.contains("intel_hasvk")));
        assert!(
            driver_candidates("0x1002", "amdgpu")
                .iter()
                .all(|p| p.contains("radeon"))
        );
        assert!(
            driver_candidates("0x10de", "nouveau")
                .iter()
                .all(|p| p.contains("nouveau"))
        );
        assert!(
            driver_candidates("0x10de", "nvidia")
                .iter()
                .all(|p| p.contains("nvidia"))
        );
        assert!(
            driver_candidates("0x1af4", "virtio_gpu")
                .iter()
                .all(|p| p.contains("virtio"))
        );
        assert!(driver_candidates("unknown", "unknown").is_empty());
        assert!(driver_candidates("0x10de", "unrecognized-driver").is_empty());
    }
}
