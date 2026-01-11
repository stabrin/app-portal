# desktop-app/src/operator_login_ui.py

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QMessageBox, QFormLayout
)
from PySide6.QtCore import Qt

# Импорты будут добавлены позже, когда появится основной рабочий UI
# from .operator_work_ui import OperatorWorkWindow 
from .task_service import TaskService 

class OperatorLoginWindow(QDialog):
    """
    Окно для входа оператора по сканированию кода-пропуска.
    """
    def __init__(self, task_service: TaskService, user_info: dict = None, parent=None):
        super().__init__(parent)
        self.task_service = task_service
        # --- ИСПРАВЛЕНИЕ: Если user_info не передан, создаем пустой словарь ---
        # Это обеспечивает совместимость с разными точками входа в приложение.
        self.user_info = user_info if user_info is not None else {}
        
        self.task_info = None # Для хранения информации о задаче после успешного входа
        
        self.setWindowTitle("Вход для оператора")
        self.setMinimumWidth(400)
        
        self._init_ui()
        
    def _init_ui(self):
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # 1. Поле для ФИО
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Введите или отсканируйте ФИО")
        # --- ДЛЯ РАЗРАБОТКИ: Значение по умолчанию ---
        self.name_input.setText("Тест")
        form_layout.addRow("ФИО Оператора:", self.name_input)

        # 2. Поле для рабочего места
        self.workstation_input = QLineEdit()
        self.workstation_input.setPlaceholderText("Отсканируйте код рабочего места")
        # --- ДЛЯ РАЗРАБОТКИ: Значение по умолчанию ---
        self.workstation_input.setText("A43aa946-cb6c-4033-8e62-15ec44c8e3e5")
        form_layout.addRow("Рабочее место:", self.workstation_input)

        # 3. Поле для кода-пропуска
        self.access_code_input = QLineEdit()
        self.access_code_input.setPlaceholderText("Ожидание сканирования кода-пропуска...")
        # Подсказка системе ввода, что здесь предпочтительна латиница
        self.access_code_input.setInputMethodHints(Qt.ImhNoPredictiveText | Qt.ImhLatinOnly)
        form_layout.addRow("Код-пропуск:", self.access_code_input)
        # --- ДЛЯ РАЗРАБОТКИ: Значение по умолчанию ---
        self.access_code_input.setText("LH1VLPVB")
        
        layout.addLayout(form_layout)

        # Логика переключения фокуса и отправки
        self.name_input.returnPressed.connect(self.workstation_input.setFocus)
        self.workstation_input.returnPressed.connect(self.access_code_input.setFocus)
        self.access_code_input.returnPressed.connect(self.process_login)
        
    def process_login(self):
        """
        Обрабатывает введенные данные и выполняет вход.
        """
        operator_name = self.name_input.text().strip()
        if not operator_name:
            QMessageBox.warning(self, "Ошибка", "Поле 'ФИО Оператора' обязательно для заполнения.")
            self.name_input.setFocus()
            return

        workstation_id = self.workstation_input.text().strip()
        if not workstation_id:
            QMessageBox.warning(self, "Ошибка", "Отсканируйте код рабочего места.")
            self.workstation_input.setFocus()
            return

        access_code = self.access_code_input.text().strip()
        if not access_code:
            QMessageBox.warning(self, "Ошибка", "Отсканируйте или введите код-пропуск.")
            self.access_code_input.setFocus()
            return
            
        logging.info(f"Попытка входа: Оператор '{operator_name}', РМ: '{workstation_id}', код-пропуск: {access_code}")
        
        try:
            # Передаем ФИО и РМ в сервисный метод
            task_info = self.task_service.get_task_by_employee_pass(access_code, operator_name, workstation_id)
            
            if task_info and task_info.get('is_valid'):
                logging.info(f"Успешный вход для сотрудника #{task_info['employee_id']} в задачу #{task_info['task_id']}")
                self.task_info = task_info
                self.task_info['operator_name'] = operator_name  # Добавляем ФИО в результат для UI

                # --- ИСПРАВЛЕНИЕ: Сохраняем client_id в user_info, который будет передан дальше ---
                client_id_from_task = task_info.get('client_id')
                if client_id_from_task:
                    self.user_info['client_id'] = client_id_from_task
                self.accept() # Закрываем диалог с успешным результатом

            else:
                error_message = task_info.get('error', 'Пропуск не найден или недействителен.')
                logging.warning(f"Неудачная попытка входа с кодом: {access_code}. Причина: {error_message}")
                QMessageBox.warning(self, "Ошибка входа", f"{error_message}\nПопробуйте еще раз.")
                self.access_code_input.clear()
                self.access_code_input.setFocus()
                
        except Exception as e:
            logging.error(f"Ошибка при проверке кода-пропуска: {e}", exc_info=True)
            QMessageBox.critical(self, "Критическая ошибка", f"Произошла ошибка при проверке пропуска: {e}")
            self.access_code_input.clear()

    def get_task_info(self):
        """Возвращает информацию о задаче после успешного входа."""
        return self.task_info

    def showEvent(self, event):
        """Гарантирует, что поле ввода ФИО в фокусе при показе окна."""
        super().showEvent(event)
        self.name_input.setFocus()
