# datamatrix-app/app/main.py

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, Blueprint
from dotenv import load_dotenv
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date
from bcrypt import checkpw

from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired

# --- Абсолютные импорты от корня пакета 'app' ---
from app.services.aggregation_service import run_aggregation_process, generate_standalone_sscc, run_import_from_dmkod
from app.services.product_service import get_all_products, add_product as add_product_service, generate_excel_template, process_excel_upload
from app.services.view_service import create_bartender_views, generate_declarator_report
from app.services.admin_service import delete_order_completely, get_tirages_for_order, delete_tirages_from_order
from app.services.task_service import process_aggregation_task_file
from app.db import get_db_connection
from app.forms import GenerateSsccForm

# --- СОЗДАНИЕ ЧЕРТЕЖА (BLUEPRINT) ---
datamatrix_bp = Blueprint(
    'datamatrix_app', __name__,
    template_folder='templates',
    static_folder='static'
)

# --- Инициализация Flask-Login ---
login_manager = LoginManager()
login_manager.login_view = 'datamatrix_app.login'
login_manager.login_message = "Пожалуйста, войдите, чтобы получить доступ к этой странице."
login_manager.login_message_category = "info"


# --- Вспомогательные функции и классы ---

class User(UserMixin):
    """Модель пользователя для Flask-Login."""
    def __init__(self, user_data):
        self.id = user_data['id']
        self.username = user_data['username']
        self.password_hash = user_data['password_hash']
        self.is_admin = user_data['is_admin']

@login_manager.user_loader
def load_user(user_id):
    """Загружает пользователя из БД по его ID."""
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (int(user_id),))
        user_data = cur.fetchone()
    conn.close()
    if user_data:
        return User(user_data)
    return None

class LoginForm(FlaskForm):
    """Форма входа."""
    username = StringField('Имя пользователя', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')


# --- РОУТЫ ПРИВЯЗАНЫ К ЧЕРТЕЖУ ---

@datamatrix_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('.index'))
    form = LoginForm()
    if form.validate_on_submit():
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (form.username.data,))
            user_data = cur.fetchone()
        conn.close()
        
        if user_data and user_data['password_hash'] and checkpw(form.password.data.encode('utf-8'), user_data['password_hash'].encode('utf-8')):
            user = User(user_data)
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('.index'))
        else:
            flash('Неверное имя пользователя или пароль.', 'danger')
            
    return render_template('login.html', title='Вход', form=form)

@datamatrix_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы успешно вышли из системы.', 'success')
    return redirect(url_for('.login'))


# --- ОСНОВНЫЕ РОУТЫ ПРИЛОЖЕНИЯ ---

@datamatrix_bp.route('/')
@login_required
def index():
    """Главная страница (Дашборд)."""
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        orders_table = os.getenv('TABLE_ORDERS', 'orders')
        cur.execute(f"SELECT * FROM {orders_table} ORDER BY id DESC LIMIT 10")
        orders = cur.fetchall()
    conn.close()
    return render_template('index.html', orders=orders, title="Панель управления")

# --- Раздел Заказы ---

@datamatrix_bp.route('/orders')
@login_required
def orders_list():
    return redirect(url_for('.index'))

@datamatrix_bp.route('/orders/new', methods=['GET', 'POST'])
@login_required
def create_order():
    if request.method == 'POST':
        client_name = request.form['client_name']
        order_date = request.form['order_date']
        notes = request.form['notes']
        conn = get_db_connection()
        with conn.cursor() as cur:
            orders_table = os.getenv('TABLE_ORDERS', 'orders')
            cur.execute(
                f"INSERT INTO {orders_table} (client_name, order_date, notes, status) VALUES (%s, %s, %s, 'new') RETURNING id",
                (client_name, order_date, notes)
            )
            new_order_id = cur.fetchone()[0]
            conn.commit()
        conn.close()
        flash(f'Заказ №{new_order_id} успешно создан!', 'success')
        return redirect(url_for('.order_details', order_id=new_order_id))
    return render_template('create_order.html', today=date.today().isoformat(), title="Новый заказ")

@datamatrix_bp.route('/orders/<int:order_id>', methods=['GET', 'POST'])
@login_required
def order_details(order_id):
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'process_dm':
            files = request.files.getlist('dm_files')
            
            if not files or all(f.filename == '' for f in files):
                flash('Ошибка: вы не выбрали ни одного файла для загрузки.', 'danger')
                return redirect(url_for('.order_details', order_id=order_id))

            mode = request.form.get('aggregation_mode')
            level1_qty, level2_qty, level3_qty = 0, 0, 0
            
            try:
                if mode in ['level1', 'level2', 'level3']:
                    level1_qty = int(request.form.get('level1_qty', 0))
                    if level1_qty <= 0:
                        flash('Ошибка: количество в коробе должно быть > 0.', 'danger')
                        return redirect(url_for('.order_details', order_id=order_id))
                if mode in ['level2', 'level3']:
                    level2_qty = int(request.form.get('level2_qty', 0))
                    if level2_qty <= 0:
                        flash('Ошибка: количество коробов на паллете должно быть > 0.', 'danger')
                        return redirect(url_for('.order_details', order_id=order_id))
                if mode == 'level3':
                    level3_qty = int(request.form.get('level3_qty', 0))
                    if level3_qty <= 0:
                        flash('Ошибка: количество паллет в контейнере должно быть > 0.', 'danger')
                        return redirect(url_for('.order_details', order_id=order_id))
            except (ValueError, TypeError):
                flash('Ошибка: некорректное значение для количества.', 'danger')
                return redirect(url_for('.order_details', order_id=order_id))
            
            dm_type = request.form.get('dm_type', 'standard')    

            logs = run_aggregation_process(order_id, files, dm_type, mode, level1_qty, level2_qty, level3_qty)
            return render_template('results.html', logs=logs, title=f"Результат обработки заказа №{order_id}")

        elif action == 'import_from_dmkod':
            # Получаем параметры агрегации из новой формы на странице dmkod_import.html
            mode = request.form.get('aggregation_mode')
            level1_qty, level2_qty, level3_qty = 0, 0, 0

            try:
                if mode in ['level1', 'level2', 'level3']:
                    level1_qty = int(request.form.get('level1_qty', 0))
                    if level1_qty <= 0:
                        raise ValueError('Количество в коробе должно быть > 0.')
                if mode in ['level2', 'level3']:
                    level2_qty = int(request.form.get('level2_qty', 0))
                    if level2_qty <= 0:
                        raise ValueError('Количество коробов на паллете должно быть > 0.')
                if mode == 'level3':
                    level3_qty = int(request.form.get('level3_qty', 0))
                    if level3_qty <= 0:
                        raise ValueError('Количество паллет в контейнере должно быть > 0.')
            except (ValueError, TypeError) as e:
                flash(f'Ошибка в параметрах агрегации: {e}', 'danger')
                # Возвращаемся на страницу импорта, чтобы пользователь мог исправить ошибку
                return redirect(url_for('.order_details', order_id=order_id))
            
            logs = run_import_from_dmkod(
                order_id, mode, level1_qty, level2_qty, level3_qty
            )
            return render_template('results.html', logs=logs, title=f"Результат обработки заказа №{order_id}")

        elif action == 'upload_foreign_sscc':
            if 'task_file' not in request.files:
                flash('Файл не был найден в запросе.', 'danger')
                return redirect(url_for('.order_details', order_id=order_id))
            
            file = request.files['task_file']
            owner_name = request.form.get('owner_name')

            if file.filename == '':
                flash('Файл не был выбран.', 'warning')
                return redirect(url_for('.order_details', order_id=order_id))
            
            if not owner_name:
                 flash('Необходимо указать имя владельца кодов.', 'danger')
                 return redirect(url_for('.order_details', order_id=order_id))

            if file:
                logs = process_aggregation_task_file(order_id, file.stream, owner_name)
                return render_template('results.html', logs=logs, title=f"Результат загрузки заданий для заказа №{order_id}")
        
        else:
            flash('Неизвестное действие.', 'warning')
            return redirect(url_for('.order_details', order_id=order_id))

    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        orders_table = os.getenv('TABLE_ORDERS', 'orders')
        cur.execute(f"SELECT * FROM {orders_table} WHERE id = %s", (order_id,))
        order = cur.fetchone()
    
    if not order:
        return "Заказ не найден!", 404

    # --- НОВАЯ ЛОГИКА: Проверяем статус заказа ---
    if order['status'] in ['dmkod', 'delta']:
        # Если статус dmkod, получаем детализацию и проверяем наличие кодов
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT gtin, dm_quantity, aggregation_level, api_codes_json FROM dmkod_aggregation_details WHERE order_id = %s AND api_codes_json IS NOT NULL",
                (order_id,)
            )
            details_with_codes = cur.fetchall()
        conn.close()
        # Рендерим новую страницу для импорта
        return render_template('dmkod_import.html', order=order, details_with_codes=details_with_codes)
    
    conn.close() # Закрываем соединение для обычных заказов
        
    return render_template('order_details.html', order=order, title=f"Заказ №{order_id}")

# --- Раздел Справочники ---

@datamatrix_bp.route('/products', methods=['GET', 'POST'])
@login_required
def products_list():
    if request.method == 'POST':
        if 'excel_file' not in request.files:
            flash('Файл не был найден в запросе.', 'danger')
            return redirect(request.url)
        file = request.files['excel_file']
        if file.filename == '':
            flash('Файл не был выбран.', 'warning')
            return redirect(request.url)
        if file:
            result = process_excel_upload(file.stream)
            flash(result['message'], 'success' if result['success'] else 'danger')
            return redirect(url_for('.products_list'))
    products = get_all_products()
    return render_template('products.html', products=products, title="Справочник товаров")

@datamatrix_bp.route('/products/add', methods=['GET', 'POST'])
@login_required
def add_product():
    if request.method == 'POST':
        add_product_service(
            gtin=request.form['gtin'], name=request.form['name'],
            desc1=request.form['description_1'], desc2=request.form['description_2'],
            desc3=request.form['description_3']
        )
        flash(f"Товар с GTIN {request.form['gtin']} был успешно добавлен/обновлен.", 'success')
        return redirect(url_for('.products_list'))
    return render_template('add_product.html', title="Добавить товар")

@datamatrix_bp.route('/products/template')
@login_required
def download_template():
    template_buffer = generate_excel_template()
    return send_file(
        template_buffer, as_attachment=True,
        download_name='products_template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# --- Раздел Отчеты ---

@datamatrix_bp.route('/reports', methods=['GET', 'POST'])
@login_required
def reports_page():
    if request.method == 'POST':
        action = request.form.get('action')
        try:
            order_id = int(request.form.get('order_id'))
        except (ValueError, TypeError):
            flash('Некорректный ID заказа.', 'danger')
            return redirect(url_for('.reports_page'))
        
        if action == 'create_view':
            result = create_bartender_views(order_id)
            flash(result['message'], 'success' if result['success'] else 'danger')
            return redirect(url_for('.reports_page'))
        
        elif action == 'declarator_report':
            result = generate_declarator_report(order_id)
            if result['success']:
                return send_file(
                    result['buffer'], as_attachment=True,
                    download_name=result['filename'],
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
            else:
                flash(result['message'], 'danger')
                return redirect(url_for('.reports_page'))
    
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        orders_table = os.getenv('TABLE_ORDERS', 'orders')
        cur.execute(f"SELECT id, client_name, order_date FROM {orders_table} ORDER BY id DESC")
        orders = cur.fetchall()
    conn.close()
    return render_template('reports.html', orders=orders, title="Отчеты и интеграции")

# --- Раздел Администрирование ---

@datamatrix_bp.route('/admin', methods=['GET', 'POST'])
@login_required
def admin_page():
    if not current_user.is_admin:
        flash("У вас нет прав для доступа к этому разделу.", "danger")
        return redirect(url_for('.index'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'delete_order':
            try:
                order_id = int(request.form.get('order_id'))
                result = delete_order_completely(order_id)
                flash(result['message'], 'success' if result['success'] else 'danger')
            except (ValueError, TypeError):
                flash('Некорректный ID заказа.', 'danger')
        return redirect(url_for('.admin_page'))
    
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        orders_table = os.getenv('TABLE_ORDERS', 'orders')
        cur.execute(f"SELECT id, client_name, order_date FROM {orders_table} ORDER BY id DESC")
        orders = cur.fetchall()
    conn.close()
    return render_template('admin.html', orders=orders, title="Администрирование")

@datamatrix_bp.route('/admin/edit_order/<int:order_id>', methods=['GET', 'POST'])
@login_required
def edit_order_page(order_id):
    if not current_user.is_admin:
        flash("У вас нет прав для доступа к этому разделу.", "danger")
        return redirect(url_for('.index'))

    if request.method == 'POST':
        tirages_to_delete = request.form.getlist('tirages_to_delete')
        result = delete_tirages_from_order(order_id, tirages_to_delete)
        flash(result['message'], 'success' if result['success'] else 'danger')
        return redirect(url_for('.edit_order_page', order_id=order_id))
    
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        orders_table = os.getenv('TABLE_ORDERS', 'orders')
        cur.execute(f"SELECT * FROM {orders_table} WHERE id = %s", (order_id,))
        order = cur.fetchone()
    conn.close()
    if not order: return "Заказ не найден!", 404
    
    tirages = get_tirages_for_order(order_id)
    return render_template('edit_order.html', order=order, tirages=tirages, title=f"Редактирование заказа №{order_id}")

@datamatrix_bp.route('/generate-sscc', methods=['GET', 'POST'])
@login_required
def generate_sscc_standalone():
    """Страница для генерации SSCC кодов по запросу."""
    form = GenerateSsccForm()
    logs = None
    generated_data = None

    if form.validate_on_submit():
        owner = form.owner.data
        quantity = form.quantity.data
        logs, generated_data = generate_standalone_sscc(quantity, owner)
        if "ОШИБКА" in logs[0]:
            flash(logs[0], 'danger')
        else:
            flash(f"Успешно сгенерировано {len(generated_data)} SSCC кодов.", 'success')

    return render_template('generate_sscc.html', form=form, logs=logs, generated_data=generated_data)


# --- ФАБРИКА ПРИЛОЖЕНИЯ ---
def create_app():
    load_dotenv()
    
    app = Flask(__name__)
    # Используем правильное имя переменной из .env файла,
    # чтобы Flask мог шифровать сессии.
    # Это исправляет ошибку "no secret key was set".
    app.secret_key = os.getenv('DATAMATRIX_SECRET_KEY')

    login_manager.init_app(app)

    app.register_blueprint(datamatrix_bp, url_prefix='/datamatrix')

    return app