import logging
from email.message import EmailMessage

from smtplib import (
    SMTP as SMTPSync,
    SMTP_SSL,
    SMTPHeloError as SMTPHeloErrorOrig,
    SMTPNotSupportedError,
    SMTPDataError,
    SMTPRecipientsRefused as SMTPRecipientsRefusedOrig,
    SMTPSenderRefused as SMTPSenderRefusedOrig,
    SMTPException as SMTPExceptionOrig
)
from aiosmtplib import (
    SMTP,
    SMTPResponseException,
    SMTPSenderRefused,
    SMTPRecipientsRefused,
    SMTPException,
    SMTPAuthenticationError,
    SMTPNotSupported,
    SMTPConnectTimeoutError,
    SMTPConnectError,
    SMTPConnectResponseError,
    SMTPServerDisconnected,
    SMTPHeloError, SMTPTimeoutError
)

from server.util.config import settings

logger = logging.getLogger('server.util.email')


class EmailNotSentError(Exception):
    """
    Thrown when an email was not sent for some reason
    """
    pass


def construct_email(recipients: list[str],
                    subject: str,
                    message: str,
                    sender: str | None = None) -> EmailMessage:
    if sender is None:
        sender = settings.EMAIL.SENDER

    email = EmailMessage()
    email.set_content(message)
    email['Subject'] = subject
    email['From'] = sender
    email['To'] = ', '.join(recipients)
    return email


async def send_message(recipients: list[str],
                       subject: str,
                       message: str,
                       sender: str | None = None) -> bool:
    email = construct_email(sender=sender, recipients=recipients, subject=subject, message=message)
    return await send_email(email)


async def send_email(email: EmailMessage) -> bool:
    if not settings.EMAIL.ENABLED:
        raise EmailNotSentError(f'Mailing system inactive, '
                                f'email with subject "{email["Subject"]}" not sent to {email["To"]}')

    if email['From'] is None:
        del email['From']
        email['From'] = settings.EMAIL.SENDER

    client = SMTP(hostname=settings.EMAIL.SMTP_HOST,
                  port=settings.EMAIL.SMTP_PORT,
                  use_tls=settings.EMAIL.SMTP_TLS,
                  start_tls=None,
                  username=settings.EMAIL.SMTP_USER,
                  password=settings.EMAIL.SMTP_PASSWORD)
    try:
        await client.connect()
        logger.debug(f'Trying to send email to {email["To"]} with subject "{email["Subject"]}"')
        status = await client.send_message(email)
        logger.debug(status)
        # await client.quit()  # FIXME: Is this necessary? Docs say yes, but then it doesn't work...
        logger.info(f'Successfully sent email to {email["To"]} with subject "{email["Subject"]}"')
        return True

    except (SMTPRecipientsRefused, SMTPResponseException, ValueError, SMTPException, SMTPTimeoutError,
            SMTPAuthenticationError, SMTPNotSupported, SMTPConnectTimeoutError, SMTPConnectError,
            SMTPConnectResponseError, SMTPServerDisconnected, SMTPHeloError, SMTPSenderRefused) as e:
        logger.warning(f'Failed sending email to {email["To"]} with subject "{email["Subject"]}"')
        logger.error(e)
        await client.quit()

        raise EmailNotSentError(f'Email with subject "{email["Subject"]}" not sent to {email["To"]} because of "{e}"')


def send_message_sync(recipients: list[str],
                      subject: str,
                      message: str,
                      sender: str | None = None) -> bool:
    email = construct_email(sender=sender, recipients=recipients, subject=subject, message=message)
    return send_email_sync(email)


def send_email_sync(email: EmailMessage) -> bool:
    if not settings.EMAIL.ENABLED:
        raise EmailNotSentError(f'Mailing system inactive, '
                                f'email with subject "{email["Subject"]}" not sent to {email["To"]}')

    if email['From'] is None:
        del email['From']
        email['From'] = settings.EMAIL.SENDER

    try:
        if not settings.EMAIL.SMTP_TLS:
            client = SMTP_SSL(host=settings.EMAIL.SMTP_HOST,
                              port=settings.EMAIL.SMTP_PORT)
        else:
            client = SMTPSync(host=settings.EMAIL.SMTP_HOST,
                              port=settings.EMAIL.SMTP_PORT)

        with client as smtp:
            smtp.login(user=settings.EMAIL.SMTP_USER,
                       password=settings.EMAIL.SMTP_PASSWORD)
            smtp.connect()
            logger.info(f'Trying to send email to {email["To"]} with subject "{email["Subject"]}"')
            status = smtp.send_message(email)
            logger.debug(status)
            logger.info(f'Successfully sent email to {email["To"]} with subject "{email["Subject"]}"')

            return True

    except (SMTPHeloErrorOrig, SMTPRecipientsRefusedOrig, SMTPSenderRefusedOrig,
            SMTPDataError, SMTPNotSupportedError, SMTPExceptionOrig) as e:
        logger.warning(f'Failed sending email to {email["To"]} with subject "{email["Subject"]}"')
        logger.error(e)
        raise EmailNotSentError(f'Email with subject "{email["Subject"]}" not sent to {email["To"]} because of "{e}"')
