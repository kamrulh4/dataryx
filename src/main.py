import multiprocessing

# MUST be the very first call in the entry point for Windows frozen builds (.exe).
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

# ---------------------------------------------------------------------------
# All heavy imports MUST happen here, BEFORE ft.run(), so that:
#   1. multiprocessing.freeze_support() above has already run.
#   2. Any loky/multiprocessing worker subprocess that is spawned by
#      threaded_processes.py (write_threaded, collect_threaded, etc.) is
#      guarded by freeze_support and exits immediately instead of re-launching
#      the full app — which would cause an infinite recursion / hang on Windows.
#   3. The imports execute outside of Flet's asyncio event loop, avoiding
#      any event-loop conflicts (e.g. AsyncIOScheduler.start() needs a loop).
# ---------------------------------------------------------------------------
import flet as ft
from core import init_db
from services.auth_service import auth_service
from services.license_validator import check_license
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

# ---------------------------------------------------------------------------
# Run DB init and license check once — BEFORE ft.run() and the event loop.
# core/__init__.py already calls init_db() at module level; calling it a
# second time here is harmless (it is idempotent) but makes the intent clear.
# ---------------------------------------------------------------------------
init_db()
check_license()

# ---------------------------------------------------------------------------
# Scheduler: initialize here (outside the event loop) so the SQLAlchemy
# jobstore is ready. We start() it inside main() where the asyncio loop IS
# running, which is what AsyncIOScheduler requires.
# ---------------------------------------------------------------------------
import atexit
from core.database.connection import get_database_url
from core.dataryx.scheduler_service import scheduler_service as _sched

try:
    _sched.initialize(get_database_url())
    atexit.register(_sched.shutdown)
except Exception as _e:
    print(f"[Scheduler] Could not initialize (non-fatal): {_e}")


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

    # Show a splash screen so the user doesn't see a blank white window
    # while the scheduler starts up inside the event loop.
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

    # Start the AsyncIOScheduler now that we are inside the asyncio event loop.
    # (AsyncIOScheduler.start() calls asyncio.get_running_loop() internally.)
    try:
        if _sched._initialized and not _sched.scheduler.running:
            _sched.start()
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
