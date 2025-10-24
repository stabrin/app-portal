import io
import logging
import json
import os
import tempfile

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape, portrait
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.lib.colors import black
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

# Configure logging for this module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [printing_service] - %(message)s')


class PrintingService:
    """
    Сервис для генерации и печати документов.
    """

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