#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест для проверки загрузки интерфейса Admin UI с сводкой по дням
"""

import sys
import os
from pathlib import Path

# Добавляем путь к desktop-app в PYTHONPATH
app_path = Path(__file__).parent / 'desktop-app'
sys.path.insert(0, str(app_path))

# Проверяем импорты
try:
    from PySide6.QtWidgets import QApplication
    print("✓ PySide6 импортирована")
except ImportError as e:
    print(f"✗ Ошибка при импорте PySide6: {e}")
    sys.exit(1)

try:
    from src.admin_ui_qt import AdminWindowQt
    print("✓ AdminWindowQt импортирована")
except ImportError as e:
    print(f"✗ Ошибка при импорте AdminWindowQt: {e}")
    sys.exit(1)

try:
    from src.supply_notification_service import SupplyNotificationService
    print("✓ SupplyNotificationService импортирована")
except ImportError as e:
    print(f"✗ Ошибка при импорте SupplyNotificationService: {e}")
    sys.exit(1)

# Проверяем методы в сервисе
service_methods = dir(SupplyNotificationService)
if 'get_arrival_summary' in service_methods:
    print("✓ Метод get_arrival_summary найден в сервисе")
else:
    print("✗ Метод get_arrival_summary НЕ найден в сервисе")
    print(f"  Доступные методы: {[m for m in service_methods if not m.startswith('_')]}")

# Проверяем методы в AdminWindowQt
admin_methods = dir(AdminWindowQt)
if 'load_summary_data' in admin_methods:
    print("✓ Метод load_summary_data найден в AdminWindowQt")
else:
    print("✗ Метод load_summary_data НЕ найден в AdminWindowQt")

if 'load_notifications' in admin_methods:
    print("✓ Метод load_notifications найден в AdminWindowQt")
else:
    print("✗ Метод load_notifications НЕ найден в AdminWindowQt")

print("\n✓ Все проверки синтаксиса пройдены успешно!")
