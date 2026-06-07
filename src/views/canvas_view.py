import flet as ft
import flet.canvas as cv
import random
from components.node_card import DraggableNodeCard

class CanvasView(ft.Container):
    def __init__(self, page: ft.Page, flow_ref, on_node_selected, on_node_deleted):
        super().__init__()
        self.main_page = page
        self.flow_ref = flow_ref
        self.on_node_selected = on_node_selected
        self.on_node_deleted_callback = on_node_deleted

        self.expand = True
        self.bgcolor = "#13161F"
        self.clip_behavior = ft.ClipBehavior.HARD_EDGE
        self.border_radius = 8
        self.border = ft.Border.all(1, ft.Colors.GREY_800)

        # Viewport transformation state
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.zoom_factor = 1.0

        # Connection state
        self.active_source_socket = None  # (node_id, socket_type)

        # Main elements
        self.canvas_shapes = []
        self.vector_layer = cv.Canvas(shapes=self.canvas_shapes, expand=True)

        self.stack = ft.Stack(
            controls=[self.vector_layer],
            expand=True
        )

        # Wrap in a background GestureDetector for panning the canvas
        self.bg_gesture_detector = ft.GestureDetector(
            content=self.stack,
            on_pan_update=self.handle_bg_pan,
            expand=True
        )

        self.content = self.bg_gesture_detector
        # NOTE: do NOT call load_flow_canvas() here — page is not mounted yet.
        # did_mount() will call it safely after the control is on the page.

    def did_mount(self):
        """Called by Flet after this control is added to the page. Safe to update here."""
        self.load_flow_canvas()

    def handle_bg_pan(self, e):
        self.pan_x += e.local_delta.x
        self.pan_y += e.local_delta.y
        self.load_flow_canvas()

    def zoom_in(self, e):
        self.zoom_factor = min(2.0, self.zoom_factor + 0.1)
        self.load_flow_canvas()

    def zoom_out(self, e):
        self.zoom_factor = max(0.5, self.zoom_factor - 0.1)
        self.load_flow_canvas()

    def reset_view(self, e):
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.load_flow_canvas()

    def draw_grid_background(self):
        # Grid line spacing adjusted by zoom factor
        spacing = 40.0 * self.zoom_factor
        offset_x = self.pan_x % spacing
        offset_y = self.pan_y % spacing
        
        # Draw grid lines up to 2500x1500 boundary
        for y in range(0, 1500, int(spacing)):
            line_y = y + offset_y
            self.canvas_shapes.append(
                cv.Line(
                    0, line_y, 2500, line_y,
                    paint=ft.Paint(color="#1E2330", stroke_width=1)
                )
            )
        for x in range(0, 2500, int(spacing)):
            line_x = x + offset_x
            self.canvas_shapes.append(
                cv.Line(
                    line_x, 0, line_x, 1500,
                    paint=ft.Paint(color="#1E2330", stroke_width=1)
                )
            )

    def load_flow_canvas(self):
        # Clear existing stack controls except the vector layer
        self.stack.controls = [self.vector_layer]
        self.canvas_shapes.clear()

        # Render grid lines first so they sit in the background
        self.draw_grid_background()

        if not self.flow_ref:
            # Nothing to draw; still update vector layer if we're mounted
            if self.page:
                self.vector_layer.update()
            return

        # Map nodes and assign grid X/Y positions if they are 0
        node_coords = {}
        for idx, node in enumerate(self.flow_ref.nodes):
            px = getattr(node, "pos_x", 0) or 0
            py = getattr(node, "pos_y", 0) or 0

            # Auto layout if X or Y is not set
            if px == 0 and py == 0:
                px = 50 + (idx * 230)
                py = 80 + (random.randint(-15, 15))
                node.pos_x = px
                node.pos_y = py

            node_coords[node.node_id] = (px, py)

        # Draw existing connections (Edges)
        for node in self.flow_ref.nodes:
            target_id = node.node_id
            source_ids = []

            # Primary: read from node_inputs (set by add_node_connection)
            node_inputs = getattr(node, "node_inputs", None)
            if node_inputs:
                main_inputs = getattr(node_inputs, "main_inputs", None) or []
                for src_node in main_inputs:
                    if src_node is not None:
                        source_ids.append(src_node.node_id)
                left_input = getattr(node_inputs, "left_input", None)
                if left_input:
                    source_ids.append(left_input.node_id)
                right_input = getattr(node_inputs, "right_input", None)
                if right_input:
                    source_ids.append(right_input.node_id)

            # Fallback: read from setting_input.depending_on_id
            setting_input = getattr(node, "setting_input", None)
            if setting_input:
                dep_id = getattr(setting_input, "depending_on_id", None)
                if dep_id and dep_id not in source_ids:
                    source_ids.append(dep_id)

            # Fallback: top-level depending_on_ids
            dep_ids = getattr(node, "depending_on_ids", None)
            if dep_ids:
                for d in dep_ids:
                    if d not in source_ids:
                        source_ids.append(d)

            print(f"[DEBUG] node {target_id} source_ids={source_ids}")
            # Filter out invalid IDs (None, -1 sentinel values)
            valid_source_ids = [s for s in source_ids if s and s > 0]
            for src_id in valid_source_ids:
                if src_id in node_coords and target_id in node_coords:
                    self.draw_bezier_connection(src_id, target_id, node_coords)

        # Add draggable node cards to the stack
        designer = self.get_designer_parent()
        selected_id = designer.selected_node_id if designer else None

        for node in self.flow_ref.nodes:
            px = node.pos_x
            py = node.pos_y
            is_sel = (selected_id == node.node_id)

            card = DraggableNodeCard(
                node=node,
                x=px * self.zoom_factor + self.pan_x,
                y=py * self.zoom_factor + self.pan_y,
                is_selected=is_sel,
                scale_factor=self.zoom_factor,
                on_drag=self.handle_node_drag,
                on_select=self.handle_node_select,
                on_delete=self.handle_node_delete,
                on_socket_click=self.handle_socket_click
            )
            self.stack.controls.append(card)

        # Add zoom controls overlay
        zoom_controls = ft.Container(
            content=ft.Row(
                [
                    ft.IconButton(ft.Icons.ZOOM_IN_ROUNDED, icon_color=ft.Colors.WHITE, tooltip="Zoom In", on_click=self.zoom_in),
                    ft.IconButton(ft.Icons.ZOOM_OUT_ROUNDED, icon_color=ft.Colors.WHITE, tooltip="Zoom Out", on_click=self.zoom_out),
                    ft.IconButton(ft.Icons.RESTART_ALT_ROUNDED, icon_color=ft.Colors.WHITE, tooltip="Reset View", on_click=self.reset_view),
                ],
                spacing=4,
            ),
            bgcolor="#1E2330",
            border_radius=6,
            padding=4,
            right=20,
            bottom=20,
            border=ft.Border.all(1, ft.Colors.GREY_800)
        )
        self.stack.controls.append(zoom_controls)

        # Safe to call update — this method is only reached after did_mount or user interaction
        if self.page:
            self.update()

    def get_designer_parent(self):
        """Walk the parent chain to find the DesignerView ancestor."""
        parent = self.parent
        while parent:
            if parent.__class__.__name__ == "DesignerView":
                return parent
            parent = parent.parent
        return None

    def draw_bezier_connection(self, src_id, target_id, coords):
        src_x, src_y = coords[src_id]
        tgt_x, tgt_y = coords[target_id]

        # Calculate exact socket anchors with zoom and pan
        start_x = (src_x + 202) * self.zoom_factor + self.pan_x
        start_y = (src_y + 25) * self.zoom_factor + self.pan_y

        end_x = (tgt_x + 6) * self.zoom_factor + self.pan_x
        end_y = (tgt_y + 25) * self.zoom_factor + self.pan_y

        # Elegant cubic bezier logic
        control_offset = max(50, abs(end_x - start_x) * 0.4)

        path = cv.Path(
            [
                cv.Path.MoveTo(start_x, start_y),
                cv.Path.CubicTo(
                    start_x + control_offset, start_y,
                    end_x - control_offset, end_y,
                    end_x, end_y
                )
            ],
            paint=ft.Paint(
                stroke_width=2.5,
                color="#2196F3",
                style=ft.PaintingStyle.STROKE,
                stroke_cap=ft.StrokeCap.ROUND
              )
          )
        self.canvas_shapes.append(path)

    def handle_node_drag(self, node_id, screen_x, screen_y, is_end=False):
        # Convert screen coordinates back to relative canvas coordinates
        px = (screen_x - self.pan_x) / self.zoom_factor
        py = (screen_y - self.pan_y) / self.zoom_factor

        # Update node coords in flow
        node = self.flow_ref.get_node(node_id)
        if node:
            node.pos_x = px
            node.pos_y = py

        # Re-render vector layer connecting paths quickly
        node_coords = {n.node_id: (n.pos_x, n.pos_y) for n in self.flow_ref.nodes}
        self.canvas_shapes.clear()

        for n in self.flow_ref.nodes:
            target_id = n.node_id
            source_ids = []

            dep_id = getattr(n, "depending_on_id", None)
            if dep_id:
                source_ids.append(dep_id)

            dep_ids = getattr(n, "depending_on_ids", None)
            if dep_ids:
                source_ids.extend(dep_ids)

            for src_id in source_ids:
                if src_id in node_coords and target_id in node_coords:
                    self.draw_bezier_connection(src_id, target_id, node_coords)

        if self.page:
            self.vector_layer.update()

        if is_end:
            designer = self.get_designer_parent()
            if designer and hasattr(designer, "save_active_flow"):
                designer.save_active_flow()

    def handle_node_select(self, node_id):
        self.on_node_selected(node_id)

    def handle_node_delete(self, node_id):
        self.on_node_deleted_callback(node_id)

    def _snack(self, message: str, color=None):
        """Show a snack bar using page.overlay — works across Flet versions."""
        print(f"[DEBUG] _snack: {message}")
        flet_page = self.page
        if not flet_page:
            return
        # Remove any old snack bars first
        flet_page.overlay[:] = [c for c in flet_page.overlay if not isinstance(c, ft.SnackBar)]
        sb = ft.SnackBar(
            content=ft.Text(message, color=ft.Colors.WHITE),
            bgcolor=color or "#2A2D3E",
            open=True
        )
        flet_page.overlay.append(sb)
        flet_page.update()

    def handle_socket_click(self, node_id, socket_type, e):
        print(f"[DEBUG] handle_socket_click: node_id={node_id}, socket_type={socket_type}, active_source={self.active_source_socket}")
        if not self.active_source_socket:
            if socket_type == "output":
                self.active_source_socket = node_id
                self._snack(f"Node {node_id} selected — click an Input socket to connect.")
            else:
                self._snack("Start from an Output socket (green circle)!", color=ft.Colors.RED_800)
        else:
            source_id = self.active_source_socket
            self.active_source_socket = None

            if socket_type == "input":
                if source_id == node_id:
                    self._snack("Cannot connect a node to itself!", color=ft.Colors.RED_800)
                    return

                source_node = self.flow_ref.get_node(source_id)
                target_node = self.flow_ref.get_node(node_id)
                print(f"[DEBUG] connecting: source={source_id}({source_node}) -> target={node_id}({target_node})")

                if source_node and target_node:
                    try:
                        # Use the proper API: target.add_node_connection(source, "main")
                        target_node.add_node_connection(source_node, "main")
                        print(f"[DEBUG] add_node_connection success")
                        # Save flow so connection persists across restarts
                        try:
                            self.flow_ref.save_flow(self.flow_ref.flow_settings.path)
                            print(f"[DEBUG] flow saved after connection")
                        except Exception as save_ex:
                            print(f"[DEBUG] save after connection failed: {save_ex}")
                        self._snack(f"✓ Connected {source_id} → {node_id}!", color=ft.Colors.GREEN_800)
                        self.load_flow_canvas()
                    except Exception as ex:
                        print(f"[DEBUG] add_node_connection failed: {ex}")
                        self._snack(f"Connection failed: {ex}", color=ft.Colors.RED_800)
                else:
                    print(f"[DEBUG] Could not find nodes: source={source_node}, target={target_node}")
                    self._snack("Could not find one or both nodes!", color=ft.Colors.RED_800)
            else:
                self._snack("Cancelled — second click must be on an Input socket.", color=ft.Colors.ORANGE_800)
