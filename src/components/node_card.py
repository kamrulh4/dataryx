import flet as ft
import os

NODE_FRIENDLY_NAMES = {
    "read": "Read Data",
    "read_csv": "Read CSV",
    "database_reader": "Database Reader",
    "cloud_storage_reader": "Cloud Storage",
    "manual_input": "Manual Input",
    "external_source": "External Source",
    "filter": "Filter Rows",
    "select": "Select Columns",
    "formula": "Formula",
    "sort": "Sort Data",
    "sample": "Take Sample",
    "unique": "Drop Duplicates",
    "text_to_rows": "Text to Rows",
    "record_id": "Add Record ID",
    "polars_code": "Polars Code",
    "window": "Window Function",
    "group_by": "Group By",
    "pivot": "Pivot Data",
    "unpivot": "Unpivot Data",
    "record_count": "Count Records",
    "join": "Join",
    "union": "Union",
    "fuzzy_match": "Fuzzy Match",
    "cross_join": "Cross Join",
    "graph_solver": "Graph Solver",
    "output": "Write Data",
    "database_writer": "DB Writer",
    "cloud_storage_writer": "Cloud Writer",
    "explore_data": "Explore Data",
}

# Category metadata: (accent_color, dark_header_bg, light_header_bg, icon, label)
NODE_CATEGORY = {
    # Input nodes
    "read": ("#4ADE80", "#166534", "#D1FAE5", ft.Icons.FOLDER_OPEN_ROUNDED, "Input"),
    "read_csv": (
        "#4ADE80",
        "#166534",
        "#D1FAE5",
        ft.Icons.TABLE_CHART_ROUNDED,
        "Input",
    ),
    "database_reader": (
        "#4ADE80",
        "#166534",
        "#D1FAE5",
        ft.Icons.STORAGE_ROUNDED,
        "Input",
    ),
    "cloud_storage_reader": (
        "#4ADE80",
        "#166534",
        "#D1FAE5",
        ft.Icons.CLOUD_DOWNLOAD_ROUNDED,
        "Input",
    ),
    "window": (
        "#60A5FA",
        "#1E3A8A",
        "#DBEAFE",
        ft.Icons.GRID_VIEW_ROUNDED,
        "Transform",
    ),
    "manual_input": (
        "#4ADE80",
        "#166534",
        "#D1FAE5",
        ft.Icons.EDIT_NOTE_ROUNDED,
        "Input",
    ),
    "external_source": (
        "#4ADE80",
        "#166534",
        "#D1FAE5",
        ft.Icons.LANGUAGE_ROUNDED,
        "Input",
    ),
    # Transform nodes
    "filter": (
        "#60A5FA",
        "#1E3A5F",
        "#DBEAFE",
        ft.Icons.FILTER_ALT_ROUNDED,
        "Transform",
    ),
    "select": (
        "#60A5FA",
        "#1E3A5F",
        "#DBEAFE",
        ft.Icons.VIEW_COLUMN_ROUNDED,
        "Transform",
    ),
    "formula": (
        "#60A5FA",
        "#1E3A5F",
        "#DBEAFE",
        ft.Icons.FUNCTIONS_ROUNDED,
        "Transform",
    ),
    "sort": ("#60A5FA", "#1E3A5F", "#DBEAFE", ft.Icons.SORT_ROUNDED, "Transform"),
    "sample": (
        "#60A5FA",
        "#1E3A5F",
        "#DBEAFE",
        ft.Icons.CONTENT_CUT_ROUNDED,
        "Transform",
    ),
    "unique": ("#60A5FA", "#1E3A5F", "#DBEAFE", ft.Icons.DEBLUR_ROUNDED, "Transform"),
    "text_to_rows": (
        "#60A5FA",
        "#1E3A5F",
        "#DBEAFE",
        ft.Icons.WRAP_TEXT_ROUNDED,
        "Transform",
    ),
    "record_id": ("#60A5FA", "#1E3A5F", "#DBEAFE", ft.Icons.TAG_ROUNDED, "Transform"),
    "polars_code": (
        "#60A5FA",
        "#1E3A5F",
        "#DBEAFE",
        ft.Icons.CODE_ROUNDED,
        "Transform",
    ),
    # Aggregate nodes
    "group_by": (
        "#FBBF24",
        "#78350F",
        "#FEF3C7",
        ft.Icons.WORKSPACES_ROUNDED,
        "Aggregate",
    ),
    "pivot": (
        "#FBBF24",
        "#78350F",
        "#FEF3C7",
        ft.Icons.PIVOT_TABLE_CHART_ROUNDED,
        "Aggregate",
    ),
    "unpivot": (
        "#FBBF24",
        "#78350F",
        "#FEF3C7",
        ft.Icons.TABLE_ROWS_ROUNDED,
        "Aggregate",
    ),
    "record_count": (
        "#FBBF24",
        "#78350F",
        "#FEF3C7",
        ft.Icons.NUMBERS_ROUNDED,
        "Aggregate",
    ),
    # Combine nodes
    "join": ("#A78BFA", "#3B1F6E", "#EDE9FE", ft.Icons.MERGE_ROUNDED, "Combine"),
    "union": ("#A78BFA", "#3B1F6E", "#EDE9FE", ft.Icons.CALL_MERGE_ROUNDED, "Combine"),
    "fuzzy_match": (
        "#A78BFA",
        "#3B1F6E",
        "#EDE9FE",
        ft.Icons.MANAGE_SEARCH_ROUNDED,
        "Combine",
    ),
    "cross_join": (
        "#A78BFA",
        "#3B1F6E",
        "#EDE9FE",
        ft.Icons.GRID_ON_ROUNDED,
        "Combine",
    ),
    "graph_solver": (
        "#A78BFA",
        "#3B1F6E",
        "#EDE9FE",
        ft.Icons.ACCOUNT_TREE_ROUNDED,
        "Combine",
    ),
    # Output nodes
    "output": ("#F87171", "#7F1D1D", "#FEE2E2", ft.Icons.SAVE_ROUNDED, "Output"),
    "database_writer": (
        "#F87171",
        "#7F1D1D",
        "#FEE2E2",
        ft.Icons.STORAGE_ROUNDED,
        "Output",
    ),
    "cloud_storage_writer": (
        "#F87171",
        "#7F1D1D",
        "#FEE2E2",
        ft.Icons.CLOUD_UPLOAD_ROUNDED,
        "Output",
    ),
    "explore_data": (
        "#F87171",
        "#7F1D1D",
        "#FEE2E2",
        ft.Icons.BAR_CHART_ROUNDED,
        "Output",
    ),
}

_DEFAULT_CATEGORY = ("#94A3B8", "#1E293B", "#F1F5F9", ft.Icons.SETTINGS_ROUNDED, "Step")

INPUT_NODE_TYPES = {
    "read",
    "read_csv",
    "manual_input",
    "database_reader",
    "cloud_storage_reader",
    "external_source",
}
OUTPUT_NODE_TYPES = {
    "output",
    "explore_data",
    "database_writer",
    "cloud_storage_writer",
}


class DraggableNodeCard(ft.GestureDetector):
    def __init__(
        self,
        node,
        x,
        y,
        is_selected,
        scale_factor=1.0,
        on_drag=None,
        on_select=None,
        on_delete=None,
        on_disconnect=None,
        on_socket_click=None,
        on_socket_drag_start=None,
        on_socket_drag_update=None,
        on_socket_drag_end=None,
        incoming_connections=None,
        outgoing_connections=None,
        page: ft.Page = None,
    ):
        self.node = node
        self.node_id = node.node_id
        self.node_type = node.node_type
        self.x = x
        self.y = y
        self.is_selected = is_selected
        self.scale_factor = scale_factor
        self.on_drag_callback = on_drag
        self.on_select_callback = on_select
        self.on_delete_callback = on_delete
        self.on_disconnect_callback = on_disconnect
        self.on_socket_click_callback = on_socket_click
        self.on_socket_drag_start_callback = on_socket_drag_start
        self.on_socket_drag_update_callback = on_socket_drag_update
        self.on_socket_drag_end_callback = on_socket_drag_end
        self._page = page

        meta = NODE_CATEGORY.get(self.node_type, _DEFAULT_CATEGORY)
        # meta is now 5-tuple: (accent, dark_bg, light_bg, icon, label)
        if len(meta) == 5:
            (
                self.accent,
                self.dark_bg,
                self.light_bg,
                self.node_icon,
                self.category_label,
            ) = meta
        else:
            # backward compat if old 4-tuple somehow
            self.accent, self.dark_bg, self.node_icon, self.category_label = meta
            self.light_bg = "#F0F4FF"

        card_content = self._build_card()

        row_children = []

        # Left Input Socket
        self.has_input_socket = self.node_type not in INPUT_NODE_TYPES
        if self.has_input_socket:
            row_children.append(self._make_socket("input"))
        else:
            row_children.append(ft.Container(width=12))

        row_children.append(card_content)

        # Right Output Socket
        self.has_output_socket = self.node_type not in OUTPUT_NODE_TYPES
        if self.has_output_socket:
            row_children.append(self._make_socket("output"))
        else:
            row_children.append(ft.Container(width=12))

        super().__init__(
            content=ft.Row(
                row_children,
                spacing=0,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_pan_update=self.drag,
            on_pan_end=self.drag_end,
            on_tap=lambda _: (
                self.on_select_callback(self.node_id)
                if self.on_select_callback
                else None
            ),
            on_secondary_tap=lambda _: (
                self.on_disconnect_callback(self.node_id)
                if self.on_disconnect_callback
                else None
            ),
            left=self.x,
            top=self.y,
            scale=self.scale_factor,
        )

    # ──────────────────────────────────────────────
    def _is_dark(self) -> bool:
        if self._page is None:
            return True
        return self._page.theme_mode == ft.ThemeMode.DARK

    def _make_socket(self, socket_type: str):
        """A GestureDetector wrapping the socket circle, supporting click AND drag."""
        color = self.accent if socket_type == "output" else "#64748B"
        tooltip = (
            "Output — drag or click to connect"
            if socket_type == "output"
            else "Input — click to finish connection"
        )

        circle = ft.Container(
            width=12,
            height=12,
            bgcolor=color,
            border_radius=6,
            border=ft.Border.all(2, "#0F172A" if self._is_dark() else "#FFFFFF"),
            shadow=ft.BoxShadow(
                blur_radius=6,
                color=ft.Colors.with_opacity(0.55, color),
                spread_radius=1,
            ),
            tooltip=tooltip,
        )

        if socket_type == "output":
            return ft.GestureDetector(
                content=circle,
                on_tap=lambda e: (
                    self.on_socket_click_callback(self.node_id, socket_type, e)
                    if self.on_socket_click_callback
                    else None
                ),
                on_pan_start=lambda e: (
                    self.on_socket_drag_start_callback(self.node_id, socket_type)
                    if self.on_socket_drag_start_callback
                    else None
                ),
                on_pan_update=lambda e: (
                    self.on_socket_drag_update_callback(
                        e.local_delta.x, e.local_delta.y
                    )
                    if self.on_socket_drag_update_callback
                    else None
                ),
                on_pan_end=lambda e: (
                    self.on_socket_drag_end_callback()
                    if self.on_socket_drag_end_callback
                    else None
                ),
            )
        else:
            return ft.GestureDetector(
                content=circle,
                on_tap=lambda e: (
                    self.on_socket_click_callback(self.node_id, socket_type, e)
                    if self.on_socket_click_callback
                    else None
                ),
            )

    # ──────────────────────────────────────────────
    def _build_card(self) -> ft.Container:
        dark = self._is_dark()

        desc_text = ""
        setting = getattr(self.node, "setting_input", None)
        if setting:
            if hasattr(setting, "description") and getattr(setting, "description", ""):
                desc_text = setting.description
            elif hasattr(setting, "get_default_description"):
                try:
                    desc_text = setting.get_default_description()
                except Exception:
                    pass

        friendly_title = NODE_FRIENDLY_NAMES.get(
            self.node_type, self.node_type.replace("_", " ").title()
        )

        # Header bg & text color based on theme
        header_bg = self.dark_bg if dark else self.light_bg
        title_color = ft.Colors.WHITE if dark else "#111827"
        icon_bg_text = "#0F172A" if dark else "#1F2937"

        # ── Header band ──────────────────────────────
        header = ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Icon(self.node_icon, color=icon_bg_text, size=12),
                        bgcolor=self.accent,
                        border_radius=5,
                        padding=3,
                        width=22,
                        height=22,
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                friendly_title,
                                color=title_color,
                                size=11,
                                weight=ft.FontWeight.W_700,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                self.category_label,
                                color=ft.Colors.with_opacity(0.75, self.accent),
                                size=9,
                                weight=ft.FontWeight.W_500,
                            ),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.LINK_OFF_ROUNDED,
                        icon_color=ft.Colors.with_opacity(0.5, title_color),
                        icon_size=12,
                        padding=0,
                        width=20,
                        height=20,
                        tooltip="Disconnect connections",
                        on_click=lambda _: (
                            self.on_disconnect_callback(self.node_id)
                            if self.on_disconnect_callback
                            else None
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE_ROUNDED,
                        icon_color=ft.Colors.with_opacity(0.5, title_color),
                        icon_size=12,
                        padding=0,
                        width=20,
                        height=20,
                        tooltip="Delete node",
                        on_click=lambda _: (
                            self.on_delete_callback(self.node_id)
                            if self.on_delete_callback
                            else None
                        ),
                    ),
                ],
                spacing=7,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=header_bg,
            padding=ft.Padding(left=8, top=6, right=4, bottom=6),
            border_radius=ft.BorderRadius(
                top_left=8, top_right=8, bottom_left=0, bottom_right=0
            ),
        )

        # ── Body ─────────────────────────────────────
        body_text_color = ft.Colors.GREY_400 if dark else "#6B7280"
        id_text_color = ft.Colors.GREY_600 if dark else "#9CA3AF"
        body_bg = "#151B27" if dark else "#F0F4FF"

        body_elements = []
        if desc_text:
            body_elements.append(
                ft.Text(
                    desc_text,
                    color=body_text_color,
                    size=9,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            )

        # Status row
        body_elements.append(
            ft.Row(
                [
                    ft.Text(
                        f"#{self.node_id}", color=id_text_color, size=9, italic=True
                    ),
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Container(
                                    width=5,
                                    height=5,
                                    bgcolor=ft.Colors.GREEN_400,
                                    border_radius=3,
                                ),
                                ft.Text(
                                    "Ready",
                                    size=9,
                                    color=ft.Colors.GREEN_400,
                                    weight=ft.FontWeight.W_500,
                                ),
                            ],
                            spacing=4,
                            tight=True,
                        ),
                        bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.GREEN),
                        padding=ft.Padding(left=6, top=2, right=6, bottom=2),
                        border_radius=10,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )

        body = ft.Container(
            content=ft.Column(body_elements, spacing=5),
            padding=ft.Padding(left=8, top=6, right=8, bottom=6),
            bgcolor=body_bg,
            border_radius=ft.BorderRadius(
                top_left=0, top_right=0, bottom_left=8, bottom_right=8
            ),
        )

        # ── Outer card ───────────────────────────────
        border_color = (
            self.accent
            if self.is_selected
            else ft.Colors.with_opacity(0.25, self.accent)
        )
        glow_color = self.accent if self.is_selected else ft.Colors.TRANSPARENT
        shadow_blur = 18 if self.is_selected else 6
        shadow_spread = 2 if self.is_selected else 0

        return ft.Container(
            content=ft.Column([header, body], spacing=0),
            width=200,
            border_radius=8,
            border=ft.Border.all(1.5, border_color),
            shadow=ft.BoxShadow(
                blur_radius=shadow_blur,
                spread_radius=shadow_spread,
                color=ft.Colors.with_opacity(0.35, glow_color),
            ),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

    # ──────────────────────────────────────────────
    def drag(self, e: ft.DragUpdateEvent):
        self.x += e.local_delta.x
        self.y += e.local_delta.y
        self.left = self.x
        self.top = self.y
        self.update()
        if self.on_drag_callback:
            self.on_drag_callback(self.node_id, self.x, self.y, is_end=False)

    def drag_end(self, e):
        if self.on_drag_callback:
            self.on_drag_callback(self.node_id, self.x, self.y, is_end=True)
