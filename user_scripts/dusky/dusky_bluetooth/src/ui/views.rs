//! Pure view composition for Dusky Bluetooth: header chrome, banners, state screens,
//! grouped device cards with inline action/audio drawers, and pairing/rename modals.

use crate::bluez::BackendStatus;
use crate::icons::render_icon;
use crate::model::{
    AdapterState, AudioProfileState, DeviceState, PairingKind, PairingPrompt, rssi_icon_name,
    rssi_quality_label, sort_devices, valid_rssi,
};
use crate::theme::{AppTheme, contrasting_fg, mix};
use crate::ui::{BluetoothApp, Element, Message, RenameState, style};
use iced_core::{Alignment, Length, Padding};
use iced_widget::{
    Column, Row, Stack, button, column, container, opaque, row, scrollable, text, text_input,
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
    content_col = content_col.push(view_header(app, active_adapter));
    content_col = content_col.push(hairline(theme));

    if let Some((ref notice_text, is_error)) = app.notice {
        content_col = content_col.push(
            container(view_notice_banner(theme, notice_text, is_error))
                .padding(Padding {
                    top: 10.0,
                    right: 14.0,
                    bottom: 0.0,
                    left: 14.0,
                })
                .width(Length::Fill),
        );
    }

    if let Some(adapter) = active_adapter
        && adapter.powered
        && adapter.discovering
    {
        content_col = content_col.push(
            container(view_scanning_banner(theme, app.active_scan_owned()))
                .padding(Padding {
                    top: 10.0,
                    right: 14.0,
                    bottom: 0.0,
                    left: 14.0,
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

    if let Some(rename) = &app.rename_modal {
        layers = layers.push(view_rename_modal(theme, rename));
    }

    if let Some(prompt) = &app.pairing_prompt {
        layers = layers.push(view_pairing_modal(app, prompt));
    }

    layers.into()
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

    let title_icon_color = if powered {
        theme.accent
    } else {
        theme.fg_muted
    };
    let brand_row = row![
        render_icon(
            if powered {
                "bluetooth"
            } else {
                "bluetooth_off"
            },
            18.0,
            title_icon_color,
        ),
        text("Dusky Bluetooth")
            .size(15)
            .font(style::bold_font())
            .color(theme.fg),
    ]
    .spacing(8)
    .align_y(Alignment::Center);

    // Adapter power toggle (Row 1)
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
                render_icon("power", 14.0, power_fg),
                text(if powered { "On" } else { "Off" })
                    .size(12.5)
                    .font(style::bold_font()),
            ]
            .spacing(5)
            .align_y(Alignment::Center),
        )
        .padding(Padding::from([5, 10]))
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
        } else {
            "Turn Bluetooth on"
        },
        iced_widget::tooltip::Position::Bottom,
    );

    // Close window button (Row 1)
    let close_btn = style::pointer(
        button(render_icon("close", 15.0, theme.fg_muted))
            .padding(Padding::from([6, 7]))
            .style(style::close_button(theme))
            .on_press(Message::RequestClose),
    );
    let close_ctrl = style::tooltip(
        theme,
        close_btn,
        "Close Dusky Bluetooth",
        iced_widget::tooltip::Position::Bottom,
    );

    let top_row = row![brand_row, h_spacer(), power_ctrl, close_ctrl]
        .spacing(6)
        .align_y(Alignment::Center);

    let mut header_col = Column::new().spacing(8).width(Length::Fill).push(top_row);

    if let Some(adapter) = active_adapter {
        // Scan / Stop toggle (Row 2)
        let can_toggle_scan = powered && !external_scan;
        let scan_fg = if !can_toggle_scan {
            theme.muted_fg()
        } else if discovering {
            contrasting_fg(theme.accent)
        } else {
            theme.fg
        };
        let scan_label = if external_scan {
            "Scanning"
        } else if discovering {
            "Stop"
        } else {
            "Scan"
        };
        let scan_btn = style::pointer_if(
            can_toggle_scan,
            button(
                row![
                    render_icon("scan", 14.0, scan_fg),
                    text(scan_label).size(12.5).font(style::bold_font()),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding(Padding::from([5, 10]))
            .style(style::toggle_chip(theme, can_toggle_scan && discovering))
            .on_press_maybe(can_toggle_scan.then_some(Message::ToggleScan)),
        );
        let scan_ctrl = style::tooltip(
            theme,
            scan_btn,
            if !powered {
                "Turn on Bluetooth to scan for devices"
            } else if external_scan {
                "Scanning started by another application"
            } else if discovering {
                "Stop scanning for nearby devices"
            } else {
                "Scan for nearby Bluetooth devices"
            },
            iced_widget::tooltip::Position::Bottom,
        );

        // Discoverable toggle (Row 2)
        let disc_icon_color = if !powered {
            theme.muted_fg()
        } else if discoverable {
            theme.accent
        } else {
            theme.fg_muted
        };
        let disc_btn = style::pointer_if(
            powered,
            button(
                row![
                    render_icon("discoverable", 14.0, disc_icon_color),
                    text(if discoverable {
                        "Visible"
                    } else {
                        "Hidden"
                    })
                    .size(12),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding(Padding::from([5, 9]))
            .style(style::icon_button(theme, powered && discoverable))
            .on_press_maybe(powered.then_some(Message::ToggleDiscoverable(!discoverable))),
        );
        let disc_ctrl = style::tooltip(
            theme,
            disc_btn,
            if !powered {
                "Turn on Bluetooth to make this PC discoverable"
            } else if discoverable {
                "Discoverable to nearby devices (click to hide)"
            } else {
                "Make this PC discoverable to nearby devices"
            },
            iced_widget::tooltip::Position::Bottom,
        );

        // Hide unnamed filter toggle (Row 2)
        let filter_btn = style::pointer(
            button(
                row![
                    render_icon(
                        "filter",
                        14.0,
                        if app.hide_unnamed {
                            theme.accent
                        } else {
                            theme.fg_muted
                        },
                    ),
                    text(if app.hide_unnamed { "Named" } else { "All" }).size(12),
                ]
                .spacing(5)
                .align_y(Alignment::Center),
            )
            .padding(Padding::from([5, 9]))
            .style(style::icon_button(theme, app.hide_unnamed))
            .on_press(Message::ToggleHideUnnamed),
        );
        let filter_ctrl = style::tooltip(
            theme,
            filter_btn,
            if app.hide_unnamed {
                "Unnamed devices hidden (click to show all)"
            } else {
                "Showing all devices (click to hide unnamed)"
            },
            iced_widget::tooltip::Position::Bottom,
        );

        let mut controls_row = Row::new()
            .spacing(6)
            .align_y(Alignment::Center)
            .push(scan_ctrl)
            .push(disc_ctrl)
            .push(filter_ctrl);

        if app.adapters.len() > 1 {
            let label = format!("{} ({})", adapter.display_name(), adapter.short_id());
            let adapter_btn = style::pointer(
                button(
                    row![
                        text(label).size(11.5),
                        render_icon("chevron_right", 12.0, theme.fg_muted),
                    ]
                    .spacing(4)
                    .align_y(Alignment::Center),
                )
                .padding(Padding::from([5, 8]))
                .style(style::secondary_button(theme))
                .on_press(Message::CycleAdapter),
            );
            controls_row = controls_row.push(style::tooltip(
                theme,
                adapter_btn,
                "Switch active Bluetooth adapter",
                iced_widget::tooltip::Position::Bottom,
            ));
        }

        header_col = header_col.push(controls_row.wrap());
    }

    container(header_col)
        .width(Length::Fill)
        .padding(Padding::from([10, 14]))
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

fn view_scanning_banner<'a>(theme: AppTheme, scan_owned: bool) -> Element<'a, Message> {
    let mut banner_row = row![
        render_icon("dot", 10.0, theme.accent),
        text(if scan_owned {
            "Scanning for nearby Bluetooth devices…"
        } else {
            "Scanning started by another application"
        })
        .size(12.5)
        .color(theme.fg)
        .width(Length::Fill),
    ]
    .spacing(8)
    .align_y(Alignment::Center);

    if scan_owned {
        let stop_btn = style::pointer(
            button(text("Stop").size(12).font(style::bold_font()))
                .padding(Padding::from([4, 9]))
                .style(style::secondary_button(theme))
                .on_press(Message::ToggleScan),
        );
        banner_row = banner_row.push(stop_btn);
    }

    container(banner_row)
        .width(Length::Fill)
        .padding(Padding::from([7, 11]))
        .style(style::scanning_banner(theme))
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
            return view_centered_state(
                theme,
                "bluetooth_off",
                theme.fg_muted,
                "No Bluetooth adapter found",
                "Connect a Bluetooth adapter or enable it in firmware. Hot-plugged adapters appear automatically.",
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
        let turn_on = style::pointer(
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
        );
        return view_centered_state(
            theme,
            "bluetooth_off",
            theme.fg_muted,
            "Bluetooth is turned off",
            "Turn Bluetooth on to connect to paired devices or scan for nearby hardware.",
            Some(turn_on),
        );
    }

    let mut paired_devices: Vec<&DeviceState> = Vec::new();
    let mut nearby_devices: Vec<&DeviceState> = Vec::new();
    let mut hidden_unnamed: usize = 0;

    for dev in app.devices.values() {
        if dev.adapter_path != adapter.path {
            continue;
        }
        if app.hide_unnamed && dev.should_hide_when_unnamed() {
            hidden_unnamed += 1;
            continue;
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
        if adapter.discovering {
            let show_unnamed = (hidden_unnamed > 0).then(|| {
                style::pointer(
                    button(
                        text(format!("Show {hidden_unnamed} unnamed"))
                            .size(12.5)
                            .font(style::bold_font()),
                    )
                    .padding(Padding::from([6, 12]))
                    .style(style::secondary_button(theme))
                    .on_press(Message::ToggleHideUnnamed),
                )
            });
            return view_centered_state(
                theme,
                "scan",
                theme.accent,
                "Searching for devices…",
                "Make sure your Bluetooth device is powered on and in pairing mode.",
                show_unnamed,
            );
        } else {
            let scan_cta = style::pointer(
                button(
                    row![
                        render_icon("scan", 15.0, contrasting_fg(theme.accent)),
                        text("Scan for Devices").size(13).font(style::bold_font()),
                    ]
                    .spacing(7)
                    .align_y(Alignment::Center),
                )
                .padding(Padding::from([8, 16]))
                .style(style::primary_button(theme))
                .on_press(Message::ToggleScan),
            );
            let cta: Element<'_, Message> = if hidden_unnamed > 0 {
                let show_unnamed = style::pointer(
                    button(
                        text(format!("Show {hidden_unnamed} unnamed"))
                            .size(12.5)
                            .font(style::bold_font()),
                    )
                    .padding(Padding::from([8, 14]))
                    .style(style::secondary_button(theme))
                    .on_press(Message::ToggleHideUnnamed),
                );
                row![scan_cta, show_unnamed]
                    .spacing(8)
                    .align_y(Alignment::Center)
                    .wrap()
                    .into()
            } else {
                scan_cta
            };
            let subtitle = if hidden_unnamed > 0 {
                "Start a scan or show hidden unnamed devices to view nearby hardware."
            } else {
                "Start a scan to discover nearby headphones, keyboards, mice, and controllers."
            };
            return view_centered_state(
                theme,
                "bluetooth",
                theme.accent,
                "No devices yet",
                subtitle,
                Some(cta),
            );
        }
    }

    let mut list_col = Column::new().spacing(10).width(Length::Fill);
    let has_paired = !paired_devices.is_empty();

    if has_paired {
        list_col = list_col.push(view_section_header(
            theme,
            "PAIRED & CONNECTED",
            paired_devices.len(),
            None,
        ));
        for dev in paired_devices {
            list_col = list_col.push(view_device_card(app, adapter, dev));
        }
    }

    if !nearby_devices.is_empty() || hidden_unnamed > 0 {
        let extra = (hidden_unnamed > 0).then(|| {
            style::pointer(
                button(
                    text(format!("Show {hidden_unnamed} unnamed"))
                        .size(11.5)
                        .color(theme.accent),
                )
                .padding(Padding::from([2, 6]))
                .style(style::icon_button(theme, false))
                .on_press(Message::ToggleHideUnnamed),
            )
        });
        list_col = list_col.push(
            container(view_section_header(
                theme,
                "NEARBY DEVICES",
                nearby_devices.len(),
                extra,
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
            .padding(Padding::from([12, 14])),
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
    subtitle: &'a str,
    action: Option<Element<'a, Message>>,
) -> Element<'a, Message> {
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
    let icon_box = container(render_icon(dev.kind.icon_name(), 20.0, dev_icon_color))
        .padding(9)
        .style(style::icon_tile(theme, dev.connected));

    // Status metadata line under device alias
    let mut meta_row = Row::new().spacing(6).align_y(Alignment::Center);
    meta_row = meta_row.push(text(dev.kind.label()).size(11.5).color(theme.muted_fg()));
    if dev.connected {
        let badge = theme.status_badge(theme.success);
        meta_row = meta_row.push(
            container(
                text("Connected")
                    .size(10.5)
                    .font(style::bold_font())
                    .color(badge.fg),
            )
            .padding(Padding::from([1, 6]))
            .style(style::status_pill(theme, theme.success)),
        );
    }
    if dev.trusted {
        let badge = theme.status_badge(theme.info);
        meta_row = meta_row.push(
            container(text("Trusted").size(10.5).color(badge.fg))
                .padding(Padding::from([1, 6]))
                .style(style::status_pill(theme, theme.info)),
        );
    }
    if dev.blocked {
        let badge = theme.status_badge(theme.danger);
        meta_row = meta_row.push(
            container(text("Blocked").size(10.5).color(badge.fg))
                .padding(Padding::from([1, 6]))
                .style(style::status_pill(theme, theme.danger)),
        );
    }

    let info_col = column![
        text(dev.display_name())
            .size(14)
            .font(style::bold_font())
            .color(theme.fg)
            .wrapping(iced_core::text::Wrapping::WordOrGlyph),
        meta_row.wrap(),
    ]
    .spacing(3)
    .width(Length::Fill);

    // Right metrics + primary button + expand chevron
    let mut right_row = Row::new().spacing(6).align_y(Alignment::Center);

    if let Some(pct) = dev.battery {
        let low = pct <= 20;
        let bat_semantic = if low { theme.danger } else { theme.success };
        let bat_badge = theme.status_badge(bat_semantic);
        let bat_chip = container(
            row![
                render_icon(
                    if low { "battery_low" } else { "battery" },
                    13.0,
                    bat_badge.fg,
                ),
                text(format!("{pct}%"))
                    .size(11.5)
                    .font(style::bold_font())
                    .color(bat_badge.fg),
            ]
            .spacing(4)
            .align_y(Alignment::Center),
        )
        .padding(Padding::from([3, 7]))
        .style(style::status_pill(theme, bat_semantic));

        right_row = right_row.push(style::tooltip(
            theme,
            bat_chip,
            format!("Battery level: {pct}%"),
            iced_widget::tooltip::Position::Top,
        ));
    }

    if let Some(rssi) = dev.rssi.and_then(valid_rssi) {
        let sig_chip = container(render_icon(rssi_icon_name(rssi), 15.0, theme.fg_muted))
            .padding(Padding::from([3, 5]))
            .style(style::neutral_pill(theme));
        right_row = right_row.push(style::tooltip(
            theme,
            sig_chip,
            format!("Signal: {rssi} dBm ({})", rssi_quality_label(rssi)),
            iced_widget::tooltip::Position::Top,
        ));
    }

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

    let main_row = row![icon_box, info_col, right_row]
        .spacing(10)
        .align_y(Alignment::Center);

    let mut card_col = Column::new().spacing(10).width(Length::Fill).push(main_row);

    if expanded {
        card_col = card_col.push(subtle_divider(theme));
        card_col = card_col.push(view_device_drawer(app, adapter, dev));
    }

    container(card_col)
        .width(Length::Fill)
        .padding(Padding::from([10, 12]))
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
            row![render_icon("edit", 13.0, theme.fg), text("Rename").size(12),]
                .spacing(5)
                .align_y(Alignment::Center),
        )
        .padding(Padding::from([5, 9]))
        .style(style::secondary_button(theme))
        .on_press(Message::OpenRename(dev.path.clone())),
    );
    actions_row = actions_row.push(style::tooltip(
        theme,
        rename_btn,
        "Set a custom alias for this device",
        iced_widget::tooltip::Position::Top,
    ));

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

    let modal_card = container(body)
        .width(Length::Fixed(390.0))
        .max_width(420.0)
        .padding(18)
        .style(style::card_surface(theme, 12.0));

    opaque(
        container(modal_card)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .padding(20)
            .style(style::backdrop_scrim(theme)),
    )
}

fn view_rename_modal<'a>(theme: AppTheme, rename: &'a RenameState) -> Element<'a, Message> {
    let header = row![
        container(render_icon("edit", 16.0, theme.accent))
            .padding(7)
            .style(style::icon_tile(theme, true)),
        column![
            text("Rename Device")
                .size(15)
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
        .padding(Padding::from([8, 10]))
        .size(13.5)
        .style(style::text_input_style(theme))
        .width(Length::Fill);

    let reset_btn = style::pointer(
        button(text("Reset").size(12.5))
            .padding(Padding::from([6, 11]))
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
            button(text("Cancel").size(12.5))
                .padding(Padding::from([6, 12]))
                .style(style::secondary_button(theme))
                .on_press(Message::CloseRename),
        ),
        style::pointer(
            button(text("Save").size(12.5).font(style::bold_font()))
                .padding(Padding::from([6, 14]))
                .style(style::primary_button(theme))
                .on_press(Message::RenameSave),
        ),
    ]
    .spacing(8)
    .align_y(Alignment::Center);

    let card = container(
        column![
            header,
            subtle_divider(theme),
            text("Set a custom alias for this Bluetooth device:")
                .size(12.5)
                .color(theme.fg_muted),
            input,
            actions,
        ]
        .spacing(12)
        .width(Length::Fill),
    )
    .width(Length::Fixed(370.0))
    .max_width(400.0)
    .padding(18)
    .style(style::card_surface(theme, 12.0));

    opaque(
        container(card)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .padding(20)
            .style(style::backdrop_scrim(theme)),
    )
}
