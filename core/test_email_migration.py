from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .smtp_cipher import decrypt_email, encrypt_smtp_password


class EncryptedEmailMigrationTests(TransactionTestCase):
    old_target = [('core', '0009_smtp_credential'), ('auth', '0012_alter_user_first_name_max_length')]
    new_target = [('core', '0010_encrypted_email'), ('auth', '0012_alter_user_first_name_max_length')]

    def test_existing_addresses_are_encrypted_and_authentication_is_preserved(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.old_target)
        self.addCleanup(self.restore_schema)
        apps = executor.loader.project_state(self.old_target).apps
        User = apps.get_model('auth', 'User')
        Credential = apps.get_model('core', 'SmtpCredential')
        user = User.objects.create(username='RKD', email='owner@example.com', password='unchanged-hash',
                                   is_active=True, is_staff=True, is_superuser=True)
        other = User.objects.create(username='SECOND', email='second@example.com', password='second-hash')
        blank = User.objects.create(username='EMPTY', email='', password='blank-hash')
        encrypted_password = encrypt_smtp_password('unchanged-app-password')
        Credential.objects.create(pk=1, username='legacy-sender@example.com', encrypted_password=encrypted_password)

        executor = MigrationExecutor(connection)
        executor.migrate(self.new_target)
        apps = executor.loader.project_state(self.new_target).apps
        User = apps.get_model('auth', 'User')
        Email = apps.get_model('core', 'OperatorEmail')
        Credential = apps.get_model('core', 'SmtpCredential')
        migrated = User.objects.get(pk=user.pk)
        self.assertEqual(migrated.email, '')
        self.assertEqual(migrated.password, 'unchanged-hash')
        self.assertTrue(migrated.is_active and migrated.is_staff and migrated.is_superuser)
        self.assertEqual(Email.objects.count(), 2)
        self.assertFalse(Email.objects.filter(user_id=blank.pk).exists())
        self.assertEqual(decrypt_email(Email.objects.get(user_id=user.pk).encrypted_email), 'owner@example.com')
        self.assertEqual(decrypt_email(Email.objects.get(user_id=other.pk).encrypted_email), 'second@example.com')
        credential = Credential.objects.get(pk=1)
        self.assertEqual(decrypt_email(credential.encrypted_email, smtp=True), 'legacy-sender@example.com')
        self.assertEqual(credential.encrypted_password, encrypted_password)
        self.assertNotIn('username', [field.name for field in Credential._meta.fields])

        # Reversing the migration must not lose addresses either.
        executor = MigrationExecutor(connection)
        executor.migrate(self.old_target)
        apps = executor.loader.project_state(self.old_target).apps
        self.assertEqual(apps.get_model('auth', 'User').objects.get(pk=user.pk).email, 'owner@example.com')
        self.assertEqual(apps.get_model('core', 'SmtpCredential').objects.get(pk=1).username, 'legacy-sender@example.com')

    def restore_schema(self):
        MigrationExecutor(connection).migrate(self.new_target)
