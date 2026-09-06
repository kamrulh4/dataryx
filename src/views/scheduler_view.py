from components.theme import get_theme
from components.control_utils import is_mounted
import flet as ft
from core.database.connection import get_db_context
from core.database import models as db_models
from core.dataryx.scheduler_service import scheduler_service, execute_job_logic
from services.auth_service import auth_service
import asyncio
from pathlib import Path
from core.shared.storage_config import storage


# ──────────────────────────────────────────────────────────────────────────────
# Helper: call scheduler_service methods safely.
# When the scheduler is not initialized (e.g. ENABLE_SCHEDULER=0 or startup
# failed), these calls would raise RuntimeError.  Wrapping them here keeps the
# UI functional in all cases.
# ──────────────────────────────────────────────────────────────────────────────
def _sched_add(job_id, flow_id, cron, tz):
    try:
        scheduler_service.add_job(
            job_id=job_id,
            flow_id=flow_id,
            cron_expression=cron,
            timezone=tz,
        )
    except Exception as e:
        print(f"[Scheduler] add_job skipped (scheduler not running?): {e}")


def _sched_remove(job_id):
    try:
        scheduler_service.remove_job(job_id)
    except Exception as e:
        print(f"[Scheduler] remove_job skipped: {e}")


def _sched_pause(job_id):
    try:
        scheduler_service.pause_job(job_id)
    except Exception as e:
        print(f"[Scheduler] pause_job skipped: {e}")


def _sched_resume(job_id):
    try:
        scheduler_service.resume_job(job_id)
    except Exception as e:
        print(f"[Scheduler] resume_job skipped: {e}")


class SchedulerView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.expand = True
        self.bgcolor = get_theme(page).BG_PAGE
        self.padding = 24

        self.jobs_list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)
        self.history_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO)

        t = get_theme(page)

        # ── Form inputs ───────────────────────────────────────────────────────
        self.name_input = ft.TextField(
            label="Flow Name", height=45, text_size=13, border_color=t.BORDER
        )
        self.script_path_input = ft.TextField(
            label="Script / Flow Path",
            hint_text="Select a .py, .yaml, .yml or .json file",
            height=45,
            text_size=13,
            border_color=t.BORDER,
            expand=True,
        )
        self.schedule_type_dropdown = ft.Dropdown(
            label="Schedule Type",
            height=45,
            text_size=13,
            border_color=t.BORDER,
            options=[
                ft.dropdown.Option("Interval"),
                ft.dropdown.Option("Cron"),
            ],
            value="Interval",
            on_select=self.on_schedule_type_change,
        )
        self.interval_input = ft.TextField(
            label="Interval (minutes)",
            height=45,
            text_size=13,
            value="30",
            border_color=t.BORDER,
            visible=True,
        )
        self.cron_input = ft.TextField(
            label="Cron Expression (e.g. */5 * * * *)",
            height=45,
            text_size=13,
            value="*/5 * * * *",
            border_color=t.BORDER,
            visible=False,
        )
        self.tz_input = ft.TextField(
            label="Timezone",
            height=45,
            text_size=13,
            value="UTC",
            border_color=t.BORDER,
        )
        self.retries_input = ft.TextField(
            label="Max Retries",
            height=45,
            text_size=13,
            value="0",
            border_color=t.BORDER,
        )

        # FilePicker supports both Python scripts and visual flow files.
        self._file_picker = ft.FilePicker()

        self._build_ui()

    # ── Event handlers ────────────────────────────────────────────────────────

    def on_schedule_type_change(self, e):
        is_interval = self.schedule_type_dropdown.value == "Interval"
        self.interval_input.visible = is_interval
        self.cron_input.visible = not is_interval
        self.update()

    async def _pick_file(self, e):
        init_dir = None
        if hasattr(storage, "temp_directory_for_flows") and storage.temp_directory_for_flows.exists():
            init_dir = str(storage.temp_directory_for_flows)
        elif hasattr(storage, "flows_directory") and storage.flows_directory.exists():
            init_dir = str(storage.flows_directory)

        files = await self._file_picker.pick_files(
            dialog_title="Select Script or Flow File",
            initial_directory=init_dir,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["py", "yaml", "yml", "json"],
            allow_multiple=False,
        )
        if files and files[0].path:
            self.script_path_input.value = files[0].path
            if is_mounted(self.script_path_input):
                self.script_path_input.update()

    # ── UI Build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        t = get_theme(self.main_page)

        script_picker_row = ft.Row(
            [
                self.script_path_input,
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                    icon_color=t.TEXT_SECONDARY,
                    tooltip="Browse script or flow file (.py / .yaml / .yml / .json)",
                    on_click=self._pick_file,
                ),
            ],
            spacing=4,
        )

        form_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        "Schedule Flow",
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        color=t.TEXT_PRIMARY,
                    ),
                    ft.Divider(color=t.DIVIDER),
                    self.name_input,
                    script_picker_row,
                    self.schedule_type_dropdown,
                    self.interval_input,
                    self.cron_input,
                    self.tz_input,
                    self.retries_input,
                    ft.FilledButton(
                        "Save Schedule",
                        icon=ft.Icons.SAVE_ROUNDED,
                        on_click=self.save_schedule,
                        bgcolor=ft.Colors.BLUE_600,
                        color=ft.Colors.WHITE,
                    ),
                ],
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
            ),
            bgcolor=t.BG_CARD,
            padding=20,
            border_radius=8,
            width=360,
        )

        jobs_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        "Scheduled Workflows",
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        color=t.TEXT_PRIMARY,
                    ),
                    ft.Divider(color=t.DIVIDER),
                    self.jobs_list,
                ],
                spacing=10,
                expand=True,
            ),
            bgcolor=t.BG_CARD,
            padding=20,
            border_radius=8,
            expand=True,
        )

        history_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        "Execution Log History",
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        color=t.TEXT_PRIMARY,
                    ),
                    ft.Divider(color=t.DIVIDER),
                    self.history_list,
                ],
                spacing=10,
                expand=True,
            ),
            bgcolor=t.BG_CARD,
            padding=20,
            border_radius=8,
            height=300,
        )

        right_layout = ft.Column(
            [jobs_panel, history_panel],
            spacing=20,
            expand=True,
        )

        self.content = ft.Row(
            [form_panel, right_layout],
            spacing=20,
            expand=True,
        )

    def did_mount(self):
        # Register FilePicker as a page service
        if self._file_picker not in self.main_page.services:
            self.main_page.services.append(self._file_picker)
            self.main_page.update()

        # Client-requested: default to the flow currently open in the
        # Designer instead of making the user browse for it every time.
        # Still fully editable/replaceable via the browse button below.
        if storage.last_active_flow_path:
            self.script_path_input.value = storage.last_active_flow_path
            if not self.name_input.value:
                self.name_input.value = storage.last_active_flow_name or ""

        self.load_jobs()
        self.load_history()

        self._running = True
        self.main_page.run_task(self._auto_refresh_loop)

    def will_unmount(self):
        self._running = False

    async def _auto_refresh_loop(self):
        """Refresh job list (next run times) and execution logs in real-time."""
        while self._running:
            await asyncio.sleep(4)
            if self._running:
                try:
                    self.load_jobs()
                    self.load_history()
                except Exception:
                    pass

    # ── Data loaders ─────────────────────────────────────────────────────────

    def load_jobs(self):
        self.jobs_list.controls.clear()
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        t = get_theme(self.main_page)

        with get_db_context() as db:
            jobs = (
                db.query(db_models.ScheduledJob)
                .filter(db_models.ScheduledJob.user_id == user_id)
                .order_by(db_models.ScheduledJob.id.desc())
                .all()
            )

            if not jobs:
                self.jobs_list.controls.append(
                    ft.Text("No scheduled flows yet.", color=t.TEXT_HINT, italic=True)
                )
            else:
                for job in jobs:
                    self._append_job_card(db, job, t)

        if is_mounted(self):
            self.jobs_list.update()

    def _append_job_card(self, db, job, t):
        """Build and append a single job card to jobs_list."""
        status_text = "Active" if job.is_active else "Paused"
        status_color = ft.Colors.GREEN_400 if job.is_active else ft.Colors.GREY_500

        reg = (
            db.query(db_models.FlowRegistration)
            .filter(db_models.FlowRegistration.id == job.flow_id)
            .first()
        )
        script_path = reg.flow_path if reg else "Unknown"
        short_path = Path(script_path).name if script_path else "No Path"

        # Human-readable schedule description
        cron_str = job.cron_expression
        if cron_str.startswith("*/") and cron_str.count("*") == 4:
            mins = cron_str.split()[0][2:]
            sched_desc = f"Every {mins} min(s)"
        else:
            sched_desc = f"Cron: {cron_str}"

        next_run = job.next_run_at.strftime("%Y-%m-%d %H:%M") if job.next_run_at else "—"

        self.jobs_list.controls.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.SCHEDULE_ROUNDED, color=ft.Colors.BLUE_300),
                        ft.Column(
                            [
                                ft.Text(
                                    job.name,
                                    weight=ft.FontWeight.BOLD,
                                    color=t.TEXT_PRIMARY,
                                    size=13,
                                ),
                                ft.Text(
                                    f"{short_path}  ·  {sched_desc}  ·  Next: {next_run}",
                                    size=11,
                                    color=t.TEXT_HINT,
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ft.Container(
                            content=ft.Text(
                                status_text, size=10, color=status_color,
                                weight=ft.FontWeight.BOLD,
                            ),
                            bgcolor=ft.Colors.with_opacity(0.12, status_color),
                            padding=ft.Padding(left=8, top=3, right=8, bottom=3),
                            border_radius=10,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.PLAY_ARROW_ROUNDED,
                            icon_color=ft.Colors.GREEN_400,
                            tooltip="Run Now",
                            on_click=lambda e, jid=job.id, fid=job.flow_id: self.run_now(jid, fid),
                        ),
                        ft.IconButton(
                            icon=(
                                ft.Icons.PAUSE_ROUNDED
                                if job.is_active
                                else ft.Icons.PLAY_CIRCLE_FILL_ROUNDED
                            ),
                            icon_color=(
                                ft.Colors.ORANGE_400 if job.is_active else ft.Colors.GREEN_400
                            ),
                            tooltip="Pause / Resume",
                            on_click=lambda e, job_ref=job: self.toggle_job(job_ref),
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_ROUNDED,
                            icon_color=ft.Colors.RED_400,
                            tooltip="Delete Job",
                            on_click=lambda e, jid=job.id: self.delete_job(jid),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                bgcolor=t.BG_PAGE,
                padding=12,
                border_radius=6,
                border=ft.Border.all(1, t.BORDER),
            )
        )

    def load_history(self):
        self.history_list.controls.clear()
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        t = get_theme(self.main_page)

        with get_db_context() as db:
            runs = (
                db.query(db_models.JobRun)
                .join(db_models.ScheduledJob)
                .filter(db_models.ScheduledJob.user_id == user_id)
                .order_by(db_models.JobRun.started_at.desc())
                .limit(20)
                .all()
            )
            if not runs:
                self.history_list.controls.append(
                    ft.Text("No execution history yet.", color=t.TEXT_HINT, italic=True)
                )
            else:
                for run in runs:
                    status_color = (
                        ft.Colors.GREEN_400
                        if run.status == "success"
                        else (
                            ft.Colors.RED_400
                            if run.status == "failed"
                            else ft.Colors.ORANGE_400
                        )
                    )
                    icon = (
                        ft.Icons.CHECK_CIRCLE_ROUNDED
                        if run.status == "success"
                        else ft.Icons.ERROR_ROUNDED
                    )
                    err_msg = f"  —  {run.error_message[:80]}" if run.error_message else ""
                    start_str = (
                        run.started_at.strftime("%Y-%m-%d %H:%M:%S")
                        if run.started_at else "?"
                    )
                    self.history_list.controls.append(
                        ft.Row(
                            [
                                ft.Icon(icon, color=status_color, size=15),
                                ft.Text(
                                    f"Job #{run.job_id}  ·  {run.status.upper()}  ·  {start_str}{err_msg}",
                                    size=11,
                                    color=t.TEXT_SECONDARY,
                                    expand=True,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.LAUNCH_ROUNDED,
                                    icon_color=ft.Colors.BLUE_300,
                                    icon_size=14,
                                    tooltip="View stdout / stderr or node results",
                                    on_click=lambda e, rid=run.id: self.show_run_details(rid),
                                ),
                            ]
                        )
                    )
        if is_mounted(self):
            self.history_list.update()

    def show_run_details(self, run_id: int):
        """Displays a dialog box with stdout/stderr (for python scripts) or a summary table of node runs (for flows)."""
        t = get_theme(self.main_page)
        with get_db_context() as db:
            run = db.query(db_models.JobRun).filter(db_models.JobRun.id == run_id).first()
            if not run:
                return

            details = []
            title = f"Execution Details (Run #{run.id})"

            # Execution stats
            duration = ""
            if run.completed_at and run.started_at:
                diff = run.completed_at - run.started_at
                duration = f"Duration: {diff.total_seconds():.2f}s"

            details.append(
                ft.Text(
                    f"Status: {run.status.upper()}  |  Started: {run.started_at.strftime('%Y-%m-%d %H:%M:%S')}  |  {duration}",
                    size=12,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.GREEN_400 if run.status == "success" else ft.Colors.RED_400,
                )
            )
            details.append(ft.Divider(color=t.DIVIDER))

            info = run.run_info
            if info:
                # ── Python Script Execution stdout/stderr ──
                if "stdout" in info or "stderr" in info:
                    stdout = info.get("stdout", "").strip()
                    stderr = info.get("stderr", "").strip()

                    if stdout:
                        details.append(ft.Text("Standard Output (stdout):", weight=ft.FontWeight.BOLD, size=12))
                        details.append(
                            ft.Container(
                                content=ft.Column(
                                    [ft.Text(stdout, font_family="monospace", size=11, color="#A8FFB2")],
                                    scroll=ft.ScrollMode.AUTO,
                                ),
                                bgcolor="#0B0F19",
                                padding=12,
                                border_radius=6,
                                width=550,
                                height=150,
                            )
                        )
                    if stderr:
                        details.append(ft.Text("Error Output (stderr):", weight=ft.FontWeight.BOLD, size=12, color=ft.Colors.RED_300))
                        details.append(
                            ft.Container(
                                content=ft.Column(
                                    [ft.Text(stderr, font_family="monospace", size=11, color="#FF9C9C")],
                                    scroll=ft.ScrollMode.AUTO,
                                ),
                                bgcolor="#0B0F19",
                                padding=12,
                                border_radius=6,
                                width=550,
                                height=150,
                            )
                        )
                # ── Visual Flow Execution node-by-node details ──
                elif "node_step_result" in info:
                    results = info.get("node_step_result", [])
                    details.append(ft.Text("Flow Steps Summary:", weight=ft.FontWeight.BOLD, size=13))
                    
                    rows = []
                    for node in results:
                        status_icon = ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, color=ft.Colors.GREEN_400, size=14) if node.get("success") else ft.Icon(ft.Icons.CANCEL_ROUNDED, color=ft.Colors.RED_400, size=14)
                        node_name = node.get("node_name") or f"Node #{node.get('node_id')}"
                        run_time = f"{node.get('run_time', 0):.2f}s" if isinstance(node.get('run_time'), (int, float)) else "0s"
                        err = node.get("error", "")
                        
                        rows.append(
                            ft.DataRow(
                                cells=[
                                    ft.DataCell(status_icon),
                                    ft.DataCell(ft.Text(node_name, size=11)),
                                    ft.DataCell(ft.Text(run_time, size=11)),
                                    ft.DataCell(ft.Text(err if err else "—", size=11, color=ft.Colors.RED_300 if err else t.TEXT_SECONDARY)),
                                ]
                            )
                        )
                    
                    table = ft.DataTable(
                        columns=[
                            ft.DataColumn(ft.Text("", size=11)),
                            ft.DataColumn(ft.Text("Step Name", size=11, weight=ft.FontWeight.BOLD)),
                            ft.DataColumn(ft.Text("Time", size=11, weight=ft.FontWeight.BOLD)),
                            ft.DataColumn(ft.Text("Errors", size=11, weight=ft.FontWeight.BOLD)),
                        ],
                        rows=rows,
                    )
                    details.append(
                        ft.Column(
                            [table],
                            scroll=ft.ScrollMode.AUTO,
                            height=250,
                        )
                    )

            # Standard failure error message (if not captured in stderr)
            if run.error_message and not (info and "stderr" in info):
                details.append(ft.Text("Error Message:", weight=ft.FontWeight.BOLD, size=12, color=ft.Colors.RED_300))
                details.append(
                    ft.Container(
                        content=ft.Column(
                            [ft.Text(run.error_message, font_family="monospace", size=11, color="#FF9C9C")],
                            scroll=ft.ScrollMode.AUTO,
                        ),
                        bgcolor="#0B0F19",
                        padding=12,
                        border_radius=6,
                        width=550,
                        height=150,
                    )
                )

            if len(details) <= 2:
                details.append(ft.Text("No execution logs captured.", color=t.TEXT_HINT, italic=True))

            dlg = ft.AlertDialog(
                title=ft.Text(title, weight=ft.FontWeight.BOLD),
                content=ft.Column(details, spacing=10, tight=True),
            )
            
            def _close(_):
                self.main_page.pop_dialog()

            dlg.actions = [ft.TextButton("Close", on_click=_close)]
            self.main_page.show_dialog(dlg)

    # ── Actions ──────────────────────────────────────────────────────────────

    def run_now(self, job_id: int, flow_id: int):
        """Trigger immediate execution of a scheduled job."""
        async def _run():
            self.show_toast("⚙ Triggering execution…")
            await execute_job_logic(job_id, flow_id)
            self.load_history()
            self.load_jobs()

        self.main_page.run_task(_run)

    def toggle_job(self, job):
        with get_db_context() as db:
            db_job = (
                db.query(db_models.ScheduledJob)
                .filter(db_models.ScheduledJob.id == job.id)
                .first()
            )
            if db_job:
                db_job.is_active = not db_job.is_active
                db.commit()
                if db_job.is_active:
                    _sched_resume(job.id)
                else:
                    _sched_pause(job.id)
        self.load_jobs()

    def delete_job(self, job_id: int):
        with get_db_context() as db:
            db_job = (
                db.query(db_models.ScheduledJob)
                .filter(db_models.ScheduledJob.id == job_id)
                .first()
            )
            if db_job:
                db.delete(db_job)
                db.commit()
        _sched_remove(job_id)
        self.load_jobs()
        self.load_history()

    def save_schedule(self, e):
        name = self.name_input.value.strip()
        script_path = self.script_path_input.value.strip()
        sched_type = self.schedule_type_dropdown.value
        tz = self.tz_input.value.strip() or "UTC"
        retries = self.retries_input.value.strip()

        if not name:
            self.show_toast("Please enter a Flow Name.")
            return
        if not script_path:
            self.show_toast("Please select a Script / Flow file.")
            return
        if not Path(script_path).exists():
            self.show_toast(f"File not found: {script_path}")
            return

        # Build cron expression
        if sched_type == "Interval":
            val = self.interval_input.value.strip()
            if not val.isdigit() or int(val) <= 0:
                self.show_toast("Interval must be a positive integer (minutes).")
                return
            cron = f"*/{val} * * * *"
        else:
            cron = self.cron_input.value.strip()
            if not cron:
                self.show_toast("Please enter a Cron Expression.")
                return

        # Basic cron validation
        parts = cron.split()
        if len(parts) != 5:
            self.show_toast("Cron expression must have exactly 5 fields.")
            return

        try:
            user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1

            with get_db_context() as db:
                # Find or create FlowRegistration for this path
                reg = (
                    db.query(db_models.FlowRegistration)
                    .filter(db_models.FlowRegistration.flow_path == script_path)
                    .first()
                )
                if not reg:
                    reg = db_models.FlowRegistration(
                        name=name,
                        flow_path=script_path,
                        owner_id=user_id,
                    )
                    db.add(reg)
                    db.commit()
                    db.refresh(reg)

                db_job = db_models.ScheduledJob(
                    name=name,
                    flow_id=reg.id,
                    user_id=user_id,
                    cron_expression=cron,
                    timezone=tz,
                    max_retries=int(retries) if retries.isdigit() else 0,
                    is_active=True,
                )
                db.add(db_job)
                db.commit()
                db.refresh(db_job)

            # Register with APScheduler (safe — no crash if scheduler is off)
            _sched_add(db_job.id, db_job.flow_id, cron, tz)

            self.show_toast("✓ Job scheduled successfully!")
            self.load_jobs()

            # Reset form
            self.name_input.value = ""
            self.script_path_input.value = ""
            self.interval_input.value = "30"
            self.cron_input.value = "*/5 * * * *"
            self.update()

        except Exception as ex:
            self.show_toast(f"Error: {ex}")

    # ── Utility ───────────────────────────────────────────────────────────────

    def show_toast(self, text: str, error: bool = False):
        self.main_page.overlay[:] = [
            c for c in self.main_page.overlay if not isinstance(c, ft.SnackBar)
        ]
        snack = ft.SnackBar(
            content=ft.Text(text, color=ft.Colors.WHITE),
            bgcolor=ft.Colors.RED_800 if error else "#2E3D50",
            open=True,
        )
        self.main_page.overlay.append(snack)
        self.main_page.update()
