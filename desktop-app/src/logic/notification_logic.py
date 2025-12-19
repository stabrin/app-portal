
import logging
import os
from PySide6.QtWidgets import QMessageBox, QFileDialog
from ..supply_notification_service import SupplyNotificationService

class NotificationLogic:
    def __init__(self, user_info, main_app_window):
        self.user_info = user_info
        self.main_app_window = main_app_window
        self.service = SupplyNotificationService(lambda: get_client_db_connection(self.user_info))

    def create_new_notification(self):
        """Создает новое уведомление о поставке."""
        dialog = NotificationEditorDialog(self.main_app_window, self.user_info)
        if dialog.exec():
            self.main_app_window.load_notifications()

    def delete_notification(self, notification_id):
        """Удаляет выбранное уведомление."""
        reply = QMessageBox.question(self.main_app_window, "Подтверждение", f"Удалить уведомление #{notification_id}?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.service.delete_notification(notification_id)
            QMessageBox.information(self.main_app_window, "Успех", "Уведомление удалено")
            self.main_app_window.load_notifications()
        except Exception as e:
            logging.exception("Error deleting notification")
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось удалить уведомление: {e}")

    def save_notification_changes(self, notification_id, data_to_save):
        """Сохраняет изменения уведомления."""
        try:
            self.service.update_notification(notification_id, data_to_save)
            QMessageBox.information(self.main_app_window, "Успех", "Изменения сохранены")
            self.main_app_window.load_notifications()
        except Exception as e:
            logging.exception("Error saving notification changes")
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось сохранить изменения: {e}")

    def create_order_from_notification(self, notification_id):
        """Создаёт заказ из уведомления."""
        try:
            success, message, needs_confirmation = self.service.create_or_recreate_order_from_notification(notification_id)
            
            if needs_confirmation:
                reply = QMessageBox.question(self.main_app_window, "Подтверждение", message, QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    success, message, _ = self.service.create_or_recreate_order_from_notification(notification_id, force_recreate=True)
                else:
                    return
            
            if success:
                QMessageBox.information(self.main_app_window, "Успех", message)
                self.main_app_window.load_notifications()
            else:
                QMessageBox.warning(self.main_app_window, "Внимание", message)
        except Exception as e:
            logging.exception("Error creating order from notification")
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось создать заказ: {e}")

    def upload_notification_doc(self, notification_id):
        """Загружает документ для уведомления."""
        filepath, _ = QFileDialog.getOpenFileName(self.main_app_window, "Выберите файл")
        if not filepath:
            return
        
        try:
            with open(filepath, 'rb') as f:
                file_data = f.read()
            
            filename = os.path.basename(filepath)
            self.service.add_notification_file(notification_id, filename, file_data, 'client_document')
            QMessageBox.information(self.main_app_window, "Успех", "Файл успешно загружен")
            return True
        except Exception as e:
            logging.exception("Error uploading notification document")
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось загрузить файл: {e}")
            return False

    def download_notification_doc(self, file_info):
        """Скачивает документ от уведомления."""
        try:
            content, filename = self.service.get_file_content(file_info['id'])
            
            save_path, _ = QFileDialog.getSaveFileName(self.main_app_window, "Сохранить файл", filename)
            if save_path:
                with open(save_path, 'wb') as f:
                    f.write(content)
                QMessageBox.information(self.main_app_window, "Успех", f"Файл сохранен в: {save_path}")
        except Exception as e:
            logging.exception("Error downloading notification document")
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось скачать файл: {e}")

    def delete_notification_doc(self, file_info):
        """Удаляет выбранный документ уведомления."""
        file_id = file_info['id']
        filename = file_info['filename']

        reply = QMessageBox.question(self.main_app_window, "Подтверждение", f"Вы уверены, что хотите удалить файл '{filename}'?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return False

        try:
            self.service.delete_notification_file(file_id)
            QMessageBox.information(self.main_app_window, "Успех", "Файл успешно удален.")
            return True
        except Exception as e:
            logging.exception("Error deleting notification document")
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось удалить файл: {e}")
            return False

    def download_order_template(self):
        """Скачивает шаблон для детализации заказа."""
        try:
            df = self.service.get_formalization_template()
            save_path, _ = QFileDialog.getSaveFileName(self.main_app_window, "Сохранить шаблон", "template_details.xlsx",
                                                        "Excel Files (*.xlsx)")
            if save_path:
                df.to_excel(save_path, index=False)
                QMessageBox.information(self.main_app_window, "Успех", f"Шаблон успешно сохранен в: {save_path}")
        except Exception as e:
            logging.exception("Error downloading order template")
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось скачать шаблон: {e}")

    def upload_order_details(self, notification_id):
        """Загружает детализацию заказа из Excel-файла."""
        reply = QMessageBox.question(self.main_app_window, "Подтверждение",
                                     "Загрузка из файла полностью заменит текущую детализацию. Продолжить?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return False

        filepath, _ = QFileDialog.getOpenFileName(self.main_app_window, "Выберите Excel-файл", "",
                                                    "Excel Files (*.xlsx *.xls)")
        if not filepath:
            return False

        try:
            with open(filepath, 'rb') as f:
                file_data = f.read()
            rows_processed = self.service.process_formalized_file(notification_id, file_data)
            QMessageBox.information(self.main_app_window, "Успех", f"Файл успешно обработан. Загружено {rows_processed} строк.")
            return True
        except Exception as e:
            logging.exception("Error uploading order details")
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось обработать файл: {e}")
            return False

    def save_order_details(self, details_to_save):
        """Сохраняет детализацию заказа."""
        try:
            self.service.save_notification_details(details_to_save)
            QMessageBox.information(self.main_app_window, "Успех", "Изменения в детализации успешно сохранены.")
        except Exception as e:
            logging.exception("Error saving order details")
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось сохранить детализацию: {e}")
