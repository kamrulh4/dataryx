import multiprocessing

# MUST be the first call in the entry point for Windows frozen builds (.exe).
# Without this, loky/multiprocessing-based process pools (write_threaded,
# collect_threaded, cache_polars_frame_to_temp_thread) will silently crash or
# hang when the app is packaged with PyInstaller / flet build windows.
multiprocessing.freeze_support()

import os
import sys
from pathlib import Path

# Dynamic PATH adjustments for internal core library imports
current_dir = Path(__file__).parent.resolve()
sys.path.insert(0, str(current_dir))
sys.path.insert(0, str(current_dir / "core"))

import flet as ft
from services.auth_service import auth_service
from components.sidebar import Sidebar
from components.theme import get_theme
from views.login_view import LoginView


def main(page: ft.Page):
    page.title = "Dataryx - Visual ETL Tool"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.spacing = 0
    page.bgcolor = get_theme(page).BG_PAGE

    # Maximize window on startup for the best Designer experience
    page.window.maximized = True
    page.window.min_width = 1280
    page.window.min_height = 720

    # Show a beautiful, native-looking splash screen immediately so the user
    # doesn't see a blank white screen during database & licensing setup.
    splash_layout = ft.Container(
        content=ft.Column(
            [
                ft.Image(
                    src="logo.png",
                    width=72,
                    height=72,
                    fit="contain",
                ),
                ft.Container(height=10),
                ft.Text(
                    "DATARYX",
                    color=ft.Colors.WHITE,
                    size=28,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(
                    "Starting Visual ETL Engine...",
                    color=ft.Colors.GREY_400,
                    size=13,
                ),
                ft.Container(height=24),
                ft.ProgressRing(
                    width=28,
                    height=28,
                    stroke_width=3,
                    color=ft.Colors.BLUE_400,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        alignment=ft.Alignment(0, 0),
        expand=True,
        bgcolor=page.bgcolor,
    )
    page.controls.append(splash_layout)
    page.update()

    # Initialize Local Database
    from core import init_db

    init_db()

    # Initialize/Check hardware license & trial
    from services.license_validator import check_license

    check_license()

    # Initialize and start the background scheduler for automated flow execution.
    from core.database.connection import get_database_url
    from core.dataryx.scheduler_service import scheduler_service as _sched
    import atexit

    try:
        _sched.initialize(get_database_url())
        _sched.start()
        atexit.register(lambda: _sched.shutdown())
    except Exception as _e:
        print(f"[Scheduler] Could not start (non-fatal): {_e}")

    def navigate_to(route_path: str):
        page.controls.clear()

        # Unauthorized route protection
        if not auth_service.token:
            page.controls.append(
                LoginView(on_login_success=lambda: navigate_to("/designer"), page=page)
            )
            page.update()
            return

        if route_path == "/logout":
            auth_service.clear_token()
            navigate_to("/login")
            return

        # Determine target view (lazy-loaded for instant startup)
        if route_path == "/designer":
            from views.designer_view import DesignerView

            content_view = DesignerView(page)
        elif route_path == "/database":
            from views.database_view import DatabaseView

            content_view = DatabaseView(page)
        elif route_path == "/cloud":
            from views.cloud_connection_view import CloudConnectionView

            content_view = CloudConnectionView(page)
        elif route_path == "/catalog":
            from views.catalog_view import CatalogView

            content_view = CatalogView(page)
        elif route_path == "/secrets":
            from views.secrets_view import SecretsView

            content_view = SecretsView(page)
        elif route_path == "/scheduler":
            from views.scheduler_view import SchedulerView

            content_view = SchedulerView(page)
        elif route_path == "/subscription":
            from views.subscription_view import SubscriptionView

            content_view = SubscriptionView(page)
        elif route_path == "/license":
            from views.license_view import LicenseView

            content_view = LicenseView(page)
        elif route_path == "/logs":
            from views.logs_view import LogsView

            content_view = LogsView(page)
        else:
            route_path = "/designer"
            from views.designer_view import DesignerView

            content_view = DesignerView(page)

        shell_layout = ft.Row(
            [
                Sidebar(
                    current_route=route_path, on_route_change=navigate_to, page=page
                ),
                ft.VerticalDivider(width=1, color=get_theme(page).BORDER),
                ft.Container(content=content_view, expand=True),
            ],
            expand=True,
            spacing=0,
        )

        page.controls.append(shell_layout)
        page.update()

    # Start at login screen
    navigate_to("/login")


# Start Flet runtime
ft.run(main, assets_dir=os.path.join(os.path.dirname(__file__), "assets"))
