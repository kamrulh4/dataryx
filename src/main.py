import multiprocessing

# MUST be the very first call in the entry point for Windows frozen builds (.exe).
# Without this, loky/multiprocessing-based process pools (write_threaded,
# collect_threaded, cache_polars_frame_to_temp_thread) will silently crash or
# hang when the app is packaged with PyInstaller / flet build windows.
multiprocessing.freeze_support()

import os
import sys
import atexit
import datetime
import traceback
import threading
from pathlib import Path

# Setup startup log file (commented out to avoid disk writes)
log_dir = Path.home() / ".dataryx"
# log_dir.mkdir(exist_ok=True)
debug_log_path = log_dir / "startup_debug.log"


def log_startup(message):
    # Logging disabled. Uncomment the block below to enable startup debugging logs.
    pass
    # try:
    #     with open(debug_log_path, "a", encoding="utf-8") as f:
    #         f.write(f"[{datetime.datetime.now().isoformat()}] {message}\n")
    # except Exception:
    #     pass


# Initialize log file (commented out to avoid disk writes)
# try:
#     with open(debug_log_path, "w", encoding="utf-8") as f:
#         f.write("=== Dataryx Startup Debug Log ===\n")
#         f.write(f"Timestamp: {datetime.datetime.now().isoformat()}\n")
#         f.write(f"Python: {sys.version}\n")
#         f.write(f"Executable: {sys.executable}\n")
#         f.write(f"Args: {sys.argv}\n")
#         f.write(f"Env DATARYX_MODE: {os.environ.get('DATARYX_MODE')}\n\n")
# except Exception:
#     pass


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    log_startup(f"CRITICAL: Uncaught Exception: {exc_value}")
    # try:
    #     with open(debug_log_path, "a", encoding="utf-8") as f:
    #         traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
    # except Exception:
    #     pass
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


sys.excepthook = handle_exception

log_startup("Script execution started")

import logging

# NOTE: Standard logging file handler commented out.
# Uncomment the block below to capture library-level logs (flet, sqlalchemy, httpx, etc.)
# to the startup debug log file. Useful for deep debugging sessions.
# try:
#     logging_handler = logging.FileHandler(debug_log_path, mode="a", encoding="utf-8")
#     logging_handler.setFormatter(
#         logging.Formatter("[%(asctime)s] %(name)s - %(levelname)s - %(message)s")
#     )
#     logging.getLogger().addHandler(logging_handler)
#     logging.getLogger().setLevel(logging.DEBUG)
#     logging.getLogger("flet").addHandler(logging_handler)
#     logging.getLogger("flet").setLevel(logging.DEBUG)
#     log_startup("Standard logging redirect configured")
# except Exception as e:
#     log_startup(f"Failed to configure standard logging: {e}")


def handle_thread_exception(args):
    log_startup(f"THREAD ERROR in {args.thread.name}: {args.exc_value}")
    # try:
    #     with open(debug_log_path, "a", encoding="utf-8") as f:
    #         traceback.print_exception(
    #             args.exc_type, args.exc_value, args.exc_traceback, file=f
    #         )
    # except Exception:
    #     pass


threading.excepthook = handle_thread_exception
log_startup("Thread exception hook configured")

# Dynamic PATH adjustments for internal core library imports
current_dir = Path(__file__).parent.resolve()
sys.path.insert(0, str(current_dir))
sys.path.insert(0, str(current_dir / "core"))

# ---------------------------------------------------------------------------
# Import ONLY flet here so the window opens in ~2 seconds.
# All heavy imports (core, polars, SQLAlchemy, views, etc.) are deferred to a
# background thread inside main() while the splash screen is visible.
# freeze_support() already ran above — subprocess spawning is safe regardless
# of when the subsequent imports happen.
# ---------------------------------------------------------------------------
log_startup("Importing flet")
import flet as ft

log_startup("Flet imported — calling ft.run() now")

# Splash background color (matches dark theme BG_PAGE; avoids importing theme before ft.run)
_SPLASH_BG = "#14161e"


def main(page: ft.Page):
    log_startup("main() called — showing splash immediately")

    page.title = "Dataryx - Visual ETL Tool"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.spacing = 0
    page.bgcolor = _SPLASH_BG
    page.window.maximized = True
    page.window.min_width = 1280
    page.window.min_height = 720

    # ── Splash screen ────────────────────────────────────────────────────────
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
        bgcolor=_SPLASH_BG,
    )
    page.controls.append(splash_layout)
    page.update()
    log_startup("Splash shown — starting background initialization thread")

    # ── Background initialization ─────────────────────────────────────────
    def background_init():
        """
        Runs in a daemon thread.
        Performs all heavy imports and one-time setup while the splash is visible.
        When done, schedules the scheduler start + initial navigation on the
        Flet event loop via page.run_task().
        """
        try:
            log_startup("BG: Importing core modules")
            from core import init_db
            from services.auth_service import auth_service
            from services.license_validator import check_license
            from components.sidebar import Sidebar
            from components.theme import get_theme
            from core.database.connection import get_database_url
            from core.dataryx.scheduler_service import scheduler_service as _sched

            log_startup("BG: Importing views")
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

            log_startup("BG: Running init_db()")
            init_db()

            log_startup("BG: Running check_license()")
            check_license()

            log_startup("BG: Initializing scheduler")
            try:
                _sched.initialize(get_database_url())
                atexit.register(_sched.shutdown)
            except Exception as _e:
                log_startup(f"BG: Scheduler init failed (non-fatal): {_e}")

            log_startup("BG: All initialization done — transitioning to app UI")

            # ── navigate_to lives here so it closes over the imported modules ──
            def navigate_to(route_path: str):
                log_startup(f"navigate_to: {route_path}")
                page.controls.clear()

                # Redirect to login if unauthenticated
                if not auth_service.token:
                    page.controls.append(
                        LoginView(
                            on_login_success=lambda: navigate_to("/designer"),
                            page=page,
                        )
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

                page.bgcolor = get_theme(page).BG_PAGE

                shell_layout = ft.Row(
                    [
                        Sidebar(
                            current_route=route_path,
                            on_route_change=navigate_to,
                            page=page,
                        ),
                        ft.VerticalDivider(width=1, color=get_theme(page).BORDER),
                        ft.Container(content=content_view, expand=True),
                    ],
                    expand=True,
                    spacing=0,
                )

                page.controls.append(shell_layout)
                page.update()

            # ── Schedule scheduler start + initial navigation on event loop ──
            async def finish_startup():
                log_startup("finish_startup: starting scheduler in event loop")
                try:
                    if _sched._initialized and not _sched.scheduler.running:
                        _sched.start()
                        log_startup("Scheduler started successfully")
                except Exception as _e:
                    log_startup(f"Scheduler start failed (non-fatal): {_e}")

                navigate_to("/login")

            page.run_task(finish_startup)

        except Exception as exc:
            log_startup(f"BG: CRITICAL ERROR during initialization: {exc}")
            # Print to stderr for console logging, but do not write to log file.
            traceback.print_exc()
            # try:
            #     with open(debug_log_path, "a", encoding="utf-8") as f:
            #         traceback.print_exc(file=f)
            # except Exception:
            #     pass
            # Show error on screen so the user isn't left with a spinner forever
            page.controls.clear()
            page.controls.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Icon(
                                ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED_400, size=48
                            ),
                            ft.Container(height=12),
                            ft.Text(
                                "Startup Error",
                                color=ft.Colors.RED_400,
                                size=20,
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(
                                str(exc),
                                color=ft.Colors.GREY_400,
                                size=13,
                                text_align=ft.TextAlign.CENTER,
                            ),
                            ft.Container(height=8),
                            ft.Text(
                                f"See log: {debug_log_path}",
                                color=ft.Colors.GREY_600,
                                size=11,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    alignment=ft.Alignment(0, 0),
                    expand=True,
                    bgcolor=_SPLASH_BG,
                )
            )
            page.update()

    threading.Thread(target=background_init, daemon=True, name="dataryx-init").start()


# Start Flet runtime
log_startup("Calling ft.run()")
try:
    ft.run(main, assets_dir=os.path.join(os.path.dirname(__file__), "assets"))
    log_startup("ft.run() returned successfully")
except Exception as e:
    log_startup(f"ft.run() failed with error: {e}")
    raise e
