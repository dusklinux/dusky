//! Initialize EGL only when Vulkan compositor creation fails.
//!
//! The layer-shell runner aborts on compositor errors, so fallback must happen
//! here. Rendering stays with Iced's wgpu renderer; these newtypes only select
//! its compositor through Iced's public traits and forward drawing unchanged.

use iced_core::{Background, Color, Font, Image, Pixels, Point, Rectangle, Size, Transformation};
use iced_core::{image, renderer, text};
use iced_renderer::graphics::{self, Shell, Viewport, compositor};

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
        self.0.present(
            &mut renderer.0,
            surface,
            viewport,
            background,
            on_pre_present,
        )
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

impl image::Renderer for Renderer {
    type Handle = image::Handle;

    fn load_image(&self, handle: &Self::Handle) -> Result<image::Allocation, image::Error> {
        self.0.load_image(handle)
    }

    fn measure_image(&self, handle: &Self::Handle) -> Option<Size<u32>> {
        self.0.measure_image(handle)
    }

    fn draw_image(&mut self, image: Image, bounds: Rectangle, clip: Rectangle) {
        self.0.draw_image(image, bounds, clip);
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
