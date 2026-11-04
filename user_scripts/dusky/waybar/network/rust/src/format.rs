use crate::Snapshot;
use std::fmt::Write;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Horizontal,
    Vertical,
    Unit,
    Up,
    Down,
    Combined,
    SessionDown,
    SessionUp,
    SessionTotal,
    BootDown,
    BootUp,
    BootTotal,
}

impl Mode {
    pub fn parse(arg: &str) -> Option<Self> {
        Some(match arg {
            "--horizontal" | "horizontal" => Self::Horizontal,
            "--vertical" | "vertical" => Self::Vertical,
            "unit" => Self::Unit,
            "up" | "upload" => Self::Up,
            "down" | "download" => Self::Down,
            "combined" | "speed" | "rate" => Self::Combined,
            "session-down" | "down-session" => Self::SessionDown,
            "session-up" | "up-session" => Self::SessionUp,
            "session-total" | "total-session" | "session" => Self::SessionTotal,
            "boot-down" | "down-boot" | "total-down" => Self::BootDown,
            "boot-up" | "up-boot" | "total-up" => Self::BootUp,
            "boot-total" | "boot" | "total" | "total-traffic" => Self::BootTotal,
            _ => return None,
        })
    }
}

// u128 intermediates keep rounding sound even near the u64 counter limit.
fn rounded(value: u128, divisor: u128) -> u128 {
    (value + divisor / 2) / divisor
}

fn rate_number(value: u128, divisor: u128) -> String {
    let tenths = rounded(value * 10, divisor);
    if tenths < 100 {
        format!("{}.{:01}", tenths / 10, tenths % 10)
    } else {
        rounded(value, divisor).to_string()
    }
}

fn speed_unit(value: u128) -> (&'static str, &'static str, u128) {
    if value >= 1_038_090_240 {
        ("GB", "network-gb", 1 << 30)
    } else if value >= 1_013_760 {
        ("MB", "network-mb", 1 << 20)
    } else {
        ("KB", "network-kb", 1 << 10)
    }
}

fn speed(value: u128, divisor: u128) -> String {
    if divisor == 1 << 10 {
        rounded(value, divisor).to_string()
    } else {
        rate_number(value, divisor)
    }
}

fn single_speed(value: u128) -> String {
    let (unit, _, divisor) = speed_unit(value);
    format!("{}{}", speed(value, divisor), &unit[..1])
}

pub fn data(value: u128) -> (String, &'static str) {
    let mb = rounded(value, 1 << 20);
    if mb <= 9999 {
        return (mb.to_string(), "MB");
    }
    let gb_tenths = rounded(value * 10, 1 << 30);
    if gb_tenths < 1000 {
        (format!("{}.{:01}", gb_tenths / 10, gb_tenths % 10), "GB")
    } else if gb_tenths < 10_000 {
        (rounded(value, 1 << 30).to_string(), "GB")
    } else {
        (rate_number(value, 1 << 40), "TB")
    }
}

fn horizontal(value: &str) -> String {
    match value.len() {
        0 => "   ".into(),
        1 => format!(" {value} "),
        2 => format!(" {value}"),
        _ => value.chars().take(3).collect(),
    }
}

fn json_string(out: &mut String, value: &str) {
    out.push('"');
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c <= '\u{1f}' => write!(out, "\\u{:04x}", c as u32).unwrap(),
            c => out.push(c),
        }
    }
    out.push('"');
}

// Plain text for Dusky Glance; format the shared snapshot without a JSON parser.
pub fn render_osd(snapshot: &Snapshot, mode: Mode) -> String {
    if snapshot.iface.is_empty() {
        return "Offline".into();
    }
    match mode {
        Mode::Up => single_speed(snapshot.tx_rate as u128),
        Mode::Down => single_speed(snapshot.rx_rate as u128),
        Mode::Combined => single_speed(snapshot.rx_rate as u128 + snapshot.tx_rate as u128),
        Mode::SessionDown => data(snapshot.session_rx as u128).0,
        Mode::SessionUp => data(snapshot.session_tx as u128).0,
        Mode::SessionTotal => data(snapshot.session_rx as u128 + snapshot.session_tx as u128).0,
        Mode::BootDown => data(snapshot.boot_rx as u128).0,
        Mode::BootUp => data(snapshot.boot_tx as u128).0,
        Mode::BootTotal => data(snapshot.boot_rx as u128 + snapshot.boot_tx as u128).0,
        _ => {
            let (unit, _, divisor) = speed_unit(snapshot.rx_rate.max(snapshot.tx_rate) as u128);
            format!(
                "{}{} {}{}",
                speed(snapshot.tx_rate as u128, divisor),
                &unit[..1],
                speed(snapshot.rx_rate as u128, divisor),
                &unit[..1]
            )
        }
    }
}

pub fn render(snapshot: &Snapshot, mode: Mode) -> String {
    let connected = !snapshot.iface.is_empty();
    let (unit, class, divisor) = if connected {
        speed_unit(snapshot.rx_rate.max(snapshot.tx_rate) as u128)
    } else {
        ("-", "network-disconnected", 1024)
    };
    let (up, down) = if connected {
        (
            speed(snapshot.tx_rate as u128, divisor),
            speed(snapshot.rx_rate as u128, divisor),
        )
    } else {
        ("-".into(), "-".into())
    };
    let text = match mode {
        Mode::Horizontal => format!(
            "{} {} {}",
            horizontal(&up),
            horizontal(unit),
            horizontal(&down)
        ),
        Mode::Vertical => format!(
            "{}\n{unit}\n{}",
            up.chars().take(3).collect::<String>(),
            down.chars().take(3).collect::<String>()
        ),
        Mode::Unit => horizontal(unit),
        Mode::Up => horizontal(&up),
        Mode::Down => horizontal(&down),
        Mode::Combined => {
            if connected {
                single_speed(snapshot.rx_rate as u128 + snapshot.tx_rate as u128)
            } else {
                "-".into()
            }
        }
        Mode::SessionDown => data(snapshot.session_rx as u128).0,
        Mode::SessionUp => data(snapshot.session_tx as u128).0,
        Mode::SessionTotal => data(snapshot.session_rx as u128 + snapshot.session_tx as u128).0,
        Mode::BootDown => data(snapshot.boot_rx as u128).0,
        Mode::BootUp => data(snapshot.boot_tx as u128).0,
        Mode::BootTotal => data(snapshot.boot_rx as u128 + snapshot.boot_tx as u128).0,
    };
    let tooltip = if connected {
        let (sr, sru) = data(snapshot.session_rx as u128);
        let (st, stu) = data(snapshot.session_tx as u128);
        let (ss, ssu) = data(snapshot.session_rx as u128 + snapshot.session_tx as u128);
        let (br, bru) = data(snapshot.boot_rx as u128);
        let (bt, btu) = data(snapshot.boot_tx as u128);
        let (bs, bsu) = data(snapshot.boot_rx as u128 + snapshot.boot_tx as u128);
        format!(
            "Upload: Session {st} {stu} | Boot {bt} {btu}\nDownload: Session {sr} {sru} | Boot {br} {bru}\nTotal Traffic: Session {ss} {ssu} | Boot {bs} {bsu}"
        )
    } else {
        "Disconnected".into()
    };
    let mut out = String::with_capacity(320);
    out.push_str("{\"text\":");
    json_string(&mut out, &text);
    out.push_str(",\"class\":");
    json_string(&mut out, class);
    out.push_str(",\"tooltip\":");
    json_string(&mut out, &tooltip);
    out.push('}');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unit_boundaries_and_rounding() {
        assert_eq!(single_speed(511), "0K");
        assert_eq!(single_speed(512), "1K");
        assert_eq!(single_speed(1_013_759), "990K");
        assert_eq!(single_speed(1_013_760), "1.0M");
        assert_eq!(single_speed(1_038_090_240), "1.0G");
        assert_eq!(single_speed(10 << 20), "10M");
        assert_eq!(data(9999 << 20), ("9999".into(), "MB"));
        assert_eq!(data(10_000 << 20), ("9.8".into(), "GB"));
        assert_eq!(data(100 << 30), ("100".into(), "GB"));
        assert_eq!(data(1000 << 30), ("1.0".into(), "TB"));
        assert_eq!(data(10 << 40), ("10".into(), "TB"));
        assert!(!data(u64::MAX as u128 * 2).0.is_empty());
    }

    #[test]
    fn layout_and_json_escaping() {
        let s = Snapshot {
            iface: "eth0".into(),
            rx_rate: 1024,
            tx_rate: 2048,
            ..Snapshot::default()
        };
        assert!(render(&s, Mode::Horizontal).starts_with("{\"text\":\" 2   KB  1 \""));
        assert!(render(&s, Mode::Vertical).starts_with("{\"text\":\"2\\nKB\\n1\""));
        let mut out = String::new();
        json_string(&mut out, "\"\\\n\t\u{1}");
        assert_eq!(out, "\"\\\"\\\\\\n\\t\\u0001\"");
        assert!(render(&Snapshot::default(), Mode::Horizontal).contains("network-disconnected"));
        assert_eq!(render_osd(&s, Mode::Horizontal), "2K 1K");
        assert_eq!(render_osd(&s, Mode::Combined), "3K");
        assert_eq!(render_osd(&Snapshot::default(), Mode::Down), "Offline");
    }
}
