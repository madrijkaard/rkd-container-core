"""Encrypt GitHub tokens before storing them in image records."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TokenDecryptionError(Exception):
    pass


def _cipher(prefix='rkd-dockestra-core:image-token:'):
    material = (prefix + settings.SECRET_KEY).encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def encrypt_token(token):
    return _cipher().encrypt(token.encode()).decode()


def decrypt_token(encrypted):
    # Tokens gravados antes da mudança de nome continuam válidos.
    for prefix in ('rkd-dockestra-core:image-token:', 'container-core:image-token:'):
        try:
            return _cipher(prefix).decrypt(encrypted.encode()).decode()
        except (InvalidToken, UnicodeDecodeError):
            continue
    raise TokenDecryptionError('O token salvo não pôde ser decifrado.')
