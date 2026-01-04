import io
import logging
import json
import textwrap
from typing import Dict, Any, Optional
from psycopg2 import sql
import re
from psycopg2.extras import RealDictCursor # Явно импортируем RealDictCursor

from .db_connector import get_client_db_connection

import psycopg2

# Библиотеки для генерации штрихкодов и работы с Windows API
try:
    import qrcode
    # Импортируем только ядро Pillow, которое не зависит от Tkinter
    from PIL import Image, ImageDraw, ImageFont, ImageWin
except ImportError:
    logging.warning("QR code libraries (qrcode, Pillow Core) not installed.")
    qrcode = None
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageTk = None

try:
    from pylibdmtx.pylibdmtx import encode as dmtx_encode
except ImportError:
    logging.warning("Библиотека pylibdmtx не установлена. Установите: pip install pylibdmtx")
    dmtx_encode = None

try:
    import barcode
    from barcode.writer import ImageWriter
except ImportError:
    logging.warning("Библиотека python-barcode не установлена. Установите: pip install python-barcode")
    barcode = None


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

class PrintingService:
    """Сервис для генерации и печати документов."""

    @staticmethod
    def _ensure_images_table_exists(conn):
        """Проверяет и при необходимости создает таблицу для хранения изображений."""
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.ap_images')")
            if cur.fetchone()[0] is None:
                logging.warning("Таблица 'ap_images' не найдена. Создание таблицы...")
                cur.execute("""
                    CREATE TABLE ap_images (
                        name TEXT NOT NULL PRIMARY KEY,
                        image_data BYTEA,
                        uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                """)
                conn.commit()
                logging.info("Таблица 'ap_images' успешно создана.")


    @staticmethod
    def _fetch_data_from_db(user_info: Dict[str, Any], data_source: str) -> Optional[str]:
        """Получает данные из БД клиента по указанному источнику (table.field)."""
        logging.debug(f"Получение данных из БД для источника: {data_source}")
        parts = data_source.split('.')
        if len(parts) != 2:
            logging.warning(f"Некорректный формат data_source: '{data_source}'. Ожидается 'table.field'.")
            return None

        table_name, field_name = parts
        try:
            with get_client_db_connection(user_info) as conn, conn.cursor() as cur:
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

    @staticmethod
    def _get_multiline_fitting_font(draw: "ImageDraw.Draw", text: str, font_name: str, max_width: int, max_height: int) -> tuple["ImageFont.FreeTypeFont", str]:
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
    def _get_fitting_font(text: str, font_name: str, max_width: int, max_height: int) -> "ImageFont.FreeTypeFont":
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
    def _get_sscc_human_readable(sscc: str) -> str:
        """
        Форматирует SSCC для человекочитаемого представления.
        Пример: (00) 0 4604060 006532 5
        """
        # --- ИЗМЕНЕНИЕ: Обрабатываем как 18-значные, так и 20-значные коды ---
        if len(sscc) == 20 and sscc.startswith("00"):
            sscc_18 = sscc[2:] # Работаем с 18-значной частью
        elif len(sscc) == 18:
            sscc_18 = sscc
        else:
            return sscc # Возвращаем как есть, если длина неверная
        
        part1 = sscc_18[0]
        part2 = sscc_18[1:8]
        part3 = sscc_18[8:17]
        part4 = sscc_18[17]
        return f"(00) {part1} {part2} {part3} {part4}"

    @staticmethod
    def generate_label_image(template_json: Dict[str, Any], data: Dict[str, Any], user_info: Dict[str, Any], text_cache: Optional[Dict] = None, static_layers_cache: Optional[Dict] = None) -> Optional["Image.Image"]:
        """Генерирует изображение этикетки с помощью Pillow."""
        logging.info("Начало генерации изображения этикетки.")
        # --- ИСПРАВЛЕНИЕ: Если data - строка, оборачиваем в dict ---
        if isinstance(data, str):
            data = {'datamatrix': data}
        if not all([Image, ImageDraw, ImageFont]):
            logging.error("Pillow не установлен. Генерация изображения невозможна.")
            raise ImportError("Библиотека Pillow не установлена.")


        # --- НОВЫЙ БЛОК: Инициализация кэшей ---
        if text_cache is None: text_cache = {}
        if static_layers_cache is None: static_layers_cache = {}
        # --- КОНЕЦ НОВОГО БЛОКА ---

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
            static_layer_drawn = False


            for obj in template_json.get("objects", []):
                logging.info(f"Обработка объекта: тип='{obj.get('type')}', источник='{obj.get('data_source')}'")
                
                # Проверяем обязательные поля объекта
                required_fields = ["type", "x_mm", "y_mm", "width_mm", "height_mm", "data_source"]
                missing_fields = [f for f in required_fields if f not in obj]
                if missing_fields:
                    logging.warning(f"Пропуск объекта: отсутствуют поля {missing_fields}.")
                    continue


                # --- НОВАЯ ЛОГИКА: Определяем, является ли объект статичным ---
                is_static = False
                if obj.get("type") == "text" and obj.get("is_custom_text"):
                    is_static = True
                elif obj.get("type") == "text_with_image":
                    is_static = True # Текст произвольный, картинка выбирается в редакторе
                elif obj.get("type") == "image":
                    # Изображение статично, если его источник не является полем из БД
                    # (т.е. это просто имя файла, выбранное в редакторе)
                    is_static = '.' not in obj.get('data_source', '')


                if is_static:
                    # Если объект статичный, пытаемся взять его из кэша слоев
                    obj_json_str = json.dumps(obj, sort_keys=True)
                    if obj_json_str in static_layers_cache:
                        cached_layer = static_layers_cache[obj_json_str]
                        label_image.paste(cached_layer, (0, 0), cached_layer)
                        continue # Переходим к следующему объекту


                if obj.get("is_custom_text") or (obj.get("type") == "text_with_image"):
                    obj_data = obj.get("data_source") # Для произвольного текста данные хранятся прямо в шаблоне
                else:
                    # --- ИСПРАВЛЕНИЕ: Упрощенная логика получения данных ---
                    # 1. Сначала ищем данные по полному ключу (например, 'packages.sscc_code')
                    obj_data = data.get(obj["data_source"])
                    # 2. Если не нашли, ищем по короткому ключу (например, 'sscc_code')
                    if obj_data is None:
                        short_key = obj["data_source"].split('.')[-1]
                        obj_data = data.get(short_key)
                    # 3. И только если ничего не нашли, идем в БД
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


                # --- НОВАЯ ЛОГИКА: Рисуем на временном слое, если объект статичный ---
                target_draw = draw
                temp_layer = None
                if is_static:
                    temp_layer = Image.new('RGBA', (width_px, height_px), (0,0,0,0))
                    target_draw = ImageDraw.Draw(temp_layer)


                if obj["type"] == "text":
                    logging.debug("Обработка как 'text'")
                    # --- НОВАЯ ЛОГИКА: Используем кэш для произвольного текста ---
                    if obj.get("is_custom_text"):
                        cache_key = obj['data_source'] # Ключ - сам текст
                        if cache_key not in text_cache:
                            # Если в кэше нет, рассчитываем и сохраняем
                            logging.debug(f"Кэширование произвольного текста: '{cache_key[:30]}...'")
                            if obj.get("single_line"):
                                font = PrintingService._get_fitting_font(str(obj_data), obj.get("font_name", "arial"), width, height)
                                wrapped_text = str(obj_data)  # Не переносим
                            else:
                                font, wrapped_text = PrintingService._get_multiline_fitting_font(draw, str(obj_data), obj.get("font_name", "arial"), width, height)
                            text_cache[cache_key] = (font, wrapped_text)
                        else:
                            # Если в кэше есть, берем готовый результат
                            logging.debug("Использование кэшированного произвольного текста.")
                            font, wrapped_text = text_cache[cache_key]
                        target_draw.text((x, y), wrapped_text, fill="black", font=font, anchor="la")
                    else:
                        # Старая логика для текста из БД
                        cache_key = (str(obj_data), width, height)
                        if cache_key not in text_cache:
                            logging.debug(f"Кэширование текста из БД: '{str(obj_data)[:30]}...'")
                            font, wrapped_text = PrintingService._get_multiline_fitting_font(draw, str(obj_data), obj.get("font_name", "arial"), width, height)
                            text_cache[cache_key] = (font, wrapped_text)
                        else:
                            logging.debug("Использование кэшированного текста из БД.")
                            font, wrapped_text = text_cache[cache_key]
                        draw.text((x, y), wrapped_text, fill="black", font=font, anchor="la")


                # --- НОВЫЙ БЛОК: Обработка композитного объекта "Текст с изображением" ---
                elif obj["type"] == "text_with_image":
                    logging.debug("Обработка как 'text_with_image'")
                    
                    # 1. Получаем данные для текста и изображения
                    text_data_source = obj.get("data_source")
                    image_data_source = obj.get("image_source")

                    
                    # Текст всегда произвольный для этого объекта
                    text_content = text_data_source
                    image_name = data.get(image_data_source, f"<{image_data_source}>")

                    # 2. Определяем геометрию
                    # Предположим, что 30% ширины объекта отводится под изображение

                    image_area_width = int(width * 0.3)
                    text_area_width = width - image_area_width
                    
                    # Позиция и размер области для изображения (слева)
                    img_x, img_y = x, y
                    img_w, img_h = image_area_width, height

                    # Позиция и размер области для текста (справа)
                    text_x = x + image_area_width
                    text_y = y
                    text_w = text_area_width
                    text_h = height

                    # 3. Рендерим изображение (логика взята из объекта "image")
                    try:
                        with get_client_db_connection(user_info) as conn:

                            PrintingService._ensure_images_table_exists(conn)
                            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                                cur.execute("SELECT image_data FROM ap_images WHERE name = %s", (image_name,))
                                result = cur.fetchone()
                        if result:
                            img_obj = Image.open(io.BytesIO(result[0])).convert("RGBA")
                            img_obj.thumbnail((img_w, img_h), Image.Resampling.LANCZOS)
                            
                            # Центрируем изображение по вертикали в его области
                            paste_y = img_y + (img_h - img_obj.height) // 2
                            target_draw.paste(img_obj, (img_x, paste_y), img_obj)
                        else:
                            logging.warning(f"Изображение '{image_name}' не найдено для объекта text_with_image.")
                    except Exception as e:
                        logging.error(f"Ошибка при отрисовке встроенного изображения '{image_name}': {e}")


                    # 4. Рендерим текст (логика взята из объекта "text")
                    # Используем _get_multiline_fitting_font для области текста
                    font, wrapped_text = PrintingService._get_multiline_fitting_font(
                        draw, str(text_content), obj.get("font_name", "arial"), text_w, text_h
                    )
                    # Рисуем текст в его области
                    target_draw.text((text_x, text_y), wrapped_text, fill="black", font=font, anchor="la")


                elif obj["type"] == "image":
                    logging.debug("Обработка как 'image'")
                    image_name = str(obj_data)
                    try:
                        # Пытаемся получить изображение из БД
                        with get_client_db_connection(user_info) as conn:
                            PrintingService._ensure_images_table_exists(conn)
                            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                                cur.execute("SELECT image_data FROM ap_images WHERE name = %s", (image_name,))
                                result = cur.fetchone()
                        if result:
                            image_bytes = result['image_data']
                            img_obj = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
                            # --- УЛУЧШЕНИЕ: Используем thumbnail для сохранения пропорций ---
                            # Это предотвратит искажение изображения, если его пропорции не совпадают с объектом.
                            img_obj.thumbnail((width, height), Image.Resampling.LANCZOS)
                            target_draw.paste(img_obj, (x, y), img_obj if img_obj.mode == 'RGBA' else None)
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
                            logging.error(f"Ошибка генерации DataMatrix для данных '{data_str}': {e}", exc_info=True)
                            continue
                    elif barcode_type in ["CODE128", "SSCC"]:
                        if not barcode:
                            logging.warning("Библиотека python-barcode не установлена. Пропуск Code128.")
                            continue
                        try:
                            Code128 = barcode.get_barcode_class('code128')
                            code128_barcode = Code128(str(obj_data), writer=ImageWriter())
                            # --- ИЗМЕНЕНИЕ: Отключаем стандартный текст под ШК ---
                            options = {
                                'module_height': 10.0, 
                                'module_width': 0.25, 
                                'write_text': False, # Не рисуем текст библиотекой
                                'quiet_zone': 2.0
                            }
                            pil_image = code128_barcode.render(writer_options=options)
                            
                            # --- НОВЫЙ БЛОК: Рисуем свой текст под ШК ---
                            human_readable_text = PrintingService._get_sscc_human_readable(str(obj_data))
                            try:
                                text_font = ImageFont.truetype("arialbd.ttf", size=32) # Полужирный Arial
                            except IOError:
                                text_font = ImageFont.load_default()

                            # Создаем новый холст, чуть выше, чтобы вместить текст
                            new_height = pil_image.height + 40 # Добавляем 40 пикселей для текста
                            final_barcode_image = Image.new('RGB', (pil_image.width, new_height), 'white')
                            final_barcode_image.paste(pil_image, (0, 0))
                            barcode_draw = ImageDraw.Draw(final_barcode_image)
                            barcode_draw.text((pil_image.width / 2, pil_image.height + 5), human_readable_text, font=text_font, fill="black", anchor="mt")
                            pil_image = final_barcode_image # Заменяем исходное изображение на новое с текстом
                            # --- КОНЕЦ НОВОГО БЛОКА ---
                            pil_image = pil_image.resize((width, height), Image.Resampling.LANCZOS) # Масштабируем до нужных размеров
                            label_image.paste(pil_image, (x, y))
                        except Exception as e:
                            logging.error(f"Ошибка генерации Code128: {e}", exc_info=True)
                            continue
                    else:
                        logging.warning(f"Тип штрихкода '{barcode_type}' не поддерживается.")
                        draw.rectangle([x, y, x + width, y + height], outline="red", fill="white")
                        draw.text((x + 5, y + 5), f"Unsupported:\n{barcode_type}", fill="red")


                # --- НОВАЯ ЛОГИКА: Сохраняем статичный слой в кэш и накладываем его ---
                if is_static and temp_layer:
                    obj_json_str = json.dumps(obj, sort_keys=True)
                    static_layers_cache[obj_json_str] = temp_layer
                    label_image.paste(temp_layer, (0, 0), temp_layer)
            
            logging.info("Изображение этикетки успешно сгенерировано.")

            return label_image.convert('1') # Принудительно возвращаем Ч/Б изображение
        
        except Exception as e:
            logging.error(f"Ошибка генерации изображения этикетки: {e}")
            raise

    @staticmethod
    def print_label_direct(printer_name: str, paper_name: Optional[str], template_json: Dict[str, Any], data: Dict[str, Any], user_info: Dict[str, Any], pregenerated_image: Optional[Image.Image] = None) -> None:
        """Отправляет этикетку на принтер напрямую через GDI."""
        logging.info(f"Прямая печать на принтер '{printer_name}'.")
        if not all([win32print, win32ui]):
            logging.error("pywin32 не установлен. Прямая печать невозможна.")
            raise ImportError("Библиотека pywin32 не установлена.")
    
        label_image = None
        h_printer = None
        try:
            # --- ИСПРАВЛЕНИЕ: Используем готовое изображение, если оно передано ---
            if pregenerated_image:
                label_image = pregenerated_image
            else:
                label_image = PrintingService.generate_label_image(template_json, data, user_info)

            if not label_image:
                logging.error("Не удалось сгенерировать изображение этикетки. Печать отменена.")
                return

            # --- НОВАЯ ЛОГИКА: Если имя бумаги не передано, пытаемся его сформировать из размеров макета ---
            if not paper_name and template_json:
                width_mm = template_json.get('width_mm')
                height_mm = template_json.get('height_mm')
                if width_mm and height_mm:
                    paper_name = f"Tilda_{int(width_mm)}x{int(height_mm)}"
                    logging.info(f"Имя бумаги не было передано. Сформировано автоматически: '{paper_name}'")

            # --- Открываем принтер и получаем его характеристики ---
            # --- ИЗМЕНЕНИЕ: Устанавливаем нужный размер бумаги перед печатью ---
            h_printer = win32print.OpenPrinter(printer_name)
            try:
                # Получаем текущие настройки принтера
                printer_defaults = win32print.GetPrinter(h_printer, 2)
                devmode = printer_defaults['pDevMode']
                
                # --- ИЗМЕНЕНИЕ: Устанавливаем размер бумаги, только если он задан ---
                if paper_name:
                    # Устанавливаем имя формы (размер бумаги)
                    devmode.FormName = paper_name
                    # Указываем, что мы изменили FormName
                    devmode.Fields = devmode.Fields | win32con.DM_FORMNAME
                    logging.info(f"Установка размера бумаги (FormName) на '{paper_name}'.")
            except Exception as e_devmode:
                # Не прерываем печать, а просто логируем предупреждение
                logging.warning(f"Не удалось установить размер бумаги '{paper_name}': {e_devmode}. Печать будет выполнена с настройками по умолчанию.")

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
    def print_generated_images(printer_name: str, paper_name: str, images: list, user_info: Dict[str, Any]) -> None:
        """Печатает список уже сгенерированных изображений Pillow."""
        logging.info(f"Начало пакетной печати {len(images)} готовых изображений на принтер '{printer_name}'.")
        if not images:
            logging.warning("Список изображений для печати пуст.")
            return
    
        for i, image in enumerate(images, 1):
            logging.info(f"Печать изображения {i}/{len(images)}")
            try:
                # Создаем "пустые" данные, так как изображение уже готово
                PrintingService.print_label_direct(printer_name, paper_name, {}, {}, user_info, pregenerated_image=image)
            except Exception as e:
                logging.error(f"Ошибка печати изображения {i}: {e}")
                raise RuntimeError(f"Ошибка печати изображения {i}/{len(images)}: {e}")

    @staticmethod
    def print_labels_for_items(printer_name: str, paper_name: str, template_json: Dict[str, Any], items_data: list, user_info: Dict[str, Any]) -> None:
        """Печатает этикетки для списка элементов."""
        # --- ИЗМЕНЕНИЕ: Этот метод теперь генерирует изображения и передает их в новый метод печати ---
        # --- НОВЫЙ БЛОК: Создаем кэши для этой пачки ---
        images_to_print = []
        text_cache, static_layers_cache = {}, {}
        for i, item_data in enumerate(items_data, 1):
            img = PrintingService.generate_label_image(template_json, item_data, user_info, text_cache, static_layers_cache)
            if img:
                images_to_print.append(img)
        
        if images_to_print:

            PrintingService.print_generated_images(printer_name, paper_name, images_to_print, user_info)