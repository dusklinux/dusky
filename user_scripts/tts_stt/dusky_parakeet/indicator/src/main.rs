use gtk::{glib, prelude::*};
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use serde_json::{Value, json};
use std::{
    cell::RefCell,
    env,
    ffi::CString,
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    rc::Rc,
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

// One request per SOCK_SEQPACKET message, matching the daemon's control protocol.
fn request(path: &CString, command: &str, session: &str) -> Option<Value> {
    let payload = serde_json::to_vec(&json!({"command": command, "session": session})).ok()?;
    unsafe {
        let raw = libc::socket(libc::AF_UNIX, libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC, 0);
        if raw < 0 {
            return None;
        }
        let fd = OwnedFd::from_raw_fd(raw);
        let timeout = libc::timeval {
            tv_sec: 1,
            tv_usec: 0,
        };
        for option in [libc::SO_RCVTIMEO, libc::SO_SNDTIMEO] {
            libc::setsockopt(
                fd.as_raw_fd(),
                libc::SOL_SOCKET,
                option,
                &timeout as *const _ as *const libc::c_void,
                size_of_val(&timeout) as u32,
            );
        }
        let mut addr: libc::sockaddr_un = std::mem::zeroed();
        addr.sun_family = libc::AF_UNIX as _;
        let bytes = path.as_bytes_with_nul();
        if bytes.len() > addr.sun_path.len() {
            return None;
        }
        for (to, from) in addr.sun_path.iter_mut().zip(bytes) {
            *to = *from as _;
        }
        if libc::connect(
            fd.as_raw_fd(),
            &addr as *const _ as *const libc::sockaddr,
            size_of_val(&addr) as u32,
        ) != 0
        {
            return None;
        }
        if libc::send(
            fd.as_raw_fd(),
            payload.as_ptr() as _,
            payload.len(),
            libc::MSG_NOSIGNAL,
        ) < 0
        {
            return None;
        }
        let mut buf = [0u8; 65536];
        let n = libc::recv(
            fd.as_raw_fd(),
            buf.as_mut_ptr() as _,
            buf.len(),
            libc::MSG_TRUNC,
        );
        if n <= 0 || n as usize > buf.len() {
            return None;
        }
        serde_json::from_slice(&buf[..n as usize]).ok()
    }
}

fn primary_card() -> Option<std::path::PathBuf> {
    let card = |path: &std::path::Path| {
        let resolved = path.canonicalize().ok()?;
        let name = resolved.file_name()?.to_str()?;
        name.strip_prefix("card")?.parse::<u32>().ok()?;
        Some(std::path::Path::new("/sys/class/drm").join(name))
    };
    if let Ok(devices) = env::var("AQ_DRM_DEVICES")
        && let Some(first) = devices.split(':').next()
        && let Some(path) = card(std::path::Path::new(first.trim()))
    {
        return Some(path);
    }
    let mut cards: Vec<_> = std::fs::read_dir("/sys/class/drm")
        .ok()?
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let name = entry.file_name();
            let index = name.to_str()?.strip_prefix("card")?.parse::<u32>().ok()?;
            Some((index, entry.path()))
        })
        .collect();
    cards.sort_by_key(|(index, _)| *index);
    cards
        .iter()
        .find(|(_, path)| {
            std::fs::read_to_string(path.join("device/boot_vga"))
                .is_ok_and(|value| value.trim() == "1")
        })
        .or_else(|| cards.first())
        .map(|(_, path)| path.clone())
}

fn configure_renderer() {
    // Match the tray/wallpaper's display GPU choice, using Wayland EGL.
    // All environment changes happen before GTK or background threads start.
    unsafe {
        env::set_var("GDK_BACKEND", "wayland");
        if env::var_os("GSK_RENDERER").is_none() {
            env::set_var("GSK_RENDERER", "gl");
        }
    }
    if [
        "DRI_PRIME",
        "__NV_PRIME_RENDER_OFFLOAD",
        "__NV_PRIME_RENDER_OFFLOAD_PROVIDER",
        "__EGL_VENDOR_LIBRARY_FILENAMES",
        "__EGL_VENDOR_LIBRARY_DIRS",
        "MESA_LOADER_DRIVER_OVERRIDE",
    ]
    .iter()
    .any(|name| env::var_os(name).is_some())
    {
        return;
    }
    let Some(card) = primary_card() else {
        return;
    };
    let vendor = std::fs::read_to_string(card.join("device/vendor")).unwrap_or_default();
    if matches!(vendor.trim(), "0x8086" | "0x1002" | "0x1af4") {
        let mesa = "/usr/share/glvnd/egl_vendor.d/50_mesa.json";
        if std::path::Path::new(mesa).is_file() {
            unsafe {
                env::set_var("__EGL_VENDOR_LIBRARY_FILENAMES", mesa);
            }
        }
        if matches!(vendor.trim(), "0x8086" | "0x1002")
            && let Ok(device) = card.join("device").canonicalize()
            && let Some(pci) = device.file_name().and_then(|name| name.to_str())
            && pci.split([':', '.']).count() == 4
            && pci
                .chars()
                .all(|c| c.is_ascii_hexdigit() || c == ':' || c == '.')
        {
            unsafe {
                env::set_var("DRI_PRIME", format!("pci-{}", pci.replace([':', '.'], "_")));
            }
        }
    }
}

fn main() {
    configure_renderer();
    let session = env::args().nth(1).unwrap_or_default();
    let runtime = env::var("XDG_RUNTIME_DIR").expect("XDG_RUNTIME_DIR unset");
    let path = CString::new(format!("{runtime}/dusky-stt/control.sock")).unwrap();
    let (commands, command_rx) = mpsc::channel::<&'static str>();
    let (status_tx, statuses) = mpsc::channel();
    thread::spawn(move || {
        loop {
            match command_rx.recv_timeout(Duration::from_millis(50)) {
                Ok(command) => {
                    request(&path, command, &session);
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
            if status_tx.send(request(&path, "status", &session)).is_err() {
                break;
            }
        }
    });
    let app = gtk::Application::builder()
        .application_id("org.dusky.SttIndicator")
        .flags(gtk::gio::ApplicationFlags::NON_UNIQUE)
        .build();
    let owner = env::args().nth(1).unwrap_or_default();
    let statuses = std::cell::RefCell::new(Some(statuses));
    app.connect_activate(move |app| {
        let window = gtk::ApplicationWindow::builder()
            .application(app)
            .title("Dusky Recording")
            .decorated(false)
            .resizable(false)
            .build();
        let css = gtk::CssProvider::new();
        css.load_from_string(
            r#"
            .dusky-pill {
                background: alpha(@theme_bg_color, 0.96);
                border: 1px solid alpha(@theme_fg_color, 0.14);
                border-radius: 28px;
                padding: 10px 16px;
                box-shadow: 0 4px 12px alpha(black, 0.18);
                color: @theme_fg_color;
            }
            .dusky-pill label { font-family: sans-serif; font-size: 13px; }
            .dusky-state { font-weight: 700; letter-spacing: 1px; }
            .dusky-clock { font-weight: 500; opacity: 0.65; }
            .dusky-dot { color: @theme_selected_bg_color; font-size: 15px; }
            .dusky-wave { color: @theme_selected_bg_color; }
            .dusky-pill button {
                background: alpha(@theme_fg_color, 0.07);
                border: none; border-radius: 18px;
                box-shadow: none; min-height: 32px; min-width: 32px;
                padding: 0; color: @theme_fg_color;
                transition: background 120ms ease;
            }
            .dusky-pill button:hover { background: alpha(@theme_fg_color, 0.16); }
            .dusky-pill button.stop { background: alpha(@theme_selected_bg_color, 0.22); }
            .dusky-pill button.stop:hover { background: alpha(@theme_selected_bg_color, 0.4); }
        "#,
        );
        gtk::style_context_add_provider_for_display(
            &gtk::prelude::WidgetExt::display(&window),
            &css,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
        // gtk.css uses USER priority and can repaint window.background.
        // Keep only this surface's transparency above that generic rule;
        // the pill's colors and controls continue to use the user's theme.
        let surface_css = gtk::CssProvider::new();
        surface_css
            .load_from_string("window.dusky-shell { background: transparent; box-shadow: none; }");
        gtk::style_context_add_provider_for_display(
            &gtk::prelude::WidgetExt::display(&window),
            &surface_css,
            gtk::STYLE_PROVIDER_PRIORITY_USER + 1,
        );
        window.add_css_class("dusky-shell");
        window.init_layer_shell();
        window.set_namespace(Some("dusky-stt"));
        window.set_layer(Layer::Overlay);
        window.set_keyboard_mode(KeyboardMode::None);
        window.set_anchor(Edge::Bottom, true);
        window.set_margin(Edge::Bottom, 56);
        let row = gtk::Box::new(gtk::Orientation::Horizontal, 10);
        row.add_css_class("dusky-pill");
        row.set_margin_top(8);
        row.set_margin_bottom(8);
        row.set_margin_start(8);
        row.set_margin_end(8);
        let dot = gtk::Label::new(Some("●"));
        dot.add_css_class("dusky-dot");
        let label = gtk::Label::new(Some("STARTING"));
        label.add_css_class("dusky-state");
        let clock = gtk::Label::new(Some("00:00"));
        clock.add_css_class("dusky-clock");
        clock.set_width_chars(5);
        let level = gtk::DrawingArea::new();
        level.add_css_class("dusky-wave");
        level.set_content_width(108);
        level.set_content_height(30);
        level.set_tooltip_text(Some("Microphone level"));
        let history = Rc::new(RefCell::new(([0.0_f64; 18], [0.0_f64; 18])));
        let bars = history.clone();
        let processing = Rc::new(std::cell::Cell::new(false));
        let is_processing = processing.clone();
        level.set_draw_func(move |area, cr, width, height| {
            let color = area.color();
            cr.set_source_rgba(
                color.red() as f64,
                color.green() as f64,
                color.blue() as f64,
                0.9,
            );
            let bars = bars.borrow();
            let step = width as f64 / 18.0;
            for (i, value) in bars.1.iter().enumerate() {
                if is_processing.get() {
                    cr.set_source_rgba(
                        color.red() as f64,
                        color.green() as f64,
                        color.blue() as f64,
                        0.18 + 0.72 * value,
                    );
                }
                let h = 3.0 + value * (height as f64 - 5.0);
                let x = i as f64 * step + step / 2.0;
                let top = (height as f64 - h) / 2.0;
                cr.set_line_width(3.0);
                cr.set_line_cap(gtk::cairo::LineCap::Round);
                cr.move_to(x, top + 1.5);
                cr.line_to(x, top + h - 1.5);
                let _ = cr.stroke();
            }
        });
        let pause = gtk::Button::with_label("Ⅱ");
        pause.set_sensitive(false);
        pause.set_tooltip_text(Some("Pause recording"));
        let stop = gtk::Button::with_label("■");
        stop.add_css_class("stop");
        stop.set_tooltip_text(Some("Stop and transcribe"));
        row.append(&dot);
        row.append(&label);
        row.append(&clock);
        row.append(&level);
        row.append(&pause);
        row.append(&stop);
        window.set_child(Some(&row));
        let tx = commands.clone();
        pause.connect_clicked(move |_| {
            let _ = tx.send("pause");
        });
        let tx = commands.clone();
        stop.connect_clicked(move |_| {
            let _ = tx.send("stop");
        });
        let rx = statuses.borrow_mut().take().expect("single activation");
        let weak = window.downgrade();
        let owner = owner.clone();
        let last_frame = std::cell::Cell::new(Instant::now());
        let animation_start = Instant::now();
        let indeterminate = std::cell::Cell::new(false);
        window.add_tick_callback(move |_, _| {
            let elapsed = last_frame
                .replace(Instant::now())
                .elapsed()
                .as_secs_f64()
                .min(0.1);
            let Some(window) = weak.upgrade() else {
                return glib::ControlFlow::Break;
            };
            while let Ok(status) = rx.try_recv() {
                let Some(status) = status else {
                    window.close();
                    return glib::ControlFlow::Break;
                };
                if !(status["state"] == "recording" || status["state"] == "finalizing")
                    || !owner.starts_with(status["session"].as_str().unwrap_or("unknown"))
                {
                    window.close();
                    return glib::ControlFlow::Break;
                }
                let finalizing = status["state"] == "finalizing";
                let capture_ready = status["capture_ready"].as_bool().unwrap_or(false);
                processing.set(finalizing);
                let paused = status["paused"].as_bool().unwrap_or(false);
                let secs = status[if finalizing {
                    "processing_seconds"
                } else {
                    "recorded_seconds"
                }]
                .as_f64()
                .unwrap_or(0.0) as u64;
                let fraction = status["progress"].as_f64().unwrap_or(0.0).clamp(0.0, 0.99);
                indeterminate.set(finalizing && (fraction <= 0.0 || fraction >= 0.99));
                if finalizing {
                    if fraction <= 0.0 {
                        label.set_text("PROCESSING");
                    } else if fraction >= 0.99 {
                        label.set_text("FINISHING");
                    } else {
                        label.set_text(&format!("PROCESSING {}%", (fraction * 100.0) as u32));
                    }
                } else {
                    label.set_text(if !capture_ready {
                        "STARTING"
                    } else if paused {
                        "PAUSED"
                    } else {
                        "REC"
                    });
                }
                pause.set_sensitive(capture_ready && !finalizing);
                stop.set_tooltip_text(Some(if finalizing {
                    "Cancel transcription"
                } else {
                    "Stop and transcribe"
                }));
                level.set_tooltip_text(Some(if finalizing {
                    "Recorded audio processed"
                } else {
                    "Microphone level"
                }));
                clock.set_text(&format!("{:02}:{:02}", secs / 60, secs % 60));
                pause.set_label(if paused { "▶" } else { "Ⅱ" });
                pause.set_tooltip_text(Some(if paused {
                    "Resume recording"
                } else {
                    "Pause recording"
                }));
                dot.set_opacity(if paused { 0.4 } else { 1.0 });
                let value = if paused {
                    0.0
                } else {
                    status["level"]
                        .as_f64()
                        .unwrap_or(0.0)
                        .clamp(0.0, 1.0)
                        .powf(0.6)
                };
                let mut bars = history.borrow_mut();
                if finalizing {
                    for i in 0..18 {
                        bars.0[i] = if (i as f64) < fraction * 18.0 {
                            1.0
                        } else {
                            0.0
                        };
                    }
                } else {
                    bars.0.rotate_left(1);
                    bars.0[17] = value;
                }
            }
            {
                let mut bars = history.borrow_mut();
                // A model call is indivisible. Animate activity until measured
                // block progress arrives, and during final result publication.
                if indeterminate.get() {
                    let phase = animation_start.elapsed().as_secs_f64() * 4.0;
                    for i in 0..18 {
                        bars.0[i] = 0.15 + 0.85 * ((phase - i as f64 * 0.45).sin() * 0.5 + 0.5);
                    }
                }
                for i in 0..18 {
                    let rate = if bars.0[i] > bars.1[i] { 35.0 } else { 16.0 };
                    bars.1[i] += (bars.0[i] - bars.1[i]) * (1.0 - (-rate * elapsed).exp());
                }
            }
            level.queue_draw();
            glib::ControlFlow::Continue
        });
        window.present();
    });
    // Session argument belongs to us, not GTK's command-line parser.
    app.run_with_args::<&str>(&[]);
}
