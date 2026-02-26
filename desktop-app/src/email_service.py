# src/email_service.py

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any

class EmailService:
    """
    Сервис для отправки email-уведомлений.
    """
    def __init__(self, smtp_config: Dict[str, Any] = None):
        """
        Инициализирует сервис с настройками SMTP.

        :param smtp_config: Словарь с ключами 'host', 'port', 'user', 'password', 'sender_email'.
        """
        # --- ИЗМЕНЕНИЕ: Жестко задаем все параметры ---
        self.config = {
            'host': 'mail.it-workshop.ru',
            'port': 465,
            'user': 'tilda@it-workshop.ru',
            'password': 'Rv3a$3', # ЗАМЕНИТЕ НА РЕАЛЬНЫЙ ПАРОЛЬ
            'sender_email': 'tilda@it-workshop.ru',
            'recipient': 'tabrin@ved-ug.ru',
            'bcc': 'sergey@tabrin.ru'
        }

    def send_email(self, to_email: str, subject: str, body_html: str, body_text: str = None):
        """
        Отправляет email-сообщение.

        :param to_email: Email получателя.
        :param subject: Тема письма.
        :param body_html: Тело письма в формате HTML.
        :param body_text: Тело письма в формате простого текста (опционально, для совместимости).
        """
        if not self.config.get('host'):
            logging.warning("SMTP хост не настроен или конфигурация неполная. Отправка email пропущена.")
            return

        # --- ИЗМЕНЕНИЕ: Используем жестко заданные адреса ---
        sender_email = self.config['sender_email']
        main_recipient = self.config['recipient']
        bcc_recipient = self.config.get('bcc')

        all_recipients = [main_recipient]
        if bcc_recipient:
            all_recipients.append(bcc_recipient)

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = main_recipient # В поле "Кому" будет основной получатель

        # Прикрепляем текстовую и HTML версии
        if body_text:
            msg.attach(MIMEText(body_text, 'plain'))
        msg.attach(MIMEText(body_html, 'html'))
        try:
            with smtplib.SMTP_SSL(self.config['host'], self.config['port']) as server:
                server.login(self.config['user'], self.config['password'])
                server.sendmail(sender_email, all_recipients, msg.as_string())
            logging.info(f"Email успешно отправлен на адреса: {', '.join(all_recipients)}")
        except Exception as e:
            logging.error(f"Ошибка при отправке email на {', '.join(all_recipients)}: {e}", exc_info=True)
            raise # Передаем исключение выше для обработки