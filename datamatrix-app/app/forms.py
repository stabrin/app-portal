from flask_wtf import FlaskForm
from wtforms import StringField, IntegerField, SubmitField
from wtforms.validators import DataRequired, NumberRange

class GenerateSsccForm(FlaskForm):
    owner = StringField('Владелец (Owner)', validators=[DataRequired(message="Укажите владельца кодов.")])
    quantity = IntegerField('Количество', validators=[DataRequired(), NumberRange(min=1, max=1000, message="Укажите количество от 1 до 1000.")])
    submit = SubmitField('Сгенерировать')