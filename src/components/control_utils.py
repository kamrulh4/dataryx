def is_mounted(control) -> bool:
    """Safely check whether a control is currently attached to a live page.

    Flet's ``BaseControl.page`` property does NOT return ``None`` for a
    control that hasn't been added to the page yet (or was removed/replaced) --
    it walks the parent chain and raises ``RuntimeError("... Control must be
    added to the page first")`` if it never reaches a ``Page`` instance. Every
    bare ``if self.page:`` / ``if not self.page:`` check in this codebase was
    therefore not actually a guard -- it crashed instead of skipping, which is
    exactly the reported "CanvasView(802) Control must be added to the page
    first" crash from `on_keyboard` -> `add_node_at_pos` -> `update_steps_ui`
    -> `load_flow_canvas`.
    """
    try:
        return control.page is not None
    except RuntimeError:
        return False
