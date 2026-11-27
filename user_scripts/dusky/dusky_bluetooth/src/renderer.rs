//! Vulkan-first wgpu compositor with Wayland EGL fallback.
//!
//! Keeps rendering on Iced's `iced_wgpu::Renderer` while controlling backend
//! adapter selection without mutating process environment variables after
//! worker threads start.

use iced_core::{Background, Color, Font, Pixels, Point, Rectangle, Size, Transformation};
use iced_core::{image, renderer, text};
use iced_renderer::graphics::{self, Shell, Viewport, compositor};

static STARTUP: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
static FIRST_PRESENT: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub fn trace_startup() {
    if std::env::var_os("DUSKY_BLUETOOTH_TRACE").is_some() {
        let _ = STARTUP.set(std::time::Instant::now());
        FIRST_PRESENT.store(true, std::sync::atomic::Ordering::Relaxed);
    }
}

pub fn trace_phase(phase: impl std::fmt::Display) {
    if let Some(start) = STARTUP.get() {
        eprintln!(
            "bluetooth {phase}: {:.1} ms",
            start.elapsed().as_secs_f64() * 1000.0
        );
    }
}

pub struct Renderer(iced_wgpu::Renderer);

pub struct Compositor(iced_wgpu::window::Compositor);

impl compositor::Default for Renderer {
    type Compositor = Compositor;
}

impl graphics::Compositor for Compositor {
    type Renderer = Renderer;
    type Surface = wgpu::Surface<'static>;

    async fn with_backend(
        settings: graphics::Settings,
        display: impl compositor::Display + Clone,
        window: impl compositor::Window + Clone,
        shell: Shell,
        backend: Option<&str>,
    ) -> Result<Self, graphics::Error> {
        if backend.is_some() || std::env::var_os("WGPU_BACKEND").is_some() {
            return iced_wgpu::window::Compositor::with_backend(
                settings, display, window, shell, backend,
            )
            .await
            .map(Self);
        }

        let mut settings = iced_wgpu::Settings::from(settings);
        if let Some(mode) = iced_wgpu::settings::present_mode_from_env() {
            settings.present_mode = mode;
        }
        settings.backends = wgpu::Backends::VULKAN;

        match iced_wgpu::window::Compositor::request(settings, Some(window.clone()), shell.clone())
            .await
        {
            Ok(compositor) => {
                trace_phase("vulkan-ready");
                Ok(Self(compositor))
            }
            Err(vulkan_error) => {
                eprintln!("Vulkan initialization failed ({vulkan_error}); trying Wayland EGL");
                settings.backends = wgpu::Backends::GL;
                iced_wgpu::window::Compositor::request(settings, Some(window), shell)
                    .await
                    .map(|compositor| {
                        trace_phase("egl-ready");
                        Self(compositor)
                    })
                    .map_err(|gl_error| {
                        graphics::Error::List(vec![vulkan_error.into(), gl_error.into()])
                    })
            }
        }
    }

    fn create_renderer(&self) -> Renderer {
        Renderer(self.0.create_renderer())
    }

    fn create_surface<W: compositor::Window + Clone>(
        &mut self,
        window: W,
        width: u32,
        height: u32,
    ) -> Self::Surface {
        self.0.create_surface(window, width, height)
    }

    fn configure_surface(&mut self, surface: &mut Self::Surface, width: u32, height: u32) {
        self.0.configure_surface(surface, width, height);
    }

    fn information(&self) -> compositor::Information {
        self.0.information()
    }

    fn present(
        &mut self,
        renderer: &mut Renderer,
        surface: &mut Self::Surface,
        viewport: &Viewport,
        background: Color,
        on_pre_present: impl FnOnce(),
    ) -> Result<(), compositor::SurfaceError> {
        let result = self.0.present(
            &mut renderer.0,
            surface,
            viewport,
            background,
            on_pre_present,
        );
        if result.is_ok()
            && FIRST_PRESENT.swap(false, std::sync::atomic::Ordering::Relaxed)
            && let Some(start) = STARTUP.get()
        {
            eprintln!(
                "bluetooth first-present: {:.1} ms",
                start.elapsed().as_secs_f64() * 1000.0
            );
        }
        result
    }

    fn screenshot(
        &mut self,
        renderer: &mut Renderer,
        viewport: &Viewport,
        background: Color,
    ) -> Vec<u8> {
        self.0.screenshot(&mut renderer.0, viewport, background)
    }
}

impl iced_core::Renderer for Renderer {
    fn start_layer(&mut self, bounds: Rectangle) {
        self.0.start_layer(bounds);
    }

    fn end_layer(&mut self) {
        self.0.end_layer();
    }

    fn start_transformation(&mut self, transformation: Transformation) {
        self.0.start_transformation(transformation);
    }

    fn end_transformation(&mut self) {
        self.0.end_transformation();
    }

    fn fill_quad(&mut self, quad: renderer::Quad, background: impl Into<Background>) {
        self.0.fill_quad(quad, background);
    }

    fn reset(&mut self, bounds: Rectangle) {
        self.0.reset(bounds);
    }

    fn allocate_image(
        &mut self,
        handle: &image::Handle,
        callback: impl FnOnce(Result<image::Allocation, image::Error>) + Send + 'static,
    ) {
        self.0.allocate_image(handle, callback);
    }
}

impl text::Renderer for Renderer {
    type Font = Font;
    type Paragraph = <iced_wgpu::Renderer as text::Renderer>::Paragraph;
    type Editor = <iced_wgpu::Renderer as text::Renderer>::Editor;

    const ICON_FONT: Font = <iced_wgpu::Renderer as text::Renderer>::ICON_FONT;
    const CHECKMARK_ICON: char = <iced_wgpu::Renderer as text::Renderer>::CHECKMARK_ICON;
    const ARROW_DOWN_ICON: char = <iced_wgpu::Renderer as text::Renderer>::ARROW_DOWN_ICON;
    const SCROLL_UP_ICON: char = <iced_wgpu::Renderer as text::Renderer>::SCROLL_UP_ICON;
    const SCROLL_DOWN_ICON: char = <iced_wgpu::Renderer as text::Renderer>::SCROLL_DOWN_ICON;
    const SCROLL_LEFT_ICON: char = <iced_wgpu::Renderer as text::Renderer>::SCROLL_LEFT_ICON;
    const SCROLL_RIGHT_ICON: char = <iced_wgpu::Renderer as text::Renderer>::SCROLL_RIGHT_ICON;
    const ICED_LOGO: char = <iced_wgpu::Renderer as text::Renderer>::ICED_LOGO;

    fn default_font(&self) -> Font {
        self.0.default_font()
    }

    fn default_size(&self) -> Pixels {
        self.0.default_size()
    }

    fn fill_paragraph(
        &mut self,
        paragraph: &Self::Paragraph,
        position: Point,
        color: Color,
        clip: Rectangle,
    ) {
        self.0.fill_paragraph(paragraph, position, color, clip);
    }

    fn fill_editor(
        &mut self,
        editor: &Self::Editor,
        position: Point,
        color: Color,
        clip: Rectangle,
    ) {
        self.0.fill_editor(editor, position, color, clip);
    }

    fn fill_text(
        &mut self,
        text: text::Text<String>,
        position: Point,
        color: Color,
        clip: Rectangle,
    ) {
        self.0.fill_text(text, position, color, clip);
    }
}

impl renderer::Headless for Renderer {
    async fn new(font: Font, size: Pixels, backend: Option<&str>) -> Option<Self> {
        <iced_wgpu::Renderer as renderer::Headless>::new(font, size, backend)
            .await
            .map(Self)
    }

    fn name(&self) -> String {
        renderer::Headless::name(&self.0)
    }

    fn screenshot(&mut self, size: Size<u32>, scale: f32, background: Color) -> Vec<u8> {
        renderer::Headless::screenshot(&mut self.0, size, scale, background)
    }
}

impl iced_core::svg::Renderer for Renderer {
    fn measure_svg(&self, handle: &iced_core::svg::Handle) -> Size<u32> {
        self.0.measure_svg(handle)
    }

    fn draw_svg(&mut self, svg: iced_core::svg::Svg, bounds: Rectangle, clip: Rectangle) {
        self.0.draw_svg(svg, bounds, clip);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "requires a working Vulkan or Wayland EGL adapter"]
    fn forwarding_matches_native_renderer_pixels() {
        use iced_core::renderer::Headless as _;
        fn scene<R>(renderer: &mut R)
        where
            R: iced_core::Renderer + text::Renderer<Font = Font> + iced_core::svg::Renderer,
        {
            let viewport = Rectangle::with_size(Size::new(160.0, 90.0));
            renderer.reset(viewport);
            renderer.fill_quad(
                renderer::Quad {
                    bounds: Rectangle {
                        x: 8.0,
                        y: 8.0,
                        width: 144.0,
                        height: 74.0,
                    },
                    border: iced_core::Border {
                        color: Color::from_rgb(0.4, 0.6, 0.9),
                        width: 1.5,
                        radius: 6.0.into(),
                    },
                    ..Default::default()
                },
                Color::from_rgb(0.12, 0.14, 0.18),
            );
            renderer.fill_text(
                text::Text {
                    content: "Bluetooth".to_string(),
                    bounds: Size::new(120.0, 28.0),
                    size: Pixels(15.0),
                    line_height: text::LineHeight::default(),
                    font: Font::MONOSPACE,
                    align_x: text::Alignment::Default,
                    align_y: iced_core::alignment::Vertical::Top,
                    shaping: text::Shaping::Advanced,
                    wrapping: text::Wrapping::None,
                },
                Point::new(16.0, 14.0),
                Color::WHITE,
                viewport,
            );
        }

        let mut native = iced_futures::futures::executor::block_on(
            <iced_wgpu::Renderer as renderer::Headless>::new(Font::MONOSPACE, Pixels(15.0), None),
        )
        .unwrap();
        let mut wrapper = iced_futures::futures::executor::block_on(Renderer::new(
            Font::MONOSPACE,
            Pixels(15.0),
            None,
        ))
        .unwrap();

        for scale in [1.0_f32, 1.5, 2.0] {
            scene(&mut native);
            scene(&mut wrapper);
            let size = Size::new((160.0 * scale).ceil() as u32, (90.0 * scale).ceil() as u32);
            assert_eq!(
                renderer::Headless::screenshot(&mut native, size, scale, Color::BLACK),
                wrapper.screenshot(size, scale, Color::BLACK),
            );
        }
    }
}
