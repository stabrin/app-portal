# src/admin_ui.py

import tkinter as tk
from tkinter import ttk

class AdminWindow(tk.Tk):
    """Главное окно для роли 'администратор'."""
    def __init__(self, user_info):
        super().__init__()
        self.user_info = user_info
        self.title(f"ТильдаКод [Пользователь: {self.user_info['name']}, Роль: {self.user_info['role']}]")
        self.geometry("600x400")

        self._create_menu()

        label = ttk.Label(self, text="Добро пожаловать, Администратор!", font=("Arial", 14))
        label.pack(expand=True)

    def _create_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Выход", command=self.quit)
        menubar.add_cascade(label="Файл", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="О программе")
        menubar.add_cascade(label="Справка", menu=help_menu)