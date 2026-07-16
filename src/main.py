import multiprocessing

# MUST be the very first call in the entry point for Windows frozen builds (.exe).
# Without this, loky/multiprocessing-based process pools (write_threaded,
# collect_threaded, cache_polars_frame_to_temp_thread) will silently crash or
# hang when the app is packaged with PyInstaller / flet build windows.
multiprocessing.freeze_support()

import os
import sys
import datetime
import traceback
from pathlib import Path

# Setup startup log file
log_dir = Path.home() / ".dataryx"
log_dir.mkdir(exist_ok=True)
debug_log_path = log_dir / "startup_debug.log"


def log_startup(message):
    try:
        with open(debug_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat()}] {message}\n")
    except Exception:
        pass


# Initialize log file
try:
    with open(debug_log_path, "w", encoding="utf-8") as f:
        f.write("=== Dataryx Startup Debug Log ===\n")
        f.write(f"Timestamp: {datetime.datetime.now().isoformat()}\n")
        f.write(f"Python: {sys.version}\n")
        f.write(f"Executable: {sys.executable}\n")
        f.write(f"Args: {sys.argv}\n")
        f.write(f"Env DATARYX_MODE: {os.environ.get('DATARYX_MODE')}\n\n")
except Exception as e:
    pass


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    log_startup(f"CRITICAL: Uncaught Exception: {exc_value}")
    try:
        with open(debug_log_path, "a", encoding="utf-8") as f:
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


sys.excepthook = handle_exception

log_startup("Script execution started")

import logging
import threading

# Configure a file handler for standard logging to capture Flet's internal DEBUG logs
try:
    logging_handler = logging.FileHandler(debug_log_path, mode="a", encoding="utf-8")
    logging_handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(name)s - %(levelname)s - %(message)s")
    )
    # Add to root logger
    logging.getLogger().addHandler(logging_handler)
    logging.getLogger().setLevel(logging.DEBUG)

    # Add to flet loggers
    logging.getLogger("flet").addHandler(logging_handler)
    logging.getLogger("flet").setLevel(logging.DEBUG)
    logging.getLogger("flet_core").addHandler(logging_handler)
    logging.getLogger("flet_core").setLevel(logging.DEBUG)

    log_startup("Standard logging redirect to startup_debug.log configured")
except Exception as e:
    log_startup(f"Failed to configure standard logging: {e}")


# Thread exception hook
def handle_thread_exception(args):
    log_startup(f"THREAD ERROR in {args.thread.name}: {args.exc_value}")
    try:
        with open(debug_log_path, "a", encoding="utf-8") as f:
            traceback.print_exception(
                args.exc_type, args.exc_value, args.exc_traceback, file=f
            )
    except Exception:
        pass


threading.excepthook = handle_thread_exception
log_startup("Thread exception hook configured")

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
log_startup("Importing flet")
import flet as ft

log_startup("Importing core.init_db")
from core import init_db

log_startup("Importing auth_service")
from services.auth_service import auth_service

log_startup("Importing check_license")
from services.license_validator import check_license

log_startup("Importing Sidebar")
from components.sidebar import Sidebar

log_startup("Importing get_theme")
from components.theme import get_theme

log_startup("Importing LoginView")
from views.login_view import LoginView

log_startup("Importing DesignerView")
from views.designer_view import DesignerView

log_startup("Importing CatalogView")
from views.catalog_view import CatalogView

log_startup("Importing SecretsView")
from views.secrets_view import SecretsView

log_startup("Importing SubscriptionView")
from views.subscription_view import SubscriptionView

log_startup("Importing DatabaseView")
from views.database_view import DatabaseView

log_startup("Importing CloudConnectionView")
from views.cloud_connection_view import CloudConnectionView

log_startup("Importing SchedulerView")
from views.scheduler_view import SchedulerView

log_startup("Importing LicenseView")
from views.license_view import LicenseView

log_startup("Importing LogsView")
from views.logs_view import LogsView

# ---------------------------------------------------------------------------
# Run DB init and license check once — BEFORE ft.run() and the event loop.
# core/__init__.py already calls init_db() at module level; calling it a
# second time here is harmless (it is idempotent) but makes the intent clear.
# ---------------------------------------------------------------------------
log_startup("Calling init_db()")
init_db()
log_startup("Calling check_license()")
check_license()

# ---------------------------------------------------------------------------
# Scheduler: initialize here (outside the event loop) so the SQLAlchemy
# jobstore is ready. We start() it inside main() where the asyncio loop IS
# running, which is what AsyncIOScheduler requires.
# ---------------------------------------------------------------------------
log_startup("Importing scheduler requirements")
import atexit
from core.database.connection import get_database_url
from core.dataryx.scheduler_service import scheduler_service as _sched

log_startup("Initializing scheduler")
try:
    _sched.initialize(get_database_url())
    atexit.register(_sched.shutdown)
except Exception as _e:
    log_startup(f"Scheduler initialization failed (non-fatal): {_e}")


def main(page: ft.Page):
    log_startup("main() execution started")
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
    log_startup("Calling page.update() for splash layout")
    page.update()
    log_startup("Splash layout page.update() completed")

    # Start the AsyncIOScheduler now that we are inside the asyncio event loop.
    # (AsyncIOScheduler.start() calls asyncio.get_running_loop() internally.)
    log_startup("Starting scheduler inside event loop")
    try:
        if _sched._initialized and not _sched.scheduler.running:
            _sched.start()
            log_startup("Scheduler started inside event loop successfully")
    except Exception as _e:
        log_startup(f"[Scheduler] Could not start (non-fatal): {_e}")

    def navigate_to(route_path: str):
        log_startup(f"navigate_to called with route_path: {route_path}")
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
log_startup("Calling ft.run()")
try:
    ft.run(main, assets_dir=os.path.join(os.path.dirname(__file__), "assets"))
    log_startup("ft.run() returned successfully")
except Exception as e:
    log_startup(f"ft.run() failed with error: {e}")
    raise e
