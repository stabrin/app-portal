# desktop-app/src/operator_work_ui.py
# Окно оператора с меню и основным полем.
import logging
from PySide6.QtWidgets import QMainWindow, QLabel, QVBoxLayout, QWidget, QPushButton, QMessageBox, QSplitter, QTreeWidget, QTreeWidgetItem, QStackedWidget, QTextEdit, QHBoxLayout, QComboBox, QLineEdit, QGroupBox, QFormLayout, QListWidget, QSpinBox, QDateEdit
from PySide6.QtCore import Qt
from PySide6.QtPrintSupport import QPrinterInfo
from PySide6.QtGui import QPixmap, QImage
from .printing_service import PrintingService

class OperatorWorkWindow(QMainWindow):
    """
    Окно оператора с меню слева и основным полем справа.
    """
    def __init__(self, task_service, catalogs_service, user_info, task_info, **kwargs):
        super().__init__(kwargs.get('parent'))
        self.task_service = task_service
        self.catalogs_service = catalogs_service
        self.task_info = task_info
        self.user_info = user_info

        self.equipment_check_state = "idle" # Состояния: idle, awaiting_screen_scan, awaiting_print_scan
        # Переменные для тестирования
        self.test_dm = None  # Первое datamatrix из задания
        self.selected_layout = None
        self.selected_printer = None
        self.test_printed = False

        self.setWindowTitle(f"Работа по задаче №{self.task_info.get('task_id')} - Оператор #{self.task_info.get('employee_id')}")
        self.setMinimumSize(1000, 700)
        
        central_widget = QWidget()
        main_layout = QHBoxLayout(central_widget)
        
        # Создаем сплиттер
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)
        
        # Левая часть: меню (1/5)
        self.menu_tree = QTreeWidget()
        self.menu_tree.setHeaderHidden(True)
        self.menu_tree.setMaximumWidth(200)  # Ограничим ширину для пропорции ~1/5
        self._build_menu()
        splitter.addWidget(self.menu_tree)
        
        # Правая часть: основное поле (4/5)
        self.content_stack = QStackedWidget()
        self._build_content_pages()
        splitter.addWidget(self.content_stack)
        
        # Устанавливаем пропорции
        splitter.setSizes([200, 800])  # Пропорции 1/5 и 4/5
        
        self.setCentralWidget(central_widget)
        
        # Подключаем обработчик клика по меню
        self.menu_tree.itemClicked.connect(self._on_menu_clicked)

    def _build_menu(self):
        """Строит меню слева."""
        root = QTreeWidgetItem(self.menu_tree, ["Меню"])
        QTreeWidgetItem(root, ["Проверка оборудования"])
        QTreeWidgetItem(root, ["Инструкции"])
        QTreeWidgetItem(root, ["Задание"])
        QTreeWidgetItem(root, ["Завершить работу"])
        self.menu_tree.expandAll()

    def _build_content_pages(self):
        """Строит страницы контента."""
        # Страница проверки оборудования
        equipment_page = QWidget()
        self._build_equipment_page(equipment_page)
        self.content_stack.addWidget(equipment_page)
        
        # Страница инструкций
        instructions_page = QWidget()
        layout = QVBoxLayout(instructions_page)
        text_edit = QTextEdit()
        text_edit.setPlainText("Инструкции по работе:\n\n1. Выполните проверку оборудования.\n2. Ознакомьтесь с заданием.\n3. Выполните задачу.\n4. Завершите работу.")
        text_edit.setReadOnly(True)
        layout.addWidget(text_edit)
        self.content_stack.addWidget(instructions_page)
        
        # Страница задания
        task_page = QWidget()
        self._build_task_page(task_page)
        self.content_stack.addWidget(task_page)
        
        # По умолчанию показываем задание
        self.content_stack.setCurrentIndex(2)

    def _build_task_page(self, page):
        """Строит страницу задания с списком GTIN."""
        main_layout = QVBoxLayout(page)
        settings = self.task_info.get('settings_json', {})
        
        # --- НОВЫЙ ИНТЕРФЕЙС ДЛЯ ВЫПОЛНЕНИЯ ЗАДАНИЯ ---
        task_group = QGroupBox("Выполнение задания")
        form_layout = QFormLayout(task_group)
        
        # Поле для сканирования товара (GTIN, EAN, etc.)
        self.scan_product_input = QLineEdit()
        self.scan_product_input.setPlaceholderText("Отсканируйте код товара (GTIN, EAN)...")
        form_layout.addRow("1. Код товара:", self.scan_product_input)
        
        # Поле для количества
        self.quantity_spinbox = QSpinBox()
        self.quantity_spinbox.setRange(1, 10000)
        self.quantity_spinbox.setValue(1)
        form_layout.addRow("2. Количество в упаковке:", self.quantity_spinbox)
        
        # --- Динамические поля для уточнений ---
        self.refine_widgets = {} # Словарь для хранения виджетов
        
        if settings.get('refine_batch'):
            self.refine_batch_input = QLineEdit()
            self.refine_widgets['batch'] = form_layout.addRow("Уточнить партию:", self.refine_batch_input)
        
        if settings.get('refine_country'):
            self.refine_country_input = QLineEdit() # В будущем можно заменить на QComboBox
            self.refine_widgets['country'] = form_layout.addRow("Уточнить страну:", self.refine_country_input)
            
        if settings.get('refine_prod_date'):
            self.refine_prod_date_input = QDateEdit(calendarPopup=True)
            self.refine_prod_date_input.setDate(Qt.QDate.currentDate())
            self.refine_widgets['prod_date'] = form_layout.addRow("Уточнить дату произв.:", self.refine_prod_date_input)
            
        # Кнопка для запуска процесса
        self.print_labels_button = QPushButton("Напечатать марки")
        self.print_labels_button.clicked.connect(self._find_gtin_and_print)
        form_layout.addRow(self.print_labels_button)
        
        main_layout.addWidget(task_group)
        main_layout.addStretch()

    def _find_gtin_and_print(self):
        """Основная логика: поиск GTIN и запуск печати."""
        scanned_code = self.scan_product_input.text().strip()
        if not scanned_code:
            QMessageBox.warning(self, "Ошибка", "Отсканируйте код товара.")
            return
            
        # Здесь будет логика поиска GTIN через catalogs_service
        # ...
        
        QMessageBox.information(self, "В разработке", f"Запущен поиск для кода: {scanned_code}")

    def _show_create_mapping_dialog(self, unknown_code):
        """Показывает диалог для создания нового сопоставления."""
        QMessageBox.information(self, "В разработке", f"Здесь будет диалог создания сопоставления для кода: {unknown_code}")

    def _build_equipment_page(self, page):
        """Строит страницу проверки оборудования."""
        layout = QVBoxLayout(page)
        
        settings_json = self.task_info.get('settings_json', {})
        sscc_source = settings_json.get('sscc_source', '')
        
        if sscc_source == "Напечатаны заранее":
            # Логика для тестирования
            group = QGroupBox("Тестирование оборудования")
            form_layout = QFormLayout(group)
            
            # Выбор макета
            self.layout_combo = QComboBox()
            try:
                layouts = self.catalogs_service.get_print_layouts()
                for layout_info in layouts:
                    self.layout_combo.addItem(layout_info['name'], layout_info['id'])
            except:
                self.layout_combo.addItems(["Макет 1", "Макет 2"])  # Заглушка
            form_layout.addRow("Макет:", self.layout_combo)
            
            # Выбор принтера
            self.printer_combo = QComboBox()
            try:
                printers = QPrinterInfo.availablePrinterNames()
                if printers:
                    self.printer_combo.addItems(printers)
                else:
                    self.printer_combo.addItems(["Нет доступных принтеров"])
            except Exception as e:
                self.printer_combo.addItems(["Ошибка получения принтеров"])
                print(f"Ошибка получения списка принтеров: {e}")
            form_layout.addRow("Принтер:", self.printer_combo)
            
            # Кнопка генерации предпросмотра
            preview_btn = QPushButton("Сгенерировать предпросмотр")
            preview_btn.clicked.connect(self._generate_preview)
            form_layout.addRow(preview_btn)
            
            # Предпросмотр изображения
            self.preview_label = QLabel("Здесь будет предпросмотр этикетки")
            self.preview_label.setAlignment(Qt.AlignCenter)
            self.preview_label.setMinimumHeight(200)
            form_layout.addRow("Предпросмотр:", self.preview_label)
            
            # Поле для сканирования ДМ
            self.dm_input = QLineEdit()
            self.dm_input.setPlaceholderText("Ожидание...")
            self.dm_input.returnPressed.connect(self._process_scan)
            form_layout.addRow("DataMatrix:", self.dm_input)
            
            layout.addWidget(group)
        else:
            layout.addWidget(QLabel("Проверка оборудования: Функционал не реализован для данной конфигурации."))
        
        layout.addStretch()

    def _on_menu_clicked(self, item, column):
        """Обработчик клика по пункту меню."""
        text = item.text(column)
        if text == "Проверка оборудования":
            self.content_stack.setCurrentIndex(0)
        elif text == "Инструкции":
            self.content_stack.setCurrentIndex(1)
        elif text == "Задание":
            self.content_stack.setCurrentIndex(2)
        elif text == "Завершить работу":
            self._finish_work()

    def _finish_work(self):
        """Завершает сессию и закрывает окно."""
        session_id = self.task_info.get('session_id')
        if session_id:
            try:
                self.task_service.close_session(session_id)
                QMessageBox.information(self, "Успех", "Сессия завершена.")
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось завершить сессию: {e}")
        self.close()

    def closeEvent(self, event):
        """Обработчик закрытия окна (крестиком)."""
        session_id = self.task_info.get('session_id')
        if session_id:
            try:
                self.task_service.close_session(session_id)
            except Exception as e:
                # Логируем ошибку, но не показываем диалог, так как окно закрывается
                print(f"Ошибка при закрытии сессии: {e}")
        event.accept()

    def _generate_preview(self):
        """Генерирует предпросмотр марки."""
        try:
            # Получить первую запись из task_datamatrix_pool
            dm_data = self.task_service.get_first_datamatrix_for_task(self.task_info['task_id'])
            if not dm_data:
                QMessageBox.warning(self, "Ошибка", "Нет доступных кодов DataMatrix для задачи.")
                return
            
            self.test_dm = dm_data['datamatrix']
            
            # Получить выбранный макет
            layout_id = self.layout_combo.currentData()
            if not layout_id:
                QMessageBox.warning(self, "Ошибка", "Выберите макет.")
                return
            
            # Найти выбранный макет в списке
            layouts = self.catalogs_service.get_print_layouts()
            template = None
            for layout in layouts:
                if layout['id'] == layout_id:
                    template = layout
                    break
            if not template:
                QMessageBox.warning(self, "Ошибка", "Макет не найден.")
                return            
            
            # Подготовить данные для генерации
            data = {
                'datamatrix': dm_data['datamatrix'],
                'gtin': dm_data['gtin'],
                'name': dm_data.get('name', ''),
                'description_1': dm_data.get('description_1', ''),
                'description_2': dm_data.get('description_2', ''),
                'description_3': dm_data.get('description_3', ''),
                'serial_number': dm_data.get('serial_number', ''),
                'batch_number': dm_data.get('batch_number', ''),
                'production_date': dm_data.get('production_date', ''),
                'best_before_date': dm_data.get('best_before_date', ''),
                'expiry_date': dm_data.get('expiry_date', ''),
                'origin_country': dm_data.get('origin_country', ''),
                'quantity': dm_data.get('quantity', '')
            }
            
            # Генерировать изображение
            image = PrintingService.generate_label_image(template, data, self.user_info)
            if image:
                # Конвертировать PIL Image в QPixmap
                image = image.convert("RGBA")
                data = image.tobytes("raw", "RGBA")
                qimage = QImage(data, image.size[0], image.size[1], QImage.Format_RGBA8888)
                pixmap = QPixmap.fromImage(qimage)
                self.preview_label.setPixmap(pixmap.scaledToWidth(300, Qt.SmoothTransformation))
                
                # Переход в режим сканирования с экрана
                self.equipment_check_state = "awaiting_screen_scan"
                self.dm_input.clear()
                self.dm_input.setPlaceholderText("Отсканируйте код с экрана")
                self.dm_input.setFocus()
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось сгенерировать изображение.")
                
        except Exception as e:
            logging.error(f"Ошибка генерации предпросмотра: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка", f"Ошибка генерации предпросмотра: {e}")

    def _process_scan(self):
        """Обрабатывает сканирование в зависимости от текущего состояния."""
        scanned = self.dm_input.text().strip()
        if not scanned:
            return

        if self.equipment_check_state == "awaiting_screen_scan":
            if scanned == self.test_dm:
                QMessageBox.information(self, "Успех", "Код с экрана считан верно. Отправка на печать...")
                self._print_test()
            else:
                QMessageBox.warning(self, "Ошибка", "Отсканированный код не совпадает с кодом на экране. Попробуйте еще раз.")
                self.dm_input.clear()

        elif self.equipment_check_state == "awaiting_print_scan":
            if scanned == self.test_dm:
                QMessageBox.information(self, "Успех", "Тестирование оборудования успешно завершено!")
                # Сохраняем настройки для сессии
                self.selected_layout = self.layout_combo.currentData()
                self.selected_printer = self.printer_combo.currentText()
                logging.info(f"Для сессии сохранены настройки: Принтер='{self.selected_printer}', Макет ID='{self.selected_layout}'")
                self.equipment_check_state = "idle"
                self.dm_input.setPlaceholderText("Тестирование завершено")
                self.dm_input.clear()
            else:
                QMessageBox.warning(self, "Ошибка", "Отсканированный код не совпадает с распечатанным. Проверьте настройки принтера и попробуйте еще раз.")
                self.dm_input.clear()

    def _print_test(self):
        """Печатает тестовую марку и переходит в режим ожидания сканирования с печати."""
        try:
            printer_name = self.printer_combo.currentText()
            layout_id = self.layout_combo.currentData()
            if not printer_name or not layout_id:
                QMessageBox.warning(self, "Ошибка", "Не выбран принтер или макет.")
                return

            # Получаем данные для печати (аналогично предпросмотру)
            dm_data = self.task_service.get_first_datamatrix_for_task(self.task_info['task_id'])
            layouts = self.catalogs_service.get_print_layouts()
            template = next((layout for layout in layouts if layout['id'] == layout_id), None)

            if not dm_data or not template:
                QMessageBox.warning(self, "Ошибка", "Не найдены данные для печати или шаблон.")
                return

            # Используем PrintingService для отправки на печать
            logging.info(f"Отправка на печать на принтер: {printer_name}")
            PrintingService.print_label_direct(
                printer_name=printer_name,
                paper_name=template.get('paper_name'), # paper_name берется из макета
                template_json=template,
                data=dm_data,
                user_info=self.user_info
            )
        except Exception as e:
            logging.error(f"Ошибка при отправке на печать: {e}", exc_info=True)
            QMessageBox.critical(self, "Ошибка печати", f"Не удалось отправить задание на печать: {e}")
            return # Прерываем переход в следующее состояние, если печать не удалась

        self.equipment_check_state = "awaiting_print_scan"
        self.dm_input.clear()
        self.dm_input.setPlaceholderText("Отсканируйте распечатанную этикетку")
        self.dm_input.setFocus()
