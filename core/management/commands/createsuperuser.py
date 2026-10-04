"""Create operators or recover RKD after verifying ownership of their email."""

import getpass
import secrets
import string
import time
import warnings
from copy import copy
from smtplib import SMTPAuthenticationError, SMTPException

from django.contrib.auth.management.commands.createsuperuser import Command as DjangoCommand
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.management.base import CommandError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from core.models import OperatorEmail, SmtpCredential
from core.smtp_cipher import (
    SmtpDecryptionError, decrypt_email, decrypt_smtp_password, encrypt_email, encrypt_smtp_password,
)


INITIAL_USERNAME = 'RKD'
CODE_LENGTH = 16
CODE_TTL_SECONDS = 300
MAX_ATTEMPTS = 3


def generate_verification_code():
    return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(CODE_LENGTH))


class Command(DjangoCommand):
    help = 'Cria um superusuário ou recupera RKD por e-mail. O primeiro usuário deve ser RKD.'
    stores_smtp_password_in_database = True
    stores_email_in_database = True
    confirms_smtp_password = True
    verification_policy = (CODE_LENGTH, MAX_ATTEMPTS, CODE_TTL_SECONDS)

    def handle(self, *args, **options):
        if not options['interactive'] or not self.stdin.isatty():
            raise CommandError('Use um terminal interativo: a confirmação por e-mail é obrigatória.')

        database = options['database']
        self.database = database
        self.pending_smtp_credential = None
        users = self.UserModel._default_manager.db_manager(database)
        username = options.get(self.UserModel.USERNAME_FIELD) or INITIAL_USERNAME
        if not users.exists() and username != INITIAL_USERNAME:
            raise CommandError('O primeiro usuário deve ser RKD. Execute com --username RKD.')
        try:
            username = self.username_field.clean(username, None)
        except ValidationError as error:
            raise CommandError('; '.join(error.messages)) from None
        existing = users.filter(**{self.UserModel.USERNAME_FIELD: username}).first()
        if existing is not None:
            if username == INITIAL_USERNAME:
                return self.recover_account(existing, users, options.get('email'))
            raise CommandError(f'O usuário {username} já existe. Nenhuma conta ou senha foi alterada.')

        try:
            email = self.read_email(options.get('email'))
            sender_email = email if username == INITIAL_USERNAME else self.read_saved_email(
                users.filter(username=INITIAL_USERNAME).first(),
            )[1]
            self.prepare_smtp_password(sender_email)
            self.verify_email(email)
            password = self.read_password(username, email)
        except (EOFError, KeyboardInterrupt):
            raise CommandError('Cadastro cancelado. Nenhum usuário foi criado.') from None

        # Never hold a database transaction while waiting for SMTP or terminal input.
        try:
            with transaction.atomic(using=database):
                user = users.create_superuser(username=username, email='', password=password)
                OperatorEmail.objects.using(database).create(user=user, encrypted_email=encrypt_email(email))
                self.save_smtp_credential()
        except IntegrityError:
            raise CommandError('O usuário já foi cadastrado por outra execução. Nenhuma senha foi alterada.') from None
        self.stdout.write(self.style.SUCCESS(f'Superusuário {username} criado com e-mail confirmado.'))

    def recover_account(self, user, users, supplied_email):
        if not (user.is_superuser and user.is_staff and user.is_active):
            raise CommandError('RKD existe, mas não é um superusuário ativo com acesso de operador. Revise a conta na administração.')
        email_record, email = self.read_saved_email(user)
        if supplied_email is not None and supplied_email.strip() != email:
            raise CommandError('A recuperação usa somente o e-mail já cadastrado de RKD; não é possível substituí-lo neste processo.')

        self.stdout.write('O superusuário RKD já existe. Iniciando a recuperação pelo e-mail cadastrado.')
        try:
            self.prepare_smtp_password(email)
            self.verify_email(email, recovery=True)
            password = self.read_password(user.username, email, recovering_user=user)
        except (EOFError, KeyboardInterrupt):
            raise CommandError('Recuperação cancelada. A senha anterior foi preservada.') from None

        # Compare and update atomically: a code issued for an old email/password
        # must not overwrite a concurrent account change or completed recovery.
        previous_hash = user.password
        user.set_password(password)
        with transaction.atomic(using=self.database):
            updated = users.filter(
                pk=user.pk, username=user.username, email=user.email, password=previous_hash,
                is_active=True, is_staff=True, is_superuser=True,
                operator_email__pk=email_record.pk, operator_email__encrypted_email=email_record.encrypted_email,
            ).update(password=user.password)
            if updated != 1:
                raise CommandError('A conta RKD foi alterada durante a recuperação. Execute novamente para receber um novo código.')
            self.save_smtp_credential()
        self.stdout.write(self.style.SUCCESS('Acesso de RKD recuperado. Entre com a nova senha.'))

    def read_saved_email(self, user):
        record = OperatorEmail.objects.using(self.database).filter(user=user).first() if user else None
        if record is None:
            raise CommandError('RKD não possui um e-mail válido cadastrado. A recuperação exige acesso ao e-mail original.')
        try:
            email = decrypt_email(record.encrypted_email)
        except SmtpDecryptionError:
            raise CommandError('O e-mail cadastrado não pôde ser decifrado. Restaure a DJANGO_SECRET_KEY original; a conta não foi alterada.') from None
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError('RKD não possui um e-mail válido cadastrado. A recuperação exige acesso ao e-mail original.') from None
        return record, email

    def prepare_smtp_password(self, email, force_prompt=False):
        self.smtp_email = email
        credential = SmtpCredential.objects.using(self.database).filter(pk=1).first()
        self.smtp_from_database = False
        if not force_prompt and credential:
            try:
                if decrypt_email(credential.encrypted_email, smtp=True) == email:
                    self.smtp_password = decrypt_smtp_password(credential.encrypted_password)
                    if self.smtp_password:
                        self.smtp_from_database = True
                        return
            except SmtpDecryptionError:
                self.stderr.write('A credencial SMTP não pôde ser decifrada com a chave atual. Informe a senha de app novamente.')
        self.stdout.write(f'Configure a senha de app do Gmail remetente: {email}.')
        while True:
            # Do not fall back to visibly echoing a secret on an unsuitable terminal.
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                try:
                    value = getpass.getpass('EMAIL_HOST_PASSWORD (senha de app do Gmail, sem espaços; digitação oculta): ')
                    confirmation = getpass.getpass('Confirme EMAIL_HOST_PASSWORD (sem espaços; digitação oculta): ')
                except getpass.GetPassWarning:
                    raise CommandError('Este terminal não permite ocultar a senha. Use um terminal interativo compatível.') from None
            if not value or not confirmation:
                self.stderr.write('A senha de app e sua confirmação não podem ser vazias.')
                continue
            if any(character.isspace() for character in value + confirmation):
                self.stderr.write('Digite a senha de app e sua confirmação sem espaços.')
                continue
            if not secrets.compare_digest(value.encode('utf-8'), confirmation.encode('utf-8')):
                self.stderr.write('As senhas de app não coincidem. Digite as duas novamente.')
                continue
            self.smtp_password = value
            self.pending_smtp_credential = {
                'encrypted_email': encrypt_email(email, smtp=True),
                'encrypted_password': encrypt_smtp_password(value),
            }
            return

    def save_smtp_credential(self):
        # Save only after verification, in the same transaction as the account.
        if self.pending_smtp_credential is not None:
            SmtpCredential.objects.using(self.database).update_or_create(pk=1, defaults=self.pending_smtp_credential)

    def read_email(self, supplied):
        while True:
            value = supplied if supplied is not None else input('E-mail: ')
            try:
                value = self.UserModel._meta.get_field('email').clean(value.strip(), None)
                validate_email(value)  # Unlike Django's default, an empty email is not allowed.
                return value
            except ValidationError:
                if supplied is not None:
                    raise CommandError('Informe um endereço de e-mail válido e não vazio.') from None
                self.stderr.write('Informe um endereço de e-mail válido e não vazio.')

    def verify_email(self, email, recovery=False):
        code = generate_verification_code()
        self.send_verification_code(email, code, recovery)
        deadline = time.monotonic() + CODE_TTL_SECONDS
        self.stdout.write(f'Código enviado para {email}. Verifique também o spam. Validade: 5 minutos. Máximo: 3 tentativas.')
        for attempt in range(MAX_ATTEMPTS):
            supplied = input('Código recebido (16 caracteres, sem hífens ou espaços, respeitando maiúsculas/minúsculas): ')
            if time.monotonic() >= deadline:
                raise CommandError('Código expirado após 5 minutos. Execute ./create-superuser.sh novamente para receber outro código.')
            if secrets.compare_digest(supplied.encode('utf-8'), code.encode('ascii')):
                self.stdout.write('E-mail confirmado. Agora defina a nova senha de acesso.' if recovery
                                  else 'E-mail confirmado. Agora defina a senha de acesso.')
                return
            remaining = MAX_ATTEMPTS - attempt - 1
            if remaining:
                self.stderr.write(f'Código incorreto. Tentativas restantes: {remaining}.')
        raise CommandError('Limite de tentativas atingido: 3 códigos incorretos. Nenhuma conta ou senha foi alterada. Execute ./create-superuser.sh novamente para receber outro código.')

    def send_verification_code(self, email, code, recovery):
        formatted_code = '-'.join(code[index:index + 4] for index in range(0, len(code), 4))
        try:
            sent = send_mail(
                'Dockestra — recuperação de acesso RKD' if recovery else 'Dockestra — confirme seu e-mail',
                f'Seu código de confirmação é:\n\n{formatted_code}\n\n'
                'Os hífens servem apenas para facilitar a leitura. '
                'Digite os 16 caracteres no terminal, sem hífens ou espaços, respeitando '
                'maiúsculas e minúsculas. Ele expira em 5 minutos e permite no máximo 3 tentativas. '
                'Se expirar ou você errar 3 vezes, execute ./create-superuser.sh novamente para receber outro código.\n'
                'Se você não iniciou esta solicitação, ignore esta mensagem.',
                self.smtp_email,
                [email],
                fail_silently=False,
                auth_user=self.smtp_email,
                auth_password=self.smtp_password,
            )
        except SMTPAuthenticationError as error:
            if self.smtp_from_database:
                self.stderr.write('O Gmail recusou a credencial salva. Informe uma nova senha de app para tentar novamente.')
                self.prepare_smtp_password(self.smtp_email, force_prompt=True)
                return self.send_verification_code(email, code, recovery)
            # Report only the numeric status, never the provider's raw response.
            raise CommandError(
                f'Não foi possível enviar o código: autenticação SMTP recusada (código {error.smtp_code}). '
                'Confira o e-mail cadastrado e use em EMAIL_HOST_PASSWORD uma senha de app válida '
                'gerada na mesma conta Google, não a senha normal do Gmail. '
                'Execute scripts/create-superuser.sh novamente para informar a senha de app no terminal. '
                'Nenhuma conta ou senha foi alterada.'
            ) from None
        except (SMTPException, OSError, ValueError):
            # SMTP exceptions can include sensitive provider responses; do not echo them.
            raise CommandError(
                'Não foi possível enviar o código. Confira a configuração SMTP do Gmail '
                'e a conexão. Nenhuma conta ou senha foi alterada.'
            ) from None
        if sent != 1:
            raise CommandError('O envio do código não foi confirmado. Nenhuma conta ou senha foi alterada.')

    def read_password(self, username, email, recovering_user=None):
        while True:
            password = getpass.getpass('Nova senha: ' if recovering_user is not None else 'Senha: ')
            confirmation = getpass.getpass('Confirme a senha: ')
            if password != confirmation:
                self.stderr.write('As senhas não coincidem.')
                continue
            if not password.strip():
                self.stderr.write('A senha não pode ser vazia.')
                continue
            if recovering_user is not None and recovering_user.check_password(password):
                self.stderr.write('Escolha uma senha diferente da senha atual.')
                continue
            try:
                validation_user = copy(recovering_user) if recovering_user is not None else self.UserModel(username=username)
                validation_user.email = email
                validate_password(password, validation_user)
            except ValidationError as error:
                self.stderr.write('; '.join(error.messages))
                continue
            return password
