"""
LogsView — Execution Log Viewer
================================
Sidebar view that lets users browse and read flow execution log files.

Features:
  - Flow selector dropdown (shows all flows with existing log files)
  - Full log content rendered with colour-coded log levels
  - Auto-refresh every 3 seconds while mounted
  - "Clear Logs" and "Copy Path" actions
  - Works with FlowLogger's ~/.dataryx/logs/flow_<id>.log convention
"""
import asyncio
import os
from pathlib import Path

import flet as ft

from components.theme import get_theme
from core.configs.flow_logger import FlowLogger
from shared.storage_config import storage

# ANSI-style colour map for log levels
_LEVEL_COLORS = {
    "ERROR":    "#FF5F57",   # red
    "CRITICAL": "#FF5F57",
    "WARNING":  "#FFBD2E",   # amber
    "WARN":     "#FFBD2E",
    "INFO":     "#27C93F",   # green
    "DEBUG":    "#8A9BAE",   # muted blue-grey
}
_DEFAULT_COLOR = "#C9D1D9"   # light grey (fallback)


def _color_for_line(line: str) -> str:
    """Return the foreground colour for a log line based on its level token."""
    upper = line.upper()
    for level, color in _LEVEL_COLORS.items():
        if f" {level} " in upper or f"- {level} -" in upper or f"[{level}]" in upper:
            return color
    return _DEFAULT_COLOR


def _parse_log_files() -> dict[int, Path]:
    """Return {flow_id: log_path} for all existing flow log files."""
    logs_dir = storage.logs_directory
    result: dict[int, Path] = {}
    if not logs_dir.exists():
        return result
    for fp in sorted(logs_dir.glob("flow_*.log")):
        try:
            flow_id = int(fp.stem.split("_")[1])
            result[flow_id] = fp
        except (IndexError, ValueError):
            continue
    return result


def _read_log_file(log_path: Path) -> list[str]:
    """Read a log file and return its lines (newest first)."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        # Show newest entries at top
        return list(reversed(lines))
    except FileNotFoundError:
        return ["(Log file not found — run a flow to generate logs)"]
    except Exception as exc:
        return [f"(Error reading log file: {exc})"]


class LogsView(ft.Container):
    """Full-page execution log viewer."""

    REFRESH_INTERVAL = 3   # seconds

    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.expand = True
        self._running = False
        self._selected_flow_id: int | None = None

        t = get_theme(page)
        self.bgcolor = t.BG_PAGE
        self.padding = 24

        # ── Dropdown for flow selection ───────────────────────────────────
        self._flow_dropdown = ft.Dropdown(
            label="Select Flow",
            hint_text="Choose a flow to view its logs",
            height=48,
            text_size=13,
            border_color=t.BORDER,
            expand=True,
        )
        self._flow_dropdown.on_change = self._on_flow_selected

        # ── Log content area ──────────────────────────────────────────────
        self._log_column = ft.Column(
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=0,
        )

        # ── Status bar ────────────────────────────────────────────────────
        self._status_text = ft.Text(
            "Select a flow to view its execution logs.",
            size=11,
            color=t.TEXT_HINT,
            italic=True,
        )

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        t = get_theme(self.main_page)

        # ── Action buttons ────────────────────────────────────────────────
        refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_color=t.TEXT_SECONDARY,
            icon_size=18,
            tooltip="Refresh logs now",
            on_click=lambda _: self._refresh_log_content(),
        )
        clear_btn = ft.IconButton(
            icon=ft.Icons.DELETE_SWEEP_ROUNDED,
            icon_color=ft.Colors.RED_400,
            icon_size=18,
            tooltip="Clear log file",
            on_click=self._on_clear_logs,
        )
        path_btn = ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
            icon_color=t.TEXT_SECONDARY,
            icon_size=18,
            tooltip="Show log file path",
            on_click=self._on_show_path,
        )

        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = ft.Row(
            [
                self._flow_dropdown,
                refresh_btn,
                clear_btn,
                path_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        )

        # ── Log display card ──────────────────────────────────────────────
        log_card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.ARTICLE_ROUNDED, color=ft.Colors.BLUE_400, size=16),
                            ft.Text("Execution Output", size=13, weight=ft.FontWeight.BOLD, color=t.TEXT_PRIMARY),
                        ],
                        spacing=8,
                    ),
                    ft.Divider(color=t.DIVIDER, height=1),
                    ft.Container(
                        content=self._log_column,
                        expand=True,
                        bgcolor=t.BG_PAGE,
                        border_radius=4,
                        padding=ft.Padding(left=10, top=6, right=10, bottom=6),
                    ),
                ],
                spacing=10,
                expand=True,
            ),
            bgcolor=t.BG_CARD,
            border_radius=10,
            padding=16,
            expand=True,
            border=ft.Border.all(1, t.BORDER),
        )

        # ── Header ────────────────────────────────────────────────────────
        header = ft.Row(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.RECEIPT_LONG_ROUNDED, color=ft.Colors.BLUE_400, size=22),
                        ft.Text(
                            "Execution Logs",
                            size=20,
                            weight=ft.FontWeight.BOLD,
                            color=t.TEXT_PRIMARY,
                        ),
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Container(
                                content=ft.Text("AUTO", size=9, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                                bgcolor=ft.Colors.GREEN_600,
                                padding=ft.Padding(left=6, right=6, top=2, bottom=2),
                                border_radius=10,
                            ),
                            ft.Text(f"Refresh every {self.REFRESH_INTERVAL}s", size=11, color=t.TEXT_HINT),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.content = ft.Column(
            [
                header,
                ft.Divider(color=get_theme(self.main_page).DIVIDER, height=1),
                ft.Container(height=8),
                toolbar,
                ft.Container(height=8),
                self._status_text,
                ft.Container(height=4),
                log_card,
            ],
            expand=True,
            spacing=0,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def did_mount(self):
        self._running = True
        self._populate_dropdown()
        self.main_page.run_task(self._auto_refresh_loop)

    def will_unmount(self):
        self._running = False

    # ── Data loading ──────────────────────────────────────────────────────────

    def _populate_dropdown(self):
        """Fill the dropdown with available flow log files."""
        log_files = _parse_log_files()
        self._flow_dropdown.options = []

        if not log_files:
            self._flow_dropdown.options.append(
                ft.dropdown.Option(key="__none__", text="No log files found — run a flow first")
            )
            self.update()
            return

        for flow_id, log_path in log_files.items():
            size_kb = log_path.stat().st_size / 1024 if log_path.exists() else 0
            self._flow_dropdown.options.append(
                ft.dropdown.Option(
                    key=str(flow_id),
                    text=f"Flow {flow_id}  ({size_kb:.1f} KB)",
                )
            )

        # Auto-select last flow if still valid
        if self._selected_flow_id and self._selected_flow_id in log_files:
            self._flow_dropdown.value = str(self._selected_flow_id)
        elif log_files:
            # Auto-select first entry
            first_id = next(iter(log_files))
            self._flow_dropdown.value = str(first_id)
            self._selected_flow_id = first_id

        self.update()

    def _refresh_log_content(self):
        """Read selected log file and render lines into the UI."""
        t = get_theme(self.main_page)

        if self._selected_flow_id is None:
            self._log_column.controls = [
                ft.Text(
                    "Select a flow from the dropdown above.",
                    size=13,
                    color=t.TEXT_HINT,
                    italic=True,
                )
            ]
            self.update()
            return

        log_path = storage.logs_directory / f"flow_{self._selected_flow_id}.log"
        lines = _read_log_file(log_path)

        if not lines:
            self._log_column.controls = [
                ft.Text("(Log file is empty)", size=12, color=t.TEXT_HINT, italic=True)
            ]
        else:
            self._log_column.controls = [
                ft.Text(
                    line.rstrip("\n"),
                    size=11,
                    color=_color_for_line(line),
                    selectable=True,
                    font_family="monospace",
                    no_wrap=True,
                )
                for line in lines[:2000]   # cap at 2000 lines for performance
            ]

        # Update status bar
        total = len(lines)
        shown = min(total, 2000)
        self._status_text.value = (
            f"Showing {shown} of {total} log lines for Flow {self._selected_flow_id} "
            f"| {log_path}"
        )
        self.update()

    async def _auto_refresh_loop(self):
        """Refresh log content every REFRESH_INTERVAL seconds."""
        while self._running:
            await asyncio.sleep(self.REFRESH_INTERVAL)
            if self._running and self._selected_flow_id is not None:
                self._populate_dropdown()
                self._refresh_log_content()

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_flow_selected(self, e):
        val = self._flow_dropdown.value
        if val and val != "__none__":
            try:
                self._selected_flow_id = int(val)
            except ValueError:
                self._selected_flow_id = None
        else:
            self._selected_flow_id = None
        self._refresh_log_content()

    def _on_clear_logs(self, e):
        if self._selected_flow_id is None:
            self._show_snack("No flow selected.")
            return

        log_path = storage.logs_directory / f"flow_{self._selected_flow_id}.log"
        try:
            # Route through FlowLogger (if a live instance exists for this
            # flow) so any open FileHandler gets closed/reopened around the
            # truncate. A raw open(path, "w") here would truncate the file
            # out from under an already-open FileHandler, leaving its write
            # position stale and corrupting subsequently-written log lines.
            instance = FlowLogger.get_instance(self._selected_flow_id)
            if instance is not None:
                instance.clear_log_file()
            else:
                with open(log_path, "w") as f:
                    f.write("")
            self._show_snack(f"Log cleared for Flow {self._selected_flow_id}")
            self._refresh_log_content()
        except Exception as exc:
            self._show_snack(f"Could not clear log: {exc}")

    def _on_show_path(self, e):
        if self._selected_flow_id is None:
            self._show_snack("No flow selected.")
            return
        log_path = storage.logs_directory / f"flow_{self._selected_flow_id}.log"

        dlg = ft.AlertDialog(
            title=ft.Text("Log File Path"),
            content=ft.TextField(
                value=str(log_path),
                read_only=True,
                expand=True,
                text_size=12,
                text_style=ft.TextStyle(font_family="monospace"),
            ),
            actions_alignment=ft.MainAxisAlignment.END,
        )

        def _close(_):
            self.main_page.pop_dialog()

        dlg.actions = [ft.TextButton("Close", on_click=_close)]
        self.main_page.show_dialog(dlg)

    def _show_snack(self, msg: str):
        snack = ft.SnackBar(content=ft.Text(msg))
        self.main_page.overlay.append(snack)
        snack.open = True
        self.main_page.update()
