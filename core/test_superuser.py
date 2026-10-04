"""Email ownership must be proven before any operator account is saved."""

import re
from io import StringIO
from smtplib import SMTPAuthenticationError, SMTPException
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from .management.commands.createsuperuser import generate_verification_code
from .models import OperatorEmail, SmtpCredential
from .smtp_cipher import decrypt_email, encrypt_email, encrypt_smtp_password


MODULE = 'core.management.commands.createsuperuser'
CODE = 'a7B2xQ9mZ1y2X3w4'
FORMATTED_CODE = 'a7B2-xQ9m-Z1y2-X3w4'
PASSWORD = 'A-strong-test-password-42!'
NEW_PASSWORD = 'A-new-access-password-73!'


class Terminal(StringIO):
    def isatty(self):
        return True


class EncryptedEmailTestMixin:
    def create_user(self, username, email, password):
        user = self.users.create_superuser(username, '', password)
        OperatorEmail.objects.create(user=user, encrypted_email=encrypt_email(email))
        if username == 'RKD':
            SmtpCredential.objects.filter(pk=1).update(encrypted_email=encrypt_email(email, smtp=True))
        return user


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='sender@gmail.com',
    DEFAULT_FROM_EMAIL='sender@gmail.com',
)
class VerifiedSuperuserTests(EncryptedEmailTestMixin, TestCase):
    def setUp(self):
        self.output = StringIO()
        self.errors = StringIO()
        self.users = get_user_model().objects
        SmtpCredential.objects.create(encrypted_email=encrypt_email('owner@example.com', smtp=True), encrypted_password=encrypt_smtp_password('test-app-password'))

    def command(self, **kwargs):
        options = {'stdin': Terminal(), 'stdout': self.output, 'stderr': self.errors}
        options.update(kwargs)
        call_command('createsuperuser', **options)

    def respond_with_email_code(self, prompt):
        self.assertFalse(self.users.exists())
        if prompt == 'E-mail: ':
            return 'owner@example.com'
        self.assertEqual(len(mail.outbox), 1)
        return re.search(r'(?m)^[A-Za-z0-9]{4}(?:-[A-Za-z0-9]{4}){3}$', mail.outbox[0].body).group().replace('-', '')

    def test_command_override_is_discovered_and_creates_rkd_only_after_verification(self):
        self.assertEqual(get_commands()['createsuperuser'], 'core')
        with patch('builtins.input', side_effect=self.respond_with_email_code), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[PASSWORD, PASSWORD]):
            self.command()
        user = self.users.get()
        self.assertEqual(user.username, 'RKD')
        self.assertEqual(user.email, '')
        self.assertEqual(decrypt_email(user.operator_email.encrypted_email), 'owner@example.com')
        self.assertTrue(user.is_active and user.is_staff and user.is_superuser)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(mail.outbox[0].to, ['owner@example.com'])
        self.assertEqual(mail.outbox[0].from_email, 'owner@example.com')
        code = re.search(r'(?m)^[A-Za-z0-9]{4}(?:-[A-Za-z0-9]{4}){3}$', mail.outbox[0].body).group()
        self.assertNotIn(code, self.output.getvalue() + self.errors.getvalue())
        self.assertNotIn(PASSWORD, self.output.getvalue() + self.errors.getvalue())

    def test_another_first_username_is_rejected_before_sending_email(self):
        with patch(f'{MODULE}.send_mail') as send:
            with self.assertRaisesMessage(CommandError, 'primeiro usuário deve ser RKD'):
                self.command(username='OTHER')
        send.assert_not_called()
        self.assertFalse(self.users.exists())

    def test_existing_rkd_is_recovered_using_its_registered_email(self):
        user = self.create_user('RKD', 'original@example.com', PASSWORD)
        self.client.force_login(user)
        user.refresh_from_db()
        original = self.users.values().get(pk=user.pk)

        def confirm(prompt):
            self.assertNotEqual(prompt, 'E-mail: ')
            self.assertEqual(self.users.get(pk=user.pk).password, original['password'])
            self.assertEqual(mail.outbox[0].to, ['original@example.com'])
            return CODE

        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', side_effect=confirm), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[NEW_PASSWORD, NEW_PASSWORD]):
            self.command(username='RKD')
        user.refresh_from_db()
        self.assertEqual(self.users.count(), 1)
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertFalse(user.check_password(PASSWORD))
        current = self.users.values().get(pk=user.pk)
        self.assertEqual({k: v for k, v in current.items() if k != 'password'},
                         {k: v for k, v in original.items() if k != 'password'})
        self.assertIn('recuperação', mail.outbox[0].subject)
        self.assertIn('recuperado', self.output.getvalue())
        self.assertNotIn(CODE, self.output.getvalue())
        self.assertNotIn(NEW_PASSWORD, self.output.getvalue())
        # Django invalidates existing authenticated sessions after the hash changes.
        self.assertFalse(self.client.get('/api/auth/session/').json()['authenticated'])
        self.assertFalse(self.client.login(username='RKD', password=PASSWORD))
        self.assertTrue(self.client.login(username='RKD', password=NEW_PASSWORD))

    def test_recovery_cannot_replace_registered_email(self):
        user = self.create_user('RKD', 'original@example.com', PASSWORD)
        with patch(f'{MODULE}.send_mail') as send:
            with self.assertRaisesMessage(CommandError, 'somente o e-mail já cadastrado'):
                self.command(username='RKD', email='different@example.com')
        send.assert_not_called()
        user.refresh_from_db()
        self.assertEqual(user.email, '')
        self.assertEqual(decrypt_email(user.operator_email.encrypted_email), 'original@example.com')
        self.assertTrue(user.check_password(PASSWORD))

    def test_recovery_requires_a_valid_existing_email(self):
        user = self.create_user('RKD', '', PASSWORD)
        for email in ('', 'invalid'):
            with self.subTest(email=email):
                OperatorEmail.objects.filter(user=user).update(encrypted_email=encrypt_email(email))
                with patch(f'{MODULE}.send_mail') as send:
                    with self.assertRaisesMessage(CommandError, 'e-mail válido cadastrado'):
                        self.command(email='new@example.com')
                send.assert_not_called()
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_recovery_does_not_reactivate_or_promote_an_existing_user(self):
        user = self.create_user('RKD', 'original@example.com', PASSWORD)
        for field in ('is_active', 'is_staff', 'is_superuser'):
            with self.subTest(field=field):
                flags = {'is_active': True, 'is_staff': True, 'is_superuser': True}
                flags[field] = False
                self.users.filter(pk=user.pk).update(**flags)
                with patch(f'{MODULE}.send_mail') as send:
                    with self.assertRaisesMessage(CommandError, 'não é um superusuário ativo'):
                        self.command()
                send.assert_not_called()
                user.refresh_from_db()
                self.assertFalse(getattr(user, field))
                self.assertTrue(user.check_password(PASSWORD))

    def test_recovery_wrong_code_or_expiration_preserves_existing_password(self):
        user = self.create_user('RKD', 'original@example.com', PASSWORD)
        for codes, clock, message in ((['wrong'] * 3, [100] * 4, 'Limite de tentativas'),
                                       ([CODE], [100, 400], 'Código expirado')):
            with self.subTest(message=message), \
                    patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                    patch(f'{MODULE}.time.monotonic', side_effect=clock), \
                    patch('builtins.input', side_effect=codes), \
                    patch(f'{MODULE}.getpass.getpass') as password:
                with self.assertRaisesMessage(CommandError, message):
                    self.command()
                password.assert_not_called()
                user.refresh_from_db()
                self.assertTrue(user.check_password(PASSWORD))

    def test_recovery_cancelled_at_code_or_password_preserves_existing_password(self):
        user = self.create_user('RKD', 'original@example.com', PASSWORD)
        for interruption in (EOFError, KeyboardInterrupt):
            with self.subTest(interruption=interruption):
                with patch('builtins.input', side_effect=interruption):
                    with self.assertRaisesMessage(CommandError, 'Recuperação cancelada'):
                        self.command()
                with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                        patch('builtins.input', return_value=CODE), \
                        patch(f'{MODULE}.getpass.getpass', side_effect=interruption):
                    with self.assertRaisesMessage(CommandError, 'Recuperação cancelada'):
                        self.command()
                user.refresh_from_db()
                self.assertTrue(user.check_password(PASSWORD))

    def test_recovery_smtp_failure_preserves_existing_password(self):
        user = self.create_user('RKD', 'original@example.com', PASSWORD)
        with patch(f'{MODULE}.send_mail', side_effect=SMTPAuthenticationError(535, b'private-response')), \
                patch(f'{MODULE}.getpass.getpass', return_value='replacement-app-password'):
            with self.assertRaisesMessage(CommandError, 'Não foi possível enviar'):
                self.command()
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_recovery_rejects_reusing_current_password(self):
        user = self.create_user('RKD', 'original@example.com', PASSWORD)
        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', return_value=CODE), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[PASSWORD, PASSWORD, NEW_PASSWORD, NEW_PASSWORD]):
            self.command()
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertIn('diferente da senha atual', self.errors.getvalue())

    def test_recovery_does_not_overwrite_concurrent_account_changes(self):
        user = self.create_user('RKD', 'original@example.com', PASSWORD)
        original = self.users.values().get(pk=user.pk)
        changes = ({'email': 'changed@example.com'}, {'password': 'changed-by-another-recovery'},
                   {'is_active': False}, {'is_staff': False}, {'is_superuser': False}, {'username': 'RENAMED'})
        for change in changes:
            with self.subTest(change=change):
                self.users.filter(pk=user.pk).update(**{k: v for k, v in original.items() if k != 'id'})

                def new_password(*args, **kwargs):
                    self.users.filter(pk=user.pk).update(**change)
                    return NEW_PASSWORD

                with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                        patch('builtins.input', return_value=CODE), \
                        patch(f'{MODULE}.Command.read_password', side_effect=new_password):
                    with self.assertRaisesMessage(CommandError, 'alterada durante a recuperação'):
                        self.command()
                self.assertEqual(self.users.values().get(pk=user.pk), {**original, **change})

    def test_existing_other_superuser_is_not_recovered(self):
        user = self.create_user('SECOND', 'second@example.com', PASSWORD)
        with patch(f'{MODULE}.send_mail') as send:
            with self.assertRaisesMessage(CommandError, 'já existe'):
                self.command(username='SECOND')
        send.assert_not_called()
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_later_superusers_also_need_email_verification(self):
        self.create_user('RKD', 'first@example.com', PASSWORD)
        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', return_value=CODE), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[PASSWORD, PASSWORD]):
            self.command(username='SECOND', email='second@example.com')
        self.assertEqual(self.users.count(), 2)
        self.assertEqual(mail.outbox[0].to, ['second@example.com'])

    def test_noinput_or_non_tty_cannot_bypass_verification(self):
        for options in ({'interactive': False}, {'stdin': StringIO()}):
            with self.subTest(options=options), patch(f'{MODULE}.send_mail') as send:
                with self.assertRaisesMessage(CommandError, 'terminal interativo'):
                    self.command(username='RKD', email='owner@example.com', **options)
                send.assert_not_called()
        self.assertFalse(self.users.exists())

    def test_wrong_case_hyphens_and_unicode_do_not_verify(self):
        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', side_effect=[CODE.upper(), FORMATTED_CODE, 'ç', '', 'wrong']), \
                patch(f'{MODULE}.getpass.getpass') as password:
            with self.assertRaisesMessage(CommandError, 'Limite de tentativas'):
                self.command(email='owner@example.com')
        password.assert_not_called()
        self.assertFalse(self.users.exists())

    def test_wrong_code_then_correct_code_succeeds(self):
        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', side_effect=['wrong', CODE]), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[PASSWORD, PASSWORD]):
            self.command(email='owner@example.com')
        self.assertEqual(self.users.count(), 1)
        self.assertIn('Tentativas restantes: 2', self.errors.getvalue())

    def test_third_error_ends_execution_without_a_fourth_prompt_or_another_email(self):
        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', side_effect=['wrong', 'wrong', 'wrong', CODE]) as answer, \
                patch(f'{MODULE}.getpass.getpass') as password:
            with self.assertRaisesMessage(CommandError, 'Execute ./create-superuser.sh novamente'):
                self.command(email='owner@example.com')
        self.assertEqual(answer.call_count, 3)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Tentativas restantes: 2', self.errors.getvalue())
        self.assertIn('Tentativas restantes: 1', self.errors.getvalue())
        self.assertIn('5 minutos', mail.outbox[0].body)
        self.assertIn('3 tentativas', mail.outbox[0].body)
        password.assert_not_called()
        self.assertFalse(self.users.exists())

    def test_third_attempt_just_before_five_minutes_can_still_succeed(self):
        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch(f'{MODULE}.time.monotonic', side_effect=[100, 101, 102, 399.999]), \
                patch('builtins.input', side_effect=['wrong', 'wrong', CODE]), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[PASSWORD, PASSWORD]):
            self.command(email='owner@example.com')
        self.assertTrue(self.users.get().check_password(PASSWORD))

    def test_email_groups_sixteen_characters_but_terminal_requires_unformatted_code(self):
        def answer(prompt):
            self.assertIn('16 caracteres, sem hífens ou espaços', prompt)
            self.assertIn('\n\n' + FORMATTED_CODE + '\n\n', mail.outbox[0].body)
            self.assertIn('sem hífens ou espaços', mail.outbox[0].body)
            self.assertFalse(self.users.exists())
            return CODE

        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', side_effect=answer), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[PASSWORD, PASSWORD]):
            self.command(email='owner@example.com')
        self.assertTrue(self.users.get().check_password(PASSWORD))

    def test_code_with_wrong_length_is_rejected_before_password_is_requested(self):
        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', side_effect=[CODE[:8], CODE[:-1], CODE + 'x', FORMATTED_CODE, '']), \
                patch(f'{MODULE}.getpass.getpass') as password:
            with self.assertRaisesMessage(CommandError, 'Limite de tentativas'):
                self.command(email='owner@example.com')
        password.assert_not_called()
        self.assertFalse(self.users.exists())

    def test_email_code_with_extra_whitespace_is_rejected_until_exact_match(self):
        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', side_effect=[' ' + CODE, CODE + ' ', CODE]), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[PASSWORD, PASSWORD]):
            self.command(email='owner@example.com')
        self.assertEqual(self.users.count(), 1)
        self.assertEqual(self.errors.getvalue().count('Código incorreto.'), 2)

    def test_expiry_is_checked_after_waiting_for_input(self):
        for response_time in (400, 401):
            with self.subTest(response_time=response_time), \
                    patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                    patch(f'{MODULE}.time.monotonic', side_effect=[100, response_time]), \
                    patch('builtins.input', return_value=CODE), \
                    patch(f'{MODULE}.getpass.getpass') as password:
                with self.assertRaisesMessage(CommandError, 'Código expirado após 5 minutos') as caught:
                    self.command(email='owner@example.com')
            self.assertIn('Execute ./create-superuser.sh novamente', str(caught.exception))
            password.assert_not_called()
        self.assertFalse(self.users.exists())

    def test_new_execution_uses_a_new_code_and_rejects_the_previous_one(self):
        new_code = 'Z1y2X3w4a7B2xQ9m'
        with patch(f'{MODULE}.generate_verification_code', side_effect=[CODE, new_code]), \
                patch('builtins.input', side_effect=['wrong'] * 3 + [CODE, new_code]), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[PASSWORD, PASSWORD]):
            with self.assertRaisesMessage(CommandError, 'Limite de tentativas'):
                self.command(email='owner@example.com')
            self.command(email='owner@example.com')
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn(FORMATTED_CODE, mail.outbox[0].body)
        self.assertIn('Z1y2-X3w4-a7B2-xQ9m', mail.outbox[1].body)
        self.assertEqual(self.users.count(), 1)

    def test_smtp_failure_does_not_create_user_or_expose_provider_details(self):
        with patch(f'{MODULE}.send_mail', side_effect=SMTPAuthenticationError(535, b'sensitive-provider-details')), \
                patch(f'{MODULE}.getpass.getpass', return_value='replacement-app-password'):
            with self.assertRaisesMessage(CommandError, 'Não foi possível enviar') as caught:
                self.command(email='owner@example.com')
        self.assertNotIn('sensitive-provider-details', str(caught.exception))
        self.assertIn('autenticação SMTP recusada (código 535)', str(caught.exception))
        self.assertIn('senha de app válida', str(caught.exception))
        self.assertIn('scripts/create-superuser.sh', str(caught.exception))
        self.assertFalse(self.users.exists())

    def test_other_smtp_failures_do_not_claim_invalid_credentials_or_expose_details(self):
        for error in (SMTPException('private-response'), OSError('private-response')):
            with self.subTest(error=type(error).__name__), patch(f'{MODULE}.send_mail', side_effect=error):
                with self.assertRaisesMessage(CommandError, 'Não foi possível enviar') as caught:
                    self.command(email='owner@example.com')
                self.assertNotIn('private-response', str(caught.exception))
                self.assertNotIn('autenticação SMTP recusada', str(caught.exception))
        self.assertFalse(self.users.exists())

    def test_unconfirmed_send_does_not_create_user(self):
        with patch(f'{MODULE}.send_mail', return_value=0):
            with self.assertRaisesMessage(CommandError, 'envio do código não foi confirmado'):
                self.command(email='owner@example.com')
        self.assertFalse(self.users.exists())

    @override_settings(EMAIL_HOST_USER='', DEFAULT_FROM_EMAIL='')
    def test_missing_smtp_environment_uses_encrypted_sender(self):
        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', return_value=CODE), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[PASSWORD, PASSWORD]):
            self.command(email='owner@example.com')
        self.assertEqual(mail.outbox[0].from_email, 'owner@example.com')

    def test_email_is_required_and_validated_even_when_supplied_as_an_option(self):
        for email in ('', 'invalid', 'owner@example.com\nBcc: other@example.com'):
            with self.subTest(email=email), patch(f'{MODULE}.send_mail') as send:
                with self.assertRaisesMessage(CommandError, 'e-mail válido'):
                    self.command(email=email)
                send.assert_not_called()
        self.assertFalse(self.users.exists())

    def test_invalid_email_can_be_corrected_before_any_email_is_sent(self):
        def answer(prompt):
            if prompt == 'E-mail: ':
                return next(emails)
            return self.respond_with_email_code(prompt)
        emails = iter(['', 'invalid', 'owner@example.com'])
        with patch('builtins.input', side_effect=answer), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[PASSWORD, PASSWORD]):
            self.command()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.users.get().email, '')
        self.assertEqual(decrypt_email(self.users.get().operator_email.encrypted_email), 'owner@example.com')

    def test_cancellation_at_code_or_password_does_not_save_user(self):
        for interruption in (EOFError, KeyboardInterrupt):
            with self.subTest(interruption=interruption):
                with patch('builtins.input', side_effect=interruption):
                    with self.assertRaisesMessage(CommandError, 'Cadastro cancelado'):
                        self.command(email='owner@example.com')
                with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                        patch('builtins.input', return_value=CODE), \
                        patch(f'{MODULE}.getpass.getpass', side_effect=interruption):
                    with self.assertRaisesMessage(CommandError, 'Cadastro cancelado'):
                        self.command(email='owner@example.com')
        self.assertFalse(self.users.exists())

    def test_password_must_match_and_pass_django_validation(self):
        with patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', return_value=CODE), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[
                    PASSWORD, 'mismatch', '', '', '123', '123', PASSWORD, PASSWORD,
                ]):
            self.command(email='owner@example.com')
        self.assertTrue(self.users.get().check_password(PASSWORD))
        self.assertEqual(len(mail.outbox), 1)

    def test_generator_draws_sixteen_independent_alphanumeric_characters(self):
        with patch(f'{MODULE}.secrets.choice', side_effect=list('a7B2xQ9mZ1y2X3w4Z1y2X3w4a7B2xQ9m')) as choice:
            self.assertEqual(generate_verification_code(), CODE)
            self.assertEqual(generate_verification_code(), 'Z1y2X3w4a7B2xQ9m')
        self.assertEqual(choice.call_count, 32)
        for call in choice.call_args_list:
            self.assertEqual(set(call.args[0]), set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'))
