from components.theme import get_theme
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
        self.bgcolor = get_theme(page).BG_PAGE
        self.clip_behavior = ft.ClipBehavior.HARD_EDGE
        self.border_radius = 8
        self.border = ft.Border.all(1, get_theme(page).BORDER)

        # Viewport transformation state
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.zoom_factor = 1.0

        # Click-to-connect state
        self.active_source_socket = None  # node_id of selected output socket

        # Drag-to-connect state
        self._drag_source_id = None  # node_id of drag source
        self._drag_cur_x = 0.0  # current drag tip screen X
        self._drag_cur_y = 0.0  # current drag tip screen Y
        self._drag_source_x = 0.0  # source socket screen X
        self._drag_source_y = 0.0  # source socket screen Y

        # Main elements
        self.canvas_shapes = []
        self.grid_shapes = []
        self.grid_layer = cv.Canvas(shapes=self.grid_shapes, expand=True)
        self.vector_layer = cv.Canvas(shapes=self.canvas_shapes, expand=True)

        self.stack = ft.Stack(
            controls=[self.grid_layer, self.vector_layer], expand=True
        )

        # Wrap in a background GestureDetector for panning the canvas
        self.bg_gesture_detector = ft.GestureDetector(
            content=self.stack, on_pan_update=self.handle_bg_pan, expand=True
        )

        self.content = self.bg_gesture_detector

        # Cache of DraggableNodeCard objects keyed by node_id.
        # Re-using existing card widgets instead of creating new ones every frame
        # eliminates the cost of instantiating ~10 widget trees per pan/zoom event.
        self._card_cache: dict = {}

        # NOTE: do NOT call load_flow_canvas() here — page is not mounted yet.

    def did_mount(self):
        """Called by Flet after this control is added to the page. Safe to update here."""
        # Build zoom controls once here — page is guaranteed mounted so theme is available.
        # Reusing this single widget avoids creating 4 new IconButton objects every frame.
        self._zoom_controls = self._build_zoom_controls()
        self.load_flow_canvas()
        self.fit_to_screen()

    # ──────────────────────────────────────────────
    # Viewport controls
    # ──────────────────────────────────────────────
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

    def fit_to_screen(self, e=None):
        """Pan so all nodes are visible near the top-left of the canvas."""
        if not self.flow_ref or not self.flow_ref.nodes:
            return
        positions = [self._node_pos(n) for n in self.flow_ref.nodes]
        if not positions:
            return
        min_x = min(p[0] for p in positions)
        min_y = min(p[1] for p in positions)
        # Shift so the leftmost/topmost node sits at (60, 60) on screen
        self.pan_x = (60 - min_x) * self.zoom_factor
        self.pan_y = (60 - min_y) * self.zoom_factor
        self.load_flow_canvas()

    # ──────────────────────────────────────────────
    # Grid
    # ──────────────────────────────────────────────
    def draw_grid_background(self):
        spacing = 40.0 * self.zoom_factor
        if spacing < 1:
            spacing = 1.0
        offset_x = self.pan_x % spacing
        offset_y = self.pan_y % spacing

        self.grid_shapes.clear()

        # Use actual window dimensions so we only draw lines for the visible area.
        # Previously this always drew a fixed 2500×1500 region regardless of window size,
        # which created ~100+ line objects every frame on Windows.
        win = self.main_page.window if self.main_page else None
        vp_w = int((getattr(win, 'width', None) or 1920)) + int(spacing) + 1
        vp_h = int((getattr(win, 'height', None) or 1080)) + int(spacing) + 1

        grid_color = get_theme(self.main_page).BG_CARD
        for y in range(0, vp_h, int(spacing)):
            line_y = y + offset_y
            self.grid_shapes.append(
                cv.Line(
                    0,
                    line_y,
                    vp_w,
                    line_y,
                    paint=ft.Paint(color=grid_color, stroke_width=1),
                )
            )
        for x in range(0, vp_w, int(spacing)):
            line_x = x + offset_x
            self.grid_shapes.append(
                cv.Line(
                    line_x,
                    0,
                    line_x,
                    vp_h,
                    paint=ft.Paint(color=grid_color, stroke_width=1),
                )
            )
        if self.page:
            self.grid_layer.update()

    # ──────────────────────────────────────────────
    # Helper: collect all connection edges for a node
    # ──────────────────────────────────────────────
    def _get_source_ids_for_node(self, node) -> list:
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

        # Fallback: setting_input.depending_on_id
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

        return [s for s in source_ids if s and s > 0]

    # ──────────────────────────────────────────────
    # Main render
    # ──────────────────────────────────────────────
    def load_flow_canvas(self):
        self.stack.controls = [self.grid_layer, self.vector_layer]
        self.canvas_shapes.clear()

        self.draw_grid_background()

        if not self.flow_ref:
            if self.page:
                self.vector_layer.update()
            return

        # Map nodes and assign grid positions
        node_coords = {}
        for idx, node in enumerate(self.flow_ref.nodes):
            px, py = self._node_pos(node)

            # Auto layout only if position was never set at all
            if px == 0 and py == 0:
                # Place new node inside the currently visible viewport area.
                # Convert viewport center back to canvas space.
                visible_origin_x = max(10.0, -self.pan_x / self.zoom_factor)
                visible_origin_y = max(10.0, -self.pan_y / self.zoom_factor)
                px = visible_origin_x + 50 + (idx * 230) % 1200
                py = visible_origin_y + 80 + (random.randint(-15, 15))
                node.pos_x = px
                node.pos_y = py
                # Persist auto-layout to node_information AND setting_input
                if hasattr(node, "node_information"):
                    node.node_information.x_position = int(px)
                    node.node_information.y_position = int(py)
                si = getattr(node, "setting_input", None)
                if si is not None:
                    if hasattr(si, "pos_x"):
                        si.pos_x = float(px)
                    if hasattr(si, "pos_y"):
                        si.pos_y = float(py)

            node_coords[node.node_id] = (px, py)

        # Pre-compute which nodes have a description text (once per frame).
        # This avoids calling _node_has_desc() twice per connection (once for src, once for tgt).
        desc_flags: dict[int | str, bool] = {
            n.node_id: self._node_has_desc(n) for n in self.flow_ref.nodes
        }

        # Draw connections (skip self-connections)
        for node in self.flow_ref.nodes:
            target_id = node.node_id
            for src_id in self._get_source_ids_for_node(node):
                if src_id == target_id:
                    continue  # never draw a self-connection loop
                if src_id in node_coords and target_id in node_coords:
                    self.draw_bezier_connection(src_id, target_id, node_coords, desc_flags=desc_flags)

        # Draw live drag preview line
        if self._drag_source_id is not None:
            self._draw_temp_line(
                self._drag_source_x,
                self._drag_source_y,
                self._drag_cur_x,
                self._drag_cur_y,
            )

        # Add draggable node cards — reuse cached widgets, only update position/scale.
        designer = self.get_designer_parent()
        selected_id = designer.selected_node_id if designer else None

        # Evict cards for nodes that no longer exist in the flow.
        current_ids = {n.node_id for n in self.flow_ref.nodes}
        for stale_id in [k for k in self._card_cache if k not in current_ids]:
            del self._card_cache[stale_id]

        for node in self.flow_ref.nodes:
            px, py = self._node_pos(node)
            is_sel = selected_id == node.node_id
            screen_x = px * self.zoom_factor + self.pan_x
            screen_y = py * self.zoom_factor + self.pan_y

            if node.node_id in self._card_cache:
                # ── Fast path: mutate the existing widget, no new object ──
                card = self._card_cache[node.node_id]
                card.left = screen_x
                card.top = screen_y
                card.scale = self.zoom_factor
                card.x = screen_x
                card.y = screen_y
                card.scale_factor = self.zoom_factor
                # Rebuild inner card only when selection state changes
                # (border glow and shadow differ between selected / not-selected).
                if card.is_selected != is_sel:
                    card.is_selected = is_sel
                    card.content.controls[1] = card._build_card()
            else:
                # ── Slow path: first time we see this node, create the widget ──
                card = DraggableNodeCard(
                    node=node,
                    x=screen_x,
                    y=screen_y,
                    is_selected=is_sel,
                    scale_factor=self.zoom_factor,
                    on_drag=self.handle_node_drag,
                    on_select=self.handle_node_select,
                    on_delete=self.handle_node_delete,
                    on_disconnect=self.handle_node_disconnect,
                    on_socket_click=self.handle_socket_click,
                    on_socket_drag_start=self.handle_socket_drag_start,
                    on_socket_drag_update=self.handle_socket_drag_update,
                    on_socket_drag_end=self.handle_socket_drag_end,
                )
                self._card_cache[node.node_id] = card

            self.stack.controls.append(card)

        # Reuse the zoom controls widget built once in did_mount.
        # Previously 4 new IconButton objects were created on every single
        # pan / drag / zoom event — this eliminates that overhead entirely.
        if hasattr(self, '_zoom_controls') and self._zoom_controls is not None:
            self.stack.controls.append(self._zoom_controls)

        if self.page:
            self.update()

    def get_designer_parent(self):
        parent = self.parent
        while parent:
            if parent.__class__.__name__ == "DesignerView":
                return parent
            parent = parent.parent
        return None

    def update_selection_only(self, selected_id):
        """Cheaply refresh the canvas selection highlight without a full redraw.

        When the user clicks a node, only the border glow / shadow on the cards
        needs to change — the grid lines and node positions are identical.
        This method walks the cached card widgets, rebuilds the inner card
        container only for cards whose selection state changed, then calls a
        single self.update() instead of the full load_flow_canvas() path.

        Falls back to load_flow_canvas() when the cache is empty (e.g. first
        render or after a flow reload) so correctness is never compromised.
        """
        if not self._card_cache:
            # Cache not yet populated — fall back to full render
            self.load_flow_canvas()
            return

        changed = False
        for node_id, card in self._card_cache.items():
            is_sel = node_id == selected_id
            if card.is_selected != is_sel:
                card.is_selected = is_sel
                # Index 1 of the ft.Row controls is always the inner card container
                # (see DraggableNodeCard.__init__: row_children = [socket, card_content, socket])
                card.content.controls[1] = card._build_card()
                changed = True

        if changed and self.page:
            self.update()

    def _build_zoom_controls(self) -> ft.Container:
        """Build the zoom controls overlay widget once and return it for reuse.

        This widget is built a single time in did_mount() and then appended to
        stack.controls on every load_flow_canvas() call — avoiding the creation
        of 4 new IconButton objects on every pan / drag / zoom event.
        """
        t = get_theme(self.main_page)
        return ft.Container(
            content=ft.Row(
                [
                    ft.IconButton(
                        ft.Icons.ZOOM_IN_ROUNDED,
                        icon_color=t.TEXT_PRIMARY,
                        tooltip="Zoom In",
                        on_click=self.zoom_in,
                    ),
                    ft.IconButton(
                        ft.Icons.ZOOM_OUT_ROUNDED,
                        icon_color=t.TEXT_PRIMARY,
                        tooltip="Zoom Out",
                        on_click=self.zoom_out,
                    ),
                    ft.IconButton(
                        ft.Icons.RESTART_ALT_ROUNDED,
                        icon_color=t.TEXT_PRIMARY,
                        tooltip="Reset View",
                        on_click=self.reset_view,
                    ),
                    ft.IconButton(
                        ft.Icons.FIT_SCREEN_ROUNDED,
                        icon_color=ft.Colors.BLUE_400,
                        tooltip="Fit All Nodes to Screen",
                        on_click=self.fit_to_screen,
                    ),
                ],
                spacing=4,
            ),
            bgcolor=t.BG_CARD,
            border_radius=6,
            padding=4,
            right=20,
            bottom=20,
            border=ft.Border.all(1, t.BORDER),
        )

    @staticmethod
    def _node_pos(node) -> tuple[float, float]:
        """Safely read a node's canvas position.

        Priority:
          1. node.pos_x / node.pos_y  (set after first drag — fastest path)
          2. node.node_information.x_position (loaded from YAML)
          3. 0.0 fallback
        """
        px = getattr(node, "pos_x", None)
        if px is None:
            px = float(
                getattr(getattr(node, "node_information", None), "x_position", 0) or 0
            )
        py = getattr(node, "pos_y", None)
        if py is None:
            py = float(
                getattr(getattr(node, "node_information", None), "y_position", 0) or 0
            )
        return float(px), float(py)

    # ──────────────────────────────────────────────
    # Bezier connection drawing
    # ──────────────────────────────────────────────
    # Card width = 200. Socket sticks out ~14px on each side.
    # Output socket center: x = node_x + 200 + 7, y = node_y + 25
    # Input  socket center: x = node_x - 7,        y = node_y + 25
    CARD_W = 200
    SOCKET_R = 7  # radius of socket circle
    CARD_HALF_H = 25  # approximate vertical mid of card

    @staticmethod
    def _node_has_desc(node) -> bool:
        """Return True if the node has a non-empty description text.

        Reading `setting_input.description` and calling `get_default_description()`
        is done here once per node and the result is reused for both the output-
        and input-socket position helpers, avoiding duplicated attribute lookups.
        """
        setting = getattr(node, "setting_input", None) if node else None
        if not setting:
            return False
        if getattr(setting, "description", ""):
            return True
        if hasattr(setting, "get_default_description"):
            try:
                return bool(setting.get_default_description())
            except Exception:
                pass
        return False

    def _output_socket_screen(self, node, node_x, node_y, has_desc: bool | None = None):
        card_half_h = 42 if (has_desc if has_desc is not None else self._node_has_desc(node)) else 32
        # Scale around card center (width=224, center_offset=112, output x_offset=218 → rel=106)
        sx = (node_x + 106) * self.zoom_factor + self.pan_x + 112
        sy = node_y * self.zoom_factor + self.pan_y + card_half_h
        return sx, sy

    def _input_socket_screen(self, node, node_x, node_y, has_desc: bool | None = None):
        card_half_h = 42 if (has_desc if has_desc is not None else self._node_has_desc(node)) else 32
        # Scale around card center (width=224, center_offset=112, input x_offset=6 → rel=-106)
        sx = (node_x - 106) * self.zoom_factor + self.pan_x + 112
        sy = node_y * self.zoom_factor + self.pan_y + card_half_h
        return sx, sy

    def draw_bezier_connection(
        self, src_id, target_id, coords, color="#2196F3", alpha=1.0,
        desc_flags: dict | None = None,
    ):
        src_x, src_y = coords[src_id]
        tgt_x, tgt_y = coords[target_id]

        src_node = self.flow_ref.get_node(src_id) if self.flow_ref else None
        tgt_node = self.flow_ref.get_node(target_id) if self.flow_ref else None

        # Use pre-computed desc flags when available to avoid redundant getattr chains.
        src_has_desc = desc_flags.get(src_id) if desc_flags else None
        tgt_has_desc = desc_flags.get(target_id) if desc_flags else None

        start_x, start_y = self._output_socket_screen(src_node, src_x, src_y, has_desc=src_has_desc)
        end_x, end_y = self._input_socket_screen(tgt_node, tgt_x, tgt_y, has_desc=tgt_has_desc)

        control_offset = max(50, abs(end_x - start_x) * 0.4)

        paint_color = ft.Colors.with_opacity(alpha, color) if alpha < 1.0 else color

        path = cv.Path(
            [
                cv.Path.MoveTo(start_x, start_y),
                cv.Path.CubicTo(
                    start_x + control_offset,
                    start_y,
                    end_x - control_offset,
                    end_y,
                    end_x,
                    end_y,
                ),
            ],
            paint=ft.Paint(
                stroke_width=2.5,
                color=paint_color,
                style=ft.PaintingStyle.STROKE,
                stroke_cap=ft.StrokeCap.ROUND,
            ),
        )
        self.canvas_shapes.append(path)

    def _draw_temp_line(self, x1, y1, x2, y2):
        """Draw a dashed preview line while dragging from a socket."""
        path = cv.Path(
            [
                cv.Path.MoveTo(x1, y1),
                cv.Path.CubicTo(
                    x1 + max(50, abs(x2 - x1) * 0.4),
                    y1,
                    x2 - max(50, abs(x2 - x1) * 0.4),
                    y2,
                    x2,
                    y2,
                ),
            ],
            paint=ft.Paint(
                stroke_width=2.0,
                color=ft.Colors.with_opacity(0.7, "#60A5FA"),
                style=ft.PaintingStyle.STROKE,
                stroke_cap=ft.StrokeCap.ROUND,
            ),
        )
        self.canvas_shapes.append(path)

    # ──────────────────────────────────────────────
    # Node drag (moving nodes around)
    # ──────────────────────────────────────────────
    def handle_node_drag(self, node_id, screen_x, screen_y, is_end=False):
        px = (screen_x - self.pan_x) / self.zoom_factor
        py = (screen_y - self.pan_y) / self.zoom_factor

        # Clamp so nodes can never disappear off the top-left corner.
        # A small positive margin keeps the card visible even at pan_x=0.
        NODE_CARD_W = 224
        NODE_CARD_H = 90
        px = max(10.0, px)
        py = max(10.0, py)

        node = self.flow_ref.get_node(node_id)
        if node:
            # Update runtime attribute (used by load_flow_canvas render loop)
            node.pos_x = px
            node.pos_y = py
            # Update node_information (used at render time)
            if hasattr(node, "node_information"):
                node.node_information.x_position = int(px)
                node.node_information.y_position = int(py)
            # CRITICAL: also update setting_input.pos_x/pos_y
            # save_flow() calls get_node_information() → set_node_information() which
            # does  node_information.x_position = self.setting_input.pos_x  (flow_node.py:446)
            # so if we skip this, the drag position is lost on every save.
            si = getattr(node, "setting_input", None)
            if si is not None:
                if hasattr(si, "pos_x"):
                    si.pos_x = float(px)
                if hasattr(si, "pos_y"):
                    si.pos_y = float(py)

        # Re-render just the vector layer (connections) — same logic as load_flow_canvas
        def _get_pos(n):
            rx = getattr(n, "pos_x", None)
            if rx is None:
                rx = getattr(getattr(n, "node_information", None), "x_position", 0) or 0
            ry = getattr(n, "pos_y", None)
            if ry is None:
                ry = getattr(getattr(n, "node_information", None), "y_position", 0) or 0
            return rx, ry

        node_coords = {n.node_id: _get_pos(n) for n in self.flow_ref.nodes}

        self.canvas_shapes.clear()

        for n in self.flow_ref.nodes:
            for src_id in self._get_source_ids_for_node(n):
                if src_id in node_coords and n.node_id in node_coords:
                    self.draw_bezier_connection(src_id, n.node_id, node_coords)

        if self.page:
            self.vector_layer.update()

        if is_end:
            designer = self.get_designer_parent()
            if designer and hasattr(designer, "save_active_flow"):
                designer.save_active_flow()

    # ──────────────────────────────────────────────
    # Socket: click-to-connect
    # ──────────────────────────────────────────────
    def handle_node_select(self, node_id):
        self.on_node_selected(node_id)

    def handle_node_delete(self, node_id):
        self.on_node_deleted_callback(node_id)

    def handle_node_disconnect(self, node_id):
        """Disconnect all incoming connections from a node (right-click action)."""
        if not self.flow_ref:
            return
        node = self.flow_ref.get_node(node_id)
        if not node:
            return
        inputs = list(node.all_inputs)
        if not inputs:
            self._snack(
                f"Node {node_id} has no incoming connections.",
                color=ft.Colors.ORANGE_800,
            )
            return
        # Remove all incoming connections
        for src_node in inputs:
            src_node.leads_to_nodes = [
                n for n in src_node.leads_to_nodes if n.node_id != node_id
            ]
        node.node_inputs.main_inputs = []
        node.node_inputs.left_input = None
        node.node_inputs.right_input = None
        # Clear depending_on_id(s)
        si = getattr(node, "setting_input", None)
        if si:
            if hasattr(si, "depending_on_id"):
                si.depending_on_id = -1
            if hasattr(si, "depending_on_ids"):
                si.depending_on_ids = []
        node.reset()
        # Save and redraw
        try:
            self.flow_ref.save_flow(self.flow_ref.flow_settings.path)
        except Exception:
            pass
        self._snack(f"✓ Disconnected node {node_id}", color=ft.Colors.GREEN_800)
        self.load_flow_canvas()

    def _snack(self, message: str, color=None):
        flet_page = self.page
        if not flet_page:
            return
        flet_page.overlay[:] = [
            c for c in flet_page.overlay if not isinstance(c, ft.SnackBar)
        ]
        sb = ft.SnackBar(
            content=ft.Text(message, color=ft.Colors.WHITE),
            bgcolor=color or "#2A2D3E",
            open=True,
        )
        flet_page.overlay.append(sb)
        flet_page.update()

    def handle_socket_click(self, node_id, socket_type, e):
        if not self.active_source_socket:
            if socket_type == "output":
                self.active_source_socket = node_id
                self._snack(
                    f"Node {node_id} selected — click an Input socket to connect."
                )
            else:
                self._snack(
                    "Start from an Output socket (green circle)!",
                    color=ft.Colors.RED_800,
                )
        else:
            source_id = self.active_source_socket
            self.active_source_socket = None

            if socket_type == "input":
                if source_id == node_id:
                    self._snack(
                        "Cannot connect a node to itself!", color=ft.Colors.RED_800
                    )
                    return
                self._do_connect(source_id, node_id)
            else:
                self._snack(
                    "Cancelled — second click must be on an Input socket.",
                    color=ft.Colors.ORANGE_800,
                )

    # ──────────────────────────────────────────────
    # Socket: drag-to-connect
    # ──────────────────────────────────────────────
    def handle_socket_drag_start(self, node_id, socket_type):
        """Called when user starts dragging from an output socket.
        Position is NOT available from DragStartEvent in this Flet version,
        so we compute the source socket screen pos from node data."""
        if socket_type != "output":
            return
        # Cancel any existing click-to-connect selection
        self.active_source_socket = None
        self._drag_source_id = node_id

        # Compute the screen position of this output socket from node data
        node = self.flow_ref.get_node(node_id) if self.flow_ref else None
        if node:
            nx, ny = self._node_pos(node)
            sx, sy = self._output_socket_screen(node, nx, ny)
        else:
            sx, sy = 0.0, 0.0

        self._drag_source_x = sx
        self._drag_source_y = sy
        # Start tip at the source socket itself
        self._drag_cur_x = sx
        self._drag_cur_y = sy

    def handle_socket_drag_update(self, delta_x, delta_y):
        """Called on every drag move — update the preview line tip."""
        if self._drag_source_id is None:
            return
        self._drag_cur_x += delta_x
        self._drag_cur_y += delta_y

        # Redraw only the vector layer (bezier connections + preview line).
        # The grid does NOT need to be redrawn during socket drag because
        # panning is not happening — removing draw_grid_background() here
        # eliminates ~100 redundant line objects being recreated per drag event.
        node_coords = {n.node_id: self._node_pos(n) for n in self.flow_ref.nodes}
        self.canvas_shapes.clear()

        for n in self.flow_ref.nodes:
            for src_id in self._get_source_ids_for_node(n):
                if src_id in node_coords and n.node_id in node_coords:
                    self.draw_bezier_connection(src_id, n.node_id, node_coords)

        # Preview line
        self._draw_temp_line(
            self._drag_source_x, self._drag_source_y, self._drag_cur_x, self._drag_cur_y
        )

        if self.page:
            self.vector_layer.update()

    def handle_socket_drag_end(self):
        """Called when the drag ends — use accumulated _drag_cur_x/y (from deltas) to find nearest input socket."""
        source_id = self._drag_source_id
        self._drag_source_id = None

        if source_id is None or not self.flow_ref:
            self.load_flow_canvas()
            return

        # Position is tracked via cumulative deltas in handle_socket_drag_update
        final_x = self._drag_cur_x
        final_y = self._drag_cur_y

        # Find the nearest input socket within a snap radius
        SNAP_RADIUS = 40  # pixels
        best_node_id = None
        best_dist = SNAP_RADIUS

        for n in self.flow_ref.nodes:
            if n.node_id == source_id:
                continue
            from components.node_card import INPUT_NODE_TYPES

            if n.node_type in INPUT_NODE_TYPES:
                continue
            nx, ny = self._node_pos(n)
            sx, sy = self._input_socket_screen(n, nx, ny)
            dist = ((sx - final_x) ** 2 + (sy - final_y) ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_node_id = n.node_id

        if best_node_id is not None:
            self._do_connect(source_id, best_node_id)
        else:
            self.load_flow_canvas()

    # ──────────────────────────────────────────────
    # Shared connection logic
    # ──────────────────────────────────────────────
    def _do_connect(self, source_id, target_id):
        source_node = self.flow_ref.get_node(source_id)
        target_node = self.flow_ref.get_node(target_id)

        if source_node and target_node:
            try:
                target_node.add_node_connection(source_node, "main")
                try:
                    self.flow_ref.save_flow(self.flow_ref.flow_settings.path)
                except Exception:
                    pass
                self._snack(
                    f"✓ Connected {source_id} → {target_id}!", color=ft.Colors.GREEN_800
                )
                self.load_flow_canvas()
            except Exception as ex:
                self._snack(f"Connection failed: {ex}", color=ft.Colors.RED_800)
                self.load_flow_canvas()
        else:
            self._snack("Could not find one or both nodes!", color=ft.Colors.RED_800)
            self.load_flow_canvas()
