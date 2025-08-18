# manual-aggregation-app/app/forms.py

from flask_wtf import FlaskForm
from wtforms import (
    StringField, 
    PasswordField, 
    SubmitField, 
    SelectMultipleField, 
    IntegerField,
    widgets
)
from wtforms.validators import DataRequired, Regexp, NumberRange, Optional

class MultiCheckboxField(SelectMultipleField):
    """
    Кастомное поле для отображения списка чекбоксов.
    """
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()

class AdminLoginForm(FlaskForm):
    """Форма входа для администратора."""
    username = StringField(
        'Имя администратора', 
        validators=[DataRequired(message="Имя пользователя обязательно.")]
    )
    password = PasswordField(
        'Пароль', 
        validators=[DataRequired(message="Пароль обязателен.")]
    )
    submit = SubmitField('Войти')

class EmployeeTokenForm(FlaskForm):
    """
    Форма входа для сотрудника с предварительной проверкой раскладки 
    и последующим сканированием QR-кода.
    """
    last_name = StringField(
        'Фамилия И.О. (маленькими латинскими буквами)', 
        validators=[
            DataRequired(message="Пожалуйста, введите фамилию."),
            # Проверяем на сервере, что введены только строчные латинские буквы,
            # пробелы и точки. Это наша "защита", если JS не сработает.
            Regexp(
                r'^[a-z\s.]+$', 
                message="Ошибка валидации: Используйте только маленькие латинские буквы, пробел и точку."
            )
        ]
    )
    access_token = StringField(
        'Ваш код доступа (из QR)', 
        validators=[DataRequired(message="Код доступа не может быть пустым.")]
    )
    submit = SubmitField('Войти')

class OrderForm(FlaskForm):
    """Форма для создания и редактирования заказа на агрегацию."""
    client_name = StringField(
        'Имя клиента',
        validators=[DataRequired(message="Имя клиента обязательно.")]
    )
    levels = MultiCheckboxField(
        'Уровни агрегации',
        choices=[
            ('set', 'Набор (товар в наборе)'),
            ('box', 'Короб (набор/товар в коробе)'),
            ('pallet', 'Паллета (короб в паллете)')
        ]
    )
    employee_count = IntegerField(
        'Количество сотрудников',
        validators=[
            DataRequired(message="Укажите количество сотрудников."),
            NumberRange(min=1, message="Должен быть как минимум 1 сотрудник.")
        ],
        default=1
    )
    set_capacity = IntegerField(
        'Макс. товаров в наборе (необязательно)',
        validators=[
            Optional(), # Делает поле необязательным
            NumberRange(min=1, message="Значение должно быть больше нуля.")
        ],
        description="Если указано, система не даст отсканировать больше товаров для одного набора."
    )
    submit = SubmitField('Сохранить заказ')