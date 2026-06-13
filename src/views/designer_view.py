import flet as ft
import polars as pl
from core import flow_file_handler
from core.dataryx.code_generator.code_generator import export_flow_to_polars
from core.schemas.input_schema import NodePromise, NodeDatasource
from services.auth_service import auth_service
import traceback
import random
import inspect
from core.schemas import input_schema


def instantiate_with_defaults(node_model, initial_params):
    import typing
    from pydantic import BaseModel
    from pydantic_core import PydanticUndefined

    def get_default_for_field(field_info):
        if field_info.default is not PydanticUndefined:
            return field_info.default
        if field_info.default_factory is not None:
            return field_info.default_factory()
        return get_default_for_type(field_info.annotation)

    def get_default_for_type(annotation):
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
        if origin is typing.Annotated:
            return get_default_for_type(args[0])
        if origin is typing.Union or (
            hasattr(typing, "UnionType") and origin is typing.UnionType
        ):
            for arg in args:
                if arg is not type(None):
                    return get_default_for_type(arg)
        if origin is typing.Literal:
            if args:
                return args[0]
            return None
        if origin is list:
            return []
        if origin is dict:
            return {}
        if isinstance(annotation, type):
            if issubclass(annotation, BaseModel):
                sub_params = {}
                for sub_field_name, sub_field_info in annotation.model_fields.items():
                    if sub_field_info.is_required():
                        sub_params[sub_field_name] = get_default_for_field(
                            sub_field_info
                        )
                try:
                    return annotation(**sub_params)
                except Exception:
                    # If validation fails with only required fields, try populating all fields (both required and optional)
                    for (
                        sub_field_name,
                        sub_field_info,
                    ) in annotation.model_fields.items():
                        if sub_field_name not in sub_params:
                            sub_params[sub_field_name] = get_default_for_field(
                                sub_field_info
                            )
                    try:
                        return annotation(**sub_params)
                    except Exception:
                        return annotation.model_construct(**sub_params)
            elif issubclass(annotation, list):
                return []
            elif issubclass(annotation, dict):
                return {}
            elif issubclass(annotation, str):
                return ""
            elif issubclass(annotation, int):
                return 0
            elif issubclass(annotation, float):
                return 0.0
            elif issubclass(annotation, bool):
                return False
        return None

    params = initial_params.copy()
    for field_name, field_info in node_model.model_fields.items():
        if field_name not in params and field_info.is_required():
            params[field_name] = get_default_for_field(field_info)
    try:
        return node_model(**params)
    except Exception:
        # Fallback to populating all optional fields if initial instantiation fails
        for field_name, field_info in node_model.model_fields.items():
            if field_name not in params:
                params[field_name] = get_default_for_field(field_info)
        try:
            return node_model(**params)
        except Exception:
            # Create a bare/mock settings object if validation still fails
            return node_model.model_construct(**params)


from views.canvas_view import CanvasView


class DesignerView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.main_page.designer_view = self
        self.is_typing = False
        self.active_flow_id = None
        self.selected_node_id = None
        self.copied_node_id = None
        self.flow_ref = None
        self.expand = True
        self.bgcolor = "#13161F"
        self.padding = 20

        self.build_designer()

    def build_designer(self):
        # ── FilePicker service (registered once in did_mount via page.services) ──
        self._file_picker_target = None  # TextField to fill when pick completes
        self._file_picker = ft.FilePicker()

        # Header panel — use PopupMenuButton
        self.flow_label_text = ft.Text(
            "Select Flow", size=13, color=ft.Colors.WHITE, weight=ft.FontWeight.W_500
        )
        self.flow_dropdown = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Row(
                    [
                        self.flow_label_text,
                        ft.Icon(
                            ft.Icons.ARROW_DROP_DOWN_ROUNDED,
                            color=ft.Colors.WHITE,
                            size=18,
                        ),
                    ],
                    spacing=4,
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                bgcolor="#1A1D26",
                border=ft.Border.all(1, ft.Colors.GREY_700),
                border_radius=6,
                padding=ft.Padding(left=10, top=8, right=10, bottom=8),
                width=240,
                height=40,
            ),
            items=[],
        )

        self.new_flow_btn = ft.IconButton(
            icon=ft.Icons.ADD_CIRCLE_OUTLINE_ROUNDED,
            icon_color=ft.Colors.BLUE_400,
            icon_size=20,
            tooltip="Create New Flow",
            on_click=self.create_new_flow,
        )

        flow_selector_row = ft.Row(
            [
                ft.Text(
                    "Flow:",
                    size=14,
                    weight=ft.FontWeight.W_600,
                    color=ft.Colors.GREY_400,
                ),
                self.flow_dropdown,
                self.new_flow_btn,
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        run_btn = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW_ROUNDED,
            icon_color=ft.Colors.GREEN_400,
            tooltip="Run Pipeline",
            disabled=True,
            on_click=self.run_pipeline,
        )

        export_btn = ft.IconButton(
            icon=ft.Icons.CODE_ROUNDED,
            icon_color=ft.Colors.BLUE_400,
            tooltip="Export Polars Code",
            disabled=True,
            on_click=self.export_code,
        )

        # Left column - Canvas View
        self.canvas = CanvasView(
            page=self.main_page,
            flow_ref=None,
            on_node_selected=self.select_node,
            on_node_deleted=self.delete_node,
        )

        left_panel = ft.Container(content=self.canvas, expand=True)

        # Right panel — Step Configuration with collapse toggle
        config_container = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        self._config_collapsed = False

        config_body = ft.Container(
            content=ft.Column(
                [ft.Divider(color=ft.Colors.GREY_800, height=1), config_container],
                spacing=8,
                expand=True,
            ),
            expand=True,
            visible=True,
        )

        def toggle_config(e):
            self._config_collapsed = not self._config_collapsed
            config_body.visible = not self._config_collapsed
            config_title_row.visible = not self._config_collapsed
            config_collapsed_tab.visible = self._config_collapsed
            config_panel.width = 36 if self._config_collapsed else 380
            config_toggle_btn.icon = (
                ft.Icons.CHEVRON_RIGHT_ROUNDED
                if self._config_collapsed
                else ft.Icons.CHEVRON_LEFT_ROUNDED
            )
            top_area.update()

        config_toggle_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
            icon_color=ft.Colors.GREY_400,
            icon_size=16,
            padding=0,
            width=28,
            height=28,
            tooltip="Collapse Step Configuration",
            on_click=toggle_config,
        )

        config_title_row = ft.Row(
            [
                ft.Icon(ft.Icons.SETTINGS_ROUNDED, size=15, color=ft.Colors.BLUE_400),
                ft.Text(
                    "Step Configuration",
                    size=13,
                    weight=ft.FontWeight.W_600,
                    color=ft.Colors.GREY_300,
                    expand=True,
                ),
                config_toggle_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
            visible=True,
        )

        # Narrow tab shown when collapsed
        config_collapsed_tab = ft.Container(
            content=ft.Column(
                [
                    ft.IconButton(
                        icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
                        icon_color=ft.Colors.GREY_400,
                        icon_size=16,
                        padding=0,
                        width=28,
                        height=28,
                        tooltip="Expand Step Configuration",
                        on_click=toggle_config,
                    ),
                    ft.Container(
                        content=ft.Text(
                            "Config",
                            size=10,
                            color=ft.Colors.GREY_500,
                            weight=ft.FontWeight.W_500,
                        ),
                        rotate=ft.Rotate(angle=1.5708),
                        margin=ft.Margin(top=24, bottom=0, left=0, right=0),
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            ),
            visible=False,
        )

        config_panel = ft.Container(
            content=ft.Column(
                [config_title_row, config_collapsed_tab, config_body],
                spacing=6,
                expand=True,
            ),
            bgcolor="#1E2330",
            padding=ft.Padding(left=12, top=10, right=12, bottom=12),
            border_radius=8,
            width=380,
            expand=False,
        )

        # Top area: Canvas (expands) + Config (fixed, collapsible right)
        top_area = ft.Row(
            [left_panel, config_panel],
            expand=True,
            spacing=8,
        )

        # Bottom area: Full-width Data Preview panel
        preview_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("No active step", color=ft.Colors.GREY_500))
            ],
            rows=[],
            heading_row_color=ft.Colors.BLUE_900,
            border_radius=6,
            column_spacing=20,
            data_row_min_height=36,
            data_row_max_height=36,
        )

        # Horizontal scroll wrapper for wide tables
        preview_scroll = ft.Row(
            [preview_table],
            scroll=ft.ScrollMode.ALWAYS,
            expand=True,
        )

        # Toggle collapse button for preview panel
        self._preview_collapsed = False

        def toggle_preview(e):
            self._preview_collapsed = not self._preview_collapsed
            preview_body.visible = not self._preview_collapsed
            preview_toggle_btn.icon = (
                ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
                if self._preview_collapsed
                else ft.Icons.KEYBOARD_ARROW_UP_ROUNDED
            )
            preview_panel.update()

        preview_toggle_btn = ft.IconButton(
            icon=ft.Icons.KEYBOARD_ARROW_UP_ROUNDED,
            icon_color=ft.Colors.GREY_400,
            icon_size=18,
            on_click=toggle_preview,
            tooltip="Collapse/Expand Data Preview",
        )

        # Both horizontal (Row) + vertical (Column) scroll for big tables
        preview_body = ft.Container(
            content=ft.Column(
                [preview_scroll],
                scroll=ft.ScrollMode.ALWAYS,
                expand=True,
            ),
            height=200,
            padding=ft.Padding(left=0, top=8, right=0, bottom=0),
        )

        preview_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.TABLE_CHART_ROUNDED,
                                size=16,
                                color=ft.Colors.BLUE_400,
                            ),
                            ft.Text(
                                "Data Preview (Polars Output)",
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color=ft.Colors.GREY_300,
                            ),
                            ft.Container(expand=True),
                            preview_toggle_btn,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    ),
                    ft.Divider(color=ft.Colors.GREY_800, height=1),
                    preview_body,
                ],
                spacing=4,
            ),
            bgcolor="#1E2330",
            padding=ft.Padding(left=16, top=10, right=16, bottom=12),
            border_radius=ft.BorderRadius(
                top_left=8, top_right=8, bottom_left=0, bottom_right=0
            ),
        )

        # Main vertical split: top (canvas+config) and bottom (preview, full width)
        main_layout = ft.Column([top_area, preview_panel], expand=True, spacing=8)

        # Save/load view references
        self.run_btn = run_btn
        self.export_btn = export_btn
        self.steps_list = ft.Column()  # mock steps list for backward compatibility
        self.config_container = config_container
        self.preview_table = preview_table

        # --- "Add Step" popup menu (grouped by node category) ---
        def _make_add_handler(node_type: str):
            return lambda _: self.add_node(node_type)

        _input_nodes = [
            ("Read Data (File)", ft.Icons.FOLDER_OPEN_ROUNDED, "read"),
            ("Database Reader", ft.Icons.STORAGE_ROUNDED, "database_reader"),
            (
                "Cloud Storage Reader",
                ft.Icons.CLOUD_DOWNLOAD_ROUNDED,
                "cloud_storage_reader",
            ),
            ("Manual Input", ft.Icons.EDIT_NOTE_ROUNDED, "manual_input"),
            ("External Source", ft.Icons.LANGUAGE_ROUNDED, "external_source"),
        ]
        _transform_nodes = [
            ("Filter Rows", ft.Icons.FILTER_ALT_ROUNDED, "filter"),
            ("Select Columns", ft.Icons.VIEW_COLUMN_ROUNDED, "select"),
            ("Formula", ft.Icons.FUNCTIONS_ROUNDED, "formula"),
            ("Sort Data", ft.Icons.SORT_ROUNDED, "sort"),
            ("Take Sample", ft.Icons.CONTENT_CUT_ROUNDED, "sample"),
            ("Drop Duplicates", ft.Icons.DEBLUR_ROUNDED, "unique"),
            ("Text to Rows", ft.Icons.WRAP_TEXT_ROUNDED, "text_to_rows"),
            ("Add Record ID", ft.Icons.TAG_ROUNDED, "record_id"),
            ("Polars Code", ft.Icons.CODE_ROUNDED, "polars_code"),
        ]
        _aggregate_nodes = [
            ("Group By", ft.Icons.WORKSPACES_ROUNDED, "group_by"),
            ("Pivot Data", ft.Icons.PIVOT_TABLE_CHART_ROUNDED, "pivot"),
            ("Unpivot Data", ft.Icons.TABLE_ROWS_ROUNDED, "unpivot"),
            ("Count Records", ft.Icons.NUMBERS_ROUNDED, "record_count"),
        ]
        _combine_nodes = [
            ("Join", ft.Icons.MERGE_ROUNDED, "join"),
            ("Union", ft.Icons.CALL_MERGE_ROUNDED, "union"),
            ("Fuzzy Match", ft.Icons.MANAGE_SEARCH_ROUNDED, "fuzzy_match"),
            ("Cross Join", ft.Icons.GRID_ON_ROUNDED, "cross_join"),
            ("Graph Solver", ft.Icons.ACCOUNT_TREE_ROUNDED, "graph_solver"),
        ]
        _output_nodes = [
            ("Write Data (File)", ft.Icons.SAVE_ROUNDED, "output"),
            ("Database Writer", ft.Icons.STORAGE_ROUNDED, "database_writer"),
            (
                "Cloud Storage Writer",
                ft.Icons.CLOUD_UPLOAD_ROUNDED,
                "cloud_storage_writer",
            ),
            ("Explore Data", ft.Icons.BAR_CHART_ROUNDED, "explore_data"),
        ]

        def _section_items(label: str, nodes):
            items = [
                ft.PopupMenuItem(
                    content=ft.Text(
                        label,
                        color=ft.Colors.GREY_500,
                        size=11,
                        weight=ft.FontWeight.BOLD,
                    ),
                    disabled=True,
                )
            ]
            for name, icon, ntype in nodes:
                items.append(
                    ft.PopupMenuItem(
                        content=ft.Row(
                            [
                                ft.Icon(icon, size=16, color=ft.Colors.BLUE_300),
                                ft.Text(name, size=13),
                            ],
                            spacing=8,
                        ),
                        on_click=_make_add_handler(ntype),
                    )
                )
            items.append(ft.PopupMenuItem())
            return items

        all_menu_items = []
        all_menu_items += _section_items("── INPUT ──", _input_nodes)
        all_menu_items += _section_items("── TRANSFORM ──", _transform_nodes)
        all_menu_items += _section_items("── AGGREGATE ──", _aggregate_nodes)
        all_menu_items += _section_items("── COMBINE ──", _combine_nodes)
        all_menu_items += _section_items("── OUTPUT ──", _output_nodes)

        add_step_menu = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.ADD_ROUNDED, size=18, color=ft.Colors.WHITE),
                        ft.Text(
                            "Add Step",
                            size=13,
                            color=ft.Colors.WHITE,
                            weight=ft.FontWeight.W_600,
                        ),
                    ],
                    spacing=6,
                ),
                bgcolor="#2563EB",
                border_radius=6,
                padding=ft.Padding(left=12, top=8, right=12, bottom=8),
            ),
            items=all_menu_items,
            tooltip="Add a new processing step",
        )
        # --- End add_step_menu ---

        header_row = ft.Row(
            [
                flow_selector_row,
                ft.Row([add_step_menu, run_btn, export_btn], spacing=8),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # NOTE: do NOT call run_task here — page is not mounted yet.
        # did_mount() will safely call initialize_default_flow after mount.

        self.content = ft.Column(
            [header_row, ft.Container(height=10), main_layout], expand=True
        )

    def did_mount(self):
        """Called by Flet after DesignerView is added to the page. Safe to kick off async work here."""
        self.main_page.on_keyboard_event = self.on_keyboard
        # Register FilePicker as a Service (page.services, not page.overlay)
        if self._file_picker not in self.main_page.services:
            self.main_page.services.append(self._file_picker)
            self.main_page.update()
        self.main_page.run_task(self.initialize_default_flow)

    def on_keyboard(self, e: ft.KeyboardEvent):
        # Ctrl+C or Cmd+C to copy selected node
        if (e.ctrl or e.meta) and e.key.lower() == "c":
            if self.selected_node_id:
                self.copied_node_id = self.selected_node_id
                snack = ft.SnackBar(content=ft.Text("Node copied to clipboard!"))
                self.main_page.overlay.append(snack)
                snack.open = True
                self.main_page.update()

        # Ctrl+V or Cmd+V to paste copied node
        elif (e.ctrl or e.meta) and e.key.lower() == "v":
            copied_node_id = self.copied_node_id
            if copied_node_id and self.flow_ref:
                src_node = self.flow_ref.get_node(copied_node_id)
                if src_node:
                    px = getattr(src_node, "pos_x", 0) + 40
                    py = getattr(src_node, "pos_y", 0) + 40
                    self.add_node_at_pos(src_node.node_type, px, py)
                    snack = ft.SnackBar(content=ft.Text("Node pasted!"))
                    self.main_page.overlay.append(snack)
                    snack.open = True
                    self.main_page.update()

        # Delete key (with Ctrl/Cmd/Shift modifier) to remove selected node
        elif (e.ctrl or e.meta or e.shift) and e.key == "Delete":
            if self.selected_node_id:
                self.delete_node(self.selected_node_id)
                snack = ft.SnackBar(content=ft.Text("Node deleted!"))
                self.main_page.overlay.append(snack)
                snack.open = True
                self.main_page.update()

    def add_node_at_pos(self, node_type: str, x: int, y: int):
        if not self.flow_ref:
            return

        node_id = random.randint(1000, 9999)
        node_promise = NodePromise(
            flow_id=self.active_flow_id,
            node_id=node_id,
            node_type=node_type,
            pos_x=x,
            pos_y=y,
        )

        from core.configs.node_store.nodes import get_all_standard_nodes

        _, _, node_defaults = get_all_standard_nodes()
        has_default = (
            node_type in node_defaults and node_defaults[node_type].has_default_settings
        )

        self.flow_ref.add_node_promise(node_promise, track_history=False)

        if node_type in ["read", "read_csv"]:
            from core.schemas.input_schema import ReceivedTable, InputCsvTable, NodeRead

            rt = ReceivedTable(path="", file_type="csv", table_settings=InputCsvTable())
            initial_settings = NodeRead(
                flow_id=self.active_flow_id,
                node_id=node_id,
                node_type="read",
                received_file=rt,
            )
            self.flow_ref.add_read(initial_settings)
        elif node_type == "explore_data":
            self.flow_ref.add_initial_node_analysis(node_promise)
        else:
            if has_default:
                setting_name_ref = "node" + node_type.replace("_", "")
                node_model = None
                for ref_name, ref in inspect.getmodule(input_schema).__dict__.items():
                    if ref_name.lower() == setting_name_ref:
                        node_model = ref
                        break

                if node_model:
                    try:
                        add_func = getattr(self.flow_ref, "add_" + node_type)
                        initial_params = {
                            "flow_id": self.active_flow_id,
                            "node_id": node_id,
                            "cache_results": False,
                            "pos_x": x,
                            "pos_y": y,
                            "node_type": node_type,
                        }
                        initial_settings = node_model(**initial_params)
                        add_func(initial_settings)
                    except Exception as e:
                        print(f"Error cloning settings for {node_type}:", e)

        self.save_active_flow()
        self.selected_node_id = node_id
        self.update_steps_ui()
        self.update_config_ui()
        self.update_preview_ui()
        self.update()

    async def initialize_default_flow(self):
        """Loads or creates the default flow. Called from did_mount so page is guaranteed mounted."""
        try:
            user_id = (
                auth_service.user_info.get("id") if auth_service.user_info else None
            )

            # Auto-import existing flows from disk on startup
            from core.shared.storage_config import storage

            flows_dirs = [storage.flows_directory, storage.temp_directory_for_flows]
            existing_paths = {
                getattr(f.flow_settings, "path", None)
                for f in flow_file_handler.get_user_flows(user_id)
            }

            for flows_dir in flows_dirs:
                if flows_dir.exists():
                    for file_path in flows_dir.glob("*.yaml"):
                        if str(file_path) not in existing_paths:
                            try:
                                flow_file_handler.import_flow(
                                    file_path, user_id=user_id
                                )
                                existing_paths.add(str(file_path))
                            except Exception as ex:
                                print(f"Error importing flow {file_path}: {ex}")
                    for file_path in flows_dir.glob("*.yml"):
                        if str(file_path) not in existing_paths:
                            try:
                                flow_file_handler.import_flow(
                                    file_path, user_id=user_id
                                )
                                existing_paths.add(str(file_path))
                            except Exception as ex:
                                print(f"Error importing flow {file_path}: {ex}")

            flows = flow_file_handler.get_user_flows(user_id)
            if flows:
                # Sort flows by modification time (descending) to select the most recently updated flow first
                def get_mod_time(f):
                    ts = getattr(f.flow_settings, "modified_on", 0) or 0
                    return ts

                flows.sort(key=get_mod_time, reverse=True)
                self.active_flow_id = flows[0].flow_id
            else:
                self.active_flow_id = flow_file_handler.add_flow(
                    "My_First_Dataryx_Flow", user_id=user_id
                )

            self.flow_ref = flow_file_handler.get_flow(self.active_flow_id)

            # Load the flow list into dropdown
            self.load_flow_list()

            self.run_btn.disabled = False
            self.export_btn.disabled = False

            self.update_steps_ui()
            if self.page:
                self.update()
        except Exception as e:
            print("Error initializing default flow:", e)
            traceback.print_exc()

    def load_flow_list(self):
        user_id = auth_service.user_info.get("id") if auth_service.user_info else None
        flows = flow_file_handler.get_user_flows(user_id)

        self.flow_dropdown.items.clear()

        for f in flows:
            fid = f.flow_id
            name = f.__name__ or str(fid)
            is_active = fid == self.active_flow_id

            if is_active:
                item_content = ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.CHECK_ROUNDED, size=16, color=ft.Colors.BLUE_400
                        ),
                        ft.Text(
                            name,
                            size=13,
                            color=ft.Colors.BLUE_400,
                            weight=ft.FontWeight.BOLD,
                        ),
                    ],
                    spacing=8,
                )
            else:
                item_content = ft.Row(
                    [
                        ft.Container(width=24),
                        ft.Text(name, size=13, color=ft.Colors.WHITE),
                    ],
                    spacing=8,
                )

            self.flow_dropdown.items.append(
                ft.PopupMenuItem(
                    content=item_content,
                    on_click=lambda _, captured_id=fid: self.switch_flow_by_id(
                        captured_id
                    ),
                )
            )

        # Update button label to show active flow name
        active_name = next(
            (
                f.__name__ or str(f.flow_id)
                for f in flows
                if f.flow_id == self.active_flow_id
            ),
            "Select Flow",
        )
        self.flow_label_text.value = active_name
        if self.flow_dropdown.page:
            try:
                self.flow_dropdown.update()
            except Exception:
                pass
        print(
            f"[DEBUG] load_flow_list: flows={[f.__name__ for f in flows]}, active={self.active_flow_id}"
        )

    def switch_flow_by_id(self, flow_id):
        print(f"[DEBUG] switch_flow_by_id triggered: flow_id={flow_id}")
        self.active_flow_id = flow_id
        self.flow_ref = flow_file_handler.get_flow(flow_id)
        self.selected_node_id = None
        self.run_btn.disabled = False
        self.export_btn.disabled = False

        self.load_flow_list()  # refresh checkmark in menu
        self.update_steps_ui()
        self.update_config_ui()
        self.update_preview_ui()
        if self.page:
            self.page.update()

    def create_new_flow(self, e):
        name_input = ft.TextField(
            label="Flow Name",
            hint_text="e.g. My_Custom_Flow",
            autofocus=True,
            width=320,
            text_size=13,
        )

        def confirm_create(evt):
            flow_name = name_input.value.strip()
            if not flow_name:
                name_input.error_text = "Flow name cannot be empty"
                name_input.update()
                return

            self.main_page.pop_dialog()

            user_id = (
                auth_service.user_info.get("id") if auth_service.user_info else None
            )
            new_flow_id = flow_file_handler.add_flow(flow_name, user_id=user_id)
            self.active_flow_id = new_flow_id
            self.flow_ref = flow_file_handler.get_flow(new_flow_id)
            self.selected_node_id = None

            self.load_flow_list()

            self.run_btn.disabled = False
            self.export_btn.disabled = False

            self.update_steps_ui()
            self.update_config_ui()
            self.update_preview_ui()
            self.update()

            if self.page:
                snack = ft.SnackBar(content=ft.Text(f"Created flow: {flow_name}"))
                self.page.overlay.append(snack)
                snack.open = True
                self.page.update()

        def cancel_create(evt):
            self.main_page.pop_dialog()

        dialog = ft.AlertDialog(
            title=ft.Text("Create New Flow"),
            content=name_input,
            actions=[
                ft.TextButton("Cancel", on_click=cancel_create),
                ft.Button(
                    "Create",
                    on_click=confirm_create,
                    bgcolor="#2563EB",
                    color=ft.Colors.WHITE,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.main_page.show_dialog(dialog)

    def update_steps_ui(self):
        if hasattr(self, "canvas") and self.canvas:
            self.canvas.flow_ref = self.flow_ref
            self.canvas.load_flow_canvas()

    def select_node(self, node_id):
        self.selected_node_id = node_id
        self.update_steps_ui()
        self.update_config_ui()
        self.update_preview_ui()
        self.update()

    def add_node(self, node_type: str):
        if not self.flow_ref:
            return

        node_id = random.randint(1000, 9999)
        node_promise = NodePromise(
            flow_id=self.active_flow_id,
            node_id=node_id,
            node_type=node_type,
            pos_x=0,
            pos_y=0,
        )

        from core.configs.node_store.nodes import get_all_standard_nodes

        _, _, node_defaults = get_all_standard_nodes()
        has_default = (
            node_type in node_defaults and node_defaults[node_type].has_default_settings
        )

        if node_type in ["read", "read_csv"]:
            from core.schemas.input_schema import ReceivedTable, InputCsvTable, NodeRead

            rt = ReceivedTable(path="", file_type="csv", table_settings=InputCsvTable())
            initial_settings = NodeRead(
                flow_id=self.active_flow_id,
                node_id=node_id,
                node_type="read",
                received_file=rt,
            )
            self.flow_ref.add_node_promise(node_promise, track_history=False)
            self.flow_ref.add_read(initial_settings)
        elif node_type == "explore_data":
            self.flow_ref.add_initial_node_analysis(node_promise)
        else:
            self.flow_ref.add_node_promise(node_promise, track_history=False)
            if has_default:
                setting_name_ref = "node" + node_type.replace("_", "")
                node_model = None
                for ref_name, ref in inspect.getmodule(input_schema).__dict__.items():
                    if ref_name.lower() == setting_name_ref:
                        node_model = ref
                        break

                if node_model:
                    try:
                        add_func = getattr(self.flow_ref, "add_" + node_type)
                        initial_params = {
                            "flow_id": self.active_flow_id,
                            "node_id": node_id,
                            "cache_results": False,
                            "pos_x": 0,
                            "pos_y": 0,
                            "node_type": node_type,
                        }

                        initial_settings = node_model(**initial_params)
                        add_func(initial_settings)
                    except Exception as e:
                        print(f"Error adding default settings for {node_type}:", e)

        self.save_active_flow()
        self.selected_node_id = node_id
        self.update_steps_ui()
        self.update_config_ui()
        self.update_preview_ui()
        self.update()

    def delete_node(self, node_id):
        if not self.flow_ref:
            return
        self.flow_ref.delete_node(node_id)
        self.save_active_flow()
        if self.selected_node_id == node_id:
            self.selected_node_id = None
        self.update_steps_ui()
        self.update_config_ui()
        self.update_preview_ui()
        self.update()

    def get_incoming_columns(self):
        if not self.flow_ref or self.selected_node_id is None:
            return []
        try:
            node = self.flow_ref.get_node(self.selected_node_id)
            if node:
                node_data = node.get_node_data(
                    flow_id=self.active_flow_id, include_example=False
                )
                if node_data and node_data.main_input and node_data.main_input.columns:
                    return node_data.main_input.columns
        except Exception as e:
            print("Error getting incoming columns:", e)
        return []

    def _build_manual_input_ui(self, node):
        """Build a spreadsheet-style editor for manual_input nodes."""
        from core.schemas.input_schema import NodeManualInput, RawData, MinimalFieldInfo

        # ── Load existing data from node ──────────────────────────────────────
        raw = (
            getattr(node.setting_input, "raw_data_format", None)
            if node.setting_input
            else None
        )
        col_names: list[str] = (
            [c.name for c in raw.columns] if (raw and raw.columns) else ["col_1"]
        )
        col_types: list[str] = (
            [c.data_type for c in raw.columns] if (raw and raw.columns) else ["Utf8"]
        )

        # Convert column-oriented data → row-oriented  list[list]
        if raw and raw.data and len(raw.data) > 0 and len(raw.data[0]) > 0:
            num_rows = len(raw.data[0])
            row_data: list[list[str]] = [
                [str(raw.data[ci][ri]) for ci in range(len(col_names))]
                for ri in range(num_rows)
            ]
        else:
            row_data = [["" for _ in col_names]]

        TYPE_OPTIONS = ["Utf8", "Int64", "Float64", "Boolean", "Date", "Datetime"]

        # ── Mutable state ─────────────────────────────────────────────────────
        state = {
            "cols": list(col_names),
            "types": list(col_types),
            "rows": [list(r) for r in row_data],
        }

        # ── Grid container (vertical scroll) ──────────────────────────────────
        # COL_W: fixed width per column cell; ROW_NUM_W: row-number gutter
        COL_W = 110
        ROW_NUM_W = 28

        grid_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)

        def rebuild_grid():
            grid_col.controls.clear()
            cols = state["cols"]
            types = state["types"]
            rows = state["rows"]

            # ── Header row ───────────────────────────────────────────────────
            # Each column header = name TextField + type Dropdown + ✕ button,
            # all packed inside a fixed-width Container so alignment is exact.
            header_cells = [ft.Container(width=ROW_NUM_W)]  # gutter spacer

            for ci, (cname, ctype) in enumerate(zip(cols, types)):
                ci_cap = ci

                name_field = ft.TextField(
                    value=cname,
                    text_size=11,
                    height=30,
                    content_padding=ft.Padding(left=5, top=2, right=2, bottom=2),
                    bgcolor="#2A3040",
                    border_color=ft.Colors.GREY_700,
                    focused_border_color=ft.Colors.BLUE_400,
                    color=ft.Colors.WHITE,
                    expand=True,
                )
                name_field.on_change = lambda e, idx=ci_cap: _update_col_name(idx, e.control.value)

                del_btn = ft.IconButton(
                    icon=ft.Icons.CLOSE_ROUNDED,
                    icon_color=ft.Colors.RED_400,
                    icon_size=12,
                    tooltip=f"Remove column",
                    width=24,
                    height=24,
                    padding=0,
                    on_click=lambda e, idx=ci_cap: _delete_col(idx),
                )

                type_dd = ft.Dropdown(
                    value=ctype,
                    options=[ft.dropdown.Option(t) for t in TYPE_OPTIONS],
                    text_size=10,
                    height=28,
                    content_padding=ft.Padding(left=5, top=0, right=2, bottom=0),
                    bgcolor="#2A3040",
                    border_color=ft.Colors.GREY_700,
                    color=ft.Colors.WHITE,
                    expand=True,
                )
                type_dd.on_select = lambda e, idx=ci_cap: _update_col_type(idx, e.control.value)

                header_cells.append(
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [name_field, del_btn],
                                    spacing=0,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                type_dd,
                            ],
                            spacing=2,
                        ),
                        width=COL_W,
                    )
                )

            # Row-delete gutter spacer (aligns with data row delete buttons)
            header_cells.append(ft.Container(width=28))

            grid_col.controls.append(ft.Row(header_cells, spacing=4))
            grid_col.controls.append(ft.Divider(height=1, color=ft.Colors.GREY_700))

            # ── Data rows ────────────────────────────────────────────────────
            for ri, row in enumerate(rows):
                ri_cap = ri
                row_cells = [
                    ft.Container(
                        content=ft.Text(str(ri + 1), size=10, color=ft.Colors.GREY_500),
                        width=ROW_NUM_W,
                        alignment=ft.Alignment(0, 0),
                    )
                ]
                for ci2 in range(len(cols)):
                    ci2_cap = ci2
                    cell_val = row[ci2] if ci2 < len(row) else ""
                    cell_field = ft.TextField(
                        value=cell_val,
                        text_size=11,
                        height=30,
                        content_padding=ft.Padding(left=5, top=2, right=4, bottom=2),
                        bgcolor="#1E2330",
                        border_color=ft.Colors.GREY_800,
                        focused_border_color=ft.Colors.BLUE_400,
                        color=ft.Colors.WHITE,
                    )
                    cell_field.on_change = lambda e, r=ri_cap, c=ci2_cap: _update_cell(r, c, e.control.value)
                    row_cells.append(ft.Container(content=cell_field, width=COL_W))

                del_row_btn = ft.IconButton(
                    icon=ft.Icons.REMOVE_CIRCLE_OUTLINE_ROUNDED,
                    icon_color=ft.Colors.RED_300,
                    icon_size=14,
                    tooltip="Remove row",
                    width=28,
                    height=28,
                    padding=0,
                    on_click=lambda e, r=ri_cap: _delete_row(r),
                )
                row_cells.append(del_row_btn)
                grid_col.controls.append(ft.Row(row_cells, spacing=4))

            try:
                if grid_col.page:
                    grid_col.update()
            except RuntimeError:
                pass

        def _update_col_name(idx, val):
            if 0 <= idx < len(state["cols"]):
                state["cols"][idx] = val

        def _update_col_type(idx, val):
            if 0 <= idx < len(state["types"]):
                state["types"][idx] = val

        def _update_cell(row, col, val):
            while len(state["rows"][row]) <= col:
                state["rows"][row].append("")
            state["rows"][row][col] = val

        def _delete_col(idx):
            if len(state["cols"]) <= 1:
                return
            state["cols"].pop(idx)
            state["types"].pop(idx)
            for r in state["rows"]:
                if idx < len(r):
                    r.pop(idx)
            rebuild_grid()

        def _delete_row(idx):
            if len(state["rows"]) <= 1:
                return
            state["rows"].pop(idx)
            rebuild_grid()

        def add_col(e):
            new_name = f"col_{len(state['cols']) + 1}"
            state["cols"].append(new_name)
            state["types"].append("Utf8")
            for r in state["rows"]:
                r.append("")
            rebuild_grid()

        def add_row(e):
            state["rows"].append(["" for _ in state["cols"]])
            rebuild_grid()

        def save_manual_input(e):
            cols = state["cols"]
            types = state["types"]
            rows = state["rows"]
            # Convert row-oriented → column-oriented
            col_data = [
                [rows[ri][ci] if ci < len(rows[ri]) else "" for ri in range(len(rows))]
                for ci in range(len(cols))
            ]

            # Cast values to appropriate types
            def cast_val(val, dtype):
                if dtype in ("Int64",):
                    try:
                        return int(val)
                    except:
                        return 0
                if dtype in ("Float64",):
                    try:
                        return float(val)
                    except:
                        return 0.0
                if dtype == "Boolean":
                    return val.lower() in ("true", "1", "yes")
                return str(val)

            col_data_cast = [
                [cast_val(v, types[ci]) for v in col_data[ci]]
                for ci in range(len(cols))
            ]
            columns = [
                MinimalFieldInfo(name=n, data_type=t) for n, t in zip(cols, types)
            ]
            raw_data = RawData(columns=columns, data=col_data_cast)
            new_input = NodeManualInput(
                flow_id=self.active_flow_id,
                node_id=node.node_id,
                raw_data_format=raw_data,
            )
            try:
                self.flow_ref.add_manual_input(new_input)
                self.save_active_flow()
                self.update_preview_ui()
                if self.page:
                    snack = ft.SnackBar(
                        content=ft.Text(
                            f"✓ Manual Input saved — {len(cols)} cols × {len(rows)} rows",
                            color=ft.Colors.WHITE,
                        ),
                        bgcolor="#1E7E34",
                        duration=2000,
                    )
                    self.page.overlay.append(snack)
                    snack.open = True
                    self.page.update()
            except Exception as ex:
                self.show_dialog("Error saving Manual Input", str(ex))

        rebuild_grid()

        # ── Action buttons ────────────────────────────────────────────────────
        action_row = ft.Row(
            [
                ft.Button(
                    "+ Add Column",
                    icon=ft.Icons.ADD_BOX_ROUNDED,
                    bgcolor="#2563EB",
                    color=ft.Colors.WHITE,
                    height=34,
                    on_click=add_col,
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
                ),
                ft.Button(
                    "+ Add Row",
                    icon=ft.Icons.ADD_ROUNDED,
                    bgcolor="#1E7E34",
                    color=ft.Colors.WHITE,
                    height=34,
                    on_click=add_row,
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
                ),
            ],
            spacing=8,
        )

        save_btn = ft.Button(
            "💾  Save Data",
            bgcolor="#2563EB",
            color=ft.Colors.WHITE,
            height=38,
            on_click=save_manual_input,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
        )

        hint_text = ft.Text(
            "Click any cell to edit. Add columns/rows as needed, then Save.",
            size=11,
            color=ft.Colors.GREY_500,
            italic=True,
        )

        # Wrap grid_col in a Row with horizontal scroll so many columns are reachable
        grid_scroll = ft.Row(
            controls=[
                ft.Container(
                    content=grid_col,
                    bgcolor="#13161F",
                    border=ft.Border.all(1, ft.Colors.GREY_800),
                    border_radius=6,
                    padding=8,
                )
            ],
            scroll=ft.ScrollMode.AUTO,
        )

        self.config_container.controls.extend(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                "Data Table",
                                size=13,
                                color=ft.Colors.GREY_300,
                                weight=ft.FontWeight.W_600,
                            ),
                            hint_text,
                            grid_scroll,
                            action_row,
                            ft.Divider(color=ft.Colors.GREY_800),
                            save_btn,
                        ],
                        spacing=10,
                    ),
                    padding=ft.Padding(top=8, left=0, right=0, bottom=0),
                )
            ]
        )

    def update_config_ui(self):

        self.config_container.controls.clear()
        if self.selected_node_id is None or not self.flow_ref:
            self.config_container.controls.append(
                ft.Text(
                    "Select a step to configure parameters", color=ft.Colors.GREY_500
                )
            )
            return

        node = self.flow_ref.get_node(self.selected_node_id)
        if not node:
            return

        # Automatically upgrade NodePromise settings placeholders to proper Pydantic settings models
        if isinstance(node.setting_input, NodePromise):
            import inspect
            from core.schemas import input_schema

            setting_name_ref = "node" + node.node_type.replace("_", "")
            node_model = None
            for ref_name, ref in inspect.getmodule(input_schema).__dict__.items():
                if ref_name.lower() == setting_name_ref:
                    node_model = ref
                    break
            if node_model:
                depending_id = None
                try:
                    main_inputs = node.node_inputs.main_inputs
                    if main_inputs:
                        depending_id = main_inputs[0].node_id
                except Exception:
                    pass
                if depending_id is None:
                    depending_id = getattr(node.setting_input, "depending_on_id", None)
                depending_ids = [depending_id] if depending_id is not None else []

                initial_params = {
                    "flow_id": getattr(node.setting_input, "flow_id", None)
                    or self.active_flow_id,
                    "node_id": node.node_id,
                    "cache_results": getattr(
                        node.setting_input, "cache_results", False
                    ),
                    "pos_x": getattr(node.setting_input, "pos_x", 0.0),
                    "pos_y": getattr(node.setting_input, "pos_y", 0.0),
                    "description": getattr(node.setting_input, "description", ""),
                    "node_reference": getattr(
                        node.setting_input, "node_reference", None
                    ),
                    "user_id": (
                        getattr(node.setting_input, "user_id", None)
                        or (
                            auth_service.user_info.get("id", 1)
                            if auth_service.user_info
                            else 1
                        )
                    ),
                    "is_flow_output": getattr(
                        node.setting_input, "is_flow_output", False
                    ),
                    "is_user_defined": getattr(
                        node.setting_input, "is_user_defined", False
                    ),
                    "output_field_config": getattr(
                        node.setting_input, "output_field_config", None
                    ),
                }
                if "depending_on_id" in node_model.model_fields:
                    initial_params["depending_on_id"] = depending_id or -1
                if "depending_on_ids" in node_model.model_fields:
                    initial_params["depending_on_ids"] = depending_ids

                try:
                    node.setting_input = instantiate_with_defaults(
                        node_model, initial_params
                    )
                except Exception as e:
                    print(
                        f"Error upgrading settings input placeholder for {node.node_type}:",
                        e,
                    )

        # Core node type heading
        self.config_container.controls.append(
            ft.Text(
                f"Configure {node.node_type.upper()} Step ({node.node_id})",
                color=ft.Colors.WHITE,
                size=15,
                weight=ft.FontWeight.BOLD,
            )
        )

        incoming_cols = self.get_incoming_columns()

        # Custom high-fidelity form builders for major ETL nodes
        if node.node_type == "manual_input":
            self._build_manual_input_ui(node)
        elif node.node_type == "database_reader":
            from core.database.connection import get_db_context
            from core.dataryx.database_connection_manager.db_connections import (
                get_all_database_connections_interface,
            )
            from core.schemas.input_schema import DatabaseSettings

            user_id = (
                auth_service.user_info.get("id", 1) if auth_service.user_info else 1
            )
            with get_db_context() as db:
                saved_conns = get_all_database_connections_interface(db, user_id)

            conn_options = [ft.dropdown.Option(c.connection_name) for c in saved_conns]

            # Get current settings
            setting = node.setting_input
            ds = getattr(setting, "database_settings", None)

            curr_conn = getattr(ds, "database_connection_name", None) if ds else None
            curr_query_mode = getattr(ds, "query_mode", "table") if ds else "table"
            curr_schema = getattr(ds, "schema_name", "") or ""
            curr_table = getattr(ds, "table_name", "") or ""
            curr_query = getattr(ds, "query", "") or ""

            conn_dropdown = ft.Dropdown(
                label="Database Connection",
                options=conn_options,
                value=curr_conn,
                height=44,
                text_size=13,
            )

            schema_input = ft.TextField(
                label="Schema Name (Optional)",
                value=curr_schema,
                height=44,
                text_size=13,
            )

            table_input = ft.TextField(
                label="Table Name",
                value=curr_table,
                height=44,
                text_size=13,
                visible=(curr_query_mode == "table"),
            )

            query_input = ft.TextField(
                label="SQL Query",
                value=curr_query,
                multiline=True,
                min_lines=3,
                max_lines=6,
                text_size=13,
                visible=(curr_query_mode == "query"),
            )

            def on_mode_change(e):
                val = e.control.value
                table_input.visible = val == "table"
                query_input.visible = val == "query"
                table_input.update()
                query_input.update()
                self.config_container.update()

            query_mode_dropdown = ft.Dropdown(
                label="Read Mode",
                options=[ft.dropdown.Option("table"), ft.dropdown.Option("query")],
                value=curr_query_mode,
                height=44,
                text_size=13,
                on_select=on_mode_change,
            )

            def save_db_reader_config(e):
                conn_name = conn_dropdown.value
                if not conn_name:
                    self.show_dialog("Error", "Please select a database connection.")
                    return

                q_mode = query_mode_dropdown.value
                sch_name = schema_input.value.strip() or None
                tbl_name = table_input.value.strip() or None
                sql_q = query_input.value.strip() or None

                if q_mode == "table" and not tbl_name:
                    self.show_dialog("Error", "Table Name is required in table mode.")
                    return
                if q_mode == "query" and not sql_q:
                    self.show_dialog("Error", "SQL Query is required in query mode.")
                    return

                db_settings = DatabaseSettings(
                    connection_mode="reference",
                    database_connection=None,
                    database_connection_name=conn_name,
                    schema_name=sch_name,
                    table_name=tbl_name if q_mode == "table" else None,
                    query=sql_q if q_mode == "query" else None,
                    query_mode=q_mode,
                )

                node.setting_input.database_settings = db_settings
                if node.setting_input.user_id is None:
                    node.setting_input.user_id = user_id

                try:
                    self.flow_ref.add_database_reader(node.setting_input)
                    self.show_dialog(
                        "✓ Saved", "Database Reader settings saved successfully!"
                    )
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Settings",
                on_click=save_db_reader_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend(
                [
                    conn_dropdown,
                    query_mode_dropdown,
                    schema_input,
                    table_input,
                    query_input,
                    save_btn,
                ]
            )

        elif node.node_type == "database_writer":
            from core.database.connection import get_db_context
            from core.dataryx.database_connection_manager.db_connections import (
                get_all_database_connections_interface,
            )
            from core.schemas.input_schema import DatabaseWriteSettings

            user_id = (
                auth_service.user_info.get("id", 1) if auth_service.user_info else 1
            )
            with get_db_context() as db:
                saved_conns = get_all_database_connections_interface(db, user_id)

            conn_options = [ft.dropdown.Option(c.connection_name) for c in saved_conns]

            # Get current settings
            setting = node.setting_input
            dws = getattr(setting, "database_write_settings", None)

            curr_conn = getattr(dws, "database_connection_name", None) if dws else None
            curr_schema = getattr(dws, "schema_name", "") or ""
            curr_table = getattr(dws, "table_name", "") or ""
            curr_if_exists = getattr(dws, "if_exists", "append") if dws else "append"

            conn_dropdown = ft.Dropdown(
                label="Database Connection",
                options=conn_options,
                value=curr_conn,
                height=44,
                text_size=13,
            )

            schema_input = ft.TextField(
                label="Schema Name (Optional)",
                value=curr_schema,
                height=44,
                text_size=13,
            )

            table_input = ft.TextField(
                label="Table Name",
                value=curr_table,
                height=44,
                text_size=13,
            )

            if_exists_dropdown = ft.Dropdown(
                label="If Table Exists",
                options=[
                    ft.dropdown.Option("append"),
                    ft.dropdown.Option("replace"),
                    ft.dropdown.Option("fail"),
                ],
                value=curr_if_exists,
                height=44,
                text_size=13,
            )

            def save_db_writer_config(e):
                conn_name = conn_dropdown.value
                if not conn_name:
                    self.show_dialog("Error", "Please select a database connection.")
                    return

                tbl_name = table_input.value.strip()
                if not tbl_name:
                    self.show_dialog("Error", "Table Name is required.")
                    return

                sch_name = schema_input.value.strip() or None
                if_ex = if_exists_dropdown.value

                db_write_settings = DatabaseWriteSettings(
                    connection_mode="reference",
                    database_connection=None,
                    database_connection_name=conn_name,
                    schema_name=sch_name,
                    table_name=tbl_name,
                    if_exists=if_ex,
                )

                node.setting_input.database_write_settings = db_write_settings
                if node.setting_input.user_id is None:
                    node.setting_input.user_id = user_id

                try:
                    self.flow_ref.add_database_writer(node.setting_input)
                    self.show_dialog(
                        "✓ Saved", "Database Writer settings saved successfully!"
                    )
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Settings",
                on_click=save_db_writer_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend(
                [
                    conn_dropdown,
                    schema_input,
                    table_input,
                    if_exists_dropdown,
                    save_btn,
                ]
            )

        elif node.node_type in ["read", "read_csv"]:
            setting = node.setting_input
            rf = getattr(setting, "received_file", None)
            file_path = getattr(rf, "path", "") if rf else ""
            file_type = getattr(rf, "file_type", "csv") if rf else "csv"

            # Extract delimiter/sheet name
            delimiter = ","
            sheet_name = ""
            has_headers = True

            if rf and rf.table_settings:
                delimiter = getattr(rf.table_settings, "delimiter", ",")
                sheet_name = getattr(rf.table_settings, "sheet_name", "") or ""
                has_headers = getattr(rf.table_settings, "has_headers", True)

            path_input = ft.TextField(
                label="File Path",
                value=file_path,
                height=44,
                text_size=13,
                expand=True,
                hint_text="Click Browse 📁 or paste path here...",
                read_only=False,
            )

            def open_picker(e):
                ext_map = {
                    "csv": ["csv"],
                    "excel": ["xlsx", "xls"],
                    "parquet": ["parquet"],
                    "json": ["json"],
                }
                allowed = ext_map.get(
                    type_dropdown.value or "csv", ["csv", "xlsx", "parquet", "json"]
                )

                async def _pick():
                    files = await self._file_picker.pick_files(
                        dialog_title="Select Data File",
                        file_type=ft.FilePickerFileType.CUSTOM,
                        allowed_extensions=allowed,
                        allow_multiple=False,
                    )
                    if files:
                        path_input.value = files[0].path
                        path_input.error_text = None
                        path_input.update()

                self.main_page.run_task(_pick)

            browse_btn = ft.IconButton(
                icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                icon_color=ft.Colors.BLUE_400,
                tooltip="Browse for file",
                on_click=open_picker,
            )

            path_row = ft.Row(
                [path_input, browse_btn],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

            delim_input = ft.TextField(
                label="Delimiter (CSV)",
                value=delimiter,
                height=44,
                text_size=13,
                visible=(file_type == "csv"),
            )
            sheet_input = ft.TextField(
                label="Sheet Name (Excel)",
                value=sheet_name,
                height=44,
                text_size=13,
                visible=(file_type == "excel"),
            )
            header_switch = ft.Switch(
                label="Has Column Headers",
                value=has_headers,
                visible=(file_type in ["csv", "excel"]),
            )

            def on_type_change(e):
                val = e.control.value
                delim_input.visible = val == "csv"
                sheet_input.visible = val == "excel"
                header_switch.visible = val in ["csv", "excel"]
                delim_input.update()
                sheet_input.update()
                header_switch.update()
                self.config_container.update()

            type_dropdown = ft.Dropdown(
                label="File Format",
                options=[
                    ft.dropdown.Option("csv"),
                    ft.dropdown.Option("excel"),
                    ft.dropdown.Option("parquet"),
                    ft.dropdown.Option("json"),
                ],
                value=file_type,
                height=44,
                text_size=13,
                on_select=on_type_change,
            )

            def save_read_config(e):
                import os
                from core.schemas.input_schema import (
                    ReceivedTable,
                    InputCsvTable,
                    InputExcelTable,
                    InputParquetTable,
                    InputJsonTable,
                )

                raw_path = path_input.value.strip()

                # ── Validate path ─────────────────────────────────
                if not raw_path:
                    path_input.error_text = (
                        "File path is required — use Browse 📁 to select a file"
                    )
                    path_input.update()
                    return
                if not os.path.isfile(raw_path):
                    path_input.error_text = f"File not found: {raw_path}"
                    path_input.update()
                    return
                path_input.error_text = None  # clear any previous error
                path_input.update()

                fmt = type_dropdown.value.lower()
                if fmt == "csv":
                    ts = InputCsvTable(
                        delimiter=delim_input.value or ",",
                        has_headers=header_switch.value,
                    )
                elif fmt == "excel":
                    ts = InputExcelTable(
                        sheet_name=sheet_input.value or None,
                        has_headers=header_switch.value,
                    )
                elif fmt == "parquet":
                    ts = InputParquetTable()
                else:
                    ts = InputJsonTable()

                node.setting_input.received_file = ReceivedTable(
                    path=raw_path, file_type=fmt, table_settings=ts
                )
                try:
                    self.flow_ref.add_read(node.setting_input)
                    self.show_dialog(
                        "✓ Saved", f"File configured:\n{os.path.basename(raw_path)}"
                    )
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Settings",
                on_click=save_read_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend(
                [
                    path_row,
                    type_dropdown,
                    delim_input,
                    sheet_input,
                    header_switch,
                    save_btn,
                ]
            )

        elif node.node_type == "group_by":
            from core.schemas.transform_schema import GroupByInput, AggColl
            from core.schemas.input_schema import NodeGroupBy

            # ── Current settings ─────────────────────────────────
            gi = getattr(node.setting_input, "groupby_input", None)
            existing_agg_cols = gi.agg_cols if gi else []
            existing_groupby_cols = {
                c.old_name for c in existing_agg_cols if c.agg == "groupby"
            }
            existing_agg_non_group = [
                c for c in existing_agg_cols if c.agg != "groupby"
            ]

            # Columns from upstream — handle str or object-with-.name
            available_cols = []
            for c in incoming_cols or []:
                if isinstance(c, str):
                    available_cols.append(c)
                elif hasattr(c, "name"):
                    available_cols.append(c.name)

            AGG_FUNCS = [
                "sum",
                "mean",
                "count",
                "min",
                "max",
                "first",
                "last",
                "n_unique",
            ]

            # ── Group By columns (checkboxes) ─────────────────────
            groupby_header = ft.Text(
                "Group By Columns",
                size=13,
                weight=ft.FontWeight.W_600,
                color=ft.Colors.BLUE_300,
            )

            groupby_checks = []
            for col in available_cols:
                cb = ft.Checkbox(
                    label=col,
                    value=(col in existing_groupby_cols),
                    label_style=ft.TextStyle(color=ft.Colors.GREY_200, size=13),
                )
                groupby_checks.append(cb)

            groupby_col_list = ft.Column(groupby_checks, spacing=4)

            # ── Aggregate columns (dynamic rows) ──────────────────
            agg_header = ft.Text(
                "Aggregate Columns",
                size=13,
                weight=ft.FontWeight.W_600,
                color=ft.Colors.ORANGE_300,
            )
            agg_rows_col = ft.Column([], spacing=6)

            def make_agg_row(col_name="", agg_func="sum", out_name=""):
                col_dd = ft.Dropdown(
                    options=[ft.dropdown.Option(c) for c in available_cols],
                    value=col_name or (available_cols[0] if available_cols else None),
                    height=40,
                    text_size=12,
                    expand=2,
                )
                func_dd = ft.Dropdown(
                    options=[ft.dropdown.Option(f) for f in AGG_FUNCS],
                    value=agg_func,
                    height=40,
                    text_size=12,
                    expand=2,
                )
                out_tf = ft.TextField(
                    hint_text="Output name (optional)",
                    value=out_name,
                    height=40,
                    text_size=12,
                    expand=2,
                )
                del_btn = ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    icon_color=ft.Colors.RED_400,
                    icon_size=16,
                    on_click=lambda _, row=None: _remove_agg_row(row),
                )
                row = ft.Row(
                    [col_dd, func_dd, out_tf, del_btn],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
                del_btn.on_click = lambda _, r=row: _remove_agg_row(r)
                return row, col_dd, func_dd, out_tf

            def _remove_agg_row(row):
                if row in agg_rows_col.controls:
                    agg_rows_col.controls.remove(row)
                    agg_rows_col.update()

            # Pre-fill existing agg cols
            for ac in existing_agg_non_group:
                row, _, _, _ = make_agg_row(ac.old_name, ac.agg, ac.new_name or "")
                agg_rows_col.controls.append(row)

            def add_agg_row(e):
                row, _, _, _ = make_agg_row()
                agg_rows_col.controls.append(row)
                agg_rows_col.update()

            add_agg_btn = ft.TextButton(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.ADD_ROUNDED, size=14),
                        ft.Text("Add Aggregation", size=12),
                    ],
                    spacing=4,
                ),
                on_click=add_agg_row,
            )

            # ── Save ─────────────────────────────────────────────
            def save_groupby(e):
                agg_cols = []
                # Group-by columns
                for cb in groupby_checks:
                    if cb.value:
                        agg_cols.append(AggColl(old_name=cb.label, agg="groupby"))
                if not agg_cols:
                    self.show_dialog(
                        "Validation", "Please select at least one Group By column."
                    )
                    return
                # Aggregate columns
                for row in agg_rows_col.controls:
                    controls = row.controls  # [col_dd, func_dd, out_tf, del_btn]
                    col_val = controls[0].value
                    func_val = controls[1].value
                    out_val = controls[2].value.strip() or None
                    if col_val and func_val:
                        agg_cols.append(
                            AggColl(old_name=col_val, agg=func_val, new_name=out_val)
                        )

                # ── Build a proper NodeGroupBy (setting_input may be NodePromise) ──
                # Get depending_on_id from node's upstream connection
                depending_id = None
                try:
                    main_inputs = node.node_inputs.main_inputs
                    if main_inputs:
                        depending_id = main_inputs[0].node_id
                except Exception:
                    pass
                # Fallback: try existing setting_input
                if depending_id is None:
                    depending_id = getattr(node.setting_input, "depending_on_id", None)

                new_settings = NodeGroupBy(
                    flow_id=getattr(node.setting_input, "flow_id", None)
                    or self.active_flow_id,
                    node_id=node.node_id,
                    depending_on_id=depending_id,
                    groupby_input=GroupByInput(agg_cols=agg_cols),
                    cache_results=getattr(node.setting_input, "cache_results", False),
                    pos_x=getattr(node.setting_input, "pos_x", 0.0),
                    pos_y=getattr(node.setting_input, "pos_y", 0.0),
                    description=getattr(node.setting_input, "description", ""),
                    node_reference=getattr(node.setting_input, "node_reference", None),
                    user_id=getattr(node.setting_input, "user_id", None),
                    is_flow_output=getattr(node.setting_input, "is_flow_output", False),
                    is_user_defined=getattr(
                        node.setting_input, "is_user_defined", False
                    ),
                    output_field_config=getattr(
                        node.setting_input, "output_field_config", None
                    ),
                )
                try:
                    self.flow_ref.add_group_by(new_settings)
                    self.save_active_flow()
                    self.show_dialog("✓ Saved", "Group By settings saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error", str(ex))

            save_btn = ft.Button(
                "Save Settings",
                on_click=save_groupby,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )

            self.config_container.controls.extend(
                [
                    groupby_header,
                    (
                        groupby_col_list
                        if available_cols
                        else ft.Text(
                            "No columns (run upstream node first)",
                            color=ft.Colors.GREY_500,
                            size=12,
                        )
                    ),
                    ft.Divider(color=ft.Colors.GREY_800, height=12),
                    agg_header,
                    ft.Row(
                        [
                            ft.Text(
                                "Column", size=11, color=ft.Colors.GREY_500, expand=2
                            ),
                            ft.Text(
                                "Function", size=11, color=ft.Colors.GREY_500, expand=2
                            ),
                            ft.Text(
                                "Output Name",
                                size=11,
                                color=ft.Colors.GREY_500,
                                expand=2,
                            ),
                            ft.Container(width=36),
                        ],
                        spacing=4,
                    ),
                    agg_rows_col,
                    add_agg_btn,
                    ft.Divider(color=ft.Colors.GREY_800, height=8),
                    save_btn,
                ]
            )

        elif node.node_type == "filter":

            # Load basic or advanced filter settings
            mode = "basic"
            field = ""
            operator = "equals"
            val = ""
            val2 = ""
            expr = ""

            fi = getattr(node.setting_input, "filter_input", None)
            if fi:
                mode = getattr(fi, "mode", "basic")
                expr = getattr(fi, "advanced_filter", "")
                if fi.basic_filter:
                    field = getattr(fi.basic_filter, "field", "")
                    op_obj = getattr(fi.basic_filter, "operator", "equals")
                    operator = (
                        op_obj.to_symbol()
                        if hasattr(op_obj, "to_symbol")
                        else str(op_obj)
                    )
                    val = getattr(fi.basic_filter, "value", "")
                    val2 = getattr(fi.basic_filter, "value2", "") or ""

            # Manual tab switcher (ft.Tab API changes across Flet versions)
            mode_state = {"current": mode}  # "basic" or "advanced"

            basic_tab_btn = ft.TextButton(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.FILTER_ALT_ROUNDED, size=14),
                        ft.Text("Basic Filter", size=12),
                    ],
                    spacing=4,
                ),
                style=ft.ButtonStyle(
                    color=ft.Colors.BLUE_400 if mode == "basic" else ft.Colors.GREY_500
                ),
            )
            adv_tab_btn = ft.TextButton(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.CODE_ROUNDED, size=14),
                        ft.Text("Advanced Expression", size=12),
                    ],
                    spacing=4,
                ),
                style=ft.ButtonStyle(
                    color=(
                        ft.Colors.BLUE_400 if mode == "advanced" else ft.Colors.GREY_500
                    )
                ),
            )
            tab_row = ft.Row([basic_tab_btn, adv_tab_btn], spacing=4)

            col_dropdown = ft.Dropdown(
                label="Target Column",
                options=[ft.dropdown.Option(c) for c in incoming_cols],
                value=field,
                height=44,
                text_size=13,
            )
            val2_input = ft.TextField(
                label="Upper Bound Value (Between)",
                value=val2,
                height=44,
                text_size=13,
                visible=(operator == "between"),
            )

            def on_op_change(e):
                val2_input.visible = e.control.value == "between"
                val2_input.update()
                self.config_container.update()

            op_dropdown = ft.Dropdown(
                label="Operator",
                options=[
                    ft.dropdown.Option("="),
                    ft.dropdown.Option("!="),
                    ft.dropdown.Option(">"),
                    ft.dropdown.Option(">="),
                    ft.dropdown.Option("<"),
                    ft.dropdown.Option("<="),
                    ft.dropdown.Option("contains"),
                    ft.dropdown.Option("starts_with"),
                    ft.dropdown.Option("ends_with"),
                    ft.dropdown.Option("is_null"),
                    ft.dropdown.Option("is_not_null"),
                    ft.dropdown.Option("between"),
                ],
                value=operator,
                height=44,
                text_size=13,
                on_select=on_op_change,
            )
            val_input = ft.TextField(
                label="Filter Value", value=val, height=44, text_size=13
            )
            expr_input = ft.TextField(
                label="Filter Expression (e.g. col('age') > 30)",
                value=expr,
                multiline=True,
                min_lines=3,
                text_size=12,
            )

            basic_form = ft.Column(
                [col_dropdown, op_dropdown, val_input, val2_input],
                spacing=10,
                visible=(mode == "basic"),
            )
            advanced_form = ft.Column(
                [expr_input], spacing=10, visible=(mode == "advanced")
            )

            def switch_to_basic(e):
                mode_state["current"] = "basic"
                basic_form.visible = True
                advanced_form.visible = False
                basic_tab_btn.style = ft.ButtonStyle(color=ft.Colors.BLUE_400)
                adv_tab_btn.style = ft.ButtonStyle(color=ft.Colors.GREY_500)
                basic_form.update()
                advanced_form.update()
                basic_tab_btn.update()
                adv_tab_btn.update()
                self.config_container.update()

            def switch_to_advanced(e):
                mode_state["current"] = "advanced"
                basic_form.visible = False
                advanced_form.visible = True
                basic_tab_btn.style = ft.ButtonStyle(color=ft.Colors.GREY_500)
                adv_tab_btn.style = ft.ButtonStyle(color=ft.Colors.BLUE_400)
                basic_form.update()
                advanced_form.update()
                basic_tab_btn.update()
                adv_tab_btn.update()
                self.config_container.update()

            basic_tab_btn.on_click = switch_to_basic
            adv_tab_btn.on_click = switch_to_advanced

            def save_filter_config(e):
                from core.schemas.transform_schema import (
                    FilterInput,
                    BasicFilter,
                    FilterOperator,
                )

                if mode_state["current"] == "basic":
                    bf = BasicFilter(
                        field=col_dropdown.value or "",
                        operator=FilterOperator.from_symbol(op_dropdown.value or "="),
                        value=val_input.value or "",
                        value2=(
                            val2_input.value or ""
                            if op_dropdown.value == "between"
                            else None
                        ),
                    )
                    fi = FilterInput(mode="basic", basic_filter=bf)
                else:
                    fi = FilterInput(
                        mode="advanced", advanced_filter=expr_input.value or ""
                    )

                node.setting_input.filter_input = fi
                try:
                    self.flow_ref.add_filter(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Filter conditions saved successfully!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Settings",
                on_click=save_filter_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend(
                [
                    tab_row,
                    ft.Divider(height=1, color=ft.Colors.GREY_800),
                    basic_form,
                    advanced_form,
                    save_btn,
                ]
            )

        elif node.node_type == "select":
            # Column selector: list of keep, rename, and type casts
            column_rows = []
            grid_cols = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, height=300)

            # Map existing configs
            existing_selects = {
                s.old_name: s for s in getattr(node.setting_input, "select_input", [])
            }

            # Gather all cols
            all_cols = list(incoming_cols)
            for old_n in existing_selects.keys():
                if old_n not in all_cols:
                    all_cols.append(old_n)

            if not all_cols:
                self.config_container.controls.append(
                    ft.Text(
                        "No input columns available. Run pipeline to infer columns.",
                        color=ft.Colors.GREY_500,
                        italic=True,
                    )
                )
                return

            for c in all_cols:
                cfg = existing_selects.get(c)
                keep_val = cfg.keep if cfg else True
                rename_val = cfg.new_name if cfg else c
                cast_val = cfg.data_type if cfg and cfg.data_type_change else "Auto"

                keep_switch = ft.Checkbox(
                    value=keep_val, label=f"Keep {c}", label_style=ft.TextStyle(size=12)
                )
                rename_tf = ft.TextField(
                    value=rename_val,
                    hint_text="Rename to",
                    height=32,
                    text_size=12,
                    expand=True,
                )
                cast_dd = ft.Dropdown(
                    options=[
                        ft.dropdown.Option("Auto"),
                        ft.dropdown.Option("String"),
                        ft.dropdown.Option("Int64"),
                        ft.dropdown.Option("Float64"),
                        ft.dropdown.Option("Boolean"),
                    ],
                    value=cast_val,
                    height=32,
                    text_size=12,
                    width=100,
                )

                column_rows.append((c, keep_switch, rename_tf, cast_dd))
                grid_cols.controls.append(
                    ft.Row(
                        [keep_switch, rename_tf, cast_dd],
                        spacing=10,
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    )
                )

            def save_select_config(e):
                from core.schemas.transform_schema import SelectInput

                new_selects = []
                for old_n, k_switch, r_tf, c_dd in column_rows:
                    si = SelectInput(
                        old_name=old_n,
                        new_name=r_tf.value.strip() or old_n,
                        keep=k_switch.value,
                        data_type=c_dd.value if c_dd.value != "Auto" else None,
                        data_type_change=(c_dd.value != "Auto"),
                    )
                    new_selects.append(si)

                node.setting_input.select_input = new_selects
                try:
                    self.flow_ref.add_select(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Select configurations saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Selection Settings",
                on_click=save_select_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend([grid_cols, save_btn])

        elif node.node_type == "formula":
            # Output column and function expression
            func = getattr(node.setting_input, "function", None)
            new_col = func.field.name if func and func.field else ""
            expr = func.function if func else ""

            new_col_input = ft.TextField(
                label="New / Target Column", value=new_col, height=44, text_size=13
            )
            expr_input = ft.TextField(
                label="Polars Expression (e.g. col('price') * col('quantity'))",
                value=expr,
                multiline=True,
                min_lines=2,
                text_size=12,
            )

            def save_formula_config(e):
                from core.schemas.transform_schema import FunctionInput, FieldInput

                fi = FieldInput(name=new_col_input.value.strip(), data_type="Auto")
                node.setting_input.function = FunctionInput(
                    field=fi, function=expr_input.value.strip()
                )
                try:
                    self.flow_ref.add_formula(node.setting_input)
                    self.save_active_flow()
                    self.show_dialog("Success", "Formula updated!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Formula Settings",
                on_click=save_formula_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend([new_col_input, expr_input, save_btn])

        elif node.node_type == "polars_code":
            pci = getattr(node.setting_input, "polars_code_input", None)
            code = (
                pci.polars_code
                if pci
                else "df = df.with_columns(double_val = col('val') * 2)"
            )

            code_input = ft.TextField(
                label="Custom Polars LazyFrame transformations",
                value=code,
                multiline=True,
                min_lines=6,
                text_size=12,
                font_family="monospace",
            )

            def save_polars_code_config(e):
                from core.schemas.transform_schema import PolarsCodeInput

                node.setting_input.polars_code_input = PolarsCodeInput(
                    polars_code=code_input.value or ""
                )
                try:
                    self.flow_ref.add_polars_code(node.setting_input)
                    self.show_dialog("Success", "Polars Code saved successfully!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Code Settings",
                on_click=save_polars_code_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend([code_input, save_btn])

        elif node.node_type == "output":
            o = getattr(node.setting_input, "output_settings", None)
            name = o.name if o else "export_result.csv"
            directory = o.directory if o else "."
            file_type = o.file_type if o else "csv"
            write_mode = o.write_mode if o else "overwrite"

            name_input = ft.TextField(
                label="Export File Name", value=name, height=44, text_size=13
            )
            dir_input = ft.TextField(
                label="Output Directory", value=directory, height=44, text_size=13
            )
            type_dropdown = ft.Dropdown(
                label="Output Format",
                options=[
                    ft.dropdown.Option("csv"),
                    ft.dropdown.Option("excel"),
                    ft.dropdown.Option("parquet"),
                ],
                value=file_type,
                height=44,
                text_size=13,
            )
            mode_dropdown = ft.Dropdown(
                label="Write Mode",
                options=[ft.dropdown.Option("overwrite"), ft.dropdown.Option("append")],
                value=write_mode,
                height=44,
                text_size=13,
            )

            def save_output_config(e):
                from core.schemas.input_schema import (
                    OutputSettings,
                    OutputCsvTable,
                    OutputParquetTable,
                    OutputExcelTable,
                )

                fmt = type_dropdown.value.lower()

                if fmt == "csv":
                    ts = OutputCsvTable()
                elif fmt == "excel":
                    ts = OutputExcelTable()
                else:
                    ts = OutputParquetTable()

                node.setting_input.output_settings = OutputSettings(
                    name=name_input.value.strip(),
                    directory=dir_input.value.strip() or ".",
                    file_type=fmt,
                    write_mode=mode_dropdown.value.lower(),
                    table_settings=ts,
                )
                try:
                    self.flow_ref.add_output(node.setting_input)
                    self.show_dialog("Success", "Output File settings saved!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Output Settings",
                on_click=save_output_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend(
                [name_input, dir_input, type_dropdown, mode_dropdown, save_btn]
            )

        elif node.node_type == "sort":
            rules = getattr(node.setting_input, "sort_input", [])
            sort_rows = []
            grid_rules = ft.Column(spacing=6)

            def rebuild_sort_rules():
                grid_rules.controls.clear()
                for idx, r in enumerate(rules):
                    col_dd = ft.Dropdown(
                        options=[ft.dropdown.Option(c) for c in incoming_cols],
                        value=r.column,
                        height=32,
                        text_size=12,
                        expand=True,
                    )
                    dir_dd = ft.Dropdown(
                        options=[ft.dropdown.Option("asc"), ft.dropdown.Option("desc")],
                        value=r.how or "asc",
                        height=32,
                        text_size=12,
                        width=80,
                    )

                    def make_del_handler(rule_idx):
                        return lambda _: delete_rule(rule_idx)

                    del_btn = ft.IconButton(
                        ft.Icons.DELETE_ROUNDED,
                        icon_color=ft.Colors.RED_400,
                        on_click=make_del_handler(idx),
                    )

                    sort_rows.append((col_dd, dir_dd))
                    grid_rules.controls.append(
                        ft.Row([col_dd, dir_dd, del_btn], spacing=10)
                    )

            def delete_rule(idx):
                rules.pop(idx)
                rebuild_sort_rules()
                self.update()

            def add_rule(e):
                from core.schemas.transform_schema import SortByInput

                rules.append(
                    SortByInput(
                        column=incoming_cols[0] if incoming_cols else "", how="asc"
                    )
                )
                rebuild_sort_rules()
                self.update()

            rebuild_sort_rules()
            add_btn = ft.Button("Add Sort Rule", icon=ft.Icons.ADD, on_click=add_rule)

            def save_sort_config(e):
                from core.schemas.transform_schema import SortByInput

                new_rules = []
                for c_dd, d_dd in sort_rows:
                    if c_dd.value:
                        new_rules.append(SortByInput(column=c_dd.value, how=d_dd.value))
                node.setting_input.sort_input = new_rules
                try:
                    self.flow_ref.add_sort(node.setting_input)
                    self.show_dialog("Success", "Sorting saved successfully!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Sorting",
                on_click=save_sort_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend([grid_rules, add_btn, save_btn])

        elif node.node_type == "join":
            # Pick columns and how mapping
            how_dd = ft.Dropdown(
                label="Join Strategy (How)",
                options=[
                    ft.dropdown.Option("inner"),
                    ft.dropdown.Option("left"),
                    ft.dropdown.Option("right"),
                    ft.dropdown.Option("full"),
                    ft.dropdown.Option("semi"),
                    ft.dropdown.Option("anti"),
                ],
                value="inner",
                height=44,
                text_size=13,
            )
            left_col_dd = ft.Dropdown(
                label="Left Key Column (Preceding Step)",
                options=[ft.dropdown.Option(c) for c in incoming_cols],
                height=44,
                text_size=13,
            )

            # Attempt to gather right input columns
            right_cols = []
            try:
                node_data = node.get_node_data(
                    flow_id=self.active_flow_id, include_example=False
                )
                if (
                    node_data
                    and node_data.right_input
                    and node_data.right_input.columns
                ):
                    right_cols = node_data.right_input.columns
            except:
                pass

            right_col_dd = ft.Dropdown(
                label="Right Key Column (Side Input)",
                options=[ft.dropdown.Option(c) for c in right_cols],
                height=44,
                text_size=13,
            )

            # Re-load values
            ji = getattr(node.setting_input, "join_input", None)
            if ji:
                how_dd.value = getattr(ji, "how", "inner")
                if ji.join_mapping and len(ji.join_mapping) > 0:
                    left_col_dd.value = ji.join_mapping[0].left_col
                    right_col_dd.value = ji.join_mapping[0].right_col

            def save_join_config(e):
                from core.schemas.transform_schema import JoinInput, JoinMap, JoinInputs

                ji_val = JoinInput(
                    join_mapping=[
                        JoinMap(
                            left_col=left_col_dd.value, right_col=right_col_dd.value
                        )
                    ],
                    left_select=JoinInputs(renames=[]),
                    right_select=JoinInputs(renames=[]),
                    how=how_dd.value,
                )
                node.setting_input.join_input = ji_val
                try:
                    self.flow_ref.add_join(node.setting_input)
                    self.show_dialog("Success", "Join configured successfully!")
                    self.update_preview_ui()
                    self.update()
                except Exception as ex:
                    self.show_dialog("Error saving", str(ex))

            save_btn = ft.Button(
                "Save Join",
                on_click=save_join_config,
                bgcolor=ft.Colors.BLUE_600,
                color=ft.Colors.WHITE,
            )
            self.config_container.controls.extend(
                [how_dd, left_col_dd, right_col_dd, save_btn]
            )

        else:
            # Dynamic Generic Schema Fallback Form Builder (handles all remaining 15+ nodes!)
            setting_input = getattr(node, "setting_input", None)
            if setting_input and hasattr(setting_input, "model_fields"):
                generic_controls = {}
                fields = setting_input.model_fields

                # Filter out system parameters
                filtered_fields = [
                    f
                    for f in fields.keys()
                    if f
                    not in [
                        "flow_id",
                        "node_id",
                        "cache_results",
                        "pos_x",
                        "pos_y",
                        "is_setup",
                        "description",
                        "node_reference",
                        "user_id",
                        "is_flow_output",
                        "is_user_defined",
                        "output_field_config",
                        "depending_on_id",
                        "depending_on_ids",
                    ]
                ]

                if not filtered_fields:
                    self.config_container.controls.append(
                        ft.Text(
                            "This step runs with standard automated values.",
                            color=ft.Colors.GREY_400,
                            italic=True,
                        )
                    )
                    return

                for f in filtered_fields:
                    field_meta = fields[f]
                    field_type = field_meta.annotation
                    current_val = getattr(setting_input, f, None)

                    if field_type == bool:
                        sw = ft.Switch(
                            label=f"Enable {f.replace('_', ' ').capitalize()}",
                            value=bool(current_val),
                        )
                        generic_controls[f] = sw
                        self.config_container.controls.append(sw)
                    elif f in [
                        "sample_size",
                        "size",
                        "limit",
                        "starting_from_line",
                        "infer_schema_length",
                    ]:
                        tf = ft.TextField(
                            label=f.replace("_", " ").capitalize(),
                            value=str(current_val if current_val is not None else 1000),
                            keyboard_type=ft.KeyboardType.NUMBER,
                            height=44,
                            text_size=13,
                        )
                        generic_controls[f] = tf
                        self.config_container.controls.append(tf)
                    else:
                        # Fallback simple string input
                        tf = ft.TextField(
                            label=f.replace("_", " ").capitalize(),
                            value=str(current_val if current_val is not None else ""),
                            height=44,
                            text_size=13,
                        )
                        generic_controls[f] = tf
                        self.config_container.controls.append(tf)

                def save_generic_config(e):
                    for f_name, ctrl in generic_controls.items():
                        if isinstance(ctrl, ft.Switch):
                            setattr(node.setting_input, f_name, ctrl.value)
                        elif isinstance(ctrl, ft.TextField):
                            f_type = fields[f_name].annotation
                            if f_type == int:
                                try:
                                    setattr(
                                        node.setting_input, f_name, int(ctrl.value or 0)
                                    )
                                except:
                                    pass
                            elif f_type == float:
                                try:
                                    setattr(
                                        node.setting_input,
                                        f_name,
                                        float(ctrl.value or 0.0),
                                    )
                                except:
                                    pass
                            else:
                                setattr(node.setting_input, f_name, ctrl.value)

                    try:
                        add_func = getattr(self.flow_ref, "add_" + node.node_type)
                        add_func(node.setting_input)
                        self.save_active_flow()
                        self.show_dialog("Success", "Settings updated!")
                        self.update_preview_ui()
                        self.update()
                    except Exception as ex:
                        self.show_dialog("Error saving", str(ex))

                save_btn = ft.Button(
                    "Save Settings",
                    on_click=save_generic_config,
                    bgcolor=ft.Colors.BLUE_600,
                    color=ft.Colors.WHITE,
                )
                self.config_container.controls.append(save_btn)
            else:
                self.config_container.controls.append(
                    ft.Text(
                        "This node configuration can be automated via yaml schema imports.",
                        color=ft.Colors.GREY_400,
                        italic=True,
                    )
                )

    def update_preview_ui(self):
        self.preview_table.columns.clear()
        self.preview_table.rows.clear()

        if self.selected_node_id is None or not self.flow_ref:
            self.preview_table.columns.append(ft.DataColumn(ft.Text("No active step")))
            return

        try:
            node = self.flow_ref.get_node(self.selected_node_id)
            if not node:
                self.preview_table.columns.append(
                    ft.DataColumn(ft.Text("Node not found"))
                )
                return

            # 1. Attempt to load real executed Polars table sample
            table_ex = node.get_table_example(include_data=True)
            if table_ex and table_ex.columns and table_ex.data:
                for col_name in table_ex.columns:
                    self.preview_table.columns.append(
                        ft.DataColumn(
                            ft.Text(
                                col_name,
                                color=ft.Colors.BLUE_200,
                                weight=ft.FontWeight.BOLD,
                            )
                        )
                    )
                for row_dict in table_ex.data:
                    cells = [
                        ft.DataCell(ft.Text(str(row_dict.get(col, ""))))
                        for col in table_ex.columns
                    ]
                    self.preview_table.rows.append(ft.DataRow(cells=cells))
                return

            # 2. If no executed results yet, check if it is a manual_input node and render configured input data
            if (
                node.node_type == "manual_input"
                and getattr(node, "setting_input", None)
                and getattr(node.setting_input, "raw_data_format", None)
            ):
                raw = node.setting_input.raw_data_format
                col_names = [c.name for c in raw.columns]
                for col in col_names:
                    self.preview_table.columns.append(
                        ft.DataColumn(
                            ft.Text(
                                col, color=ft.Colors.BLUE_200, weight=ft.FontWeight.BOLD
                            )
                        )
                    )
                if raw.data and len(raw.data) > 0:
                    num_rows = len(raw.data[0])
                    for ri in range(num_rows):
                        cells = []
                        for ci in range(len(col_names)):
                            val = raw.data[ci][ri] if ri < len(raw.data[ci]) else ""
                            cells.append(ft.DataCell(ft.Text(str(val))))
                        self.preview_table.rows.append(ft.DataRow(cells=cells))
                return

            # 3. If file read node, show initial file preview
            if (
                node.node_type in ["read", "read_csv"]
                and hasattr(node.setting_input, "received_file")
                and node.setting_input.received_file
                and node.setting_input.received_file.path
            ):
                rf = node.setting_input.received_file
                delimiter = ","
                if rf.table_settings:
                    delimiter = getattr(rf.table_settings, "delimiter", ",")
                df = pl.read_csv(rf.path, separator=delimiter, n_rows=10)
                for col_name in df.columns:
                    self.preview_table.columns.append(
                        ft.DataColumn(
                            ft.Text(
                                col_name,
                                color=ft.Colors.BLUE_200,
                                weight=ft.FontWeight.BOLD,
                            )
                        )
                    )
                for row_data in df.rows():
                    cells = [ft.DataCell(ft.Text(str(val))) for val in row_data]
                    self.preview_table.rows.append(ft.DataRow(cells=cells))
                return

            # Fallback mock schema
            self.preview_table.columns.append(
                ft.DataColumn(ft.Text("Preview Unavailable"))
            )
            self.preview_table.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(
                            ft.Text("Run the pipeline to generate preview data.")
                        )
                    ]
                )
            )

        except Exception as e:
            self.preview_table.columns.append(
                ft.DataColumn(ft.Text("Error Rendering Preview"))
            )
            self.preview_table.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(
                            ft.Text(
                                f"Could not load data: {str(e)}",
                                color=ft.Colors.RED_400,
                            )
                        )
                    ]
                )
            )

    def run_pipeline(self, e):
        if not self.flow_ref:
            return

        # Verify run count on Auth VPS
        try:
            profile = auth_service.get_profile()
            sub = profile.get("subscription", {})
            if sub.get("is_expired"):
                self.show_dialog(
                    "Subscription Expired",
                    "Your Dataryx subscription has expired. Please renew to run flows.",
                )
                return
            if sub.get("conversions_remaining", 0) <= 0:
                self.show_dialog(
                    "Limit Reached",
                    "You have 0 remaining conversion runs. Please upgrade your account.",
                )
                return

            # Executing flow graph locally in-memory (Direct Python function call!)
            self.flow_ref.flow_settings.execution_mode = "Development"
            self.save_active_flow()
            self.flow_ref.run_graph()

            # Decrement VPS run count
            auth_service.decrement_run()
            self.show_dialog(
                "Pipeline Completed",
                "Dataryx executed the pipeline successfully! run count decremented.",
            )
            self.update_preview_ui()
            self.update()
        except Exception as ex:
            self.show_dialog(
                "Execution Error", f"Failed to execute pipeline: {str(ex)}"
            )

    def export_code(self, e):
        if not self.flow_ref:
            return
        try:
            code = export_flow_to_polars(self.flow_ref)
            self.show_dialog("Generated Python / Polars Code", code, is_code=True)
        except Exception as ex:
            self.show_dialog("Export Error", f"Could not generate code: {str(ex)}")

    def show_dialog(self, title: str, message: str, is_code: bool = False):
        content = (
            ft.TextField(
                value=message,
                multiline=True,
                read_only=True,
                text_size=12,
                width=600,
                height=350,
                text_style=ft.TextStyle(font_family="monospace"),
            )
            if is_code
            else ft.Text(message, size=14)
        )

        def close_dialog(e):
            self.main_page.pop_dialog()

        dialog = ft.AlertDialog(
            title=ft.Text(title),
            content=content,
            actions=[ft.TextButton("Close", on_click=close_dialog)],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.main_page.show_dialog(dialog)

    def save_active_flow(self):
        if (
            self.flow_ref
            and getattr(self.flow_ref, "flow_settings", None)
            and getattr(self.flow_ref.flow_settings, "path", None)
        ):
            try:
                self.flow_ref.save_flow(self.flow_ref.flow_settings.path)
            except Exception as e:
                print("Error saving flow to disk:", e)
