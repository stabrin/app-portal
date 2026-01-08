# desktop-app/src/operator_work_ui.py
# Окно оператора с меню и основным полем.
import logging
from PySide6.QtWidgets import QMainWindow, QLabel, QVBoxLayout, QWidget, QPushButton, QMessageBox, QSplitter, QTreeWidget, QTreeWidgetItem, QStackedWidget, QTextEdit, QHBoxLayout, QComboBox, QLineEdit, QGroupBox, QFormLayout, QListWidget
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
        layout = QVBoxLayout(page)
        
        layout.addWidget(QLabel("Список GTIN для задания:"))
        
        self.gtin_list = QListWidget()
        gtins = self.task_info.get('gtins', [])
        for gtin in gtins:
            self.gtin_list.addItem(gtin)
        layout.addWidget(self.gtin_list)
        
        print_btn = QPushButton("Печать марки для выбранного GTIN")
        print_btn.clicked.connect(self._print_selected_gtin)
        layout.addWidget(print_btn)
        
        layout.addStretch()

    def _print_selected_gtin(self):
        """Печатает марку для выбранного GTIN."""
        selected_item = self.gtin_list.currentItem()
        if not selected_item:
            QMessageBox.warning(self, "Ошибка", "Выберите GTIN.")
            return
        gtin = selected_item.text()
        # TODO: Использовать выбранный макет и принтер из тестирования
        QMessageBox.information(self, "Печать", f"Печать марки для GTIN: {gtin}")

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
        # Здесь должна быть логика печати
        # ...
        logging.info(f"Отправка на печать на принтер: {self.printer_combo.currentText()}")
        self.equipment_check_state = "awaiting_print_scan"
        self.dm_input.clear()
        self.dm_input.setPlaceholderText("Отсканируйте распечатанную этикетку")
        self.dm_input.setFocus()
