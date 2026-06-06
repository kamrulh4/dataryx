import flet as ft
from shared.storage_config import storage
from core.fileExplorer.funcs import SecureFileExplorer

class CatalogView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__()
        self.main_page = page
        self.current_dir = str(storage.user_data_directory)
        self.expand = True
        self.bgcolor = "#13161F"
        self.padding = 20
        self.build_catalog()

    def build_catalog(self):
        title = ft.Text("Data Catalog & Files", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)
        path_text = ft.Text(self.current_dir, size=13, color=ft.Colors.BLUE_300, italic=True)
        
        files_col = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

        def load_directory_contents():
            files_col.controls.clear()
            explorer = SecureFileExplorer(self.current_dir, storage.user_data_directory)
            try:
                contents = explorer.list_contents()
                # Back/up directory button if not at root
                if self.current_dir != str(storage.user_data_directory):
                    def go_up(e):
                        from pathlib import Path
                        self.current_dir = str(Path(self.current_dir).parent)
                        path_text.value = self.current_dir
                        load_directory_contents()
                        self.update()

                    files_col.controls.append(
                        ft.ListTile(
                            leading=ft.Icon(ft.Icons.ARROW_UPWARD_ROUNDED, color=ft.Colors.BLUE_400),
                            title=ft.Text(".. (Parent Directory)", color=ft.Colors.BLUE_200),
                            on_click=go_up
                        )
                    )

                for item in contents:
                    is_dir = item.is_directory
                    icon = ft.Icons.FOLDER_ROUNDED if is_dir else ft.Icons.INSERT_DRIVE_FILE_ROUNDED
                    icon_color = ft.Colors.AMBER_400 if is_dir else ft.Colors.BLUE_400
                    
                    def make_click_handler(path_str, is_directory):
                        if is_directory:
                            def handler(e):
                                self.current_dir = path_str
                                path_text.value = self.current_dir
                                load_directory_contents()
                                self.update()
                            return handler
                        else:
                            def handler(e):
                                # Just show file info dialog
                                self.show_file_dialog(path_str)
                            return handler

                    files_col.controls.append(
                        ft.ListTile(
                            leading=ft.Icon(icon, color=icon_color),
                            title=ft.Text(item.name, color=ft.Colors.WHITE),
                            subtitle=ft.Text(f"Size: {item.size} bytes" if not is_dir else "Folder", color=ft.Colors.GREY_500),
                            on_click=make_click_handler(item.path, is_dir),
                        )
                    )
            except Exception as e:
                files_col.controls.append(
                    ft.Text(f"Error loading directory: {str(e)}", color=ft.Colors.RED_400)
                )

        load_directory_contents()

        self.content = ft.Column(
            [
                ft.Row([title]),
                path_text,
                ft.Divider(color=ft.Colors.GREY_800),
                ft.Container(
                    content=files_col,
                    expand=True,
                    bgcolor="#1E2330",
                    padding=16,
                    border_radius=8
                )
            ],
            expand=True
        )


    def show_file_dialog(self, path: str):
        def close_dialog(e):
            self.main_page.pop_dialog()

        dialog = ft.AlertDialog(
            title=ft.Text("File Info"),
            content=ft.Text(f"Selected File Path:\n{path}", size=13),
            actions=[ft.TextButton("Close", on_click=close_dialog)],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.main_page.show_dialog(dialog)
