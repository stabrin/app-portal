#!/usr/bin/env python
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'desktop-app'))

from PySide6.QtWidgets import QApplication
from desktop_app.src.admin_ui_qt import AdminWindowQt

if __name__ == '__main__':
    app = QApplication(sys.argv)
    user_info = {
        'client_db_config': {},
        'name': 'local-admin'
    }
    w = AdminWindowQt(user_info)
    w.show()
    sys.exit(app.exec())
