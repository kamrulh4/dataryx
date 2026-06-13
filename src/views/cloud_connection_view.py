import flet as ft
from core.database.connection import get_db_context
from core.dataryx.database_connection_manager.db_connections import (
    get_all_cloud_connections_interface,
    store_cloud_connection,
    delete_cloud_connection,
)
from core.schemas.cloud_storage_schemas import FullCloudStorageConnection
from services.auth_service import auth_service

class CloudConnectionView(ft.Container):
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
            label="Cloud Storage Type",
            options=[
                ft.dropdown.Option("s3"),
                ft.dropdown.Option("azure"),
            ],
            value="s3",
            height=45,
            text_size=13,
        )
        self.type_dropdown.on_select = self.on_type_change
        
        # AWS S3 inputs
        self.s3_region = ft.TextField(label="AWS Region", height=45, text_size=13, value="us-east-1")
        self.s3_key_id = ft.TextField(label="AWS Access Key ID", height=45, text_size=13)
        self.s3_secret = ft.TextField(label="AWS Secret Access Key", password=True, can_reveal_password=True, height=45, text_size=13)
        
        # Azure inputs
        self.azure_acc_name = ft.TextField(label="Azure Account Name", height=45, text_size=13, visible=False)
        self.azure_acc_key = ft.TextField(label="Azure Account Key", password=True, can_reveal_password=True, height=45, text_size=13, visible=False)
        
        # Common inputs
        self.endpoint_url = ft.TextField(label="Custom Endpoint URL (Optional)", height=45, text_size=13)
        self.verify_ssl = ft.Switch(label="Verify SSL", value=True)

        self.build_ui()

    def on_type_change(self, e):
        is_s3 = self.type_dropdown.value == "s3"
        self.s3_region.visible = is_s3
        self.s3_key_id.visible = is_s3
        self.s3_secret.visible = is_s3
        
        self.azure_acc_name.visible = not is_s3
        self.azure_acc_key.visible = not is_s3
        self.update()

    def build_ui(self):
        form_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Add Cloud Storage", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    ft.Divider(color=ft.Colors.GREY_800),
                    self.name_input,
                    self.type_dropdown,
                    self.s3_region,
                    self.s3_key_id,
                    self.s3_secret,
                    self.azure_acc_name,
                    self.azure_acc_key,
                    self.endpoint_url,
                    ft.Row([self.verify_ssl]),
                    ft.Button("Save Connection", on_click=self.save_connection, bgcolor=ft.Colors.BLUE_600, color=ft.Colors.WHITE),
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
                    ft.Text("Saved Cloud Connections", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
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
            connections = get_all_cloud_connections_interface(db, user_id)
            
        if not connections:
            self.connections_list.controls.append(ft.Text("No saved cloud connections.", color=ft.Colors.GREY_500))
        else:
            for conn in connections:
                self.connections_list.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.CLOUD_QUEUE_ROUNDED, color=ft.Colors.BLUE_300),
                                ft.Column(
                                    [
                                        ft.Text(conn.connection_name, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                                        ft.Text(f"{conn.storage_type.upper()} | {conn.aws_region or 'Azure'}", size=11, color=ft.Colors.GREY_400),
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
            delete_cloud_connection(db, name, user_id)
        self.load_connections()

    def save_connection(self, e):
        name = self.name_input.value.strip()
        if not name:
            self.show_toast("Please enter connection name")
            return
            
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        is_s3 = self.type_dropdown.value == "s3"
        
        conn_schema = FullCloudStorageConnection(
            connection_name=name,
            storage_type=self.type_dropdown.value,
            auth_method="access_key" if is_s3 else "account_key",
            aws_region=self.s3_region.value.strip() if is_s3 else None,
            aws_access_key_id=self.s3_key_id.value.strip() if is_s3 else None,
            aws_secret_access_key=self.s3_secret.value if is_s3 else None,
            azure_account_name=self.azure_acc_name.value.strip() if not is_s3 else None,
            azure_account_key=self.azure_acc_key.value if not is_s3 else None,
            endpoint_url=self.endpoint_url.value.strip() or None,
            verify_ssl=self.verify_ssl.value,
        )

        try:
            with get_db_context() as db:
                store_cloud_connection(db, conn_schema, user_id)
            self.show_toast("✓ Cloud connection saved successfully!")
            self.load_connections()
        except Exception as ex:
            self.show_toast(f"Error saving: {str(ex)}")

    def show_toast(self, text: str):
        snack = ft.SnackBar(content=ft.Text(text))
        self.main_page.overlay.append(snack)
        snack.open = True
        self.main_page.update()
