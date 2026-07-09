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
from core import init_db
from services.auth_service import auth_service
from components.sidebar import Sidebar
from components.theme import get_theme
from views.login_view import LoginView
from views.designer_view import DesignerView
from views.catalog_view import CatalogView
from views.secrets_view import SecretsView
from views.subscription_view import SubscriptionView
from views.database_view import DatabaseView
from views.cloud_connection_view import CloudConnectionView
from views.scheduler_view import SchedulerView
from views.license_view import LicenseView
from views.logs_view import LogsView
from services.license_validator import check_license


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

    # Initialize Local Database
    init_db()

    # Initialize/Check hardware license & trial
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

        # Determine target view
        if route_path == "/designer":
            content_view = DesignerView(page)
        elif route_path == "/database":
            content_view = DatabaseView(page)
        elif route_path == "/cloud":
            content_view = CloudConnectionView(page)
        elif route_path == "/catalog":
            content_view = CatalogView(page)
        elif route_path == "/secrets":
            content_view = SecretsView(page)
        elif route_path == "/scheduler":
            content_view = SchedulerView(page)
        elif route_path == "/subscription":
            content_view = SubscriptionView(page)
        elif route_path == "/license":
            content_view = LicenseView(page)
        elif route_path == "/logs":
            content_view = LogsView(page)
        else:
            route_path = "/designer"
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
