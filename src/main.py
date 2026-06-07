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
from views.login_view import LoginView
from views.designer_view import DesignerView
from views.catalog_view import CatalogView
from views.secrets_view import SecretsView
from views.subscription_view import SubscriptionView
from views.database_view import DatabaseView
from views.cloud_connection_view import CloudConnectionView
from views.scheduler_view import SchedulerView

def main(page: ft.Page):
    page.title = "Dataryx - Visual ETL Tool"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.spacing = 0
    
    # Initialize Local Database
    init_db()

    # Initialize and start background scheduler service for automated flow execution
    # from core.database.connection import get_database_url
    # from core.dataryx.scheduler_service import scheduler_service
    # import atexit
    # try:
    #     scheduler_service.initialize(get_database_url())
    #     scheduler_service.start()
    #     atexit.register(lambda: scheduler_service.shutdown())
    # except Exception as e:
    #     print("Error starting scheduler service:", e)

    def navigate_to(route_path: str):
        page.controls.clear()
        
        # Unauthorized route protection
        if not auth_service.token:
            page.controls.append(
                LoginView(on_login_success=lambda: navigate_to("/designer"))
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
        else:
            route_path = "/designer"
            content_view = DesignerView(page)

        shell_layout = ft.Row(
            [
                Sidebar(current_route=route_path, on_route_change=navigate_to),
                ft.VerticalDivider(width=1, color=ft.Colors.GREY_800),
                ft.Container(content=content_view, expand=True)
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
