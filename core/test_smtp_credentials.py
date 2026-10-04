"""Exercise bootstrap and recovery without sending any real email."""

import getpass
import warnings
from io import StringIO
from smtplib import SMTPAuthenticationError
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, IntegrityError, transaction
from django.test import TestCase, override_settings

from .models import OperatorEmail, SmtpCredential
from .smtp_cipher import decrypt_email, encrypt_email, SmtpDecryptionError, decrypt_smtp_password, encrypt_smtp_password
from .test_superuser import EncryptedEmailTestMixin, CODE, MODULE, NEW_PASSWORD, PASSWORD, Terminal


APP_PASSWORD = 'abcdefghijklmnop'


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   EMAIL_HOST_USER='sender@gmail.com', DEFAULT_FROM_EMAIL='sender@gmail.com',
                   EMAIL_HOST_PASSWORD='legacy-env-password-must-never-be-used')
class SmtpCredentialTests(EncryptedEmailTestMixin, TestCase):
    def setUp(self):
        self.output = StringIO()
        self.errors = StringIO()
        self.users = get_user_model().objects

    def command(self, **options):
        call_command('createsuperuser', stdin=Terminal(), stdout=self.output, stderr=self.errors, **options)

    def store_credential(self, password=APP_PASSWORD):
        return SmtpCredential.objects.create(encrypted_email=encrypt_email('owner@example.com', smtp=True),
                                             encrypted_password=encrypt_smtp_password(password))

    def test_email_then_hidden_app_password_and_database_contains_only_ciphertext(self):
        prompts = []

        def hidden(prompt):
            prompts.append(prompt)
            return APP_PASSWORD if 'EMAIL_HOST_PASSWORD' in prompt else PASSWORD

        def answer(prompt):
            if prompt == 'E-mail: ':
                self.assertEqual(prompts, [])
            else:
                self.assertTrue(prompts[0].startswith('EMAIL_HOST_PASSWORD'))
                self.assertTrue(prompts[1].startswith('Confirme EMAIL_HOST_PASSWORD'))
            self.assertFalse(SmtpCredential.objects.exists())
            self.assertFalse(self.users.exists())
            return 'owner@example.com' if prompt == 'E-mail: ' else CODE

        with patch(f'{MODULE}.getpass.getpass', side_effect=hidden), \
                patch('builtins.input', side_effect=answer), \
                patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch(f'{MODULE}.send_mail', return_value=1) as send:
            self.command()
        self.assertEqual(send.call_args.kwargs['auth_password'], APP_PASSWORD)
        self.assertEqual(send.call_args.kwargs['auth_user'], 'owner@example.com')
        with connection.cursor() as cursor:
            cursor.execute('SELECT encrypted_password, encrypted_email FROM smtp_credential')
            encrypted, smtp_email = cursor.fetchone()
            cursor.execute('SELECT encrypted_email FROM operator_email')
            operator_email = cursor.fetchone()[0]
            cursor.execute('SELECT email FROM auth_user')
            self.assertEqual(cursor.fetchone()[0], '')
        for encrypted_address in (smtp_email, operator_email):
            self.assertNotIn('owner@example.com', encrypted_address)
        self.assertEqual(decrypt_email(smtp_email, smtp=True), 'owner@example.com')
        self.assertEqual(decrypt_email(operator_email), 'owner@example.com')
        self.assertEqual(send.call_args.args[2], 'owner@example.com')
        self.assertEqual(send.call_args.args[3], ['owner@example.com'])
        self.assertNotIn(APP_PASSWORD, encrypted)
        self.assertEqual(decrypt_smtp_password(encrypted), APP_PASSWORD)
        self.assertTrue(self.users.get(username='RKD').check_password(PASSWORD))
        for secret in (APP_PASSWORD, 'abcd efgh ijkl mnop', encrypted, PASSWORD, CODE):
            self.assertNotIn(secret, self.output.getvalue() + self.errors.getvalue())

    def test_recovery_reuses_saved_credential_without_prompting_for_it(self):
        self.create_user('RKD', 'owner@example.com', PASSWORD)
        credential = self.store_credential()
        with patch(f'{MODULE}.getpass.getpass', side_effect=[NEW_PASSWORD, NEW_PASSWORD]) as hidden, \
                patch('builtins.input', return_value=CODE), \
                patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch(f'{MODULE}.send_mail', return_value=1) as send:
            self.command()
        self.assertEqual(hidden.call_count, 2)
        self.assertEqual(send.call_args.kwargs['auth_password'], APP_PASSWORD)
        self.assertEqual(SmtpCredential.objects.get().encrypted_password, credential.encrypted_password)

    def test_mismatches_case_changes_and_spaces_require_both_entries_again_before_sending(self):
        entries = iter([
            APP_PASSWORD, APP_PASSWORD.upper(),
            APP_PASSWORD, 'abcdefghijklmnopq',
            'abcd efgh ijkl mnop', 'abcd efgh ijkl mnop',
            APP_PASSWORD, APP_PASSWORD + ' ',
            '\t' + APP_PASSWORD, APP_PASSWORD,
            APP_PASSWORD, APP_PASSWORD,
            PASSWORD, PASSWORD,
        ])

        with patch(f'{MODULE}.send_mail', return_value=1) as send:
            def hidden(prompt):
                if 'EMAIL_HOST_PASSWORD' in prompt:
                    send.assert_not_called()
                    self.assertFalse(SmtpCredential.objects.exists())
                return next(entries)

            with patch(f'{MODULE}.getpass.getpass', side_effect=hidden) as password_input, \
                    patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                    patch('builtins.input', return_value=CODE):
                self.command(email='owner@example.com')
        self.assertEqual(password_input.call_count, 14)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.kwargs['auth_password'], APP_PASSWORD)
        self.assertIn('não coincidem', self.errors.getvalue())
        self.assertIn('sem espaços', self.errors.getvalue())
        self.assertEqual(decrypt_smtp_password(SmtpCredential.objects.get().encrypted_password), APP_PASSWORD)

    def test_cancelling_second_app_password_entry_never_sends_or_saves(self):
        for recovery in (False, True):
            user = self.create_user('RKD', 'owner@example.com', PASSWORD) if recovery else None
            for interruption in (EOFError, KeyboardInterrupt):
                with self.subTest(recovery=recovery, interruption=interruption), \
                        patch(f'{MODULE}.getpass.getpass', side_effect=[APP_PASSWORD, interruption]), \
                        patch(f'{MODULE}.send_mail') as send:
                    with self.assertRaises(CommandError):
                        self.command(email='owner@example.com')
                send.assert_not_called()
                self.assertFalse(SmtpCredential.objects.exists())
                if user:
                    user.refresh_from_db()
                    self.assertTrue(user.check_password(PASSWORD))
                else:
                    self.assertFalse(self.users.exists())

    def test_failed_verification_or_cancellation_does_not_persist_new_credential(self):
        for answers in (['wrong'] * 3, [EOFError], [KeyboardInterrupt]):
            with self.subTest(answers=answers), \
                    patch(f'{MODULE}.getpass.getpass', return_value=APP_PASSWORD), \
                    patch('builtins.input', side_effect=answers), \
                    patch(f'{MODULE}.send_mail', return_value=1):
                with self.assertRaises(CommandError):
                    self.command(email='owner@example.com')
            self.assertFalse(SmtpCredential.objects.exists())
            self.assertFalse(self.users.exists())

    def test_invalid_initial_app_password_is_not_saved_and_details_are_hidden(self):
        with patch(f'{MODULE}.getpass.getpass', return_value=APP_PASSWORD), \
                patch(f'{MODULE}.send_mail', side_effect=SMTPAuthenticationError(535, APP_PASSWORD.encode())):
            with self.assertRaisesMessage(CommandError, 'autenticação SMTP recusada') as caught:
                self.command(email='owner@example.com')
        self.assertNotIn(APP_PASSWORD, str(caught.exception))
        self.assertFalse(SmtpCredential.objects.exists())
        self.assertFalse(self.users.exists())

    def test_revoked_stored_password_can_be_replaced_during_verified_recovery(self):
        self.create_user('RKD', 'owner@example.com', PASSWORD)
        self.store_credential('revoked-password')
        with patch(f'{MODULE}.getpass.getpass', side_effect=[APP_PASSWORD, APP_PASSWORD, NEW_PASSWORD, NEW_PASSWORD]), \
                patch(f'{MODULE}.send_mail', side_effect=[SMTPAuthenticationError(535, b'private'), 1]) as send, \
                patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', return_value=CODE):
            self.command()
        self.assertEqual(send.call_args_list[0].kwargs['auth_password'], 'revoked-password')
        self.assertEqual(send.call_args_list[1].kwargs['auth_password'], APP_PASSWORD)
        self.assertEqual(decrypt_smtp_password(SmtpCredential.objects.get().encrypted_password), APP_PASSWORD)
        self.assertTrue(self.users.get().check_password(NEW_PASSWORD))

    def test_failed_replacement_preserves_saved_credential_and_account(self):
        user = self.create_user('RKD', 'owner@example.com', PASSWORD)
        credential = self.store_credential('revoked-password')
        with patch(f'{MODULE}.getpass.getpass', return_value=APP_PASSWORD), \
                patch(f'{MODULE}.send_mail', side_effect=[SMTPAuthenticationError(535, b'private'), 1]), \
                patch('builtins.input', side_effect=['wrong'] * 3):
            with self.assertRaises(CommandError):
                self.command()
        self.assertEqual(SmtpCredential.objects.get().encrypted_password, credential.encrypted_password)
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_changed_sender_does_not_receive_old_sender_password(self):
        credential = self.store_credential('old-sender-password')
        credential.encrypted_email = encrypt_email('different@gmail.com', smtp=True)
        credential.save()
        with override_settings(EMAIL_HOST_USER='different@gmail.com'), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[APP_PASSWORD, APP_PASSWORD, PASSWORD, PASSWORD]), \
                patch(f'{MODULE}.send_mail', return_value=1) as send, \
                patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', return_value=CODE):
            self.command(email='owner@example.com')
        self.assertEqual(send.call_args.kwargs['auth_password'], APP_PASSWORD)
        self.assertEqual(decrypt_email(SmtpCredential.objects.get().encrypted_email, smtp=True), 'owner@example.com')

    def test_changed_key_requires_a_new_app_password(self):
        self.store_credential()
        with override_settings(SECRET_KEY='different-secret-key'), \
                patch(f'{MODULE}.getpass.getpass', side_effect=[APP_PASSWORD, APP_PASSWORD, PASSWORD, PASSWORD]) as hidden, \
                patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', return_value=CODE):
            self.command(email='owner@example.com')
            self.assertEqual(decrypt_smtp_password(SmtpCredential.objects.get().encrypted_password), APP_PASSWORD)
        self.assertEqual(hidden.call_count, 4)
        self.assertIn('não pôde ser decifrada', self.errors.getvalue())

    def test_recovery_cannot_replace_an_unreadable_saved_email(self):
        user = self.create_user('RKD', 'owner@example.com', PASSWORD)
        credential = self.store_credential()
        for encrypted_address in ('corrupted', encrypt_email('owner@example.com')):
            OperatorEmail.objects.filter(user=user).update(encrypted_email=encrypted_address)
            with override_settings(SECRET_KEY='different-key'), patch(f'{MODULE}.send_mail') as send:
                with self.assertRaisesMessage(CommandError, 'DJANGO_SECRET_KEY original'):
                    self.command(email='attacker@example.com')
            send.assert_not_called()
        self.assertEqual(SmtpCredential.objects.get().encrypted_email, credential.encrypted_email)
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_concurrent_change_to_encrypted_email_blocks_recovery(self):
        user = self.create_user('RKD', 'owner@example.com', PASSWORD)
        self.store_credential()

        def change_email(*args, **kwargs):
            OperatorEmail.objects.filter(user=user).update(encrypted_email=encrypt_email('changed@example.com'))
            return NEW_PASSWORD

        with patch(f'{MODULE}.Command.read_password', side_effect=change_email), \
                patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', return_value=CODE):
            with self.assertRaisesMessage(CommandError, 'alterada durante a recuperação'):
                self.command()
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_email_ciphers_are_randomized_and_cannot_use_password_ciphertext(self):
        for smtp in (True, False):
            encrypted = encrypt_email('owner@example.com', smtp=smtp)
            self.assertNotEqual(encrypted, encrypt_email('owner@example.com', smtp=smtp))
            with self.assertRaises(SmtpDecryptionError):
                decrypt_email(encrypted, smtp=not smtp)
            with self.assertRaises(SmtpDecryptionError):
                decrypt_email(encrypt_smtp_password('owner@example.com'), smtp=smtp)
            with override_settings(SECRET_KEY='different-key'), self.assertRaises(SmtpDecryptionError):
                decrypt_email(encrypted, smtp=smtp)

    def test_empty_password_reprompts_and_unsafe_terminal_is_refused(self):
        with patch(f'{MODULE}.getpass.getpass', side_effect=['', '', APP_PASSWORD, APP_PASSWORD, PASSWORD, PASSWORD]), \
                patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', return_value=CODE):
            self.command(email='owner@example.com')
        self.assertIn('não podem ser vazias', self.errors.getvalue())
        SmtpCredential.objects.all().delete()

        def unsafe_terminal(*args):
            warnings.warn('Cannot control echo', getpass.GetPassWarning)

        with patch(f'{MODULE}.getpass.getpass', side_effect=unsafe_terminal), patch(f'{MODULE}.send_mail') as send:
            with self.assertRaisesMessage(CommandError, 'não permite ocultar'):
                self.command()
        send.assert_not_called()
        self.assertFalse(SmtpCredential.objects.exists())

    def test_cipher_rejects_tampering_wrong_key_and_github_tokens(self):
        from .token_cipher import encrypt_token
        encrypted = encrypt_smtp_password(APP_PASSWORD)
        self.assertNotEqual(encrypted, encrypt_smtp_password(APP_PASSWORD))
        for invalid in ('plaintext', encrypted[:-8] + 'AAAAAAAA', encrypt_token(APP_PASSWORD)):
            with self.assertRaises(SmtpDecryptionError):
                decrypt_smtp_password(invalid)
        with override_settings(SECRET_KEY='different-secret-key'):
            with self.assertRaises(SmtpDecryptionError):
                decrypt_smtp_password(encrypted)

    def test_credential_and_user_save_are_atomic(self):
        with patch(f'{MODULE}.getpass.getpass', side_effect=[APP_PASSWORD, APP_PASSWORD, PASSWORD, PASSWORD]), \
                patch(f'{MODULE}.generate_verification_code', return_value=CODE), \
                patch('builtins.input', return_value=CODE), \
                patch(f'{MODULE}.Command.save_smtp_credential', side_effect=IntegrityError):
            with self.assertRaises(CommandError):
                self.command(email='owner@example.com')
        self.assertFalse(self.users.exists())
        self.assertFalse(SmtpCredential.objects.exists())
        self.assertFalse(OperatorEmail.objects.exists())

    def test_database_enforces_single_installation_credential(self):
        self.store_credential()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SmtpCredential.objects.create(pk=2, encrypted_email=encrypt_email('second@gmail.com', smtp=True), encrypted_password='unused')
