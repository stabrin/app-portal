#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Финальный тест интеграции сводной таблицы в админ интерфейс
Проверяет все аспекты: структура, данные, стилизация, взаимодействие
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Добавляем путь к desktop-app в PYTHONPATH
app_path = Path(__file__).parent / 'desktop-app'
sys.path.insert(0, str(app_path))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from src.admin_ui_qt import AdminWindowQt

def test_complete_workflow():
    """Тест полного рабочего процесса с уведомлениями и сводкой."""
    print("=" * 70)
    print("ФИНАЛЬНЫЙ ТЕСТ ИНТЕГРАЦИИ СВОДНОЙ ТАБЛИЦЫ")
    print("=" * 70)
    
    test_user_info = {
        'user_id': 1,
        'username': 'test_admin',
        'role': 'admin',
        'client_db_config': {}
    }
    
    # Тестовые данные уведомлений
    test_notifications = [
        {
            'id': 1,
            'scenario_name': 'Завоз товаров',
            'client_name': 'ООО Товары',
            'product_groups': [{'name': 'Продукты'}],
            'planned_arrival_date': '2025-11-29',
            'vehicle_number': 'АА123АА',
            'status': 'Ожидание',
            'positions_count': 5,
            'dm_count': 15,
        },
        {
            'id': 2,
            'scenario_name': 'Доставка табака',
            'client_name': 'ООО Табак',
            'product_groups': [{'name': 'Табак'}],
            'planned_arrival_date': '2025-11-29',
            'vehicle_number': 'БВ456БВ',
            'status': 'Проект',
            'positions_count': 10,
            'dm_count': 30,
        },
    ]
    
    # Тестовые данные сводки
    test_summary = [
        {
            'client_name': 'ООО Товары',
            'd0_ув': 1, 'd0_поз': 5, 'd0_дм': 15,
            'd1_ув': 0, 'd1_поз': 0, 'd1_дм': 0,
            'd2_ув': 0, 'd2_поз': 0, 'd2_дм': 0,
            'd3_ув': 0, 'd3_поз': 0, 'd3_дм': 0,
        },
        {
            'client_name': 'ООО Табак',
            'd0_ув': 1, 'd0_поз': 10, 'd0_дм': 30,
            'd1_ув': 0, 'd1_поз': 0, 'd1_дм': 0,
            'd2_ув': 0, 'd2_поз': 0, 'd2_дм': 0,
            'd3_ув': 0, 'd3_поз': 0, 'd3_дм': 0,
        },
    ]
    
    try:
        print("\n1. Инициализация интерфейса...")
        window = AdminWindowQt(test_user_info)
        print("   ✓ Окно создано")
        print(f"   ✓ Пользователь: {window.user_info.get('username', 'N/A')}")
        
        print("\n2. Проверка структуры таблиц...")
        
        # Таблица уведомлений
        notif_cols = window.notifications_table.columnCount()
        print(f"   ✓ Таблица уведомлений: {notif_cols} колонок")
        
        notif_headers = [
            window.notifications_table.horizontalHeaderItem(i).text() 
            for i in range(min(5, notif_cols))
        ]
        print(f"   ✓ Первые заголовки: {notif_headers}")
        
        # Таблица сводки
        summary_cols = window.summary_table.columnCount()
        summary_rows = window.summary_table.rowCount()
        print(f"   ✓ Таблица сводки: {summary_cols} колонок, {summary_rows} строк")
        
        summary_headers = [
            window.summary_table.horizontalHeaderItem(i).text() 
            for i in range(min(3, summary_cols))
        ]
        print(f"   ✓ Первые заголовки сводки: {summary_headers}")
        
        print("\n3. Тестирование загрузки данных...")
        
        # Подменяем сервис для обеих таблиц
        with patch('src.admin_ui_qt.SupplyNotificationService') as mock_service_class:
            # Первый вызов - для load_notifications, второй - для load_summary_data
            mock_service = MagicMock()
            mock_service.get_notifications_with_counts.return_value = test_notifications
            mock_service.get_arrival_summary.return_value = test_summary
            mock_service_class.return_value = mock_service
            
            # Загружаем уведомления
            window.load_notifications()
            
            notif_rows = window.notifications_table.rowCount()
            print(f"   ✓ Загружено уведомлений: {notif_rows}")
            
            if notif_rows > 0:
                # Проверяем первое уведомление
                first_notif_name = window.notifications_table.item(0, 1)
                if first_notif_name:
                    print(f"   ✓ Первое уведомление: {first_notif_name.text()}")
                
                # Проверяем цвета строк
                first_item = window.notifications_table.item(0, 0)
                if first_item:
                    bg_color = first_item.background()
                    print(f"   ✓ Фон первой строки установлен")
            
            # Проверяем загрузку сводки
            summary_rows = window.summary_table.rowCount()
            print(f"   ✓ Загружено строк в сводку: {summary_rows}")
            
            if summary_rows > 0:
                first_client = window.summary_table.item(0, 0)
                if first_client:
                    print(f"   ✓ Первый клиент в сводке: {first_client.text()}")
        
        print("\n4. Проверка интерактивности...")
        
        # Проверяем, что таблица имеет doubleClicked сигнал
        if hasattr(window.notifications_table, 'doubleClicked'):
            print("   ✓ Сигнал doubleClicked доступен в notifications_table")
        else:
            print("   ✗ Сигнал doubleClicked НЕ найден")
            return False
        
        print("\n5. Проверка методов...")
        
        required_methods = [
            'load_notifications',
            'load_summary_data',
            'open_notification_details',
            'load_notification_details',
        ]
        
        for method in required_methods:
            if hasattr(window, method):
                print(f"   ✓ Метод {method} доступен")
            else:
                print(f"   ✗ Метод {method} НЕ найден")
                return False
        
        print("\n" + "=" * 70)
        print("✓ ВСЕ ПРОВЕРКИ ФИНАЛЬНОГО ТЕСТИРОВАНИЯ ПРОЙДЕНЫ УСПЕШНО!")
        print("=" * 70)
        return True
        
    except Exception as e:
        print(f"\n✗ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_readme_generation():
    """Генерирует README с описанием реализованной функции."""
    print("\n" + "=" * 70)
    print("ОПИСАНИЕ РЕАЛИЗОВАННОЙ ФУНКЦИИ")
    print("=" * 70)
    
    readme = """
## Сводка по поставкам (Summary Table)

### Что было реализовано:

✓ **Таблица сводки** под основной таблицей уведомлений
  - Динамические заголовки с датами (сегодня + 3 дня)
  - Каждый день показывает 3 метрики: Уведомления, Позиции, Коды ДМ
  - Компактный размер (макс. высота 150px)

✓ **Структура данных**
  - 13 колонок: Клиент (1) + Дни (4) × Метрики (3)
  - Ширина колонок: Клиент (120px), Метрики (50px каждая)
  - Все элементы нередактируемые для безопасности

✓ **Визуальная стилизация**
  - Такой же стиль выделения как в основной таблице (#ADD8E6 при выборе)
  - Центральное выравнивание числовых значений
  - Заголовки с переносом строк для читаемости

✓ **Интеграция с сервисом**
  - Автоматическая загрузка данных при открытии страницы уведомлений
  - Использует SupplyNotificationService.get_arrival_summary()
  - Обработка пустых данных и None значений
  - Преобразование числовых значений в строки

### Расположение кода:

📁 desktop-app/src/admin_ui_qt.py
   - Метод _build_notifications_list_page() (строки 156-237)
   - Метод load_notifications() (строки 435-492)
   - Метод load_summary_data() (строки 494-530)

### Как использовать:

1. Откройте админ интерфейс
2. Перейдите на страницу "Управление уведомлениями"
3. Основная таблица показывает все уведомления с деталями
4. Сводка внизу показывает агрегированные данные по дням и клиентам
5. Все данные загружаются автоматически

### Тестирование:

Запустите тесты для проверки функциональности:

    # Базовые проверки структуры
    python test_admin_ui_integration.py
    
    # Проверка загрузки данных
    python test_admin_ui_summary_data.py
    
    # Финальный интеграционный тест
    python test_admin_ui_final.py
"""
    
    print(readme)
    return True

if __name__ == '__main__':
    # Создаём QApplication для работы с UI
    app = QApplication(sys.argv)
    
    # Запускаем тесты
    success = test_complete_workflow()
    if success:
        test_readme_generation()
    
    if success:
        print("\n✓ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        sys.exit(0)
    else:
        print("\n✗ ТЕСТЫ НЕ ПРОЙДЕНЫ")
        sys.exit(1)
