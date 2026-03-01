# src/api_service.py
import functools
import os
import requests
import logging
import time
import json
import pandas as pd
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ApiService:
    """
    Сервис для инкапсуляции всех взаимодействий с внешним API ДМкод.
    Реализует "ленивую" загрузку и автоматическое обновление/повторную аутентификацию.
    """

    def __init__(self, user_info: dict, order_service=None, reauth_handler: Optional[Callable[[], bool]] = None):
        """
        Инициализирует сервис.
        :param user_info: Словарь с данными пользователя.
        :param order_service: Экземпляр OrderService для работы с БД заказов.
        :param reauth_handler: Функция обратного вызова для выполнения полной повторной аутентификации.
                               Должна возвращать True в случае успеха.
        """
        self.user_info = user_info
        self.order_service = order_service
        self.reauth_handler = reauth_handler
        api_config = self.user_info.get('client_api_config', {})
        self.api_base_url = api_config.get('api_base_url')

        if not self.api_base_url:
            # Не выбрасываем исключение, а логируем, т.к. API может не использоваться
            logger.warning("URL для подключения к API не найден в конфигурации пользователя.")

    def authenticate(self) -> bool:
        """
        Выполняет аутентификацию в API, используя учетные данные из user_info.
        Сохраняет токены в user_info.
        Возвращает True в случае успеха, иначе выбрасывает исключение.
        """
        api_config = self.user_info.get('client_api_config', {})
        api_email = api_config.get('api_email')
        api_password = api_config.get('api_password')

        if not self.api_base_url or not api_email or not api_password:
            logger.error("API credentials (URL, email, password) are not fully configured.")
            raise ConnectionError("Учетные данные API не настроены.")

        logger.info(f"Попытка аутентификации в API для пользователя {api_email}...")
        try:
            url = f"{self.api_base_url.rstrip('/')}/user/token"
            # Используем GET, как было в оригинальном коде в auth_qt
            response = requests.get(url, json={'email': api_email, 'password': api_password}, timeout=15)
            response.raise_for_status()
            
            new_tokens = response.json()
            self.user_info['api_access_token'] = new_tokens.get('access')
            self.user_info['api_refresh_token'] = new_tokens.get('refresh')
            
            if not self.user_info['api_access_token']:
                raise ValueError("API did not return an access token.")

            logger.info("Аутентификация в API прошла успешно. Токены получены.")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Не удалось получить токен: {e}", exc_info=True)
            raise ConnectionError("Не удалось выполнить аутентификацию в API.") from e

    def refresh_token(self) -> bool:
        """Обновляет access и refresh токены, используя текущий refresh токен."""
        refresh_token = self.user_info.get('api_refresh_token')
        if not refresh_token:
            logger.warning("Refresh token не найден. Невозможно обновить токен доступа.")
            raise ConnectionError("Refresh token отсутствует.")

        logger.info("Токен доступа истек или невалиден. Попытка обновления...")
        try:
            url = f"{self.api_base_url.rstrip('/')}/user/token/refresh"
            response = requests.post(url, json={'refresh': refresh_token}, timeout=10)
            response.raise_for_status()
            
            new_tokens = response.json()
            self.user_info['api_access_token'] = new_tokens['access']
            self.user_info['api_refresh_token'] = new_tokens['refresh']
            
            logger.info("Токены успешно обновлены.")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Не удалось обновить токен: {e}", exc_info=True)
            # Очищаем старые токены, так как они, вероятно, недействительны
            self.user_info['api_access_token'] = None
            self.user_info['api_refresh_token'] = None
            raise ConnectionError("Не удалось обновить токен. Требуется повторная авторизация.") from e

    def _get_auth_headers(self) -> Optional[dict]:
        """Создает заголовок авторизации, если токен существует."""
        access_token = self.user_info.get('api_access_token')
        if not access_token:
            return None
        return {'Authorization': f'Bearer {access_token}'}

    def _ensure_token(self):
        """Гарантирует наличие действительного токена, обновляя или получая его заново."""
        if self._get_auth_headers():
            return

        logger.info("Токен доступа отсутствует. Попытка восстановить сессию...")
        try:
            # Сначала пытаемся обновить. Это сработает, если есть действительный refresh-токен.
            if self.user_info.get('api_refresh_token'):
                self.refresh_token()
                logger.info("Сессия восстановлена с помощью refresh-токена.")
                return
        except ConnectionError:
            # Если refresh не удался, переходим к полной аутентификации.
            logger.warning("Не удалось обновить токен. Требуется полная аутентификация.")

        # Если дошли до сюда, нужна полная аутентификация.
        if self.reauth_handler:
            logger.info("Вызов обработчика повторной аутентификации...")
            if not self.reauth_handler():
                raise ConnectionError("Повторная аутентификация не удалась или была отменена.")
        else:
            # Если обработчик не предоставлен, пытаемся аутентифицироваться напрямую.
            logger.info("Обработчик не найден, попытка прямой аутентификации...")
            self.authenticate()
            
        if not self.user_info.get('api_access_token'):
             raise ConnectionError("Не удалось получить токен доступа после всех попыток.")


    def _api_request(self, method, url, is_retry=False, **kwargs):
        """
        Обертка для всех API-запросов с ленивой загрузкой и автоматическим
        обновлением/повторной аутентификацией.
        """
        if not self.api_base_url:
            raise ConnectionError("URL API не настроен.")
            
        # 1. Гарантируем наличие токена перед запросом.
        self._ensure_token()

        # --- ОТКАТ ИЗМЕНЕНИЙ: Убираем специальную обработку для GET-запросов. ---
        # Библиотека requests будет передавать 'json' в теле запроса,
        # как того требует API, даже для метода GET.

        # 2. Выполняем запрос.
        try:
            headers = self._get_auth_headers()
            if headers is None:
                 # Этого не должно произойти, если _ensure_token отработал корректно
                 raise ConnectionError("Не удалось сформировать заголовок авторизации.")
            
            response = requests.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            # 3. Обрабатываем ошибку авторизации.
            if e.response.status_code == 401 and not is_retry:
                logger.warning("Запрос не авторизован (401). Попытка обновить токен и повторить.")
                # Очищаем недействительный токен
                self.user_info['api_access_token'] = None
                # Рекурсивный вызов, который снова пройдет через _ensure_token.
                # is_retry=True предотвращает бесконечный цикл.
                return self._api_request(method, url, is_retry=True, **kwargs)
            
            # Если это не 401 или если это уже повторная попытка, пробрасываем ошибку.
            logger.error(f"Ошибка API запроса ({method.upper()} {url}): {e.response.status_code} - {e.response.text}")
            raise

    # --- НОВЫЙ ВЫСОКОУРОВНЕВЫЙ МЕТОД ---
    def request_codes_full_cycle(self, order_id, progress_callback):
        """
        Выполняет полную цепочку: создание заказа (если нужно), пауза, создание запроса на коды.
        """
        if not self.order_service:
            raise ValueError("OrderService не был предоставлен для выполнения этой операции.")

        def log(message):
            if progress_callback:
                progress_callback(message)

        log("Шаг 1/7: Проверка токена API...")
        self.get_participants()
        log("Токен API в порядке.")

        order_data = self.order_service.get_order_by_id(order_id)
        api_order_id = order_data.get('api_order_id')

        if not api_order_id:
            log("\nШаг 2-3/7: Создание заказа в API...")
            order_creation_data = self.order_service.get_order_for_api_creation(order_id)
            products_payload = [
                {"gtin": p['gtin'], "code_template": order_creation_data['dm_template'], "qty": int(p['dm_quantity']), "unit_type": "UNIT", "release_method": "IMPORT", "payment_type": 2} 
                for p in order_creation_data['products']
            ]
            api_payload = {
                "participant_id": order_creation_data['client_api_id'], 
                "production_order_id": order_creation_data['notes'] or "", 
                "contact_person": self.user_info['name'], 
                "products": products_payload
            }
            
            log(f"Тело запроса на создание заказа:\n{json.dumps(api_payload, indent=2, ensure_ascii=False)}")
            response_data = self.create_order(api_payload)
            api_order_id = response_data.get('order_id')
            if not api_order_id:
                raise Exception(f"API не вернуло ID заказа: {response_data}")

            self.order_service.update_order_api_id(order_id, api_order_id)
            log(f"Заказ в API создан с ID: {api_order_id}")
        else:
            log(f"\nШаг 2-3/7: Заказ ID {api_order_id} уже существует.")

        log(f"\nШаг 4/7: Ожидание активации заказа ID {api_order_id}...")
        max_wait_time, check_interval = 300, 5
        start_time = time.time()
        while time.time() - start_time < max_wait_time:
            details = self.get_order_details(api_order_id)
            orders_list = details.get('orders', [])
            if not orders_list:
                log("Ожидание данных заказа от API...")
                time.sleep(check_interval)
                continue
            
            order_obj = orders_list[0]
            order_active = order_obj.get('state') == 'ACTIVE'
            products_active = all(p.get('state') == 'ACTIVE' for p in order_obj.get('products', []))

            if order_active and products_active:
                log("Заказ и все продукты активны.")
                break
            
            log(f"Ожидание... (проверка через {check_interval} сек)")
            time.sleep(check_interval)
        else:
            final_details_str = json.dumps(details, indent=2, ensure_ascii=False)
            raise Exception(f"Время ожидания активации заказа истекло. Последний ответ от API:\n{final_details_str}")

        log(f"\nШаг 5/7: Создание запроса на коды...")
        suborder_req_payload = {"order_id": int(api_order_id)}
        suborder_req_response = self.create_suborder_request(suborder_req_payload)
        log(f"Ответ API: {json.dumps(suborder_req_response, ensure_ascii=False)}")

        log(f"\nШаг 6/7: Ожидание активного запроса на коды...")
        suborders_to_sign = []
        total_codes = 0
        gtin_summary = {}
        max_wait_time, check_interval = 120, 3
        start_time = time.time()
        while time.time() - start_time < max_wait_time:
            suborders_details = self.get_suborders(api_order_id)
            for order in suborders_details.get('orders', []):
                for suborder in order.get('suborders', []):
                    if suborder.get('state') == 'ACTIVE':
                        suborders_to_sign.append(suborder)
                        for product in suborder.get('suborder_products', []):
                            qty = product.get('qty', 0)
                            gtin = product.get('gtin')
                            total_codes += qty
                            if gtin:
                                gtin_summary[gtin] = gtin_summary.get(gtin, 0) + qty
            if suborders_to_sign:
                break
            log(f"Ожидание... (проверка через {check_interval} сек)")
            time.sleep(check_interval)
        
        if not suborders_to_sign:
            raise Exception(f"Не найдено активных запросов к подписи после их создания. Последний ответ от API: {json.dumps(suborders_details, indent=2, ensure_ascii=False)}")

        summary_text = f"\n--- Сводка для подписи ---\n"
        summary_text += f"Найдено запросов к подписи: {len(suborders_to_sign)}\n"
        summary_text += f"Общее количество кодов: {total_codes}\n\n"
        summary_text += "Детализация по GTIN:\n"
        for gtin, qty in gtin_summary.items():
            summary_text += f"  - GTIN: {gtin}, Кол-во: {qty}\n"
        log(summary_text)

        self.order_service.update_order_status(order_id, 'Запрос создан')
        
        final_message = (
            f"Запрос на {total_codes} кодов успешно создан в ДМ.Код.\n\n"
            "Пожалуйста, перейдите на сайт ДМ.Код и подпишите созданный запрос с помощью ЭЦП."
        )
        return final_message

    def get_codes_full_cycle(self, order_id, post_processing_mode, progress_callback):
        """
        Выполняет полный цикл: разбивка на тиражи, подготовка JSON, скачивание кодов.
        """
        if not self.order_service:
            raise ValueError("OrderService не был предоставлен для выполнения этой операции.")

        def log(message):
            if progress_callback:
                progress_callback(message)

        log("--- НАЧАЛО ПОЛНОГО ЦИКЛА ПОЛУЧЕНИЯ КОДОВ ---")
        
        # Шаг 1: Тиражи
        log("\n--- Шаг 1/3: Создание тиражей ---")
        self._split_runs_step(order_id, post_processing_mode, log)
        
        # Шаг 2: JSON
        log("\n--- Шаг 2/3: Запрос на подготовку JSON ---")
        self._prepare_json_step(order_id, log)

        # Шаг 3: Скачивание
        log("\n--- Шаг 3/3: Скачивание кодов ---")
        self._download_codes_step(order_id, log)
        
        self.order_service.update_order_status(order_id, 'Коды скачаны')
        log("\n--- ПОЛНЫЙ ЦИКЛ ПОЛУЧЕНИЯ КОДОВ УСПЕШНО ЗАВЕРШЕН ---")
        return "Все шаги (тиражи, JSON, скачивание) успешно выполнены."

    def _split_runs_step(self, order_id, post_processing_mode, log):
        """Часть полного цикла: создание тиражей."""
        log("Синхронизация активных тиражей из API...")
        api_order_id = self.order_service.get_order_by_id(order_id).get('api_order_id')
        
        api_printruns_response = self.get_printruns(api_order_id)
        orders_list = api_printruns_response.get('orders', [])
        if not orders_list:
            raise Exception("Ответ API на get_printruns не содержит списка 'orders'.")
        
        api_printruns = orders_list[0].get('printruns', [])
        gtin_to_active_run_id = {p['gtin']: p['id'] for p in api_printruns if p.get('state') == 'ACTIVE'}

        self.order_service.clear_and_sync_printruns(order_id, gtin_to_active_run_id)
        log("Синхронизация завершена.")
        
        details_data = self.order_service.get_details_for_splitting(order_id, post_processing_mode)
        if not details_data:
            raise Exception("В заказе нет детализации для создания тиражей.")
        details_df = pd.DataFrame(details_data)
        log(f"Найдено {len(details_df)} позиций для обработки в локальной БД.")

        order_details_from_api = self.get_order_details(api_order_id)
        api_products = order_details_from_api.get('orders', [{}])[0].get('products', [])
        if not api_products:
            raise Exception("API не вернуло список продуктов в заказе.")
        # --- ИЗМЕНЕНИЕ: Добавлено детальное логирование ---
        log(f"От API получено {len(api_products)} товаров. GTINs: {[p.get('gtin') for p in api_products]}")

        # --- ИСПРАВЛЕНИЕ: Обновляем справочник товаров на основе данных из API ---
        products_to_upsert = [{'gtin': p['gtin'], 'name': p['name']} for p in api_products if p.get('name') and p.get('gtin')]
        if products_to_upsert:
            log(f"Подготовлено к обновлению/вставке {len(products_to_upsert)} товаров в локальный справочник 'products'.")
            try:
                from .utils import upsert_data_to_db
                upsert_df = pd.DataFrame(products_to_upsert)
                with self.order_service._get_connection() as conn:
                    with conn.cursor() as cur:
                        upsert_data_to_db(cur, upsert_df, 'products', 'gtin')
                    conn.commit() # <-- ИСПРАВЛЕНИЕ: Добавлена фиксация транзакции
                log("Успешно выполнена операция обновления/вставки в справочник товаров.")
            except Exception as e:
                log(f"ОШИБКА при обновлении справочника товаров: {e}")
                logger.error("Ошибка при обновлении справочника товаров из API", exc_info=True)
        else:
            log("Не найдено товаров с GTIN и именем в ответе API для обновления локального справочника.")

        gtin_to_api_product_id = {p['gtin']: p['id'] for p in api_products if p.get('state') == 'ACTIVE' and p.get('qty') == p.get('qty_received')}
        details_df['api_product_id'] = details_df['gtin'].map(gtin_to_api_product_id)
        log("Сопоставление продуктов с API завершено.")

        for i, row in details_df.iterrows():
            if pd.notna(row.get('api_id')):
                log(f"Пропуск GTIN {row['gtin']}, тираж уже существует (ID: {row['api_id']}).")
                continue
            
            if pd.isna(row.get('api_product_id')):
                log(f"Пропуск GTIN {row['gtin']}, не найден активный продукт в API.")
                continue

            log(f"--- Создаю тираж для GTIN {row['gtin']}...")
            try:
                tirage_payload = {"order_product_id": int(row['api_product_id']), "qty": int(row['dm_quantity'])}
                response_data = self.create_printrun(tirage_payload)
                new_printrun_id = response_data.get('printrun_id')
                if not new_printrun_id:
                    raise Exception(f"API не вернуло 'printrun_id' для GTIN {row['gtin']}.")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 400:
                    raise Exception("API вернуло ошибку 400. Вероятно, система еще обрабатывает предыдущий запрос. Подождите несколько минут и попробуйте снова.")
                else:
                    raise

            if 'id' in row and pd.notna(row['id']):
                self.order_service.update_detail_api_id(row['id'], new_printrun_id)
            else:
                self.order_service.update_details_api_id_by_gtin(order_id, row['gtin'], new_printrun_id)
            log(f"  Успешно создан тираж ID {new_printrun_id} для GTIN {row['gtin']}.")

            log("  Ожидание готовности API к созданию следующего тиража...")
            max_wait, interval = 180, 5
            start_time = time.time()
            while time.time() - start_time < max_wait:
                runs_resp = self.get_printruns(api_order_id)
                # --- ИСПРАВЛЕНИЕ: Добавлена проверка на пустой ответ от API ---
                # Если API вернуло пустой ответ, orders_list будет пустым, и код упадет с ошибкой.
                orders_list = runs_resp.get('orders', [])
                if not orders_list:
                    log("  API вернуло пустой ответ, ожидание...")
                    time.sleep(interval)
                    continue # Переходим к следующей итерации цикла
                is_awaiting = any(p.get('state') == 'AWAITING' for p in orders_list[0].get('printruns', []))
                if not is_awaiting:
                    log("  API готово.")
                    break
                log(f"  API занято (статус AWAITING). Проверка через {interval} сек...")
                time.sleep(interval)
            else:
                raise Exception("Время ожидания готовности API истекло. Один из тиражей остался в статусе AWAITING.")
        
        self.order_service.update_order_status(order_id, 'Тиражи созданы')
        log("Шаг создания тиражей успешно завершен.")

    def _prepare_json_step(self, order_id, log):
        """Часть полного цикла: подготовка JSON."""
        unique_printrun_ids = self.order_service.get_unique_printrun_ids(order_id)
        if not unique_printrun_ids:
            raise Exception("Не найдено уникальных ID тиражей для запроса JSON.")

        log(f"Найдено {len(unique_printrun_ids)} уникальных тиражей для запроса.")
        for i, printrun_id in enumerate(unique_printrun_ids):
            log(f"--- {i+1}/{len(unique_printrun_ids)}: Запрос JSON для тиража ID {printrun_id}...")
            self.create_printrun_json({"printrun_id": printrun_id})
            log(f"  Запрос для тиража {printrun_id} успешно отправлен.")
            time.sleep(0.5)

        log("\nОжидание генерации JSON сервером...")
        max_wait, interval = 300, 5
        start_time = time.time()
        api_order_id = self.order_service.get_order_by_id(order_id).get('api_order_id')
        while time.time() - start_time < max_wait:
            runs_resp = self.get_printruns(api_order_id)
            runs_list = runs_resp.get('orders', [{}])[0].get('printruns', [])
            api_runs_status = {p['id']: p.get('json', False) for p in runs_list}
            
            all_ready = True
            for run_id in unique_printrun_ids:
                if not api_runs_status.get(run_id, False):
                    all_ready = False
                    log(f"  JSON для тиража {run_id} еще не готов.")
                    break
            
            if all_ready:
                log("  Все JSON-файлы готовы.")
                break
            
            log(f"  Проверка через {interval} сек...")
            time.sleep(interval)
        else:
            raise Exception("Время ожидания готовности JSON истекло.")
        
        self.order_service.update_order_status(order_id, 'JSON заказан')
        log("Шаг подготовки JSON успешно завершен.")

    def _download_codes_step(self, order_id, log):
        """Часть полного цикла: скачивание кодов."""
        unique_printrun_ids = self.order_service.get_unique_printrun_ids(order_id)
        if not unique_printrun_ids:
            raise Exception("Не найдено уникальных ID тиражей для скачивания кодов.")

        # --- ИСПРАВЛЕНИЕ: Обновление справочника товаров перенесено сюда ---
        # Это гарантирует, что справочник обновится, даже если шаг создания тиражей был пропущен.
        api_order_id = self.order_service.get_order_by_id(order_id).get('api_order_id')
        order_details_from_api = self.get_order_details(api_order_id)
        api_products = order_details_from_api.get('orders', [{}])[0].get('products', [])
        if not api_products:
            log("ВНИМАНИЕ: API не вернуло список продуктов в заказе. Обновление справочника пропущено.")
        else:
            log(f"  Приступаю к обработке списка продуктов, полученных из API.")
            log(f"От API получено {len(api_products)} товаров для обновления справочника. GTINs: {[p.get('gtin') for p in api_products]}")
            products_to_upsert = [{'gtin': p['gtin'], 'name': p['name']} for p in api_products if p.get('name') and p.get('gtin')]
            if products_to_upsert:
                log(f"Подготовлено к обновлению/вставке {len(products_to_upsert)} товаров в локальный справочник 'products'.")
                try:
                    from .utils import upsert_data_to_db
                    upsert_df = pd.DataFrame(products_to_upsert)
                    log(f"  DataFrame для UPSERT создан, размер: {len(upsert_df)} строк.")
                    with self.order_service._get_connection() as conn:
                        with conn.cursor() as cur:
                            upsert_data_to_db(cur, 'products', upsert_df, 'gtin')
                    conn.commit() # <-- ИСПРАВЛЕНИЕ: Добавлена фиксация транзакции
                    log("Успешно выполнена операция обновления/вставки в справочник товаров.")
                except Exception as e:
                    log(f"  Произошла ошибка при UPSERT: {e}")
                    log(f"ОШИБКА при обновлении справочника товаров: {e}")
                    logger.error("Ошибка при обновлении справочника товаров из API", exc_info=True)
            else:
                log("Не найдено товаров с GTIN и именем в ответе API для обновления локального справочника.")

        log(f"Найдено {len(unique_printrun_ids)} тиражей для скачивания.")
        total_codes_downloaded = 0
        for i, printrun_id in enumerate(unique_printrun_ids):
            log(f"--- {i+1}/{len(unique_printrun_ids)}: Скачивание кодов для тиража ID {printrun_id}...")
            try:
                codes_json = self.download_printrun_json({"printrun_id": printrun_id})
                if not codes_json or 'codes' not in codes_json:
                    log(f"  ВНИМАНИЕ: Для тиража {printrun_id} получен пустой ответ или ответ без ключа 'codes'.")
                    continue
                
                num_codes = len(codes_json['codes'])
                total_codes_downloaded += num_codes
                log(f"  Скачано {num_codes} кодов. Сохранение в базу данных...")
                
                self.order_service.save_downloaded_codes(printrun_id, codes_json)
                log("  Коды успешно сохранены.")

            except Exception as e:
                log(f"  ОШИБКА при скачивании кодов для тиража {printrun_id}: {e}")
                continue
        
        log(f"\nСкачивание завершено. Всего скачано кодов: {total_codes_downloaded}.")

    # --- Существующие низкоуровневые методы ---
    def prepare_utilisation_data_full_cycle(self, order_id, progress_callback):
        """
        Выполняет полный цикл подготовки сведений для отчета и запрашивает подтверждение.
        Возвращает специальный кортеж для вызова диалога в UI.
        """
        if not self.order_service:
            raise ValueError("OrderService не был предоставлен для выполнения этой операции.")

        def log(message):
            if progress_callback:
                progress_callback(message)

        log("--- НАЧАЛО ПОДГОТОВКИ СВЕДЕНИЙ ДЛЯ ОТЧЕТА ---")
        order_data = self.order_service.get_order_by_id(order_id)
        order_status = order_data.get('status')
        log(f"Статус заказа: {order_status}")

        # Получаем все ID загрузок, которые уже есть в БД
        existing_upload_ids = self.order_service.get_all_utilisation_upload_ids(order_id, order_status)
        newly_created_upload_ids = []

        # Логика для статуса 'delta'
        if order_status == 'delta':
            results_to_process = self.order_service.get_delta_results_for_upload(order_id)
            if not results_to_process:
                log("Нет новых данных от 'Дельта' для отправки.")
            else:
                log(f"Найдено {len(results_to_process)} записей от 'Дельта' для обработки.")
                for i, result in enumerate(results_to_process):
                    log(f"--- {i+1}/{len(results_to_process)}: Отправка данных для тиража ID {result['printrun_id']} ---")
                    response_data = self.upload_utilisation_data(result['codes_json'])
                    upload_id = response_data.get('utilisation_upload_id')
                    if not upload_id: raise ValueError(f"API не вернуло 'utilisation_upload_id': {response_data}")
                    newly_created_upload_ids.append(upload_id)
                    self.order_service.update_delta_result_upload_id(result['id'], upload_id)
                    log(f"  Ответ API: {json.dumps(response_data, ensure_ascii=False)}")
                    time.sleep(2)
        # Логика для статуса 'dmkod'
        elif order_status == 'dmkod':
            details_to_process = self.order_service.get_dmkod_details_for_upload(order_id)
            if not details_to_process:
                log("Нет новых тиражей для отправки сведений.")
            else:
                for detail in details_to_process:
                    attributes = {k: v.strftime('%Y-%m-%d') for k, v in {'production_date': detail.get('production_date'), 'expiration_date': detail.get('expiry_date')}.items() if v}
                    if detail.get('fias_code'): attributes['fias_id'] = detail['fias_code']
                    payload = {"all_from_printrun": detail['api_id']}
                    if attributes: payload['attributes'] = attributes
                    response_data = self.upload_utilisation_data(payload)
                    upload_id = response_data.get('utilisation_upload_id')
                    if not upload_id: raise ValueError(f"API не вернуло 'utilisation_upload_id': {response_data}")
                    newly_created_upload_ids.append(upload_id)
                    self.order_service.update_detail_utilisation_upload_id(detail['detail_id'], upload_id)
                    log(f"  Записи присвоен ID из API: {upload_id}")
                    time.sleep(2)

        log("\n--- Итоговая проверка ---")
        summary = self.order_service.get_order_summary(order_id)
        summary_msg_1 = f"По клиенту '{summary['client_name']}' заказу №{order_id} всего в заказе {summary['total_products']} товаров. Заказано {summary['ordered_codes']} кодов, получено {summary['received_codes']} кодов."
        log(summary_msg_1)
        total_success, total_not_found, total_duplicated = self.get_aggregated_utilisation_results(order_id, order_status)
        summary_msg_2 = f"Результаты обработки в API: \n  - Успешно принято: {total_success}\n  - Не найдено: {total_not_found}\n  - Дубликаты: {total_duplicated}"
        log(summary_msg_2)
        final_prompt = f"{summary_msg_1}\n\n{summary_msg_2}\n\nПодготовить отчет?"
        return ('ask_prepare_report', final_prompt)

    def create_utilisation_report_full_cycle(self, order_id, progress_callback):
        """Выполняет полный цикл создания отчета о нанесении."""
        if not self.order_service:
            raise ValueError("OrderService не был предоставлен для выполнения этой операции.")

        def log(message):
            if progress_callback:
                progress_callback(message)

        log("--- НАЧАЛО ПОДГОТОВКИ ОТЧЕТА О НАНЕСЕНИИ ---")
        details_to_process = self.order_service.get_details_for_report(order_id)

        if not details_to_process:
            raise Exception("Не найдено позиций с ID тиража (api_id) для подготовки отчета.")

        log(f"Найдено {len(details_to_process)} позиций для обработки.")
        for i, detail in enumerate(details_to_process):
            log(f"--- {i+1}/{len(details_to_process)}: Отправка запроса для GTIN {detail['gtin']} (ID тиража: {detail['api_id']}) ---")
            self.create_utilisation_report({"printrun_id": detail['api_id']})
            log(f"  Запрос для тиража {detail['api_id']} успешно отправлен.")

        self.order_service.update_order_status(order_id, 'Отчет подготовлен')
        return "Отчет об использовании кодов успешно подготовлен и отправлен в АПИ."

    def check_utilisation_report_status(self, order_id: int, progress_callback: Callable):
        """
        Проверяет статус отчета об утилизации, сверяя заказанные, полученные и успешно обработанные коды.
        """
        if not self.order_service:
            raise ValueError("OrderService не был предоставлен для выполнения этой операции.")

        def log(message):
            if progress_callback:
                progress_callback(message)

        log("--- НАЧАЛО ПРОВЕРКИ СТАТУСА ОТЧЕТА ---")

        # 1. Получаем сводку по заказу из локальной БД
        log("Шаг 1/2: Получение сводки по заказу из локальной БД...")
        summary = self.order_service.get_order_summary(order_id)
        ordered_codes = summary.get('ordered_codes', 0)
        received_codes = summary.get('received_codes', 0)
        log(f"  Заказано кодов: {ordered_codes}")
        log(f"  Получено кодов: {received_codes}")

        # 2. Получаем агрегированные результаты из API
        log("\nШаг 2/2: Получение итогов обработки из API ДМ.Код...")
        order_data = self.order_service.get_order_by_id(order_id)
        order_status = order_data.get('status')
        total_success, total_not_found, total_duplicated = self.get_aggregated_utilisation_results(order_id, order_status)
        log(f"  Успешно принято API: {total_success}")
        log(f"  Не найдено в API: {total_not_found}")
        log(f"  Дубликаты в API: {total_duplicated}")

        # 3. Сравнение и формирование результата
        log("\n--- РЕЗУЛЬТАТ ПРОВЕРКИ ---")
        errors = []
        if ordered_codes != received_codes:
            errors.append(f"Расхождение в заказанных ({ordered_codes}) и полученных ({received_codes}) кодах.")
        if received_codes != total_success:
            errors.append(f"Расхождение в полученных ({received_codes}) и успешно принятых API ({total_success}) кодах.")
        if total_not_found > 0:
            errors.append(f"API не нашло {total_not_found} кодов.")
        if total_duplicated > 0:
            errors.append(f"API обнаружило {total_duplicated} дубликатов.")

        if not errors:
            log("\n--- ЭТАП 2: Проверка статуса отчетов о нанесении ---")
            api_order_id = order_data.get('api_order_id')
            if not api_order_id:
                raise ValueError("Не найден ID заказа в API (api_order_id) для выполнения второго этапа проверки.")

            log(f"Запрос статуса отчетов о нанесении для заказа API ID: {api_order_id}...")
            subreports_data = self.get_utilisation_subreports(api_order_id)
            
            all_subreports = subreports_data.get('orders', [{}])[0].get('utilisation_subreports', [])
            closed_subreports = [sr for sr in all_subreports if sr.get('state') == 'CLOSED']
            
            total_closed_qty = sum(sr.get('qty', 0) for sr in closed_subreports)
            log(f"Найдено {len(closed_subreports)} закрытых отчетов. Общее количество кодов в них: {total_closed_qty}.")

            if total_closed_qty == total_success:
                log("УСПЕХ: Количество кодов в закрытых отчетах совпадает с общим количеством успешно принятых кодов.")
                self.order_service.update_order_status(order_id, 'Отчет проверен')
                
                client_name = summary.get('client_name', 'N/A')
                email_prompt = (
                    f"Проверка успешно пройдена для заказа №{order_id} (клиент: {client_name}).\n\n"
                    "Отправить email-уведомление об успешном завершении?"
                )
                return ('ask_send_email_notification', email_prompt)
            else:
                log("ОШИБКА: Количество кодов в закрытых отчетах не совпадает с успешно принятыми.")
                problematic_reports = [
                    sr for sr in all_subreports 
                    if sr.get('request') is not None or sr.get('fail_reason') is not None or sr.get('fail_reason_extra') is not None
                ]
                if problematic_reports:
                    error_details = "\n".join([f"  - ID {sr['id']} (GTIN {sr['gtin']}): State={sr['state']}, FailReason={sr.get('fail_reason')}" for sr in problematic_reports])
                    raise Exception(f"Обнаружены отчеты с ошибками или в обработке:\n{error_details}")
                else:
                    raise Exception("Еще не все отчеты об утилизации приняты Честным знаком. Проверку нужно повторить через 5 минут.")
        else:
            error_message = "Обнаружены расхождения:\n- " + "\n- ".join(errors) + "\n\nТребуется ручная проверка."
            raise Exception(error_message)

    def get_participants(self):
        """Получает список участников (клиентов) из API."""
        logger.info("Получение списка участников из API...")
        try:
            participants_url = f"{self.api_base_url.rstrip('/')}/psp/participants"
            response = self._api_request('get', participants_url)
            return response.json().get('participants', [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Не удалось получить список участников из API: {e}", exc_info=True)
            raise

    def create_order(self, payload: dict):
        """Создает заказ в API ДМкод."""
        logger.info(f"Отправка запроса на создание заказа в API. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/order/create"
            response = self._api_request('post', url, json=payload, timeout=30)
            logger.info(f"Заказ успешно создан в API. Ответ: {response.json()}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при создании заказа в API: {e}", exc_info=True)
            raise

    def create_suborder_request(self, payload: dict):
        """Создает запрос на коды (suborder) в API ДМкод."""
        logger.info(f"Отправка запроса на создание suborder. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/suborders/create"
            response = self._api_request('post', url, json=payload, timeout=30)
            logger.info(f"Запрос на коды успешно отправлен. Ответ: {response.json()}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при создании запроса на коды: {e}", exc_info=True)
            raise

    def get_order_details(self, api_order_id: int):
        """Получает детали заказа из API."""
        logger.info(f"Запрос деталей заказа ID {api_order_id} из API.")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/orders"
            response = self._api_request('get', url, json={"order_id": api_order_id}, timeout=30)
            logger.info(f"Детали заказа {api_order_id} успешно получены.")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении деталей заказа {api_order_id}: {e}", exc_info=True)
            raise

    def get_suborders(self, api_order_id: int):
        """Получает список подзаказов (запросов на коды) для заказа."""
        logger.info(f"Запрос подзаказов для заказа ID {api_order_id} из API.")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/suborders"
            response = self._api_request('get', url, json={"order_id": api_order_id}, timeout=30)
            logger.info(f"Подзаказы для заказа {api_order_id} успешно получены.")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении подзаказов для заказа {api_order_id}: {e}", exc_info=True)
            raise

    def create_printrun(self, payload: dict):
        """Создает тираж (printrun) в API."""
        logger.info(f"Отправка запроса на создание тиража. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/printrun/create"
            response = self._api_request('post', url, json=payload, timeout=30)
            logger.info(f"Тираж успешно создан. Ответ: {response.json()}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при создании тиража: {e}", exc_info=True)
            raise

    def get_printruns(self, order_id: int) -> list:
        """
        Получает список тиражей для конкретного заказа.
        Обращается к эндпоинту psp/printruns.
        """
        url = f"{self.api_base_url.rstrip('/')}/psp/printruns"
        payload = {'order_id': int(order_id)} 
        logging.info(f"Запрос списка тиражей для заказа ID {order_id} из API. Эндпоинт: {url}")
        try:
            response = self._api_request('get', url, json=payload)
            logging.info(f"Список тиражей для заказа {order_id} успешно получен.")
            return response.json()
        except Exception as e:
            logging.error(f"Ошибка при запросе списка тиражей для заказа {order_id}: {e}", exc_info=True)
            raise

    def create_printrun_json(self, payload: dict):
        """Запрашивает подготовку JSON-файла с кодами для тиража."""
        logger.info(f"Отправка запроса на подготовку JSON для тиража. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/printrun/json/create"
            response = self._api_request('post', url, json=payload, timeout=30)
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе JSON для тиража: {e}", exc_info=True)
            raise

    def download_printrun_json(self, payload: dict):
        """Скачивает готовый JSON-файл с кодами для тиража."""
        logger.info(f"Отправка запроса на скачивание кодов для тиража. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/printrun/json/download"
            response = self._api_request('get', url, json=payload, timeout=60)
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при скачивании кодов для тиража: {e}", exc_info=True)
            raise

    def upload_utilisation_data(self, payload: dict):
        """
        Отправляет сведения об использовании кодов (атрибуция, агрегация).
        """
        logger.info(f"Отправка сведений об использовании. Payload: {payload}")
        import json
        try:
            if isinstance(payload, str):
                payload_dict = json.loads(payload)
            else:
                payload_dict = payload

            url = f"{self.api_base_url.rstrip('/')}/psp/utilisation/upload"
            response = self._api_request('post', url, json=payload_dict, timeout=240)
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при отправке сведений об использовании: {e}", exc_info=True)
            raise

    def get_utilisation_result(self, utilisation_upload_id: int):
        """
        Получает результат обработки ранее отправленных сведений об использовании.
        """
        logger.info(f"Запрос результата для utilisation_upload_id: {utilisation_upload_id}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/utilisation/upload/result"
            payload = {'utilisation_upload_id': int(utilisation_upload_id)}
            response = self._api_request('get', url, json=payload, timeout=60)
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении результата для utilisation_upload_id {utilisation_upload_id}: {e}", exc_info=True)
            raise

    def create_utilisation_report(self, payload: dict):
        """
        Отправляет запрос на создание отчета об использовании (нанесении).
        """
        logger.info(f"Отправка запроса на создание отчета. Payload: {payload}")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/utilisation/report/create"
            response = self._api_request('post', url, json=payload, timeout=120)
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при создании отчета об использовании: {e}", exc_info=True)
            raise

    def get_utilisation_subreports(self, api_order_id: int):
        """Получает детали отчетов о нанесении (subreports) из API."""
        logger.info(f"Запрос отчетов о нанесении для заказа ID {api_order_id} из API.")
        try:
            url = f"{self.api_base_url.rstrip('/')}/psp/utilisation/subreports"
            response = self._api_request('get', url, json={"order_id": api_order_id}, timeout=30)
            logger.info(f"Отчеты о нанесении для заказа {api_order_id} успешно получены.")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при получении отчетов о нанесении для заказа {api_order_id}: {e}", exc_info=True)
            raise

    def send_report_success_email(self, order_id: int, progress_callback: Callable):
        """Отправляет email-уведомление об успешной проверке отчета."""
        if not self.order_service:
            raise ValueError("OrderService не был предоставлен для выполнения этой операции.")
        
        import io

        def log(message):
            if progress_callback:
                progress_callback(message)

        log(f"Подготовка email-уведомления для заказа #{order_id}...")
        summary = self.order_service.get_order_summary(order_id)
        client_name = summary.get('client_name', 'N/A')

        subject = f"Успех: Отчет по заказу №{order_id} ({client_name}) принят и проверен"
        body_html = f"""
        <p>По заказу <b>№{order_id}</b> клиента <b>{client_name}</b> отчет о нанесении кодов маркировки был успешно подан, принят "Честным знаком" и проверен системой.</p>
        <p>Отклонений не выявлено.</p>
        """

        # --- НОВЫЙ БЛОК: Генерация и прикрепление отчета ---
        attachment_data = None
        log("Генерация отчета декларанта для вложения...")
        try:
            report_df, report_name = self.order_service.get_declarator_report_data(order_id)
            if report_df is not None:
                output = io.BytesIO()
                report_df.to_excel(output, index=False)
                attachment_data = (output.getvalue(), f"{report_name}_order_{order_id}.xlsx")
                log(f"Отчет '{attachment_data[1]}' успешно сгенерирован.")
        except Exception as e:
            log(f"ПРЕДУПРЕЖДЕНИЕ: Не удалось сгенерировать отчет для вложения: {e}")
        
        from .email_service import EmailService
        email_service = EmailService()
        email_service.send_email(to_email="ignored@example.com", subject=subject, body_html=body_html, attachment=attachment_data) # type: ignore
        log(f"Email-уведомление для заказа #{order_id} успешно отправлено.")

        # --- НОВЫЙ БЛОК: Автоматическая архивация ---
        try:
            log(f"Автоматическая архивация заказа #{order_id}...")
            self.order_service.move_order_to_archive(order_id)
            log(f"Заказ #{order_id} и связанное уведомление успешно перенесены в архив.")
        except Exception as e:
            log(f"ПРЕДУПРЕЖДЕНИЕ: Не удалось автоматически архивировать заказ: {e}")
        return f"Email-уведомление отправлено, заказ #{order_id} перенесен в архив."

    def get_aggregated_utilisation_results(self, order_id: int, order_status: str) -> tuple[int, int, int]:
        """
        Собирает все ID загрузок для заказа, запрашивает их статусы в API и агрегирует результаты.
        """
        logger.info(f"Сбор агрегированных результатов для заказа ID {order_id}, статус '{order_status}'")
        all_upload_ids = []
        
        # Используем order_service для получения ID из базы данных
        if not self.order_service:
            raise ValueError("OrderService не был предоставлен для выполнения этой операции.")
            
        all_upload_ids = self.order_service.get_all_utilisation_upload_ids(order_id, order_status)
        
        if not all_upload_ids:
            logger.warning(f"Не найдено ID загрузок для заказа {order_id}. Возвращаю нули.")
            return 0, 0, 0

        logger.info(f"Проверка статуса обработки для {len(all_upload_ids)} загрузок...")
        total_success, total_not_found, total_duplicated = 0, 0, 0
        
        for upload_id in all_upload_ids:
            result = self.get_utilisation_result(upload_id)
            include_data = result.get('include', {})
            total_success += include_data.get('success', {}).get('count', 0)
            total_not_found += include_data.get('not_found', {}).get('count', 0)
            total_duplicated += include_data.get('duplicated', {}).get('count', 0)
            time.sleep(0.5) # Небольшая пауза между запросами к API

        logger.info(f"Результаты для заказа {order_id}: Успешно={total_success}, Не найдено={total_not_found}, Дубликаты={total_duplicated}")
        return total_success, total_not_found, total_duplicated