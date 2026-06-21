from components.theme import get_theme
import flet as ft
from core.database.connection import get_db_context
from core.dataryx.database_connection_manager.db_connections import (
    get_all_database_connections_interface,
    store_database_connection,
    delete_database_connection,
)
from core.schemas.input_schema import FullDatabaseConnection
from services.auth_service import auth_service

class DatabaseView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.expand = True
        self.bgcolor = get_theme(page).BG_PAGE
        self.padding = 24

        self.connections_list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)
        
        # Form inputs
        t = get_theme(page)
        self.name_input = ft.TextField(label="Connection Name", height=45, text_size=13, border_color=t.BORDER)
        self.type_dropdown = ft.Dropdown(
            label="Database Type",
            options=[
                ft.dropdown.Option("postgres"),
                ft.dropdown.Option("mysql"),
                ft.dropdown.Option("sqlite"),
            ],
            value="postgres",
            height=45,
            text_size=13,
            border_color=t.BORDER,
        )
        self.type_dropdown.on_select = self.on_type_change
        self.driver_dropdown = ft.Dropdown(
            label="Driver",
            options=[
                ft.dropdown.Option("sqlalchemy", text="SQLAlchemy"),
                ft.dropdown.Option("connectorx", text="Connector/X (Fast)"),
            ],
            value="sqlalchemy",
            height=45,
            text_size=13,
            border_color=t.BORDER,
        )
        self.host_input = ft.TextField(label="Host", height=45, text_size=13, value="localhost", border_color=t.BORDER)
        self.port_input = ft.TextField(label="Port", height=45, text_size=13, value="5432", border_color=t.BORDER)
        self.db_input = ft.TextField(label="Database Name", height=45, text_size=13, border_color=t.BORDER)
        self.user_input = ft.TextField(label="Username", height=45, text_size=13, border_color=t.BORDER)
        self.pass_input = ft.TextField(label="Password", password=True, can_reveal_password=True, height=45, text_size=13, border_color=t.BORDER)
        self.ssl_switch = ft.Switch(label="SSL Enabled", value=False)

        self.build_ui()

    def on_type_change(self, e):
        is_sqlite = self.type_dropdown.value == "sqlite"
        self.host_input.visible = not is_sqlite
        self.port_input.visible = not is_sqlite
        self.user_input.visible = not is_sqlite
        self.pass_input.visible = not is_sqlite
        self.ssl_switch.visible = not is_sqlite
        self.driver_dropdown.visible = not is_sqlite
        
        if is_sqlite:
            self.db_input.label = "SQLite File Path (e.g. ./local.db)"
        else:
            self.db_input.label = "Database Name"
        self.update()

    def build_ui(self):
        t = get_theme(self.main_page)
        form_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Add Connection", size=18, weight=ft.FontWeight.BOLD, color=t.TEXT_PRIMARY),
                    ft.Divider(color=t.DIVIDER),
                    self.name_input,
                    self.type_dropdown,
                    self.driver_dropdown,
                    self.host_input,
                    self.port_input,
                    self.db_input,
                    self.user_input,
                    self.pass_input,
                    ft.Row([self.ssl_switch]),
                    ft.Row(
                        [
                            ft.Button("Test Connection", on_click=self.test_connection, bgcolor=ft.Colors.BLUE_GREY_600, color=ft.Colors.WHITE),
                            ft.Button("Save Connection", on_click=self.save_connection, bgcolor=ft.Colors.BLUE_600, color=ft.Colors.WHITE),
                        ],
                        spacing=10,
                    ),
                ],
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
            ),
            bgcolor=t.BG_CARD,
            padding=20,
            border_radius=8,
            width=350,
        )

        list_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Saved Database Connections", size=18, weight=ft.FontWeight.BOLD, color=t.TEXT_PRIMARY),
                    ft.Divider(color=t.DIVIDER),
                    self.connections_list,
                ],
                spacing=10,
                expand=True,
            ),
            bgcolor=t.BG_CARD,
            padding=20,
            border_radius=8,
            expand=True,
        )

        self.content = ft.Row(
            [
                form_panel,
                list_panel,
            ],
            spacing=20,
            expand=True,
        )

    def did_mount(self):
        self.load_connections()

    def load_connections(self):
        self.connections_list.controls.clear()
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        t = get_theme(self.main_page)
        
        with get_db_context() as db:
            connections = get_all_database_connections_interface(db, user_id)
            
        if not connections:
            self.connections_list.controls.append(ft.Text("No saved database connections.", color=t.TEXT_HINT))
        else:
            for conn in connections:
                driver_name = "Connector/X" if getattr(conn, "driver", "sqlalchemy") == "connectorx" else "SQLAlchemy"
                conn_info = f"{conn.database_type.upper()} ({driver_name}) | {conn.host or 'local'}:{conn.port or ''} | {conn.database}"
                self.connections_list.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.STORAGE_ROUNDED, color=ft.Colors.BLUE_300),
                                ft.Column(
                                    [
                                        ft.Text(conn.connection_name, weight=ft.FontWeight.BOLD, color=t.TEXT_PRIMARY),
                                        ft.Text(conn_info, size=11, color=t.TEXT_HINT),
                                    ],
                                    spacing=2,
                                    expand=True,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE_ROUNDED,
                                    icon_color=ft.Colors.RED_400,
                                    on_click=lambda e, name=conn.connection_name: self.delete_conn(name),
                                )
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        bgcolor=t.BG_PAGE,
                        padding=12,
                        border_radius=6,
                        border=ft.Border.all(1, t.BORDER),
                    )
                )
        self.update()

    def delete_conn(self, name: str):
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        with get_db_context() as db:
            delete_database_connection(db, name, user_id)
        self.load_connections()

    def test_connection(self, e):
        db_type = self.type_dropdown.value
        db_name = (self.db_input.value or "").strip()
        
        if db_type == "sqlite":
            if not db_name:
                self.show_toast("✗ SQLite connection failed: file path is required")
                return
            import sqlite3
            try:
                conn = sqlite3.connect(db_name)
                conn.cursor().execute("SELECT 1")
                conn.close()
                self.show_toast("✓ SQLite Connection successful!")
            except Exception as ex:
                self.show_toast(f"✗ SQLite Connection failed: {str(ex)}")
            return

        host = (self.host_input.value or "").strip()
        port = (self.port_input.value or "").strip()
        user = (self.user_input.value or "").strip()
        pwd = self.pass_input.value or ""
        
        if not host or not port or not user or not db_name:
            self.show_toast("✗ Connection failed: Host, Port, Username, and Database Name are required")
            return

        from pydantic import SecretStr
        from core.dataryx.sources.external_sources.sql_source.utils import construct_sql_uri
        
        driver = "postgresql" if db_type == "postgres" else "mysql"
        
        try:
            url = construct_sql_uri(
                database_type=driver,
                host=host,
                port=int(port) if port.isdigit() else None,
                username=user,
                password=SecretStr(pwd),
                database=db_name,
            )
        except Exception as ex:
            self.show_toast(f"✗ Connection failed: {str(ex)}")
            return

        import asyncio

        async def run_test_async():
            self.show_toast("Testing connection...")
            try:
                import polars as pl
                engine_type = self.driver_dropdown.value if self.driver_dropdown.visible else "sqlalchemy"
                test_url = url
                if engine_type == "connectorx":
                    import re
                    test_url = re.sub(r'(\w+)\+\w+(://)', r'\1\2', url)
                
                await asyncio.to_thread(
                    pl.read_database_uri, 
                    "SELECT 1", 
                    test_url, 
                    engine=engine_type
                )
                self.show_toast("✓ Connection successful!")
            except Exception as ex:
                err_msg = str(ex)
                if "timed out waiting for connection" in err_msg:
                    err_msg = "Connection timed out. Please check your host and port."
                elif "Connection refused" in err_msg:
                    err_msg = "Connection refused. Please check if the database is running on the host/port."
                elif "Access denied" in err_msg or "authentication failed" in err_msg.lower():
                    err_msg = "Authentication failed. Please check your username and password."
                elif "database" in err_msg.lower() and "does not exist" in err_msg.lower():
                    err_msg = f"Database '{db_name}' does not exist."
                self.show_toast(f"✗ Connection failed: {err_msg}")

        self.main_page.run_task(run_test_async)

    def save_connection(self, e):
        name = (self.name_input.value or "").strip()
        if not name:
            self.show_toast("Please enter connection name")
            return
            
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        is_sqlite = self.type_dropdown.value == "sqlite"
        db_name = (self.db_input.value or "").strip()
        
        try:
            conn_schema = FullDatabaseConnection(
                connection_name=name,
                host=(self.host_input.value or "").strip() if not is_sqlite else None,
                port=int((self.port_input.value or "").strip()) if not is_sqlite and (self.port_input.value or "").strip() else None,
                database=db_name,
                database_type=self.type_dropdown.value,
                username=(self.user_input.value or "").strip() if not is_sqlite else None,
                password=self.pass_input.value if not is_sqlite else "",
                ssl_enabled=self.ssl_switch.value if not is_sqlite else False,
                driver=self.driver_dropdown.value if not is_sqlite else "sqlalchemy",
            )
            
            with get_db_context() as db:
                store_database_connection(db, conn_schema, user_id)
            self.show_toast("✓ Connection saved successfully!")
            self.load_connections()
        except Exception as ex:
            self.show_toast(f"Error saving: {str(ex)}")

    def show_toast(self, text: str):
        snack = ft.SnackBar(content=ft.Text(text))
        self.main_page.overlay.append(snack)
        snack.open = True
        self.main_page.update()
