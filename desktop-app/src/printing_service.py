import io
import logging
import json
import os
import tempfile
import textwrap
from typing import Dict, Any, Optional
from psycopg2 import sql
from psycopg2.extras import RealDictCursor # Явно импортируем RealDictCursor
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
    from pylibdmtx.pylibdmtx import encode as dmtx_encode
except ImportError:
    logging.warning("Библиотека pylibdmtx не установлена. Установите: pip install pylibdmtx")
    dmtx_encode = None

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
    level=logging.DEBUG,
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

        logging.debug(f"Параметры подключения к БД: {conn_params}")
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
    def _get_multiline_fitting_font(draw: ImageDraw.Draw, text: str, font_name: str, max_width: int, max_height: int) -> tuple[ImageFont.FreeTypeFont, str]:
        """
        Подбирает шрифт и переносит текст по словам, чтобы он поместился в заданные рамки.
        """
        font_size = min(max_height, 72) # Начинаем с разумного максимального размера шрифта
        font = None
        wrapped_text = text

        def load_font(size):
            try:
                return ImageFont.truetype(f"{font_name.lower()}.ttf", size=size, encoding='unic')
            except IOError:
                try:
                    return ImageFont.truetype("arial.ttf", size=size, encoding='unic')
                except IOError:
                    return ImageFont.load_default()

        while font_size > 4: # Минимальный размер шрифта
            font = load_font(font_size)

            # --- НОВЫЙ, БОЛЕЕ ТОЧНЫЙ АЛГОРИТМ ПЕРЕНОСА СТРОК ---
            lines = []
            words = text.split()
            if not words:
                return font, ""

            current_line = words[0]
            for word in words[1:]:
                # Проверяем ширину текущей строки + новое слово
                if draw.textbbox((0,0), current_line + " " + word, font=font)[2] <= max_width:
                    current_line += " " + word
                else:
                    # Если не помещается, завершаем текущую строку и начинаем новую
                    lines.append(current_line)
                    current_line = word
            lines.append(current_line) # Добавляем последнюю строку

            wrapped_text = "\n".join(lines)
            text_height = draw.textbbox((0,0), wrapped_text, font=font)[3]

            if text_height <= max_height:
                return font, wrapped_text # Шрифт и текст подходят
            else:
                # Если не помещается, уменьшаем размер шрифта и пробуем снова
                font_size -= 1
        
        # Если цикл завершился, значит, даже самый маленький шрифт не поместился.
        # Возвращаем самый маленький шрифт и максимально обернутый текст.
        return font, wrapped_text
    @staticmethod
    def _get_fitting_font(text: str, font_name: str, max_width: int, max_height: int) -> ImageFont.FreeTypeFont:
        """
        Подбирает максимальный размер шрифта, чтобы текст поместился в заданные рамки.
        """
        font_size = max_height  # Начинаем с максимальной высоты
        font = None

        # Пытаемся загрузить указанный шрифт, с фолбэком на Arial и дефолтный
        def load_font(size):
            try:
                return ImageFont.truetype(f"{font_name.lower()}.ttf", size=size)
            except IOError:
                try:
                    return ImageFont.truetype("arial.ttf", size=size)
                except IOError:
                    return ImageFont.load_default()

        while font_size > 5:  # Минимальный размер шрифта
            font = load_font(font_size)

            # Для растровых шрифтов (load_default) getbbox может не работать как надо
            if not hasattr(font, 'getbbox'):
                return font # Возвращаем как есть

            text_bbox = font.getbbox(text)
            text_width = text_bbox[2] - text_bbox[0]
            if text_width <= max_width:
                return font  # Шрифт подходит по ширине и высоте (т.к. начали с max_height)
            font_size -= 1
        
        return font # Возвращаем самый маленький из попробованных, если ничего не подошло
    @staticmethod
    def generate_label_image(template_json: Dict[str, Any], data: Dict[str, Any], user_info: Dict[str, Any], text_cache: Optional[Dict] = None) -> Optional[Image.Image]:
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

            # --- ИСПРАВЛЕНИЕ: Используем переданный кэш или создаем новый, если он не передан ---
            if text_cache is None:
                text_cache = {}

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

                # --- НОВАЯ ЛОГИКА: Обработка произвольного текста ---
                if obj.get("is_custom_text"):
                    obj_data = obj.get("data_source") # Для произвольного текста данные хранятся прямо в шаблоне
                else:
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
                    # --- НОВАЯ ЛОГИКА: Используем кэш для произвольного текста ---
                    if obj.get("is_custom_text"):
                        cache_key = obj['data_source'] # Ключ - сам текст
                        if cache_key not in text_cache:
                            # Если в кэше нет, рассчитываем и сохраняем
                            logging.debug(f"Кэширование произвольного текста: '{cache_key[:30]}...'")
                            font, wrapped_text = PrintingService._get_multiline_fitting_font(draw, str(obj_data), obj.get("font_name", "arial"), width, height)
                            text_cache[cache_key] = (font, wrapped_text)
                        else:
                            # Если в кэше есть, берем готовый результат
                            logging.debug("Использование кэшированного произвольного текста.")
                            font, wrapped_text = text_cache[cache_key]
                        draw.text((x, y), wrapped_text, fill="black", font=font, anchor="la")

                elif obj["type"] == "image":
                    logging.debug("Обработка как 'image'")
                    image_name = str(obj_data)
                    try:
                        # Пытаемся получить изображение из БД
                        with PrintingService._get_client_db_connection(user_info) as conn:
                            with conn.cursor() as cur:
                                cur.execute("SELECT image_data FROM ap_images WHERE name = %s", (image_name,))
                                result = cur.fetchone()
                        if result:
                            image_bytes = result[0]
                            img_obj = Image.open(io.BytesIO(image_bytes))
                            img_obj = img_obj.resize((width, height), Image.Resampling.LANCZOS)
                            label_image.paste(img_obj, (x, y), img_obj if img_obj.mode == 'RGBA' else None)
                        else:
                            logging.warning(f"Изображение с именем '{image_name}' не найдено в БД.")
                    except Exception as e:
                        logging.error(f"Ошибка при отрисовке изображения '{image_name}': {e}")

                
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
                        if not dmtx_encode:
                            logging.warning("Библиотека pylibdmtx не установлена. Пропуск DataMatrix.")
                            continue
                        try:
                            data_str = str(obj_data).strip()
                            if not data_str:
                                logging.warning("Данные для DataMatrix пусты. Пропуск.")
                                continue
                            # --- ИСПРАВЛЕНИЕ: Преобразуем результат pylibdmtx в изображение Pillow ---
                            # dmtx_encode возвращает специальный объект, а не готовое изображение.
                            # Создаем изображение из его пикселей, ширины и высоты.
                            encoded_dm = dmtx_encode(data_str.encode('utf-8'))
                            # --- ИЗМЕНЕНИЕ: Преобразуем в 1-битный режим для совместимости с термотрансферными принтерами ---
                            barcode_image = Image.frombytes('RGB', (encoded_dm.width, encoded_dm.height), encoded_dm.pixels).convert('1')
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
            return label_image.convert('1') # Принудительно возвращаем Ч/Б изображение
        
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
        try:
            # --- НОВАЯ ЛОГИКА: Сначала генерируем полное изображение ---
            label_image = PrintingService.generate_label_image(template_json, data, user_info)
            if not label_image:
                logging.error("Не удалось сгенерировать изображение этикетки. Печать отменена.")
                return

            # --- Открываем принтер и получаем его характеристики ---
            h_printer = win32print.OpenPrinter(printer_name)
            dc = win32ui.CreateDC()
            dc.CreatePrinterDC(printer_name)

            # Физические размеры бумаги в пикселях
            paper_width_px = dc.GetDeviceCaps(win32con.PHYSICALWIDTH)
            paper_height_px = dc.GetDeviceCaps(win32con.PHYSICALHEIGHT)
            logging.info(f"Физический размер бумаги: {paper_width_px}x{paper_height_px} px.")

            label_width_px, label_height_px = label_image.size
            logging.info(f"Размер сгенерированного макета: {label_width_px}x{label_height_px} px.")

            final_image = label_image
            # --- Логика масштабирования и позиционирования ---
            if label_width_px > paper_width_px or label_height_px > paper_height_px:
                logging.info("Макет больше бумаги. Масштабирую для вписывания.")
                # Сохраняем пропорции
                ratio = min(paper_width_px / label_width_px, paper_height_px / label_height_px)
                new_width = int(label_width_px * ratio)
                new_height = int(label_height_px * ratio)
                final_image = label_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
                logging.info(f"Новый размер макета: {new_width}x{new_height} px.")
            else:
                logging.info("Макет меньше или равен бумаге. Масштабирование не требуется.")

            # --- Печать подготовленного изображения ---
            dc.StartDoc(f"Label from TildaKod: {template_json.get('name', 'N/A')}")
            dc.StartPage()

            # Преобразуем изображение Pillow в формат, понятный для GDI
            dib = ImageWin.Dib(final_image)
            # Размещаем изображение в левом верхнем углу (0, 0)
            dib.draw(dc.GetSafeHdc(), (0, 0, final_image.width, final_image.height))

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
        ttk.Button(controls_frame, text="Сохранить макет", command=lambda: self._save_layout(show_success_message=True)).pack(fill=tk.X, pady=5)
        ttk.Separator(controls_frame).pack(fill=tk.X, pady=10)
        # --- НОВЫЕ КНОПКИ ---
        ttk.Button(controls_frame, text="Предпросмотр", command=self._open_preview).pack(fill=tk.X, pady=2)
        ttk.Button(controls_frame, text="Тестовая печать", command=self._open_test_print_dialog).pack(fill=tk.X, pady=2)
        ttk.Separator(controls_frame).pack(fill=tk.X, pady=10)

        self.tools_frame = ttk.LabelFrame(controls_frame, text="Инструменты")
        self.tools_frame.pack(fill=tk.X, pady=5)

        ttk.Button(self.tools_frame, text="Добавить Текст", command=lambda: self._add_object_to_canvas("text")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить Произвольный текст", command=lambda: self._add_object_to_canvas("custom_text")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить QR-код", command=lambda: self._add_object_to_canvas("QR")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить SSCC", command=lambda: self._add_object_to_canvas("SSCC")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить DataMatrix", command=lambda: self._add_object_to_canvas("DataMatrix")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить Изображение", command=lambda: self._add_object_to_canvas("image")).pack(fill=tk.X, pady=2)
        ttk.Separator(self.tools_frame).pack(fill=tk.X, pady=5)
        ttk.Button(self.tools_frame, text="Удалить выделенное", command=self._delete_selected_object, style="Danger.TButton").pack(fill=tk.X, pady=2)

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
        ttk.Button(controls_frame, text="Загрузить изображение...", command=self._upload_image).pack(fill=tk.X, pady=(10, 2))
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)

    def _upload_image(self):
        """Открывает диалог для загрузки изображения и сохранения его в БД."""
        logging.debug("Запуск процесса загрузки изображения.")
        if not tk:
            return

        file_path = tk.filedialog.askopenfilename(
            title="Выберите изображение",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp"), ("Все файлы", "*.*")],
            parent=self
        )
        if not file_path:
            logging.debug("Выбор файла отменен.")
            return

        image_name = simpledialog.askstring("Имя изображения", "Введите уникальное имя для этого изображения:", parent=self)
        if not image_name:
            logging.debug("Ввод имени отменен.")
            return

        try:
            with open(file_path, 'rb') as f:
                image_data = f.read()

            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ap_images (name, image_data) VALUES (%s, %s) ON CONFLICT (name) DO UPDATE SET image_data = EXCLUDED.image_data;",
                        (image_name, image_data)
                    )
                conn.commit()
            
            logging.info(f"Изображение '{image_name}' успешно загружено/обновлено в БД.")
            messagebox.showinfo("Успех", f"Изображение '{image_name}' успешно загружено.", parent=self)
        except Exception as e:
            logging.error(f"Ошибка при загрузке изображения: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось загрузить изображение: {e}", parent=self)

    def _delete_selected_object(self):
        """Удаляет выделенный объект с холста и из шаблона."""
        logging.debug("Попытка удаления выделенного объекта.")
        if self.selected_object_id is None:
            messagebox.showwarning("Внимание", "Нет выделенных объектов для удаления.", parent=self)
            return

        if not messagebox.askyesno("Подтверждение", "Вы уверены, что хотите удалить выделенный объект?", parent=self):
            logging.debug("Удаление отменено пользователем.")
            return

        try:
            # Удаляем объект из списка
            del self.template['objects'][self.selected_object_id]
            logging.info(f"Объект с индексом {self.selected_object_id} удален из шаблона.")

            # Сбрасываем выделение и перерисовываем холст
            self.selected_object_id = None
            self._draw_canvas_background()
        except IndexError:
            logging.error(f"Ошибка удаления: индекс {self.selected_object_id} вне диапазона.")

    def _open_preview(self):
        """Открывает окно предпросмотра с тестовыми данными."""
        # --- НОВАЯ ЛОГИКА: Сохраняем макет и открываем диалог печати ---
        if not self.template:
            messagebox.showwarning("Внимание", "Нет активного макета.", parent=self)
            return

        try:
            # 1. Сохраняем текущий макет
            self._save_layout(show_success_message=False) # Сохраняем без всплывающего окна

            # 2. Получаем тестовые данные
            test_data = self._get_test_data_for_template() # Используем существующий метод
            if not test_data:
                messagebox.showwarning("Внимание", "Не удалось найти тестовые данные для предпросмотра.", parent=self)
                return

            # --- ИСПРАВЛЕНИЕ: Убираем лишний цикл и вызываем диалог один раз ---
            # Диалог сам обработает весь список `test_data` и сгенерирует все этикетки.
            from .admin_ui import PrintWorkplaceLabelsDialog
            PrintWorkplaceLabelsDialog(self, self.user_info, f"Предпросмотр: {self.template['name']}", test_data, preselected_layout=self.template['name'])

        except Exception as e:
            logging.error(f"Ошибка при создании предпросмотра: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось создать предпросмотр: {e}", parent=self)

    def _open_test_print_dialog(self):
        """Открывает диалог тестовой печати."""
        if not self.template:
            messagebox.showwarning("Внимание", "Нет активного макета для печати.", parent=self)
            return

        try:
            test_data = self._get_test_data_for_template()
            if not test_data:
                messagebox.showwarning("Внимание", "Не удалось найти тестовые данные для печати.", parent=self)
                return

            # Для тестовой печати используем только первый набор данных
            item_to_print = test_data[0]
            
            # Вызываем стандартный диалог печати, но передаем ему один макет и один набор данных
            from .admin_ui import PrintWorkplaceLabelsDialog
            # Создаем "фальшивый" список макетов
            layout_for_dialog = [{'name': self.template['name'], 'json': self.template}]
            
            # Модифицируем конструктор PrintWorkplaceLabelsDialog, чтобы он мог принимать макет напрямую
            # или создаем новый специализированный диалог.
            # Пока что используем существующий, но это может потребовать рефакторинга.
            # Для простоты, мы можем передать данные через items_to_print и заблокировать выбор макета.
            
            # Временное решение: используем PrintWorkplaceLabelsDialog, как он есть.
            # Он сам загрузит макеты, пользователь должен будет выбрать текущий.
            PrintWorkplaceLabelsDialog(self, self.user_info, f"Тест: {self.template['name']}", [item_to_print])

        except Exception as e:
            logging.error(f"Ошибка при открытии диалога тестовой печати: {e}", exc_info=True)
            messagebox.showerror("Ошибка", f"Не удалось открыть диалог печати: {e}", parent=self)

    def _get_test_data_for_template(self) -> list:
        """Собирает тестовые данные для всех источников в макете."""
        if not self.template:
            return []

        # --- ИЗМЕНЕНИЕ: Собираем только те источники, что есть в макете ---
        data_sources = {
            obj['data_source'] for obj in self.template.get('objects', []) if not obj.get('is_custom_text')
        }
        
        # Создаем базовый набор данных с заглушками только для нужных источников
        base_test_data = {}
        for source in data_sources:
            base_test_data[source] = f"<{source}>"
        logging.debug(f"Подготовлены заглушки для следующих источников: {list(data_sources)}")

        # --- НОВАЯ ЛОГИКА: Загружаем реальные данные для заглушек ---
        try:
            # Используем один коннект для всех запросов
            with self._get_client_db_connection() as conn:
                logging.debug(f"Соединение с БД получено: {conn}, тип: {type(conn)}")
                with conn.cursor(cursor_factory=RealDictCursor) as cur: # Явно указываем cursor_factory
                    # --- ВОССТАНОВЛЕННЫЙ БЛОК: Загружаем SSCC, если он нужен ---
                    sscc_needed = "packages.sscc_code" in data_sources
                    logging.debug(f"Проверка 'packages.sscc_code' в data_sources: {sscc_needed}")
                    if sscc_needed:
                        cur.execute("SELECT distinct sscc as sscc_code FROM packages p left join items i on p.id = i.package_id where order_id=1")
                        packages = cur.fetchall()
                        logging.debug(f"Получено {len(packages)} строк с SSCC кодами.")
                        if packages: base_test_data['packages.sscc_code'] = packages[0]['sscc_code']

                    # --- ВОССТАНОВЛЕННЫЙ БЛОК: Загружаем данные для QR, если они нужны ---
                    qr_workplace_needed = "QR: Конфигурация рабочего места" in data_sources
                    wp_name_needed = "ap_workplaces.warehouse_name" in data_sources
                    wp_num_needed = "ap_workplaces.workplace_number" in data_sources
                    logging.debug(f"Проверка 'QR: Конфигурация рабочего места' и 'ap_workplaces.*' в data_sources: {qr_workplace_needed or wp_name_needed or wp_num_needed}")
                    if qr_workplace_needed or wp_name_needed or wp_num_needed:
                        cur.execute("SELECT warehouse_name, workplace_number FROM ap_workplaces where warehouse_name='Тестовый склад'")
                        wps = cur.fetchall()
                        logging.debug(f"Получено {len(wps)} строк с данными о рабочих местах.")
                        if wps:
                            wp = wps[0]
                            base_test_data["QR: Конфигурация рабочего места"] = json.dumps({
                                "type": "workplace_config",
                                "warehouse": wp['warehouse_name'],
                                "workplace": wp['workplace_number']
                            }, ensure_ascii=False)
                            base_test_data["ap_workplaces.warehouse_name"] = wp['warehouse_name']
                            base_test_data["ap_workplaces.workplace_number"] = wp['workplace_number']

                    # --- ВОССТАНОВЛЕННЫЙ БЛОК: Загружаем текстовые поля, если они есть в макете ---
                    for source in data_sources:
                        # Пропускаем уже обработанные поля и DataMatrix
                        if source and '.' in source and not source.startswith('QR:') and source != 'items.datamatrix' and \
                           source not in ["packages.sscc_code", "ap_workplaces.warehouse_name", "ap_workplaces.workplace_number"]:
                            table, field = source.split('.')
                            query = sql.SQL("SELECT {} FROM {} WHERE {} IS NOT NULL LIMIT 1").format(
                                sql.Identifier(field), sql.Identifier(table), sql.Identifier(field)
                            )
                            logging.debug(f"Выполнение запроса для тестовых данных: {query.as_string(conn)}")
                            cur.execute(query)
                            all_data = cur.fetchall()
                            logging.debug(f"Для источника '{source}' загружено {len(all_data)} строк.")
                            if all_data:
                                base_test_data[source] = all_data[0][field]

                    # --- ИЗМЕНЕНИЕ: Загружаем DataMatrix, только если он нужен ---
                    datamatrix_needed = "items.datamatrix" in data_sources
                    datamatrix_codes = []
                    logging.debug(f"Проверка 'items.datamatrix' в data_sources: {datamatrix_needed}")
                    if datamatrix_needed:
                        # --- ВОССТАНОВЛЕННАЯ ЛОГИКА: Загружаем ВСЕ коды для предпросмотра ---
                        cur.execute("SELECT datamatrix FROM items WHERE order_id=1")
                        results = cur.fetchall()
                        logging.debug(f"Получено {len(results)} строк с DataMatrix кодами из БД.")
                        if results:
                            datamatrix_codes = [str(row['datamatrix']) for row in results]

            # --- Формируем итоговый список ---
            final_test_data_list = []
            # Если коды найдены, создаем по этикетке на каждый код
            if datamatrix_codes:
                for dm_code in datamatrix_codes:
                    item_data = base_test_data.copy()
                    item_data['items.datamatrix'] = dm_code
                    final_test_data_list.append(item_data)
            else:
                # Если кодов нет, создаем одну этикетку с DM-заглушкой
                base_test_data['items.datamatrix'] = f"0104604060006532215!\"#%&'()*+,-./:;<=>?_1234567890ABCDEFGHIJKLM{chr(29)}91EE06{chr(29)}92QUFBQUFBPT0="
                final_test_data_list.append(base_test_data)

            return final_test_data_list

        except Exception as e:
            logging.warning(f"Не удалось получить все тестовые данные из БД: {e}")
            # Не прерываем, используем заглушки
            # Возвращаем одну этикетку с базовыми заглушками
            return [base_test_data]

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
            # --- НОВАЯ ЛОГИКА: Панель инструментов всегда активна в редакторе ---
            # Просто включаем все кнопки один раз при переходе в режим редактора.
            for child_widget in self.tools_frame.winfo_children():
                try:
                    if isinstance(child_widget, ttk.Button):
                        child_widget.state(["!disabled"])
                except tk.TclError:
                    pass
        
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
        self._toggle_properties_panel(False) # Очищаем и деактивируем панель свойств
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
            self._toggle_properties_panel(False) # Очищаем и деактивируем панель свойств
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

    def _save_layout(self, show_success_message=True) -> None:
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
            if show_success_message:
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

        # --- ИСПРАВЛЕНИЕ: Корректно определяем тип объекта ---
        new_object = {
            "type": "text" if obj_type in ["text", "custom_text"] else ("image" if obj_type == "image" else "barcode"),
            "x_mm": 10,
            "y_mm": 10,
            "width_mm": 40 if obj_type in ["text", "custom_text"] else 30,
            "height_mm": 15 if obj_type in ["text", "custom_text"] else 30
        }
        
        if obj_type == "text":
            new_object["data_source"] = self.available_text_sources[0]
            new_object["font_name"] = "Helvetica"
        elif obj_type == "custom_text":
            new_object["is_custom_text"] = True
            new_object["data_source"] = "Ваш текст" # Текст по умолчанию
            new_object["font_name"] = "Helvetica"
        elif obj_type == "image":
            new_object["data_source"] = "" # По умолчанию источник не выбран
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
        if obj_data.get('type') == 'text':
            fill_color = "lightyellow"
            display_text = "Текст"
        # --- ИСПРАВЛЕНИЕ: Добавляем отдельную обработку для изображений ---
        elif obj_data.get('type') == 'image':
            fill_color = "lightgreen" # Другой цвет для наглядности
            display_text = "IMG"
        else:
            # Этот блок теперь только для штрихкодов
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
        # --- ОКОНЧАТЕЛЬНОЕ ИСПРАВЛЕНИЕ: Управляем только видимостью панели, а не состоянием виджетов. ---
        # Это простое решение гарантирует, что этот метод не повлияет на другие элементы интерфейса.
        if active:
            if not self.properties_frame.winfo_ismapped():
                self.properties_frame.pack(fill=tk.X, pady=10)
        else:
            if self.properties_frame.winfo_ismapped():
                self.properties_frame.pack_forget()
    def _toggle_tools_panel(self, active: bool) -> None:
        """Включает/выключает панель инструментов."""
        logging.debug(f"Переключение панели инструментов: {'вкл' if active else 'выкл'}")
        state = "normal" if active else "disabled"
        for child_widget in self.tools_frame.winfo_children():
            try:
                if isinstance(child_widget, ttk.Button):
                    child_widget.state([state] if state == "normal" else [state])
                else:
                    child_widget.config(state=state)
            except tk.TclError:
                # Пропускаем виджеты, которые не поддерживают изменение состояния (например, Separator)
                pass

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

        if obj_data.get("is_custom_text"):
            data_source_widget = ttk.Entry(self.data_source_container_frame)
            data_source_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
            data_source_widget.insert(0, current_data_source)
        elif obj_type == 'text':
            data_source_widget = ttk.Combobox(self.data_source_container_frame, values=self.available_text_sources, state="readonly")
            data_source_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
            data_source_widget.set(current_data_source or self.available_text_sources[0])

        elif obj_type == 'image':
            # --- НОВАЯ ЛОГИКА: Кнопка для вызова диалога выбора изображения ---
            data_source_widget = ttk.Entry(self.data_source_container_frame, state="readonly")
            data_source_widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
            data_source_widget.config(state="normal")
            data_source_widget.delete(0, tk.END)
            data_source_widget.insert(0, current_data_source)
            data_source_widget.config(state="readonly")

            def open_dialog():
                dialog = ImageSelectionDialog(self, self.user_info)
                self.wait_window(dialog) # Ждем закрытия диалога
                if dialog.selected_image_name:
                    # --- ИСПРАВЛЕНИЕ: Сразу обновляем данные в шаблоне и перерисовываем ---
                    if self.selected_object_id is not None:
                        self.template['objects'][self.selected_object_id]['data_source'] = dialog.selected_image_name
                        self._update_properties_panel() # Обновляем панель, чтобы показать новое значение
                        self._draw_canvas_background() # Перерисовываем холст

            ttk.Button(self.data_source_container_frame, text="Выбрать...", command=open_dialog).pack(side=tk.LEFT, padx=(5,0))

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

class ImageSelectionDialog(tk.Toplevel if tk else object):
    """Диалог для визуального выбора изображения из базы данных."""
    def __init__(self, parent, user_info):
        super().__init__(parent)
        self.title("Выбор изображения")
        self.geometry("600x500")
        self.transient(parent)
        self.grab_set()

        self.user_info = user_info
        self.selected_image_name = None
        self.image_references = [] # Для предотвращения удаления изображений сборщиком мусора

        # --- Создание прокручиваемой области ---
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._load_images()

    def _get_client_db_connection(self):
        # Используем метод из основного сервиса
        return PrintingService._get_client_db_connection(self.user_info)

    def _load_images(self):
        """Загружает изображения из БД и отображает их."""
        try:
            with self._get_client_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT name, image_data FROM ap_images ORDER BY name;")
                    images = cur.fetchall()

            for name, image_data in images:
                item_frame = ttk.Frame(self.scrollable_frame, padding=5, borderwidth=1, relief="solid")
                item_frame.pack(fill=tk.X, padx=5, pady=5)

                # Отображение миниатюры
                try:
                    img = Image.open(io.BytesIO(image_data))
                    img.thumbnail((80, 80), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self.image_references.append(photo) # Сохраняем ссылку

                    img_label = ttk.Label(item_frame, image=photo)
                    img_label.pack(side=tk.LEFT, padx=5)
                except Exception as e:
                    logging.warning(f"Не удалось создать миниатюру для '{name}': {e}")

                # Имя и кнопка
                ttk.Label(item_frame, text=name, font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=10)
                ttk.Button(item_frame, text="Выбрать", command=lambda n=name: self._select_and_close(n)).pack(side=tk.RIGHT, padx=10)

        except Exception as e:
            logging.error(f"Ошибка загрузки изображений для диалога выбора: {e}")
            messagebox.showerror("Ошибка", f"Не удалось загрузить список изображений: {e}", parent=self)

    def _select_and_close(self, name):
        self.selected_image_name = name
        self.destroy()

class PreviewWindow(tk.Toplevel):
    """Новое окно для предпросмотра и печати отдельных этикеток."""
    def __init__(self, parent, images: list, on_print_all_callback, on_print_current_callback):
        super().__init__(parent)
        self.parent = parent
        self.images = images
        self.current_index = 0
        self.on_print_all_callback = on_print_all_callback
        self.on_print_current_callback = on_print_current_callback
        self.title("Предпросмотр этикеток")
        self.geometry("600x500")
        self.transient(parent)
        self.grab_set()

        self._create_widgets()
        self._show_image(0)

    def _create_widgets(self):
        self.info_label = ttk.Label(self, text="", font=("Arial", 12))
        self.info_label.pack(pady=10)

        self.image_label = ttk.Label(self)
        self.image_label.pack(padx=10, pady=10, expand=True, fill="both")

        nav_frame = ttk.Frame(self)
        nav_frame.pack(pady=10)

        self.prev_button = ttk.Button(nav_frame, text="<< Назад", command=self._show_prev)
        self.prev_button.pack(side=tk.LEFT, padx=10)

        self.print_all_button = ttk.Button(nav_frame, text="Напечатать все", command=self._print_all)
        self.print_all_button.pack(side=tk.LEFT, padx=10)
        self.print_current_button = ttk.Button(nav_frame, text="Напечатать текущую", command=self._print_current)
        self.print_current_button.pack(side=tk.LEFT, padx=10)

        self.next_button = ttk.Button(nav_frame, text="Далее >>", command=self._show_next)
        self.next_button.pack(side=tk.LEFT, padx=10)

    def _show_image(self, index):
        self.current_index = index
        image = self.images[index]

        max_w, max_h = 500, 350
        img_w, img_h = image.size
        ratio = min(max_w / img_w, max_h / img_h)
        new_size = (int(img_w * ratio), int(img_h * ratio))
        
        resized_image = image.resize(new_size, Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(resized_image)

        self.image_label.config(image=photo)
        self.image_label.image = photo

        self.info_label.config(text=f"Этикетка {index + 1} из {len(self.images)}")
        self.prev_button.config(state="normal" if index > 0 else "disabled")
        self.next_button.config(state="normal" if index < len(self.images) - 1 else "disabled")

    def _show_next(self):
        if self.current_index < len(self.images) - 1: self._show_image(self.current_index + 1)
    def _show_prev(self):
        if self.current_index > 0: self._show_image(self.current_index - 1)

    def _print_all(self):
        """Вызывает callback для печати всех этикеток."""
        self.on_print_all_callback()
        self.destroy()

    def _print_current(self):
        """Вызывает callback для печати текущей этикетки."""
        self.on_print_current_callback(self.current_index)
        # Окно не закрываем, чтобы можно было напечатать другие страницы