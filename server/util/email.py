import logging
from email.message import EmailMessage

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
    SMTPHeloError,
    SMTPTimeoutError,
    SMTPResponse,
)

from server.util.config import settings

logger = logging.getLogger('server.util.email')


class EmailNotSentError(Exception):
    """
    Thrown when an email was not sent for some reason
    """

    pass


def construct_email(
    recipients: list[str],
    bcc: list[str],
    subject: str,
    message: str,
    sender: str | None = None,
) -> EmailMessage:
    if sender is None:
        sender = settings.EMAIL.SENDER

    email = EmailMessage()
    email.set_content(message)
    email['Subject'] = subject
    email['From'] = sender
    email['To'] = ', '.join(recipients)
    email['Bcc'] = ', '.join(bcc)
    return email


async def send_message(
    recipients: list[str],
    bcc: list[str],
    subject: str,
    message: str,
    sender: str | None = None,
) -> tuple[dict[str, SMTPResponse], str]:
    email = construct_email(sender=sender, recipients=recipients, bcc=bcc, subject=subject, message=message)
    return await send_email(email)


async def send_email(email: EmailMessage, fail_on_error: bool = False) -> tuple[dict[str, SMTPResponse], str]:
    if not settings.EMAIL.ENABLED:
        raise EmailNotSentError(f'Mailing system inactive, email with subject "{email["Subject"]}" not sent to {email["To"]} (Bcc: {email["Bcc"]})')

    if email['From'] is None:
        del email['From']
        email['From'] = settings.EMAIL.SENDER

    client = SMTP(
        hostname=settings.EMAIL.SMTP_HOST,
        port=settings.EMAIL.SMTP_PORT,
        use_tls=settings.EMAIL.SMTP_TLS,
        start_tls=settings.EMAIL.SMTP_START_TLS,
        validate_certs=settings.EMAIL.SMTP_CHECK_CERT,
        username=settings.EMAIL.SMTP_USERNAME,
        password=settings.EMAIL.SMTP_PASSWORD,
    )

    logger.debug(f'Trying to send email to {email["To"]} with subject "{email["Subject"]}"')
    async with client as connection:
        status: tuple[dict[str, SMTPResponse], str] | None = None
        try:
            status = await connection.send_message(email)
            logger.debug(status)
            logger.info(f'Successfully sent email to {email["To"]} with subject "{email["Subject"]}"')
            return status

        except (
            SMTPRecipientsRefused,
            SMTPResponseException,
            ValueError,
            SMTPException,
            SMTPTimeoutError,
            SMTPAuthenticationError,
            SMTPNotSupported,
            SMTPConnectTimeoutError,
            SMTPConnectError,
            SMTPConnectResponseError,
            SMTPServerDisconnected,
            SMTPHeloError,
            SMTPSenderRefused,
        ) as e:
            logger.warning(
                f'Failed sending email to {email["To"]} (Bcc: {email["Bcc"]}) with subject "{email["Subject"]}"',
            )
            logger.error(e)

            if status and not fail_on_error:
                return status

            raise Exception(
                f'Email with subject "{email["Subject"]}" not sent to {email["To"]} (Bcc: {email["Bcc"]}) because of "{e}"',
            )
