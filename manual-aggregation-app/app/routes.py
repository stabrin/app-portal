# manual-aggregation-app/app/routes.py

from flask import Blueprint, render_template, request, flash, redirect, url_for, Response, session
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps

# --- Импорты из нашего приложения ---
from .auth import verify_admin_credentials, verify_employee_token, login_manager
from .forms import AdminLoginForm, EmployeeTokenForm, OrderForm
from .services.order_service import (
    create_new_order, 
    get_all_orders, 
    get_order_by_id, 
    update_order,
    get_tokens_for_order,
    delete_order_completely,
    get_aggregations_for_order,
    delete_aggregations_by_ids,
    assign_name_to_token
)
from .services.pdf_service import generate_tokens_pdf, generate_control_codes_pdf
from .services.report_service import get_aggregation_report_for_order, generate_aggregation_excel_report

# --- Основной Blueprint ---
manual_aggregation_bp = Blueprint('manual_aggregation_app', __name__, template_folder='templates', static_folder='static')

# --- Декоратор для проверки роли администратора ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or getattr(current_user, 'role', None) != 'admin':
            flash("У вас нет прав для доступа к этой странице.", "warning")
            return redirect(url_for('.login_choice'))
        return f(*args, **kwargs)
    return decorated_function


# --- РОУТЫ АВТОРИЗАЦИИ ---

@manual_aggregation_bp.route('/login')
def login_choice():
    return render_template('auth/login_choice.html')

@manual_aggregation_bp.route('/login/admin', methods=['GET', 'POST'])
def login_admin():
    if current_user.is_authenticated and getattr(current_user, 'role', None) == 'admin':
        return redirect(url_for('.dashboard'))
    form = AdminLoginForm()
    if form.validate_on_submit():
        user = verify_admin_credentials(form.username.data, form.password.data)
        if user:
            login_user(user)
            return redirect(url_for('.dashboard'))
        flash("Неверные учетные данные администратора.", "danger")
    return render_template('auth/login_admin.html', form=form)

@manual_aggregation_bp.route('/login/employee', methods=['GET', 'POST'])
def login_employee():
    if current_user.is_authenticated and getattr(current_user, 'role', None) == 'employee':
        return redirect(url_for('.employee_task_page'))
        
    form = EmployeeTokenForm()
    if form.validate_on_submit():
        last_name = form.last_name.data
        access_token = form.access_token.data
        
        user = verify_employee_token(access_token)
        
        if user:
            # Проверка статуса заказа, к которому привязан токен
            order_id = user.data.get('order_id')
            order = get_order_by_id(order_id)
            if not order:
                flash("Ошибка: заказ, к которому привязан ваш пропуск, не найден.", "danger")
                return render_template('auth/login_employee.html', form=form)

            if order.get('status') != 'active':
                status = order.get('status')
                if status == 'closed':
                    message = "Доступ запрещен: Заказ, к которому привязан ваш пропуск, был закрыт администратором."
                elif status == 'new':
                    message = "Доступ запрещен: Заказ еще не был активирован администратором."
                else:
                    message = f"Доступ запрещен: Заказ находится в статусе '{status}' и недоступен для работы."
                flash(message, "danger")
                return render_template('auth/login_employee.html', form=form)

            # Сохраняем ФИО сотрудника в базу данных
            assign_name_to_token(access_token, last_name)
            
            # Сохраняем ФИО в сессию для отображения на странице задания
            session['employee_name'] = last_name
            
            login_user(user)
            return redirect(url_for('.employee_task_page'))
            
        flash("Неверный или неактивный код доступа.", "danger")
    return render_template('auth/login_employee.html', form=form)
    
@manual_aggregation_bp.route('/logout')
@login_required
def logout():
    # Очищаем сессию при выходе
    session.pop('employee_name', None)
    logout_user()
    flash("Вы успешно вышли из системы.", "success")
    return redirect(url_for('.login_choice'))


# --- РОУТЫ АДМИНИСТРАТОРА ---

@manual_aggregation_bp.route('/')
def index():
    """
    Главная страница приложения. Перенаправляет на страницу выбора входа.
    Это более явный и надежный способ организации входа, чем использование
    декоратора @login_required на корневом маршруте, что может вызывать
    проблемы с reverse proxy.
    """
    return redirect(url_for('.login_choice'))

@manual_aggregation_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    all_orders = get_all_orders()
    return render_template('admin/dashboard.html', orders=all_orders)

@manual_aggregation_bp.route('/orders/new', methods=['GET', 'POST'])
@login_required
@admin_required
def create_order():
    form = OrderForm()
    if form.validate_on_submit():
        result = create_new_order(
            form.client_name.data, 
            form.levels.data, 
            form.employee_count.data,
            form.set_capacity.data
        )
        flash(result.get('message'), 'success' if result.get('success') else 'danger')

        if result.get('success'):
            return render_template('admin/order_tokens.html', 
                                   order_id=result.get('order_id'), 
                                   tokens=result.get('tokens'))
        else:
            # В случае ошибки создания, снова показываем форму с введенными данными и ошибками
            return render_template('admin/create_order.html', form=form)
            
    # Для GET запроса просто показываем пустую форму
    return render_template('admin/create_order.html', form=form)

@manual_aggregation_bp.route('/orders/edit/<int:order_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_order(order_id):
    order_data = get_order_by_id(order_id)
    if not order_data:
        flash("Заказ не найден.", "danger")
        return redirect(url_for('.dashboard'))

    form = OrderForm()

    if form.validate_on_submit():
        # POST: Данные валидны, обновляем заказ
        result = update_order(
            order_id, 
            form.client_name.data, 
            form.levels.data, 
            form.employee_count.data,
            form.set_capacity.data,
            form.status.data
        )
        flash(result.get('message'), 'success' if result.get('success') else 'danger')
        return redirect(url_for('.edit_order', order_id=order_id))

    # GET: Заполняем форму данными из БД для отображения
    if request.method == 'GET':
        form.client_name.data = order_data.get('client_name')
        form.levels.data = order_data.get('aggregation_levels', [])
        form.employee_count.data = order_data.get('employee_count')
        form.set_capacity.data = order_data.get('set_capacity')
        form.status.data = order_data.get('status')

    return render_template('admin/edit_order.html', order=order_data, form=form)
    
@manual_aggregation_bp.route('/orders/<int:order_id>/download-tokens')
@login_required
@admin_required
def download_tokens_pdf(order_id):
    order_data = get_order_by_id(order_id)
    if not order_data:
        flash("Заказ не найден.", "danger")
        return redirect(url_for('.dashboard'))

    tokens = get_tokens_for_order(order_id)

    if not tokens:
        flash("Для этого заказа не найдено токенов.", "warning")
        return redirect(url_for('.edit_order', order_id=order_id))

    pdf_bytes = generate_tokens_pdf(order_data, tokens)

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment;filename=order_{order_id}_tokens.pdf"}
    )

@manual_aggregation_bp.route('/downloads/control-codes')
@login_required
@admin_required
def download_control_codes():
    """Генерирует PDF с универсальными управляющими QR-кодами."""
    pdf_bytes = generate_control_codes_pdf()
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment;filename=control_codes.pdf"}
    )

@manual_aggregation_bp.route('/reports/<int:order_id>/download')
@login_required
@admin_required
def download_report_excel(order_id):
    """Скачивает детальный отчет по агрегациям в формате Excel."""
    try:
        order_data = get_order_by_id(order_id)
        if not order_data:
            flash(f"Заказ №{order_id} не найден.", "danger")
            return redirect(url_for('.reports_page'))

        excel_buffer = generate_aggregation_excel_report(order_id)
        
        return Response(
            excel_buffer.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment;filename=order_{order_id}_aggregations_report.xlsx"}
        )
    except Exception as e:
        flash(f"Не удалось сгенерировать отчет: {e}", "danger")
        return redirect(url_for('.reports_page'))

@manual_aggregation_bp.route('/reports', methods=['GET', 'POST'])
@login_required
@admin_required
def reports_page():
    # Получаем все заказы для выпадающего списка
    orders_list = get_all_orders()
    report_data = None
    selected_order_id = None

    if request.method == 'POST':
        order_id = request.form.get('order_id')
        if order_id:
            try:
                selected_order_id = int(order_id)
                # Формируем отчет для выбранного заказа
                report_data = get_aggregation_report_for_order(selected_order_id)
            except ValueError:
                flash("Некорректный ID заказа.", "danger")

    return render_template(
        'admin/reports.html', 
        orders=orders_list,
        report_data=report_data,
        selected_order_id=selected_order_id
    )

@manual_aggregation_bp.route('/admin/manage', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_data():
    """Страница для управления данными: удаление заказов и агрегаций."""
    orders_list = get_all_orders()
    aggregations_to_show = None
    selected_order_id_for_view = None

    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'delete_order':
            order_id_to_delete = request.form.get('order_id_to_delete')
            if order_id_to_delete:
                result = delete_order_completely(int(order_id_to_delete))
                flash(result['message'], 'success' if result['success'] else 'danger')
            return redirect(url_for('.manage_data'))
        
        elif action == 'delete_aggregations':
            ids_to_delete = request.form.getlist('aggregation_ids')
            order_id_of_aggregations = request.form.get('order_id')
            result = delete_aggregations_by_ids(ids_to_delete)
            flash(result['message'], 'success' if result['success'] else 'danger')
            return redirect(url_for('.manage_data', order_id_to_show=order_id_of_aggregations))

    order_id_to_show = request.args.get('order_id_to_show')
    if order_id_to_show:
        try:
            selected_order_id_for_view = int(order_id_to_show)
            aggregations_to_show = get_aggregations_for_order(selected_order_id_for_view)
        except (ValueError, TypeError):
            flash("Некорректный ID заказа для просмотра.", "danger")

    return render_template('admin/manage_data.html', 
                           orders=orders_list, 
                           aggregations=aggregations_to_show,
                           selected_order_id=selected_order_id_for_view)

@manual_aggregation_bp.route('/admin/scanner-test')
@login_required
@admin_required
def scanner_test():
    """Страница для тестирования и настройки сканера."""
    return render_template('admin/scanner_test.html')

# --- РОУТЫ СОТРУДНИКА ---
@manual_aggregation_bp.route('/task')
@login_required
def employee_task_page():
    if getattr(current_user, 'role', None) != 'employee':
        flash("Доступ запрещен.", "danger")
        return redirect(url_for('.login_choice'))

    order_id = current_user.data.get('order_id')
    order_data = get_order_by_id(order_id)
    
    if not order_data:
        flash("Ошибка: заказ, к которому привязан ваш пропуск, больше не существует.", "danger")
        logout_user()
        return redirect(url_for('.login_employee'))

    # Получаем ФИО из сессии, которую мы установили при входе
    employee_name = session.get('employee_name', 'Не указано')

    return render_template(
        'employee/employee_task.html',
        order_id=order_data.get('id'),
        client_name=order_data.get('client_name'),
        pass_id=current_user.id, # ИЗМЕНЕНИЕ: используем ID пользователя, а не ID из данных
        employee_name=employee_name, # Передаем ФИО в шаблон
        aggregation_levels=order_data.get('aggregation_levels', []) 
    )