import io
import logging
import json
import os
import tempfile
from psycopg2 import sql
import psycopg2
from typing import Dict, Any

# Библиотеки для генерации штрихкодов и работы с Windows API
try:
    import qrcode # type: ignore
    from PIL import Image, ImageDraw, ImageFont, ImageTk, ImageWin # type: ignore
except ImportError:
    logging.warning("QR code generation libraries (qrcode, Pillow) not installed. QR code features will be limited. Install with: pip install qrcode Pillow")
    qrcode = None
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageTk = None
    ImageWin = None

try:
    from pystrich.datamatrix import DataMatrixEncoder
except ImportError:
    logging.warning("Библиотека pystrich не установлена. Генерация DataMatrix не будет работать. Установите: pip install pystrich")
    DataMatrixEncoder = None


try:
    import win32print
    import win32api
    import win32con
    import win32ui
    import win32gui
    from pywintypes import error as pywin_error
except ImportError:
    logging.warning("pywin32 not installed. Windows printing features will be limited. Install with: pip install pywin32")
    win32print = None
    win32api = None
    win32con = None
    win32ui = None
    pywin_error = None


# Добавляем импорты для редактора
try:
    import tkinter as tk
    from tkinter import ttk, simpledialog, messagebox
    import psycopg2
except ImportError:
    tk = None # Помечаем как недоступный, если среда без GUI

# Configure logging for this module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [printing_service] - %(message)s')


class PrintingService:
    """
    Сервис для генерации и печати документов.
    """

    @staticmethod
    def _get_client_db_connection(user_info: Dict[str, Any]):
        """Helper to get a client DB connection."""
        db_config = user_info.get("client_db_config")
        if not db_config:
            logging.error("Client DB configuration not found in user_info.")
            return None
        
        conn_params = {
            'host': db_config.get('db_host'), 'port': db_config.get('db_port'),
            'dbname': db_config.get('db_name'), 'user': db_config.get('db_user'),
            'password': db_config.get('db_password')
        }
        
        temp_cert_file = None
        try:
            if db_config.get('db_ssl_cert'):
                with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                    fp.write(db_config['db_ssl_cert'])
                    temp_cert_file = fp.name
                conn_params.update({'sslmode': 'verify-full', 'sslrootcert': temp_cert_file})

            conn = psycopg2.connect(**conn_params)
            return conn
        except Exception as e:
            logging.error(f"Failed to connect to client DB: {e}")
            return None
        finally:
            if temp_cert_file and os.path.exists(temp_cert_file):
                try:
                    os.remove(temp_cert_file)
                except OSError as e:
                    logging.warning(f"Could not delete temporary cert file {temp_cert_file}: {e}")

    @staticmethod
    def _fetch_data_from_db(user_info: Dict[str, Any], data_source: str) -> Optional[str]:
        """Fetches data from the client DB based on data_source (e.g., 'items.datamatrix')."""
        parts = data_source.split('.')
        if len(parts) != 2:
            logging.warning(f"Некорректный формат data_source для БД: '{data_source}'. Ожидается 'table.field'.")
            return None
        
        table_name, field_name = parts

        conn = None
        try:
            conn = PrintingService._get_client_db_connection(user_info)
            if not conn:
                return None
            
            with conn.cursor() as cur:
                # ВАЖНО: Используем безопасную параметризацию SQL из psycopg2
                query = sql.SQL("SELECT {field} FROM {table} LIMIT 1").format(
                    field=sql.Identifier(field_name),
                    table=sql.Identifier(table_name)
                )
                cur.execute(query)
                result = cur.fetchone()
                return result[0] if result else None
        except Exception as e:
            logging.error(f"Ошибка при получении данных из БД для '{data_source}': {e}")
            return None
        finally:
            if conn:
                conn.close()

    @staticmethod
    def generate_label_image(template_json: Dict[str, Any], data: Dict[str, Any], user_info: Dict[str, Any]):
        """
        Генерирует изображение этикетки в памяти с помощью Pillow, обходясь без ReportLab.
        """
        if Image is None or ImageDraw is None or ImageFont is None:
            raise ImportError("Библиотека Pillow не установлена. Генерация изображения невозможна.")

        # Используем стандартный DPI для конвертации мм в пиксели
        DPI = 300
        dots_per_mm = DPI / 25.4

        width_px = int(template_json.get("width_mm", 100) * dots_per_mm)
        height_px = int(template_json.get("height_mm", 50) * dots_per_mm)

        # Создаем белое изображение
        label_image = Image.new('RGB', (width_px, height_px), 'white')
        draw = ImageDraw.Draw(label_image)

        for obj in template_json.get("objects", []):
            obj_data = data.get(obj["data_source"])
            
            # НОВАЯ ЛОГИКА: Если obj_data не предоставлен, пытаемся получить его из БД
            if obj_data is None and obj["data_source"] and '.' in obj["data_source"] and not obj["data_source"].startswith("QR:"):
                obj_data = PrintingService._fetch_data_from_db(user_info, obj["data_source"])
                if obj_data is None:
                    logging.warning(f"Источник данных '{obj['data_source']}' не найден в переданных данных и не удалось получить из БД. Пропуск объекта.")
                    continue
            elif obj_data is None: # If it's not a DB source and still None
                logging.warning(f"Источник данных '{obj['data_source']}' не найден. Пропуск объекта.")
                continue

            # Конвертируем координаты и размеры из мм в пиксели
            x = int(obj["x_mm"] * dots_per_mm)
            y = int(obj["y_mm"] * dots_per_mm)
            width = int(obj["width_mm"] * dots_per_mm)
            height = int(obj["height_mm"] * dots_per_mm)

            if obj["type"] == "text":
                text = str(obj_data)
                try:
                    # Пытаемся загрузить системный шрифт Arial
                    font = ImageFont.truetype("arial.ttf", size=int(height * 0.8))
                except IOError:
                    # Если не найден, используем шрифт по умолчанию
                    font = ImageFont.load_default()
                
                # Рисуем текст. Координаты (x, y) - это верхний левый угол.
                draw.text((x, y), text, fill="black", font=font)

            elif obj["type"] == "barcode":
                barcode_type = obj.get("barcode_type", "QR").upper()
                if barcode_type == "QR":
                    if qrcode is None:
                        logging.warning("Библиотека qrcode не установлена, пропуск QR-кода.")
                        continue
                    
                    qr_gen = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=1)
                    qr_gen.add_data(str(obj_data))
                    qr_gen.make(fit=True)
                    barcode_image = qr_gen.make_image(fill_color="black", back_color="white")

                    # Масштабируем QR-код под нужный размер и вставляем на этикетку
                    barcode_image = barcode_image.resize((width, height), Image.Resampling.LANCZOS)
                    label_image.paste(barcode_image, (x, y))
                elif barcode_type == "DATAMATRIX":
                    if DataMatrixEncoder is None:
                        logging.warning("Библиотека pystrich не установлена, пропуск DataMatrix.")
                        continue
                    
                    encoder = DataMatrixEncoder(str(obj_data))
                    # Сохраняем во временный буфер в памяти, т.к. pystrich работает с файлами
                    with io.BytesIO() as buffer:
                        encoder.save(buffer, "PNG")
                        buffer.seek(0)
                        barcode_image = Image.open(buffer).convert("RGB")
                    barcode_image = barcode_image.resize((width, height), Image.Resampling.NEAREST)
                    label_image.paste(barcode_image, (x, y))
                else:
                    logging.warning(f"Генерация изображения для типа штрихкода '{barcode_type}' пока не реализована.")
                    # Рисуем заглушку
                    draw.rectangle([x, y, x + width, y + height], outline="red", fill="white")
                    draw.text((x + 5, y + 5), f"Unsupported:\n{barcode_type}", fill="red")

        return label_image

    @staticmethod
    def preview_image(image: Image.Image):
        """
        Открывает новое окно Tkinter для отображения сгенерированного изображения.
        """
        if tk is None or ImageTk is None:
            raise ImportError("Tkinter или Pillow.ImageTk не доступны. Предпросмотр невозможен.")
        
        preview_window = tk.Toplevel()
        preview_window.title("Предпросмотр этикетки")
        preview_window.grab_set()

        # Конвертируем изображение для Tkinter
        photo_image = ImageTk.PhotoImage(image)

        # Отображаем изображение в Label
        label = tk.Label(preview_window, image=photo_image)
        # ВАЖНО: сохраняем ссылку на изображение, чтобы оно не было удалено сборщиком мусора
        label.image = photo_image 
        label.pack(padx=10, pady=10)

    @staticmethod
    def print_label_direct(printer_name: str, template_json: Dict[str, Any], data: Dict[str, Any], user_info: Dict[str, Any]):
        """
        Генерирует и отправляет этикетку НАПРЯМУЮ на принтер, минуя PDF.
        Использует GDI-команды pywin32 для отрисовки.
        """
        if win32print is None or win32ui is None:
            raise ImportError("Библиотека pywin32 не установлена. Прямая печать невозможна.")

        h_printer = None
        dc = None
        try:
            # 1. Получаем хендл принтера и создаем Device Context (DC) - "холст" для рисования
            h_printer = win32print.OpenPrinter(printer_name)
            dc = win32ui.CreateDC()
            dc.CreatePrinterDC(printer_name)

            # 2. Получаем DPI принтера для перевода мм в точки (пиксели принтера)
            dpi_x = dc.GetDeviceCaps(88) # LOGPIXELSX
            dpi_y = dc.GetDeviceCaps(90) # LOGPIXELSY
            dots_per_mm_x = dpi_x / 25.4
            dots_per_mm_y = dpi_y / 25.4

            # 3. Начинаем процесс печати
            dc.StartDoc(f"Label from TildaKod: {template_json.get('name', 'N/A')}")
            dc.StartPage()

            # 4. Итерируемся по объектам в шаблоне и рисуем каждый
            for obj in template_json.get("objects", []):
                obj_data = data.get(obj["data_source"])                
                # НОВАЯ ЛОГИКА: Если obj_data не предоставлен, пытаемся получить его из БД
                if obj_data is None and obj["data_source"] and '.' in obj["data_source"] and not obj["data_source"].startswith("QR:"):
                    obj_data = PrintingService._fetch_data_from_db(user_info, obj["data_source"])
                    if obj_data is None:
                        logging.warning(f"Источник данных '{obj['data_source']}' не найден в переданных данных и не удалось получить из БД. Пропуск объекта.")
                        continue
                elif obj_data is None: # If it's not a DB source and still None
                    logging.warning(f"Источник данных '{obj['data_source']}' не найден. Пропуск объекта.")
                    continue


                # Конвертируем координаты и размеры из мм в точки
                x = int(obj["x_mm"] * dots_per_mm_x)
                y = int(obj["y_mm"] * dots_per_mm_y)
                width = int(obj["width_mm"] * dots_per_mm_x)
                height = int(obj["height_mm"] * dots_per_mm_y)

                if obj["type"] == "text":
                    text = str(obj_data)
                    # --- ИСПРАВЛЕНИЕ: Логика создания шрифта перенесена внутрь блока "text" ---
                    font_height = -int(height * 0.8)
                    font = win32ui.CreateFont({
                        'name': obj.get("font_name", "Arial"),
                        'height': font_height,
                        'weight': 400,
                        'charset': 204 # RUSSIAN_CHARSET
                    })
                    dc.SelectObject(font)
                    # Пока используем простой TextOut
                    dc.TextOut(x, y, text)
                    win32gui.DeleteObject(font.GetHandle()) # Освобождаем ресурс шрифта
                elif obj["type"] == "barcode":
                    # Для штрихкодов генерируем картинку в памяти и "впечатываем" ее на холст
                    barcode_type = obj.get("barcode_type", "QR").upper()
                    barcode_image = None

                    if qrcode is None or Image is None:
                        logging.warning("Библиотеки для генерации штрихкодов (qrcode, Pillow) не установлены.")
                        continue

                    # Генерируем PIL Image
                    if barcode_type == "QR":
                        qr_gen = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=1)
                        qr_gen.add_data(str(obj_data)) # type: ignore
                        qr_gen.make(fit=True)
                        barcode_image = qr_gen.make_image(fill_color="black", back_color="white") # type: ignore
                    # TODO: Добавить генерацию для других типов штрихкодов (SSCC, DataMatrix)
                    elif barcode_type == "DATAMATRIX":
                        if DataMatrixEncoder is None:
                            logging.warning("Библиотека pystrich не установлена, пропуск DataMatrix.")
                            continue
                        
                        encoder = DataMatrixEncoder(str(obj_data))
                        # Сохраняем во временный буфер в памяти
                        with io.BytesIO() as buffer:
                            encoder.save(buffer, "PNG")
                            buffer.seek(0)
                            barcode_image = Image.open(buffer).convert("RGB")

                    # Потребуется библиотека python-barcode или аналоги
                    else:
                        logging.warning(f"Прямая печать для типа штрихкода '{barcode_type}' пока не реализована.")
                        continue

                    if barcode_image:
                        # --- ИСПРАВЛЕНИЕ: Используем ImageWin.Dib для отрисовки PIL Image на DC ---
                        if ImageWin is None:
                            logging.error("ImageWin (часть Pillow) не доступен. Невозможно напечатать изображение.")
                            raise ImportError("ImageWin не доступен.")

                        # 1. Масштабируем изображение до нужных размеров печати.
                        scaled_image = barcode_image.resize((width, height), Image.Resampling.LANCZOS)
                        
                        # 2. Конвертируем изображение в RGB, если оно в другом формате.
                        if scaled_image.mode != 'RGB':
                            scaled_image = scaled_image.convert('RGB')
                        
                        # 3. Создаем DIB из PIL Image
                        dib = ImageWin.Dib(scaled_image)

                        # 4. Создаем битмап и выбираем его в memory DC
                        bmp = win32ui.CreateBitmap()
                        bmp.CreateCompatibleBitmap(dc, width, height)
                        mem_dc = dc.CreateCompatibleDC()
                        mem_dc.SelectObject(bmp)
                        
                        # 5. Рисуем DIB на memory DC
                        dib.draw(mem_dc.GetSafeHdc(), (0, 0, width, height))
                        
                        # 6. Копируем готовый битмап из memory DC на "холст" принтера.
                        dc.BitBlt((x, y), (width, height), mem_dc, (0, 0), win32con.SRCCOPY)
                        
                        # Очищаем ресурсы
                        mem_dc.DeleteDC()
                        win32gui.DeleteObject(bmp.GetHandle())

            # 5. Завершаем печать
            dc.EndPage()
            dc.EndDoc()
            logging.info(f"Этикетка успешно отправлена на принтер '{printer_name}' напрямую.")

        except pywin_error as e:
            logging.error(f"Ошибка Win32 API при прямой печати: {e}")
            raise RuntimeError(f"Ошибка печати (Win32): {e.strerror}") from e
        except Exception as e:
            logging.error(f"Неизвестная ошибка при прямой печати: {e}")
            raise RuntimeError(f"Неизвестная ошибка прямой печати: {e}")
        finally:
            # Гарантированно освобождаем ресурсы
            if dc:
                dc.DeleteDC()
            if h_printer:
                win32print.ClosePrinter(h_printer)

    @staticmethod
    def print_labels_for_items(printer_name: str, paper_name: str, template_json: Dict[str, Any], items_data: list, user_info: Dict[str, Any]):
        """
        Генерирует и печатает по одной этикетке для каждого элемента в списке.

        :param printer_name: Имя принтера.
        :param paper_name: Имя формата бумаги.
        :param template_json: JSON-макет этикетки.
        :param items_data: Список словарей, где каждый словарь - данные для одной этикетки.
                           Пример: [{'orders.client_name': 'A'}, {'orders.client_name': 'B'}]
        """
        if not items_data:
            logging.warning("Список элементов для печати пуст.")
            return

        logging.info(f"Начало пакетной печати {len(items_data)} этикеток на принтер '{printer_name}'...")

        # --- ИСПРАВЛЕНИЕ: Возвращаем логику прямой печати для каждого элемента ---
        # Этот метод теперь отвечает только за печать, а не за предпросмотр.
        for i, item_data in enumerate(items_data):
            try:
                logging.info(f"Отправка на печать этикетки {i+1}/{len(items_data)}...")
                # Используем метод прямой печати для каждого элемента
                PrintingService.print_label_direct(printer_name, template_json, item_data, user_info)
            except Exception as e:
                logging.error(f"Ошибка при печати элемента {i+1}: {item_data}. Ошибка: {e}")
                # В зависимости от требований, можно либо прервать, либо продолжить печать
                raise RuntimeError(f"Ошибка при печати этикетки {i+1}. Процесс прерван.") from e

# --- Класс визуального редактора макетов ---

class LabelEditorWindow(tk.Toplevel if tk else object):
    """
    Окно визуального редактора макетов этикеток.
    """
    def __init__(self, parent, user_info):
        if not tk:
            raise RuntimeError("Tkinter не доступен в текущем окружении.")
        super().__init__(parent)
        self.title("Редактор макетов")
        self.geometry("1200x800")
        self.grab_set()

        self.user_info = user_info
        self.template = None  # Здесь будет храниться JSON-представление макета
        self.canvas_scale = 5  # Масштаб для отображения мм на холсте (5 пикселей = 1 мм)
        self.selected_object_id = None # ID объекта в списке self.template['objects']
        self.canvas_objects = {} # Словарь для связи ID объекта с тегом на холсте
        self.active_view = None # Текущий отображаемый фрейм ('list' или 'editor')

        # Заглушка для списка макетов. В будущем будет грузиться из БД.
        self.layouts_list = []

        # Проверяем наличие данных для подключения к БД клиента
        if not self.user_info.get("client_db_config"):
            messagebox.showerror("Ошибка конфигурации", "Не найдены данные для подключения к базе данных клиента. Редактор макетов не может быть запущен.")
            self.destroy()
            return

        self._create_widgets()

    def _get_client_db_connection(self):
        """Создает и возвращает подключение к БД клиента."""
        db_config = self.user_info.get("client_db_config")
        if not db_config or not db_config.get('db_name'):
            raise ConnectionError("Конфигурация базы данных клиента неполная.")

        conn_params = {
            'host': db_config['db_host'],
            'port': db_config['db_port'],
            'dbname': db_config['db_name'],
            'user': db_config['db_user'],
            'password': db_config['db_password']
        }

        temp_cert_file = None
        try:
            if db_config.get('db_ssl_cert'):
                with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                    fp.write(db_config['db_ssl_cert'])
                    temp_cert_file = fp.name
                conn_params.update({'sslmode': 'verify-full', 'sslrootcert': temp_cert_file})

            conn = psycopg2.connect(**conn_params)
            return conn

        finally:
            # Удаляем временный файл сертификата после установки соединения
            if temp_cert_file and os.path.exists(temp_cert_file):
                try:
                    os.remove(temp_cert_file)
                except OSError as e:
                    logging.warning(f"Не удалось удалить временный файл сертификата {temp_cert_file}: {e}")

    def _create_widgets(self):
        # Основной разделенный фрейм
        # --- Фрейм для списка макетов (начальный экран) ---
        self.list_view_frame = ttk.Frame(self, padding="10")
        self._create_list_view_widgets()

        # --- Фрейм для редактора (холст и инструменты) ---
        self.editor_view_frame = ttk.Frame(self)
        self._create_editor_view_widgets()

        # Показываем начальный экран
        self._switch_view('list')

    def _create_list_view_widgets(self):
        """Создает виджеты для экрана со списком макетов."""
        list_controls_frame = ttk.Frame(self.list_view_frame)
        list_controls_frame.pack(side=tk.TOP, fill=tk.X, pady=5)

        ttk.Button(list_controls_frame, text="Создать новый макет", command=self._prompt_for_new_layout).pack(side=tk.LEFT, padx=5)
        ttk.Button(list_controls_frame, text="Редактировать", command=self._edit_selected_layout).pack(side=tk.LEFT, padx=5)
        ttk.Button(list_controls_frame, text="Удалить", command=self._delete_selected_layout).pack(side=tk.LEFT, padx=5)

        tree_frame = ttk.Frame(self.list_view_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.layouts_tree = ttk.Treeview(tree_frame, columns=('name', 'size'), show='headings')
        self.layouts_tree.heading('name', text='Название макета')
        self.layouts_tree.heading('size', text='Размер (мм)')
        self.layouts_tree.column('name', width=250)
        self.layouts_tree.column('size', width=100, anchor=tk.CENTER)
        self.layouts_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.layouts_tree.yview)
        self.layouts_tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._load_layouts_to_tree()

    def _create_editor_view_widgets(self):
        """Создает виджеты для экрана редактирования (холст, инструменты)."""
        # PanedWindow теперь является дочерним элементом editor_view_frame
        paned_window = ttk.PanedWindow(self.editor_view_frame, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True)

        # --- Левая панель (управление) ---
        controls_frame = ttk.Frame(paned_window, width=300, padding="10")
        paned_window.add(controls_frame, weight=1)

        # Кнопки управления
        ttk.Button(controls_frame, text="<< К списку макетов", command=lambda: self._switch_view('list')).pack(fill=tk.X, pady=5)
        ttk.Button(controls_frame, text="Сохранить макет", command=self._save_layout).pack(fill=tk.X, pady=5)
        ttk.Separator(controls_frame).pack(fill=tk.X, pady=10)

        # Фрейм для инструментов (изначально неактивен)
        self.tools_frame = ttk.LabelFrame(controls_frame, text="Инструменты")
        self.tools_frame.pack(fill=tk.X, pady=5)

        # Кнопки для добавления объектов
        ttk.Button(self.tools_frame, text="Добавить Текст", command=lambda: self._add_object_to_canvas("text")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить QR-код", command=lambda: self._add_object_to_canvas("QR")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить SSCC", command=lambda: self._add_object_to_canvas("SSCC")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить DataMatrix", command=lambda: self._add_object_to_canvas("DataMatrix")).pack(fill=tk.X, pady=2)

        # --- Новая панель свойств ---
        self.properties_frame = ttk.LabelFrame(controls_frame, text="Свойства объекта")
        self.properties_frame.pack(fill=tk.X, pady=10)

        # --- ИЗМЕНЕНИЕ: Заменяем текстовое поле для источника данных на выпадающий список ---
        self.prop_entries = {}
        prop_fields = {
            "x_mm": "X (мм):",
            "y_mm": "Y (мм):",
            "width_mm": "Ширина (мм):",
            "height_mm": "Высота (мм):",
        }

        # Определяем возможные источники данных
        self.available_text_sources = [
            "ap_workplaces.warehouse_name",
            "ap_workplaces.workplace_number",
            "orders.client_name", # Пример для других макетов
            "packages.sscc_code"  # Пример для других макетов
        ]
        self.available_qr_sources = [
            "QR: Конфигурация рабочего места",
            "QR: Конфигурация сервера"
        ]
        self.available_sscc_sources = [
            "packages.sscc_code"
        ]
        self.available_datamatrix_sources = [
            "items.datamatrix"
        ]

        # Создаем текстовые поля
        for key, text in prop_fields.items():
            frame = ttk.Frame(self.properties_frame)
            frame.pack(fill=tk.X, padx=5, pady=2)
            ttk.Label(frame, text=text, width=15).pack(side=tk.LEFT)
            entry = ttk.Entry(frame)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.prop_entries[key] = entry

        # --- ИЗМЕНЕНИЕ: Создаем фрейм-контейнер для динамического виджета data_source ---
        self.data_source_container_frame = ttk.Frame(self.properties_frame)
        self.data_source_container_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(self.data_source_container_frame, text="Источник данных:", width=15).pack(side=tk.LEFT)
        self.prop_entries["data_source"] = None # Инициализируем как None, будет заполнено динамически

        self.apply_props_button = ttk.Button(self.properties_frame, text="Применить", command=self._apply_properties)
        self.apply_props_button.pack(pady=5)

        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        # --- Правая панель редактора (холст) ---
        canvas_frame = ttk.Frame(paned_window)
        paned_window.add(canvas_frame, weight=4)

        self.canvas = tk.Canvas(canvas_frame, bg="lightgrey")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Привязываем событие клика к холсту
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        # Изначально деактивируем все панели
        self._toggle_properties_panel(False)
        self._toggle_tools_panel(False)

    def _switch_view(self, view_name: str):
        """Переключает между видом списка и видом редактора."""
        if self.active_view == view_name:
            return

        # Скрываем все
        self.list_view_frame.pack_forget()
        self.editor_view_frame.pack_forget()

        if view_name == 'list':
            self.title("Редактор макетов - Список")
            self.list_view_frame.pack(fill=tk.BOTH, expand=True)
            self._load_layouts_to_tree() # Обновляем список
        elif view_name == 'editor':
            layout_name = self.template.get('name', 'Новый макет') if self.template else 'Редактор'
            self.title(f"Редактор макетов - {layout_name}")
            self.editor_view_frame.pack(fill=tk.BOTH, expand=True)
            self._draw_canvas_background()
        
        self.active_view = view_name

    def _prompt_for_new_layout(self):
        """Запрашивает у пользователя имя и размеры нового макета."""
        name = simpledialog.askstring("Новый макет", "Введите название макета:", parent=self)
        if not name:
            return
        
        # Проверка на уникальность имени
        # Теперь проверяем в self.layouts_list, который загружен из БД
        if any(layout['name'] == name for layout in self.layouts_list):
            messagebox.showerror("Ошибка", "Макет с таким названием уже существует.", parent=self)
            return

        size_str = simpledialog.askstring("Размеры макета", "Введите размеры этикетки (Ширина x Высота) в мм:", parent=self)
        if not size_str: # Пользователь нажал "Отмена" на втором диалоге
            return

        try:
            width_str, height_str = size_str.lower().split('x')
            width_mm = int(width_str.strip())
            height_mm = int(height_str.strip())
        except (ValueError, IndexError):
            messagebox.showerror("Ошибка", "Неверный формат. Введите размеры в формате '100 x 50'.", parent=self)
            return

        # Создаем базовую структуру шаблона
        new_template = {
            "name": name,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "objects": []
        }
        # Не добавляем в layouts_list здесь, это произойдет при сохранении
        self.template = new_template # Устанавливаем как текущий редактируемый

        self.selected_object_id = None
        self.canvas_objects.clear()

        self._switch_view('editor')
        self._toggle_tools_panel(True)
        self._toggle_properties_panel(False)

    def _edit_selected_layout(self):
        """Открывает выбранный макет для редактирования."""
        selected_item = self.layouts_tree.focus()
        if not selected_item:
            messagebox.showwarning("Внимание", "Выберите макет из списка для редактирования.", parent=self)
            return
        
        layout_name = self.layouts_tree.item(selected_item)['values'][0]
        
        # Находим макет в нашем списке-заглушке
        layout_to_edit = next((l for l in self.layouts_list if l['name'] == layout_name), None)

        if layout_to_edit:
            self.template = layout_to_edit
            self.selected_object_id = None
            self.canvas_objects.clear()
            self._switch_view('editor')
            self._toggle_tools_panel(True)
            self._toggle_properties_panel(False)

    def _delete_selected_layout(self):
        selected_item = self.layouts_tree.focus()
        if not selected_item:
            messagebox.showwarning("Внимание", "Выберите макет для удаления.", parent=self)
            return

        layout_name = self.layouts_tree.item(selected_item)['values'][0]
        if not messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите удалить макет '{layout_name}'?\nЭто действие необратимо.", parent=self):
            return

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM label_templates WHERE name = %s", (layout_name,))
                conn.commit()
            
            messagebox.showinfo("Успех", f"Макет '{layout_name}' успешно удален.", parent=self)
            self._load_layouts_to_tree() # Обновляем список

        except Exception as e:
            logging.error(f"Ошибка удаления макета из БД: {e}")
            messagebox.showerror("Ошибка", f"Не удалось удалить макет из базы данных: {e}", parent=self)

    def _save_layout(self):
        """Сохраняет текущий макет."""
        if not self.template:
            return

        layout_name = self.template.get('name')
        if not layout_name:
            messagebox.showerror("Ошибка", "У макета отсутствует имя.", parent=self)
            return

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    # Используем INSERT ... ON CONFLICT (UPSERT)
                    cur.execute("""
                        INSERT INTO label_templates (name, template_json, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (name) DO UPDATE SET
                            template_json = EXCLUDED.template_json,
                            updated_at = NOW();
                    """, (layout_name, json.dumps(self.template)))
                conn.commit()
            
            logging.info(f"Макет '{layout_name}' успешно сохранен в БД.")
            messagebox.showinfo("Сохранено", f"Макет '{layout_name}' успешно сохранен.", parent=self)
            
            # Обновляем заголовок окна, если это был новый макет
            self.title(f"Редактор макетов - {layout_name}")

        except Exception as e:
            logging.error(f"Ошибка сохранения макета в БД: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить макет в базу данных: {e}", parent=self)

    def _draw_canvas_background(self):
        """Отрисовывает фон (этикетку) на холсте."""
        self.canvas.delete("all")  # Очищаем холст

        if not self.template:
            return

        width_px = self.template['width_mm'] * self.canvas_scale
        height_px = self.template['height_mm'] * self.canvas_scale

        # Рисуем белый прямоугольник, представляющий этикетку
        self.canvas.create_rectangle(10, 10, 10 + width_px, 10 + height_px, fill="white", outline="black", tags="label_bg")

        # Перерисовываем все существующие объекты
        for i, obj in enumerate(self.template['objects']):
            self._draw_object(obj, i)

    def _load_layouts_to_tree(self):
        """Загружает список макетов в Treeview."""
        self.layouts_list.clear()
        # Очищаем дерево
        for i in self.layouts_tree.get_children():
            self.layouts_tree.delete(i)
        
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT name, template_json FROM label_templates ORDER BY name")
                    for row in cur.fetchall():
                        name, template_data = row
                        self.layouts_list.append(template_data) # Сохраняем полный JSON
                        size_str = f"{template_data.get('width_mm', '?')} x {template_data.get('height_mm', '?')}"
                        self.layouts_tree.insert('', 'end', values=(name, size_str))
            logging.info(f"Загружено {len(self.layouts_list)} макетов из БД.")
        except Exception as e:
            logging.error(f"Ошибка загрузки макетов из БД: {e}")
            messagebox.showerror("Ошибка", f"Не удалось загрузить макеты из базы данных: {e}", parent=self)
            
    def _add_object_to_canvas(self, obj_type: str):
        """Добавляет новый объект на холст и в шаблон."""
        if not self.template:
            messagebox.showwarning("Внимание", "Сначала создайте новый макет.", parent=self)
            return

        if obj_type == "text":
            new_object = {
                "type": "text",
                "data_source": "ap_workplaces.warehouse_name", # ИСПРАВЛЕНИЕ: Используем значение из списка available_text_sources
                "x_mm": 10,
                "y_mm": 10,
                "width_mm": 40,
                "height_mm": 15,
                "font_name": "Helvetica" # Можно будет добавить в свойства
            }
        else: # Это штрихкод
            new_object = {
                "type": "barcode",
                "barcode_type": obj_type, # obj_type здесь это 'QR', 'SSCC' и т.д.
                "data_source": "QR: Конфигурация рабочего места", # Используем значение из списка available_qr_sources
                "x_mm": 10,
                "y_mm": 10,
                "width_mm": 30,
                "height_mm": 30
            }
        
        # Добавляем объект в список и получаем его индекс (ID)
        object_id = len(self.template["objects"])
        self.template["objects"].append(new_object)

        # Отрисовываем объект на холсте
        self._draw_object(new_object, object_id)
        logging.info(f"Добавлен новый объект: {obj_type}. Текущий шаблон: {json.dumps(self.template, indent=2)}")

    def _draw_object(self, obj_data: dict, object_id: int):
        """Отрисовывает один объект на холсте."""
        canvas_tag = f"obj_{object_id}"
        self.canvas_objects[object_id] = canvas_tag

        x_px = 10 + obj_data['x_mm'] * self.canvas_scale
        y_px = 10 + obj_data['y_mm'] * self.canvas_scale
        width_px = obj_data['width_mm'] * self.canvas_scale
        height_px = obj_data['height_mm'] * self.canvas_scale

        # Рисуем прямоугольник-заглушку
        outline_color = "blue" if object_id == self.selected_object_id else "grey"
        
        if obj_data['type'] == 'text':
            fill_color = "lightyellow"
            display_text = "Текст"
        else: # barcode
            fill_color = "lightblue"
            display_text = obj_data['barcode_type']

        self.canvas.create_rectangle(x_px, y_px, x_px + width_px, y_px + height_px, fill=fill_color, outline=outline_color, width=2, tags=(canvas_tag, "object"))
        self.canvas.create_text(x_px + width_px / 2, y_px + height_px / 2, text=display_text, tags=(canvas_tag, "object_text"))

    def _on_canvas_click(self, event):
        """Обрабатывает клики по холсту для выделения объектов."""
        clicked_items = self.canvas.find_withtag(tk.CURRENT)
        if not clicked_items:
            self._select_object(None) # Клик по пустому месту
            return

        # Ищем тег объекта, например "obj_0"
        for tag in self.canvas.gettags(clicked_items[0]):
            if tag.startswith("obj_"):
                try:
                    object_id = int(tag.split("_")[1])
                    self._select_object(object_id)
                    return
                except (ValueError, IndexError):
                    continue
        
        # Если кликнули, но не по объекту (например, по фону)
        self._select_object(None)

    def _select_object(self, object_id: int or None):
        """Выделяет объект и обновляет UI."""
        if self.selected_object_id == object_id:
            return # Объект уже выделен

        self.selected_object_id = object_id
        self._draw_canvas_background() # Перерисовываем все для обновления рамок

        if object_id is not None:
            self._toggle_properties_panel(True)
            self._update_properties_panel()
            
            # --- НОВАЯ ЛОГИКА: Обновляем список источников данных в зависимости от типа объекта ---
            obj_data = self.template['objects'][object_id]
            if obj_data['type'] == 'text':
                self.data_source_combo['values'] = self.available_text_sources
            elif obj_data['type'] == 'barcode':
                self.data_source_combo['values'] = self.available_qr_sources

        else:
            self._toggle_properties_panel(False)

    def _toggle_properties_panel(self, active: bool):
        """Включает или выключает панель свойств."""
        state = "normal" if active else "disabled"
        for widget in self.properties_frame.winfo_children():
            # ttk.Entry и ttk.Button не имеют метода configure для state в некоторых случаях
            try:
                widget.config(state=state)
            except tk.TclError:
                if isinstance(widget, (ttk.Entry, ttk.Button, ttk.Combobox)): # Добавляем Combobox
                    widget.state([state] if state == "normal" else [state])
                elif isinstance(widget, ttk.Frame): # Рекурсивно для вложенных фреймов
                    for sub_widget in widget.winfo_children():
                         try: sub_widget.config(state=state)
                         except tk.TclError: pass

    def _toggle_tools_panel(self, active: bool):
        """Включает или выключает панель инструментов."""
        state = "normal" if active else "disabled"
        for widget in self.tools_frame.winfo_children():
            try:
                widget.config(state=state)
            except tk.TclError:
                 if isinstance(widget, ttk.Button):
                    widget.state([state] if state == "normal" else [state])

    def _update_properties_panel(self):
        """Заполняет панель свойств данными выделенного объекта."""
        if self.selected_object_id is None:
            return

        obj_data = self.template['objects'][self.selected_object_id]
        for key, widget in self.prop_entries.items():
            # --- ИЗМЕНЕНИЕ: Раздельная логика для Entry и Combobox ---
            if isinstance(widget, ttk.Combobox):
                widget.set(obj_data.get(key, ''))
            else: # Это ttk.Entry
                widget.delete(0, tk.END)
                widget.insert(0, str(obj_data.get(key, '')))

    def _apply_properties(self):
        """Применяет изменения из панели свойств к объекту."""
        if self.selected_object_id is None:
            return

        try:
            for key, widget in self.prop_entries.items():
                # --- ИЗМЕНЕНИЕ: Получаем значение из Combobox или Entry ---
                if key == 'data_source':
                    # Для data_source получаем значение из динамически созданного виджета
                    self.template['objects'][self.selected_object_id][key] = widget.get()
                else:
                    value_str = widget.get()
                    try:
                        self.template['objects'][self.selected_object_id][key] = float(value_str)
                    except ValueError:
                        messagebox.showerror("Ошибка", f"Значение для поля '{key}' должно быть числом.", parent=self)
                        return # Прерываем применение свойств
            
            self._draw_canvas_background() # Перерисовываем холст с новыми данными
            logging.info(f"Свойства объекта {self.selected_object_id} обновлены.")
        except ValueError:
            messagebox.showerror("Ошибка", "Значения геометрических свойств должны быть числами.", parent=self)

    def _switch_view(self, view_name: str):
        """Переключает между видом списка и видом редактора."""
        if self.active_view == view_name:
            return

        # Скрываем все
        self.list_view_frame.pack_forget()
        self.editor_view_frame.pack_forget()

        if view_name == 'list':
            self.title("Редактор макетов - Список")
            self.list_view_frame.pack(fill=tk.BOTH, expand=True)
            self._load_layouts_to_tree() # Обновляем список
        elif view_name == 'editor':
            layout_name = self.template.get('template_name', 'Новый макет') if self.template else 'Редактор'
            self.title(f"Редактор макетов - {layout_name}")
            self.editor_view_frame.pack(fill=tk.BOTH, expand=True)
            self._draw_canvas_background()
        
        self.active_view = view_name

    def _prompt_for_new_layout(self):
        """Запрашивает у пользователя имя и размеры нового макета."""
        name = simpledialog.askstring("Новый макет", "Введите название макета:", parent=self)
        if not name:
            return
        
        # Проверка на уникальность имени
        # Теперь проверяем в self.layouts_list, который загружен из БД
        if any(layout['name'] == name for layout in self.layouts_list):
            messagebox.showerror("Ошибка", "Макет с таким названием уже существует.", parent=self)
            return

        size_str = simpledialog.askstring("Размеры макета", "Введите размеры этикетки (Ширина x Высота) в мм:", parent=self)
        if not size_str: # Пользователь нажал "Отмена" на втором диалоге
            return

        try:
            width_str, height_str = size_str.lower().split('x')
            width_mm = int(width_str.strip())
            height_mm = int(height_str.strip())
        except (ValueError, IndexError):
            messagebox.showerror("Ошибка", "Неверный формат. Введите размеры в формате '100 x 50'.", parent=self)
            return

        # Создаем базовую структуру шаблона
        new_template = {
            "name": name,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "objects": []
        }
        # Не добавляем в layouts_list здесь, это произойдет при сохранении
        self.template = new_template # Устанавливаем как текущий редактируемый

        self.selected_object_id = None
        self.canvas_objects.clear()

        self._switch_view('editor')
        self._toggle_tools_panel(True)
        self._toggle_properties_panel(False)

    def _edit_selected_layout(self):
        """Открывает выбранный макет для редактирования."""
        selected_item = self.layouts_tree.focus()
        if not selected_item:
            messagebox.showwarning("Внимание", "Выберите макет из списка для редактирования.", parent=self)
            return
        
        layout_name = self.layouts_tree.item(selected_item)['values'][0]
        
        # Находим макет в нашем списке-заглушке
        layout_to_edit = next((l for l in self.layouts_list if l['name'] == layout_name), None)

        if layout_to_edit:
            self.template = layout_to_edit
            self.selected_object_id = None
            self.canvas_objects.clear()
            self._switch_view('editor')
            self._toggle_tools_panel(True)
            self._toggle_properties_panel(False)

    def _delete_selected_layout(self):
        selected_item = self.layouts_tree.focus()
        if not selected_item:
            messagebox.showwarning("Внимание", "Выберите макет для удаления.", parent=self)
            return

        layout_name = self.layouts_tree.item(selected_item)['values'][0]
        if not messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите удалить макет '{layout_name}'?\nЭто действие необратимо.", parent=self):
            return

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM label_templates WHERE name = %s", (layout_name,))
                conn.commit()
            
            messagebox.showinfo("Успех", f"Макет '{layout_name}' успешно удален.", parent=self)
            self._load_layouts_to_tree() # Обновляем список

        except Exception as e:
            logging.error(f"Ошибка удаления макета из БД: {e}")
            messagebox.showerror("Ошибка", f"Не удалось удалить макет из базы данных: {e}", parent=self)

    def _save_layout(self):
        """Сохраняет текущий макет."""
        if not self.template:
            return

        layout_name = self.template.get('name')
        if not layout_name:
            messagebox.showerror("Ошибка", "У макета отсутствует имя.", parent=self)
            return

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    # Используем INSERT ... ON CONFLICT (UPSERT)
                    cur.execute("""
                        INSERT INTO label_templates (name, template_json, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (name) DO UPDATE SET
                            template_json = EXCLUDED.template_json,
                            updated_at = NOW();
                    """, (layout_name, json.dumps(self.template)))
                conn.commit()
            
            logging.info(f"Макет '{layout_name}' успешно сохранен в БД.")
            messagebox.showinfo("Сохранено", f"Макет '{layout_name}' успешно сохранен.", parent=self)
            
            # Обновляем заголовок окна, если это был новый макет
            self.title(f"Редактор макетов - {layout_name}")

        except Exception as e:
            logging.error(f"Ошибка сохранения макета в БД: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить макет в базу данных: {e}", parent=self)

    def _draw_canvas_background(self):
        """Отрисовывает фон (этикетку) на холсте."""
        self.canvas.delete("all")  # Очищаем холст

        if not self.template:
            return

        width_px = self.template['width_mm'] * self.canvas_scale
        height_px = self.template['height_mm'] * self.canvas_scale

        # Рисуем белый прямоугольник, представляющий этикетку
        self.canvas.create_rectangle(10, 10, 10 + width_px, 10 + height_px, fill="white", outline="black", tags="label_bg")

        # Перерисовываем все существующие объекты
        for i, obj in enumerate(self.template['objects']):
            self._draw_object(obj, i)

    def _load_layouts_to_tree(self):
        """Загружает список макетов в Treeview."""
        self.layouts_list.clear()
        # Очищаем дерево
        for i in self.layouts_tree.get_children():
            self.layouts_tree.delete(i)
        
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT name, template_json FROM label_templates ORDER BY name")
                    for row in cur.fetchall():
                        name, template_data = row
                        self.layouts_list.append(template_data) # Сохраняем полный JSON
                        size_str = f"{template_data.get('width_mm', '?')} x {template_data.get('height_mm', '?')}"
                        self.layouts_tree.insert('', 'end', values=(name, size_str))
            logging.info(f"Загружено {len(self.layouts_list)} макетов из БД.")
        except Exception as e:
            logging.error(f"Ошибка загрузки макетов из БД: {e}")
            messagebox.showerror("Ошибка", f"Не удалось загрузить макеты из базы данных: {e}", parent=self)
            
    def _add_object_to_canvas(self, obj_type: str):
        """Добавляет новый объект на холст и в шаблон."""
        if not self.template:
            messagebox.showwarning("Внимание", "Сначала создайте новый макет.", parent=self)
            return

        if obj_type == "text":
            new_object = {
                "type": "text",
                "data_source": "orders.client_name", # Значение по умолчанию для текста
                "x_mm": 10,
                "y_mm": 10,
                "width_mm": 40,
                "height_mm": 15,
                "font_name": "Helvetica" # Можно будет добавить в свойства
            }
        else: # Это штрихкод
            new_object = {
                "type": "barcode",
                "barcode_type": obj_type, # obj_type здесь это 'QR', 'SSCC' и т.д.
                "data_source": "", # Будет установлено ниже в зависимости от barcode_type
                "x_mm": 10,
                "y_mm": 10,
                "width_mm": 30,
                "height_mm": 30
            }
            if obj_type == "QR":
                new_object["data_source"] = "QR: Конфигурация рабочего места"
            elif obj_type == "SSCC":
                new_object["data_source"] = "packages.sscc_code"
            elif obj_type == "DataMatrix":
                new_object["data_source"] = "items.datamatrix"
        
        # Добавляем объект в список и получаем его индекс (ID)
        object_id = len(self.template["objects"])
        self.template["objects"].append(new_object)

        # Отрисовываем объект на холсте
        self._draw_object(new_object, object_id)
        logging.info(f"Добавлен новый объект: {obj_type}. Текущий шаблон: {json.dumps(self.template, indent=2)}")

    def _draw_object(self, obj_data: dict, object_id: int):
        """Отрисовывает один объект на холсте."""
        canvas_tag = f"obj_{object_id}"
        self.canvas_objects[object_id] = canvas_tag

        x_px = 10 + obj_data['x_mm'] * self.canvas_scale
        y_px = 10 + obj_data['y_mm'] * self.canvas_scale
        width_px = obj_data['width_mm'] * self.canvas_scale
        height_px = obj_data['height_mm'] * self.canvas_scale

        # Рисуем прямоугольник-заглушку
        outline_color = "blue" if object_id == self.selected_object_id else "grey"
        
        if obj_data['type'] == 'text':
            fill_color = "lightyellow"
            display_text = "Текст"
        else: # barcode
            fill_color = "lightblue"
            display_text = obj_data['barcode_type']

        self.canvas.create_rectangle(x_px, y_px, x_px + width_px, y_px + height_px, fill=fill_color, outline=outline_color, width=2, tags=(canvas_tag, "object"))
        self.canvas.create_text(x_px + width_px / 2, y_px + height_px / 2, text=display_text, tags=(canvas_tag, "object_text"))

    def _on_canvas_click(self, event):
        """Обрабатывает клики по холсту для выделения объектов."""
        clicked_items = self.canvas.find_withtag(tk.CURRENT)
        if not clicked_items:
            self._select_object(None) # Клик по пустому месту
            return

        # Ищем тег объекта, например "obj_0"
        for tag in self.canvas.gettags(clicked_items[0]):
            if tag.startswith("obj_"):
                try:
                    object_id = int(tag.split("_")[1])
                    self._select_object(object_id)
                    return
                except (ValueError, IndexError):
                    continue
        
        # Если кликнули, но не по объекту (например, по фону)
        self._select_object(None)

    def _select_object(self, object_id: int or None):
        """Выделяет объект и обновляет UI."""
        if self.selected_object_id == object_id:
            return # Объект уже выделен

        self.selected_object_id = object_id
        self._draw_canvas_background() # Перерисовываем все для обновления рамок

        if object_id is not None:
            self._toggle_properties_panel(True)
            self._update_properties_panel()
        else:
            self._toggle_properties_panel(False)

    def _toggle_tools_panel(self, active: bool):
        """Включает или выключает панель инструментов."""
        state = "normal" if active else "disabled"
        for widget in self.tools_frame.winfo_children():
            try:
                widget.config(state=state)
            except tk.TclError:
                 if isinstance(widget, ttk.Button):
                    widget.state([state] if state == "normal" else [state])

    def _update_properties_panel(self):
        """Заполняет панель свойств данными выделенного объекта."""
        if self.selected_object_id is None:
            return

        obj_data = self.template['objects'][self.selected_object_id]
        
        # Обновляем статические свойства (x, y, width, height)
        for key in ["x_mm", "y_mm", "width_mm", "height_mm"]:
            entry_widget = self.prop_entries[key]
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, str(obj_data.get(key, '')))

        # --- ИЗМЕНЕНИЕ: Динамическое создание/обновление виджета data_source ---
        # Сначала удаляем предыдущий виджет data_source из контейнера
        for widget in self.data_source_container_frame.winfo_children():
            if widget != self.data_source_container_frame.winfo_children()[0]: # Сохраняем Label
                widget.destroy()

        obj_type = obj_data['type']
        current_data_source = obj_data.get('data_source', '')
        data_source_widget = None

        if obj_type == 'text':
            # Для текста используем обычное поле ввода для ручного ввода
            data_source_widget = ttk.Entry(self.data_source_container_frame)
            data_source_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
            data_source_widget.insert(0, current_data_source)
        elif obj_type == 'barcode':
            barcode_type = obj_data.get('barcode_type', '').upper()
            values = []
            if barcode_type == 'QR':
                values = self.available_qr_sources
            elif barcode_type == 'SSCC':
                values = self.available_sscc_sources
            elif barcode_type == 'DATAMATRIX':
                values = self.available_datamatrix_sources
            
            data_source_widget = ttk.Combobox(self.data_source_container_frame, values=values, state="readonly")
            data_source_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
            data_source_widget.set(current_data_source)
        else:
            # Заглушка для неизвестных типов объектов
            data_source_widget = ttk.Entry(self.data_source_container_frame, state="disabled")
            data_source_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
            data_source_widget.insert(0, "Неизвестный тип объекта")

        self.prop_entries["data_source"] = data_source_widget # Обновляем ссылку на виджет

    def _toggle_properties_panel(self, active: bool):
        """Включает или выключает панель свойств."""
        state = "normal" if active else "disabled"
        # Проходим по всем дочерним элементам properties_frame
        for child_widget in self.properties_frame.winfo_children():
            # Если это фрейм (например, data_source_container_frame или фреймы для x,y,w,h)
            if isinstance(child_widget, ttk.Frame):
                for grand_child_widget in child_widget.winfo_children():
                    try: grand_child_widget.config(state=state)
                    except tk.TclError: # Combobox/Entry могут требовать .state()
                        if isinstance(grand_child_widget, (ttk.Entry, ttk.Combobox)):
                            grand_child_widget.state([state] if state == "normal" else [state])
            else: # Для виджетов напрямую в properties_frame (например, apply_props_button)
                try: child_widget.config(state=state)
                except tk.TclError:
                    if isinstance(child_widget, ttk.Button):
                        child_widget.state([state] if state == "normal" else [state])

    def _apply_properties(self):
        """Применяет изменения из панели свойств к объекту."""
        if self.selected_object_id is None:
            return

        try:
            for key, entry in self.prop_entries.items():
                if key == 'data_source':
                    self.template['objects'][self.selected_object_id][key] = entry.get()
                else:
                    value = float(entry.get())
                    self.template['objects'][self.selected_object_id][key] = value
            
            self._draw_canvas_background() # Перерисовываем холст с новыми данными
            logging.info(f"Свойства объекта {self.selected_object_id} обновлены.")
        except ValueError:
            messagebox.showerror("Ошибка", "Значения геометрических свойств должны быть числами.", parent=self)