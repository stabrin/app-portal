# src/email_service.py

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email import encoders
from typing import Dict, Any, Optional, Tuple

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
            'host': 'smtp.mail.ru',
            'port': 465,
            'user': 's.tabrin@tilda.center',
            'password': 'СЕКРЕТНЫЙ_ПАРОЛЬ', # ЗАМЕНИТЕ НА РЕАЛЬНЫЙ ПАРОЛЬ
            'sender_email': 's.tabrin@tilda.center',
            'recipient': 's.tabrin@tilda.center',
            'bcc': 's.tabrin@tilda.center'
        }

    def send_email(self, to_email: str, subject: str, body_html: str, body_text: str = None, attachment: Optional[Tuple[bytes, str]] = None):
        """
        Отправляет email-сообщение.

        :param to_email: Email получателя.
        :param subject: Тема письма.
        :param body_html: Тело письма в формате HTML.
        :param body_text: Тело письма в формате простого текста (опционально, для совместимости).
        :param attachment: Кортеж (содержимое_файла_в_байтах, имя_файла).
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

        # --- ИЗМЕНЕНИЕ: Используем MIMEMultipart('mixed') для поддержки вложений ---
        msg = MIMEMultipart('mixed')
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = main_recipient # В поле "Кому" будет основной получатель

        # Создаем контейнер для текстовой и HTML частей
        msg_alternative = MIMEMultipart('alternative')
        msg.attach(msg_alternative)

        # Прикрепляем текстовую и HTML версии
        if body_text:
            msg_alternative.attach(MIMEText(body_text, 'plain'))
        msg_alternative.attach(MIMEText(body_html, 'html'))

        # --- НОВЫЙ БЛОК: Прикрепление файла ---
        if attachment:
            part = MIMEBase('application', "octet-stream")
            part.set_payload(attachment[0])
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{attachment[1]}"')
            msg.attach(part)

        try:
            with smtplib.SMTP_SSL(self.config['host'], self.config['port']) as server:
                server.login(self.config['user'], self.config['password'])
                server.sendmail(sender_email, all_recipients, msg.as_string())
            logging.info(f"Email успешно отправлен на адреса: {', '.join(all_recipients)}")
        except Exception as e:
            logging.error(f"Ошибка при отправке email на {', '.join(all_recipients)}: {e}", exc_info=True)
            raise # Передаем исключение выше для обработки