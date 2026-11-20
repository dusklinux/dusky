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
    if let Some(aq_devices) = aq_devices {
        for entry in aq_devices.split(':') {
            let p = std::path::Path::new(entry.trim());
            if let Ok(real) = std::fs::canonicalize(p)
                && let Some(name) = real.file_name().and_then(|s| s.to_str())
                && is_card_name(name)
                && let Some(pair) = read_card_vendor_driver(drm, name)
            {
                return Some(pair);
            }
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

fn data_dirs() -> Vec<std::path::PathBuf> {
    let raw = env::var("XDG_DATA_DIRS").unwrap_or_default();
    let dirs: Vec<_> = raw
        .split(':')
        .filter(|s| !s.is_empty())
        .map(std::path::PathBuf::from)
        .collect();
    if dirs.is_empty() {
        vec![
            std::path::PathBuf::from("/usr/local/share"),
            std::path::PathBuf::from("/usr/share"),
        ]
    } else {
        dirs
    }
}

fn find_data_file(rel: &str) -> Option<String> {
    data_dirs()
        .into_iter()
        .map(|d| d.join(rel))
        .find(|p| p.is_file())
        .map(|p| p.to_string_lossy().into_owned())
}

fn configure_environment() -> Vec<&'static str> {
    let mut keys = Vec::with_capacity(4);
    let explicit_override = [
        "WGPU_POWER_PREF",
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
    .any(|name| env::var_os(name).is_some());
    if explicit_override {
        return keys;
    }

    let aq_devices = env::var("AQ_DRM_DEVICES").ok();
    let primary = detect_primary_gpu_vendor(Path::new("/sys/class/drm"), aq_devices.as_deref());
    let mut set_prime = false;

    if let Some((vendor, driver, pci_device)) = primary {
        let candidates = driver_candidates(&vendor, &driver);
        if !candidates.is_empty() {
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
                    set_prime = true;
                }
                let mesa_fallback = "/usr/share/glvnd/egl_vendor.d/50_mesa.json";
                if let Some(mesa_egl) = find_data_file("glvnd/egl_vendor.d/50_mesa.json")
                    .or_else(|| Path::new(mesa_fallback).is_file().then(|| mesa_fallback.into()))
                {
                    unsafe { env::set_var("__EGL_VENDOR_LIBRARY_FILENAMES", mesa_egl) };
                    keys.push("__EGL_VENDOR_LIBRARY_FILENAMES");
                }
            }
            let drivers: Vec<String> = candidates
                .iter()
                .filter_map(|c| {
                    let rel = c.strip_prefix("/usr/share/").unwrap_or(c);
                    find_data_file(rel).or_else(|| Path::new(c).is_file().then(|| (*c).into()))
                })
                .collect();
            if !drivers.is_empty() {
                unsafe { env::set_var("VK_DRIVER_FILES", drivers.join(":")) };
                keys.push("VK_DRIVER_FILES");
            }
        }
    }

    if !set_prime {
        unsafe { env::set_var("WGPU_POWER_PREF", "low") };
        keys.push("WGPU_POWER_PREF");
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
            if matches!(driver, "nouveau" | "nova") {
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
        let fallback = format!("/missing:{}", root.join("card10").display());
        assert_eq!(
            detect_primary_gpu_vendor(&root, Some(&fallback)).unwrap().0,
            "0x10de"
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
            driver_candidates("0x10de", "nova")
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
