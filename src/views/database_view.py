import flet as ft
from core.database.connection import get_db_context
from core.dataryx.database_connection_manager.db_connections import (
    get_all_database_connections_interface,
    store_database_connection,
    delete_database_connection,
)
from core.schemas.input_schema import FullDatabaseConnection
from services.auth_service import auth_service
from sqlalchemy import create_engine

class DatabaseView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.expand = True
        self.bgcolor = "#13161F"
        self.padding = 24

        self.connections_list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)
        
        # Form inputs
        self.name_input = ft.TextField(label="Connection Name", height=45, text_size=13)
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
        )
        self.type_dropdown.on_change = self.on_type_change
        self.host_input = ft.TextField(label="Host", height=45, text_size=13, value="localhost")
        self.port_input = ft.TextField(label="Port", height=45, text_size=13, value="5432")
        self.db_input = ft.TextField(label="Database Name", height=45, text_size=13)
        self.user_input = ft.TextField(label="Username", height=45, text_size=13)
        self.pass_input = ft.TextField(label="Password", password=True, can_reveal_password=True, height=45, text_size=13)
        self.ssl_switch = ft.Switch(label="SSL Enabled", value=False)

        self.build_ui()

    def on_type_change(self, e):
        is_sqlite = self.type_dropdown.value == "sqlite"
        self.host_input.visible = not is_sqlite
        self.port_input.visible = not is_sqlite
        self.user_input.visible = not is_sqlite
        self.pass_input.visible = not is_sqlite
        self.ssl_switch.visible = not is_sqlite
        
        if is_sqlite:
            self.db_input.label = "SQLite File Path (e.g. ./local.db)"
        else:
            self.db_input.label = "Database Name"
        self.update()

    def build_ui(self):
        form_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Add Connection", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    ft.Divider(color=ft.Colors.GREY_800),
                    self.name_input,
                    self.type_dropdown,
                    self.host_input,
                    self.port_input,
                    self.db_input,
                    self.user_input,
                    self.pass_input,
                    ft.Row([self.ssl_switch]),
                    ft.Row(
                        [
                            ft.ElevatedButton("Test Connection", on_click=self.test_connection, bgcolor=ft.Colors.BLUE_GREY_600, color=ft.Colors.WHITE),
                            ft.ElevatedButton("Save Connection", on_click=self.save_connection, bgcolor=ft.Colors.BLUE_600, color=ft.Colors.WHITE),
                        ],
                        spacing=10,
                    ),
                ],
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
            ),
            bgcolor="#1E2330",
            padding=20,
            border_radius=8,
            width=350,
        )

        list_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Saved Database Connections", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    ft.Divider(color=ft.Colors.GREY_800),
                    self.connections_list,
                ],
                spacing=10,
                expand=True,
            ),
            bgcolor="#1E2330",
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
        
        with get_db_context() as db:
            connections = get_all_database_connections_interface(db, user_id)
            
        if not connections:
            self.connections_list.controls.append(ft.Text("No saved database connections.", color=ft.Colors.GREY_500))
        else:
            for conn in connections:
                self.connections_list.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.STORAGE_ROUNDED, color=ft.Colors.BLUE_300),
                                ft.Column(
                                    [
                                        ft.Text(conn.connection_name, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                                        ft.Text(f"{conn.database_type.upper()} | {conn.host or 'local'}:{conn.port or ''} | {conn.database}", size=11, color=ft.Colors.GREY_400),
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
                        bgcolor="#13161F",
                        padding=12,
                        border_radius=6,
                        border=ft.Border.all(1, ft.Colors.GREY_800),
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
        db_name = self.db_input.value.strip()
        
        if db_type == "sqlite":
            url = f"sqlite:///{db_name}"
        else:
            host = self.host_input.value.strip()
            port = self.port_input.value.strip()
            user = self.user_input.value.strip()
            pwd = self.pass_input.value
            driver = "postgresql" if db_type == "postgres" else "mysql+pymysql"
            url = f"{driver}://{user}:{pwd}@{host}:{port}/{db_name}"

        try:
            engine = create_engine(url)
            with engine.connect() as conn:
                pass
            self.show_toast("✓ Connection successful!")
        except Exception as ex:
            self.show_toast(f"✗ Connection failed: {str(ex)}")

    def save_connection(self, e):
        name = self.name_input.value.strip()
        if not name:
            self.show_toast("Please enter connection name")
            return
            
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        conn_schema = FullDatabaseConnection(
            connection_name=name,
            host=self.host_input.value.strip() if self.type_dropdown.value != "sqlite" else None,
            port=int(self.port_input.value.strip()) if self.type_dropdown.value != "sqlite" and self.port_input.value.strip() else None,
            database=self.db_input.value.strip(),
            database_type=self.type_dropdown.value,
            username=self.user_input.value.strip() if self.type_dropdown.value != "sqlite" else None,
            password=self.pass_input.value if self.type_dropdown.value != "sqlite" else "",
            ssl_enabled=self.ssl_switch.value if self.type_dropdown.value != "sqlite" else False,
        )

        try:
            with get_db_context() as db:
                store_database_connection(db, conn_schema, user_id)
            self.show_toast("✓ Connection saved successfully!")
            self.load_connections()
        except Exception as ex:
            self.show_toast(f"Error saving: {str(ex)}")

    def show_toast(self, text: str):
        self.main_page.snack_bar = ft.SnackBar(content=ft.Text(text))
        self.main_page.snack_bar.open = True
        self.main_page.update()
