from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0006_image_repository_image_branch'),
    ]

    operations = [
        migrations.AddField(
            model_name='image',
            name='isPrivate',
            field=models.PositiveSmallIntegerField(choices=[(0, 'Não'), (1, 'Sim')], default=0),
        ),
        migrations.AddField(
            model_name='image',
            name='token',
            field=models.TextField(blank=True, default=''),
        ),
    ]
