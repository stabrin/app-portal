# desktop-app/src/operator_work_ui.py
# This is a placeholder file for the main operator window.

from PySide6.QtWidgets import QMainWindow, QLabel, QVBoxLayout, QWidget

class OperatorWorkWindow(QMainWindow):
    """
    Placeholder for the main operator work window.
    This window will contain the UI for printing and aggregation.
    """
    def __init__(self, task_service, task_info, parent=None):
        super().__init__(parent)
        self.task_service = task_service
        self.task_info = task_info

        self.setWindowTitle(f"Работа по задаче №{self.task_info.get('task_id')} - Оператор #{self.task_info.get('employee_id')}")
        self.setMinimumSize(800, 600)
        
        central_widget = QWidget()
        layout = QVBoxLayout(central_widget)
        
        # Placeholder content
        label = QLabel(f"Окно для выполнения задачи. GTINs: {self.task_info.get('gtins')}")
        layout.addWidget(label)
        
        self.setCentralWidget(central_widget)
