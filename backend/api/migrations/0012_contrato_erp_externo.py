from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('api', '0011_asignaturaerp_hora_mixta')]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='SucursalErp',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                                  serialize=False, verbose_name='ID')),
                        ('nombre', models.CharField(max_length=100)),
                        ('codigo', models.CharField(max_length=20)),
                        ('estado', models.BooleanField()),
                    ],
                    options={'db_table': 'academico_sucursal', 'managed': False},
                ),
                migrations.AddField(
                    model_name='personaerp', name='estado_general',
                    field=models.CharField(max_length=16, choices=[
                        ('ACTIVO', 'Activo'), ('INACTIVO', 'Inactivo'), ('RETIRADO', 'Retirado'),
                    ]),
                ),
                migrations.AddConstraint(
                    model_name='personaerp',
                    constraint=models.CheckConstraint(
                        condition=models.Q(estado_general__in=['ACTIVO', 'INACTIVO', 'RETIRADO']),
                        name='persona_estado_general_valido',
                    ),
                ),
                migrations.AlterField(
                    model_name='personaerp', name='fecha_creacion',
                    field=models.DateTimeField(default=django.utils.timezone.now, editable=False),
                ),
                migrations.AlterField(
                    model_name='programaerp', name='nombre',
                    field=models.CharField(max_length=150, unique=True),
                ),
                migrations.RemoveField(model_name='programaerp', name='sucursal_id'),
                migrations.AddField(
                    model_name='programaerp', name='sucursal',
                    field=models.ForeignKey(
                        to='api.sucursalerp', on_delete=django.db.models.deletion.DO_NOTHING),
                ),
                *[
                    migrations.AddField(model_name='programaerp', name=nombre, field=campo)
                    for nombre, campo in [
                        ('codigo_snies', models.CharField(max_length=20, blank=True, null=True)),
                        ('activo', models.BooleanField(default=True)),
                        ('numero_semanas', models.PositiveIntegerField(default=0)),
                        ('cupo_limitado', models.BooleanField(default=False)),
                        ('admision_proceso_fijo', models.BooleanField(default=False)),
                        ('requiere_aprobacion_admisiones', models.BooleanField(default=True)),
                        ('requiere_aprobacion_director', models.BooleanField(default=True)),
                        ('requiere_aprobacion_credito', models.BooleanField(default=True)),
                        ('numero_resolucion', models.CharField(max_length=50, blank=True, null=True)),
                        ('archivo_resolucion', models.CharField(max_length=100, blank=True, null=True)),
                        ('tipo_programa_id', models.BigIntegerField(blank=True, null=True)),
                        ('dirigido_a', models.TextField(default='', blank=True)),
                        ('duracion_descripcion', models.CharField(max_length=255, default='', blank=True)),
                        ('fecha_inicio', models.DateField(blank=True, null=True)),
                        ('inicio_sujeto_a_cupo', models.BooleanField(default=False)),
                        ('modalidades', models.JSONField(default=list, blank=True)),
                        ('numero_horas', models.PositiveIntegerField(blank=True, null=True)),
                        ('permite_credito', models.BooleanField(default=True)),
                    ]
                ],
                migrations.AddField(
                    model_name='inscripcionerp', name='estado',
                    field=models.CharField(max_length=10, default='ACTIVA'),
                ),
            ],
        ),
    ]
