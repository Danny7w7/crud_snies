from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api', '0010_asignaturaerp_configuracionnotaerp_cortenotaerp_and_more')]

    operations = [
        migrations.AddField(
            model_name='asignaturaerp', name='hora_mixta',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
