import io
import logging
import json
import os
import tempfile
from typing import Dict, Any

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape, portrait
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.lib.colors import black
    # Для штрихкодов
    from reportlab.graphics.barcode import qr, code128, datamatrix
    from reportlab.graphics.shapes import Drawing
except ImportError:
    logging.warning("ReportLab not installed. PDF generation features will be limited. Install with: pip install reportlab")
    canvas = None # Mark as unavailable

try:
    import qrcode
    from PIL import Image
except ImportError:
    logging.warning("QR code generation libraries (qrcode, Pillow) not installed. QR code features will be limited. Install with: pip install qrcode Pillow")
    qrcode = None
    Image = None

try:
    import win32print
    import win32api
    import win32con
    from pywintypes import error as pywin_error
except ImportError:
    logging.warning("pywin32 not installed. Windows printing features will be limited. Install with: pip install pywin32")
    win32print = None
    win32api = None
    win32con = None
    pywin_error = None

# Добавляем импорты для редактора
try:
    import tkinter as tk
    from tkinter import ttk, simpledialog, messagebox
except ImportError:
    tk = None # Помечаем как недоступный, если среда без GUI

# Configure logging for this module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [printing_service] - %(message)s')


class PrintingService:
    """
    Сервис для генерации и печати документов.
    """

    @staticmethod
    def generate_pdf_from_template(template_json: Dict[str, Any], data: Dict[str, Any]) -> io.BytesIO:
        """
        Генерирует PDF на основе JSON-шаблона и словаря с данными.

        :param template_json: Словарь, представляющий JSON-структуру макета.
        :param data: Словарь с данными для подстановки, например:
                     {'packages.sscc_code': '12345...', 'orders.client_name': 'Client A'}
        """
        if canvas is None:
            raise ImportError("Библиотека ReportLab не установлена.")

        buffer = io.BytesIO()
        
        label_width = template_json.get("width_mm", 100) * mm
        label_height = template_json.get("height_mm", 50) * mm

        c = canvas.Canvas(buffer, pagesize=(label_width, label_height))
        c.setFillColor(black)

        for obj in template_json.get("objects", []):
            try:
                # Получаем данные из словаря по ключу из data_source
                obj_data = data.get(obj["data_source"])
                if obj_data is None:
                    logging.warning(f"Источник данных '{obj['data_source']}' не найден в предоставленных данных. Пропуск объекта.")
                    continue

                x = obj["x_mm"] * mm
                y = obj["y_mm"] * mm
                width = obj["width_mm"] * mm
                height = obj["height_mm"] * mm

                if obj["type"] == "text":
                    c.setFont(obj.get("font_name", "Helvetica"), obj.get("font_size", 10))
                    c.drawString(x, y, str(obj_data))

                elif obj["type"] == "image":
                    # Предполагаем, что в obj_data находятся байты картинки
                    if isinstance(obj_data, bytes):
                        image_stream = io.BytesIO(obj_data)
                        reportlab_img = ImageReader(image_stream)
                        c.drawImage(reportlab_img, x, y, width=width, height=height, preserveAspectRatio=True)
                    else:
                        logging.warning(f"Источник данных для картинки '{obj['data_source']}' не является байтами.")

                elif obj["type"] == "barcode":
                    barcode_type = obj.get("barcode_type", "QR").upper()
                    barcode_drawing = None

                    if barcode_type == "QR":
                        barcode = qr.QrCodeWidget(str(obj_data))
                        bounds = barcode.getBounds()
                        barcode_width = bounds[2] - bounds[0]
                        barcode_height = bounds[3] - bounds[1]
                        
                        # Масштабируем виджет под заданные размеры
                        scale_x = width / barcode_width if barcode_width else 1
                        scale_y = height / barcode_height if barcode_height else 1
                        transform = [min(scale_x, scale_y), 0, 0, min(scale_x, scale_y), x, y]
                        
                        barcode_drawing = Drawing(width, height, transform=transform)
                        barcode_drawing.add(barcode)

                    elif barcode_type == "SSCC": # SSCC обычно кодируют в Code128
                        barcode = code128.Code128(str(obj_data), barHeight=height, barWidth=width / 100) # barWidth - примерный расчет
                        barcode_drawing = Drawing(width, height)
                        barcode_drawing.add(barcode)
                        # Позиционирование для Code128 может требовать подстройки
                        barcode_drawing.translate(x, y)

                    elif barcode_type == "DATAMATRIX":
                        barcode = datamatrix.DataMatrix(str(obj_data))
                        # Логика масштабирования аналогична QR
                        bounds = barcode.getBounds()
                        barcode_width = bounds[2] - bounds[0]
                        barcode_height = bounds[3] - bounds[1]
                        scale = min(width / barcode_width, height / barcode_height)
                        transform = [scale, 0, 0, scale, x, y]
                        barcode_drawing = Drawing(width, height, transform=transform)
                        barcode_drawing.add(barcode)

                    if barcode_drawing:
                        barcode_drawing.drawOn(c, 0, 0) # Рисуем на холсте

            except Exception as e:
                logging.error(f"Ошибка при рендеринге объекта {obj.get('type')}: {e}")

        c.showPage()
        c.save()
        buffer.seek(0)
        return buffer


    @staticmethod
    def generate_workplace_label_pdf(workplace_name: str, token: str) -> io.BytesIO:
        """
        Генерирует PDF-файл с этикеткой для рабочего места, содержащей QR-код с токеном.
        """
        if canvas is None or qrcode is None or Image is None:
            logging.error("Невозможно сгенерировать PDF: отсутствуют необходимые библиотеки (ReportLab, qrcode, Pillow).")
            # Return an empty buffer or raise an error, depending on desired behavior
            # For now, return a buffer with an error message
            error_buffer = io.BytesIO()
            if canvas: # If canvas is available, write a proper PDF error
                c = canvas.Canvas(error_buffer, pagesize=A4)
                c.drawString(10, 750, "Error: PDF generation libraries missing.")
                c.save()
            else: # Otherwise, just a raw byte message
                error_buffer.write(b"Error: PDF generation libraries missing.")
            error_buffer.seek(0)
            return error_buffer

        buffer = io.BytesIO()
        
        # Define a standard label size, e.g., 100x50 mm (width x height)
        # You might want to make this configurable or pass it as an argument
        label_width = 100 * mm
        label_height = 50 * mm
        
        c = canvas.Canvas(buffer, pagesize=(label_width, label_height))
        c.setFillColor(black) # Ensure text is black

        # QR Code generation
        qr_payload = json.dumps({"type": "workplace_token", "token": token})
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=4, # Adjust box_size for desired QR code density/size
            border=2,   # Border around QR code
        )
        qr.add_data(qr_payload)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")

        # Convert PIL Image to ReportLab ImageReader
        img_buffer = io.BytesIO()
        qr_img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        reportlab_qr_img = ImageReader(img_buffer)

        # Calculate QR code size and position
        qr_display_size = 40 * mm # Let's make QR code 40x40mm on the label
        qr_x = 5 * mm
        qr_y = (label_height - qr_display_size) / 2 # Center vertically

        c.drawImage(reportlab_qr_img, qr_x, qr_y, width=qr_display_size, height=qr_display_size)

        # Text
        text_start_x = qr_x + qr_display_size + 5 * mm # 5mm gap after QR
        
        # Adjust text positioning based on label height and content
        current_y = label_height - 10 * mm # Start 10mm from top

        c.setFont('Helvetica-Bold', 10)
        c.drawString(text_start_x, current_y, "Рабочее место:")
        current_y -= 5 * mm
        c.setFont('Helvetica', 10)
        c.drawString(text_start_x, current_y, workplace_name)

        current_y -= 10 * mm # Gap
        c.setFont('Helvetica-Bold', 8)
        c.drawString(text_start_x, current_y, "Токен доступа:")
        current_y -= 5 * mm
        c.setFont('Helvetica', 8)
        # Token might be long, consider wrapping or smaller font if needed
        c.drawString(text_start_x, current_y, token)

        c.showPage()
        c.save()
        buffer.seek(0)
        return buffer

    @staticmethod
    def print_pdf(printer_name: str, pdf_buffer: io.BytesIO, paper_name: str = None):
        """
        Отправляет PDF-файл на указанный принтер с использованием системного диалога печати.
        Это более надежный способ, чем "RAW" печать, так как использует установленный PDF-просмотрщик.
        
        :param printer_name: Имя принтера. Если None или пустая строка, будет использован принтер по умолчанию.
        :param pdf_buffer: io.BytesIO объект, содержащий данные PDF.
        :param paper_name: (Не используется напрямую в этой реализации, но может быть передан
                           для более сложных сценариев с DEVMODE).
        """
        if win32api is None:
            logging.error("Невозможно выполнить печать: отсутствуют библиотеки pywin32.")
            raise RuntimeError("pywin32 libraries are not installed.")

        temp_pdf_path = None
        try:
            # Save the PDF buffer to a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", mode="wb") as temp_file:
                temp_file.write(pdf_buffer.getvalue())
                temp_pdf_path = temp_file.name
            
            logging.info(f"Временный PDF файл создан: {temp_pdf_path}")

            # Use ShellExecute to print the PDF
            # verb "printto" allows specifying a printer
            # Parameters: hwnd, lpOperation, lpFile, lpParameters, lpDirectory, nShowCmd
            # lpParameters for "printto" is the printer name
            
            # win32api.ShellExecute returns a handle if successful, or an error code <= 32
            # 0 = The operating system is out of memory or resources.
            # 2 = The specified file was not found.
            # 3 = The specified path was not found.
            # ... (various error codes) ...
            # 31 = No association for file extension.
            
            # If printer_name is None or empty, ShellExecute will attempt to use the default printer.
            # For the "printto" verb, it's generally best to provide a printer name.
            # If not provided, try to get the default.
            if not printer_name:
                try:
                    printer_name = win32print.GetDefaultPrinter()
                except Exception:
                    logging.warning("Не удалось получить принтер по умолчанию. Попытка печати без указания принтера.")
                    printer_name = "" # Let ShellExecute decide
                
            # ShellExecute(hwnd, lpOperation, lpFile, lpParameters, lpDirectory, nShowCmd)
            # hwnd: 0 (no parent window)
            # lpOperation: "printto"
            # lpFile: path to PDF file
            # lpParameters: printer name (can be empty for default, but "printto" usually expects it)
            # lpDirectory: ""
            # nShowCmd: win32con.SW_HIDE (0) to try silent printing
            
            result = win32api.ShellExecute(
                0,
                "printto",
                temp_pdf_path,
                printer_name,
                "",
                win32con.SW_HIDE
            )

            if result <= 32: # Error codes are <= 32
                error_message = f"ShellExecute failed with error code: {result}. "
                if result == 31:
                    error_message += "No application is associated with the .pdf file extension, or the associated application does not support the 'printto' verb."
                elif result == 8:
                    error_message += "Invalid parameter (e.g., printer name might be invalid)."
                logging.error(f"Ошибка при отправке PDF на печать через ShellExecute: {error_message}")
                raise RuntimeError(f"Ошибка печати: {error_message}")
            
            logging.info(f"PDF успешно отправлен на принтер '{printer_name}' через ShellExecute.")
            return True
        except Exception as e:
            logging.error(f"Неизвестная ошибка печати: {e}")
            raise RuntimeError(f"Неизвестная ошибка печати: {e}")
        finally:
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                try:
                    os.remove(temp_pdf_path)
                    logging.info(f"Временный PDF файл удален: {temp_pdf_path}")
                except Exception as e:
                    logging.warning(f"Не удалось удалить временный PDF файл {temp_pdf_path}: {e}")


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

        self._create_widgets()

    def _create_widgets(self):
        # Основной разделенный фрейм
        paned_window = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True)

        # --- Левая панель (инструменты) ---
        controls_frame = ttk.Frame(paned_window, width=300, padding="10")
        paned_window.add(controls_frame, weight=1)

        # Кнопка создания нового макета
        ttk.Button(controls_frame, text="Создать новый макет", command=self._prompt_for_new_layout).pack(fill=tk.X, pady=5)
        ttk.Separator(controls_frame).pack(fill=tk.X, pady=10)

        # Фрейм для инструментов (изначально неактивен)
        self.tools_frame = ttk.LabelFrame(controls_frame, text="Инструменты")
        self.tools_frame.pack(fill=tk.X, pady=5)

        # Кнопки для добавления объектов
        ttk.Button(self.tools_frame, text="Добавить QR-код", command=lambda: self._add_object_to_canvas("QR")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить SSCC", command=lambda: self._add_object_to_canvas("SSCC")).pack(fill=tk.X, pady=2)
        ttk.Button(self.tools_frame, text="Добавить DataMatrix", command=lambda: self._add_object_to_canvas("DataMatrix")).pack(fill=tk.X, pady=2)

        # --- Новая панель свойств ---
        self.properties_frame = ttk.LabelFrame(controls_frame, text="Свойства объекта")
        self.properties_frame.pack(fill=tk.X, pady=10)

        self.prop_entries = {}
        prop_fields = {
            "x_mm": "X (мм):",
            "y_mm": "Y (мм):",
            "width_mm": "Ширина (мм):",
            "height_mm": "Высота (мм):"
        }

        for key, text in prop_fields.items():
            frame = ttk.Frame(self.properties_frame)
            frame.pack(fill=tk.X, padx=5, pady=2)
            ttk.Label(frame, text=text, width=12).pack(side=tk.LEFT)
            entry = ttk.Entry(frame)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.prop_entries[key] = entry

        self.apply_props_button = ttk.Button(self.properties_frame, text="Применить", command=self._apply_properties)
        self.apply_props_button.pack(pady=5)

        # Изначально деактивируем свойства
        for widget in self.properties_frame.winfo_children():
            widget.configure(state="disabled")

        # Изначально деактивируем инструменты
        for widget in self.tools_frame.winfo_children():
            widget.configure(state="disabled")

        # --- Правая панель (холст) ---
        canvas_frame = ttk.Frame(paned_window)
        paned_window.add(canvas_frame, weight=4)

        self.canvas = tk.Canvas(canvas_frame, bg="lightgrey")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Привязываем событие клика к холсту
        self.canvas.bind("<Button-1>", self._on_canvas_click)

    def _prompt_for_new_layout(self):
        """Запрашивает у пользователя размеры нового макета."""
        size_str = simpledialog.askstring("Новый макет", "Введите размеры этикетки (Ширина x Высота) в мм:", parent=self)
        if not size_str:
            return

        try:
            width_str, height_str = size_str.lower().split('x')
            width_mm = int(width_str.strip())
            height_mm = int(height_str.strip())
        except (ValueError, IndexError):
            messagebox.showerror("Ошибка", "Неверный формат. Введите размеры в формате '100 x 50'.", parent=self)
            return

        # Создаем базовую структуру шаблона
        self.template = {
            "template_name": "Новый макет",
            "width_mm": width_mm,
            "height_mm": height_mm,
            "objects": []
        }
        self.selected_object_id = None
        self.canvas_objects.clear()

        self._draw_canvas_background()
        # Активируем инструменты
        for widget in self.tools_frame.winfo_children():
            widget.configure(state="normal")
        # Деактивируем свойства, пока ничего не выбрано
        self._toggle_properties_panel(False)

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

    def _add_object_to_canvas(self, barcode_type: str):
        """Добавляет новый объект (пока только штрихкод) на холст и в шаблон."""
        if not self.template:
            messagebox.showwarning("Внимание", "Сначала создайте новый макет.", parent=self)
            return

        # Создаем объект с параметрами по умолчанию
        new_object = {
            "type": "barcode",
            "barcode_type": barcode_type,
            "data_source": "placeholder.data", # Заглушка
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
        logging.info(f"Добавлен новый объект: {barcode_type}. Текущий шаблон: {json.dumps(self.template, indent=2)}")

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
        self.canvas.create_rectangle(x_px, y_px, x_px + width_px, y_px + height_px, fill="lightblue", outline=outline_color, width=2, tags=(canvas_tag, "object"))
        # Добавляем текст
        self.canvas.create_text(x_px + width_px / 2, y_px + height_px / 2, text=obj_data['barcode_type'], tags=(canvas_tag, "object_text"))

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

    def _toggle_properties_panel(self, active: bool):
        """Включает или выключает панель свойств."""
        state = "normal" if active else "disabled"
        for widget in self.properties_frame.winfo_children():
            # ttk.Entry и ttk.Button не имеют метода configure для state в некоторых случаях
            try:
                widget.config(state=state)
            except tk.TclError:
                if isinstance(widget, (ttk.Entry, ttk.Button)):
                    widget.state([state] if state == "normal" else [state])
                elif isinstance(widget, ttk.Frame): # Рекурсивно для вложенных фреймов
                    for sub_widget in widget.winfo_children():
                         try: sub_widget.config(state=state)
                         except tk.TclError: pass

    def _update_properties_panel(self):
        """Заполняет панель свойств данными выделенного объекта."""
        if self.selected_object_id is None:
            return

        obj_data = self.template['objects'][self.selected_object_id]
        for key, entry in self.prop_entries.items():
            entry.delete(0, tk.END)
            entry.insert(0, str(obj_data.get(key, '')))

    def _apply_properties(self):
        """Применяет изменения из панели свойств к объекту."""
        if self.selected_object_id is None:
            return

        try:
            for key, entry in self.prop_entries.items():
                value = float(entry.get())
                self.template['objects'][self.selected_object_id][key] = value
            
            self._draw_canvas_background() # Перерисовываем холст с новыми данными
            logging.info(f"Свойства объекта {self.selected_object_id} обновлены.")
        except ValueError:
            messagebox.showerror("Ошибка", "Значения свойств должны быть числами.", parent=self)