//! Select the discovered compositor GPU before any executor threads start.
//! Explicit user GPU/offload preferences take precedence.
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
    // 1. Check AQ_DRM_DEVICES (set by Hyprland via gpu.lua, ordered with primary card first)
    if let Some(aq_devices) = aq_devices
        && let Some(first) = aq_devices.split(':').next()
    {
        let p = std::path::Path::new(first.trim());
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

/// Called on the main thread before any executors are created.
pub fn optimize_environment() {
    AUTOMATIC_ENV.get_or_init(configure_environment);
}

/// Launchers and finite commands inherit the session's GPU preferences, not
/// automatic graphics tuning applied only for Snap's own compositor.
pub fn inherit_session_environment(command: &mut Command) {
    if let Some(keys) = AUTOMATIC_ENV.get() {
        for key in keys {
            command.env_remove(key);
        }
    }
}

fn configure_environment() -> Vec<&'static str> {
    let mut keys = Vec::with_capacity(4);
    // Iced discovers the enabled WGPU backends unless explicitly overridden.
    // Filter driver discovery before starting any threads.
    let power_overridden = env::var_os("WGPU_POWER_PREF").is_some();
    if !power_overridden {
        unsafe { env::set_var("WGPU_POWER_PREF", "low") };
        keys.push("WGPU_POWER_PREF");
    }
    // Respect explicit GPU/offload selection, including the legacy Vulkan API.
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

    // 2. Determine matching Vulkan ICD candidates based on the actual primary GPU
    let candidates = driver_candidates(&vendor, &driver);
    if candidates.is_empty() {
        return keys;
    }

    // Mesa selects this PCI GPU for EGL and puts it first for Vulkan.
    // This does not prevent enumeration/initialization of same-driver GPUs.
    // Leave unknown/non-PCI devices to their driver defaults.
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
        if env::var_os("__EGL_VENDOR_LIBRARY_FILENAMES").is_none()
            && std::path::Path::new(mesa_egl).is_file()
        {
            unsafe { env::set_var("__EGL_VENDOR_LIBRARY_FILENAMES", mesa_egl) };
            keys.push("__EGL_VENDOR_LIBRARY_FILENAMES");
        }
    }

    // Include all installed matching ICDs: ANV and HASVK cover different Intel
    // generations. Driver filtering is not physical-device filtering.
    // Unrecognized filenames retain normal discovery, including custom ICDs.
    let drivers: Vec<_> = candidates
        .iter()
        .copied()
        .filter(|candidate| std::path::Path::new(candidate).is_file())
        .collect();
    if !drivers.is_empty() {
        unsafe { env::set_var("VK_DRIVER_FILES", drivers.join(":")) };
        keys.push("VK_DRIVER_FILES");
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
            if driver == "nouveau" {
                &[
                    "/usr/share/vulkan/icd.d/nouveau_icd.x86_64.json",
                    "/usr/share/vulkan/icd.d/nouveau_icd.json",
                ]
            } else if driver == "nvidia" {
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
        // Other unknown/virtual drivers retain normal backend discovery.
        _ => &[],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gpu_discovery_respects_compositor_order_and_rejects_ambiguity() {
        let root = std::env::temp_dir().join(format!("dusky-snap-gpu-{}", std::process::id()));
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
    fn installed_driver_candidates_cover_vendor_and_nvidia_driver_variants() {
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

#[cfg(test)]
mod ambiguity_tests {
    use super::*;
    #[test]
    fn ambiguous_multiple_gpus_do_not_filter_from_firmware_hint() {
        let root =
            std::env::temp_dir().join(format!("dusky-snap-gpu-ambiguous-{}", std::process::id()));
        for (card, vendor) in [("card0", "0x8086"), ("card1", "0x1002")] {
            let device = root.join(card).join("device");
            std::fs::create_dir_all(&device).unwrap();
            std::fs::write(device.join("vendor"), vendor).unwrap();
        }
        std::fs::write(root.join("card0/device/boot_vga"), "1").unwrap();
        let choice = detect_primary_gpu_vendor(&root, None);
        std::fs::remove_dir_all(&root).unwrap();
        assert!(
            choice.is_none(),
            "firmware GPU order does not establish Hyprland's chosen GPU"
        );
    }
}
