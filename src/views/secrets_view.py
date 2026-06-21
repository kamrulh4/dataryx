from components.theme import get_theme
import flet as ft
from core.database.connection import get_db_context
from core.dataryx.database_connection_manager.db_connections import get_all_database_connections_interface, store_database_connection
from core.schemas.input_schema import FullDatabaseConnection
from services.auth_service import auth_service

class SecretsView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.connections = []
        self.expand = True
        self.bgcolor = get_theme(page).BG_PAGE
        self.padding = 20
        self.build_secrets()

    def build_secrets(self):
        t = get_theme(self.main_page)
        
        title = ft.Text("Credentials & Database Connections", size=18, weight=ft.FontWeight.BOLD, color=t.TEXT_PRIMARY)
        subtitle = ft.Text("Define SQL and cloud credentials securely stored locally in SQLite.", color=t.TEXT_SECONDARY, size=13)
        
        conn_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

        def load_connections():
            conn_list.controls.clear()
            user_id = auth_service.user_info.get("id") if auth_service.user_info else 0
            with get_db_context() as db:
                self.connections = get_all_database_connections_interface(db, user_id)
                
            if not self.connections:
                conn_list.controls.append(ft.Text("No database connections found. Add one below.", color=t.TEXT_HINT))
                return

            for conn in self.connections:
                driver_name = "Connector/X" if getattr(conn, "driver", "sqlalchemy") == "connectorx" else "SQLAlchemy"
                conn_type_driver = f"{conn.database_type.upper()} ({driver_name})"
                conn_list.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Row(
                                    [
                                        ft.Icon(ft.Icons.STORAGE_ROUNDED, color=ft.Colors.BLUE_400),
                                        ft.Column(
                                            [
                                                ft.Text(conn.connection_name, color=t.TEXT_PRIMARY, size=14, weight=ft.FontWeight.BOLD),
                                                ft.Text(f"{conn_type_driver} | Host: {conn.host} | Database: {conn.database}", color=t.TEXT_HINT, size=12),
                                            ],
                                            spacing=2
                                        )
                                    ],
                                    spacing=12
                                ),
                                ft.IconButton(ft.Icons.DELETE_ROUNDED, icon_color=ft.Colors.RED_400, on_click=lambda _: self.delete_conn(conn.connection_name))
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                        ),
                        bgcolor=t.BG_CARD_ALT,
                        padding=12,
                        border_radius=6,
                        border=ft.Border.all(1, t.BORDER)
                    )
                )

        # Add New Connection Panel
        name_input = ft.TextField(label="Connection Name", height=42, text_size=13, border_color=t.BORDER)
        type_input = ft.Dropdown(
            label="Database Type",
            options=[ft.dropdown.Option("postgres"), ft.dropdown.Option("mysql"), ft.dropdown.Option("sqlite")],
            height=42,
            text_size=13,
            border_color=t.BORDER
        )
        driver_input = ft.Dropdown(
            label="Driver",
            options=[
                ft.dropdown.Option("sqlalchemy", text="SQLAlchemy"),
                ft.dropdown.Option("connectorx", text="Connector/X (Fast)"),
            ],
            value="sqlalchemy",
            height=42,
            text_size=13,
            border_color=t.BORDER
        )
        host_input = ft.TextField(label="Host", height=42, text_size=13, border_color=t.BORDER)
        port_input = ft.TextField(label="Port", height=42, text_size=13, border_color=t.BORDER)
        user_input = ft.TextField(label="User", height=42, text_size=13, border_color=t.BORDER)
        pass_input = ft.TextField(label="Password", password=True, can_reveal_password=True, height=42, text_size=13, border_color=t.BORDER)
        db_input = ft.TextField(label="Database Name", height=42, text_size=13, border_color=t.BORDER)

        def on_type_change(e):
            is_sqlite = type_input.value == "sqlite"
            host_input.visible = not is_sqlite
            port_input.visible = not is_sqlite
            user_input.visible = not is_sqlite
            pass_input.visible = not is_sqlite
            driver_input.visible = not is_sqlite
            self.update()

        type_input.on_change = on_type_change

        def add_connection(e):
            if not name_input.value or not type_input.value:
                self.show_dialog("Error", "Name and Database Type are required.")
                return

            user_id = auth_service.user_info.get("id") if auth_service.user_info else 0
            is_sqlite = type_input.value == "sqlite"
            new_conn = FullDatabaseConnection(
                connection_name=name_input.value.strip(),
                database_type=type_input.value,
                host=host_input.value.strip() if not is_sqlite else None,
                port=int(port_input.value.strip()) if not is_sqlite and port_input.value.strip() else (None if is_sqlite else 5432),
                username=user_input.value.strip() if not is_sqlite else "",
                password=pass_input.value if not is_sqlite else "",
                database=db_input.value.strip(),
                driver=driver_input.value if not is_sqlite else "sqlalchemy"
            )

            try:
                with get_db_context() as db:
                    store_database_connection(db, new_conn, user_id)
                self.show_dialog("Success", "Connection saved successfully!")
                # Reset fields
                name_input.value = ""
                host_input.value = ""
                port_input.value = ""
                user_input.value = ""
                pass_input.value = ""
                db_input.value = ""
                load_connections()
                self.update()
            except Exception as ex:
                self.show_dialog("Error", f"Failed to store connection: {str(ex)}")

        add_btn = ft.Button("Add Connection", on_click=add_connection, bgcolor=ft.Colors.BLUE_600, color=ft.Colors.WHITE)

        add_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Add SQL Database Connection", size=15, weight=ft.FontWeight.W_600, color=t.TEXT_PRIMARY),
                    ft.Divider(color=t.DIVIDER),
                    name_input,
                    type_input,
                    driver_input,
                    host_input,
                    port_input,
                    user_input,
                    pass_input,
                    db_input,
                    ft.Container(height=8),
                    add_btn
                ],
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
            ),
            width=320,
            bgcolor=t.BG_CARD,
            padding=16,
            border_radius=8
        )

        load_connections()

        self.content = ft.Column(
            [
                title,
                subtitle,
                ft.Divider(color=t.DIVIDER),
                ft.Row(
                    [
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text("Active Connections", size=15, weight=ft.FontWeight.W_600, color=t.TEXT_PRIMARY),
                                    ft.Divider(color=t.DIVIDER),
                                    conn_list
                                ],
                                expand=True
                            ),
                            expand=True,
                            bgcolor=t.BG_CARD,
                            padding=16,
                            border_radius=8
                        ),
                        add_panel
                    ],
                    expand=True,
                    spacing=16
                )
            ],
            expand=True
        )

    def delete_conn(self, conn_name: str):
        # Delete connection
        from core.dataryx.database_connection_manager.db_connections import delete_database_connection
        user_id = auth_service.user_info.get("id") if auth_service.user_info else 0
        try:
            with get_db_context() as db:
                delete_database_connection(db, conn_name, user_id)
            self.show_dialog("Deleted", f"Connection '{conn_name}' deleted.")
            self.build_secrets() # Re-build / Refresh view
            self.update()
        except Exception as e:
            self.show_dialog("Error", str(e))

    def show_dialog(self, title: str, message: str):
        def close_dialog(e):
            self.main_page.pop_dialog()

        dialog = ft.AlertDialog(
            title=ft.Text(title),
            content=ft.Text(message),
            actions=[ft.TextButton("Close", on_click=close_dialog)],
        )
        self.main_page.show_dialog(dialog)
