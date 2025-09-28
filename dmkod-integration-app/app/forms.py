from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import StringField, PasswordField, SubmitField, BooleanField, SelectField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

class LoginForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')

class IntegrationForm(FlaskForm):
    client_id = SelectField('Клиент', coerce=int, validators=[DataRequired(message="Выберите клиента.")])
    product_group_id = SelectField('Товарная группа', coerce=int, validators=[DataRequired(message="Выберите товарную группу.")])
    fias_code = StringField('Код ФИАС (GUID)', validators=[Optional()])
    xls_file = FileField('Файл с заказом (XLS/XLSX)', validators=[
        FileRequired("Выберите файл с заказом."), 
        FileAllowed(['xls', 'xlsx'], 'Только XLS и XLSX файлы!')
    ])
    details_file = FileField('Файл с детализацией агрегации (XLS/XLSX)', validators=[
        Optional(),
        FileAllowed(['xls', 'xlsx'], 'Допустимы только файлы XLS и XLSX!')
    ])
    notes = TextAreaField('Примечания', validators=[Length(max=500)])
    submit = SubmitField('Создать интеграцию')

class ProductGroupForm(FlaskForm):
    group_name = StringField('Системное имя группы', validators=[DataRequired(), Length(max=100)])
    display_name = StringField('Отображаемое название', validators=[DataRequired(), Length(max=255)])
    code_template = TextAreaField('Шаблон кода (опционально)', validators=[Length(max=1000)])
    dm_template = TextAreaField('Шаблон DataMatrix (опционально)', validators=[Length(max=1000)])
    fias_required = BooleanField('Требуется адрес ФИАС')
    submit = SubmitField('Сохранить')