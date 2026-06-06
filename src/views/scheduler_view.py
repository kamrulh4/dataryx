import flet as ft
from core.database.connection import get_db_context
from core.database import models as db_models
from core.dataryx.scheduler_service import scheduler_service, execute_job_logic
from services.auth_service import auth_service
import asyncio

class SchedulerView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.expand = True
        self.bgcolor = "#13161F"
        self.padding = 24

        self.jobs_list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)
        self.history_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO)

        # New job inputs
        self.name_input = ft.TextField(label="Job Name", height=45, text_size=13)
        self.flow_dropdown = ft.Dropdown(label="Select Flow", height=45, text_size=13)
        self.cron_input = ft.TextField(label="Cron Expression (e.g. */5 * * * *)", height=45, text_size=13, value="*/5 * * * *")
        self.tz_input = ft.TextField(label="Timezone", height=45, text_size=13, value="UTC")
        self.retries_input = ft.TextField(label="Max Retries", height=45, text_size=13, value="0")

        self.build_ui()

    def build_ui(self):
        form_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Schedule Flow", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    ft.Divider(color=ft.Colors.GREY_800),
                    self.name_input,
                    self.flow_dropdown,
                    self.cron_input,
                    self.tz_input,
                    self.retries_input,
                    ft.ElevatedButton("Save Schedule", on_click=self.save_schedule, bgcolor=ft.Colors.BLUE_600, color=ft.Colors.WHITE),
                ],
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
            ),
            bgcolor="#1E2330",
            padding=20,
            border_radius=8,
            width=320,
        )

        jobs_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Scheduled Workflows", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    ft.Divider(color=ft.Colors.GREY_800),
                    self.jobs_list,
                ],
                spacing=10,
                expand=True,
            ),
            bgcolor="#1E2330",
            padding=20,
            border_radius=8,
            expand=True,
        )

        history_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Execution Log History", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    ft.Divider(color=ft.Colors.GREY_800),
                    self.history_list,
                ],
                spacing=10,
                expand=True,
            ),
            bgcolor="#1E2330",
            padding=20,
            border_radius=8,
            height=300,
        )

        right_layout = ft.Column(
            [
                jobs_panel,
                history_panel,
            ],
            spacing=20,
            expand=True,
        )

        self.content = ft.Row(
            [
                form_panel,
                right_layout,
            ],
            spacing=20,
            expand=True,
        )

    def did_mount(self):
        self.load_flows()
        self.load_jobs()
        self.load_history()

    def load_flows(self):
        self.flow_dropdown.options.clear()
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        with get_db_context() as db:
            flows = db.query(db_models.FlowRegistration).filter(db_models.FlowRegistration.owner_id == user_id).all()
            for f in flows:
                self.flow_dropdown.options.append(ft.dropdown.Option(key=str(f.id), text=f.name))
        self.update()

    def load_jobs(self):
        self.jobs_list.controls.clear()
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        with get_db_context() as db:
            jobs = db.query(db_models.ScheduledJob).filter(db_models.ScheduledJob.user_id == user_id).all()
            for job in jobs:
                status_text = "Active" if job.is_active else "Paused"
                status_color = ft.Colors.GREEN_400 if job.is_active else ft.Colors.GREY_500
                self.jobs_list.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.SCHEDULE_ROUNDED, color=ft.Colors.BLUE_300),
                                ft.Column(
                                    [
                                        ft.Text(job.name, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                                        ft.Text(f"Cron: {job.cron_expression} | {job.timezone} | Next Run: {job.next_run_at or 'None'}", size=11, color=ft.Colors.GREY_400),
                                    ],
                                    spacing=2,
                                    expand=True,
                                ),
                                ft.Text(status_text, size=11, color=status_color, weight=ft.FontWeight.BOLD),
                                ft.IconButton(
                                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                                    icon_color=ft.Colors.GREEN_400,
                                    tooltip="Run Now",
                                    on_click=lambda e, jid=job.id, fid=job.flow_id: self.run_now(jid, fid),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.PAUSE_ROUNDED if job.is_active else ft.Icons.PLAY_CIRCLE_FILL_ROUNDED,
                                    icon_color=ft.Colors.ORANGE_400 if job.is_active else ft.Colors.GREEN_400,
                                    tooltip="Pause/Resume",
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
                        bgcolor="#13161F",
                        padding=12,
                        border_radius=6,
                        border=ft.Border.all(1, ft.Colors.GREY_800),
                    )
                )
        self.update()

    def load_history(self):
        self.history_list.controls.clear()
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        with get_db_context() as db:
            runs = db.query(db_models.JobRun).join(db_models.ScheduledJob).filter(db_models.ScheduledJob.user_id == user_id).order_by(db_models.JobRun.started_at.desc()).limit(10).all()
            if not runs:
                self.history_list.controls.append(ft.Text("No execution history available.", color=ft.Colors.GREY_500))
            else:
                for run in runs:
                    status_color = ft.Colors.GREEN_400 if run.status == "success" else ft.Colors.RED_400 if run.status == "failed" else ft.Colors.ORANGE_400
                    err_msg = f" - Error: {run.error_message}" if run.error_message else ""
                    self.history_list.controls.append(
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED if run.status == "success" else ft.Icons.ERROR_ROUNDED, color=status_color, size=16),
                                ft.Text(f"Job #{run.job_id} | Status: {run.status.upper()} | Start: {run.started_at.strftime('%Y-%m-%d %H:%M:%S')}{err_msg}", size=11, color=ft.Colors.GREY_300, expand=True)
                            ]
                        )
                    )
        self.update()

    def run_now(self, job_id: int, flow_id: int):
        async def run_async():
            self.show_toast("Triggered execution...")
            await execute_job_logic(job_id, flow_id)
            self.load_history()
            self.load_jobs()
        self.main_page.run_task(run_async)

    def toggle_job(self, job):
        user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
        with get_db_context() as db:
            db_job = db.query(db_models.ScheduledJob).filter(db_models.ScheduledJob.id == job.id).first()
            if db_job:
                db_job.is_active = not db_job.is_active
                db.commit()
                if db_job.is_active:
                    scheduler_service.resume_job(job.id)
                else:
                    scheduler_service.pause_job(job.id)
        self.load_jobs()

    def delete_job(self, job_id: int):
        with get_db_context() as db:
            db_job = db.query(db_models.ScheduledJob).filter(db_models.ScheduledJob.id == job_id).first()
            if db_job:
                db.delete(db_job)
                db.commit()
                scheduler_service.remove_job(job_id)
        self.load_jobs()
        self.load_history()

    def save_schedule(self, e):
        name = self.name_input.value.strip()
        flow_id = self.flow_dropdown.value
        cron = self.cron_input.value.strip()
        tz = self.tz_input.value.strip()
        retries = self.retries_input.value.strip()

        if not name or not flow_id or not cron:
            self.show_toast("Please fill name, flow, and cron expression")
            return

        try:
            # Validate cron expression
            parts = cron.split()
            if len(parts) != 5:
                raise ValueError("Cron expression must contain exactly 5 elements")

            user_id = auth_service.user_info.get("id", 1) if auth_service.user_info else 1
            db_job = db_models.ScheduledJob(
                name=name,
                flow_id=int(flow_id),
                user_id=user_id,
                cron_expression=cron,
                timezone=tz,
                max_retries=int(retries) if retries.isdigit() else 0,
                is_active=True,
            )

            with get_db_context() as db:
                db.add(db_job)
                db.commit()
                db.refresh(db_job)
                
            # Load into scheduler service
            scheduler_service.add_job(
                job_id=db_job.id,
                flow_id=db_job.flow_id,
                cron_expression=db_job.cron_expression,
                timezone=db_job.timezone,
            )

            self.show_toast("✓ Job scheduled successfully!")
            self.load_jobs()
        except Exception as ex:
            self.show_toast(f"Error: {str(ex)}")

    def show_toast(self, text: str):
        self.main_page.snack_bar = ft.SnackBar(content=ft.Text(text))
        self.main_page.snack_bar.open = True
        self.main_page.update()
