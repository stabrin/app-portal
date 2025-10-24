import os
import requests
import json
from functools import wraps
import logging
import pandas as pd # Уже импортирован
import re
import math
from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, Response, send_file
from flask_login import login_user, logout_user, login_required, current_user
from psycopg2 import sql
from dateutil.relativedelta import relativedelta
from psycopg2.extras import RealDictCursor
from bcrypt import checkpw
from io import BytesIO, StringIO

from .db import get_db_connection
from .forms import LoginForm, IntegrationForm, ProductGroupForm
from .auth import User

# --- ИСПРАВЛЕННАЯ ФУНКЦИЯ: Полная копия из datamatrix-app ---
GS_SEPARATOR = '\x1d'
def parse_datamatrix(dm_string: str) -> dict:
    """Разбирает (парсит) строку DataMatrix на составные части."""
    result = {
        'datamatrix': dm_string, 'gtin': '', 'serial': '',
        'crypto_part_91': '', 'crypto_part_92': '', 'crypto_part_93': ''
    }
    cleaned_dm = dm_string.replace(' ', '\x1d').strip()
    parts = cleaned_dm.split(GS_SEPARATOR)
    if len(parts) > 0:
        main_part = parts.pop(0)
        if main_part.startswith('01'):
            result['gtin'] = main_part[2:16]
            serial_part = main_part[16:]
            if serial_part.startswith('21'):
                # Убираем возможный GS в конце серийного номера
                result['serial'] = serial_part[2:].split(GS_SEPARATOR)[0]

    for part in parts:
        if not part: continue
        if part.startswith('91'): result['crypto_part_91'] = part[2:]
        elif part.startswith('92'): result['crypto_part_92'] = part[2:]
        elif part.startswith('93'): result['crypto_part_93'] = part[2:]
    return result

# 1. Определяем Blueprint
import zipfile # Добавляем импорт для работы с ZIP-архивами
dmkod_bp = Blueprint(
    'dmkod_integration_app', __name__,
    static_folder='static'
)

def _sanitize_filename_part(text):
    """
    Sanitizes a string to be safe for use as part of a filename.
    Removes invalid characters, replaces spaces with underscores, and strips leading/trailing underscores/dots.
    """
    if not isinstance(text, str):
        text = str(text)
    # Remove characters that are not alphanumeric, space, hyphen, or dot
    sanitized = re.sub(r'[^\w\s.-]', '', text)
    # Replace spaces with underscores
    sanitized = sanitized.replace(' ', '_')
    # Remove leading/trailing underscores or dots
    sanitized = sanitized.strip('_.')
    return sanitized

def api_token_required(f):
    """
    Кастомный декоратор, который проверяет наличие 'api_access_token' в сессии.
    Если токена нет, перенаправляет на страницу входа приложения.
    Используется после @login_required.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'api_access_token' not in session:
            flash('Для доступа к этому приложению необходима авторизация в API ДМкод.', 'warning')
            # Принудительно выходим из локальной сессии, чтобы пользователь попал на нужную страницу входа
            logout_user()
            return redirect(url_for('.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function




# 2. Роуты аутентификации
@dmkod_bp.route('/login', methods=['GET', 'POST'])
def login():
    logging.debug(f"Entering login route. Method: {request.method}")
    if current_user.is_authenticated:
        logging.debug("User is already authenticated. Redirecting to dashboard.")
        return redirect(url_for('.dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        logging.debug("Login form validated successfully.")
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (form.username.data,))
            user_data = cur.fetchone()
        conn.close()

        # 1. Проверяем локального пользователя
        if not (user_data and user_data.get('password_hash') and checkpw(form.password.data.encode('utf-8'), user_data['password_hash'].encode('utf-8'))):
            logging.warning(f"Failed login attempt for user: {form.username.data}")
            flash('Неверное имя пользователя или пароль.', 'danger')
            return render_template('login.html', title='Вход', form=form)

        # 2. Если локальная проверка прошла, получаем API токен
        try:
            api_base_url = os.getenv('API_BASE_URL')
            # Убираем возможный слэш в конце и корректно соединяем URL
            token_url = f"{api_base_url.rstrip('/')}/user/token"
            api_credentials = {
                "email": os.getenv('API_EMAIL'),
                "password": os.getenv('API_PASSWORD')
            }
            # Используем GET и передаем данные в теле запроса как JSON.
            # Это нестандартный способ, но требуется для данного API.
            response = requests.get(token_url, json=api_credentials)
            response.raise_for_status()  # Вызовет ошибку, если статус ответа не 2xx

            tokens = response.json()
            session['api_access_token'] = tokens.get('access')
            session['api_refresh_token'] = tokens.get('refresh')

        except requests.exceptions.RequestException as e:
            logging.error(f"API authentication failed: {e}")
            flash(f'Ошибка аутентификации в API: {e}', 'danger')
            return render_template('login.html', title='Вход', form=form)

        # 3. Если все успешно, логиним пользователя в сессию Flask
        user = User(user_data)
        login_user(user, remember=form.remember.data)
        logging.info(f"User {user.username} logged in successfully.")
        next_page = request.args.get('next')
        flash('Вы успешно вошли в систему.', 'success')
        return redirect(next_page or url_for('.dashboard'))

    logging.debug("Rendering login page.")
    return render_template('dmkod_login.html', title='Вход', form=form)

@dmkod_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы успешно вышли из системы.', 'success')
    return redirect(url_for('.login'))

# 3. Основные роуты приложения
@dmkod_bp.route('/')
def index():
    """
    Главная страница, которая перенаправляет либо на вход, либо на панель управления.
    """
    logging.debug("Entering index route '/'.")
    if current_user.is_authenticated:
        logging.debug("User is authenticated, redirecting to dashboard.")
        return redirect(url_for('.dashboard'))
    logging.debug("User is not authenticated, redirecting to login.")
    return redirect(url_for('.login'))

@dmkod_bp.route('/dashboard')
@login_required
@api_token_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    PER_PAGE = 10
    offset = (page - 1) * PER_PAGE

    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Запрос для получения заказов для текущей страницы
        cur.execute("""
            SELECT 
                o.id, o.client_name, o.status, o.notes, o.created_at,
                pg.display_name as product_group_name
            FROM orders o
            LEFT JOIN dmkod_product_groups pg ON o.product_group_id = pg.id
            ORDER BY o.id DESC
            LIMIT %s OFFSET %s
        """, (PER_PAGE, offset))
        orders = cur.fetchall()

        # Запрос для получения общего количества заказов
        cur.execute("SELECT COUNT(id) AS total FROM orders")
        total_orders = cur.fetchone()['total']

    conn.close()
    total_pages = math.ceil(total_orders / PER_PAGE)

    return render_template('dmkod_index.html', 
                           orders=orders, 
                           title="Интеграция с ДМкод",
                           current_page=page, 
                           total_pages=total_pages)


# 4. Новый роут для справочника
@dmkod_bp.route('/participants')
@login_required
@api_token_required
def participants():
    """Страница для отображения списка клиентов из API."""
    participants_list = []
    access_token = session.get('api_access_token')
    if not access_token:
        flash('Токен API не найден. Пожалуйста, войдите заново.', 'warning')
        return redirect(url_for('.login'))

    try:
        api_base_url = os.getenv('API_BASE_URL')
        # Корректно формируем URL, убирая возможный слэш в конце
        participants_url = f"{api_base_url.rstrip('/')}/psp/participants"
        headers = {'Authorization': f'Bearer {access_token}'}
        response = requests.get(participants_url, headers=headers)
        response.raise_for_status()
        # Проверяем, есть ли что-то в ответе перед декодированием
        if response.text:
            # Извлекаем список участников из ключа 'participants' в ответе API
            data = response.json()
            participants_list = data.get('participants', [])
    except (requests.exceptions.RequestException, requests.exceptions.JSONDecodeError) as e:
        error_text = response.text if 'response' in locals() and response.text else str(e)
        flash(f'Не удалось получить список клиентов. Ошибка: {error_text}', 'danger')

    # Добавляем загрузку товарных групп из БД
    product_groups = []
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM dmkod_product_groups ORDER BY id")
            product_groups = cur.fetchall()
        conn.close()
    except Exception as e:
        flash(f'Не удалось загрузить справочник товарных групп: {e}', 'danger')

    return render_template('dmkod_participants.html', participants=participants_list, product_groups=product_groups, title="Справочники")

@dmkod_bp.route('/api_tester', methods=['GET', 'POST'])
@login_required
@api_token_required
def api_tester():
    """Страница для тестирования запросов к API ДМкод."""
    api_response = None
    api_base_url = os.getenv('API_BASE_URL', '').rstrip('/')
    
    if request.method == 'POST':
        endpoint = request.form.get('endpoint')
        method = request.form.get('method', 'GET').upper()
        body = request.form.get('body')
        
        access_token = session.get('api_access_token')
        if not access_token:
            flash('Токен API не найден. Пожалуйста, войдите заново.', 'warning')
            return redirect(url_for('.login'))
            
        full_url = f"{api_base_url}{endpoint}"
        headers = {'Authorization': f'Bearer {access_token}'}
        
        try:
            response = None
            request_kwargs = {'headers': headers, 'timeout': 30}
            
            # Добавляем тело запроса, если оно есть, для всех методов
            if body:
                request_kwargs['json'] = json.loads(body)
                
            if method == 'GET':
                response = requests.get(full_url, **request_kwargs)
            elif method == 'POST':
                response = requests.post(full_url, **request_kwargs)
            
            response.raise_for_status()
            
            api_response = {
                'status_code': response.status_code,
                'headers': json.dumps(dict(response.headers), indent=2, ensure_ascii=False),
                'body': json.dumps(response.json(), indent=2, ensure_ascii=False) if response.text else ''
            }
        except requests.exceptions.RequestException as e:
            # Более подробный вывод ошибки сети
            flash(f'Ошибка сети при выполнении запроса к {full_url}: {e}', 'danger')
        except json.JSONDecodeError as e:
            # Ошибка парсинга JSON
            flash(f'Ошибка декодирования JSON ответа: {e}. Сырой ответ: {response.text if "response" in locals() else "нет ответа"}', 'danger')


    return render_template('dmkod_api_tester.html', title="Тестировщик API", api_response=api_response, base_url=api_base_url)

@dmkod_bp.route('/integration/new', methods=['GET', 'POST'])
@login_required
@api_token_required
def create_integration():
    """Страница создания новой интеграции."""
    form = IntegrationForm()
    conn = get_db_connection()

    # --- Заполняем выпадающие списки ---
    # 1. Клиенты из API
    participants_list = []
    access_token = session.get('api_access_token')
    if access_token:
        try:
            api_base_url = os.getenv('API_BASE_URL')
            participants_url = f"{api_base_url.rstrip('/')}/psp/participants"
            headers = {'Authorization': f'Bearer {access_token}'}
            response = requests.get(participants_url, headers=headers)
            response.raise_for_status()
            if response.text:
                participants_list = response.json().get('participants', [])
        except (requests.exceptions.RequestException, requests.exceptions.JSONDecodeError) as e:
            flash(f'Не удалось загрузить список клиентов из API: {e}', 'warning')
    form.client_id.choices = [(p['id'], p['name']) for p in participants_list]

    # 2. Товарные группы из нашей БД
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, display_name, fias_required FROM dmkod_product_groups ORDER BY display_name")
        product_groups = cur.fetchall()
    # Передаем полный список в шаблон для JS, а в форму только id и имя
    form.product_group_id.choices = [(pg['id'], pg['display_name']) for pg in product_groups]    
    # --- Конец заполнения списков ---

    if form.validate_on_submit():
        fias_code = form.fias_code.data
        selected_pg_id = form.product_group_id.data
        
        # --- Дополнительная валидация для ФИАС ---
        fias_is_required = False
        for pg in product_groups:
            if pg['id'] == selected_pg_id and pg['fias_required']:
                fias_is_required = True
                break
        
        if fias_is_required and not fias_code:
            # Если ФИАС нужен, но не предоставлен, возвращаем ошибку
            flash('Для выбранной товарной группы необходимо указать код ФИАС.', 'danger')
            # Перезагружаем шаблон с уже введенными данными
            return render_template('dmkod_create_integration.html', title="Создание новой интеграции", form=form, product_groups_data=product_groups)
        # --- Конец валидации ---

        try:
            # Получаем имя клиента по ID
            client_name = dict(form.client_id.choices).get(form.client_id.data)

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Создаем запись в таблице 'orders'
                cur.execute(
                    """
                    INSERT INTO orders (client_name, status, notes, fias_code, participant_id, product_group_id) 
                    VALUES (%s, %s, %s, %s, %s, %s) 
                    RETURNING id
                    """,
                    (client_name, 'dmkod', form.notes.data, fias_code, form.client_id.data, selected_pg_id)
                )
                order_id = cur.fetchone()['id']

                # 2. Сохраняем файл в 'dmkod_order_files'
                file = form.xls_file.data
                cur.execute(
                    "INSERT INTO dmkod_order_files (order_id, filename, file_data) VALUES (%s, %s, %s)",
                    (order_id, file.filename, file.read())
                )

                # 3. Обрабатываем и сохраняем файл с детализацией, если он есть
                details_file = form.details_file.data
                if details_file:
                    try:
                        # Указываем dtype={'GTIN': str}, чтобы pandas не обрезал ведущие нули
                        df = pd.read_excel(details_file, dtype={'GTIN': str})
                        # Переименовываем колонки для удобства
                        df.rename(columns={
                            'GTIN': 'gtin',
                            'Кол-во': 'dm_quantity',
                            'Агрегация': 'aggregation_level',
                            'Дата производства': 'production_date',
                            'Срок годности': 'shelf_life_years',
                            'Окончание срока годности': 'expiry_date'
                        }, inplace=True)

                        details_to_insert = []
                        for index, row in df.iterrows():
                            prod_date = pd.to_datetime(row.get('production_date'), errors='coerce')
                            exp_date = pd.to_datetime(row.get('expiry_date'), errors='coerce')
                            shelf_life = pd.to_numeric(row.get('shelf_life_years'), errors='coerce')

                            # Логика расчета срока годности
                            if pd.notna(prod_date) and pd.notna(shelf_life) and pd.isna(exp_date):
                                exp_date = prod_date + relativedelta(years=int(shelf_life))

                            details_to_insert.append((
                                order_id,
                                str(row.get('gtin', '')),
                                int(row.get('dm_quantity', 0)),
                                int(row.get('aggregation_level', 0)),
                                None if pd.isna(prod_date) else prod_date.date(),
                                None if pd.isna(exp_date) else exp_date.date()
                            ))
                        
                        # Массовая вставка в dmkod_aggregation_details
                        if details_to_insert:
                            insert_query = sql.SQL("""
                                INSERT INTO dmkod_aggregation_details (order_id, gtin, dm_quantity, aggregation_level, production_date, expiry_date) 
                                VALUES {}
                            """).format(sql.SQL(',').join(map(sql.Literal, details_to_insert)))
                            cur.execute(insert_query)

                    except Exception as e:
                        raise Exception(f"Ошибка при обработке файла детализации: {e}")

            conn.commit()
            flash(f'Интеграция #{order_id} для клиента "{client_name}" успешно создана.', 'success')
            return redirect(url_for('.dashboard'))
        except Exception as e:
            conn.rollback()
            flash(f'Произошла ошибка при создании интеграции: {e}', 'danger')
        finally:
            conn.close()
    
    conn.close() # Закрываем соединение, если форма не валидна
    return render_template('dmkod_create_integration.html', title="Создание новой интеграции", form=form, product_groups_data=product_groups)

@dmkod_bp.route('/integration/<int:order_id>/edit', methods=['GET', 'POST'])
@login_required
@api_token_required
def edit_integration(order_id):
    conn = get_db_connection()
    try:
        if request.method == 'POST':
            action = request.form.get('action')

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if action == 'delete_details':
                    cur.execute("DELETE FROM dmkod_aggregation_details WHERE order_id = %s", (order_id,))
                    flash('Все записи детализации для этого заказа были удалены.', 'success')

                elif action == 'replace_details':
                    details_file = request.files.get('details_file')
                    if not details_file:
                        flash('Файл для замены не был выбран.', 'danger')
                    else:
                        # 1. Сначала удаляем старые записи
                        cur.execute("DELETE FROM dmkod_aggregation_details WHERE order_id = %s", (order_id,))
                        
                        # 2. Затем загружаем новые (логика скопирована из create_integration)
                        # Указываем dtype={'GTIN': str}, чтобы pandas не обрезал ведущие нули
                        df = pd.read_excel(details_file, dtype={'GTIN': str})
                        df.rename(columns={
                            'GTIN': 'gtin', 'Кол-во': 'dm_quantity', 'Агрегация': 'aggregation_level',
                            'Дата производства': 'production_date', 'Срок годности': 'shelf_life_years',
                            'Окончание срока годности': 'expiry_date'
                        }, inplace=True)

                        details_to_insert = []
                        for index, row in df.iterrows():
                            prod_date = pd.to_datetime(row.get('production_date'), errors='coerce')
                            exp_date = pd.to_datetime(row.get('expiry_date'), errors='coerce')
                            shelf_life = pd.to_numeric(row.get('shelf_life_years'), errors='coerce')

                            if pd.notna(prod_date) and pd.notna(shelf_life) and pd.isna(exp_date):
                                exp_date = prod_date + relativedelta(years=int(shelf_life))

                            details_to_insert.append((
                                order_id, str(row.get('gtin', '')), int(row.get('dm_quantity', 0)),
                                int(row.get('aggregation_level', 0)),
                                None if pd.isna(prod_date) else prod_date.date(),
                                None if pd.isna(exp_date) else exp_date.date()
                            ))
                        
                        if details_to_insert:
                            insert_query = sql.SQL("""
                                INSERT INTO dmkod_aggregation_details (order_id, gtin, dm_quantity, aggregation_level, production_date, expiry_date) 
                                VALUES {}
                            """).format(sql.SQL(',').join(map(sql.Literal, details_to_insert)))
                            cur.execute(insert_query)
                        
                        flash(f'Детализация заказа успешно заменена. Загружено {len(details_to_insert)} строк.', 'success')

                elif action == 'save_table_changes':
                    updates = []
                    for key, value in request.form.items():
                        # Ищем ключи вида "gtin-123", "dm_quantity-123" и т.д.
                        if '-' in key:
                            try:
                                field, detail_id_str = key.split('-', 1)
                            except ValueError:
                                continue # Пропускаем ключи, которые не соответствуют формату

                            if detail_id_str.isdigit():
                                detail_id = int(detail_id_str)
                                # Собираем все изменения для одной строки
                                found = False
                                for u in updates:
                                    if u['id'] == detail_id:
                                        u[field] = value # Сохраняем как есть, пустая строка будет обработана ниже
                                        found = True
                                        break
                                if not found:
                                    updates.append({'id': detail_id, field: value})
                    
                    # Применяем изменения к базе данных
                    for update_data in updates:
                        detail_id = update_data.pop('id')
                        # Формируем SQL-запрос динамически
                        set_clauses = [sql.SQL("{} = %s").format(sql.Identifier(key)) for key in update_data.keys()]
                        values = list(update_data.values())
                        # Заменяем пустые строки на None, чтобы в БД не попадали пустые значения
                        # для числовых или датовых полей.
                        processed_values = [v if v != '' else None for v in values]
                        processed_values.append(detail_id)
                        
                        query = sql.SQL("UPDATE dmkod_aggregation_details SET {} WHERE id = %s").format(sql.SQL(', ').join(set_clauses))
                        cur.execute(query, processed_values)
                    
                    flash(f'Изменения в {len(updates)} строках успешно сохранены.', 'success')

                elif action == 'upload_delta_result':
                    delta_file = request.files.get('delta_file')
                    if not delta_file:
                        flash('Файл с результатами от "Дельта" не был выбран.', 'danger')
                    else:
                        try:
                            # Читаем содержимое файла
                            file_content = delta_file.read()
                            # Пытаемся декодировать как JSON
                            codes_data = json.loads(file_content)

                            # Получаем ID тиража и ID загрузки из формы
                            printrun_id = request.form.get('printrun_id', type=int)
                            utilisation_upload_id = request.form.get('utilisation_upload_id', type=int)

                            # Сохраняем в новую таблицу delta_result
                            cur.execute(
                                """
                                INSERT INTO delta_result (order_id, printrun_id, utilisation_upload_id, codes_json)
                                VALUES (%s, %s, %s, %s)
                                """,
                                (order_id, printrun_id, utilisation_upload_id, json.dumps(codes_data))
                            )
                            flash('Результаты из "Дельта" успешно загружены и сохранены.', 'success')

                        except json.JSONDecodeError:
                            flash('Ошибка: загруженный файл не является корректным JSON.', 'danger')
                        except Exception as e:
                            # Откатываем транзакцию в случае другой ошибки
                            conn.rollback()
                            flash(f'Произошла ошибка при обработке файла: {e}', 'danger')

                elif action == 'upload_delta_csv':
                    delta_csv_file = request.files.get('delta_csv_file')
                    if not delta_csv_file:
                        flash('CSV-файл с результатами "Дельта" не был выбран.', 'danger')
                        return redirect(url_for('.edit_integration', order_id=order_id))

                    try:
                        # 1. Валидация имени файла
                        expected_filename_pattern = f"order_{order_id}.csv"
                        if expected_filename_pattern not in delta_csv_file.filename:
                            flash(f'Ошибка: Имя файла должно содержать "{expected_filename_pattern}", но получено "{delta_csv_file.filename}".', 'danger')
                            return redirect(url_for('.edit_integration', order_id=order_id))

                        logging.info(f"[Delta CSV] Начало обработки файла '{delta_csv_file.filename}' для заказа #{order_id}.")
                        # Читаем CSV-файл с помощью pandas
                        # Используем BytesIO для обработки потока файла и указываем кодировку/разделитель
                        file_content = delta_csv_file.read().decode('utf-8')
                        # --- КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: Сразу указываем pandas читать SSCC как строки ---
                        # Это предотвращает их автоматическое преобразование в числа (float) и потерю точности.
                        df = pd.read_csv(StringIO(file_content), sep='\t', dtype={'Barcode': str, 
                                                                                  'BoxSSCC': str, 
                                                                                  'PaletSSCC': str})
                        df.columns = df.columns.str.strip() # Очищаем пробелы в заголовках

                        # Проверяем наличие обязательных колонок
                        required_columns = ['DataMatrix', 'Barcode', 'StartDate', 'EndDate', 'BoxSSCC', 'PaletSSCC']
                        if not all(col in df.columns for col in required_columns):
                            flash(f'Ошибка: В файле отсутствуют необходимые колонки. Ожидаются: {", ".join(required_columns)}.', 'danger')
                            return redirect(url_for('.edit_integration', order_id=order_id))
                        
                        # --- ИСПРАВЛЕННЫЙ БЛОК: Нормализация SSCC ---
                        # Принудительно преобразуем колонки в строковый тип (StringDtype), чтобы избежать ошибки
                        # Берем последние 18 символов, чтобы отсечь возможные префиксы от сканера.
                        df['BoxSSCC'] = df['BoxSSCC'].str[-18:]
                        df['PaletSSCC'] = df['PaletSSCC'].str[-18:]
                        # Преобразуем даты в нужный формат
                        df['StartDate'] = pd.to_datetime(df['StartDate'], format='%Y-%m-%d').dt.strftime('%Y-%m-%d')
                        df['EndDate'] = pd.to_datetime(df['EndDate'], format='%Y-%m-%d').dt.strftime('%Y-%m-%d')

                        # --- НОВЫЙ БЛОК: Создание записей в 'packages' ---
                        # 1. Собираем все уникальные SSCC коробов и паллет
                        unique_boxes = df[['BoxSSCC']].dropna().drop_duplicates().rename(columns={'BoxSSCC': 'sscc'})
                        unique_pallets = df[['PaletSSCC']].dropna().drop_duplicates().rename(columns={'PaletSSCC': 'sscc'})

                        logging.info(f"[Delta CSV] Найдено {len(unique_boxes)} уникальных коробов и {len(unique_pallets)} уникальных паллет в файле.")
                        packages_to_insert = []
                        # --- ИСПРАВЛЕНИЕ: Добавляем 'level' сразу при создании ---
                        if not unique_boxes.empty:
                            unique_boxes_with_level = unique_boxes.copy()
                            unique_boxes_with_level['level'] = 1
                            packages_to_insert.append(unique_boxes_with_level)

                        if not unique_pallets.empty:
                            unique_pallets_with_level = unique_pallets.copy()
                            unique_pallets_with_level['level'] = 2
                            packages_to_insert.append(unique_pallets_with_level)
                        
                        if packages_to_insert:
                            all_packages_df = pd.concat(packages_to_insert, ignore_index=True)
                            all_packages_df['owner'] = 'delta' # Указываем владельца
                            logging.info(f"[Delta CSV] Всего подготовлено к обработке {len(all_packages_df)} упаковок (короба и паллеты).")
                            all_packages_df['parent_id'] = None # Родители будут определены на след. шаге

                            # 2. Определяем связи "короб-паллета"
                            box_pallet_map = df[['BoxSSCC', 'PaletSSCC']].dropna().drop_duplicates()
                            
                            # Создаем словарь {pallet_sscc: pallet_id}
                            # Мы не знаем ID паллет заранее, поэтому используем сами SSCC как временные ID
                            
                            # Создаем словарь {box_sscc: pallet_sscc}
                            box_to_pallet_sscc_map = pd.Series(box_pallet_map.PaletSSCC.values, index=box_pallet_map.BoxSSCC).to_dict()

                            # Функция для поиска parent_id (SSCC паллеты)
                            def find_parent_sscc(row):
                                if row['level'] == 1:
                                    return box_to_pallet_sscc_map.get(row['sscc'])
                                return None

                            all_packages_df['parent_sscc'] = all_packages_df.apply(find_parent_sscc, axis=1)

                            # --- НОВАЯ ЛОГИКА: Блокировка таблицы и получение ID перед вставкой ---
                            # Это решает проблему с гонкой состояний и ошибкой UniqueViolation.
                            packages_table_name = os.getenv('TABLE_PACKAGES', 'packages')
                            sequence_name = f"{packages_table_name}_id_seq"
                            num_packages = len(all_packages_df)

                            # 1. Блокируем таблицу в эксклюзивном режиме на время транзакции
                            logging.info(f"[Delta CSV] Блокирую таблицу '{packages_table_name}' для безопасной вставки...")
                            cur.execute(sql.SQL("LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE").format(table=sql.Identifier(packages_table_name)))

                            # --- НОВЫЙ ШАГ: Синхронизация счетчика перед получением ID ---
                            # Это гарантирует, что sequence не отстает от реальных данных в таблице,
                            # решая проблему UniqueViolation при конкурентном доступе.
                            logging.info(f"[Delta CSV] Синхронизирую счетчик '{sequence_name}'...")
                            cur.execute(sql.SQL("SELECT setval('{seq}', (SELECT MAX(id) FROM {tbl}), true)").format(
                                seq=sql.Identifier(sequence_name),
                                tbl=sql.Identifier(packages_table_name)
                            ))

                            # 2. Получаем блок ID из последовательности
                            logging.info(f"[Delta CSV] Резервирую {num_packages} ID из последовательности '{sequence_name}'...")
                            cur.execute(sql.SQL("SELECT nextval('{seq}') FROM generate_series(1, %s)").format(seq=sql.Identifier(sequence_name)), (num_packages,))
                            # --- ИСПРАВЛЕНИЕ: Используем доступ по ключу 'nextval' для RealDictCursor ---
                            # Вместо row[0] используем row['nextval'], так как курсор возвращает словари.
                            new_ids = [row['nextval'] for row in cur.fetchall()]

                            # 3. Присваиваем ID нашему DataFrame
                            all_packages_df['id'] = new_ids
                            logging.info("[Delta CSV] ID успешно присвоены данным в памяти.")

                            # 4. Выполняем обычную массовую вставку (не UPSERT)
                            from psycopg2.extras import execute_values
                            columns = all_packages_df.columns.tolist()
                            data_tuples = [tuple(x) for x in all_packages_df.to_numpy()]
                            
                            insert_query = sql.SQL("INSERT INTO {table} ({cols}) VALUES %s").format(
                                table=sql.Identifier(packages_table_name),
                                cols=sql.SQL(', ').join(map(sql.Identifier, columns))
                            )
                            
                            logging.info(f"[Delta CSV] Выполняю массовую вставку {len(data_tuples)} записей в '{packages_table_name}'...")
                            execute_values(cur, insert_query, data_tuples, page_size=1000)
                            flash(f"Создано {len(all_packages_df)} упаковок (короба и паллеты).", 'info')
                            logging.info(f"[Delta CSV] Вставка упаковок завершена. Блокировка будет снята после коммита.")
                            
                            # --- НОВЫЙ БЛОК: Обновление parent_id ---
                            # После того как все короба и паллеты вставлены,
                            # мы можем обновить parent_id для коробов, используя parent_sscc.
                            update_parent_id_query = sql.SQL("""
                                UPDATE {packages_table} p_child
                                SET parent_id = p_parent.id
                                FROM {packages_table} AS p_parent
                                WHERE p_child.parent_sscc = p_parent.sscc
                                  AND p_child.parent_sscc IS NOT NULL
                                  AND p_child.parent_id IS NULL;
                            """).format(packages_table=sql.Identifier(packages_table_name))
                            
                            logging.info("[Delta CSV] Выполняю запрос на обновление parent_id для связки коробов и паллет...")
                            cur.execute(update_parent_id_query)
                            updated_parents_count = cur.rowcount
                            flash(f"Связи 'короб-паллета' обновлены для {updated_parents_count} записей.", 'info')
                            logging.info(f"[Delta CSV] Обновлено {updated_parents_count} связей parent_id.")
                            
                            # Очищаем временное поле parent_sscc
                            if updated_parents_count > 0:
                                cleanup_query = sql.SQL("""
                                    UPDATE {packages_table} SET parent_sscc = NULL WHERE parent_sscc IS NOT NULL;
                                """).format(packages_table=sql.Identifier(packages_table_name))
                                cur.execute(cleanup_query)
                                flash("Временные данные по связям очищены.", 'info')

                        # --- КОНЕЦ НОВОГО БЛОКА ---

                        # --- НОВЫЙ БЛОК: Создание и загрузка записей в 'items' ---
                        logging.info("[Delta CSV] Начинаю подготовку данных для таблицы 'items'.")
                        # 1. Получаем карту {sscc: id} для только что созданных коробов
                        packages_table_name = os.getenv('TABLE_PACKAGES', 'packages')
                        box_ssccs_tuple = tuple(df['BoxSSCC'].dropna().unique())
                        # --- ИСПРАВЛЕНИЕ: Выполняем запрос, только если есть короба ---
                        sscc_to_id_map = {}
                        if box_ssccs_tuple:
                            cur.execute(
                                sql.SQL("SELECT sscc, id FROM {table} WHERE sscc IN %s").format(
                                    table=sql.Identifier(packages_table_name)
                                ),
                                (box_ssccs_tuple,)
                            )
                            sscc_to_id_map = {row['sscc']: row['id'] for row in cur.fetchall()}

                        logging.info(f"[Delta CSV] Создана карта SSCC->ID для {len(sscc_to_id_map)} коробов.")

                        # 2. Создаем DataFrame для 'items' с использованием корректного парсера
                        parsed_dm_data = [parse_datamatrix(dm) for dm in df['DataMatrix']]
                        items_df = pd.DataFrame(parsed_dm_data)
                        
                        # Добавляем остальные нужные колонки
                        items_df['order_id'] = order_id
                        # BoxSSCC нужен для маппинга, берем его из исходного DataFrame
                        items_df['BoxSSCC'] = df['BoxSSCC']
                        
                        # 3. Связываем с 'packages' через package_id
                        if sscc_to_id_map:
                            items_df['package_id'] = items_df['BoxSSCC'].map(sscc_to_id_map)
                        else:
                            items_df['package_id'] = None

                        # --- ИСПРАВЛЕНИЕ: Заменяем NaN на None перед загрузкой в БД ---
                        # Это предотвращает ошибку 'integer out of range' для кодов без короба.
                        items_df['package_id'] = items_df['package_id'].astype('object').where(pd.notna(items_df['package_id']), None)
                        
                        # 4. Убираем временные колонки и загружаем в БД
                        # --- ИСПРАВЛЕНО: Добавлены недостающие колонки crypto_part ---
                        columns_to_save = ['datamatrix', 'gtin', 'serial', 
                                           'crypto_part_91', 'crypto_part_92', 'crypto_part_93', 
                                           'order_id', 'package_id']
                        items_to_upload = items_df[columns_to_save]
                        
                        # Проверка на дубликаты в 'items' перед вставкой
                        from .utils import upsert_data_to_db
                        upsert_data_to_db(cur, 'TABLE_ITEMS', items_to_upload, 'datamatrix')
                        
                        total_codes_processed = len(items_to_upload)
                        unlinked_codes_count = items_to_upload['package_id'].isna().sum()
                        
                        logging.info(f"[Delta CSV] Всего обработано {total_codes_processed} кодов. Из них не связано с коробами: {unlinked_codes_count}.")
                        flash(f"Всего в систему загружено {total_codes_processed} кодов. Из них не связано с коробами: {unlinked_codes_count} шт.", 'success')

                        # --- ВОССТАНОВЛЕННЫЙ БЛОК: Сохранение результатов в delta_result ---
                        # --- НОВАЯ ЛОГИКА: Группировка по printrun_id и дате производства ---
                        df_for_json = df.copy()
                        df_for_json.rename(columns={'Barcode': 'gtin', 'StartDate': 'production_date', 'EndDate': 'expiration_date'}, inplace=True)

                        # 1. Получаем карту {gtin: printrun_id} из деталей заказа
                        cur.execute(
                            "SELECT gtin, api_id FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL",
                            (order_id,)
                        )
                        gtin_to_printrun_map = {row['gtin']: row['api_id'] for row in cur.fetchall()}
                        if not gtin_to_printrun_map:
                            raise Exception("Не удалось найти ID тиражей (api_id) в деталях заказа. Убедитесь, что тиражи созданы в API.")

                        # 2. Добавляем printrun_id в DataFrame
                        df_for_json['printrun_id'] = df_for_json['gtin'].map(gtin_to_printrun_map)

                        # 3. Группируем по printrun_id, дате производства и сроку годности
                        grouped_for_api = df_for_json.groupby(['printrun_id', 'production_date', 'expiration_date'])['DataMatrix'].apply(list).reset_index()

                        # 4. Формируем DataFrame для upsert
                        def create_payload(row):
                            # --- ИЗМЕНЕНО: Формируем структуру JSON согласно новому требованию ---
                            cleaned_codes = [code.replace(GS_SEPARATOR, '') for code in row['DataMatrix']]
                            payload = {
                                "include": [{"code": c} for c in cleaned_codes],
                                "attributes": {
                                    "production_date": str(row['production_date']),
                                    "expiration_date": str(row['expiration_date'])
                                }
                            }
                            return json.dumps(payload)

                        grouped_for_api['codes_json'] = grouped_for_api.apply(create_payload, axis=1)
                        grouped_for_api['order_id'] = order_id
                        # Преобразуем printrun_id в integer для корректной вставки
                        grouped_for_api['printrun_id'] = grouped_for_api['printrun_id'].astype(int)
                        grouped_for_api['production_date'] = pd.to_datetime(grouped_for_api['production_date']).dt.date

                        # 5. Выбираем колонки и выполняем upsert
                        delta_result_df = grouped_for_api[['order_id', 'printrun_id', 'production_date', 'codes_json']]

                        # Используем upsert для атомарного добавления/обновления
                        from .utils import upsert_data_to_db
                        upsert_data_to_db(cur, 'TABLE_DELTA_RESULT', delta_result_df, ['order_id', 'printrun_id', 'production_date'])

                        flash('Результаты из CSV-файла "Дельта" успешно сохранены для дальнейшей отправки в API.', 'success')

                    except Exception as e:
                        logging.error(f"Ошибка при обработке CSV-файла 'Дельта' для заказа #{order_id}", exc_info=True)
                        flash(f'Произошла критическая ошибка при обработке файла: {e}', 'danger')
                        return redirect(url_for('.edit_integration', order_id=order_id))

            conn.commit()
            return redirect(url_for('.edit_integration', order_id=order_id))

        # --- GET-запрос: загрузка данных для отображения ---
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Основная информация о заказе
            cur.execute("""
                SELECT o.*, pg.display_name as product_group_name
                FROM orders o
                LEFT JOIN dmkod_product_groups pg ON o.product_group_id = pg.id
                WHERE o.id = %s
            """, (order_id,))
            order = cur.fetchone()

            if not order:
                flash(f'Заказ с ID {order_id} не найден.', 'danger')
                return redirect(url_for('.dashboard'))

            # Проверяем, можно ли редактировать этот заказ (разрешаем dmkod и delta)
            if order['status'] not in ('dmkod', 'delta'):
                flash(f'Заказ №{order_id} имеет статус "{order["status"]}" и не может быть отредактирован.', 'warning')
                return redirect(url_for('.dashboard'))

            # 2. Информация об оригинальном файле
            cur.execute("SELECT id, filename FROM dmkod_order_files WHERE order_id = %s LIMIT 1", (order_id,))
            original_file = cur.fetchone()

            # 3. Детализация заказа
            cur.execute("SELECT * FROM dmkod_aggregation_details WHERE order_id = %s ORDER BY id", (order_id,))
            details = cur.fetchall()

        # НАШ ДИАГНОСТИЧЕСКИЙ ЛОГ
        logging.info(f"--- [DIAGNOSTIC LOG] --- Attempting to render 'dmkod_edit_integration.html' for order_id={order_id}")

        return render_template('dmkod_edit_integration.html', 
                               title=f"Редактирование заказа №{order_id}",
                               order=order,
                               original_file=original_file,
                               details=details)
    except Exception as e:
        conn.rollback()
        logging.error(f"--- [DIAGNOSTIC LOG] --- Exception in edit_integration for order_id={order_id}", exc_info=True)
        flash(f'Произошла ошибка: {e}', 'danger')
        return redirect(url_for('.dashboard'))
    finally:
        conn.close()

@dmkod_bp.route('/integration/download_original/<int:file_id>')
@login_required
@api_token_required
def download_original_file(file_id):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT filename, file_data FROM dmkod_order_files WHERE id = %s", (file_id,))
            file_data = cur.fetchone()

        if not file_data:
            flash('Файл не найден.', 'danger')
            return redirect(request.referrer or url_for('.dashboard'))

        return send_file(
            BytesIO(file_data['file_data']),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=file_data['filename']
        )
    except Exception as e:
        flash(f'Ошибка при скачивании файла: {e}', 'danger')
        return redirect(request.referrer or url_for('.dashboard'))
    finally:
        conn.close()

# --- CRUD для товарных групп ---

@dmkod_bp.route('/product_group/new', methods=['GET', 'POST'])
@login_required
@api_token_required
def create_product_group():
    form = ProductGroupForm()
    if form.validate_on_submit():
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO dmkod_product_groups (group_name, display_name, code_template, dm_template, fias_required)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (form.group_name.data, form.display_name.data, form.code_template.data, form.dm_template.data, form.fias_required.data)
                )
            conn.commit()
            flash('Товарная группа успешно создана.', 'success')
            return redirect(url_for('.participants'))
        except Exception as e:
            conn.rollback()
            flash(f'Ошибка при создании группы: {e}', 'danger')
        finally:
            conn.close()
    return render_template('dmkod_product_group_form.html', form=form, title="Новая товарная группа")

@dmkod_bp.route('/product_group/<int:group_id>/edit', methods=['GET', 'POST'])
@login_required
@api_token_required
def edit_product_group(group_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM dmkod_product_groups WHERE id = %s", (group_id,))
        group = cur.fetchone()
    
    if not group:
        flash('Товарная группа не найдена.', 'danger')
        return redirect(url_for('.participants'))

    form = ProductGroupForm(data=group)

    if form.validate_on_submit():
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE dmkod_product_groups
                    SET group_name=%s, display_name=%s, code_template=%s, dm_template=%s, fias_required=%s
                    WHERE id=%s
                    """,
                    (form.group_name.data, form.display_name.data, form.code_template.data, form.dm_template.data, form.fias_required.data, group_id)
                )
            conn.commit()
            flash('Товарная группа успешно обновлена.', 'success')
            return redirect(url_for('.participants'))
        except Exception as e:
            conn.rollback()
            flash(f'Ошибка при обновлении группы: {e}', 'danger')
        finally:
            conn.close()
    else:
        conn.close() # Закрываем соединение, если это GET-запрос

    return render_template('dmkod_product_group_form.html', form=form, title="Редактирование товарной группы")

@dmkod_bp.route('/product_group/<int:group_id>/delete', methods=['POST'])
@login_required
@api_token_required
def delete_product_group(group_id):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM dmkod_product_groups WHERE id = %s", (group_id,))
    conn.commit()
    conn.close()
    flash('Товарная группа удалена.', 'success')
    return redirect(url_for('.participants'))

@dmkod_bp.route('/integration_panel', methods=['GET', 'POST'])
@login_required
@api_token_required
def integration_panel():
    """Страница 'Интеграция' с выбором заказа."""
    api_response = None
    selected_order = None
    selected_order_id = request.form.get('order_id', type=int) if request.method == 'POST' else request.args.get('order_id', type=int)

    conn = get_db_connection()
    try: # Этот try-блок теперь охватывает и GET, и POST
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, client_name, created_at, api_order_id, api_status FROM orders WHERE status IN ('dmkod', 'delta') ORDER BY id DESC")
            orders = cur.fetchall()

            # Если заказ выбран, загружаем его полную информацию
            if selected_order_id:
                cur.execute("SELECT * FROM orders WHERE id = %s", (selected_order_id,))
                selected_order = cur.fetchone()

        action = request.form.get('action')
        if action: # Все действия теперь внутри этого блока
            if not selected_order_id:
                flash('Пожалуйста, сначала выберите заказ.', 'warning')
                return redirect(url_for('.integration_panel'))
            
            if action == 'create_order':
                access_token = session.get('api_access_token')
                if not access_token:
                    flash('Токен API не найден. Пожалуйста, войдите заново.', 'warning')
                    return redirect(url_for('.login'))

                try:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        # 1. Получаем основную информацию о заказе и товарной группе
                        cur.execute("""
                            SELECT o.participant_id, o.notes, pg.dm_template
                            FROM orders o
                            JOIN dmkod_product_groups pg ON o.product_group_id = pg.id
                            WHERE o.id = %s
                        """, (selected_order_id,))
                        order_info = cur.fetchone()

                        if not order_info:
                            raise Exception("Не найдена информация о заказе или товарной группе.")

                        # 2. Агрегируем данные по продуктам
                        cur.execute("""
                            SELECT gtin, SUM(dm_quantity) as total_qty
                            FROM dmkod_aggregation_details
                            WHERE order_id = %s AND gtin IS NOT NULL AND gtin != ''
                            GROUP BY gtin
                        """, (selected_order_id,))
                        products_data = cur.fetchall()

                        if not products_data:
                            raise Exception("В заказе нет детализации по продуктам (GTIN) для отправки.")

                    # 3. Формируем тело запроса к API
                    products_payload = [
                        {
                            "gtin": p['gtin'],
                            "code_template": order_info['dm_template'],
                            "qty": int(p['total_qty']),
                            "unit_type": "UNIT",
                            "release_method": "IMPORT",
                            "payment_type": 2
                        } for p in products_data
                    ]
                    
                    api_payload = {
                        "participant_id": order_info['participant_id'],
                        "production_order_id": order_info['notes'] or "",
                        "contact_person": current_user.username,
                        "products": products_payload
                    }

                    # 4. Отправляем запрос к API
                    api_base_url = os.getenv('API_BASE_URL', '').rstrip('/')
                    full_url = f"{api_base_url}/psp/order/create"
                    headers = {'Authorization': f'Bearer {access_token}'}
                    
                    response = requests.post(full_url, headers=headers, json=api_payload, timeout=30)
                    response.raise_for_status()
                    
                    response_data = response.json()
                    api_order_id = response_data.get('order_id')

                    # 5. Обновляем наш заказ, записывая ID из API
                    if api_order_id:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE orders SET api_order_id = %s WHERE id = %s", (api_order_id, selected_order_id))
                        conn.commit()
                        flash(f'Заказ в API успешно создан с ID: {api_order_id}.', 'success')
                        # Перенаправляем, чтобы обновить состояние кнопок
                        return redirect(url_for('.integration_panel', order_id=selected_order_id))

                    api_response = {
                        'status_code': response.status_code,
                        'body': json.dumps(response_data, indent=2, ensure_ascii=False)
                    }

                except Exception as e:
                    conn.rollback()
                    error_body = ""
                    if 'response' in locals() and hasattr(response, 'text'):
                        error_body = response.text
                    api_response = {
                        'status_code': response.status_code if 'response' in locals() else 500,
                        'body': f"ОШИБКА: {e}\n\nОтвет сервера (если был):\n{error_body}"
                    }
            elif action == 'create_suborder_request':
                access_token = session.get('api_access_token')
                if not access_token:
                    flash('Токен API не найден. Пожалуйста, войдите заново.', 'warning')
                    return redirect(url_for('.login'))
                
                if not selected_order or not selected_order.get('api_order_id'):
                    flash('Сначала необходимо создать заказ в API (кнопка "Создать заказ").', 'danger')
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))

                try:
                    api_payload = { "order_id": int(selected_order['api_order_id']) }

                    # Отправляем запрос к API
                    api_base_url = os.getenv('API_BASE_URL', '').rstrip('/')
                    full_url = f"{api_base_url}/psp/suborders/create"
                    headers = {'Authorization': f'Bearer {access_token}'}
                    
                    response = requests.post(full_url, headers=headers, json=api_payload, timeout=30)
                    response.raise_for_status()
                    
                    response_data = response.json()

                    # --- ИСПРАВЛЕННАЯ ЛОГИКА ---
                    # Обновляем статус заказа в любом случае, если запрос прошел успешно (статус 2xx)
                    with conn.cursor() as cur:
                        cur.execute("UPDATE orders SET api_status = 'Запрос создан' WHERE id = %s", (selected_order_id,))
                    conn.commit()
                    flash('Запрос на получение кодов успешно отправлен. Статус заказа обновлен на "Запрос создан".', 'success')
                    # Перенаправляем, чтобы обновить состояние кнопок
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))

                except Exception as e:
                    conn.rollback()
                    error_body = response.text if 'response' in locals() and hasattr(response, 'text') else ""
                    api_response = {
                        'status_code': response.status_code if 'response' in locals() else 500,
                        'body': f"ОШИБКА: {e}\n\nОтвет сервера (если был):\n{error_body}"
                    }
                    return render_template('integration_panel.html', orders=orders, selected_order_id=selected_order_id, selected_order=selected_order, api_response=api_response, title="Интеграция")
            elif action == 'split_runs': # Полностью переписанная логика
                access_token = session.get('api_access_token')
                if not access_token:
                    flash('Токен API не найден. Пожалуйста, войдите заново.', 'warning')
                    return redirect(url_for('.login'))
    
                if not selected_order or not selected_order.get('api_order_id'):
                    flash('Сначала необходимо создать заказ в API.', 'danger')
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))
    
                try:
                    conn_local = get_db_connection()
                    api_base_url = os.getenv('API_BASE_URL', '').rstrip('/')
                    headers = {'Authorization': f'Bearer {access_token}'}
    
                    # --- Шаг 1: Собираем данные из нашей БД в DataFrame ---
                    with conn_local.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(
                            "SELECT id, gtin, dm_quantity, api_id FROM dmkod_aggregation_details WHERE order_id = %s",
                            (selected_order_id,)
                        )
                        details_data = cur.fetchall()
                        if not details_data:
                            raise Exception("В заказе нет детализации для создания тиражей.")
                    details_df = pd.DataFrame(details_data)
    
                    # --- Шаг 2: Получение деталей заказа из API и обогащение DataFrame ---
                    get_order_url = f"{api_base_url}/psp/orders"
                    get_order_payload = {"order_id": selected_order['api_order_id']}
                    response_get = requests.get(get_order_url, headers=headers, json=get_order_payload, timeout=30)
                    response_get.raise_for_status()
                    order_details_from_api = response_get.json()
    
                    if not order_details_from_api.get('orders'):
                        raise Exception("API не вернуло информацию о заказе.")
    
                    api_order_data = order_details_from_api['orders'][0]
                    api_products = api_order_data.get('products', [])
                    if not api_products:
                        raise Exception("API не вернуло список продуктов в заказе.")
    
                    # Создаем словарь для быстрого поиска api_product_id по gtin
                    # с учетом условий: state=ACTIVE и qty=qty_received
                    gtin_to_api_product_id = {}
                    for p in api_products:
                        if p.get('state') == 'ACTIVE' and p.get('qty') == p.get('qty_received'):
                            gtin_to_api_product_id[p['gtin']] = p['id']
                    details_df['api_product_id'] = details_df['gtin'].map(gtin_to_api_product_id)
    
                    # --- Шаг 3: Обновление/добавление названий товаров ---
                    products_to_upsert = [{'gtin': p['gtin'], 'name': p['name']} for p in api_products if p.get('name')]
                    if products_to_upsert:
                        with conn_local.cursor() as cur:
                            from .utils import upsert_data_to_db
                            upsert_df = pd.DataFrame(products_to_upsert)
                            upsert_data_to_db(cur, 'TABLE_PRODUCTS', upsert_df, 'gtin')
                        conn_local.commit()
    
                    # --- Шаг 4: Цикл создания тиражей и обновления api_id ---
                    create_tirage_url = f"{api_base_url}/psp/printrun/create"
                    get_tirages_url = f"{api_base_url}/psp/printruns"
                    user_logs = [f"Начинаю создание тиражей для {len(details_df)} позиций заказа."]
 
                    for i, row in details_df.iterrows():
                        api_product_id = row.get('api_product_id')
                        if pd.isna(api_product_id):
                            log_msg = f"Пропуск строки {i+1}/{len(details_df)} (gtin: {row['gtin']}), т.к. не найден api_product_id."
                            logging.warning(log_msg)
                            user_logs.append(log_msg)
                            continue
    
                        # Пропускаем строки, для которых тираж уже был создан ранее
                        if pd.notna(row.get('api_id')):
                            log_msg = f"Пропуск строки {i+1}/{len(details_df)} (gtin: {row['gtin']}), так как тираж (api_id: {row['api_id']}) уже существует."
                            user_logs.append(log_msg)
                            continue
 
                        user_logs.append(f"--- Создаю тираж {i+1}/{len(details_df)} ---")
                        tirage_payload = {
                            "order_product_id": int(api_product_id),
                            "qty": int(row['dm_quantity'])
                        }
                        user_logs.append(f"  GTIN: {row['gtin']}, Кол-во: {row['dm_quantity']}")
                        
                        # Отправляем POST на создание тиража
                        response_post = requests.post(create_tirage_url, headers=headers, json=tirage_payload, timeout=30)
                        response_post.raise_for_status()
                        response_data = response_post.json()
                        user_logs.append(f"  Запрос на создание тиража отправлен. Ответ: {response_post.status_code}, Тело: {json.dumps(response_data)}")
 
                        # --- НОВАЯ ЛОГИКА: Получаем ID тиража напрямую из ответа ---
                        new_printrun_id = response_data.get('printrun_id')
                        
                        if new_printrun_id:
                            user_logs.append(f"  Получен ID нового тиража: {new_printrun_id}")
                        else:
                            # Если ID не пришел, прерываем операцию с ошибкой
                            raise Exception(f"API не вернуло 'printrun_id' в ответе на создание тиража. Ответ: {json.dumps(response_data)}")
                        
                        # Обновляем поле api_id в нашей БД
                        with conn_local.cursor() as cur:
                            cur.execute(
                                "UPDATE dmkod_aggregation_details SET api_id = %s WHERE id = %s",
                                (new_printrun_id, row['id'])
                            )
                        # Обновляем DataFrame, чтобы на следующей итерации этот ID считался существующим
                        conn_local.commit()
                        user_logs.append(f"  ID тиража {new_printrun_id} присвоен позиции заказа (ID: {row['id']}) в базе данных.")
    
                    # --- Шаг 5: Обновление статуса заказа ---
                    with conn_local.cursor() as cur:
                        cur.execute("UPDATE orders SET api_status = 'Тиражи созданы' WHERE id = %s", (selected_order_id,))
                    conn_local.commit()
                    flash('Тиражи успешно созданы в API.', 'success')
                    # Перенаправляем, чтобы обновить состояние кнопок
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))
    
                    api_response = {
                        'status_code': 200,
                        'body': "\n".join(user_logs)
                    }
    
                except Exception as e:
                    if 'conn_local' in locals() and conn_local: conn_local.rollback()
                    error_body = ""
                    if 'response_post' in locals() and hasattr(response_post, 'text'):
                        error_body = response_post.text
                    elif 'response_get' in locals() and hasattr(response_get, 'text'):
                        error_body = response_get.text
                    
                    api_response = {
                        'status_code': 500,
                        'body': f"ОШИБКА: {e}\n\nОтвет сервера (если был):\n{error_body}"
                    }
                finally:
                    if 'conn_local' in locals() and conn_local: conn_local.close()
            
            elif action == 'prepare_json':
                access_token = session.get('api_access_token')
                if not access_token:
                    flash('Токен API не найден. Пожалуйста, войдите заново.', 'warning')
                    return redirect(url_for('.login'))

                if not selected_order or not selected_order.get('api_order_id'):
                    flash('Сначала необходимо создать заказ и разбить его на тиражи.', 'danger')
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))

                user_logs = []
                try:
                    conn_local = get_db_connection()
                    with conn_local.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(
                            "SELECT id, api_id, gtin FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL ORDER BY id",
                            (selected_order_id,)
                        )
                        details_to_process = cur.fetchall()

                    if not details_to_process:
                        raise Exception("Не найдено позиций с ID тиража (api_id) для обработки.")

                    api_base_url = os.getenv('API_BASE_URL', '').rstrip('/')
                    full_url = f"{api_base_url}/psp/printrun/json/create"
                    headers = {'Authorization': f'Bearer {access_token}'}
                    
                    import time # Импортируем модуль для задержки
                    user_logs.append(f"Найдено {len(details_to_process)} позиций для обработки.")

                    for i, detail in enumerate(details_to_process):
                        payload = {"printrun_id": detail['api_id']}
                        user_logs.append(f"--- {i+1}/{len(details_to_process)}: Отправка запроса для GTIN {detail['gtin']} (ID тиража: {detail['api_id']}) ---")
                        
                        response = requests.post(full_url, headers=headers, json=payload, timeout=30)
                        
                        user_logs.append(f"  URL: {full_url}")
                        user_logs.append(f"  Тело: {json.dumps(payload)}")
                        user_logs.append(f"  Статус ответа: {response.status_code}")
                        
                        response.raise_for_status() # Прервет выполнение, если статус не 2xx
                        
                        # Добавляем небольшую паузу, чтобы API успело обработать запрос
                        time.sleep(0.5) # Пауза в 0.5 секунды

                    # Обновляем статус заказа в нашей БД
                    with conn_local.cursor() as cur:
                        cur.execute(
                            "UPDATE orders SET api_status = 'JSON заказан' WHERE id = %s",
                            (selected_order_id,)
                        )
                    conn_local.commit()

                    flash('Операция "Подготовить JSON" успешно выполнена. Статус заказа обновлен на "JSON заказан".', 'success')
                    # Перенаправляем, чтобы обновить состояние кнопок
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))
                    api_response = {
                        'status_code': 200, # Используем 200, т.к. операция прошла успешно
                        'body': "\n".join(user_logs)
                    }

                except Exception as e:
                    error_body = response.text if 'response' in locals() and hasattr(response, 'text') else ""
                    user_logs.append(f"\n!!! ОШИБКА: {e}\nОтвет сервера (если был):\n{error_body}")
                    api_response = {'status_code': 500, 'body': "\n".join(user_logs)}
                finally:
                    if 'conn_local' in locals() and conn_local: conn_local.close()

            elif action == 'download_codes':
                access_token = session.get('api_access_token')
                if not access_token:
                    flash('Токен API не найден. Пожалуйста, войдите заново.', 'warning')
                    return redirect(url_for('.login'))

                if not selected_order or not selected_order.get('api_order_id'):
                    flash('Сначала необходимо создать заказ в API и разбить его на тиражи.', 'danger')
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))

                user_logs = []
                zip_buffer = BytesIO()
                
                try:
                    conn_local = get_db_connection()
                    with conn_local.cursor(cursor_factory=RealDictCursor) as cur:
                        # Получаем имя клиента для формирования имени файла
                        cur.execute("SELECT client_name FROM orders WHERE id = %s", (selected_order_id,))
                        client_name_row = cur.fetchone()
                        if not client_name_row:
                            raise Exception(f"Не удалось найти клиента для заказа ID {selected_order_id}.")
                        client_name = client_name_row['client_name'] # Получаем оригинальное имя клиента

                        # Получаем детализацию заказа с api_id
                        cur.execute(
                            "SELECT id, api_id, gtin FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL ORDER BY id",
                            (selected_order_id,)
                        )
                        details_to_process = cur.fetchall()

                    if not details_to_process:
                        raise Exception("Не найдено позиций с ID тиража (api_id) для скачивания кодов.")

                    api_base_url = os.getenv('API_BASE_URL', '').rstrip('/')
                    full_url = f"{api_base_url}/psp/printrun/json/download"
                    headers = {'Authorization': f'Bearer {access_token}'}
                    
                    user_logs.append(f"Найдено {len(details_to_process)} позиций для скачивания кодов.")

                    # Санитизируем имя клиента для использования в именах файлов
                    sanitized_client_name = _sanitize_filename_part(client_name)

                    with conn_local.cursor() as cur:
                        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                            for i, detail in enumerate(details_to_process):
                                payload = {"printrun_id": detail['api_id']}
                                user_logs.append(f"--- {i+1}/{len(details_to_process)}: Запрос кодов для GTIN {detail['gtin']} (ID тиража: {detail['api_id']}) ---")
                                
                                # Отправляем GET-запрос к API
                                response = requests.get(full_url, headers=headers, json=payload, timeout=60)
                                
                                user_logs.append(f"  URL: {full_url}")
                                user_logs.append(f"  Тело запроса: {json.dumps(payload)}")
                                user_logs.append(f"  Статус ответа: {response.status_code}")
                                
                                response.raise_for_status()
                                
                                response_data = response.json()
                                codes = response_data.get('codes', [])

                                if not codes:
                                    user_logs.append(f"  В ответе для тиража {detail['api_id']} не найдено кодов.")
                                    continue
                                
                                # Сохраняем JSON с кодами в dmkod_aggregation_details для текущей строки
                                cur.execute(
                                    "UPDATE dmkod_aggregation_details SET api_codes_json = %s WHERE id = %s",
                                    (json.dumps({'codes': codes}), detail['id'])
                                )
                                user_logs.append(f"  Сохранено {len(codes)} кодов в базу данных для строки ID {detail['id']}.")

                                # Формируем CSV-содержимое для ZIP-архива
                                csv_content = "\n".join(codes)
                                
                                csv_filename_parts = [f"{i+1}", f"{selected_order_id}"]
                                if sanitized_client_name:
                                    csv_filename_parts.append(sanitized_client_name)
                                csv_filename_parts.append(f"{len(codes)}")
                                filename = "_".join(csv_filename_parts) + ".csv"
                                
                                zf.writestr(filename, csv_content)
                                user_logs.append(f"  Создан файл '{filename}' с {len(codes)} кодами.")
                        
                        # Обновляем статус заказа в нашей БД
                        cur.execute(
                            "UPDATE orders SET api_status = 'Коды скачаны' WHERE id = %s",
                            (selected_order_id,)
                        )
                    conn_local.commit()
                    zip_buffer.seek(0) # Перематываем буфер в начало

                    # Формируем имя ZIP-файла, избегая лишнего подчеркивания
                    zip_download_name_parts = [f"codes_order_{selected_order_id}"]
                    if sanitized_client_name:
                        zip_download_name_parts.append(sanitized_client_name)
                    final_zip_download_name = "_".join(zip_download_name_parts) + ".zip"
                    
                    flash('Коды успешно скачаны и упакованы в ZIP-архив.', 'success')
                    
                    # Вместо прямой отправки файла, сохраняем его в сессии и делаем редирект
                    session['download_file'] = {'data': zip_buffer.getvalue(), 'name': final_zip_download_name, 'mimetype': 'application/zip'}
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))
                    
                except Exception as e:
                    if 'conn_local' in locals() and conn_local: conn_local.rollback() # Откатываем транзакцию при ошибке
                    error_body = response.text if 'response' in locals() and hasattr(response, 'text') else ""
                    user_logs.append(f"\n!!! ОШИБКА: {e}\nОтвет сервера (если был):\n{error_body}")
                    api_response = {'status_code': 500, 'body': "\n".join(user_logs)}
                finally:
                    if 'conn_local' in locals() and conn_local: conn_local.close()

            elif action == 'prepare_report_data':
                access_token = session.get('api_access_token')
                if not access_token:
                    flash('Токен API не найден. Пожалуйста, войдите заново.', 'warning')
                    return redirect(url_for('.login'))

                if not selected_order or not selected_order.get('api_order_id'):
                    flash('Сначала необходимо создать заказ в API и разбить его на тиражи.', 'danger')
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))

                # --- ИСПРАВЛЕННАЯ ЛОГИКА: Обработка статуса 'delta' ---
                if selected_order.get('status') == 'delta':
                    user_logs = []
                    conn_local = None # Инициализируем переменную
                    try:
                        conn_local = get_db_connection()
                        with conn_local.cursor(cursor_factory=RealDictCursor) as cur:
                            # 1. Получаем все необработанные записи из delta_result
                            cur.execute(
                                "SELECT id, codes_json FROM delta_result WHERE order_id = %s AND utilisation_upload_id IS NULL",
                                (selected_order_id,)
                            )
                            results_to_process = cur.fetchall()

                        if not results_to_process:
                            flash("Нет новых данных от 'Дельта' для подготовки сведений.", 'info')
                            return redirect(url_for('.integration_panel', order_id=selected_order_id))

                        api_base_url = os.getenv('API_BASE_URL', '').rstrip('/')
                        full_url = f"{api_base_url}/psp/utilisation/upload"
                        headers = {'Authorization': f'Bearer {access_token}'}
                        user_logs.append(f"Найдено {len(results_to_process)} записей от 'Дельта' для обработки.")
                        
                        updated_count = 0
                        with conn_local.cursor() as cur:
                            for i, result in enumerate(results_to_process):
                                payload = result['codes_json'] # JSON уже готов
                                user_logs.append(f"--- {i+1}/{len(results_to_process)}: Отправка данных для записи ID {result['id']} ---")
                                
                                response = requests.post(full_url, headers=headers, json=payload, timeout=120)
                                user_logs.append(f"  Статус ответа: {response.status_code}")
                                response.raise_for_status()

                                response_data = response.json()
                                utilisation_upload_id = response_data.get('utilisation_upload_id')

                                if not utilisation_upload_id:
                                    raise Exception(f"API не вернуло 'utilisation_upload_id' в ответе. Ответ: {json.dumps(response_data)}")

                                # Обновляем запись в delta_result
                                cur.execute(
                                    "UPDATE delta_result SET utilisation_upload_id = %s WHERE id = %s",
                                    (utilisation_upload_id, result['id'])
                                )
                                user_logs.append(f"  Записи ID {result['id']} присвоен utilisation_upload_id: {utilisation_upload_id}")
                                updated_count += 1
                        
                        conn_local.commit()
                        # --- ДОБАВЛЕНО: Обновляем api_status заказа ---
                        with conn_local.cursor() as cur:
                            cur.execute("UPDATE orders SET api_status = 'Сведения подготовлены' WHERE id = %s", (selected_order_id,))
                        conn_local.commit()
                        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                        flash(f'Успешно обработано {updated_count} записей. Сведения подготовлены.', 'success')
                        return redirect(url_for('.integration_panel', order_id=selected_order_id))

                    except Exception as e:
                        if conn_local: conn_local.rollback()
                        error_body = response.text if 'response' in locals() and hasattr(response, 'text') else ""
                        user_logs.append(f"\n!!! ОШИБКА: {e}\nОтвет сервера (если был):\n{error_body}")
                        api_response = {'status_code': 500, 'body': "\n".join(user_logs)}
                    finally:
                        if conn_local: conn_local.close()
                    # ВАЖНО: Завершаем выполнение здесь, чтобы не провалиться в старую логику
                    return render_template('integration_panel.html', orders=orders, selected_order_id=selected_order_id, selected_order=selected_order, api_response=api_response, title="Интеграция")

                user_logs = []
                try:
                    conn_local = get_db_connection()
                    with conn_local.cursor() as cur:
                        # Получаем все необходимые данные одним запросом, объединяя таблицы
                        cur.execute(
                            """
                            SELECT 
                                d.api_id, d.gtin, d.production_date, d.expiry_date,
                                o.fias_code
                            FROM dmkod_aggregation_details d
                            JOIN orders o ON d.order_id = o.id
                            WHERE d.order_id = %s AND d.api_id IS NOT NULL 
                            ORDER BY d.id
                            """,
                            (selected_order_id,)
                        )
                        details_to_process = cur.fetchall()

                    if not details_to_process:
                        raise Exception("Не найдено позиций с ID тиража (api_id) для обработки.")

                    api_base_url = os.getenv('API_BASE_URL', '').rstrip('/')
                    full_url = f"{api_base_url}/psp/utilisation/upload"
                    headers = {'Authorization': f'Bearer {access_token}'}
                    
                    user_logs.append(f"Найдено {len(details_to_process)} позиций для подготовки сведений.")

                    for i, detail in enumerate(details_to_process):
                        attributes = {}
                        if detail.get('production_date'):
                            attributes['production_date'] = detail['production_date'].strftime('%Y-%m-%d')
                        if detail.get('expiry_date'):
                            attributes['expiration_date'] = detail['expiry_date'].strftime('%Y-%m-%d')
                        if detail.get('fias_code'):
                            attributes['fias_id'] = detail['fias_code']

                        payload = {"all_from_printrun": detail['api_id']}
                        if attributes:
                            payload['attributes'] = attributes

                        user_logs.append(f"--- {i+1}/{len(details_to_process)}: Отправка запроса для GTIN {detail['gtin']} (ID тиража: {detail['api_id']}) ---")
                        
                        # РЕКОМЕНДАЦИЯ: Запускайте Gunicorn с увеличенным таймаутом, например:
                        # gunicorn --workers 3 --timeout 300 'app:create_app()'
                        # Увеличиваем таймаут для одного запроса, чтобы дать API больше времени на обработку.
                        response = requests.post(full_url, headers=headers, json=payload, timeout=120)
                        
                        user_logs.append(f"  URL: {full_url}")
                        user_logs.append(f"  Тело: {json.dumps(payload)}")
                        user_logs.append(f"  Статус ответа: {response.status_code}")
                        
                        response.raise_for_status()

                    # Обновляем статус заказа в нашей БД
                    with conn_local.cursor() as cur:
                        cur.execute("UPDATE orders SET api_status = 'Сведения подготовлены' WHERE id = %s", (selected_order_id,))
                    conn_local.commit()

                    flash('Операция "Подготовить сведения" успешно выполнена. Статус заказа обновлен.', 'success')
                    # Перенаправляем, чтобы обновить состояние кнопок
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))
                    api_response = {'status_code': 200, 'body': "\n".join(user_logs)}

                except Exception as e:
                    error_body = response.text if 'response' in locals() and hasattr(response, 'text') else ""
                    user_logs.append(f"\n!!! ОШИБКА: {e}\nОтвет сервера (если был):\n{error_body}")
                    api_response = {'status_code': 500, 'body': "\n".join(user_logs)}
                finally:
                    if 'conn_local' in locals() and conn_local: conn_local.close()

            elif action == 'prepare_report':
                access_token = session.get('api_access_token')
                if not access_token:
                    flash('Токен API не найден. Пожалуйста, войдите заново.', 'warning')
                    return redirect(url_for('.login'))

                if not selected_order or not selected_order.get('api_order_id'):
                    flash('Сначала необходимо создать заказ в API и разбить его на тиражи.', 'danger')
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))

                user_logs = []
                try:
                    conn_local = get_db_connection()
                    with conn_local.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute(
                            "SELECT id, api_id, gtin FROM dmkod_aggregation_details WHERE order_id = %s AND api_id IS NOT NULL ORDER BY id",
                            (selected_order_id,)
                        )
                        details_to_process = cur.fetchall()

                    if not details_to_process:
                        raise Exception("Не найдено позиций с ID тиража (api_id) для подготовки отчета.")

                    api_base_url = os.getenv('API_BASE_URL', '').rstrip('/')
                    full_url = f"{api_base_url}/psp/utilisation/report/create"
                    headers = {'Authorization': f'Bearer {access_token}'}
                    
                    user_logs.append(f"Найдено {len(details_to_process)} позиций для подготовки отчета.")

                    for i, detail in enumerate(details_to_process):
                        payload = {"printrun_id": detail['api_id']}
                        user_logs.append(f"--- {i+1}/{len(details_to_process)}: Отправка запроса для GTIN {detail['gtin']} (ID тиража: {detail['api_id']}) ---")
                        
                        # Увеличиваем таймаут для одного запроса, чтобы дать API больше времени на обработку.
                        # Общий таймаут Gunicorn также должен быть увеличен.
                        response = requests.post(full_url, headers=headers, json=payload, timeout=120)
                        
                        user_logs.append(f"  URL: {full_url}")
                        user_logs.append(f"  Тело: {json.dumps(payload)}")
                        user_logs.append(f"  Статус ответа: {response.status_code}")
                        
                        response.raise_for_status()

                    # Обновляем статус заказа в нашей БД
                    with conn_local.cursor() as cur:
                        cur.execute(
                            "UPDATE orders SET api_status = 'Отчет подготовлен' WHERE id = %s",
                            (selected_order_id,)
                        )
                    conn_local.commit()

                    flash('Операция "Подготовить отчет" успешно выполнена. Статус заказа обновлен.', 'success')
                    # Перенаправляем, чтобы обновить состояние кнопок
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))
                    api_response = {'status_code': 200, 'body': "\n".join(user_logs)}

                except Exception as e:
                    error_body = response.text if 'response' in locals() and hasattr(response, 'text') else ""
                    user_logs.append(f"\n!!! ОШИБКА: {e}\nОтвет сервера (если был):\n{error_body}")
                    api_response = {'status_code': 500, 'body': "\n".join(user_logs)}
                finally:
                    if 'conn_local' in locals() and conn_local: conn_local.close()

            elif action == 'export_delta':
                user_logs = []
                try:
                    conn_local = get_db_connection()
                    with conn_local.cursor(cursor_factory=RealDictCursor) as cur:
                        # Получаем все детали заказа с кодами и датами
                        cur.execute(
                            """
                            SELECT 
                                api_codes_json,
                                production_date,
                                expiry_date
                            FROM dmkod_aggregation_details
                            WHERE order_id = %s AND api_codes_json IS NOT NULL
                            """,
                            (selected_order_id,)
                        )
                        details_to_process = cur.fetchall()

                    if not details_to_process:
                        raise Exception("В заказе нет скачанных кодов для выгрузки.")

                    # Используем StringIO для сборки CSV в памяти
                    output = StringIO()
                    # Используем pandas для удобной работы с данными и CSV
                    all_rows = []

                    for detail in details_to_process:
                        codes = detail['api_codes_json'].get('codes', [])
                        prod_date = detail.get('production_date')
                        exp_date = detail.get('expiry_date')

                        life_time_months = ''
                        if prod_date and exp_date:
                            # Считаем разницу в месяцах
                            delta = relativedelta(exp_date, prod_date)
                            life_time_months = delta.years * 12 + delta.months

                        for code in codes:
                            if not code or len(code) < 16:
                                continue # Пропускаем некорректные коды
                            
                            all_rows.append({
                                'DataMatrix': code,
                                'DataMatrixCode': '',
                                'Barcode': code[3:16], # Извлекаем EAN-13 (символы с 4 по 16)
                                'LifeTime': life_time_months
                            })
                    
                    if not all_rows:
                        raise Exception("Не найдено корректных кодов для выгрузки.")

                    # --- ИСПРАВЛЕНО: Обновляем статус заказа на 'delta' в той же транзакции ---
                    with conn_local.cursor() as cur:
                        cur.execute("UPDATE orders SET status = 'delta' WHERE id = %s", (selected_order_id,))
                        conn_local.commit()
                    user_logs.append(f"Статус заказа #{selected_order_id} обновлен на 'delta'.")
                    flash(f"Статус заказа #{selected_order_id} обновлен на 'delta'.", "info")

                    df = pd.DataFrame(all_rows)

                    # Используем StringIO для сборки CSV в памяти,
                    # так как старая версия pandas не поддерживает line_terminator.
                    # Мы сделаем замену вручную.
                    buffer = StringIO()
                    import csv
                    df.to_csv(buffer, sep='\t', index=False, encoding='utf-8', quoting=csv.QUOTE_NONE)
                    
                    # Получаем содержимое как строку и заменяем окончания строк на CRLF для Windows
                    csv_content_windows = buffer.getvalue().replace('\n', '\r\n')
                    
                    # Отправляем итоговый файл
                    return send_file(BytesIO(csv_content_windows.encode('utf-8')),
                                     mimetype='text/csv',
                                     as_attachment=True,
                                     download_name=f'delta_export_order_{selected_order_id}.csv')
                except Exception as e:
                    if 'conn_local' in locals() and conn_local: conn_local.rollback()
                    flash(f'Ошибка при формировании отчета "Дельта": {e}', 'danger')
                    return redirect(url_for('.integration_panel', order_id=selected_order_id))
                finally:
                    if 'conn_local' in locals() and conn_local: conn_local.close()

        # Если это POST-запрос без действия (просто выбор заказа), перенаправляем на GET с параметром
        elif request.method == 'POST' and not action and selected_order_id:
            return redirect(url_for('.integration_panel', order_id=selected_order_id))

    except Exception as e:
        flash(f'Произошла критическая ошибка: {e}', 'danger')
        if conn: conn.rollback()
        orders = [] # Очищаем список заказов в случае ошибки
    finally:
        # Проверяем, есть ли файл для скачивания в сессии
        if 'download_file' in session:
            file_info = session.pop('download_file')
            if conn: conn.close() # Закрываем соединение перед отправкой файла
            return send_file(
                BytesIO(file_info['data']),
                mimetype=file_info['mimetype'],
                as_attachment=True,
                download_name=file_info['name']
            )
        if conn: conn.close()

    return render_template('integration_panel.html', orders=orders, selected_order_id=selected_order_id, selected_order=selected_order, api_response=api_response, title="Интеграция")
    
@dmkod_bp.route('/admin', methods=['GET', 'POST'])
@login_required
@api_token_required
def admin():
    """Страница администрирования для удаления заказов."""
    # Проверяем, является ли пользователь администратором
    if not current_user.is_admin:
        from flask import abort
        abort(403) # Доступ запрещен

    conn = get_db_connection()

    if request.method == 'POST':
        order_ids_to_delete = request.form.getlist('order_ids')
        if not order_ids_to_delete:
            flash('Не выбрано ни одного заказа для удаления.', 'warning')
        else:
            try:
                with conn.cursor() as cur:
                    # ВАЖНО: Каскадное удаление связанных данных
                    cur.execute("DELETE FROM dmkod_aggregation_details WHERE order_id = ANY(%s)", (order_ids_to_delete,))
                    cur.execute("DELETE FROM dmkod_order_files WHERE order_id = ANY(%s)", (order_ids_to_delete,))
                    cur.execute("DELETE FROM orders WHERE id = ANY(%s)", (order_ids_to_delete,))
                conn.commit()
                flash(f'Успешно удалено заказов: {len(order_ids_to_delete)}.', 'success')
            except Exception as e:
                conn.rollback()
                flash(f'Ошибка при удалении заказов: {e}', 'danger')
        
        conn.close()
        return redirect(url_for('.admin'))

    # Логика для GET-запроса
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    o.id, o.client_name, o.created_at, o.notes,
                    pg.display_name as product_group_name
                FROM orders o
                LEFT JOIN dmkod_product_groups pg ON o.product_group_id = pg.id
                WHERE o.status IN ('dmkod', 'delta') ORDER BY o.id DESC
            """)
            orders = cur.fetchall()
    except Exception as e:
        flash(f'Ошибка при загрузке заказов: {e}', 'danger')
        orders = []
    finally:
        conn.close()

    return render_template('admin.html', orders=orders, title="Администрирование")