from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0005_setup_port_setup_volume'),
    ]

    operations = [
        migrations.AddField(
            model_name='image',
            name='repository',
            field=models.CharField(blank=True, default='', max_length=256),
        ),
        migrations.AddField(
            model_name='image',
            name='branch',
            field=models.CharField(blank=True, default='', max_length=256),
        ),
    ]
