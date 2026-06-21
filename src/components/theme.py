"""
Dataryx Theme System
Centralised colour tokens for Dark and Light modes.

Usage:
    from components.theme import get_theme, is_dark_mode

    t = get_theme(page)
    container = ft.Container(bgcolor=t.BG_PAGE)
"""
import flet as ft

# ---------------------------------------------------------------------------
# Token definitions
# ---------------------------------------------------------------------------

class _DarkTheme:
    BG_PAGE      = "#13161F"   # outermost page / view background
    BG_SIDEBAR   = "#1A1F2C"   # sidebar background
    BG_CARD      = "#1E2330"   # card / panel background
    BG_CARD_ALT  = "#252B3B"   # alternate card (slightly lighter)
    BORDER       = ft.Colors.GREY_800
    TEXT_PRIMARY = ft.Colors.WHITE
    TEXT_SECONDARY = ft.Colors.GREY_300
    TEXT_HINT    = ft.Colors.GREY_500
    DIVIDER      = ft.Colors.GREY_800


class _LightTheme:
    BG_PAGE      = "#F4F6FA"
    BG_SIDEBAR   = "#FFFFFF"
    BG_CARD      = "#FFFFFF"
    BG_CARD_ALT  = "#EEF1F8"
    BORDER       = ft.Colors.GREY_300
    TEXT_PRIMARY = "#111827"
    TEXT_SECONDARY = "#374151"
    TEXT_HINT    = "#6B7280"
    DIVIDER      = ft.Colors.GREY_300


DARK  = _DarkTheme()
LIGHT = _LightTheme()


def get_theme(page: ft.Page):
    """Return the correct theme token set for the current page theme_mode."""
    return DARK if page.theme_mode == ft.ThemeMode.DARK else LIGHT


def is_dark(page: ft.Page) -> bool:
    return page.theme_mode == ft.ThemeMode.DARK


def toggle_theme(page: ft.Page):
    """Flip between dark and light mode and persist the choice."""
    if page.theme_mode == ft.ThemeMode.DARK:
        page.theme_mode = ft.ThemeMode.LIGHT
    else:
        page.theme_mode = ft.ThemeMode.DARK
    page.update()
