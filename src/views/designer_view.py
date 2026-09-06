import flet as ft
import polars as pl
from core import flow_file_handler
from core.configs import logger
from core.dataryx.code_generator.code_generator import export_flow_to_polars
from core.schemas.input_schema import NodePromise, NodeDatasource
from services.auth_service import auth_service
from services.license_validator import check_license
from components.theme import get_theme, is_dark
from components.control_utils import is_mounted
import traceback
import random
import inspect
import asyncio
from functools import lru_cache
from core.schemas import input_schema
from views.data_profiler_view import open_data_profiler


@lru_cache(maxsize=1)
def _get_expression_doc_lookup() -> dict:
    """{function_name_lower: doc_string} for every polars_expr_transformer
    function -- the same catalog that already powers Formula/Filter's
    expression engine (`to_expr`), so no docs need to be hand-written here.
    """
    try:
        from polars_expr_transformer.function_overview import get_expression_overview

        lookup = {}
        for category in get_expression_overview():
            for expr in category.expressions:
                lookup[expr.name.lower()] = expr.doc.strip()
        return lookup
    except Exception:
        return {}


def build_column_keep_section(
    title: str, columns: list, existing_renames=None, text_color=None
):
    """Builds a "which columns to keep / rename" section for Join and Fuzzy
    Match (Left data / Right data), matching the original Dataryx's
    selectDynamic component -- this control was present in the original
    Vue app but got dropped during the Flet port, so both nodes silently
    kept every column with no user control.

    text_color: theme-appropriate text color for the column-name labels
    (e.g. t.TEXT_PRIMARY). Defaults to a hardcoded light grey, which reads
    fine on the app's dark theme but is nearly invisible in light mode --
    callers should pass the caller's current theme color.

    Returns (container, get_selections) where get_selections() returns a
    list of (old_name, new_name, keep) tuples reflecting current UI state.
    """
    if text_color is None:
        text_color = ft.Colors.GREY_200
    existing_map = {r.old_name: r for r in (existing_renames or [])}
    # (old_name, new_name_field, keep_checkbox, row_container) -- kept for
    # EVERY column regardless of the search filter, so filtering (which only
    # toggles row visibility) never loses a column's current keep/rename state.
    row_widgets = []
    rows_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, height=170)

    for col in columns:
        existing = existing_map.get(col)
        new_name_field = ft.TextField(
            value=(existing.new_name if existing and existing.new_name else col),
            height=32,
            text_size=11,
            content_padding=5,
            expand=True,
        )
        keep_checkbox = ft.Checkbox(
            value=(existing.keep if existing is not None else True)
        )
        row = ft.Row(
            [
                ft.Text(col, size=12, expand=True, color=text_color),
                new_name_field,
                keep_checkbox,
            ],
            spacing=8,
        )
        row_widgets.append((col, new_name_field, keep_checkbox, row))
        rows_col.controls.append(row)

    def apply_filter(filter_text: str):
        needle = filter_text.lower()
        for col, _, _, row in row_widgets:
            row.visible = (needle in col.lower()) if needle else True
        try:
            rows_col.update()
        except Exception:
            pass

    search_input = ft.TextField(
        hint_text="Filter columns...",
        height=32,
        text_size=11,
        content_padding=5,
        on_change=lambda e: apply_filter(e.control.value or ""),
    )

    def set_all(value: bool):
        def _handler(e):
            for _, _, cb, row in row_widgets:
                if row.visible:
                    cb.value = value
            try:
                rows_col.update()
            except Exception:
                pass

        return _handler

    check_all_btn = ft.IconButton(
        icon=ft.Icons.CHECK_BOX_ROUNDED,
        icon_size=18,
        tooltip="Keep all columns",
        on_click=set_all(True),
    )
    uncheck_all_btn = ft.IconButton(
        icon=ft.Icons.CHECK_BOX_OUTLINE_BLANK_ROUNDED,
        icon_size=18,
        tooltip="Keep no columns",
        on_click=set_all(False),
    )

    header_row = ft.Row(
        [
            ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_300),
            ft.Container(content=search_input, expand=True),
            check_all_btn,
            uncheck_all_btn,
        ],
        spacing=8,
    )
    column_labels_row = ft.Row(
        [
            ft.Text("Original column name", size=11, weight=ft.FontWeight.W_600, expand=True, color=ft.Colors.GREY_400),
            ft.Text("New column name", size=11, weight=ft.FontWeight.W_600, expand=True, color=ft.Colors.GREY_400),
            ft.Text("Keep", size=11, weight=ft.FontWeight.W_600, width=40, color=ft.Colors.GREY_400),
        ],
        spacing=8,
    )

    section = ft.Container(
        content=ft.Column([header_row, column_labels_row, rows_col], spacing=6),
        padding=10,
        border=ft.Border.all(1, ft.Colors.GREY_800),
        border_radius=6,
        margin=ft.Margin(top=8, bottom=0, left=0, right=0),
    )

    def get_selections():
        return [
            (old_name, (nf.value or old_name), cb.value)
            for old_name, nf, cb, _ in row_widgets
        ]

    return section, get_selections


def instantiate_with_defaults(node_model, initial_params):
    import typing
    from pydantic import BaseModel
    from pydantic_core import PydanticUndefined

    def get_default_for_field(field_info):
        if field_info.default is not PydanticUndefined:
            return field_info.default
        if field_info.default_factory is not None:
            return field_info.default_factory()
        return get_default_for_type(field_info.annotation)

    def get_default_for_type(annotation):
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
        if origin is typing.Annotated:
            return get_default_for_type(args[0])
        if origin is typing.Union or (
            hasattr(typing, "UnionType") and origin is typing.UnionType
        ):
            for arg in args:
                if arg is not type(None):
                    return get_default_for_type(arg)
        if origin is typing.Literal:
            if args:
                return args[0]
            return None
        if origin is list:
            return []
        if origin is dict:
            return {}
        if isinstance(annotation, type):
            if issubclass(annotation, BaseModel):
                sub_params = {}
                for sub_field_name, sub_field_info in annotation.model_fields.items():
                    if sub_field_info.is_required():
                        sub_params[sub_field_name] = get_default_for_field(
                            sub_field_info
                        )
                try:
                    return annotation(**sub_params)
                except Exception:
                    # If validation fails with only required fields, try populating all fields (both required and optional)
                    for (
                        sub_field_name,
                        sub_field_info,
                    ) in annotation.model_fields.items():
                        if sub_field_name not in sub_params:
                            sub_params[sub_field_name] = get_default_for_field(
                                sub_field_info
                            )
                    try:
                        return annotation(**sub_params)
                    except Exception:
                        return annotation.model_construct(**sub_params)
            elif issubclass(annotation, list):
                return []
            elif issubclass(annotation, dict):
                return {}
            elif issubclass(annotation, str):
                return ""
            elif issubclass(annotation, int):
                return 0
            elif issubclass(annotation, float):
                return 0.0
            elif issubclass(annotation, bool):
                return False
        return None

    params = initial_params.copy()
    for field_name, field_info in node_model.model_fields.items():
        if field_name not in params and field_info.is_required():
            params[field_name] = get_default_for_field(field_info)
    try:
        return node_model(**params)
    except Exception:
        # Fallback to populating all optional fields if initial instantiation fails
        for field_name, field_info in node_model.model_fields.items():
            if field_name not in params:
                params[field_name] = get_default_for_field(field_info)
        try:
            return node_model(**params)
        except Exception:
            # Create a bare/mock settings object if validation still fails
            return node_model.model_construct(**params)


from views.canvas_view import CanvasView


class DesignerView(ft.Container):
    # Shared function catalog for expression editors (Formula node's Main
    # Settings tab, and Filter node's Advanced Expression tab -- both
    # execute through the same polars_expr_transformer engine, so the same
    # functions are valid in both places). Kept as a single class-level
    # constant instead of being duplicated per node type.
    FUNCTIONS_BY_CATEGORY = {
        "Logic": [
            (
                "IF / ELSE",
                "if [condition] then then_value else else_value endif",
            ),
            ("AND", "[col1] and [col2]"),
            ("OR", "[col1] or [col2]"),
            ("NOT", "not [col]"),
            ("EQUALS", "equals([col1], [col2])"),
            ("DOES_NOT_EQUAL", "does_not_equal([col1], [col2])"),
            ("BETWEEN", "between([col], min_val, max_val)"),
            ("CONTAINS", "contains([col], search_for)"),
            ("IS_EMPTY", "is_empty([col])"),
            ("IS_NOT_EMPTY", "is_not_empty([col])"),
            ("IS_STRING", "is_string([col])"),
            ("COALESCE", "coalesce([col1], [col2])"),
            ("IFNULL", "ifnull([col], default)"),
            ("NVL", "nvl([col], default)"),
            ("NULLIF", "nullif([col1], [col2])"),
            ("GREATEST", "greatest([col1], [col2])"),
            ("LEAST", "least([col1], [col2])"),
        ],
        "String": [
            ("CONCAT", "concat([col1], [col2])"),
            ("SUBSTRING", "substring([col], start, num_chars)"),
            ("MID", "mid([col], start, num_chars)"),
            ("LOWERCASE", "lowercase([col])"),
            ("UPPERCASE", "uppercase([col])"),
            ("TITLECASE", "titlecase([col])"),
            ("TRIM", "trim([col])"),
            ("LEFT_TRIM", "left_trim([col])"),
            ("RIGHT_TRIM", "right_trim([col])"),
            ("LEFT", "left([col], num_chars)"),
            ("RIGHT", "right([col], num_chars)"),
            ("LENGTH", "length([col])"),
            ("REPLACE", "replace([col], find_text, replace_with)"),
            ("FIND_POSITION", "find_position([col], sub)"),
            ("PAD_LEFT", "pad_left([col], length, pad_character)"),
            ("PAD_RIGHT", "pad_right([col], length, pad_character)"),
            ("REPEAT", "repeat([col], count)"),
            ("REVERSE", "reverse([col])"),
            ("SPLIT", "split([col], delimiter)"),
            ("STARTS_WITH", "starts_with([col], prefix)"),
            ("ENDS_WITH", "ends_with([col], suffix)"),
            ("COUNT_MATCH", "count_match([col], pattern)"),
            (
                "STRING_SIMILARITY",
                "string_similarity([col1], [col2], levenshtein)",
            ),
        ],
        "Math": [
            ("ABS", "abs([col])"),
            ("ROUND", "round([col], 2)"),
            ("CEIL", "ceil([col])"),
            ("FLOOR", "floor([col])"),
            ("SQRT", "sqrt([col])"),
            ("POWER", "power([col], exponent)"),
            ("MOD", "mod([col], divisor)"),
            ("SIGN", "sign([col])"),
            ("NEGATION", "negation([col])"),
            ("EXP", "exp([col])"),
            ("LOG", "log([col])"),
            ("LOG10", "log10([col])"),
            ("LOG2", "log2([col])"),
            ("SIN", "sin([col])"),
            ("COS", "cos([col])"),
            ("TAN", "tan([col])"),
            ("ASIN", "asin([col])"),
            ("ACOS", "acos([col])"),
            ("ATAN", "atan([col])"),
            ("TANH", "tanh([col])"),
            ("RANDOM_INT", "random_int(0, 100)"),
        ],
        "Date": [
            ("YEAR", "year([col])"),
            ("MONTH", "month([col])"),
            ("DAY", "day([col])"),
            ("HOUR", "hour([col])"),
            ("MINUTE", "minute([col])"),
            ("SECOND", "second([col])"),
            ("QUARTER", "quarter([col])"),
            ("WEEK", "week([col])"),
            ("WEEKDAY", "weekday([col])"),
            ("DAYOFWEEK", "dayofweek([col])"),
            ("DAYOFYEAR", "dayofyear([col])"),
            ("TODAY", "today()"),
            ("NOW", "now()"),
            ("START_OF_MONTH", "start_of_month([col])"),
            ("END_OF_MONTH", "end_of_month([col])"),
            ("ADD_DAYS", "add_days([col], days)"),
            ("ADD_WEEKS", "add_weeks([col], weeks)"),
            ("ADD_MONTHS", "add_months([col], months)"),
            ("ADD_YEARS", "add_years([col], years)"),
            ("ADD_HOURS", "add_hours([col], hours)"),
            ("ADD_MINUTES", "add_minutes([col], minutes)"),
            ("ADD_SECONDS", "add_seconds([col], seconds)"),
            ("DATE_DIFF_DAYS", "date_diff_days([col1], [col2])"),
            ("DATE_TRUNCATE", "date_truncate([col], 1mo)"),
            ("FORMAT_DATE", "format_date([col], %Y-%m-%d)"),
        ],
        "Type conversions": [
            ("TO_STRING", "to_string([col])"),
            ("TO_DATE", "to_date([col])"),
            ("TO_DATETIME", "to_datetime([col])"),
            ("TO_INTEGER", "to_integer([col])"),
            ("TO_FLOAT", "to_float([col])"),
            ("TO_NUMBER", "to_number([col])"),
            ("TO_BOOLEAN", "to_boolean([col])"),
            ("TO_DECIMAL", "to_decimal([col], 2)"),
        ],
    }

    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.main_page.designer_view = self
        self.is_typing = False
        self.active_flow_id = None
        self.selected_node_id = None
        self.copied_node_id = None
        self.flow_ref = None
        self.expand = True
        self.bgcolor = get_theme(self.main_page).BG_PAGE
        self.padding = 20

        self.build_designer()

    def build_designer(self):
        # ── FilePicker service (registered once in did_mount via page.services) ──
        self._file_picker_target = None  # TextField to fill when pick completes
        self._file_picker = ft.FilePicker()

        # Header panel — use PopupMenuButton
        t = get_theme(self.main_page)
        self.flow_label_text = ft.Text(
            "Select Flow", size=13, color=t.TEXT_PRIMARY, weight=ft.FontWeight.W_500
        )
        self.flow_dropdown = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Row(
                    [
                        self.flow_label_text,
                        ft.Icon(
                            ft.Icons.ARROW_DROP_DOWN_ROUNDED,
                            color=t.TEXT_PRIMARY,
                            size=18,
                        ),
                    ],
                    spacing=4,
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                bgcolor=t.BG_CARD_ALT,
                border=ft.Border.all(1, t.BORDER),
                border_radius=6,
                padding=ft.Padding(left=10, top=8, right=10, bottom=8),
                width=240,
                height=40,
            ),
            items=[],
        )

        self.new_flow_btn = ft.IconButton(
            icon=ft.Icons.ADD_CIRCLE_OUTLINE_ROUNDED,
            icon_color=ft.Colors.BLUE_400,
            icon_size=20,
            tooltip="Create New Flow",
            on_click=self.create_new_flow,
        )

        self.import_flow_btn = ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
            icon_color=ft.Colors.BLUE_400,
            icon_size=20,
            tooltip="Import Flow from File",
            on_click=self.import_flow_from_file,
        )

        flow_selector_row = ft.Row(
            [
                ft.Text(
                    "Flow:",
                    size=14,
                    weight=ft.FontWeight.W_600,
                    color=t.TEXT_SECONDARY,
                ),
                self.flow_dropdown,
                self.new_flow_btn,
                self.import_flow_btn,
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        run_btn = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW_ROUNDED,
            icon_color=ft.Colors.GREEN_400,
            tooltip="Run Pipeline",
            disabled=True,
            on_click=self.run_pipeline,
        )

        export_btn = ft.IconButton(
            icon=ft.Icons.CODE_ROUNDED,
            icon_color=ft.Colors.BLUE_400,
            tooltip="Export Dataryx Code",
            disabled=True,
            on_click=self.export_code,
        )

        # Left column - Canvas View
        self.canvas = CanvasView(
            page=self.main_page,
            flow_ref=None,
            on_node_selected=self.select_node,
            on_node_deleted=self.delete_node,
        )

        left_panel = ft.Container(content=self.canvas, expand=True)

        # Right panel — Step Configuration with collapse toggle
        config_container = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        self._config_collapsed = False

        config_body = ft.Container(
            content=ft.Column(
                [ft.Divider(color=t.DIVIDER, height=1), config_container],
                spacing=8,
                expand=True,
            ),
            expand=True,
            visible=True,
        )

        def toggle_config(e):
            self._config_collapsed = not self._config_collapsed
            config_body.visible = not self._config_collapsed
            config_title_row.visible = not self._config_collapsed
            config_collapsed_tab.visible = self._config_collapsed
            config_panel.width = 36 if self._config_collapsed else 380
            config_toggle_btn.icon = (
                ft.Icons.CHEVRON_RIGHT_ROUNDED
                if self._config_collapsed
                else ft.Icons.CHEVRON_LEFT_ROUNDED
            )
            top_area.update()

        config_toggle_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
            icon_color=t.ICON_COLOR,
            icon_size=16,
            padding=0,
            width=28,
            height=28,
            tooltip="Collapse Step Configuration",
            on_click=toggle_config,
        )

        config_title_row = ft.Row(
            [
                ft.Icon(ft.Icons.SETTINGS_ROUNDED, size=15, color=ft.Colors.BLUE_400),
                ft.Text(
                    "Step Configuration",
                    size=13,
                    weight=ft.FontWeight.W_600,
                    color=t.TEXT_PRIMARY,
                    expand=True,
                ),
                config_toggle_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
            visible=True,
        )

        # Narrow tab shown when collapsed
        config_collapsed_tab = ft.Container(
            content=ft.Column(
                [
                    ft.IconButton(
                        icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
                        icon_color=t.ICON_COLOR,
                        icon_size=16,
                        padding=0,
                        width=28,
                        height=28,
                        tooltip="Expand Step Configuration",
                        on_click=toggle_config,
                    ),
                    ft.Container(
                        content=ft.Text(
                            "Config",
                            size=10,
                            color=t.TEXT_HINT,
                            weight=ft.FontWeight.W_500,
                        ),
                        rotate=ft.Rotate(angle=1.5708),
                        margin=ft.Margin(top=24, bottom=0, left=0, right=0),
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            ),
            visible=False,
        )

        config_panel = ft.Container(
            content=ft.Column(
                [config_title_row, config_collapsed_tab, config_body],
                spacing=6,
                expand=True,
            ),
            bgcolor=get_theme(self.main_page).BG_CARD,
            padding=ft.Padding(left=12, top=10, right=12, bottom=12),
            border_radius=8,
            width=380,
            expand=False,
            visible=False,
        )

        # Left sidebar Data Actions Panel matching client requirements
        self.data_actions_panel = self._build_data_actions_panel()

        # Canvas Stack containing the canvas at the bottom and the floating actions panel on top!
        self.canvas_stack = ft.Stack(
            [
                left_panel,
                self.data_actions_panel,
            ],
            expand=True,
        )

        # Top area: Canvas Stack (expands) + Config (fixed, collapsible right)
        top_area = ft.Row(
            [self.canvas_stack, config_panel],
            expand=True,
            spacing=8,
        )

        # Bottom area: Full-width Data Preview panel
        preview_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("No active step", color=ft.Colors.GREY_500))
            ],
            rows=[],
            heading_row_color=(
                ft.Colors.BLUE_100
                if not is_dark(self.main_page)
                else ft.Colors.BLUE_900
            ),
            border_radius=6,
            column_spacing=20,
            data_row_min_height=36,
            data_row_max_height=36,
        )

        # Horizontal scroll wrapper for wide tables
        preview_scroll = ft.Row(
            [preview_table],
            scroll=ft.ScrollMode.ALWAYS,
            expand=True,
        )

        # Toggle collapse button for preview panel
        self._preview_collapsed = False

        def toggle_preview(e):
            self._preview_collapsed = not self._preview_collapsed
            preview_body.visible = not self._preview_collapsed
            preview_toggle_btn.icon = (
                ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
                if self._preview_collapsed
                else ft.Icons.KEYBOARD_ARROW_UP_ROUNDED
            )
            preview_panel.update()

        preview_toggle_btn = ft.IconButton(
            icon=ft.Icons.KEYBOARD_ARROW_UP_ROUNDED,
            icon_color=ft.Colors.GREY_400,
            icon_size=18,
            on_click=toggle_preview,
            tooltip="Collapse/Expand Data Preview",
        )

        # Both horizontal (Row) + vertical (Column) scroll for big tables
        # Kept as self._preview_scroll_col so update_preview_ui() can reset
        # it to the top on every refresh -- previously the vertical scroll
        # position was left wherever the user last scrolled it (e.g. after
        # viewing a long result set), so switching nodes or re-running the
        # pipeline could show new data starting mid-scroll instead of from
        # row 1 (client-reported: "move the scroller on top").
        self._preview_scroll_col = ft.Column(
            [preview_scroll],
            scroll=ft.ScrollMode.ALWAYS,
            expand=True,
        )
        preview_body = ft.Container(
            content=self._preview_scroll_col,
            height=160,
            padding=ft.Padding(left=0, top=8, right=0, bottom=0),
        )

        def _open_profiler(e):
            if not self.selected_node_id or not self.flow_ref:
                snack = ft.SnackBar(
                    content=ft.Text("Select a node first.", color=ft.Colors.WHITE),
                    bgcolor="#2E3D50",
                    open=True,
                )
                self.page.overlay.append(snack)
                self.page.update()
                return
            node = self.flow_ref.get_node(self.selected_node_id)
            if node:
                open_data_profiler(self.page, node)

        def _refresh_preview_clicked(e):
            self.update_preview_ui()
            self.update()

        def _extract_cell_text(cell: ft.DataCell) -> str:
            content = cell.content
            if isinstance(content, ft.GestureDetector):
                content = content.content
            return content.value if isinstance(content, ft.Text) else ""

        async def _copy_all_preview_clicked(e):
            if not self.preview_table.rows:
                return
            headers = [
                col.label.value if isinstance(col.label, ft.Text) else ""
                for col in self.preview_table.columns
            ]
            lines = ["\t".join(headers)]
            lines.extend(
                "\t".join(_extract_cell_text(c) for c in row.cells)
                for row in self.preview_table.rows
            )
            await self.main_page.clipboard.set("\n".join(lines))
            snack = ft.SnackBar(
                content=ft.Text(
                    "✓ Copied preview data to clipboard!", color=ft.Colors.WHITE
                ),
                bgcolor=ft.Colors.GREEN_800,
                open=True,
            )
            self.main_page.overlay.append(snack)
            self.main_page.update()

        _dark = self.main_page and is_dark(self.main_page)
        refresh_color = ft.Colors.BLUE_600 if not _dark else "#60A5FA"
        copy_all_btn = ft.TextButton(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.CONTENT_COPY_ROUNDED, size=14, color=refresh_color),
                    ft.Text(
                        "Copy",
                        size=12,
                        color=refresh_color,
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=4,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=_copy_all_preview_clicked,
            tooltip="Copy visible sample data to clipboard",
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.HOVERED: ft.Colors.with_opacity(0.08, refresh_color)
                },
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.Padding(left=8, top=4, right=8, bottom=4),
            ),
        )
        refresh_preview_btn = ft.TextButton(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.REFRESH_ROUNDED, size=14, color=refresh_color),
                    ft.Text(
                        "Refresh Preview",
                        size=12,
                        color=refresh_color,
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=4,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=_refresh_preview_clicked,
            tooltip="Load/recompute data preview for the selected step",
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.HOVERED: ft.Colors.with_opacity(0.08, refresh_color)
                },
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.Padding(left=8, top=4, right=8, bottom=4),
            ),
        )

        _dark = self.main_page and is_dark(self.main_page)
        profile_color = ft.Colors.BLUE_600 if not _dark else "#60A5FA"
        profile_btn = ft.TextButton(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.QUERY_STATS_ROUNDED, size=14, color=profile_color),
                    ft.Text(
                        "Profile",
                        size=12,
                        color=profile_color,
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=4,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=_open_profiler,
            tooltip="Open Data Profiler for selected node",
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.HOVERED: ft.Colors.with_opacity(0.08, profile_color)
                },
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.Padding(left=8, top=4, right=8, bottom=4),
            ),
        )

        preview_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.TABLE_CHART_ROUNDED,
                                size=16,
                                color=ft.Colors.BLUE_400,
                            ),
                            ft.Text(
                                "Data Preview (Dataryx Output)",
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color=t.TEXT_PRIMARY,
                            ),
                            ft.Container(expand=True),
                            copy_all_btn,
                            ft.Container(width=4),
                            refresh_preview_btn,
                            ft.Container(width=4),
                            profile_btn,
                            ft.Container(width=4),
                            preview_toggle_btn,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    ),
                    ft.Divider(color=t.DIVIDER, height=1),
                    preview_body,
                ],
                spacing=4,
            ),
            bgcolor=get_theme(self.main_page).BG_CARD,
            padding=ft.Padding(left=16, top=10, right=16, bottom=12),
            border_radius=ft.BorderRadius(
                top_left=8, top_right=8, bottom_left=0, bottom_right=0
            ),
        )

        # Main vertical split: top (canvas+config) and bottom (preview, full width)
        main_layout = ft.Column([top_area, preview_panel], expand=True, spacing=8)

        # Save/load view references
        self.run_btn = run_btn
        self.export_btn = export_btn
        self.steps_list = ft.Column()  # mock steps list for backward compatibility
        self.config_container = config_container
        self.preview_table = preview_table

        # --- "Add Step" popup menu (grouped by node category) ---
        def _make_add_handler(node_type: str):
            return lambda _: self.add_node(node_type)

        _input_nodes = [
            ("Read Data (File)", ft.Icons.FOLDER_OPEN_ROUNDED, "read"),
            ("Database Reader", ft.Icons.STORAGE_ROUNDED, "database_reader"),
            (
                "Cloud Storage Reader",
                ft.Icons.CLOUD_DOWNLOAD_ROUNDED,
                "cloud_storage_reader",
            ),
            ("Manual Input", ft.Icons.EDIT_NOTE_ROUNDED, "manual_input"),
            ("External Source", ft.Icons.LANGUAGE_ROUNDED, "external_source"),
        ]
        _transform_nodes = [
            ("Filter Rows", ft.Icons.FILTER_ALT_ROUNDED, "filter"),
            ("Select Columns", ft.Icons.VIEW_COLUMN_ROUNDED, "select"),
            ("Formula", ft.Icons.FUNCTIONS_ROUNDED, "formula"),
            ("Sort Data", ft.Icons.SORT_ROUNDED, "sort"),
            ("Take Sample", ft.Icons.CONTENT_CUT_ROUNDED, "sample"),
            ("Drop Duplicates", ft.Icons.DEBLUR_ROUNDED, "unique"),
            ("Text to Rows", ft.Icons.WRAP_TEXT_ROUNDED, "text_to_rows"),
            ("Add Record ID", ft.Icons.TAG_ROUNDED, "record_id"),
            ("Polars Code", ft.Icons.CODE_ROUNDED, "polars_code"),
        ]
        _aggregate_nodes = [
            ("Group By", ft.Icons.WORKSPACES_ROUNDED, "group_by"),
            ("Pivot Data", ft.Icons.PIVOT_TABLE_CHART_ROUNDED, "pivot"),
            ("Unpivot Data", ft.Icons.TABLE_ROWS_ROUNDED, "unpivot"),
            ("Count Records", ft.Icons.NUMBERS_ROUNDED, "record_count"),
        ]
        _combine_nodes = [
            ("Join", ft.Icons.MERGE_ROUNDED, "join"),
            ("Union", ft.Icons.CALL_MERGE_ROUNDED, "union"),
            ("Fuzzy Match", ft.Icons.MANAGE_SEARCH_ROUNDED, "fuzzy_match"),
            ("Cross Join", ft.Icons.GRID_ON_ROUNDED, "cross_join"),
            ("Graph Solver", ft.Icons.ACCOUNT_TREE_ROUNDED, "graph_solver"),
        ]
        _output_nodes = [
            ("Write Data (File)", ft.Icons.SAVE_ROUNDED, "output"),
            ("Database Writer", ft.Icons.STORAGE_ROUNDED, "database_writer"),
            (
                "Cloud Storage Writer",
                ft.Icons.CLOUD_UPLOAD_ROUNDED,
                "cloud_storage_writer",
            ),
            ("Explore Data", ft.Icons.BAR_CHART_ROUNDED, "explore_data"),
        ]

        def _section_items(label: str, nodes):
            items = [
                ft.PopupMenuItem(
                    content=ft.Text(
                        label,
                        color=ft.Colors.GREY_500,
                        size=11,
                        weight=ft.FontWeight.BOLD,
                    ),
                    disabled=True,
                )
            ]
            for name, icon, ntype in nodes:
                items.append(
                    ft.PopupMenuItem(
                        content=ft.Row(
                            [
                                ft.Icon(icon, size=16, color=ft.Colors.BLUE_300),
                                ft.Text(name, size=13),
                            ],
                            spacing=8,
                        ),
                        on_click=_make_add_handler(ntype),
                    )
                )
            items.append(ft.PopupMenuItem())
            return items

        all_menu_items = []
        all_menu_items += _section_items("── INPUT ──", _input_nodes)
        all_menu_items += _section_items("── TRANSFORM ──", _transform_nodes)
        all_menu_items += _section_items("── AGGREGATE ──", _aggregate_nodes)
        all_menu_items += _section_items("── COMBINE ──", _combine_nodes)
        all_menu_items += _section_items("── OUTPUT ──", _output_nodes)

        # Toggle button for Left Data Actions Panel
        self.toggle_actions_btn = ft.IconButton(
            icon=ft.Icons.MENU_OPEN_ROUNDED,
            selected_icon=ft.Icons.MENU_ROUNDED,
            selected=False,
            icon_color=ft.Colors.WHITE,
            selected_icon_color=ft.Colors.WHITE,
            bgcolor="#2563EB",
            tooltip="Toggle Data Actions Panel",
            on_click=self.toggle_data_actions_panel,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
        )

        header_row = ft.Row(
            [
                ft.Row([self.toggle_actions_btn, flow_selector_row], spacing=8),
                ft.Row([run_btn, export_btn], spacing=8),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # NOTE: do NOT call run_task here — page is not mounted yet.
        # did_mount() will safely call initialize_default_flow after mount.

        self.content = ft.Column(
            [header_row, ft.Container(height=10), main_layout], expand=True
        )

    def did_mount(self):
        """Called by Flet after DesignerView is added to the page. Safe to kick off async work here."""
        self.main_page.on_keyboard_event = self.on_keyboard
        # Register FilePicker as a Service (page.services, not page.overlay)
        if self._file_picker not in self.main_page.services:
            self.main_page.services.append(self._file_picker)
            self.main_page.update()
        self.main_page.run_task(self.initialize_default_flow)

    def _snack(self, message: str, color=None):
        """Show a brief snack-bar notification on the page."""
        bgcolor = color if color is not None else ft.Colors.BLUE_GREY_700
        snack = ft.SnackBar(
            content=ft.Text(message, color=ft.Colors.WHITE),
            bgcolor=bgcolor,
            open=True,
        )
        self.main_page.overlay.append(snack)
        self.main_page.update()

    def on_keyboard(self, e: ft.KeyboardEvent):
        # Ctrl+C or Cmd+C to copy selected node
        if (e.ctrl or e.meta) and e.key.lower() == "c":
            if self.selected_node_id:
                self.copied_node_id = self.selected_node_id
                snack = ft.SnackBar(content=ft.Text("Node copied to clipboard!"))
                self.main_page.overlay.append(snack)
                snack.open = True
                self.main_page.update()

        # Ctrl+V or Cmd+V to paste copied node
        elif (e.ctrl or e.meta) and e.key.lower() == "v":
            copied_node_id = self.copied_node_id
            if copied_node_id and self.flow_ref:
                src_node = self.flow_ref.get_node(copied_node_id)
                if src_node:
                    px = getattr(src_node, "pos_x", 0) + 40
                    py = getattr(src_node, "pos_y", 0) + 40
                    self.add_node_at_pos(src_node.node_type, px, py)
                    snack = ft.SnackBar(content=ft.Text("Node pasted!"))
                    self.main_page.overlay.append(snack)
                    snack.open = True
                    self.main_page.update()

        # Delete key (with Ctrl/Cmd/Shift modifier) to remove selected node
        elif (e.ctrl or e.meta or e.shift) and e.key == "Delete":
            if self.selected_node_id:
                self.delete_node(self.selected_node_id)
                snack = ft.SnackBar(content=ft.Text("Node deleted!"))
                self.main_page.overlay.append(snack)
                snack.open = True
                self.main_page.update()

    def add_node_at_pos(self, node_type: str, x: int, y: int):
        if not self.flow_ref:
            return

        node_id = random.randint(1000, 9999)
        node_promise = NodePromise(
            flow_id=self.active_flow_id,
            node_id=node_id,
            node_type=node_type,
            pos_x=x,
            pos_y=y,
        )

        from core.configs.node_store.nodes import get_all_standard_nodes

        _, _, node_defaults = get_all_standard_nodes()
        has_default = (
            node_type in node_defaults and node_defaults[node_type].has_default_settings
        )

        self.flow_ref.add_node_promise(node_promise, track_history=False)

        if node_type in ["read", "read_csv"]:
            from core.schemas.input_schema import ReceivedTable, InputCsvTable, NodeRead

            rt = ReceivedTable(path="", file_type="csv", table_settings=InputCsvTable())
            initial_settings = NodeRead(
                flow_id=self.active_flow_id,
                node_id=node_id,
                node_type="read",
                received_file=rt,
            )
            self.flow_ref.add_read(initial_settings)
        elif node_type == "explore_data":
            self.flow_ref.add_initial_node_analysis(node_promise)
        else:
            if has_default:
                setting_name_ref = "node" + node_type.replace("_", "")
                node_model = None
                for ref_name, ref in inspect.getmodule(input_schema).__dict__.items():
                    if ref_name.lower() == setting_name_ref:
                        node_model = ref
                        break

                if node_model:
                    try:
                        add_func = getattr(self.flow_ref, "add_" + node_type)
                        initial_params = {
                            "flow_id": self.active_flow_id,
                            "node_id": node_id,
                            "cache_results": False,
                            "pos_x": x,
                            "pos_y": y,
                            "node_type": node_type,
                        }
                        initial_settings = node_model(**initial_params)
                        add_func(initial_settings)
                    except Exception as e:
                        print(f"Error cloning settings for {node_type}:", e)

        self.save_active_flow()
        self.selected_node_id = node_id
        self.update_steps_ui()
        self.update_config_ui()
        self.update_preview_ui()
        self.update()

    async def initialize_default_flow(self):
        """Loads or creates the default flow. Called from did_mount so page is guaranteed mounted."""
        try:
            user_id = (
                auth_service.user_info.get("id") if auth_service.user_info else None
            )

            # Auto-import existing flows from disk on startup
            from core.shared.storage_config import storage

            flows_dirs = [storage.flows_directory, storage.temp_directory_for_flows]
            existing_paths = {
                getattr(f.flow_settings, "path", None)
                for f in flow_file_handler.get_user_flows(user_id)
            }

            for flows_dir in flows_dirs:
                if flows_dir.exists():
                    for file_path in flows_dir.glob("*.yaml"):
                        if str(file_path) not in existing_paths:
                            try:
                                flow_file_handler.import_flow(
                                    file_path, user_id=user_id
                                )
                                existing_paths.add(str(file_path))
                            except Exception as ex:
                                print(f"Error importing flow {file_path}: {ex}")
                    for file_path in flows_dir.glob("*.yml"):
                        if str(file_path) not in existing_paths:
                            try:
                                flow_file_handler.import_flow(
                                    file_path, user_id=user_id
                                )
                                existing_paths.add(str(file_path))
                            except Exception as ex:
                                print(f"Error importing flow {file_path}: {ex}")

            flows = flow_file_handler.get_user_flows(user_id)
            if flows:
                # Sort flows by modification time (descending) to select the most recently updated flow first
                def get_mod_time(f):
                    ts = getattr(f.flow_settings, "modified_on", 0) or 0
                    return ts

                flows.sort(key=get_mod_time, reverse=True)
                self.active_flow_id = flows[0].flow_id
            else:
                self.active_flow_id = flow_file_handler.add_flow(
                    "My_First_Dataryx_Flow", user_id=user_id
                )

            self.flow_ref = flow_file_handler.get_flow(self.active_flow_id)

            # Load the flow list into dropdown
            self.load_flow_list()

            self.run_btn.disabled = False
            self.export_btn.disabled = False

            self.update_steps_ui()
            if is_mounted(self):
                self.update()
        except Exception as e:
            print("Error initializing default flow:", e)
            traceback.print_exc()

    def load_flow_list(self):
        user_id = auth_service.user_info.get("id") if auth_service.user_info else None
        flows = flow_file_handler.get_user_flows(user_id)

        # Client-requested: let the Scheduler default to "the flow I'm
        # currently working on" instead of making the user browse for it.
        # load_flow_list() runs right after every place self.flow_ref
        # changes (init/switch/import/create), so this is the single spot
        # that reliably sees every active-flow change.
        if self.flow_ref is not None:
            from core.shared.storage_config import storage

            fs = self.flow_ref.flow_settings
            storage.set_last_active_flow(
                getattr(fs, "path", None), getattr(fs, "name", None)
            )

        self.flow_dropdown.items.clear()

        for f in flows:
            fid = f.flow_id
            name = f.__name__ or str(fid)
            is_active = fid == self.active_flow_id

            if is_active:
                item_content = ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.CHECK_ROUNDED, size=16, color=ft.Colors.BLUE_400
                        ),
                        ft.Text(
                            name,
                            size=13,
                            color=ft.Colors.BLUE_400,
                            weight=ft.FontWeight.BOLD,
                        ),
                    ],
                    spacing=8,
                )
            else:
                item_content = ft.Row(
                    [
                        ft.Container(width=24),
                        ft.Text(name, size=13, color=ft.Colors.WHITE),
                    ],
                    spacing=8,
                )

            self.flow_dropdown.items.append(
                ft.PopupMenuItem(
                    content=item_content,
                    on_click=lambda _, captured_id=fid: self.switch_flow_by_id(
                        captured_id
                    ),
                )
            )

        # Update button label to show active flow name
        active_name = next(
            (
                f.__name__ or str(f.flow_id)
                for f in flows
                if f.flow_id == self.active_flow_id
            ),
            "Select Flow",
        )
        self.flow_label_text.value = active_name
        if self.flow_dropdown.page:
            try:
                self.flow_dropdown.update()
            except Exception:
                pass
        logger.debug(
            f"load_flow_list: flows={[f.__name__ for f in flows]}, active={self.active_flow_id}"
        )

    def switch_flow_by_id(self, flow_id):
        logger.debug(f"switch_flow_by_id triggered: flow_id={flow_id}")
        self.active_flow_id = flow_id
        self.flow_ref = flow_file_handler.get_flow(flow_id)
        self.selected_node_id = None
        self.run_btn.disabled = False
        self.export_btn.disabled = False

        self.load_flow_list()  # refresh checkmark in menu
        self.update_steps_ui()
        self.update_config_ui()
        self.update_preview_ui()
        if is_mounted(self):
            self.page.update()

    def import_flow_from_file(self, e):
        from pathlib import Path

        user_id = auth_service.user_info.get("id") if auth_service.user_info else None

        async def _pick_flow():
            files = await self._file_picker.pick_files(
                dialog_title="Import Flow File",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["yaml", "yml", "json"],
                allow_multiple=False,
            )
            if files and files[0].path:
                file_path = Path(files[0].path)
                try:
                    flow_id = flow_file_handler.import_flow(file_path, user_id=user_id)
                    self.active_flow_id = flow_id
                    self.flow_ref = flow_file_handler.get_flow(flow_id)
                    self.selected_node_id = None
                    self.load_flow_list()
                    self.run_btn.disabled = False
                    self.export_btn.disabled = False
                    self.update_steps_ui()
                    self.update_config_ui()
                    self.update_preview_ui()
                    self.canvas.load_flow_canvas()
                    self.update()
                    self.show_dialog(
                        "Success", f"Successfully imported flow: {file_path.stem}"
                    )
                except Exception as ex:
                    self.show_dialog(
                        "Import Error", f"Failed to import flow file: {str(ex)}"
                    )

        self.main_page.run_task(_pick_flow)

    # --- Client Requested Left "Data Actions" Sidebar Panel ---
    node_categories = {
        "Input Sources": [
            ("Read Data", ft.Icons.TABLE_CHART_ROUNDED, "read_csv"),
            ("Database Reader", ft.Icons.STORAGE_ROUNDED, "database_reader"),
            (
                "Cloud Storage Reader",
                ft.Icons.CLOUD_DOWNLOAD_ROUNDED,
                "cloud_storage_reader",
            ),
            ("Manual Input", ft.Icons.EDIT_NOTE_ROUNDED, "manual_input"),
            ("External Source", ft.Icons.LANGUAGE_ROUNDED, "external_source"),
        ],
        "Transformations": [
            ("Filter Rows", ft.Icons.FILTER_ALT_ROUNDED, "filter"),
            ("Select Columns", ft.Icons.VIEW_COLUMN_ROUNDED, "select"),
            ("Formula", ft.Icons.FUNCTIONS_ROUNDED, "formula"),
            ("Sort Data", ft.Icons.SORT_ROUNDED, "sort"),
            ("Take Sample", ft.Icons.CONTENT_CUT_ROUNDED, "sample"),
            ("Drop Duplicates", ft.Icons.DEBLUR_ROUNDED, "unique"),
            ("Text to Rows", ft.Icons.WRAP_TEXT_ROUNDED, "text_to_rows"),
            ("Add Record ID", ft.Icons.TAG_ROUNDED, "record_id"),
            ("Polars Code", ft.Icons.CODE_ROUNDED, "polars_code"),
        ],
        "Combine Operations": [
            ("Join", ft.Icons.MERGE_ROUNDED, "join"),
            ("Union", ft.Icons.CALL_MERGE_ROUNDED, "union"),
            ("Fuzzy Match", ft.Icons.MANAGE_SEARCH_ROUNDED, "fuzzy_match"),
            ("Cross Join", ft.Icons.GRID_ON_ROUNDED, "cross_join"),
            ("Graph Solver", ft.Icons.ACCOUNT_TREE_ROUNDED, "graph_solver"),
        ],
        "Aggregations": [
            ("Group By", ft.Icons.WORKSPACES_ROUNDED, "group_by"),
            ("Pivot Data", ft.Icons.PIVOT_TABLE_CHART_ROUNDED, "pivot"),
            ("Unpivot Data", ft.Icons.TABLE_ROWS_ROUNDED, "unpivot"),
            ("Count Records", ft.Icons.NUMBERS_ROUNDED, "record_count"),
            ("Window Function", ft.Icons.WINDOW_ROUNDED, "window"),
        ],
        "Output": [
            ("Write CSV/Parquet", ft.Icons.SAVE_ROUNDED, "output"),
            ("Database Writer", ft.Icons.STORAGE_ROUNDED, "database_writer"),
            (
                "Cloud Storage Writer",
                ft.Icons.CLOUD_UPLOAD_ROUNDED,
                "cloud_storage_writer",
            ),
            ("Explore Data", ft.Icons.BAR_CHART_ROUNDED, "explore_data"),
        ],
    }

    def toggle_data_actions_panel(self, e=None):
        self.data_actions_panel.visible = not self.data_actions_panel.visible
        self.toggle_actions_btn.selected = self.data_actions_panel.visible
        try:
            self.toggle_actions_btn.update()
        except Exception:
            pass
        try:
            self.data_actions_panel.update()
        except Exception:
            pass

    def _build_data_actions_panel(self):
        t = get_theme(self.main_page)

        search_tf = ft.TextField(
            hint_text="Search nodes...",
            prefix_icon=ft.Icons.SEARCH_ROUNDED,
            height=36,
            text_size=12,
            content_padding=ft.Padding(left=8, top=0, right=8, bottom=0),
            border_radius=6,
            border_color=t.BORDER,
            on_change=lambda e: filter_nodes(e.control.value),
        )

        categories_container = ft.Column(spacing=4, expand=True)
        accordion_states = {}

        def toggle_category(cat_name):
            visible = not accordion_states[cat_name]["items"].visible
            accordion_states[cat_name]["items"].visible = visible
            accordion_states[cat_name]["icon"].name = (
                ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
                if visible
                else ft.Icons.KEYBOARD_ARROW_RIGHT_ROUNDED
            )
            self.update()

        def make_node_item(name, icon, ntype):
            def on_node_click(e):
                self.add_node(ntype)

            return ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(icon, size=16, color=ft.Colors.BLUE_400),
                        ft.Text(name, size=12, color=t.TEXT_PRIMARY),
                    ],
                    spacing=8,
                ),
                padding=ft.Padding(left=12, top=6, right=12, bottom=6),
                border_radius=4,
                on_click=on_node_click,
                ink=True,
            )

        def rebuild_categories(filter_text=""):
            categories_container.controls.clear()
            filter_text = filter_text.lower().strip()

            for cat, items in self.node_categories.items():
                filtered_items = [
                    (name, icon, ntype)
                    for name, icon, ntype in items
                    if filter_text in name.lower() or filter_text in ntype.lower()
                ]

                if filter_text and not filtered_items:
                    continue

                chevron_icon = ft.Icon(
                    ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED,
                    size=16,
                    color=t.TEXT_HINT,
                )

                header_row = ft.Container(
                    content=ft.Row(
                        [
                            ft.Text(
                                cat,
                                size=11,
                                weight=ft.FontWeight.BOLD,
                                color=t.TEXT_HINT,
                                expand=True,
                            ),
                            chevron_icon,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    padding=ft.Padding(left=4, top=6, right=4, bottom=6),
                    on_click=lambda _, c=cat: toggle_category(c),
                    ink=True,
                )

                items_col = ft.Column(
                    controls=[
                        make_node_item(name, icon, ntype)
                        for name, icon, ntype in filtered_items
                    ],
                    spacing=2,
                    visible=True,
                )

                accordion_states[cat] = {"icon": chevron_icon, "items": items_col}

                categories_container.controls.append(
                    ft.Column(
                        [header_row, items_col],
                        spacing=0,
                    )
                )

            try:
                categories_container.update()
            except Exception:
                pass

        def filter_nodes(val):
            rebuild_categories(val)

        rebuild_categories()

        scrollable_content = ft.Column(
            [categories_container],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        panel = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.GRID_VIEW_ROUNDED,
                                color=ft.Colors.BLUE_400,
                                size=16,
                            ),
                            ft.Text(
                                "Data Actions",
                                size=13,
                                weight=ft.FontWeight.BOLD,
                                color=t.TEXT_PRIMARY,
                                expand=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
                                icon_size=16,
                                icon_color=t.TEXT_SECONDARY,
                                tooltip="Hide Actions Panel",
                                on_click=self.toggle_data_actions_panel,
                            ),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(color=t.DIVIDER, height=1),
                    search_tf,
                    scrollable_content,
                ],
                spacing=8,
                expand=True,
            ),
            bgcolor=t.BG_CARD,
            padding=ft.Padding(left=12, top=12, right=12, bottom=12),
            width=260,
            border_radius=8,
            border=ft.Border.all(1, t.BORDER),
            shadow=ft.BoxShadow(
                blur_radius=15,
                color=(
                    ft.Colors.with_opacity(0.4, ft.Colors.BLACK)
                    if is_dark(self.main_page)
                    else ft.Colors.with_opacity(0.15, ft.Colors.BLACK)
                ),
                offset=ft.Offset(0, 4),
            ),
            visible=False,
            left=12,
            top=12,
            bottom=12,
            animate=ft.Animation(duration=200, curve=ft.AnimationCurve.EASE_IN_OUT),
        )
        return panel

    def create_new_flow(self, e):
        from pathlib import Path

        name_input = ft.TextField(
            label="Flow Name",
            hint_text="e.g. My_Custom_Flow",
            autofocus=True,
            width=320,
            text_size=13,
        )

        chosen_dir = [None]  # boxed so the nested handlers can set it

        location_input = ft.TextField(
            label="Save Location (optional)",
            hint_text="Default location",
            read_only=True,
            width=320,
            text_size=13,
        )

        def choose_location(evt):
            async def _pick():
                picked = await self._file_picker.get_directory_path(
                    dialog_title="Choose Folder to Store Flow",
                )
                if picked:
                    chosen_dir[0] = picked
                    location_input.value = picked
                    location_input.update()

            self.main_page.run_task(_pick)

        browse_location_btn = ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
            icon_color=ft.Colors.BLUE_400,
            tooltip="Browse for folder",
            on_click=choose_location,
        )

        def confirm_create(evt):
            flow_name = name_input.value.strip()
            if not flow_name:
                name_input.error_text = "Flow name cannot be empty"
                name_input.update()
                return

            self.main_page.pop_dialog()

            user_id = (
                auth_service.user_info.get("id") if auth_service.user_info else None
            )
            flow_path = (
                str(Path(chosen_dir[0]) / f"{flow_name}.yaml")
                if chosen_dir[0]
                else None
            )
            new_flow_id = flow_file_handler.add_flow(
                flow_name, flow_path=flow_path, user_id=user_id
            )
            self.active_flow_id = new_flow_id
            self.flow_ref = flow_file_handler.get_flow(new_flow_id)
            self.selected_node_id = None

            self.load_flow_list()

            self.run_btn.disabled = False
            self.export_btn.disabled = False

            self.update_steps_ui()
            self.update_config_ui()
            self.update_preview_ui()
            self.update()

            if is_mounted(self):
                snack = ft.SnackBar(content=ft.Text(f"Created flow: {flow_name}"))
                self.page.overlay.append(snack)
                snack.open = True
                self.page.update()

        def cancel_create(evt):
            self.main_page.pop_dialog()

        dialog = ft.AlertDialog(
            title=ft.Text("Create New Flow"),
            content=ft.Column(
                [
                    name_input,
                    ft.Row(
                        [location_input, browse_location_btn],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=10,
                tight=True,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=cancel_create),
                ft.Button(
                    "Create",
                    on_click=confirm_create,
                    bgcolor="#2563EB",
                    color=ft.Colors.WHITE,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.main_page.show_dialog(dialog)

    def update_steps_ui(self):
        if hasattr(self, "canvas") and self.canvas:
            self.canvas.flow_ref = self.flow_ref
            self.canvas.load_flow_canvas()

    def select_node(self, node_id):
        self.selected_node_id = node_id
        # Use the lightweight selection-only canvas refresh instead of a full
        # load_flow_canvas() call (which would rebuild the grid + all node cards).
        # update_steps_ui() is intentionally kept for add_node/delete/reload callers.
        if hasattr(self, "canvas") and self.canvas:
            self.canvas.update_selection_only(node_id)
        self.update_config_ui()
        # Selecting a node no longer auto-recomputes its preview -- for a
        # node sitting downstream of a Group By/Sort/Join on a large
        # dataset, that recompute can take a long time and made simply
        # clicking through the canvas feel like it was hanging. Preview is
        # now opt-in via the "Refresh Preview" button; settings-save still
        # auto-refreshes since that's a deliberate action, not browsing.
        self._show_preview_placeholder()
        self.update()
        if self.selected_node_id is not None:
            self.show_config_dialog()

    # Node types with no configurable settings at all -- opening a dialog
    # that just says "This step runs with standard automated values" adds
    # a click with no purpose, so skip it entirely (client-requested).
    NODE_TYPES_WITHOUT_SETTINGS = {"record_count"}

    def show_config_dialog(self):
        if self.selected_node_id is None or not self.flow_ref:
            return
        node = self.flow_ref.get_node(self.selected_node_id)
        if not node:
            return
        if node.node_type in self.NODE_TYPES_WITHOUT_SETTINGS:
            return

        friendly_title = node.node_type.replace("_", " ").title()
        t = get_theme(self.main_page)

        def close_dialog(e):
            self.main_page.pop_dialog()

        # Dynamically size the dialog based on node type
        is_large_editor = node.node_type in ("formula", "polars_code", "filter")
        # Window has more stacked fields (output col, value col, function,
        # partition checklist, order by, descending) than the default
        # 500px dialog comfortably fits without scrolling to the Save button.
        is_medium_editor = node.node_type == "window"
        if is_large_editor:
            width, height = 800, 600
        elif is_medium_editor:
            width, height = 520, 680
        else:
            width, height = 500, 500

        # Wrap in a scrollable, well-padded container
        dialog_content = ft.Container(
            content=self.config_container,
            width=width,
            height=height,
            padding=ft.Padding(left=8, top=8, right=8, bottom=8),
        )

        close_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_size=18,
            icon_color=ft.Colors.GREY_400,
            tooltip="Close",
            on_click=close_dialog,
            style=ft.ButtonStyle(
                padding=ft.Padding(left=4, top=4, right=4, bottom=4),
            ),
        )

        title_row = ft.Row(
            [
                ft.Icon(ft.Icons.SETTINGS_ROUNDED, size=20, color=ft.Colors.BLUE_400),
                ft.Text(
                    f"Configure {friendly_title} Step (#{node.node_id})",
                    size=16,
                    weight=ft.FontWeight.W_700,
                    color=t.TEXT_PRIMARY,
                    expand=True,
                ),
                close_btn,
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        dialog = ft.AlertDialog(
            modal=True,
            title=title_row,
            content=dialog_content,
            bgcolor=t.BG_PAGE,
            shape=ft.RoundedRectangleBorder(radius=10),
        )
        self.main_page.show_dialog(dialog)

    def add_node(self, node_type: str):
        if not self.flow_ref:
            return

        node_id = random.randint(1000, 9999)
        node_promise = NodePromise(
            flow_id=self.active_flow_id,
            node_id=node_id,
            node_type=node_type,
            pos_x=0,
            pos_y=0,
        )

        from core.configs.node_store.nodes import get_all_standard_nodes

        _, _, node_defaults = get_all_standard_nodes()
        has_default = (
            node_type in node_defaults and node_defaults[node_type].has_default_settings
        )

        if node_type in ["read", "read_csv"]:
            from core.schemas.input_schema import ReceivedTable, InputCsvTable, NodeRead

            rt = ReceivedTable(path="", file_type="csv", table_settings=InputCsvTable())
            initial_settings = NodeRead(
                flow_id=self.active_flow_id,
                node_id=node_id,
                node_type="read",
                received_file=rt,
            )
            self.flow_ref.add_node_promise(node_promise, track_history=False)
            self.flow_ref.add_read(initial_settings)
        elif node_type == "explore_data":
            self.flow_ref.add_initial_node_analysis(node_promise)
        else:
            self.flow_ref.add_node_promise(node_promise, track_history=False)
            if has_default:
                setting_name_ref = "node" + node_type.replace("_", "")
                node_model = None
                for ref_name, ref in inspect.getmodule(input_schema).__dict__.items():
                    if ref_name.lower() == setting_name_ref:
                        node_model = ref
                        break

                if node_model:
                    try:
                        add_func = getattr(self.flow_ref, "add_" + node_type)
                        initial_params = {
                            "flow_id": self.active_flow_id,
                            "node_id": node_id,
                            "cache_results": False,
                            "pos_x": 0,
                            "pos_y": 0,
                            "node_type": node_type,
                        }

                        initial_settings = node_model(**initial_params)
                        add_func(initial_settings)
                    except Exception as e:
                        print(f"Error adding default settings for {node_type}:", e)

        self.save_active_flow()
        self.selected_node_id = node_id
        self.update_steps_ui()
        self.update_config_ui()
        self.update_preview_ui()
        self.update()

    def delete_node(self, node_id):
        if not self.flow_ref:
            return
        self.flow_ref.delete_node(node_id)
        self.save_active_flow()
        if self.selected_node_id == node_id:
            self.selected_node_id = None
        self.update_steps_ui()
        self.update_config_ui()
        self.update_preview_ui()
        self.update()

    def get_incoming_columns(self):
        if not self.flow_ref or self.selected_node_id is None:
            return []
        try:
            node = self.flow_ref.get_node(self.selected_node_id)
            if node:
                node_data = node.get_node_data(
                    flow_id=self.active_flow_id, include_example=False
                )
                if node_data and node_data.main_input and node_data.main_input.columns:
                    return node_data.main_input.columns
        except Exception as e:
            print("Error getting incoming columns:", e)
        return []

    def _build_manual_input_ui(self, node):
        """Build a spreadsheet-style editor for manual_input nodes."""
        from core.schemas.input_schema import NodeManualInput, RawData, MinimalFieldInfo

        # ── Load existing data from node ──────────────────────────────────────
        raw = (
            getattr(node.setting_input, "raw_data_format", None)
            if node.setting_input
            else None
        )
        col_names: list[str] = (
            [c.name for c in raw.columns] if (raw and raw.columns) else ["col_1"]
        )
        col_types: list[str] = (
            [c.data_type for c in raw.columns] if (raw and raw.columns) else ["Utf8"]
        )

        # Convert column-oriented data → row-oriented  list[list]
        if raw and raw.data and len(raw.data) > 0 and len(raw.data[0]) > 0:
            num_rows = len(raw.data[0])
            row_data: list[list[str]] = [
                [str(raw.data[ci][ri]) for ci in range(len(col_names))]
                for ri in range(num_rows)
            ]
        else:
            row_data = [["" for _ in col_names]]

        TYPE_OPTIONS = ["Utf8", "Int64", "Float64", "Boolean", "Date", "Datetime"]

        # ── Mutable state ─────────────────────────────────────────────────────
        state = {
            "cols": list(col_names),
            "types": list(col_types),
            "rows": [list(r) for r in row_data],
        }

        # ── Grid container (vertical scroll) ──────────────────────────────────
        # COL_W: fixed width per column cell; ROW_NUM_W: row-number gutter
        COL_W = 110
        ROW_NUM_W = 28

        grid_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)

        def rebuild_grid():
            grid_col.controls.clear()
            cols = state["cols"]
            types = state["types"]
            rows = state["rows"]

            # ── Header row ───────────────────────────────────────────────────
            # Each column header = name TextField + type Dropdown + ✕ button,
            # all packed inside a fixed-width Container so alignment is exact.
            header_cells = [ft.Container(width=ROW_NUM_W)]  # gutter spacer

            for ci, (cname, ctype) in enumerate(zip(cols, types)):
                ci_cap = ci

                name_field = ft.TextField(
                    value=cname,
                    text_size=11,
                    height=30,
                    content_padding=ft.Padding(left=5, top=2, right=2, bottom=2),
                    bgcolor="#2A3040",
                    border_color=ft.Colors.GREY_700,
                    focused_border_color=ft.Colors.BLUE_400,
                    color=ft.Colors.WHITE,
                    expand=True,
                )
                name_field.on_change = lambda e, idx=ci_cap: _update_col_name(
                    idx, e.control.value
                )

                del_btn = ft.IconButton(
                    icon=ft.Icons.CLOSE_ROUNDED,
                    icon_color=ft.Colors.RED_400,
                    icon_size=12,
                    tooltip=f"Remove column",
                    width=24,
                    height=24,
                    padding=0,
                    on_click=lambda e, idx=ci_cap: _delete_col(idx),
                )

                type_dd = ft.Dropdown(
                    value=ctype,
                    options=[ft.dropdown.Option(t) for t in TYPE_OPTIONS],
                    text_size=10,
                    height=28,
                    content_padding=ft.Padding(left=5, top=0, right=2, bottom=0),
                    bgcolor="#2A3040",
                    border_color=ft.Colors.GREY_700,
                    color=ft.Colors.WHITE,
                    expand=True,
                )
                type_dd.on_select = lambda e, idx=ci_cap: _update_col_type(
                    idx, e.control.value
                )

                header_cells.append(
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [name_field, del_btn],
                                    spacing=0,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                type_dd,
                            ],
                            spacing=2,
                        ),
                        width=COL_W,
                    )
                )

            # Row-delete gutter spacer (aligns with data row delete buttons)
            header_cells.append(ft.Container(width=28))

            grid_col.controls.append(ft.Row(header_cells, spacing=4))
            grid_col.controls.append(ft.Divider(height=1, color=ft.Colors.GREY_700))

            # ── Data rows ────────────────────────────────────────────────────
            for ri, row in enumerate(rows):
                ri_cap = ri
                row_cells = [
                    ft.Container(
                        content=ft.Text(str(ri + 1), size=10, color=ft.Colors.GREY_500),
                        width=ROW_NUM_W,
                        alignment=ft.Alignment(0, 0),
                    )
                ]
                for ci2 in range(len(cols)):
                    ci2_cap = ci2
                    cell_val = row[ci2] if ci2 < len(row) else ""
                    cell_field = ft.TextField(
                        value=cell_val,
                        text_size=11,
                        height=30,
                        content_padding=ft.Padding(left=5, top=2, right=4, bottom=2),
                        bgcolor=get_theme(self.main_page).BG_CARD,
                        border_color=ft.Colors.GREY_800,
                        focused_border_color=ft.Colors.BLUE_400,
                        color=ft.Colors.WHITE,
                    )
                    cell_field.on_change = lambda e, r=ri_cap, c=ci2_cap: _update_cell(
                        r, c, e.control.value
                    )
                    row_cells.append(ft.Container(content=cell_field, width=COL_W))

                del_row_btn = ft.IconButton(
                    icon=ft.Icons.REMOVE_CIRCLE_OUTLINE_ROUNDED,
                    icon_color=ft.Colors.RED_300,
                    icon_size=14,
                    tooltip="Remove row",
                    width=28,
                    height=28,
                    padding=0,
                    on_click=lambda e, r=ri_cap: _delete_row(r),
                )
                row_cells.append(del_row_btn)
                grid_col.controls.append(ft.Row(row_cells, spacing=4))

            try:
                if grid_col.page:
                    grid_col.update()
            except RuntimeError:
                pass

        def _update_col_name(idx, val):
            if 0 <= idx < len(state["cols"]):
                state["cols"][idx] = val

        def _update_col_type(idx, val):
            if 0 <= idx < len(state["types"]):
                state["types"][idx] = val

        def _update_cell(row, col, val):
            while len(state["rows"][row]) <= col:
                state["rows"][row].append("")
            state["rows"][row][col] = val

        def _delete_col(idx):
            if len(state["cols"]) <= 1:
                return
            state["cols"].pop(idx)
            state["types"].pop(idx)
            for r in state["rows"]:
                if idx < len(r):
                    r.pop(idx)
            rebuild_grid()

        def _delete_row(idx):
            if len(state["rows"]) <= 1:
                return
            state["rows"].pop(idx)
            rebuild_grid()

        def add_col(e):
            new_name = f"col_{len(state['cols']) + 1}"
            state["cols"].append(new_name)
            state["types"].append("Utf8")
            for r in state["rows"]:
                r.append("")
            rebuild_grid()

        def add_row(e):
            state["rows"].append(["" for _ in state["cols"]])
            rebuild_grid()

        def save_manual_input(e):
            cols = state["cols"]
            types = state["types"]
            rows = state["rows"]
            # Convert row-oriented → column-oriented
            col_data = [
                [rows[ri][ci] if ci < len(rows[ri]) else "" for ri in range(len(rows))]
                for ci in range(len(cols))
            ]

            # Cast values to appropriate types
            def cast_val(val, dtype):
                if dtype in ("Int64",):
                    try:
                        return int(val)
                    except:
                        return 0
                if dtype in ("Float64",):
                    try:
                        return float(val)
                    except:
                        return 0.0
                if dtype == "Boolean":
                    return val.lower() in ("true", "1", "yes")
                return str(val)

            col_data_cast = [
                [cast_val(v, types[ci]) for v in col_data[ci]]
                for ci in range(len(cols))
            ]
            columns = [
                MinimalFieldInfo(name=n, data_type=t) for n, t in zip(cols, types)
            ]
            raw_data = RawData(columns=columns, data=col_data_cast)
            new_input = NodeManualInput(
                flow_id=self.active_flow_id,
                node_id=node.node_id,
                raw_data_format=raw_data,
            )
            try:
                self.flow_ref.add_manual_input(new_input)
                self.save_active_flow()
                self.update_preview_ui()
                if is_mounted(self):
                    snack = ft.SnackBar(
                        content=ft.Text(
                            f"✓ Manual Input saved — {len(cols)} cols × {len(rows)} rows",
                            color=ft.Colors.WHITE,
                        ),
                        bgcolor="#1E7E34",
                        duration=2000,
                    )
                    self.page.overlay.append(snack)
                    snack.open = True
                    self.page.update()
            except Exception as ex:
                self.show_dialog("Error saving Manual Input", str(ex))

        rebuild_grid()

        # ── Action buttons ────────────────────────────────────────────────────
        action_row = ft.Row(
            [
                ft.Button(
                    "+ Add Column",
                    icon=ft.Icons.ADD_BOX_ROUNDED,
                    bgcolor="#2563EB",
                    color=ft.Colors.WHITE,
                    height=34,
                    on_click=add_col,
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
                ),
                ft.Button(
                    "+ Add Row",
                    icon=ft.Icons.ADD_ROUNDED,
                    bgcolor="#1E7E34",
                    color=ft.Colors.WHITE,
                    height=34,
                    on_click=add_row,
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
                ),
            ],
            spacing=8,
        )

        save_btn = ft.Button(
            "💾  Save Data",
            bgcolor="#2563EB",
            color=ft.Colors.WHITE,
            height=38,
            on_click=save_manual_input,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
        )

        hint_text = ft.Text(
            "Click any cell to edit. Add columns/rows as needed, then Save.",
            size=11,
            color=ft.Colors.GREY_500,
            italic=True,
        )

        # Wrap grid_col in a Row with horizontal scroll so many columns are reachable
        grid_scroll = ft.Row(
            controls=[
                ft.Container(
                    content=grid_col,
                    bgcolor=get_theme(self.main_page).BG_PAGE,
                    border=ft.Border.all(1, ft.Colors.GREY_800),
                    border_radius=6,
                    padding=8,
                )
            ],
            scroll=ft.ScrollMode.AUTO,
        )

        self.config_container.controls.extend(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                "Data Table",
                                size=13,
                                color=ft.Colors.GREY_300,
                                weight=ft.FontWeight.W_600,
                            ),
                            hint_text,
                            grid_scroll,
                            action_row,
                            ft.Divider(color=ft.Colors.GREY_800),
                            save_btn,
                        ],
                        spacing=10,
                    ),
                    padding=ft.Padding(top=8, left=0, right=0, bottom=0),
                )
            ]
        )

    def update_config_ui(self):

        self.config_container.controls.clear()
        if self.selected_node_id is None or not self.flow_ref:
            self.config_container.controls.append(
                ft.Text(
                    "Select a step to configure parameters", color=ft.Colors.GREY_500
                )
            )
            return

        node = self.flow_ref.get_node(self.selected_node_id)
        if not node:
            return

        # Automatically upgrade NodePromise settings placeholders to proper Pydantic settings models
        if isinstance(node.setting_input, NodePromise):
            import inspect
            from core.schemas import input_schema

            setting_name_ref = "node" + node.node_type.replace("_", "")
            node_model = None
            for ref_name, ref in inspect.getmodule(input_schema).__dict__.items():
                if ref_name.lower() == setting_name_ref:
                    node_model = ref
                    break
            if node_model:
                depending_id = None
                try:
                    main_inputs = node.node_inputs.main_inputs
                    if main_inputs:
                        depending_id = main_inputs[0].node_id
                except Exception:
                    pass
                if depending_id is None:
                    depending_id = getattr(node.setting_input, "depending_on_id", None)
                depending_ids = [depending_id] if depending_id is not None else []

                initial_params = {
                    "flow_id": getattr(node.setting_input, "flow_id", None)
                    or self.active_flow_id,
                    "node_id": node.node_id,
                    "cache_results": getattr(
                        node.setting_input, "cache_results", False
                    ),
                    "pos_x": getattr(node.setting_input, "pos_x", 0.0),
                    "pos_y": getattr(node.setting_input, "pos_y", 0.0),
                    "description": getattr(node.setting_input, "description", ""),
                    "node_reference": getattr(
                        node.setting_input, "node_reference", None
                    ),
                    "user_id": (
                        getattr(node.setting_input, "user_id", None)
                        or (
                            auth_service.user_info.get("id", 1)
                            if auth_service.user_info
                            else 1
                        )
                    ),
                    "is_flow_output": getattr(
                        node.setting_input, "is_flow_output", False
                    ),
                    "is_user_defined": getattr(
                        node.setting_input, "is_user_defined", False
                    ),
                    "output_field_config": getattr(
                        node.setting_input, "output_field_config", None
                    ),
                }
                if "depending_on_id" in node_model.model_fields:
                    initial_params["depending_on_id"] = depending_id or -1
                if "depending_on_ids" in node_model.model_fields:
                    initial_params["depending_on_ids"] = depending_ids

                try:
                    node.setting_input = instantiate_with_defaults(
                        node_model, initial_params
                    )
                except Exception as e:
                    print(
                        f"Error upgrading settings input placeholder for {node.node_type}:",
                        e,
                    )

        # Core node type heading - removed redundant text in pop-up dialog
        pass

        incoming_cols = self.get_incoming_columns()

        # Custom high-fidelity form builders for major ETL nodes
        if node.node_type == "explore_data":
            # Explore Data has NO configuration — it simply reads from its connected input node.
            input_nodes = node.all_inputs
            if input_nodes:
                input_node = input_nodes[0]
                status_text = f"✓ Connected to node {input_node.node_id}"
                status_color = ft.Colors.GREEN_400
            else:
                status_text = "⚠ Connect this node to a data source"
                status_color = ft.Colors.AMBER_400

            self.config_container.controls.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Icon(
                                        ft.Icons.INFO_OUTLINE,
                                        color=ft.Colors.BLUE_300,
                                        size=18,
                                    ),
                                    ft.Text(
                                        "No configuration required",
                                        color=ft.Colors.WHITE,
                                        weight=ft.FontWeight.BOLD,
                                        size=13,
                                    ),
                                ],
                                spacing=6,
                            ),
                            ft.Text(
                                "This node automatically reads data from the previous node. "
                                "Just connect it to any data source or transformation node.",
                                color=ft.Colors.GREY_400,
                                size=12,
                            ),
                            ft.Container(height=8),
                            ft.Text(status_text, color=status_color, size=12),
                        ],
                        spacing=8,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
                    border_radius=8,
                    padding=12,
                )
            )
        elif node.node_type == "unique":
            from core.schemas.transform_schema import UniqueInput

            t = get_theme(self.main_page)
            setting = node.setting_input
            raw_ui = getattr(setting, "unique_input", None)
            # Guard: if deserialized as raw str/dict from old format, rebuild properly
            if isinstance(raw_ui, str) or not isinstance(raw_ui, UniqueInput):
                raw_ui = UniqueInput()
                setting.unique_input = raw_ui
            unique_input = raw_ui
            curr_columns = unique_input.columns or []
            curr_strategy = unique_input.strategy or "any"

            strategy_dropdown = ft.Dropdown(
                label="Keep Strategy",
                options=[
                    ft.dropdown.Option("any", "Any (fastest)"),
                    ft.dropdown.Option("first", "First row"),
                    ft.dropdown.Option("last", "Last row"),
                    ft.dropdown.Option("none", "None (remove all duplicates)"),
                ],
                value=curr_strategy,
                height=44,
                text_size=13,
            )

            col_checkboxes = []
            for col in incoming_cols:
                cb = ft.Checkbox(
                    label=col,
                    value=(col in curr_columns),
                    label_style=ft.TextStyle(color=t.TEXT_PRIMARY, size=12),
                )
                col_checkboxes.append(cb)

            def save_unique_config(e):
                selected_cols = [cb.label for cb in col_checkboxes if cb.value]
                ui = UniqueInput(
                    columns=selected_cols if selected_cols else None,
                    strategy=strategy_dropdown.value or "any",
                )
                node.setting_input.unique_input = ui
                try:
                    self.flow_ref.add_unique(node.setting_input)
                    self.save_active_flow()
                    self.update_preview_ui()
                    self._snack("✓ Drop Duplicates configured", ft.Colors.GREEN_700)
                except Exception as ex:
                    self._snack(
                        f"Error saving Drop Duplicates: {ex}", ft.Colors.RED_700
                    )

            self.config_container.controls.append(
                ft.Column(
                    controls=[
                        strategy_dropdown,
                        ft.Text(
                            "Columns to consider (leave blank = all columns):",
                            color=ft.Colors.GREY_400,
                            size=12,
                        ),
                        ft.Column(
                            controls=col_checkboxes,
                            scroll=ft.ScrollMode.AUTO,
                            height=200,
                        ),
                        ft.ElevatedButton(
                            "Apply",
                            on_click=save_unique_config,
                            bgcolor=ft.Colors.BLUE_700,
                            color=ft.Colors.WHITE,
                        ),
                    ],
                    spacing=8,
                )
            )
        elif node.node_type == "pivot":
            from core.schemas.input_schema import NodePivot
            from core.schemas.transform_schema import PivotInput

            t = get_theme(self.main_page)
            setting = node.setting_input
            pivot_input = getattr(setting, "pivot_input", None) or PivotInput(
                index_columns=[], pivot_column="", value_col="", aggregations=[]
            )

            available_cols = []
            for c in incoming_cols or []:
                if isinstance(c, str):
                    available_cols.append(c)
                elif hasattr(c, "name"):
                    available_cols.append(c.name)

            existing_index_cols = pivot_input.index_columns or []
            existing_pivot_col = pivot_input.pivot_column or ""
            existing_val_col = pivot_input.value_col or ""
            existing_aggs = pivot_input.aggregations or []

            # Draggable Columns list
            columns_draggable_list = []
            for col in available_cols:
                col_type = "String"
                for c_obj in incoming_cols or []:
                    if hasattr(c_obj, "name") and c_obj.name == col:
                        col_type = c_obj.data_type or "String"
                        break
                    elif isinstance(c_obj, dict) and c_obj.get("name") == col:
                        col_type = c_obj.get("data_type") or "String"
                        break

                columns_draggable_list.append(
                    ft.Draggable(
                        group="pivot_fields",
                        content=ft.Container(
                            content=ft.Row(
                                [
                                    ft.Icon(
                                        ft.Icons.DRAG_INDICATOR_ROUNDED,
                                        size=14,
                                        color=ft.Colors.GREY_500,
                                    ),
                                    ft.Text(
                                        f"{col} ({col_type})",
                                        size=12,
                                        color=t.TEXT_PRIMARY,
                                    ),
                                ],
                                spacing=6,
                            ),
                            bgcolor=t.BG_CARD,
                            padding=ft.Padding(left=10, top=6, right=10, bottom=6),
                            border_radius=4,
                            border=ft.Border.all(1, t.BORDER),
                        ),
                        data=col,
                    )
                )

            columns_source_col = ft.Column(
                columns_draggable_list,
                spacing=6,
                scroll=ft.ScrollMode.AUTO,
                height=130,
            )

            dropped_index_keys = list(existing_index_cols)
            dropped_pivot_col = [existing_pivot_col]
            dropped_val_col = [existing_val_col]

            index_target_cols_row = ft.Row(spacing=6, wrap=True)

            def remove_index_col(col):
                if col in dropped_index_keys:
                    dropped_index_keys.remove(col)
                    update_drag_targets()

            def on_drop_index(e):
                col = e.data
                if col not in dropped_index_keys:
                    dropped_index_keys.append(col)
                    update_drag_targets()
                _drag_hover(index_target_container, False)

            index_target_container = ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "Index Keys",
                            size=11,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.GREY_400,
                        ),
                        index_target_cols_row,
                    ],
                    spacing=4,
                ),
                bgcolor=t.BG_CARD,
                padding=10,
                border_radius=6,
                border=ft.Border.all(1, t.BORDER),
                width=float("inf"),
            )

            def _drag_hover(container: ft.Container, entering: bool):
                # Visual feedback while a column is dragged over a drop
                # target -- previously there was none, so dropping felt
                # unresponsive/uncertain (client: "not dropping columns
                # seamlessly").
                container.border = ft.Border.all(
                    2 if entering else 1,
                    ft.Colors.BLUE_400 if entering else t.BORDER,
                )
                try:
                    container.update()
                except Exception:
                    pass

            index_drag_target = ft.DragTarget(
                group="pivot_fields",
                on_accept=on_drop_index,
                on_will_accept=lambda e: _drag_hover(index_target_container, True),
                on_leave=lambda e: _drag_hover(index_target_container, False),
                content=index_target_container,
            )

            pivot_target_col_row = ft.Row(spacing=6, wrap=True)

            def remove_pivot_col():
                dropped_pivot_col[0] = ""
                update_drag_targets()

            def on_drop_pivot(e):
                dropped_pivot_col[0] = e.data
                update_drag_targets()
                _drag_hover(pivot_target_container, False)

            pivot_target_container = ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "Pivot Column",
                            size=11,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.GREY_400,
                        ),
                        pivot_target_col_row,
                    ],
                    spacing=4,
                ),
                bgcolor=t.BG_CARD,
                padding=10,
                border_radius=6,
                border=ft.Border.all(1, t.BORDER),
                width=float("inf"),
            )

            pivot_drag_target = ft.DragTarget(
                group="pivot_fields",
                on_accept=on_drop_pivot,
                on_will_accept=lambda e: _drag_hover(pivot_target_container, True),
                on_leave=lambda e: _drag_hover(pivot_target_container, False),
                content=pivot_target_container,
            )

            value_target_col_row = ft.Row(spacing=6, wrap=True)

            def remove_value_col():
                dropped_val_col[0] = ""
                update_drag_targets()

            def on_drop_value(e):
                dropped_val_col[0] = e.data
                update_drag_targets()
                _drag_hover(value_target_container, False)

            value_target_container = ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "Value Column",
                            size=11,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.GREY_400,
                        ),
                        value_target_col_row,
                    ],
                    spacing=4,
                ),
                bgcolor=t.BG_CARD,
                padding=10,
                border_radius=6,
                border=ft.Border.all(1, t.BORDER),
                width=float("inf"),
            )

            value_drag_target = ft.DragTarget(
                group="pivot_fields",
                on_accept=on_drop_value,
                on_will_accept=lambda e: _drag_hover(value_target_container, True),
                on_leave=lambda e: _drag_hover(value_target_container, False),
                content=value_target_container,
            )

            def update_drag_targets():
                index_target_cols_row.controls.clear()
                if not dropped_index_keys:
                    index_target_cols_row.controls.append(
                        ft.Text(
                            "Drag Index Keys here",
                            size=11,
                            color=ft.Colors.GREY_500,
                            italic=True,
                        )
                    )
                else:
                    for c in dropped_index_keys:
                        index_target_cols_row.controls.append(
                            ft.Chip(
                                label=ft.Text(c, size=11),
                                on_click=lambda e, col=c: remove_index_col(col),
                                bgcolor=ft.Colors.BLUE_900,
                                leading=ft.Icon(
                                    ft.Icons.CLOSE_ROUNDED,
                                    size=12,
                                    color=ft.Colors.RED_300,
                                ),
                            )
                        )

                pivot_target_col_row.controls.clear()
                if not dropped_pivot_col[0]:
                    pivot_target_col_row.controls.append(
                        ft.Text(
                            "Drag Pivot Column here",
                            size=11,
                            color=ft.Colors.GREY_500,
                            italic=True,
                        )
                    )
                else:
                    pivot_target_col_row.controls.append(
                        ft.Chip(
                            label=ft.Text(dropped_pivot_col[0], size=11),
                            on_click=lambda e: remove_pivot_col(),
                            bgcolor=ft.Colors.ORANGE_900,
                            leading=ft.Icon(
                                ft.Icons.CLOSE_ROUNDED, size=12, color=ft.Colors.RED_300
                            ),
                        )
                    )

                value_target_col_row.controls.clear()
                if not dropped_val_col[0]:
                    value_target_col_row.controls.append(
                        ft.Text(
                            "Drag Value Column here",
                            size=11,
                            color=ft.Colors.GREY_500,
                            italic=True,
                        )
                    )
                else:
                    value_target_col_row.controls.append(
                        ft.Chip(
                            label=ft.Text(dropped_val_col[0], size=11),
                            on_click=lambda e: remove_value_col(),
                            bgcolor=ft.Colors.GREEN_900,
                            leading=ft.Icon(
                                ft.Icons.CLOSE_ROUNDED, size=12, color=ft.Colors.RED_300
                            ),
                        )
                    )
                try:
                    index_target_cols_row.update()
                    pivot_target_col_row.update()
                    value_target_col_row.update()
                except Exception:
                    pass

            agg_funcs = ["count", "sum", "min", "max", "mean", "first", "last"]
            agg_checks = []
            for func in agg_funcs:
                cb = ft.Checkbox(
                    label=func.upper(),
                    value=(func in existing_aggs),
                    label_style=ft.TextStyle(color=t.TEXT_PRIMARY, size=11),
                )
                agg_checks.append(cb)
            agg_checks_row = ft.Row(agg_checks, wrap=True, spacing=10)

            def save_pivot_config(e):
                sel_aggs = [cb.label.lower() for cb in agg_checks if cb.value]

                if not dropped_pivot_col[0]:
                    self._snack(
                        "⚠ Please select/drag a Pivot Column.", ft.Colors.AMBER_700
                    )
                    return
                if not dropped_val_col[0]:
                    self._snack(
                        "⚠ Please select/drag a Value Column.", ft.Colors.AMBER_700
                    )
                    return
                if not sel_aggs:
                    self._snack(
                        "⚠ Please select at least one aggregation method.",
                        ft.Colors.AMBER_700,
                    )
                    return

                depending_id = None
                try:
                    main_inputs = node.node_inputs.main_inputs
                    if main_inputs:
                        depending_id = main_inputs[0].node_id
                except Exception:
                    pass
                if depending_id is None:
                    depending_id = getattr(node.setting_input, "depending_on_id", None)

                pi = PivotInput(
                    index_columns=dropped_index_keys,
                    pivot_column=dropped_pivot_col[0],
                    value_col=dropped_val_col[0],
                    aggregations=sel_aggs,
                )

                new_settings = NodePivot(
                    flow_id=getattr(node.setting_input, "flow_id", None)
                    or self.active_flow_id,
                    node_id=node.node_id,
                    depending_on_id=depending_id,
                    pivot_input=pi,
                    cache_results=getattr(node.setting_input, "cache_results", False),
                    pos_x=getattr(node.setting_input, "pos_x", 0.0),
                    pos_y=getattr(node.setting_input, "pos_y", 0.0),
                    description=getattr(node.setting_input, "description", ""),
                    node_reference=getattr(node.setting_input, "node_reference", None),
                    user_id=getattr(node.setting_input, "user_id", None),
                    is_flow_output=getattr(node.setting_input, "is_flow_output", False),
                )
                try:
                    self.flow_ref.add_pivot(new_settings)
                    self.save_active_flow()
                    self._snack("✓ Pivot configuration saved!", ft.Colors.GREEN_700)
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Apply",
                on_click=save_pivot_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            # Main Settings Content Columns
            main_settings_tab_content = ft.Column(
                [
                    columns_source_col,
                    ft.Container(height=4),
                    index_drag_target,
                    pivot_drag_target,
                    value_drag_target,
                    ft.Container(height=4),
                    ft.Text(
                        "Select aggregations",
                        size=11,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.GREY_400,
                    ),
                    agg_checks_row,
                    ft.Container(height=6),
                    save_btn,
                ],
                spacing=6,
                expand=True,
            )

            general_settings_tab_content = ft.Column(
                [
                    ft.Text("Step Description", weight=ft.FontWeight.BOLD, size=13),
                    ft.TextField(
                        label="Enter description...",
                        value=getattr(node.setting_input, "description", "") or "",
                        multiline=True,
                        min_lines=3,
                        max_lines=3,
                        text_size=12,
                    ),
                    ft.Switch(
                        label="Cache execution results",
                        value=getattr(node.setting_input, "cache_results", False),
                    ),
                ],
                spacing=12,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            )

            schema_rows = []
            for col in available_cols:
                schema_rows.append(
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.TAG_ROUNDED, size=14, color=ft.Colors.BLUE_300
                            ),
                            ft.Text(
                                col, size=12, weight=ft.FontWeight.W_500, expand=True
                            ),
                            ft.Text("Auto", size=11, color=ft.Colors.GREY_400),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    )
                )

            schema_validator_tab_content = ft.Column(
                [
                    ft.Text(
                        "Predicted Input Schema", weight=ft.FontWeight.BOLD, size=13
                    ),
                    ft.Divider(height=1, color=t.BORDER),
                    ft.Column(
                        schema_rows, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True
                    ),
                ],
                spacing=12,
                expand=True,
            )

            # Tab Control matching Flet's TabBarView pattern
            tabs_root = ft.Tabs(
                length=3,
                height=460,  # fixed height, not expand=True (see Formula node comment below) -- avoids conflicting with parent config_container's scroll=AUTO
                content=ft.Column(
                    expand=True,
                    controls=[
                        ft.TabBar(
                            tabs=[
                                ft.Tab(label="Main Settings"),
                                ft.Tab(label="General Settings"),
                                ft.Tab(label="Schema Validator"),
                            ]
                        ),
                        ft.TabBarView(
                            expand=True,
                            controls=[
                                main_settings_tab_content,
                                general_settings_tab_content,
                                schema_validator_tab_content,
                            ],
                        ),
                    ],
                ),
            )

            self.config_container.controls.append(tabs_root)
            update_drag_targets()

        elif node.node_type == "window":
            from core.schemas.input_schema import NodeWindow
            from core.schemas.transform_schema import WindowInput

            t = get_theme(self.main_page)
            setting = node.setting_input
            window_input = getattr(setting, "window_input", None) or WindowInput(
                output_column="window_out",
                value_column="",
                function="sum",
                partition_by=[],
                order_by=None,
                descending=False,
            )

            available_cols = []
            for c in incoming_cols or []:
                if isinstance(c, str):
                    available_cols.append(c)
                elif hasattr(c, "name"):
                    available_cols.append(c.name)

            out_col_tf = ft.TextField(
                label="Output Column",
                value=window_input.output_column or "window_out",
                height=44,
                text_size=13,
            )

            value_col_dd = ft.Dropdown(
                label="Value Column",
                options=[ft.dropdown.Option(c) for c in available_cols],
                value=window_input.value_column
                or (available_cols[0] if available_cols else None),
                height=44,
                text_size=13,
            )

            func_dd = ft.Dropdown(
                label="Function",
                options=[
                    ft.dropdown.Option("sum", "SUM"),
                    ft.dropdown.Option("mean", "MEAN"),
                    ft.dropdown.Option("min", "MIN"),
                    ft.dropdown.Option("max", "MAX"),
                    ft.dropdown.Option("count", "COUNT"),
                    ft.dropdown.Option("rank", "RANK"),
                    ft.dropdown.Option("dense_rank", "DENSE_RANK"),
                    ft.dropdown.Option("row_number", "ROW_NUMBER"),
                    ft.dropdown.Option("lead", "LEAD"),
                    ft.dropdown.Option("lag", "LAG"),
                ],
                value=window_input.function or "sum",
                height=44,
                text_size=13,
            )

            partition_header = ft.Text(
                "Partition By Columns",
                size=13,
                weight=ft.FontWeight.W_600,
                color=ft.Colors.BLUE_300,
            )
            partition_checks = []
            existing_partitions = window_input.partition_by or []
            for col in available_cols:
                cb = ft.Checkbox(
                    label=col,
                    value=(col in existing_partitions),
                    label_style=ft.TextStyle(color=t.TEXT_PRIMARY, size=12),
                )
                partition_checks.append(cb)
            partition_col = ft.Column(
                partition_checks, spacing=4, scroll=ft.ScrollMode.AUTO, height=120
            )

            order_by_dd = ft.Dropdown(
                label="Order By Column (Optional)",
                options=[ft.dropdown.Option("")]
                + [ft.dropdown.Option(c) for c in available_cols],
                value=window_input.order_by or "",
                height=44,
                text_size=13,
            )

            desc_switch = ft.Switch(
                label="Descending",
                value=window_input.descending,
            )

            def save_window_config(e):
                sel_partitions = [cb.label for cb in partition_checks if cb.value]
                out_name = out_col_tf.value.strip()
                if not out_name:
                    self.show_dialog(
                        "Validation Error", "Output column name cannot be empty."
                    )
                    return
                if not value_col_dd.value and func_dd.value != "row_number":
                    self.show_dialog(
                        "Validation Error", "Please select a Value Column."
                    )
                    return

                depending_id = None
                try:
                    main_inputs = node.node_inputs.main_inputs
                    if main_inputs:
                        depending_id = main_inputs[0].node_id
                except Exception:
                    pass
                if depending_id is None:
                    depending_id = getattr(node.setting_input, "depending_on_id", None)

                wi = WindowInput(
                    output_column=out_name,
                    value_column=value_col_dd.value or "",
                    function=func_dd.value,
                    partition_by=sel_partitions,
                    order_by=order_by_dd.value or None,
                    descending=desc_switch.value,
                )

                new_settings = NodeWindow(
                    flow_id=getattr(node.setting_input, "flow_id", None)
                    or self.active_flow_id,
                    node_id=node.node_id,
                    depending_on_id=depending_id,
                    window_input=wi,
                    cache_results=getattr(node.setting_input, "cache_results", False),
                    pos_x=getattr(node.setting_input, "pos_x", 0.0),
                    pos_y=getattr(node.setting_input, "pos_y", 0.0),
                    description=getattr(node.setting_input, "description", ""),
                    node_reference=getattr(node.setting_input, "node_reference", None),
                    user_id=getattr(node.setting_input, "user_id", None),
                    is_flow_output=getattr(node.setting_input, "is_flow_output", False),
                )
                try:
                    self.flow_ref.add_window(new_settings)
                    self.save_active_flow()
                    self.show_dialog("Success", "Window function configuration saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Window Settings",
                on_click=save_window_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend(
                [
                    # Small top spacer so the "Output Column" floating label
                    # doesn't visually collide with the dialog title above it.
                    ft.Container(height=10),
                    out_col_tf,
                    value_col_dd,
                    func_dd,
                    ft.Container(height=4),
                    partition_header,
                    partition_col,
                    ft.Container(height=4),
                    order_by_dd,
                    desc_switch,
                    ft.Container(height=8),
                    save_btn,
                ]
            )

        elif node.node_type == "manual_input":
            self._build_manual_input_ui(node)
        elif node.node_type == "database_reader":
            from core.database.connection import get_db_context
            from core.dataryx.database_connection_manager.db_connections import (
                get_all_database_connections_interface,
            )
            from core.schemas.input_schema import DatabaseSettings

            t = get_theme(self.main_page)
            user_id = (
                auth_service.user_info.get("id", 1) if auth_service.user_info else 1
            )
            with get_db_context() as db:
                saved_conns = get_all_database_connections_interface(db, user_id)

            conn_options = [ft.dropdown.Option(c.connection_name) for c in saved_conns]

            # Get current settings
            setting = node.setting_input
            ds = getattr(setting, "database_settings", None)

            curr_conn = getattr(ds, "database_connection_name", None) if ds else None
            curr_query_mode = getattr(ds, "query_mode", "table") if ds else "table"
            curr_schema = getattr(ds, "schema_name", "") or ""
            curr_table = getattr(ds, "table_name", "") or ""
            curr_query = getattr(ds, "query", "") or ""

            conn_dropdown = ft.Dropdown(
                label="Database Connection",
                options=conn_options,
                value=curr_conn,
                height=44,
                text_size=13,
            )

            schema_input = ft.TextField(
                label="Schema Name (Optional)",
                value=curr_schema,
                height=44,
                text_size=13,
            )

            table_input = ft.TextField(
                label="Table Name",
                value=curr_table,
                height=44,
                text_size=13,
                visible=(curr_query_mode == "table"),
            )

            # Styled to match the Formula/Polars Code editors (client-requested:
            # "make it look like the formula typing code space") -- Read mode
            # only, since Write mode has no query field.
            query_input = ft.TextField(
                value=curr_query,
                multiline=True,
                min_lines=6,
                expand=True,
                text_size=12,
                text_style=ft.TextStyle(
                    font_family="Courier New", color=t.TEXT_PRIMARY
                ),
                border=ft.InputBorder.NONE,
                bgcolor=t.BG_CARD,
            )
            query_line_numbers_col = ft.Column(
                [
                    ft.Container(
                        content=ft.Text(
                            str(i),
                            size=11,
                            color=ft.Colors.GREY_500,
                            font_family="Courier New",
                        ),
                        height=18,
                        alignment=ft.alignment.Alignment(1, 0),
                    )
                    for i in range(1, 7)
                ],
                spacing=0,
            )
            query_editor_container = ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "SQL Query", size=12, color=t.TEXT_SECONDARY
                        ),
                        ft.Container(
                            content=ft.Row(
                                [
                                    ft.Container(
                                        content=query_line_numbers_col,
                                        padding=ft.Padding(top=10, right=4),
                                        alignment=ft.alignment.Alignment(1, -1),
                                    ),
                                    ft.Container(content=query_input, expand=True),
                                ],
                                spacing=4,
                                expand=True,
                            ),
                            border=ft.Border.all(1, t.BORDER),
                            border_radius=6,
                            bgcolor=t.BG_CARD,
                            padding=4,
                        ),
                    ],
                    spacing=4,
                ),
                visible=(curr_query_mode == "query"),
            )

            def on_mode_change(e):
                val = e.control.value
                table_input.visible = val == "table"
                query_editor_container.visible = val == "query"
                table_input.update()
                query_editor_container.update()
                self.config_container.update()

            query_mode_dropdown = ft.Dropdown(
                label="Read Mode",
                options=[ft.dropdown.Option("table"), ft.dropdown.Option("query")],
                value=curr_query_mode,
                height=44,
                text_size=13,
                on_select=on_mode_change,
            )

            def save_db_reader_config(e):
                conn_name = conn_dropdown.value
                if not conn_name:
                    self.show_dialog("Error", "Please select a database connection.")
                    return

                q_mode = query_mode_dropdown.value
                sch_name = schema_input.value.strip() or None
                tbl_name = table_input.value.strip() or None
                sql_q = query_input.value.strip() or None

                if q_mode == "table" and not tbl_name:
                    self.show_dialog("Error", "Table Name is required in table mode.")
                    return
                if q_mode == "query" and not sql_q:
                    self.show_dialog("Error", "SQL Query is required in query mode.")
                    return

                db_settings = DatabaseSettings(
                    connection_mode="reference",
                    database_connection=None,
                    database_connection_name=conn_name,
                    schema_name=sch_name,
                    table_name=tbl_name if q_mode == "table" else None,
                    query=sql_q if q_mode == "query" else None,
                    query_mode=q_mode,
                )

                node.setting_input.database_settings = db_settings
                if node.setting_input.user_id is None:
                    node.setting_input.user_id = user_id

                try:
                    self.flow_ref.add_database_reader(node.setting_input)
                    self.show_dialog(
                        "✓ Saved", "Database Reader settings saved successfully!"
                    )
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Settings",
                on_click=save_db_reader_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend(
                [
                    conn_dropdown,
                    query_mode_dropdown,
                    schema_input,
                    table_input,
                    query_editor_container,
                    save_btn,
                ]
            )

        elif node.node_type == "database_writer":
            from core.database.connection import get_db_context
            from core.dataryx.database_connection_manager.db_connections import (
                get_all_database_connections_interface,
            )
            from core.schemas.input_schema import DatabaseWriteSettings

            user_id = (
                auth_service.user_info.get("id", 1) if auth_service.user_info else 1
            )
            with get_db_context() as db:
                saved_conns = get_all_database_connections_interface(db, user_id)

            conn_options = [ft.dropdown.Option(c.connection_name) for c in saved_conns]

            # Get current settings
            setting = node.setting_input
            dws = getattr(setting, "database_write_settings", None)

            curr_conn = getattr(dws, "database_connection_name", None) if dws else None
            curr_schema = getattr(dws, "schema_name", "") or ""
            curr_table = getattr(dws, "table_name", "") or ""
            curr_if_exists = getattr(dws, "if_exists", "append") if dws else "append"

            conn_dropdown = ft.Dropdown(
                label="Database Connection",
                options=conn_options,
                value=curr_conn,
                height=44,
                text_size=13,
            )

            schema_input = ft.TextField(
                label="Schema Name (Optional)",
                value=curr_schema,
                height=44,
                text_size=13,
            )

            table_input = ft.TextField(
                label="Table Name",
                value=curr_table,
                height=44,
                text_size=13,
            )

            if_exists_dropdown = ft.Dropdown(
                label="If Table Exists",
                options=[
                    ft.dropdown.Option("append"),
                    ft.dropdown.Option("replace"),
                    ft.dropdown.Option("fail"),
                ],
                value=curr_if_exists,
                height=44,
                text_size=13,
            )

            def save_db_writer_config(e):
                conn_name = conn_dropdown.value
                if not conn_name:
                    self.show_dialog("Error", "Please select a database connection.")
                    return

                tbl_name = table_input.value.strip()
                if not tbl_name:
                    self.show_dialog("Error", "Table Name is required.")
                    return

                sch_name = schema_input.value.strip() or None
                if_ex = if_exists_dropdown.value

                db_write_settings = DatabaseWriteSettings(
                    connection_mode="reference",
                    database_connection=None,
                    database_connection_name=conn_name,
                    schema_name=sch_name,
                    table_name=tbl_name,
                    if_exists=if_ex,
                )

                node.setting_input.database_write_settings = db_write_settings
                if node.setting_input.user_id is None:
                    node.setting_input.user_id = user_id

                try:
                    self.flow_ref.add_database_writer(node.setting_input)
                    self.show_dialog(
                        "✓ Saved", "Database Writer settings saved successfully!"
                    )
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Settings",
                on_click=save_db_writer_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend(
                [
                    conn_dropdown,
                    schema_input,
                    table_input,
                    if_exists_dropdown,
                    save_btn,
                ]
            )

        elif node.node_type in ["read", "read_csv"]:
            setting = node.setting_input
            rf = getattr(setting, "received_file", None)
            file_path = getattr(rf, "path", "") if rf else ""
            file_type = getattr(rf, "file_type", "csv") if rf else "csv"

            # Extract delimiter/sheet name
            delimiter = ","
            sheet_name = ""
            has_headers = True

            if rf and rf.table_settings:
                delimiter = getattr(rf.table_settings, "delimiter", ",")
                sheet_name = getattr(rf.table_settings, "sheet_name", "") or ""
                has_headers = getattr(rf.table_settings, "has_headers", True)

            path_input = ft.TextField(
                label="File Path",
                value=file_path,
                height=44,
                text_size=13,
                expand=True,
                hint_text="Click Browse 📁 or paste path here...",
                read_only=False,
            )

            def _get_excel_sheet_names(path: str) -> list[str]:
                """Reads actual sheet names from an .xlsx/.xls file. Returns
                [] on any failure (missing file, not really excel, etc.) so
                callers can fall back to free-text entry instead of crashing."""
                import os

                if not path or not os.path.isfile(path):
                    return []
                try:
                    import fastexcel

                    return list(fastexcel.read_excel(path).sheet_names)
                except Exception:
                    return []

            def refresh_sheet_options(path: str):
                names = _get_excel_sheet_names(path)
                current = sheet_input.value
                options = [ft.dropdown.Option(n) for n in names]
                # Keep a previously-saved sheet name selectable even if it's
                # not in the freshly-read list (e.g. stale/renamed sheet) so
                # switching files never silently discards existing config.
                if current and current not in names:
                    options.append(ft.dropdown.Option(current))
                sheet_input.options = options
                try:
                    sheet_input.update()
                except Exception:
                    pass

            def open_picker(e):
                ext_map = {
                    "csv": ["csv"],
                    "excel": ["xlsx", "xls"],
                    "parquet": ["parquet"],
                    "json": ["json"],
                }
                allowed = ext_map.get(
                    type_dropdown.value or "csv", ["csv", "xlsx", "parquet", "json"]
                )

                async def _pick():
                    files = await self._file_picker.pick_files(
                        dialog_title="Select Data File",
                        file_type=ft.FilePickerFileType.CUSTOM,
                        allowed_extensions=allowed,
                        allow_multiple=False,
                    )
                    if files:
                        path_input.value = files[0].path
                        path_input.error_text = None
                        path_input.update()
                        if type_dropdown.value == "excel":
                            refresh_sheet_options(files[0].path)

                self.main_page.run_task(_pick)

            browse_btn = ft.IconButton(
                icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                icon_color=ft.Colors.BLUE_400,
                tooltip="Browse for file",
                on_click=open_picker,
            )

            path_row = ft.Row(
                [path_input, browse_btn],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

            delim_input = ft.TextField(
                label="Delimiter (CSV)",
                value=delimiter,
                height=44,
                text_size=13,
                visible=(file_type == "csv"),
            )
            # Editable dropdown ("combobox"): shows real sheet names read
            # from the file when available, but still allows typing a name
            # manually if detection fails or the file isn't picked yet --
            # nothing is lost compared to the old plain text field.
            sheet_input = ft.Dropdown(
                label="Sheet Name (Excel)",
                editable=True,
                options=[ft.dropdown.Option(n) for n in _get_excel_sheet_names(file_path)]
                if file_type == "excel"
                else [],
                value=sheet_name or None,
                height=44,
                text_size=13,
                visible=(file_type == "excel"),
            )
            header_switch = ft.Switch(
                label="Has Column Headers",
                value=has_headers,
                visible=(file_type in ["csv", "excel"]),
            )

            def on_type_change(e):
                val = e.control.value
                delim_input.visible = val == "csv"
                sheet_input.visible = val == "excel"
                header_switch.visible = val in ["csv", "excel"]
                delim_input.update()
                sheet_input.update()
                header_switch.update()
                if val == "excel" and not sheet_input.options:
                    refresh_sheet_options(path_input.value)
                self.config_container.update()

            type_dropdown = ft.Dropdown(
                label="File Format",
                options=[
                    ft.dropdown.Option("csv"),
                    ft.dropdown.Option("excel"),
                    ft.dropdown.Option("parquet"),
                    ft.dropdown.Option("json"),
                ],
                value=file_type,
                height=44,
                text_size=13,
                on_select=on_type_change,
            )

            def save_read_config(e):
                import os
                from core.schemas.input_schema import (
                    ReceivedTable,
                    InputCsvTable,
                    InputExcelTable,
                    InputParquetTable,
                    InputJsonTable,
                )

                raw_path = path_input.value.strip()

                # ── Validate path ─────────────────────────────────
                if not raw_path:
                    path_input.error_text = (
                        "File path is required — use Browse 📁 to select a file"
                    )
                    path_input.update()
                    return
                if not os.path.isfile(raw_path):
                    path_input.error_text = f"File not found: {raw_path}"
                    path_input.update()
                    return
                path_input.error_text = None  # clear any previous error
                path_input.update()

                fmt = type_dropdown.value.lower()
                if fmt == "csv":
                    ts = InputCsvTable(
                        delimiter=delim_input.value or ",",
                        has_headers=header_switch.value,
                    )
                elif fmt == "excel":
                    ts = InputExcelTable(
                        sheet_name=sheet_input.value or None,
                        has_headers=header_switch.value,
                    )
                elif fmt == "parquet":
                    ts = InputParquetTable()
                else:
                    ts = InputJsonTable()

                node.setting_input.received_file = ReceivedTable(
                    path=raw_path, file_type=fmt, table_settings=ts
                )
                try:
                    self.flow_ref.add_read(node.setting_input)
                    self.show_dialog(
                        "✓ Saved", f"File configured:\n{os.path.basename(raw_path)}"
                    )
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Settings",
                on_click=save_read_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend(
                [
                    path_row,
                    type_dropdown,
                    delim_input,
                    sheet_input,
                    header_switch,
                    save_btn,
                ]
            )

        elif node.node_type == "group_by":
            from core.schemas.transform_schema import GroupByInput, AggColl
            from core.schemas.input_schema import NodeGroupBy

            t = get_theme(self.main_page)
            # ── Current settings ─────────────────────────────────
            gi = getattr(node.setting_input, "groupby_input", None)
            existing_agg_cols = gi.agg_cols if gi else []
            existing_groupby_cols = {
                c.old_name for c in existing_agg_cols if c.agg == "groupby"
            }
            existing_agg_non_group = [
                c for c in existing_agg_cols if c.agg != "groupby"
            ]

            # Columns from upstream — handle str or object-with-.name
            available_cols = []
            for c in incoming_cols or []:
                if isinstance(c, str):
                    available_cols.append(c)
                elif hasattr(c, "name"):
                    available_cols.append(c.name)

            AGG_FUNCS = [
                "sum",
                "mean",
                "count",
                "min",
                "max",
                "first",
                "last",
                "n_unique",
            ]

            # ── Group By columns (checkboxes) ─────────────────────
            groupby_header = ft.Text(
                "Group By Columns",
                size=13,
                weight=ft.FontWeight.W_600,
                color=ft.Colors.BLUE_300,
            )

            groupby_checks = []
            for col in available_cols:
                cb = ft.Checkbox(
                    label=col,
                    value=(col in existing_groupby_cols),
                    label_style=ft.TextStyle(color=t.TEXT_PRIMARY, size=13),
                )
                groupby_checks.append(cb)

            groupby_col_list = ft.Column(groupby_checks, spacing=4)

            # ── Aggregate columns (dynamic rows) ──────────────────
            agg_header = ft.Text(
                "Aggregate Columns",
                size=13,
                weight=ft.FontWeight.W_600,
                color=ft.Colors.ORANGE_300,
            )
            agg_rows_col = ft.Column([], spacing=6)

            def make_agg_row(col_name="", agg_func="sum", out_name=""):
                col_dd = ft.Dropdown(
                    options=[ft.dropdown.Option(c) for c in available_cols],
                    value=col_name or (available_cols[0] if available_cols else None),
                    height=40,
                    text_size=12,
                    expand=2,
                )
                func_dd = ft.Dropdown(
                    options=[ft.dropdown.Option(f) for f in AGG_FUNCS],
                    value=agg_func,
                    height=40,
                    text_size=12,
                    expand=2,
                )
                out_tf = ft.TextField(
                    hint_text="Output name (optional)",
                    value=out_name,
                    height=40,
                    text_size=12,
                    expand=2,
                )
                del_btn = ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    icon_color=ft.Colors.RED_400,
                    icon_size=16,
                    on_click=lambda _, row=None: _remove_agg_row(row),
                )
                row = ft.Row(
                    [col_dd, func_dd, out_tf, del_btn],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
                del_btn.on_click = lambda _, r=row: _remove_agg_row(r)
                return row, col_dd, func_dd, out_tf

            def _remove_agg_row(row):
                if row in agg_rows_col.controls:
                    agg_rows_col.controls.remove(row)
                    agg_rows_col.update()

            # Pre-fill existing agg cols
            for ac in existing_agg_non_group:
                row, _, _, _ = make_agg_row(ac.old_name, ac.agg, ac.new_name or "")
                agg_rows_col.controls.append(row)

            def add_agg_row(e):
                row, _, _, _ = make_agg_row()
                agg_rows_col.controls.append(row)
                agg_rows_col.update()

            add_agg_btn = ft.TextButton(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.ADD_ROUNDED, size=14),
                        ft.Text("Add Aggregation", size=12),
                    ],
                    spacing=4,
                ),
                on_click=add_agg_row,
            )

            # ── Save ─────────────────────────────────────────────
            def save_groupby(e):
                agg_cols = []
                # Group-by columns
                for cb in groupby_checks:
                    if cb.value:
                        agg_cols.append(AggColl(old_name=cb.label, agg="groupby"))
                if not agg_cols:
                    self.show_dialog(
                        "Validation", "Please select at least one Group By column."
                    )
                    return
                # Aggregate columns
                for row in agg_rows_col.controls:
                    controls = row.controls  # [col_dd, func_dd, out_tf, del_btn]
                    col_val = controls[0].value
                    func_val = controls[1].value
                    out_val = controls[2].value.strip() or None
                    if col_val and func_val:
                        agg_cols.append(
                            AggColl(old_name=col_val, agg=func_val, new_name=out_val)
                        )

                # ── Build a proper NodeGroupBy (setting_input may be NodePromise) ──
                # Get depending_on_id from node's upstream connection
                depending_id = None
                try:
                    main_inputs = node.node_inputs.main_inputs
                    if main_inputs:
                        depending_id = main_inputs[0].node_id
                except Exception:
                    pass
                # Fallback: try existing setting_input
                if depending_id is None:
                    depending_id = getattr(node.setting_input, "depending_on_id", None)

                new_settings = NodeGroupBy(
                    flow_id=getattr(node.setting_input, "flow_id", None)
                    or self.active_flow_id,
                    node_id=node.node_id,
                    depending_on_id=depending_id,
                    groupby_input=GroupByInput(agg_cols=agg_cols),
                    cache_results=getattr(node.setting_input, "cache_results", False),
                    pos_x=getattr(node.setting_input, "pos_x", 0.0),
                    pos_y=getattr(node.setting_input, "pos_y", 0.0),
                    description=getattr(node.setting_input, "description", ""),
                    node_reference=getattr(node.setting_input, "node_reference", None),
                    user_id=getattr(node.setting_input, "user_id", None),
                    is_flow_output=getattr(node.setting_input, "is_flow_output", False),
                    is_user_defined=getattr(
                        node.setting_input, "is_user_defined", False
                    ),
                    output_field_config=getattr(
                        node.setting_input, "output_field_config", None
                    ),
                )
                try:
                    self.flow_ref.add_group_by(new_settings)
                    self.save_active_flow()
                    self.show_dialog("✓ Saved", "Group By settings saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error", str(ex))

            save_btn = ft.Button(
                "Save Settings",
                on_click=save_groupby,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend(
                [
                    groupby_header,
                    (
                        groupby_col_list
                        if available_cols
                        else ft.Text(
                            "No columns (run upstream node first)",
                            color=ft.Colors.GREY_500,
                            size=12,
                        )
                    ),
                    ft.Divider(color=ft.Colors.GREY_800, height=12),
                    agg_header,
                    ft.Row(
                        [
                            ft.Text(
                                "Column", size=11, color=ft.Colors.GREY_500, expand=2
                            ),
                            ft.Text(
                                "Function", size=11, color=ft.Colors.GREY_500, expand=2
                            ),
                            ft.Text(
                                "Output Name",
                                size=11,
                                color=ft.Colors.GREY_500,
                                expand=2,
                            ),
                            ft.Container(width=36),
                        ],
                        spacing=4,
                    ),
                    agg_rows_col,
                    add_agg_btn,
                    ft.Divider(color=ft.Colors.GREY_800, height=8),
                    save_btn,
                ]
            )

        elif node.node_type == "filter":

            # Load basic or advanced filter settings
            mode = "basic"
            field = ""
            operator = "equals"
            val = ""
            val2 = ""
            expr = ""

            fi = getattr(node.setting_input, "filter_input", None)
            if fi:
                mode = getattr(fi, "mode", "basic")
                expr = getattr(fi, "advanced_filter", "")
                if fi.basic_filter:
                    field = getattr(fi.basic_filter, "field", "")
                    op_obj = getattr(fi.basic_filter, "operator", "equals")
                    operator = (
                        op_obj.to_symbol()
                        if hasattr(op_obj, "to_symbol")
                        else str(op_obj)
                    )
                    val = getattr(fi.basic_filter, "value", "")
                    val2 = getattr(fi.basic_filter, "value2", "") or ""

            # Manual tab switcher (ft.Tab API changes across Flet versions)
            mode_state = {"current": mode}  # "basic" or "advanced"

            basic_tab_btn = ft.TextButton(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.FILTER_ALT_ROUNDED, size=14),
                        ft.Text("Basic Filter", size=12),
                    ],
                    spacing=4,
                ),
                style=ft.ButtonStyle(
                    color=ft.Colors.BLUE_400 if mode == "basic" else ft.Colors.GREY_500
                ),
            )
            adv_tab_btn = ft.TextButton(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.CODE_ROUNDED, size=14),
                        ft.Text("Advanced Expression", size=12),
                    ],
                    spacing=4,
                ),
                style=ft.ButtonStyle(
                    color=(
                        ft.Colors.BLUE_400 if mode == "advanced" else ft.Colors.GREY_500
                    )
                ),
            )
            tab_row = ft.Row([basic_tab_btn, adv_tab_btn], spacing=4)

            col_dropdown = ft.Dropdown(
                label="Target Column",
                options=[ft.dropdown.Option(c) for c in incoming_cols],
                value=field,
                height=44,
                text_size=13,
            )
            val2_input = ft.TextField(
                label="Upper Bound Value (Between)",
                value=val2,
                height=44,
                text_size=13,
                visible=(operator == "between"),
            )

            def on_op_change(e):
                val2_input.visible = e.control.value == "between"
                val2_input.update()
                self.config_container.update()

            op_dropdown = ft.Dropdown(
                label="Operator",
                options=[
                    ft.dropdown.Option("="),
                    ft.dropdown.Option("!="),
                    ft.dropdown.Option(">"),
                    ft.dropdown.Option(">="),
                    ft.dropdown.Option("<"),
                    ft.dropdown.Option("<="),
                    ft.dropdown.Option("contains"),
                    ft.dropdown.Option("not_contains"),
                    ft.dropdown.Option("starts_with"),
                    ft.dropdown.Option("ends_with"),
                    ft.dropdown.Option("is_null"),
                    ft.dropdown.Option("is_not_null"),
                    ft.dropdown.Option("in"),
                    ft.dropdown.Option("not_in"),
                    ft.dropdown.Option("between"),
                ],
                value=operator,
                height=44,
                text_size=13,
                on_select=on_op_change,
            )
            val_input = ft.TextField(
                label="Filter Value", value=val, height=44, text_size=13
            )
            t = get_theme(self.main_page)

            expr_input = ft.TextField(
                value=expr,
                multiline=True,
                min_lines=10,
                max_lines=10,
                text_size=12,
                text_style=ft.TextStyle(
                    font_family="Courier New", color=t.TEXT_PRIMARY
                ),
                expand=True,
                border=ft.InputBorder.NONE,
                bgcolor=t.BG_CARD,
            )

            def insert_filter_text(text):
                expr_input.value = (expr_input.value or "") + text
                expr_input.update()

            # Function sidebar for the advanced expression box -- same
            # catalog and collapse-by-default pattern as the Formula node's
            # editor (client-requested: "copy the formula option"). Filter's
            # advanced_filter runs through the exact same polars_expr_transformer
            # engine as Formula, so every one of these functions is valid here.
            filter_functions_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=260)

            filter_doc_lookup = _get_expression_doc_lookup()

            def update_filter_functions_list(filter_text=""):
                filter_functions_col.controls.clear()
                filter_text = filter_text.lower()
                for cat, funcs in self.FUNCTIONS_BY_CATEGORY.items():
                    filtered = [
                        f
                        for f in funcs
                        if filter_text in f[0].lower() or filter_text in f[1].lower()
                    ]
                    if not filtered:
                        continue
                    category_tile_controls = [
                        ft.Container(
                            content=ft.Text(
                                name, size=11, weight=ft.FontWeight.W_500, color=ft.Colors.BLUE_400
                            ),
                            padding=ft.Padding(left=10, top=2, right=10, bottom=2),
                            on_click=lambda e, temp=template: insert_filter_text(temp),
                            tooltip=filter_doc_lookup.get(
                                name.lower().replace(" ", "_"), template
                            ),
                        )
                        for name, template in filtered
                    ]
                    filter_functions_col.controls.append(
                        ft.ExpansionTile(
                            title=ft.Text(cat, size=12, weight=ft.FontWeight.BOLD),
                            controls=category_tile_controls,
                            expanded=bool(filter_text),
                        )
                    )
                try:
                    filter_functions_col.update()
                except Exception:
                    pass

            filter_search_input = ft.TextField(
                hint_text="Filter functions...",
                height=32,
                text_size=11,
                content_padding=5,
                on_change=lambda e: update_filter_functions_list(e.control.value),
            )
            update_filter_functions_list()

            filter_func_sidebar = ft.Container(
                content=ft.Column(
                    [filter_search_input, filter_functions_col], spacing=6
                ),
                width=180,
                border=ft.Border(right=ft.border.BorderSide(1, t.BORDER)),
                padding=ft.Padding(right=8, top=0, left=0, bottom=0),
            )

            # Line-numbers gutter, same pattern as Formula/Polars Code.
            filter_line_numbers_col = ft.Column(
                [
                    ft.Container(
                        content=ft.Text(
                            str(i), size=11, color=ft.Colors.GREY_500, font_family="Courier New"
                        ),
                        height=18,
                        alignment=ft.alignment.Alignment(1, 0),
                    )
                    for i in range(1, 11)
                ],
                spacing=0,
            )
            filter_editor_container = ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            content=filter_line_numbers_col,
                            padding=ft.Padding(top=10, right=4),
                            alignment=ft.alignment.Alignment(1, -1),
                        ),
                        ft.Container(content=expr_input, expand=True),
                    ],
                    spacing=4,
                    expand=True,
                ),
                border=ft.Border.all(1, t.BORDER),
                border_radius=6,
                bgcolor=t.BG_CARD,
                padding=4,
                expand=True,
            )

            basic_form = ft.Column(
                [col_dropdown, op_dropdown, val_input, val2_input],
                spacing=10,
                visible=(mode == "basic"),
            )
            advanced_form = ft.Column(
                [
                    ft.Text(
                        "Filter Expression -- same functions as Formula, "
                        "e.g. contains([col], 'x') or [age] > 30",
                        size=11,
                        color=t.TEXT_SECONDARY,
                    ),
                    ft.Row(
                        [filter_func_sidebar, filter_editor_container],
                        spacing=8,
                        height=300,
                    ),
                ],
                spacing=8,
                visible=(mode == "advanced"),
            )

            def switch_to_basic(e):
                mode_state["current"] = "basic"
                basic_form.visible = True
                advanced_form.visible = False
                basic_tab_btn.style = ft.ButtonStyle(color=ft.Colors.BLUE_400)
                adv_tab_btn.style = ft.ButtonStyle(color=ft.Colors.GREY_500)
                basic_form.update()
                advanced_form.update()
                basic_tab_btn.update()
                adv_tab_btn.update()
                self.config_container.update()

            def switch_to_advanced(e):
                mode_state["current"] = "advanced"
                basic_form.visible = False
                advanced_form.visible = True
                basic_tab_btn.style = ft.ButtonStyle(color=ft.Colors.GREY_500)
                adv_tab_btn.style = ft.ButtonStyle(color=ft.Colors.BLUE_400)
                basic_form.update()
                advanced_form.update()
                basic_tab_btn.update()
                adv_tab_btn.update()
                self.config_container.update()

            basic_tab_btn.on_click = switch_to_basic
            adv_tab_btn.on_click = switch_to_advanced

            def save_filter_config(e):
                from core.schemas.transform_schema import (
                    FilterInput,
                    BasicFilter,
                    FilterOperator,
                )

                if mode_state["current"] == "basic":
                    bf = BasicFilter(
                        field=col_dropdown.value or "",
                        operator=FilterOperator.from_symbol(op_dropdown.value or "="),
                        value=val_input.value or "",
                        value2=(
                            val2_input.value or ""
                            if op_dropdown.value == "between"
                            else None
                        ),
                    )
                    fi = FilterInput(mode="basic", basic_filter=bf)
                else:
                    fi = FilterInput(
                        mode="advanced", advanced_filter=expr_input.value or ""
                    )

                node.setting_input.filter_input = fi
                try:
                    self.flow_ref.add_filter(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Filter conditions saved successfully!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Settings",
                on_click=save_filter_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend(
                [
                    tab_row,
                    ft.Divider(height=1, color=ft.Colors.GREY_800),
                    basic_form,
                    advanced_form,
                    save_btn,
                ]
            )

        elif node.node_type == "select":
            t = get_theme(self.main_page)
            # Column selector: list of keep, rename, and type casts
            column_rows = []
            grid_cols = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, height=300)

            # Map existing configs
            existing_selects = {
                s.old_name: s for s in getattr(node.setting_input, "select_input", [])
            }

            # Gather all cols
            all_cols = list(incoming_cols)
            for old_n in existing_selects.keys():
                if old_n not in all_cols:
                    all_cols.append(old_n)

            if not all_cols:
                self.config_container.controls.append(
                    ft.Text(
                        "No input columns available. Run pipeline to infer columns.",
                        color=ft.Colors.GREY_500,
                        italic=True,
                    )
                )
                return

            for c in all_cols:
                cfg = existing_selects.get(c)
                keep_val = cfg.keep if cfg else True
                rename_val = cfg.new_name if cfg else c
                cast_val = cfg.data_type if cfg and cfg.data_type_change else "Auto"

                keep_switch = ft.Checkbox(
                    value=keep_val,
                    label=f"Keep {c}",
                    label_style=ft.TextStyle(color=t.TEXT_PRIMARY, size=12),
                )
                rename_tf = ft.TextField(
                    value=rename_val,
                    hint_text="Rename to",
                    height=32,
                    text_size=12,
                    expand=True,
                )
                cast_dd = ft.Dropdown(
                    options=[
                        ft.dropdown.Option("Auto"),
                        ft.dropdown.Option("String"),
                        ft.dropdown.Option("Int64"),
                        ft.dropdown.Option("Float64"),
                        ft.dropdown.Option("Boolean"),
                    ],
                    value=cast_val,
                    height=32,
                    text_size=12,
                    width=100,
                )

                column_rows.append((c, keep_switch, rename_tf, cast_dd))
                grid_cols.controls.append(
                    ft.Row(
                        [keep_switch, rename_tf, cast_dd],
                        spacing=10,
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    )
                )

            def save_select_config(e):
                from core.schemas.transform_schema import SelectInput

                new_selects = []
                for old_n, k_switch, r_tf, c_dd in column_rows:
                    si = SelectInput(
                        old_name=old_n,
                        new_name=r_tf.value.strip() or old_n,
                        keep=k_switch.value,
                        data_type=c_dd.value if c_dd.value != "Auto" else None,
                        data_type_change=(c_dd.value != "Auto"),
                    )
                    new_selects.append(si)

                node.setting_input.select_input = new_selects
                try:
                    self.flow_ref.add_select(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Select configurations saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Selection Settings",
                on_click=save_select_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend([grid_cols, save_btn])

        elif node.node_type == "formula":
            t = get_theme(self.main_page)
            func = getattr(node.setting_input, "function", None)
            new_col = func.field.name if func and func.field else ""
            expr = func.function if func else ""
            data_type = (
                func.field.data_type
                if func and func.field and func.field.data_type
                else "Auto"
            )

            new_col_input = ft.TextField(
                label="Output field", value=new_col, height=44, text_size=13
            )
            data_type_dropdown = ft.Dropdown(
                label="Data type",
                value=data_type,
                options=[
                    ft.dropdown.Option("Auto"),
                    ft.dropdown.Option("String"),
                    ft.dropdown.Option("Integer"),
                    ft.dropdown.Option("Double"),
                    ft.dropdown.Option("Boolean"),
                    ft.dropdown.Option("Date"),
                ],
                height=44,
                text_size=13,
            )

            # Shared with Filter's Advanced Expression editor -- see the
            # FUNCTIONS_BY_CATEGORY class constant docstring.
            functions_by_category = self.FUNCTIONS_BY_CATEGORY

            search_input = ft.TextField(
                hint_text="Filter functions...",
                height=32,
                text_size=11,
                content_padding=5,
            )

            # NOTE: height + expand=True on the same scrollable Column is a
            # conflicting/undefined combination in Flet -- it caused content
            # (like the last "Type conversions" category) to render outside
            # the visible/scrollable area with no way to reach it. A fixed
            # height alone gives deterministic, reliably-scrollable bounds.
            functions_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=260)

            validator_icon = ft.Icon(
                ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED,
                color=ft.Colors.GREEN_400,
                size=14,
            )
            validator_text = ft.Text(
                "Function valid, run process to see results",
                size=11,
                color=ft.Colors.GREEN_400,
            )
            validator_row = ft.Row([validator_icon, validator_text], spacing=4)

            def validate_formula(e):
                val = expr_input.value or ""
                stack = []
                mapping = {")": "(", "]": "["}
                is_balanced = True
                for char in val:
                    if char in "([":
                        stack.append(char)
                    elif char in ")]":
                        if not stack or stack[-1] != mapping[char]:
                            is_balanced = False
                            break
                        stack.pop()
                if stack:
                    is_balanced = False

                if not val.strip():
                    validator_icon.name = ft.Icons.INFO_OUTLINE
                    validator_icon.color = ft.Colors.AMBER_400
                    validator_text.value = "Enter an expression"
                    validator_text.color = ft.Colors.AMBER_400
                elif not is_balanced:
                    validator_icon.name = ft.Icons.ERROR_OUTLINE_ROUNDED
                    validator_icon.color = ft.Colors.RED_400
                    validator_text.value = "⚠ Unbalanced parentheses or brackets"
                    validator_text.color = ft.Colors.RED_400
                else:
                    validator_icon.name = ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED
                    validator_icon.color = ft.Colors.GREEN_400
                    validator_text.value = "Function valid, run process to see results"
                    validator_text.color = ft.Colors.GREEN_400
                try:
                    validator_row.update()
                except Exception:
                    pass

            expr_input = ft.TextField(
                value=expr,
                multiline=True,
                min_lines=10,
                max_lines=10,
                text_size=12,
                text_style=ft.TextStyle(
                    font_family="Courier New", color=t.TEXT_PRIMARY
                ),
                expand=True,
                on_change=validate_formula,
                border=ft.InputBorder.NONE,
                bgcolor=t.BG_CARD,
            )

            def insert_text(text):
                expr_input.value = (expr_input.value or "") + text
                expr_input.update()
                validate_formula(None)

            formula_doc_lookup = _get_expression_doc_lookup()

            def update_functions_list(filter_text=""):
                functions_col.controls.clear()
                filter_text = filter_text.lower()
                for cat, funcs in functions_by_category.items():
                    filtered = [
                        f
                        for f in funcs
                        if filter_text in f[0].lower() or filter_text in f[1].lower()
                    ]
                    if not filtered:
                        continue

                    category_tile_controls = []
                    for name, template in filtered:
                        category_tile_controls.append(
                            ft.Container(
                                content=ft.Text(
                                    name,
                                    size=11,
                                    weight=ft.FontWeight.W_500,
                                    color=ft.Colors.BLUE_400,
                                ),
                                padding=ft.Padding(left=10, top=2, right=10, bottom=2),
                                on_click=lambda e, temp=template: insert_text(temp),
                                tooltip=formula_doc_lookup.get(
                                    name.lower().replace(" ", "_"), template
                                ),
                            )
                        )

                    functions_col.controls.append(
                        ft.ExpansionTile(
                            title=ft.Text(cat, size=12, weight=ft.FontWeight.BOLD),
                            controls=category_tile_controls,
                            # Collapsed by default: with the fuller function
                            # catalog (~90 entries) having every category
                            # pre-expanded crammed too much content into the
                            # bounded sidebar list at once, which triggered
                            # nested-scroll bubbling into the parent dialog.
                            expanded=bool(filter_text),
                        )
                    )
                try:
                    functions_col.update()
                except Exception:
                    pass

            search_input.on_change = lambda e: update_functions_list(search_input.value)

            columns_list_col = ft.Column(
                scroll=ft.ScrollMode.AUTO,
                spacing=4,
                expand=True,
            )
            for col in incoming_cols:
                columns_list_col.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(
                                    ft.Icons.TAG_ROUNDED,
                                    size=14,
                                    color=ft.Colors.BLUE_300,
                                ),
                                ft.Text(
                                    col,
                                    size=11,
                                    weight=ft.FontWeight.W_500,
                                    color=t.TEXT_PRIMARY,
                                ),
                            ],
                            spacing=6,
                        ),
                        padding=ft.Padding(left=8, top=4, right=8, bottom=4),
                        border_radius=4,
                        on_click=lambda e, c=col: insert_text(f"[{c}]"),
                    )
                )

            sidebar_content_col = ft.Column(
                [search_input, functions_col],
                spacing=6,
                expand=True,
            )

            active_left_tab = ["functions"]

            def select_left_tab(tab_name):
                active_left_tab[0] = tab_name
                if tab_name == "columns":
                    columns_tab_btn.icon_color = ft.Colors.BLUE_400
                    functions_tab_btn.icon_color = ft.Colors.GREY_400
                    sidebar_content_col.controls = [columns_list_col]
                else:
                    columns_tab_btn.icon_color = ft.Colors.GREY_400
                    functions_tab_btn.icon_color = ft.Colors.BLUE_400
                    sidebar_content_col.controls = [search_input, functions_col]
                try:
                    sidebar_content_col.update()
                    columns_tab_btn.update()
                    functions_tab_btn.update()
                except Exception:
                    pass

            columns_tab_btn = ft.IconButton(
                icon=ft.Icons.VIEW_COLUMN_ROUNDED,
                icon_color=ft.Colors.GREY_400,
                tooltip="Input Columns",
                on_click=lambda e: select_left_tab("columns"),
            )
            functions_tab_btn = ft.IconButton(
                icon=ft.Icons.SETTINGS_ROUNDED,
                icon_color=ft.Colors.BLUE_400,
                tooltip="Functions",
                on_click=lambda e: select_left_tab("functions"),
            )

            tab_selection_row = ft.Row(
                [columns_tab_btn, functions_tab_btn],
                spacing=4,
                alignment=ft.MainAxisAlignment.START,
            )

            left_sidebar = ft.Container(
                content=ft.Column(
                    [tab_selection_row, sidebar_content_col], spacing=6, expand=True
                ),
                width=180,
                border=ft.Border(right=ft.border.BorderSide(1, t.BORDER)),
                padding=ft.Padding(right=8, top=0, left=0, bottom=0),
            )

            line_numbers_col = ft.Column(
                [
                    ft.Container(
                        content=ft.Text(
                            str(i),
                            size=11,
                            color=ft.Colors.GREY_500,
                            font_family="Courier New",
                        ),
                        height=18,
                        alignment=ft.alignment.Alignment(1, 0),
                    )
                    for i in range(1, 11)
                ],
                spacing=0,
            )

            editor_container = ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            content=line_numbers_col,
                            padding=ft.Padding(top=10, right=4),
                            alignment=ft.alignment.Alignment(1, -1),
                        ),
                        ft.Container(
                            content=expr_input,
                            expand=True,
                        ),
                    ],
                    spacing=4,
                    expand=True,
                ),
                border=ft.Border.all(1, t.BORDER),
                border_radius=6,
                bgcolor=t.BG_CARD,
                padding=4,
            )

            right_editor = ft.Container(
                content=ft.Column(
                    [editor_container, validator_row], spacing=8, expand=True
                ),
                expand=True,
                padding=ft.Padding(left=8, top=0, right=0, bottom=0),
            )

            middle_row = ft.Row(
                [left_sidebar, right_editor],
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )

            top_row = ft.Row(
                [
                    ft.Container(content=new_col_input, expand=True),
                    ft.Container(content=data_type_dropdown, expand=True),
                ],
                spacing=8,
            )

            def save_formula_config(e):
                from core.schemas.transform_schema import FunctionInput, FieldInput

                col_name = new_col_input.value.strip()
                if not col_name:
                    self._snack(
                        "⚠ Output field name cannot be empty", ft.Colors.AMBER_700
                    )
                    return
                if not expr_input.value.strip():
                    self._snack("⚠ Expression cannot be empty", ft.Colors.AMBER_700)
                    return
                fi = FieldInput(name=col_name, data_type=data_type_dropdown.value)
                node.setting_input.function = FunctionInput(
                    field=fi, function=expr_input.value.strip()
                )
                try:
                    result = self.flow_ref.add_formula(node.setting_input)
                    self.save_active_flow()
                    self.update_preview_ui()
                    self._snack("✓ Formula saved", ft.Colors.GREEN_700)
                    validate_formula(None)
                except Exception as ex:
                    self._snack(f"Error saving formula: {ex}", ft.Colors.RED_700)

            def format_formula(e):
                val = expr_input.value or ""
                if not val.strip():
                    return
                try:
                    import ast

                    formatted = ast.unparse(ast.parse(val.strip()))
                    expr_input.value = formatted
                    expr_input.update()
                    self._snack("✓ Formula formatted", ft.Colors.GREEN_700)
                except Exception:
                    import re

                    for cat, funcs in functions_by_category.items():
                        for name, _ in funcs:
                            val = re.sub(
                                rf"\b{name}\b\s*\(",
                                f"{name}(",
                                val,
                                flags=re.IGNORECASE,
                            )
                    expr_input.value = val.strip()
                    expr_input.update()
                    self._snack("✓ Formula cleaned", ft.Colors.GREEN_700)
                validate_formula(None)

            format_btn = ft.TextButton(
                "Format Formula",
                on_click=format_formula,
                icon=ft.Icons.CLEANING_SERVICES_ROUNDED,
                style=ft.ButtonStyle(color=ft.Colors.BLUE_400),
            )

            save_btn = ft.Button(
                "Apply",
                on_click=save_formula_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            buttons_row = ft.Row(
                [save_btn, format_btn],
                spacing=12,
                alignment=ft.MainAxisAlignment.START,
            )

            # Tab Content Wrappers
            main_settings_tab_content = ft.Column(
                [
                    # Small top spacer so the "Output field"/"Data type"
                    # floating labels don't visually collide with the
                    # TabBar's divider line right above this content.
                    ft.Container(height=10),
                    top_row,
                    ft.Container(height=4),
                    middle_row,
                    ft.Container(height=4),
                    buttons_row,
                ],
                spacing=6,
                expand=True,
            )

            general_settings_tab_content = ft.Column(
                [
                    ft.Text("Step Description", weight=ft.FontWeight.BOLD, size=13),
                    ft.TextField(
                        label="Enter description...",
                        value=getattr(node.setting_input, "description", "") or "",
                        multiline=True,
                        min_lines=3,
                        max_lines=3,
                        text_size=12,
                    ),
                    ft.Switch(
                        label="Cache execution results",
                        value=getattr(node.setting_input, "cache_results", False),
                    ),
                ],
                spacing=12,
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            )

            schema_rows = []
            for col in incoming_cols:
                schema_rows.append(
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.TAG_ROUNDED, size=14, color=ft.Colors.BLUE_300
                            ),
                            ft.Text(
                                col, size=12, weight=ft.FontWeight.W_500, expand=True
                            ),
                            ft.Text("Auto", size=11, color=ft.Colors.GREY_400),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    )
                )

            schema_validator_tab_content = ft.Column(
                [
                    ft.Text(
                        "Predicted Input Schema", weight=ft.FontWeight.BOLD, size=13
                    ),
                    ft.Divider(height=1, color=t.BORDER),
                    ft.Column(
                        schema_rows, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True
                    ),
                ],
                spacing=12,
                expand=True,
            )

            # Tab Control matching Flet's TabBarView pattern
            tabs_root = ft.Tabs(
                length=3,
                height=570,  # fixed height: expand=True conflicted with config_container's scroll=AUTO, ballooning this dialog with blank space instead of respecting the 800x600 bounds
                content=ft.Column(
                    expand=True,
                    controls=[
                        ft.TabBar(
                            tabs=[
                                ft.Tab(label="Main Settings"),
                                ft.Tab(label="General Settings"),
                                ft.Tab(label="Schema Validator"),
                            ]
                        ),
                        ft.TabBarView(
                            expand=True,
                            controls=[
                                main_settings_tab_content,
                                general_settings_tab_content,
                                schema_validator_tab_content,
                            ],
                        ),
                    ],
                ),
            )

            self.config_container.controls.append(tabs_root)
            update_functions_list()
            validate_formula(None)

        elif node.node_type == "polars_code":
            # Sample/placeholder text shown for a fresh, unconfigured node.
            # NOTE: the input variable is `input_df` (single input) or
            # `input_df_1`, `input_df_2`, ... (multiple inputs, 1-indexed) --
            # the previous default here ("df = df.with_columns(...)")
            # referenced an undefined `df` variable and crashed immediately
            # on Apply. Verified against PolarsCodeParser.get_executable():
            # single-line expressions starting with input_df/pl./col()/expr()
            # are returned directly; multi-line code and no-input code must
            # assign to `output_df`.
            _POLARS_CODE_SAMPLE = """# Example of usage (you can remove this)
# Single line transformations:
#   input_df.filter(pl.col('column_name') > 0)

# Multi-line transformations (must assign to output_df):
#   result = input_df.select(['a', 'b'])
#   filtered = result.filter(pl.col('a') > 0)
#   output_df = filtered.with_columns(pl.col('b').alias('new_b'))

# Multiple input dataframes are available as input_df_1, input_df_2, etc:
#   output_df = input_df_1.join(input_df_2, on='id')

# No inputs example (node will act as a starter node):
#   output_df = pl.DataFrame({'a': [1, 2, 3], 'b': ['x', 'y', 'z']})

# Your code here:
input_df"""

            pci = getattr(node.setting_input, "polars_code_input", None)
            # Blank saved code counts as "not configured yet" -- a node saved
            # with an empty editor would otherwise come back empty forever
            # instead of offering the usage sample again.
            saved_code = (getattr(pci, "polars_code", "") or "").strip()
            code = pci.polars_code if saved_code else _POLARS_CODE_SAMPLE

            t = get_theme(self.main_page)

            code_input = ft.TextField(
                value=code,
                multiline=True,
                # Must match the 20-row line-number gutter below, the same way
                # the Formula editor pins its field to its 10-row gutter. With
                # min_lines=6 and no max_lines the field rendered as a 6-line
                # band floating in the middle of the box: only those lines were
                # clickable, the text sat against gutter number 8 instead of 1,
                # and the sample code looked like it was missing entirely.
                min_lines=20,
                max_lines=20,
                expand=True,
                text_size=12,
                text_style=ft.TextStyle(
                    font_family="Courier New", color=t.TEXT_PRIMARY
                ),
                border=ft.InputBorder.NONE,
                bgcolor=t.BG_CARD,
                # Without this, the field's cursor defaults to the end of
                # `value` (after the boilerplate comment block), which
                # scrolls the editor straight to the bottom on open --
                # client-reported as the scroller/view starting in the
                # wrong place. Pin the initial cursor/scroll to the top.
                selection=ft.TextSelection(base_offset=0, extent_offset=0),
            )

            # Line-numbers gutter, matching the Formula node's editor style
            # (client-requested: "replicate the code formatting in the
            # formula function to polars code function"). This is a static
            # decorative gutter, same as Formula's -- not dynamically synced
            # to scroll position.
            line_numbers_col = ft.Column(
                [
                    ft.Container(
                        content=ft.Text(
                            str(i),
                            size=11,
                            color=ft.Colors.GREY_500,
                            font_family="Courier New",
                        ),
                        height=18,
                        alignment=ft.alignment.Alignment(1, 0),
                    )
                    for i in range(1, 21)
                ],
                spacing=0,
            )
            editor_container = ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            content=line_numbers_col,
                            padding=ft.Padding(top=10, right=4),
                            alignment=ft.alignment.Alignment(1, -1),
                        ),
                        ft.Container(content=code_input, expand=True),
                    ],
                    spacing=4,
                    expand=True,
                    # Top-align both columns so the first line of code sits
                    # next to gutter number 1; with the default centre
                    # alignment any leftover height pushed the text down and
                    # the numbers no longer matched the lines.
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                border=ft.Border.all(1, t.BORDER),
                border_radius=6,
                bgcolor=t.BG_CARD,
                padding=4,
            )

            def save_polars_code_config(e):
                from core.schemas.transform_schema import PolarsCodeInput

                node.setting_input.polars_code_input = PolarsCodeInput(
                    polars_code=code_input.value or ""
                )
                try:
                    self.flow_ref.add_polars_code(node.setting_input)
                    self.show_dialog("Success", "Polars Code saved successfully!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            def format_polars_code(e):
                val = code_input.value or ""
                if not val.strip():
                    return
                try:
                    import autopep8

                    formatted = autopep8.fix_code(val)
                    code_input.value = formatted
                    code_input.update()
                    self._snack("✓ Code formatted", ft.Colors.GREEN_700)
                except Exception as ex:
                    self._snack(f"⚠ Formatting failed: {ex}", ft.Colors.ORANGE_700)

            save_btn = ft.Button(
                "Save Code Settings",
                on_click=save_polars_code_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            format_btn = ft.TextButton(
                "Format Code",
                on_click=format_polars_code,
                icon=ft.Icons.CLEANING_SERVICES_ROUNDED,
                style=ft.ButtonStyle(color=ft.Colors.BLUE_400),
            )

            buttons_row = ft.Row(
                [save_btn, format_btn],
                spacing=12,
                alignment=ft.MainAxisAlignment.START,
            )
            self.config_container.controls.extend(
                [
                    ft.Text(
                        "Write custom Polars DataFrame transformations",
                        size=12,
                        color=t.TEXT_SECONDARY,
                    ),
                    editor_container,
                    buttons_row,
                ]
            )

        elif node.node_type == "output":
            from pathlib import Path

            def _default_output_dir() -> str:
                # A blank/"." directory used to resolve to the process's
                # working directory, which for a packaged Windows .exe is
                # unpredictable and often not writable -- default to the
                # user's Downloads folder instead (falls back to home).
                downloads = Path.home() / "Downloads"
                if downloads.is_dir():
                    return str(downloads)
                return str(Path.home())

            o = getattr(node.setting_input, "output_settings", None)
            name = o.name if o else "export_result.csv"
            directory = (o.directory if o and o.directory and o.directory != "." else None) or _default_output_dir()
            file_type = o.file_type if o else "csv"
            write_mode = o.write_mode if o else "overwrite"

            name_input = ft.TextField(
                label="Export File Name", value=name, height=44, text_size=13
            )
            dir_input = ft.TextField(
                label="Output Directory",
                value=directory,
                height=44,
                text_size=13,
                expand=True,
                hint_text="Click Browse 📁 or paste path here...",
            )

            def open_dir_picker(e):
                async def _pick():
                    picked = await self._file_picker.get_directory_path(
                        dialog_title="Select Output Directory",
                    )
                    if picked:
                        dir_input.value = picked
                        dir_input.update()

                self.main_page.run_task(_pick)

            browse_dir_btn = ft.IconButton(
                icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                icon_color=ft.Colors.BLUE_400,
                tooltip="Browse for directory",
                on_click=open_dir_picker,
            )
            dir_row = ft.Row(
                [dir_input, browse_dir_btn],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

            type_dropdown = ft.Dropdown(
                label="Output Format",
                options=[
                    ft.dropdown.Option("csv"),
                    ft.dropdown.Option("excel"),
                    ft.dropdown.Option("parquet"),
                ],
                value=file_type,
                height=44,
                text_size=13,
            )
            mode_dropdown = ft.Dropdown(
                label="Write Mode",
                options=[ft.dropdown.Option("overwrite"), ft.dropdown.Option("append")],
                value=write_mode,
                height=44,
                text_size=13,
            )

            def save_output_config(e):
                from core.schemas.input_schema import (
                    OutputSettings,
                    OutputCsvTable,
                    OutputParquetTable,
                    OutputExcelTable,
                )

                fmt = type_dropdown.value.lower()

                if fmt == "csv":
                    ts = OutputCsvTable()
                elif fmt == "excel":
                    ts = OutputExcelTable()
                else:
                    ts = OutputParquetTable()

                node.setting_input.output_settings = OutputSettings(
                    name=name_input.value.strip(),
                    directory=dir_input.value.strip() or _default_output_dir(),
                    file_type=fmt,
                    write_mode=mode_dropdown.value.lower(),
                    table_settings=ts,
                )
                try:
                    self.flow_ref.add_output(node.setting_input)
                    self.show_dialog("Success", "Output File settings saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Output Settings",
                on_click=save_output_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend(
                [name_input, dir_row, type_dropdown, mode_dropdown, save_btn]
            )

        elif node.node_type == "sort":
            rules = getattr(node.setting_input, "sort_input", [])
            sort_rows = []
            grid_rules = ft.Column(spacing=6)

            def rebuild_sort_rules():
                grid_rules.controls.clear()
                sort_rows.clear()
                for idx, r in enumerate(rules):
                    col_dd = ft.Dropdown(
                        options=[ft.dropdown.Option(c) for c in incoming_cols],
                        value=r.column,
                        height=32,
                        text_size=12,
                        expand=True,
                    )
                    dir_dd = ft.Dropdown(
                        options=[ft.dropdown.Option("asc"), ft.dropdown.Option("desc")],
                        value=r.how or "asc",
                        height=32,
                        text_size=12,
                        width=80,
                    )

                    def make_del_handler(rule_idx):
                        return lambda _: delete_rule(rule_idx)

                    del_btn = ft.IconButton(
                        ft.Icons.DELETE_ROUNDED,
                        icon_color=ft.Colors.RED_400,
                        on_click=make_del_handler(idx),
                    )

                    sort_rows.append((col_dd, dir_dd))
                    grid_rules.controls.append(
                        ft.Row([col_dd, dir_dd, del_btn], spacing=10)
                    )

            def delete_rule(idx):
                rules.pop(idx)
                rebuild_sort_rules()
                grid_rules.update()

            def add_rule(e):
                from core.schemas.transform_schema import SortByInput

                rules.append(
                    SortByInput(
                        column=incoming_cols[0] if incoming_cols else "", how="asc"
                    )
                )
                rebuild_sort_rules()
                grid_rules.update()

            rebuild_sort_rules()
            add_btn = ft.Button("Add Sort Rule", icon=ft.Icons.ADD, on_click=add_rule)

            def save_sort_config(e):
                from core.schemas.transform_schema import SortByInput

                new_rules = []
                for c_dd, d_dd in sort_rows:
                    if c_dd.value:
                        new_rules.append(SortByInput(column=c_dd.value, how=d_dd.value))
                node.setting_input.sort_input = new_rules
                try:
                    self.flow_ref.add_sort(node.setting_input)
                    self.show_dialog("Success", "Sorting saved successfully!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Sorting",
                on_click=save_sort_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend([grid_rules, add_btn, save_btn])

        elif node.node_type == "join":
            # Setup similar to fuzzy_match, but for standard join
            how_dd = ft.Dropdown(
                label="Join Strategy (How)",
                options=[
                    ft.dropdown.Option("inner"),
                    ft.dropdown.Option("left"),
                    ft.dropdown.Option("right"),
                    ft.dropdown.Option("full"),
                    ft.dropdown.Option("cross"),
                    ft.dropdown.Option("semi"),
                    ft.dropdown.Option("anti"),
                ],
                value="inner",
                height=44,
                text_size=13,
            )

            # Attempt to gather right input columns
            right_cols = []
            try:
                node_data = node.get_node_data(
                    flow_id=self.active_flow_id, include_example=False
                )
                if (
                    node_data
                    and node_data.right_input
                    and node_data.right_input.columns
                ):
                    right_cols = node_data.right_input.columns
            except:
                pass

            from core.schemas.transform_schema import JoinMap

            # Load initial settings if present (supports one or more key mappings)
            ji = getattr(node.setting_input, "join_input", None)
            if ji:
                how_dd.value = getattr(ji, "how", "inner")

            join_mappings = (
                list(ji.join_mapping)
                if ji and ji.join_mapping
                else [JoinMap(left_col="", right_col="")]
            )

            mapping_rows = []
            mapping_col = ft.Column(spacing=6)

            def rebuild_join_rows():
                mapping_col.controls.clear()
                mapping_rows.clear()
                for idx, m in enumerate(join_mappings):
                    l_dd = ft.Dropdown(
                        options=[ft.dropdown.Option(c) for c in incoming_cols],
                        value=m.left_col if m.left_col in incoming_cols else None,
                        height=44,
                        text_size=13,
                        expand=True,
                        label="Left Key Column" if idx == 0 else None,
                    )
                    r_dd = ft.Dropdown(
                        options=[ft.dropdown.Option(c) for c in right_cols],
                        value=m.right_col if m.right_col in right_cols else None,
                        height=44,
                        text_size=13,
                        expand=True,
                        label="Right Key Column" if idx == 0 else None,
                    )

                    def make_del_handler(row_idx):
                        return lambda _: delete_join_row(row_idx)

                    del_btn = ft.IconButton(
                        ft.Icons.REMOVE_CIRCLE_OUTLINE_ROUNDED,
                        icon_color=ft.Colors.RED_400,
                        tooltip="Remove condition",
                        on_click=make_del_handler(idx),
                    )

                    mapping_rows.append((l_dd, r_dd))
                    mapping_col.controls.append(
                        ft.Row([l_dd, r_dd, del_btn], spacing=10)
                    )

            def delete_join_row(idx):
                join_mappings.pop(idx)
                rebuild_join_rows()
                mapping_col.update()

            def add_join_row(e):
                join_mappings.append(JoinMap(left_col="", right_col=""))
                rebuild_join_rows()
                mapping_col.update()

            rebuild_join_rows()
            add_row_btn = ft.TextButton(
                "Add Join Condition", icon=ft.Icons.ADD, on_click=add_join_row
            )

            # Restores the "which columns to keep / rename" control from the
            # original Dataryx app (Left data / Right data) -- dropped during
            # the Flet port, so both sides silently kept every column with no
            # user control. Match keys stay usable for the join either way,
            # whether or not the user keeps them in the output (same as before).
            _keep_section_t = get_theme(self.main_page)
            left_select_section, get_left_selections = build_column_keep_section(
                "Left data",
                incoming_cols,
                ji.left_select.renames if ji else None,
                text_color=_keep_section_t.TEXT_PRIMARY,
            )
            right_select_section, get_right_selections = build_column_keep_section(
                "Right data",
                right_cols,
                ji.right_select.renames if ji else None,
                text_color=_keep_section_t.TEXT_PRIMARY,
            )

            def save_join_config(e):
                from core.schemas.transform_schema import JoinInput, JoinInputs, SelectInput
                from core.schemas.input_schema import NodeJoin

                new_mapping = [
                    JoinMap(left_col=l_dd.value, right_col=r_dd.value)
                    for l_dd, r_dd in mapping_rows
                    if l_dd.value and r_dd.value
                ]
                if not new_mapping:
                    self.show_dialog(
                        "Error", "Please select both Left and Right key columns for at least one join condition."
                    )
                    return

                ji_val = JoinInput(
                    join_mapping=new_mapping,
                    left_select=JoinInputs(
                        renames=[
                            SelectInput(old_name=old, new_name=new, keep=keep)
                            for old, new, keep in get_left_selections()
                        ]
                    ),
                    right_select=JoinInputs(
                        renames=[
                            SelectInput(old_name=old, new_name=new, keep=keep)
                            for old, new, keep in get_right_selections()
                        ]
                    ),
                    how=how_dd.value,
                )
                node.setting_input = NodeJoin(
                    flow_id=self.active_flow_id,
                    node_id=node.node_id,
                    join_input=ji_val,
                )
                try:
                    self.flow_ref.add_join(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Join configured successfully!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Join",
                on_click=save_join_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend([
                how_dd,
                ft.Row(
                    [
                        ft.Text("Join Conditions", weight=ft.FontWeight.BOLD),
                        add_row_btn,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                mapping_col,
                left_select_section,
                right_select_section,
                ft.Row([save_btn], alignment=ft.MainAxisAlignment.END)
            ])


        elif node.node_type == "fuzzy_match":
            # Advanced Fuzzy Match UI (supports one or more fuzzy criteria)
            how_dd = ft.Dropdown(
                label="Join Strategy (How)",
                options=[
                    ft.dropdown.Option("inner"),
                    ft.dropdown.Option("left"),
                    ft.dropdown.Option("right"),
                    ft.dropdown.Option("full"),
                ],
                value="inner",
                height=44,
                text_size=13,
            )

            right_cols = []
            try:
                node_data = node.get_node_data(flow_id=self.active_flow_id, include_example=False)
                if node_data and node_data.right_input and node_data.right_input.columns:
                    right_cols = node_data.right_input.columns
            except:
                pass

            from core.schemas.transform_schema import FuzzyMap

            # Load initial settings if present
            ji = getattr(node.setting_input, "join_input", None)
            if ji:
                how_dd.value = getattr(ji, "how", "inner")

            fuzzy_mappings = (
                list(ji.join_mapping)
                if ji and ji.join_mapping
                else [
                    FuzzyMap(
                        left_col="",
                        right_col="",
                        fuzzy_type="levenshtein",
                        threshold_score=80.0,
                    )
                ]
            )

            mapping_rows = []
            mapping_col = ft.Column(spacing=10)

            def rebuild_fuzzy_rows():
                mapping_col.controls.clear()
                mapping_rows.clear()
                for idx, m in enumerate(fuzzy_mappings):
                    l_dd = ft.Dropdown(
                        options=[ft.dropdown.Option(c) for c in incoming_cols],
                        value=m.left_col if m.left_col in incoming_cols else None,
                        height=44,
                        text_size=13,
                        expand=True,
                        label="Left Column",
                    )
                    r_dd = ft.Dropdown(
                        options=[ft.dropdown.Option(c) for c in right_cols],
                        value=m.right_col if m.right_col in right_cols else None,
                        height=44,
                        text_size=13,
                        expand=True,
                        label="Right Column",
                    )
                    alg_dd = ft.Dropdown(
                        options=[
                            ft.dropdown.Option("levenshtein"),
                            ft.dropdown.Option("jaro"),
                            ft.dropdown.Option("jaro_winkler"),
                            ft.dropdown.Option("hamming"),
                            ft.dropdown.Option("damerau_levenshtein"),
                            ft.dropdown.Option("indel"),
                        ],
                        value=m.fuzzy_type or "levenshtein",
                        height=44,
                        text_size=13,
                        width=200,
                        label="Fuzzy Algorithm",
                    )
                    thresh_slider = ft.Slider(
                        min=0, max=100, divisions=100,
                        value=m.threshold_score or 80.0,
                        label="{value}%",
                        expand=True,
                    )

                    def make_del_handler(row_idx):
                        return lambda _: delete_fuzzy_row(row_idx)

                    del_btn = ft.IconButton(
                        ft.Icons.REMOVE_CIRCLE_OUTLINE_ROUNDED,
                        icon_color=ft.Colors.RED_400,
                        tooltip="Remove setting",
                        on_click=make_del_handler(idx),
                    )

                    mapping_rows.append((l_dd, r_dd, alg_dd, thresh_slider))
                    mapping_col.controls.append(
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Text(
                                                f"Setting {idx + 1}",
                                                weight=ft.FontWeight.BOLD,
                                                size=12,
                                            ),
                                            del_btn,
                                        ],
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    ),
                                    ft.Row([l_dd, r_dd], spacing=10),
                                    alg_dd,
                                    ft.Column(
                                        [
                                            ft.Text(
                                                "Similarity Threshold (%)",
                                                size=12,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                            thresh_slider,
                                        ],
                                        spacing=2,
                                    ),
                                ],
                                spacing=8,
                            ),
                            padding=10,
                            border=ft.Border.all(1, ft.Colors.GREY_700),
                            border_radius=8,
                        )
                    )

            def delete_fuzzy_row(idx):
                fuzzy_mappings.pop(idx)
                rebuild_fuzzy_rows()
                mapping_col.update()

            def add_fuzzy_row(e):
                fuzzy_mappings.append(
                    FuzzyMap(
                        left_col="",
                        right_col="",
                        fuzzy_type="levenshtein",
                        threshold_score=80.0,
                    )
                )
                rebuild_fuzzy_rows()
                mapping_col.update()

            rebuild_fuzzy_rows()
            add_row_btn = ft.TextButton(
                "Add Fuzzy Setting", icon=ft.Icons.ADD, on_click=add_fuzzy_row
            )

            # Same restored "Left data / Right data" column-keep control as
            # the Join node above -- see build_column_keep_section's docstring.
            _keep_section_t = get_theme(self.main_page)
            left_select_section, get_left_selections = build_column_keep_section(
                "Left data",
                incoming_cols,
                ji.left_select.renames if ji else None,
                text_color=_keep_section_t.TEXT_PRIMARY,
            )
            right_select_section, get_right_selections = build_column_keep_section(
                "Right data",
                right_cols,
                ji.right_select.renames if ji else None,
                text_color=_keep_section_t.TEXT_PRIMARY,
            )

            def save_fuzzy_config(e):
                from core.schemas.transform_schema import FuzzyMatchInput, JoinInputs, SelectInput
                from core.schemas.input_schema import NodeFuzzyMatch

                new_mapping = [
                    FuzzyMap(
                        left_col=l_dd.value,
                        right_col=r_dd.value,
                        fuzzy_type=alg_dd.value,
                        threshold_score=thresh_slider.value,
                    )
                    for l_dd, r_dd, alg_dd, thresh_slider in mapping_rows
                    if l_dd.value and r_dd.value
                ]
                if not new_mapping:
                    self.show_dialog(
                        "Error", "Please select both Left and Right columns for at least one fuzzy setting."
                    )
                    return

                ji_val = FuzzyMatchInput(
                    join_mapping=new_mapping,
                    left_select=JoinInputs(
                        renames=[
                            SelectInput(old_name=old, new_name=new, keep=keep)
                            for old, new, keep in get_left_selections()
                        ]
                    ),
                    right_select=JoinInputs(
                        renames=[
                            SelectInput(old_name=old, new_name=new, keep=keep)
                            for old, new, keep in get_right_selections()
                        ]
                    ),
                    how=how_dd.value,
                )
                node.setting_input = NodeFuzzyMatch(
                    flow_id=self.active_flow_id,
                    node_id=node.node_id,
                    join_input=ji_val,
                )
                try:
                    self.flow_ref.add_fuzzy_match(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Fuzzy Match configured successfully!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Fuzzy Match",
                on_click=save_fuzzy_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend([
                how_dd,
                ft.Row(
                    [
                        ft.Text("Fuzzy Mapping Settings", weight=ft.FontWeight.BOLD),
                        add_row_btn,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                mapping_col,
                left_select_section,
                right_select_section,
                ft.Row([save_btn], alignment=ft.MainAxisAlignment.END)
            ])



        elif node.node_type == "cross_join":
            t = get_theme(self.main_page)
            # Dedicated Cross Join UI
            right_cols = []
            try:
                node_data = node.get_node_data(
                    flow_id=self.active_flow_id, include_example=False
                )
                if (
                    node_data
                    and node_data.right_input
                    and node_data.right_input.columns
                ):
                    right_cols = node_data.right_input.columns
            except:
                pass

            from core.schemas.transform_schema import SelectInput, JoinInputs
            from core.schemas.input_schema import NodeCrossJoin
            
            # Map existing configs
            existing_left_selects = {}
            existing_right_selects = {}
            cji = getattr(node.setting_input, "cross_join_input", None)
            if cji:
                existing_left_selects = {s.old_name: s for s in getattr(cji.left_select, "renames", [])}
                existing_right_selects = {s.old_name: s for s in getattr(cji.right_select, "renames", [])}

            # Left Columns list
            left_rows = []
            left_col_layout = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, height=220)
            for c in incoming_cols:
                cfg = existing_left_selects.get(c)
                keep_val = cfg.keep if cfg else True
                rename_val = cfg.new_name if cfg else c
                
                keep_switch = ft.Checkbox(
                    value=keep_val,
                    label=f"Keep {c}",
                    label_style=ft.TextStyle(color=t.TEXT_PRIMARY, size=12),
                )
                rename_tf = ft.TextField(
                    value=rename_val,
                    hint_text="Rename to",
                    height=32,
                    text_size=12,
                    expand=True,
                )
                left_rows.append((c, keep_switch, rename_tf))
                left_col_layout.controls.append(ft.Row([keep_switch, rename_tf], spacing=6))

            # Right Columns list
            right_rows = []
            right_col_layout = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, height=220)
            for c in right_cols:
                cfg = existing_right_selects.get(c)
                keep_val = cfg.keep if cfg else True
                rename_val = cfg.new_name if cfg else c
                
                keep_switch = ft.Checkbox(
                    value=keep_val,
                    label=f"Keep {c}",
                    label_style=ft.TextStyle(color=t.TEXT_PRIMARY, size=12),
                )
                rename_tf = ft.TextField(
                    value=rename_val,
                    hint_text="Rename to",
                    height=32,
                    text_size=12,
                    expand=True,
                )
                right_rows.append((c, keep_switch, rename_tf))
                right_col_layout.controls.append(ft.Row([keep_switch, rename_tf], spacing=6))

            # Tab Control matching Flet's TabBarView pattern
            tabs_root = ft.Tabs(
                length=2,
                height=280,
                content=ft.Column(
                    controls=[
                        ft.TabBar(
                            tabs=[
                                ft.Tab(label="Left Columns"),
                                ft.Tab(label="Right Columns"),
                            ]
                        ),
                        ft.TabBarView(
                            height=220,
                            controls=[
                                left_col_layout,
                                right_col_layout,
                            ],
                        ),
                    ],
                ),
            )

            def save_cross_join_config(e):
                from core.schemas.transform_schema import CrossJoinInput, JoinInputs
                from core.schemas.input_schema import NodeCrossJoin
                
                left_selects = []
                for old_n, k_switch, r_tf in left_rows:
                    left_selects.append(SelectInput(
                        old_name=old_n,
                        new_name=r_tf.value.strip() or old_n,
                        keep=k_switch.value,
                    ))

                right_selects = []
                for old_n, k_switch, r_tf in right_rows:
                    right_selects.append(SelectInput(
                        old_name=old_n,
                        new_name=r_tf.value.strip() or old_n,
                        keep=k_switch.value,
                    ))

                cj_val = CrossJoinInput(
                    left_select=JoinInputs(renames=left_selects),
                    right_select=JoinInputs(renames=right_selects),
                )
                node.setting_input = NodeCrossJoin(
                    flow_id=self.active_flow_id,
                    node_id=node.node_id,
                    cross_join_input=cj_val,
                )
                try:
                    self.flow_ref.add_cross_join(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Cross Join configured successfully!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Cross Join",
                on_click=save_cross_join_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend([
                ft.Text("Cross Join Column Selection", weight=ft.FontWeight.BOLD),
                tabs_root,
                ft.Row([save_btn], alignment=ft.MainAxisAlignment.END)
            ])

        elif node.node_type == "cloud_storage_reader":
            # Dedicated Cloud Storage Reader UI (was falling through to the
            # broken generic fallback, which rendered/saved the nested
            # CloudStorageReadSettings object as a raw string). Mirrors the
            # existing database_reader pattern: reuses the already-existing
            # get_all_cloud_connections_interface() used by CloudConnectionView.
            from core.database.connection import get_db_context
            from core.dataryx.database_connection_manager.db_connections import (
                get_all_cloud_connections_interface,
            )
            from core.schemas.cloud_storage_schemas import CloudStorageReadSettings

            user_id = (
                auth_service.user_info.get("id", 1) if auth_service.user_info else 1
            )
            with get_db_context() as db:
                saved_conns = get_all_cloud_connections_interface(db, user_id)

            conn_options = [ft.dropdown.Option(c.connection_name) for c in saved_conns]

            existing = getattr(node.setting_input, "cloud_storage_settings", None)
            if not isinstance(existing, CloudStorageReadSettings):
                existing = None

            auth_mode_dd = ft.Dropdown(
                label="Auth Mode",
                options=[
                    ft.dropdown.Option("access_key"),
                    ft.dropdown.Option("iam_role"),
                    ft.dropdown.Option("service_principal"),
                    ft.dropdown.Option("managed_identity"),
                    ft.dropdown.Option("sas_token"),
                    ft.dropdown.Option("aws-cli"),
                    ft.dropdown.Option("env_vars"),
                ],
                value=(existing.auth_mode if existing else "aws-cli"),
                height=44,
                text_size=13,
            )
            conn_dropdown = ft.Dropdown(
                label="Saved Connection (not needed for AWS CLI / Env Vars)",
                options=conn_options,
                value=(existing.connection_name if existing else None),
                height=44,
                text_size=13,
            )
            resource_path_input = ft.TextField(
                label="Resource Path (e.g. s3://bucket/path/to/file.csv)",
                value=(existing.resource_path if existing else ""),
                height=44,
                text_size=13,
            )
            scan_mode_dd = ft.Dropdown(
                label="Scan Mode",
                options=[
                    ft.dropdown.Option("single_file"),
                    ft.dropdown.Option("directory"),
                ],
                value=(existing.scan_mode if existing else "single_file"),
                height=44,
                text_size=13,
            )
            file_format_dd = ft.Dropdown(
                label="File Format",
                options=[
                    ft.dropdown.Option("csv"),
                    ft.dropdown.Option("parquet"),
                    ft.dropdown.Option("json"),
                    ft.dropdown.Option("delta"),
                    ft.dropdown.Option("iceberg"),
                ],
                value=(existing.file_format if existing else "parquet"),
                height=44,
                text_size=13,
            )

            csv_header_switch = ft.Switch(
                label="CSV has header row",
                value=(existing.csv_has_header if existing else True) or False,
            )
            csv_delimiter_input = ft.TextField(
                label="CSV Delimiter",
                value=(existing.csv_delimiter if existing else ",") or ",",
                height=44,
                text_size=13,
            )
            csv_encoding_input = ft.TextField(
                label="CSV Encoding",
                value=(existing.csv_encoding if existing else "utf8") or "utf8",
                height=44,
                text_size=13,
            )
            csv_options_col = ft.Column(
                controls=[csv_header_switch, csv_delimiter_input, csv_encoding_input],
                visible=(file_format_dd.value == "csv"),
                spacing=8,
            )

            def toggle_reader_format(e):
                csv_options_col.visible = file_format_dd.value == "csv"
                csv_options_col.update()

            file_format_dd.on_select = toggle_reader_format

            def save_cloud_reader_config(e):
                if not resource_path_input.value.strip():
                    self.show_dialog("Error", "Resource Path is required.")
                    return
                if auth_mode_dd.value not in ("aws-cli", "env_vars") and not conn_dropdown.value:
                    self.show_dialog(
                        "Error",
                        "Please select a saved connection, or switch Auth Mode to AWS CLI / Env Vars.",
                    )
                    return

                node.setting_input.cloud_storage_settings = CloudStorageReadSettings(
                    auth_mode=auth_mode_dd.value,
                    connection_name=conn_dropdown.value,
                    resource_path=resource_path_input.value.strip(),
                    scan_mode=scan_mode_dd.value,
                    file_format=file_format_dd.value,
                    csv_has_header=csv_header_switch.value,
                    csv_delimiter=csv_delimiter_input.value or ",",
                    csv_encoding=csv_encoding_input.value or "utf8",
                )
                if node.setting_input.user_id is None:
                    node.setting_input.user_id = user_id

                try:
                    self.flow_ref.add_cloud_storage_reader(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Cloud Storage Reader settings saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Cloud Storage Reader Settings",
                on_click=save_cloud_reader_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend([
                auth_mode_dd,
                conn_dropdown,
                resource_path_input,
                scan_mode_dd,
                file_format_dd,
                csv_options_col,
                save_btn,
            ])

        elif node.node_type == "cloud_storage_writer":
            # Dedicated Cloud Storage Writer UI (same fix as the reader above,
            # mirroring the existing database_writer pattern).
            from core.database.connection import get_db_context
            from core.dataryx.database_connection_manager.db_connections import (
                get_all_cloud_connections_interface,
            )
            from core.schemas.cloud_storage_schemas import CloudStorageWriteSettings

            user_id = (
                auth_service.user_info.get("id", 1) if auth_service.user_info else 1
            )
            with get_db_context() as db:
                saved_conns = get_all_cloud_connections_interface(db, user_id)

            conn_options = [ft.dropdown.Option(c.connection_name) for c in saved_conns]

            existing = getattr(node.setting_input, "cloud_storage_settings", None)
            if not isinstance(existing, CloudStorageWriteSettings):
                existing = None

            auth_mode_dd = ft.Dropdown(
                label="Auth Mode",
                options=[
                    ft.dropdown.Option("access_key"),
                    ft.dropdown.Option("iam_role"),
                    ft.dropdown.Option("service_principal"),
                    ft.dropdown.Option("managed_identity"),
                    ft.dropdown.Option("sas_token"),
                    ft.dropdown.Option("aws-cli"),
                    ft.dropdown.Option("env_vars"),
                ],
                value=(existing.auth_mode if existing else "aws-cli"),
                height=44,
                text_size=13,
            )
            conn_dropdown = ft.Dropdown(
                label="Saved Connection (not needed for AWS CLI / Env Vars)",
                options=conn_options,
                value=(existing.connection_name if existing else None),
                height=44,
                text_size=13,
            )
            resource_path_input = ft.TextField(
                label="Resource Path (e.g. s3://bucket/path/to/file.csv)",
                value=(existing.resource_path if existing else ""),
                height=44,
                text_size=13,
            )
            write_mode_dd = ft.Dropdown(
                label="Write Mode",
                options=[
                    ft.dropdown.Option("overwrite"),
                    ft.dropdown.Option("append"),
                ],
                value=(existing.write_mode if existing else "overwrite"),
                height=44,
                text_size=13,
            )
            file_format_dd = ft.Dropdown(
                label="File Format",
                options=[
                    ft.dropdown.Option("csv"),
                    ft.dropdown.Option("parquet"),
                    ft.dropdown.Option("json"),
                    ft.dropdown.Option("delta"),
                ],
                value=(existing.file_format if existing else "parquet"),
                height=44,
                text_size=13,
            )

            parquet_compression_dd = ft.Dropdown(
                label="Parquet Compression",
                options=[
                    ft.dropdown.Option("snappy"),
                    ft.dropdown.Option("gzip"),
                    ft.dropdown.Option("brotli"),
                    ft.dropdown.Option("lz4"),
                    ft.dropdown.Option("zstd"),
                ],
                value=(existing.parquet_compression if existing else "snappy"),
                height=44,
                text_size=13,
                visible=(file_format_dd.value == "parquet"),
            )
            csv_delimiter_input = ft.TextField(
                label="CSV Delimiter",
                value=(existing.csv_delimiter if existing else ",") or ",",
                height=44,
                text_size=13,
                visible=(file_format_dd.value == "csv"),
            )
            csv_encoding_input = ft.TextField(
                label="CSV Encoding",
                value=(existing.csv_encoding if existing else "utf8") or "utf8",
                height=44,
                text_size=13,
                visible=(file_format_dd.value == "csv"),
            )

            def toggle_writer_format(e):
                parquet_compression_dd.visible = file_format_dd.value == "parquet"
                csv_delimiter_input.visible = file_format_dd.value == "csv"
                csv_encoding_input.visible = file_format_dd.value == "csv"
                parquet_compression_dd.update()
                csv_delimiter_input.update()
                csv_encoding_input.update()

            file_format_dd.on_select = toggle_writer_format

            def save_cloud_writer_config(e):
                if not resource_path_input.value.strip():
                    self.show_dialog("Error", "Resource Path is required.")
                    return
                if auth_mode_dd.value not in ("aws-cli", "env_vars") and not conn_dropdown.value:
                    self.show_dialog(
                        "Error",
                        "Please select a saved connection, or switch Auth Mode to AWS CLI / Env Vars.",
                    )
                    return

                node.setting_input.cloud_storage_settings = CloudStorageWriteSettings(
                    auth_mode=auth_mode_dd.value,
                    connection_name=conn_dropdown.value,
                    resource_path=resource_path_input.value.strip(),
                    write_mode=write_mode_dd.value,
                    file_format=file_format_dd.value,
                    parquet_compression=parquet_compression_dd.value or "snappy",
                    csv_delimiter=csv_delimiter_input.value or ",",
                    csv_encoding=csv_encoding_input.value or "utf8",
                )
                if node.setting_input.user_id is None:
                    node.setting_input.user_id = user_id

                try:
                    self.flow_ref.add_cloud_storage_writer(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Cloud Storage Writer settings saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Cloud Storage Writer Settings",
                on_click=save_cloud_writer_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend([
                auth_mode_dd,
                conn_dropdown,
                resource_path_input,
                write_mode_dd,
                file_format_dd,
                parquet_compression_dd,
                csv_delimiter_input,
                csv_encoding_input,
                save_btn,
            ])

        elif node.node_type == "external_source":
            # External Source is driven by a dynamic plugin registry (each
            # registered source has its own settings shape keyed off
            # `identifier`), and the original app itself marks this node
            # "not production ready". Building a full dynamic settings UI is
            # out of scope here; this only replaces the broken generic
            # fallback (which rendered/saved the nested source_settings
            # object as a raw string, corrupting it) with a safe message.
            identifier = getattr(node.setting_input, "identifier", None)
            self.config_container.controls.append(
                ft.Text(
                    f"External Source ({identifier or 'not configured'}) is not "
                    "configurable from this UI yet. Configure it via a YAML flow "
                    "import instead.",
                    color=ft.Colors.GREY_400,
                    italic=True,
                )
            )

        elif node.node_type == "record_id":
            # Dedicated Record ID UI (was falling through to the broken generic
            # fallback, which rendered/saved the nested RecordIdInput object as
            # a raw string instead of proper controls).
            from core.schemas.transform_schema import RecordIdInput

            t = get_theme(self.main_page)
            existing = getattr(node.setting_input, "record_id_input", None)
            if not isinstance(existing, RecordIdInput):
                existing = RecordIdInput()

            offset_input = ft.TextField(
                label="Offset (starting value)",
                value=str(existing.offset),
                keyboard_type=ft.KeyboardType.NUMBER,
                height=44,
                text_size=13,
            )
            output_name_input = ft.TextField(
                label="Output column name",
                value=existing.output_column_name or "record_id",
                height=44,
                text_size=13,
            )

            group_by_checkboxes = [
                ft.Checkbox(
                    label=col,
                    value=(col in (existing.group_by_columns or [])),
                    label_style=ft.TextStyle(color=t.TEXT_PRIMARY, size=12),
                )
                for col in incoming_cols
            ]
            group_by_col_container = ft.Column(
                controls=[
                    ft.Text(
                        "Group by columns:",
                        color=ft.Colors.GREY_400,
                        size=12,
                    ),
                    ft.Column(
                        controls=group_by_checkboxes,
                        scroll=ft.ScrollMode.AUTO,
                        height=160,
                    ),
                ],
                visible=bool(existing.group_by),
                spacing=4,
            )

            def toggle_group_by(e):
                group_by_col_container.visible = group_by_switch.value
                group_by_col_container.update()

            group_by_switch = ft.Switch(
                label="Assign record ID by group",
                value=bool(existing.group_by),
                on_change=toggle_group_by,
            )

            def save_record_id_config(e):
                try:
                    offset_val = int(offset_input.value or 1)
                except ValueError:
                    offset_val = 1

                selected_group_cols = [
                    cb.label for cb in group_by_checkboxes if cb.value
                ]

                node.setting_input.record_id_input = RecordIdInput(
                    output_column_name=output_name_input.value.strip() or "record_id",
                    offset=offset_val,
                    group_by=group_by_switch.value,
                    group_by_columns=selected_group_cols,
                )
                try:
                    self.flow_ref.add_record_id(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Record ID settings saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Record ID Settings",
                on_click=save_record_id_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend([
                offset_input,
                output_name_input,
                group_by_switch,
                group_by_col_container,
                save_btn,
            ])

        elif node.node_type == "text_to_rows":
            # Dedicated Text to Rows UI (was falling through to the broken
            # generic fallback, which rendered/saved the nested
            # TextToRowsInput object as a raw string instead of proper controls).
            from core.schemas.transform_schema import TextToRowsInput

            existing = getattr(node.setting_input, "text_to_rows_input", None)
            if not isinstance(existing, TextToRowsInput):
                existing = None

            col_to_split_dd = ft.Dropdown(
                label="Column to split",
                options=[ft.dropdown.Option(c) for c in incoming_cols],
                value=(
                    existing.column_to_split
                    if existing and existing.column_to_split in incoming_cols
                    else None
                ),
                height=44,
                text_size=13,
            )

            is_fixed_initial = not existing or existing.split_by_fixed_value

            fixed_value_input = ft.TextField(
                label="Split by value",
                value=(
                    existing.split_fixed_value
                    if existing and existing.split_fixed_value
                    else ","
                ),
                height=44,
                text_size=13,
                visible=is_fixed_initial,
            )
            split_col_dd = ft.Dropdown(
                label="Column that contains the value to split",
                options=[ft.dropdown.Option(c) for c in incoming_cols],
                value=(
                    existing.split_by_column
                    if existing and existing.split_by_column in incoming_cols
                    else None
                ),
                height=44,
                text_size=13,
                visible=not is_fixed_initial,
            )

            def toggle_split_mode(e):
                is_fixed = split_mode_group.value == "fixed"
                fixed_value_input.visible = is_fixed
                split_col_dd.visible = not is_fixed
                fixed_value_input.update()
                split_col_dd.update()

            split_mode_group = ft.RadioGroup(
                value="fixed" if is_fixed_initial else "column",
                on_change=toggle_split_mode,
                content=ft.Row(
                    [
                        ft.Radio(value="fixed", label="Split by a fixed value"),
                        ft.Radio(value="column", label="Split by a column"),
                    ]
                ),
            )

            output_name_input = ft.TextField(
                label="Output column name",
                hint_text="Enter output column name",
                value=(
                    existing.output_column_name
                    if existing and existing.output_column_name
                    else ""
                ),
                height=44,
                text_size=13,
            )

            def save_text_to_rows_config(e):
                if not col_to_split_dd.value:
                    self.show_dialog("Error", "Please select a column to split.")
                    return

                is_fixed = split_mode_group.value == "fixed"
                if is_fixed and not (fixed_value_input.value or "").strip():
                    self.show_dialog("Error", "Please enter a value to split by.")
                    return
                if not is_fixed and not split_col_dd.value:
                    self.show_dialog(
                        "Error", "Please select the column containing the split value."
                    )
                    return

                node.setting_input.text_to_rows_input = TextToRowsInput(
                    column_to_split=col_to_split_dd.value,
                    output_column_name=output_name_input.value.strip() or None,
                    split_by_fixed_value=is_fixed,
                    split_fixed_value=fixed_value_input.value if is_fixed else None,
                    split_by_column=split_col_dd.value if not is_fixed else None,
                )
                try:
                    self.flow_ref.add_text_to_rows(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Text to Rows settings saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Text to Rows Settings",
                on_click=save_text_to_rows_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend([
                col_to_split_dd,
                ft.Text("Split method", weight=ft.FontWeight.BOLD),
                split_mode_group,
                fixed_value_input,
                split_col_dd,
                output_name_input,
                save_btn,
            ])

        elif node.node_type == "union":
            # Union has no user-configurable settings in the original app either
            # (it concatenates all inputs). It was previously falling through to
            # the generic fallback, which rendered the nested union_input object
            # as a raw string and would corrupt it if "saved".
            self.config_container.controls.append(
                ft.Text(
                    "Union combines multiple tables into one. This step has no "
                    "settings to configure.",
                    color=ft.Colors.GREY_400,
                    italic=True,
                )
            )

        elif node.node_type == "graph_solver":
            # Dedicated Graph Solver UI (was falling through to the broken
            # generic fallback, which rendered/saved the nested
            # GraphSolverInput object as a raw string instead of proper controls).
            from core.schemas.transform_schema import GraphSolverInput

            existing = getattr(node.setting_input, "graph_solver_input", None)
            if not isinstance(existing, GraphSolverInput):
                existing = None

            col_from_dd = ft.Dropdown(
                label="From column",
                options=[ft.dropdown.Option(c) for c in incoming_cols],
                value=(
                    existing.col_from
                    if existing and existing.col_from in incoming_cols
                    else None
                ),
                height=44,
                text_size=13,
            )
            col_to_dd = ft.Dropdown(
                label="To column",
                options=[ft.dropdown.Option(c) for c in incoming_cols],
                value=(
                    existing.col_to
                    if existing and existing.col_to in incoming_cols
                    else None
                ),
                height=44,
                text_size=13,
            )
            output_name_input = ft.TextField(
                label="Output column name",
                value=(existing.output_column_name if existing else "graph_group")
                or "graph_group",
                height=44,
                text_size=13,
            )

            def save_graph_solver_config(e):
                if not col_from_dd.value or not col_to_dd.value:
                    self.show_dialog(
                        "Error", "Please select both a From column and a To column."
                    )
                    return

                node.setting_input.graph_solver_input = GraphSolverInput(
                    col_from=col_from_dd.value,
                    col_to=col_to_dd.value,
                    output_column_name=output_name_input.value.strip() or "graph_group",
                )
                try:
                    self.flow_ref.add_graph_solver(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Graph Solver settings saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Graph Solver Settings",
                on_click=save_graph_solver_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend([
                col_from_dd,
                col_to_dd,
                output_name_input,
                save_btn,
            ])

        elif node.node_type == "unpivot":
            # Dedicated Unpivot UI (was falling through to the broken generic
            # fallback, which rendered/saved the nested UnpivotInput object as
            # a raw string instead of proper controls).
            from core.schemas.transform_schema import UnpivotInput

            t = get_theme(self.main_page)
            existing = getattr(node.setting_input, "unpivot_input", None)
            if not isinstance(existing, UnpivotInput):
                existing = UnpivotInput()

            index_checkboxes = [
                ft.Checkbox(
                    label=col,
                    value=(col in (existing.index_columns or [])),
                    label_style=ft.TextStyle(color=t.TEXT_PRIMARY, size=12),
                )
                for col in incoming_cols
            ]
            value_checkboxes = [
                ft.Checkbox(
                    label=col,
                    value=(col in (existing.value_columns or [])),
                    label_style=ft.TextStyle(color=t.TEXT_PRIMARY, size=12),
                )
                for col in incoming_cols
            ]

            is_column_mode_initial = existing.data_type_selector_mode != "data_type"

            value_columns_container = ft.Column(
                controls=[
                    ft.Text("Columns to unpivot:", color=ft.Colors.GREY_400, size=12),
                    ft.Column(
                        controls=value_checkboxes,
                        scroll=ft.ScrollMode.AUTO,
                        height=140,
                    ),
                ],
                visible=is_column_mode_initial,
                spacing=4,
            )
            data_type_dd = ft.Dropdown(
                label="Select columns by data type",
                options=[
                    ft.dropdown.Option("all"),
                    ft.dropdown.Option("numeric"),
                    ft.dropdown.Option("string"),
                    ft.dropdown.Option("date"),
                    ft.dropdown.Option("float"),
                ],
                value=existing.data_type_selector or "all",
                height=44,
                text_size=13,
                visible=not is_column_mode_initial,
            )

            def toggle_unpivot_mode(e):
                is_column = mode_switch.value
                value_columns_container.visible = is_column
                data_type_dd.visible = not is_column
                value_columns_container.update()
                data_type_dd.update()

            mode_switch = ft.Switch(
                label="Select columns to unpivot manually (off = select by data type)",
                value=is_column_mode_initial,
                on_change=toggle_unpivot_mode,
            )

            def save_unpivot_config(e):
                is_column_mode = mode_switch.value
                selected_index_cols = [cb.label for cb in index_checkboxes if cb.value]
                selected_value_cols = (
                    [cb.label for cb in value_checkboxes if cb.value]
                    if is_column_mode
                    else []
                )

                node.setting_input.unpivot_input = UnpivotInput(
                    index_columns=selected_index_cols,
                    value_columns=selected_value_cols,
                    data_type_selector=(
                        None if is_column_mode else (data_type_dd.value or "all")
                    ),
                    data_type_selector_mode="column" if is_column_mode else "data_type",
                )
                try:
                    self.flow_ref.add_unpivot(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Unpivot settings saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Unpivot Settings",
                on_click=save_unpivot_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend([
                ft.Text("Index columns (kept as-is):", color=ft.Colors.GREY_400, size=12),
                ft.Column(
                    controls=index_checkboxes,
                    scroll=ft.ScrollMode.AUTO,
                    height=140,
                ),
                mode_switch,
                value_columns_container,
                data_type_dd,
                save_btn,
            ])

        else:
            # Dynamic Generic Schema Fallback Form Builder (handles all remaining 15+ nodes!)
            setting_input = getattr(node, "setting_input", None)
            if setting_input and hasattr(setting_input, "model_fields"):
                generic_controls = {}
                fields = setting_input.model_fields

                # Filter out system parameters
                filtered_fields = [
                    f
                    for f in fields.keys()
                    if f
                    not in [
                        "flow_id",
                        "node_id",
                        "cache_results",
                        "pos_x",
                        "pos_y",
                        "is_setup",
                        "description",
                        "node_reference",
                        "user_id",
                        "is_flow_output",
                        "is_user_defined",
                        "output_field_config",
                        "depending_on_id",
                        "depending_on_ids",
                    ]
                ]

                if not filtered_fields:
                    self.config_container.controls.append(
                        ft.Text(
                            "This step runs with standard automated values.",
                            color=ft.Colors.GREY_400,
                            italic=True,
                        )
                    )
                    return

                for f in filtered_fields:
                    field_meta = fields[f]
                    field_type = field_meta.annotation
                    current_val = getattr(setting_input, f, None)

                    if field_type == bool:
                        sw = ft.Switch(
                            label=f"Enable {f.replace('_', ' ').capitalize()}",
                            value=bool(current_val),
                        )
                        generic_controls[f] = sw
                        self.config_container.controls.append(sw)
                    elif f in [
                        "sample_size",
                        "size",
                        "limit",
                        "starting_from_line",
                        "infer_schema_length",
                    ]:
                        tf = ft.TextField(
                            label=f.replace("_", " ").capitalize(),
                            value=str(current_val if current_val is not None else 1000),
                            keyboard_type=ft.KeyboardType.NUMBER,
                            height=44,
                            text_size=13,
                        )
                        generic_controls[f] = tf
                        self.config_container.controls.append(tf)
                    else:
                        # Fallback simple string input
                        tf = ft.TextField(
                            label=f.replace("_", " ").capitalize(),
                            value=str(current_val if current_val is not None else ""),
                            height=44,
                            text_size=13,
                        )
                        generic_controls[f] = tf
                        self.config_container.controls.append(tf)

                def save_generic_config(e):
                    for f_name, ctrl in generic_controls.items():
                        if isinstance(ctrl, ft.Switch):
                            setattr(node.setting_input, f_name, ctrl.value)
                        elif isinstance(ctrl, ft.TextField):
                            f_type = fields[f_name].annotation
                            if f_type == int:
                                try:
                                    setattr(
                                        node.setting_input, f_name, int(ctrl.value or 0)
                                    )
                                except:
                                    pass
                            elif f_type == float:
                                try:
                                    setattr(
                                        node.setting_input,
                                        f_name,
                                        float(ctrl.value or 0.0),
                                    )
                                except:
                                    pass
                            else:
                                setattr(node.setting_input, f_name, ctrl.value)

                    try:
                        add_func = getattr(self.flow_ref, "add_" + node.node_type)
                        add_func(node.setting_input)
                        self.save_active_flow()
                        self.show_dialog("Success", "Settings updated!")
                        self.update_preview_ui()
                        self.update()
                    except Exception as ex:
                        self.show_dialog("Error saving", str(ex))

                save_btn = ft.Button(
                    "Save Settings",
                    on_click=save_generic_config,
                    bgcolor=ft.Colors.BLUE_600,
                    color=ft.Colors.WHITE,
                )
                self.config_container.controls.append(save_btn)
            else:
                self.config_container.controls.append(
                    ft.Text(
                        "This node configuration can be automated via yaml schema imports.",
                        color=ft.Colors.GREY_400,
                        italic=True,
                    )
                )

    def _show_preview_placeholder(self):
        """Cheap, no-compute placeholder shown on node selection. Does not
        call get_table_example() -- use the "Refresh Preview" button
        (update_preview_ui) to actually load/recompute a step's data.
        """
        self.preview_table.columns.clear()
        self.preview_table.rows.clear()

        if self.selected_node_id is None or not self.flow_ref:
            self.preview_table.columns.append(ft.DataColumn(ft.Text("No active step")))
            return

        self.preview_table.columns.append(
            ft.DataColumn(ft.Text("Preview not loaded"))
        )
        self.preview_table.rows.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(
                        ft.Text(
                            "Click 'Refresh Preview' above to load data for this step."
                        )
                    )
                ]
            )
        )

    @staticmethod
    def _format_preview_value(val) -> str:
        """Format a preview-table cell value, adding thousands separators to
        int/whole-float values (e.g. 124263680 -> 124,263,680) for
        readability -- bool is checked first since bool is a subclass of int
        in Python and shouldn't be formatted as a number.
        """
        if isinstance(val, bool):
            return str(val)
        if isinstance(val, int):
            return f"{val:,}"
        if isinstance(val, float):
            return f"{val:,}"
        return str(val)

    def _copy_value_to_clipboard(self, value):
        """Returns a click handler that copies `value` to the clipboard,
        for the preview table's right-click-to-copy cells."""

        async def _do_copy(e=None):
            await self.main_page.clipboard.set(self._format_preview_value(value))
            snack = ft.SnackBar(
                content=ft.Text("✓ Copied to clipboard!", color=ft.Colors.WHITE),
                bgcolor=ft.Colors.GREEN_800,
                open=True,
            )
            self.main_page.overlay.append(snack)
            self.main_page.update()

        return _do_copy

    def _make_preview_cell(self, val) -> ft.DataCell:
        """Builds a preview-table cell that copies its value to the
        clipboard on right-click (client-requested: "ability to copy
        values by right click on the table")."""
        return ft.DataCell(
            ft.GestureDetector(
                content=ft.Text(self._format_preview_value(val)),
                on_secondary_tap=self._copy_value_to_clipboard(val),
            )
        )

    def update_preview_ui(self):
        # Reset the Data Preview panel's vertical scroll to the top on
        # every refresh -- fire-and-forget via run_task since this method
        # is sync but Column.scroll_to() is async (same pattern used for
        # the DB connection test in database_view.py).
        if self.main_page and getattr(self, "_preview_scroll_col", None):
            self.main_page.run_task(
                self._preview_scroll_col.scroll_to, offset=0, duration=0
            )

        self.preview_table.columns.clear()
        self.preview_table.rows.clear()

        if self.selected_node_id is None or not self.flow_ref:
            self.preview_table.columns.append(ft.DataColumn(ft.Text("No active step")))
            return

        try:
            node = self.flow_ref.get_node(self.selected_node_id)
            if not node:
                self.preview_table.columns.append(
                    ft.DataColumn(ft.Text("Node not found"))
                )
                return

            _dark = self.main_page and is_dark(self.main_page)
            col_color = ft.Colors.BLUE_800 if not _dark else ft.Colors.BLUE_200

            # 1. Attempt to load real executed Polars table sample
            table_ex = node.get_table_example(include_data=True)
            if table_ex and table_ex.columns and table_ex.data:
                for col_name in table_ex.columns:
                    self.preview_table.columns.append(
                        ft.DataColumn(
                            ft.Text(
                                col_name,
                                color=col_color,
                                weight=ft.FontWeight.BOLD,
                            )
                        )
                    )
                for row_dict in table_ex.data:
                    cells = [
                        self._make_preview_cell(row_dict.get(col, ""))
                        for col in table_ex.columns
                    ]
                    self.preview_table.rows.append(ft.DataRow(cells=cells))
                return

            # 2. If no executed results yet, check if it is a manual_input node and render configured input data
            if (
                node.node_type == "manual_input"
                and getattr(node, "setting_input", None)
                and getattr(node.setting_input, "raw_data_format", None)
            ):
                raw = node.setting_input.raw_data_format
                col_names = [c.name for c in raw.columns]
                for col in col_names:
                    self.preview_table.columns.append(
                        ft.DataColumn(
                            ft.Text(col, color=col_color, weight=ft.FontWeight.BOLD)
                        )
                    )
                if raw.data and len(raw.data) > 0:
                    num_rows = len(raw.data[0])
                    for ri in range(num_rows):
                        cells = []
                        for ci in range(len(col_names)):
                            val = raw.data[ci][ri] if ri < len(raw.data[ci]) else ""
                            cells.append(self._make_preview_cell(val))
                        self.preview_table.rows.append(ft.DataRow(cells=cells))
                return

            # 3. If file read node, show initial file preview
            if (
                node.node_type in ["read", "read_csv"]
                and hasattr(node.setting_input, "received_file")
                and node.setting_input.received_file
                and node.setting_input.received_file.path
            ):
                rf = node.setting_input.received_file
                if rf.file_type == "csv":
                    delimiter = ","
                    if rf.table_settings:
                        delimiter = getattr(rf.table_settings, "delimiter", ",")
                    # This is just a quick "peek at the file before you've hit
                    # Run" preview -- without truncate_ragged_lines, a single row
                    # with a different field count than the header (e.g. an
                    # unescaped delimiter character inside a text field, common
                    # in large real-world exports) throws "found more fields
                    # than defined in Schema" and blanks the preview, even
                    # though the real read path (create_from_path_csv) already
                    # tolerates exactly this and loads the file fine on Run.
                    df = pl.read_csv(
                        rf.path,
                        separator=delimiter,
                        n_rows=10,
                        ignore_errors=True,
                        truncate_ragged_lines=True,
                    )
                else:
                    # Non-CSV formats (Excel, JSON, Parquet) were previously
                    # ALSO run through pl.read_csv() above -- treating a
                    # binary .xlsx file's raw bytes as UTF-8 CSV text threw
                    # "invalid utf-8 sequence" every time, even though the
                    # actual Run path reads these formats correctly. Route
                    # through the same reader Run uses instead.
                    from core.dataryx.flow_data_engine.flow_data_engine import (
                        FlowDataEngine,
                    )

                    fde = FlowDataEngine.create_from_path(rf)
                    df = fde.data_frame.head(10).collect()
                for col_name in df.columns:
                    self.preview_table.columns.append(
                        ft.DataColumn(
                            ft.Text(
                                col_name,
                                color=col_color,
                                weight=ft.FontWeight.BOLD,
                            )
                        )
                    )
                for row_data in df.rows():
                    cells = [self._make_preview_cell(val) for val in row_data]
                    self.preview_table.rows.append(ft.DataRow(cells=cells))
                return

            # Fallback mock schema
            self.preview_table.columns.append(
                ft.DataColumn(ft.Text("Preview Unavailable"))
            )
            self.preview_table.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(
                            ft.Text("Run the pipeline to generate preview data.")
                        )
                    ]
                )
            )

        except Exception as e:
            err_msg = str(e)
            if "Operation not permitted" in err_msg or (hasattr(e, "errno") and e.errno == 1):
                err_msg = (
                    "macOS blocked access to this file (Operation not permitted). "
                    "Please move the file out of your Downloads/Desktop/Documents folder, "
                    "or grant Terminal/VS Code permission to access these folders in "
                    "macOS System Settings > Privacy & Security > Files and Folders."
                )
            else:
                err_msg = f"Could not load data: {err_msg}"

            self.preview_table.columns.append(
                ft.DataColumn(ft.Text("Error Rendering Preview"))
            )
            self.preview_table.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(
                            ft.Text(
                                err_msg,
                                color=ft.Colors.RED_400,
                            )
                        )
                    ]
                )
            )

    async def run_pipeline(self, e):
        if not self.flow_ref:
            return

        # Check hardware license / trial validity before allowing execution
        is_licensed, err = check_license()
        if not is_licensed:
            self.show_dialog(
                "License Expired",
                "Your 3-month free trial has expired.\n\n"
                "To continue using Dataryx, please:\n"
                "1. Go to the License page (key icon in the sidebar)\n"
                "2. Copy your Hardware ID\n"
                "3. Send it to license@dataryxke.com\n\n"
                "We will generate and send you an activation key.",
            )
            return

        # Execute the flow graph locally in-memory. run_graph() is a
        # synchronous, potentially long-running (seconds-to-minutes on big
        # files) Polars computation -- calling it directly here would block
        # Flet's single event loop for that whole duration, freezing the
        # entire UI (no spinner, no other clicks, app looks "hung"). Running
        # it via asyncio.to_thread offloads the actual work to a worker
        # thread so the event loop stays responsive.
        original_icon = self.run_btn.icon
        self.run_btn.disabled = True
        self.run_btn.icon = ft.Icons.HOURGLASS_TOP_ROUNDED
        self.run_btn.update()
        try:
            self.flow_ref.flow_settings.execution_mode = "Development"
            self.save_active_flow()
            run_info = await asyncio.to_thread(self.flow_ref.run_graph)
            self.update_preview_ui()
            self.update()

            # Check actual run results — not just whether run_graph() raised
            if run_info is not None:
                failed_nodes = [
                    nr for nr in run_info.node_step_result if not nr.success
                ]
                skipped_nodes = [
                    nr
                    for nr in run_info.node_step_result
                    if hasattr(nr, "skipped") and nr.skipped
                ]

                # Client-requested: show how long the run actually took.
                # RunInformation already tracks start_time/end_time (used
                # internally) but nothing surfaced it in the UI before.
                duration_line = ""
                if run_info.start_time and run_info.end_time:
                    total_seconds = (
                        run_info.end_time - run_info.start_time
                    ).total_seconds()
                    if total_seconds < 60:
                        duration_str = f"{total_seconds:.1f}s"
                    else:
                        minutes, seconds = divmod(int(total_seconds), 60)
                        duration_str = f"{minutes}m {seconds}s"
                    duration_line = f"\n\nCompleted in {duration_str}."

                if failed_nodes:
                    failed_ids = ", ".join(str(nr.node_id) for nr in failed_nodes)
                    self.show_dialog(
                        "⚠ Pipeline Completed with Errors",
                        f"Execution finished but {len(failed_nodes)} node(s) failed: [{failed_ids}].\n\n"
                        "Please check the configuration of the highlighted node(s)."
                        f"{duration_line}",
                    )
                else:
                    self.show_dialog(
                        "Pipeline Completed",
                        f"Dataryx executed the pipeline successfully!{duration_line}",
                    )
            else:
                self.show_dialog(
                    "Pipeline Completed",
                    "Dataryx executed the pipeline successfully!",
                )
        except Exception as ex:
            self.show_dialog(
                "Execution Error", f"Failed to execute pipeline: {str(ex)}"
            )
        finally:
            self.run_btn.disabled = False
            self.run_btn.icon = original_icon
            self.run_btn.update()

    def export_code(self, e):
        if not self.flow_ref:
            return
        self.show_code_export_dialog()

    def show_code_export_dialog(self):
        if not self.flow_ref:
            return

        t = get_theme(self.main_page)

        def format_python_code(code: str) -> str:
            try:
                import autopep8

                return autopep8.fix_code(code)
            except Exception:
                try:
                    import ast

                    return ast.unparse(ast.parse(code))
                except Exception:
                    # Fallback to manual clean formatter
                    lines = code.split("\n")
                    formatted_lines = []
                    indent_level = 0
                    for line in lines:
                        stripped = line.strip()
                        if not stripped:
                            formatted_lines.append("")
                            continue
                        if (
                            stripped.startswith("elif")
                            or stripped.startswith("else:")
                            or stripped.startswith("except ")
                            or stripped.startswith("finally:")
                        ):
                            indent_level = max(0, indent_level - 1)
                        base_indent = "    " * indent_level
                        formatted_lines.append(f"{base_indent}{stripped}")
                        if stripped.endswith(":") and not stripped.startswith("#"):
                            indent_level += 1
                    return "\n".join(formatted_lines)

        def _get_codes():
            try:
                raw_polars = export_flow_to_polars(self.flow_ref)
                polars_code = format_python_code(raw_polars)
            except Exception as ex:
                polars_code = f"# Error generating Polars code: {str(ex)}"

            dataryx_code = polars_code.replace(
                "import polars as pl", "import dataryx as ff"
            ).replace("pl.", "ff.")

            try:
                import yaml

                dataryx_data = self.flow_ref.get_dataryx_data()
                data = dataryx_data.model_dump(mode="json")
                project_yaml = yaml.dump(
                    data, default_flow_style=False, sort_keys=False, allow_unicode=True
                )
            except Exception as ex:
                project_yaml = f"# Error generating project YAML: {str(ex)}"

            return dataryx_code, polars_code, project_yaml

        dataryx_code, polars_code, project_yaml = _get_codes()

        # Textfield displaying the code
        code_tf = ft.TextField(
            value=dataryx_code,
            multiline=True,
            read_only=True,
            text_size=12,
            text_style=ft.TextStyle(font_family="Courier New"),
            expand=True,
            height=400,
            width=800,
        )

        # Manual tab switcher — ft.Tabs API differs across versions
        # "Polars" tab removed (client-requested) -- Dataryx code is derived
        # from it internally (see _get_codes), it's just no longer exposed
        # as its own tab.
        _selected_tab = [0]  # mutable ref
        tab_labels = ["Dataryx", "Project"]
        tab_contents = [dataryx_code, project_yaml]
        tab_btns: list[ft.TextButton] = []

        def _make_tab_style(active: bool) -> ft.ButtonStyle:
            return ft.ButtonStyle(
                color=ft.Colors.BLUE_400 if active else ft.Colors.GREY_500,
                overlay_color=ft.Colors.TRANSPARENT,
                padding=ft.Padding(left=12, top=6, right=12, bottom=6),
                side=ft.BorderSide(
                    width=0 if not active else 2,
                    color=ft.Colors.BLUE_400,
                ),
                shape=ft.RoundedRectangleBorder(radius=4),
            )

        def switch_tab(idx: int, e=None):
            _selected_tab[0] = idx
            code_tf.value = tab_contents[idx]
            for i, btn in enumerate(tab_btns):
                btn.style = _make_tab_style(i == idx)
            code_tf.update()
            for btn in tab_btns:
                btn.update()

        for i, lbl in enumerate(tab_labels):
            btn = ft.TextButton(
                lbl,
                style=_make_tab_style(i == 0),
                on_click=lambda e, idx=i: switch_tab(idx),
            )
            tab_btns.append(btn)

        tab_row = ft.Row(tab_btns, spacing=4)

        def handle_refresh(e):
            nonlocal dataryx_code, polars_code, project_yaml, tab_contents
            dataryx_code, polars_code, project_yaml = _get_codes()
            tab_contents = [dataryx_code, project_yaml]
            switch_tab(_selected_tab[0])

        async def handle_copy(e):
            await self.main_page.clipboard.set(code_tf.value)
            snack = ft.SnackBar(
                content=ft.Text("✓ Copied code to clipboard!", color=ft.Colors.WHITE),
                bgcolor=ft.Colors.GREEN_800,
                open=True,
            )
            self.main_page.overlay.append(snack)
            self.main_page.update()

        def close_dialog(e):
            self.main_page.pop_dialog()

        header_row = ft.Row(
            [
                ft.Text(
                    "Generated code",
                    size=16,
                    weight=ft.FontWeight.W_700,
                    color=t.TEXT_PRIMARY,
                ),
                ft.Container(width=16),
                tab_row,
                ft.IconButton(
                    icon=ft.Icons.REFRESH_ROUNDED,
                    icon_color=ft.Colors.BLUE_400,
                    tooltip="Refresh Code",
                    on_click=handle_refresh,
                ),
                ft.IconButton(
                    icon=ft.Icons.COPY_ROUNDED,
                    icon_color=ft.Colors.BLUE_400,
                    tooltip="Copy to Clipboard",
                    on_click=handle_copy,
                ),
                ft.IconButton(
                    icon=ft.Icons.CLOSE_ROUNDED,
                    icon_color=ft.Colors.GREY_400,
                    tooltip="Close",
                    on_click=close_dialog,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        dialog = ft.AlertDialog(
            modal=True,
            title=header_row,
            content=ft.Container(
                content=code_tf,
                width=850,
                height=450,
                padding=ft.Padding(left=4, top=4, right=4, bottom=4),
            ),
            bgcolor=t.BG_PAGE,
            shape=ft.RoundedRectangleBorder(radius=10),
        )

        self.main_page.show_dialog(dialog)

    def show_dialog(self, title: str, message: str, is_code: bool = False):
        content = (
            ft.TextField(
                value=message,
                multiline=True,
                read_only=True,
                text_size=12,
                width=600,
                height=350,
                text_style=ft.TextStyle(font_family="monospace"),
            )
            if is_code
            else ft.Text(message, size=14)
        )

        def close_dialog(e):
            self.main_page.pop_dialog()

        dialog = ft.AlertDialog(
            title=ft.Text(title),
            content=content,
            actions=[ft.TextButton("Close", on_click=close_dialog)],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.main_page.show_dialog(dialog)

    def save_active_flow(self):
        if (
            self.flow_ref
            and getattr(self.flow_ref, "flow_settings", None)
            and getattr(self.flow_ref.flow_settings, "path", None)
        ):
            try:
                self.flow_ref.save_flow(self.flow_ref.flow_settings.path)
            except Exception as e:
                print("Error saving flow to disk:", e)
