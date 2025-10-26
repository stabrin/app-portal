import io
import logging
import json
import os
import tempfile
from typing import Dict, Any, Optional
from psycopg2 import sql
import psycopg2

# Библиотеки для генерации штрихкодов и работы с Windows API
try:
    import qrcode
    from PIL import Image, ImageDraw, ImageFont, ImageTk, ImageWin
except ImportError:
    logging.warning("QR code generation libraries (qrcode, Pillow) not installed. Install with: pip install qrcode Pillow")
    qrcode = None
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageTk = None
    ImageWin = None

try:
    from pystrich.datamatrix import DataMatrixEncoder
except ImportError:
    logging.warning("Библиотека pystrich не установлена. Установите: pip install pystrich")
    DataMatrixEncoder = None

try:
    import win32print
    import win32ui
    import win32con
    import win32gui
    from pywintypes import error as pywin_error
except ImportError:
    logging.warning("pywin32 not installed. Install with: pip install pywin32")
    win32print = None
    win32ui = None
    win32con = None
    win32gui = None
    pywin_error = None

try:
    import tkinter as tk
    from tkinter import ttk, simpledialog, messagebox
except ImportError:
    logging.warning("tkinter not installed. GUI features will be limited.")
    tk = None

# Конфигурация логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [printing_service] - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('printing_service.log', encoding='utf-8')
    ]
)

class PrintingService:
    """Сервис для генерации и печати документов."""

    @staticmethod
    def _get_client_db_connection(user_info: Dict[str, Any]) -> Optional[psycopg2.extensions.connection]:
        """Создает подключение к базе данных клиента."""
        logging.debug("Попытка установить соединение с БД клиента.")
        db_config = user_info.get("client_db_config")
        if not db_config:
            logging.error("Отсутствует конфигурация БД в user_info.")
            raise ValueError("Конфигурация базы данных клиента не предоставлена.")

        conn_params = {
            'host': db_config.get('db_host'),
            'port': db_config.get('db_port'),
            'dbname': db_config.get('db_name'),
            'user': db_config.get('db_user'),
            'password': db_config.get('db_password')
        }

        if not all(conn_params.values()):
            logging.error(f"Неполные параметры подключения: {conn_params}")
            raise ValueError("Неполные параметры подключения к базе данных.")

        temp_cert_file = None
        try:
            if db_config.get('db_ssl_cert'):
                logging.debug("Создание временного файла сертификата SSL.")
                with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                    fp.write(db_config['db_ssl_cert'])
                    temp_cert_file = fp.name
                conn_params.update({'sslmode': 'verify-full', 'sslrootcert': temp_cert_file})

            conn = psycopg2.connect(**conn_params)
            logging.info(f"Успешное подключение к БД: {conn_params['dbname']}")
            return conn
        except Exception as e:
            logging.error(f"Ошибка подключения к БД: {e}")
            raise
        finally:
            if temp_cert_file and os.path.exists(temp_cert_file):
                try:
                    os.remove(temp_cert_file)
                    logging.debug(f"Временный файл сертификата {temp_cert_file} удален.")
                except OSError as e:
                    logging.warning(f"Не удалось удалить временный файл сертификата {temp_cert_file}: {e}")

    @staticmethod
    def _fetch_data_from_db(user_info: Dict[str, Any], data_source: str) -> Optional[str]:
        """Получает данные из БД клиента по указанному источнику (table.field)."""
        logging.debug(f"Получение данных из БД для источника: {data_source}")
        parts = data_source.split('.')
        if len(parts) != 2:
            logging.warning(f"Некорректный формат data_source: '{data_source}'. Ожидается 'table.field'.")
            return None

        table_name, field_name = parts
        conn = None
        try:
            conn = PrintingService._get_client_db_connection(user_info)
            if not conn:
                logging.error("Не удалось установить соединение с БД.")
                return None

            with conn.cursor() as cur:
                query = sql.SQL("SELECT {field} FROM {table} LIMIT 1").format(
                    field=sql.Identifier(field_name),
                    table=sql.Identifier(table_name)
                )
                logging.debug(f"Выполнение запроса: {query.as_string(conn)}")
                cur.execute(query)
                result = cur.fetchone()
                if result:
                    logging.debug(f"Данные получены: {result[0]}")
                    return str(result[0])
                logging.warning(f"Данные для '{data_source}' не найдены в БД.")
                return None
        except Exception as e:
            logging.error(f"Ошибка получения данных из БД для '{data_source}': {e}")
            return None
        finally:
            if conn:
                conn.close()
                logging.debug("Соединение с БД закрыто.")

    @staticmethod
    def generate_label_image(template_json: Dict[str, Any], data: Dict[str, Any], user_info: Dict[str, Any]) -> Optional[Image.Image]:
        """Генерирует изображение этикетки с помощью Pillow."""
        logging.info("Начало генерации изображения этикетки.")
        if not all([Image, ImageDraw, ImageFont]):
            logging.error("Pillow не установлен. Генерация изображения невозможна.")
            raise ImportError("Библиотека Pillow не установлена.")

        try:
            # Проверяем обязательные параметры шаблона
            if not template_json.get("width_mm") or not template_json.get("height_mm"):
                logging.error("Отсутствуют размеры этикетки (width_mm или height_mm) в template_json.")
                raise ValueError("Некорректный шаблон: отсутствуют размеры этикетки.")

            DPI = 300
            dots_per_mm = DPI / 25.4
            width_px = int(template_json["width_mm"] * dots_per_mm)
            height_px = int(template_json["height_mm"] * dots_per_mm)
            logging.debug(f"Размеры этикетки: {width_px}x{height_px} пикселей (DPI={DPI})")

            label_image = Image.new('RGB', (width_px, height_px), 'white')
            draw = ImageDraw.Draw(label_image)

            for obj in template_json.get("objects", []):
                logging.info(f"Обработка объекта: тип='{obj.get('type')}', источник='{obj.get('data_source')}'")
                
                # Проверяем обязательные поля объекта
                required_fields = ["type", "x_mm", "y_mm", "width_mm", "height_mm", "data_source"]
                missing_fields = [f for f in required_fields if f not in obj]
                if missing_fields:
                    logging.warning(f"Пропуск объекта: отсутствуют поля {missing_fields}.")
                    continue

                obj_data = data.get(obj["data_source"])
                if obj_data is None and obj["data_source"] and '.' in obj["data_source"] and not obj["data_source"].startswith("QR:"):
                    logging.debug(f"Данные для '{obj['data_source']}' не найдены в data, попытка получения из БД.")
                    obj_data = PrintingService._fetch_data_from_db(user_info, obj["data_source"])
                
                if obj_data is None:
                    logging.warning(f"Данные для '{obj['data_source']}' не найдены. Пропуск объекта.")
                    continue

                logging.debug(f"Данные для объекта: '{str(obj_data)[:50]}...'")

                # Конвертируем координаты и размеры
                try:
                    x = int(float(obj["x_mm"]) * dots_per_mm)
                    y = int(float(obj["y_mm"]) * dots_per_mm)
                    width = int(float(obj["width_mm"]) * dots_per_mm)
                    height = int(float(obj["height_mm"]) * dots_per_mm)
                    logging.debug(f"Рассчитанные размеры (px): x={x}, y={y}, width={width}, height={height}")
                except (ValueError, TypeError) as e:
                    logging.error(f"Ошибка преобразования координат для объекта: {e}")
                    continue

                if obj["type"] == "text":
                    logging.debug("Обработка как 'text'")
                    try:
                        font = ImageFont.truetype("arial.ttf", size=int(height * 0.8))
                    except IOError:
                        logging.warning("Шрифт Arial не найден, используется шрифт по умолчанию.")
                        font = ImageFont.load_default()
                    draw.text((x, y), str(obj_data), fill="black", font=font)
                
                elif obj["type"] == "barcode":
                    barcode_type = obj.get("barcode_type", "QR").upper()
                    logging.debug(f"Обработка как 'barcode', подтип: '{barcode_type}'")
                    
                    if barcode_type == "QR":
                        if not qrcode:
                            logging.warning("Библиотека qrcode не установлена. Пропуск QR-кода.")
                            continue
                        try:
                            qr_gen = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=1)
                            qr_gen.add_data(str(obj_data))
                            qr_gen.make(fit=True)
                            barcode_image = qr_gen.make_image(fill_color="black", back_color="white")
                            barcode_image = barcode_image.resize((width, height), Image.Resampling.LANCZOS)
                            label_image.paste(barcode_image, (x, y))
                        except Exception as e:
                            logging.error(f"Ошибка генерации QR-кода: {e}")
                            continue
                    
                    elif barcode_type == "DATAMATRIX":
                        if not DataMatrixEncoder:
                            logging.warning("Библиотека pystrich не установлена. Пропуск DataMatrix.")
                            continue
                        try:
                            data_str = str(obj_data).strip()
                            if not data_str:
                                logging.warning("Данные для DataMatrix пусты. Пропуск.")
                                continue
                            if not data_str.isascii():
                                logging.warning(f"Данные содержат не-ASCII символы: '{data_str}'. Перекодировка в ASCII.")
                                data_str = data_str.encode('ascii', errors='ignore').decode('ascii')
                                if not data_str:
                                    logging.warning("После перекодировки данные пусты. Пропуск.")
                                    continue
                            encoder = DataMatrixEncoder(data_str)
                            with io.BytesIO() as buffer:
                                encoder.save(buffer, "PNG")
                                buffer.seek(0)
                                barcode_image = Image.open(buffer).convert("RGB")
                            barcode_image = barcode_image.resize((width, height), Image.Resampling.NEAREST)
                            label_image.paste(barcode_image, (x, y))
                        except Exception as e:
                            logging.error(f"Ошибка генерации DataMatrix для данных '{data_str}': {e}")
                            continue
                    
                    else:
                        logging.warning(f"Тип штрихкода '{barcode_type}' не поддерживается.")
                        draw.rectangle([x, y, x + width, y + height], outline="red", fill="white")
                        draw.text((x + 5, y + 5), f"Unsupported:\n{barcode_type}", fill="red")
            
            logging.info("Изображение этикетки успешно сгенерировано.")
            return label_image
        
        except Exception as e:
            logging.error(f"Ошибка генерации изображения этикетки: {e}")
            raise

    @staticmethod
    def preview_image(image: Image.Image) -> None:
        """Открывает окно предпросмотра изображения."""
        logging.info("Открытие окна предпросмотра этикетки.")
        if not all([tk, ImageTk]):
            logging.error("Tkinter или Pillow.ImageTk не доступны.")
            raise ImportError("Tkinter или Pillow.ImageTk не установлены.")

        try:
            preview_window = tk.Toplevel()
            preview_window.title("Предпросмотр этикетки")
            preview_window.grab_set()
            photo_image = ImageTk.PhotoImage(image)
            label = tk.Label(preview_window, image=photo_image)
            label.image = photo_image  # Сохраняем ссылку
            label.pack(padx=10, pady=10)
            logging.debug("Окно предпросмотра успешно создано.")
        except Exception as e:
            logging.error(f"Ошибка при открытии предпросмотра: {e}")
            raise

    @staticmethod
    def print_label_direct(printer_name: str, template_json: Dict[str, Any], data: Dict[str, Any], user_info: Dict[str, Any]) -> None:
        """Отправляет этикетку на принтер напрямую через GDI."""
        logging.info(f"Прямая печать на принтер '{printer_name}'.")
        if not all([win32print, win32ui]):
            logging.error("pywin32 не установлен. Прямая печать невозможна.")
            raise ImportError("Библиотека pywin32 не установлена.")

        h_printer = None
        dc = None
        try:
            # Открываем принтер и создаем DC
            h_printer = win32print.OpenPrinter(printer_name)
            dc = win32ui.CreateDC()
            dc.CreatePrinterDC(printer_name)
            logging.debug(f"Успешно открыт принтер '{printer_name}'.")

            # Получаем DPI принтера
            dpi_x = dc.GetDeviceCaps(88)  # LOGPIXELSX
            dpi_y = dc.GetDeviceCaps(90)  # LOGPIXELSY
            dots_per_mm_x = dpi_x / 25.4
            dots_per_mm_y = dpi_y / 25.4
            logging.debug(f"DPI принтера: x={dpi_x}, y={dpi_y}")

            # Начинаем печать
            dc.StartDoc(f"Label from TildaKod: {template_json.get('name', 'N/A')}")
            dc.StartPage()

            for obj in template_json.get("objects", []):
                logging.debug(f"Обработка объекта: тип='{obj.get('type')}', источник='{obj.get('data_source')}'")
                
                required_fields = ["type", "x_mm", "y_mm", "width_mm", "height_mm", "data_source"]
                missing_fields = [f for f in required_fields if f not in obj]
                if missing_fields:
                    logging.warning(f"Пропуск объекта: отсутствуют поля {missing_fields}.")
                    continue

                obj_data = data.get(obj["data_source"])
                if obj_data is None and obj["data_source"] and '.' in obj["data_source"] and not obj["data_source"].startswith("QR:"):
                    logging.debug(f"Данные для '{obj['data_source']}' не найдены в data, попытка получения из БД.")
                    obj_data = PrintingService._fetch_data_from_db(user_info, obj["data_source"])
                
                if obj_data is None:
                    logging.warning(f"Данные для '{obj['data_source']}' не найдены. Пропуск объекта.")
                    continue

                try:
                    x = int(float(obj["x_mm"]) * dots_per_mm_x)
                    y = int(float(obj["y_mm"]) * dots_per_mm_y)
                    width = int(float(obj["width_mm"]) * dots_per_mm_x)
                    height = int(float(obj["height_mm"]) * dots_per_mm_y)
                    logging.debug(f"Рассчитанные размеры (px): x={x}, y={y}, width={width}, height={height}")
                except (ValueError, TypeError) as e:
                    logging.error(f"Ошибка преобразования координат для объекта: {e}")
                    continue

                if obj["type"] == "text":
                    logging.debug("Обработка как 'text'")
                    try:
                        font_height = -int(height * 0.8)
                        font = win32ui.CreateFont({
                            'name': obj.get("font_name", "Arial"),
                            'height': font_height,
                            'weight': 400,
                            'charset': 204  # RUSSIAN_CHARSET
                        })
                        dc.SelectObject(font)
                        dc.TextOut(x, y, str(obj_data))
                        win32gui.DeleteObject(font.GetHandle())
                    except Exception as e:
                        logging.error(f"Ошибка отрисовки текста: {e}")
                        continue
                
                elif obj["type"] == "barcode":
                    barcode_type = obj.get("barcode_type", "QR").upper()
                    logging.debug(f"Обработка как 'barcode', подтип: '{barcode_type}'")
                    
                    if not all([qrcode, Image, ImageWin]):
                        logging.warning("Библиотеки для штрихкодов (qrcode, Pillow, ImageWin) не установлены.")
                        continue

                    barcode_image = None
                    try:
                        data_str = str(obj_data).strip()
                        if not data_str:
                            logging.warning("Данные для штрихкода пусты. Пропуск.")
                            continue

                        if barcode_type == "QR":
                            qr_gen = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=1)
                            qr_gen.add_data(data_str)
                            qr_gen.make(fit=True)
                            barcode_image = qr_gen.make_image(fill_color="black", back_color="white")
                        
                        elif barcode_type == "DATAMATRIX":
                            if not DataMatrixEncoder:
                                logging.warning("Библиотека pystrich не установлена. Пропуск DataMatrix.")
                                continue
                            if not data_str.isascii():
                                logging.warning(f"Данные содержат не-ASCII символы: '{data_str}'. Перекодировка в ASCII.")
                                data_str = data_str.encode('ascii', errors='ignore').decode('ascii')
                                if not data_str:
                                    logging.warning("После перекодировки данные пусты. Пропуск.")
                                    continue
                            encoder = DataMatrixEncoder(data_str)
                            with io.BytesIO() as buffer:
                                encoder.save(buffer, "PNG")
                                buffer.seek(0)
                                barcode_image = Image.open(buffer).convert("RGB")
                        
                        else:
                            logging.warning(f"Тип штрихкода '{barcode_type}' не поддерживается.")
                            continue

                        if barcode_image:
                            scaled_image = barcode_image.resize((width, height), Image.Resampling.LANCZOS)
                            if scaled_image.mode != 'RGB':
                                scaled_image = scaled_image.convert('RGB')
                            dib = ImageWin.Dib(scaled_image)
                            bmp = win32ui.CreateBitmap()
                            bmp.CreateCompatibleBitmap(dc, width, height)
                            mem_dc = dc.CreateCompatibleDC()
                            mem_dc.SelectObject(bmp)
                            dib.draw(mem_dc.GetSafeHdc(), (0, 0, width, height))
                            dc.BitBlt((x, y), (width, height), mem_dc, (0, 0), win32con.SRCCOPY)
                            mem_dc.DeleteDC()
                            win32gui.DeleteObject(bmp.GetHandle())
                    except Exception as e:
                        logging.error(f"Ошибка генерации штрихкода '{barcode_type}': {e}")
                        continue

            dc.EndPage()
            dc.EndDoc()
            logging.info(f"Этикетка успешно напечатана на '{printer_name}'.")
        
        except pywin_error as e:
            logging.error(f"Ошибка Win32 API при прямой печати: {e}")
            raise RuntimeError(f"Ошибка печати (Win32): {e.strerror}") from e
        except Exception as e:
            logging.error(f"Неизвестная ошибка при прямой печати: {e}")
            raise RuntimeError(f"Неизвестная ошибка прямой печати: {e}")
        finally:
            if dc:
                dc.DeleteDC()
                logging.debug("Device Context освобожден.")
            if h_printer:
                win32print.ClosePrinter(h_printer)
                logging.debug(f"Принтер '{printer_name}' закрыт.")

    @staticmethod
    def print_labels_for_items(printer_name: str, paper_name: str, template_json: Dict[str, Any], items_data: list, user_info: Dict[str, Any]) -> None:
        """Печатает этикетки для списка элементов."""
        logging.info(f"Начало пакетной печати {len(items_data)} этикеток на принтер '{printer_name}'.")
        if not items_data:
            logging.warning("Список элементов для печати пуст.")
            return

        if not paper_name:
            logging.warning("Формат бумаги не указан.")

        for i, item_data in enumerate(items_data, 1):
            logging.info(f"Печать этикетки {i}/{len(items_data)}: {item_data}")
            try:
                PrintingService.print_label_direct(printer_name, template_json, item_data, user_info)
            except Exception as e:
                logging.error(f"Ошибка печати этикетки {i}: {e}")
                raise RuntimeError(f"Ошибка печати этикетки {i}/{len(items_data)}: {e}")

class LabelEditorWindow(tk.Toplevel if tk else object):
    """Окно визуального редактора макетов этикеток."""
    
    def __init__(self, parent, user_info: Dict[str, Any]):
        if not tk:
            logging.error("Tkinter не доступен. Редактор макетов не может быть запущен.")
            raise RuntimeError("Tkinter не доступен.")
        
        super().__init__(parent)
        logging.info("Инициализация редактора макетов.")
        self.title("Редактор макетов")
        self.geometry("1200x800")
        self.grab_set()

        self.user_info = user_info
        self.template: Optional[Dict[str, Any]] = None
        self.canvas_scale = 5  # 5 пикселей = 1 мм
        self.selected_object_id: Optional[int] = None
        self.canvas_objects: Dict[int, str] = {}
        self.active_view: Optional[str] = None
        self.layouts_list: list = []
        self.prop_entries: Dict[str, Any] = {}
        
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

        if not self.user_info.get("client_db_config"):
            logging.error("Отсутствует конфигурация БД клиента.")
            messagebox.showerror("Ошибка", "Конфигурация БД клиента не предоставлена.")
            self.destroy()
            return

        self._create_widgets()
        logging.info("Редактор макетов успешно инициализирован.")

    def _get_client_db_connection(self) -> psycopg2.extensions.connection:
        """Создает подключение к БД клиента."""
        logging.debug("Создание подключения к БД клиента.")
        db_config = self.user_info.get("client_db_config")
        if not db_config or not all([db_config.get(k) for k in ['db_host', 'db_port', 'db_name', 'db_user', 'db_password']]):
            logging.error(f"Неполная конфигурация БД: {db_config}")
            raise ConnectionError("Неполная конфигурация базы данных клиента.")

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
                logging.debug("Создание временного файла сертификата SSL.")
                with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.crt', encoding='utf-8') as fp:
                    fp.write(db_config['db_ssl_cert'])
                    temp_cert_file = fp.name
                conn_params.update({'sslmode': 'verify-full', 'sslrootcert': temp_cert_file})

            conn = psycopg2.connect(**conn_params)
            logging.info(f"Успешное подключение к БД: {conn_params['dbname']}")
            return conn
        except Exception as e:
            logging.error(f"Ошибка подключения к БД: {e}")
            raise
        finally:
            if temp_cert_file and os.path.exists(temp_cert_file):
                try:
                    os.remove(temp_cert_file)
                    logging.debug(f"Временный файл сертификата {temp_cert_file} удален.")
                except OSError as e:
                    logging.warning(f"Не удалось удалить временный файл {temp_cert_file}: {e}")

    def _create_widgets(self) -> None:
        """Создает виджеты редактора."""
        logging.debug("Создание виджетов редактора.")
        self.list_view_frame = ttk.Frame(self, padding="10")
        self._create_list_view_widgets()

        self.editor_view_frame = ttk.Frame(self)
        self._create_editor_view_widgets()

        self._switch_view('list')

    def _create_list_view_widgets(self) -> None:
        """Создает виджеты для списка макетов."""
        logging.debug("Создание виджетов для списка макетов.")
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

    def _create_editor_view_widgets(self) -> None:
        """Создает виджеты для редактора."""
        logging.debug("Создание виджетов для редактора.")
        paned_window = ttk.PanedWindow(self.editor_view_frame, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True)

        controls_frame = ttk.Frame(paned_window, width=300, padding="10")
        paned_window.add(controls_frame, weight=1)

        ttk.Button(controls_frame, text="<< К списку макетов", command=lambda: self._switch_view('list')).pack(fill=tk.X, pady=5)
        ttk.Button(controls_frame, text="Сохранить макет", command=self._save_layout).pack(fill=tk.X, pady=5)
        ttk.Separator(controls_frame).pack(fill=tk.X, pady=10)

        self.tools_frame = ttk.LabelFrame(controls_frame, text="Инструменты")
        self.tools_frame.pack(fill=tk.X, pady=5)

        ttk.Button(self.tools_frame, text="Добавить Текст", command=lambda: self._add_object_to_canvas("text")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить QR-код", command=lambda: self._add_object_to_canvas("QR")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить SSCC", command=lambda: self._add_object_to_canvas("SSCC")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить DataMatrix", command=lambda: self._add_object_to_canvas("DataMatrix")).pack(fill=tk.X, pady=2)

        self.properties_frame = ttk.LabelFrame(controls_frame, text="Свойства объекта")
        self.properties_frame.pack(fill=tk.X, pady=10)

        prop_fields = {
            "x_mm": "X (мм):",
            "y_mm": "Y (мм):",
            "width_mm": "Ширина (мм):",
            "height_mm": "Высота (мм):"
        }

        for key, text in prop_fields.items():
            frame = ttk.Frame(self.properties_frame)
            frame.pack(fill=tk.X, padx=5, pady=2)
            ttk.Label(frame, text=text, width=15).pack(side=tk.LEFT)
            entry = ttk.Entry(frame)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.prop_entries[key] = entry

        self.data_source_container_frame = ttk.Frame(self.properties_frame)
        self.data_source_container_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(self.data_source_container_frame, text="Источник данных:", width=15).pack(side=tk.LEFT)
        self.prop_entries["data_source"] = None

        self.apply_props_button = ttk.Button(self.properties_frame, text="Применить", command=self._apply_properties)
        self.apply_props_button.pack(pady=5)

        canvas_frame = ttk.Frame(paned_window)
        paned_window.add(canvas_frame, weight=4)

        self.canvas = tk.Canvas(canvas_frame, bg="lightgrey")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        self._toggle_properties_panel(False)
        self._toggle_tools_panel(False)

    def _switch_view(self, view_name: str) -> None:
        """Переключает между видом списка и редактора."""
        logging.debug(f"Переключение вида на '{view_name}'.")
        if self.active_view == view_name:
            return

        self.list_view_frame.pack_forget()
        self.editor_view_frame.pack_forget()

        if view_name == 'list':
            self.title("Редактор макетов - Список")
            self.list_view_frame.pack(fill=tk.BOTH, expand=True)
            self._load_layouts_to_tree()
        elif view_name == 'editor':
            layout_name = self.template.get('name', 'Новый макет') if self.template else 'Редактор'
            self.title(f"Редактор макетов - {layout_name}")
            self.editor_view_frame.pack(fill=tk.BOTH, expand=True)
            self._draw_canvas_background()
        
        self.active_view = view_name
        logging.info(f"Активный вид: {view_name}")

    def _prompt_for_new_layout(self) -> None:
        """Запрашивает параметры нового макета."""
        logging.debug("Запрос параметров нового макета.")
        name = simpledialog.askstring("Новый макет", "Введите название макета:", parent=self)
        if not name:
            logging.debug("Создание макета отменено пользователем.")
            return

        if any(layout['name'] == name for layout in self.layouts_list):
            logging.warning(f"Макет с именем '{name}' уже существует.")
            messagebox.showerror("Ошибка", "Макет с таким названием уже существует.", parent=self)
            return

        size_str = simpledialog.askstring("Размеры макета", "Введите размеры этикетки (Ширина x Высота) в мм:", parent=self)
        if not size_str:
            logging.debug("Ввод размеров макета отменен пользователем.")
            return

        try:
            width_str, height_str = size_str.lower().split('x')
            width_mm = int(width_str.strip())
            height_mm = int(height_str.strip())
        except (ValueError, IndexError):
            logging.error(f"Неверный формат размеров: '{size_str}'.")
            messagebox.showerror("Ошибка", "Неверный формат. Введите размеры в формате '100 x 50'.", parent=self)
            return

        self.template = {
            "name": name,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "objects": []
        }
        self.selected_object_id = None
        self.canvas_objects.clear()
        self._switch_view('editor')
        self._toggle_tools_panel(True)
        self._toggle_properties_panel(False)
        logging.info(f"Создан новый макет: {name} ({width_mm}x{height_mm} мм)")

    def _edit_selected_layout(self) -> None:
        """Открывает выбранный макет для редактирования."""
        logging.debug("Редактирование выбранного макета.")
        selected_item = self.layouts_tree.focus()
        if not selected_item:
            logging.warning("Макет не выбран для редактирования.")
            messagebox.showwarning("Внимание", "Выберите макет из списка для редактирования.", parent=self)
            return

        layout_name = self.layouts_tree.item(selected_item)['values'][0]
        layout_to_edit = next((l for l in self.layouts_list if l['name'] == layout_name), None)
        if layout_to_edit:
            self.template = layout_to_edit
            self.selected_object_id = None
            self.canvas_objects.clear()
            self._switch_view('editor')
            self._toggle_tools_panel(True)
            self._toggle_properties_panel(False)
            logging.info(f"Открыт для редактирования макет: {layout_name}")

    def _delete_selected_layout(self) -> None:
        """Удаляет выбранный макет."""
        logging.debug("Удаление выбранного макета.")
        selected_item = self.layouts_tree.focus()
        if not selected_item:
            logging.warning("Макет не выбран для удаления.")
            messagebox.showwarning("Внимание", "Выберите макет для удаления.", parent=self)
            return

        layout_name = self.layouts_tree.item(selected_item)['values'][0]
        if not messagebox.askyesno("Подтверждение", f"Вы уверены, что хотите удалить макет '{layout_name}'?", parent=self):
            logging.debug("Удаление макета отменено пользователем.")
            return

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM label_templates WHERE name = %s", (layout_name,))
                conn.commit()
            logging.info(f"Макет '{layout_name}' удален из БД.")
            messagebox.showinfo("Успех", f"Макет '{layout_name}' успешно удален.", parent=self)
            self._load_layouts_to_tree()
        except Exception as e:
            logging.error(f"Ошибка удаления макета '{layout_name}': {e}")
            messagebox.showerror("Ошибка", f"Не удалось удалить макет: {e}", parent=self)

    def _save_layout(self) -> None:
        """Сохраняет текущий макет в БД."""
        logging.debug("Сохранение макета.")
        if not self.template:
            logging.warning("Попытка сохранить пустой макет.")
            return

        layout_name = self.template.get('name')
        if not layout_name:
            logging.error("Отсутствует имя макета.")
            messagebox.showerror("Ошибка", "У макета отсутствует имя.", parent=self)
            return

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
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
            self.title(f"Редактор макетов - {layout_name}")
        except Exception as e:
            logging.error(f"Ошибка сохранения макета '{layout_name}': {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить макет: {e}", parent=self)

    def _draw_canvas_background(self) -> None:
        """Отрисовывает фон этикетки на холсте."""
        logging.debug("Отрисовка фона холста.")
        self.canvas.delete("all")
        if not self.template:
            logging.warning("Шаблон не задан, холст не отрисован.")
            return

        try:
            width_px = float(self.template['width_mm']) * self.canvas_scale
            height_px = float(self.template['height_mm']) * self.canvas_scale
            self.canvas.create_rectangle(10, 10, 10 + width_px, 10 + height_px, fill="white", outline="black", tags="label_bg")
            for i, obj in enumerate(self.template['objects']):
                self._draw_object(obj, i)
            logging.debug("Фон холста успешно отрисован.")
        except (KeyError, ValueError) as e:
            logging.error(f"Ошибка отрисовки фона холста: {e}")

    def _load_layouts_to_tree(self) -> None:
        """Загружает список макетов в Treeview."""
        logging.debug("Загрузка списка макетов в Treeview.")
        self.layouts_list.clear()
        for i in self.layouts_tree.get_children():
            self.layouts_tree.delete(i)

        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT name, template_json FROM label_templates ORDER BY name")
                    for row in cur.fetchall():
                        name, template_data = row
                        self.layouts_list.append(template_data)
                        size_str = f"{template_data.get('width_mm', '?')} x {template_data.get('height_mm', '?')}"
                        self.layouts_tree.insert('', 'end', values=(name, size_str))
            logging.info(f"Загружено {len(self.layouts_list)} макетов из БД.")
        except Exception as e:
            logging.error(f"Ошибка загрузки макетов из БД: {e}")
            messagebox.showerror("Ошибка", f"Не удалось загрузить макеты: {e}", parent=self)

    def _add_object_to_canvas(self, obj_type: str) -> None:
        """Добавляет объект на холст и в шаблон."""
        logging.debug(f"Добавление объекта типа '{obj_type}' на холст.")
        if not self.template:
            logging.warning("Попытка добавить объект без активного макета.")
            messagebox.showwarning("Внимание", "Сначала создайте новый макет.", parent=self)
            return

        new_object = {
            "type": "text" if obj_type == "text" else "barcode",
            "x_mm": 10,
            "y_mm": 10,
            "width_mm": 40 if obj_type == "text" else 30,
            "height_mm": 15 if obj_type == "text" else 30
        }
        
        if obj_type == "text":
            new_object["data_source"] = self.available_text_sources[0]
            new_object["font_name"] = "Helvetica"
        else:
            new_object["barcode_type"] = obj_type
            if obj_type == "QR":
                new_object["data_source"] = self.available_qr_sources[0]
            elif obj_type == "SSCC":
                new_object["data_source"] = self.available_sscc_sources[0]
            elif obj_type == "DataMatrix":
                new_object["data_source"] = self.available_datamatrix_sources[0]

        object_id = len(self.template["objects"])
        self.template["objects"].append(new_object)
        self._draw_object(new_object, object_id)
        logging.info(f"Добавлен объект: {obj_type}, ID: {object_id}")

    def _draw_object(self, obj_data: Dict[str, Any], object_id: int) -> None:
        """Отрисовывает объект на холсте."""
        logging.debug(f"Отрисовка объекта ID: {object_id}, тип: {obj_data.get('type')}")
        canvas_tag = f"obj_{object_id}"
        self.canvas_objects[object_id] = canvas_tag

        try:
            x_px = 10 + float(obj_data['x_mm']) * self.canvas_scale
            y_px = 10 + float(obj_data['y_mm']) * self.canvas_scale
            width_px = float(obj_data['width_mm']) * self.canvas_scale
            height_px = float(obj_data['height_mm']) * self.canvas_scale
        except (KeyError, ValueError) as e:
            logging.error(f"Ошибка преобразования координат объекта ID {object_id}: {e}")
            return

        outline_color = "blue" if object_id == self.selected_object_id else "grey"
        if obj_data['type'] == 'text':
            fill_color = "lightyellow"
            display_text = "Текст"
        else:
            fill_color = "lightblue"
            display_text = obj_data['barcode_type']

        self.canvas.create_rectangle(x_px, y_px, x_px + width_px, y_px + height_px, fill=fill_color, outline=outline_color, width=2, tags=(canvas_tag, "object"))
        self.canvas.create_text(x_px + width_px / 2, y_px + height_px / 2, text=display_text, tags=(canvas_tag, "object_text"))
        logging.debug(f"Объект ID {object_id} отрисован на холсте.")

    def _on_canvas_click(self, event: tk.Event) -> None:
        """Обрабатывает клик по холсту."""
        logging.debug(f"Клик по холсту: x={event.x}, y={event.y}")
        clicked_items = self.canvas.find_withtag(tk.CURRENT)
        if not clicked_items:
            self._select_object(None)
            return

        for tag in self.canvas.gettags(clicked_items[0]):
            if tag.startswith("obj_"):
                try:
                    object_id = int(tag.split("_")[1])
                    self._select_object(object_id)
                    return
                except (ValueError, IndexError):
                    continue
        self._select_object(None)

    def _select_object(self, object_id: Optional[int]) -> None:
        """Выделяет объект и обновляет UI."""
        logging.debug(f"Выбор объекта ID: {object_id}")
        if self.selected_object_id == object_id:
            return

        self.selected_object_id = object_id
        self._draw_canvas_background()
        if object_id is not None:
            self._toggle_properties_panel(True)
            self._update_properties_panel()
        else:
            self._toggle_properties_panel(False)

    def _toggle_properties_panel(self, active: bool) -> None:
        """Включает/выключает панель свойств."""
        logging.debug(f"Переключение панели свойств: {'вкл' if active else 'выкл'}")
        state = "normal" if active else "disabled"
        for child_widget in self.properties_frame.winfo_children():
            if isinstance(child_widget, ttk.Frame):
                for grand_child_widget in child_widget.winfo_children():
                    try:
                        grand_child_widget.config(state=state)
                    except tk.TclError:
                        if isinstance(grand_child_widget, (ttk.Entry, ttk.Combobox)):
                            grand_child_widget.state([state] if state == "normal" else [state])
            else:
                try:
                    child_widget.config(state=state)
                except tk.TclError:
                    if isinstance(child_widget, ttk.Button):
                        child_widget.state([state] if state == "normal" else [state])

    def _update_properties_panel(self) -> None:
        """Обновляет панель свойств для выбранного объекта."""
        logging.debug(f"Обновление панели свойств для объекта ID: {self.selected_object_id}")
        if self.selected_object_id is None:
            return

        obj_data = self.template['objects'][self.selected_object_id]
        for key in ["x_mm", "y_mm", "width_mm", "height_mm"]:
            entry_widget = self.prop_entries[key]
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, str(obj_data.get(key, '')))

        for widget in self.data_source_container_frame.winfo_children():
            if widget != self.data_source_container_frame.winfo_children()[0]:
                widget.destroy()

        obj_type = obj_data['type']
        current_data_source = obj_data.get('data_source', '')
        data_source_widget = None

        if obj_type == 'text':
            data_source_widget = ttk.Combobox(self.data_source_container_frame, values=self.available_text_sources, state="readonly")
            data_source_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
            data_source_widget.set(current_data_source or self.available_text_sources[0])
        elif obj_type == 'barcode':
            barcode_type = obj_data.get('barcode_type', '').upper()
            values = {
                'QR': self.available_qr_sources,
                'SSCC': self.available_sscc_sources,
                'DATAMATRIX': self.available_datamatrix_sources
            }.get(barcode_type, [])
            data_source_widget = ttk.Combobox(self.data_source_container_frame, values=values, state="readonly")
            data_source_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
            data_source_widget.set(current_data_source or values[0] if values else '')
        else:
            data_source_widget = ttk.Entry(self.data_source_container_frame, state="disabled")
            data_source_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
            data_source_widget.insert(0, "Неизвестный тип объекта")

        self.prop_entries["data_source"] = data_source_widget
        logging.debug("Панель свойств обновлена.")

    def _apply_properties(self) -> None:
        """Применяет свойства к выбранному объекту."""
        logging.debug(f"Применение свойств для объекта ID: {self.selected_object_id}")
        if self.selected_object_id is None:
            return

        try:
            for key, entry in self.prop_entries.items():
                if key == 'data_source':
                    self.template['objects'][self.selected_object_id][key] = entry.get()
                else:
                    value = float(entry.get())
                    self.template['objects'][self.selected_object_id][key] = value
            self._draw_canvas_background()
            logging.info(f"Свойства объекта ID {self.selected_object_id} обновлены.")
        except ValueError:
            logging.error("Ошибка: геометрические свойства должны быть числами.")
            messagebox.showerror("Ошибка", "Значения геометрических свойств должны быть числами.", parent=self)