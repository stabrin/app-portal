
import logging
from PySide6.QtWidgets import QMessageBox
from ..task_service import TaskService


class OrderLogic:
    def __init__(self, order_service, main_app_window):
        self.order_service = order_service
        self.main_app_window = main_app_window

    def create_production_task(self, order_id, scenario_data):
        """
        Создает производственную задачу на основе текущего заказа,
        если она еще не существует. В противном случае, просто переключается на нее.
        """
        existing_task = self.main_app_window.task_service.get_task_by_order_id(order_id)

        if existing_task:
            QMessageBox.information(self.main_app_window, "Задача уже существует",
                                    f"Задача для заказа #{order_id} уже существует. Переключаемся на нее.")
            self.main_app_window.menu_tree.setCurrentItem(self.main_app_window.menu_items['tasks'])
            self.main_app_window._on_menu_clicked(self.main_app_window.menu_items['tasks'], 0)
            return

        calculated_task_type = "unknown"
        if scenario_data.get('type') == 'Ручная агрегация':
            calculated_task_type = "manual_aggregation"
        elif scenario_data.get('post_processing') == 'Собственный алгоритм':
            calculated_task_type = "marking"

        try:
            new_task_id = self.main_app_window.task_service.create_task(
                order_id,
                calculated_task_type,
                {}
            )

            QMessageBox.information(self.main_app_window, "Успех", f"Задача #{new_task_id} успешно создана.")

            self.main_app_window.menu_tree.setCurrentItem(self.main_app_window.menu_items['tasks'])
            self.main_app_window._on_menu_clicked(self.main_app_window.menu_items['tasks'], 0)

        except Exception as e:
            logging.error(f"Ошибка при создании задачи: {e}", exc_info=True)
            QMessageBox.critical(self.main_app_window, "Ошибка", f"Не удалось создать задачу: {e}")
