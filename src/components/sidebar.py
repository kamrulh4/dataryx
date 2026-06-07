import flet as ft


class Sidebar(ft.Container):
    """Collapsible navigation sidebar.

    Expanded  → 200 px wide, shows icon + label for every item.
    Collapsed → 56 px wide, shows icon only with tooltip.
    """

    EXPANDED_WIDTH = 250
    COLLAPSED_WIDTH = 56

    def __init__(self, current_route: str, on_route_change):
        super().__init__()
        self.current_route = current_route
        self.on_route_change = on_route_change
        self._collapsed = False

        self.width = self.EXPANDED_WIDTH
        self.bgcolor = "#1A1F2C"
        self.padding = 0
        self.border = ft.Border(right=ft.BorderSide(1, ft.Colors.GREY_800))
        self.animate = ft.Animation(duration=180, curve=ft.AnimationCurve.EASE_IN_OUT)

        self._build()

    # ── Build ──────────────────────────────────────────────────────
    def _build(self):
        self._nav_items_col = ft.Column(spacing=2, expand=True)

        self._toggle_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
            icon_color=ft.Colors.GREY_500,
            icon_size=18,
            tooltip="Collapse sidebar",
            on_click=self._toggle,
        )

        # Logo: image + text (text hidden when collapsed)
        self._logo_text = ft.Text(
            "DATARYX",
            color=ft.Colors.WHITE,
            size=16,
            weight=ft.FontWeight.BOLD,
            visible=True,
            no_wrap=True,
        )
        self._logo_img = ft.Image(src="logo.png", width=28, height=28, fit="contain")

        # Expanded header: logo + toggle button side by side
        self._header_expanded = ft.Container(
            content=ft.Row(
                [
                    ft.Row(
                        [self._logo_img, self._logo_text],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._toggle_btn,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(left=14, top=20, right=8, bottom=16),
            visible=True,
        )

        # Collapsed header: just the expand button, centered
        self._expand_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
            icon_color=ft.Colors.GREY_400,
            icon_size=18,
            tooltip="Expand sidebar",
            on_click=self._toggle,
        )
        self._header_collapsed = ft.Container(
            content=ft.Column(
                [self._expand_btn],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(left=0, top=20, right=0, bottom=16),
            visible=False,
        )

        self.content = ft.Column(
            [
                self._header_expanded,
                self._header_collapsed,
                ft.Divider(color=ft.Colors.GREY_800, height=1),
                ft.Container(
                    content=self._nav_items_col,
                    padding=ft.Padding(left=8, top=8, right=8, bottom=8),
                    expand=True,
                ),
                ft.Divider(color=ft.Colors.GREY_800, height=1),
                ft.Container(
                    content=self._make_item(
                        ft.Icons.LOGOUT_ROUNDED, "Sign Out", "/logout"
                    ),
                    padding=ft.Padding(left=8, top=8, right=8, bottom=16),
                ),
            ],
            spacing=0,
            expand=True,
        )

        self._refresh_items()

    # ── Logo ────────────────────────────────────────────────────────
    def _make_logo(self) -> ft.Row:
        self._logo_img = ft.Image(src="logo.png", width=28, height=28, fit="contain")
        self._logo_text = ft.Text(
            "DATARYX",
            color=ft.Colors.WHITE,
            size=16,
            weight=ft.FontWeight.BOLD,
            visible=True,
        )
        return ft.Row(
            [self._logo_img, self._logo_text],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # ── Single nav item ─────────────────────────────────────────────
    def _make_item(self, icon: str, label: str, route: str) -> ft.Container:
        is_active = self.current_route == route
        bg_color = (
            ft.Colors.with_opacity(0.15, ft.Colors.BLUE)
            if is_active
            else ft.Colors.TRANSPARENT
        )
        icon_color = ft.Colors.BLUE_400 if is_active else ft.Colors.GREY_400
        txt_color = ft.Colors.WHITE if is_active else ft.Colors.GREY_300
        font_w = ft.FontWeight.W_600 if is_active else ft.FontWeight.NORMAL

        # When collapsed show only icon; when expanded show icon + label
        label_ctrl = ft.Text(
            label,
            color=txt_color,
            size=13,
            weight=font_w,
            visible=not self._collapsed,
            no_wrap=True,
            expand=True,
        )

        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, color=icon_color, size=20),
                    label_ctrl,
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(
                left=10 if not self._collapsed else 8,
                top=10,
                right=10,
                bottom=10,
            ),
            border_radius=8,
            bgcolor=bg_color,
            tooltip=label if self._collapsed else "",
            on_click=lambda _, r=route: self.on_route_change(r),
            ink=True,
            animate=ft.Animation(duration=150, curve=ft.AnimationCurve.EASE_IN_OUT),
        )

    # ── Refresh list ────────────────────────────────────────────────
    def _refresh_items(self):
        nav = [
            (ft.Icons.PLAY_ARROW_ROUNDED, "Flow Designer", "/designer"),
            (ft.Icons.STORAGE_ROUNDED, "Database Connections", "/database"),
            (ft.Icons.CLOUD_QUEUE_ROUNDED, "Cloud Connections", "/cloud"),
            (ft.Icons.FOLDER_OPEN_ROUNDED, "File Catalog", "/catalog"),
            (ft.Icons.KEY_ROUNDED, "Credentials & Secrets", "/secrets"),
            (ft.Icons.SCHEDULE_ROUNDED, "Workflow Scheduler", "/scheduler"),
            (ft.Icons.CREDIT_CARD_ROUNDED, "Subscription & Account", "/subscription"),
        ]
        self._nav_items_col.controls = [
            self._make_item(icon, label, route) for icon, label, route in nav
        ]

    # ── Toggle ──────────────────────────────────────────────────────
    def _toggle(self, e):
        self._collapsed = not self._collapsed
        self.width = self.COLLAPSED_WIDTH if self._collapsed else self.EXPANDED_WIDTH

        # Swap header: expanded vs collapsed
        self._header_expanded.visible = not self._collapsed
        self._header_collapsed.visible = self._collapsed

        # Rebuild nav items with updated collapsed state (label visibility)
        self._refresh_items()
        self.update()
