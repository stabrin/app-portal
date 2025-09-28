import os
import requests
import json
import logging
import pandas as pd
from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, Response, send_file
from flask_login import login_user, logout_user, login_required, current_user
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from bcrypt import checkpw
from io import BytesIO

from .db import get_db_connection
from .forms import LoginForm, IntegrationForm, ProductGroupForm
from .auth import User

# 1. Определяем Blueprint
dmkod_bp = Blueprint(
    'dmkod_integration_app', __name__,
    static_folder='static'
)

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
def dashboard():
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Используем JOIN для получения названия товарной группы
        cur.execute("""
            SELECT 
                o.id, o.client_name, o.status, o.notes, o.created_at,
                pg.display_name as product_group_name
            FROM orders o
            LEFT JOIN dmkod_product_groups pg ON o.product_group_id = pg.id
            ORDER BY o.id DESC 
            LIMIT 20
        """)
        orders = cur.fetchall()
    conn.close()
    return render_template('dmkod_index.html', orders=orders, title="Интеграция с ДМкод")


# 4. Новый роут для справочника
@dmkod_bp.route('/participants')
@login_required
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
                        df = pd.read_excel(details_file)
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
def edit_integration(order_id):
    conn = get_db_connection()
    try:
        if request.method == 'POST':
            action = request.form.get('action')
            
            with conn.cursor() as cur:
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
                        df = pd.read_excel(details_file)
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
                            field, detail_id_str = key.split('-', 1)
                            if detail_id_str.isdigit():
                                detail_id = int(detail_id_str)
                                # Собираем все изменения для одной строки
                                found = False
                                for u in updates:
                                    if u['id'] == detail_id:
                                        u[field] = value if value else None
                                        found = True
                                        break
                                if not found:
                                    updates.append({'id': detail_id, field: value if value else None})
                    
                    # Применяем изменения к базе данных
                    for update_data in updates:
                        detail_id = update_data.pop('id')
                        # Формируем SQL-запрос динамически
                        set_clauses = [sql.SQL("{} = %s").format(sql.Identifier(key)) for key in update_data.keys()]
                        values = list(update_data.values())
                        values.append(detail_id)
                        
                        query = sql.SQL("UPDATE dmkod_aggregation_details SET {} WHERE id = %s").format(sql.SQL(', ').join(set_clauses))
                        cur.execute(query, values)
                    
                    flash(f'Изменения в {len(updates)} строках успешно сохранены.', 'success')

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

            # Проверяем, можно ли редактировать этот заказ
            if order['status'] != 'dmkod':
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
def integration_panel():
    """Страница 'Интеграция' с выбором заказа."""
    api_response = None
    selected_order = None
    selected_order_id = request.form.get('order_id', type=int) if request.method == 'POST' else request.args.get('order_id', type=int)

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, client_name, created_at FROM orders WHERE status = 'dmkod' ORDER BY id DESC")
            orders = cur.fetchall()

            # Если заказ выбран, загружаем его полную информацию
            if selected_order_id:
                cur.execute("SELECT * FROM orders WHERE id = %s", (selected_order_id,))
                selected_order = cur.fetchone()

    except Exception as e:
        flash(f'Ошибка при загрузке заказов: {e}', 'danger')
        orders = []
    finally:
        conn.close()

    if request.method == 'POST':
        action = request.form.get('action')
        if not selected_order_id:
            flash('Пожалуйста, сначала выберите заказ.', 'warning')
            return redirect(url_for('.integration_panel'))

        if action:
            if action == 'create_order':
                access_token = session.get('api_access_token')
                if not access_token:
                    flash('Токен API не найден. Пожалуйста, войдите заново.', 'warning')
                    return redirect(url_for('.login'))

                try:
                    conn_local = get_db_connection()
                    with conn_local.cursor(cursor_factory=RealDictCursor) as cur:
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
                        with conn_local.cursor() as cur:
                            cur.execute("UPDATE orders SET api_order_id = %s WHERE id = %s", (api_order_id, selected_order_id))
                        conn_local.commit()
                        flash(f'Заказ в API успешно создан с ID: {api_order_id}.', 'success')

                    api_response = {
                        'status_code': response.status_code,
                        'body': json.dumps(response_data, indent=2, ensure_ascii=False)
                    }

                except Exception as e:
                    if 'conn_local' in locals() and conn_local: conn_local.rollback()
                    error_body = ""
                    if 'response' in locals() and hasattr(response, 'text'):
                        error_body = response.text
                    api_response = {
                        'status_code': response.status_code if 'response' in locals() else 500,
                        'body': f"ОШИБКА: {e}\n\nОтвет сервера (если был):\n{error_body}"
                    }
                finally:
                    if 'conn_local' in locals() and conn_local: conn_local.close()
            else:
                # Заглушка для других кнопок
                api_response = {
                    'status_code': 200,
                    'body': json.dumps({
                        "message": f"Запрос '{action}' для заказа #{selected_order_id} получен.",
                        "details": "Это заглушка. Реальная логика вызова API еще не реализована."
                    }, indent=2, ensure_ascii=False)
                }
        elif not action and selected_order_id:
             # Если просто выбрали заказ из списка, перенаправляем, чтобы URL был чистым
             return redirect(url_for('.integration_panel', order_id=selected_order_id))

    return render_template('integration_panel.html', orders=orders, selected_order_id=selected_order_id, selected_order=selected_order, api_response=api_response, title="Интеграция")


@dmkod_bp.route('/admin', methods=['GET', 'POST'])
@login_required
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
                WHERE o.status = 'dmkod' ORDER BY o.id DESC
            """)
            orders = cur.fetchall()
    except Exception as e:
        flash(f'Ошибка при загрузке заказов: {e}', 'danger')
        orders = []
    finally:
        conn.close()

    return render_template('admin.html', orders=orders, title="Администрирование")