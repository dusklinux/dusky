//! Native Wayland overlay with independent image and vector paint layers.
use crate::{
    Options, Result,
    capture::{self, Capture, Monitor},
    document::{Document, Layer, Point, Rect, Tool},
    output::{self, Config},
    theme,
};
use iced::{
    Color, Element, Length, Rectangle, Size, Subscription, Task, Theme, Vector, keyboard, mouse,
    widget::{
        self, Canvas, canvas, checkbox, column, container, image as picture, mouse_area, row,
        scrollable, slider, text, text_input,
    },
    window,
};
use std::{
    fs::File,
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug, PartialEq)]
enum Phase {
    Pick,
    Preparing,
    Edit,
    Preview,
    Scroll,
}
#[derive(Clone, Copy, Debug)]
enum Motion {
    Down,
    Move,
    Up,
    PanDown,
    PanMove,
    PanUp,
}
#[derive(Clone, Copy, Debug)]
enum Action {
    Copy,
    Save,
    Both,
    Pin,
    SaveAs,
    Undo,
    Redo,
    Delete,
    Raise,
    Lower,
    Backdrop,
    Grow,
    Shadow,
    Smaller,
    Larger,
    Ocr,
    Folder,
    Path,
    Edit,
    Close,
    ScrollFrame,
    ScrollAuto,
    ScrollStop,
    ScrollDone,
}
#[derive(Clone)]
enum Message {
    Event(iced::Event),
    Opened(window::Id),
    Drag,
    Pointer(Motion, Point),
    Wheel(f32, bool),
    Tool(Tool),
    Act(Action),
    Width(f64),
    Color(String),
    Filled(bool),
    More,
    History,
    Annotate(bool),
    Mode(&'static str),
    Text(String),
    Font(String),
    TextStyle(u8),
    CommitText,
    CancelSheet,
    SavePath(String),
    SaveConfirm,
    Recent(PathBuf),
    Recents(Vec<(PathBuf, picture::Handle)>),
    Rendered(u64, std::result::Result<picture::Handle, String>),
    AutoSave(u64),
    Finished(std::result::Result<Finish, String>),
    Loaded(std::result::Result<Capture, String>),
    SelectionReady(std::result::Result<(Document, Option<picture::Handle>), String>),
    Ocr(std::result::Result<String, String>),
    Notice(std::result::Result<(), String>),
    ScrollFrame(std::result::Result<::image::RgbaImage, String>),
    AutoEnded(std::result::Result<Document, String>),
    Expire(u64),
    Hover(bool),
    Platform(Platform),
    Smoke,
}
impl std::fmt::Debug for Message {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Dusky Snap event")
    }
}
#[derive(Clone)]
struct Finish {
    path: PathBuf,
    doc: Document,
    close: bool,
    preview: bool,
    pin: bool,
}
#[derive(Clone, Debug)]
enum Platform {
    Cancel,
    Theme(Theme),
}
#[derive(Clone)]
struct Gesture {
    start: Point,
    last: Point,
    points: Vec<Point>,
    original: Option<Layer>,
    resize: Option<usize>,
}
struct App {
    options: Options,
    config: Config,
    capture: Capture,
    phase: Phase,
    image: picture::Handle,
    image_size: (u32, u32),
    rendering: bool,
    render_dirty: bool,
    render_serial: u64,
    render_size: (u32, u32),
    tool: Tool,
    color: String,
    width: f64,
    filled: bool,
    selected: Option<usize>,
    gesture: Option<Gesture>,
    hover: Option<Point>,
    zoom: f32,
    pan: Vector,
    window: Option<window::Id>,
    editor_size: Option<Size>,
    more: bool,
    history: bool,
    picker_controls: bool,
    busy: bool,
    status: String,
    text_draft: String,
    font: String,
    text_style: u8,
    text_anchor: Option<Point>,
    save_path: Option<String>,
    recents: Vec<(PathBuf, picture::Handle)>,
    theme: Theme,
    start: Instant,
    preview_path: Option<PathBuf>,
    pin: bool,
    hovering: bool,
    expiry: u64,
    remaining: Duration,
    expiry_start: Instant,
    stitcher: Option<capture::Stitcher>,
    scroll_stop: Arc<AtomicBool>,
    scroll_origin: Option<Rect>,
}
struct SharedPixels(Arc<::image::RgbaImage>);
impl AsRef<[u8]> for SharedPixels {
    fn as_ref(&self) -> &[u8] {
        self.0.as_raw()
    }
}
fn handle(im: &Arc<::image::RgbaImage>) -> picture::Handle {
    let pixels = iced::advanced::graphics::core::Bytes::from_owner(SharedPixels(im.clone()));
    picture::Handle::from_rgba(im.width(), im.height(), pixels)
}
fn owned_handle(im: ::image::RgbaImage) -> picture::Handle {
    picture::Handle::from_rgba(im.width(), im.height(), im.into_raw())
}
fn job<T: Send + 'static>(
    work: impl FnOnce() -> Result<T> + Send + 'static,
    done: impl FnOnce(std::result::Result<T, String>) -> Message + Send + 'static,
) -> Task<Message> {
    crate::worker::perform(move || done(work().map_err(|e| e.to_string())))
}
fn delay(duration: Duration, message: Message) -> Task<Message> {
    Task::perform(
        async move {
            futures_timer::Delay::new(duration).await;
            message
        },
        |m| m,
    )
}
fn spawn_preview(path: &std::path::Path, pin: bool) -> Result<()> {
    let mut cmd = std::process::Command::new(std::env::current_exe()?);
    crate::gpu::inherit_session_environment(&mut cmd);
    cmd.arg(if pin { "--pin" } else { "--preview" })
        .arg(path)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()?;
    Ok(())
}
fn float_rules(picker: Option<&Monitor>) -> Result<()> {
    let mut rules = String::from(
        r#"hl.window_rule({name="dusky_snap_native",match={class="^dusky-snap(-(pin|pick))?$"},float=true,center=true,no_dim=true,opacity="1.0 override 1.0 override",persistent_size=false,border_size=0,no_shadow=true,decorate=false,rounding=0}); hl.window_rule({name="dusky_snap_pin",match={class="^dusky-snap-(pin|pick)$"},pin=true})"#,
    );
    if let Some(monitor) = picker {
        // A normal fullscreen surface loses pointer events to pinned windows.
        // Map the picker as the top pinned float, covering the captured output.
        rules.push_str(&format!(
            r#"; hl.window_rule({{name="dusky_snap_pick",match={{class="^dusky-snap-pick$"}},monitor={},size="monitor_w monitor_h",move="0 0",center=false,no_anim=true}})"#,
            serde_json::to_string(&monitor.name)?
        ));
    }
    capture::command("hyprctl", &["eval", &rules], None, Duration::from_secs(3))?;
    Ok(())
}
static CANCEL: AtomicBool = AtomicBool::new(false);
static SIGNAL_FD: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(-1);
extern "C" fn cancel_signal(_: i32) {
    CANCEL.store(true, Ordering::Relaxed);
    let fd = SIGNAL_FD.load(Ordering::Relaxed);
    if fd >= 0 {
        unsafe {
            libc::write(fd, b"x".as_ptr().cast(), 1);
        }
    }
}
fn instance(o: &Options) -> Result<Option<File>> {
    if o.preview.is_some() {
        return Ok(None);
    }
    let root = output::runtime()?;
    let lock = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(root.join("overlay.lock"))?;
    let contended = match lock.try_lock() {
        Ok(()) => false,
        Err(std::fs::TryLockError::WouldBlock) => true,
        Err(e) => return Err(e.into()),
    };
    if contended {
        if let Ok(pid) = std::fs::read_to_string(root.join("overlay.pid"))
            && let Ok(pid) = pid.trim().parse::<i32>()
            && pid > 1
        {
            unsafe {
                libc::kill(pid, libc::SIGUSR1);
            }
        }
        if o.file.is_none() && !o.clipboard {
            return Err("Overlay dismissed".into());
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if lock.try_lock().is_ok() {
                break;
            }
            if Instant::now() > deadline {
                return Err("Previous editor is still closing".into());
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    std::fs::write(root.join("overlay.pid"), std::process::id().to_string())?;
    unsafe {
        libc::signal(libc::SIGUSR1, cancel_signal as *const () as usize);
    }
    Ok(Some(lock))
}
pub fn run(options: Options, start: Instant) -> Result<()> {
    let _lock = match instance(&options) {
        Ok(l) => l,
        Err(e) if e.to_string() == "Overlay dismissed" => return Ok(()),
        Err(e) => return Err(e),
    };
    // Environment mutation must precede capture, graphics and worker threads.
    crate::gpu::optimize_environment();
    let config = output::config()?;
    let loaded = options.file.is_some() || options.clipboard || options.preview.is_some();
    // Iced otherwise scans system fonts on the graphics startup path. Start
    // the same global font system while Wayland captures the screen. Its
    // OnceLock synchronizes the renderer if discovery is still in progress.
    let _fonts = if loaded || options.editor || options.mode != "fullscreen" {
        Some(
            std::thread::Builder::new()
                .name("snap-fonts".into())
                .spawn(move || {
                    let _ = iced::advanced::graphics::text::font_system();
                    crate::trace(start, "fonts-ready");
                })?,
        )
    } else {
        None
    };
    let capture = if let Some(p) = &options.preview {
        capture::load(&Options {
            file: Some(p.clone()),
            ..options.clone()
        })?
    } else if loaded {
        capture::load(&options)?
    } else {
        capture::take(&options)?
    };
    crate::trace(start, "capture-ready");
    if options.mode == "fullscreen" && !loaded && !options.editor {
        finish(
            &capture.document,
            &config,
            options.copy,
            options.save,
            options.output.clone(),
        )?;
        return Ok(());
    }
    let phase = if options.preview.is_some() {
        Phase::Preview
    } else if loaded || options.mode == "fullscreen" {
        Phase::Edit
    } else {
        Phase::Pick
    };
    float_rules((phase == Phase::Pick).then_some(&capture.monitor))?;
    let (mw, mh) = capture.monitor.logical_size();
    let initial_size = if phase == Phase::Pick {
        Size::new(mw as f32, mh as f32)
    } else {
        window_size(&capture.document, phase, &capture.monitor)
    };
    let settings = window::Settings {
        size: initial_size,
        min_size: Some(Size::new(
            (if phase == Phase::Preview {
                280.0_f32
            } else {
                360.0_f32
            })
            .min((mw - 32).max(1) as f32),
            (if phase == Phase::Preview {
                100.0_f32
            } else {
                220.0_f32
            })
            .min((mh - 32).max(1) as f32),
        )),
        decorations: false,
        transparent: true,
        exit_on_close_request: false,
        platform_specific: window::settings::PlatformSpecific {
            application_id: if phase == Phase::Pick {
                "dusky-snap-pick".into()
            } else if options.pin {
                "dusky-snap-pin".into()
            } else {
                "dusky-snap".into()
            },
            ..Default::default()
        },
        ..Default::default()
    };
    let initial = std::sync::Mutex::new(Some((options, config, capture)));
    iced::application(
        move || {
            let (o, c, cap) = initial
                .lock()
                .unwrap()
                .take()
                .expect("application booted once");
            App::boot(o, c, cap, phase, start)
        },
        App::update,
        App::view,
    )
    .window(settings)
    .title("Dusky Snap")
    .default_font(iced::Font::with_name("Atkinson Hyperlegible"))
    .executor::<BackgroundExecutor>()
    .subscription(App::subscription)
    .theme(|app: &App| app.theme.clone())
    .style(|app: &App, _| iced::theme::Style {
        background_color: Color::TRANSPARENT,
        text_color: app.theme.palette().text,
    })
    .run()?;
    Ok(())
}
fn window_size(doc: &Document, phase: Phase, monitor: &Monitor) -> Size {
    let (mw, mh) = monitor.logical_size();
    let [left, top, right, bottom] = monitor.reserved;
    let maxw = (mw - left - right - 32).max(1) as f32;
    let maxh = (mh - top - bottom - 32).max(1) as f32;
    if phase == Phase::Scroll {
        return Size::new(480.0_f32.min(maxw), 220.0_f32.min(maxh));
    }
    let r = doc.canvas();
    if phase == Phase::Preview {
        let scale = ((maxw - 24.0).min(720.0) / r.w as f32)
            .min((maxh - 92.0).max(1.0) / r.h as f32)
            .min(1.0);
        return Size::new(
            (r.w as f32 * scale + 24.0).max(324.0).min(maxw),
            (r.h as f32 * scale + 92.0).min(maxh),
        );
    }
    let width = maxw.min(1100.0);
    let scale = ((width - 24.0) / r.w as f32)
        .min((maxh - 170.0).max(1.0) / r.h as f32)
        .min(1.0);
    Size::new(width, (r.h as f32 * scale + 170.0).max(260.0).min(maxh))
}
fn finish(
    doc: &Document,
    config: &Config,
    copy: bool,
    save: bool,
    path: Option<PathBuf>,
) -> Result<(PathBuf, Document)> {
    let (png, thumbnail) = {
        let image = doc.render()?;
        (output::encode(&image)?, output::thumbnail(&image, 240, 160))
    };
    let mut doc = doc.clone();
    if save {
        let mut path = path
            .map(Ok)
            .unwrap_or_else(|| output::destination(config))?;
        if path.extension().is_none() {
            path.set_extension("png");
        }
        if path
            .extension()
            .is_none_or(|e| !e.eq_ignore_ascii_case("png"))
        {
            return Err("Output must be a PNG file".into());
        }
        let path = std::path::absolute(path)?;
        output::atomic_write(&path, &png)?;
        doc.saved_path = Some(path);
    }
    if copy {
        output::copy(png, "image/png")?;
    }
    let path = output::persist(&doc, Some(thumbnail))?;
    Ok((path, doc))
}
impl App {
    fn doc(&self) -> &Document {
        &self.capture.document
    }
    fn doc_mut(&mut self) -> &mut Document {
        &mut self.capture.document
    }
    fn boot(
        options: Options,
        config: Config,
        capture: Capture,
        phase: Phase,
        start: Instant,
    ) -> (Self, Task<Message>) {
        let (image, image_size) = if phase == Phase::Pick {
            (
                handle(&capture.document.source),
                capture.document.source.dimensions(),
            )
        } else {
            let initial = capture
                .document
                .render()
                .unwrap_or_else(|_| capture.document.source.as_ref().clone());
            let size = initial.dimensions();
            (owned_handle(initial), size)
        };
        let hover = (phase == Phase::Pick).then_some(capture.pointer).flatten();
        let pin = options.pin;
        let app = Self {
            width: config.line_width,
            color: config.colors.first().cloned().unwrap_or("#f7768e".into()),
            options,
            config,
            capture,
            phase,
            image,
            image_size,
            rendering: false,
            render_dirty: false,
            render_serial: 0,
            render_size: image_size,
            tool: Tool::Arrow,
            filled: false,
            selected: None,
            gesture: None,
            hover,
            zoom: 1.0,
            pan: Vector::ZERO,
            window: None,
            editor_size: None,
            more: false,
            history: false,
            picker_controls: false,
            busy: false,
            status: String::new(),
            text_draft: String::new(),
            font: "Sans".into(),
            text_style: 0,
            text_anchor: None,
            save_path: None,
            recents: vec![],
            theme: theme::load(),
            start,
            preview_path: None,
            pin,
            hovering: false,
            expiry: 1,
            remaining: Duration::from_secs(10),
            expiry_start: Instant::now(),
            stitcher: None,
            scroll_stop: Arc::new(AtomicBool::new(false)),
            scroll_origin: None,
        };
        let mut tasks = vec![
            window::latest().map(|id| id.map(Message::Opened).unwrap_or(Message::Notice(Ok(())))),
        ];
        if phase == Phase::Preview && !pin {
            tasks.push(delay(Duration::from_secs(10), Message::Expire(1)));
        }
        (app, Task::batch(tasks))
    }
    fn subscription(&self) -> Subscription<Message> {
        Subscription::batch([
            iced::event::listen().map(Message::Event),
            Subscription::run(platform_events).map(Message::Platform),
        ])
    }
    fn close(&mut self) -> Task<Message> {
        self.scroll_stop.store(true, Ordering::Relaxed);
        if self.phase == Phase::Preparing {
            return iced::exit();
        }
        if self.busy {
            return Task::none();
        }
        self.busy = true;
        let doc = self.doc().clone();
        job(
            move || {
                let path = output::persist(&doc, None)?;
                Ok(Finish {
                    path,
                    doc,
                    close: true,
                    preview: false,
                    pin: false,
                })
            },
            Message::Finished,
        )
    }
    fn render(&mut self) -> Task<Message> {
        if self.rendering {
            self.render_dirty = true;
            return Task::none();
        }
        self.rendering = true;
        self.render_dirty = false;
        self.render_serial = self.render_serial.wrapping_add(1);
        let serial = self.render_serial;
        self.render_size = im_dimensions(self.doc());
        let doc = self.doc().clone();
        let revision = doc.revision;
        Task::batch([
            job(
                move || Ok(owned_handle(doc.render()?)),
                move |r| Message::Rendered(serial, r),
            ),
            delay(Duration::from_millis(400), Message::AutoSave(revision)),
        ])
    }
    fn export(&mut self, doc: Document, action: Action) -> Task<Message> {
        self.busy = true;
        self.status = "Preparing screenshot…".into();
        let config = self.config.clone();
        let path = self.options.output.clone();
        let is_preview = self.phase == Phase::Preview;
        let copy = matches!(action, Action::Copy | Action::Both);
        let save = matches!(action, Action::Save | Action::Both);
        let pin = matches!(action, Action::Pin);
        let preview = !is_preview && pin;
        job(
            move || {
                let (path, doc) = finish(&doc, &config, copy, save, path)?;
                Ok(Finish {
                    path,
                    doc,
                    close: !is_preview,
                    preview,
                    pin,
                })
            },
            Message::Finished,
        )
    }
    fn act(&mut self, action: Action) -> Task<Message> {
        if matches!(action, Action::ScrollStop) {
            self.scroll_stop.store(true, Ordering::Relaxed);
            return Task::none();
        }
        if self.busy {
            return Task::none();
        }
        match action {
            Action::Close => return self.close(),
            Action::Undo => {
                self.selected = None;
                self.doc_mut().undo();
            }
            Action::Redo => {
                self.selected = None;
                self.doc_mut().redo();
            }
            Action::Delete => {
                if let Some(i) = self.selected.take() {
                    self.doc_mut().checkpoint();
                    self.doc_mut().state.layers.remove(i);
                }
            }
            Action::Raise | Action::Lower => {
                if let Some(i) = self.selected {
                    let n = self.doc().state.layers.len();
                    let j = if matches!(action, Action::Raise) {
                        (i + 1).min(n - 1)
                    } else {
                        i.saturating_sub(1)
                    };
                    if i != j {
                        self.doc_mut().checkpoint();
                        self.doc_mut().state.layers.swap(i, j);
                        self.selected = Some(j);
                    }
                }
            }
            Action::Backdrop => {
                self.doc_mut().checkpoint();
                self.doc_mut().state.backdrop = (self.doc().state.backdrop + 1) % 5;
            }
            Action::Grow => {
                self.doc_mut().checkpoint();
                self.doc_mut().state.grow = !self.doc().state.grow;
            }
            Action::Shadow => {
                self.doc_mut().checkpoint();
                self.doc_mut().state.shadow = !self.doc().state.shadow;
            }
            Action::SaveAs => {
                let config = self.config.clone();
                return job(
                    move || Ok(output::destination(&config)?.to_string_lossy().into_owned()),
                    |r| match r {
                        Ok(p) => Message::SavePath(p),
                        Err(e) => Message::Notice(Err(e)),
                    },
                );
            }
            Action::Edit => {
                self.phase = Phase::Edit;
                self.pin = false;
                self.expiry += 1;
                return job(|| set_pinned(false), Message::Notice)
                    .chain(self.resize())
                    .chain(self.render());
            }
            Action::Copy | Action::Save | Action::Both | Action::Pin => {
                if self.phase == Phase::Preview && matches!(action, Action::Pin) {
                    self.pin = !self.pin;
                    self.expiry += 1;
                    self.remaining = Duration::from_secs(10);
                    self.expiry_start = Instant::now();
                    let pin = self.pin;
                    let task = job(move || set_pinned(pin), Message::Notice);
                    return if pin {
                        task
                    } else {
                        Task::batch([task, delay(self.remaining, Message::Expire(self.expiry))])
                    };
                }
                return self.export(self.doc().clone(), action);
            }
            Action::Ocr => {
                self.busy = true;
                self.status = "Reading text…".into();
                let doc = self.doc().clone();
                let config = self.config.clone();
                return job(move || output::ocr(&doc, &config), Message::Ocr);
            }
            Action::Path | Action::Folder => {
                let doc = self.doc().clone();
                let config = self.config.clone();
                let folder = matches!(action, Action::Folder);
                return job(
                    move || {
                        let p = if let Some(p) = doc.saved_path {
                            p
                        } else {
                            finish(&doc, &config, false, true, None)?
                                .1
                                .saved_path
                                .ok_or("Screenshot was not saved")?
                        };
                        if folder {
                            let mut cmd = std::process::Command::new("xdg-open");
                            crate::gpu::inherit_session_environment(&mut cmd);
                            cmd.arg(p.parent().ok_or("No containing folder")?).spawn()?;
                        } else {
                            output::copy(
                                p.to_string_lossy().as_bytes().to_vec(),
                                "text/plain;charset=utf-8",
                            )?;
                        }
                        Ok(())
                    },
                    Message::Notice,
                );
            }
            Action::Smaller | Action::Larger => {
                if let Some(i) = self.selected {
                    self.doc_mut().checkpoint();
                    self.doc_mut().state.layers[i].scale(if matches!(action, Action::Smaller) {
                        0.9
                    } else {
                        1.1
                    });
                }
            }
            Action::ScrollStop => return Task::none(),
            Action::ScrollFrame => return self.scroll_frame(),
            Action::ScrollAuto => return self.scroll_auto(),
            Action::ScrollDone => {
                if let Some(s) = self.stitcher.take() {
                    self.capture.document = Document::new(s.image);
                    self.phase = Phase::Edit;
                    self.zoom = 1.0;
                    self.pan = Vector::ZERO;
                    return self.resize().chain(self.render());
                }
                return Task::none();
            }
        }
        self.render()
    }
    fn resize(&mut self) -> Task<Message> {
        if let Some(id) = self.window {
            if self.phase == Phase::Pick {
                return Task::none();
            }
            let size = window_size(self.doc(), self.phase, &self.capture.monitor);
            // Also handle a user-requested fullscreen editor before resizing.
            window::set_mode(id, window::Mode::Windowed)
                .chain(job(move || resize_window(size), Message::Notice))
                // An unchanged size need not produce a Wayland resize event.
                .chain(
                    window::size(id).map(|size| {
                        Message::Event(iced::Event::Window(window::Event::Resized(size)))
                    }),
                )
        } else {
            Task::none()
        }
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::Opened(id) => {
                self.window = Some(id);
                crate::trace(self.start, "window-opened");
                let opening = if self.phase != Phase::Preview {
                    window::run(id, |window| {
                        Message::Notice(crate::focus::handoff(window).map_err(|e| e.to_string()))
                    })
                } else {
                    Task::none()
                };
                let opening = if self.phase == Phase::Pick {
                    let monitor = self.capture.monitor.clone();
                    opening.chain(job(move || position_picker(&monitor), Message::Notice))
                } else {
                    opening
                };
                if self.options.smoke {
                    return opening.chain(delay(Duration::from_millis(150), Message::Smoke));
                }
                return opening;
            }
            Message::Drag => {
                self.expiry += 1;
                if let Some(id) = self.window {
                    return window::drag(id);
                }
            }
            Message::Act(a) => return self.act(a),
            Message::Tool(t) => {
                self.tool = t;
                self.selected = None;
            }
            Message::Pointer(m, p) => return self.pointer(m, p),
            Message::Wheel(delta, ctrl) => {
                if ctrl {
                    self.zoom = (self.zoom * (1.0 + delta * 0.12)).clamp(0.2, 12.0);
                } else {
                    self.width = (self.width + delta as f64).clamp(1.0, 80.0);
                    return self.patch_selected();
                }
            }
            Message::Width(v) => {
                self.width = v;
                return self.patch_selected();
            }
            Message::Filled(v) => {
                self.filled = v;
                return self.patch_selected();
            }
            Message::Color(v) => {
                self.color = v;
                return self.patch_selected();
            }
            Message::More => self.more = !self.more,
            Message::History => {
                self.history = !self.history;
                if self.history {
                    return job(
                        || {
                            Ok(output::recent_paths()?
                                .into_iter()
                                .take(5)
                                .filter_map(|p| {
                                    let source = p.with_file_name(format!(
                                        "{}-thumb.png",
                                        p.file_stem()?.to_string_lossy()
                                    ));
                                    let im = ::image::open(source).ok()?.into_rgba8();
                                    Some((p, owned_handle(im)))
                                })
                                .collect())
                        },
                        |r| Message::Recents(r.unwrap_or_default()),
                    );
                }
            }
            Message::Annotate(v) => self.options.editor = v,
            Message::Mode(v) => self.options.mode = v.into(),
            Message::Text(v) => self.text_draft = v,
            Message::Font(v) => self.font = v,
            Message::TextStyle(v) => self.text_style = v,
            Message::CommitText => {
                if let Some(p) = self.text_anchor.take()
                    && !self.text_draft.is_empty()
                {
                    let layer = self.layer(vec![p, p], self.text_draft.clone());
                    self.doc_mut().checkpoint();
                    self.doc_mut().state.layers.push(layer);
                    self.text_draft.clear();
                    return self.render();
                }
            }
            Message::CancelSheet => {
                self.text_anchor = None;
                self.save_path = None;
            }
            Message::SavePath(p) => self.save_path = Some(p),
            Message::SaveConfirm => {
                if let Some(p) = self.save_path.take() {
                    self.busy = true;
                    let doc = self.doc().clone();
                    let config = self.config.clone();
                    return job(
                        move || {
                            let (path, doc) =
                                finish(&doc, &config, false, true, Some(PathBuf::from(p)))?;
                            Ok(Finish {
                                path,
                                doc,
                                close: true,
                                preview: false,
                                pin: false,
                            })
                        },
                        Message::Finished,
                    );
                }
            }
            Message::Recents(v) => self.recents = v,
            Message::Recent(p) => {
                self.busy = true;
                let options = self.options.clone();
                return job(
                    move || {
                        capture::load(&Options {
                            file: Some(p),
                            clipboard: false,
                            ..options
                        })
                    },
                    Message::Loaded,
                );
            }
            Message::Loaded(r) => {
                self.busy = false;
                match r {
                    Ok(c) => {
                        self.history = false;
                        self.capture = c;
                        self.phase = Phase::Edit;
                        self.selected = None;
                        self.zoom = 1.0;
                        self.pan = Vector::ZERO;
                        return self.resize().chain(self.render());
                    }
                    Err(e) => self.status = e,
                }
            }
            Message::SelectionReady(result) => {
                self.busy = false;
                match result {
                    Ok((doc, image)) => {
                        if let Some(image) = image {
                            self.capture.document = doc;
                            self.image = image;
                            self.image_size = im_dimensions(self.doc());
                            let size = window_size(self.doc(), Phase::Edit, &self.capture.monitor);
                            self.editor_size =
                                Some(Size::new(size.width.round(), size.height.round()));
                            self.phase = Phase::Preparing;
                            self.busy = true;
                            return self.resize();
                        }
                        return self.export(
                            doc,
                            if self.options.save {
                                if self.options.copy {
                                    Action::Both
                                } else {
                                    Action::Save
                                }
                            } else {
                                Action::Copy
                            },
                        );
                    }
                    Err(e) => {
                        self.phase = Phase::Pick;
                        self.status = e;
                    }
                }
            }
            Message::Rendered(serial, result) => {
                if serial == self.render_serial {
                    self.rendering = false;
                    let again = self.render_dirty;
                    match result {
                        Ok(im) => {
                            self.image_size = self.render_size;
                            self.image = im;
                        }
                        Err(e) => self.status = e,
                    }
                    if again {
                        return self.render();
                    }
                }
            }
            Message::AutoSave(revision) => {
                if revision == self.doc().revision && !self.busy && self.gesture.is_none() {
                    let doc = self.doc().clone();
                    return job(move || output::working(&doc).map(|_| ()), Message::Notice);
                }
            }
            Message::Finished(result) => {
                self.busy = false;
                match result {
                    Ok(f) => {
                        self.capture.document = f.doc;
                        self.preview_path = Some(f.path.clone());
                        if f.close {
                            if f.preview {
                                let path = f.path;
                                let pin = f.pin;
                                return job(
                                    move || spawn_preview(&path, pin),
                                    |r| match r {
                                        Ok(()) => Message::Event(iced::Event::Window(
                                            window::Event::Closed,
                                        )),
                                        Err(e) => Message::Notice(Err(e)),
                                    },
                                );
                            }
                            return iced::exit();
                        }
                        self.status = "Done".into();
                    }
                    Err(e) => self.status = e,
                }
            }
            Message::Notice(Err(e)) => {
                self.status = e;
                if self.phase == Phase::Preparing && self.editor_size.take().is_some() {
                    self.phase = Phase::Edit;
                    self.busy = false;
                }
            }
            Message::Ocr(r) => {
                self.busy = false;
                self.status = match r {
                    Ok(t) => format!("Copied {} characters", t.chars().count()),
                    Err(e) => e,
                };
            }
            Message::Platform(Platform::Theme(theme)) => self.theme = theme,
            Message::Platform(Platform::Cancel) => return self.close(),
            Message::Event(iced::Event::Window(window::Event::Closed)) => return iced::exit(),
            Message::Event(iced::Event::Window(window::Event::CloseRequested)) => {
                return self.close();
            }
            Message::Event(iced::Event::Window(window::Event::Resized(size))) => {
                if self.phase == Phase::Preparing
                    && self.editor_size.is_some_and(|target| {
                        (size.width - target.width).abs() <= 1.0
                            && (size.height - target.height).abs() <= 1.0
                    })
                {
                    self.editor_size = None;
                    self.phase = Phase::Edit;
                    self.busy = false;
                }
            }
            Message::Event(iced::Event::Keyboard(keyboard::Event::KeyPressed {
                key,
                modifiers,
                ..
            })) => {
                if self.text_anchor.is_some() || self.save_path.is_some() {
                    if key == keyboard::Key::Named(keyboard::key::Named::Escape) {
                        self.text_anchor = None;
                        self.save_path = None;
                    }
                    return Task::none();
                }
                use keyboard::{Key, key::Named};
                match key.as_ref() {
                    Key::Named(Named::Tab) if self.phase == Phase::Pick => {
                        self.picker_controls = !self.picker_controls;
                        self.history = false;
                    }
                    Key::Named(Named::Escape)
                        if self.phase == Phase::Pick && self.picker_controls =>
                    {
                        self.picker_controls = false;
                        self.history = false;
                    }
                    Key::Named(Named::Escape) => return self.close(),
                    Key::Named(Named::Enter) => return self.act(Action::Both),
                    Key::Named(Named::Delete) => return self.act(Action::Delete),
                    Key::Character(c) if modifiers.control() => {
                        let a = match c.to_lowercase().as_str() {
                            "z" if modifiers.shift() => Some(Action::Redo),
                            "z" => Some(Action::Undo),
                            "y" => Some(Action::Redo),
                            "c" => Some(Action::Copy),
                            "s" if modifiers.shift() => Some(Action::SaveAs),
                            "s" => Some(Action::Save),
                            "p" => Some(Action::Pin),
                            _ => None,
                        };
                        if let Some(a) = a {
                            return self.act(a);
                        }
                    }
                    Key::Character(c) => match c.to_lowercase().as_str() {
                        "a" => self.tool = Tool::Arrow,
                        "v" => self.tool = Tool::Select,
                        "l" => self.tool = Tool::Line,
                        "d" => self.tool = Tool::Pen,
                        "h" => self.tool = Tool::Highlight,
                        "r" => self.tool = Tool::Rectangle,
                        "e" => {
                            if self.phase == Phase::Preview {
                                return self.act(Action::Edit);
                            }
                            self.tool = Tool::Ellipse
                        }
                        "n" => self.tool = Tool::Marker,
                        "t" => self.tool = Tool::Text,
                        "x" => self.tool = Tool::Redact,
                        "c" => self.tool = Tool::Crop,
                        "o" => return self.act(Action::Ocr),
                        "b" => return self.act(Action::Backdrop),
                        "p" => return self.act(Action::Pin),
                        _ => {}
                    },
                    _ => {}
                }
            }
            Message::Event(iced::Event::Window(window::Event::RedrawRequested(_))) => {
                crate::trace(self.start, "redraw");
            }
            Message::Expire(token) => {
                if self.phase == Phase::Preview
                    && token == self.expiry
                    && !self.pin
                    && !self.hovering
                {
                    if self.busy {
                        return delay(Duration::from_millis(250), Message::Expire(token));
                    }
                    return self.close();
                }
            }
            Message::Hover(inside) => {
                if self.phase == Phase::Preview {
                    self.hovering = inside;
                    self.expiry += 1;
                    if inside {
                        self.remaining = self.remaining.saturating_sub(self.expiry_start.elapsed());
                    } else {
                        self.expiry_start = Instant::now();
                        if !self.pin {
                            return delay(self.remaining, Message::Expire(self.expiry));
                        }
                    }
                }
            }
            Message::ScrollFrame(r) => {
                self.busy = false;
                match r {
                    Ok(im) => {
                        if let Some(s) = &mut self.stitcher {
                            match s.append(im) {
                                Ok(changed) => {
                                    self.status = if changed {
                                        format!("{} pixels tall", s.image.height())
                                    } else {
                                        "Page has not moved".into()
                                    }
                                }
                                Err(e) => self.status = e.to_string(),
                            }
                        } else {
                            self.stitcher = Some(capture::Stitcher::new(im));
                            self.status = "Scroll the page, then Add frame".into()
                        }
                    }
                    Err(e) => self.status = e,
                }
            }
            Message::AutoEnded(r) => {
                self.busy = false;
                match r {
                    Ok(doc) => {
                        self.capture.document = doc;
                        self.phase = Phase::Edit;
                        return self.resize().chain(self.render());
                    }
                    Err(e) => self.status = e,
                }
            }
            Message::Smoke => {
                let before = self.doc().render();
                self.doc_mut().checkpoint();
                let layer = self.layer(
                    vec![Point::new(50.0, 50.0), Point::new(140.0, 100.0)],
                    String::new(),
                );
                self.doc_mut().state.layers.push(layer);
                self.doc_mut().undo();
                assert_eq!(before.unwrap(), self.doc().render().unwrap());
                self.doc_mut().redo();
                assert!(!self.doc().state.layers.is_empty());
                eprintln!("dusky-snap Rust UI smoke: render, arrow, undo, redo passed");
                return iced::exit();
            }
            _ => {}
        }
        Task::none()
    }
    fn layer(&self, points: Vec<Point>, value: String) -> Layer {
        let c = theme::hex(&self.color).unwrap_or(Color::from_rgb8(247, 118, 142));
        Layer {
            tool: self.tool,
            points,
            color: [c.r as f64, c.g as f64, c.b as f64, 1.0],
            width: self.width,
            filled: self.filled,
            text: value,
            font: self.font.clone(),
            text_style: self.text_style,
        }
    }
    fn patch_selected(&mut self) -> Task<Message> {
        if self.busy {
            return Task::none();
        }
        if let Some(i) = self.selected {
            let c = theme::hex(&self.color).unwrap_or(Color::WHITE);
            let (width, filled) = (self.width, self.filled);
            self.doc_mut().checkpoint();
            let l = &mut self.doc_mut().state.layers[i];
            l.width = width;
            l.filled = filled;
            l.color = [c.r as f64, c.g as f64, c.b as f64, 1.0];
            return self.render();
        }
        Task::none()
    }
    fn pointer(&mut self, motion: Motion, p: Point) -> Task<Message> {
        if self.busy || self.text_anchor.is_some() || self.save_path.is_some() {
            return Task::none();
        }
        self.hover = Some(p);
        match motion {
            Motion::PanDown => {
                self.gesture = Some(Gesture {
                    start: p,
                    last: p,
                    points: vec![],
                    original: None,
                    resize: None,
                });
                return Task::none();
            }
            Motion::PanMove => {
                if let Some(g) = &mut self.gesture {
                    self.pan += Vector::new((p.x - g.last.x) as f32, (p.y - g.last.y) as f32);
                    g.last = p;
                }
                return Task::none();
            }
            Motion::PanUp => {
                self.gesture = None;
                return Task::none();
            }
            _ => {}
        }
        if matches!(motion, Motion::Down) {
            if self.phase == Phase::Pick {
                self.picker_controls = false;
                self.history = false;
            }
            if self.phase == Phase::Edit
                && !self.doc().state.grow
                && !self.doc().canvas().contains(p)
            {
                return Task::none();
            }
            let mut g = Gesture {
                start: p,
                last: p,
                points: vec![p],
                original: None,
                resize: None,
            };
            if self.phase == Phase::Edit && self.tool == Tool::Select {
                self.selected = self
                    .doc()
                    .state
                    .layers
                    .iter()
                    .rposition(|l| l.bounds().contains(p));
                if let Some(i) = self.selected {
                    let l = self.doc().state.layers[i].clone();
                    g.resize = l
                        .points
                        .iter()
                        .enumerate()
                        .filter(|(j, _)| *j == 0 || *j + 1 == l.points.len())
                        .find(|(_, q)| (p.x - q.x).hypot(p.y - q.y) < 12.0 / self.zoom as f64)
                        .map(|(j, _)| j);
                    self.width = l.width;
                    self.filled = l.filled;
                    self.color = format!(
                        "#{:02x}{:02x}{:02x}",
                        (l.color[0] * 255.0) as u8,
                        (l.color[1] * 255.0) as u8,
                        (l.color[2] * 255.0) as u8
                    );
                    self.doc_mut().checkpoint();
                    g.original = Some(l);
                }
            }
            if self.phase == Phase::Edit && self.tool == Tool::Text {
                self.text_anchor = Some(p);
                self.text_draft.clear();
                return Task::none();
            }
            if self.phase == Phase::Edit && self.tool == Tool::Eyedropper {
                if let Ok(im) = self.doc().composed_source() {
                    let x = p.x.clamp(0.0, im.width().saturating_sub(1) as f64) as u32;
                    let y = p.y.clamp(0.0, im.height().saturating_sub(1) as f64) as u32;
                    let c = im.get_pixel(x, y);
                    self.color = format!("#{:02x}{:02x}{:02x}", c[0], c[1], c[2]);
                }
                return Task::none();
            }
            self.gesture = Some(g);
        } else if matches!(motion, Motion::Move) {
            if let Some(g) = &mut self.gesture {
                g.last = p;
                if matches!(self.tool, Tool::Pen | Tool::Highlight)
                    && g.points.len() < 2048
                    && g.points
                        .last()
                        .is_none_or(|q| (p.x - q.x).hypot(p.y - q.y) > 0.5)
                {
                    g.points.push(p);
                }
            }
            if self.phase == Phase::Edit
                && self.tool == Tool::Select
                && let (Some(i), Some(g)) = (self.selected, self.gesture.as_ref())
                && let Some(mut l) = g.original.clone()
            {
                if let Some(j) = g.resize {
                    l.points[j] = p;
                } else {
                    l.translate(p.x - g.start.x, p.y - g.start.y);
                }
                self.doc_mut().state.layers[i] = l;
                self.doc_mut().revision = self.doc().revision.wrapping_add(1);
                return self.render();
            }
        } else if matches!(motion, Motion::Up) {
            let Some(mut g) = self.gesture.take() else {
                return Task::none();
            };
            g.last = p;
            let rect = Rect::between(g.start, p).pixel_bounds();
            if self.phase == Phase::Pick {
                let bounds = self.doc().state.crop;
                let window = if rect.w < 4.0 && rect.h < 4.0 {
                    self.capture
                        .windows
                        .iter()
                        .find(|w| w.rect.contains(p))
                        .cloned()
                } else {
                    None
                };
                let rect = if rect.w < 4.0 && rect.h < 4.0 {
                    window.as_ref().map_or(bounds, |w| w.rect)
                } else {
                    rect.intersect(bounds)
                };
                if rect.w < 1.0 || rect.h < 1.0 {
                    return Task::none();
                }
                if self.options.mode == "scroll" {
                    self.doc_mut().checkpoint();
                    self.doc_mut().state.crop = rect;
                    self.scroll_origin = Some(rect);
                    self.phase = Phase::Scroll;
                    self.status = "Scroll the page, then Add frame; or start Auto".into();
                    return self.resize().chain(self.scroll_frame());
                }
                let mut doc = self.doc().clone();
                doc.checkpoint();
                doc.state.crop = rect;
                let editor = self.options.editor;
                let cursor = self.options.cursor;
                self.busy = true;
                if self.options.editor {
                    self.phase = Phase::Preparing;
                }
                return job(
                    move || {
                        if let Some(window) = window
                            && let Some(image) = capture::window(&window.identifier, cursor)?
                        {
                            doc = Document::new(image);
                        }
                        let image = if editor {
                            Some(owned_handle(doc.render()?))
                        } else {
                            None
                        };
                        Ok((doc, image))
                    },
                    Message::SelectionReady,
                );
            }
            match self.tool {
                Tool::Select => {
                    if let (Some(i), Some(mut l)) = (self.selected, g.original) {
                        if let Some(j) = g.resize {
                            l.points[j] = p;
                        } else {
                            l.translate(p.x - g.start.x, p.y - g.start.y);
                        }
                        self.doc_mut().state.layers[i] = l;
                    } else {
                        return Task::none();
                    }
                }
                Tool::Crop => {
                    let (w, h) = self.doc().composed_dimensions();
                    let r = rect.intersect(Rect {
                        x: 0.0,
                        y: 0.0,
                        w: w as f64,
                        h: h as f64,
                    });
                    if r.w < 1.0 || r.h < 1.0 {
                        return Task::none();
                    }
                    self.doc_mut().checkpoint();
                    self.doc_mut().state.crop = r;
                    self.zoom = 1.0;
                    self.pan = Vector::ZERO;
                }
                Tool::Cut | Tool::Insert => {
                    let insert = self.tool == Tool::Insert;
                    self.doc_mut().apply_band(g.start, p, insert);
                }
                Tool::Ocr => {
                    let mut doc = self.doc().clone();
                    if rect.w > 2.0 && rect.h > 2.0 {
                        doc.state.crop = rect.intersect(doc.state.crop);
                    }
                    let config = self.config.clone();
                    self.busy = true;
                    return job(move || output::ocr(&doc, &config), Message::Ocr);
                }
                Tool::Text | Tool::Eyedropper => return Task::none(),
                _ => {
                    let points = if matches!(self.tool, Tool::Pen | Tool::Highlight) {
                        g.points.push(p);
                        g.points
                    } else {
                        vec![g.start, p]
                    };
                    let value = if self.tool == Tool::Marker {
                        (self
                            .doc()
                            .state
                            .layers
                            .iter()
                            .filter(|l| l.tool == Tool::Marker)
                            .count()
                            + 1)
                        .to_string()
                    } else {
                        String::new()
                    };
                    let l = self.layer(points, value);
                    self.doc_mut().checkpoint();
                    self.doc_mut().state.layers.push(l);
                }
            }
            return self.render();
        }
        Task::none()
    }
    fn scroll_frame(&mut self) -> Task<Message> {
        let Some(crop) = self.scroll_origin else {
            return Task::none();
        };
        self.busy = true;
        let monitor = self.capture.monitor.clone();
        job(
            move || {
                let _park = ParkedWindow::new(&monitor, crop)?;
                capture::frame(&monitor, crop, false)
            },
            Message::ScrollFrame,
        )
    }
    fn scroll_auto(&mut self) -> Task<Message> {
        let Some(crop) = self.scroll_origin else {
            return Task::none();
        };
        self.busy = true;
        self.scroll_stop = Arc::new(AtomicBool::new(false));
        let stop = self.scroll_stop.clone();
        let monitor = self.capture.monitor.clone();
        let initial = self.stitcher.take();
        self.status = "Auto capture · Stop, or relaunch Dusky Snap".into();
        job(
            move || {
                let _park = ParkedWindow::new(&monitor, crop)?;
                let mut stitch = if let Some(s) = initial {
                    s
                } else {
                    capture::Stitcher::new(capture::frame(&monitor, crop, false)?)
                };
                for _ in 0..150 {
                    if stop.load(Ordering::Relaxed) {
                        break;
                    }
                    crate::input::scroll(&monitor, crop, true)?;
                    std::thread::sleep(Duration::from_millis(450));
                    let next = capture::frame(&monitor, crop, false)?;
                    if !stitch.append(next)? {
                        break;
                    }
                }
                Ok(Document::new(stitch.image))
            },
            Message::AutoEnded,
        )
    }
    fn view(&self) -> Element<'_, Message> {
        if self.phase == Phase::Preparing {
            // Reveal only after both cropped pixels and the final configure
            // exist. Never draw the desktop texture inside the editor layout.
            return widget::Space::new()
                .width(Length::Fill)
                .height(Length::Fill)
                .into();
        }
        if matches!(self.phase, Phase::Pick | Phase::Edit) {
            return widget::responsive(|size| self.overlay(size)).into();
        }
        let header = row![
            mouse_area(
                container(
                    text(if self.pin {
                        "Pinned capture"
                    } else {
                        "Dusky Snap"
                    })
                    .size(13)
                )
                .width(Length::Fill)
                .padding(6)
            )
            .on_press(Message::Drag),
            icon_button(
                "close",
                "Close · Esc",
                Message::Act(Action::Close),
                false,
                self.theme.palette().text
            )
        ]
        .align_y(iced::Alignment::Center)
        .spacing(12);
        if self.phase == Phase::Scroll {
            return container(
                column![
                    header,
                    text("Scrolling capture").size(22),
                    text("Scroll the page, then add a frame, or start Auto.").size(13),
                    row![
                        button("Add frame").on_press(Message::Act(Action::ScrollFrame)),
                        button("Auto").on_press(Message::Act(Action::ScrollAuto)),
                        button("Stop").on_press(Message::Act(Action::ScrollStop)),
                        button("Edit result").on_press(Message::Act(Action::ScrollDone))
                    ]
                    .spacing(4),
                    text(&self.status).size(12)
                ]
                .spacing(12),
            )
            .padding(16)
            .width(Length::Fill)
            .height(Length::Fill)
            .style(theme::panel)
            .into();
        }
        mouse_area(widget::responsive(|size| self.preview(size)))
            .on_enter(Message::Hover(true))
            .on_exit(Message::Hover(false))
            .into()
    }
    fn preview(&self, size: Size) -> Element<'_, Message> {
        let width = (size.width - 24.0).max(1.0);
        let height = (size.height - 92.0).max(1.0);
        let scale = (width / self.image_size.0 as f32)
            .min(height / self.image_size.1 as f32)
            .min(1.0);
        let w = self.image_size.0 as f32 * scale;
        let h = self.image_size.1 as f32 * scale;
        let ink = self.theme.palette().text;
        let actions = row![
            icon_button(
                "pin",
                if self.pin { "Unpin · P" } else { "Pin · P" },
                Message::Act(Action::Pin),
                self.pin,
                ink
            ),
            icon_button(
                "pen",
                "Annotate · E",
                Message::Act(Action::Edit),
                false,
                ink
            ),
            icon_button(
                "copy",
                "Copy · Ctrl+C",
                Message::Act(Action::Copy),
                false,
                ink
            ),
            icon_button(
                "save",
                "Save · Ctrl+S",
                Message::Act(Action::Save),
                false,
                ink
            ),
            icon_button(
                "path",
                "Copy file path",
                Message::Act(Action::Path),
                false,
                ink
            ),
            icon_button(
                "folder",
                "Open containing folder",
                Message::Act(Action::Folder),
                false,
                ink
            ),
            icon_button(
                "close",
                "Close · Esc",
                Message::Act(Action::Close),
                false,
                ink
            )
        ]
        .spacing(2)
        .align_y(iced::Alignment::Center);
        let image = mouse_area(
            picture(self.image.clone())
                .width(w)
                .height(h)
                .content_fit(iced::ContentFit::Contain),
        )
        .on_press(Message::Drag);
        let bar_width = width.min(270.0);
        widget::stack![
            widget::pin(image).position(iced::Point::new(
                (size.width - w) / 2.0,
                76.0 + (height - h) / 2.0
            )),
            widget::pin(card(horizontal(actions)).width(bar_width))
                .position(iced::Point::new((size.width - bar_width) / 2.0, 10.0)),
            widget::pin(container(text(&self.status).size(11)).height(14).clip(true))
                .position(iced::Point::new(12.0, size.height - 14.0))
        ]
        .into()
    }
    fn overlay(&self, size: Size) -> Element<'_, Message> {
        if self.phase == Phase::Edit {
            return self.editor(size);
        }
        let view = Surface { app: self }.view(size);
        // Each stack child is a separate renderer layer. In particular, vector
        // guides must be above image batches on both WGPU and Tiny Skia.
        let mut stack = widget::Stack::new()
            .width(Length::Fill)
            .height(Length::Fill);
        // Reuse the same texture to keep the frozen desktop behind a zoomed,
        // panned or letterboxed picker. The normal fullscreen path draws once.
        if view.rect != Rectangle::with_size(size) {
            stack = stack.push(
                picture(self.image.clone())
                    .width(size.width)
                    .height(size.height)
                    .content_fit(iced::ContentFit::Fill),
            );
        }
        stack = stack.push(
            widget::pin(
                picture(self.image.clone())
                    .width(view.rect.width)
                    .height(view.rect.height)
                    .content_fit(iced::ContentFit::Fill),
            )
            .position(iced::Point::new(view.rect.x, view.rect.y)),
        );
        stack = stack.push(
            Canvas::new(Surface { app: self })
                .width(Length::Fill)
                .height(Length::Fill),
        );
        if self.phase == Phase::Pick {
            if !self.picker_controls || self.gesture.is_some() {
                return stack.into();
            }
            let controls = row![
                button("Region").on_press(Message::Mode("region")),
                button("Window").on_press(Message::Mode("window")),
                button("Scroll").on_press(Message::Mode("scroll")),
                checkbox(self.options.editor)
                    .label("Annotate")
                    .on_toggle(Message::Annotate),
                button(text("Recents").size(12)).on_press(Message::History),
                text("Tab to hide").size(11)
            ]
            .spacing(8)
            .align_y(iced::Alignment::Center)
            .wrap();
            let width = (size.width - 32.0).min(640.0);
            let x = (size.width - width) / 2.0;
            stack = stack
                .push(widget::pin(card(controls).width(width)).position(iced::Point::new(x, 16.0)));
            if self.history {
                let mut shelf = column![text("Recent captures").size(14)].spacing(8);
                for (i, (path, im)) in self.recents.iter().enumerate() {
                    shelf = shelf.push(
                        button(
                            row![
                                picture(im.clone())
                                    .width(84)
                                    .height(48)
                                    .content_fit(iced::ContentFit::Contain)
                                    .border_radius(6),
                                text(format!("Capture {}", i + 1)).size(12)
                            ]
                            .spacing(12)
                            .align_y(iced::Alignment::Center),
                        )
                        .on_press(Message::Recent(path.clone())),
                    );
                }
                if self.recents.is_empty() {
                    shelf = shelf.push(text("No captures yet").size(12));
                }
                stack = stack
                    .push(widget::pin(card(shelf).width(280)).position(iced::Point::new(x, 100.0)));
            }
            return stack.into();
        }
        stack.into()
    }
    fn editor(&self, size: Size) -> Element<'_, Message> {
        let ink = self.theme.palette().text;
        let mut canvas_size =
            Size::new((size.width - 24.0).max(1.0), (size.height - 170.0).max(1.0));
        // Keep spare window height outside the image-and-toolbar group.
        let fit = (canvas_size.width / self.image_size.0 as f32)
            .min(canvas_size.height / self.image_size.1 as f32)
            .min(1.0);
        canvas_size.height = self.image_size.1 as f32 * fit;
        let view = Surface { app: self }.view(canvas_size);
        let image_canvas = widget::stack![
            widget::pin(
                picture(self.image.clone())
                    .width(view.rect.width)
                    .height(view.rect.height)
                    .content_fit(iced::ContentFit::Fill)
            )
            .position(iced::Point::new(view.rect.x, view.rect.y)),
            Canvas::new(Surface { app: self })
                .width(Length::Fill)
                .height(Length::Fill)
        ]
        .width(Length::Fill)
        .height(canvas_size.height);
        let tools = [
            (Tool::Select, "select", "Select / move · V"),
            (Tool::Arrow, "arrow", "Arrow · A"),
            (Tool::DoubleArrow, "double", "Double arrow"),
            (Tool::Line, "line", "Line · L"),
            (Tool::Curve, "curve", "Curve"),
            (Tool::Pen, "pen", "Freehand · D"),
            (Tool::Highlight, "highlight", "Highlighter · H"),
            (Tool::Rectangle, "box", "Rectangle · R"),
            (Tool::Ellipse, "ellipse", "Ellipse · E"),
            (Tool::Marker, "number", "Number · N"),
            (Tool::Text, "text", "Text · T"),
            (Tool::Redact, "redact", "Redact · X"),
            (Tool::Blur, "blur", "Blur · drag to soften a region"),
            (Tool::Pixelate, "pixelate", "Pixelate"),
            (Tool::Spotlight, "spotlight", "Spotlight"),
            (Tool::Crop, "crop", "Crop · C"),
            (Tool::Cut, "cut", "Remove a horizontal strip"),
            (Tool::Insert, "insert", "Insert a horizontal strip"),
            (Tool::Eyedropper, "dropper", "Pick color"),
            (Tool::Ocr, "ocr", "Read text in a region · O"),
        ];
        let bar_width = (size.width - 24.0).max(1.0);
        let mut toolbar = widget::Row::new()
            .spacing(2)
            .align_y(iced::Alignment::Center);
        for (tool, icon, label) in tools {
            toolbar = toolbar.push(icon_button(
                icon,
                label,
                Message::Tool(tool),
                self.tool == tool,
                if self.tool == tool {
                    self.theme.palette().primary
                } else {
                    ink
                },
            ));
        }
        toolbar = toolbar.push(icon_button(
            "undo",
            "Undo · Ctrl+Z",
            Message::Act(Action::Undo),
            false,
            ink,
        ));
        toolbar = toolbar.push(icon_button(
            "redo",
            "Redo · Ctrl+Y",
            Message::Act(Action::Redo),
            false,
            ink,
        ));
        toolbar = toolbar.push(icon_button(
            "layout",
            "Canvas and object options",
            Message::More,
            self.more,
            ink,
        ));
        // Reserve the three 34px output buttons, row gaps, and card padding.
        let top = row![
            container(horizontal(toolbar)).max_width((bar_width - 128.0).max(1.0)),
            icon_button(
                "copy",
                "Copy and close · Ctrl+C / right-click",
                Message::Act(Action::Copy),
                false,
                self.theme.palette().primary
            ),
            icon_button(
                "save",
                "Save and close · Ctrl+S",
                Message::Act(Action::Save),
                false,
                ink
            ),
            icon_button(
                "close",
                "Close · Esc",
                Message::Act(Action::Close),
                false,
                ink
            )
        ]
        .spacing(2)
        .align_y(iced::Alignment::Center);
        let bar_height = 54.0;
        let x = 12.0;
        let y = ((size.height - canvas_size.height - 170.0) / 2.0).max(0.0) + 8.0;
        let top = container(compact_card(top)).center_x(Length::Fill);
        let mut colors = widget::Row::new()
            .spacing(4)
            .align_y(iced::Alignment::Center);
        for hex in &self.config.colors {
            let c = theme::hex(hex).unwrap_or(Color::WHITE);
            let selected = self.color.eq_ignore_ascii_case(hex);
            colors = colors.push(
                widget::button(container(widget::Space::new().width(18).height(18)).style(
                    move |_| container::Style {
                        background: Some(c.into()),
                        border: iced::Border {
                            radius: 20.0.into(),
                            width: if selected { 2.0 } else { 0.0 },
                            color: ink,
                        },
                        ..Default::default()
                    },
                ))
                .padding(5)
                .style(move |t, s| theme::control(t, s, selected))
                .on_press(Message::Color(hex.clone())),
            );
        }
        let size_label = if self.tool == Tool::Blur {
            "Blur radius"
        } else if self.tool == Tool::Pixelate {
            "Block size"
        } else if self.tool == Tool::Text {
            "Text size"
        } else {
            "Stroke"
        };
        let controls = row![
            colors,
            text_input("#rrggbb", &self.color)
                .on_input(Message::Color)
                .style(theme::input)
                .width(92)
                .size(12)
                .padding(7),
            text(size_label).size(12),
            slider(1.0..=80.0, self.width, Message::Width)
                .style(theme::slider)
                .width(100),
            text(format!(
                "{} px",
                if self.tool == Tool::Text {
                    (self.width * 5.0 + 12.0) as u32
                } else if self.tool == Tool::Pixelate {
                    (self.width * 4.0).max(8.0) as u32
                } else {
                    self.width as u32
                }
            ))
            .size(12),
            checkbox(self.filled)
                .label("Fill")
                .on_toggle(Message::Filled),
            icon_button(
                "ocr",
                "Read all text · O",
                Message::Act(Action::Ocr),
                false,
                ink
            ),
            icon_button(
                "pin",
                "Pin this capture · P",
                Message::Act(Action::Pin),
                false,
                ink
            ),
            icon_button(
                "saveas",
                "Save as… · Ctrl+Shift+S",
                Message::Act(Action::SaveAs),
                false,
                ink
            )
        ]
        .spacing(10)
        .align_y(iced::Alignment::Center);
        let bottom = container(compact_card(horizontal(controls))).center_x(Length::Fill);
        let status = container(text(&self.status).size(11))
            .height(14)
            .width(Length::Fill)
            .clip(true);
        let body = column![top, image_canvas, bottom, status]
            .spacing(8)
            .padding(12)
            .width(Length::Fill)
            .height(Length::Shrink);
        let mut stack = widget::stack![container(body).center_y(Length::Fill)]
            .width(Length::Fill)
            .height(Length::Fill);
        if self.more {
            let extras = row![
                button("Backdrop").on_press(Message::Act(Action::Backdrop)),
                button(if self.doc().state.grow {
                    "Fixed canvas"
                } else {
                    "Grow canvas"
                })
                .on_press(Message::Act(Action::Grow)),
                button("Shadow").on_press(Message::Act(Action::Shadow)),
                button("Raise").on_press(Message::Act(Action::Raise)),
                button("Lower").on_press(Message::Act(Action::Lower)),
                button("Smaller").on_press(Message::Act(Action::Smaller)),
                button("Larger").on_press(Message::Act(Action::Larger)),
                button("Delete").on_press(Message::Act(Action::Delete))
            ]
            .spacing(2)
            .wrap();
            stack = stack.push(
                widget::pin(card(extras).width(bar_width))
                    .position(iced::Point::new(x, y + bar_height + 8.0)),
            );
        }
        if self.text_anchor.is_some() || self.save_path.is_some() {
            let sheet: Element<'_, Message> = if let Some(path) = &self.save_path {
                column![
                    text("Save capture").size(20),
                    text_input("Destination PNG path", path)
                        .on_input(Message::SavePath)
                        .style(theme::input)
                        .on_submit(Message::SaveConfirm),
                    row![
                        button("Cancel").on_press(Message::CancelSheet),
                        button("Save PNG").on_press(Message::SaveConfirm)
                    ]
                    .spacing(8)
                ]
                .spacing(16)
                .into()
            } else {
                column![
                    text("Add text").size(20),
                    text_input("Your annotation…", &self.text_draft)
                        .on_input(Message::Text)
                        .style(theme::input)
                        .on_submit(Message::CommitText),
                    text_input("Font family", &self.font)
                        .on_input(Message::Font)
                        .style(theme::input),
                    row![
                        button("Plain").on_press(Message::TextStyle(0)),
                        button("Outline").on_press(Message::TextStyle(1)),
                        button("Label").on_press(Message::TextStyle(2)),
                        button("Cancel").on_press(Message::CancelSheet),
                        button("Add text").on_press(Message::CommitText)
                    ]
                    .spacing(4)
                ]
                .spacing(16)
                .into()
            };
            stack = stack.push(widget::opaque(
                container(card(sheet).width(520))
                    .width(Length::Fill)
                    .height(Length::Fill)
                    .center_x(Length::Fill)
                    .center_y(Length::Fill)
                    .style(|_| container::Style {
                        background: Some(Color::BLACK.scale_alpha(0.5).into()),
                        ..Default::default()
                    }),
            ));
        }
        stack.into()
    }
}
fn card<'a>(content: impl Into<Element<'a, Message>>) -> widget::Container<'a, Message> {
    container(widget::opaque(
        container(content)
            .padding(10)
            .width(Length::Fill)
            .style(theme::panel),
    ))
}
// Let Iced measure text and configured swatches instead of reserving empty
// toolbar space. Parent limits still bound the horizontal scroll viewport.
fn compact_card<'a>(content: impl Into<Element<'a, Message>>) -> widget::Container<'a, Message> {
    container(widget::opaque(
        container(content).padding(10).style(theme::panel),
    ))
}
fn horizontal<'a>(content: impl Into<Element<'a, Message>>) -> widget::Scrollable<'a, Message> {
    scrollable(content)
        .direction(widget::scrollable::Direction::Horizontal(
            widget::scrollable::Scrollbar::new()
                .width(0)
                .scroller_width(0),
        ))
        .height(34)
}
fn button<'a>(content: impl Into<Element<'a, Message>>) -> widget::Button<'a, Message> {
    widget::button(content)
        .padding([8, 12])
        .style(|t, s| theme::control(t, s, false))
}
fn icon_button<'a>(
    kind: &'static str,
    label: &'a str,
    message: Message,
    active: bool,
    color: Color,
) -> Element<'a, Message> {
    let b = widget::button(Canvas::new(Icon { kind, color }).width(22).height(22))
        .padding(6)
        .style(move |t, s| {
            let mut style = theme::control(t, s, active);
            if kind == "close" {
                style.border.radius = 30.0.into();
                style.background = Some(t.palette().text.scale_alpha(0.06).into());
                if matches!(s, widget::button::Status::Hovered) {
                    style.background = Some(t.palette().danger.scale_alpha(0.2).into());
                }
            }
            style
        })
        .on_press(message);
    widget::tooltip(
        b,
        container(text(label).size(12))
            .padding(8)
            .style(theme::panel),
        widget::tooltip::Position::Bottom,
    )
    .delay(Duration::from_millis(350))
    .into()
}
struct Icon {
    kind: &'static str,
    color: Color,
}
impl canvas::Program<Message> for Icon {
    type State = ();
    fn draw(
        &self,
        _: &(),
        renderer: &iced::Renderer,
        _: &Theme,
        bounds: Rectangle,
        _: mouse::Cursor,
    ) -> Vec<canvas::Geometry> {
        let mut f = canvas::Frame::new(renderer, bounds.size());
        let stroke = canvas::Stroke::default()
            .with_color(self.color)
            .with_width(1.6)
            .with_line_cap(canvas::LineCap::Round)
            .with_line_join(canvas::LineJoin::Round);
        let p = |x, y| iced::Point::new(x, y);
        let mut line = |pts: &[(f32, f32)]| {
            f.stroke(
                &canvas::Path::new(|b| {
                    b.move_to(p(pts[0].0, pts[0].1));
                    for &(x, y) in &pts[1..] {
                        b.line_to(p(x, y));
                    }
                }),
                stroke,
            )
        };
        match self.kind {
            "copy" => {
                line(&[(8., 7.), (18., 7.), (18., 18.), (8., 18.), (8., 7.)]);
                line(&[(14., 4.), (4., 4.), (4., 14.)]);
            }
            "saveas" => {
                line(&[(4., 4.), (16., 4.), (18., 6.), (18., 10.)]);
                line(&[(4., 4.), (4., 18.), (10., 18.)]);
                line(&[(8., 4.), (8., 9.), (14., 9.), (14., 4.)]);
                line(&[(13., 16.), (21., 16.)]);
                line(&[(17., 12.), (17., 20.)]);
            }
            "save" => {
                line(&[
                    (4., 4.),
                    (16., 4.),
                    (19., 7.),
                    (19., 18.),
                    (4., 18.),
                    (4., 4.),
                ]);
                line(&[(8., 4.), (8., 9.), (15., 9.), (15., 4.)]);
                line(&[(8., 18.), (8., 13.), (15., 13.), (15., 18.)]);
            }
            "pin" => {
                line(&[
                    (8., 3.),
                    (16., 3.),
                    (15., 10.),
                    (18., 13.),
                    (5., 13.),
                    (8., 10.),
                    (8., 3.),
                ]);
                line(&[(11., 13.), (11., 20.)]);
            }
            "path" => {
                line(&[
                    (9., 6.),
                    (6., 3.),
                    (3., 6.),
                    (3., 10.),
                    (8., 15.),
                    (11., 15.),
                ]);
                line(&[
                    (11., 7.),
                    (14., 7.),
                    (19., 12.),
                    (19., 16.),
                    (16., 19.),
                    (13., 16.),
                ]);
                line(&[(7., 11.), (15., 11.)]);
            }
            "folder" => line(&[
                (3., 6.),
                (9., 6.),
                (11., 8.),
                (19., 8.),
                (19., 18.),
                (3., 18.),
                (3., 6.),
            ]),
            "close" => {
                line(&[(7., 7.), (15., 15.)]);
                line(&[(15., 7.), (7., 15.)]);
            }
            "arrow" | "double" => {
                line(&[(4., 18.), (18., 4.), (10., 4.)]);
                line(&[(18., 4.), (18., 12.)]);
                if self.kind == "double" {
                    line(&[(4., 10.), (4., 18.), (12., 18.)]);
                }
            }
            "line" => line(&[(5., 17.), (17., 5.)]),
            "select" => {
                line(&[(6., 3.), (17., 12.), (12., 13.), (10., 19.), (6., 3.)]);
            }
            "pen" | "highlight" => {
                line(&[
                    (4., 18.),
                    (6., 12.),
                    (15., 3.),
                    (19., 7.),
                    (10., 16.),
                    (4., 18.),
                ]);
                line(&[(6., 12.), (10., 16.)]);
                if self.kind == "highlight" {
                    line(&[(3., 21.), (19., 21.)]);
                }
            }
            "crop" => {
                line(&[(7., 3.), (7., 16.), (20., 16.)]);
                line(&[(3., 7.), (16., 7.), (16., 20.)]);
            }
            "box" | "redact" | "pixelate" => {
                line(&[(4., 4.), (18., 4.), (18., 18.), (4., 18.), (4., 4.)]);
                if self.kind == "pixelate" {
                    line(&[(11., 4.), (11., 18.)]);
                    line(&[(4., 11.), (18., 11.)]);
                }
                if self.kind == "redact" {
                    line(&[(7., 8.), (15., 8.)]);
                    line(&[(7., 11.), (15., 11.)]);
                    line(&[(7., 14.), (15., 14.)]);
                }
            }
            "text" => {
                line(&[(4., 5.), (18., 5.)]);
                line(&[(11., 5.), (11., 18.)]);
                line(&[(7., 18.), (15., 18.)]);
            }
            "cut" | "insert" => {
                line(&[(4., 4.), (18., 4.)]);
                line(&[(4., 18.), (18., 18.)]);
                line(&[(7., 11.), (15., 11.)]);
                if self.kind == "insert" {
                    line(&[(11., 7.), (11., 15.)]);
                }
            }
            "undo" | "redo" => {
                let flip = if self.kind == "redo" { -1. } else { 1. };
                let x = |v: f32| 11. + (v - 11.) * flip;
                line(&[(x(8.), 5.), (x(4.), 9.), (x(8.), 13.)]);
                line(&[(x(4.), 9.), (x(15.), 9.), (x(18.), 12.), (x(18.), 16.)]);
            }
            "layout" => {
                line(&[(4., 5.), (18., 5.)]);
                line(&[(4., 11.), (18., 11.)]);
                line(&[(4., 17.), (18., 17.)]);
                line(&[(8., 3.), (8., 7.)]);
                line(&[(14., 9.), (14., 13.)]);
                line(&[(8., 15.), (8., 19.)]);
            }
            "dropper" => {
                line(&[
                    (5., 14.),
                    (14., 5.),
                    (18., 9.),
                    (9., 18.),
                    (4., 19.),
                    (5., 14.),
                ]);
                line(&[(12., 4.), (19., 11.)]);
            }
            "ocr" => {
                line(&[(4., 8.), (4., 4.), (8., 4.)]);
                line(&[(14., 4.), (18., 4.), (18., 8.)]);
                line(&[(18., 14.), (18., 18.), (14., 18.)]);
                line(&[(8., 18.), (4., 18.), (4., 14.)]);
                line(&[(8., 9.), (14., 9.)]);
                line(&[(8., 13.), (14., 13.)]);
            }
            "curve" => f.stroke(
                &canvas::Path::new(|b| {
                    b.move_to(p(4., 17.));
                    b.bezier_curve_to(p(2., -2.), p(20., 24.), p(18., 5.));
                }),
                stroke,
            ),
            "ellipse" | "spotlight" | "blur" | "number" => {
                f.stroke(&canvas::Path::circle(p(11., 11.), 7.), stroke);
                if self.kind == "spotlight" {
                    f.stroke(&canvas::Path::circle(p(11., 11.), 3.), stroke);
                }
                if self.kind == "blur" {
                    for (x, y) in [(8., 8.), (14., 8.), (11., 11.), (8., 14.), (14., 14.)] {
                        f.fill(&canvas::Path::circle(p(x, y), 1.), self.color);
                    }
                }
                if self.kind == "number" {
                    f.fill_text(canvas::Text {
                        content: "1".into(),
                        position: p(11., 11.),
                        align_x: iced::alignment::Horizontal::Center.into(),
                        align_y: iced::alignment::Vertical::Center,
                        size: 14.0.into(),
                        color: self.color,
                        ..Default::default()
                    });
                }
            }
            _ => {}
        }
        vec![f.into_geometry()]
    }
}
fn im_dimensions(doc: &Document) -> (u32, u32) {
    let r = doc.canvas();
    let pad = if doc.state.backdrop > 0 { 64 } else { 0 };
    (r.w as u32 + pad, r.h as u32 + pad)
}
fn own_window() -> Result<serde_json::Value> {
    let clients: serde_json::Value = serde_json::from_slice(&capture::command(
        "hyprctl",
        &["-j", "clients"],
        None,
        Duration::from_secs(2),
    )?)?;
    clients
        .as_array()
        .and_then(|cs| {
            cs.iter()
                .find(|c| c["pid"].as_u64() == Some(std::process::id() as u64))
        })
        .cloned()
        .ok_or_else(|| "Screenshot window disappeared".into())
}
fn position_picker(monitor: &Monitor) -> Result<()> {
    // Hyprland's new_float_force_onscreen can override the initial move rule.
    // Correct after mapping, when the borderless window's final extents exist.
    let script = format!(
        "hl.dsp.window.move({{window=\"pid:{}\",x={},y={},relative=false}})",
        std::process::id(),
        monitor.x,
        monitor.y,
    );
    capture::command(
        "hyprctl",
        &["dispatch", &script],
        None,
        Duration::from_secs(2),
    )?;
    Ok(())
}
fn resize_window(size: Size) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(2);
    let client = loop {
        let client = own_window()?;
        if client["fullscreen"].as_i64() == Some(0) {
            break client;
        }
        if Instant::now() >= deadline {
            return Err("Hyprland did not leave fullscreen".into());
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let address = client["address"].as_str().ok_or("Window has no address")?;
    let selector = serde_json::to_string(&format!("address:{address}"))?;
    let unpin = if client["pinned"].as_bool() == Some(true) {
        "hl.dispatch(hl.dsp.window.pin({window=w})); "
    } else {
        ""
    };
    let script = format!(
        "local w={selector}; {unpin}hl.dispatch(hl.dsp.window.resize({{window=w,x={},y={},relative=false}})); hl.dispatch(hl.dsp.window.center({{window=w}}))",
        size.width.round() as i32,
        size.height.round() as i32
    );
    capture::command("hyprctl", &["eval", &script], None, Duration::from_secs(2))?;
    Ok(())
}
struct ParkedWindow {
    address: String,
    x: i64,
    y: i64,
}
impl ParkedWindow {
    fn new(monitor: &Monitor, crop: Rect) -> Result<Self> {
        let client = own_window()?;
        let address = client["address"]
            .as_str()
            .ok_or("Window has no address")?
            .to_string();
        let x = client["at"][0].as_i64().ok_or("Missing window x")?;
        let y = client["at"][1].as_i64().ok_or("Missing window y")?;
        let width = client["size"][0].as_i64().ok_or("Missing window width")?;
        let monitors: Vec<Monitor> = serde_json::from_slice(&capture::command(
            "hyprctl",
            &["-j", "monitors"],
            None,
            Duration::from_secs(2),
        )?)?;
        let left = monitors
            .iter()
            .map(|m| m.x as i64)
            .min()
            .ok_or("No outputs")?;
        let height = client["size"][1].as_i64().ok_or("Missing window height")?;
        let (mw, mh) = monitor.logical_size();
        let [reserved_left, reserved_top, reserved_right, reserved_bottom] = monitor.reserved;
        let (pw, ph) = if monitor.transform % 2 == 1 {
            (monitor.height, monitor.width)
        } else {
            (monitor.width, monitor.height)
        };
        let cx = crop.x * mw as f64 / pw as f64;
        let cy = crop.y * mh as f64 / ph as f64;
        let right = (crop.x + crop.w) * mw as f64 / pw as f64;
        let bottom = (crop.y + crop.h) * mh as f64 / ph as f64;
        let middle_x = ((cx + right - width as f64) / 2.0).clamp(
            reserved_left as f64 + 8.0,
            (mw as f64 - reserved_right as f64 - width as f64 - 8.0)
                .max(reserved_left as f64 + 8.0),
        ) as i64;
        let middle_y = ((cy + bottom - height as f64) / 2.0).clamp(
            reserved_top as f64 + 8.0,
            (mh as f64 - reserved_bottom as f64 - height as f64 - 8.0)
                .max(reserved_top as f64 + 8.0),
        ) as i64;
        let position = if cy - reserved_top as f64 >= height as f64 + 16.0 {
            (
                monitor.x as i64 + middle_x,
                monitor.y as i64 + reserved_top as i64 + 8,
            )
        } else if mh as f64 - reserved_bottom as f64 - bottom >= height as f64 + 16.0 {
            (
                monitor.x as i64 + middle_x,
                monitor.y as i64 + bottom.ceil() as i64 + 8,
            )
        } else if cx - reserved_left as f64 >= width as f64 + 16.0 {
            (
                monitor.x as i64 + reserved_left as i64 + 8,
                monitor.y as i64 + middle_y,
            )
        } else if mw as f64 - reserved_right as f64 - right >= width as f64 + 16.0 {
            (
                monitor.x as i64 + right.ceil() as i64 + 8,
                monitor.y as i64 + middle_y,
            )
        } else {
            (left - width - 64, y)
        };
        let park = Self { address, x, y };
        park.move_to(position.0, position.1)?;
        // This is intentional pointer placement for capture; it settles focus
        // dimming before the first frame, as well as directing injected wheels.
        let (sx, sy) = monitor.logical_size();
        let (px, py) = if monitor.transform % 2 == 1 {
            (monitor.height, monitor.width)
        } else {
            (monitor.width, monitor.height)
        };
        let cx = monitor.x as f64 + (crop.x + crop.w / 2.0) * sx as f64 / px as f64;
        let cy = monitor.y as f64 + (crop.y + crop.h / 2.0) * sy as f64 / py as f64;
        let script = format!(
            "hl.dsp.cursor.move({{x={},y={}}})",
            cx.round() as i64,
            cy.round() as i64
        );
        capture::command(
            "hyprctl",
            &["dispatch", &script],
            None,
            Duration::from_secs(2),
        )?;
        std::thread::sleep(Duration::from_millis(250));
        Ok(park)
    }
    fn move_to(&self, x: i64, y: i64) -> Result<()> {
        let script = format!(
            "hl.dsp.window.move({{window={},x={x},y={y},relative=false}})",
            serde_json::to_string(&format!("address:{}", self.address))?
        );
        capture::command(
            "hyprctl",
            &["dispatch", &script],
            None,
            Duration::from_secs(2),
        )?;
        Ok(())
    }
}
impl Drop for ParkedWindow {
    fn drop(&mut self) {
        let _ = self.move_to(self.x, self.y);
    }
}
fn set_pinned(pin: bool) -> Result<()> {
    let clients: serde_json::Value = serde_json::from_slice(&capture::command(
        "hyprctl",
        &["-j", "clients"],
        None,
        Duration::from_secs(2),
    )?)?;
    let client = clients
        .as_array()
        .and_then(|cs| {
            cs.iter()
                .find(|c| c["pid"].as_u64() == Some(std::process::id() as u64))
        })
        .ok_or("Preview window disappeared")?;
    let is_pin = client["pinned"].as_bool().unwrap_or(false);
    if is_pin != pin {
        let address = client["address"].as_str().ok_or("Window has no address")?;
        let script = format!(
            "hl.dsp.window.pin({{ window = {} }})",
            serde_json::to_string(&format!("address:{address}"))?
        );
        capture::command(
            "hyprctl",
            &["dispatch", &script],
            None,
            Duration::from_secs(2),
        )?;
    }
    Ok(())
}
#[derive(Default)]
struct SurfaceState {
    left: bool,
    middle: bool,
    ctrl: bool,
    logo: bool,
    last: Option<iced::Point>,
}
struct Surface<'a> {
    app: &'a App,
}
#[derive(Clone, Copy)]
struct View {
    rect: Rectangle,
    scale: f32,
    origin: Point,
}
impl Surface<'_> {
    fn view(&self, size: Size) -> View {
        let app = self.app;
        let (w, h) = app.image_size;
        let picker = app.phase == Phase::Pick;
        let scale = (size.width / w as f32)
            .min(size.height / h as f32)
            .min(if picker { f32::MAX } else { 1.0 })
            * app.zoom;
        let rect = Rectangle {
            x: (size.width - w as f32 * scale) / 2.0 + app.pan.x,
            y: (size.height - h as f32 * scale) / 2.0 + app.pan.y,
            width: w as f32 * scale,
            height: h as f32 * scale,
        };
        let canvas = app.doc().canvas();
        let pad = if app.doc().state.backdrop > 0 {
            32.0
        } else {
            0.0
        };
        View {
            rect,
            scale,
            origin: if app.phase == Phase::Pick {
                Point::default()
            } else {
                Point::new(canvas.x - pad, canvas.y - pad)
            },
        }
    }
}
impl View {
    fn source(self, p: iced::Point) -> Point {
        Point::new(
            (p.x - self.rect.x) as f64 / self.scale as f64 + self.origin.x,
            (p.y - self.rect.y) as f64 / self.scale as f64 + self.origin.y,
        )
    }
    fn screen(self, p: Point) -> iced::Point {
        iced::Point::new(
            self.rect.x + (p.x - self.origin.x) as f32 * self.scale,
            self.rect.y + (p.y - self.origin.y) as f32 * self.scale,
        )
    }
    fn bounds(self, r: Rect) -> Rectangle {
        let p = self.screen(Point::new(r.x, r.y));
        Rectangle {
            x: p.x,
            y: p.y,
            width: r.w as f32 * self.scale,
            height: r.h as f32 * self.scale,
        }
    }
}
impl canvas::Program<Message> for Surface<'_> {
    type State = SurfaceState;
    fn update(
        &self,
        state: &mut SurfaceState,
        event: &canvas::Event,
        bounds: Rectangle,
        cursor: mouse::Cursor,
    ) -> Option<canvas::Action<Message>> {
        let position = (if let canvas::Event::Mouse(mouse::Event::CursorMoved { position }) = event
        {
            cursor.position().map(|_| *position)
        } else {
            cursor.position()
        })
        .map(|p| iced::Point::new(p.x - bounds.x, p.y - bounds.y))
        .or(state.last);
        if let canvas::Event::Keyboard(keyboard::Event::ModifiersChanged(mods)) = event {
            state.ctrl = mods.control();
            state.logo = mods.logo();
            return None;
        }
        let p = position?;
        let source = self.view(bounds.size()).source(p);
        let motion = match event {
            canvas::Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Right))
                if self.app.phase == Phase::Edit
                    && !state.logo
                    && cursor.is_over(bounds)
                    && self.app.text_anchor.is_none()
                    && self.app.save_path.is_none()
                    && self.app.doc().canvas().contains(source) =>
            {
                return Some(canvas::Action::publish(Message::Act(Action::Copy)).and_capture());
            }
            canvas::Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Left))
                if cursor.is_over(bounds) =>
            {
                state.left = true;
                Motion::Down
            }
            canvas::Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Left))
                if state.left =>
            {
                state.left = false;
                Motion::Up
            }
            canvas::Event::Mouse(mouse::Event::ButtonPressed(mouse::Button::Middle))
                if cursor.is_over(bounds) =>
            {
                state.middle = true;
                Motion::PanDown
            }
            canvas::Event::Mouse(mouse::Event::ButtonReleased(mouse::Button::Middle))
                if state.middle =>
            {
                state.middle = false;
                Motion::PanUp
            }
            canvas::Event::Mouse(mouse::Event::CursorMoved { .. }) if state.middle => {
                Motion::PanMove
            }
            canvas::Event::Mouse(mouse::Event::CursorMoved { .. })
                if state.left || cursor.is_over(bounds) =>
            {
                Motion::Move
            }
            canvas::Event::Mouse(mouse::Event::WheelScrolled { delta })
                if cursor.is_over(bounds) =>
            {
                let y = match delta {
                    mouse::ScrollDelta::Lines { y, .. } => *y,
                    mouse::ScrollDelta::Pixels { y, .. } => *y / 30.0,
                };
                return Some(canvas::Action::publish(Message::Wheel(y, state.ctrl)).and_capture());
            }
            _ => return None,
        };
        state.last = Some(p);
        let p = if matches!(motion, Motion::PanDown | Motion::PanMove | Motion::PanUp) {
            Point::new(p.x as f64, p.y as f64)
        } else {
            source
        };
        Some(canvas::Action::publish(Message::Pointer(motion, p)).and_capture())
    }
    fn draw(
        &self,
        _: &SurfaceState,
        renderer: &iced::Renderer,
        theme: &Theme,
        bounds: Rectangle,
        cursor: mouse::Cursor,
    ) -> Vec<canvas::Geometry> {
        let app = self.app;
        let view = self.view(bounds.size());
        let mut frame = canvas::Frame::new(renderer, bounds.size());
        let color = theme.palette().primary;
        let outline = |frame: &mut canvas::Frame, r: Rect| {
            let b = view.bounds(r);
            frame.stroke_rectangle(
                iced::Point::new(b.x, b.y),
                b.size(),
                canvas::Stroke::default().with_color(color).with_width(1.5),
            );
        };
        if app.phase == Phase::Pick {
            // Wayland's pointer-enter position is usable without a motion event.
            let hover = cursor
                .position_in(bounds)
                .map(|p| view.source(p))
                .or(app.hover);
            // Paint-only guidance never intercepts the capture surface. Hide it
            // while selecting, or when the pointer approaches its screen edge.
            if app.gesture.is_none()
                && !app.picker_controls
                && hover.is_none_or(|p| view.screen(p).y < bounds.height - 48.0)
            {
                frame.fill_text(canvas::Text {
                    content: "Drag a region · click a window · Tab options · Esc cancel".into(),
                    position: iced::Point::new(bounds.width / 2.0, bounds.height - 24.0),
                    align_x: iced::alignment::Horizontal::Center.into(),
                    color: Color::WHITE.scale_alpha(0.75),
                    size: 12.0.into(),
                    ..Default::default()
                });
            }
            if let Some(p) = hover {
                let p = view.screen(p);
                let stroke = canvas::Stroke::default()
                    .with_color(Color::WHITE.scale_alpha(0.55))
                    .with_width(1.0);
                frame.stroke(
                    &canvas::Path::line(
                        iced::Point::new(0.0, p.y),
                        iced::Point::new(bounds.width, p.y),
                    ),
                    stroke,
                );
                frame.stroke(
                    &canvas::Path::line(
                        iced::Point::new(p.x, 0.0),
                        iced::Point::new(p.x, bounds.height),
                    ),
                    stroke,
                );
            }
            let selection = app
                .gesture
                .as_ref()
                .map(|g| Rect::between(g.start, g.last))
                .or_else(|| {
                    hover.and_then(|p| {
                        app.capture
                            .windows
                            .iter()
                            .find(|w| w.rect.contains(p))
                            .map(|w| w.rect)
                    })
                });
            if let Some(r) = selection {
                let b = view.bounds(r);
                let dim = Color::from_rgba(0.0, 0.0, 0.0, 0.45);
                frame.fill_rectangle(
                    iced::Point::ORIGIN,
                    Size::new(bounds.width, b.y.max(0.0)),
                    dim,
                );
                frame.fill_rectangle(
                    iced::Point::new(0.0, b.y + b.height),
                    Size::new(bounds.width, (bounds.height - b.y - b.height).max(0.0)),
                    dim,
                );
                frame.fill_rectangle(
                    iced::Point::new(0.0, b.y),
                    Size::new(b.x.max(0.0), b.height),
                    dim,
                );
                frame.fill_rectangle(
                    iced::Point::new(b.x + b.width, b.y),
                    Size::new((bounds.width - b.x - b.width).max(0.0), b.height),
                    dim,
                );
                outline(&mut frame, r);
                for t in [1.0 / 3.0, 2.0 / 3.0] {
                    let stroke = canvas::Stroke::default()
                        .with_color(Color::WHITE.scale_alpha(0.3))
                        .with_width(1.0);
                    frame.stroke(
                        &canvas::Path::line(
                            iced::Point::new(b.x + b.width * t, b.y),
                            iced::Point::new(b.x + b.width * t, b.y + b.height),
                        ),
                        stroke,
                    );
                    frame.stroke(
                        &canvas::Path::line(
                            iced::Point::new(b.x, b.y + b.height * t),
                            iced::Point::new(b.x + b.width, b.y + b.height * t),
                        ),
                        stroke,
                    );
                }
                for (x, y) in [
                    (b.x, b.y),
                    (b.x + b.width, b.y),
                    (b.x, b.y + b.height),
                    (b.x + b.width, b.y + b.height),
                ] {
                    frame.fill(
                        &canvas::Path::circle(iced::Point::new(x, y), 3.0),
                        Color::WHITE,
                    );
                }
                let px = r.pixel_bounds();
                frame.fill_text(canvas::Text {
                    content: format!("{} × {}", px.w, px.h),
                    position: iced::Point::new(b.x + 6.0, (b.y - 20.0).max(20.0)),
                    color: Color::WHITE,
                    size: 14.0.into(),
                    ..Default::default()
                });
            }
        } else {
            frame.stroke_rectangle(
                iced::Point::new(view.rect.x - 1.0, view.rect.y - 1.0),
                Size::new(view.rect.width + 2.0, view.rect.height + 2.0),
                canvas::Stroke::default()
                    .with_color(color.scale_alpha(0.7))
                    .with_width(1.0),
            );
            if let Some(i) = app.selected.and_then(|i| app.doc().state.layers.get(i)) {
                let l = i;
                outline(&mut frame, l.bounds());
                for p in [l.points.first(), l.points.last()].into_iter().flatten() {
                    frame.fill(&canvas::Path::circle(view.screen(*p), 4.0), color);
                }
            }
            if let Some(g) = &app.gesture
                && app.tool != Tool::Select
            {
                let mut ink = theme::hex(&app.color).unwrap_or(color);
                if app.tool == Tool::Highlight {
                    ink.a = 0.3;
                }
                let a = view.screen(g.start);
                let b = view.screen(g.last);
                match app.tool {
                    Tool::Pen | Tool::Highlight => {
                        let path = canvas::Path::new(|p| {
                            p.move_to(a);
                            for point in &g.points {
                                p.line_to(view.screen(*point));
                            }
                            p.line_to(b);
                        });
                        frame.stroke(
                            &path,
                            canvas::Stroke::default()
                                .with_color(ink)
                                .with_width((app.width as f32 * view.scale).max(1.0)),
                        );
                    }
                    Tool::Arrow | Tool::DoubleArrow | Tool::Curve | Tool::Line => {
                        let path = canvas::Path::new(|p| {
                            p.move_to(a);
                            if app.tool == Tool::Curve {
                                let mid = iced::Point::new(
                                    (a.x + b.x) / 2.0 - (b.y - a.y) * 0.2,
                                    (a.y + b.y) / 2.0 + (b.x - a.x) * 0.2,
                                );
                                p.bezier_curve_to(mid, mid, b);
                            } else {
                                p.line_to(b);
                            }
                        });
                        frame.stroke(
                            &path,
                            canvas::Stroke::default()
                                .with_color(ink)
                                .with_width((app.width as f32 * view.scale).max(1.0)),
                        );
                        if matches!(app.tool, Tool::Arrow | Tool::DoubleArrow) {
                            let mut head = |tip: iced::Point, tail: iced::Point| {
                                let angle = (tip.y - tail.y).atan2(tip.x - tail.x);
                                let length = (app.width as f32 * 4.0).max(12.0) * view.scale;
                                frame.fill(
                                    &canvas::Path::new(|p| {
                                        p.move_to(tip);
                                        p.line_to(iced::Point::new(
                                            tip.x - length * (angle - 0.45).cos(),
                                            tip.y - length * (angle - 0.45).sin(),
                                        ));
                                        p.line_to(iced::Point::new(
                                            tip.x - length * (angle + 0.45).cos(),
                                            tip.y - length * (angle + 0.45).sin(),
                                        ));
                                        p.close();
                                    }),
                                    ink,
                                );
                            };
                            head(b, a);
                            if app.tool == Tool::DoubleArrow {
                                head(a, b);
                            }
                        }
                    }
                    Tool::Ellipse => {
                        let center = iced::Point::new((a.x + b.x) / 2.0, (a.y + b.y) / 2.0);
                        let ellipse = canvas::Path::new(|p| {
                            p.ellipse(canvas::path::arc::Elliptical {
                                center,
                                radii: Vector::new(
                                    (a.x - b.x).abs() / 2.0,
                                    (a.y - b.y).abs() / 2.0,
                                ),
                                rotation: iced::Radians(0.0),
                                start_angle: iced::Radians(0.0),
                                end_angle: iced::Radians(std::f32::consts::TAU),
                            })
                        });
                        if app.filled {
                            frame.fill(&ellipse, ink);
                        } else {
                            frame.stroke(
                                &ellipse,
                                canvas::Stroke::default()
                                    .with_color(ink)
                                    .with_width(app.width as f32 * view.scale),
                            );
                        }
                    }
                    Tool::Rectangle => {
                        let r = view.bounds(Rect::between(g.start, g.last));
                        if app.filled {
                            frame.fill_rectangle(iced::Point::new(r.x, r.y), r.size(), ink);
                        } else {
                            frame.stroke_rectangle(
                                iced::Point::new(r.x, r.y),
                                r.size(),
                                canvas::Stroke::default()
                                    .with_color(ink)
                                    .with_width(app.width as f32 * view.scale),
                            );
                        }
                    }
                    Tool::Redact | Tool::Blur | Tool::Pixelate => {
                        let r = view.bounds(Rect::between(g.start, g.last));
                        frame.fill_rectangle(
                            iced::Point::new(r.x, r.y),
                            r.size(),
                            if app.tool == Tool::Redact {
                                Color::BLACK
                            } else {
                                color.scale_alpha(0.22)
                            },
                        );
                        outline(&mut frame, Rect::between(g.start, g.last));
                    }
                    _ => outline(&mut frame, Rect::between(g.start, g.last)),
                }
            }
        }
        vec![frame.into_geometry()]
    }
    fn mouse_interaction(
        &self,
        _: &SurfaceState,
        bounds: Rectangle,
        cursor: mouse::Cursor,
    ) -> mouse::Interaction {
        if cursor.is_over(bounds) {
            if self.app.tool == Tool::Select && self.app.phase == Phase::Edit {
                mouse::Interaction::Grab
            } else {
                mouse::Interaction::Crosshair
            }
        } else {
            mouse::Interaction::default()
        }
    }
}
fn platform_events() -> impl iced::futures::Stream<Item = Platform> {
    let (tx, rx) = iced::futures::channel::mpsc::unbounded();
    std::thread::Builder::new()
        .name("snap-events".into())
        .spawn(move || {
            let fd = unsafe { libc::inotify_init1(libc::IN_NONBLOCK | libc::IN_CLOEXEC) };
            let mut pipe = [-1; 2];
            if unsafe { libc::pipe2(pipe.as_mut_ptr(), libc::O_NONBLOCK | libc::O_CLOEXEC) } != 0 {
                return;
            }
            SIGNAL_FD.store(pipe[1], Ordering::Relaxed);
            let palette = theme::path();
            if let Some(parent) = palette.parent()
                && let Ok(path) = std::ffi::CString::new(parent.as_os_str().as_encoded_bytes())
                && fd >= 0
            {
                unsafe {
                    libc::inotify_add_watch(
                        fd,
                        path.as_ptr(),
                        libc::IN_CLOSE_WRITE | libc::IN_MOVED_TO | libc::IN_CREATE,
                    );
                }
            }
            let mut bytes = [0u8; 8192];
            loop {
                if CANCEL.swap(false, Ordering::Relaxed)
                    && tx.unbounded_send(Platform::Cancel).is_err()
                {
                    break;
                }
                let mut fds = [
                    libc::pollfd {
                        fd,
                        events: libc::POLLIN,
                        revents: 0,
                    },
                    libc::pollfd {
                        fd: pipe[0],
                        events: libc::POLLIN,
                        revents: 0,
                    },
                ];
                unsafe {
                    libc::poll(fds.as_mut_ptr(), fds.len() as _, -1);
                }
                if fds[1].revents & libc::POLLIN != 0 {
                    unsafe {
                        libc::read(pipe[0], bytes.as_mut_ptr().cast(), bytes.len());
                    }
                }
                if fds[0].revents & libc::POLLIN != 0 {
                    let n = unsafe { libc::read(fd, bytes.as_mut_ptr().cast(), bytes.len()) };
                    if n > 0 && tx.unbounded_send(Platform::Theme(theme::load())).is_err() {
                        break;
                    }
                }
                if tx.is_closed() {
                    break;
                }
            }
            SIGNAL_FD.store(-1, Ordering::Relaxed);
            for fd in [fd, pipe[0], pipe[1]] {
                if fd >= 0 {
                    unsafe {
                        libc::close(fd);
                    }
                }
            }
        })
        .expect("start filesystem and signal watcher");
    rx
}
pub(crate) struct BackgroundExecutor(iced::futures::executor::ThreadPool);
impl iced::Executor for BackgroundExecutor {
    fn new() -> std::result::Result<Self, std::io::Error> {
        crate::worker::initialize()?;
        iced::futures::executor::ThreadPool::builder()
            .pool_size(2)
            .name_prefix("snap-worker")
            .create()
            .map(Self)
    }
    fn spawn(&self, future: impl std::future::Future<Output = ()> + Send + 'static) {
        self.0.spawn_ok(future);
    }
    fn block_on<T>(&self, future: impl std::future::Future<Output = T>) -> T {
        iced::futures::executor::block_on(future)
    }
}
#[cfg(test)]
mod tests {
    use super::*;

    fn picker(options: Options) -> App {
        let cap = Capture {
            document: Document::new(::image::RgbaImage::from_pixel(
                320,
                240,
                ::image::Rgba([80, 120, 160, 255]),
            )),
            monitor: Monitor {
                width: 320,
                height: 240,
                scale: 1.0,
                ..Default::default()
            },
            windows: vec![],
            pointer: None,
        };
        App::boot(options, Config::default(), cap, Phase::Pick, Instant::now()).0
    }

    #[test]
    fn quick_selection_never_enters_editor_and_failure_keeps_picker() {
        for options in [
            Options {
                copy: true,
                ..Options::default()
            },
            Options {
                save: true,
                ..Options::default()
            },
        ] {
            let mut app = picker(options);
            let original = app.doc().state.crop;
            let _ = app.pointer(Motion::Down, Point::new(80.0, 60.0));
            let _ = app.pointer(Motion::Up, Point::new(240.0, 160.0));
            assert_eq!(app.phase, Phase::Pick);
            assert!(app.busy);
            assert_eq!(app.doc().state.crop, original);
            let mut doc = app.doc().clone();
            doc.state.crop = Rect {
                x: 80.0,
                y: 60.0,
                w: 160.0,
                h: 100.0,
            };
            let _ = app.update(Message::SelectionReady(Ok((doc, None))));
            assert_eq!(
                app.phase,
                Phase::Pick,
                "clipboard/save worker must not display editor"
            );
            assert!(app.busy);
            assert_eq!(
                app.doc().state.crop,
                original,
                "export owns selection; picker remains unchanged"
            );
            let _ = app.update(Message::Finished(Err("clipboard unavailable".into())));
            assert!(!app.busy);
            assert_eq!(app.doc().state.crop, original);
        }
        let mut app = picker(Options {
            editor: true,
            ..Options::default()
        });
        let original = app.doc().state.crop;
        let _ = app.pointer(Motion::Down, Point::new(80.0, 60.0));
        let _ = app.pointer(Motion::Up, Point::new(240.0, 160.0));
        let _ = app.update(Message::SelectionReady(Err("window capture failed".into())));
        assert_eq!(app.phase, Phase::Pick);
        assert!(!app.busy);
        assert_eq!(app.doc().state.crop, original);
    }

    #[test]
    fn editor_waits_for_cropped_pixels_and_final_configure() {
        let mut app = picker(Options {
            editor: true,
            ..Options::default()
        });
        let _ = app.pointer(Motion::Down, Point::new(80.0, 60.0));
        let _ = app.pointer(Motion::Up, Point::new(240.0, 160.0));
        assert_eq!(app.phase, Phase::Preparing);
        let mut doc = app.doc().clone();
        doc.state.crop = Rect {
            x: 80.0,
            y: 60.0,
            w: 160.0,
            h: 100.0,
        };
        let image = owned_handle(doc.render().unwrap());
        let _ = app.update(Message::Event(iced::Event::Window(window::Event::Resized(
            Size::new(288.0, 208.0),
        ))));
        assert_eq!(
            app.phase,
            Phase::Preparing,
            "configure alone cannot expose uncropped pixels"
        );
        let _ = app.update(Message::SelectionReady(Ok((doc, Some(image)))));
        assert_eq!(app.phase, Phase::Preparing);
        assert!(app.busy);
        assert_eq!(app.image_size, (160, 100));
        let _ = app.update(Message::Event(iced::Event::Window(window::Event::Resized(
            Size::new(320.0, 240.0),
        ))));
        assert_eq!(
            app.phase,
            Phase::Preparing,
            "old picker size cannot expose editor"
        );
        let size = app.editor_size.unwrap();
        let _ = app.update(Message::Event(iced::Event::Window(window::Event::Resized(
            size,
        ))));
        assert_eq!(app.phase, Phase::Edit);
        assert!(!app.busy);
        assert_eq!(app.image_size, (160, 100));
    }

    #[test]
    fn picker_pixels_share_source_allocation_and_outlive_the_document() {
        let source = Arc::new(::image::RgbaImage::from_pixel(
            32,
            24,
            ::image::Rgba([12, 34, 56, 78]),
        ));
        let pointer = source.as_raw().as_ptr();
        let picture::Handle::Rgba { pixels, .. } = handle(&source) else {
            panic!("RGBA handle");
        };
        assert_eq!(
            pointer,
            pixels.as_ptr(),
            "picker must not copy source pixels"
        );
        drop(source);
        assert_eq!(pixels.len(), 32 * 24 * 4);
        assert_eq!(&pixels[..4], &[12, 34, 56, 78]);
    }

    #[test]
    #[ignore = "requires a working Wayland GPU or software renderer"]
    fn first_picker_frame_has_guides_without_pointer_motion() {
        use iced::advanced::{
            Layout, layout,
            renderer::{Headless as _, Renderer as _},
            widget::Tree,
        };
        let source = ::image::RgbaImage::from_pixel(320, 240, ::image::Rgba([100, 120, 140, 255]));
        let cap = Capture {
            document: Document::new(source),
            monitor: Monitor {
                width: 320,
                height: 240,
                scale: 1.0,
                ..Default::default()
            },
            windows: vec![capture::Window {
                rect: Rect {
                    x: 80.0,
                    y: 60.0,
                    w: 160.0,
                    h: 100.0,
                },
                identifier: String::new(),
            }],
            pointer: Some(Point::new(140.0, 110.0)),
        };
        let (mut app, _) = App::boot(
            Options::default(),
            Config::default(),
            cap,
            Phase::Pick,
            Instant::now(),
        );
        assert_eq!(app.hover, Some(Point::new(140.0, 110.0)));
        let mut renderer = iced::futures::executor::block_on(iced::Renderer::new(
            iced::Font::DEFAULT,
            iced::Pixels(16.0),
            None,
        ))
        .expect("working renderer");
        let bounds = Rectangle::with_size(Size::new(320.0, 240.0));
        let mut draw = |app: &App, scale: f32| {
            let mut content = app.overlay(bounds.size());
            let mut tree = Tree::new(&content);
            let node = content.as_widget_mut().layout(
                &mut tree,
                &renderer,
                &layout::Limits::new(bounds.size(), bounds.size()),
            );
            renderer.reset(bounds);
            content.as_widget().draw(
                &tree,
                &mut renderer,
                &app.theme,
                &iced::advanced::renderer::Style::default(),
                Layout::new(&node),
                mouse::Cursor::Unavailable,
                &bounds,
            );
            renderer.screenshot(
                Size::new((320.0 * scale) as u32, (240.0 * scale) as u32),
                scale,
                Color::BLACK,
            )
        };
        for scale in [1.0, 1.5, 2.0] {
            let guides = draw(&app, scale);
            app.hover = None;
            let plain = draw(&app, scale);
            app.hover = app.capture.pointer;
            let pixel = |bytes: &[u8], x: u32, y: u32| {
                let index = (((y as f32 * scale) as usize * (320.0 * scale) as usize)
                    + (x as f32 * scale) as usize)
                    * 4;
                bytes[index..index + 4].to_vec()
            };
            assert_ne!(
                pixel(&guides, 10, 110),
                pixel(&plain, 10, 110),
                "crosshair at {scale}"
            );
            assert!(
                pixel(&guides, 20, 20)[0] < pixel(&plain, 20, 20)[0],
                "outside window dims at {scale}"
            );
            assert_eq!(
                pixel(&guides, 100, 100),
                pixel(&plain, 100, 100),
                "selected pixels stay clear at {scale}"
            );
            app.hover = None;
            app.zoom = 0.5;
            app.pan = Vector::new(15.0, 10.0);
            let transformed = draw(&app, scale);
            assert_eq!(
                pixel(&transformed, 20, 20),
                pixel(&plain, 20, 20),
                "frozen background survives picker zoom and pan at {scale}"
            );
            app.zoom = 1.0;
            app.pan = Vector::ZERO;
            app.hover = app.capture.pointer;
        }
    }
    #[test]
    fn preview_is_bounded_to_discovered_monitor() {
        let monitor = Monitor {
            width: 1280,
            height: 720,
            scale: 1.0,
            ..Default::default()
        };
        for (w, h) in [(900, 540), (300, 10000), (10000, 300)] {
            let d = Document::new(::image::RgbaImage::new(w, h));
            let size = window_size(&d, Phase::Preview, &monitor);
            assert!(size.width <= 1280.0 - 32.0 && size.height <= 720.0 - 32.0);
            assert!(size.width >= 280.0 && size.height >= 92.0);
        }
    }
    #[test]
    fn native_selection_coordinates_survive_editor_resizing() {
        let mut document = Document::new(::image::RgbaImage::new(2560, 1440));
        document.state.crop = Rect {
            x: 600.0,
            y: 500.0,
            w: 1000.0,
            h: 600.0,
        };
        let cap = Capture {
            document,
            monitor: Monitor {
                width: 2560,
                height: 1440,
                scale: 2.0,
                ..Default::default()
            },
            windows: vec![],
            pointer: None,
        };
        let (app, _) = App::boot(
            Options {
                editor: true,
                ..Default::default()
            },
            Config::default(),
            cap,
            Phase::Edit,
            Instant::now(),
        );
        for size in [
            Size::new(360.0, 220.0),
            Size::new(1280.0, 720.0),
            Size::new(2560.0, 1440.0),
            Size::new(720.0, 1280.0),
            Size::new(3000.0, 400.0),
        ] {
            let view = Surface { app: &app }.view(size);
            assert!(view.rect.x >= 0.0 && view.rect.y >= 0.0);
            assert!(view.rect.x + view.rect.width <= size.width + 0.1);
            assert!(view.rect.y + view.rect.height <= size.height + 0.1);
            let native = Point::new(1100.0, 800.0);
            let restored = view.source(view.screen(native));
            assert!((restored.x - native.x).abs() < 0.001 && (restored.y - native.y).abs() < 0.001);
        }
    }
}
