from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user, login_user, logout_user
from psycopg2.extras import RealDictCursor

from .db import get_db_connection
from .forms import LoginForm
from .auth import User

main_blueprint = Blueprint('main', __name__)

@main_blueprint.route('/')
@login_required
def index():
    """
    Главная страница портала с динамическим отображением ссылок на приложения.
    """
    visible_apps = set()
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT app_name, visibility_rule FROM app_visibility")
            rules = cur.fetchall()

        for rule in rules:
            app_name = rule['app_name']
            visibility_rule = rule['visibility_rule']

            if visibility_rule == 'All':
                visible_apps.add(app_name)
            else:
                # Убедимся, что у current_user есть атрибут username
                if hasattr(current_user, 'username'):
                    allowed_users = [user.strip() for user in visibility_rule.split(',')]
                    if current_user.username in allowed_users:
                        visible_apps.add(app_name)
    except Exception as e:
        print(f"Ошибка при проверке видимости приложений: {e}")
    finally:
        if conn:
            conn.close()

    # Передаем множество видимых приложений в шаблон
    return render_template('index.html', visible_apps=visible_apps)

@main_blueprint.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    form = LoginForm()
    if form.validate_on_submit():
        conn = get_db_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE username = %s", (form.username.data,))
                user_data = cur.fetchone()

            if user_data:
                user = User(user_data)
                if user.check_password(form.password.data):
                    login_user(user)
                
                    next_page = request.args.get('next')
                    flash('Вы успешно вошли в систему.', 'success')
                    return redirect(next_page or url_for('main.index'))
            else:
                flash('Неверное имя пользователя или пароль.', 'danger')
        except Exception as e:
            flash(f'Произошла ошибка аутентификации: {e}', 'danger')
        finally:
            if conn:
                conn.close()

    return render_template('portal_login.html', form=form, title="Вход")

@main_blueprint.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы успешно вышли из системы.', 'info')
    return redirect(url_for('main.login'))