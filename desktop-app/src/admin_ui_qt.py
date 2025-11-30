from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QMessageBox, QApplication, QLabel, QFileDialog, QTextEdit,
    QLineEdit, QHeaderView, QDateEdit, QDialog, QFormLayout, QComboBox,
    QInputDialog, QTreeWidget, QTreeWidgetItem, QStackedWidget, QAbstractItemView
)
from PySide6.QtCore import Qt, Slot, QDate
from PySide6.QtGui import QColor
import sys
import traceback
import logging

import pandas as pd
from .db_connector import get_client_db_connection
from .catalogs_service import CatalogsService
from .supply_notification_service import SupplyNotificationService
import psycopg2
import psycopg2.extras
import base64
import os


class AdminWindowQt(QMainWindow):
    """Переносная версия tkinter админ-интерфейса на PySide6 с левым меню и правой стеком контента."""
    def __init__(self, user_info: dict):
        super().__init__()
        self.user_info = user_info
        self.setWindowTitle(f"Admin - {user_info.get('name', '')}")
        self.resize(1200, 700)
        self._build_ui()

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
            'reports': item_admin_reports,
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

        # Страница 2: Сохранение конфигурации
        self.page_save_config = self._build_save_config_page()
        self.content_stack.addWidget(self.page_save_config)

        # Страница 3: Конфигурация складов
        self.page_workplaces = self._build_workplaces_page()
        self.content_stack.addWidget(self.page_workplaces)

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
            'save_config': 2,
            'workplaces': 3,
            'placeholder': 4,
        }

        # Собираем основной layout
        main_layout.addWidget(self.menu_tree, 1)
        main_layout.addWidget(self.content_stack, 4)
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

        # Показываем приветственную страницу по умолчанию
        self.content_stack.setCurrentIndex(self.stack_indices['welcome'])

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
        elif text == "Сохранить INI":
            self.content_stack.setCurrentIndex(self.stack_indices['save_config'])
        elif text == "Конфигурация складов":
            try:
                # При переключении на склады, загружаем их
                self.load_warehouses()
            except Exception:
                logging.exception("Error loading warehouses on menu click")
            self.content_stack.setCurrentIndex(self.stack_indices['workplaces'])
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
        btn_delete = QPushButton("Удалить")
        btn_delete.clicked.connect(self.delete_notification)
        btn_archive = QPushButton("В архив")
        btn_archive.clicked.connect(self.archive_notification)
        
        controls.addWidget(btn_new)
        controls.addWidget(btn_edit)
        controls.addWidget(btn_delete)
        controls.addWidget(btn_archive)
        controls.addStretch()
        layout.addLayout(controls)

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
        actions_layout = QHBoxLayout()
        btn_save = QPushButton("Сохранить изменения")
        btn_save.clicked.connect(self.save_notification_changes)
        btn_create_order = QPushButton("Создать заказ")
        btn_create_order.clicked.connect(self.create_order_from_notification)
        actions_layout.addWidget(btn_save)
        actions_layout.addWidget(btn_create_order)
        actions_layout.addStretch()
        general_layout.addLayout(actions_layout)

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
            self.notifications_table.setRowCount(0)
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            notifications = service.get_notifications_with_counts()

            for notif in notifications:
                row = self.notifications_table.rowCount()
                self.notifications_table.insertRow(row)
                
                # Обработка product_groups (может быть список, строка или None)
                product_groups = notif.get('product_groups', '')
                if isinstance(product_groups, list):
                    # Если это список словарей, извлекаем значения
                    if product_groups and isinstance(product_groups[0], dict):
                        product_groups = ', '.join([str(pg.get('name', '')) if isinstance(pg, dict) else str(pg) for pg in product_groups])
                    else:
                        product_groups = ', '.join([str(pg) for pg in product_groups])
                elif product_groups is None:
                    product_groups = ''
                
                items = [
                    str(notif.get('id', '')),  # Скрытая колонка ID
                    notif.get('scenario_name', ''),
                    notif.get('client_name', ''),
                    str(product_groups),
                    str(notif.get('planned_arrival_date', '')),
                    notif.get('vehicle_number', ''),
                    notif.get('status', ''),
                    str(notif.get('positions_count', 0)),  # Новая колонка: позиции
                    str(notif.get('dm_count', 0))  # Новая колонка: коды ДМ
                ]
                
                # Определяем цвет фона в зависимости от статуса
                status = notif.get('status', '')
                bg_color = QColor("white")  # По умолчанию белый
                
                if status == 'Проект':
                    bg_color = QColor("#FFB6C6")  # Светло-розовый (lightpink)
                elif status == 'Ожидание':
                    bg_color = QColor("#FFFFE0")  # Светло-жёлтый (light yellow)
                elif status == 'Заказ создан':
                    bg_color = QColor("#90EE90")  # Светло-зелёный (light green)
                
                for col, text in enumerate(items):
                    it = QTableWidgetItem(str(text))
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    it.setBackground(bg_color)
                    self.notifications_table.setItem(row, col, it)
            
            # Загружаем сводку
            self.load_summary_data()
        except (Exception, psycopg2.Error) as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить уведомления: {e}")

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
            # ИСПРАВЛЕНИЕ: Используем правильное имя метода
            success, message, needs_confirmation = service.create_or_recreate_order_from_notification(self.current_notification_id)
            if success:
                QMessageBox.information(self, "Успех", message)
                self.load_notifications()
            else:
                QMessageBox.warning(self, "Внимание", message)
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

    def archive_notification(self):
        """Архивирует выбранное уведомление."""
        sel = self.notifications_table.currentRow()
        if sel < 0:
            QMessageBox.warning(self, "Внимание", "Выберите уведомление для архивирования")
            return
        notif_id = int(self.notifications_table.item(sel, 0).text())
        try:
            service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))
            service.archive_notification(notif_id)
            QMessageBox.information(self, "Успех", "Уведомление архивировано")
            self.load_notifications()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось архивировать уведомление: {e}")

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
