import flet as ft

def main(page: ft.Page):
    def dropdown_changed(e):
        print(f"Dropdown changed: {dd.value}")
        lbl.value = f"Selected: {dd.value}"
        page.update()

    dd = ft.Dropdown(
        options=[
            ft.dropdown.Option("Red"),
            ft.dropdown.Option("Green"),
            ft.dropdown.Option("Blue"),
        ],
        width=200,
    )
    dd.on_change = dropdown_changed
    lbl = ft.Text()
    
    page.add(dd, lbl)

ft.app(target=main)
