//! Initialize EGL only when Vulkan compositor creation fails.
//!
//! The layer-shell runner aborts on compositor errors, so fallback must happen
//! here. Rendering stays with Iced's wgpu renderer; these newtypes only select
//! its compositor through Iced's public traits and forward drawing unchanged.

use iced_core::{Background, Color, Font, Pixels, Point, Rectangle, Size, Transformation};
use iced_core::{image, renderer, text};
use iced_renderer::graphics::{self, Shell, Viewport, compositor};

static STARTUP: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
static FIRST_PRESENT: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub fn trace_startup() {
    if std::env::var_os("DUSKY_TRAY_TRACE").is_some() {
        let _ = STARTUP.set(std::time::Instant::now());
        FIRST_PRESENT.store(true, std::sync::atomic::Ordering::Relaxed);
    }
}

pub fn trace_phase(phase: &str) {
    if let Some(start) = STARTUP.get() {
        eprintln!(
            "tray {phase}: {:.1} ms",
            start.elapsed().as_secs_f64() * 1000.0
        );
    }
}

pub struct Renderer(iced_wgpu::Renderer, f32, f32, GlowCache);

struct GlowCache {
    glyphs: graphics::text::cosmic_text::SwashCache,
    paragraphs:
        std::collections::VecDeque<(graphics::text::Paragraph, iced_core::svg::Handle, Rectangle)>,
}

impl Default for GlowCache {
    fn default() -> Self {
        Self {
            glyphs: graphics::text::cosmic_text::SwashCache::new(),
            paragraphs: std::collections::VecDeque::new(),
        }
    }
}

impl GlowCache {
    fn paragraph(
        &mut self,
        paragraph: &graphics::text::Paragraph,
    ) -> (iced_core::svg::Handle, Rectangle) {
        if let Some((_, handle, bounds)) = self
            .paragraphs
            .iter()
            .find(|(cached, _, _)| cached == paragraph)
        {
            return (handle.clone(), *bounds);
        }
        // Rasterize the already shaped text using Iced's exact fonts. Group
        // equal-alpha pixels into paths, then let resvg cache a true Gaussian
        // blur. Color and intensity do not change the underlying mask.
        use std::fmt::Write as _;
        let mut paths = std::collections::BTreeMap::<u8, String>::new();
        let mut min_x = 0;
        let mut min_y = 0;
        let mut max_x = 1;
        let mut max_y = 1;
        paragraph.buffer().draw(
            graphics::text::font_system()
                .write()
                .expect("Write font system")
                .raw(),
            &mut self.glyphs,
            graphics::text::cosmic_text::Color::rgb(255, 255, 255),
            |x, y, _, _, color| {
                if color.a() == 0 {
                    return;
                }
                min_x = min_x.min(x);
                min_y = min_y.min(y);
                max_x = max_x.max(x + 1);
                max_y = max_y.max(y + 1);
                let _ = write!(paths.entry(color.a()).or_default(), "M{x} {y}h1v1h-1z");
            },
        );
        let bounds = Rectangle {
            x: (min_x - 8) as f32,
            y: (min_y - 8) as f32,
            width: (max_x - min_x + 16) as f32,
            height: (max_y - min_y + 16) as f32,
        };
        let mut svg = format!(
            r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="{} {} {} {}"><defs><filter id="halo" filterUnits="userSpaceOnUse" x="{}" y="{}" width="{}" height="{}"><feGaussianBlur stdDeviation="2"/></filter></defs><g fill="white" filter="url(#halo)">"#,
            bounds.x,
            bounds.y,
            bounds.width,
            bounds.height,
            bounds.x,
            bounds.y,
            bounds.width,
            bounds.height
        );
        for (alpha, path) in paths {
            let _ = write!(
                svg,
                r#"<path opacity="{}" d="{path}"/>"#,
                f32::from(alpha) / 255.0
            );
        }
        svg.push_str("</g></svg>");
        let handle = iced_core::svg::Handle::from_memory(svg.into_bytes());
        // Bound retained clock/slider history; repeated frames use one cached mask.
        if self.paragraphs.len() == 32 {
            self.paragraphs.pop_front();
        }
        self.paragraphs
            .push_back((paragraph.clone(), handle.clone(), bounds));
        (handle, bounds)
    }
}

impl Renderer {
    pub fn with_opacity(&mut self, opacity: f32, draw: impl FnOnce(&mut Self)) {
        let previous = self.1;
        self.1 *= opacity;
        draw(self);
        self.1 = previous;
    }

    pub fn with_glow(&mut self, strength: f32, draw: impl FnOnce(&mut Self)) {
        let previous = self.2;
        self.2 = strength;
        draw(self);
        self.2 = previous;
    }

    fn glow_paragraph(
        &mut self,
        paragraph: &graphics::text::Paragraph,
        position: Point,
        color: Color,
        clip: Rectangle,
    ) {
        if self.2 <= 0.0 {
            return;
        }
        use iced_core::svg::Renderer as _;
        let (handle, bounds) = self.3.paragraph(paragraph);
        self.0.draw_svg(
            iced_core::svg::Svg::new(handle)
                .color(color)
                .opacity(color.a * self.2 * 0.5544),
            Rectangle {
                x: position.x + bounds.x,
                y: position.y + bounds.y,
                ..bounds
            },
            clip,
        );
    }

    pub fn draw_icon(
        &mut self,
        mut svg: iced_core::svg::Svg,
        halo: iced_core::svg::Handle,
        bounds: Rectangle,
        clip: Rectangle,
    ) {
        use iced_core::svg::Renderer as _;
        svg.opacity *= self.1;
        if self.2 > 0.0 {
            let mut glow = svg.clone();
            glow.handle = halo;
            glow.opacity *= self.2 * 0.5544;
            self.0.draw_svg(
                glow,
                Rectangle {
                    x: bounds.x - bounds.width / 3.0,
                    y: bounds.y - bounds.height / 3.0,
                    width: bounds.width * 5.0 / 3.0,
                    height: bounds.height * 5.0 / 3.0,
                },
                clip,
            );
        }
        self.0.draw_svg(svg, bounds, clip);
    }

    fn fade_color(&self, color: Color) -> Color {
        Color {
            a: color.a * self.1,
            ..color
        }
    }
}

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
        // Preserve Iced's exact behavior for explicit backend preferences.
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
            Ok(compositor) => Ok(Self(compositor)),
            Err(vulkan_error) => {
                // The failed request has dropped its instance/surface/device.
                // Do not mutate the environment now that workers are running.
                eprintln!("Vulkan initialization failed ({vulkan_error}); trying Wayland EGL");
                settings.backends = wgpu::Backends::GL;
                iced_wgpu::window::Compositor::request(settings, Some(window), shell)
                    .await
                    .map(Self)
                    .map_err(|gl_error| {
                        graphics::Error::List(vec![vulkan_error.into(), gl_error.into()])
                    })
            }
        }
    }

    fn create_renderer(&self) -> Renderer {
        Renderer(self.0.create_renderer(), 1.0, 0.0, GlowCache::default())
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
                "tray first-present: {:.1} ms",
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

    fn fill_quad(&mut self, mut quad: renderer::Quad, background: impl Into<Background>) {
        let mut background = background.into();
        if self.1 < 1.0 {
            quad.border.color = self.fade_color(quad.border.color);
            quad.shadow.color = self.fade_color(quad.shadow.color);
            background = match background {
                Background::Color(color) => Background::Color(self.fade_color(color)),
                Background::Gradient(gradient) => {
                    Background::Gradient(gradient.scale_alpha(self.1))
                }
            };
        }
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
        let color = self.fade_color(color);
        self.glow_paragraph(paragraph, position, color, clip);
        self.0.fill_paragraph(paragraph, position, color, clip);
    }

    fn fill_editor(
        &mut self,
        editor: &Self::Editor,
        position: Point,
        color: Color,
        clip: Rectangle,
    ) {
        self.0
            .fill_editor(editor, position, self.fade_color(color), clip);
    }

    fn fill_text(
        &mut self,
        text: text::Text<String>,
        position: Point,
        color: Color,
        clip: Rectangle,
    ) {
        let color = self.fade_color(color);
        if self.2 > 0.0 {
            use iced_core::text::Paragraph as _;
            let paragraph = graphics::text::Paragraph::with_text(text.as_ref());
            let mut origin = position;
            origin.x -= match text.align_x {
                text::Alignment::Center => paragraph.min_bounds().width / 2.0,
                text::Alignment::Right => paragraph.min_bounds().width,
                _ => 0.0,
            };
            origin.y -= match text.align_y {
                iced_core::alignment::Vertical::Center => paragraph.min_bounds().height / 2.0,
                iced_core::alignment::Vertical::Bottom => paragraph.min_bounds().height,
                _ => 0.0,
            };
            self.glow_paragraph(&paragraph, origin, color, clip);
        }
        self.0.fill_text(text, position, color, clip);
    }
}

impl renderer::Headless for Renderer {
    async fn new(font: Font, size: Pixels, backend: Option<&str>) -> Option<Self> {
        <iced_wgpu::Renderer as renderer::Headless>::new(font, size, backend)
            .await
            .map(|renderer| Self(renderer, 1.0, 0.0, GlowCache::default()))
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
    fn draw_svg(&mut self, mut svg: iced_core::svg::Svg, bounds: Rectangle, clip: Rectangle) {
        svg.opacity *= self.1;
        self.0.draw_svg(svg, bounds, clip);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "requires a working Vulkan or Wayland EGL adapter"]
    fn rendered_glow_preserves_zero_and_increases_glyph_edge_light() {
        use iced_core::{Element, Layout, Theme, layout, mouse, widget::Tree};
        use iced_core::{Renderer as _, renderer::Headless as _};
        let mut renderer = iced_futures::futures::executor::block_on(Renderer::new(
            Font::DEFAULT,
            Pixels(20.0),
            None,
        ))
        .expect("a render adapter is required for this pixel test");
        let size = Size::new(160, 60);
        let viewport = Rectangle::with_size(Size::new(160.0, 60.0));
        let mut render = |strength: Option<f32>| {
            let content: Element<'_, (), Theme, Renderer> = iced_widget::row![
                iced_widget::text("100")
                    .size(20)
                    .color(Color::from_rgb(0.5, 0.7, 0.9)),
                crate::icons::icon("󰕾", 24.0, Some(Color::from_rgb(0.5, 0.7, 0.9))),
            ]
            .spacing(16)
            .into();
            let mut content = match strength {
                Some(strength) => crate::appearance::glow(content, strength),
                None => content,
            };
            let mut tree = Tree::new(&content);
            let node = content
                .as_widget_mut()
                .layout(
                    &mut tree,
                    &renderer,
                    &layout::Limits::new(Size::ZERO, viewport.size()),
                )
                .move_to(Point::new(20.0, 20.0));
            renderer.reset(viewport);
            content.as_widget().draw(
                &tree,
                &mut renderer,
                &Theme::Dark,
                &renderer::Style::default(),
                Layout::new(&node),
                mouse::Cursor::Unavailable,
                &viewport,
            );
            renderer.screenshot(size, 1.0, Color::BLACK)
        };
        let plain = render(None);
        let zero = render(Some(0.0));
        assert_eq!(plain, zero, "zero must preserve every original pixel");
        let half = render(Some(0.5));
        let full = render(Some(1.0));
        assert_eq!(
            renderer.3.paragraphs.len(),
            1,
            "changing intensity must reuse the text mask"
        );
        let energy = |pixels: &[u8]| -> u64 {
            pixels
                .as_chunks::<4>()
                .0
                .iter()
                .map(|pixel| pixel[..3].iter().map(|v| u64::from(*v)).sum::<u64>())
                .sum()
        };
        assert!(energy(&zero) < energy(&half));
        assert!(energy(&half) < energy(&full));
        // Confirm light radiates into background pixels rather than only
        // brightening the existing opaque glyph interiors.
        let edge_pixels = plain
            .as_chunks::<4>()
            .0
            .iter()
            .zip(full.as_chunks::<4>().0.iter())
            .filter(|(before, after)| before[..3] == [0, 0, 0] && after[..3] != [0, 0, 0])
            .count();
        assert!(edge_pixels > 0);
        eprintln!(
            "glow pixel test: RGB sums 0/50/100 = {}/{}/{}, {} newly lit edge pixels",
            energy(&zero),
            energy(&half),
            energy(&full),
            edge_pixels
        );
    }
}
