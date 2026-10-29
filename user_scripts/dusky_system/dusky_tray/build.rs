fn main() {
    let helper = std::path::Path::new("../click_away_to_dismiss");
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("OUT_DIR is unset"));
    let protocol = helper.join("hyprland-focus-grab-v1.xml");
    for name in ["dusky.c", "hyprland-focus-grab-v1.xml"] {
        println!("cargo:rerun-if-changed={}", helper.join(name).display());
    }
    for (mode, name) in [
        ("client-header", "hyprland-focus-grab-v1-client-protocol.h"),
        ("private-code", "hyprland-focus-grab-v1-client-protocol.c"),
    ] {
        let status = std::process::Command::new("wayland-scanner")
            .arg(mode)
            .arg(&protocol)
            .arg(output.join(name))
            .status()
            .expect("wayland-scanner is required to build Dusky Tray");
        assert!(status.success(), "wayland-scanner failed for {name}");
    }
    // Quoted C includes search the source directory first. Stage dusky.c here
    // so an old generated header beside the checkout cannot override this one.
    let source = output.join("dusky.c");
    std::fs::copy(helper.join("dusky.c"), &source).expect("Cannot stage click-away source");
    cc::Build::new()
        .file(source)
        .file(output.join("hyprland-focus-grab-v1-client-protocol.c"))
        .include(&output)
        .warnings(false)
        .compile("dusky_click_away");
    for library in ["wayland-client", "dl", "pthread"] {
        println!("cargo:rustc-link-lib={library}");
    }
}
