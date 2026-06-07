import flet as ft
from services.auth_service import auth_service

class SubscriptionView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.expand = True
        self.bgcolor = "#13161F"
        self.padding = 20
        self.build_subscription()

    def build_subscription(self):
        title = ft.Text("Subscription & Account Settings", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)
        
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

        # Upgrade Section
        upgrade_widgets = []
        if paypal_link and ("free" in plan_name.lower() or is_expired):
            upgrade_widgets.extend([
                ft.Text("Upgrade Plan" if not is_expired else "Renew Plan", size=15, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_300),
                ft.Text("Unlock premium data nodes, unlimited pipelines, and file sizes up to 100MB." if not is_expired else "Your Pro subscription has expired. Renew your subscription to restore full access to premium features.", color=ft.Colors.GREY_400, size=13),
                ft.Container(height=8),
                ft.Button(
                    content="Upgrade to Pro with PayPal" if not is_expired else "Renew Pro with PayPal",
                    icon=ft.Icons.PAYMENT_ROUNDED,
                    bgcolor=ft.Colors.BLUE_600,
                    color=ft.Colors.WHITE,
                    url=paypal_link,
                    height=44
                )
            ])
        else:
            upgrade_widgets.append(
                ft.Text("Premium accounts are managed via your administrator workspace settings.", color=ft.Colors.GREY_500, size=13, italic=True)
            )

        self.content = ft.Column(
            [
                title,
                ft.Divider(color=ft.Colors.GREY_800),
                ft.Row(
                    [
                        # Profile Info Card
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text("Profile details", size=15, weight=ft.FontWeight.W_600, color=ft.Colors.GREY_300),
                                    ft.Divider(color=ft.Colors.GREY_800),
                                    ft.Row([ft.Text("Name:", color=ft.Colors.GREY_500, size=13), ft.Text(name, color=ft.Colors.WHITE, size=13, weight=ft.FontWeight.BOLD)]),
                                    ft.Row([ft.Text("Email:", color=ft.Colors.GREY_500, size=13), ft.Text(email, color=ft.Colors.WHITE, size=13)]),
                                ],
                                spacing=12
                            ),
                            expand=True,
                            bgcolor="#1E2330",
                            padding=20,
                            border_radius=8
                        ),
                        # Subscription Limit Card
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text("Subscription", size=15, weight=ft.FontWeight.W_600, color=ft.Colors.GREY_300),
                                    ft.Divider(color=ft.Colors.GREY_800),
                                    ft.Row([ft.Text("Active Plan:", color=ft.Colors.GREY_500, size=13), ft.Text(plan_name, color=ft.Colors.BLUE_300, size=13, weight=ft.FontWeight.BOLD)]),
                                    ft.Row([ft.Text("Remaining Runs:", color=ft.Colors.GREY_500, size=13), ft.Text(str(remaining_runs), color=ft.Colors.WHITE, size=13, weight=ft.FontWeight.BOLD)]),
                                    ft.Row([ft.Text("Max File Size Limit:", color=ft.Colors.GREY_500, size=13), ft.Text(f"{max_size} MB", color=ft.Colors.WHITE, size=13)]),
                                    ft.Row([ft.Text("Expiry:", color=ft.Colors.GREY_500, size=13), ft.Text(str(expiry)[:10], color=ft.Colors.WHITE, size=13)]),
                                    ft.Row([ft.Text("Status:", color=ft.Colors.GREY_500, size=13), ft.Text(status_text, color=status_color, size=13, weight=ft.FontWeight.BOLD)]),
                                ],
                                spacing=10
                            ),
                            expand=True,
                            bgcolor="#1E2330",
                            padding=20,
                            border_radius=8
                        )
                    ],
                    spacing=16
                ),
                ft.Container(height=12),
                # Upgrade Banner
                ft.Container(
                    content=ft.Column(upgrade_widgets, spacing=8),
                    bgcolor="#1E2330",
                    padding=20,
                    border_radius=8
                )
            ],
            spacing=16
        )
