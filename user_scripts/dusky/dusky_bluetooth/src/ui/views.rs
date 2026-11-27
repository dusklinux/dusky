//! Pure view composition for Dusky Bluetooth: header chrome, banners, state screens,
//! grouped device cards with inline action/audio drawers, and pairing/rename modals.

use crate::bluez::BackendStatus;
use crate::icons::render_icon;
use crate::model::{
    AdapterState, AudioProfileState, DeviceState, HideScope, PairingKind, PairingPrompt,
    VisibilityMode, decode_advertising_flags, decode_appearance, decode_class,
    format_bytes_compact, format_bytes_full, format_hex_bytes, format_rate_compact,
    format_rate_full, normalize_mac, rssi_icon_name, rssi_quality_label, service_uuid_name,
    sort_devices, valid_rssi,
};
use crate::theme::{AppTheme, contrasting_fg, mix};
use crate::ui::{
    BluetoothApp, DeviceVisibility, Element, Message, RenameState, classify_device_visibility,
    format_device_info_json, format_device_info_plain, style,
};
use iced_core::{Alignment, Length, Padding, mouse};
use iced_widget::{
    Column, Row, Stack, button, column, container, mouse_area, opaque, row, scrollable, text,
    text_input,
};

fn h_spacer<'a>() -> Element<'a, Message> {
    container(Row::new()).width(Length::Fill).into()
}

fn hairline<'a>(theme: AppTheme) -> Element<'a, Message> {
    container(Row::new())
        .width(Length::Fill)
        .height(1.0)
        .style(style::hairline_rule(theme))
        .into()
}

fn subtle_divider<'a>(theme: AppTheme) -> Element<'a, Message> {
    container(Row::new())
        .width(Length::Fill)
        .height(1.0)
        .style(style::subtle_rule(theme))
        .into()
}

pub fn view_root(app: &BluetoothApp) -> Element<'_, Message> {
    let theme = app.theme;
    let active_adapter = app.active_adapter_state();

    let mut content_col = Column::new()
        .spacing(0)
        .width(Length::Fill)
        .height(Length::Fill);
    let is_discovering = active_adapter.is_some_and(|a| a.powered && a.discovering);
    content_col = content_col.push(view_header(app, active_adapter));
    content_col = content_col.push(if is_discovering {
        container(Row::new())
            .width(Length::Fill)
            .height(2.0)
            .style(style::accent_hairline(theme))
            .into()
    } else {
        hairline(theme)
    });

    if let Some((ref notice_text, is_error)) = app.notice {
        content_col = content_col.push(
            container(view_notice_banner(theme, notice_text, is_error))
                .padding(Padding {
                    top: 7.0,
                    right: 10.0,
                    bottom: 0.0,
                    left: 10.0,
                })
                .width(Length::Fill),
        );
    }

    content_col = content_col.push(view_body(app, active_adapter));

    let shell = container(content_col)
        .width(Length::Fill)
        .height(Length::Fill)
        .style(style::root_shell(theme));

    let mut layers = Stack::new().width(Length::Fill).height(Length::Fill);
    layers = layers.push(shell);

    if let Some(toast) = &app.toast {
        let toast_pill = container(
            row![
                render_icon(
                    match toast.kind {
                        crate::ui::ToastKind::Info => "info",
                        crate::ui::ToastKind::Success => "check",
                        crate::ui::ToastKind::Error => "warning",
                    },
                    14.0,
                    match toast.kind {
                        crate::ui::ToastKind::Info => theme.accent,
                        crate::ui::ToastKind::Success => theme.success,
                        crate::ui::ToastKind::Error => theme.danger,
                    },
                ),
                text(&toast.message).size(12.5).color(theme.fg),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        )
        .padding(Padding::from([8, 13]))
        .style(style::toast_card(theme, toast.kind));

        layers = layers.push(
            container(toast_pill)
                .width(Length::Fill)
                .height(Length::Fill)
                .align_x(Alignment::Center)
                .align_y(Alignment::End)
                .padding(Padding {
                    top: 0.0,
                    right: 16.0,
                    bottom: 16.0,
                    left: 16.0,
                }),
        );
    }

    if app.settings_open {
        layers = layers.push(view_settings_modal(app));
    }

    if let Some(path) = app.info_device.as_deref()
        && let Some(dev) = app.devices.get(path)
    {
        layers = layers.push(view_info_modal(app, dev));
    }

    if let Some(rename) = &app.rename_modal {
        layers = layers.push(view_rename_modal(theme, app.window_height, rename));
    }

    if let Some(prompt) = &app.pairing_prompt {
        layers = layers.push(view_pairing_modal(app, prompt));
    }

    layers.into()
}

fn view_header_traffic<'a>(
    app: &'a BluetoothApp,
    powered: bool,
    rfkill_blocked: bool,
) -> Element<'a, Message> {
    let theme = app.theme;

    if !powered && rfkill_blocked {
        let badge = theme.status_badge(theme.danger);
        let pill = container(
            row![
                render_icon("warning", 11.5, badge.fg),
                text("RF-kill").size(10.5).font(style::bold_font()).color(badge.fg),
            ]
            .spacing(4)
            .align_y(Alignment::Center),
        )
        .padding(Padding::from([3, 6]))
        .style(style::status_pill(theme, theme.danger));
        let tip = app
            .rfkill
            .detail()
            .unwrap_or_else(|| "Bluetooth is blocked by RF-kill.".to_string());
        return container(style::tooltip(
            theme,
            pill,
            tip,
            iced_widget::tooltip::Position::Bottom,
        ))
        .width(Length::Fill)
        .align_x(Alignment::End)
        .into();
    }

    if !powered || !app.traffic.available {
        return container(Row::new()).width(Length::Fill).into();
    }

    let t = &app.traffic;
    let up_color = if t.active_tx { theme.accent } else { theme.fg_muted };
    let dn_color = if t.active_rx { theme.success } else { theme.fg_muted };

    let content: Element<'a, Message> = if app.window_width < 470.0 {
        // Ultra-compact <= 11-char layout for 360px single-row fit.
        row![
            text(format!("↑{}", format_bytes_compact(t.total_tx)))
                .size(10.0)
                .font(style::mono_font())
                .color(up_color),
            text(format!("↓{}", format_bytes_compact(t.total_rx)))
                .size(10.0)
                .font(style::mono_font())
                .color(dn_color),
        ]
        .spacing(4)
        .align_y(Alignment::Center)
        .into()
    } else if app.window_width < 620.0 {
        row![
            text(format!(
                "↑{} {}",
                format_bytes_compact(t.total_tx),
                format_rate_compact(t.tx_rate_bps)
            ))
            .size(10.5)
            .font(style::mono_font())
            .color(up_color),
            text(format!(
                "↓{} {}",
                format_bytes_compact(t.total_rx),
                format_rate_compact(t.rx_rate_bps)
            ))
            .size(10.5)
            .font(style::mono_font())
            .color(dn_color),
        ]
        .spacing(6)
        .align_y(Alignment::Center)
        .into()
    } else {
        let mut r = row![
            text(format!(
                "↑ {} {}",
                format_bytes_full(t.total_tx),
                format_rate_full(t.tx_rate_bps)
            ))
            .size(10.5)
            .font(style::mono_font())
            .color(up_color),
            text(format!(
                "↓ {} {}",
                format_bytes_full(t.total_rx),
                format_rate_full(t.rx_rate_bps)
            ))
            .size(10.5)
            .font(style::mono_font())
            .color(dn_color),
        ]
        .spacing(8)
        .align_y(Alignment::Center);
        if let Some(pan) = &t.pan {
            r = r.push(
                text(format!("PAN:{}", pan.iface))
                    .size(10.0)
                    .font(style::mono_font())
                    .color(theme.info),
            );
        }
        r.into()
    };

    let mut tip_lines = vec![
        format!(
            "Adapter {} traffic",
            if t.adapter_short_id.is_empty() {
                "Bluetooth"
            } else {
                t.adapter_short_id.as_str()
            }
        ),
        format!(
            "Sent: ↑ {} ({})",
            format_bytes_full(t.total_tx),
            format_rate_full(t.tx_rate_bps)
        ),
        format!(
            "Received: ↓ {} ({})",
            format_bytes_full(t.total_rx),
            format_rate_full(t.rx_rate_bps)
        ),
    ];
    if let Some(pan) = &t.pan {
        tip_lines.push(format!(
            "PAN ({}): ↑ {} ({})  ↓ {} ({})",
            pan.iface,
            format_bytes_full(pan.total_tx),
            format_rate_full(pan.tx_rate_bps),
            format_bytes_full(pan.total_rx),
            format_rate_full(pan.rx_rate_bps)
        ));
    }

    let pill = container(content)
        .padding(Padding::from([3, 5]))
        .style(style::traffic_pill(theme));

    container(style::tooltip(
        theme,
        pill,
        tip_lines.join("\n"),
        iced_widget::tooltip::Position::Bottom,
    ))
    .width(Length::Fill)
    .align_x(Alignment::End)
    .into()
}

fn view_header<'a>(
    app: &'a BluetoothApp,
    active_adapter: Option<&'a AdapterState>,
) -> Element<'a, Message> {
    let theme = app.theme;
    let has_adapter = active_adapter.is_some();
    let powered = active_adapter.is_some_and(|a| a.powered);
    let discovering = active_adapter.is_some_and(|a| a.discovering);
    let discoverable = active_adapter.is_some_and(|a| a.discoverable);
    let scan_owned = app.active_scan_owned();
    let external_scan = discovering && !scan_owned;
    let rfkill_blocked =
        app.rfkill.is_blocked() || active_adapter.is_some_and(AdapterState::is_rfkill_blocked);

    // Power toggle (short text kept)
    let power_fg = if !has_adapter {
        theme.muted_fg()
    } else if powered {
        contrasting_fg(theme.accent)
    } else {
        theme.fg_muted
    };
    let power_btn = style::pointer_if(
        has_adapter,
        button(
            row![
                render_icon("power", 13.0, power_fg),
                text(if powered { "On" } else { "Off" })
                    .size(11.5)
                    .font(style::bold_font()),
            ]
            .spacing(4)
            .align_y(Alignment::Center),
        )
        .padding(Padding::from([4, 6]))
        .style(style::power_switch_button(theme, has_adapter && powered))
        .on_press_maybe(has_adapter.then_some(Message::TogglePower(!powered))),
    );
    let power_ctrl = style::tooltip(
        theme,
        power_btn,
        if !has_adapter {
            "No Bluetooth adapter available"
        } else if powered {
            "Turn Bluetooth off"
        } else if rfkill_blocked {
            "Bluetooth blocked by RF-kill (click to attempt power on)"
        } else {
            "Turn Bluetooth on"
        },
        iced_widget::tooltip::Position::Bottom,
    );

    // Scan / Stop toggle (short text kept)
    let can_toggle_scan = powered && !external_scan;
    let scan_fg = if !can_toggle_scan {
        theme.muted_fg()
    } else if discovering {
        contrasting_fg(theme.accent)
    } else {
        theme.fg
    };
    let scan_label = if external_scan {
        "Ext"
    } else if discovering {
        "Stop"
    } else {
        "Scan"
    };
    let scan_btn = style::pointer_if(
        can_toggle_scan,
        button(
            row![
                render_icon("scan", 13.0, scan_fg),
                text(scan_label).size(11.5).font(style::bold_font()),
            ]
            .spacing(4)
            .align_y(Alignment::Center),
        )
        .padding(Padding::from([4, 6]))
        .style(style::toggle_chip(theme, can_toggle_scan && discovering))
        .on_press_maybe(can_toggle_scan.then_some(Message::ToggleScan)),
    );
    let scan_ctrl = style::tooltip(
        theme,
        scan_btn,
        if !powered {
            "Turn on Bluetooth to scan"
        } else if external_scan {
            "Scanning started by another application"
        } else if discovering {
            "Stop scanning for nearby devices"
        } else {
            "Scan for nearby Bluetooth devices"
        },
        iced_widget::tooltip::Position::Bottom,
    );

    // Discoverable (icon-only)
    let disc_color = if !powered {
        theme.muted_fg()
    } else if discoverable {
        theme.accent
    } else {
        theme.fg_muted
    };
    let disc_btn = style::pointer_if(
        powered,
        button(render_icon("discoverable", 14.0, disc_color))
            .padding(Padding::from([5, 5]))
            .style(style::icon_button(theme, powered && discoverable))
            .on_press_maybe(powered.then_some(Message::ToggleDiscoverable(!discoverable))),
    );
    let disc_tip = if !powered {
        "Turn on Bluetooth to make this PC discoverable".to_string()
    } else if discoverable {
        let timeout = active_adapter.map_or(0, |a| a.discoverable_timeout);
        if timeout == 0 {
            "Always visible to nearby devices (click to hide)".to_string()
        } else {
            format!("Visible to nearby devices ({timeout}s timeout; click to hide)")
        }
    } else if app.config.discoverable_always {
        "Hidden (click to make always visible)".to_string()
    } else {
        format!(
            "Hidden (click to make visible for {}s)",
            app.config.discoverable_timeout_secs
        )
    };
    let disc_ctrl = style::tooltip(
        theme,
        disc_btn,
        disc_tip,
        iced_widget::tooltip::Position::Bottom,
    );

    // Hide unnamed filter (icon-only)
    let filter_btn = style::pointer(
        button(render_icon(
            "filter",
            14.0,
            if app.config.hide_unnamed {
                theme.accent
            } else {
                theme.fg_muted
            },
        ))
        .padding(Padding::from([5, 5]))
        .style(style::icon_button(theme, app.config.hide_unnamed))
        .on_press(Message::ToggleHideUnnamed),
    );
    let filter_ctrl = style::tooltip(
        theme,
        filter_btn,
        if app.config.hide_unnamed {
            "Unnamed devices hidden (click to show all)"
        } else {
            "Showing all devices (click to hide unnamed)"
        },
        iced_widget::tooltip::Position::Bottom,
    );

    // Preferences / Settings (icon-only)
    let settings_highlight = app.total_hidden_macs() > 0 || rfkill_blocked;
    let settings_btn = style::pointer(
        button(render_icon(
            "settings",
            14.0,
            if settings_highlight {
                theme.accent
            } else {
                theme.fg_muted
            },
        ))
        .padding(Padding::from([5, 5]))
        .style(style::icon_button(theme, settings_highlight))
        .on_press(Message::OpenSettings),
    );
    let settings_tip = match active_adapter {
        Some(a) if app.adapters.len() > 1 => {
            format!("Adapter preferences ({} • {})", a.display_name(), a.short_id())
        }
        _ => "Adapter preferences & hidden devices".to_string(),
    };
    let settings_ctrl = style::tooltip(
        theme,
        settings_btn,
        settings_tip,
        iced_widget::tooltip::Position::Bottom,
    );

    // Close window (icon-only)
    let close_btn = style::pointer(
        button(render_icon("close", 14.0, theme.fg_muted))
            .padding(Padding::from([5, 5]))
            .style(style::close_button(theme))
            .on_press(Message::RequestClose),
    );
    let close_ctrl = style::tooltip(
        theme,
        close_btn,
        "Close Dusky Bluetooth",
        iced_widget::tooltip::Position::Bottom,
    );

    let mut top_row = Row::new()
        .spacing(3)
        .align_y(Alignment::Center)
        .push(power_ctrl)
        .push(scan_ctrl)
        .push(disc_ctrl)
        .push(filter_ctrl);

    if let Some(adapter) = active_adapter
        && app.adapters.len() > 1
        && app.window_width >= 430.0
    {
        let adapter_btn = style::pointer(
            button(
                row![
                    text(adapter.short_id()).size(10.5).font(style::mono_font()),
                    render_icon("chevron_right", 11.0, theme.fg_muted),
                ]
                .spacing(2)
                .align_y(Alignment::Center),
            )
            .padding(Padding::from([4, 6]))
            .style(style::secondary_button(theme))
            .on_press(Message::CycleAdapter),
        );
        top_row = top_row.push(style::tooltip(
            theme,
            adapter_btn,
            format!(
                "Active adapter: {} ({}) — click to switch",
                adapter.display_name(),
                adapter.short_id()
            ),
            iced_widget::tooltip::Position::Bottom,
        ));
    }

    top_row = top_row
        .push(view_header_traffic(app, powered, rfkill_blocked))
        .push(settings_ctrl)
        .push(close_ctrl);

    container(top_row)
        .width(Length::Fill)
        .padding(Padding::from([6, 6]))
        .style(style::header_bar(theme))
        .into()
}

fn view_notice_banner<'a>(
    theme: AppTheme,
    message: &'a str,
    is_error: bool,
) -> Element<'a, Message> {
    let badge = theme.status_badge(if is_error {
        theme.danger
    } else {
        theme.warning
    });
    let icon_color = if is_error { badge.fg } else { theme.warning };
    let text_color = if is_error { badge.fg } else { theme.fg };

    let dismiss_btn = style::pointer(
        button(render_icon("close", 13.0, text_color))
            .padding(Padding::from([3, 5]))
            .style(style::close_button(theme))
            .on_press(Message::DismissNotice),
    );
    let dismiss_ctrl = style::tooltip(
        theme,
        dismiss_btn,
        "Dismiss message",
        iced_widget::tooltip::Position::Left,
    );

    container(
        row![
            render_icon(if is_error { "warning" } else { "info" }, 15.0, icon_color),
            text(message)
                .size(12.5)
                .color(text_color)
                .width(Length::Fill),
            dismiss_ctrl,
        ]
        .spacing(8)
        .align_y(Alignment::Center),
    )
    .width(Length::Fill)
    .padding(Padding::from([8, 11]))
    .style(style::notice_banner(theme, is_error))
    .into()
}


fn view_body<'a>(
    app: &'a BluetoothApp,
    active_adapter: Option<&'a AdapterState>,
) -> Element<'a, Message> {
    let theme = app.theme;

    match app.status {
        BackendStatus::Connecting => {
            return view_centered_state(
                theme,
                "bluetooth",
                theme.accent,
                "Connecting to Bluetooth…",
                "Waiting for BlueZ on the system bus.",
                None,
            );
        }
        BackendStatus::Unavailable => {
            return view_centered_state(
                theme,
                "bluetooth_off",
                theme.fg_muted,
                "Bluetooth service unavailable",
                "org.bluez is not currently active on the system bus. This window will connect automatically once bluetoothd starts.",
                None,
            );
        }
        BackendStatus::NoAdapter => {
            let (title, subtitle) = if app.rfkill.is_blocked() {
                (
                    "Bluetooth blocked by RF-kill",
                    app.rfkill.detail().unwrap_or_else(|| {
                        "Bluetooth is blocked by an RF-kill switch.".to_string()
                    }),
                )
            } else {
                (
                    "No Bluetooth adapter found",
                    "Connect a Bluetooth adapter or enable it in firmware. Hot-plugged adapters appear automatically.".to_string(),
                )
            };
            return view_centered_state(
                theme,
                if app.rfkill.is_blocked() {
                    "warning"
                } else {
                    "bluetooth_off"
                },
                if app.rfkill.is_blocked() {
                    theme.danger
                } else {
                    theme.fg_muted
                },
                title,
                subtitle,
                None,
            );
        }
        BackendStatus::Ready => {}
    }

    let Some(adapter) = active_adapter else {
        return view_centered_state(
            theme,
            "bluetooth_off",
            theme.fg_muted,
            "No Bluetooth adapter found",
            "Connect a Bluetooth adapter to manage devices.",
            None,
        );
    };

    if !adapter.powered {
        let rfkill_blocked = app.rfkill.is_blocked() || adapter.is_rfkill_blocked();
        let hard_blocked = app.rfkill.hard_blocked;
        let action = (!hard_blocked).then(|| {
            style::pointer(
                button(
                    row![
                        render_icon("power", 15.0, contrasting_fg(theme.accent)),
                        text("Turn On Bluetooth").size(13).font(style::bold_font()),
                    ]
                    .spacing(7)
                    .align_y(Alignment::Center),
                )
                .padding(Padding::from([8, 16]))
                .style(style::primary_button(theme))
                .on_press(Message::TogglePower(true)),
            )
        });
        let (icon, color, title, subtitle) = if rfkill_blocked {
            let detail = app.rfkill.detail().unwrap_or_else(|| {
                "Bluetooth is blocked by RF-kill (hardware or software switch).".to_string()
            });
            let hint = if hard_blocked {
                format!("{detail} Toggle your physical radio switch or BIOS/EC key to re-enable.")
            } else {
                format!("{detail} Unblock via `rfkill unblock bluetooth` or try turning it on.")
            };
            ("warning", theme.danger, "Bluetooth blocked by RF-kill", hint)
        } else {
            (
                "bluetooth_off",
                theme.fg_muted,
                "Bluetooth is turned off",
                "Turn Bluetooth on to connect to paired devices or scan for nearby hardware."
                    .to_string(),
            )
        };
        return view_centered_state(theme, icon, color, title, subtitle, action);
    }

    let mut paired_devices: Vec<&DeviceState> = Vec::new();
    let mut nearby_devices: Vec<&DeviceState> = Vec::new();
    let mut hidden_unnamed: usize = 0;
    let mut hidden_by_mac: usize = 0;

    for dev in app.devices.values() {
        if dev.adapter_path != adapter.path {
            continue;
        }
        match classify_device_visibility(
            dev,
            app.config.hide_unnamed,
            &app.session_hidden,
            &app.config.hidden_devices,
        ) {
            DeviceVisibility::HiddenByMac => {
                hidden_by_mac += 1;
                continue;
            }
            DeviceVisibility::HiddenUnnamed => {
                hidden_unnamed += 1;
                continue;
            }
            DeviceVisibility::Visible => {}
        }
        if dev.paired || dev.connected {
            paired_devices.push(dev);
        } else {
            nearby_devices.push(dev);
        }
    }

    sort_devices(&mut paired_devices);
    sort_devices(&mut nearby_devices);

    if paired_devices.is_empty() && nearby_devices.is_empty() {
        let mut actions = Row::new().spacing(8).align_y(Alignment::Center);
        let mut has_action = false;
        if !adapter.discovering {
            actions = actions.push(style::pointer(
                button(
                    row![
                        render_icon("scan", 14.0, contrasting_fg(theme.accent)),
                        text("Scan for Devices").size(12.5).font(style::bold_font()),
                    ]
                    .spacing(6)
                    .align_y(Alignment::Center),
                )
                .padding(Padding::from([7, 14]))
                .style(style::primary_button(theme))
                .on_press(Message::ToggleScan),
            ));
            has_action = true;
        }
        if hidden_unnamed > 0 {
            actions = actions.push(style::pointer(
                button(
                    text(format!("Show {hidden_unnamed} unnamed"))
                        .size(12)
                        .font(style::bold_font()),
                )
                .padding(Padding::from([7, 12]))
                .style(style::secondary_button(theme))
                .on_press(Message::ToggleHideUnnamed),
            ));
            has_action = true;
        }
        if hidden_by_mac > 0 {
            actions = actions.push(style::pointer(
                button(
                    text(format!("Manage {hidden_by_mac} hidden"))
                        .size(12)
                        .font(style::bold_font()),
                )
                .padding(Padding::from([7, 12]))
                .style(style::secondary_button(theme))
                .on_press(Message::OpenSettings),
            ));
            has_action = true;
        }

        let action_el = has_action.then(|| actions.wrap().into());
        if adapter.discovering {
            return view_centered_state(
                theme,
                "scan",
                theme.accent,
                "Searching for devices…",
                "Make sure your Bluetooth device is powered on and in pairing mode.",
                action_el,
            );
        }
        let subtitle = if hidden_unnamed > 0 || hidden_by_mac > 0 {
            "Start a scan or adjust hidden devices to view nearby hardware."
        } else {
            "Start a scan to discover nearby headphones, keyboards, mice, and controllers."
        };
        return view_centered_state(
            theme,
            "bluetooth",
            theme.accent,
            "No devices yet",
            subtitle,
            action_el,
        );
    }

    let mut list_col = Column::new().spacing(6).width(Length::Fill);
    let has_paired = !paired_devices.is_empty();

    if has_paired {
        let paired_trailing = (hidden_by_mac > 0 && nearby_devices.is_empty() && !adapter.discovering)
            .then(|| {
                style::pointer(
                    button(
                        text(format!("{hidden_by_mac} hidden"))
                            .size(11)
                            .color(theme.accent),
                    )
                    .padding(Padding::from([2, 6]))
                    .style(style::icon_button(theme, false))
                    .on_press(Message::OpenSettings),
                )
            });
        list_col = list_col.push(view_section_header(
            theme,
            "PAIRED & CONNECTED",
            paired_devices.len(),
            paired_trailing,
        ));
        for dev in paired_devices {
            list_col = list_col.push(view_device_card(app, adapter, dev));
        }
    }

    if !nearby_devices.is_empty() || hidden_unnamed > 0 || adapter.discovering || (hidden_by_mac > 0 && !has_paired) {
        let mut trailing_row = Row::new().spacing(4).align_y(Alignment::Center);
        let mut has_trailing = false;

        if adapter.discovering {
            let scan_txt = if app.active_scan_owned() {
                "Scanning…"
            } else {
                "External scan"
            };
            trailing_row = trailing_row.push(
                row![
                    render_icon("dot", 8.0, theme.accent),
                    text(scan_txt).size(11).color(theme.accent),
                ]
                .spacing(4)
                .align_y(Alignment::Center),
            );
            has_trailing = true;
        }
        if hidden_by_mac > 0 {
            trailing_row = trailing_row.push(style::pointer(
                button(
                    text(format!("{hidden_by_mac} hidden"))
                        .size(11)
                        .color(theme.accent),
                )
                .padding(Padding::from([2, 6]))
                .style(style::icon_button(theme, false))
                .on_press(Message::OpenSettings),
            ));
            has_trailing = true;
        }
        if hidden_unnamed > 0 {
            trailing_row = trailing_row.push(style::pointer(
                button(
                    text(format!("+{hidden_unnamed} unnamed"))
                        .size(11)
                        .color(theme.accent),
                )
                .padding(Padding::from([2, 6]))
                .style(style::icon_button(theme, false))
                .on_press(Message::ToggleHideUnnamed),
            ));
            has_trailing = true;
        }

        list_col = list_col.push(
            container(view_section_header(
                theme,
                "NEARBY DEVICES",
                nearby_devices.len(),
                has_trailing.then(|| trailing_row.into()),
            ))
            .padding(Padding {
                top: if has_paired { 4.0 } else { 0.0 },
                right: 0.0,
                bottom: 0.0,
                left: 0.0,
            }),
        );
        for dev in nearby_devices {
            list_col = list_col.push(view_device_card(app, adapter, dev));
        }
    }

    scrollable(
        container(list_col)
            .width(Length::Fill)
            .padding(Padding::from([8, 10])),
    )
    .width(Length::Fill)
    .height(Length::Fill)
    .style(style::clean_scrollable(theme))
    .into()
}

fn view_centered_state<'a>(
    theme: AppTheme,
    icon_name: &'static str,
    icon_color: iced_core::Color,
    title: &'a str,
    subtitle: impl Into<String>,
    action: Option<Element<'a, Message>>,
) -> Element<'a, Message> {
    let subtitle = subtitle.into();
    let icon_badge = container(render_icon(icon_name, 28.0, icon_color))
        .padding(14)
        .style(style::icon_tile(theme, icon_color == theme.accent));

    let mut card = Column::new()
        .spacing(10)
        .align_x(Alignment::Center)
        .max_width(360.0)
        .push(icon_badge)
        .push(
            text(title)
                .size(16)
                .font(style::bold_font())
                .color(theme.fg)
                .align_x(Alignment::Center),
        )
        .push(
            text(subtitle)
                .size(12.5)
                .color(theme.fg_muted)
                .align_x(Alignment::Center),
        );

    if let Some(btn) = action {
        card = card.push(container(btn).padding(Padding {
            top: 6.0,
            right: 0.0,
            bottom: 0.0,
            left: 0.0,
        }));
    }

    container(card)
        .width(Length::Fill)
        .height(Length::Fill)
        .center_x(Length::Fill)
        .center_y(Length::Fill)
        .padding(24)
        .into()
}

fn view_section_header<'a>(
    theme: AppTheme,
    title: &'static str,
    count: usize,
    trailing: Option<Element<'a, Message>>,
) -> Element<'a, Message> {
    let mut header = row![
        text(title)
            .size(11)
            .font(style::bold_font())
            .color(theme.muted_fg()),
        container(text(count.to_string()).size(10.5).color(theme.fg_muted))
            .padding(Padding::from([1, 6]))
            .style(style::neutral_pill(theme)),
        h_spacer(),
    ]
    .spacing(6)
    .align_y(Alignment::Center);

    if let Some(extra) = trailing {
        header = header.push(extra);
    }

    header.into()
}

fn view_device_card<'a>(
    app: &'a BluetoothApp,
    adapter: &'a AdapterState,
    dev: &'a DeviceState,
) -> Element<'a, Message> {
    let theme = app.theme;
    let expanded = app.expanded_device.as_deref() == Some(dev.path.as_str());

    let dev_icon_color = if dev.connected {
        theme.accent
    } else if dev.blocked {
        theme.danger
    } else {
        theme.fg
    };
    let icon_box = container(render_icon(dev.kind.icon_name(), 18.0, dev_icon_color))
        .padding(7)
        .style(style::icon_tile(theme, dev.connected));

    // Status metadata line under device alias
    let mut meta_row = Row::new().spacing(5).align_y(Alignment::Center);
    meta_row = meta_row.push(text(dev.kind.label()).size(11).color(theme.muted_fg()));
    if dev.connected {
        let badge = theme.status_badge(theme.success);
        meta_row = meta_row.push(
            container(
                text("Connected")
                    .size(10)
                    .font(style::bold_font())
                    .color(badge.fg),
            )
            .padding(Padding::from([1, 5]))
            .style(style::status_pill(theme, theme.success)),
        );
    }
    if dev.trusted {
        let badge = theme.status_badge(theme.info);
        meta_row = meta_row.push(
            container(text("Trusted").size(10).color(badge.fg))
                .padding(Padding::from([1, 5]))
                .style(style::status_pill(theme, theme.info)),
        );
    }
    if dev.blocked {
        let badge = theme.status_badge(theme.danger);
        meta_row = meta_row.push(
            container(text("Blocked").size(10).color(badge.fg))
                .padding(Padding::from([1, 5]))
                .style(style::status_pill(theme, theme.danger)),
        );
    }

    let info_col = column![
        text(dev.display_name())
            .size(13.5)
            .font(style::bold_font())
            .color(theme.fg)
            .wrapping(iced_core::text::Wrapping::WordOrGlyph),
        meta_row.wrap(),
    ]
    .spacing(2)
    .width(Length::Fill);

    // Compact metrics placed inside the full-card click area
    let mut metrics_row = Row::new().spacing(4).align_y(Alignment::Center);

    if let Some(pct) = dev.battery {
        let low = pct <= 20;
        let bat_semantic = if low { theme.danger } else { theme.success };
        let bat_badge = theme.status_badge(bat_semantic);
        let bat_chip = container(
            row![
                render_icon(
                    if low { "battery_low" } else { "battery" },
                    12.0,
                    bat_badge.fg,
                ),
                text(format!("{pct}%"))
                    .size(11)
                    .font(style::bold_font())
                    .color(bat_badge.fg),
            ]
            .spacing(3)
            .align_y(Alignment::Center),
        )
        .padding(Padding::from([2, 6]))
        .style(style::status_pill(theme, bat_semantic));

        metrics_row = metrics_row.push(style::tooltip(
            theme,
            bat_chip,
            format!("Battery level: {pct}%"),
            iced_widget::tooltip::Position::Top,
        ));
    }

    if let Some(rssi) = dev.rssi.and_then(valid_rssi) {
        let sig_chip = container(render_icon(rssi_icon_name(rssi), 14.0, theme.fg_muted))
            .padding(Padding::from([2, 4]))
            .style(style::neutral_pill(theme));
        metrics_row = metrics_row.push(style::tooltip(
            theme,
            sig_chip,
            format!("Signal: {rssi} dBm ({})", rssi_quality_label(rssi)),
            iced_widget::tooltip::Position::Top,
        ));
    }

    // Primary button + expand chevron stay outside the card mouse_area
    let mut right_row = Row::new().spacing(5).align_y(Alignment::Center);

    // Primary one-click action
    let primary_action: Element<'_, Message> = if let Some(op) = dev.in_flight {
        button(text(op.label()).size(12).font(style::bold_font()))
            .padding(Padding::from([5, 11]))
            .style(style::secondary_button(theme))
            .into()
    } else if dev.connected {
        style::pointer(
            button(
                row![
                    render_icon("disconnect", 13.0, theme.fg),
                    text("Disconnect").size(12).font(style::bold_font()),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding(Padding::from([5, 10]))
            .style(style::secondary_button(theme))
            .on_press(Message::DisconnectDevice(dev.path.clone())),
        )
    } else if dev.paired {
        let enabled = adapter.powered && !dev.blocked;
        let fg = if enabled {
            contrasting_fg(theme.accent)
        } else {
            theme.muted_fg()
        };
        let btn = style::pointer_if(
            enabled,
            button(
                row![
                    render_icon("connect", 13.0, fg),
                    text("Connect").size(12).font(style::bold_font()),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding(Padding::from([5, 11]))
            .style(style::primary_button(theme))
            .on_press_maybe(enabled.then(|| Message::ConnectDevice(dev.path.clone()))),
        );
        if enabled {
            btn
        } else {
            style::tooltip(
                theme,
                btn,
                if dev.blocked {
                    "Unblock this device to connect"
                } else {
                    "Turn on Bluetooth to connect"
                },
                iced_widget::tooltip::Position::Top,
            )
        }
    } else {
        let enabled = adapter.powered && !dev.blocked;
        let fg = if enabled {
            contrasting_fg(theme.accent)
        } else {
            theme.muted_fg()
        };
        let btn = style::pointer_if(
            enabled,
            button(
                row![
                    render_icon("pair", 13.0, fg),
                    text("Pair").size(12).font(style::bold_font()),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding(Padding::from([5, 11]))
            .style(style::primary_button(theme))
            .on_press_maybe(enabled.then(|| Message::PairDevice(dev.path.clone()))),
        );
        if enabled {
            btn
        } else {
            style::tooltip(
                theme,
                btn,
                if dev.blocked {
                    "Unblock this device to pair"
                } else {
                    "Turn on Bluetooth to pair"
                },
                iced_widget::tooltip::Position::Top,
            )
        }
    };
    right_row = right_row.push(primary_action);

    let expand_btn = style::pointer(
        button(render_icon(
            if expanded {
                "chevron_up"
            } else {
                "chevron_down"
            },
            15.0,
            theme.fg_muted,
        ))
        .padding(Padding::from([5, 6]))
        .style(style::icon_button(theme, expanded))
        .on_press(Message::ToggleExpand(dev.path.clone())),
    );
    right_row = right_row.push(style::tooltip(
        theme,
        expand_btn,
        if expanded {
            "Hide device actions"
        } else {
            "More device actions"
        },
        iced_widget::tooltip::Position::Left,
    ));

    let summary_area = mouse_area(
        row![icon_box, info_col, metrics_row]
            .spacing(8)
            .align_y(Alignment::Center)
            .width(Length::Fill),
    )
    .interaction(mouse::Interaction::Pointer)
    .on_press(Message::CardPressed(dev.path.clone()));

    let main_row = row![summary_area, right_row]
        .spacing(6)
        .align_y(Alignment::Center);

    let mut card_col = Column::new().spacing(7).width(Length::Fill).push(main_row);

    if expanded {
        card_col = card_col.push(subtle_divider(theme));
        card_col = card_col.push(view_device_drawer(app, adapter, dev));
    }

    container(card_col)
        .width(Length::Fill)
        .padding(Padding::from([7, 9]))
        .style(style::device_card(theme, dev.connected, expanded))
        .into()
}

fn view_device_drawer<'a>(
    app: &'a BluetoothApp,
    adapter: &'a AdapterState,
    dev: &'a DeviceState,
) -> Element<'a, Message> {
    let theme = app.theme;
    let busy = dev.in_flight.is_some();

    let copy_btn = style::pointer(
        button(render_icon("copy", 13.0, theme.fg_muted))
            .padding(Padding::from([4, 6]))
            .style(style::icon_button(theme, false))
            .on_press(Message::CopyAddress(dev.address.clone())),
    );
    let copy_ctrl = style::tooltip(
        theme,
        copy_btn,
        "Copy MAC address",
        iced_widget::tooltip::Position::Top,
    );

    let meta_line = row![
        text(&dev.address)
            .size(11.5)
            .font(style::mono_font())
            .color(theme.fg_muted),
        copy_ctrl,
        h_spacer(),
        text(if dev.paired { "Paired" } else { "Not paired" })
            .size(11.5)
            .color(theme.muted_fg()),
    ]
    .spacing(6)
    .align_y(Alignment::Center);

    let mut actions_row = Row::new().spacing(6).align_y(Alignment::Center);

    // Allow direct Connect for unpaired BLE/Just-Works devices
    if !dev.paired && !dev.connected && dev.supports_generic_connect() {
        let enabled = adapter.powered && !busy && !dev.blocked;
        let connect_btn = style::pointer_if(
            enabled,
            button(
                row![
                    render_icon(
                        "connect",
                        13.0,
                        if enabled { theme.fg } else { theme.muted_fg() }
                    ),
                    text("Connect").size(12),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding(Padding::from([5, 9]))
            .style(style::secondary_button(theme))
            .on_press_maybe(enabled.then(|| Message::ConnectDevice(dev.path.clone()))),
        );
        let connect_ctrl = if enabled {
            connect_btn
        } else {
            style::tooltip(
                theme,
                connect_btn,
                if dev.blocked {
                    "Unblock this device to connect"
                } else if !adapter.powered {
                    "Turn on Bluetooth to connect"
                } else {
                    "Operation in progress"
                },
                iced_widget::tooltip::Position::Top,
            )
        };
        actions_row = actions_row.push(connect_ctrl);
    }

    // Trust / Untrust
    let trust_btn = style::pointer(
        button(
            row![
                render_icon(
                    if dev.trusted { "untrust" } else { "trust" },
                    13.0,
                    if dev.trusted { theme.info } else { theme.fg },
                ),
                text(if dev.trusted { "Untrust" } else { "Trust" }).size(12),
            ]
            .spacing(5)
            .align_y(Alignment::Center),
        )
        .padding(Padding::from([5, 9]))
        .style(style::secondary_button(theme))
        .on_press(Message::ToggleTrust(dev.path.clone())),
    );
    actions_row = actions_row.push(style::tooltip(
        theme,
        trust_btn,
        if dev.trusted {
            "Remove trusted status"
        } else {
            "Allow device to reconnect without authorization prompts"
        },
        iced_widget::tooltip::Position::Top,
    ));

    // Block / Unblock
    let block_btn = style::pointer(
        button(
            row![
                render_icon(
                    "block",
                    13.0,
                    if dev.blocked { theme.danger } else { theme.fg },
                ),
                text(if dev.blocked { "Unblock" } else { "Block" }).size(12),
            ]
            .spacing(5)
            .align_y(Alignment::Center),
        )
        .padding(Padding::from([5, 9]))
        .style(style::secondary_button(theme))
        .on_press(Message::ToggleBlock(dev.path.clone())),
    );
    actions_row = actions_row.push(style::tooltip(
        theme,
        block_btn,
        if dev.blocked {
            "Allow connections from this device again"
        } else {
            "Reject incoming and outgoing connections for this device"
        },
        iced_widget::tooltip::Position::Top,
    ));

    // Rename
    let rename_btn = style::pointer(
        button(
            row![render_icon("edit", 12.5, theme.fg), text("Rename").size(11.5),]
                .spacing(4)
                .align_y(Alignment::Center),
        )
        .padding(Padding::from([4, 8]))
        .style(style::secondary_button(theme))
        .on_press(Message::OpenRename(dev.path.clone())),
    );
    actions_row = actions_row.push(style::tooltip(
        theme,
        rename_btn,
        "Set a custom alias for this device",
        iced_widget::tooltip::Position::Top,
    ));

    // Device Info modal trigger
    let info_btn = style::pointer(
        button(
            row![render_icon("info", 12.5, theme.accent), text("Info").size(11.5),]
                .spacing(4)
                .align_y(Alignment::Center),
        )
        .padding(Padding::from([4, 8]))
        .style(style::secondary_button(theme))
        .on_press(Message::OpenInfo(dev.path.clone())),
    );
    actions_row = actions_row.push(style::tooltip(
        theme,
        info_btn,
        "Inspect live BlueZ properties, UUIDs, and adapter details",
        iced_widget::tooltip::Position::Top,
    ));

    // Per-device Hide (only for disconnected devices; inline [For session] [Always] [Cancel])
    if !dev.connected {
        let confirming_hide = app.confirm_hide.as_deref() == Some(dev.path.as_str());
        if confirming_hide {
            let session_btn = style::pointer(
                button(text("For session").size(11.5).font(style::bold_font()))
                    .padding(Padding::from([4, 8]))
                    .style(style::secondary_button(theme))
                    .on_press(Message::HideDevice(dev.path.clone(), HideScope::Session)),
            );
            let always_btn = style::pointer(
                button(text("Always").size(11.5).font(style::bold_font()))
                    .padding(Padding::from([4, 8]))
                    .style(style::primary_button(theme))
                    .on_press(Message::HideDevice(dev.path.clone(), HideScope::Persistent)),
            );
            let cancel_btn = style::pointer(
                button(text("Cancel").size(11.5))
                    .padding(Padding::from([4, 8]))
                    .style(style::secondary_button(theme))
                    .on_press(Message::CancelHide),
            );
            actions_row = actions_row
                .push(session_btn)
                .push(always_btn)
                .push(cancel_btn);
        } else {
            let hide_btn = style::pointer(
                button(
                    row![
                        render_icon("eye_off", 12.5, theme.fg_muted),
                        text("Hide").size(11.5),
                    ]
                    .spacing(4)
                    .align_y(Alignment::Center),
                )
                .padding(Padding::from([4, 8]))
                .style(style::secondary_button(theme))
                .on_press(Message::RequestHideDevice(dev.path.clone())),
            );
            actions_row = actions_row.push(style::tooltip(
                theme,
                hide_btn,
                "Hide this device for this session or permanently",
                iced_widget::tooltip::Position::Top,
            ));
        }
    }

    // Remove (two-step inline confirmation for paired devices)
    let can_remove = adapter.powered && !busy;
    let confirming_remove =
        can_remove && app.confirm_remove.as_deref() == Some(dev.path.as_str());
    let danger_badge = theme.status_badge(theme.danger);

    if confirming_remove {
        let confirm_btn = style::pointer(
            button(
                row![
                    render_icon("trash", 13.0, contrasting_fg(theme.danger)),
                    text("Confirm Remove?").size(12).font(style::bold_font()),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding(Padding::from([5, 9]))
            .style(style::confirm_danger_button(theme))
            .on_press(Message::RemoveDevice(dev.path.clone())),
        );
        let cancel_btn = style::pointer(
            button(text("Cancel").size(12))
                .padding(Padding::from([5, 9]))
                .style(style::secondary_button(theme))
                .on_press(Message::CancelRemove),
        );
        actions_row = actions_row.push(confirm_btn).push(cancel_btn);
    } else {
        let remove_btn = style::pointer_if(
            can_remove,
            button(
                row![
                    render_icon(
                        "trash",
                        13.0,
                        if can_remove {
                            danger_badge.fg
                        } else {
                            theme.muted_fg()
                        },
                    ),
                    text("Remove").size(12),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding(Padding::from([5, 9]))
            .style(style::danger_button(theme))
            .on_press_maybe(can_remove.then(|| Message::RemoveDevice(dev.path.clone()))),
        );
        actions_row = actions_row.push(style::tooltip(
            theme,
            remove_btn,
            if !adapter.powered {
                "Turn on Bluetooth to remove this device"
            } else {
                "Remove this device from BlueZ known devices"
            },
            iced_widget::tooltip::Position::Top,
        ));
    }

    let mut drawer = Column::new().spacing(8).width(Length::Fill);
    drawer = drawer.push(meta_line);
    drawer = drawer.push(actions_row.wrap());

    if dev.connected && dev.is_audio_device() {
        drawer = drawer.push(view_audio_profile_row(app, dev));
    }

    drawer.into()
}

fn view_audio_profile_row<'a>(app: &'a BluetoothApp, dev: &'a DeviceState) -> Element<'a, Message> {
    let theme = app.theme;
    let state = app
        .audio_profiles
        .get(&dev.path)
        .unwrap_or(&AudioProfileState::Loading);

    let mut section = Column::new().spacing(6).width(Length::Fill);
    section = section.push(
        row![
            render_icon("audio", 13.0, theme.fg_muted),
            text("Audio Profile")
                .size(11.5)
                .font(style::bold_font())
                .color(theme.fg_muted),
        ]
        .spacing(6)
        .align_y(Alignment::Center),
    );

    match state {
        AudioProfileState::Loading => {
            section = section.push(
                text("Checking PipeWire audio profiles…")
                    .size(11.5)
                    .color(theme.muted_fg()),
            );
        }
        AudioProfileState::Unavailable(reason) => {
            section = section.push(text(reason).size(11.5).color(theme.muted_fg()));
        }
        AudioProfileState::Available {
            card_id,
            profiles,
            active_index,
        } => {
            let mut chips = Row::new().spacing(6).align_y(Alignment::Center);
            for profile in profiles {
                let is_active = *active_index == Some(profile.index);
                let chip = style::pointer(
                    button(text(profile.short_label()).size(11.5).font(if is_active {
                        style::bold_font()
                    } else {
                        style::regular_font()
                    }))
                    .padding(Padding::from([4, 9]))
                    .style(style::toggle_chip(theme, is_active))
                    .on_press(Message::SelectAudioProfile {
                        device_path: dev.path.clone(),
                        card_id: *card_id,
                        profile_index: profile.index,
                    }),
                );
                chips = chips.push(style::tooltip(
                    theme,
                    chip,
                    profile.description.clone(),
                    iced_widget::tooltip::Position::Top,
                ));
            }
            section = section.push(chips.wrap());
        }
    }

    container(section)
        .width(Length::Fill)
        .padding(Padding {
            top: 4.0,
            right: 0.0,
            bottom: 0.0,
            left: 0.0,
        })
        .into()
}

fn view_pairing_modal<'a>(
    app: &'a BluetoothApp,
    prompt: &'a PairingPrompt,
) -> Element<'a, Message> {
    let theme = app.theme;

    let heading = match &prompt.kind {
        PairingKind::RequestPinCode => "Enter PIN Code",
        PairingKind::RequestPasskey => "Enter Passkey",
        PairingKind::RequestConfirmation { passkey: Some(_) } => "Confirm Pairing Passkey",
        PairingKind::RequestConfirmation { passkey: None } => "Authorize Pairing",
        PairingKind::DisplayPinCode { .. } => "Enter PIN on Device",
        PairingKind::DisplayPasskey { .. } => "Enter Passkey on Device",

        PairingKind::AuthorizeService { .. } => "Authorize Bluetooth Service",
    };

    let mut body = Column::new()
        .spacing(12)
        .width(Length::Fill)
        .push(
            row![
                container(render_icon("pair", 18.0, theme.accent))
                    .padding(8)
                    .style(style::icon_tile(theme, true)),
                column![
                    text(heading)
                        .size(15)
                        .font(style::bold_font())
                        .color(theme.fg),
                    text(format!("{} ({})", prompt.device_label, prompt.address))
                        .size(12)
                        .color(theme.fg_muted),
                ]
                .spacing(2)
                .width(Length::Fill),
            ]
            .spacing(10)
            .align_y(Alignment::Center),
        )
        .push(subtle_divider(theme));

    match &prompt.kind {
        PairingKind::RequestPinCode => {
            body = body.push(
                text("Enter the PIN code (up to 16 characters) to authenticate this device:")
                    .size(12.5)
                    .color(theme.fg_muted),
            );
            let input = text_input("PIN code", &app.pairing_input)
                .id(crate::ui::pairing_input_id())
                .secure(!app.pairing_show_text)
                .on_input(Message::PairingInputChanged)
                .on_submit(Message::PairingSubmit)
                .padding(Padding::from([8, 10]))
                .size(13.5)
                .style(style::text_input_style(theme))
                .width(Length::Fill);
            let vis_btn = style::pointer(
                button(render_icon(
                    if app.pairing_show_text {
                        "eye_off"
                    } else {
                        "eye"
                    },
                    15.0,
                    theme.fg_muted,
                ))
                .padding(Padding::from([8, 9]))
                .style(style::secondary_button(theme))
                .on_press(Message::PairingToggleVisibility),
            );
            let vis_ctrl = style::tooltip(
                theme,
                vis_btn,
                if app.pairing_show_text {
                    "Hide PIN"
                } else {
                    "Show PIN"
                },
                iced_widget::tooltip::Position::Top,
            );
            body = body.push(row![input, vis_ctrl].spacing(6).align_y(Alignment::Center));

            let can_submit = !app.pairing_input.is_empty();
            let actions = row![
                h_spacer(),
                style::pointer(
                    button(text("Cancel").size(12.5))
                        .padding(Padding::from([6, 12]))
                        .style(style::secondary_button(theme))
                        .on_press(Message::PairingReject),
                ),
                style::pointer_if(
                    can_submit,
                    button(text("Pair").size(12.5).font(style::bold_font()))
                        .padding(Padding::from([6, 14]))
                        .style(style::primary_button(theme))
                        .on_press_maybe(can_submit.then_some(Message::PairingSubmit)),
                ),
            ]
            .spacing(8)
            .align_y(Alignment::Center);
            body = body.push(actions);
        }
        PairingKind::RequestPasskey => {
            body = body.push(
                text("Enter the 6-digit numeric passkey displayed on the device:")
                    .size(12.5)
                    .color(theme.fg_muted),
            );
            let input = text_input("000000", &app.pairing_input)
                .id(crate::ui::pairing_input_id())
                .on_input(Message::PairingInputChanged)
                .on_submit(Message::PairingSubmit)
                .padding(Padding::from([8, 10]))
                .size(15)
                .font(style::mono_font())
                .style(style::text_input_style(theme))
                .width(Length::Fill);
            body = body.push(input);

            let can_submit = !app.pairing_input.is_empty();
            let actions = row![
                h_spacer(),
                style::pointer(
                    button(text("Cancel").size(12.5))
                        .padding(Padding::from([6, 12]))
                        .style(style::secondary_button(theme))
                        .on_press(Message::PairingReject),
                ),
                style::pointer_if(
                    can_submit,
                    button(text("Pair").size(12.5).font(style::bold_font()))
                        .padding(Padding::from([6, 14]))
                        .style(style::primary_button(theme))
                        .on_press_maybe(can_submit.then_some(Message::PairingSubmit)),
                ),
            ]
            .spacing(8)
            .align_y(Alignment::Center);
            body = body.push(actions);
        }
        PairingKind::RequestConfirmation { passkey } => {
            if let Some(code) = passkey {
                body = body.push(
                    text(
                        "Confirm that the following passkey matches the one shown on your device:",
                    )
                    .size(12.5)
                    .color(theme.fg_muted),
                );
                body = body.push(
                    container(
                        text(format!("{code:06}"))
                            .size(28)
                            .font(style::mono_font())
                            .color(theme.accent),
                    )
                    .width(Length::Fill)
                    .center_x(Length::Fill)
                    .padding(Padding::from([12, 16]))
                    .style(style::passkey_box(theme)),
                );
            } else {
                body = body.push(
                    text("Allow this Bluetooth device to pair with your computer?")
                        .size(12.5)
                        .color(theme.fg_muted),
                );
            }

            let actions = row![
                h_spacer(),
                style::pointer(
                    button(text("Deny").size(12.5))
                        .padding(Padding::from([6, 12]))
                        .style(style::danger_button(theme))
                        .on_press(Message::PairingCancel),
                ),
                style::pointer(
                    button(text("Confirm").size(12.5).font(style::bold_font()))
                        .padding(Padding::from([6, 14]))
                        .style(style::primary_button(theme))
                        .on_press(Message::PairingConfirm),
                ),
            ]
            .spacing(8)
            .align_y(Alignment::Center);
            body = body.push(actions);
        }
        PairingKind::DisplayPinCode { pin } => {
            body = body.push(
                text("Type this PIN code on the remote Bluetooth device and press Enter:")
                    .size(12.5)
                    .color(theme.fg_muted),
            );
            body = body.push(
                container(
                    text(pin)
                        .size(26)
                        .font(style::mono_font())
                        .color(theme.accent),
                )
                .width(Length::Fill)
                .center_x(Length::Fill)
                .padding(Padding::from([12, 16]))
                .style(style::passkey_box(theme)),
            );
            body = body.push(
                row![
                    text("Waiting for device confirmation…")
                        .size(11.5)
                        .color(theme.muted_fg())
                        .width(Length::Fill),
                    style::pointer(
                        button(text("Cancel Pairing").size(12))
                            .padding(Padding::from([5, 11]))
                            .style(style::secondary_button(theme))
                            .on_press(Message::PairingCancel),
                    ),
                ]
                .spacing(8)
                .align_y(Alignment::Center),
            );
        }
        PairingKind::DisplayPasskey { passkey, entered } => {
            body = body.push(
                text("Type this passkey on the remote Bluetooth device and press Enter:")
                    .size(12.5)
                    .color(theme.fg_muted),
            );
            let digits = format!("{passkey:06}");
            let entered_idx = usize::from(*entered);
            let mut digit_row = Row::new().spacing(8).align_y(Alignment::Center);
            for (idx, ch) in digits.chars().enumerate() {
                let digit_color = if idx < entered_idx {
                    theme.success
                } else if idx == entered_idx {
                    theme.accent
                } else {
                    mix(theme.fg, theme.fg_muted, 0.35)
                };
                digit_row = digit_row.push(
                    text(ch.to_string())
                        .size(28)
                        .font(style::mono_font())
                        .color(digit_color),
                );
            }
            body = body.push(
                container(digit_row)
                    .width(Length::Fill)
                    .center_x(Length::Fill)
                    .padding(Padding::from([12, 16]))
                    .style(style::passkey_box(theme)),
            );
            body = body.push(
                row![
                    text("Waiting for device input…")
                        .size(11.5)
                        .color(theme.muted_fg())
                        .width(Length::Fill),
                    style::pointer(
                        button(text("Cancel Pairing").size(12))
                            .padding(Padding::from([5, 11]))
                            .style(style::secondary_button(theme))
                            .on_press(Message::PairingCancel),
                    ),
                ]
                .spacing(8)
                .align_y(Alignment::Center),
            );
        }
        PairingKind::AuthorizeService { service_name, uuid } => {
            body = body.push(
                text("This device is requesting access to a Bluetooth service:")
                    .size(12.5)
                    .color(theme.fg_muted),
            );
            body = body.push(
                container(
                    column![
                        text(service_name)
                            .size(13.5)
                            .font(style::bold_font())
                            .color(theme.fg),
                        text(uuid)
                            .size(11)
                            .font(style::mono_font())
                            .color(theme.muted_fg()),
                    ]
                    .spacing(3),
                )
                .width(Length::Fill)
                .padding(Padding::from([9, 12]))
                .style(style::passkey_box(theme)),
            );

            let actions = row![
                style::pointer(
                    button(text("Deny").size(12.5))
                        .padding(Padding::from([6, 11]))
                        .style(style::danger_button(theme))
                        .on_press(Message::PairingReject),
                ),
                h_spacer(),
                style::pointer(
                    button(text("Accept").size(12.5))
                        .padding(Padding::from([6, 11]))
                        .style(style::secondary_button(theme))
                        .on_press(Message::PairingAcceptService(false)),
                ),
                style::pointer(
                    button(text("Always Accept").size(12.5).font(style::bold_font()))
                        .padding(Padding::from([6, 12]))
                        .style(style::primary_button(theme))
                        .on_press(Message::PairingAcceptService(true)),
                ),
            ]
            .spacing(8)
            .align_y(Alignment::Center);
            body = body.push(actions);
        }
    }

    let max_h = (app.window_height - 24.0).max(240.0);
    let modal_card = container(
        scrollable(body)
            .width(Length::Fill)
            .height(Length::Shrink)
            .style(style::clean_scrollable(theme)),
    )
    .width(Length::Fill)
    .max_width(390.0)
    .max_height(max_h)
    .padding(14)
    .style(style::card_surface(theme, 12.0));

    opaque(
        container(modal_card)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .padding(12)
            .style(style::backdrop_scrim(theme)),
    )
}

fn view_rename_modal<'a>(
    theme: AppTheme,
    window_height: f32,
    rename: &'a RenameState,
) -> Element<'a, Message> {
    let header = row![
        container(render_icon("edit", 16.0, theme.accent))
            .padding(7)
            .style(style::icon_tile(theme, true)),
        column![
            text("Rename Device")
                .size(14.5)
                .font(style::bold_font())
                .color(theme.fg),
            text(&rename.address)
                .size(11.5)
                .font(style::mono_font())
                .color(theme.fg_muted),
        ]
        .spacing(2)
        .width(Length::Fill),
    ]
    .spacing(10)
    .align_y(Alignment::Center);

    let input = text_input("Device alias", &rename.draft)
        .id(crate::ui::rename_input_id())
        .on_input(Message::RenameDraftChanged)
        .on_submit(Message::RenameSave)
        .padding(Padding::from([7, 10]))
        .size(13)
        .style(style::text_input_style(theme))
        .width(Length::Fill);

    let reset_btn = style::pointer(
        button(text("Reset").size(12))
            .padding(Padding::from([5, 10]))
            .style(style::secondary_button(theme))
            .on_press(Message::RenameReset),
    );
    let reset_ctrl = style::tooltip(
        theme,
        reset_btn,
        "Restore default hardware name",
        iced_widget::tooltip::Position::Top,
    );

    let actions = row![
        reset_ctrl,
        h_spacer(),
        style::pointer(
            button(text("Cancel").size(12))
                .padding(Padding::from([5, 11]))
                .style(style::secondary_button(theme))
                .on_press(Message::CloseRename),
        ),
        style::pointer(
            button(text("Save").size(12).font(style::bold_font()))
                .padding(Padding::from([5, 13]))
                .style(style::primary_button(theme))
                .on_press(Message::RenameSave),
        ),
    ]
    .spacing(8)
    .align_y(Alignment::Center);

    let max_h = (window_height - 24.0).max(240.0);
    let card = container(
        scrollable(
            column![
                header,
                subtle_divider(theme),
                text("Set a custom alias for this Bluetooth device:")
                    .size(12)
                    .color(theme.fg_muted),
                input,
                actions,
            ]
            .spacing(10)
            .width(Length::Fill),
        )
        .width(Length::Fill)
        .height(Length::Shrink)
        .style(style::clean_scrollable(theme)),
    )
    .width(Length::Fill)
    .max_width(370.0)
    .max_height(max_h)
    .padding(14)
    .style(style::card_surface(theme, 12.0));

    opaque(
        container(card)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .padding(12)
            .style(style::backdrop_scrim(theme)),
    )
}

fn info_kv_row<'a>(
    theme: AppTheme,
    label: impl Into<String>,
    value: impl Into<String>,
    mono: bool,
) -> Element<'a, Message> {
    let label_str = label.into();
    let value_str = value.into();
    let val_text = text(value_str.clone())
        .size(if mono { 11.0 } else { 11.5 })
        .font(if mono {
            style::mono_font()
        } else {
            style::regular_font()
        })
        .color(theme.fg)
        .wrapping(iced_core::text::Wrapping::WordOrGlyph)
        .width(Length::Fill);

    let row_el = row![
        text(label_str.clone())
            .size(11)
            .font(style::bold_font())
            .color(theme.muted_fg())
            .width(Length::Fixed(116.0)),
        val_text,
    ]
    .spacing(8)
    .align_y(Alignment::Start)
    .width(Length::Fill);

    style::pointer(
        button(row_el)
            .width(Length::Fill)
            .padding(Padding::from([3, 6]))
            .style(style::info_row_button(theme))
            .on_press(Message::CopyText {
                label: label_str,
                value: value_str,
            }),
    )
}

fn info_section<'a>(
    theme: AppTheme,
    title: &'static str,
    rows: Vec<Element<'a, Message>>,
) -> Element<'a, Message> {
    let mut col = Column::new().spacing(2).width(Length::Fill);
    for r in rows {
        col = col.push(r);
    }
    column![
        text(title)
            .size(10.5)
            .font(style::bold_font())
            .color(theme.muted_fg()),
        container(col)
            .width(Length::Fill)
            .padding(Padding::from([5, 6]))
            .style(style::modal_section_box(theme)),
    ]
    .spacing(4)
    .width(Length::Fill)
    .into()
}

fn view_info_modal<'a>(app: &'a BluetoothApp, dev: &'a DeviceState) -> Element<'a, Message> {
    let theme = app.theme;
    let adapter = app.adapters.get(&dev.adapter_path);
    let yn = |b: bool| if b { "Yes" } else { "No" };

    let copy_text_btn = style::pointer(
        button(
            row![render_icon("copy", 12.0, theme.fg), text("Text").size(11.5)]
                .spacing(4)
                .align_y(Alignment::Center),
        )
        .padding(Padding::from([4, 8]))
        .style(style::secondary_button(theme))
        .on_press(Message::CopyText {
            label: "device info".into(),
            value: format_device_info_plain(dev, adapter),
        }),
    );
    let copy_json_btn = style::pointer(
        button(
            row![render_icon("copy", 12.0, theme.fg), text("JSON").size(11.5)]
                .spacing(4)
                .align_y(Alignment::Center),
        )
        .padding(Padding::from([4, 8]))
        .style(style::secondary_button(theme))
        .on_press(Message::CopyText {
            label: "device info JSON".into(),
            value: format_device_info_json(dev, adapter),
        }),
    );
    let close_btn = style::pointer(
        button(render_icon("close", 14.0, theme.fg_muted))
            .padding(Padding::from([5, 5]))
            .style(style::close_button(theme))
            .on_press(Message::CloseInfo),
    );

    let header = row![
        container(render_icon(dev.kind.icon_name(), 17.0, theme.accent))
            .padding(7)
            .style(style::icon_tile(theme, dev.connected)),
        column![
            text(dev.display_name())
                .size(14)
                .font(style::bold_font())
                .color(theme.fg)
                .wrapping(iced_core::text::Wrapping::WordOrGlyph),
            text(&dev.address)
                .size(11)
                .font(style::mono_font())
                .color(theme.fg_muted),
        ]
        .spacing(1)
        .width(Length::Fill),
        style::tooltip(
            theme,
            copy_text_btn,
            "Copy all properties as plain text",
            iced_widget::tooltip::Position::Bottom,
        ),
        style::tooltip(
            theme,
            copy_json_btn,
            "Copy all properties as JSON",
            iced_widget::tooltip::Position::Bottom,
        ),
        close_btn,
    ]
    .spacing(6)
    .align_y(Alignment::Center);

    // 1. Device
    let mut device_rows = vec![info_kv_row(theme, "Alias", dev.alias.clone(), false)];
    if let Some(name) = &dev.name {
        device_rows.push(info_kv_row(theme, "Name", name.clone(), false));
    }
    let addr_val = match &dev.address_type {
        Some(at) => format!("{} ({at})", dev.address),
        None => dev.address.clone(),
    };
    device_rows.push(info_kv_row(theme, "Address", addr_val, true));
    device_rows.push(info_kv_row(theme, "Kind", dev.kind.label(), false));
    device_rows.push(info_kv_row(theme, "Paired", yn(dev.paired), false));
    device_rows.push(info_kv_row(theme, "Bonded", yn(dev.bonded), false));
    device_rows.push(info_kv_row(theme, "Connected", yn(dev.connected), false));
    device_rows.push(info_kv_row(theme, "Trusted", yn(dev.trusted), false));
    device_rows.push(info_kv_row(theme, "Blocked", yn(dev.blocked), false));
    device_rows.push(info_kv_row(
        theme,
        "Services Resolved",
        yn(dev.services_resolved),
        false,
    ));
    device_rows.push(info_kv_row(
        theme,
        "Legacy Pairing",
        yn(dev.legacy_pairing),
        false,
    ));
    if let Some(w) = dev.wake_allowed {
        device_rows.push(info_kv_row(theme, "Wake Allowed", yn(w), false));
    }
    if let Some(pb) = &dev.preferred_bearer {
        device_rows.push(info_kv_row(theme, "Preferred Bearer", pb.clone(), false));
    }
    if let Some(cp) = dev.cable_pairing {
        device_rows.push(info_kv_row(theme, "Cable Pairing", yn(cp), false));
    }

    // 2. Hardware & Radio
    let mut hw_rows = Vec::new();
    if dev.class != 0 {
        let val = decode_class(dev.class)
            .map(|d| format!("{d} (0x{:06x})", dev.class))
            .unwrap_or_else(|| format!("0x{:06x}", dev.class));
        hw_rows.push(info_kv_row(theme, "Class", val, false));
    }
    if dev.appearance != 0 {
        let val = decode_appearance(dev.appearance)
            .map(|d| format!("{d} (0x{:04x})", dev.appearance))
            .unwrap_or_else(|| format!("0x{:04x}", dev.appearance));
        hw_rows.push(info_kv_row(theme, "Appearance", val, false));
    }
    if let Some(icon) = &dev.icon_hint {
        hw_rows.push(info_kv_row(theme, "Icon", icon.clone(), true));
    }
    if let Some(pct) = dev.battery {
        hw_rows.push(info_kv_row(theme, "Battery", format!("{pct}%"), false));
    }
    if let Some(rssi) = dev.rssi.and_then(valid_rssi) {
        hw_rows.push(info_kv_row(
            theme,
            "RSSI",
            format!("{rssi} dBm ({})", rssi_quality_label(rssi)),
            false,
        ));
    }
    if let Some(txp) = dev.tx_power {
        hw_rows.push(info_kv_row(theme, "Tx Power", format!("{txp} dBm"), false));
    }
    if let Some(m) = &dev.modalias {
        hw_rows.push(info_kv_row(theme, "Modalias", m.clone(), true));
    }

    // 3. Services (UUIDs)
    let mut svc_rows = Vec::new();
    if dev.uuids.is_empty() {
        svc_rows.push(
            container(
                text("No services advertised")
                    .size(11.5)
                    .color(theme.muted_fg()),
            )
            .padding(Padding::from([3, 6]))
            .into(),
        );
    } else {
        for uuid in &dev.uuids {
            svc_rows.push(info_kv_row(
                theme,
                service_uuid_name(uuid),
                uuid.clone(),
                true,
            ));
        }
    }

    // 4. Advertising Data (only when non-empty)
    let mut adv_rows = Vec::new();
    if !dev.advertising_flags.is_empty() {
        adv_rows.push(info_kv_row(
            theme,
            "Adv Flags",
            decode_advertising_flags(&dev.advertising_flags),
            false,
        ));
    }
    for (company, bytes) in &dev.manufacturer_data {
        adv_rows.push(info_kv_row(
            theme,
            format!("Mfg 0x{company:04x}"),
            format_hex_bytes(bytes),
            true,
        ));
    }
    for (uuid, bytes) in &dev.service_data {
        adv_rows.push(info_kv_row(
            theme,
            service_uuid_name(uuid),
            format_hex_bytes(bytes),
            true,
        ));
    }

    // 5. Parent Adapter
    let mut adapter_rows = Vec::new();
    if let Some(a) = adapter {
        adapter_rows.push(info_kv_row(theme, "Alias", a.alias.clone(), false));
        if !a.name.is_empty() {
            adapter_rows.push(info_kv_row(theme, "Name", a.name.clone(), false));
        }
        let a_addr = match &a.address_type {
            Some(at) => format!("{} ({at})", a.address),
            None => a.address.clone(),
        };
        adapter_rows.push(info_kv_row(theme, "Address", a_addr, true));
        adapter_rows.push(info_kv_row(theme, "Controller", a.short_id(), true));
        if a.class != 0 {
            let val = decode_class(a.class)
                .map(|d| format!("{d} (0x{:06x})", a.class))
                .unwrap_or_else(|| format!("0x{:06x}", a.class));
            adapter_rows.push(info_kv_row(theme, "Class", val, false));
        }
        let pwr_val = match &a.power_state {
            Some(ps) => format!("{} ({ps})", yn(a.powered)),
            None => yn(a.powered).to_string(),
        };
        adapter_rows.push(info_kv_row(theme, "Powered", pwr_val, false));
        let disc_val = if a.discoverable {
            if a.discoverable_timeout == 0 {
                "Always".to_string()
            } else {
                format!("Yes ({}s)", a.discoverable_timeout)
            }
        } else {
            "No".to_string()
        };
        adapter_rows.push(info_kv_row(theme, "Discoverable", disc_val, false));
        let pair_val = if a.pairable {
            if a.pairable_timeout == 0 {
                "Yes (Always)".to_string()
            } else {
                format!("Yes ({}s)", a.pairable_timeout)
            }
        } else {
            "No".to_string()
        };
        adapter_rows.push(info_kv_row(theme, "Pairable", pair_val, false));
        if let Some(m) = &a.modalias {
            adapter_rows.push(info_kv_row(theme, "Modalias", m.clone(), true));
        }
    } else {
        adapter_rows.push(info_kv_row(
            theme,
            "Controller",
            dev.adapter_path.clone(),
            true,
        ));
    }

    let mut sections = Column::new()
        .spacing(8)
        .width(Length::Fill)
        .push(info_section(theme, "DEVICE", device_rows));
    if !hw_rows.is_empty() {
        sections = sections.push(info_section(theme, "HARDWARE & RADIO", hw_rows));
    }
    sections = sections.push(info_section(theme, "SERVICES (UUIDs)", svc_rows));
    if !adv_rows.is_empty() {
        sections = sections.push(info_section(theme, "ADVERTISING DATA", adv_rows));
    }
    sections = sections.push(info_section(theme, "ADAPTER", adapter_rows));

    let hint = text("Click any row to copy its value.")
        .size(10.5)
        .color(theme.muted_fg());

    let max_h = (app.window_height - 24.0).max(240.0);
    let card = container(
        column![
            header,
            subtle_divider(theme),
            scrollable(sections)
                .width(Length::Fill)
                .height(Length::Shrink)
                .style(style::clean_scrollable(theme)),
            hint,
        ]
        .spacing(8)
        .width(Length::Fill),
    )
    .width(Length::Fill)
    .max_width(430.0)
    .max_height(max_h)
    .padding(14)
    .style(style::card_surface(theme, 12.0));

    opaque(
        container(card)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .padding(12)
            .style(style::backdrop_scrim(theme)),
    )
}

fn view_settings_modal<'a>(app: &'a BluetoothApp) -> Element<'a, Message> {
    let theme = app.theme;
    let active_adapter = app.active_adapter_state();

    let close_btn = style::pointer(
        button(render_icon("close", 14.0, theme.fg_muted))
            .padding(Padding::from([5, 5]))
            .style(style::close_button(theme))
            .on_press(Message::CloseSettings),
    );
    let header = row![
        container(render_icon("settings", 16.0, theme.accent))
            .padding(7)
            .style(style::icon_tile(theme, true)),
        column![
            text("Adapter Preferences")
                .size(14.5)
                .font(style::bold_font())
                .color(theme.fg),
            text(match active_adapter {
                Some(a) => format!("{} ({})", a.short_id(), a.address),
                None => "No active Bluetooth adapter".to_string(),
            })
            .size(11)
            .font(style::mono_font())
            .color(theme.fg_muted),
        ]
        .spacing(1)
        .width(Length::Fill),
        close_btn,
    ]
    .spacing(8)
    .align_y(Alignment::Center);

    let mut body = Column::new().spacing(10).width(Length::Fill);

    // 1. Multi-adapter selector (always available here, including at <430px width)
    if app.adapters.len() > 1 {
        let mut chips = Row::new().spacing(5).align_y(Alignment::Center);
        for adapter in app.adapters.values() {
            let selected = app.active_adapter.as_deref() == Some(adapter.path.as_str());
            let label = format!("{} ({})", adapter.display_name(), adapter.short_id());
            chips = chips.push(style::pointer(
                button(text(label).size(11.5).font(if selected {
                    style::bold_font()
                } else {
                    style::regular_font()
                }))
                .padding(Padding::from([4, 8]))
                .style(style::toggle_chip(theme, selected))
                .on_press(Message::SelectAdapter(adapter.path.clone())),
            ));
        }
        body = body.push(
            column![
                text("ADAPTER")
                    .size(10.5)
                    .font(style::bold_font())
                    .color(theme.muted_fg()),
                chips.wrap(),
            ]
            .spacing(5)
            .width(Length::Fill),
        );
    }

    if let Some(adapter) = active_adapter {
        let powered = adapter.powered;

        // 2. Local Adapter Alias
        let alias_input = text_input("Adapter alias", &app.adapter_alias_draft)
            .id(crate::ui::adapter_alias_input_id())
            .on_input(Message::AdapterAliasDraftChanged)
            .on_submit(Message::AdapterAliasSave)
            .padding(Padding::from([6, 9]))
            .size(12.5)
            .style(style::text_input_style(theme))
            .width(Length::Fill);
        let save_alias = style::pointer(
            button(text("Save").size(11.5).font(style::bold_font()))
                .padding(Padding::from([6, 10]))
                .style(style::primary_button(theme))
                .on_press(Message::AdapterAliasSave),
        );
        let reset_alias = style::pointer(
            button(text("Reset").size(11.5))
                .padding(Padding::from([6, 9]))
                .style(style::secondary_button(theme))
                .on_press(Message::AdapterAliasReset),
        );
        body = body.push(
            column![
                text("LOCAL ADAPTER NAME")
                    .size(10.5)
                    .font(style::bold_font())
                    .color(theme.muted_fg()),
                row![alias_input, save_alias, reset_alias]
                    .spacing(5)
                    .align_y(Alignment::Center),
            ]
            .spacing(5)
            .width(Length::Fill),
        );

        // 3. Visibility Mode (live BlueZ state; clicking Always/Temporary also saves preset)
        let live_mode = adapter.visibility_mode();
        let is_hidden = matches!(live_mode, VisibilityMode::Hidden);
        let is_always = matches!(live_mode, VisibilityMode::AlwaysVisible);
        let is_temp = matches!(live_mode, VisibilityMode::Temporary { .. });
        let current_temp_secs = match live_mode {
            VisibilityMode::Temporary { timeout_secs } => timeout_secs,
            _ => app.config.discoverable_timeout_secs,
        };

        let mode_row = row![
            style::pointer_if(
                powered,
                button(text("Hidden").size(11.5).font(if is_hidden {
                    style::bold_font()
                } else {
                    style::regular_font()
                }))
                .padding(Padding::from([5, 9]))
                .style(style::toggle_chip(theme, powered && is_hidden))
                .on_press_maybe(powered.then_some(Message::SetVisibilityMode(VisibilityMode::Hidden))),
            ),
            style::pointer_if(
                powered,
                button(text("Always visible").size(11.5).font(if is_always {
                    style::bold_font()
                } else {
                    style::regular_font()
                }))
                .padding(Padding::from([5, 9]))
                .style(style::toggle_chip(theme, powered && is_always))
                .on_press_maybe(
                    powered.then_some(Message::SetVisibilityMode(VisibilityMode::AlwaysVisible))
                ),
            ),
            style::pointer_if(
                powered,
                button(text("Temporary").size(11.5).font(if is_temp {
                    style::bold_font()
                } else {
                    style::regular_font()
                }))
                .padding(Padding::from([5, 9]))
                .style(style::toggle_chip(theme, powered && is_temp))
                .on_press_maybe(powered.then_some(Message::SetVisibilityMode(
                    VisibilityMode::Temporary {
                        timeout_secs: app.config.discoverable_timeout_secs,
                    }
                ))),
            ),
        ]
        .spacing(5)
        .align_y(Alignment::Center)
        .wrap();

        let mut timeout_row = Row::new().spacing(4).align_y(Alignment::Center);
        for (label, secs) in [
            ("1m", 60_u32),
            ("3m", 180),
            ("5m", 300),
            ("10m", 600),
            ("30m", 1800),
        ] {
            let selected = current_temp_secs == secs && !is_always;
            timeout_row = timeout_row.push(style::pointer_if(
                powered,
                button(text(label).size(11).font(if selected {
                    style::bold_font()
                } else {
                    style::regular_font()
                }))
                .padding(Padding::from([3, 7]))
                .style(style::toggle_chip(theme, powered && selected))
                .on_press_maybe(powered.then_some(Message::SetVisibilityMode(
                    VisibilityMode::Temporary { timeout_secs: secs },
                ))),
            ));
        }

        let vis_col = column![
            text("VISIBILITY (DISCOVERABLE)")
                .size(10.5)
                .font(style::bold_font())
                .color(theme.muted_fg()),
            mode_row,
            row![
                text("Timeout:")
                    .size(11)
                    .color(theme.muted_fg()),
                timeout_row.wrap(),
            ]
            .spacing(6)
            .align_y(Alignment::Center),
        ]
        .spacing(5)
        .width(Length::Fill);
        body = body.push(vis_col);

        // 4. Pairing (Pairable + PairableTimeout)
        let pair_toggle = style::pointer_if(
            powered,
            button(
                text(if adapter.pairable { "Pairable: On" } else { "Pairable: Off" })
                    .size(11.5)
                    .font(style::bold_font()),
            )
            .padding(Padding::from([4, 9]))
            .style(style::toggle_chip(theme, powered && adapter.pairable))
            .on_press_maybe(powered.then_some(Message::SetPairable(!adapter.pairable))),
        );
        let mut ptime_row = Row::new().spacing(4).align_y(Alignment::Center);
        for (label, secs) in [("Always", 0_u32), ("3m", 180), ("5m", 300)] {
            let selected = adapter.pairable_timeout == secs;
            ptime_row = ptime_row.push(style::pointer_if(
                powered && adapter.pairable,
                button(text(label).size(11).font(if selected {
                    style::bold_font()
                } else {
                    style::regular_font()
                }))
                .padding(Padding::from([3, 7]))
                .style(style::toggle_chip(theme, powered && adapter.pairable && selected))
                .on_press_maybe(
                    (powered && adapter.pairable).then_some(Message::SetPairableTimeout(secs)),
                ),
            ));
        }
        body = body.push(
            column![
                text("PAIRING")
                    .size(10.5)
                    .font(style::bold_font())
                    .color(theme.muted_fg()),
                row![pair_toggle, ptime_row.wrap()]
                    .spacing(6)
                    .align_y(Alignment::Center)
                    .wrap(),
            ]
            .spacing(5)
            .width(Length::Fill),
        );
    }

    // 5. Hidden Devices Manager
    let mut hidden_entries: Vec<(String, bool)> = Vec::new();
    for mac in &app.config.hidden_devices {
        hidden_entries.push((mac.clone(), true));
    }
    let mut session_only: Vec<String> = app
        .session_hidden
        .iter()
        .filter(|mac| !app.config.hidden_devices.contains(*mac))
        .cloned()
        .collect();
    session_only.sort();
    for mac in session_only {
        hidden_entries.push((mac, false));
    }

    let mut hidden_header = row![
        text("HIDDEN DEVICES")
            .size(10.5)
            .font(style::bold_font())
            .color(theme.muted_fg()),
        h_spacer(),
    ]
    .align_y(Alignment::Center);
    if !hidden_entries.is_empty() {
        hidden_header = hidden_header.push(style::pointer(
            button(text("Unhide all").size(11).color(theme.accent))
                .padding(Padding::from([2, 6]))
                .style(style::icon_button(theme, false))
                .on_press(Message::UnhideAllDevices),
        ));
    }

    let mut hidden_list = Column::new().spacing(4).width(Length::Fill);
    if hidden_entries.is_empty() {
        hidden_list = hidden_list.push(
            text("No hidden devices. Use Hide in a device drawer to filter noisy hardware.")
                .size(11)
                .color(theme.muted_fg()),
        );
    } else {
        for (mac, persistent) in hidden_entries {
            let known_name = app
                .devices
                .values()
                .find(|d| normalize_mac(&d.address) == mac)
                .map(|d| d.display_name().to_string());
            let scope_badge = if persistent {
                let b = theme.status_badge(theme.info);
                container(text("Always").size(10).color(b.fg))
                    .padding(Padding::from([1, 5]))
                    .style(style::status_pill(theme, theme.info))
            } else {
                container(text("Session").size(10).color(theme.fg_muted))
                    .padding(Padding::from([1, 5]))
                    .style(style::neutral_pill(theme))
            };
            let label_col = match known_name {
                Some(name) => column![
                    text(name).size(11.5).font(style::bold_font()).color(theme.fg),
                    text(mac.clone())
                        .size(10.5)
                        .font(style::mono_font())
                        .color(theme.muted_fg()),
                ]
                .spacing(1)
                .width(Length::Fill),
                None => column![
                    text(mac.clone())
                        .size(11)
                        .font(style::mono_font())
                        .color(theme.fg),
                ]
                .width(Length::Fill),
            };
            let unhide_btn = style::pointer(
                button(text("Unhide").size(11))
                    .padding(Padding::from([3, 8]))
                    .style(style::secondary_button(theme))
                    .on_press(Message::UnhideDevice(mac)),
            );
            hidden_list = hidden_list.push(
                row![label_col, scope_badge, unhide_btn]
                    .spacing(6)
                    .align_y(Alignment::Center),
            );
        }
    }
    body = body.push(
        column![
            hidden_header,
            container(hidden_list)
                .width(Length::Fill)
                .padding(Padding::from([6, 8]))
                .style(style::modal_section_box(theme)),
        ]
        .spacing(4)
        .width(Length::Fill),
    );

    // 6. RF-kill & Adapter Footer
    let rfkill_blocked =
        app.rfkill.is_blocked() || active_adapter.is_some_and(AdapterState::is_rfkill_blocked);
    if rfkill_blocked {
        let badge = theme.status_badge(theme.danger);
        let msg = app
            .rfkill
            .detail()
            .unwrap_or_else(|| "Bluetooth is blocked by RF-kill.".to_string());
        body = body.push(
            container(
                row![
                    render_icon("warning", 14.0, badge.fg),
                    text(msg)
                        .size(11.5)
                        .color(badge.fg)
                        .width(Length::Fill),
                ]
                .spacing(7)
                .align_y(Alignment::Center),
            )
            .width(Length::Fill)
            .padding(Padding::from([6, 8]))
            .style(style::notice_banner(theme, true)),
        );
    }

    let max_h = (app.window_height - 24.0).max(240.0);
    let card = container(
        column![
            header,
            subtle_divider(theme),
            scrollable(body)
                .width(Length::Fill)
                .height(Length::Shrink)
                .style(style::clean_scrollable(theme)),
        ]
        .spacing(9)
        .width(Length::Fill),
    )
    .width(Length::Fill)
    .max_width(410.0)
    .max_height(max_h)
    .padding(14)
    .style(style::card_surface(theme, 12.0));

    opaque(
        container(card)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .padding(12)
            .style(style::backdrop_scrim(theme)),
    )
}
