"""Authenticated encryption for the installation's SMTP password."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SmtpDecryptionError(Exception):
    pass


def _cipher(purpose='smtp-password'):
    # Separate this key's purpose from the existing GitHub token cipher.
    material = (f'rkd-dockestra-core:{purpose}:' + settings.SECRET_KEY).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def encrypt_smtp_password(password):
    return _cipher().encrypt(password.encode()).decode('ascii')


def decrypt_smtp_password(encrypted):
    try:
        return _cipher().decrypt(encrypted.encode('ascii')).decode()
    except (InvalidToken, UnicodeError, ValueError):
        raise SmtpDecryptionError('A credencial SMTP salva não pôde ser decifrada.') from None


def encrypt_email(email, *, smtp=False):
    purpose = 'smtp-email' if smtp else 'operator-email'
    return _cipher(purpose).encrypt(email.encode()).decode('ascii')


def decrypt_email(encrypted, *, smtp=False):
    purpose = 'smtp-email' if smtp else 'operator-email'
    try:
        return _cipher(purpose).decrypt(encrypted.encode('ascii')).decode()
    except (InvalidToken, UnicodeError, ValueError):
        raise SmtpDecryptionError('O e-mail salvo não pôde ser decifrado.') from None
