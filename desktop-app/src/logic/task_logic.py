
import logging
import json
from PySide6.QtWidgets import QMessageBox
from ..task_service import TaskService
from ..admin_ui_qt import EmployeePassesViewerDialog

class TaskLogic:
    def __init__(self, task_service, main_app_window):
        self.task_service = task_service
        self.main_app_window = main_app_window

    def load_task_details(self, task_data, marking_settings_group, aggregation_type_combo, employee_count_spinbox, nesting_level_spinbox, sscc_source_combo, refine_prod_date_checkbox, refine_batch_checkbox, refine_country_checkbox, settings_json_edit, btn_take_in_work, btn_complete):
        """Загружает детали задачи в виджеты."""
        settings_json = task_data.get('settings_json', {})
        if isinstance(settings_json, str):
            try:
                settings_json = json.loads(settings_json)
            except json.JSONDecodeError:
                settings_json = {}
        
        settings_json_edit.setText(json.dumps(settings_json, indent=4, ensure_ascii=False))

        if marking_settings_group:
            aggregation_type_combo.setCurrentText(settings_json.get('aggregation_type', 'Без агрегации'))
            employee_count_spinbox.setValue(settings_json.get('employee_count', 3))
            nesting_level_spinbox.setValue(settings_json.get('nesting_level', 1))
            sscc_source_combo.setCurrentText(settings_json.get('sscc_source', 'Генерируем сами'))
            refine_prod_date_checkbox.setChecked(settings_json.get('refine_prod_date', False))
            refine_batch_checkbox.setChecked(settings_json.get('refine_batch', False))
            refine_country_checkbox.setChecked(settings_json.get('refine_country', False))
        
        status = task_data.get('status')
        btn_take_in_work.setEnabled(status == 'new')
        btn_complete.setEnabled(status == 'in_progress')

    def save_changes(self, task_data, status_combo, settings_json_edit, marking_settings_group, aggregation_type_combo, employee_count_spinbox, nesting_level_spinbox, sscc_source_combo, refine_prod_date_checkbox, refine_batch_checkbox, refine_country_checkbox):
        """Сохраняет изменения статуса и JSON-настроек."""
        task_id = task_data['id']
        
        new_status = status_combo.currentText()
        if new_status != task_data.get('status'):
            try:
                self.task_service.update_task_status(task_id, new_status)
                task_data['status'] = new_status
                QMessageBox.information(self.main_app_window, "Успех", "Статус задачи обновлен.")
            except Exception as e:
                QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось обновить статус: {e}")
                return

        try:
            settings_text = settings_json_edit.toPlainText()
            settings_data = json.loads(settings_text) if settings_text else {}

            if marking_settings_group:
                settings_data['aggregation_type'] = aggregation_type_combo.currentText()
                settings_data['employee_count'] = employee_count_spinbox.value()
                if aggregation_type_combo.currentText() != 'Без агрегации':
                    settings_data['nesting_level'] = nesting_level_spinbox.value()
                    settings_data['sscc_source'] = sscc_source_combo.currentText()
                    settings_data['refine_prod_date'] = refine_prod_date_checkbox.isChecked()
                    settings_data['refine_batch'] = refine_batch_checkbox.isChecked()
                    settings_data['refine_country'] = refine_country_checkbox.isChecked()
                else:
                    if 'nesting_level' in settings_data:
                        del settings_data['nesting_level']
                    if 'sscc_source' in settings_data:
                        del settings_data['sscc_source']
                    if 'refine_prod_date' in settings_data: del settings_data['refine_prod_date']
                    if 'refine_batch' in settings_data: del settings_data['refine_batch']
                    if 'refine_country' in settings_data: del settings_data['refine_country']

            self.task_service.update_task_settings(task_id, settings_data)
            QMessageBox.information(self.main_app_window, "Успех", "Настройки задачи сохранены.")
        except json.JSONDecodeError:
            QMessageBox.critical(self.main_app_window, "Ошибка", "Некорректный формат JSON в настройках.")
            return
        except Exception as e:
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось сохранить настройки: {e}")

        self.main_app_window.load_tasks()

    def update_status(self, task_data, new_status, status_combo):
        """Обработчик для кнопок быстрой смены статуса."""
        task_id = task_data['id']
        try:
            self.task_service.update_task_status(task_id, new_status)
            task_data['status'] = new_status
            status_combo.setCurrentText(new_status)
            QMessageBox.information(self.main_app_window, "Успех", f"Статус задачи обновлен на '{new_status}'.")
            self.main_app_window.load_tasks()
        except Exception as e:
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось обновить статус: {e}")

    def generate_employee_passes(self, task_data, employee_count_spinbox):
        """Генерирует уникальные коды доступа для сотрудников."""
        try:
            task_id = task_data['id']
            employee_count = employee_count_spinbox.value()

            generated_codes = self.task_service.generate_employee_passes(task_id, employee_count)
            
            QMessageBox.information(self.main_app_window, "Успех", 
                                    f"Успешно сгенерировано {len(generated_codes)} пропусков для задачи #{task_id}.")
            
            dialog = EmployeePassesViewerDialog(self.main_app_window, self.task_service, self.main_app_window.user_info, task_id)
            dialog.exec()
            
        except Exception as e:
            logging.error(f"Ошибка при генерации пропусков: {e}", exc_info=True)
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось сгенерировать пропуски: {e}")
