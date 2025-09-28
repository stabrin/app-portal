# dmkod-integration-app/app/main.py

import os
from flask import Flask, render_template, redirect, url_for, flash, request, Blueprint
from psycopg2.extras import RealDictCursor
from bcrypt import checkpw
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user, abort
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired
from dotenv import load_dotenv

from .db import get_db_connection

# --- СОЗДАНИЕ ЧЕРТЕЖА (BLUEPRINT) ---
dmkod_bp = Blueprint(
    'dmkod_integration_app', __name__,
    template_folder='templates',
    static_folder='static'
)

# --- Инициализация Flask-Login ---
login_manager = LoginManager()
login_manager.login_view = 'dmkod_integration_app.login'
login_manager.login_message = "Пожалуйста, войдите, чтобы получить доступ к этой странице."
login_manager.login_message_category = "info"


# --- Модель пользователя и форма входа (аналогично datamatrix-app) ---

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


# --- РОУТЫ АУТЕНТИФИКАЦИИ ---

@dmkod_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('.panel'))
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
            return redirect(next_page or url_for('.panel'))
        else:
            flash('Неверное имя пользователя или пароль.', 'danger')
            
    return render_template('login.html', title='Вход', form=form)

@dmkod_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы успешно вышли из системы.', 'success')
    return redirect(url_for('.login'))


# --- ОСНОВНОЙ РОУТ ПРИЛОЖЕНИЯ ---

@dmkod_bp.route('/', methods=['GET', 'POST'])
@login_required
def panel():
    """Панель управления интеграцией."""
    orders_table = os.getenv('TABLE_ORDERS', 'orders')
    selected_order_id = request.form.get('order_id', type=int) if request.method == 'POST' else request.args.get('order_id', type=int)

    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT id, client_name, created_at FROM {orders_table} WHERE status = 'dmkod' ORDER BY id DESC")
        orders = cur.fetchall()
    conn.close()

    if request.method == 'POST':
        action = request.form.get('action')
        if not selected_order_id:
            flash('Пожалуйста, сначала выберите заказ.', 'warning')
            return redirect(url_for('.panel')) # Перенаправляем без ID

        if action:
            # Здесь будет логика для кнопок. Пока это заглушки.
            flash(f'Выбрано действие "{action}" для заказа №{selected_order_id}. Логика еще не реализована.', 'info')
        
        return redirect(url_for('.panel', order_id=selected_order_id))

    return render_template('index.html',
                           orders=orders,
                           selected_order_id=selected_order_id,
                           title="Панель управления")


@dmkod_bp.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    """Страница администрирования для удаления заказов."""
    if not current_user.is_admin:
        abort(403) # Доступ запрещен для не-администраторов

    orders_table = os.getenv('TABLE_ORDERS', 'orders')
    conn = get_db_connection()

    if request.method == 'POST':
        order_ids_to_delete = request.form.getlist('order_ids')
        if not order_ids_to_delete:
            flash('Не выбрано ни одного заказа для удаления.', 'warning')
        else:
            try:
                with conn.cursor() as cur:
                    # ВАЖНО: здесь нужно также удалять связанные данные
                    # из других таблиц (детализация, файлы и т.д.)
                    # Пока удаляем только сам заказ для примера.
                    # Используем `ANY` для безопасной передачи списка ID.
                    cur.execute(f"DELETE FROM {orders_table} WHERE id = ANY(%s)", (order_ids_to_delete,))
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
            cur.execute(f"SELECT id, client_name, created_at FROM {orders_table} WHERE status = 'dmkod' ORDER BY id DESC")
            orders = cur.fetchall()
    except Exception as e:
        flash(f'Ошибка при загрузке заказов: {e}', 'danger')
        orders = []
    finally:
        conn.close()

    return render_template('admin.html', orders=orders, title="Администрирование")


# --- ФАБРИКА ПРИЛОЖЕНИЯ ---
def create_app():
    # Указываем путь к файлу .env, который находится в корне проекта,
    # на два уровня выше текущего файла.
    dotenv_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)
    
    app = Flask(__name__)
    # Убедитесь, что в вашем .env файле есть ключ DMKOD_SECRET_KEY
    app.secret_key = os.getenv('DMKOD_SECRET_KEY')

    login_manager.init_app(app)

    app.register_blueprint(dmkod_bp, url_prefix='/dmkod')

    return app