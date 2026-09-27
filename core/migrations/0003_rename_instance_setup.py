from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0002_alter_environment_created_by_and_more'),
    ]

    operations = [
        migrations.RenameModel(old_name='Instance', new_name='Setup'),
        migrations.AlterModelTable(name='setup', table='setup'),
    ]
