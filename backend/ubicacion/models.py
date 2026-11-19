from django.db import models


class Ubicacion(models.Model):
    """Registro local de estudiantes ya ubicados (tabla managed).

    Si el estudiante ya aparece aqui, no se le vuelven a pedir los datos.
    """

    codigo_estudiante = models.CharField(max_length=50, unique=True)
    persona_id = models.BigIntegerField(null=True, blank=True)
    fecha_ubicacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_ubicacion']

    def __str__(self):
        return f'{self.codigo_estudiante} - {self.fecha_ubicacion}'


class EquivalenciaEstudianteSwa(models.Model):
    """Referencia heredada; no significa que se haya completado el formulario."""

    codigo_estudiante = models.CharField(max_length=50, unique=True)
    persona_swa_id = models.BigIntegerField()
    persona_erp = models.ForeignKey(
        'api.PersonaErp', on_delete=models.DO_NOTHING,
        related_name='equivalencias_swa', db_constraint=False,
    )
    vinculo_erp = models.ForeignKey(
        'api.VinculoPersona', on_delete=models.DO_NOTHING,
        related_name='equivalencias_swa', db_constraint=False,
    )

    class Meta:
        ordering = ['codigo_estudiante']


class EquivalenciaPersonaSwa(models.Model):
    """Cada ID original sigue siendo trazable aunque se unifique la persona."""

    persona_swa_id = models.BigIntegerField(unique=True)
    persona_erp = models.ForeignKey(
        'api.PersonaErp', on_delete=models.DO_NOTHING, db_constraint=False,
        related_name='identidades_swa',
    )


class AsociacionProgramaSwa(models.Model):
    """Relacion contextual; un codigo puede tener carreras distintas por periodo."""

    codigo_estudiante = models.CharField(max_length=50)
    persona_swa_id = models.BigIntegerField()
    programa_swa_id = models.IntegerField()
    periodo_swa = models.IntegerField()
    persona_erp = models.ForeignKey(
        'api.PersonaErp', on_delete=models.DO_NOTHING, null=True,
        db_constraint=False, related_name='programas_swa',
    )
    vinculo_erp = models.ForeignKey(
        'api.VinculoPersona', on_delete=models.DO_NOTHING, null=True,
        db_constraint=False, related_name='programas_swa',
    )
    motivo_pendiente = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['codigo_estudiante', 'periodo_swa'], name='swa_codigo_periodo_unico',
        )]


class AvanceAsignaturaSwa(models.Model):
    """Intento academico fuente y referencias a su proyeccion en el ERP."""

    asociacion = models.ForeignKey(AsociacionProgramaSwa, on_delete=models.CASCADE,
                                  related_name='avances')
    periodo_swa = models.IntegerField()
    programa_swa_id = models.IntegerField()
    asignatura_swa = models.CharField(max_length=50)
    grupo_swa = models.CharField(max_length=50)
    codigo_estudiante = models.CharField(max_length=50)
    modulo = models.PositiveSmallIntegerField(null=True)
    estado_origen = models.CharField(max_length=50, blank=True)
    estado_definitiva_origen = models.CharField(max_length=50, blank=True)
    destino_notas = models.CharField(max_length=20)
    registro_origen = models.JSONField(default=dict)
    definitiva_origen = models.TextField(null=True, blank=True)
    motivo_pendiente = models.TextField(blank=True, default='')
    estado_migracion = models.CharField(max_length=20, default='pendiente')
    matricula_erp = models.ForeignKey('api.MatriculaErp', on_delete=models.DO_NOTHING,
                                    null=True, db_constraint=False, related_name='+')
    inscripcion_actual_erp = models.ForeignKey('api.InscripcionErp', on_delete=models.DO_NOTHING,
                                    null=True, db_constraint=False, related_name='+')
    inscripcion_historica_erp = models.ForeignKey('api.InscripcionHistoricaErp', on_delete=models.DO_NOTHING,
                                    null=True, db_constraint=False, related_name='+')
    ultimo_importado = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['periodo_swa', 'programa_swa_id', 'asignatura_swa',
                    'grupo_swa', 'codigo_estudiante'], name='swa_registro_asignatura_unico',
        )]


class EquivalenciaPensumSwa(models.Model):
    programa_swa_id = models.IntegerField()
    pensum_swa_id = models.IntegerField()
    pensum_erp = models.ForeignKey('api.PensumErp', on_delete=models.DO_NOTHING,
                                  db_constraint=False, related_name='+')
    origen = models.JSONField(default=dict)
    ultimo_importado = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['programa_swa_id', 'pensum_swa_id'],
                                              name='swa_programa_pensum_unico')]


class EquivalenciaMatriculaSwa(models.Model):
    periodo_swa = models.IntegerField()
    programa_swa_id = models.IntegerField()
    codigo_estudiante = models.CharField(max_length=50)
    matricula_erp = models.ForeignKey('api.MatriculaErp', on_delete=models.DO_NOTHING,
                                     db_constraint=False, related_name='+')
    ultimo_importado = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['periodo_swa', 'programa_swa_id', 'codigo_estudiante'],
                                              name='swa_matricula_unica')]


class CalificacionSwa(models.Model):
    avance = models.ForeignKey(AvanceAsignaturaSwa, on_delete=models.CASCADE,
                              related_name='calificaciones')
    tipo_nota_swa = models.IntegerField()
    estado_origen = models.IntegerField(null=True)
    nota_origen = models.TextField(null=True, blank=True)
    porcentaje_origen = models.TextField(null=True, blank=True)
    datos_origen = models.JSONField(default=dict)
    nota_erp = models.ForeignKey('api.NotaErp', on_delete=models.DO_NOTHING,
                               null=True, db_constraint=False, related_name='+')
    ultimo_importado = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['avance', 'tipo_nota_swa'],
                                              name='swa_avance_tipo_nota_unico')]


class ConfiguracionNotaSwa(models.Model):
    configuracion_erp = models.OneToOneField('api.ConfiguracionNotaErp', on_delete=models.DO_NOTHING,
                                           db_constraint=False, related_name='+')
    ultimo_importado = models.JSONField(default=dict)
