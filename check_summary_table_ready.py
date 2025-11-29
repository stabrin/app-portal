#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Быстрая проверка готовности таблицы сводки перед боевым использованием
"""

import sys
from pathlib import Path

# Добавляем путь к desktop-app в PYTHONPATH
app_path = Path(__file__).parent / 'desktop-app'
sys.path.insert(0, str(app_path))

def check_all():
    """Проверка всех компонентов."""
    print("=" * 70)
    print("БЫСТРАЯ ПРОВЕРКА ГОТОВНОСТИ СВОДНОЙ ТАБЛИЦЫ")
    print("=" * 70)
    
    checks = []
    
    # 1. Проверка импортов
    print("\n1️⃣ ПРОВЕРКА ИМПОРТОВ")
    print("-" * 70)
    
    try:
        from PySide6.QtWidgets import QApplication, QTableWidget
        print("   ✅ PySide6 доступна")
        checks.append(("PySide6", True))
    except ImportError as e:
        print(f"   ❌ Ошибка PySide6: {e}")
        checks.append(("PySide6", False))
    
    try:
        from src.admin_ui_qt import AdminWindowQt
        print("   ✅ AdminWindowQt доступна")
        checks.append(("AdminWindowQt", True))
    except ImportError as e:
        print(f"   ❌ Ошибка AdminWindowQt: {e}")
        checks.append(("AdminWindowQt", False))
    
    try:
        from src.supply_notification_service import SupplyNotificationService
        print("   ✅ SupplyNotificationService доступна")
        checks.append(("SupplyNotificationService", True))
    except ImportError as e:
        print(f"   ❌ Ошибка SupplyNotificationService: {e}")
        checks.append(("SupplyNotificationService", False))
    
    # 2. Проверка методов
    print("\n2️⃣ ПРОВЕРКА МЕТОДОВ")
    print("-" * 70)
    
    try:
        from src.supply_notification_service import SupplyNotificationService
        import inspect
        
        methods = [m for m in dir(SupplyNotificationService) if not m.startswith('_')]
        
        if 'get_arrival_summary' in methods:
            print("   ✅ Метод get_arrival_summary существует")
            checks.append(("get_arrival_summary", True))
        else:
            print("   ❌ Метод get_arrival_summary НЕ найден")
            checks.append(("get_arrival_summary", False))
        
        if 'get_notifications_with_counts' in methods:
            print("   ✅ Метод get_notifications_with_counts существует")
            checks.append(("get_notifications_with_counts", True))
        else:
            print("   ❌ Метод get_notifications_with_counts НЕ найден")
            checks.append(("get_notifications_with_counts", False))
            
    except Exception as e:
        print(f"   ❌ Ошибка при проверке методов: {e}")
        checks.append(("Service methods", False))
    
    # 3. Проверка структуры админ окна
    print("\n3️⃣ ПРОВЕРКА СТРУКТУРЫ АДМИН ОКНА")
    print("-" * 70)
    
    try:
        from PySide6.QtWidgets import QApplication
        from src.admin_ui_qt import AdminWindowQt
        
        app = QApplication.instance() or QApplication(sys.argv)
        
        test_user_info = {
            'user_id': 1,
            'username': 'test',
            'role': 'admin',
            'client_db_config': {}
        }
        
        window = AdminWindowQt(test_user_info)
        
        if hasattr(window, 'notifications_table'):
            cols = window.notifications_table.columnCount()
            print(f"   ✅ notifications_table существует ({cols} колонок)")
            checks.append(("notifications_table", True))
        else:
            print("   ❌ notifications_table НЕ найдена")
            checks.append(("notifications_table", False))
        
        if hasattr(window, 'summary_table'):
            cols = window.summary_table.columnCount()
            print(f"   ✅ summary_table существует ({cols} колонок)")
            if cols == 13:
                print("      ✓ Количество колонок корректно (13)")
                checks.append(("summary_table", True))
            else:
                print(f"      ⚠️  Ожидалось 13 колонок, получено {cols}")
                checks.append(("summary_table", False))
        else:
            print("   ❌ summary_table НЕ найдена")
            checks.append(("summary_table", False))
        
        if hasattr(window, 'load_summary_data'):
            print("   ✅ Метод load_summary_data существует")
            checks.append(("load_summary_data", True))
        else:
            print("   ❌ Метод load_summary_data НЕ найден")
            checks.append(("load_summary_data", False))
        
        if hasattr(window, 'load_notifications'):
            print("   ✅ Метод load_notifications существует")
            checks.append(("load_notifications", True))
        else:
            print("   ❌ Метод load_notifications НЕ найден")
            checks.append(("load_notifications", False))
            
    except Exception as e:
        print(f"   ❌ Ошибка при проверке структуры: {e}")
        checks.append(("Admin window structure", False))
    
    # 4. Проверка файлов
    print("\n4️⃣ ПРОВЕРКА ФАЙЛОВ")
    print("-" * 70)
    
    required_files = [
        'desktop-app/src/admin_ui_qt.py',
        'desktop-app/src/supply_notification_service.py',
        'SUMMARY_TABLE_IMPLEMENTATION.md',
        'SUMMARY_TABLE_REPORT.md',
    ]
    
    for file_path in required_files:
        full_path = Path(__file__).parent / file_path
        if full_path.exists():
            size = full_path.stat().st_size
            print(f"   ✅ {file_path} ({size} байт)")
            checks.append((f"File: {file_path}", True))
        else:
            print(f"   ❌ {file_path} НЕ НАЙДЕН")
            checks.append((f"File: {file_path}", False))
    
    # 5. Проверка тестов
    print("\n5️⃣ ПРОВЕРКА ТЕСТОВЫХ ФАЙЛОВ")
    print("-" * 70)
    
    test_files = [
        'test_admin_ui_integration.py',
        'test_admin_ui_summary_data.py',
        'test_admin_ui_final.py',
    ]
    
    for test_file in test_files:
        full_path = Path(__file__).parent / test_file
        if full_path.exists():
            print(f"   ✅ {test_file} доступен")
            checks.append((f"Test: {test_file}", True))
        else:
            print(f"   ⚠️  {test_file} НЕ найден (опционально)")
            checks.append((f"Test: {test_file}", False))
    
    # Итоговый результат
    print("\n" + "=" * 70)
    print("ИТОГОВАЯ ПРОВЕРКА")
    print("=" * 70)
    
    total = len(checks)
    passed = sum(1 for _, result in checks if result)
    failed = total - passed
    
    print(f"\n📊 Результаты:")
    print(f"   ✅ Пройдено: {passed}/{total}")
    print(f"   ❌ Не пройдено: {failed}/{total}")
    
    if failed > 0:
        print(f"\n❌ КРИТИЧЕСКИЕ ОШИБКИ:")
        for name, result in checks:
            if not result:
                print(f"   - {name}")
    
    print("\n" + "=" * 70)
    
    if failed == 0:
        print("✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ!")
        print("🚀 ГОТОВО К БОЕВОМУ ИСПОЛЬЗОВАНИЮ")
        print("=" * 70)
        return 0
    else:
        print("❌ НАЙДЕНЫ ОШИБКИ - ТРЕБУЕТСЯ ИСПРАВЛЕНИЕ")
        print("=" * 70)
        return 1

if __name__ == '__main__':
    sys.exit(check_all())
