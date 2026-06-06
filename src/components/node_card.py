import flet as ft
import os

NODE_FRIENDLY_NAMES = {
    "read": "Read Data",
    "read_csv": "Read CSV",
    "database_reader": "Database Reader",
    "cloud_storage_reader": "Cloud Storage Reader",
    "manual_input": "Manual Input",
    "external_source": "External Source",
    
    "filter": "Filter Rows",
    "select": "Select Columns",
    "formula": "Formula",
    "sort": "Sort Data",
    "sample": "Take Sample",
    "unique": "Drop Duplicates",
    "text_to_rows": "Text to Rows",
    "record_id": "Add Record ID",
    "polars_code": "Polars Code",
    
    "group_by": "Group By",
    "pivot": "Pivot Data",
    "unpivot": "Unpivot Data",
    "record_count": "Count Records",
    
    "join": "Join",
    "union": "Union",
    "fuzzy_match": "Fuzzy Match",
    "cross_join": "Cross Join",
    "graph_solver": "Graph Solver",
    
    "output": "Write Data",
    "database_writer": "Database Writer",
    "cloud_storage_writer": "Cloud Storage Writer",
    "explore_data": "Explore Data",
}

class DraggableNodeCard(ft.GestureDetector):
    def __init__(self, node, x, y, is_selected, scale_factor=1.0, on_drag=None, on_select=None, on_delete=None, on_socket_click=None, incoming_connections=None, outgoing_connections=None):
        self.node = node
        self.node_id = node.node_id
        self.node_type = node.node_type
        self.x = x
        self.y = y
        self.is_selected = is_selected
        self.scale_factor = scale_factor
        self.on_drag_callback = on_drag
        self.on_select_callback = on_select
        self.on_delete_callback = on_delete
        self.on_socket_click_callback = on_socket_click

        # Determine theme and accent color based on category
        self.category_color = self.get_category_color()
        self.icon_glyph, self.custom_icon_path = self.get_node_icon()

        # Build card content
        card_content = self.build_card_ui()

        # Sockets layout: input socket (left), main card, output socket (right)
        row_children = []
        
        # Left Socket (Input) - Show for non-input categories
        self.has_input_socket = self.node_type not in ["read", "read_csv", "manual_input", "database_reader", "cloud_storage_reader", "external_source"]
        if self.has_input_socket:
            self.input_socket = ft.Container(
                width=12,
                height=12,
                bgcolor=ft.Colors.GREY_600,
                border_radius=6,
                border=ft.Border.all(1.5, ft.Colors.WHITE),
                tooltip="Input Socket (Click to Connect)",
                on_click=lambda e: self.on_socket_click_callback(self.node_id, "input", e)
            )
            row_children.append(self.input_socket)
        else:
            row_children.append(ft.Container(width=12)) # spacer

        # Main clickable card content
        row_children.append(card_content)

        # Right Socket (Output) - Show for non-output categories
        self.has_output_socket = self.node_type not in ["output", "explore_data", "database_writer", "cloud_storage_writer"]
        if self.has_output_socket:
            self.output_socket = ft.Container(
                width=12,
                height=12,
                bgcolor=self.category_color,
                border_radius=6,
                border=ft.Border.all(1.5, ft.Colors.WHITE),
                tooltip="Output Socket (Click to Connect)",
                on_click=lambda e: self.on_socket_click_callback(self.node_id, "output", e)
            )
            row_children.append(self.output_socket)
        else:
            row_children.append(ft.Container(width=12)) # spacer

        super().__init__(
            content=ft.Row(row_children, spacing=4, alignment=ft.MainAxisAlignment.CENTER),
            on_pan_update=self.drag,
            on_pan_end=self.drag_end,
            on_tap=lambda _: self.on_select_callback(self.node_id),
            left=self.x,
            top=self.y,
            scale=self.scale_factor
        )

    def get_category_color(self):
        if self.node_type in ["read", "read_csv", "manual_input", "database_reader", "cloud_storage_reader", "external_source"]:
            return "#4CAF50" # Green for Inputs
        elif self.node_type in ["output", "explore_data", "database_writer", "cloud_storage_writer"]:
            return "#F44336" # Red for Outputs
        elif self.node_type in ["join", "fuzzy_match", "cross_join", "union", "graph_solver"]:
            return "#9C27B0" # Purple for Combines
        elif self.node_type in ["group_by", "pivot", "unpivot", "record_count"]:
            return "#FFC107" # Amber for Aggregates
        else:
            return "#2196F3" # Blue for Transformations


    def get_node_icon(self):
        # Check for custom generated icons
        assets_icons_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "icons")
        
        node_type_file = self.node_type
        if node_type_file in ["read", "read_csv"]:
            node_type_file = "input_data"

        # Check svg first for vector crispness
        svg_filename = f"{node_type_file}.svg"
        if os.path.exists(os.path.join(assets_icons_dir, svg_filename)):
            return None, f"/icons/{svg_filename}"
            
        # Check png
        png_filename = f"{node_type_file}.png"
        if os.path.exists(os.path.join(assets_icons_dir, png_filename)):
            return None, f"/icons/{png_filename}"

        # Fallback default material icons
        if self.node_type in ["read", "read_csv", "manual_input", "database_reader", "cloud_storage_reader", "external_source"]:
            return ft.Icons.INPUT_ROUNDED, None
        elif self.node_type in ["output", "explore_data", "database_writer", "cloud_storage_writer"]:
            return ft.Icons.OUTPUT_ROUNDED, None
        elif self.node_type in ["join", "fuzzy_match", "cross_join", "union", "graph_solver"]:
            return ft.Icons.MERGE_TYPE_ROUNDED, None
        elif self.node_type in ["group_by", "pivot", "unpivot", "record_count"]:
            return ft.Icons.FUNCTIONS_ROUNDED, None
        else:
            return ft.Icons.SETTINGS_INPUT_COMPONENT, None

    def build_card_ui(self):
        # Dynamic icon control
        if self.custom_icon_path:
            icon_ctrl = ft.Image(src=self.custom_icon_path, width=20, height=20, fit="contain")
        else:
            icon_ctrl = ft.Icon(self.icon_glyph, color=self.category_color, size=18)

        border_color = self.category_color if self.is_selected else ft.Colors.GREY_800
        bgcolor = ft.Colors.with_opacity(0.12, self.category_color) if self.is_selected else "#1E2330"

        # Extract dynamic description
        desc_text = ""
        setting = getattr(self.node, "setting_input", None)
        if setting:
            if hasattr(setting, "description") and getattr(setting, "description", ""):
                desc_text = setting.description
            elif hasattr(setting, "get_default_description"):
                try:
                    desc_text = setting.get_default_description()
                except Exception:
                    pass

        friendly_title = NODE_FRIENDLY_NAMES.get(self.node_type, self.node_type.upper())

        # Construct the elements inside the column
        elements = [
            ft.Row(
                [
                    ft.Row([icon_ctrl, ft.Text(friendly_title, color=ft.Colors.WHITE, size=12, weight=ft.FontWeight.BOLD)], spacing=6),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE_ROUNDED,
                        icon_color=ft.Colors.RED_400,
                        icon_size=14,
                        padding=0,
                        width=20,
                        height=20,
                        on_click=lambda _: self.on_delete_callback(self.node_id)
                    )
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN
            ),
            ft.Divider(height=1, color=ft.Colors.GREY_800)
        ]

        if desc_text:
            elements.append(
                ft.Text(desc_text, color=ft.Colors.GREY_400, size=10, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
            )

        elements.append(
            ft.Row(
                [
                    ft.Text(f"ID: {self.node_id}", color=ft.Colors.GREY_500, size=10, italic=True),
                    ft.Container(
                        content=ft.Text("Active", size=9, color=ft.Colors.GREEN_300, weight=ft.FontWeight.W_600),
                        bgcolor=ft.Colors.with_opacity(0.15, ft.Colors.GREEN),
                        padding=ft.Padding.symmetric(vertical=2, horizontal=6),
                        border_radius=4
                    )
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN
            )
        )

        return ft.Container(
            content=ft.Column(
                elements,
                spacing=8
            ),
            width=180,
            padding=10,
            bgcolor=bgcolor,
            border_radius=8,
            border=ft.Border.all(1.5, border_color),
            shadow=ft.BoxShadow(
                spread_radius=1,
                blur_radius=10,
                color=ft.Colors.with_opacity(0.3, self.category_color) if self.is_selected else ft.Colors.TRANSPARENT
            )
        )

    def drag(self, e):
        # Update coordinates on drag
        self.x += e.local_delta.x
        self.y += e.local_delta.y
        self.left = self.x
        self.top = self.y
        self.update()
        if self.on_drag_callback:
            self.on_drag_callback(self.node_id, self.x, self.y, is_end=False)

    def drag_end(self, e):
        if self.on_drag_callback:
            self.on_drag_callback(self.node_id, self.x, self.y, is_end=True)
