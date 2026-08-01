import flet as ft
from components.theme import get_theme, is_dark, toggle_theme
from services.auth_service import auth_service


class LoginView(ft.Container):
    def __init__(self, on_login_success, page: ft.Page = None):
        super().__init__()
        self._page = page
        self.on_login_success = on_login_success
        self.is_register_mode = False
        self.expand = True
        t = get_theme(self._page) if self._page else None
        self.bgcolor = t.BG_CARD if t else "#1E2330"
        self.build_login()

    def build_login(self):
        t = get_theme(self._page) if self._page else None
        border_color = t.BORDER if t else ft.Colors.GREY_700
        text_primary = t.TEXT_PRIMARY if t else ft.Colors.WHITE
        text_secondary = t.TEXT_SECONDARY if t else ft.Colors.GREY_400
        card_bgcolor = t.BG_CARD if t else "#1E2330"
        toggle_text_color = (
            ft.Colors.BLUE_600 if t and t.BG_PAGE == "#F4F6FA" else ft.Colors.BLUE_400
        )
        error_color = (
            ft.Colors.RED_600 if t and t.BG_PAGE == "#F4F6FA" else ft.Colors.RED_400
        )

        # Fields
        email_field = ft.TextField(
            label="Email Address",
            hint_text="enter your email",
            border_color=border_color,
            focused_border_color=ft.Colors.BLUE_400,
            text_size=14,
            height=48,
        )

        password_field = ft.TextField(
            label="Password",
            hint_text="enter your password",
            password=True,
            can_reveal_password=True,
            border_color=border_color,
            focused_border_color=ft.Colors.BLUE_400,
            text_size=14,
            height=48,
        )

        fullname_field = ft.TextField(
            label="Full Name",
            hint_text="your full name",
            border_color=border_color,
            focused_border_color=ft.Colors.BLUE_400,
            text_size=14,
            height=48,
            visible=False,
        )

        error_text = ft.Text(color=error_color, size=12, text_align=ft.TextAlign.CENTER)
        submit_btn = ft.Button(
            content="Sign In",
            bgcolor=ft.Colors.BLUE_600,
            color=ft.Colors.WHITE,
            height=48,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        toggle_mode_text = ft.Text(
            "Don't have an account? Sign Up",
            color=toggle_text_color,
            size=12,
            weight=ft.FontWeight.W_600,
            text_align=ft.TextAlign.CENTER,
        )

        def toggle_mode(e):
            self.is_register_mode = not self.is_register_mode
            fullname_field.visible = self.is_register_mode
            submit_btn.content = "Sign Up" if self.is_register_mode else "Sign In"
            toggle_mode_text.value = (
                "Already have an account? Sign In"
                if self.is_register_mode
                else "Don't have an account? Sign Up"
            )
            error_text.value = ""
            self.update()

        toggle_container = ft.Container(
            content=toggle_mode_text,
            on_click=toggle_mode,
            margin=ft.Margin(top=12),
        )

        def handle_submit(e):
            error_text.value = ""
            submit_btn.disabled = True
            self.update()

            email = email_field.value.strip()
            password = password_field.value.strip()
            fullname = fullname_field.value.strip()

            if not email or not password:
                error_text.value = "All fields are required"
                submit_btn.disabled = False
                self.update()
                return

            try:
                if self.is_register_mode:
                    if not fullname:
                        error_text.value = "Full Name is required"
                        submit_btn.disabled = False
                        self.update()
                        return
                    auth_service.register(email, password, fullname)
                    # Automatically log in after registration
                    auth_service.login(email, password)
                else:
                    auth_service.login(email, password)

                # Fetch profile details
                auth_service.get_profile()
                self.on_login_success()
            except Exception as ex:
                error_text.value = str(ex)
                submit_btn.disabled = False
                self.update()

        submit_btn.on_click = handle_submit
        email_field.on_submit = handle_submit
        password_field.on_submit = handle_submit
        fullname_field.on_submit = handle_submit

        card = ft.Card(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Image(
                                    src="logo.png", width=42, height=42, fit="contain"
                                ),
                                ft.Text(
                                    "DATARYX",
                                    color=text_primary,
                                    size=24,
                                    weight=ft.FontWeight.BOLD,
                                ),
                            ],
                            spacing=12,
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        ft.Text(
                            "Sleek. Fast. Lightweight. Visual ETL.",
                            color=text_secondary,
                            size=14,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.Container(height=16),
                        fullname_field,
                        email_field,
                        password_field,
                        error_text,
                        ft.Container(height=8),
                        submit_btn,
                        toggle_container,
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    spacing=12,
                ),
                padding=32,
                width=400,
            ),
            bgcolor=card_bgcolor,
            elevation=8,
        )

        def handle_toggle_theme(e):
            toggle_theme(self._page)
            self.bgcolor = get_theme(self._page).BG_CARD
            self.build_login()
            self.update()

        _dark = self._page and is_dark(self._page)
        theme_toggle = ft.Container(
            content=ft.IconButton(
                icon=(
                    ft.Icons.LIGHT_MODE_ROUNDED if _dark else ft.Icons.DARK_MODE_ROUNDED
                ),
                icon_color=t.TEXT_PRIMARY if t else ft.Colors.GREY_400,
                icon_size=24,
                tooltip="Switch theme",
                on_click=handle_toggle_theme,
            ),
            right=20,
            top=20,
        )

        self.content = ft.Stack(
            [
                ft.Container(
                    content=card,
                    alignment=ft.Alignment(0, 0),
                    expand=True,
                ),
                theme_toggle,
            ],
            expand=True,
        )
