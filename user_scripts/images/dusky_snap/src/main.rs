mod capture;
mod document;
mod gpu;
mod input;
mod output;
mod raster;
mod theme;
mod ui;

use std::{path::PathBuf, time::Instant};

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Clone, Debug, Default)]
pub struct Options {
    pub mode: String,
    pub file: Option<PathBuf>,
    pub clipboard: bool,
    pub editor: bool,
    pub copy: bool,
    pub save: bool,
    pub output: Option<PathBuf>,
    pub preview: Option<PathBuf>,
    pub pin: bool,
    pub cursor: bool,
    pub smoke: bool,
}

fn parse() -> Result<Option<Options>> {
    parse_args(std::env::args().skip(1))
}
fn parse_args(mut args: impl Iterator<Item = String>) -> Result<Option<Options>> {
    let mut o = Options {
        mode: "smart".into(),
        ..Options::default()
    };
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--help" | "-h" => {
                println!(
                    "dusky_snap — Wayland screenshot and annotation tool\n\nUsage: dusky_snap [OPTIONS] [IMAGE]\n\n  --region, --window, --fullscreen, --scroll  Capture mode (default: smart)\n  --editor, --annotate Annotate before output (default)\n  --file IMAGE         Edit an image or saved working document\n  --clipboard          Edit the clipboard image\n  --copy               Copy PNG without preview\n  --save               Save PNG without preview\n  --output PATH        Save to this PNG path\n  --cursor             Include the pointer\n  --pin IMAGE          Keep an image on screen\n  --version            Print version\n\nCapture workflows:\n  dusky_snap --region --copy       Region to clipboard\n  dusky_snap --region              Region to editor\n  dusky_snap --fullscreen --copy   Focused screen to clipboard\n  dusky_snap --fullscreen          Focused screen to editor\n\nDrag a region or click a window; click empty space for the monitor.\nEditor: V select, A arrow, L line, D pen, H highlight, R rectangle,\nE ellipse, N marker, T text, X redact, C crop, O OCR, B backdrop.\nCtrl+Z/Y undo/redo; Ctrl+C copy; Ctrl+S save; Enter copy and save;\nCtrl+Shift+S Save As; P pin; Esc dismiss. Right-click copies and closes. Scroll changes tool width;\nCtrl+scroll zooms; middle-drag pans; Delete removes a layer.\n\nConfig: $XDG_CONFIG_HOME/dusky/snap/config.json (optional)."
                );
                return Ok(None);
            }
            "--version" | "-V" => {
                println!("dusky_snap {}", env!("CARGO_PKG_VERSION"));
                return Ok(None);
            }
            "--region" | "--window" | "--fullscreen" | "--scroll" => o.mode = arg[2..].into(),
            "--editor" | "--annotate" => o.editor = true,
            "--file" => o.file = Some(args.next().ok_or("--file needs a path")?.into()),
            "--clipboard" => o.clipboard = true,
            "--copy" => o.copy = true,
            "--save" => o.save = true,
            "--output" => {
                o.output = Some(args.next().ok_or("--output needs a path")?.into());
                o.save = true;
            }
            "--cursor" => o.cursor = true,
            "--pin" => {
                o.pin = true;
                o.preview = Some(args.next().ok_or("--pin needs a path")?.into());
            }
            "--preview" => o.preview = Some(args.next().ok_or("--preview needs a path")?.into()),
            "--smoke-test" => o.smoke = true,
            _ if !arg.starts_with('-') && o.file.is_none() => o.file = Some(arg.into()),
            _ => return Err(format!("Unknown argument: {arg}").into()),
        }
    }
    if o.clipboard && o.file.is_some() {
        return Err("Choose --file or --clipboard".into());
    }
    if (o.copy || o.save) && (o.file.is_some() || o.clipboard || o.preview.is_some()) {
        return Err("Quick output cannot be combined with file, clipboard, or pin input".into());
    }
    if !o.copy && !o.save {
        o.editor = true;
    }
    Ok(Some(o))
}

fn main() {
    let start = Instant::now();
    let result = parse().and_then(|o| match o {
        Some(o) => ui::run(o, start),
        None => Ok(()),
    });
    if let Err(error) = result {
        eprintln!("dusky_snap: {error}");
        std::process::exit(1);
    }
}

pub fn trace(start: Instant, stage: &str) {
    if std::env::var_os("DUSKY_SNAP_PROFILE_STARTUP").is_some() {
        eprintln!(
            "dusky_snap {stage} {:.3} ms",
            start.elapsed().as_secs_f64() * 1000.0
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn options(args: &[&str]) -> Options {
        parse_args(args.iter().map(|s| s.to_string()))
            .unwrap()
            .unwrap()
    }
    #[test]
    fn capture_workflows_are_explicit_and_order_independent() {
        assert!(options(&[]).editor);
        for mode in ["--region", "--fullscreen"] {
            let edit = options(&[mode]);
            assert!(edit.editor && !edit.copy && !edit.save);
            let quick = options(&[mode, "--copy"]);
            assert!(quick.copy && !quick.editor && !quick.save);
            assert!(!options(&["--copy", mode]).editor);
            assert!(options(&[mode, "--editor"]).editor);
            assert!(options(&[mode, "--annotate"]).editor);
        }
        let save = options(&["--region", "--save"]);
        assert!(save.save && !save.editor && !save.copy);
    }
}
