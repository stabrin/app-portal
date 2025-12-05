#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест проверки интеграции сводной таблицы с Admin UI
"""

import sys
import os
from pathlib import Path

# Добавляем путь к desktop-app в PYTHONPATH
app_path = Path(__file__).parent / 'desktop-app'
sys.path.insert(0, str(app_path))

from PySide6.QtWidgets import QApplication, QTableWidget
from src.admin_ui_qt import AdminWindowQt
from src.supply_notification_service import SupplyNotificationService

def test_admin_window_structure():
    """Проверяет, что окно Admin UI правильно инициализировано."""
    print("=" * 60)
    print("Тест структуры окна Admin UI")
    print("=" * 60)
    
    # Минимальные данные пользователя для тестирования
    test_user_info = {
        'user_id': 1,
        'username': 'test_user',
        'role': 'admin',
        'client_db_config': {
            'host': 'localhost',
            'port': 5432,
            'database': 'test_db',
            'user': 'test_user',
            'password': 'test_pass'
        }
    }
    
    try:
        # Создаём окно
        window = AdminWindowQt(test_user_info)
        print("✓ Окно AdminWindowQt создано успешно")
        
        # Проверяем наличие страницы уведомлений
        if hasattr(window, 'page_notifications_list'):
            print("✓ Страница notifications_list инициализирована")
        else:
            print("✗ Страница notifications_list НЕ найдена")
            return False
        
        # Проверяем таблицу уведомлений
        if hasattr(window, 'notifications_table'):
            print("✓ Таблица notifications_table инициализирована")
            print(f"  - Тип: {type(window.notifications_table)}")
            print(f"  - Количество колонок: {window.notifications_table.columnCount()}")
            print(f"  - Заголовки: {[window.notifications_table.horizontalHeaderItem(i).text() if window.notifications_table.horizontalHeaderItem(i) else '' for i in range(window.notifications_table.columnCount())]}")
        else:
            print("✗ Таблица notifications_table НЕ найдена")
            return False
        
        # Проверяем сводную таблицу
        if hasattr(window, 'summary_table'):
            print("✓ Таблица summary_table инициализирована")
            print(f"  - Тип: {type(window.summary_table)}")
            print(f"  - Количество колонок: {window.summary_table.columnCount()}")
            headers = [window.summary_table.horizontalHeaderItem(i).text() if window.summary_table.horizontalHeaderItem(i) else '' for i in range(window.summary_table.columnCount())]
            print(f"  - Заголовки (первые 3):")
            for i, h in enumerate(headers[:3]):
                print(f"    [{i}]: {h}")
        else:
            print("✗ Таблица summary_table НЕ найдена")
            return False
        
        # Проверяем методы загрузки данных
        if hasattr(window, 'load_notifications'):
            print("✓ Метод load_notifications доступен")
        else:
            print("✗ Метод load_notifications НЕ найден")
            return False
        
        if hasattr(window, 'load_summary_data'):
            print("✓ Метод load_summary_data доступен")
        else:
            print("✗ Метод load_summary_data НЕ найден")
            return False
        
        print("\n✓ Все проверки структуры пройдены успешно!")
        return True
        
    except Exception as e:
        print(f"\n✗ ОШИБКА при создании окна: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_summary_table_headers():
    """Проверяет корректность заголовков сводной таблицы."""
    print("\n" + "=" * 60)
    print("Тест заголовков сводной таблицы")
    print("=" * 60)
    
    from datetime import datetime, timedelta
    
    # Проверяем, что заголовки включают даты
    today = datetime.now().date()
    expected_dates = [
        (today + timedelta(days=i)).strftime('%d.%m.%Y')
        for i in range(4)
    ]
    
    print(f"Ожидаемые даты:")
    for i, date in enumerate(expected_dates):
        print(f"  d{i}: {date}")
    
    # Ожидаемые метрики
    expected_metrics = ['Ув', 'Поз', 'ДМ']
    print(f"\nОжидаемые метрики: {expected_metrics}")
    
    # Ожидаемое количество колонок: 1 (Client) + 4*3 (days*metrics)
    expected_columns = 1 + 4 * 3
    print(f"\nОжидаемое количество колонок: {expected_columns}")
    
    # Проверяем структуру
    test_user_info = {
        'user_id': 1,
        'username': 'test_user',
        'role': 'admin',
        'client_db_config': {}
    }
    
    try:
        window = AdminWindowQt(test_user_info)
        actual_columns = window.summary_table.columnCount()
        
        if actual_columns == expected_columns:
            print(f"✓ Количество колонок совпадает: {actual_columns}")
        else:
            print(f"✗ Количество колонок не совпадает: ожидалось {expected_columns}, получено {actual_columns}")
            return False
        
        # Проверяем первый заголовок
        first_header = window.summary_table.horizontalHeaderItem(0).text() if window.summary_table.horizontalHeaderItem(0) else ''
        if first_header == "Клиент":
            print(f"✓ Первый заголовок корректен: '{first_header}'")
        else:
            print(f"✗ Первый заголовок неверен: ожидалось 'Клиент', получено '{first_header}'")
        
        # Проверяем, что в заголовках присутствуют даты
        all_headers = [window.summary_table.horizontalHeaderItem(i).text() if window.summary_table.horizontalHeaderItem(i) else '' for i in range(actual_columns)]
        
        print(f"\nВсе заголовки ({len(all_headers)} шт):")
        for i, h in enumerate(all_headers):
            print(f"  [{i}]: {h[:20]}..." if len(h) > 20 else f"  [{i}]: {h}")
        
        # Проверяем, что хотя бы одна дата присутствует в заголовках
        any_date_found = any(expected_dates[0][:5] in h for h in all_headers)
        if any_date_found:
            print(f"✓ Даты присутствуют в заголовках")
        else:
            print(f"✗ Даты НЕ найдены в заголовках")
            return False
        
        print("\n✓ Все проверки заголовков пройдены успешно!")
        return True
        
    except Exception as e:
        print(f"\n✗ ОШИБКА при проверке заголовков: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_task_management_structure():
    """Проверяет, что UI для управления задачами правильно инициализировано."""
    print("\n" + "=" * 60)
    print("Тест структуры страницы 'Управление задачами'")
    print("=" * 60)
    
    test_user_info = {'client_db_config': {}, 'name': 'test-admin'}
    
    try:
        window = AdminWindowQt(test_user_info)
        print("✓ Окно AdminWindowQt создано")

        # Проверяем наличие страницы задач
        if hasattr(window, 'page_tasks'):
            print("✓ Атрибут page_tasks найден")
        else:
            print("✗ Атрибут page_tasks НЕ найден")
            return False

        # Проверяем наличие таблицы задач
        if hasattr(window, 'tasks_table'):
            print("✓ Таблица tasks_table найдена")
            print(f"  - Тип: {type(window.tasks_table)}")
            actual_cols = window.tasks_table.columnCount()
            expected_cols = 5
            if actual_cols == expected_cols:
                print(f"✓ Количество колонок совпадает: {actual_cols}")
            else:
                print(f"✗ Количество колонок НЕ совпадает: ожидалось {expected_cols}, получено {actual_cols}")
                return False
        else:
            print("✗ Таблица tasks_table НЕ найдена")
            return False

        # Проверяем наличие стека управления задачами
        if hasattr(window, 'task_management_stack'):
            print("✓ Стек task_management_stack найден")
        else:
            print("✗ Стек task_management_stack НЕ найден")
            return False
            
        # Проверяем наличие методов
        if hasattr(window, 'load_tasks'):
            print("✓ Метод load_tasks найден")
        else:
            print("✗ Метод load_tasks НЕ найден")
            return False
            
        if hasattr(window, 'on_task_select'):
            print("✓ Метод on_task_select найден")
        else:
            print("✗ Метод on_task_select НЕ найден")
            return False

        print("\n✓ Все проверки структуры 'Управление задачами' пройдены!")
        return True

    except Exception as e:
        print(f"\n✗ ОШИБКА при проверке страницы 'Управление задачами': {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    # Создаём QApplication для работы с UI
    app = QApplication(sys.argv)
    
    # Запускаем тесты
    success = True
    success = test_admin_window_structure() and success
    success = test_summary_table_headers() and success
    success = test_task_management_structure() and success
    
    if success:
        print("\n" + "=" * 60)
