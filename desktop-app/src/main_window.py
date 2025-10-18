# src/main_window.py

import tkinter as tk
import subprocess
import sys
import os

def run_db_setup():
    """
    Запускает скрипт setup_database.py в новом окне терминала.
    """
    try:
        # Определяем корневую папку проекта (на уровень выше, чем 'src')
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        script_path = os.path.join(project_root, 'scripts', 'setup_database.py')

        # Проверяем, существует ли скрипт
        if not os.path.exists(script_path):
            print(f"Ошибка: Скрипт не найден по пути {script_path}")
            # Можно показать ошибку и в GUI
            tk.messagebox.showerror("Ошибка", f"Скрипт не найден:\n{script_path}")
            return

        # sys.executable - это путь к интерпретатору Python, который запустил это приложение
        # (правильный Python из вашего .venv)
        command = [sys.executable, script_path]

        # Запускаем скрипт в новом окне консоли
        subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE)

    except Exception as e:
        print(f"Не удалось запустить скрипт: {e}")
        tk.messagebox.showerror("Ошибка запуска", f"Не удалось запустить скрипт:\n{e}")


# 1. Создаем главное окно приложения
root = tk.Tk()

# 2. Устанавливаем заголовок окна
root.title("ТильдаКод")

# 3. Устанавливаем начальный размер окна (ширина x высота)
root.geometry("600x400")

# 4. Создаем главное меню
menubar = tk.Menu(root)
root.config(menu=menubar)

# -- Меню "Файл" --
file_menu = tk.Menu(menubar, tearoff=0)
file_menu.add_command(label="Инициализация БД", command=run_db_setup)
file_menu.add_separator()
file_menu.add_command(label="Выход", command=root.quit)
menubar.add_cascade(label="Файл", menu=file_menu)

# -- Меню "Справка" --
help_menu = tk.Menu(menubar, tearoff=0)
help_menu.add_command(label="О программе") # Пока без функции
menubar.add_cascade(label="Справка", menu=help_menu)

# 6. Запускаем главный цикл приложения.
#    Окно будет отображаться и ждать действий пользователя, пока его не закроют.
root.mainloop()
