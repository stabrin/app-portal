# desktop-app/src/operator_login_ui.py

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QMessageBox
)
from PySide6.QtCore import Qt

# Импорты будут добавлены позже, когда появится основной рабочий UI
# from .operator_work_ui import OperatorWorkWindow 
from .task_service import TaskService 

class OperatorLoginWindow(QDialog):
    """
    Окно для входа оператора по сканированию кода-пропуска.
    """
    def __init__(self, task_service: TaskService, parent=None):
        super().__init__(parent)
        self.task_service = task_service
        self.task_info = None # Для хранения информации о задаче после успешного входа
        
        self.setWindowTitle("Вход для оператора")
        self.setMinimumWidth(400)
        
        self._init_ui()
        
    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        label = QLabel("Отсканируйте ваш пропуск для начала работы")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        
        self.access_code_input = QLineEdit()
        self.access_code_input.setPlaceholderText("Ожидание сканирования кода...")
        self.access_code_input.returnPressed.connect(self.process_login)
        layout.addWidget(self.access_code_input)
        
        self.setLayout(layout)
        
    def process_login(self):
        """
        Обрабатывает введенный код доступа.
        """
        access_code = self.access_code_input.text().strip()
        if not access_code:
            return
            
        logging.info(f"Попытка входа с кодом-пропуском: {access_code}")
        
        try:
            # Шаг 2.1 плана: валидация пропуска и получение данных
            task_info = self.task_service.get_task_by_employee_pass(access_code)
            
            if task_info and task_info.get('is_valid'):
                logging.info(f"Успешный вход для сотрудника #{task_info['employee_id']} в задачу #{task_info['task_id']}")
                self.task_info = task_info # Сохраняем информацию для передачи
                self.accept() # Закрываем диалог с успешным результатом

            else:
                error_message = task_info.get('error', 'Пропуск не найден или недействителен.')
                logging.warning(f"Неудачная попытка входа с кодом: {access_code}. Причина: {error_message}")
                QMessageBox.warning(self, "Ошибка входа", f"{error_message}\nПопробуйте еще раз.")
                self.access_code_input.clear()
                
        except Exception as e:
            logging.error(f"Ошибка при проверке кода-пропуска: {e}", exc_info=True)
            QMessageBox.critical(self, "Критическая ошибка", f"Произошла ошибка при проверке пропуска: {e}")
            self.access_code_input.clear()

    def get_task_info(self):
        """Возвращает информацию о задаче после успешного входа."""
        return self.task_info

    def showEvent(self, event):
        """Гарантирует, что поле ввода в фокусе при показе окна."""
        super().showEvent(event)
        self.access_code_input.setFocus()
