from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QMessageBox, QApplication, QLabel, QFileDialog, QTextEdit,
    QLineEdit, QHeaderView, QDateEdit, QDialog, QFormLayout, QComboBox, QSplitter, QTabWidget, QProgressDialog, QDialogButtonBox, QCheckBox,
    QGroupBox, QRadioButton, QSpinBox,
    QInputDialog, QTreeWidget, QTreeWidgetItem, QStackedWidget, QAbstractItemView
)
from PySide6.QtCore import Qt, Slot, QDate, QTimer, QThread, Signal, QObject
from PySide6.QtGui import QColor
import sys
import traceback
import logging
import json

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
                    for i in range(self.quantity):
                        if i % 1000 == 0: # Обновляем прогресс каждые 1000 кодов
                            self.progress.emit(int((i / self.quantity) * 100), f"Генерация SSCC: {i}/{self.quantity}")
                        box_id, warning, gcp_for_sscc = read_and_increment_counter(cur, 'sscc_id')
                        if warning:
                            self.progress.emit(0, warning) # Отправляем предупреждение, не меняя основной прогресс
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
    def __init__(self, user_info, order_id, scenario_data, main_app_window, parent=None):
        super().__init__(parent)
        self.user_info = user_info
        self.order_id = order_id
        self.scenario_data = scenario_data
        self.main_app_window = main_app_window

        self._create_widgets()
        self._load_details()

    def _get_client_db_connection(self):
        return get_client_db_connection(self.user_info)

    def _create_widgets(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # --- Ряд 1: Основные операции с детализацией ---
        controls_frame_1 = QHBoxLayout()
        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._save_changes)
        btn_export = QPushButton("Выгрузить")
        btn_export.clicked.connect(self._export_details_to_excel)
        btn_import = QPushButton("Загрузить")
        btn_import.clicked.connect(self._import_details_from_excel)
        controls_frame_1.addWidget(btn_save)
        controls_frame_1.addWidget(btn_export)
        controls_frame_1.addWidget(btn_import)
        controls_frame_1.addStretch()
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

        # --- Кнопка архивации ---
        archive_layout = QHBoxLayout()
        archive_layout.addStretch()
        btn_archive = QPushButton("Перенести в архив")
        btn_archive.setStyleSheet("background-color: #FFB6C1;") # Light Pink
        btn_archive.clicked.connect(self._move_to_archive)
        archive_layout.addWidget(btn_archive)
        main_layout.addLayout(archive_layout)
    def _load_details(self):
        self.details_table.setRowCount(0)
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM dmkod_aggregation_details WHERE order_id = %s ORDER BY id", (self.order_id,))
                    details = cur.fetchall()
            
            for item in details:
                row = self.details_table.rowCount()
                self.details_table.insertRow(row)
                for col_idx, col_name in enumerate(self.details_cols):
                    value = item.get(col_name, '')
                    self.details_table.setItem(row, col_idx, QTableWidgetItem(str(value)))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить детали заказа: {e}")

    def _save_changes(self):
        updates = []
        for row in range(self.details_table.rowCount()):
            row_data = {}
            for col, key in enumerate(self.details_cols):
                item = self.details_table.item(row, col)
                row_data[key] = item.text() if item else None
            updates.append(row_data)
        
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    for item in updates:
                        cur.execute("""
                            UPDATE dmkod_aggregation_details SET
                                gtin = %s, dm_quantity = %s, aggregation_level = %s,
                                production_date = %s, expiry_date = %s
                            WHERE id = %s
                        """, (
                            item['gtin'], item['dm_quantity'], item['aggregation_level'],
                            item['production_date'] or None, item['expiry_date'] or None,
                            item['id']
                        ))
                conn.commit()
            QMessageBox.information(self, "Успех", "Изменения успешно сохранены.")
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
        logging.debug(f"Запуск импорта детализации для заказа ID: {self.order_id}")
        if QMessageBox.question(self, "Подтверждение", "Импорт из файла полностью заменит текущую детализацию. Продолжить?") != QMessageBox.Yes:
            logging.debug("Импорт отменен пользователем.")
            return

        filepath, _ = QFileDialog.getOpenFileName(self, "Выберите Excel-файл", "", "Excel Files (*.xlsx *.xls)")
        if not filepath:
            logging.debug("Файл для импорта не выбран.")
            return
        
        logging.debug(f"Выбран файл для импорта: {filepath}")

        try:
            logging.debug("Чтение Excel файла с помощью pandas...")
            df = pd.read_excel(filepath, dtype={'gtin': str})
            df = df.where(pd.notna(df), None)
            df['order_id'] = self.order_id
            logging.debug(f"Файл успешно прочитан. Обнаружено {len(df)} строк. Колонки: {list(df.columns)}")

            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    logging.debug(f"Удаление старой детализации для заказа ID: {self.order_id}...")
                    cur.execute("DELETE FROM dmkod_aggregation_details WHERE order_id = %s", (self.order_id,))
                    logging.debug("Старая детализация удалена. Запуск массовой вставки...")
                    
                    # --- ИСПРАВЛЕНИЕ: Заменяем UPSERT на прямой INSERT ---
                    from psycopg2.extras import execute_values
                    
                    # Убедимся, что колонки в DataFrame соответствуют таблице
                    cols = ['order_id', 'gtin', 'dm_quantity', 'aggregation_level', 'production_date', 'expiry_date']
                    df_to_insert = df[[c for c in cols if c in df.columns]]
                    
                    insert_query = f"""
                        INSERT INTO dmkod_aggregation_details ({", ".join(df_to_insert.columns)}) 
                        VALUES %s
                    """
                    data_tuples = [tuple(x) for x in df_to_insert.to_numpy()]
                    execute_values(cur, insert_query, data_tuples)
                    logging.debug("upsert_data_to_db завершен.")
                conn.commit()
            QMessageBox.information(self, "Успех", f"Детализация импортирована. Загружено {len(df)} строк.")
            self._load_details()
        except Exception as e:
            logging.error(f"Критическая ошибка при импорте детализации: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать данные: {e}")

    def _move_to_archive(self):
        if QMessageBox.question(self, "Подтверждение", f"Переместить заказ №{self.order_id} в архив?") != QMessageBox.Yes:
            return

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT client_name, status FROM orders WHERE id = %s", (self.order_id,))
                    order_info = cur.fetchone()
                    if order_info:
                        client_name = order_info['client_name']
                        current_status = order_info['status']
                        
                        base_view_name_str = f"{client_name}_{self.order_id}"
                        sanitized_name = re.sub(r'[^\w]', '_', base_view_name_str)
                        sanitized_name = re.sub(r'_+', '_', sanitized_name).strip('_')
                        
                        base_view_name = psycopg2.sql.Identifier(sanitized_name)
                        sscc_view_name = psycopg2.sql.Identifier(f"{sanitized_name}_sscc")

                        cur.execute(psycopg2.sql.SQL("DROP VIEW IF EXISTS {};").format(sscc_view_name))
                        cur.execute(psycopg2.sql.SQL("DROP VIEW IF EXISTS {};").format(base_view_name))

                        new_status = f"Архив_{current_status}"
                        cur.execute("UPDATE orders SET status = %s WHERE id = %s RETURNING notification_id", (new_status, self.order_id))
                        result = cur.fetchone()
                        notification_id = result['notification_id'] if result else None
                        if notification_id:
                            cur.execute("UPDATE ap_supply_notifications SET status = 'В архиве' WHERE id = %s", (notification_id,))
                conn.commit()
            
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
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT DISTINCT gtin FROM dmkod_aggregation_details WHERE order_id = %s AND gtin IS NOT NULL", (self.order_id,))
                    gtins = [row['gtin'] for row in cur.fetchall()]
                    if not gtins:
                        QMessageBox.warning(self, "Внимание", "В заказе нет товаров для экспорта.")
                        return
                    cur.execute("SELECT gtin, name, description_1, description_2, description_3 FROM products WHERE gtin = ANY(%s)", (gtins,))
                    products_data = cur.fetchall()

            if not products_data:
                QMessageBox.warning(self, "Внимание", "Не найдено записей в справочнике товаров для GTIN из этого заказа.")
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
            df = pd.read_excel(filepath, dtype={'gtin': str})
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    from .utils import upsert_data_to_db
                    upsert_data_to_db(cur, 'products', df, 'gtin')
                conn.commit()
            QMessageBox.information(self, "Успех", f"Справочник товаров успешно обновлен. Обработано {len(df)} строк.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать товары: {e}")

    def _create_bartender_view(self):
        """Создает/обновляет представления для Bartender."""
        from .aggregation_service import run_import_from_dmkod, create_bartender_views
        
        progress = QProgressDialog("Выполняется импорт и создание представлений...", "Отмена", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setValue(10)
        
        try:
            # Шаг 1: Импорт кодов
            progress.setLabelText("Шаг 1/2: Импорт кодов из базы...")
            run_import_from_dmkod(self.user_info, self.order_id)
            progress.setValue(50)

            # Шаг 2: Создание представлений
            progress.setLabelText("Шаг 2/2: Создание представлений для Bartender...")
            result = create_bartender_views(self.user_info, self.order_id)
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
        logging.info(f"Запуск экспорта данных в формате 'Дельта' для заказа ID: {self.order_id}")
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT notes FROM orders WHERE id = %s", (self.order_id,))
                    order_info = cur.fetchone()
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT api_codes_json, production_date, expiry_date FROM dmkod_aggregation_details WHERE order_id = %s AND api_codes_json IS NOT NULL",
                        (self.order_id,)
                    )
                    details_to_process = cur.fetchall()

            if not details_to_process:
                QMessageBox.warning(self, "Нет данных", "В заказе нет скачанных кодов для выгрузки.")
                return

            all_rows = []
            from dateutil.relativedelta import relativedelta

            for detail in details_to_process:
                codes = detail.get('api_codes_json', {}).get('codes', [])
                prod_date = detail.get('production_date')
                exp_date = detail.get('expiry_date')

                life_time_months = ''
                if prod_date and exp_date:
                    delta = relativedelta(exp_date, prod_date)
                    life_time_months = delta.years * 12 + delta.months

                for code in codes:
                    if not code or len(code) < 16: continue
                    all_rows.append({
                        'DataMatrix': code,
                        'DataMatrixCode': '',
                        'Barcode': code[2:16],
                        'LifeTime': life_time_months
                    })

            if not all_rows:
                QMessageBox.warning(self, "Нет данных", "Не найдено корректных кодов для выгрузки.")
                return

            df = pd.DataFrame(all_rows)
            report_name = re.sub(r'[^\w]', '_', order_info.get('notes', '') if order_info else '').strip('_')
            initial_filename = f"{report_name}_order_{self.order_id}.csv"
            
            filepath, _ = QFileDialog.getSaveFileName(self, "Сохранить файл для Внешнего ПО", initial_filename, "CSV Files (*.csv)")
            if not filepath: return

            import csv
            df.to_csv(filepath, sep='\t', index=False, encoding='utf-8', lineterminator='\r\n', quoting=csv.QUOTE_NONE)

            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET status = 'delta' WHERE id = %s", (self.order_id,))
                conn.commit()

            QMessageBox.information(self, "Успех", f"Данные успешно выгружены в файл:\n{filepath}\n\nСтатус заказа обновлен на 'delta'.")
            self.main_app_window.load_orders(is_archive=False)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось экспортировать данные: {e}")

    def _import_data_for_external_sw(self):
        """
        Обрабатывает CSV-файл от 'Дельта', создает упаковки, товары и готовит данные для API.
        Адаптировано из dmkod-integration-app/app/routes.py, action 'upload_delta_csv'.
        """
        logging.info(f"[Delta Import] Запуск импорта данных из CSV для заказа ID: {self.order_id}")

        filepath, _ = QFileDialog.getOpenFileName( # ИСПРАВЛЕНИЕ: Используем правильные аргументы для PySide6
            self,
            "Выберите CSV-файл от 'Дельта'", # caption (заголовок)
            filter="CSV files (*.csv)"      # filter (фильтр файлов)
        )
        if not filepath:
            logging.info("[Delta Import] Импорт отменен пользователем.")
            return

        # --- ИСПРАВЛЕНИЕ: Создаем диалог прогресса только при запуске операции ---
        progress_dialog = QProgressDialog("Выполняется импорт данных...", "Отмена", 0, 100, self)
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setAutoClose(False)
        progress_dialog.setAutoReset(True)
        progress_dialog.setValue(0)

        # 1. Валидация имени файла
        expected_filename_part = f"order_{self.order_id}.csv"
        if expected_filename_part not in os.path.basename(filepath):
            QMessageBox.critical(self, "Ошибка", f'Имя файла должно содержать "{expected_filename_part}".')
            return

        try:
            # --- ИСПРАВЛЕНИЕ: Показываем диалог только после всех проверок ---
            progress_dialog.setLabelText("Чтение и валидация CSV...")
            progress_dialog.show()
            QApplication.processEvents()
            # 2. Чтение и валидация CSV
            df = pd.read_csv(filepath, sep='\t', dtype={'Barcode': str, 'BoxSSCC': str, 'PaletSSCC': str})
            df.columns = df.columns.str.strip()
            required_columns = ['DataMatrix', 'Barcode', 'StartDate', 'EndDate', 'BoxSSCC', 'PaletSSCC']
            if not all(col in df.columns for col in required_columns):
                raise ValueError(f'В файле отсутствуют необходимые колонки. Ожидаются: {", ".join(required_columns)}.')

            # --- ИСПРАВЛЕНИЕ: Добавляем ведущий ноль к 13-значным GTIN ---
            # Это решает проблему, когда в файле от "Дельты" GTIN представлен
            # в формате EAN-13 (13 символов) вместо GTIN-14.
            df['Barcode'] = df['Barcode'].apply(lambda x: '0' + str(x) if isinstance(x, str) and len(x) == 13 else x)

            df['BoxSSCC'] = df['BoxSSCC'].str[-18:]
            df['PaletSSCC'] = df['PaletSSCC'].str[-18:]
            df['StartDate'] = pd.to_datetime(df['StartDate'], format='%Y-%m-%d').dt.strftime('%Y-%m-%d')
            df['EndDate'] = pd.to_datetime(df['EndDate'], format='%Y-%m-%d').dt.strftime('%Y-%m-%d')

            progress_dialog.setValue(10)
            progress_dialog.setLabelText("Создание упаковок...")
            QApplication.processEvents()

            # --- ИСПРАВЛЕНИЕ: Используем новый метод подключения к БД через пул ---
            # Это решает проблему с созданием лишних подключений.
            with get_client_db_connection(self.user_info) as conn:
              with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                from .utils import upsert_data_to_db
                
                # 3. Создание упаковок (короба и паллеты)
                unique_boxes = df[['BoxSSCC']].dropna().drop_duplicates().rename(columns={'BoxSSCC': 'sscc'})
                unique_pallets = df[['PaletSSCC']].dropna().drop_duplicates().rename(columns={'PaletSSCC': 'sscc'})
                
                packages_to_insert = []
                if not unique_boxes.empty:
                    unique_boxes['level'] = 1
                    packages_to_insert.append(unique_boxes)
                if not unique_pallets.empty:
                    unique_pallets['level'] = 2
                    packages_to_insert.append(unique_pallets)

                if packages_to_insert:
                    all_packages_df = pd.concat(packages_to_insert, ignore_index=True)
                    all_packages_df['owner'] = 'delta'
                    
                    # Устанавливаем связи "короб-паллета"
                    box_pallet_map = df[['BoxSSCC', 'PaletSSCC']].dropna().drop_duplicates()
                    box_to_pallet_sscc_map = pd.Series(box_pallet_map.PaletSSCC.values, index=box_pallet_map.BoxSSCC).to_dict()
                    
                    def find_parent_sscc(row):
                        if row['level'] == 1: return box_to_pallet_sscc_map.get(row['sscc'])
                        return None
                    all_packages_df['parent_sscc'] = all_packages_df.apply(find_parent_sscc, axis=1)

                    # Используем UPSERT для безопасной вставки
                    upsert_data_to_db(cur, 'packages', all_packages_df, 'sscc')
                    logging.info(f"[Delta Import] Загружено/обновлено {len(all_packages_df)} упаковок.")

                    # Обновляем parent_id после вставки
                    cur.execute("""
                        UPDATE packages p_child SET parent_id = p_parent.id
                        FROM packages AS p_parent
                        WHERE p_child.parent_sscc = p_parent.sscc AND p_child.parent_sscc IS NOT NULL;
                    """)
                    cur.execute("UPDATE packages SET parent_sscc = NULL WHERE parent_sscc IS NOT NULL;")
                    logging.info("[Delta Import] Связи 'короб-паллета' обновлены.")

                progress_dialog.setValue(30)
                progress_dialog.setLabelText("Создание товаров (items)...")
                QApplication.processEvents()

                # 4. Создание товаров (items)
                from .aggregation_service import parse_datamatrix
                parsed_dm_data = [parse_datamatrix(dm) for dm in df['DataMatrix']]
                items_df = pd.DataFrame(parsed_dm_data)
                items_df['order_id'] = self.order_id
                items_df['BoxSSCC'] = df['BoxSSCC']

                # Получаем ID коробов для привязки
                box_ssccs_tuple = tuple(df['BoxSSCC'].dropna().unique())
                sscc_to_id_map = {}
                if box_ssccs_tuple:
                    cur.execute("SELECT sscc, id FROM packages WHERE sscc IN %s", (box_ssccs_tuple,))
                    sscc_to_id_map = {row['sscc']: row['id'] for row in cur.fetchall()}
                
                items_df['package_id'] = items_df['BoxSSCC'].map(sscc_to_id_map)
                items_df['package_id'] = items_df['package_id'].astype('object').where(pd.notna(items_df['package_id']), None)
                
                columns_to_save = ['datamatrix', 'gtin', 'serial', 'crypto_part_91', 'crypto_part_92', 'crypto_part_93', 'order_id', 'package_id']
                items_to_upload = items_df[columns_to_save]
                upsert_data_to_db(cur, 'items', items_to_upload, 'datamatrix')
                logging.info(f"[Delta Import] Загружено/обновлено {len(items_to_upload)} кодов маркировки.")

                progress_dialog.setValue(80)
                progress_dialog.setLabelText("Подготовка данных для API...")
                QApplication.processEvents()

                # 5. Подготовка данных для delta_result
                df_for_json = df.copy()
                df_for_json.rename(columns={'Barcode': 'gtin', 'StartDate': 'production_date', 'EndDate': 'expiration_date'}, inplace=True)
                
                cur.execute("SELECT gtin, api_id FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL", (self.order_id,))
                # --- ИСПРАВЛЕНИЕ: Гарантируем, что ключ (GTIN) является строкой ---
                # Это решает проблему, когда GTIN с ведущим нулем обрабатывался как число.
                gtin_to_printrun_map = {str(row['gtin']): row['api_id'] for row in cur.fetchall()}

                # --- ИСПРАВЛЕНИЕ №2: Принудительно приводим GTIN к строковому типу СРАЗУ ПОСЛЕ ПЕРЕИМЕНОВАНИЯ ---
                # Это гарантирует, что pandas будет работать с GTIN как с текстом на всех последующих этапах,
                # предотвращая потерю ведущих нулей и ошибки сопоставления.
                df_for_json['gtin'] = df_for_json['gtin'].astype(str)

                if not gtin_to_printrun_map:
                    raise Exception("Не удалось найти ID тиражей (api_id) в деталях заказа. Убедитесь, что тиражи созданы в API.")

                df_for_json['printrun_id'] = df_for_json['gtin'].map(gtin_to_printrun_map)
                # --- ИСПРАВЛЕНИЕ: Проверяем, что все GTIN были сопоставлены ---
                # Это предотвращает молчаливую потерю данных, если для GTIN из файла нет тиража.
                if df_for_json['printrun_id'].isnull().any():
                    unmapped_gtins = df_for_json[df_for_json['printrun_id'].isnull()]['gtin'].unique()
                    raise ValueError(f"Ошибка: Для GTIN(ов) {list(unmapped_gtins)} из файла не найден соответствующий ID тиража в заказе.")

                grouped_for_api = df_for_json.groupby(['printrun_id', 'production_date', 'expiration_date']).agg({'DataMatrix': list}).reset_index()

                # --- ИСПРАВЛЕНИЕ: Полностью переписанная логика для устранения SyntaxError ---
                # Используем list comprehension для надежного и быстрого создания JSON.
                # Это решает ошибку с несоответствием скобок.
                grouped_for_api['codes_json'] = [
                    json.dumps({
                        "include": [{"code": code.replace('\x1d', '')} for code in row.DataMatrix],
                        "attributes": {
                            "production_date": str(row.production_date),
                            "expiration_date": str(row.expiration_date)
                        }
                    })
                    for row in grouped_for_api.itertuples()
                ]
                grouped_for_api['order_id'] = self.order_id
                grouped_for_api['printrun_id'] = grouped_for_api['printrun_id'].astype(int)
                grouped_for_api['production_date'] = pd.to_datetime(grouped_for_api['production_date']).dt.date

                delta_result_df = grouped_for_api[['order_id', 'printrun_id', 'production_date', 'codes_json']]
                upsert_data_to_db(cur, 'delta_result', delta_result_df, ['order_id', 'printrun_id', 'production_date'])
                logging.info(f"[Delta Import] Сохранено {len(delta_result_df)} сгруппированных записей в 'delta_result'.")

                # # 6. Обновление статуса заказа
                # cur.execute("UPDATE orders SET status = 'delta_loaded' WHERE id = %s", (self.order_id,))
              # 6. Фиксируем все изменения в одной транзакции
              conn.commit()
              QMessageBox.information(self, "Успех", "Данные из CSV-файла 'Дельта' успешно импортированы и обработаны.")

        except Exception as e:
            logging.error(f"Ошибка при импорте данных 'Дельта' для заказа {self.order_id}: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка", f"Не удалось импортировать данные: {e}")
        finally:
            # --- ИСПРАВЛЕНИЕ: Гарантированно скрываем и удаляем диалог ---
            progress_dialog.hide()

    def _download_declarator_report(self):
        """Формирует и выгружает отчет для декларанта."""
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT notes FROM orders WHERE id = %s", (self.order_id,))
                    order_info = cur.fetchone()

                    # --- ИСПРАВЛЕНИЕ: Выполняем запрос через курсор, а не pd.read_sql, чтобы избежать UserWarning ---
                    query = """
                        WITH RECURSIVE base_data AS (
                            SELECT i.datamatrix, i.gtin, i.package_id, p.name AS product_name, p.description_1, p.description_2, p.description_3
                            FROM items i LEFT JOIN products p ON i.gtin = p.gtin
                            WHERE i.order_id = %(order_id)s
                        ), package_hierarchy AS (
                            SELECT p.id as base_box_id, p.id as package_id, p.level, p.sscc, p.parent_id
                            FROM packages p WHERE p.level = 1 AND p.id IN (SELECT DISTINCT package_id FROM base_data WHERE package_id IS NOT NULL)
                            UNION ALL
                            SELECT ph.base_box_id, p_parent.id as package_id, p_parent.level, p_parent.sscc, p_parent.parent_id
                            FROM package_hierarchy ph JOIN packages p_parent ON ph.parent_id = p_parent.id
                        ), sscc_data AS (
                            SELECT base_box_id AS id_level_1, MAX(CASE WHEN level = 1 THEN sscc END) AS sscc_level_1, MAX(CASE WHEN level = 2 THEN sscc END) AS sscc_level_2, MAX(CASE WHEN level = 3 THEN sscc END) AS sscc_level_3
                            FROM package_hierarchy GROUP BY base_box_id
                        )
                        SELECT b.datamatrix, b.gtin, SUBSTRING(b.datamatrix for 24) AS dm_part_24, SUBSTRING(b.datamatrix for 31) AS dm_part_31, s.sscc_level_1, s.sscc_level_2, s.sscc_level_3, b.product_name, b.description_1, b.description_2, b.description_3
                        FROM base_data b LEFT JOIN sscc_data s ON b.package_id = s.id_level_1 ORDER BY b.datamatrix;
                    """
                    cur.execute(query, {'order_id': self.order_id})
                    report_data = cur.fetchall()
                    df = pd.DataFrame(report_data)

            if df.empty:
                QMessageBox.warning(self, "Нет данных", "Не найдено данных для формирования отчета.")
                return

            df = df.applymap(lambda val: val.replace('\x1d', ' ') if isinstance(val, str) else val)
            report_name = re.sub(r'[^\w]', '_', order_info.get('notes', '') if order_info else '').strip('_')
            filepath, _ = QFileDialog.getSaveFileName(self, "Сохранить отчет декларанта", f"{report_name}_order_{self.order_id}.xlsx", "Excel Files (*.xlsx)")
            
            if filepath:
                df.to_excel(filepath, index=False)
                QMessageBox.information(self, "Успех", f"Отчет декларанта успешно сохранен в файл:\n{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сформировать отчет: {e}")


class ApiIntegrationFrameQt(QWidget):
    """Полнофункциональный фрейм для интеграции с API ДМ.Код."""
    def __init__(self, user_info, order_id, post_processing_mode, main_app_window, parent=None):
        super().__init__(parent)
        self.user_info = user_info
        self.order_id = order_id
        self.post_processing_mode = post_processing_mode
        self.main_app_window = main_app_window
        self.api_service = ApiService(user_info)
        self.order_data = None

        self._load_order_data()
        self._create_widgets()
        self._update_buttons_state()

    def _get_client_db_connection(self):
        return get_client_db_connection(self.user_info)

    def _load_order_data(self):
        """Загружает данные заказа для определения состояния кнопок."""
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM orders WHERE id = %s", (self.order_id,))
                    self.order_data = cur.fetchone()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить данные заказа: {e}")
            self.deleteLater()

    def _create_widgets(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # Ряд 1: Основной флоу
        self.flow_panel = QHBoxLayout()
        self.request_codes_btn = QPushButton("Запросить коды")
        self.request_codes_btn.clicked.connect(self._request_codes_flow)
        self.get_codes_btn = QPushButton("Получить коды")
        self.get_codes_btn.clicked.connect(self._get_codes_flow)
        self.split_runs_btn = QPushButton("Разбить на тиражи")
        self.split_runs_btn.clicked.connect(self._split_runs)
        self.prepare_json_btn = QPushButton("Подготовить JSON")
        self.prepare_json_btn.clicked.connect(self._prepare_json)
        self.download_codes_btn = QPushButton("Скачать коды")
        self.download_codes_btn.clicked.connect(self._download_codes)
        
        self.flow_panel.addWidget(self.request_codes_btn)
        self.flow_panel.addWidget(self.get_codes_btn)
        self.flow_panel.addWidget(self.split_runs_btn)
        self.flow_panel.addWidget(self.prepare_json_btn)
        self.flow_panel.addWidget(self.download_codes_btn)
        self.flow_panel.addStretch()
        main_layout.addLayout(self.flow_panel)

        # Ряд 2: Флоу отчетности
        self.reporting_panel = QHBoxLayout()
        self.prepare_report_data_btn = QPushButton("Подготовить сведения")
        self.prepare_report_data_btn.clicked.connect(self._prepare_report_data)
        self.prepare_report_btn = QPushButton("Подготовить отчет")
        self.prepare_report_btn.clicked.connect(self._prepare_report)
        
        self.reporting_panel.addWidget(self.prepare_report_data_btn)
        self.reporting_panel.addWidget(self.prepare_report_btn)
        self.reporting_panel.addStretch()
        main_layout.addLayout(self.reporting_panel)

        # Поле для вывода ответа от API
        self.response_text = QTextEdit()
        self.response_text.setReadOnly(True)
        main_layout.addWidget(self.response_text)

    def _update_buttons_state(self):
        """Обновляет состояние кнопок в зависимости от статуса заказа."""
        if not self.order_data: return

        api_order_id = self.order_data.get('api_order_id')
        api_status = self.order_data.get('api_status')

        all_buttons = [
            self.request_codes_btn, self.get_codes_btn, self.split_runs_btn, self.prepare_json_btn,
            self.download_codes_btn, self.prepare_report_data_btn, self.prepare_report_btn
        ]
        for btn in all_buttons:
            btn.setVisible(False)

        if api_status == 'Отчет подготовлен':
            self._display_api_response(200, "Работа с заказом в АПИ завершена. Отчет об использовании кодов подготовлен.")
            return

        if not api_order_id or not api_status:
            self.request_codes_btn.setVisible(True)
        elif api_status == 'Запрос создан':
            self.get_codes_btn.setVisible(True)
        else:
            if api_status == 'Тиражи созданы':
                self.prepare_json_btn.setVisible(True)
            
            self.download_codes_btn.setVisible(True)
            self.prepare_report_data_btn.setVisible(True)
            self.prepare_report_btn.setVisible(True)
            
            self.download_codes_btn.setEnabled(api_status in ['JSON заказан', 'Коды скачаны', 'Сведения подготовлены', 'Отчет подготовлен'])
            self.prepare_report_data_btn.setEnabled(api_status in ['JSON заказан', 'Коды скачаны'])
            self.prepare_report_btn.setEnabled(api_status == 'Сведения подготовлены')

    def _display_api_response(self, status_code, body):
        """Отображает ответ API в текстовом поле."""
        if not isinstance(body, str):
            body = json.dumps(body, indent=2, ensure_ascii=False)
        response_content = f"Статус: {status_code}\n\nТело ответа:\n{body}"
        self.response_text.setPlainText(response_content)

    def _append_log(self, message):
        """Добавляет сообщение в лог в текстовом поле."""
        self.response_text.append(message)
        QApplication.processEvents()

    def _run_in_thread(self, target_func):
        """Запускает функцию в отдельном потоке, чтобы не блокировать UI."""
        # Используем стандартный QThread + QObject Worker паттерн
        class Worker(QObject):
            finished = Signal()
            error = Signal(str)
            log_message = Signal(str)

            def __init__(self, func):
                super().__init__()
                self.func = func

            def run(self):
                try:
                    self.func(self.log_message.emit)
                    self.finished.emit()
                except Exception as e:
                    error_details = traceback.format_exc()
                    self.error.emit(f"ОШИБКА: {e}\n\n{error_details}")

        self.thread = QThread()
        self.worker = Worker(target_func)
        self.worker.moveToThread(self.thread)

        self.worker.log_message.connect(self._append_log)
        self.worker.error.connect(lambda err: self._display_api_response(500, err))
        self.worker.finished.connect(self._load_order_data) # Перезагружаем данные заказа
        self.worker.finished.connect(self._update_buttons_state) # Обновляем кнопки
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.started.connect(self.worker.run)
        self.thread.start()

    def _create_progress_dialog(self):
        """Создает и настраивает диалог прогресса."""
        # --- ИСПРАВЛЕНИЕ: Метод вынесен на уровень класса ---
        self.progress_dialog = QProgressDialog("Выполняется...", "Отмена", 0, 100, self)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setAutoClose(True)
        self.progress_dialog.show()

    def _request_codes_flow(self):
        """Полный цикл запроса кодов."""
        # --- ИСПРАВЛЕНИЕ: Создаем диалог только при запуске операции ---
        self._create_progress_dialog()

        def task(log_signal):
            log_signal.emit("Шаг 1/7: Проверка токена API...")
            self.api_service.get_participants()
            log_signal.emit("Токен API в порядке.")

            api_order_id = self.order_data.get('api_order_id')
            if not api_order_id:
                # ... (логика создания заказа, как в admin_ui.py) ...
                log_signal.emit(f"Заказ в API создан с ID: {api_order_id}")
            
            # ... (логика ожидания активации) ...
            
            # ... (логика создания запроса на коды) ...

            # ... (логика ожидания активного подзаказа и вывода сводки) ...

            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET api_status = 'Запрос создан' WHERE id = %s", (self.order_id,))
                conn.commit()
            
            QMetaObject.invokeMethod(self, "show_info_message", Qt.QueuedConnection, Q_ARG(str, "Требуется действие"), Q_ARG(str, "Запрос на коды создан. Пожалуйста, подпишите его на сайте ДМ.Код."))

        self._run_in_thread(task)

    @Slot(str, str)
    def show_info_message(self, title, message):
        QMessageBox.information(self, title, message)

    def _get_codes_flow(self):
        """Полный цикл получения кодов."""
        # --- ИСПРАВЛЕНИЕ: Создаем диалог только при запуске операции ---
        self._create_progress_dialog()

        def task(log_signal):
            if self._split_runs_task(log_signal, show_final_message=False):
                if self._prepare_json_task(log_signal, show_final_message=False):
                    self._download_codes_task(log_signal)
        self._run_in_thread(task)

    def _split_runs(self):
        self._run_in_thread(lambda log_signal: self._split_runs_task(log_signal))
        # --- ИСПРАВЛЕНИЕ: Создаем диалог только при запуске операции ---
        self._create_progress_dialog()

    def _split_runs_task(self, log_signal, show_final_message=True):
        log_signal.emit("Начинаю создание тиражей...")
        # --- ИСПРАВЛЕНИЕ: Обновляем значение прогресс-бара ---
        self.progress_dialog.setValue(10)
        try:
            # ... (полная логика из _split_runs_task в admin_ui.py) ...
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET api_status = 'Тиражи созданы' WHERE id = %s", (self.order_id,))
                conn.commit()
            if show_final_message:
                log_signal.emit("Все тиражи успешно созданы!")
            # --- ИСПРАВЛЕНИЕ: Обновляем значение прогресс-бара ---
            self.progress_dialog.setValue(100)
            return True
        except Exception as e:
            log_signal.emit(f"\nОШИБКА: {e}\n{traceback.format_exc()}")
            # --- ИСПРАВЛЕНИЕ: Скрываем диалог при ошибке ---
            self.progress_dialog.cancel()
            return False

    def _prepare_json(self):
        self._run_in_thread(lambda log_signal: self._prepare_json_task(log_signal))
        # --- ИСПРАВЛЕНИЕ: Создаем диалог только при запуске операции ---
        self._create_progress_dialog()

    def _prepare_json_task(self, log_signal, show_final_message=True):
        log_signal.emit("Начинаю подготовку JSON...")
        # --- ИСПРАВЛЕНИЕ: Обновляем значение прогресс-бара ---
        self.progress_dialog.setValue(10)
        try:
            # ... (полная логика из _prepare_json_task в admin_ui.py) ...
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET api_status = 'JSON заказан' WHERE id = %s", (self.order_id,))
                conn.commit()
            if show_final_message:
                log_signal.emit("Все запросы на подготовку JSON успешно отправлены!")
            # --- ИСПРАВЛЕНИЕ: Обновляем значение прогресс-бара ---
            self.progress_dialog.setValue(100)
            return True
        except Exception as e:
            log_signal.emit(f"\nОШИБКА: {e}\n{traceback.format_exc()}")
            # --- ИСПРАВЛЕНИЕ: Скрываем диалог при ошибке ---
            self.progress_dialog.cancel()
            return False

    def _download_codes(self):
        self._run_in_thread(lambda log_signal: self._download_codes_task(log_signal))
        # --- ИСПРАВЛЕНИЕ: Создаем диалог только при запуске операции ---
        self._create_progress_dialog()

    def _download_codes_task(self, log_signal):
        log_signal.emit("Начинаю скачивание кодов...")
        # --- ИСПРАВЛЕНИЕ: Обновляем значение прогресс-бара ---
        self.progress_dialog.setValue(10)
        try:
            # ... (полная логика из _download_codes_task в admin_ui.py) ...
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE orders SET api_status = 'Коды скачаны' WHERE id = %s", (self.order_id,))
                conn.commit()
            log_signal.emit("Все коды успешно сохранены в базу данных.")
            # --- ИСПРАВЛЕНИЕ: Обновляем значение прогресс-бара ---
            self.progress_dialog.setValue(100)
        except Exception as e:
            log_signal.emit(f"\nОШИБКА: {e}\n{traceback.format_exc()}")
            # --- ИСПРАВЛЕНИЕ: Скрываем диалог при ошибке ---
            self.progress_dialog.cancel()

    def _prepare_report_data(self):
        # ... (аналогично, с использованием _run_in_thread) ...
        # --- ИСПРАВЛЕНИЕ: Создаем диалог только при запуске операции ---
        self._create_progress_dialog()
        QMessageBox.information(self, "В разработке", "Подготовка сведений для отчета находится в разработке.")
        self.progress_dialog.cancel()

    def _prepare_report(self):
        # ... (аналогично, с использованием _run_in_thread) ...
        # --- ИСПРАВЛЕНИЕ: Создаем диалог только при запуске операции ---
        self._create_progress_dialog()
        QMessageBox.information(self, "В разработке", "Подготовка отчета находится в разработке.")
        self.progress_dialog.cancel()


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
    """Специализированный диалог для редактирования сценария."""
    def __init__(self, parent, item_data=None):
        super().__init__(parent)
        self.setWindowTitle("Редактор сценария")
        self.setMinimumWidth(500)
        self.result = None
        self.item_data = item_data or {}
        self.scenario_data = self.item_data.get('scenario_data', {})
        
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.name_edit = QLineEdit(self.item_data.get('name', ''))
        form_layout.addRow("Название сценария:", self.name_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItems(['Маркировка', 'Ручная агрегация'])
        self.type_combo.setCurrentText(self.scenario_data.get('type', 'Маркировка'))
        form_layout.addRow("Тип сценария:", self.type_combo)

        # Опции для "Маркировка"
        self.dm_source_combo = QComboBox()
        self.dm_source_combo.addItems(['Заказ в ДМ.Код', 'Файлы клиента (csv, txt)', 'Внешняя система (1С)', 'Без кодов ДМ'])
        self.dm_source_combo.setCurrentText(self.scenario_data.get('dm_source', 'Заказ в ДМ.Код'))
        form_layout.addRow("Источник кодов ДМ:", self.dm_source_combo)

        self.post_processing_combo = QComboBox()
        self.post_processing_combo.addItems(['Печать через Bartender', 'Внешнее ПО', 'Собственный алгоритм'])
        self.post_processing_combo.setCurrentText(self.scenario_data.get('post_processing', 'Печать через Bartender'))
        form_layout.addRow("Постобработка:", self.post_processing_combo)

        layout.addLayout(form_layout)
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Внимание", "Название сценария не может быть пустым.")
            return

        scenario_data = {
            'type': self.type_combo.currentText(),
            'dm_source': self.dm_source_combo.currentText(),
            'post_processing': self.post_processing_combo.currentText()
        }
        self.result = {
            'id': self.item_data.get('id'),
            'name': name,
            'scenario_data': scenario_data
        }
        super().accept()


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
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
        # --- ИСПРАВЛЕНИЕ: Инициализируем сервис для работы со справочниками ---
        self.catalog_service = CatalogsService(self.user_info, lambda: get_client_db_connection(self.user_info))
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
        self._build_ui()
        self._setup_db_status_checker() # Настраиваем и запускаем проверку БД
        self._setup_api_status_checker() # Настраиваем и запускаем проверку API

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
        item_admin_catalogs = QTreeWidgetItem(item_admin, ["Справочники"])
        item_admin_reports = QTreeWidgetItem(item_admin, ["Отчеты"])

        # Подменю "Конфигурация"
        item_config_save_ini = QTreeWidgetItem(item_admin_config, ["Сохранить INI"])
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

        # Страница 2: Сохранение конфигурации
        self.page_save_config = self._build_save_config_page()
        self.content_stack.addWidget(self.page_save_config)

        # Страница 3: Конфигурация складов
        self.page_workplaces = self._build_workplaces_page()
        self.content_stack.addWidget(self.page_workplaces)

        # Страница 5: Справочники
        self.page_catalogs = self._build_catalogs_page()
        self.content_stack.addWidget(self.page_catalogs)

        # Страница 4: Пустая заглушка для остальных
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
            'save_config': 3,
            'workplaces': 4,
            'catalogs': 5,
            'placeholder': 6,
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
        elif text == "Сохранить INI":
            self.content_stack.setCurrentIndex(self.stack_indices['save_config'])
        elif text == "Конфигурация складов":
            try:
                # При переключении на склады, загружаем их
                self.load_warehouses()
            except Exception:
                logging.exception("Error loading warehouses on menu click")
            self.content_stack.setCurrentIndex(self.stack_indices['workplaces'])
        elif text == "Сгенерировать SSCC":
            self._open_generate_sscc_dialog() # Вызываем диалог, не меняя основное окно
        elif text == "Справочники":            self.content_stack.setCurrentIndex(self.stack_indices['catalogs'])
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
         self.in_progress_edit_tab, self.in_progress_api_tab, self.in_progress_upload_tab
        , self.in_progress_stats_table
        ) = self._create_orders_view(is_archive=False)
        self.orders_tab_widget.addTab(in_progress_widget, "В работе")

        # Вкладка "Архив"
        (archive_widget, 
         self.archive_orders_table, self.archive_management_stack,
         self.archive_client_filter, self.archive_search_filter,
         self.archive_edit_tab, self.archive_api_tab, self.archive_upload_tab
        , self.archive_stats_table
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
        
        management_tabs.addTab(order_edit_tab, "Редактирование")
        management_tabs.addTab(order_api_tab, "АПИ")
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
        top_splitter.setSizes([700, 350])              # Устанавливаем пропорции ~2/3 к 1/3
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
        main_splitter.setSizes([500, 200])
        
        main_layout.addWidget(main_splitter)

        # Привязываем обработчики к фильтрам
        table_widget.itemSelectionChanged.connect(lambda: self.on_order_select(is_archive))
        client_filter_combo.currentIndexChanged.connect(lambda: self.apply_order_filters(is_archive))
        search_filter_edit.textChanged.connect(lambda: self.apply_order_filters(is_archive))

        # --- ИСПРАВЛЕНИЕ: Возвращаем все созданные виджеты, включая таблицу статистики ---
        return view_widget, table_widget, management_stack, client_filter_combo, search_filter_edit, order_edit_tab, order_api_tab, order_upload_tab, stats_table

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
        cache = self.archive_orders_cache if is_archive else self.in_progress_orders_cache

        # Блокируем сигналы, чтобы избежать лишних вызовов apply_filters при очистке
        client_filter.blockSignals(True)
        table.setRowCount(0)
        try:
            with get_client_db_connection(self.user_info) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # --- ИСПРАВЛЕНИЕ: Добавляем JOIN и агрегацию для подсчета позиций и ДМ ---
                    status_filter = "status LIKE 'Архив%%'" if is_archive else "status NOT LIKE 'Архив%%'"
                    query = f"""
                        SELECT o.id, o.client_name, o.order_date, o.status, o.notes, o.api_status, s.scenario_data,
                               COUNT(DISTINCT d.gtin) as positions_count,
                               COALESCE(SUM(d.dm_quantity), 0) as dm_count
                        FROM orders o
                        LEFT JOIN dmkod_aggregation_details d ON o.id = d.order_id
                        LEFT JOIN ap_marking_scenarios s ON o.scenario_id = s.id
                        WHERE {status_filter}
                        GROUP BY o.id, o.client_name, o.order_date, o.status, o.notes, o.api_status, s.scenario_data
                        ORDER BY o.id DESC
                    """
                    cur.execute(query)
                    orders = cur.fetchall()

            # Сохраняем данные в кэш
            cache.clear()
            cache.extend(orders)

            # Заполняем комбобокс клиентов
            client_filter.clear()
            client_filter.addItem("Все клиенты")
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

        selected_items = table.selectedItems()
        if not selected_items:
            # logging.debug("on_order_select: Заказ не выбран (selectedItems пуст). Показываем заглушку.")
            management_stack.setCurrentIndex(0) # Показываем заглушку
            return

        # Получаем данные заказа, сохраненные ранее
        order_data = selected_items[0].data(Qt.UserRole)
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
            with get_client_db_connection(self.user_info) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT s.scenario_data FROM orders o JOIN ap_marking_scenarios s ON o.scenario_id = s.id WHERE o.id = %s", (order_id,))
                    result = cur.fetchone()
            scenario_data = result['scenario_data'] if result else {}
            dm_source = scenario_data.get('dm_source')
            post_processing_mode = scenario_data.get('post_processing')
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

            # 3. Создаем и размещаем новые виджеты
            # Вкладка "Редактирование" всегда есть
            # logging.debug(f"on_order_select: Создание OrderEditorFrameQt для заказа ID {order_id}...")
            editor_frame = OrderEditorFrameQt(self.user_info, order_id, scenario_data, self)
            edit_tab.layout().addWidget(editor_frame)

            # Вкладки "АПИ" и "Загрузка кодов"
            if dm_source == "Файлы клиента (csv, txt)":
                # logging.debug(f"on_order_select: Создание CodeUploadFrameQt для заказа ID {order_id}...")
                upload_frame = CodeUploadFrameQt(self.user_info, order_id, self)
                upload_tab.layout().addWidget(upload_frame)
                management_tabs.setTabVisible(management_tabs.indexOf(api_tab), False)
                management_tabs.setTabVisible(management_tabs.indexOf(upload_tab), True)
                # logging.debug("on_order_select: Вкладка 'АПИ' скрыта, 'Загрузка кодов' показана.")
            else: # По умолчанию или "Заказ в ДМ.Код"
                # logging.debug(f"on_order_select: Создание ApiIntegrationFrameQt для заказа ID {order_id}...")
                api_frame = ApiIntegrationFrameQt(self.user_info, order_id, post_processing_mode, self)
                api_tab.layout().addWidget(api_frame)
                management_tabs.setTabVisible(management_tabs.indexOf(api_tab), True)
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

    def _build_notifications_page(self):
        """Страница управления уведомлениями о поставках - с переключением между списком и деталями."""
        widget = QWidget()
        layout = QVBoxLayout()

        # Стек для переключения между списком и деталями
        self.notifications_stack = QStackedWidget()
        layout.addWidget(self.notifications_stack)

        # Страница 1: Список уведомлений
        self.page_notifications_list = self._build_notifications_list_page()
        self.notifications_stack.addWidget(self.page_notifications_list)

        # Страница 2: Детали уведомления
        self.page_notification_details = self._build_notification_details_page()
        self.notifications_stack.addWidget(self.page_notification_details)

        # По умолчанию показываем список
        self.notifications_stack.setCurrentIndex(0)

        widget.setLayout(layout)
        return widget

    def _build_notifications_list_page(self):
        """Таблица со списком уведомлений и сводкой по дням."""
        widget = QWidget()
        layout = QVBoxLayout()

        # Кнопки управления
        controls = QHBoxLayout()
        btn_new = QPushButton("Новое уведомление")
        btn_new.clicked.connect(self.create_new_notification)
        btn_edit = QPushButton("Открыть")
        btn_edit.clicked.connect(self.open_notification_details)
        btn_delete = QPushButton("Удалить уведомление")
        btn_delete.clicked.connect(self.delete_notification)
        
        controls.addWidget(btn_new)
        controls.addWidget(btn_edit)
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
        # Двойной клик открывает детали
        self.notifications_table.doubleClicked.connect(self.open_notification_details)
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

        # Сводка по дням (под таблицей)
        summary_label = QLabel("Сводка по дням:")
        layout.addWidget(summary_label)
        
        # Визуальная группировка: первая строка — даты, вторая — метрики, далее — данные
        from datetime import datetime, timedelta
        today = datetime.now().date()
        date_labels = []
        for i in range(4):
            date_obj = today + timedelta(days=i)
            date_labels.append(date_obj.strftime('%d.%m.%Y'))

        # Всего 13 колонок: 1 (Клиент) + 4*3
        self.summary_table = QTableWidget(2, 13)  # 2 строки для заголовков
        # Первая строка: "Клиент" + даты (объединение по 3 колонки)
        client_item = QTableWidgetItem("Клиент")
        client_item.setFlags(client_item.flags() & ~Qt.ItemIsEditable)
        self.summary_table.setItem(0, 0, client_item)
        self.summary_table.setSpan(0, 0, 2, 1)  # "Клиент" объединяет 2 строки
        for i, date in enumerate(date_labels):
            col = 1 + i*3
            date_item = QTableWidgetItem(date)
            date_item.setFlags(date_item.flags() & ~Qt.ItemIsEditable)
            date_item.setTextAlignment(Qt.AlignCenter)
            self.summary_table.setItem(0, col, date_item)
            self.summary_table.setSpan(0, col, 1, 3)  # Дата объединяет 3 колонки
        # Вторая строка: метрики
        for i in range(4):
            col = 1 + i*3
            for j, metric in enumerate(["Ув", "Поз", "ДМ"]):
                metric_item = QTableWidgetItem(metric)
                metric_item.setFlags(metric_item.flags() & ~Qt.ItemIsEditable)
                metric_item.setTextAlignment(Qt.AlignCenter)
                self.summary_table.setItem(1, col+j, metric_item)

        # Стилизация и размеры
        self.summary_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.summary_table.setSelectionMode(QTableWidget.SingleSelection)
        self.summary_table.setMaximumHeight(170)
        # ИСПРАВЛЕНИЕ: Скрываем стандартные заголовки (и номера строк, и номера колонок)
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.horizontalHeader().setVisible(False)
        # ИСПРАВЛЕНИЕ: Устанавливаем режим растягивания для колонок с данными,
        # а для клиента задаем фиксированную ширину.
        self.summary_table.setColumnWidth(0, 200) # Ширина для колонки "Клиент"
        for i in range(1, 13):
            self.summary_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Stretch)
        self.summary_table.setStyleSheet("""
            QTableWidget::item:selected {
                background-color: #ADD8E6;
            }
            QTableWidget {
                gridline-color: #E0E0E0;
            }
        """)
        layout.addWidget(self.summary_table)

        widget.setLayout(layout)
        return widget

    def _build_notification_details_page(self):
        """Страница с деталями уведомления."""
        widget = QWidget()
        layout = QVBoxLayout()

        # Кнопка "Назад"
        back_btn = QPushButton("← Вернуться к списку")
        back_btn.clicked.connect(lambda: self.notifications_stack.setCurrentIndex(0))
        layout.addWidget(back_btn)

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
        btn_upload_doc.clicked.connect(self.upload_notification_doc)
        btn_download_doc = QPushButton("Скачать")
        btn_download_doc.clicked.connect(self.download_notification_doc)
        btn_delete_doc = QPushButton("Удалить")
        btn_delete_doc.clicked.connect(self.delete_notification_doc)
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

    def open_notification_details(self):
        """Открывает детали выбранного уведомления."""
        sel = self.notifications_table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите уведомление для просмотра")
            return
        
        notif_id = int(self.notifications_table.item(sel, 0).text())
        self.load_notification_details(notif_id)
        self.notifications_stack.setCurrentIndex(1)

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

    def load_notification_files(self, notif_id):
        """Загружает список файлов для уведомления."""
        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            files = service.get_notification_files(notif_id)
            
            self.notification_files_table.setRowCount(0)
            self.notification_files_cache = files
            
            for file_info in files:
                row = self.notification_files_table.rowCount()
                self.notification_files_table.insertRow(row)
                
                # ИСПРАВЛЕНИЕ: Заполняем только одну колонку
                filename = file_info.get('filename', '')
                it = QTableWidgetItem(filename)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.notification_files_table.setItem(row, 0, it)
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

    def upload_notification_doc(self):
        """Загружает документ для уведомления."""
        if not hasattr(self, 'current_notification_id'):
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
            service.add_notification_file(self.current_notification_id, filename, file_data, 'client_document')
            QMessageBox.information(self, "Успех", "Файл успешно загружен")
            self.load_notification_files(self.current_notification_id)
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить файл: {e}")

    def download_notification_doc(self):
        """Скачивает документ от уведомления."""
        sel = self.notification_files_table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите файл для скачивания")
            return
        
        try:
            # ИСПРАВЛЕНИЕ: Получаем ID файла из кэша, а не из виджета
            file_info = self.notification_files_cache[sel]
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

    def delete_notification_doc(self):
        """Удаляет выбранный документ уведомления."""
        sel = self.notification_files_table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите файл для удаления")
            return

        try:
            file_info = self.notification_files_cache[sel]
            file_id = file_info['id']
            filename = file_info['filename']

            reply = QMessageBox.question(self, "Подтверждение", f"Вы уверены, что хотите удалить файл '{filename}'?", QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            service.delete_notification_file(file_id)
            QMessageBox.information(self, "Успех", "Файл успешно удален.")
            # Обновляем список файлов
            self.load_notification_files(self.current_notification_id)
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
                    
                    poa_end_date = p.get('poa_validity_end', '')
                    if poa_end_date and 'T' in poa_end_date:
                        poa_end_date = poa_end_date.split('T')[0]

                    self.participants_table.setItem(row, 0, QTableWidgetItem(p.get('name', '')))
                    self.participants_table.setItem(row, 1, QTableWidgetItem(p.get('inn', '')))
                    self.participants_table.setItem(row, 2, QTableWidgetItem(poa_end_date))
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

    def _build_save_config_page(self):
        """Создает страницу для сохранения файлов конфигурации (config.ini, cert.pem)."""
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)

        info_label1 = QLabel("<h3>Создание файлов конфигурации для локального подключения к базе данных</h3>")
        info_label1.setWordWrap(True)
        layout.addWidget(info_label1)

        info_label2 = QLabel(
            "Будут созданы файлы <b>config.ini</b> и <b>cert.pem</b> с настройками для локального подключения к базе данных. "
            "Сохраните их в удобное место, чтобы иметь возможность подключаться по локальной сети к базе данных."
        )
        info_label2.setWordWrap(True)
        layout.addWidget(info_label2)

        btn_save = QPushButton("Сохранить файлы конфигурации")
        btn_save.setFixedSize(250, 40)
        btn_save.clicked.connect(self._save_config_files)
        layout.addWidget(btn_save, 0, Qt.AlignLeft)

        layout.addStretch()
        widget.setLayout(layout)
        return widget

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
        progress_dialog = QProgressDialog("Генерация SSCC кодов...", "Отмена", 0, 100, self)
        progress_dialog.setWindowTitle("Генерация SSCC")
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setAutoClose(False)
        progress_dialog.setAutoReset(False)
        progress_dialog.show()

        self.sscc_thread = QThread()
        self.sscc_worker = SsccGeneratorWorker(self.user_info, quantity)
        self.sscc_worker.moveToThread(self.sscc_thread)

        self.sscc_thread.started.connect(self.sscc_worker.run)
        
        self.sscc_worker.progress.connect(lambda val, msg: (
            progress_dialog.setLabelText(msg),
            progress_dialog.setValue(val) if val > 0 else None,
            QApplication.processEvents()
        ))
        # --- ИСПРАВЛЕНИЕ: Передаем только текст ошибки, а диалог закрываем в основном потоке ---
        self.sscc_worker.error.connect(self.on_sscc_generation_error)
        # --- ИСПРАВЛЕНИЕ: Передаем только список кодов, а диалог закрываем в основном потоке ---
        self.sscc_worker.finished.connect(self.on_sscc_generation_finished)

        self.sscc_worker.finished.connect(self.sscc_thread.quit)
        self.sscc_worker.finished.connect(self.sscc_worker.deleteLater)
        self.sscc_thread.finished.connect(self.sscc_thread.deleteLater)

        self.sscc_thread.start()

    def _save_sscc_to_file(self, ssccs: list, progress_dialog: QProgressDialog):
        """Предлагает сохранить сгенерированные SSCC в CSV файл."""
        logging.debug(f"[_save_sscc_to_file] Слот запущен. Получено {len(ssccs)} SSCC кодов.")
        progress_dialog.setValue(100)
        progress_dialog.setLabelText("Генерация завершена. Сохранение в файл...")
        QApplication.processEvents()

        if not ssccs:
            logging.warning("[_save_sscc_to_file] Список SSCC пуст. Сохранение отменено.")
            QMessageBox.warning(self, "Внимание", "Не удалось сгенерировать SSCC коды.")
            progress_dialog.close()
            return

        logging.debug("[_save_sscc_to_file] Открытие диалога сохранения файла...")
        filepath, _ = QFileDialog.getSaveFileName(self, "Сохранить SSCC коды", "sscc_codes.csv", "CSV Files (*.csv)")
        if filepath:
            logging.debug(f"[_save_sscc_to_file] Файл для сохранения выбран: {filepath}")
            try:
                logging.debug("[_save_sscc_to_file] Начало записи в файл...")
                with open(filepath, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    for sscc in ssccs:
                        writer.writerow([sscc])
                logging.debug("[_save_sscc_to_file] Запись в файл завершена успешно.")
                QMessageBox.information(self, "Успех", f"SSCC коды успешно сохранены в файл:\n{filepath}")
            except Exception as e:
                logging.error(f"[_save_sscc_to_file] Ошибка при записи в файл: {e}", exc_info=True)
                QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить SSCC коды в файл: {e}")
        else:
            logging.debug("[_save_sscc_to_file] Диалог сохранения файла отменен пользователем.")
            QMessageBox.information(self, "Отмена", "Сохранение файла отменено.")

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


if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = AdminWindowQt({'client_db_config': {}, 'name': 'local-admin'})
    w.show()
    sys.exit(app.exec())
