import copy
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QMessageBox, QApplication, QLabel, QFileDialog, QTextEdit,
    QLineEdit, QHeaderView, QDateEdit, QDialog, QFormLayout, QComboBox, QSplitter, QTabWidget, QProgressDialog, QDialogButtonBox, QCheckBox,
    QGroupBox, QRadioButton, QSpinBox,
    QInputDialog, QTreeWidget, QTreeWidgetItem, QStackedWidget, QAbstractItemView,
    QGraphicsScene, QGraphicsView, QGraphicsRectItem, QGraphicsTextItem, QGraphicsItem
)
# --- NEW IMPORTS FOR PRINTING ---
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from PySide6.QtCore import Qt, Slot, QDate, QTimer, QThread, Signal, QObject, QRectF, QSize
from PySide6.QtGui import QColor, QPen, QPainter, QFont, QPixmap
import sys
import traceback
import logging
import json
from datetime import datetime
import io
# --- NEW IMPORTS FOR BARCODE GENERATION ---
from reportlab.graphics.barcode import code128
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPM
from PIL import Image
# --- END NEW IMPORTS ---

from dateutil.relativedelta import relativedelta
import pandas as pd
from .db_connector import get_client_db_connection
from .catalogs_service import CatalogsService
from .supply_notification_service import SupplyNotificationService
from .aggregation_service import run_aggregation_process_desktop
from .api_service import ApiService # ИСПРАВЛЕНИЕ: Добавляем импорт ApiService
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor # ИСПРАВЛЕНИЕ: Добавляем импорт RealDictCursor
from .sscc_service import generate_sscc, read_and_increment_counter # НОВОЕ: Импорт для генерации SSCC
from .order_service import OrderService # НОВЫЙ СЕРВИС
from .task_service import TaskService # НОВЫЙ СЕРВИС
import base64
import os
import re # ИСПРАВЛЕНИЕ: Добавляем импорт модуля re
import csv # Для работы с CSV
# --- НОВЫЙ КЛАСС: Рабочий для проверки API в фоновом потоке ---
class ApiStatusWorker(QObject):
    finished = Signal(bool)

    def __init__(self, user_info):
        super().__init__()
        self.user_info = user_info

    def run(self):
        """Выполняет проверку токена API."""
        is_valid = False
        try:
            # Проверяем, есть ли вообще конфиг для API
            if not self.user_info.get('client_api_config', {}).get('api_base_url'):
                self.finished.emit(False)
                return
            
            from .api_service import ApiService
            api_service = ApiService(self.user_info)
            api_service.get_participants() # Этот вызов проверит и при необходимости обновит токен
            is_valid = True
        except Exception as e:
            logging.error(f"Ошибка при фоновой проверке токена API: {e}")
        
        self.finished.emit(is_valid)

# --- НОВЫЙ КЛАСС: Рабочий для проверки статуса БД в фоновом потоке ---
class DbStatusWorker(QObject):
    finished = Signal(bool)

    def __init__(self, user_info):
        super().__init__()
        self.user_info = user_info

    def run(self):
        """Пытается получить соединение с БД клиента."""
        is_connected = False
        try:
            with get_client_db_connection(self.user_info) as conn:
                is_connected = (conn is not None)
        except Exception as e:
            logging.error(f"Ошибка при фоновой проверке соединения с БД: {e}")
        self.finished.emit(is_connected)


# НОВЫЙ КЛАСС: Рабочий для генерации SSCC в фоновом потоке
class SsccGeneratorWorker(QObject):
    finished = Signal(list)
    progress = Signal(int, str)
    error = Signal(str)

    def __init__(self, user_info, quantity):
        super().__init__()
        self.user_info = user_info
        self.quantity = quantity

    def run(self):
        generated_ssccs = []
        try:
            with get_client_db_connection(self.user_info) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # --- ИЗМЕНЕНИЕ: Резервируем ID одним запросом для производительности ---
                    # Вместо инкремента на каждой итерации, получаем диапазон ID сразу.
                    start_id, warning, gcp_for_sscc = read_and_increment_counter(cur, 'sscc_id', increment_by=self.quantity)
                    if warning:
                        # Отправляем предупреждение, если оно есть
                        self.error.emit(warning)

                    start_id = start_id - self.quantity # read_and_increment_counter возвращает ВЕРХНЮЮ границу

                    for i in range(self.quantity):
                        box_id = start_id + i + 1
                        # Генерация SSCC теперь происходит локально, без запросов к БД в цикле
                        _, full_sscc = generate_sscc(box_id, gcp_for_sscc)
                        generated_ssccs.append(full_sscc)

                    conn.commit() # Фиксируем изменения счетчика в БД
            self.finished.emit(generated_ssccs)
        except Exception as e:
            logging.error(f"Ошибка генерации SSCC: {e}\n{traceback.format_exc()}")
            self.error.emit(f"Ошибка генерации SSCC: {e}. Подробности в лог-файле.")

# --- НОВЫЙ БЛОК: Классы-заглушки для вкладок управления заказом ---
# Определяем их здесь, вне основного класса AdminWindowQt, чтобы не нарушать его структуру.
class OrderEditorFrameQt(QWidget):
    """Полнофункциональный фрейм для редактирования заказа."""
    def __init__(self, order_service, order_id, scenario_data, main_app_window, parent=None, is_archive=False, show_create_task_button=False):
        super().__init__(parent)
        self.order_service = order_service
        self.order_id = order_id
        self.scenario_data = scenario_data
        self.main_app_window = main_app_window
        self.is_archive = is_archive
        self.show_create_task_button = show_create_task_button

        self._create_widgets()
        self._load_details()

    def _create_widgets(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # --- ИЗМЕНЕНИЕ: Для архивных заказов оставляем только отчет и детализацию ---
        if self.is_archive:
            # --- Ряд 3 (только кнопка отчета) ---
            controls_frame_3 = QHBoxLayout()
            btn_download_report = QPushButton("Отчет декларанта")
            btn_download_report.clicked.connect(self._download_declarator_report)
            controls_frame_3.addWidget(btn_download_report)
            controls_frame_3.addStretch()
            main_layout.addLayout(controls_frame_3)

            # --- Таблица детализации ---
            self.details_table = QTableWidget()
            self.details_cols = ["id", "gtin", "dm_quantity", "aggregation_level", "production_date", "expiry_date"]
            self.details_table.setColumnCount(len(self.details_cols))
            self.details_table.setHorizontalHeaderLabels(["ID", "GTIN", "Кол-во", "Агрегация", "Дата произв.", "Годен до"])
            self.details_table.setColumnHidden(0, True) # Скрываем ID
            self.details_table.setEditTriggers(QAbstractItemView.NoEditTriggers) # Запрещаем редактирование
            main_layout.addWidget(self.details_table)

        else: # --- Старая логика для неархивных заказов ---
            # --- Ряд 1: Основные операции ---
            controls_frame_1 = QHBoxLayout()
            
            # --- ИЗМЕНЕНИЕ: Поле для комментария (номер контейнера) ---
            self.comment_label = QLabel("Комментарий (контейнер):")
            self.comment_edit = QLineEdit()
            
            btn_save = QPushButton("Сохранить")
            btn_save.clicked.connect(self._save_changes)

            controls_frame_1.addWidget(self.comment_label)
            controls_frame_1.addWidget(self.comment_edit, 1) # Растягиваем поле ввода
            controls_frame_1.addWidget(btn_save)
            
            main_layout.addLayout(controls_frame_1)

            # --- Ряд 2: Операции с товарами и Bartender ---
            controls_frame_2 = QHBoxLayout()
            btn_export_prod = QPushButton("Экспорт товаров")
            btn_export_prod.clicked.connect(self._export_products_to_excel)
            btn_import_prod = QPushButton("Импорт товаров")
            btn_import_prod.clicked.connect(self._import_products_from_excel)
            btn_create_view = QPushButton("Создать View")
            btn_create_view.clicked.connect(self._create_bartender_view)
            controls_frame_2.addWidget(btn_export_prod)
            controls_frame_2.addWidget(btn_import_prod)
            controls_frame_2.addWidget(btn_create_view)
            controls_frame_2.addStretch()
            main_layout.addLayout(controls_frame_2)

            # --- Ряд 3: Отчеты и интеграции ---
            controls_frame_3 = QHBoxLayout()
            btn_export_delta = QPushButton("Экспорт (Внешнее ПО)")
            btn_export_delta.clicked.connect(self._export_data_for_external_sw)
            btn_import_delta = QPushButton("Импорт (Внешнее ПО)")
            btn_import_delta.clicked.connect(self._import_data_for_external_sw)
            btn_download_report = QPushButton("Отчет декларанта")
            btn_download_report.clicked.connect(self._download_declarator_report)
            controls_frame_3.addWidget(btn_export_delta)
            controls_frame_3.addWidget(btn_import_delta)
            controls_frame_3.addWidget(btn_download_report)
            controls_frame_3.addStretch()
            main_layout.addLayout(controls_frame_3)

            # --- Таблица детализации ---
            self.details_table = QTableWidget()
            self.details_cols = ["id", "gtin", "dm_quantity", "aggregation_level", "production_date", "expiry_date"]
            self.details_table.setColumnCount(len(self.details_cols))
            self.details_table.setHorizontalHeaderLabels(["ID", "GTIN", "Кол-во", "Агрегация", "Дата произв.", "Годен до"])
            self.details_table.setColumnHidden(0, True) # Скрываем ID
            main_layout.addWidget(self.details_table)

            # --- Кнопки внизу ---
            bottom_buttons_layout = QHBoxLayout()
            
            # --- НОВЫЙ БЛОК: Кнопка создания задачи ---
            if self.show_create_task_button:
                btn_create_task = QPushButton("Создать задачу")
                btn_create_task.setStyleSheet("background-color: #90EE90;") # Light Green
                btn_create_task.clicked.connect(self._create_production_task)
                bottom_buttons_layout.addWidget(btn_create_task)

            bottom_buttons_layout.addStretch() # Этот разделитель отодвинет кнопку архивации вправо
            
            btn_archive = QPushButton("Перенести в архив")
            btn_archive.setStyleSheet("background-color: #FFB6C1;") # Light Pink
            btn_archive.clicked.connect(self._move_to_archive)
            bottom_buttons_layout.addWidget(btn_archive)
            
            main_layout.addLayout(bottom_buttons_layout)

    def _create_production_task(self):
        """
        Создает производственную задачу на основе текущего заказа, 
        если она еще не существует. В противном случае, просто переключается на нее.
        """
        # --- НОВЫЙ БЛОК: Проверка существования задачи ---
        existing_task = self.main_app_window.task_service.get_task_by_order_id(self.order_id)
        
        if existing_task:
            QMessageBox.information(self, "Задача уже существует", f"Задача для заказа #{self.order_id} уже существует. Переключаемся на нее.")
            # Переключаемся на страницу задач
            self.main_app_window.menu_tree.setCurrentItem(self.main_app_window.menu_items['tasks'])
            self.main_app_window._on_menu_clicked(self.main_app_window.menu_items['tasks'], 0)
            return
        # --- КОНЕЦ НОВОГО БЛОКА ---

        calculated_task_type = "unknown"
        if self.scenario_data.get('type') == 'Ручная агрегация':
            calculated_task_type = "manual_aggregation"
        elif self.scenario_data.get('post_processing') == 'Собственный алгоритм':
            calculated_task_type = "marking" # Or a more specific type if known

        try:
            # Вызываем метод сервиса через главное окно
            new_task_id = self.main_app_window.task_service.create_task(
                self.order_id,
                calculated_task_type,
                {} # Пустой JSON настроек
            )
            
            QMessageBox.information(self, "Успех", f"Задача #{new_task_id} успешно создана.")
            
            # Переключаемся на страницу задач
            self.main_app_window.menu_tree.setCurrentItem(self.main_app_window.menu_items['tasks'])
            self.main_app_window._on_menu_clicked(self.main_app_window.menu_items['tasks'], 0)

        except Exception as e:
            logging.error(f"Ошибка при создании задачи: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать задачу: {e}")

    def _load_details(self):
        self.details_table.setRowCount(0)
        try:
            # --- ИЗМЕНЕНИЕ: Загружаем не только детали, но и основную информацию о заказе ---
            if not self.is_archive:
                order_data = self.order_service.get_order_by_id(self.order_id)
                if order_data: # Добавляем проверку, что данные заказа получены
                    self.comment_edit.setText(order_data.get('notes', ''))

            details = self.order_service.get_order_details(self.order_id)
            for item in details:
                row = self.details_table.rowCount()
                self.details_table.insertRow(row)
                for col_idx, col_name in enumerate(self.details_cols):
                    value = item.get(col_name, '')
                    self.details_table.setItem(row, col_idx, QTableWidgetItem(str(value)))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить детали заказа: {e}")

    def _save_changes(self):
        # --- ИЗМЕНЕНИЕ: Собираем данные не только из таблицы, но и из поля комментария ---
        
        # 1. Собираем данные из таблицы детализации
        detail_updates = []
        for row in range(self.details_table.rowCount()):
            row_data = {}
            for col, key in enumerate(self.details_cols):
                item = self.details_table.item(row, col)
                row_data[key] = item.text() if item else None
            # Убедимся, что ID есть в данных, он нужен для UPDATE
            if 'id' not in row_data or not row_data['id']:
                 id_item = self.details_table.item(row, self.details_cols.index('id'))
                 if id_item:
                     row_data['id'] = id_item.text()
            detail_updates.append(row_data)
        
        # 2. Получаем комментарий
        comment_text = self.comment_edit.text()

        try:
            # 3. Вызываем обновленный сервисный метод для сохранения всего вместе
            self.order_service.save_order_changes(self.order_id, detail_updates, comment_text)
            QMessageBox.information(self, "Успех", "Изменения успешно сохранены.")
            
            # 4. Обновляем список заказов, чтобы отобразить новый комментарий
            if self.main_app_window:
                 self.main_app_window.load_orders(is_archive=self.is_archive)

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить изменения: {e}")

    def _export_details_to_excel(self):
        items_to_export = []
        for row in range(self.details_table.rowCount()):
            row_data = {}
            for col, key in enumerate(self.details_cols):
                item = self.details_table.item(row, col)
                row_data[key] = item.text() if item else ''
            items_to_export.append(row_data)
        
        if not items_to_export:
            QMessageBox.warning(self, "Внимание", "Нет данных для экспорта.")
            return

        df = pd.DataFrame(items_to_export)
        filepath, _ = QFileDialog.getSaveFileName(self, "Сохранить детализацию", f"order_{self.order_id}_details.xlsx", "Excel Files (*.xlsx)")
        if filepath:
            df.to_excel(filepath, index=False)
            QMessageBox.information(self, "Успех", f"Детализация выгружена в файл:\n{filepath}")

    def _import_details_from_excel(self):
        if QMessageBox.question(self, "Подтверждение", "Импорт из файла полностью заменит текущую детализацию. Продолжить?") != QMessageBox.Yes:
            return

        filepath, _ = QFileDialog.getOpenFileName(self, "Выберите Excel-файл", "", "Excel Files (*.xlsx *.xls)")
        if not filepath:
            return
        
        try:
            imported_count = self.order_service.import_details_from_excel(self.order_id, filepath)
            QMessageBox.information(self, "Успех", f"Детализация импортирована. Загружено {imported_count} строк.")
            self._load_details()
        except Exception as e:
            logging.error(f"Критическая ошибка при импорте детализации: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать данные: {e}")

    def _move_to_archive(self):
        if QMessageBox.question(self, "Подтверждение", f"Переместить заказ №{self.order_id} в архив?") != QMessageBox.Yes:
            return

        try:
            self.order_service.move_order_to_archive(self.order_id)
            
            if self.main_app_window:
                self.main_app_window.load_orders(is_archive=False)
                self.main_app_window.load_orders(is_archive=True)

            QMessageBox.information(self, "Успех", "Заказ успешно перемещен в архив.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось архивировать заказ: {e}")

    # --- Заглушки для остального функционала ---
    def _export_products_to_excel(self):
        """Выгружает в Excel данные о товарах, связанных с текущим заказом."""
        try:
            products_data = self.order_service.get_products_for_order(self.order_id)
            if not products_data:
                QMessageBox.warning(self, "Внимание", "Не найдено товаров в заказе для экспорта.")
                return

            df = pd.DataFrame(products_data)
            filepath, _ = QFileDialog.getSaveFileName(self, "Сохранить товары", f"order_{self.order_id}_products.xlsx", "Excel Files (*.xlsx)")
            if filepath:
                df.to_excel(filepath, index=False)
                QMessageBox.information(self, "Успех", f"Товары заказа успешно выгружены в файл:\n{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось экспортировать товары: {e}")

    def _import_products_from_excel(self):
        """Импортирует (обновляет) данные о товарах из Excel-файла в общий справочник."""
        if QMessageBox.question(self, "Подтверждение", "Данные из файла обновят записи в общем справочнике товаров. Продолжить?") != QMessageBox.Yes:
            return

        filepath, _ = QFileDialog.getOpenFileName(self, "Выберите Excel-файл для импорта товаров", "", "Excel Files (*.xlsx *.xls)")
        if not filepath:
            return

        try:
            count = self.order_service.import_products_from_excel(filepath)
            QMessageBox.information(self, "Успех", f"Справочник товаров успешно обновлен. Обработано {count} строк.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать товары: {e}")

    def _create_bartender_view(self):
        """Создает/обновляет представления для Bartender."""
        progress = QProgressDialog("Выполняется импорт и создание представлений...", "Отмена", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setValue(10)
        
        try:
            # Сервисный метод теперь инкапсулирует оба шага
            result = self.order_service.create_bartender_views_for_order(self.order_id)
            progress.setValue(100)

            if result.get('success'):
                QMessageBox.information(self, "Успех", result.get('message', 'Представления успешно созданы/обновлены.'))
            else:
                QMessageBox.critical(self, "Ошибка", result.get('message', 'Произошла неизвестная ошибка.'))
        except Exception as e:
            progress.setValue(100)
            QMessageBox.critical(self, "Критическая ошибка", f"Не удалось создать представления: {e}")

    def _export_data_for_external_sw(self):
        """Выгружает данные в формате 'Дельта' для внешнего ПО."""
        try:
            df, report_name = self.order_service.export_data_for_external_sw(self.order_id)

            if df is None:
                QMessageBox.warning(self, "Нет данных", "В заказе нет скачанных кодов для выгрузки.")
                return
            
            initial_filename = f"{report_name}_order_{self.order_id}.csv"
            filepath, _ = QFileDialog.getSaveFileName(self, "Сохранить файл для Внешнего ПО", initial_filename, "CSV Files (*.csv)")
            if not filepath: 
                return

            import csv
            df.to_csv(filepath, sep='\t', index=False, encoding='utf-8', lineterminator='\r\n', quoting=csv.QUOTE_NONE)
            
            QMessageBox.information(self, "Успех", f"Данные успешно выгружены в файл:\n{filepath}\n\nСтатус заказа обновлен на 'delta'.")
            self.main_app_window.load_orders(is_archive=False) # Обновляем UI
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось экспортировать данные: {e}")

    def _import_data_for_external_sw(self):
        """Обрабатывает CSV-файл от 'Дельта'."""
        filepath, _ = QFileDialog.getOpenFileName(self, "Выберите CSV-файл от 'Дельта'", filter="CSV files (*.csv)")
        if not filepath:
            return

        expected_filename_part = f"order_{self.order_id}.csv"
        if expected_filename_part not in os.path.basename(filepath):
            QMessageBox.critical(self, "Ошибка", f'Имя файла должно содержать "{expected_filename_part}".')
            return

        progress_dialog = QProgressDialog("Выполняется импорт данных...", "Отмена", 0, 100, self)
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.show()

        try:
            self.order_service.import_data_from_external_sw(self.order_id, filepath)
            progress_dialog.setValue(100)
            QMessageBox.information(self, "Успех", "Данные из CSV-файла 'Дельта' успешно импортированы и обработаны.")
        except Exception as e:
            logging.error(f"Ошибка при импорте данных 'Дельта' для заказа {self.order_id}: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать данные: {e}")
        finally:
            progress_dialog.hide()

    def _download_declarator_report(self):
        """Формирует и выгружает отчет для декларанта."""
        try:
            df, report_name = self.order_service.get_declarator_report_data(self.order_id)
            
            if df is None:
                QMessageBox.warning(self, "Нет данных", "Не найдено данных для формирования отчета.")
                return

            filepath, _ = QFileDialog.getSaveFileName(self, "Сохранить отчет декларанта", f"{report_name}_order_{self.order_id}.xlsx", "Excel Files (*.xlsx)")
            if filepath:
                df.to_excel(filepath, index=False)
                QMessageBox.information(self, "Успех", f"Отчет декларанта успешно сохранен в файл:\n{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сформировать отчет: {e}")


# --- НОВЫЙ БЛОК: Фрейм для редактирования задачи ---
class TaskEditorFrameQt(QWidget):
    """Фрейм для просмотра и редактирования производственной задачи."""
    def __init__(self, task_service, task_data, main_app_window, parent=None):
        super().__init__(parent)
        self.task_service = task_service
        self.task_data = task_data
        self.main_app_window = main_app_window

        logging.debug(f"TaskEditorFrameQt.__init__: Received task_data: {self.task_data}") # DEBUG LOG

        self._create_widgets()
        self._load_task_details()

    def _create_widgets(self):
        main_layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # Поля ID, Заказ, Тип, Дата создания удалены, т.к. они есть в таблице
        
        self.status_combo = QComboBox()
        self.status_combo.addItems(['new', 'in_progress', 'completed', 'error'])
        current_status = self.task_data.get('status')
        if current_status in ['new', 'in_progress', 'completed', 'error']: # Check if status is valid before setting
            self.status_combo.setCurrentText(current_status)
        else: # Set to default if status is unexpected
            self.status_combo.setCurrentIndex(0) # 'new'

        form_layout.addRow("Статус:", self.status_combo)

        main_layout.addLayout(form_layout)

        # --- NEW MARKING SETTINGS ---
        if self.task_data.get('type') == 'marking':
            self.marking_settings_group = QGroupBox("Параметры маркировки")
            marking_layout = QFormLayout(self.marking_settings_group)

            # Aggregation Type
            self.aggregation_type_combo = QComboBox()
            self.aggregation_type_combo.addItems(["Без агрегации", "Отрывающий", "Закрывающий"])
            marking_layout.addRow("Тип агрегации:", self.aggregation_type_combo)

            # Employee Count
            self.employee_count_spinbox = QSpinBox()
            self.employee_count_spinbox.setRange(1, 100)
            self.employee_count_spinbox.setValue(3) # Default
            
            # --- NEW: Generate Passes Button ---
            self.btn_generate_passes = QPushButton("Сгенерировать пропуски")
            self.btn_generate_passes.clicked.connect(self._generate_employee_passes)
            
            employee_layout = QHBoxLayout()
            employee_layout.addWidget(self.employee_count_spinbox)
            employee_layout.addWidget(self.btn_generate_passes)
            marking_layout.addRow("Количество сотрудников:", employee_layout)
            # --- END NEW ---

            # --- NEW FIELD: Nesting Level ---
            self.nesting_level_label = QLabel("Уровень вложений:")
            self.nesting_level_spinbox = QSpinBox()
            self.nesting_level_spinbox.setRange(1, 10)
            self.nesting_level_spinbox.setValue(1) # Default
            marking_layout.addRow(self.nesting_level_label, self.nesting_level_spinbox)
            # --- END NEW FIELD ---

            # SSCC Source
            self.sscc_source_label = QLabel("Способ получения SSCC:")
            self.sscc_source_combo = QComboBox()
            self.sscc_source_combo.addItems(["Генерируем сами", "Предоставляет клиент"])
            marking_layout.addRow(self.sscc_source_label, self.sscc_source_combo)

            main_layout.addWidget(self.marking_settings_group)

            # Connect signal for dynamic visibility
            self.aggregation_type_combo.currentTextChanged.connect(self._on_aggregation_type_changed)
        else:
            self.marking_settings_group = None
        # --- END NEW MARKING SETTINGS ---

        # Редактор JSON
        main_layout.addWidget(QLabel("Параметры (settings_json):"))
        self.settings_json_edit = QTextEdit()
        self.settings_json_edit.setAcceptRichText(False)
        main_layout.addWidget(self.settings_json_edit)

        # Кнопки управления
        buttons_layout = QHBoxLayout()
        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._save_changes)
        
        # --- ИЗМЕНЕНИЕ: Добавляем кнопки управления статусом ---
        self.btn_take_in_work = QPushButton("Взять в работу")
        self.btn_take_in_work.clicked.connect(lambda: self._update_status('in_progress'))
        
        self.btn_complete = QPushButton("Завершить")
        self.btn_complete.clicked.connect(lambda: self._update_status('completed'))

        buttons_layout.addWidget(btn_save)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.btn_take_in_work)
        buttons_layout.addWidget(self.btn_complete)

        main_layout.addLayout(buttons_layout)

    def _on_aggregation_type_changed(self, text):
        if self.marking_settings_group:
            is_aggregation_enabled = (text != "Без агрегации")
            # SSCC widgets
            self.sscc_source_label.setVisible(is_aggregation_enabled)
            self.sscc_source_combo.setVisible(is_aggregation_enabled)
            # Nesting level widgets
            self.nesting_level_label.setVisible(is_aggregation_enabled)
            self.nesting_level_spinbox.setVisible(is_aggregation_enabled)

    def _load_task_details(self):
        """Загружает детали задачи в виджеты."""
        settings_json = self.task_data.get('settings_json', {})
        if isinstance(settings_json, str):
            try:
                settings_json = json.loads(settings_json)
            except json.JSONDecodeError:
                settings_json = {}
        
        self.settings_json_edit.setText(json.dumps(settings_json, indent=4, ensure_ascii=False))

        # --- NEW: Load marking settings ---
        if self.marking_settings_group:
            self.aggregation_type_combo.setCurrentText(settings_json.get('aggregation_type', 'Без агрегации'))
            self.employee_count_spinbox.setValue(settings_json.get('employee_count', 3))
            self.nesting_level_spinbox.setValue(settings_json.get('nesting_level', 1))
            self.sscc_source_combo.setCurrentText(settings_json.get('sscc_source', 'Генерируем сами'))
            # Trigger initial visibility update
            self._on_aggregation_type_changed(self.aggregation_type_combo.currentText())
        # --- END NEW ---
        
        # Обновляем состояние кнопок в зависимости от статуса
        status = self.task_data.get('status')
        self.btn_take_in_work.setEnabled(status == 'new')
        self.btn_complete.setEnabled(status == 'in_progress')


    def _save_changes(self):
        """Сохраняет изменения статуса и JSON-настроек."""
        task_id = self.task_data['id']
        
        # 1. Сохранение статуса
        new_status = self.status_combo.currentText()
        if new_status != self.task_data.get('status'):
            try:
                self.task_service.update_task_status(task_id, new_status)
                self.task_data['status'] = new_status # Обновляем локальные данные
                QMessageBox.information(self, "Успех", "Статус задачи обновлен.")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось обновить статус: {e}")
                return

        # 2. Сохранение JSON
        try:
            settings_text = self.settings_json_edit.toPlainText()
            settings_data = json.loads(settings_text) if settings_text else {}

            # --- NEW: Update settings from widgets if they exist ---
            if self.marking_settings_group:
                settings_data['aggregation_type'] = self.aggregation_type_combo.currentText()
                settings_data['employee_count'] = self.employee_count_spinbox.value()
                if self.aggregation_type_combo.currentText() != 'Без агрегации':
                    settings_data['nesting_level'] = self.nesting_level_spinbox.value()
                    settings_data['sscc_source'] = self.sscc_source_combo.currentText()
                else:
                    # Clean up keys that are not applicable
                    if 'nesting_level' in settings_data:
                        del settings_data['nesting_level']
                    if 'sscc_source' in settings_data:
                        del settings_data['sscc_source']
            # --- END NEW ---

            self.task_service.update_task_settings(task_id, settings_data)
            QMessageBox.information(self, "Успех", "Настройки задачи сохранены.")
        except json.JSONDecodeError:
            QMessageBox.critical(self, "Ошибка", "Некорректный формат JSON в настройках.")
            return
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить настройки: {e}")

        # Обновляем всю страницу задач
        self.main_app_window.load_tasks()
        # Перезагружаем детали в панели
        self._load_task_details()


    def _update_status(self, new_status):
        """Обработчик для кнопок быстрой смены статуса."""
        task_id = self.task_data['id']
        try:
            self.task_service.update_task_status(task_id, new_status)
            self.task_data['status'] = new_status
            self.status_combo.setCurrentText(new_status)
            QMessageBox.information(self, "Успех", f"Статус задачи обновлен на '{new_status}'.")
            self.main_app_window.load_tasks()
            self._load_task_details() # Обновляем состояние кнопок
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось обновить статус: {e}")

    def _generate_employee_passes(self):
        """Генерирует уникальные коды доступа для сотрудников."""
        try:
            task_id = self.task_data['id']
            employee_count = self.employee_count_spinbox.value()

            # Вызываем сервисный метод
            generated_codes = self.task_service.generate_employee_passes(task_id, employee_count)
            
            QMessageBox.information(self, "Успех", 
                                    f"Успешно сгенерировано {len(generated_codes)} пропусков для задачи #{task_id}.")
            
            # Открываем окно просмотра
            dialog = EmployeePassesViewerDialog(self, self.task_service, task_id)
            dialog.exec()
            
        except Exception as e:
            logging.error(f"Ошибка при генерации пропусков: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка", f"Не удалось сгенерировать пропуски: {e}")


class ApiIntegrationFrameQt(QWidget):
    """Полнофункциональный фрейм для интеграции с API ДМ.Код."""
    def __init__(self, api_service, order_id, post_processing_mode, main_app_window, parent=None):
        super().__init__(parent)
        self.api_service = api_service
        self.order_id = order_id
        self.post_processing_mode = post_processing_mode
        self.main_app_window = main_app_window
        self.order_data = None

        self._load_order_data()
        self._create_widgets()
        self._update_buttons_state()

    def _load_order_data(self):
        """Загружает данные заказа для определения состояния кнопок."""
        try:
            # Используем order_service, который находится внутри api_service
            self.order_data = self.api_service.order_service.get_order_by_id(self.order_id)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить данные заказа: {e}")
            self.deleteLater()

    def _create_widgets(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # Панель с основными кнопками циклов
        buttons_layout = QHBoxLayout()
        self.request_codes_btn = QPushButton("1. Запросить коды")
        self.request_codes_btn.clicked.connect(self._request_codes_flow)
        
        self.get_codes_btn = QPushButton("2. Получить коды")
        self.get_codes_btn.clicked.connect(self._get_codes_flow)

        self.prepare_report_data_btn = QPushButton("3. Подготовить сведения")
        self.prepare_report_data_btn.clicked.connect(self._prepare_report_data_flow)

        self.prepare_report_btn = QPushButton("4. Подготовить отчет")
        self.prepare_report_btn.clicked.connect(self._prepare_report_flow)
        
        buttons_layout.addWidget(self.request_codes_btn)
        buttons_layout.addWidget(self.get_codes_btn)
        buttons_layout.addWidget(self.prepare_report_data_btn)
        buttons_layout.addWidget(self.prepare_report_btn)
        buttons_layout.addStretch()
        main_layout.addLayout(buttons_layout)

        # Поле для вывода логов
        self.response_text = QTextEdit()
        self.response_text.setReadOnly(True)
        self.response_text.setLineWrapMode(QTextEdit.NoWrap)
        main_layout.addWidget(self.response_text)

    def _update_buttons_state(self):
        """Обновляет состояние кнопок в зависимости от статуса заказа."""
        if not self.order_data: return

        api_status = self.order_data.get('api_status')

        # Сначала деактивируем все
        self.request_codes_btn.setEnabled(False)
        self.get_codes_btn.setEnabled(False)
        self.prepare_report_data_btn.setEnabled(False)
        self.prepare_report_btn.setEnabled(False)
        
        # Активируем нужные в зависимости от статуса
        if not api_status:
            self.request_codes_btn.setEnabled(True)
        elif api_status == 'Запрос создан':
            self.get_codes_btn.setEnabled(True)
        elif api_status == 'Коды скачаны':
            self.prepare_report_data_btn.setEnabled(True)
        elif api_status == 'Сведения подготовлены':
            # Обе кнопки активны, т.к. пользователь может хотеть пере-подготовить сведения
            self.prepare_report_data_btn.setEnabled(True)
            self.prepare_report_btn.setEnabled(True)
        elif api_status == 'Отчет подготовлен':
            self._display_api_response("Завершено", "Работа с заказом в АПИ полностью завершена.")
        else: # Для промежуточных статусов ('Тиражи созданы', 'JSON заказан')
            self.get_codes_btn.setEnabled(True) # Позволяем перезапустить весь цикл получения кодов

    def _display_api_response(self, title, body):
        self.response_text.setPlainText(f"--- {title} ---\n\n{body}")

    def _append_log(self, message):
        self.response_text.append(message)
        QApplication.processEvents() # Обновляем UI для отображения лога

    def _run_in_thread(self, target_func, *args):
        """Запускает функцию в отдельном потоке, чтобы не блокировать UI."""
        class Worker(QObject):
            finished = Signal(object, object) # (результат, ошибка)

            def __init__(self, func, *func_args):
                super().__init__()
                self.func = func
                self.func_args = func_args

            def run(self):
                try:
                    result = self.func(*self.func_args)
                    self.finished.emit(result, None)
                except Exception as e:
                    self.finished.emit(None, e)

        self.thread = QThread()
        self.worker = Worker(target_func, *args)
        self.worker.moveToThread(self.thread)

        self.worker.finished.connect(self._on_task_finished)
        self.thread.started.connect(self.worker.run)
        self.thread.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()

    def _on_task_finished(self, result, error):
        """Обрабатывает результат выполнения фоновой задачи."""
        if error:
            logging.error("Ошибка в фоновой задаче API", exc_info=error)
            QMessageBox.critical(self, "Ошибка выполнения", str(error))
        elif result and isinstance(result, str):
             QMessageBox.information(self, "Результат операции", result)

        self._load_order_data()
        self._update_buttons_state() # Перезагружаем данные и обновляем кнопки
        if not error:
            self._append_log("\nОперация успешно завершена.")

    def _request_codes_flow(self):
        """Запускает полный цикл запроса кодов."""
        self._display_api_response("1. Запрос кодов", "Запуск операции...")
        self._run_in_thread(
            self.api_service.request_codes_full_cycle, 
            self.order_id, 
            self._append_log
        )
    
    def _get_codes_flow(self):
        """Запускает полный цикл получения кодов."""
        self._display_api_response("2. Получение кодов", "Запуск операции...")
        self._run_in_thread(
            self.api_service.get_codes_full_cycle,
            self.order_id,
            self.post_processing_mode,
            self._append_log
        )
    
    def _prepare_report_data_flow(self):
        QMessageBox.warning(self, "В разработке", "Цикл 'Подготовить сведения' еще не реализован.")

    def _prepare_report_flow(self):
        QMessageBox.warning(self, "В разработке", "Цикл 'Подготовить отчет' еще не реализован.")


class CodeUploadFrameQt(QWidget):
    """Полнофункциональный фрейм для загрузки кодов из файла."""
    def __init__(self, user_info, order_id, main_app_window, parent=None):
        super().__init__(parent)
        self.user_info = user_info
        self.order_id = order_id
        self.main_app_window = main_app_window
        self._create_widgets()

    def _create_widgets(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)

        main_layout.addWidget(QLabel("Загрузите файлы с кодами маркировки (csv, txt):"))

        # Поле для выбора файлов
        file_layout = QHBoxLayout()
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setReadOnly(True)
        btn_browse = QPushButton("Обзор...")
        btn_browse.clicked.connect(self._select_files)
        file_layout.addWidget(self.file_path_edit)
        file_layout.addWidget(btn_browse)
        main_layout.addLayout(file_layout)

        # Тип кодов
        dm_type_layout = QHBoxLayout()
        dm_type_layout.addWidget(QLabel("Тип кодов DataMatrix:"))
        self.dm_type_combo = QComboBox()
        self.dm_type_combo.addItems(["standard", "tobacco"])
        dm_type_layout.addWidget(self.dm_type_combo)
        dm_type_layout.addStretch()
        main_layout.addLayout(dm_type_layout)

        # Настройки агрегации
        agg_group = QGroupBox("Настройки агрегации")
        agg_layout = QVBoxLayout()
        self.agg_none_radio = QRadioButton("Без агрегации")
        self.agg_none_radio.setChecked(True)
        self.agg_level1_radio = QRadioButton("Агрегация в короба:")
        self.level1_qty_spinbox = QSpinBox()
        self.level1_qty_spinbox.setRange(1, 10000)
        self.level1_qty_spinbox.setValue(10)
        
        level1_layout = QHBoxLayout()
        level1_layout.addWidget(self.agg_level1_radio)
        level1_layout.addWidget(self.level1_qty_spinbox)
        level1_layout.addStretch()

        agg_layout.addWidget(self.agg_none_radio)
        agg_layout.addLayout(level1_layout)
        agg_group.setLayout(agg_layout)
        main_layout.addWidget(agg_group)

        # Кнопка запуска
        btn_run = QPushButton("Запустить обработку")
        btn_run.clicked.connect(self._run_processing)
        main_layout.addWidget(btn_run)
        main_layout.addStretch()

    def _select_files(self):
        filepaths, _ = QFileDialog.getOpenFileNames(self, "Выберите файлы с кодами", "", "Текстовые файлы (*.txt *.csv);;Все файлы (*.*)")
        if filepaths:
            self.file_path_edit.setText(";".join(filepaths))

    def _run_processing(self):
        filepaths = self.file_path_edit.text().split(";")
        if not all(filepaths):
            QMessageBox.warning(self, "Внимание", "Не выбраны файлы для загрузки.")
            return

        dm_type = self.dm_type_combo.currentText()
        aggregation_mode = "level1" if self.agg_level1_radio.isChecked() else "none"
        level1_qty = self.level1_qty_spinbox.value()

        # Создаем диалог для логов
        log_dialog = QDialog(self)
        log_dialog.setWindowTitle(f"Лог обработки заказа №{self.order_id}")
        log_dialog.setMinimumSize(600, 400)
        log_layout = QVBoxLayout()
        log_text = QTextEdit()
        log_text.setReadOnly(True)
        log_layout.addWidget(log_text)
        log_dialog.setLayout(log_layout)

        # Worker для выполнения задачи в фоне
        class Worker(QObject):
            finished = Signal()
            log_message = Signal(str)

            def __init__(self, user_info, order_id):
                super().__init__()
                self.user_info = user_info
                self.order_id = order_id

            def run(self):
                logs = run_aggregation_process_desktop(
                    user_info=self.user_info, order_id=self.order_id, filepaths=filepaths,
                    dm_type=dm_type, aggregation_mode=aggregation_mode, level1_qty=level1_qty
                )
                for line in logs:
                    self.log_message.emit(line)
                self.finished.emit()

        self.thread = QThread()
        self.worker = Worker(self.user_info, self.order_id)
        self.worker.moveToThread(self.thread)
        self.worker.log_message.connect(log_text.append)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.started.connect(self.worker.run)
        
        self.thread.start()
        log_dialog.exec() # Показываем модальное окно с логами
        self.main_app_window.load_orders(is_archive=False) # Обновляем список заказов


class ScenarioEditorDialog(QDialog):
    """Специализированный диалог для редактирования сценария, перенесенный из Tkinter."""
    def __init__(self, parent, item_data=None):
        super().__init__(parent)
        self.setWindowTitle("Редактор сценария маркировки")
        self.setMinimumWidth(500)
        self.result = None
        
        # Глубокое копирование, чтобы избежать изменения исходных данных до сохранения
        self.item_data = copy.deepcopy(item_data) if item_data else {}
        self.scenario_data = self.item_data.get('scenario_data', {})
        self.widgets = {} # Словарь для хранения виджетов

        self._build_ui()
        self._on_type_change() # Настраиваем видимость при инициализации
        self._on_options_change() # И еще раз для вложенных опций

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        # 1. Название сценария
        form_layout = QFormLayout()
        self.name_edit = QLineEdit(self.item_data.get('name', ''))
        form_layout.addRow("Название сценария:", self.name_edit)

        # 2. Тип сценария
        self.type_combo = QComboBox()
        self.type_combo.addItems(['Маркировка', 'Ручная агрегация'])
        self.type_combo.setCurrentText(self.scenario_data.get('type', 'Маркировка'))
        self.type_combo.currentTextChanged.connect(self._on_type_change)
        form_layout.addRow("Тип сценария:", self.type_combo)
        
        main_layout.addLayout(form_layout)

        # 3. Контейнеры для опций
        self.marking_frame = QGroupBox("Опции маркировки")
        self.aggregation_frame = QGroupBox("Опции ручной агрегации")

        self._create_marking_widgets(self.marking_frame)
        self._create_manual_aggregation_widgets(self.aggregation_frame)
        
        main_layout.addWidget(self.marking_frame)
        main_layout.addWidget(self.aggregation_frame)
        
        # Кнопки
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def _create_marking_widgets(self, parent):
        layout = QFormLayout(parent)
        
        # Источник кодов ДМ
        self.widgets['dm_source'] = QComboBox()
        self.widgets['dm_source'].addItems(['Заказ в ДМ.Код', 'Файлы клиента (csv, txt)', 'Внешняя система (1С)', 'Без кодов ДМ'])
        self.widgets['dm_source'].setCurrentText(self.scenario_data.get('dm_source', 'Заказ в ДМ.Код'))
        layout.addRow("Источник кодов ДМ:", self.widgets['dm_source'])

        # Нужна агрегация
        self.widgets['aggregation_needed'] = QCheckBox()
        self.widgets['aggregation_needed'].setChecked(self.scenario_data.get('aggregation_needed', False))
        self.widgets['aggregation_needed'].stateChanged.connect(self._on_options_change)
        layout.addRow("Нужна агрегация:", self.widgets['aggregation_needed'])

        # Источник кодов SSCC
        self.widgets['sscc_source_label'] = QLabel("Источник кодов SSCC:")
        self.widgets['sscc_source'] = QComboBox()
        self.widgets['sscc_source'].addItems(['Генерировать самостоятельно', 'Предоставит клиент'])
        self.widgets['sscc_source'].setCurrentText(self.scenario_data.get('sscc_source', 'Генерировать самостоятельно'))
        layout.addRow(self.widgets['sscc_source_label'], self.widgets['sscc_source'])

        # Постобработка
        self.widgets['post_processing'] = QComboBox()
        self.widgets['post_processing'].addItems(['Печать через Bartender', 'Внешнее ПО', 'Собственный алгоритм'])
        self.widgets['post_processing'].setCurrentText(self.scenario_data.get('post_processing', 'Печать через Bartender'))
        self.widgets['post_processing'].currentTextChanged.connect(self._on_options_change)
        layout.addRow("Постобработка:", self.widgets['post_processing'])

        # Дополнительные опции для "Собственный алгоритм"
        self.custom_algo_frame = QWidget()
        custom_algo_layout = QVBoxLayout(self.custom_algo_frame)
        custom_algo_layout.setContentsMargins(0,0,0,0)
        self.widgets['clarify_prod_date'] = QCheckBox("Уточнить дату производства")
        self.widgets['clarify_prod_date'].setChecked(self.scenario_data.get('clarify_prod_date', False))
        self.widgets['clarify_prod_country'] = QCheckBox("Уточнить страну производства")
        self.widgets['clarify_prod_country'].setChecked(self.scenario_data.get('clarify_prod_country', False))
        custom_algo_layout.addWidget(self.widgets['clarify_prod_date'])
        custom_algo_layout.addWidget(self.widgets['clarify_prod_country'])
        layout.addRow(self.custom_algo_frame)

    def _create_manual_aggregation_widgets(self, parent):
        layout = QFormLayout(parent)
        
        # Варианты агрегации
        self.widgets['manual_agg_variant'] = QComboBox()
        self.widgets['manual_agg_variant'].addItems(['Агрегация в набор', 'Агрегация в короб', 'Агрегация в набор а затем в короб'])
        self.widgets['manual_agg_variant'].setCurrentText(self.scenario_data.get('manual_agg_variant', 'Агрегация в набор'))
        self.widgets['manual_agg_variant'].currentTextChanged.connect(self._on_options_change)
        layout.addRow("Варианты агрегации:", self.widgets['manual_agg_variant'])
        
        # Дополнительные опции
        self.manual_agg_options_frame = QWidget()
        manual_options_layout = QVBoxLayout(self.manual_agg_options_frame)
        manual_options_layout.setContentsMargins(0,0,0,0)
        self.widgets['manual_clarify_prod_date'] = QCheckBox("Уточнить дату производства")
        self.widgets['manual_clarify_prod_date'].setChecked(self.scenario_data.get('clarify_prod_date', False))
        self.widgets['manual_clarify_prod_country'] = QCheckBox("Уточнить страну производства")
        self.widgets['manual_clarify_prod_country'].setChecked(self.scenario_data.get('clarify_prod_country', False))
        manual_options_layout.addWidget(self.widgets['manual_clarify_prod_date'])
        manual_options_layout.addWidget(self.widgets['manual_clarify_prod_country'])
        layout.addRow(self.manual_agg_options_frame)

    def _on_type_change(self):
        """Показывает/скрывает фреймы в зависимости от типа сценария."""
        selected_type = self.type_combo.currentText()
        is_marking = (selected_type == 'Маркировка')
        self.marking_frame.setVisible(is_marking)
        self.aggregation_frame.setVisible(not is_marking)

    def _on_options_change(self):
        """Показывает/скрывает доп. опции в зависимости от выбора."""
        # Для вкладки "Маркировка"
        show_sscc = self.widgets['aggregation_needed'].isChecked()
        self.widgets['sscc_source_label'].setVisible(show_sscc)
        self.widgets['sscc_source'].setVisible(show_sscc)
        
        show_custom_algo = (self.widgets['post_processing'].currentText() == 'Собственный алгоритм')
        self.custom_algo_frame.setVisible(show_custom_algo)

        # Для вкладки "Ручная агрегация"
        show_manual_options = (self.widgets['manual_agg_variant'].currentText() == 'Агрегация в набор а затем в короб')
        self.manual_agg_options_frame.setVisible(show_manual_options)

    def accept(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Внимание", "Название сценария не может быть пустым.")
            return

        scenario_data = {'type': self.type_combo.currentText()}

        if scenario_data['type'] == 'Маркировка':
            scenario_data['dm_source'] = self.widgets['dm_source'].currentText()
            scenario_data['aggregation_needed'] = self.widgets['aggregation_needed'].isChecked()
            if scenario_data['aggregation_needed']:
                scenario_data['sscc_source'] = self.widgets['sscc_source'].currentText()
            scenario_data['post_processing'] = self.widgets['post_processing'].currentText()
            scenario_data['clarify_prod_date'] = self.widgets['clarify_prod_date'].isChecked()
            scenario_data['clarify_prod_country'] = self.widgets['clarify_prod_country'].isChecked()

        elif scenario_data['type'] == 'Ручная агрегация':
            scenario_data['manual_agg_variant'] = self.widgets['manual_agg_variant'].currentText()
            scenario_data['clarify_prod_date'] = self.widgets['manual_clarify_prod_date'].isChecked()
            scenario_data['clarify_prod_country'] = self.widgets['manual_clarify_prod_country'].isChecked()

        self.result = {
            'id': self.item_data.get('id'),
            'name': name,
            'scenario_data': scenario_data
        }
        super().accept()


# --- Новые классы для редактора макетов ---
class PrintableObjectItem(QGraphicsRectItem):
    """Кастомный элемент на сцене, представляющий объект на этикетке."""
    def __init__(self, obj_data, object_id, scale):
        self.obj_data = obj_data
        self.object_id = object_id
        self.scale = scale

        x = obj_data.get('x_mm', 0) * scale
        y = obj_data.get('y_mm', 0) * scale
        w = obj_data.get('width_mm', 10) * scale
        h = obj_data.get('height_mm', 10) * scale
        super().__init__(x, y, w, h)

        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        
        # Визуальное оформление
        obj_type = obj_data.get('type')
        if obj_type == 'text':
            self.setBrush(QColor("#fff8dc")) # Cornsilk
        elif obj_type == 'barcode':
            self.setBrush(QColor("#add8e6")) # LightBlue
        elif obj_type == 'image':
            self.setBrush(QColor("#d0f0c0")) # TeaGreen
        elif obj_type == 'text_with_image':
            self.setBrush(QColor("#E6E6FA")) # Lavender
        else:
            self.setBrush(QColor("#f0f0f0"))

        self.setPen(QPen(QColor("gray"), 1))

        # Добавляем текст внутрь
        self.text_item = QGraphicsTextItem(self._get_display_text(), self)
        self.text_item.setDefaultTextColor(QColor("black"))
        # Центрируем текст
        br = self.text_item.boundingRect()
        self.text_item.setPos(x + (w - br.width()) / 2, y + (h - br.height()) / 2)

    def _get_display_text(self):
        obj_type = self.obj_data.get('type')
        if obj_type == 'barcode':
            return self.obj_data.get('barcode_type', 'BARCODE')
        elif obj_type == 'text':
            if self.obj_data.get('is_custom_text'):
                # Не показываем длинный текст в предпросмотре
                return "'...' (свой текст)" if self.obj_data.get('data_source') else "Свой текст"
            return self.obj_data.get('data_source', 'text')
        elif obj_type == 'image':
            return 'IMG'
        elif obj_type == 'text_with_image':
            return 'Текст+IMG'
        return obj_type or "object"
    
    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            # Обновляем координаты в исходном словаре данных
            new_x = round(self.pos().x() / self.scale, 2)
            new_y = round(self.pos().y() / self.scale, 2)

            # --- ИСПРАВЛЕНИЕ: Проверяем, изменились ли координаты, чтобы избежать рекурсии ---
            if self.obj_data.get('x_mm') != new_x or self.obj_data.get('y_mm') != new_y:
                self.obj_data['x_mm'] = new_x
                self.obj_data['y_mm'] = new_y
                # Сообщаем родительскому диалогу об изменении
                if self.scene() and hasattr(self.scene().parent(), 'on_item_moved'):
                    self.scene().parent().on_item_moved(self.object_id)
        return super().itemChange(change, value)


class LabelEditorDialog(QDialog):
    """Диалоговое окно для визуального редактора макетов этикеток."""
    def __init__(self, parent, user_info, catalog_service, layout_data=None):
        super().__init__(parent)
        self.user_info = user_info
        self.catalog_service = catalog_service
        self.is_new_layout = not bool(layout_data)
        self.template = json.loads(json.dumps(layout_data or {})) # Глубокая копия
        title = "Новый макет" if self.is_new_layout else f"Редактор: {self.template.get('name', '')}"
        self.setWindowTitle(title)
        self.setMinimumSize(1200, 800)
        self.canvas_scale = 5
        self.selected_object_id = None
        # --- НОВЫЙ БЛОК: Шаблоны и источники данных ---
        self.object_templates = {
            'text': { "type": "text", "x_mm": 10, "y_mm": 10, "width_mm": 40, "height_mm": 15, "data_source": "ap_workplaces.warehouse_name", "font_name": "arial" },
            'custom_text': { "type": "text", "is_custom_text": True, "x_mm": 10, "y_mm": 10, "width_mm": 40, "height_mm": 15, "data_source": "", "font_name": "arial" },
            'qr': { "type": "barcode", "barcode_type": "QR", "x_mm": 10, "y_mm": 10, "width_mm": 30, "height_mm": 30, "data_source": "QR: Конфигурация рабочего места" },
            'sscc': { "type": "barcode", "barcode_type": "SSCC", "x_mm": 10, "y_mm": 10, "width_mm": 50, "height_mm": 20, "data_source": "packages.sscc_code" },
            'datamatrix': { "type": "barcode", "barcode_type": "DataMatrix", "x_mm": 10, "y_mm": 10, "width_mm": 30, "height_mm": 30, "data_source": "items.datamatrix" },
            'image': { "type": "image", "x_mm": 10, "y_mm": 10, "width_mm": 30, "height_mm": 30, "data_source": "" },
            'text_with_image': { "type": "text_with_image", "is_custom_text": True, "x_mm": 10, "y_mm": 10, "width_mm": 60, "height_mm": 30, "data_source": "", "image_source": "", "font_name": "arial" }
        }
        self.available_text_sources = [
            "ap_workplaces.warehouse_name",
            "ap_workplaces.workplace_number",
            "orders.client_name",
            "packages.sscc_code"
        ]
        self.available_qr_sources = [
            "QR: Конфигурация рабочего места",
            "QR: Конфигурация сервера"
        ]
        self.available_sscc_sources = ["packages.sscc_code"]
        self.available_datamatrix_sources = ["items.datamatrix"]
        self._build_editor_ui()
        self._load_template_to_ui()
        self._redraw_canvas()
    def _build_editor_ui(self):
        main_layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_widget.setMaximumWidth(350)
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        # --- ИЗМЕНЕНИЕ: Добавляем все кнопки для создания объектов ---
        tools_group = QGroupBox("Инструменты")
        tools_layout = QVBoxLayout(tools_group)
        btn_add_text = QPushButton("Добавить Текст (БД)")
        btn_add_text.clicked.connect(lambda: self._add_object('text'))
        btn_add_custom_text = QPushButton("Добавить Текст (свой)")
        btn_add_custom_text.clicked.connect(lambda: self._add_object('custom_text'))
        btn_add_qr = QPushButton("Добавить QR-код")
        btn_add_qr.clicked.connect(lambda: self._add_object('qr'))
        btn_add_sscc = QPushButton("Добавить SSCC")
        btn_add_sscc.clicked.connect(lambda: self._add_object('sscc'))
        btn_add_dm = QPushButton("Добавить DataMatrix")
        btn_add_dm.clicked.connect(lambda: self._add_object('datamatrix'))
        btn_add_image = QPushButton("Добавить Изображение")
        btn_add_image.clicked.connect(lambda: self._add_object('image'))
        btn_add_text_image = QPushButton("Добавить Текст+Картинка")
        btn_add_text_image.clicked.connect(lambda: self._add_object('text_with_image'))
        tools_layout.addWidget(btn_add_text)
        tools_layout.addWidget(btn_add_custom_text)
        tools_layout.addWidget(btn_add_qr)
        tools_layout.addWidget(btn_add_sscc)
        tools_layout.addWidget(btn_add_dm)
        tools_layout.addWidget(btn_add_image)
        tools_layout.addWidget(btn_add_text_image)
        tools_layout.addStretch()

        # --- ИЗМЕНЕНИЕ: Создаем все возможные виджеты для панели свойств ---


        props_group = QGroupBox("Свойства объекта")


        self.props_layout = QFormLayout(props_group)


        


        self.prop_x = QLineEdit()


        self.prop_y = QLineEdit()


        self.prop_w = QLineEdit()


        self.prop_h = QLineEdit()


        


        self.prop_is_custom_text = QCheckBox("Произвольный текст")


        


        # --- ИЗМЕНЕНИЕ: Создаем контейнеры для динамических виджетов ---


        self.prop_data_source_widget = QWidget()


        data_source_layout = QHBoxLayout(self.prop_data_source_widget)


        data_source_layout.setContentsMargins(0, 0, 0, 0)


        self.prop_data_source_combo = QComboBox()


        self.prop_data_source_combo.setEditable(False) # По умолчанию нередактируемый


        self.prop_data_source_entry = QLineEdit()


        data_source_layout.addWidget(self.prop_data_source_combo)


        data_source_layout.addWidget(self.prop_data_source_entry)


        


        self.prop_image_source_widget = QWidget()


        image_source_layout = QHBoxLayout(self.prop_image_source_widget)


        image_source_layout.setContentsMargins(0, 0, 0, 0)


        self.prop_image_source_combo = QComboBox()


        self.prop_image_source_combo.setEditable(True) # Можно вписать имя


        image_source_layout.addWidget(self.prop_image_source_combo)





        self.props_layout.addRow("X (мм):", self.prop_x)


        self.props_layout.addRow("Y (мм):", self.prop_y)


        self.props_layout.addRow("Ширина (мм):", self.prop_w)


        self.props_layout.addRow("Высота (мм):", self.prop_h)


        self.props_layout.addRow(self.prop_is_custom_text)


        self.props_layout.addRow("Источник:", self.prop_data_source_widget)


        self.props_layout.addRow("Источник картинки:", self.prop_image_source_widget)


        


        btn_apply_props = QPushButton("Применить свойства")


        btn_apply_props.clicked.connect(self._apply_properties)


        self.props_layout.addWidget(btn_apply_props)





        controls_layout.addWidget(tools_group)


        controls_layout.addWidget(props_group)


        controls_layout.addStretch()


        controls_layout.addWidget(button_box)





        canvas_widget = QWidget()


        canvas_layout = QVBoxLayout(canvas_widget)


        self.scene = QGraphicsScene()


        self.scene.selectionChanged.connect(self._on_scene_selection_changed)


        self.view = QGraphicsView(self.scene)


        self.view.setRenderHint(QPainter.Antialiasing)


        canvas_layout.addWidget(self.view)





        splitter.addWidget(controls_widget)


        splitter.addWidget(canvas_widget)


        splitter.setSizes([300, 900])


        


        # Коннекторы


        self.prop_is_custom_text.stateChanged.connect(self._update_properties_panel)








    def _redraw_canvas(self):


        self.scene.blockSignals(True)


        try:


            self.scene.clear()


            if not self.template: return


            


            width_px = float(self.template['width_mm']) * self.canvas_scale


            height_px = float(self.template['height_mm']) * self.canvas_scale


            self.scene.setBackgroundBrush(QColor("lightgrey"))


            label_rect = self.scene.addRect(0, 0, width_px, height_px, Qt.NoPen, QColor("white"))


            label_rect.setZValue(-1)





            for i, obj_data in enumerate(self.template.get('objects', [])):


                self._draw_object(obj_data, i)


        except (KeyError, ValueError, TypeError) as e:


            logging.error(f"Ошибка отрисовки холста: {e}")


        finally:


            self.scene.blockSignals(False)








    def _draw_object(self, obj_data, object_id):


        item = PrintableObjectItem(obj_data, object_id, self.canvas_scale)


        self.scene.addItem(item)


        if self.selected_object_id == object_id:


            item.setSelected(True)





    def _add_object(self, template_key: str):


        import copy


        new_object = copy.deepcopy(self.object_templates[template_key])


        


        if 'objects' not in self.template: self.template['objects'] = []


        self.template['objects'].append(new_object)


        self._redraw_canvas()





    def _load_template_to_ui(self):


        if self.template.get('objects'):


            self.selected_object_id = 0


        else:


            self.selected_object_id = None


        self._update_properties_panel()


        self._redraw_canvas() 





    def _update_properties_panel(self):


        logging.debug(f"Updating properties panel for selected_id: {self.selected_object_id}")





        is_object_selected = self.selected_object_id is not None and self.selected_object_id < len(self.template.get('objects', []))


        


        # Включаем/выключаем все поля, кроме чекбокса


        self.prop_x.setEnabled(is_object_selected)


        self.prop_y.setEnabled(is_object_selected)


        self.prop_w.setEnabled(is_object_selected)


        self.prop_h.setEnabled(is_object_selected)


        self.prop_data_source_widget.setEnabled(is_object_selected)


        self.prop_image_source_widget.setEnabled(is_object_selected)


        self.props_layout.labelForField(self.prop_data_source_widget).setEnabled(is_object_selected)


        self.props_layout.labelForField(self.prop_image_source_widget).setEnabled(is_object_selected)








        # Сначала скрываем все опциональные виджеты


        self.prop_is_custom_text.setVisible(False)


        self.props_layout.labelForField(self.prop_image_source_widget).setVisible(False)


        self.prop_image_source_widget.setVisible(False)


        self.prop_data_source_combo.setVisible(False)


        self.prop_data_source_entry.setVisible(False)





        if not is_object_selected:


            self.prop_x.clear(); self.prop_y.clear(); self.prop_w.clear(); self.prop_h.clear()


            self.prop_data_source_combo.clear(); self.prop_data_source_entry.clear()


            logging.debug("Properties panel cleared and disabled.")


            return


            


        obj_data = self.template['objects'][self.selected_object_id]


        obj_type = obj_data.get("type")





        # Заполняем универсальные поля


        self.prop_x.setText(str(obj_data.get('x_mm', '')))


        self.prop_y.setText(str(obj_data.get('y_mm', '')))


        self.prop_w.setText(str(obj_data.get('width_mm', '')))


        self.prop_h.setText(str(obj_data.get('height_mm', '')))


        


        # Блокируем сигналы, чтобы не вызывать _update_properties_panel рекурсивно


        self.prop_is_custom_text.blockSignals(True)


        self.prop_is_custom_text.setChecked(obj_data.get('is_custom_text', False))


        self.prop_is_custom_text.blockSignals(False)





        # Настраиваем панель под конкретный тип объекта


        if obj_type == 'text':


            self.prop_is_custom_text.setVisible(True)


            if obj_data.get('is_custom_text'):


                self.prop_data_source_entry.setVisible(True)


                self.prop_data_source_entry.setText(obj_data.get('data_source', ''))


            else:


                self.prop_data_source_combo.setVisible(True)


                self.prop_data_source_combo.setEditable(False)


                self.prop_data_source_combo.clear()


                self.prop_data_source_combo.addItems(self.available_text_sources)


                self.prop_data_source_combo.setCurrentText(obj_data.get('data_source', ''))





        elif obj_type == 'barcode':


            self.prop_data_source_combo.setVisible(True)


            self.prop_data_source_combo.setEditable(False)


            self.prop_data_source_combo.clear()


            barcode_type = obj_data.get('barcode_type', '').upper()


            sources = {


                'QR': self.available_qr_sources,


                'SSCC': self.available_sscc_sources,


                'DATAMATRIX': self.available_datamatrix_sources


            }.get(barcode_type, [])


            self.prop_data_source_combo.addItems(sources)


            self.prop_data_source_combo.setCurrentText(obj_data.get('data_source', ''))





        elif obj_type == 'image':


            self.prop_data_source_combo.setVisible(True)


            self.prop_data_source_combo.setEditable(True)


            self.prop_data_source_combo.clear()


            # TODO: Загружать список доступных картинок из БД


            self.prop_data_source_combo.addItem(obj_data.get('data_source', '')) # Добавляем текущее значение


            self.prop_data_source_combo.setCurrentText(obj_data.get('data_source', ''))





        elif obj_type == 'text_with_image':


            self.prop_data_source_entry.setVisible(True)


            self.prop_data_source_entry.setText(obj_data.get('data_source', ''))


            self.prop_image_source_widget.setVisible(True)


            self.props_layout.labelForField(self.prop_image_source_widget).setVisible(True)


            self.prop_image_source_combo.clear()


            # TODO: Загружать список доступных картинок из БД


            self.prop_image_source_combo.addItem(obj_data.get('image_source', ''))


            self.prop_image_source_combo.setCurrentText(obj_data.get('image_source', ''))





        logging.debug("Properties panel updated successfully.")





    def _apply_properties(self):


        if self.selected_object_id is None: return


        logging.debug(f"Applying properties for object_id: {self.selected_object_id}")


        try:


            obj_data = self.template['objects'][self.selected_object_id]


            obj_type = obj_data.get("type")





            obj_data['x_mm'] = float(self.prop_x.text())


            obj_data['y_mm'] = float(self.prop_y.text())


            obj_data['width_mm'] = float(self.prop_w.text())


            obj_data['height_mm'] = float(self.prop_h.text())





            if obj_type == 'text':


                is_custom = self.prop_is_custom_text.isChecked()


                obj_data['is_custom_text'] = is_custom


                if is_custom:


                    obj_data['data_source'] = self.prop_data_source_entry.text()


                else:


                    obj_data['data_source'] = self.prop_data_source_combo.currentText()


            


            elif obj_type in ['barcode', 'image']:


                obj_data['data_source'] = self.prop_data_source_combo.currentText()





            elif obj_type == 'text_with_image':


                obj_data['data_source'] = self.prop_data_source_entry.text()


                obj_data['image_source'] = self.prop_image_source_combo.currentText()





            self._redraw_canvas()


            logging.debug("Properties applied and canvas redrawn.")


        except (ValueError, IndexError) as e:


            QMessageBox.warning(self, "Ошибка", f"Некорректные данные в свойствах: {e}")


            


    def _on_scene_selection_changed(self):


        logging.debug("Scene selection changed.")


        selected_items = self.scene.selectedItems()


        


        new_selected_id = None


        if selected_items:


            item = selected_items[0]


            if isinstance(item, PrintableObjectItem):


                new_selected_id = item.object_id





        if self.selected_object_id != new_selected_id:


            self.selected_object_id = new_selected_id


            if new_selected_id is None:


                logging.debug("No items selected or a non-PrintableObjectItem was selected.")


            else:


                logging.debug(f"Item selected: id={self.selected_object_id}")


            self._update_properties_panel()





    def on_item_moved(self, object_id):


        """Слот, вызываемый из PrintableObjectItem при перемещении."""


        if self.selected_object_id == object_id:


            self._update_properties_panel()


            


    def accept(self):


        try:


            if not self.template.get('name'):


                QMessageBox.warning(self, "Ошибка", "Название макета не может быть пустым.")


                return





            self.catalog_service.upsert_print_layout(self.template)


            super().accept()


        except Exception as e:


            QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить макет: {e}")








# --- NEW DIALOG FOR EMPLOYEE PASSES ---


class EmployeePassesViewerDialog(QDialog):


    """Диалог для просмотра и печати пропусков сотрудников."""


    def __init__(self, parent, task_service, task_id):


        super().__init__(parent)


        self.task_service = task_service


        self.task_id = task_id


        self.pass_details = None


        self.setWindowTitle(f"Пропуски для задачи #{task_id}")


        self.setMinimumSize(600, 400)


        self._build_ui()


        self._load_passes()





        def _build_ui(self):





            main_layout = QVBoxLayout(self)





            self.table = QTableWidget()





            self.table.setColumnCount(1)





            self.table.setHorizontalHeaderLabels(["Код доступа (пропуск)"])





            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)





            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)





            main_layout.addWidget(self.table)





            buttons_layout = QHBoxLayout()





            btn_print = QPushButton("Печать")





            btn_print.clicked.connect(self._print_passes)





            btn_close = QPushButton("Закрыть")





            btn_close.clicked.connect(self.accept)





            buttons_layout.addStretch()





            buttons_layout.addWidget(btn_print)





            buttons_layout.addWidget(btn_close)





            main_layout.addLayout(buttons_layout)





    





        def _load_passes(self):





            try:





                self.pass_details = self.task_service.get_employee_passes_details(self.task_id)





                if not self.pass_details or not self.pass_details.get("passes"):





                    QMessageBox.warning(self, "Нет данных", "Не найдено сгенерированных пропусков для этой задачи.")





                    return





                self.table.setRowCount(len(self.pass_details["passes"]))





                for i, access_code in enumerate(self.pass_details["passes"]):





                    self.table.setItem(i, 0, QTableWidgetItem(access_code))





            except Exception as e:





                QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить пропуски: {e}")





                self.close()





    





        def _print_passes(self):





            """Запускает процесс печати пропусков."""





            if not self.pass_details or not self.pass_details.get("passes"):





                QMessageBox.warning(self, "Нет данных", "Нет пропусков для печати.")





                return





            printer = QPrinter(QPrinter.HighResolution)





            printer.setPageSizeMM(QSize(60, 40))





            printer.setPageMargins(2, 2, 2, 2, QPrinter.Millimeter)





            dialog = QPrintDialog(printer, self)





            if dialog.exec() != QDialog.Accepted:





                return





            painter = QPainter()





            if not painter.begin(printer):





                QMessageBox.critical(self, "Ошибка", "Не удалось запустить процесс печати.")





                return





            dpi_x = printer.resolution()





            dpi_y = printer.resolution()





            def mm_to_px(mm, dpi):





                return (mm / 25.4) * dpi





            font_main = QFont("Arial", pointSize=10)





            font_small = QFont("Arial", pointSize=8)





            client_name = self.pass_details.get('client_name', 'N/A')





            container_number = self.pass_details.get('container_number', 'N/A')





            print_date = datetime.now().strftime("%d.%m.%Y")





            passes = self.pass_details["passes"]





            for i, access_code in enumerate(passes):





                if i > 0:





                    printer.newPage()





                painter.setFont(font_main)





                painter.drawText(QRectF(mm_to_px(2, dpi_x), mm_to_px(2, dpi_y), mm_to_px(56, dpi_x), mm_to_px(8, dpi_y)), Qt.AlignLeft, f"Клиент: {client_name}")





                painter.drawText(QRectF(mm_to_px(2, dpi_x), mm_to_px(8, dpi_y), mm_to_px(56, dpi_x), mm_to_px(8, dpi_y)), Qt.AlignLeft, f"Контейнер: {container_number}")





                painter.setFont(font_small)





                painter.drawText(QRectF(mm_to_px(2, dpi_x), mm_to_px(14, dpi_y), mm_to_px(56, dpi_x), mm_to_px(6, dpi_y)), Qt.AlignLeft, f"Дата: {print_date}")





                try:





                    barcode = code128.Code128(access_code, barHeight=mm_to_px(10, dpi_y), barWidth=mm_to_px(0.2, dpi_x))





                    drawing = Drawing(mm_to_px(54, dpi_x), mm_to_px(12, dpi_y))





                    drawing.add(barcode)





                    pil_image = renderPM.drawToPIL(drawing)





                    buffer = io.BytesIO()





                    pil_image.save(buffer, format="PNG")





                    buffer.seek(0)





                    barcode_pixmap = QPixmap()





                    barcode_pixmap.loadFromData(buffer.getvalue(), "PNG")





                    barcode_x_px = mm_to_px(3, dpi_x)





                    barcode_y_px = mm_to_px(20, dpi_y)





                    painter.drawPixmap(int(barcode_x_px), int(barcode_y_px), barcode_pixmap.width(), barcode_pixmap.height(), barcode_pixmap)





                except Exception as e:





                    logging.error(f"Ошибка генерации штрихкоды: {e}", exc_info=True)





                    painter.drawText(QRectF(mm_to_px(2, dpi_x), mm_to_px(20, dpi_y), mm_to_px(56, dpi_x), mm_to_px(12, dpi_y)), Qt.AlignCenter, "Ошибка ШК")





                notes_rect_y_px = mm_to_px(33, dpi_y)





                notes_rect_height_px = mm_to_px(5, dpi_y)





                painter.drawRect(int(mm_to_px(2, dpi_x)), int(notes_rect_y_px), int(mm_to_px(56, dpi_x)), int(notes_rect_height_px))





            painter.end()





            QMessageBox.information(self, "Успех", "Задание на печать отправлено.")





        





    





    # --- END NEW DIALOG ---








class AdminWindowQt(QMainWindow):
    """Переносная версия tkinter админ-интерфейса на PySide6 с левым меню и правой стеком контента."""
    def __init__(self, user_info: dict):
        super().__init__()
        self.user_info = user_info
        self.setWindowTitle(f"Admin - {user_info.get('name', '')}")
        self.resize(1200, 700)
        
        # --- ИСПРАВЛЕНИЕ: Инициализируем кэши для заказов ---
        self.in_progress_orders_cache = []
        self.archive_orders_cache = []

        # --- ИСПРАВЛЕНИЕ: Инициализируем сервисы ---
        self.catalog_service = CatalogsService(self.user_info, lambda: get_client_db_connection(self.user_info))
        self.order_service = OrderService(self.user_info)
        self.task_service = TaskService(self.user_info) # НОВЫЙ СЕРВИС
        # --- ИЗМЕНЕНИЕ: Передаем обработчик для повторной аутентификации ---
        self.api_service = ApiService(self.user_info, self.order_service, reauth_handler=self._reauthenticate_api)
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        self._build_ui()
        self._setup_db_status_checker() # Настраиваем и запускаем проверку БД
        self._setup_api_status_checker() # Настраиваем и запускаем проверку API

    def _reauthenticate_api(self):
        """
        Handles the full re-authentication flow for the API.
        Called by ApiService when a refresh token is invalid.
        Returns True on success, False on failure.
        """
        logging.info("Запуск процесса повторной аутентификации в API...")
        try:
            # Метод authenticate использует учетные данные из self.user_info.
            success = self.api_service.authenticate()
            if success:
                QMessageBox.information(self, "Аутентификация API", "Аутентификация в API прошла успешно. Токены обновлены.")
                self._update_api_status() # Обновляем индикатор
            return success
        except Exception as e:
            logger.error(f"Повторная аутентификация в API не удалась: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка аутентификации API", f"Не удалось повторно пройти аутентификацию в API.\n\nОшибка: {e}\n\nПожалуйста, проверьте настройки API или перезапустите приложение.")
            self._set_api_status_color(False) # Обновляем индикатор
            return False

    def _build_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout()

        # --- LEFT PANEL: TREE MENU (1/5) ---
        self.menu_tree = QTreeWidget()
        self.menu_tree.setHeaderLabel("Меню")
        self.menu_tree.setMaximumWidth(250)
        self.menu_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.menu_tree.itemClicked.connect(self._on_menu_clicked)

        # Главные пункты меню
        item_notifications = QTreeWidgetItem(self.menu_tree, ["Управление уведомлениями"])
        item_orders = QTreeWidgetItem(self.menu_tree, ["Управление заказами"])
        item_tasks = QTreeWidgetItem(self.menu_tree, ["Управление задачами"])
        item_admin = QTreeWidgetItem(self.menu_tree, ["Администрирование"])

        # Подменю "Администрирование"
        item_admin_config = QTreeWidgetItem(item_admin, ["Конфигурация"])
        item_admin_print = QTreeWidgetItem(item_admin, ["Управление печатью"])
        item_admin_utilities = QTreeWidgetItem(item_admin, ["Служебные"]) # НОВЫЙ ПОДРАЗДЕЛ
        item_generate_sscc = QTreeWidgetItem(item_admin_utilities, ["Сгенерировать SSCC"]) # НОВЫЙ ПУНКТ
        item_config_save_ini = QTreeWidgetItem(item_admin_utilities, ["Сохранить INI"]) # ПЕРЕМЕЩЕНО
        item_upload_lenta = QTreeWidgetItem(item_admin_utilities, ["Загрузить Ленту"]) # НОВЫЙ ПУНКТ
        item_admin_catalogs = QTreeWidgetItem(item_admin, ["Справочники"])
        item_admin_reports = QTreeWidgetItem(item_admin, ["Отчеты"])

        # Подменю "Конфигурация"
        item_config_workplaces = QTreeWidgetItem(item_admin_config, ["Конфигурация складов"])

        # Сохраняем ссылки для быстрого доступа
        self.menu_items = {
            'notifications': item_notifications,
            'orders': item_orders,
            'tasks': item_tasks,
            'admin': item_admin,
            'config': item_admin_config,
            'print': item_admin_print,
            'catalogs': item_admin_catalogs,
            'reports': item_admin_reports,
            'utilities': item_admin_utilities, # Добавляем в словарь
            'generate_sscc': item_generate_sscc, # Добавляем в словарь
            'save_ini': item_config_save_ini,
            'upload_lenta': item_upload_lenta,
            'workplaces': item_config_workplaces,
        }

        # Меню свернуто по умолчанию (не разворачиваем никакие пункты)

        # --- RIGHT PANEL: STACKED WIDGET (4/5) ---
        self.content_stack = QStackedWidget()

        # Страница 0: Приветствие
        self.page_welcome = self._build_welcome_page()
        self.content_stack.addWidget(self.page_welcome)

        # Страница 1: Управление уведомлениями
        self.page_notifications = self._build_notifications_page()
        self.content_stack.addWidget(self.page_notifications)

        # Страница 2: Управление заказами
        self.page_orders = self._build_orders_page()
        self.content_stack.addWidget(self.page_orders)

        # НОВАЯ СТРАНИЦА: Управление задачами
        self.page_tasks = self._build_tasks_page()
        self.content_stack.addWidget(self.page_tasks)

        # Страница 3: Конфигурация складов
        self.page_workplaces = self._build_workplaces_page()
        self.content_stack.addWidget(self.page_workplaces)

        # Страница 4: Справочники
        self.page_catalogs = self._build_catalogs_page()
        self.content_stack.addWidget(self.page_catalogs)

        # Страница 5: Управление печатью
        self.page_print_management = self._build_print_management_page()
        self.content_stack.addWidget(self.page_print_management)

        # Страница 6: Пустая заглушка для остальных
        self.page_placeholder = QWidget()
        placeholder_layout = QVBoxLayout()
        placeholder_layout.addWidget(QLabel("Раздел находится в разработке"))
        self.page_placeholder.setLayout(placeholder_layout)
        self.content_stack.addWidget(self.page_placeholder)

        # Сохраняем индексы для быстрого доступа
        self.stack_indices = {
            'welcome': 0,
            'notifications': 1,
            'orders': 2,
            'tasks': 3, # НОВЫЙ ИНДЕКС
            'workplaces': 4,
            'catalogs': 5,
            'print_management': 6,
            'placeholder': 7,
        }

        # Собираем основной layout
        main_layout.addWidget(self.menu_tree, 1)
        main_layout.addWidget(self.content_stack, 4)
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

        # --- НОВЫЙ БЛОК: Создание строки состояния и индикатора API ---
        status_bar = self.statusBar()
        
        # Индикатор API
        status_bar.addPermanentWidget(QLabel("API:"))
        self.api_status_indicator = QLabel()
        self.api_status_indicator.setFixedSize(16, 16)
        self.api_status_indicator.setToolTip("Статус подключения к API ДМ.Код\nДвойной клик для принудительного обновления токена.")
        self._set_api_status_color(None) 
        status_bar.addPermanentWidget(self.api_status_indicator)

        # Разделитель
        status_bar.addPermanentWidget(QLabel("  |  "))

        # Индикатор БД
        status_bar.addPermanentWidget(QLabel("БД:"))
        self.db_status_indicator = QLabel()
        self.db_status_indicator.setFixedSize(16, 16)
        self.db_status_indicator.setToolTip("Статус подключения к базе данных клиента.\nДвойной клик для проверки соединения.")
        self._set_db_status_color(None)
        status_bar.addPermanentWidget(self.db_status_indicator)

        # Показываем приветственную страницу по умолчанию
        self.content_stack.setCurrentIndex(self.stack_indices['welcome'])

    def _setup_api_status_checker(self):
        """Настраивает и запускает периодическую проверку статуса API."""
        # Запускаем первую проверку сразу
        self._update_api_status()

        # Создаем таймер для периодических проверок (каждые 10 минут)
        self.api_check_timer = QTimer(self)
        self.api_check_timer.setInterval(600 * 1000) # 600 секунд = 10 минут
        self.api_check_timer.timeout.connect(self._update_api_status)
        self.api_check_timer.start()

    def _setup_db_status_checker(self):
        """Настраивает и запускает периодическую проверку статуса БД."""
        # Запускаем первую проверку сразу
        self._update_db_status()

        # Создаем таймер для периодических проверок (каждые 5 минут)
        self.db_check_timer = QTimer(self)
        self.db_check_timer.setInterval(300 * 1000) # 300 секунд = 5 минут
        self.db_check_timer.timeout.connect(self._update_db_status)
        self.db_check_timer.start()

    @Slot()
    def _update_api_status(self):
        """Запускает проверку API в фоновом потоке."""
        self.thread = QThread()
        self.worker = ApiStatusWorker(self.user_info)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._set_api_status_color)
        
        # Очистка после завершения
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()

    @Slot()
    def _update_db_status(self):
        """Запускает проверку БД в фоновом потоке."""
        self.db_thread = QThread()
        self.db_worker = DbStatusWorker(self.user_info)
        self.db_worker.moveToThread(self.db_thread)

        self.db_thread.started.connect(self.db_worker.run)
        self.db_worker.finished.connect(self._set_db_status_color)
        
        # Очистка после завершения
        self.db_worker.finished.connect(self.db_thread.quit)
        self.db_worker.finished.connect(self.db_worker.deleteLater)
        self.db_thread.finished.connect(self.db_thread.deleteLater)

        self.db_thread.start()

    @Slot(bool)
    def _set_api_status_color(self, is_valid):
        """Устанавливает цвет индикатора в зависимости от статуса."""
        if is_valid is None:
            color = "grey" # Начальный статус
        else:
            color = "green" if is_valid else "red"
        
        self.api_status_indicator.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                border-radius: 8px;
                border: 1px solid black;
            }}
        """)

    @Slot(bool)
    def _set_db_status_color(self, is_connected):
        """Устанавливает цвет индикатора БД."""
        if is_connected is None:
            color = "grey" # Начальный статус
        else:
            color = "green" if is_connected else "red"
        
        self.db_status_indicator.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                border-radius: 8px;
                border: 1px solid black;
            }}
        """)

    def mouseDoubleClickEvent(self, event):
        """Обрабатывает двойной клик по окну (для индикатора)."""
        if self.api_status_indicator.underMouse():
            logging.info("Принудительное обновление токена API по двойному клику...")
            QMessageBox.information(self, "Обновление токена", "Запущено принудительное обновление токена API...")
            self._update_api_status() # Просто запускаем обычную проверку
        elif self.db_status_indicator.underMouse():
            logging.info("Принудительная проверка соединения с БД по двойному клику...")
            QMessageBox.information(self, "Проверка соединения", "Запущена проверка соединения с базой данных...")
            self._update_db_status()
        super().mouseDoubleClickEvent(event)

    @Slot(QTreeWidgetItem, int)
    def _on_menu_clicked(self, item: QTreeWidgetItem, column: int):
        """Обработчик клика на пункт меню — переключает правую панель."""
        text = item.text(column)
        
        # В зависимости от текста пункта меню, переключаем страницу
        if text == "Управление уведомлениями":
            try:
                # При переключении на уведомления, загружаем их
                self.load_notifications()
            except Exception:
                logging.exception("Error loading notifications on menu click")
            self.content_stack.setCurrentIndex(self.stack_indices['notifications'])
        elif text == "Управление заказами":
            try:
                # --- ИСПРАВЛЕНИЕ: Загружаем статистику при первом открытии ---
                self._load_order_statistics()
                self.load_orders(is_archive=False) # Загружаем активные заказы
            except Exception:
                logging.exception("Error loading orders on menu click")
            self.content_stack.setCurrentIndex(self.stack_indices['orders'])
        elif text == "Управление задачами":
            try:
                self.load_tasks()
            except Exception:
                logging.exception("Error loading tasks on menu click")
            self.content_stack.setCurrentIndex(self.stack_indices['tasks'])
        elif text == "Сохранить INI":
            self._save_config_files() # Сразу вызываем сохранение
        elif text == "Конфигурация складов":
            try:
                # При переключении на склады, загружаем их
                self.load_warehouses()
            except Exception:
                logging.exception("Error loading warehouses on menu click")
            self.content_stack.setCurrentIndex(self.stack_indices['workplaces'])
        elif text == "Сгенерировать SSCC":
            self._open_generate_sscc_dialog() # Вызываем диалог, не меняя основное окно
        elif text == "Загрузить Ленту":
            self._open_lenta_upload_dialog() # Вызываем новый диалог
        elif text == "Справочники":
            self.content_stack.setCurrentIndex(self.stack_indices['catalogs'])
        elif text == "Управление печатью":
            self.content_stack.setCurrentIndex(self.stack_indices['print_management'])
        else:
            # Для всех остальных пунктов пока показываем заглушку
            self.content_stack.setCurrentIndex(self.stack_indices['placeholder'])

    def _build_welcome_page(self):
        """Страница приветствия при открытии админ-интерфейса."""
        widget = QWidget()
        layout = QVBoxLayout()
        layout.addStretch()

        # Текст приветствия
        username = self.user_info.get('name', 'Администратор')
        welcome_label = QLabel(f"Добро пожаловать, {username}")
        welcome_font = welcome_label.font()
        welcome_font.setPointSize(18)
        welcome_label.setFont(welcome_font)
        welcome_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(welcome_label)

        layout.addStretch()
        widget.setLayout(layout)
        return widget

    def _build_orders_page(self):
        """Создает страницу для управления заказами с вкладками 'В работе' и 'Архив'."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        self.orders_tab_widget = QTabWidget()
        
        # Вкладка "В работе"
        # --- ИСПРАВЛЕНИЕ: Распаковываем все 5 возвращаемых значений ---
        (in_progress_widget, 
         self.in_progress_orders_table, self.in_progress_management_stack,
         self.in_progress_client_filter, self.in_progress_search_filter,
         self.in_progress_edit_tab, self.in_progress_api_tab, self.in_progress_upload_tab,
         self.in_progress_docs_tab, # --- ИЗМЕНЕНИЕ: Добавляем вкладку документов ---
         self.in_progress_stats_table
        ) = self._create_orders_view(is_archive=False)
        self.orders_tab_widget.addTab(in_progress_widget, "В работе")

        # Вкладка "Архив"
        (archive_widget, 
         self.archive_orders_table, self.archive_management_stack,
         self.archive_client_filter, self.archive_search_filter,
         self.archive_edit_tab, self.archive_api_tab, self.archive_upload_tab,
         self.archive_docs_tab, # --- ИЗМЕНЕНИЕ: Добавляем вкладку документов ---
         self.archive_stats_table
        ) = self._create_orders_view(is_archive=True)
        self.orders_tab_widget.addTab(archive_widget, "Архив")

        self.orders_tab_widget.currentChanged.connect(self._on_orders_tab_changed)

        layout.addWidget(self.orders_tab_widget)
        return widget

    def _create_orders_view(self, is_archive):
        """Создает UI для одной вкладки заказов (активных или архивных)."""
        # Основной виджет вкладки
        view_widget = QWidget()
        main_layout = QVBoxLayout(view_widget)

        # Разделитель для таблицы и статистики
        main_splitter = QSplitter(Qt.Vertical)

        # Верхняя панель (таблица и управление)
        top_widget = QWidget()
        top_layout = QHBoxLayout(top_widget)
        top_splitter = QSplitter(Qt.Horizontal)

        # Левая часть: таблица заказов
        table_widget = QTableWidget(0, 6)
        table_widget.setHorizontalHeaderLabels(["Дата", "Клиент / Заказ №", "Статус", "Кол-во позиций", "Кол-во ДМ", "Комментарий"])
        table_widget.setSelectionBehavior(QAbstractItemView.SelectRows)
        table_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        table_widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        # ИСПРАВЛЕНИЕ: Делаем таблицу нередактируемой и добавляем стиль для выделения
        table_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table_widget.setStyleSheet("""
            QTableWidget::item:selected { background-color: #ADD8E6; }
        """)
        
        # Вкладки для управления
        management_tabs = QTabWidget()
        # --- ИСПРАВЛЕНИЕ: Создаем локальные виджеты для вкладок, а не атрибуты класса ---
        order_edit_tab = QWidget()
        order_edit_tab.setLayout(QVBoxLayout())
        order_api_tab = QWidget()
        order_api_tab.setLayout(QVBoxLayout())
        order_upload_tab = QWidget()
        order_upload_tab.setLayout(QVBoxLayout())
        order_docs_tab = QWidget() # --- ИЗМЕНЕНИЕ: Создаем виджет для вкладки документов ---
        order_docs_tab.setLayout(QVBoxLayout())
        
        management_tabs.addTab(order_edit_tab, "Редактирование")
        management_tabs.addTab(order_api_tab, "АПИ")
        management_tabs.addTab(order_docs_tab, "Документы") # --- ИЗМЕНЕНИЕ: Добавляем вкладку в таб-виджет ---
        management_tabs.addTab(order_upload_tab, "Загрузка кодов")

        # --- НОВЫЙ БЛОК: Фильтры для заказов ---
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Клиент:"))
        # --- ИСПРАВЛЕНИЕ: Явно указываем родительский виджет (view_widget), чтобы избежать преждевременного удаления ---
        client_filter_combo = QComboBox(view_widget) 
        client_filter_combo.addItem("Все клиенты")
        filter_layout.addWidget(client_filter_combo)

        filter_layout.addWidget(QLabel("Поиск:"))
        # --- ИСПРАВЛЕНИЕ: Явно указываем родительский виджет ---
        search_filter_edit = QLineEdit(view_widget) 
        search_filter_edit.setPlaceholderText("Поиск по клиенту, комментарию, статусу...")
        filter_layout.addWidget(search_filter_edit)
        
        # Добавляем фильтры над таблицей
        table_container_layout = QVBoxLayout()
        table_container_layout.addLayout(filter_layout)
        table_container_layout.addWidget(table_widget)
        table_container_widget = QWidget()
        table_container_widget.setLayout(table_container_layout)

        # Правая часть: панель управления
        management_stack = QStackedWidget()
        placeholder_label = QLabel("Выберите заказ для управления")
        placeholder_label.setAlignment(Qt.AlignCenter)
        management_stack.addWidget(placeholder_label) # Индекс 0
        management_stack.addWidget(management_tabs) # Индекс 1

        # --- ИСПРАВЛЕНИЕ: Правильно добавляем виджеты в сплиттер и задаем пропорции ---
        top_splitter.addWidget(table_container_widget) # Слева - контейнер с таблицей и фильтрами
        top_splitter.addWidget(management_stack)       # Справа - панель управления
        top_splitter.setSizes([800, 400])              # Пропорция 2:1 для таблицы и панели управления
        top_layout.addWidget(top_splitter)

        # Нижняя панель (статистика)
        bottom_widget = QWidget()
        stats_layout = QVBoxLayout(bottom_widget)
        stats_table = QTableWidget(0, 5)
        stats_table.setHorizontalHeaderLabels(["Тип обработки", "Клиент", "Статус", "Кол-во позиций", "Кол-во ДМ"])
        stats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        stats_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        stats_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        stats_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        stats_layout.addWidget(stats_table)

        main_splitter.addWidget(top_widget) # top_widget теперь содержит корректно настроенный top_splitter
        main_splitter.addWidget(bottom_widget)
        main_splitter.setSizes([600, 200])             # Пропорция 3:1 для основной области и статистики
        
        main_layout.addWidget(main_splitter)

        # Привязываем обработчики к фильтрам
        table_widget.itemSelectionChanged.connect(lambda: self.on_order_select(is_archive))
        client_filter_combo.currentIndexChanged.connect(lambda: self.apply_order_filters(is_archive))
        search_filter_edit.textChanged.connect(lambda: self.apply_order_filters(is_archive))

        # --- ИЗМЕНЕНИЕ: Возвращаем все созданные виджеты, включая новую вкладку документов ---
        return view_widget, table_widget, management_stack, client_filter_combo, search_filter_edit, order_edit_tab, order_api_tab, order_upload_tab, order_docs_tab, stats_table

    def _on_orders_tab_changed(self, index):
        """Загружает данные при переключении вкладок 'В работе' / 'Архив'."""
        is_archive = (index == 1)
        self._load_order_statistics()
        self.load_orders(is_archive)

    def load_orders(self, is_archive):
        """Загружает заказы в соответствующую таблицу."""
        table = self.archive_orders_table if is_archive else self.in_progress_orders_table
        # --- НОВЫЙ БЛОК: Получаем нужные виджеты фильтров ---
        client_filter = self.archive_client_filter if is_archive else self.in_progress_client_filter
        search_filter = self.archive_search_filter if is_archive else self.in_progress_search_filter
        cache = self.archive_orders_cache if is_archive else self.in_progress_orders_cache # --- ИЗМЕНЕНИЕ: Используем правильный кеш ---

        # Блокируем сигналы, чтобы избежать лишних вызовов apply_filters при очистке
        client_filter.blockSignals(True)
        table.setRowCount(0)
        try:
            orders = self.order_service.get_orders(is_archive)

            # Сохраняем данные в кэш
            cache.clear()
            cache.extend(orders)

            # Заполняем комбобокс клиентов
            client_filter.clear()
            client_filter.addItem("Все клиенты")
            if orders:
                client_names = sorted(list(set(o['client_name'] for o in orders)))
                client_filter.addItems(client_names)

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить заказы: {e}")
        finally:
            client_filter.blockSignals(False)
            # После загрузки применяем фильтры
            self.apply_order_filters(is_archive)

    def on_order_select(self, is_archive):
        """Обработчик выбора заказа в таблице. Отображает панель управления."""
        # logging.debug(f"on_order_select: Сработал обработчик выбора заказа. is_archive={is_archive}")
        table = self.archive_orders_table if is_archive else self.in_progress_orders_table
        management_stack = self.archive_management_stack if is_archive else self.in_progress_management_stack

        current_row = table.currentRow()
        if current_row < 0:
            management_stack.setCurrentIndex(0) # Показываем заглушку
            return

        item = table.item(current_row, 0)
        if not item:
            management_stack.setCurrentIndex(0)
            return

        # Получаем данные заказа, сохраненные ранее
        order_data = item.data(Qt.UserRole)
        if not order_data:
            management_stack.setCurrentIndex(0)
            # logging.debug("on_order_select: Данные заказа (UserRole) не найдены. Показываем заглушку.")
            return

        # --- НОВАЯ ЛОГИКА: Переносим логику из Tkinter-версии ---
        try:
            order_id = order_data['id']
            order_status = order_data['status']

            # logging.debug(f"on_order_select: Выбран заказ ID: {order_id}, Статус: {order_status}")

            # 1. Получаем данные сценария для этого заказа
            result = self.order_service.get_order_scenario_data(order_id)
            scenario_data = result['scenario_data'] if result else {}
            dm_source = scenario_data.get('dm_source')
            post_processing_mode = scenario_data.get('post_processing')
            scenario_type = scenario_data.get('type')
            show_create_task = (scenario_type == 'Ручная агрегация' or post_processing_mode == 'Собственный алгоритм')
            # logging.debug(f"on_order_select: Данные сценария получены. dm_source: '{dm_source}', post_processing: '{post_processing_mode}'.")

            # 2. Очищаем вкладки от старых виджетов
            # logging.debug("on_order_select: Начало очистки вкладок панели управления.")
            management_tabs = management_stack.widget(1) # Получаем QTabWidget
            for i in range(management_tabs.count()):
                tab = management_tabs.widget(i)
                if tab.layout() is not None:
                    while tab.layout().count():
                        item = tab.layout().takeAt(0)
                        widget = item.widget()
                        if widget:
                            widget.deleteLater()
            # logging.debug("on_order_select: Очистка вкладок завершена.")

            # --- ИСПРАВЛЕНИЕ: Получаем правильные виджеты вкладок для текущей панели ---
            edit_tab = self.archive_edit_tab if is_archive else self.in_progress_edit_tab
            api_tab = self.archive_api_tab if is_archive else self.in_progress_api_tab
            upload_tab = self.archive_upload_tab if is_archive else self.in_progress_upload_tab
            docs_tab = self.archive_docs_tab if is_archive else self.in_progress_docs_tab # --- ИЗМЕНЕНИЕ: Получаем виджет вкладки документов ---

            # 3. Создаем и размещаем новые виджеты
            # Вкладка "Редактирование" всегда есть
            # --- ИЗМЕНЕНИЕ: Передаем сервис заказов и флаг is_archive в редактор ---
            editor_frame = OrderEditorFrameQt(self.order_service, order_id, scenario_data, self, is_archive=is_archive, show_create_task_button=show_create_task)
            edit_tab.layout().addWidget(editor_frame)

            # --- ИЗМЕНЕНИЕ: Создаем и заполняем вкладку "Документы" ---
            docs_frame = self._create_order_documents_frame(result.get('notification_id'))
            docs_tab.layout().addWidget(docs_frame)

            # Вкладки "АПИ" и "Загрузка кодов"
            # --- ИЗМЕНЕНИЕ: Для архивных заказов скрываем вкладку АПИ ---
            if is_archive:
                management_tabs.setTabVisible(management_tabs.indexOf(api_tab), False)
                management_tabs.setTabVisible(management_tabs.indexOf(docs_tab), True) # Показываем документы
                management_tabs.setTabVisible(management_tabs.indexOf(upload_tab), False)
            else:
                if dm_source == "Файлы клиента (csv, txt)":
                    # logging.debug(f"on_order_select: Создание CodeUploadFrameQt для заказа ID {order_id}...")
                    upload_frame = CodeUploadFrameQt(self.user_info, order_id, self)
                    upload_tab.layout().addWidget(upload_frame)
                    management_tabs.setTabVisible(management_tabs.indexOf(api_tab), False)
                    management_tabs.setTabVisible(management_tabs.indexOf(docs_tab), True) # Показываем документы
                    management_tabs.setTabVisible(management_tabs.indexOf(upload_tab), True)
                    # logging.debug("on_order_select: Вкладка 'АПИ' скрыта, 'Загрузка кодов' показана.")
                else: # По умолчанию или "Заказ в ДМ.Код"
                    # logging.debug(f"on_order_select: Создание ApiIntegrationFrameQt для заказа ID {order_id}...")
                    api_frame = ApiIntegrationFrameQt(self.api_service, order_id, post_processing_mode, self)
                    api_tab.layout().addWidget(api_frame)
                    management_tabs.setTabVisible(management_tabs.indexOf(api_tab), True)
                    management_tabs.setTabVisible(management_tabs.indexOf(docs_tab), True) # Показываем документы
                    management_tabs.setTabVisible(management_tabs.indexOf(upload_tab), False)
                    # logging.debug("on_order_select: Вкладка 'АПИ' показана, 'Загрузка кодов' скрыта.")
                    # Активируем вкладку АПИ только для нужных статусов
                    is_api_enabled = order_status in ('delta', 'dmkod')
                    management_tabs.setTabEnabled(management_tabs.indexOf(api_tab), is_api_enabled)
                    # logging.debug(f"on_order_select: Вкладка 'АПИ' {'включена' if is_api_enabled else 'отключена'} для статуса '{order_status}'.")

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось отобразить панель управления: {e}")
            management_stack.setCurrentIndex(0)
            return

        # Переключаем QStackedWidget на панель с вкладками
        management_stack.setCurrentIndex(1)

    def _create_order_documents_frame(self, notification_id):
        """Создает виджет для вкладки 'Документы' заказа, аналогичный тому, что в уведомлениях."""
        frame = QWidget()
        layout = QVBoxLayout(frame)
        
        if not notification_id:
            layout.addWidget(QLabel("Заказ не связан с уведомлением, документы недоступны."))
            return frame

        # Сохраняем ID для использования в обработчиках
        frame.notification_id = notification_id

        # Кнопки управления
        controls = QHBoxLayout()
        btn_upload_doc = QPushButton("Загрузить")
        btn_download_doc = QPushButton("Скачать")
        btn_delete_doc = QPushButton("Удалить")
        controls.addWidget(btn_upload_doc)
        controls.addWidget(btn_download_doc)
        controls.addWidget(btn_delete_doc)
        controls.addStretch()
        layout.addLayout(controls)

        # Таблица файлов
        files_table = QTableWidget(0, 1)
        files_table.setHorizontalHeaderLabels(["Имя файла"])
        files_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        files_table.setSelectionBehavior(QTableWidget.SelectRows)
        files_table.setSelectionMode(QTableWidget.SingleSelection)
        files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        files_table.setStyleSheet("QTableWidget::item:selected { background-color: #ADD8E6; }")
        layout.addWidget(files_table)
        frame.files_table = files_table # Сохраняем ссылку на таблицу

        # Привязка обработчиков (используем методы от уведомлений, передавая ID)
        # Мы используем self.current_notification_id, который устанавливается в load_notification_details
        # Здесь мы его переопределим для контекста заказа
        btn_upload_doc.clicked.connect(lambda: self.upload_notification_doc(notification_id))
        btn_download_doc.clicked.connect(lambda: self.download_notification_doc(frame))
        btn_delete_doc.clicked.connect(lambda: self.delete_notification_doc(frame))

        self.load_notification_files(notification_id, target_table=files_table)
        return frame

    def _build_tasks_page(self):
        """Создает страницу для управления задачами."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        view_widget, self.tasks_table, self.task_management_stack = self._create_tasks_view()
        
        layout.addWidget(view_widget)
        return widget

    def _create_tasks_view(self):
        """Создает UI для вкладки задач."""
        view_widget = QWidget()
        main_layout = QHBoxLayout(view_widget)
        splitter = QSplitter(Qt.Horizontal)

        # Левая часть: таблица задач
        table_widget = QTableWidget(0, 5)
        table_widget.setHorizontalHeaderLabels(["ID", "Клиент / Заказ №", "Тип", "Статус", "Дата создания"]) # Updated headers
        table_widget.setColumnHidden(0, True) # Hide ID column
        table_widget.setSelectionBehavior(QAbstractItemView.SelectRows)
        table_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        table_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table_widget.setStyleSheet("QTableWidget::item:selected { background-color: #ADD8E6; }")
        table_widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch) # Stretch Client/Order column
        
        # Правая часть: панель управления
        management_stack = QStackedWidget()
        placeholder_label = QLabel("Выберите задачу для управления")
        placeholder_label.setAlignment(Qt.AlignCenter)
        management_stack.addWidget(placeholder_label) # Индекс 0
        
        # Виджет-контейнер, в который будет добавляться TaskEditorFrameQt
        editor_container = QWidget()
        editor_container.setLayout(QVBoxLayout())
        management_stack.addWidget(editor_container) # Индекс 1

        splitter.addWidget(table_widget)
        splitter.addWidget(management_stack)
        splitter.setSizes([700, 500])
        main_layout.addWidget(splitter)
        
        table_widget.itemSelectionChanged.connect(self.on_task_select)

        return view_widget, table_widget, management_stack

    def load_tasks(self):
        """Загружает задачи в таблицу."""
        self.tasks_table.setRowCount(0)
        try:
            tasks = self.task_service.get_tasks()
            for task in tasks:
                row = self.tasks_table.rowCount()
                self.tasks_table.insertRow(row)
                
                # Format created_at date
                created_at_dt = task.get('created_at')
                formatted_created_at = created_at_dt.strftime("%d.%m.%Y") if created_at_dt else ""

                items_to_add = [
                    str(task.get('id', '')), # Keep ID in the list, but it will be hidden
                    f"{task.get('client_name', '')} / Заказ № {task.get('order_id', '')}",
                    task.get('type', ''),
                    task.get('status', ''),
                    formatted_created_at
                ]

                bg_color = QColor("white")
                status = task.get('status')
                if status == 'new': bg_color = QColor("#FFFFE0") # Light Yellow
                elif status == 'in_progress': bg_color = QColor("#ADD8E6") # Light Blue
                elif status == 'completed': bg_color = QColor("#90EE90") # Light Green
                elif status == 'error': bg_color = QColor("#FFB6C1") # Light Pink

                for col, text in enumerate(items_to_add):
                    item = QTableWidgetItem(text)
                    item.setBackground(bg_color)
                    if col == 0:
                        item.setData(Qt.UserRole, task) # Stores task data
                    self.tasks_table.setItem(row, col, item)

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить задачи: {e}")

    def on_task_select(self):
        """Обработчик выбора задачи в таблице."""
        current_row = self.tasks_table.currentRow()
        if current_row < 0:
            self.task_management_stack.setCurrentIndex(0)
            return

        item = self.tasks_table.item(current_row, 0)
        if not item:
            self.task_management_stack.setCurrentIndex(0)
            return

        task_data = item.data(Qt.UserRole)
        if not task_data:
            self.task_management_stack.setCurrentIndex(0)
            return
        
        logging.debug(f"AdminWindowQt.on_task_select: task_data retrieved: {task_data}") # DEBUG LOG
            
        # Очищаем контейнер от старого виджета
        editor_container = self.task_management_stack.widget(1)
        layout = editor_container.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # Создаем и добавляем новый фрейм редактора
        editor_frame = TaskEditorFrameQt(self.task_service, task_data, self)
        layout.addWidget(editor_frame)
        
        self.task_management_stack.setCurrentIndex(1)

    def _load_order_statistics(self):
        """Загружает и отображает статистику по активным заказам."""
        # --- ИСПРАВЛЕНИЕ: Определяем, какую таблицу обновлять, и проверяем, нужно ли это делать ---
        current_tab_index = self.orders_tab_widget.currentIndex()
        target_table = self.in_progress_stats_table if current_tab_index == 0 else self.archive_stats_table
        
        # Если выбрана вкладка "Архив", просто очищаем ее таблицу статистики и выходим
        if current_tab_index == 1:
            target_table.setRowCount(0)
            return
            
        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            stats_data = service.get_order_statistics()
            
            target_table.setRowCount(0)
            if not stats_data:
                return

            # --- НОВЫЙ БЛОК: Подсчет итогов ---
            total_positions = 0
            total_dm = 0

            for row_data in stats_data:
                row = target_table.rowCount()
                target_table.insertRow(row)
                
                positions = int(row_data.get('positions_count', 0))
                dm = int(row_data.get('dm_count', 0))
                total_positions += positions
                total_dm += dm

                target_table.setItem(row, 0, QTableWidgetItem(str(row_data.get('post_processing_type', ''))))
                target_table.setItem(row, 1, QTableWidgetItem(str(row_data.get('client_name', ''))))
                target_table.setItem(row, 2, QTableWidgetItem(str(row_data.get('custom_status', ''))))
                target_table.setItem(row, 3, QTableWidgetItem(str(positions)))
                target_table.setItem(row, 4, QTableWidgetItem(str(dm)))

            # Добавляем итоговую строку, если были данные
            if stats_data:
                total_row = target_table.rowCount()
                target_table.insertRow(total_row)
                bold_font = target_table.font(); bold_font.setBold(True)
                
                total_label = QTableWidgetItem("ИТОГО")
                total_label.setFont(bold_font)
                target_table.setItem(total_row, 2, total_label)
                
                total_pos_item = QTableWidgetItem(str(total_positions)); total_pos_item.setFont(bold_font)
                target_table.setItem(total_row, 3, total_pos_item)
                
                total_dm_item = QTableWidgetItem(str(total_dm)); total_dm_item.setFont(bold_font)
                target_table.setItem(total_row, 4, total_dm_item)

        except Exception as e:
            logging.error(f"Ошибка при загрузке статистики заказов: {e}", exc_info=True)
            # Можно добавить label с ошибкой в self.stats_layout, если нужно

    def apply_order_filters(self, is_archive):
        """Фильтрует и отображает заказы на основе значений в полях фильтра."""
        table = self.archive_orders_table if is_archive else self.in_progress_orders_table
        cache = self.archive_orders_cache if is_archive else self.in_progress_orders_cache
        client_filter = self.archive_client_filter if is_archive else self.in_progress_client_filter
        search_filter = self.archive_search_filter if is_archive else self.in_progress_search_filter

        client_query = client_filter.currentText()
        search_query = search_filter.text().lower()

        table.setRowCount(0)
        
        filtered_data = cache
        if client_query and client_query != "Все клиенты":
            filtered_data = [o for o in filtered_data if o.get('client_name') == client_query]

        if search_query:
            filtered_data = [
                o for o in filtered_data
                if search_query in str(o.get('client_name', '')).lower() or
                   search_query in str(o.get('notes', '')).lower() or
                   search_query in str(o.get('status', '')).lower()
            ]

        for order in filtered_data:
            row = table.rowCount()
            table.insertRow(row)

            api_status = order.get('api_status', '')
            status = order.get('status', '')
            bg_color = QColor("white")
            if api_status == 'Отчет подготовлен': bg_color = QColor("#FFB6C6")
            elif api_status == 'Коды скачаны': bg_color = QColor("#90EE90")
            elif api_status == 'Запрос создан': bg_color = QColor("#FFFFE0")
            elif status == 'completed': bg_color = QColor("#B0E0E6")

            items_to_add = [
                str(order['order_date']),
                f"{order['client_name']} / Заказ № {order['id']}",
                # --- ИСПРАВЛЕНИЕ: Добавляем проверку на None для scenario_data ---
                (order.get('scenario_data') or {}).get('post_processing', order['status']),
                # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
                    str(order.get('positions_count', 0)),
                    str(order.get('dm_count', 0)),
                order['notes']
            ]
            for col, text in enumerate(items_to_add):
                item = QTableWidgetItem(text)
                item.setBackground(bg_color)
                if col == 0: item.setData(Qt.UserRole, order)
                table.setItem(row, col, item)

    def _build_notifications_list_page(self):
        """Таблица со списком уведомлений и сводкой по дням."""
        widget = QWidget()
        layout = QVBoxLayout()

        # Кнопки управления
        controls = QHBoxLayout()
        btn_new = QPushButton("Новое уведомление")
        btn_new.clicked.connect(self.create_new_notification) # Этот метод останется для создания
        btn_delete = QPushButton("Удалить уведомление")
        btn_delete.clicked.connect(self.delete_notification)
        btn_refresh = QPushButton("Обновить")
        btn_refresh.clicked.connect(self.load_notifications)
        controls.addWidget(btn_new)
        controls.addWidget(btn_delete)
        controls.addStretch()
        layout.addLayout(controls)

        # --- НОВЫЙ БЛОК: Фильтры для уведомлений ---
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Клиент:"))
        self.notif_client_filter_combo = QComboBox()
        self.notif_client_filter_combo.addItem("Все клиенты")
        self.notif_client_filter_combo.currentIndexChanged.connect(self.apply_notification_filters)
        filter_layout.addWidget(self.notif_client_filter_combo)

        filter_layout.addWidget(QLabel("Поиск:"))
        self.notif_search_filter_edit = QLineEdit()
        self.notif_search_filter_edit.setPlaceholderText("Поиск по сценарию, клиенту, ТС, статусу...")
        self.notif_search_filter_edit.textChanged.connect(self.apply_notification_filters)
        filter_layout.addWidget(self.notif_search_filter_edit)
        
        layout.addLayout(filter_layout)

        # Кэш для хранения всех загруженных уведомлений
        self.all_notifications_cache = []

        # Таблица уведомлений (9 видимых колонок + ID скрытый)
        self.notifications_table = QTableWidget(0, 9)
        self.notifications_table.setHorizontalHeaderLabels([
            "ID", "Сценарий", "Клиент", "Товары", "Дата прибытия", "ТС/Контейнер", "Статус", "Позиций", "Кодов ДМ"
        ])
        
        # Скрываем колонку ID
        self.notifications_table.setColumnHidden(0, True)
        
        self.notifications_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.notifications_table.setSelectionMode(QTableWidget.SingleSelection)
        self.notifications_table.setStyleSheet("""
            QTableWidget::item:selected {
                background-color: #ADD8E6;
            }
        """)
        # Выбор строки загружает детали
        self.notifications_table.itemSelectionChanged.connect(self.on_notification_select)
        layout.addWidget(self.notifications_table)

        # ИСПРАВЛЕНИЕ: Устанавливаем разумную ширину для большинства колонок,
        # а последнюю растягиваем, чтобы занять все свободное место.
        header = self.notifications_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents) # Сценарий
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents) # Клиент
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents) # Товары
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        self.notifications_table.setColumnWidth(4, 110) # Дата прибытия
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.notifications_table.setColumnWidth(5, 120) # ТС/Контейнер
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.notifications_table.setColumnWidth(6, 100) # Статус
        header.setSectionResizeMode(7, QHeaderView.Fixed)
        self.notifications_table.setColumnWidth(7, 70) # Позиций
        header.setSectionResizeMode(8, QHeaderView.Stretch) # Кодов ДМ (растягивается)

        widget.setLayout(layout)
        return widget

    def _build_notifications_page(self):
        """Создает страницу управления уведомлениями в стиле 'список-детали'."""
        widget = QWidget()
        main_layout = QHBoxLayout(widget)
        
        # --- ИЗМЕНЕНИЕ: Создаем вертикальный разделитель для основной области и сводки ---
        main_splitter = QSplitter(Qt.Vertical)

        # --- Верхняя панель: Список и детали ---
        top_widget = QWidget()
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(0,0,0,0)

        # Горизонтальный разделитель для списка (слева) и деталей (справа)
        top_splitter = QSplitter(Qt.Horizontal)

        # Левая панель: Список уведомлений
        left_panel = self._build_notifications_list_page()
        top_splitter.addWidget(left_panel)

        # Правая панель: Детали уведомления
        self.notification_details_stack = QStackedWidget()
        placeholder_widget = QWidget()
        placeholder_layout = QVBoxLayout(placeholder_widget)
        placeholder_label = QLabel("Выберите уведомление для просмотра деталей")
        placeholder_label.setAlignment(Qt.AlignCenter)
        placeholder_layout.addWidget(placeholder_label)
        self.notification_details_stack.addWidget(placeholder_widget) # Индекс 0
        details_widget = self._build_notification_details_page()
        self.notification_details_stack.addWidget(details_widget) # Индекс 1
        top_splitter.addWidget(self.notification_details_stack)
        
        top_splitter.setSizes([800, 400]) # Пропорции для списка и деталей
        top_layout.addWidget(top_splitter)

        # --- Нижняя панель: Сводка по дням ---
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        
        summary_label = QLabel("Сводка по дням:")
        bottom_layout.addWidget(summary_label)

        # --- ИЗМЕНЕНИЕ: Логика создания таблицы сводки перенесена сюда ---
        from datetime import datetime, timedelta
        today = datetime.now().date()
        date_labels = [ (today + timedelta(days=i)).strftime('%d.%m.%Y') for i in range(4) ]

        self.summary_table = QTableWidget(2, 13)
        client_item = QTableWidgetItem("Клиент")
        client_item.setFlags(client_item.flags() & ~Qt.ItemIsEditable)
        self.summary_table.setItem(0, 0, client_item)
        self.summary_table.setSpan(0, 0, 2, 1)

        for i, date in enumerate(date_labels):
            col = 1 + i * 3
            date_item = QTableWidgetItem(date)
            date_item.setFlags(date_item.flags() & ~Qt.ItemIsEditable)
            date_item.setTextAlignment(Qt.AlignCenter)
            self.summary_table.setItem(0, col, date_item)
            self.summary_table.setSpan(0, col, 1, 3)

        for i in range(4):
            col = 1 + i * 3
            for j, metric in enumerate(["Ув", "Поз", "ДМ"]):
                metric_item = QTableWidgetItem(metric)
                metric_item.setFlags(metric_item.flags() & ~Qt.ItemIsEditable)
                metric_item.setTextAlignment(Qt.AlignCenter)
                self.summary_table.setItem(1, col + j, metric_item)

        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.horizontalHeader().setVisible(False)
        self.summary_table.setColumnWidth(0, 200)
        for i in range(1, 13):
            self.summary_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Stretch)
        self.summary_table.setStyleSheet("""
            QTableWidget::item:selected { background-color: #ADD8E6; }
            QTableWidget { gridline-color: #E0E0E0; }
        """)
        bottom_layout.addWidget(self.summary_table)

        # Собираем главный сплиттер
        main_splitter.addWidget(top_widget)
        main_splitter.addWidget(bottom_widget)
        main_splitter.setSizes([600, 200]) # Пропорции для основной области и сводки

        main_layout.addWidget(main_splitter)
        return widget

    def _build_notification_details_page(self):
        """Страница с деталями уведомления."""
        widget = QWidget()
        layout = QVBoxLayout()

        # Основная область с вкладками
        self.notification_details_notebook = self._create_notification_tabs()
        layout.addWidget(self.notification_details_notebook)

        widget.setLayout(layout)
        return widget

    def _create_notification_tabs(self):
        """Создаёт виджет с вкладками для деталей уведомления."""
        from PySide6.QtWidgets import QTabWidget, QTextEdit
        
        tabs = QTabWidget()

        # Вкладка 1: Общая информация
        general_tab = QWidget()
        general_layout = QVBoxLayout()

        # ИСПРАВЛЕНИЕ: Заменяем QHBoxLayout на QFormLayout для компоновки "ключ: значение"
        from PySide6.QtWidgets import QFormLayout
        form_layout = QFormLayout()

        # Создаем и добавляем виджеты парами
        self.notif_scenario_label = QLabel("")
        form_layout.addRow("Сценарий маркировки:", self.notif_scenario_label)

        self.notif_client_label = QLabel("")
        form_layout.addRow("Клиент:", self.notif_client_label)

        self.notif_product_label = QLabel("")
        form_layout.addRow("Товарная группа:", self.notif_product_label)

        self.notif_status_label = QLabel("")
        form_layout.addRow("Статус:", self.notif_status_label)

        self.notif_arrival_date_input = QDateEdit()
        self.notif_arrival_date_input.setCalendarPopup(True)
        self.notif_arrival_date_input.setDisplayFormat("yyyy-MM-dd")
        form_layout.addRow("Планируемая дата прибытия:", self.notif_arrival_date_input)

        self.notif_vehicle_input = QLineEdit()
        form_layout.addRow("Номер контейнера/ТС:", self.notif_vehicle_input)

        self.notif_comments_text = QTextEdit()
        self.notif_comments_text.setMaximumHeight(100)
        form_layout.addRow("Комментарии:", self.notif_comments_text)

        general_layout.addLayout(form_layout)

        # Кнопки действий
        # --- ИСПРАВЛЕНИЕ: Сохраняем ссылку на layout, чтобы иметь к нему доступ позже ---
        self.notification_actions_layout = QHBoxLayout()
        self.btn_save_notification = QPushButton("Сохранить изменения")
        self.btn_save_notification.clicked.connect(self.save_notification_changes)
        # --- ИСПРАВЛЕНИЕ: Создаем кнопку, но пока не добавляем в layout.
        # Она будет добавлена динамически в load_notification_details.
        self.btn_create_order = QPushButton("Создать/Обновить заказ")
        self.btn_create_order.clicked.connect(self.create_order_from_notification)
        self.notification_actions_layout.addWidget(self.btn_save_notification)
        self.notification_actions_layout.addStretch()
        general_layout.addLayout(self.notification_actions_layout)

        general_layout.addStretch()
        general_tab.setLayout(general_layout)
        tabs.addTab(general_tab, "Общая информация")

        # Вкладка 2: Документы
        docs_tab = QWidget()
        docs_layout = QVBoxLayout()
        docs_controls = QHBoxLayout()
        btn_upload_doc = QPushButton("Загрузить")
        btn_upload_doc.clicked.connect(lambda: self.upload_notification_doc())
        btn_download_doc = QPushButton("Скачать")
        btn_download_doc.clicked.connect(lambda: self.download_notification_doc())
        btn_delete_doc = QPushButton("Удалить")
        btn_delete_doc.clicked.connect(lambda: self.delete_notification_doc())
        docs_controls.addWidget(btn_upload_doc)
        docs_controls.addWidget(btn_download_doc)
        docs_controls.addWidget(btn_delete_doc)
        docs_controls.addStretch()
        docs_layout.addLayout(docs_controls)
        # ИСПРАВЛЕНИЕ: Убираем колонку "Размер" и делаем одну колонку на всю ширину
        self.notification_files_table = QTableWidget(0, 1)
        self.notification_files_table.setHorizontalHeaderLabels(["Имя файла"])
        self.notification_files_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.notification_files_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.notification_files_table.setSelectionMode(QTableWidget.SingleSelection)
        # ИСПРАВЛЕНИЕ: Делаем таблицу нередактируемой
        self.notification_files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # ИСПРАВЛЕНИЕ: Добавляем подсветку для выбранной строки
        self.notification_files_table.setStyleSheet("""
            QTableWidget::item:selected { background-color: #ADD8E6; }
        """)
        docs_layout.addWidget(self.notification_files_table)
        docs_tab.setLayout(docs_layout)
        tabs.addTab(docs_tab, "Документы")

        # Вкладка 3: Детализация заказа
        details_tab = QWidget()
        details_layout = QVBoxLayout()
        details_controls = QHBoxLayout()
        btn_download_template = QPushButton("Скачать шаблон")
        btn_download_template.clicked.connect(self.download_order_template)
        btn_upload_details = QPushButton("Загрузить из файла")
        btn_upload_details.clicked.connect(self.upload_order_details)
        btn_save_details = QPushButton("Сохранить детализацию")
        btn_save_details.clicked.connect(self.save_order_details)
        details_controls.addWidget(btn_download_template)
        details_controls.addWidget(btn_upload_details)
        details_controls.addWidget(btn_save_details)
        details_controls.addStretch()
        details_layout.addLayout(details_controls)
        self.order_details_table = QTableWidget(0, 7)
        self.order_details_table.setHorizontalHeaderLabels([
            "ID", "GTIN", "Кол-во", "Агрегация", "Дата производства", "Срок годн. (мес)", "Годен до"
        ])
        # ИСПРАВЛЕНИЕ: Скрываем системную колонку ID
        self.order_details_table.setColumnHidden(0, True)
        self.order_details_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.order_details_table.setSelectionMode(QTableWidget.SingleSelection)
        details_layout.addWidget(self.order_details_table)
        details_tab.setLayout(details_layout)
        tabs.addTab(details_tab, "Детализация заказа")

        return tabs

    def load_notifications(self):
        """Загружает список уведомлений из БД клиента."""
        try:
            # Блокируем сигналы, чтобы не вызывать фильтрацию при каждой смене
            self.notif_client_filter_combo.blockSignals(True)
            self.notif_search_filter_edit.blockSignals(True)

            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            notifications = service.get_notifications_with_counts()
            
            # Сохраняем данные в кэш
            self.all_notifications_cache = notifications

            # Заполняем комбобокс клиентов
            self.notif_client_filter_combo.clear()
            self.notif_client_filter_combo.addItem("Все клиенты")
            client_names = sorted(list(set(n['client_name'] for n in notifications)))
            self.notif_client_filter_combo.addItems(client_names)
            
            # Загружаем сводку
            self.load_summary_data()
        except psycopg2.OperationalError as e:
            # --- ИСПРАВЛЕНИЕ: Перехватываем ошибку соединения с БД, чтобы избежать RuntimeError ---
            # Это предотвращает падение приложения, если get_client_db_connection не может установить соединение
            # и некорректно обрабатывает исключение внутри генератора.
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка соединения", f"Не удалось подключиться к базе данных:\n{e}")
        except (Exception, psycopg2.Error) as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить уведомления: {e}")
        finally:
            # Разблокируем сигналы и применяем фильтры
            self.notif_client_filter_combo.blockSignals(False)
            self.notif_search_filter_edit.blockSignals(False)
            self.apply_notification_filters()

    def apply_notification_filters(self):
        """Фильтрует и отображает уведомления на основе значений в полях фильтра."""
        client_query = self.notif_client_filter_combo.currentText()
        search_query = self.notif_search_filter_edit.text().lower()

        self.notifications_table.setRowCount(0)

        filtered_data = self.all_notifications_cache
        if client_query and client_query != "Все клиенты":
            filtered_data = [n for n in filtered_data if n.get('client_name') == client_query]

        if search_query:
            filtered_data = [
                n for n in filtered_data
                if search_query in str(n.get('scenario_name', '')).lower() or
                   search_query in str(n.get('client_name', '')).lower() or
                   search_query in str(n.get('vehicle_number', '')).lower() or
                   search_query in str(n.get('status', '')).lower()
            ]

        for notif in filtered_data:
            row = self.notifications_table.rowCount()
            self.notifications_table.insertRow(row)
            
            product_groups = notif.get('product_groups', '')
            if isinstance(product_groups, list): product_groups = ', '.join([str(pg.get('name', '')) if isinstance(pg, dict) else str(pg) for pg in product_groups])
            
            items = [str(notif.get('id', '')), notif.get('scenario_name', ''), notif.get('client_name', ''), str(product_groups), str(notif.get('planned_arrival_date', '')), notif.get('vehicle_number', ''), notif.get('status', ''), str(notif.get('positions_count', 0)), str(notif.get('dm_count', 0))]
            
            status = notif.get('status', '')
            bg_color = QColor("white")
            if status == 'Проект': bg_color = QColor("#FFB6C6")
            elif status == 'Ожидание': bg_color = QColor("#FFFFE0")
            elif status == 'Заказ создан': bg_color = QColor("#90EE90")
            
            for col, text in enumerate(items):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                it.setBackground(bg_color)
                self.notifications_table.setItem(row, col, it)

    def load_summary_data(self):
        """Загружает и отображает сводку по дням."""
        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            summary_data = service.get_arrival_summary()

            # ИСПРАВЛЕНИЕ: Очищаем только строки с данными, оставляя заголовки
            while self.summary_table.rowCount() > 2:
                self.summary_table.removeRow(2)

            if not summary_data:
                return

            # --- НОВЫЙ БЛОК: Инициализация словаря для итогов ---
            totals = {f"d{i}_{m}": 0 for i in range(4) for m in ['ув', 'поз', 'дм']}

            for row_data in summary_data:
                row = self.summary_table.rowCount()
                self.summary_table.insertRow(row)

                # Первая колонка - название клиента
                client_name = row_data.get('client_name', row_data.get('client', ''))
                it = QTableWidgetItem(str(client_name))
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.summary_table.setItem(row, 0, it)

                # Остальные колонки - данные по дням (ув, поз, дм)
                col_index = 1
                for i in range(4):
                    day_key = f"d{i}"
                    for metric in ['ув', 'поз', 'дм']:
                        key = f"{day_key}_{metric}"
                        value = row_data.get(key)
                        if value is None: value = 0
                        
                        # --- НОВЫЙ БЛОК: Суммируем значения для итогов ---
                        totals[key] += int(value)

                        it = QTableWidgetItem(str(int(value)))
                        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                        it.setTextAlignment(Qt.AlignCenter)
                        self.summary_table.setItem(row, col_index, it)
                        col_index += 1
            
            # --- НОВЫЙ БЛОК: Добавление итоговой строки ---
            if summary_data:
                total_row_index = self.summary_table.rowCount()
                self.summary_table.insertRow(total_row_index)
                
                # Настройка шрифта и фона для итоговой строки
                bold_font = self.summary_table.font()
                bold_font.setBold(True)
                total_bg_color = QColor("#E0E0E0") # Светло-серый

                # Ячейка "ИТОГО"
                total_label_item = QTableWidgetItem("ИТОГО")
                total_label_item.setFont(bold_font)
                total_label_item.setBackground(total_bg_color)
                self.summary_table.setItem(total_row_index, 0, total_label_item)

                # Заполнение итоговых значений
                total_col_index = 1
                for i in range(4):
                    for metric in ['ув', 'поз', 'дм']:
                        key = f"d{i}_{metric}"
                        total_value_item = QTableWidgetItem(str(totals[key]))
                        total_value_item.setFont(bold_font)
                        total_value_item.setTextAlignment(Qt.AlignCenter)
                        total_value_item.setBackground(total_bg_color)
                        self.summary_table.setItem(total_row_index, total_col_index, total_value_item)
                        total_col_index += 1

        except (Exception, psycopg2.Error) as e:
            traceback.print_exc()
            logging.debug(f"Не удалось загрузить сводку: {e}")

    def create_new_notification(self):
        """Создает новое уведомление о поставке."""
        # ИСПРАВЛЕНИЕ: Заменяем заглушку на вызов диалогового окна
        dialog = NotificationEditorDialog(self, self.user_info)
        # exec() открывает диалог модально и возвращает результат (Accepted или Rejected)
        if dialog.exec():
            # Если диалог был закрыт через "Сохранить", обновляем список
            self.load_notifications()

    def on_notification_select(self):
        """Обработчик выбора уведомления в таблице. Отображает панель управления."""
        sel = self.notifications_table.currentRow()
        if sel < 0:
            self.notification_details_stack.setCurrentIndex(0) # Показываем заглушку
            return
        
        try:
            notif_id = int(self.notifications_table.item(sel, 0).text())
            self.load_notification_details(notif_id)
            # Переключаем правую панель на виджет с деталями
            self.notification_details_stack.setCurrentIndex(1)
        except (ValueError, AttributeError) as e:
            logging.error(f"Ошибка при выборе уведомления: {e}")
            self.notification_details_stack.setCurrentIndex(0)

    def load_notification_details(self, notif_id):
        """Загружает и отображает детали уведомления."""
        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            notif_data = service.get_notification_by_id(notif_id)
            
            if not notif_data:
                QMessageBox.critical(self, "Ошибка", "Не удалось загрузить данные уведомления")
                return
            
            # Сохраняем текущий ID
            self.current_notification_id = notif_id
            # --- ИСПРАВЛЕНИЕ: Сохраняем все данные уведомления для последующего использования ---
            self.current_notification_data = notif_data
            
            # --- ИСПРАВЛЕНИЕ: Динамически добавляем и настраиваем кнопку "Создать/Обновить заказ" ---
            # Удаляем кнопку, если она была добавлена ранее, чтобы избежать дублирования
            if self.btn_create_order.parent() is not None:
                self.btn_create_order.parent().layout().removeWidget(self.btn_create_order)
                self.btn_create_order.setParent(None)

            status = notif_data.get('status', '')

            if status == 'Ожидание':
                self.btn_create_order.setText("Создать заказ")
                self.notification_actions_layout.insertWidget(1, self.btn_create_order) # Добавляем кнопку после "Сохранить"
            elif status == 'Заказ создан':
                self.btn_create_order.setText("Обновить заказ")
                self.notification_actions_layout.insertWidget(1, self.btn_create_order)

            # Заполняем поля
            self.notif_scenario_label.setText(notif_data.get('scenario_name', ''))
            self.notif_client_label.setText(notif_data.get('client_name', ''))
            
            product_groups = notif_data.get('product_groups', '')
            if isinstance(product_groups, list):
                product_groups = ', '.join([str(pg.get('name', '')) if isinstance(pg, dict) else str(pg) for pg in product_groups])
            self.notif_product_label.setText(str(product_groups))
            
            self.notif_status_label.setText(notif_data.get('status', ''))
            # ИСПРАВЛЕНИЕ: Устанавливаем дату в QDateEdit
            arrival_date_str = str(notif_data.get('planned_arrival_date', ''))
            if arrival_date_str:
                self.notif_arrival_date_input.setDate(QDate.fromString(arrival_date_str, "yyyy-MM-dd"))
            else:
                # Если даты нет, ставим сегодняшнюю
                self.notif_arrival_date_input.setDate(QDate.currentDate())
            self.notif_vehicle_input.setText(notif_data.get('vehicle_number', '') or '')
            self.notif_comments_text.setPlainText(notif_data.get('comments', ''))
            
            # Загружаем документы
            self.load_notification_files(notif_id)

            # Загружаем детализацию
            self.load_order_details(notif_id)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить детали уведомления: {e}")

    def load_notification_files(self, notif_id, target_table=None):
        """Загружает список файлов для уведомления."""
        # --- ИЗМЕНЕНИЕ: Принимаем целевую таблицу как аргумент ---
        table = target_table if target_table is not None else self.notification_files_table

        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            files = service.get_notification_files(notif_id)
            
            table.setRowCount(0)
            table.files_cache = files # Сохраняем кэш прямо в виджете таблицы
            
            for file_info in files:
                row = table.rowCount()
                table.insertRow(row)
                
                # ИСПРАВЛЕНИЕ: Заполняем только одну колонку
                filename = file_info.get('filename', '')
                it = QTableWidgetItem(filename)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 0, it)
        except Exception as e:
            traceback.print_exc()

    def load_order_details(self, notif_id):
        """Загружает детализацию заказа для уведомления."""
        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            details = service.get_notification_details(notif_id) if hasattr(service, 'get_notification_details') else []
            
            self.order_details_table.setRowCount(0)
            
            for detail in details:
                row = self.order_details_table.rowCount()
                self.order_details_table.insertRow(row)
                
                items = [
                    str(detail.get('id', '')),
                    detail.get('gtin', ''),
                    str(detail.get('quantity', '')),
                    detail.get('aggregation', ''),
                    str(detail.get('production_date', '')),
                    str(detail.get('shelf_life_months', '')),
                    str(detail.get('expiry_date', ''))
                ]
                
                for col, text in enumerate(items):
                    it = QTableWidgetItem(str(text))
                    # ИСПРАВЛЕНИЕ: Убираем флаг, запрещающий редактирование, чтобы ячейки были изменяемыми
                    self.order_details_table.setItem(row, col, it)
        except Exception as e:
            traceback.print_exc()

    def save_notification_changes(self):
        """Сохраняет изменения уведомления."""
        if not hasattr(self, 'current_notification_id'):
            QMessageBox.warning(self, "Ошибка", "Не выбрано уведомление")
            return
        
        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            data_to_save = {
                # ИСПРАВЛЕНИЕ: Получаем дату из QDateEdit в нужном формате
                'planned_arrival_date': self.notif_arrival_date_input.date().toString("yyyy-MM-dd") or None,
                'vehicle_number': self.notif_vehicle_input.text(),
                'comments': self.notif_comments_text.toPlainText()
            }
            # --- ИСПРАВЛЕНИЕ: Добавляем товарные группы из сохраненных данных ---
            if hasattr(self, 'current_notification_data'):
                data_to_save['product_groups'] = self.current_notification_data.get('product_groups', [])
            service.update_notification(self.current_notification_id, data_to_save)
            QMessageBox.information(self, "Успех", "Изменения сохранены")
            self.load_notifications()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить изменения: {e}")

    def create_order_from_notification(self):
        """Создаёт заказ из уведомления."""
        if not hasattr(self, 'current_notification_id'):
            QMessageBox.warning(self, "Ошибка", "Не выбрано уведомление")
            return
        
        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            # --- ИСПРАВЛЕНИЕ: Полностью переносим логику из Tkinter ---
            success, message, needs_confirmation = service.create_or_recreate_order_from_notification(self.current_notification_id)
            
            if needs_confirmation:
                # Если требуется подтверждение, показываем диалог Да/Нет
                reply = QMessageBox.question(self, "Подтверждение", message, QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    # Если пользователь согласен, вызываем сервис повторно с флагом force_recreate
                    success, message, _ = service.create_or_recreate_order_from_notification(self.current_notification_id, force_recreate=True)
                else:
                    return # Пользователь отменил операцию
            
            if success: # Показываем сообщение только в случае успеха
                QMessageBox.information(self, "Успех", message)
                self.load_notifications() # Обновляем список в любом случае
            else:
                QMessageBox.warning(self, "Внимание", message) # Показываем предупреждение, если не success
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать заказ: {e}")

    def upload_notification_doc(self, notification_id=None):
        """Загружает документ для уведомления."""
        # --- ИЗМЕНЕНИЕ: Определяем ID уведомления ---
        notif_id = notification_id
        if notif_id is None:
            if hasattr(self, 'current_notification_id'):
                notif_id = self.current_notification_id
        
        if not notif_id:
            QMessageBox.warning(self, "Ошибка", "Не выбрано уведомление")
            return
        
        filepath = QFileDialog.getOpenFileName(self, "Выберите файл")[0]
        if not filepath:
            return
        
        try:
            with open(filepath, 'rb') as f:
                file_data = f.read()
            
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            filename = os.path.basename(filepath)
            service.add_notification_file(notif_id, filename, file_data, 'client_document')
            QMessageBox.information(self, "Успех", "Файл успешно загружен")
            
            # Обновляем обе таблицы, если они существуют
            self.load_notification_files(notif_id, self.notification_files_table)
            if hasattr(self, 'in_progress_docs_tab'): # Проверяем, создана ли вкладка
                self.on_order_select(is_archive=False) # Перезагружаем панель управления
            if hasattr(self, 'archive_docs_tab'):
                self.on_order_select(is_archive=True)

        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить файл: {e}")

    def download_notification_doc(self, parent_frame=None):
        """Скачивает документ от уведомления."""
        # --- ИЗМЕНЕНИЕ: Определяем, из какой таблицы скачивать ---
        table = parent_frame.files_table if parent_frame else self.notification_files_table
        sel = table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите файл для скачивания")
            return
        
        try:
            # ИСПРАВЛЕНИЕ: Получаем ID файла из кэша, а не из виджета
            file_info = table.files_cache[sel]
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            content, filename = service.get_file_content(file_info['id'])
            
            save_path, _ = QFileDialog.getSaveFileName(self, "Сохранить файл", filename)
            if save_path:
                with open(save_path, 'wb') as f:
                    f.write(content)
                QMessageBox.information(self, "Успех", f"Файл сохранен в: {save_path}")
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось скачать файл: {e}")

    def delete_notification_doc(self, parent_frame=None):
        """Удаляет выбранный документ уведомления."""
        # --- ИЗМЕНЕНИЕ: Определяем, из какой таблицы удалять ---
        table = parent_frame.files_table if parent_frame else self.notification_files_table
        notif_id = parent_frame.notification_id if parent_frame else self.current_notification_id
        sel = table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите файл для удаления")
            return

        try:
            file_info = table.files_cache[sel]
            file_id = file_info['id']
            filename = file_info['filename']

            reply = QMessageBox.question(self, "Подтверждение", f"Вы уверены, что хотите удалить файл '{filename}'?", QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            service.delete_notification_file(file_id)
            QMessageBox.information(self, "Успех", "Файл успешно удален.")
            # Обновляем список файлов
            self.load_notification_files(notif_id, table)
            # Обновляем и другую таблицу, если нужно
            if table is not self.notification_files_table:
                self.load_notification_files(self.current_notification_id, self.notification_files_table)
            else:
                self.on_order_select(is_archive=self.orders_tab_widget.currentIndex() == 1)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить файл: {e}")

    def _build_catalogs_page(self):
        """Создает страницу для управления справочниками."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        notebook = QTabWidget()
        layout.addWidget(notebook)

        # --- ИСПРАВЛЕНИЕ: Первым добавляем справочник участников из АПИ ---
        self._build_participants_tab(notebook)

        # Остальные справочники
        self._build_local_clients_tab(notebook)
        self._build_product_groups_tab(notebook)
        self._build_products_tab(notebook)
        self._build_scenarios_tab(notebook)

        return widget

    def _build_local_clients_tab(self, parent_notebook):
        """Создает вкладку для управления локальными клиентами."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Панель с кнопками
        controls_layout = QHBoxLayout()
        btn_add = QPushButton("Добавить")
        btn_edit = QPushButton("Редактировать")
        btn_delete = QPushButton("Удалить")
        btn_export = QPushButton("Выгрузить в Excel")
        btn_import = QPushButton("Загрузить из Excel")
        btn_refresh = QPushButton("Обновить")
        controls_layout.addWidget(btn_add)
        controls_layout.addWidget(btn_edit)
        controls_layout.addWidget(btn_delete)
        controls_layout.addStretch()
        controls_layout.addWidget(btn_export)
        controls_layout.addWidget(btn_import)
        controls_layout.addWidget(btn_refresh)
        layout.addLayout(controls_layout)

        # Таблица
        self.local_clients_table = QTableWidget(0, 3)
        self.local_clients_table.setHorizontalHeaderLabels(["ID", "Наименование", "ИНН"])
        self.local_clients_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.local_clients_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.local_clients_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.local_clients_table)
        
        parent_notebook.addTab(tab, "Клиенты (локальные)")

        # Привязка обработчиков
        btn_refresh.clicked.connect(self._refresh_local_clients)
        btn_add.clicked.connect(self._add_local_client)
        btn_edit.clicked.connect(self._edit_local_client)
        self.local_clients_table.doubleClicked.connect(self._edit_local_client)
        btn_delete.clicked.connect(self._delete_local_client)
        btn_export.clicked.connect(self._export_local_clients)
        btn_import.clicked.connect(self._import_local_clients)

        # Загрузка данных при первом открытии
        self._refresh_local_clients()

    def _refresh_local_clients(self):
        """Обновляет данные в таблице локальных клиентов."""
        try:
            self.local_clients_table.setRowCount(0)
            clients = self.catalog_service.get_local_clients()
            for client in clients:
                row = self.local_clients_table.rowCount()
                self.local_clients_table.insertRow(row)
                self.local_clients_table.setItem(row, 0, QTableWidgetItem(str(client['id'])))
                self.local_clients_table.setItem(row, 1, QTableWidgetItem(client['name']))
                self.local_clients_table.setItem(row, 2, QTableWidgetItem(client.get('inn', '')))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить локальных клиентов: {e}")

    def _add_local_client(self):
        """Открывает диалог для добавления нового клиента."""
        name, ok1 = QInputDialog.getText(self, "Новый клиент", "Наименование:")
        if not ok1 or not name: return
        inn, ok2 = QInputDialog.getText(self, "Новый клиент", "ИНН (опционально):")
        if not ok2: return

        try:
            self.catalog_service.upsert_local_client({'name': name, 'inn': inn})
            self._refresh_local_clients()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось добавить клиента: {e}")

    def _edit_local_client(self):
        """Открывает диалог для редактирования выбранного клиента."""
        sel_row = self.local_clients_table.currentRow()
        if sel_row < 0: return

        client_id = self.local_clients_table.item(sel_row, 0).text()
        current_name = self.local_clients_table.item(sel_row, 1).text()
        current_inn = self.local_clients_table.item(sel_row, 2).text()

        name, ok1 = QInputDialog.getText(self, "Редактировать клиента", "Наименование:", text=current_name)
        if not ok1 or not name: return
        inn, ok2 = QInputDialog.getText(self, "Редактировать клиента", "ИНН (опционально):", text=current_inn)
        if not ok2: return

        try:
            self.catalog_service.upsert_local_client({'id': client_id, 'name': name, 'inn': inn})
            self._refresh_local_clients()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось обновить клиента: {e}")

    def _delete_local_client(self):
        """Удаляет выбранного клиента."""
        sel_row = self.local_clients_table.currentRow()
        if sel_row < 0: return

        client_id = self.local_clients_table.item(sel_row, 0).text()
        client_name = self.local_clients_table.item(sel_row, 1).text()

        if QMessageBox.question(self, "Подтверждение", f"Удалить клиента '{client_name}'?") == QMessageBox.Yes:
            try:
                self.catalog_service.delete_local_client(int(client_id))
                self._refresh_local_clients()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось удалить клиента: {e}")

    def _export_local_clients(self):
        """Выгружает справочник локальных клиентов в Excel."""
        try:
            df = self.catalog_service.get_local_clients_template()
            clients = self.catalog_service.get_local_clients()
            if clients:
                df = pd.DataFrame(clients)

            filepath, _ = QFileDialog.getSaveFileName(self, "Выгрузка: Клиенты (локальные)", "local_clients.xlsx", "Excel Files (*.xlsx)")
            if filepath:
                df.to_excel(filepath, index=False)
                QMessageBox.information(self, "Успех", "Справочник выгружен.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось выгрузить файл: {e}")

    def _import_local_clients(self):
        """Импортирует локальных клиентов из Excel."""
        filepath, _ = QFileDialog.getOpenFileName(self, "Импорт: Клиенты (локальные)", "", "Excel Files (*.xlsx *.xls)")
        if not filepath: return
        try:
            df = pd.read_excel(filepath, dtype={'id': str, 'inn': str})
            self.catalog_service.process_local_clients_import(df)
            self._refresh_local_clients()
            QMessageBox.information(self, "Успех", "Данные успешно импортированы.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка импорта: {e}")

    def _build_participants_tab(self, parent_notebook):
        """Создает вкладку для справочника участников из API (только чтение)."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Панель с кнопками
        controls_layout = QHBoxLayout()
        btn_refresh = QPushButton("Обновить")
        controls_layout.addWidget(btn_refresh)
        controls_layout.addStretch()
        layout.addLayout(controls_layout)

        # Таблица
        self.participants_table = QTableWidget(0, 3)
        self.participants_table.setHorizontalHeaderLabels(["Наименование", "ИНН", "Окончание доверенности"])
        self.participants_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.participants_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.participants_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.participants_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.participants_table)
        
        parent_notebook.addTab(tab, "Участники (из АПИ)")

        def refresh_data():
            try:
                self.participants_table.setRowCount(0)
                participants = self.catalog_service.get_participants_catalog()
                for p in participants:
                    row = self.participants_table.rowCount()
                    self.participants_table.insertRow(row)
                    
                    poa_end_date_str = p.get('poa_validity_end', '')
                    if poa_end_date_str and 'T' in poa_end_date_str:
                        poa_end_date_str = poa_end_date_str.split('T')[0]

                    # Сначала создаем и вставляем все элементы
                    item_name = QTableWidgetItem(p.get('name', ''))
                    item_inn = QTableWidgetItem(p.get('inn', ''))
                    item_date = QTableWidgetItem(poa_end_date_str)
                    
                    self.participants_table.setItem(row, 0, item_name)
                    self.participants_table.setItem(row, 1, item_inn)
                    self.participants_table.setItem(row, 2, item_date)

                    # Логика подсветки
                    if poa_end_date_str:
                        expiry_date = QDate.fromString(poa_end_date_str, "yyyy-MM-dd")
                        if expiry_date.isValid():
                            today = QDate.currentDate()
                            days_left = today.daysTo(expiry_date)
                            if 0 <= days_left < 30:
                                pale_pink = QColor("#FFB6C1") # LightPink
                                for col in range(self.participants_table.columnCount()):
                                    self.participants_table.item(row, col).setBackground(pale_pink)
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить участников из АПИ: {e}")

        btn_refresh.clicked.connect(refresh_data)
        refresh_data()

    def _build_product_groups_tab(self, parent_notebook):
        """Создает вкладку для управления товарными группами."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Панель кнопок
        controls_layout = QHBoxLayout()
        btn_add = QPushButton("Добавить")
        btn_edit = QPushButton("Редактировать")
        btn_delete = QPushButton("Удалить")
        btn_export = QPushButton("Выгрузить в Excel")
        btn_import = QPushButton("Загрузить из Excel")
        btn_refresh = QPushButton("Обновить")
        controls_layout.addWidget(btn_add)
        controls_layout.addWidget(btn_edit)
        controls_layout.addWidget(btn_delete)
        controls_layout.addStretch()
        controls_layout.addWidget(btn_export)
        controls_layout.addWidget(btn_import)
        controls_layout.addWidget(btn_refresh)
        layout.addLayout(controls_layout)

        # Таблица
        self.product_groups_table = QTableWidget(0, 6)
        self.product_groups_table.setHorizontalHeaderLabels(["ID", "Системное имя", "Отображаемое имя", "Нужен ФИАС", "Шаблон кода", "Шаблон ДМ"])
        self.product_groups_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.product_groups_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.product_groups_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.product_groups_table)
        
        parent_notebook.addTab(tab, "Товарные группы")

        # Привязка обработчиков
        btn_refresh.clicked.connect(self._refresh_product_groups)
        btn_add.clicked.connect(self._add_product_group)
        btn_edit.clicked.connect(self._edit_product_group)
        self.product_groups_table.doubleClicked.connect(self._edit_product_group)
        btn_delete.clicked.connect(self._delete_product_group)
        btn_export.clicked.connect(self._export_product_groups)
        btn_import.clicked.connect(self._import_product_groups)

        # Загрузка данных при первом открытии
        self._refresh_product_groups()

    def _refresh_product_groups(self):
        """Обновляет данные в таблице товарных групп."""
        try:
            self.product_groups_table.setRowCount(0)
            groups = self.catalog_service.get_product_groups()
            for group in groups:
                row = self.product_groups_table.rowCount()
                self.product_groups_table.insertRow(row)
                self.product_groups_table.setItem(row, 0, QTableWidgetItem(str(group['id'])))
                self.product_groups_table.setItem(row, 1, QTableWidgetItem(group.get('group_name', '')))
                self.product_groups_table.setItem(row, 2, QTableWidgetItem(group.get('display_name', '')))
                self.product_groups_table.setItem(row, 3, QTableWidgetItem(str(group.get('fias_required', False))))
                self.product_groups_table.setItem(row, 4, QTableWidgetItem(group.get('code_template', '')))
                self.product_groups_table.setItem(row, 5, QTableWidgetItem(group.get('dm_template', '')))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить товарные группы: {e}")

    def _open_product_group_editor(self, group_data=None):
        """Открывает универсальный диалог для редактирования товарной группы."""
        is_new = group_data is None
        group_data = group_data or {}
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Редактор товарной группы")
        layout = QVBoxLayout(dialog)
        form_layout = QFormLayout()

        # Создаем поля ввода
        fields = {
            'group_name': QLineEdit(group_data.get('group_name', '')),
            'display_name': QLineEdit(group_data.get('display_name', '')),
            'fias_required': QCheckBox(),
            'code_template': QLineEdit(group_data.get('code_template', '')),
            'dm_template': QLineEdit(group_data.get('dm_template', ''))
        }
        fields['fias_required'].setChecked(bool(group_data.get('fias_required', False)))

        form_layout.addRow("Системное имя:", fields['group_name'])
        form_layout.addRow("Отображаемое имя:", fields['display_name'])
        form_layout.addRow("Нужен ФИАС:", fields['fias_required'])
        form_layout.addRow("Шаблон кода:", fields['code_template'])
        form_layout.addRow("Шаблон ДМ:", fields['dm_template'])
        
        layout.addLayout(form_layout)

        # Кнопки
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec():
            try:
                result = {key: widget.text() if isinstance(widget, QLineEdit) else widget.isChecked() for key, widget in fields.items()}
                if not is_new:
                    result['id'] = group_data['id']
                
                self.catalog_service.upsert_product_group(result)
                self._refresh_product_groups()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить товарную группу: {e}")

    def _add_product_group(self):
        self._open_product_group_editor()

    def _edit_product_group(self):
        sel_row = self.product_groups_table.currentRow()
        if sel_row < 0: return
        
        group_data = {
            'id': int(self.product_groups_table.item(sel_row, 0).text()),
            'group_name': self.product_groups_table.item(sel_row, 1).text(),
            'display_name': self.product_groups_table.item(sel_row, 2).text(),
            'fias_required': self.product_groups_table.item(sel_row, 3).text().lower() == 'true',
            'code_template': self.product_groups_table.item(sel_row, 4).text(),
            'dm_template': self.product_groups_table.item(sel_row, 5).text()
        }
        self._open_product_group_editor(group_data)

    def _delete_product_group(self):
        sel_row = self.product_groups_table.currentRow()
        if sel_row < 0: return

        group_id = int(self.product_groups_table.item(sel_row, 0).text())
        group_name = self.product_groups_table.item(sel_row, 2).text()

        if QMessageBox.question(self, "Подтверждение", f"Удалить товарную группу '{group_name}'?") == QMessageBox.Yes:
            try:
                self.catalog_service.delete_product_group(group_id)
                self._refresh_product_groups()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось удалить товарную группу: {e}")

    def _export_product_groups(self):
        try:
            df = self.catalog_service.get_product_groups_template()
            groups = self.catalog_service.get_product_groups()
            if groups:
                df = pd.DataFrame(groups)

            filepath, _ = QFileDialog.getSaveFileName(self, "Выгрузка: Товарные группы", "product_groups.xlsx", "Excel Files (*.xlsx)")
            if filepath:
                df.to_excel(filepath, index=False)
                QMessageBox.information(self, "Успех", "Справочник выгружен.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось выгрузить файл: {e}")

    def _import_product_groups(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Импорт: Товарные группы", "", "Excel Files (*.xlsx *.xls)")
        if not filepath: return
        try:
            df = pd.read_excel(filepath, dtype={'id': str})
            self.catalog_service.process_product_groups_import(df)
            self._refresh_product_groups()
            QMessageBox.information(self, "Успех", "Данные успешно импортированы.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка импорта: {e}")

    def _build_product_groups_tab(self, parent_notebook):
        """Создает вкладку для управления товарными группами."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Панель кнопок
        controls_layout = QHBoxLayout()
        btn_add = QPushButton("Добавить")
        btn_edit = QPushButton("Редактировать")
        btn_delete = QPushButton("Удалить")
        btn_export = QPushButton("Выгрузить в Excel")
        btn_import = QPushButton("Загрузить из Excel")
        btn_refresh = QPushButton("Обновить")
        controls_layout.addWidget(btn_add)
        controls_layout.addWidget(btn_edit)
        controls_layout.addWidget(btn_delete)
        controls_layout.addStretch()
        controls_layout.addWidget(btn_export)
        controls_layout.addWidget(btn_import)
        controls_layout.addWidget(btn_refresh)
        layout.addLayout(controls_layout)

        # Таблица
        self.product_groups_table = QTableWidget(0, 6)
        self.product_groups_table.setHorizontalHeaderLabels(["ID", "Системное имя", "Отображаемое имя", "Нужен ФИАС", "Шаблон кода", "Шаблон ДМ"])
        self.product_groups_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.product_groups_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.product_groups_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.product_groups_table)
        
        parent_notebook.addTab(tab, "Товарные группы")

        # Привязка обработчиков
        btn_refresh.clicked.connect(self._refresh_product_groups)
        btn_add.clicked.connect(self._add_product_group)
        btn_edit.clicked.connect(self._edit_product_group)
        self.product_groups_table.doubleClicked.connect(self._edit_product_group)
        btn_delete.clicked.connect(self._delete_product_group)
        btn_export.clicked.connect(self._export_product_groups)
        btn_import.clicked.connect(self._import_product_groups)

        # Загрузка данных при первом открытии
        self._refresh_product_groups()

    def _refresh_product_groups(self):
        """Обновляет данные в таблице товарных групп."""
        try:
            self.product_groups_table.setRowCount(0)
            groups = self.catalog_service.get_product_groups()
            for group in groups:
                row = self.product_groups_table.rowCount()
                self.product_groups_table.insertRow(row)
                self.product_groups_table.setItem(row, 0, QTableWidgetItem(str(group['id'])))
                self.product_groups_table.setItem(row, 1, QTableWidgetItem(group.get('group_name', '')))
                self.product_groups_table.setItem(row, 2, QTableWidgetItem(group.get('display_name', '')))
                self.product_groups_table.setItem(row, 3, QTableWidgetItem(str(group.get('fias_required', False))))
                self.product_groups_table.setItem(row, 4, QTableWidgetItem(group.get('code_template', '')))
                self.product_groups_table.setItem(row, 5, QTableWidgetItem(group.get('dm_template', '')))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить товарные группы: {e}")

    def _open_product_group_editor(self, group_data=None):
        """Открывает универсальный диалог для редактирования товарной группы."""
        is_new = group_data is None
        group_data = group_data or {}
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Редактор товарной группы")
        layout = QVBoxLayout(dialog)
        form_layout = QFormLayout()

        # Создаем поля ввода
        fields = {
            'group_name': QLineEdit(group_data.get('group_name', '')),
            'display_name': QLineEdit(group_data.get('display_name', '')),
            'fias_required': QCheckBox(),
            'code_template': QLineEdit(group_data.get('code_template', '')),
            'dm_template': QLineEdit(group_data.get('dm_template', ''))
        }
        fields['fias_required'].setChecked(bool(group_data.get('fias_required', False)))

        form_layout.addRow("Системное имя:", fields['group_name'])
        form_layout.addRow("Отображаемое имя:", fields['display_name'])
        form_layout.addRow("Нужен ФИАС:", fields['fias_required'])
        form_layout.addRow("Шаблон кода:", fields['code_template'])
        form_layout.addRow("Шаблон ДМ:", fields['dm_template'])
        
        layout.addLayout(form_layout)

        # Кнопки
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec():
            try:
                result = {key: widget.text() if isinstance(widget, QLineEdit) else widget.isChecked() for key, widget in fields.items()}
                if not is_new:
                    result['id'] = group_data['id']
                
                self.catalog_service.upsert_product_group(result)
                self._refresh_product_groups()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить товарную группу: {e}")

    def _add_product_group(self):
        self._open_product_group_editor()

    def _edit_product_group(self):
        sel_row = self.product_groups_table.currentRow()
        if sel_row < 0: return
        
        group_data = {
            'id': int(self.product_groups_table.item(sel_row, 0).text()),
            'group_name': self.product_groups_table.item(sel_row, 1).text(),
            'display_name': self.product_groups_table.item(sel_row, 2).text(),
            'fias_required': self.product_groups_table.item(sel_row, 3).text().lower() == 'true',
            'code_template': self.product_groups_table.item(sel_row, 4).text(),
            'dm_template': self.product_groups_table.item(sel_row, 5).text()
        }
        self._open_product_group_editor(group_data)

    def _delete_product_group(self):
        sel_row = self.product_groups_table.currentRow()
        if sel_row < 0: return

        group_id = int(self.product_groups_table.item(sel_row, 0).text())
        group_name = self.product_groups_table.item(sel_row, 2).text()

        if QMessageBox.question(self, "Подтверждение", f"Удалить товарную группу '{group_name}'?") == QMessageBox.Yes:
            try:
                self.catalog_service.delete_product_group(group_id)
                self._refresh_product_groups()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось удалить товарную группу: {e}")

    def _export_product_groups(self):
        try:
            df = self.catalog_service.get_product_groups_template()
            groups = self.catalog_service.get_product_groups()
            if groups:
                df = pd.DataFrame(groups)

            filepath, _ = QFileDialog.getSaveFileName(self, "Выгрузка: Товарные группы", "product_groups.xlsx", "Excel Files (*.xlsx)")
            if filepath:
                df.to_excel(filepath, index=False)
                QMessageBox.information(self, "Успех", "Справочник выгружен.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось выгрузить файл: {e}")

    def _import_product_groups(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Импорт: Товарные группы", "", "Excel Files (*.xlsx *.xls)")
        if not filepath: return
        try:
            df = pd.read_excel(filepath, dtype={'id': str})
            self.catalog_service.process_product_groups_import(df)
            self._refresh_product_groups()
            QMessageBox.information(self, "Успех", "Данные успешно импортированы.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка импорта: {e}")

    def _build_products_tab(self, parent_notebook):
        """Создает вкладку для управления справочником товаров."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Панель кнопок
        controls_layout = QHBoxLayout()
        btn_add = QPushButton("Добавить")
        btn_edit = QPushButton("Редактировать")
        btn_delete = QPushButton("Удалить")
        btn_export = QPushButton("Выгрузить в Excel")
        btn_import = QPushButton("Загрузить из Excel")
        btn_refresh = QPushButton("Обновить")
        controls_layout.addWidget(btn_add)
        controls_layout.addWidget(btn_edit)
        controls_layout.addWidget(btn_delete)
        controls_layout.addStretch()
        controls_layout.addWidget(btn_export)
        controls_layout.addWidget(btn_import)
        controls_layout.addWidget(btn_refresh)
        layout.addLayout(controls_layout)

        # Таблица
        self.products_table = QTableWidget(0, 5)
        self.products_table.setHorizontalHeaderLabels(["GTIN", "Наименование", "Описание 1", "Описание 2", "Описание 3"])
        self.products_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.products_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.products_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.products_table)
        
        parent_notebook.addTab(tab, "Товары")

        # Привязка обработчиков
        btn_refresh.clicked.connect(self._refresh_products)
        btn_add.clicked.connect(self._add_product)
        btn_edit.clicked.connect(self._edit_product)
        self.products_table.doubleClicked.connect(self._edit_product)
        btn_delete.clicked.connect(self._delete_product)
        btn_export.clicked.connect(self._export_products)
        btn_import.clicked.connect(self._import_products)

        # Загрузка данных при первом открытии
        self._refresh_products()

    def _refresh_products(self):
        """Обновляет данные в таблице товаров."""
        try:
            self.products_table.setRowCount(0)
            products = self.catalog_service.get_products()
            for prod in products:
                row = self.products_table.rowCount()
                self.products_table.insertRow(row)
                self.products_table.setItem(row, 0, QTableWidgetItem(prod.get('gtin', '')))
                self.products_table.setItem(row, 1, QTableWidgetItem(prod.get('name', '')))
                self.products_table.setItem(row, 2, QTableWidgetItem(prod.get('description_1', '')))
                self.products_table.setItem(row, 3, QTableWidgetItem(prod.get('description_2', '')))
                self.products_table.setItem(row, 4, QTableWidgetItem(prod.get('description_3', '')))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить товары: {e}")

    def _open_product_editor(self, product_data=None):
        """Открывает диалог для редактирования товара."""
        is_new = product_data is None
        product_data = product_data or {}
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Редактор товара")
        layout = QVBoxLayout(dialog)
        form_layout = QFormLayout()

        fields = {
            'gtin': QLineEdit(product_data.get('gtin', '')),
            'name': QLineEdit(product_data.get('name', '')),
            'description_1': QLineEdit(product_data.get('description_1', '')),
            'description_2': QLineEdit(product_data.get('description_2', '')),
            'description_3': QLineEdit(product_data.get('description_3', ''))
        }

        if not is_new:
            fields['gtin'].setReadOnly(True)

        form_layout.addRow("GTIN:", fields['gtin'])
        form_layout.addRow("Наименование:", fields['name'])
        form_layout.addRow("Описание 1:", fields['description_1'])
        form_layout.addRow("Описание 2:", fields['description_2'])
        form_layout.addRow("Описание 3:", fields['description_3'])
        
        layout.addLayout(form_layout)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec():
            try:
                result = {key: widget.text() for key, widget in fields.items()}
                if not result.get('gtin') or not result.get('name'):
                    raise ValueError("GTIN и Наименование являются обязательными полями.")
                
                self.catalog_service.upsert_product(result)
                self._refresh_products()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить товар: {e}")

    def _add_product(self):
        self._open_product_editor()

    def _edit_product(self):
        sel_row = self.products_table.currentRow()
        if sel_row < 0: return
        
        product_data = {
            'gtin': self.products_table.item(sel_row, 0).text(),
            'name': self.products_table.item(sel_row, 1).text(),
            'description_1': self.products_table.item(sel_row, 2).text(),
            'description_2': self.products_table.item(sel_row, 3).text(),
            'description_3': self.products_table.item(sel_row, 4).text()
        }
        self._open_product_editor(product_data)

    def _delete_product(self):
        sel_row = self.products_table.currentRow()
        if sel_row < 0: return

        gtin = self.products_table.item(sel_row, 0).text()
        if QMessageBox.question(self, "Подтверждение", f"Удалить товар с GTIN '{gtin}'?") == QMessageBox.Yes:
            try:
                self.catalog_service.delete_product(gtin)
                self._refresh_products()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось удалить товар: {e}")

    def _export_products(self):
        try:
            df = self.catalog_service.get_products_template()
            products = self.catalog_service.get_products()
            if products:
                df = pd.DataFrame(products)

            filepath, _ = QFileDialog.getSaveFileName(self, "Выгрузка: Товары", "products.xlsx", "Excel Files (*.xlsx)")
            if filepath:
                df.to_excel(filepath, index=False)
                QMessageBox.information(self, "Успех", "Справочник 'Товары' выгружен.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось выгрузить файл: {e}")

    def _import_products(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Импорт: Товары", "", "Excel Files (*.xlsx *.xls)")
        if not filepath: return
        try:
            df = pd.read_excel(filepath, dtype={'gtin': str})
            self.catalog_service.process_products_import(df)
            self._refresh_products()
            QMessageBox.information(self, "Успех", "Данные успешно импортированы.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка импорта: {e}")

    def _build_scenarios_tab(self, parent_notebook):
        """Создает вкладку для управления сценариями маркировки."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Панель кнопок
        controls_layout = QHBoxLayout()
        btn_add = QPushButton("Добавить")
        btn_edit = QPushButton("Редактировать")
        btn_delete = QPushButton("Удалить")
        btn_export = QPushButton("Выгрузить в Excel")
        btn_import = QPushButton("Загрузить из Excel")
        btn_refresh = QPushButton("Обновить")
        controls_layout.addWidget(btn_add)
        controls_layout.addWidget(btn_edit)
        controls_layout.addWidget(btn_delete)
        controls_layout.addStretch()
        controls_layout.addWidget(btn_export)
        controls_layout.addWidget(btn_import)
        controls_layout.addWidget(btn_refresh)
        layout.addLayout(controls_layout)

        # Таблица
        self.scenarios_table = QTableWidget(0, 3)
        self.scenarios_table.setHorizontalHeaderLabels(["ID", "Название сценария", "Параметры (JSON)"])
        self.scenarios_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.scenarios_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.scenarios_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.scenarios_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.scenarios_table)
        
        parent_notebook.addTab(tab, "Сценарии маркировки")

        # Привязка обработчиков
        btn_refresh.clicked.connect(self._refresh_scenarios)
        btn_add.clicked.connect(self._add_scenario)
        btn_edit.clicked.connect(self._edit_scenario)
        self.scenarios_table.doubleClicked.connect(self._edit_scenario)
        btn_delete.clicked.connect(self._delete_scenario)
        btn_export.clicked.connect(self._export_scenarios)
        btn_import.clicked.connect(self._import_scenarios)

        # Загрузка данных при первом открытии
        self._refresh_scenarios()

    def _refresh_scenarios(self):
        """Обновляет данные в таблице сценариев."""
        try:
            self.scenarios_table.setRowCount(0)
            scenarios = self.catalog_service.get_marking_scenarios()
            for s in scenarios:
                row = self.scenarios_table.rowCount()
                self.scenarios_table.insertRow(row)
                self.scenarios_table.setItem(row, 0, QTableWidgetItem(str(s['id'])))
                self.scenarios_table.setItem(row, 1, QTableWidgetItem(s.get('name', '')))
                
                # --- ИСПРАВЛЕНИЕ: Отображаем JSON как одну строку без форматирования ---
                scenario_data_str = json.dumps(s.get('scenario_data', {}), ensure_ascii=False)
                self.scenarios_table.setItem(row, 2, QTableWidgetItem(scenario_data_str))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить сценарии: {e}")

    def _open_scenario_editor(self, scenario_data=None):
        """Открывает диалог для редактирования сценария."""
        dialog = ScenarioEditorDialog(self, scenario_data)
        if dialog.exec():
            try:
                self.catalog_service.upsert_marking_scenario(dialog.result)
                self._refresh_scenarios()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить сценарий: {e}")

    def _add_scenario(self):
        self._open_scenario_editor()

    def _edit_scenario(self):
        sel_row = self.scenarios_table.currentRow()
        if sel_row < 0: return
        
        try:
            scenario_data = {
                'id': int(self.scenarios_table.item(sel_row, 0).text()),
                'name': self.scenarios_table.item(sel_row, 1).text(),
                'scenario_data': json.loads(self.scenarios_table.item(sel_row, 2).text())
            }
            self._open_scenario_editor(scenario_data)
        except (json.JSONDecodeError, ValueError) as e:
            QMessageBox.critical(self, "Ошибка данных", f"Не удалось прочитать параметры сценария: {e}")

    def _delete_scenario(self):
        sel_row = self.scenarios_table.currentRow()
        if sel_row < 0: return

        scenario_id = int(self.scenarios_table.item(sel_row, 0).text())
        scenario_name = self.scenarios_table.item(sel_row, 1).text()

        if QMessageBox.question(self, "Подтверждение", f"Удалить сценарий '{scenario_name}'?") == QMessageBox.Yes:
            try:
                self.catalog_service.delete_marking_scenario(scenario_id)
                self._refresh_scenarios()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось удалить сценарий: {e}")

    def _export_scenarios(self):
        # Эта функция потребует доработки для корректной выгрузки JSON
        QMessageBox.information(self, "В разработке", "Экспорт сценариев в разработке.")

    def _import_scenarios(self):
        # Эта функция потребует доработки для корректной загрузки JSON
        QMessageBox.information(self, "В разработке", "Импорт сценариев в разработке.")

    def _build_print_management_page(self):
        """Создает страницу для управления макетами печати."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        controls_layout = QHBoxLayout()
        btn_create = QPushButton("Создать макет")
        btn_create.clicked.connect(self._create_new_layout)
        btn_edit = QPushButton("Редактировать макет")
        btn_edit.clicked.connect(self._edit_selected_layout)
        btn_delete = QPushButton("Удалить макет")
        btn_delete.clicked.connect(self._delete_selected_layout)
        btn_refresh = QPushButton("Обновить")
        btn_refresh.clicked.connect(self._refresh_print_layouts)
        
        controls_layout.addWidget(btn_create)
        controls_layout.addWidget(btn_edit)
        controls_layout.addWidget(btn_delete)
        controls_layout.addStretch()
        controls_layout.addWidget(btn_refresh)
        layout.addLayout(controls_layout)

        self.print_layouts_table = QTableWidget(0, 2)
        self.print_layouts_table.setHorizontalHeaderLabels(["Название макета", "Размер (мм)"])
        self.print_layouts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.print_layouts_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.print_layouts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.print_layouts_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.print_layouts_table.doubleClicked.connect(self._edit_selected_layout)
        layout.addWidget(self.print_layouts_table)

        # Первая загрузка данных
        self._refresh_print_layouts()

        return widget

    def _refresh_print_layouts(self):
        """Загружает список макетов в таблицу."""
        logging.debug("Starting refresh of print layouts.")
        try:
            self.print_layouts_table.setRowCount(0)
            layouts = self.catalog_service.get_print_layouts()
            logging.debug(f"Retrieved {len(layouts)} layouts from catalog_service.")
            self.print_layouts_table.setRowCount(len(layouts))
            for i, layout_data in enumerate(layouts):
                name = layout_data.get('name', '')
                size_str = f"{layout_data.get('width_mm', '?')} x {layout_data.get('height_mm', '?')}"
                
                item_name = QTableWidgetItem(name)
                # Сохраняем все данные макета в элементе таблицы
                item_name.setData(Qt.UserRole, layout_data) 
                
                self.print_layouts_table.setItem(i, 0, item_name)
                self.print_layouts_table.setItem(i, 1, QTableWidgetItem(size_str))

        except Exception as e:
            logging.error(f"Failed to load print layouts: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить макеты печати: {e}")

    def _create_new_layout(self):
        """Открывает диалог для создания нового макета."""
        name, ok = QInputDialog.getText(self, "Новый макет", "Введите название макета:")
        if not ok or not name:
            return

        size_str, ok = QInputDialog.getText(self, "Размеры макета", "Введите размеры этикетки (Ширина x Высота) в мм:", text="100 x 50")
        if not ok or not size_str:
            return

        try:
            width_str, height_str = size_str.lower().split('x')
            width_mm = int(width_str.strip())
            height_mm = int(height_str.strip())
        except (ValueError, IndexError):
            QMessageBox.showerror("Ошибка", "Неверный формат. Введите размеры в формате '100 x 50'.")
            return
            
        new_layout_data = {
            "name": name,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "objects": []
        }
        self._open_layout_editor(new_layout_data)

    def _edit_selected_layout(self):
        """Открывает редактор для выбранного макета."""
        selected_items = self.print_layouts_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Внимание", "Выберите макет для редактирования.")
            return
        
        layout_data = selected_items[0].data(Qt.UserRole)
        self._open_layout_editor(layout_data)

    def _open_layout_editor(self, layout_data):
        """Открывает диалог редактора макетов."""
        if not layout_data:
            return
        # Создаем глубокую копию, чтобы изменения в диалоге не затрагивали данные в таблице до сохранения
        data_copy = json.loads(json.dumps(layout_data))
        dialog = LabelEditorDialog(self, self.user_info, self.catalog_service, data_copy)
        if dialog.exec():
            self._refresh_print_layouts()

    def _delete_selected_layout(self):
        """Удаляет выбранный макет."""
        selected_items = self.print_layouts_table.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Внимание", "Выберите макет для удаления.")
            return

        layout_data = selected_items[0].data(Qt.UserRole)
        layout_name = layout_data.get('name')

        if QMessageBox.question(self, "Подтверждение", f"Вы уверены, что хотите удалить макет '{layout_name}'?") == QMessageBox.Yes:
            try:
                self.catalog_service.delete_print_layout(layout_name)
                self._refresh_print_layouts()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось удалить макет: {e}")

    def download_order_template(self):
        """Скачивает шаблон для детализации заказа."""
        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            df = service.get_formalization_template()

            save_path, _ = QFileDialog.getSaveFileName(self, "Сохранить шаблон", "template_details.xlsx", "Excel Files (*.xlsx)")

            if save_path:
                df.to_excel(save_path, index=False)
                QMessageBox.information(self, "Успех", f"Шаблон успешно сохранен в: {save_path}")
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось скачать шаблон: {e}")

    def upload_order_details(self):
        """Загружает детализацию заказа из Excel-файла."""
        if not hasattr(self, 'current_notification_id'):
            QMessageBox.warning(self, "Внимание", "Сначала выберите уведомление.")
            return

        reply = QMessageBox.question(self, "Подтверждение", "Загрузка из файла полностью заменит текущую детализацию. Продолжить?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        filepath, _ = QFileDialog.getOpenFileName(self, "Выберите Excel-файл", "", "Excel Files (*.xlsx *.xls)")
        if not filepath:
            return

        try:
            with open(filepath, 'rb') as f:
                file_data = f.read()
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            rows_processed = service.process_formalized_file(self.current_notification_id, file_data)
            self.load_order_details(self.current_notification_id) # Обновляем таблицу
            QMessageBox.information(self, "Успех", f"Файл успешно обработан. Загружено {rows_processed} строк.")
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось обработать файл: {e}")

    def save_order_details(self):
        """Сохраняет детализацию заказа."""
        if not hasattr(self, 'current_notification_id'):
            QMessageBox.warning(self, "Внимание", "Не выбрано уведомление для сохранения.")
            return

        details_to_save = []
        try:
            for row in range(self.order_details_table.rowCount()):
                # Функция для безопасного получения текста из ячейки
                def get_item_text(r, c):
                    item = self.order_details_table.item(r, c)
                    return item.text().strip() if item else ""

                # Функция для безопасного преобразования в int или None
                def to_int_or_none(value_str):
                    return int(value_str) if value_str.isdigit() else None

                # Собираем данные из строки
                row_data = (
                    int(get_item_text(row, 0)),  # id
                    get_item_text(row, 1) or None,  # gtin
                    to_int_or_none(get_item_text(row, 2)),  # quantity
                    to_int_or_none(get_item_text(row, 3)),  # aggregation
                    get_item_text(row, 4) or None,  # production_date
                    to_int_or_none(get_item_text(row, 5)),  # shelf_life_months
                    get_item_text(row, 6) or None,  # expiry_date
                )
                details_to_save.append(row_data)

            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            service.save_notification_details(details_to_save)
            QMessageBox.information(self, "Успех", "Изменения в детализации успешно сохранены.")
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить детализацию: {e}")

    def delete_notification(self):
        """Удаляет выбранное уведомление."""
        sel = self.notifications_table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите уведомление для удаления")
            return
        notif_id = int(self.notifications_table.item(sel, 0).text())
        reply = QMessageBox.question(self, "Подтверждение", f"Удалить уведомление #{notif_id}?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            service.delete_notification(notif_id)
            QMessageBox.information(self, "Успех", "Уведомление удалено")
            self.load_notifications()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить уведомление: {e}")

    # --- ИСПРАВЛЕНИЕ: Перемещаем все недостающие методы внутрь класса AdminWindowQt ---

    def _save_config_files(self):
        """Сохраняет файлы config.ini и cert.pem в выбранную пользователем папку."""
        save_path = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения файлов конфигурации")
        if not save_path:
            return

        try:
            with get_client_db_connection(self.user_info) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT setting_key, setting_value FROM ap_settings WHERE setting_key IN ('LOCAL_SERVER_ADDRESS', 'LOCAL_SERVER_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD')")
                    settings_from_db = {row['setting_key']: row['setting_value'] for row in cur.fetchall()}

            ssl_cert_content = self.user_info.get('client_db_config', {}).get('db_ssl_cert', '')

            def xor_cipher(data, key):
                return bytes([ord(c) ^ ord(k) for c, k in zip(data, key * (len(data) // len(key) + 1))])

            encryption_key = "TildaKodSecretKey"
            encrypted_bytes = xor_cipher(settings_from_db['DB_PASSWORD'], encryption_key)
            encrypted_password_b64 = base64.b64encode(encrypted_bytes).decode('ascii')

            ini_content = f"[database]\nhost = {settings_from_db['LOCAL_SERVER_ADDRESS']}\nport = {settings_from_db['LOCAL_SERVER_PORT']}\ndbname = {settings_from_db['DB_NAME']}\nuser = {settings_from_db['DB_USER']}\npassword = {encrypted_password_b64}"

            with open(os.path.join(save_path, 'config.ini'), 'w', encoding='utf-8') as f:
                f.write(ini_content)
            if ssl_cert_content:
                with open(os.path.join(save_path, 'cert.pem'), 'w', encoding='utf-8') as f:
                    f.write(ssl_cert_content)
            QMessageBox.information(self, "Успех", f"Файлы 'config.ini' и 'cert.pem' успешно сохранены в папку:\n{save_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать файлы конфигурации: {e}")

    def _build_workplaces_page(self):
        """Создает страницу для управления складами и рабочими местами."""
        widget = QWidget()
        layout = QVBoxLayout()

        controls = QHBoxLayout()
        btn_create = QPushButton("Создать склад")
        btn_create.clicked.connect(self.create_new_warehouse)
        btn_change = QPushButton("Изменить кол-во")
        btn_change.clicked.connect(self.change_workplace_count)
        btn_print = QPushButton("Печать этикеток")
        btn_print.clicked.connect(self.open_workplace_printing_dialog)
        controls.addWidget(btn_create)
        controls.addWidget(btn_change)
        controls.addWidget(btn_print)
        layout.addLayout(controls)

        self.warehouses_table = QTableWidget(0, 2)
        self.warehouses_table.setHorizontalHeaderLabels(["Название склада", "Кол-во рабочих мест"])
        self.warehouses_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.warehouses_table.setSelectionMode(QTableWidget.SingleSelection)
        self.warehouses_table.setStyleSheet("QTableWidget::item:selected { background-color: #ADD8E6; }")
        layout.addWidget(self.warehouses_table)

        widget.setLayout(layout)
        return widget

    def load_warehouses(self):
        """Загружает данные о складах в таблицу."""
        try:
            self.warehouses_table.setRowCount(0)
            with get_client_db_connection(self.user_info) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT warehouse_name, COUNT(*) as workplace_count FROM ap_workplaces GROUP BY warehouse_name ORDER BY warehouse_name")
                    rows = cur.fetchall()

            for r in rows:
                row = self.warehouses_table.rowCount()
                self.warehouses_table.insertRow(row)
                it_name = QTableWidgetItem(r['warehouse_name'])
                it_count = QTableWidgetItem(str(r['workplace_count']))
                it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
                it_count.setFlags(it_count.flags() & ~Qt.ItemIsEditable)
                self.warehouses_table.setItem(row, 0, it_name)
                self.warehouses_table.setItem(row, 1, it_count)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить список складов: {e}")

    def create_new_warehouse(self):
        """Открывает диалоги для создания нового склада."""
        name, ok = QInputDialog.getText(self, "Новый склад", "Введите название нового склада:")
        if not ok or not name:
            return
        count, ok2 = QInputDialog.getInt(self, "Количество мест", "Введите количество рабочих мест:", 1, 1, 10000)
        if not ok2:
            return
        try:
            with get_client_db_connection(self.user_info) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM ap_workplaces WHERE warehouse_name = %s LIMIT 1", (name,))
                    if cur.fetchone():
                        QMessageBox.critical(self, "Ошибка", f"Склад '{name}' уже существует.")
                        return
                    for i in range(1, count + 1):
                        cur.execute("INSERT INTO ap_workplaces (warehouse_name, workplace_number) VALUES (%s, %s)", (name, i))
                conn.commit()
            QMessageBox.information(self, "Успех", f"Склад '{name}' с {count} местами создан")
            self.load_warehouses()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать склад: {e}")

    def change_workplace_count(self):
        """Изменяет количество рабочих мест для выбранного склада."""
        sel = self.warehouses_table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите склад для изменения.")
            return

        try:
            warehouse_name = self.warehouses_table.item(sel, 0).text()
            current_count = int(self.warehouses_table.item(sel, 1).text())
        except (AttributeError, ValueError):
            QMessageBox.critical(self, "Ошибка", "Не удалось прочитать данные о складе.")
            return

        new_count, ok = QInputDialog.getInt(self, "Изменить количество", f"Введите новое общее количество мест для склада '{warehouse_name}':", current_count, 0, 10000)

        if not ok or new_count == current_count:
            return

        try:
            with get_client_db_connection(self.user_info) as conn:
                with conn.cursor() as cur:
                    if new_count > current_count:
                        to_add = new_count - current_count
                        cur.execute("SELECT COALESCE(MAX(workplace_number), 0) FROM ap_workplaces WHERE warehouse_name = %s", (warehouse_name,))
                        max_num = cur.fetchone()[0]
                        for i in range(1, to_add + 1):
                            cur.execute("INSERT INTO ap_workplaces (warehouse_name, workplace_number) VALUES (%s, %s)", (warehouse_name, max_num + i))
                        msg = f"Добавлено {to_add} новых рабочих мест."
                    else: # new_count < current_count
                        to_delete = current_count - new_count
                        reply = QMessageBox.question(self, "Подтверждение", f"Удалить {to_delete} рабочих мест со склада '{warehouse_name}'?\nБудут удалены места с наибольшими номерами.", QMessageBox.Yes | QMessageBox.No)
                        if reply != QMessageBox.Yes:
                            return
                        cur.execute("""
                            DELETE FROM ap_workplaces
                            WHERE id IN (
                                SELECT id FROM ap_workplaces WHERE warehouse_name = %s ORDER BY workplace_number DESC LIMIT %s
                            )
                        """, (warehouse_name, to_delete))
                        msg = f"Удалено {to_delete} рабочих мест."
                conn.commit()
            QMessageBox.information(self, "Успех", msg)
            self.load_warehouses()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось изменить количество мест: {e}")

    def open_workplace_printing_dialog(self):
        """Открывает диалог печати этикеток для склада."""
        sel = self.warehouses_table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите склад для печати")
            return
        warehouse_name = self.warehouses_table.item(sel, 0).text()
        QMessageBox.information(self, "Печать", f"Вызов печати этикеток для склада: {warehouse_name} (в разработке)")
    def _open_generate_sscc_dialog(self):
        """Открывает диалог для запроса количества SSCC и запускает генерацию."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Генерация SSCC кодов")
        dialog.setMinimumWidth(350)
        layout = QVBoxLayout(dialog)

        form_layout = QFormLayout()
        quantity_label = QLabel("Количество SSCC (макс. 1 000 000):")
        quantity_spinbox = QSpinBox()
        quantity_spinbox.setRange(1, 1_000_000)
        quantity_spinbox.setValue(100) # Значение по умолчанию
        form_layout.addRow(quantity_label, quantity_spinbox)
        layout.addLayout(form_layout)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() == QDialog.Accepted:
            quantity = quantity_spinbox.value()
            self._generate_and_save_sscc(quantity)

    def _generate_and_save_sscc(self, quantity: int):
        """Запускает генерацию SSCC в фоновом потоке и сохраняет результат."""
        logging.debug(f"[_generate_and_save_sscc] Запуск генерации для {quantity} SSCC кодов.")

        logging.debug("[_generate_and_save_sscc] Создание и настройка QThread и SsccGeneratorWorker.")
        self.sscc_thread = QThread()
        self.sscc_worker = SsccGeneratorWorker(self.user_info, quantity)
        self.sscc_worker.moveToThread(self.sscc_thread)

        self.sscc_thread.started.connect(self.sscc_worker.run)
        # --- ИСПРАВЛЕНИЕ: Передаем только текст ошибки, а диалог закрываем в основном потоке ---
        self.sscc_worker.error.connect(self.on_sscc_generation_error)
        # --- ИСПРАВЛЕНИЕ: Передаем только список кодов, а диалог закрываем в основном потоке ---
        self.sscc_worker.finished.connect(self.on_sscc_generation_finished)

        self.sscc_worker.finished.connect(self.sscc_thread.quit)
        self.sscc_worker.finished.connect(self.sscc_worker.deleteLater)
        self.sscc_thread.finished.connect(self.sscc_thread.deleteLater)

        logging.debug("[_generate_and_save_sscc] Запуск фонового потока...")
        self.sscc_thread.start()

        logging.debug("[_generate_and_save_sscc] Процесс генерации запущен.")

    @Slot(str)
    def on_sscc_generation_error(self, error_message: str):
        """Слот для обработки ошибки генерации SSCC."""
        logging.error(f"[on_sscc_generation_error] Получена ошибка от воркера: {error_message}")
        QMessageBox.critical(self, "Ошибка генерации", error_message)

    @Slot(list)
    def on_sscc_generation_finished(self, ssccs: list):
        """Слот, который вызывается после успешной генерации SSCC."""
        logging.debug(f"[on_sscc_generation_finished] Воркер завершил работу. Получено {len(ssccs)} кодов.")
        self._save_sscc_to_file(ssccs)

    def _save_sscc_to_file(self, ssccs: list):
        """Предлагает сохранить сгенерированные SSCC в CSV файл."""
        logging.debug(f"[_save_sscc_to_file] Слот запущен. Получено {len(ssccs)} SSCC кодов.")

        if not ssccs:
            logging.warning("[_save_sscc_to_file] Список SSCC пуст. Сохранение отменено.")
            QMessageBox.warning(self, "Внимание", "Не удалось сгенерировать SSCC коды.")
            return

        logging.debug("[_save_sscc_to_file] Открытие диалога сохранения файла...")
        filepath, _ = QFileDialog.getSaveFileName(self, "Сохранить SSCC коды", "sscc_codes.csv", "CSV Files (*.csv)")

        if filepath:
            logging.debug(f"[_save_sscc_to_file] Файл для сохранения выбран: {filepath}")
            try:
                logging.debug("[_save_sscc_to_file] Начало записи в файл...")
                with open(filepath, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerows([[sscc] for sscc in ssccs])
                logging.debug("[_save_sscc_to_file] Запись в файл завершена успешно.")
                QMessageBox.information(self, "Успех", f"SSCC коды успешно сохранены в файл:\n{filepath}")
            except Exception as e:
                logging.error(f"[_save_sscc_to_file] Ошибка при записи в файл: {e}", exc_info=True)
                QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить SSCC коды в файл: {e}")
        else:
            logging.debug("[_save_sscc_to_file] Диалог сохранения файла отменен пользователем.")
            QMessageBox.information(self, "Отмена", "Сохранение файла отменено.")

    def _open_lenta_upload_dialog(self):
        """Открывает диалог для специальной загрузки 'Лента'."""
        dialog = LentaUploadDialog(self, self.user_info)
        dialog.exec()

# --- НОВЫЙ КЛАСС: Диалог для создания уведомления ---
class NotificationEditorDialog(QDialog):
    def __init__(self, parent, user_info):
        super().__init__(parent)
        self.user_info = user_info
        self.setWindowTitle("Новое уведомление о поставке")
        self.setMinimumWidth(500)

        # Инициализация сервисов
        self.service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
        self.catalog_service = CatalogsService(self.user_info, lambda: get_client_db_connection(self.user_info))

        self._build_ui()
        self._load_catalogs()
        self._on_scenario_change() # Первичная загрузка клиентов

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # Сценарий
        self.scenario_combo = QComboBox()
        self.scenario_combo.currentIndexChanged.connect(self._on_scenario_change)
        form_layout.addRow("Сценарий маркировки:", self.scenario_combo)

        # Клиент
        self.client_combo = QComboBox()
        form_layout.addRow("Клиент:", self.client_combo)

        # Товарная группа
        self.product_group_combo = QComboBox()
        form_layout.addRow("Товарная группа:", self.product_group_combo)

        # Дата прибытия
        self.arrival_date_edit = QDateEdit(QDate.currentDate())
        self.arrival_date_edit.setCalendarPopup(True)
        self.arrival_date_edit.setDisplayFormat("yyyy-MM-dd")
        form_layout.addRow("Планируемая дата прибытия:", self.arrival_date_edit)

        # Номер ТС
        self.vehicle_number_edit = QLineEdit()
        form_layout.addRow("Номер контейнера/ТС:", self.vehicle_number_edit)

        # Комментарии
        self.comments_edit = QTextEdit()
        self.comments_edit.setMaximumHeight(80)
        form_layout.addRow("Комментарии:", self.comments_edit)

        layout.addLayout(form_layout)

        # Кнопки
        button_box = QHBoxLayout()
        btn_save = QPushButton("Создать")
        btn_save.clicked.connect(self.save)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        button_box.addStretch()
        button_box.addWidget(btn_save)
        button_box.addWidget(btn_cancel)
        layout.addLayout(button_box)

    def _load_catalogs(self):
        """Загружает данные для выпадающих списков."""
        try:
            self.scenarios = self.catalog_service.get_marking_scenarios()
            self.scenario_combo.addItems([s['name'] for s in self.scenarios])

            self.product_groups = self.catalog_service.get_product_groups()
            self.product_group_combo.addItems([pg['display_name'] for pg in self.product_groups])
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить справочники: {e}")

    def _on_scenario_change(self):
        """Обновляет список клиентов при смене сценария."""
        selected_scenario_name = self.scenario_combo.currentText()
        scenario = next((s for s in self.scenarios if s['name'] == selected_scenario_name), None)
        if not scenario: return

        source = 'api' if scenario.get('scenario_data', {}).get('dm_source') == 'Заказ в ДМ.Код' else 'local'
        self.client_combo.clear()
        try:
            self.clients = self.catalog_service.get_local_clients() if source == 'local' else self.catalog_service.get_participants_catalog()
            self.client_combo.addItems([c['name'] for c in self.clients])
            self.client_source = source
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить клиентов: {e}")

    def save(self):
        """Собирает данные и сохраняет новое уведомление."""
        try:
            # Сбор данных
            scenario = self.scenarios[self.scenario_combo.currentIndex()]
            client = self.clients[self.client_combo.currentIndex()]
            pg = self.product_groups[self.product_group_combo.currentIndex()]

            data = {
                'scenario_id': scenario['id'],
                'scenario_name': scenario['name'],
                'client_name': client['name'],
                'product_groups': [{'id': pg['id'], 'name': pg['display_name']}],
                'planned_arrival_date': self.arrival_date_edit.date().toString("yyyy-MM-dd"),
                'vehicle_number': self.vehicle_number_edit.text(),
                'comments': self.comments_edit.toPlainText(),
                'client_api_id': client.get('id') if self.client_source == 'api' else None,
                'client_local_id': client.get('id') if self.client_source == 'local' else None,
            }

            new_id = self.service.create_notification(data)
            QMessageBox.information(self, "Успех", f"Уведомление #{new_id} успешно создано.")
            self.accept() # Закрываем диалог с успешным результатом
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить уведомление: {e}")

# --- НОВЫЙ КЛАСС: Диалог для загрузки "Лента" ---
class LentaUploadDialog(QDialog):
    def __init__(self, parent, user_info):
        super().__init__(parent)
        self.user_info = user_info
        self.setWindowTitle("Загрузка уведомления для 'Ленты'")
        self.setMinimumWidth(550)
        self.filepath = None

        # Инициализация сервисов
        self.service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
        self.catalog_service = CatalogsService(self.user_info, lambda: get_client_db_connection(self.user_info))

        self._build_ui()
        self._load_catalogs()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # --- Поля, как в обычном уведомлении ---
        self.scenario_combo = QComboBox()
        form_layout.addRow("Сценарий маркировки:", self.scenario_combo)

        self.client_combo = QComboBox()
        form_layout.addRow("Клиент:", self.client_combo)

        self.product_group_combo = QComboBox()
        form_layout.addRow("Товарная группа:", self.product_group_combo)

        self.arrival_date_edit = QDateEdit(QDate.currentDate())
        self.arrival_date_edit.setCalendarPopup(True)
        self.arrival_date_edit.setDisplayFormat("yyyy-MM-dd")
        form_layout.addRow("Планируемая дата прибытия:", self.arrival_date_edit)

        self.vehicle_number_edit = QLineEdit()
        form_layout.addRow("Номер контейнера/ТС:", self.vehicle_number_edit)

        # --- Специальное поле для выбора файла ---
        file_layout = QHBoxLayout()
        self.file_path_label = QLineEdit()
        self.file_path_label.setReadOnly(True)
        self.file_path_label.setPlaceholderText("Файл не выбран...")
        btn_browse = QPushButton("Обзор...")
        btn_browse.clicked.connect(self._select_file)
        file_layout.addWidget(self.file_path_label)
        file_layout.addWidget(btn_browse)
        form_layout.addRow("Файл для загрузки:", file_layout)

        layout.addLayout(form_layout)

        # Кнопки
        button_box = QHBoxLayout()
        btn_save = QPushButton("Сохранить и обработать")
        btn_save.clicked.connect(self.save_and_process)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        button_box.addStretch()
        button_box.addWidget(btn_save)
        button_box.addWidget(btn_cancel)
        layout.addLayout(button_box)

    def _select_file(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Выберите файл", "", "Excel Files (*.xlsx *.xls)")
        if filepath:
            self.filepath = filepath
            self.file_path_label.setText(os.path.basename(filepath))

    def _load_catalogs(self):
        try:
            self.scenarios = self.catalog_service.get_marking_scenarios()
            self.scenario_combo.addItems([s['name'] for s in self.scenarios])

            self.clients = self.catalog_service.get_local_clients()
            self.client_combo.addItems([c['name'] for c in self.clients])

            self.product_groups = self.catalog_service.get_product_groups()
            self.product_group_combo.addItems([pg['display_name'] for pg in self.product_groups])
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить справочники: {e}")

    def save_and_process(self):
        if not self.filepath:
            QMessageBox.warning(self, "Внимание", "Не выбран файл для загрузки.")
            return
        
        container_id = self.vehicle_number_edit.text().strip()
        if not container_id:
            QMessageBox.warning(self, "Внимание", "Поле 'Номер контейнера/ТС' обязательно для заполнения.")
            return

        logging.debug("[LentaUpload] Starting save_and_process.")

        try:
            # 1. Создание уведомления и загрузка файла
            scenario = self.scenarios[self.scenario_combo.currentIndex()]
            client = self.clients[self.client_combo.currentIndex()]
            pg = self.product_groups[self.product_group_combo.currentIndex()]

            notif_data = {
                'scenario_id': scenario['id'], 'scenario_name': scenario['name'], 'client_name': client['name'],
                'product_groups': [{'id': pg['id'], 'name': pg['display_name']}],
                'planned_arrival_date': self.arrival_date_edit.date().toString("yyyy-MM-dd"), 'comments': '',
                'vehicle_number': container_id, 'client_local_id': client.get('id'),
            }
            logging.debug(f"[LentaUpload] Notification data prepared: {notif_data}")
            
            # Шаг 1: Создание уведомления. Этот метод управляет своей транзакцией.
            new_notif_id = self.service.create_notification(notif_data)
            logging.debug(f"[LentaUpload] Notification created with ID: {new_notif_id}")

            # Шаг 1.1: Прикрепление файла к уведомлению. Этот метод также управляет своей транзакцией.
            with open(self.filepath, 'rb') as f:
                file_data = f.read()
            self.service.add_notification_file(new_notif_id, os.path.basename(self.filepath), file_data, 'lenta_upload')
            logging.debug(f"[LentaUpload] File '{os.path.basename(self.filepath)}' attached to notification ID: {new_notif_id}")

            # --- НОВАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ ---
            # Сначала обрабатываем файл и готовим данные
            logging.debug(f"[LentaUpload] Reading Excel file: {self.filepath}, using columns by index [1, 2, 3].")
            df = pd.read_excel(self.filepath, header=0, usecols=[1, 2, 3], names=['gtin', 'sscc', 'quantity'], dtype=str)
            logging.debug(f"[LentaUpload] Excel file read. Initial rows: {len(df)}. First 5 rows:\n{df.head().to_string()}")

            df['gtin'] = df['gtin'].apply(lambda x: str(x).strip().zfill(14) if pd.notna(x) and len(str(x).strip()) < 14 else (str(x).strip() if pd.notna(x) else None))
            df['sscc'] = df['sscc'].apply(lambda x: str(x).strip() if pd.notna(x) and len(str(x).strip()) == 18 else None)
            df.dropna(subset=['sscc', 'gtin'], inplace=True)
            logging.debug(f"[LentaUpload] Rows after dropping invalid SSCC/GTIN: {len(df)}")

            df_unique = df.drop_duplicates().copy()
            logging.debug(f"[LentaUpload] Unique rows count: {len(df_unique)}")

            if df_unique.empty:
                logging.warning("[LentaUpload] No unique valid data found in the file. Aborting.")
                raise ValueError("В файле не найдено корректных уникальных строк для обработки.")

            # Шаг 2: Вставка в ap_supply_notification_details
            # ИСПРАВЛЕНИЕ: Считаем количество SSCC для каждого GTIN, а не суммируем Quantity.
            df_grouped = df_unique.groupby('gtin').agg(sscc_count=('sscc', 'count')).reset_index()
            logging.debug(f"[LentaUpload] Data grouped by GTIN for details. Resulting groups: {len(df_grouped)}")

            # ИСПРАВЛЕНИЕ: Реализуем вставку в ap_supply_notification_details напрямую,
            # так как метод save_grouped_details_from_df отсутствует в сервисе.
            with get_client_db_connection(self.user_info) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    today = QDate.currentDate()
                    expiry_date = today.addMonths(36)
                    
                    details_to_insert = []
                    for _, row in df_grouped.iterrows():
                        details_to_insert.append((
                            new_notif_id,
                            row['gtin'],
                            row['sscc_count'],
                            1, # aggregation (1 - Короб)
                            today.toString("yyyy-MM-dd"), # production_date
                            36, # shelf_life_months
                            expiry_date.toString("yyyy-MM-dd") # expiry_date
                        ))
                    
                    cols_details = ['notification_id', 'gtin', 'quantity', 'aggregation', 'production_date', 'shelf_life_months', 'expiry_date']
                    insert_query_details = f"INSERT INTO ap_supply_notification_details ({', '.join(cols_details)}) VALUES %s"
                    logging.debug(f"[LentaUpload] Preparing to insert {len(details_to_insert)} rows into ap_supply_notification_details.")
                    from psycopg2.extras import execute_values
                    execute_values(cur, insert_query_details, details_to_insert)
                    logging.debug(f"[LentaUpload] Insertion into ap_supply_notification_details finished.")
                conn.commit()
                logging.debug("[LentaUpload] Transaction for notification_details committed.")

            # Шаг 3: Создание заказа на основе уведомления
            # ИСПРАВЛЕНИЕ: Корректно обрабатываем кортеж из 3-х значений, возвращаемый сервисом
            result = self.service.create_or_recreate_order_from_notification(new_notif_id)
            success, message, third_param = result

            # ID заказа возвращается во втором параметре (message) в случае успеха
            new_order_id = None
            if success:
                # ИСПРАВЛЕНИЕ: Извлекаем только числовой ID из ответного сообщения
                match = re.search(r'№(\d+)', message)
                if match:
                    new_order_id = int(match.group(1))
                else:
                    raise ValueError(f"Не удалось извлечь ID заказа из сообщения: '{message}'")

            if not success:
                raise Exception(f"Не удалось создать заказ: {message}")
            logging.debug(f"[LentaUpload] Order created/updated with ID: {new_order_id}")

            # Шаг 4: Вставка в aggregation_tasks с реальным order_id
            with get_client_db_connection(self.user_info) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    df_unique['order_id'] = new_order_id
                    df_unique['container_id'] = container_id
                    df_unique['owner'] = client['name']
                    
                    from psycopg2.extras import execute_values
                    tasks_to_insert = df_unique[['order_id', 'container_id', 'gtin', 'sscc', 'owner']]
                    insert_query_tasks = f"INSERT INTO aggregation_tasks ({', '.join(tasks_to_insert.columns)}) VALUES %s"
                    logging.debug(f"[LentaUpload] Preparing to insert {len(tasks_to_insert)} rows into aggregation_tasks.")
                    execute_values(cur, insert_query_tasks, [tuple(x) for x in tasks_to_insert.to_numpy()])
                    logging.debug(f"[LentaUpload] Insertion into aggregation_tasks finished.")
                    conn.commit()
                logging.debug("[LentaUpload] Transaction for aggregation_tasks committed.")

            QMessageBox.information(self, "Успех", f"Уведомление #{new_notif_id} создано и данные успешно обработаны.")
            self.accept()
        except Exception as e:
            logging.exception("[LentaUpload] An error occurred during processing.")
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Произошла ошибка при обработке: {e}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = AdminWindowQt({'client_db_config': {}, 'name': 'local-admin'})
    w.show()
    sys.exit(app.exec())
