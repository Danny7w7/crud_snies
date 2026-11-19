"""Referencias al esquema academico del ERP; sus tablas no son del migrador."""

from django.db import models


class DestinoAcademico(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        alias = kwargs.get('using') or self._state.db or 'default'
        if alias != 'default':
            raise PermissionError('El destino academico solo se escribe en default.')
        kwargs['using'] = alias
        return super().save(*args, **kwargs)


class PeriodoErp(DestinoAcademico):
    nombre = models.CharField(max_length=20, unique=True)
    activo = models.BooleanField(default=True)
    fecha_inicio_general = models.DateField(null=True, blank=True)
    fecha_fin_general = models.DateField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'academico_periodoacademico'


class AsignaturaErp(DestinoAcademico):
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=200)
    metodologia = models.CharField(max_length=200)
    creditos = models.IntegerField()
    hora_teorica = models.SmallIntegerField()
    hora_practica = models.SmallIntegerField()
    hora_mixta = models.PositiveSmallIntegerField(default=0)
    tipo_asignatura = models.CharField(max_length=50)

    class Meta:
        managed = False
        db_table = 'academico_asignatura'


class PensumErp(DestinoAcademico):
    programa = models.ForeignKey('api.ProgramaErp', on_delete=models.DO_NOTHING)
    codigo = models.CharField(max_length=20, unique=True)
    estado = models.BooleanField(default=True)
    intensidad_horaria = models.PositiveIntegerField(default=0)
    periodo = models.ForeignKey(PeriodoErp, on_delete=models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'academico_pensum'


class PensumAsignaturaErp(DestinoAcademico):
    pensum = models.ForeignKey(PensumErp, on_delete=models.DO_NOTHING)
    asignatura = models.ForeignKey(AsignaturaErp, on_delete=models.DO_NOTHING)
    semestre = models.PositiveSmallIntegerField()

    class Meta:
        managed = False
        db_table = 'academico_pensumasignatura'


class RestriccionPensumErp(DestinoAcademico):
    pensum_asignatura = models.ForeignKey(PensumAsignaturaErp, on_delete=models.DO_NOTHING)
    tipo = models.CharField(max_length=15)
    asignatura = models.ForeignKey(AsignaturaErp, on_delete=models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'academico_pensumasignaturarestriccion'
        constraints = [models.UniqueConstraint(fields=['pensum_asignatura', 'tipo', 'asignatura'],
                                               name='unique_restriccion_por_materia')]


class MatriculaErp(DestinoAcademico):
    persona = models.ForeignKey('api.PersonaErp', on_delete=models.DO_NOTHING)
    programa = models.ForeignKey('api.ProgramaErp', on_delete=models.DO_NOTHING)
    pensum = models.ForeignKey(PensumErp, on_delete=models.DO_NOTHING)
    periodo = models.ForeignKey(PeriodoErp, on_delete=models.DO_NOTHING)
    estado = models.BooleanField(default=True)
    semestre_actual = models.PositiveSmallIntegerField(default=1)
    fecha_inicio = models.DateTimeField(auto_now_add=True)
    fecha_fin = models.DateTimeField(null=True, blank=True)
    codigo = models.CharField(max_length=20)

    class Meta:
        managed = False
        db_table = 'academico_matricula'


class InscripcionBase(DestinoAcademico):
    persona = models.ForeignKey('api.PersonaErp', on_delete=models.DO_NOTHING)
    programa = models.ForeignKey('api.ProgramaErp', on_delete=models.DO_NOTHING)
    asignatura = models.ForeignKey(AsignaturaErp, on_delete=models.DO_NOTHING)
    matricula = models.ForeignKey(MatriculaErp, on_delete=models.DO_NOTHING)
    periodo = models.ForeignKey(PeriodoErp, on_delete=models.DO_NOTHING)
    definitiva = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        abstract = True


class InscripcionErp(InscripcionBase):
    carga_id = models.BigIntegerField(null=True, blank=True)
    codigo_grupo = models.CharField(max_length=20, blank=True, default='')
    estado = models.CharField(max_length=10, default='ACTIVA')

    class Meta:
        managed = False
        db_table = 'academico_asignaturamatricula'


class InscripcionHistoricaErp(InscripcionBase):
    class Meta:
        managed = False
        db_table = 'academico_asignaturamatriculahistorico'


class ConfiguracionNotaErp(DestinoAcademico):
    programa = models.ForeignKey('api.ProgramaErp', on_delete=models.DO_NOTHING)
    pensum = models.ForeignKey(PensumErp, on_delete=models.DO_NOTHING, null=True, blank=True)
    asignatura = models.ForeignKey(AsignaturaErp, on_delete=models.DO_NOTHING)
    periodo = models.ForeignKey(PeriodoErp, on_delete=models.DO_NOTHING)
    requiere_ausencias = models.CharField(max_length=15, default='NO')

    class Meta:
        managed = False
        db_table = 'academico_configuracionnota'
        constraints = [models.UniqueConstraint(fields=['programa', 'pensum', 'asignatura', 'periodo'],
                  name='unique_configuracion_nota_programa_pensum_asignatura_periodo')]


class CorteNotaErp(DestinoAcademico):
    configuracion = models.ForeignKey(ConfiguracionNotaErp, on_delete=models.DO_NOTHING)
    codigo = models.CharField(max_length=20)
    descripcion = models.CharField(max_length=200, blank=True, default='')
    porcentaje = models.PositiveSmallIntegerField()
    orden = models.PositiveSmallIntegerField(default=1)

    class Meta:
        managed = False
        db_table = 'academico_cortenota'
        constraints = [models.UniqueConstraint(fields=['configuracion', 'codigo'],
                                               name='unique_corte_codigo_por_configuracion')]


class NotaErp(InscripcionBase):
    asignatura_matricula = models.ForeignKey(InscripcionErp, on_delete=models.DO_NOTHING)
    corte = models.ForeignKey(CorteNotaErp, on_delete=models.DO_NOTHING, null=True, blank=True)
    nota = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # Nota no posee una columna definitiva; se hereda solo el resto de referencias.
    definitiva = None

    class Meta:
        managed = False
        db_table = 'academico_nota'
        constraints = [models.UniqueConstraint(fields=['asignatura_matricula', 'corte'],
                 condition=models.Q(corte__isnull=False), name='unique_asignatura_matricula_corte')]


MODELOS_ACADEMICOS = (PeriodoErp, AsignaturaErp, PensumErp, PensumAsignaturaErp,
                     RestriccionPensumErp, MatriculaErp, InscripcionErp,
                     InscripcionHistoricaErp, ConfiguracionNotaErp, CorteNotaErp, NotaErp)
