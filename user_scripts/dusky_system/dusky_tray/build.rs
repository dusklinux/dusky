fn main() {
    let helper = std::path::Path::new("../click_away_to_dismiss");
    for name in [
        "dusky.c",
        "hyprland-focus-grab-v1-client-protocol.c",
        "hyprland-focus-grab-v1-client-protocol.h",
    ] {
        println!("cargo:rerun-if-changed={}", helper.join(name).display());
    }
    cc::Build::new()
        .file(helper.join("dusky.c"))
        .file(helper.join("hyprland-focus-grab-v1-client-protocol.c"))
        .include(helper)
        .warnings(false)
        .compile("dusky_click_away");
    for library in ["wayland-client", "dl", "pthread"] {
        println!("cargo:rustc-link-lib={library}");
    }
}
