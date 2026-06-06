import flet as ft

class Sidebar(ft.Container):
    def __init__(self, current_route: str, on_route_change):
        super().__init__()
        self.current_route = current_route
        self.on_route_change = on_route_change
        self.width = 240
        self.bgcolor = "#1A1F2C"
        self.padding = 16
        self.border = ft.Border(right=ft.BorderSide(1, ft.Colors.GREY_800))
        self.build_sidebar()

    def build_sidebar(self):
        def make_nav_item(icon: str, label: str, route: str):
            is_active = self.current_route == route
            bg_color = ft.Colors.with_opacity(0.15, ft.Colors.BLUE) if is_active else ft.Colors.TRANSPARENT
            icon_color = ft.Colors.BLUE_400 if is_active else ft.Colors.GREY_400
            text_color = ft.Colors.WHITE if is_active else ft.Colors.GREY_300
            
            return ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(icon, color=icon_color, size=20),
                        ft.Text(label, color=text_color, size=14, weight=ft.FontWeight.W_600 if is_active else ft.FontWeight.NORMAL),
                    ],
                    spacing=12,
                ),
                padding=ft.Padding.symmetric(vertical=12, horizontal=16),
                border_radius=8,
                bgcolor=bg_color,
                on_click=lambda _: self.on_route_change(route),
                ink=True,
            )

        self.content = ft.Column(
            [
                # Logo & Header
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Image(src="logo.png", width=36, height=36, fit="contain"),
                            ft.Text("DATARYX", color=ft.Colors.WHITE, size=20, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=12,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    margin=ft.Margin(top=24, bottom=36),
                ),
                # Navigation Links
                make_nav_item(ft.Icons.PLAY_ARROW_ROUNDED, "Flow Designer", "/designer"),
                make_nav_item(ft.Icons.STORAGE_ROUNDED, "Database Connections", "/database"),
                make_nav_item(ft.Icons.CLOUD_QUEUE_ROUNDED, "Cloud Connections", "/cloud"),
                make_nav_item(ft.Icons.FOLDER_OPEN_ROUNDED, "File Catalog", "/catalog"),
                make_nav_item(ft.Icons.KEY_ROUNDED, "Credentials & Secrets", "/secrets"),
                make_nav_item(ft.Icons.SCHEDULE_ROUNDED, "Workflow Scheduler", "/scheduler"),
                make_nav_item(ft.Icons.CREDIT_CARD_ROUNDED, "Subscription & Account", "/subscription"),
                ft.Container(expand=True),
                # Footer / Logout
                ft.Divider(color=ft.Colors.GREY_800),
                make_nav_item(ft.Icons.LOGOUT_ROUNDED, "Sign Out", "/logout"),
            ],
            spacing=8,
            expand=True,
        )

