"""Move existing addresses to encrypted storage without changing authentication."""

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def cipher(purpose):
    material = (f'rkd-dockestra-core:{purpose}:' + settings.SECRET_KEY).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def encrypt_existing(apps, schema_editor):
    alias = schema_editor.connection.alias
    Credential = apps.get_model('core', 'SmtpCredential')
    Email = apps.get_model('core', 'OperatorEmail')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    smtp_cipher = cipher('smtp-email')
    operator_cipher = cipher('operator-email')
    for credential in Credential.objects.using(alias).all().iterator():
        credential.encrypted_email = smtp_cipher.encrypt(credential.username.encode()).decode('ascii')
        credential.save(using=alias, update_fields=['encrypted_email'])
    for user in User.objects.using(alias).exclude(email='').iterator():
        Email.objects.using(alias).create(
            user_id=user.pk, encrypted_email=operator_cipher.encrypt(user.email.encode()).decode('ascii'),
        )
        User.objects.using(alias).filter(pk=user.pk).update(email='')


def restore_existing(apps, schema_editor):
    alias = schema_editor.connection.alias
    Credential = apps.get_model('core', 'SmtpCredential')
    Email = apps.get_model('core', 'OperatorEmail')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    smtp_cipher = cipher('smtp-email')
    operator_cipher = cipher('operator-email')
    for credential in Credential.objects.using(alias).all().iterator():
        credential.username = smtp_cipher.decrypt(credential.encrypted_email.encode('ascii')).decode()
        credential.save(using=alias, update_fields=['username'])
    for record in Email.objects.using(alias).all().iterator():
        User.objects.using(alias).filter(pk=record.user_id).update(
            email=operator_cipher.decrypt(record.encrypted_email.encode('ascii')).decode(),
        )


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0009_smtp_credential'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='smtpcredential', name='encrypted_email',
            field=models.TextField(default='', editable=False), preserve_default=False,
        ),
        migrations.CreateModel(
            name='OperatorEmail',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('encrypted_email', models.TextField(editable=False)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='operator_email', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'operator_email'},
        ),
        # Give SQLite a temporary value when restoring this column on rollback;
        # restore_existing immediately replaces it with the decrypted address.
        migrations.AlterField(model_name='smtpcredential', name='username',
                              field=models.EmailField(default='', max_length=254)),
        migrations.RunPython(encrypt_existing, restore_existing),
        migrations.RemoveField(model_name='smtpcredential', name='username'),
    ]
