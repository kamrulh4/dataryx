from components.theme import get_theme
import flet as ft
from pydantic import SecretStr
from core.database.connection import get_db_context
from core.dataryx.database_connection_manager.db_connections import (
    get_all_database_connections_interface,
    get_database_connection_schema,
    store_database_connection,
    update_database_connection,
    delete_database_connection,
)
from core.schemas.input_schema import FullDatabaseConnection
from core.secret_manager.secret_manager import decrypt_secret
from services.auth_service import auth_service


class DatabaseView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.expand = True
        self.bgcolor = get_theme(page).BG_PAGE
        self.padding = 24

        self.connections_list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)
        self.editing_connection_name: str | None = None
        self._connections_cache: list = []

        # Form inputs
        t = get_theme(page)
        self.name_input = ft.TextField(
            label="Connection Name", height=45, text_size=13, border_color=t.BORDER
        )
        self.type_dropdown = ft.Dropdown(
            label="Database Type",
            options=[
                ft.dropdown.Option("postgres"),
                ft.dropdown.Option("mysql"),
                ft.dropdown.Option("sqlite"),
                ft.dropdown.Option("db2", text="IBM Db2"),
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
        self.host_input = ft.TextField(
            label="Host",
            height=45,
            text_size=13,
            value="localhost",
            border_color=t.BORDER,
        )
        self.port_input = ft.TextField(
            label="Port", height=45, text_size=13, value="5432", border_color=t.BORDER
        )
        self.db_input = ft.TextField(
            label="Database Name", height=45, text_size=13, border_color=t.BORDER
        )
        self.user_input = ft.TextField(
            label="Username", height=45, text_size=13, border_color=t.BORDER
        )
        self.pass_input = ft.TextField(
            label="Password",
            password=True,
            can_reveal_password=True,
            height=45,
            text_size=13,
            border_color=t.BORDER,
        )
        self.ssl_switch = ft.Switch(label="SSL Enabled", value=False)

        self.build_ui()

    def on_type_change(self, e):
        is_sqlite = self.type_dropdown.value == "sqlite"
        is_db2 = self.type_dropdown.value == "db2"
        self.host_input.visible = not is_sqlite
        self.port_input.visible = not is_sqlite
        self.user_input.visible = not is_sqlite
        self.pass_input.visible = not is_sqlite
        self.ssl_switch.visible = not is_sqlite
        # DB2 only has one working driver path here (SQLAlchemy + ibm_db --
        # Connector/X doesn't support DB2), so there's no real choice to
        # show; force it and hide the dropdown, same treatment as sqlite.
        self.driver_dropdown.visible = not is_sqlite and not is_db2
        if is_db2:
            self.driver_dropdown.value = "sqlalchemy"

        if is_sqlite:
            self.db_input.label = "SQLite File Path (e.g. ./local.db)"
        else:
            self.db_input.label = "Database Name"
        self.update()

    def build_ui(self):
        t = get_theme(self.main_page)
        self.form_title = ft.Text(
            "Add Connection",
            size=18,
            weight=ft.FontWeight.BOLD,
            color=t.TEXT_PRIMARY,
        )
        self.save_btn = ft.Button(
            "Save Connection",
            on_click=self.save_connection,
            bgcolor=ft.Colors.BLUE_600,
            color=ft.Colors.WHITE,
        )
        self.cancel_edit_btn = ft.TextButton(
            "Cancel Edit",
            on_click=self.cancel_edit,
            visible=False,
        )
        form_panel = ft.Container(
            content=ft.Column(
                [
                    self.form_title,
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
                            ft.Button(
                                "Test Connection",
                                on_click=self.test_connection,
                                bgcolor=ft.Colors.BLUE_GREY_600,
                                color=ft.Colors.WHITE,
                            ),
                            self.save_btn,
                        ],
                        spacing=10,
                    ),
                    self.cancel_edit_btn,
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
                    ft.Text(
                        "Saved Database Connections",
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        color=t.TEXT_PRIMARY,
                    ),
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
        self._connections_cache = connections

        if not connections:
            self.connections_list.controls.append(
                ft.Text("No saved database connections.", color=t.TEXT_HINT)
            )
        else:
            for conn in connections:
                driver_name = (
                    "Connector/X"
                    if getattr(conn, "driver", "sqlalchemy") == "connectorx"
                    else "SQLAlchemy"
                )
                conn_info = f"{conn.database_type.upper()} ({driver_name}) | {conn.host or 'local'}:{conn.port or ''} | {conn.database}"
                self.connections_list.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(
                                    ft.Icons.STORAGE_ROUNDED, color=ft.Colors.BLUE_300
                                ),
                                ft.Column(
                                    [
                                        ft.Text(
                                            conn.connection_name,
                                            weight=ft.FontWeight.BOLD,
                                            color=t.TEXT_PRIMARY,
                                        ),
                                        ft.Text(conn_info, size=11, color=t.TEXT_HINT),
                                    ],
                                    spacing=2,
                                    expand=True,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.BOLT_ROUNDED,
                                    icon_color=ft.Colors.AMBER_400,
                                    tooltip="Test this connection",
                                    on_click=lambda e, name=conn.connection_name: self.test_saved_connection(
                                        name
                                    ),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.EDIT_ROUNDED,
                                    icon_color=ft.Colors.BLUE_300,
                                    on_click=lambda e, name=conn.connection_name: self.start_edit(
                                        name
                                    ),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE_ROUNDED,
                                    icon_color=ft.Colors.RED_400,
                                    on_click=lambda e, name=conn.connection_name: self.delete_conn(
                                        name
                                    ),
                                ),
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
        if self.editing_connection_name == name:
            self.cancel_edit(None)
        self.load_connections()

    def start_edit(self, name: str):
        conn = next(
            (c for c in self._connections_cache if c.connection_name == name), None
        )
        if not conn:
            return
        self.editing_connection_name = name
        self.name_input.value = conn.connection_name
        self.name_input.disabled = True
        self.type_dropdown.value = conn.database_type
        self.driver_dropdown.value = conn.driver
        self.host_input.value = conn.host or ""
        self.port_input.value = str(conn.port) if conn.port else ""
        self.db_input.value = conn.database or ""
        self.user_input.value = conn.username or ""
        # Password is never sent back from the DB layer, so it can't be
        # pre-filled -- leave blank and only touch the stored secret if the
        # user types a new one (see update_database_connection).
        self.pass_input.value = ""
        self.pass_input.hint_text = "Leave blank to keep current password"
        self.ssl_switch.value = conn.ssl_enabled or False
        self.on_type_change(None)
        self.form_title.value = f"Edit Connection: {name}"
        self.save_btn.content = "Update Connection"
        self.cancel_edit_btn.visible = True
        self.update()

    def cancel_edit(self, e):
        self.editing_connection_name = None
        self.name_input.value = ""
        self.name_input.disabled = False
        self.host_input.value = "localhost"
        self.port_input.value = "5432"
        self.db_input.value = ""
        self.user_input.value = ""
        self.pass_input.value = ""
        self.pass_input.hint_text = None
        self.ssl_switch.value = False
        self.type_dropdown.value = "postgres"
        self.driver_dropdown.value = "sqlalchemy"
        self.on_type_change(None)
        self.form_title.value = "Add Connection"
        self.save_btn.content = "Save Connection"
        self.cancel_edit_btn.visible = False
        self.update()

    def test_connection(self, e):
        """Test using whatever is currently typed into the form. In edit
        mode the password field is intentionally left blank (see
        start_edit), so this will fail auth for an untouched password --
        use the saved-connection row's own Test icon (test_saved_connection)
        to test with the actual stored password instead.
        """
        db_type = self.type_dropdown.value
        db_name = (self.db_input.value or "").strip()

        if db_type == "sqlite":
            self._test_sqlite(db_name)
            return

        host = (self.host_input.value or "").strip()
        port = (self.port_input.value or "").strip()
        user = (self.user_input.value or "").strip()
        pwd = self.pass_input.value or ""

        if not host or not port or not user or not db_name:
            self.show_toast(
                "✗ Connection failed: Host, Port, Username, and Database Name are required"
            )
            return

        driver = (
            self.driver_dropdown.value if self.driver_dropdown.visible else "sqlalchemy"
        )
        self._test_remote_connection(
            db_type,
            host,
            int(port) if port.isdigit() else None,
            user,
            pwd,
            db_name,
            driver,
        )

    def test_saved_connection(self, name: str):
        """Test a saved connection using its real stored password, without
        requiring it to be retyped into the form."""
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        with get_db_context() as db:
            conn = get_database_connection_schema(db, name, user_id)

        if not conn:
            self.show_toast(f"✗ Connection '{name}' not found")
            return

        if conn.database_type == "sqlite":
            self._test_sqlite(conn.database or "")
            return

        real_password = decrypt_secret(conn.password.get_secret_value()).get_secret_value()
        self._test_remote_connection(
            conn.database_type,
            conn.host,
            conn.port,
            conn.username,
            real_password,
            conn.database or "",
            conn.driver,
        )

    def _test_sqlite(self, db_name: str):
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

    def _test_remote_connection(
        self,
        db_type: str,
        host: str | None,
        port: int | None,
        user: str | None,
        pwd: str,
        db_name: str,
        driver: str,
    ):
        from pydantic import SecretStr
        from core.dataryx.sources.external_sources.sql_source.utils import (
            construct_sql_uri,
        )

        try:
            url = construct_sql_uri(
                database_type=db_type,
                host=host,
                port=port,
                username=user,
                password=SecretStr(pwd),
                database=db_name,
            )
        except Exception as ex:
            self.show_toast(f"✗ Connection failed: {str(ex)}")
            return

        import asyncio

        def _run_sqlalchemy_test():
            from sqlalchemy import create_engine, text

            engine = create_engine(url)
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
            finally:
                engine.dispose()

        async def run_test_async():
            self.show_toast("Testing connection...")
            try:
                import polars as pl

                if driver == "connectorx":
                    import re

                    test_url = re.sub(r"(\w+)\+\w+(://)", r"\1\2", url)
                    await asyncio.to_thread(
                        pl.read_database_uri, "SELECT 1", test_url, engine="connectorx"
                    )
                else:
                    # pl.read_database_uri() only accepts engine="connectorx"
                    # or "adbc" -- passing "sqlalchemy" raises ValueError, so
                    # this path (the default driver choice for every type,
                    # including the new DB2 option) needs a real SQLAlchemy
                    # connection instead, same fix as the actual read path
                    # in sql_source.py.
                    await asyncio.to_thread(_run_sqlalchemy_test)
                self.show_toast("✓ Connection successful!")
            except Exception as ex:
                err_msg = str(ex)
                if "timed out waiting for connection" in err_msg:
                    err_msg = "Connection timed out. Please check your host and port."
                elif "Connection refused" in err_msg:
                    err_msg = "Connection refused. Please check if the database is running on the host/port."
                elif (
                    "Access denied" in err_msg
                    or "authentication failed" in err_msg.lower()
                ):
                    err_msg = "Authentication failed. Please check your username and password."
                elif (
                    "database" in err_msg.lower()
                    and "does not exist" in err_msg.lower()
                ):
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
        raw_password = self.pass_input.value or ""

        try:
            if self.editing_connection_name:
                with get_db_context() as db:
                    update_database_connection(
                        db,
                        connection_name=self.editing_connection_name,
                        user_id=user_id,
                        database_type=self.type_dropdown.value,
                        username=(
                            (self.user_input.value or "").strip()
                            if not is_sqlite
                            else None
                        ),
                        host=(
                            (self.host_input.value or "").strip()
                            if not is_sqlite
                            else None
                        ),
                        port=(
                            int((self.port_input.value or "").strip())
                            if not is_sqlite and (self.port_input.value or "").strip()
                            else None
                        ),
                        database=db_name,
                        ssl_enabled=self.ssl_switch.value if not is_sqlite else False,
                        driver=(
                            self.driver_dropdown.value
                            if not is_sqlite
                            else "sqlalchemy"
                        ),
                        password=SecretStr(raw_password) if raw_password else None,
                    )
                self.show_toast("✓ Connection updated successfully!")
                self.cancel_edit(None)
            else:
                conn_schema = FullDatabaseConnection(
                    connection_name=name,
                    host=(
                        (self.host_input.value or "").strip() if not is_sqlite else None
                    ),
                    port=(
                        int((self.port_input.value or "").strip())
                        if not is_sqlite and (self.port_input.value or "").strip()
                        else None
                    ),
                    database=db_name,
                    database_type=self.type_dropdown.value,
                    username=(
                        (self.user_input.value or "").strip() if not is_sqlite else None
                    ),
                    password=raw_password if not is_sqlite else "",
                    ssl_enabled=self.ssl_switch.value if not is_sqlite else False,
                    driver=(
                        self.driver_dropdown.value if not is_sqlite else "sqlalchemy"
                    ),
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
