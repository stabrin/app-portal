#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест загрузки данных сводной таблицы с имитацией данных
"""

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

# Добавляем путь к desktop-app в PYTHONPATH
app_path = Path(__file__).parent / 'desktop-app'
sys.path.insert(0, str(app_path))

from PySide6.QtWidgets import QApplication
from src.admin_ui_qt import AdminWindowQt

def test_summary_data_loading():
    """Проверяет загрузку данных в сводную таблицу."""
    print("=" * 60)
    print("Тест загрузки данных сводной таблицы")
    print("=" * 60)
    
    test_user_info = {
        'user_id': 1,
        'username': 'test_user',
        'role': 'admin',
        'client_db_config': {}
    }
    
    try:
        # Создаём окно
        window = AdminWindowQt(test_user_info)
        print("✓ Окно создано")
        
        # Подготавливаем тестовые данные
        test_summary_data = [
            {
                'client_name': 'ООО Товары',
                'd0_ув': 3,
                'd0_поз': 15,
                'd0_дм': 45,
                'd1_ув': 2,
                'd1_поз': 10,
                'd1_дм': 30,
                'd2_ув': 1,
                'd2_поз': 5,
                'd2_дм': 15,
                'd3_ув': 0,
                'd3_поз': 0,
                'd3_дм': 0,
            },
            {
                'client_name': 'ООО Табак',
                'd0_ув': 5,
                'd0_поз': 25,
                'd0_дм': 75,
                'd1_ув': 3,
                'd1_поз': 15,
                'd1_дм': 45,
                'd2_ув': 2,
                'd2_поз': 10,
                'd2_дм': 30,
                'd3_ув': 1,
                'd3_поз': 5,
                'd3_дм': 15,
            },
        ]
        
        # Подменяем сервис
        with patch('src.admin_ui_qt.SupplyNotificationService') as mock_service_class:
            mock_service = MagicMock()
            mock_service.get_arrival_summary.return_value = test_summary_data
            mock_service_class.return_value = mock_service
            
            # Вызываем метод загрузки
            window.load_summary_data()
            
            # Проверяем, что таблица заполнена
            row_count = window.summary_table.rowCount()
            print(f"✓ Таблица заполнена: {row_count} строк")
            
            if row_count == 2:
                print("✓ Количество строк соответствует тестовым данным")
            else:
                print(f"✗ Ожидалось 2 строки, получено {row_count}")
                return False
            
            # Проверяем первую строку
            first_client = window.summary_table.item(0, 0)
            if first_client and first_client.text() == 'ООО Товары':
                print(f"✓ Первый клиент корректен: '{first_client.text()}'")
            else:
                print(f"✗ Первый клиент неверен")
                return False
            
            # Проверяем некоторые значения из первой строки
            # Колонка 1 (d0_ув): индекс 1
            d0_ув_item = window.summary_table.item(0, 1)
            if d0_ув_item and d0_ув_item.text() == '3':
                print(f"✓ Значение d0_ув корректно: {d0_ув_item.text()}")
            else:
                print(f"✗ Значение d0_ув неверно: ожидалось '3', получено '{d0_ув_item.text() if d0_ув_item else 'None'}'")
                return False
            
            # Проверяем второй клиент
            second_client = window.summary_table.item(1, 0)
            if second_client and second_client.text() == 'ООО Табак':
                print(f"✓ Второй клиент корректен: '{second_client.text()}'")
            else:
                print(f"✗ Второй клиент неверен")
                return False
            
            # Проверяем, что все элементы в таблице нередактируемые
            from PySide6.QtCore import Qt
            all_editable = False
            for row in range(row_count):
                for col in range(13):
                    item = window.summary_table.item(row, col)
                    if item:
                        if item.flags() & Qt.ItemIsEditable:
                            all_editable = True
                            print(f"✗ Элемент [{row},{col}] редактируемый")
                            break
            
            if not all_editable:
                print("✓ Все элементы в таблице нередактируемые")
            else:
                return False
            
            print("\n✓ Все проверки загрузки данных пройдены успешно!")
            return True
            
    except Exception as e:
        print(f"\n✗ ОШИБКА при загрузке данных: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_empty_data_loading():
    """Проверяет загрузку пустых данных."""
    print("\n" + "=" * 60)
    print("Тест загрузки пустых данных")
    print("=" * 60)
    
    test_user_info = {
        'user_id': 1,
        'username': 'test_user',
        'role': 'admin',
        'client_db_config': {}
    }
    
    try:
        window = AdminWindowQt(test_user_info)
        
        # Подменяем сервис с пустыми данными
        with patch('src.admin_ui_qt.SupplyNotificationService') as mock_service_class:
            mock_service = MagicMock()
            mock_service.get_arrival_summary.return_value = []
            mock_service_class.return_value = mock_service
            
            # Вызываем метод загрузки
            window.load_summary_data()
            
            # Проверяем, что таблица пуста
            row_count = window.summary_table.rowCount()
            if row_count == 0:
                print(f"✓ Таблица осталась пустой при пустых данных")
            else:
                print(f"✗ Таблица содержит {row_count} строк, ожидалось 0")
                return False
            
            print("\n✓ Тест пустых данных пройден успешно!")
            return True
            
    except Exception as e:
        print(f"\n✗ ОШИБКА при тесте пустых данных: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    # Создаём QApplication для работы с UI
    app = QApplication(sys.argv)
    
    # Запускаем тесты
    success = True
    success = test_summary_data_loading() and success
    success = test_empty_data_loading() and success
    
    if success:
        print("\n" + "=" * 60)
        print("✓ ВСЕ ТЕСТЫ ЗАГРУЗКИ ДАННЫХ ПРОЙДЕНЫ УСПЕШНО!")
        print("=" * 60)
        sys.exit(0)
    else:
        print("\n" + "=" * 60)
        print("✗ НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОЙДЕНЫ")
        print("=" * 60)
        sys.exit(1)
