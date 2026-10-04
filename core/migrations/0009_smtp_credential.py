from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0008_setup_instances')]

    operations = [
        migrations.CreateModel(
            name='SmtpCredential',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('username', models.EmailField(max_length=254)),
                ('encrypted_password', models.TextField(editable=False)),
            ],
            options={
                'db_table': 'smtp_credential',
                'constraints': [models.CheckConstraint(condition=models.Q(id=1), name='smtp_credential_singleton')],
            },
        ),
    ]
