import flet as ft
from services.auth_service import auth_service
from services.hwid import get_hwid_display
from services.license_validator import activate_license, get_license_info, check_license
from components.theme import get_theme


class SubscriptionView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.expand = True
        self.bgcolor = get_theme(page).BG_PAGE
        self.padding = 20
        self.build_subscription()

    def build_subscription(self):
        t = get_theme(self.main_page)
        title = ft.Text(
            "Subscription & Account Settings",
            size=18,
            weight=ft.FontWeight.BOLD,
            color=t.TEXT_PRIMARY,
        )

        # Load user and subscription details
        user = auth_service.user_info or {}
        sub = auth_service.subscription_info or {}

        email = user.get("email", "N/A")
        name = user.get("full_name", "N/A")
        plan_name = sub.get("product", "Free Plan")
        remaining_runs = sub.get("conversions_remaining", 0)
        max_size = sub.get("max_file_size_mb", 10)
        expiry = sub.get("expiry", "Lifetime")
        is_expired = sub.get("is_expired", False)
        paypal_link = sub.get("paypal_link", "")

        # Fallback to the Pro upgrade PayPal link if not set dynamically for Free or Expired accounts
        if not paypal_link and ("free" in plan_name.lower() or is_expired):
            paypal_link = "https://www.paypal.com/ncp/payment/7HSHNZT23E6P6"

        status_text = "Expired" if is_expired else "Active"
        status_color = ft.Colors.RED_400 if is_expired else ft.Colors.GREEN_400

        # Load local Hardware License details
        hwid = get_hwid_display()
        license_info = get_license_info()
        is_licensed, _ = check_license()

        if is_licensed and license_info:
            if license_info.get("is_trial"):
                hw_status_text = (
                    f"Active Trial ({license_info.get('days_left', 0)} days left)"
                )
                hw_status_color = ft.Colors.GREEN_400
            else:
                hw_status_text = f"Activated ({license_info.get('tier', 'Basic')})"
                hw_status_color = ft.Colors.BLUE_400
        else:
            hw_status_text = "Expired / Inactive"
            hw_status_color = ft.Colors.RED_400

        # Upgrade Section
        upgrade_widgets = []
        if paypal_link and ("free" in plan_name.lower() or is_expired):
            upgrade_widgets.extend(
                [
                    ft.Text(
                        "Upgrade Plan" if not is_expired else "Renew Plan",
                        size=15,
                        weight=ft.FontWeight.BOLD,
                        color=(
                            ft.Colors.BLUE_600
                            if t.BG_PAGE == "#F4F6FA"
                            else ft.Colors.BLUE_300
                        ),
                    ),
                    ft.Text(
                        (
                            "Unlock premium data nodes, unlimited pipelines, and file sizes up to 100MB."
                            if not is_expired
                            else "Your Pro subscription has expired. Renew your subscription to restore full access to premium features."
                        ),
                        color=t.TEXT_SECONDARY,
                        size=13,
                    ),
                    ft.Container(height=8),
                    ft.Button(
                        content=(
                            "Upgrade to Pro with PayPal"
                            if not is_expired
                            else "Renew Pro with PayPal"
                        ),
                        icon=ft.Icons.PAYMENT_ROUNDED,
                        bgcolor=ft.Colors.BLUE_600,
                        color=ft.Colors.WHITE,
                        url=paypal_link,
                        height=44,
                    ),
                ]
            )
        else:
            upgrade_widgets.append(
                ft.Text(
                    "Premium accounts are managed via your administrator workspace settings.",
                    color=t.TEXT_HINT,
                    size=13,
                    italic=True,
                )
            )

        # License Key entry widgets
        key_input = ft.TextField(
            hint_text="Paste your DRYX-... key here to activate",
            prefix_icon=ft.Icons.VPN_KEY_OUTLINED,
            text_size=12,
            height=40,
            border_color=t.BORDER,
            focused_border_color=ft.Colors.BLUE_400,
            expand=True,
        )

        activation_status = ft.Text("", size=12, visible=False)

        def do_activation(e):
            key = key_input.value.strip()
            if not key:
                activation_status.value = "Please paste your license key"
                activation_status.color = ft.Colors.RED_400
                activation_status.visible = True
                self.update()
                return

            success, message = activate_license(key)
            if success:
                activation_status.value = "Hardware license activated successfully!"
                activation_status.color = ft.Colors.GREEN_400
                activation_status.visible = True
                # Refresh UI
                self.build_subscription()
                self.update()
            else:
                activation_status.value = f"Activation failed: {message}"
                activation_status.color = ft.Colors.RED_400
                activation_status.visible = True
                self.update()

        activate_btn = ft.Button(
            "Activate Key",
            icon=ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED,
            on_click=do_activation,
            bgcolor=ft.Colors.BLUE_600,
            color=ft.Colors.WHITE,
            height=40,
        )

        async def copy_hwid_btn(e):
            await self.main_page.clipboard.set(hwid)
            activation_status.value = "Hardware ID copied to clipboard!"
            activation_status.color = (
                ft.Colors.BLUE_600 if t.BG_PAGE == "#F4F6FA" else ft.Colors.BLUE_300
            )
            activation_status.visible = True
            self.update()

        self.content = ft.Column(
            [
                title,
                ft.Divider(color=t.DIVIDER),
                ft.Row(
                    [
                        # Profile Info Card
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text(
                                        "Profile Details",
                                        size=15,
                                        weight=ft.FontWeight.W_600,
                                        color=t.TEXT_PRIMARY,
                                    ),
                                    ft.Divider(color=t.DIVIDER),
                                    ft.Row(
                                        [
                                            ft.Text(
                                                "Name:",
                                                color=t.TEXT_SECONDARY,
                                                size=13,
                                            ),
                                            ft.Text(
                                                name,
                                                color=t.TEXT_PRIMARY,
                                                size=13,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                        ]
                                    ),
                                    ft.Row(
                                        [
                                            ft.Text(
                                                "Email:",
                                                color=t.TEXT_SECONDARY,
                                                size=13,
                                            ),
                                            ft.Text(
                                                email, color=t.TEXT_PRIMARY, size=13
                                            ),
                                        ]
                                    ),
                                ],
                                spacing=12,
                            ),
                            expand=True,
                            bgcolor=t.BG_CARD,
                            padding=20,
                            border_radius=8,
                        ),
                        # Subscription Limit Card
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text(
                                        "Subscription",
                                        size=15,
                                        weight=ft.FontWeight.W_600,
                                        color=t.TEXT_PRIMARY,
                                    ),
                                    ft.Divider(color=t.DIVIDER),
                                    ft.Row(
                                        [
                                            ft.Text(
                                                "Active Plan:",
                                                color=t.TEXT_SECONDARY,
                                                size=13,
                                            ),
                                            ft.Text(
                                                plan_name,
                                                color=(
                                                    ft.Colors.BLUE_600
                                                    if t.BG_PAGE == "#F4F6FA"
                                                    else ft.Colors.BLUE_300
                                                ),
                                                size=13,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                        ]
                                    ),
                                    ft.Row(
                                        [
                                            ft.Text(
                                                "Remaining Runs:",
                                                color=t.TEXT_SECONDARY,
                                                size=13,
                                            ),
                                            ft.Text(
                                                str(remaining_runs),
                                                color=t.TEXT_PRIMARY,
                                                size=13,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                        ]
                                    ),
                                    ft.Row(
                                        [
                                            ft.Text(
                                                "Max File Size Limit:",
                                                color=t.TEXT_SECONDARY,
                                                size=13,
                                            ),
                                            ft.Text(
                                                f"{max_size} MB",
                                                color=t.TEXT_PRIMARY,
                                                size=13,
                                            ),
                                        ]
                                    ),
                                    ft.Row(
                                        [
                                            ft.Text(
                                                "Expiry:",
                                                color=t.TEXT_SECONDARY,
                                                size=13,
                                            ),
                                            ft.Text(
                                                str(expiry)[:10],
                                                color=t.TEXT_PRIMARY,
                                                size=13,
                                            ),
                                        ]
                                    ),
                                    ft.Row(
                                        [
                                            ft.Text(
                                                "Status:",
                                                color=t.TEXT_SECONDARY,
                                                size=13,
                                            ),
                                            ft.Text(
                                                status_text,
                                                color=status_color,
                                                size=13,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                        ]
                                    ),
                                ],
                                spacing=10,
                            ),
                            expand=True,
                            bgcolor=t.BG_CARD,
                            padding=20,
                            border_radius=8,
                        ),
                    ],
                    spacing=16,
                ),
                ft.Container(height=12),
                # Hardware License & HWID Activation Card
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                "Hardware Device License",
                                size=15,
                                weight=ft.FontWeight.W_600,
                                color=t.TEXT_PRIMARY,
                            ),
                            ft.Divider(color=t.DIVIDER),
                            ft.Row(
                                [
                                    ft.Text(
                                        "Hardware Status:",
                                        color=t.TEXT_SECONDARY,
                                        size=13,
                                    ),
                                    ft.Text(
                                        hw_status_text,
                                        color=hw_status_color,
                                        size=13,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                ]
                            ),
                            ft.Row(
                                [
                                    ft.Text(
                                        "Your Hardware ID:",
                                        color=t.TEXT_SECONDARY,
                                        size=13,
                                    ),
                                    ft.Text(
                                        hwid,
                                        color=t.TEXT_PRIMARY,
                                        size=12,
                                        selectable=True,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.COPY,
                                        icon_size=16,
                                        tooltip="Copy HWID",
                                        on_click=copy_hwid_btn,
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.START,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Row(
                                [
                                    key_input,
                                    activate_btn,
                                ],
                                spacing=10,
                            ),
                            activation_status,
                            ft.Text(
                                "Send your Hardware ID to support@dataryx.com to request an activation key.",
                                color=t.TEXT_HINT,
                                size=11,
                                italic=True,
                            ),
                        ],
                        spacing=12,
                    ),
                    bgcolor=t.BG_CARD,
                    padding=20,
                    border_radius=8,
                ),
                ft.Container(height=12),
                # Upgrade Banner
                ft.Container(
                    content=ft.Column(upgrade_widgets, spacing=8),
                    bgcolor=t.BG_CARD,
                    padding=20,
                    border_radius=8,
                ),
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
        )
