import flet as ft
from services.hwid import get_hwid_display
from services.license_validator import activate_license, get_license_info, check_license

class LicenseView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.expand = True
        self.bgcolor = "#13161F"
        self.alignment = ft.Alignment(0, 0)
        self.build_license()

    def build_license(self):
        hwid = get_hwid_display()
        
        # Check active license/trial info
        license_info = get_license_info()
        is_licensed, _ = check_license()
        
        if is_licensed and license_info:
            if license_info.get("is_trial"):
                status_header = "Trial Active"
                status_color = ft.Colors.GREEN_400
                status_msg = f"Your 3-month trial is active. {license_info.get('days_left', 0)} days remaining."
            else:
                status_header = f"Activated ({license_info.get('tier', 'Basic')} Tier)"
                status_color = ft.Colors.BLUE_400
                status_msg = "Your hardware license is active."
        else:
            status_header = "License Required"
            status_color = ft.Colors.RED_400
            status_msg = "Your trial has expired or no license was found. Please activate to run workflows."

        hwid_field = ft.TextField(
            value=hwid,
            read_only=True,
            text_size=12,
            height=40,
            border_color=ft.Colors.GREY_700,
            focused_border_color=ft.Colors.BLUE_400,
            expand=True,
        )

        key_field = ft.TextField(
            label="License Key",
            hint_text="Paste your DRYX-... key here",
            prefix_icon=ft.Icons.VPN_KEY_OUTLINED,
            multiline=True,
            min_lines=2,
            max_lines=4,
            text_size=12,
            border_color=ft.Colors.GREY_700,
            focused_border_color=ft.Colors.BLUE_400,
        )

        status_txt = ft.Text("", size=13, visible=False)

        def copy_hwid(e):
            self.main_page.set_clipboard(hwid)
            status_txt.value = "Hardware ID copied to clipboard!"
            status_txt.color = ft.Colors.BLUE_300
            status_txt.visible = True
            self.update()

        def handle_activate(e):
            key = key_field.value.strip()
            if not key:
                status_txt.value = "Please paste your license key"
                status_txt.color = ft.Colors.RED_400
                status_txt.visible = True
                self.update()
                return

            success, message = activate_license(key)
            if success:
                status_txt.value = "License activated successfully!"
                status_txt.color = ft.Colors.GREEN_400
                status_txt.visible = True
                # Re-build UI to reflect the changes
                self.build_license()
                self.update()
            else:
                status_txt.value = f"Activation failed: {message}"
                status_txt.color = ft.Colors.RED_400
                status_txt.visible = True
                self.update()

        activate_btn = ft.Button(
            content=ft.Text("Activate", weight=ft.FontWeight.BOLD),
            on_click=handle_activate,
            bgcolor=ft.Colors.BLUE_600,
            color=ft.Colors.WHITE,
            height=44,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        self.content = ft.Card(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Image(src="logo.png", width=42, height=42, fit="contain"),
                                ft.Text("DATARYX", color=ft.Colors.WHITE, size=24, weight=ft.FontWeight.BOLD),
                            ],
                            spacing=12,
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        ft.Text("Hardware License & Activation", color=ft.Colors.GREY_400, size=14, text_align=ft.TextAlign.CENTER),
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Text("Status: ", color=ft.Colors.GREY_400, size=13),
                                            ft.Text(status_header, color=status_color, size=13, weight=ft.FontWeight.BOLD)
                                        ],
                                        alignment=ft.MainAxisAlignment.CENTER,
                                    ),
                                    ft.Text(status_msg, color=ft.Colors.GREY_300, size=12, text_align=ft.TextAlign.CENTER),
                                ],
                                spacing=4,
                            ),
                            padding=10,
                            bgcolor="#1E2330",
                            border_radius=8,
                        ),
                        ft.Divider(color=ft.Colors.GREY_800),
                        ft.Text("Your Hardware ID (HWID)", color=ft.Colors.GREY_400, size=13, weight=ft.FontWeight.W_600),
                        ft.Row(
                            [
                                hwid_field,
                                ft.IconButton(
                                    icon=ft.Icons.COPY,
                                    icon_size=18,
                                    tooltip="Copy HWID",
                                    on_click=copy_hwid,
                                )
                            ],
                            spacing=8,
                        ),
                        ft.Text(
                            "Share this ID with the vendor to receive your activation license key.",
                            color=ft.Colors.GREY_500,
                            size=11,
                            italic=True,
                        ),
                        ft.Container(height=4),
                        key_field,
                        status_txt,
                        ft.Container(height=4),
                        activate_btn,
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    spacing=12,
                ),
                padding=24,
                width=420,
            ),
            bgcolor="#1E2330",
            elevation=8,
        )
