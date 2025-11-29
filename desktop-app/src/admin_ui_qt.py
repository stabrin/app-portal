from PySide6.QtWidgets import QMainWindow, QWidget, QLabel, QVBoxLayout, QApplication
import sys


class AdminWindowQt(QMainWindow):
    def __init__(self, user_info: dict):
        super().__init__()
        self.user_info = user_info
        self.setWindowTitle(f"Admin - {user_info.get('name', 'unknown')}")
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout()
        label = QLabel("Admin UI (PySide6) - заглушка")
        layout.addWidget(label)
        central.setLayout(layout)
        self.setCentralWidget(central)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = AdminWindowQt({'name': 'local-admin'})
    w.show()
    sys.exit(app.exec())
