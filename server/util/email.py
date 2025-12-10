import asyncio
import logging
import ssl
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
    SMTPStatus,
)
from aiosmtplib.protocol import SMTPProtocol

from server.util.config import settings

logger = logging.getLogger('server.util.email')


# This is just a hot-fix patch to allow us to set an alternative server hostname;
# on port forwarding, hosts between cert and localhost would mismatch
async def _create_connection(self, timeout: float | None) -> SMTPResponse:
    if self.loop is None:
        raise RuntimeError('No event loop set')

    protocol = SMTPProtocol(loop=self.loop, connection_lost_callback=self.close)

    tls_context: ssl.SSLContext | None = None
    ssl_handshake_timeout: float | None = None
    if self.use_tls:
        tls_context = self._get_tls_context()
        ssl_handshake_timeout = timeout

    if self.hostname is None:
        raise RuntimeError('No hostname provided; default should have been set')
    if self.port is None:
        raise RuntimeError('No port provided; default should have been set')

    connect_coro = self.loop.create_connection(
        lambda: protocol,
        host=self.hostname,
        port=self.port,
        ssl=tls_context,
        ssl_handshake_timeout=ssl_handshake_timeout,
        local_addr=self.source_address,
        server_hostname=settings.EMAIL.SMTP_REMOTE_HOST,
    )

    try:
        transport, _ = await asyncio.wait_for(connect_coro, timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise SMTPConnectTimeoutError(
            f'Timed out connecting to {self.hostname} on port {self.port}',
        ) from exc
    except OSError as exc:
        raise SMTPConnectError(
            f'Error connecting to {self.hostname} on port {self.port}: {exc}',
        ) from exc

    self.protocol = protocol
    self.transport = transport

    try:
        response = await protocol.read_response(timeout=timeout)
    except SMTPServerDisconnected as exc:
        raise SMTPConnectError(
            f'Error connecting to {self.hostname} on port {self.port}: {exc}',
        ) from exc
    except SMTPTimeoutError as exc:
        raise SMTPConnectTimeoutError(
            'Timed out waiting for server ready message',
        ) from exc

    if response.code != SMTPStatus.ready:
        raise SMTPConnectResponseError(response.code, response.message)

    return response


SMTP._create_connection = _create_connection


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
