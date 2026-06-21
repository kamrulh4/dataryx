"""
Data Profiler View
------------------
A modal dialog that shows column-level statistics for a selected node's output.
Powered by Polars .describe() — no extra dependencies needed.

Usage:
    from views.data_profiler_view import open_data_profiler
    open_data_profiler(page, node)
"""

import flet as ft
import polars as pl
from components.theme import get_theme, is_dark


# ── Colour helpers ────────────────────────────────────────────────────────────
_TYPE_COLOURS = {
    "Int":      "#60A5FA",   # blue
    "Float":    "#34D399",   # teal
    "Str":      "#FBBF24",   # amber
    "Date":     "#A78BFA",   # purple
    "Bool":     "#F87171",   # red
    "Other":    "#94A3B8",   # slate
}

def _type_colour(dtype_str: str) -> str:
    for key, col in _TYPE_COLOURS.items():
        if key.lower() in dtype_str.lower():
            return col
    return _TYPE_COLOURS["Other"]


def _dtype_badge(dtype_str: str) -> ft.Container:
    colour = _type_colour(dtype_str)
    short = dtype_str.split("(")[0][:10]   # trim long generic names
    return ft.Container(
        content=ft.Text(short, size=9, color="#0F172A", weight=ft.FontWeight.W_700),
        bgcolor=colour,
        border_radius=4,
        padding=ft.Padding(left=5, top=2, right=5, bottom=2),
    )


def _null_bar(null_pct: float, page: ft.Page = None) -> ft.Stack:
    """Tiny progress-bar showing null percentage."""
    bar_w = 80
    filled = max(0.0, min(1.0, null_pct / 100.0))
    colour = "#F87171" if filled > 0.3 else ("#FBBF24" if filled > 0.05 else "#34D399")
    bg = "#1E2A3A" if (page is None or is_dark(page)) else "#E2E8F0"
    return ft.Stack(
        [
            ft.Container(width=bar_w, height=8, bgcolor=bg, border_radius=4),
            ft.Container(
                width=max(4.0, bar_w * filled),
                height=8,
                bgcolor=colour,
                border_radius=4,
            ),
        ],
    )


# ── Core profiling logic ──────────────────────────────────────────────────────
def _profile_df(df: pl.DataFrame) -> list[dict]:
    """Return a list of per-column stat dicts from a Polars DataFrame."""
    rows = []
    n_rows = len(df)

    for col_name in df.columns:
        series = df[col_name]
        dtype_str = str(series.dtype)

        null_count = series.null_count()
        null_pct = (null_count / n_rows * 100) if n_rows > 0 else 0.0
        unique_count = series.n_unique()

        # Numeric stats
        min_val = max_val = mean_val = std_val = ""
        if series.dtype in (
            pl.Int8, pl.Int16, pl.Int32, pl.Int64,
            pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
            pl.Float32, pl.Float64,
        ):
            try:
                min_val  = f"{series.min():.4g}"
                max_val  = f"{series.max():.4g}"
                mean_val = f"{series.mean():.4g}"
                std_val  = f"{series.std():.4g}"
            except Exception:
                pass

        # Sample values (first 3 non-null, stringified)
        try:
            samples = (
                series.drop_nulls()
                .head(3)
                .cast(pl.Utf8)
                .to_list()
            )
            sample_str = ", ".join(str(s) for s in samples)
            if len(sample_str) > 40:
                sample_str = sample_str[:37] + "…"
        except Exception:
            sample_str = ""

        rows.append({
            "name":     col_name,
            "dtype":    dtype_str,
            "nulls":    null_count,
            "null_pct": null_pct,
            "unique":   unique_count,
            "min":      min_val,
            "max":      max_val,
            "mean":     mean_val,
            "std":      std_val,
            "samples":  sample_str,
        })

    return rows


# ── Column header helper ──────────────────────────────────────────────────────
def _col_header(label: str, width: int) -> ft.Container:
    return ft.Container(
        content=ft.Text(label, size=10, color=ft.Colors.GREY_400,
                        weight=ft.FontWeight.W_600),
        width=width,
        alignment=ft.Alignment(-1, 0),   # center-left
    )


def _cell(text: str, width: int, colour: str = ft.Colors.GREY_300,
          align: ft.TextAlign = ft.TextAlign.LEFT) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=11, color=colour, text_align=align,
                        overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
        width=width,
    )


# ── Build the dialog content ──────────────────────────────────────────────────
def _build_profiler_content(
    df: pl.DataFrame,
    node_label: str,
    page: ft.Page = None,
) -> ft.Column:

    n_rows, n_cols = df.shape
    profile = _profile_df(df)

    t = get_theme(page) if page else None
    
    # ── Summary cards ────────────────────────────────────────────────────────
    def _summary_card(icon, label: str, value: str, colour: str) -> ft.Container:
        card_bg = "#1E2A3A" if (page is None or is_dark(page)) else t.BG_CARD_ALT
        card_border = "#2E3D50" if (page is None or is_dark(page)) else t.BORDER
        label_color = ft.Colors.GREY_400 if (page is None or is_dark(page)) else t.TEXT_SECONDARY
        val_color = ft.Colors.WHITE if (page is None or is_dark(page)) else t.TEXT_PRIMARY
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [ft.Icon(icon, size=16, color=colour),
                         ft.Text(label, size=10, color=label_color)],
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(value, size=18, weight=ft.FontWeight.W_700,
                            color=val_color),
                ],
                spacing=2,
            ),
            bgcolor=card_bg,
            border_radius=8,
            padding=ft.Padding(left=14, top=10, right=14, bottom=10),
            border=ft.Border.all(1, card_border),
            expand=True,
        )

    total_nulls  = sum(r["nulls"] for r in profile)
    num_cols_cnt = sum(
        1 for r in profile
        if any(t in r["dtype"] for t in ("Int", "Float", "UInt"))
    )

    summary_row = ft.Row(
        [
            _summary_card(ft.Icons.GRID_ON_ROUNDED,        "Rows",    f"{n_rows:,}", "#60A5FA"),
            _summary_card(ft.Icons.VIEW_COLUMN_ROUNDED,    "Columns", f"{n_cols:,}", "#A78BFA"),
            _summary_card(ft.Icons.NUMBERS_ROUNDED,        "Numeric", f"{num_cols_cnt:,}", "#34D399"),
            _summary_card(ft.Icons.BLOCK_ROUNDED,          "Nulls",   f"{total_nulls:,}", "#F87171"),
        ],
        spacing=8,
    )

    # ── Table header ─────────────────────────────────────────────────────────
    WIDTHS = dict(name=150, dtype=90, nulls=70, null_bar=90, unique=65,
                  min=75, max=75, mean=75, std=70, samples=170)

    header_row = ft.Container(
        content=ft.Row(
            [
                _col_header("Column",   WIDTHS["name"]),
                _col_header("Type",     WIDTHS["dtype"]),
                _col_header("Nulls",    WIDTHS["nulls"]),
                _col_header("Null %",   WIDTHS["null_bar"]),
                _col_header("Unique",   WIDTHS["unique"]),
                _col_header("Min",      WIDTHS["min"]),
                _col_header("Max",      WIDTHS["max"]),
                _col_header("Mean",     WIDTHS["mean"]),
                _col_header("Std",      WIDTHS["std"]),
                _col_header("Samples",  WIDTHS["samples"]),
            ],
            spacing=6,
        ),
        bgcolor=t.BG_PAGE if t else "#13161F",
        padding=ft.Padding(left=12, top=8, right=12, bottom=8),
        border_radius=ft.BorderRadius(top_left=6, top_right=6,
                                       bottom_left=0, bottom_right=0),
        border=ft.Border(bottom=ft.border.BorderSide(1, t.BORDER if t else "#2E3D50")),
    )

    # ── Table rows ───────────────────────────────────────────────────────────
    data_rows = []
    for i, r in enumerate(profile):
        if t:
            if is_dark(page):
                row_bg = "#151B27" if i % 2 == 0 else "#1A2030"
                cell_text_color = ft.Colors.WHITE
                unique_color = ft.Colors.GREY_300
                samples_color = ft.Colors.GREY_400
                nulls_color = "#F87171" if r["nulls"] > 0 else ft.Colors.GREY_500
            else:
                row_bg = "#FFFFFF" if i % 2 == 0 else t.BG_PAGE
                cell_text_color = t.TEXT_PRIMARY
                unique_color = t.TEXT_SECONDARY
                samples_color = t.TEXT_SECONDARY
                nulls_color = "#DC2626" if r["nulls"] > 0 else t.TEXT_HINT
        else:
            row_bg = "#151B27" if i % 2 == 0 else "#1A2030"
            cell_text_color = ft.Colors.WHITE
            unique_color = ft.Colors.GREY_300
            samples_color = ft.Colors.GREY_400
            nulls_color = "#F87171" if r["nulls"] > 0 else ft.Colors.GREY_500

        row_ctrl = ft.Container(
            content=ft.Row(
                [
                    _cell(r["name"],    WIDTHS["name"],    cell_text_color),
                    ft.Container(content=_dtype_badge(r["dtype"]), width=WIDTHS["dtype"]),
                    _cell(f'{r["nulls"]:,}', WIDTHS["nulls"], nulls_color),
                    ft.Container(
                        content=_null_bar(r["null_pct"], page),
                        width=WIDTHS["null_bar"],
                    ),
                    _cell(f'{r["unique"]:,}', WIDTHS["unique"], unique_color),
                    _cell(r["min"],   WIDTHS["min"],   "#34D399" if (page is None or is_dark(page)) else ft.Colors.GREEN_700),
                    _cell(r["max"],   WIDTHS["max"],   "#34D399" if (page is None or is_dark(page)) else ft.Colors.GREEN_700),
                    _cell(r["mean"],  WIDTHS["mean"],  "#60A5FA" if (page is None or is_dark(page)) else ft.Colors.BLUE_700),
                    _cell(r["std"],   WIDTHS["std"],   "#60A5FA" if (page is None or is_dark(page)) else ft.Colors.BLUE_700),
                    _cell(r["samples"], WIDTHS["samples"], samples_color),
                ],
                spacing=6,
            ),
            bgcolor=row_bg,
            padding=ft.Padding(left=12, top=7, right=12, bottom=7),
        )
        data_rows.append(row_ctrl)

    table_body = ft.Column(data_rows, spacing=0)

    table_scroll = ft.Container(
        content=ft.Row(
            [
                ft.Column(
                    [header_row, table_body],
                    spacing=0,
                    scroll=ft.ScrollMode.AUTO,
                )
            ],
            scroll=ft.ScrollMode.ALWAYS,
        ),
        height=420,
        border=ft.Border.all(1, t.BORDER if t else "#2E3D50"),
        border_radius=6,
    )

    # ── Assemble ─────────────────────────────────────────────────────────────
    return ft.Column(
        [
            ft.Row(
                [
                    ft.Icon(ft.Icons.QUERY_STATS_ROUNDED,
                            size=20, color="#60A5FA" if (page is None or is_dark(page)) else ft.Colors.BLUE_600),
                    ft.Text(
                        f"Data Profile — {node_label}",
                        size=16,
                        weight=ft.FontWeight.W_700,
                        color=t.TEXT_PRIMARY if t else ft.Colors.WHITE,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Divider(color=t.DIVIDER if t else "#2E3D50", height=1),
            summary_row,
            ft.Container(height=4),
            table_scroll,
        ],
        spacing=10,
        tight=True,
    )


# ── Public entry point ────────────────────────────────────────────────────────
def open_data_profiler(page: ft.Page, node) -> None:
    """
    Open the Data Profiler dialog for the given node.

    Args:
        page: The active Flet Page.
        node: A FlowNode whose executed Polars data we want to profile.
    """
    df: pl.DataFrame | None = None
    node_label = getattr(node, "node_type", "Node").replace("_", " ").title()

    # ── Guard: only profile nodes that have data ────────────────────────────
    node_stats = getattr(node, "node_stats", None)
    has_run = getattr(node_stats, "has_run_with_current_setup", False)
    node_type = getattr(node, "node_type", "")

    # Check if manual_input has configured data (available without running)
    has_manual_data = (
        node_type == "manual_input"
        and getattr(node, "setting_input", None)
        and getattr(node.setting_input, "raw_data_format", None)
        and getattr(node.setting_input.raw_data_format, "columns", None)
    )

    if not has_run and not has_manual_data:
        snack = ft.SnackBar(
            content=ft.Text(
                "⚠ Run the pipeline first to profile this node's data.",
                color=ft.Colors.WHITE,
            ),
            bgcolor="#2E3D50",
            open=True,
        )
        page.overlay.append(snack)
        page.update()
        return

    # ── If manual_input and not yet run, build df from raw_data_format ───────
    if not has_run and has_manual_data:
        try:
            raw = node.setting_input.raw_data_format
            col_names = [c.name for c in raw.columns]
            col_data = raw.data  # list of column arrays (column-major)
            if col_data and len(col_data) > 0:
                num_rows = len(col_data[0])
                rows_dicts = []
                for ri in range(num_rows):
                    row = {col_names[ci]: (col_data[ci][ri] if ri < len(col_data[ci]) else None)
                           for ci in range(len(col_names))}
                    rows_dicts.append(row)
                if rows_dicts:
                    df = pl.DataFrame(rows_dicts)
        except Exception:
            pass

        if df is None or df.is_empty():
            snack = ft.SnackBar(
                content=ft.Text(
                    "⚠ No data configured in this node yet.",
                    color=ft.Colors.WHITE,
                ),
                bgcolor="#2E3D50",
                open=True,
            )
            page.overlay.append(snack)
            page.update()
        else:
            # Show profiler with the configured data
            content = _build_profiler_content(df, node_label, page)

            def _close_early(_):
                page.pop_dialog()

            dlg = ft.AlertDialog(
                modal=True,
                bgcolor=get_theme(page).BG_PAGE,
                shape=ft.RoundedRectangleBorder(radius=10),
                content=ft.Container(
                    content=content,
                    width=1000,
                    padding=ft.Padding(left=4, top=4, right=4, bottom=4),
                ),
                actions=[
                    ft.TextButton(
                        "Close",
                        style=ft.ButtonStyle(color=ft.Colors.BLUE_600 if not is_dark(page) else ft.Colors.BLUE_400),
                        on_click=_close_early,
                    )
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            page.show_dialog(dlg)
        return

    # --- Strategy 1: get_resulting_data().collect() ---
    # FlowDataEngine uses .collect() to get a pl.DataFrame
    try:
        result_obj = node.get_resulting_data()
        if result_obj is not None:
            raw_df = result_obj.collect()
            if isinstance(raw_df, pl.DataFrame) and not raw_df.is_empty():
                df = raw_df
    except Exception:
        pass

    # --- Strategy 2: get_resulting_data().to_pylist() → rebuild DataFrame ---
    if df is None:
        try:
            result_obj = node.get_resulting_data()
            if result_obj is not None:
                rows = result_obj.to_pylist()
                if rows:
                    df = pl.DataFrame(rows)
        except Exception:
            pass

    # --- Strategy 3: get_table_example (same source as Data Preview panel) ---
    if df is None:
        try:
            table_ex = node.get_table_example(include_data=True)
            if table_ex and table_ex.columns and table_ex.data:
                df = pl.DataFrame(table_ex.data)
        except Exception:
            pass

    # Nothing available
    if df is None or df.is_empty():
        snack = ft.SnackBar(
            content=ft.Text(
                "⚠ No data available yet. Run the pipeline first.",
                color=ft.Colors.WHITE,
            ),
            bgcolor="#2E3D50",
            open=True,
        )
        page.overlay.append(snack)
        page.update()
        return

    content = _build_profiler_content(df, node_label, page)

    def _close_dlg(_):
        page.pop_dialog()

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=get_theme(page).BG_PAGE,
        shape=ft.RoundedRectangleBorder(radius=10),
        content=ft.Container(
            content=content,
            width=1000,
            padding=ft.Padding(left=4, top=4, right=4, bottom=4),
        ),
        actions=[
            ft.TextButton(
                "Close",
                style=ft.ButtonStyle(color=ft.Colors.BLUE_600 if not is_dark(page) else ft.Colors.BLUE_400),
                on_click=_close_dlg,
            )
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    page.show_dialog(dlg)
