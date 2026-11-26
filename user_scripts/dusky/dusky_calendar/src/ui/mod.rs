//! Shared UI state models, message enum, view modes, and UI submodule registry.

pub mod agenda_view;
pub mod app;
pub mod grid_widgets;
pub mod header;
pub mod month_view;
pub mod overlays;
pub mod sidebar;
pub mod style;
pub mod week_view;

use crate::model::{
    CalendarColor, EditScope, EventKey, EventOccurrence, RecurrenceFreq, RecurrenceRule,
};
use crate::storage::StoredView;
use crate::theme::SemanticToken;
use crate::{InitialView, IpcCommand};
use chrono::{DateTime, NaiveDate, Utc};
use iced_core::Size;
use iced_widget::text_editor;
use std::time::Instant;

pub type Element<'a, M = Message> =
    iced_core::Element<'a, M, iced_core::Theme, crate::renderer::Renderer>;

pub const WIDE_BREAKPOINT: f32 = 1000.0;
pub const NARROW_BREAKPOINT: f32 = 700.0;
pub const SIDEBAR_WIDTH: f32 = 252.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ViewMode {
    Month,
    Week,
    Agenda,
}

impl ViewMode {
    pub fn label(self) -> &'static str {
        match self {
            Self::Month => "Month",
            Self::Week => "Week",
            Self::Agenda => "Agenda",
        }
    }

    pub fn icon_name(self) -> &'static str {
        match self {
            Self::Month => "month",
            Self::Week => "week",
            Self::Agenda => "agenda",
        }
    }
}

impl From<InitialView> for ViewMode {
    fn from(value: InitialView) -> Self {
        match value {
            InitialView::Month => Self::Month,
            InitialView::Week => Self::Week,
            InitialView::Agenda => Self::Agenda,
        }
    }
}

impl From<StoredView> for ViewMode {
    fn from(value: StoredView) -> Self {
        match value {
            StoredView::Month => Self::Month,
            StoredView::Week => Self::Week,
            StoredView::Agenda => Self::Agenda,
        }
    }
}

impl From<ViewMode> for StoredView {
    fn from(value: ViewMode) -> Self {
        match value {
            ViewMode::Month => Self::Month,
            ViewMode::Week => Self::Week,
            ViewMode::Agenda => Self::Agenda,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ViewportTier {
    Wide,
    Medium,
    Narrow,
}

impl ViewportTier {
    pub fn from_width(width: f32) -> Self {
        if width >= WIDE_BREAKPOINT {
            Self::Wide
        } else if width >= NARROW_BREAKPOINT {
            Self::Medium
        } else {
            Self::Narrow
        }
    }
}

#[derive(Debug, Clone)]
pub struct QuickAddDraft {
    pub title: String,
    pub calendar_id: String,
    pub start_date: NaiveDate,
    pub end_date: NaiveDate,
    pub all_day: bool,
    pub start_time: String,
    pub end_time: String,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EditorDateTarget {
    Start,
    End,
    RecurrenceUntil,
}

pub struct EventEditorDraft {
    pub original_calendar_id: Option<String>,
    pub original_key: Option<EventKey>,
    pub occurrence_id: Option<DateTime<Utc>>,
    pub clicked_occurrence: Option<EventOccurrence>,
    pub is_recurring_source: bool,
    pub edit_scope: EditScope,
    pub summary: String,
    pub calendar_id: String,
    pub all_day: bool,
    pub start_date: NaiveDate,
    pub start_date_text: String,
    pub end_date: NaiveDate,
    pub end_date_text: String,
    pub start_time_text: String,
    pub end_time_text: String,
    pub recurrence: RecurrenceRule,
    pub interval_text: String,
    pub until_date_text: String,
    pub count_text: String,
    pub reminder_minutes: Option<u32>,
    pub location: String,
    pub notes: text_editor::Content<crate::renderer::Renderer>,
    pub active_date_picker: Option<(EditorDateTarget, NaiveDate)>,
    pub error: Option<String>,
    pub confirm_delete: bool,
}

#[derive(Debug, Clone)]
pub struct CalendarFormDraft {
    pub editing_id: Option<String>,
    pub name: String,
    pub color: CalendarColor,
    pub custom_hex_text: String,
    pub make_default: bool,
}

#[derive(Debug, Clone)]
pub struct CalendarManagerState {
    pub form: Option<CalendarFormDraft>,
    pub import_path_text: String,
    pub import_target_calendar_id: String,
    pub confirm_delete_id: Option<String>,
    pub error: Option<String>,
}

pub enum Overlay {
    QuickAdd(QuickAddDraft),
    EventPreview {
        occurrence: EventOccurrence,
        confirm_delete: bool,
    },
    DayOverflow {
        date: NaiveDate,
    },
    EventEditor(Box<EventEditorDraft>),
    CalendarManager(CalendarManagerState),
}

impl Overlay {
    pub fn captures_text_shortcuts(&self) -> bool {
        matches!(
            self,
            Self::QuickAdd(_) | Self::EventEditor(_) | Self::CalendarManager(_)
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToastKind {
    Info,
    Success,
    Error,
}

#[derive(Debug, Clone)]
pub enum ToastAction {
    UndoDelete,
    JumpTo {
        date: NaiveDate,
        key: Option<EventKey>,
    },
}

#[derive(Debug, Clone)]
pub struct ToastNotice {
    pub id: u64,
    pub kind: ToastKind,
    pub message: String,
    pub expires_at: Instant,
    pub action: Option<ToastAction>,
}

/// Deferred deletion held in memory during the 5-second Undo toast window.
#[derive(Debug, Clone)]
pub struct PendingDeletion {
    pub toast_id: u64,
    pub calendar_id: String,
    pub key: EventKey,
    pub scope: EditScope,
    pub occurrence_id: Option<DateTime<Utc>>,
    pub summary: String,
}

#[derive(Debug, Clone)]
pub enum Message {
    // View & date navigation
    SwitchView(ViewMode),
    GoToday,
    NavigatePrev,
    NavigateNext,
    SelectDate(NaiveDate),
    ActivateDate(NaiveDate),
    MonthWheelStep(i32),
    MiniCalendarShiftMonth(i32),
    MiniCalendarShiftYear(i32),
    ToggleSidebar,
    ToggleWeekAllDayExpanded,
    ZoomWeek(f32),
    QuickAddDateRange {
        start_date: NaiveDate,
        end_date: NaiveDate,
    },
    QuickAddTimeRange {
        date: NaiveDate,
        start_minutes: u32,
        end_minutes: u32,
    },

    // Window chrome & search
    DragWindow,
    RequestClose,
    ToggleSearch,
    SearchChanged(String),
    SelectSearchResult(EventOccurrence),
    ClearSearch,

    // Calendar visibility, preferences & management
    ToggleCalendarVisible(String),
    OpenCalendarManager,
    CalendarManagerStartCreate,
    CalendarManagerStartEdit(String),
    CalendarManagerCancelForm,
    CalendarManagerNameChanged(String),
    CalendarManagerSelectToken(SemanticToken),
    CalendarManagerHexChanged(String),
    CalendarManagerToggleDefault,
    CalendarManagerSaveForm,
    CalendarManagerRequestDelete(String),
    CalendarManagerConfirmDelete(String),
    CalendarManagerCancelDelete,
    CalendarManagerExport(String),
    CalendarManagerImportPathChanged(String),
    CalendarManagerImportTargetChanged(String),
    CalendarManagerImportSubmit,
    TogglePref24h,
    TogglePrefWeekStartMonday,
    TogglePrefShowWeekNumbers,

    // Quick-Add, Day Overflow & Event Preview
    OpenQuickAdd { date: Option<NaiveDate>, hour: Option<u32> },
    QuickAddTitleChanged(String),
    QuickAddToggleAllDay,
    QuickAddStartTimeChanged(String),
    QuickAddEndTimeChanged(String),
    QuickAddSelectCalendar(String),
    QuickAddSave,
    QuickAddSubmit,
    QuickAddPromoteToEditor,
    OpenDayOverflow(NaiveDate),
    OpenEventPreview(EventOccurrence),
    OpenExternalUrl(String),
    PreviewExportEvent,
    PreviewEditEvent,
    PreviewRequestDelete,
    PreviewCancelDelete,
    PreviewConfirmDelete(EditScope),

    // Full Event Editor
    OpenNewEventEditor { date: NaiveDate, hour: Option<u32> },
    EditorScopeChanged(EditScope),
    EditorSummaryChanged(String),
    EditorSelectCalendar(String),
    EditorToggleAllDay,
    EditorStartDateChanged(String),
    EditorEndDateChanged(String),
    EditorStepDate { is_start: bool, delta_days: i64 },
    EditorStartTimeChanged(String),
    EditorEndTimeChanged(String),
    EditorStepTime { is_start: bool, delta_minutes: i64 },
    EditorToggleDatePicker(EditorDateTarget),
    EditorDatePickerShiftMonth(i32),
    EditorDatePickerSelect(NaiveDate),
    EditorRecurrenceFreqChanged(RecurrenceFreq),
    EditorRecurrenceIntervalChanged(String),
    EditorRecurrenceToggleWeekday(usize),
    EditorRecurrenceEndModeChanged(u8),
    EditorRecurrenceUntilChanged(String),
    EditorRecurrenceCountChanged(String),
    EditorReminderChanged(Option<u32>),
    EditorLocationChanged(String),
    EditorNotesAction(text_editor::Action),
    EditorSave,
    EditorRequestDelete,
    EditorCancelDelete,
    EditorConfirmDelete(EditScope),

    // Overlays, toasts, undo & system events
    CloseOverlay,
    UndoDelete,
    CommitPendingDelete(u64),
    DismissToast(u64),
    ToastJump(NaiveDate, Option<EventKey>),
    WindowResized(Size),
    SystemEvent(iced::Event),
    IpcReceived(IpcCommand),
    Tick,
    NoOp,
}
