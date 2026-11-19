from django.db import models
from django.utils import timezone

# Django registra tambien estas referencias externas al cargar la aplicacion.
from .modelos_academicos import (PeriodoErp, AsignaturaErp, PensumErp, PensumAsignaturaErp,
    RestriccionPensumErp, MatriculaErp, InscripcionErp, InscripcionHistoricaErp,
    ConfiguracionNotaErp, CorteNotaErp, NotaErp)


class PersonaErp(models.Model):
    """Tabla 'erp_persona' de la ERP local: aqui se guarda la informacion."""

    class EstadoGeneral(models.TextChoices):
        ACTIVO = 'ACTIVO', 'Activo'
        INACTIVO = 'INACTIVO', 'Inactivo'
        RETIRADO = 'RETIRADO', 'Retirado'

    id = models.BigIntegerField(primary_key=True, editable=False)
    nro_documento = models.BigIntegerField()
    primer_nombre = models.CharField(max_length=100)
    primer_apellido = models.CharField(max_length=100)
    segundo_nombre = models.CharField(max_length=100, blank=True)
    segundo_apellido = models.CharField(max_length=100, blank=True)
    email = models.EmailField(max_length=254)
    telefono = models.BigIntegerField(null=True, blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    fecha_creacion = models.DateTimeField(default=timezone.now, editable=False)
    tipo_documento_id = models.BigIntegerField()
    estado_general = models.CharField(max_length=16, choices=EstadoGeneral.choices)

    class Meta:
        managed = False
        db_table = 'erp_persona'
        verbose_name = 'Persona (ERP)'
        verbose_name_plural = 'Personas (ERP)'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(estado_general__in=['ACTIVO', 'INACTIVO', 'RETIRADO']),
                name='persona_estado_general_valido',
            ),
            models.UniqueConstraint(
                fields=['tipo_documento_id', 'nro_documento'],
                name='unique_tipo_nro_documento',
            ),
        ]

    def save(self, *args, **kwargs):
        using = kwargs.get('using') or self._state.db or 'default'
        if using != 'default':
            raise PermissionError('Las personas ERP solo se escriben en default.')
        kwargs['using'] = using
        if self.pk is None:
            raise ValueError('La migracion requiere un Persona.id de SWA explicito.')
        if self.estado_general not in self.EstadoGeneral.values:
            raise ValueError('Estado general de persona pendiente de evidencia explícita.')
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.primer_nombre} {self.primer_apellido}'.strip()


class SecuenciaCodigoPersona(models.Model):
    """Secuencia compartida con el ERP; este proyecto no crea su tabla."""

    prefijo = models.CharField(max_length=10, primary_key=True)
    consecutivo = models.IntegerField(default=0)

    class Meta:
        managed = False
        db_table = 'erp_secuenciacodigopersona'


class DatosSecundarios(models.Model):
    """Tabla 'erp_datossecundarios' de la ERP local (1:1 con persona)."""

    id = models.BigAutoField(primary_key=True)
    fecha_expedicion_documento = models.DateField(null=True, blank=True)
    lugar_expedicion_documento = models.ForeignKey(
        'Municipio',
        on_delete=models.DO_NOTHING,
        related_name='datos_secundarios_expedicion',
        null=True,
        blank=True,
    )
    barrio = models.CharField(max_length=100)
    direccion = models.CharField(max_length=200, blank=True)
    direccion_institucional = models.CharField(max_length=200, default='')
    telefono_fijo = models.CharField(max_length=15, null=True, blank=True)
    sexo_biologico = models.SmallIntegerField(null=True, blank=True)
    estado_civil = models.SmallIntegerField(null=True, blank=True)
    fecha_registro = models.DateTimeField(default=timezone.now)
    estrato = models.SmallIntegerField(null=True, blank=True)
    libreta_militar = models.CharField(max_length=50, null=True, blank=True)
    distrito_militar = models.CharField(max_length=50, null=True, blank=True)
    pais_nacimiento = models.CharField(max_length=3)
    lugar_nacimiento = models.ForeignKey(
        'Municipio',
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
    )
    afp_id = models.BigIntegerField(null=True, blank=True)
    arl_id = models.BigIntegerField(null=True, blank=True)
    caja_compensacion_id = models.BigIntegerField(null=True, blank=True)
    eps_id = models.BigIntegerField(null=True, blank=True)
    municipio_id = models.BigIntegerField(null=True, blank=True)
    persona = models.OneToOneField(
        PersonaErp,
        on_delete=models.DO_NOTHING,
        db_column='persona_id',
        related_name='datos_secundarios',
    )
    sede_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'erp_datossecundarios'
        verbose_name = 'Datos secundarios (ERP)'
        verbose_name_plural = 'Datos secundarios (ERP)'


class TipoVinculo(models.Model):
    """Tabla 'erp_tipovinculo' de la ERP local."""

    id = models.BigAutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    estado = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'erp_tipovinculo'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class SucursalErp(models.Model):
    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=20)
    estado = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'academico_sucursal'


class ProgramaErp(models.Model):
    """Referencia al programa del destino; nunca al catalogo SWA."""

    id = models.BigAutoField(primary_key=True)
    nombre = models.CharField(max_length=150, unique=True)
    codigo_snies = models.CharField(max_length=20, null=True, blank=True)
    activo = models.BooleanField(default=True)
    numero_semanas = models.PositiveIntegerField(default=0)
    cupo_limitado = models.BooleanField(default=False)
    admision_proceso_fijo = models.BooleanField(default=False)
    requiere_aprobacion_admisiones = models.BooleanField(default=True)
    requiere_aprobacion_director = models.BooleanField(default=True)
    requiere_aprobacion_credito = models.BooleanField(default=True)
    numero_resolucion = models.CharField(max_length=50, null=True, blank=True)
    archivo_resolucion = models.CharField(max_length=100, null=True, blank=True)
    sucursal = models.ForeignKey(SucursalErp, on_delete=models.DO_NOTHING)
    tipo_programa_id = models.BigIntegerField(null=True, blank=True)
    dirigido_a = models.TextField(blank=True, default='')
    duracion_descripcion = models.CharField(max_length=255, blank=True, default='')
    fecha_inicio = models.DateField(null=True, blank=True)
    inicio_sujeto_a_cupo = models.BooleanField(default=False)
    modalidades = models.JSONField(default=list, blank=True)
    numero_horas = models.PositiveIntegerField(null=True, blank=True)
    permite_credito = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'academico_programa'


class VinculoPersona(models.Model):
    """Tabla 'erp_vinculopersona' de la ERP local."""

    id = models.BigAutoField(primary_key=True)
    activo = models.BooleanField()
    periodo = models.CharField(max_length=20)
    programa_secundario = models.ForeignKey(
        ProgramaErp, on_delete=models.DO_NOTHING, null=True, blank=True,
        related_name='+',
    )
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    persona = models.ForeignKey(
        PersonaErp,
        on_delete=models.DO_NOTHING,
        db_column='persona_id',
        related_name='vinculos',
    )
    programa = models.ForeignKey(
        ProgramaErp, on_delete=models.DO_NOTHING, null=True, blank=True,
        related_name='+',
    )
    tipo_vinculo = models.ForeignKey(
        TipoVinculo,
        on_delete=models.DO_NOTHING,
        db_column='tipo_vinculo_id',
        related_name='vinculos',
    )

    class Meta:
        managed = False
        db_table = 'erp_vinculopersona'
        verbose_name = 'Vínculo de persona (ERP)'
        verbose_name_plural = 'Vínculos de persona (ERP)'

    def __str__(self):
        return f'{self.persona_id} - {self.periodo}'


class Departamento(models.Model):
    """Tabla 'erp_departamento' de la ERP local."""

    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=10)

    class Meta:
        managed = False
        db_table = 'erp_departamento'
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.codigo})'


class Municipio(models.Model):
    """Tabla 'erp_municipio' de la ERP local."""

    departamento = models.ForeignKey(
        Departamento,
        on_delete=models.DO_NOTHING,
        db_column='departamento_id',
        related_name='municipios',
    )
    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=10)

    class Meta:
        managed = False
        db_table = 'erp_municipio'
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.codigo})'


class Persona(models.Model):
    """Tabla 'persona' de la base de datos de produccion (solo lectura)."""

    id = models.IntegerField(primary_key=True)
    identificacion = models.CharField(max_length=255, blank=True, null=True)
    tipo_identificacion = models.IntegerField(blank=True, null=True)
    nombre = models.CharField(max_length=255, blank=True, null=True)
    apellido = models.CharField(max_length=255, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    telefono = models.CharField(max_length=255, blank=True, null=True)
    email = models.CharField(max_length=255, blank=True, null=True)
    sexo = models.CharField(max_length=1, blank=True, null=True)
    estado_civil = models.IntegerField(blank=True, null=True)
    saldo_a_favor = models.FloatField(blank=True, null=True)
    insert_log = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'persona'
        verbose_name = 'Persona'
        verbose_name_plural = 'Personas'

    def __str__(self):
        return f'{self.nombre} {self.apellido}'.strip()


class EstudianteSwa(models.Model):
    """Tabla 'estudiante' de produccion para vincular el codigo del estudiante."""

    codigo = models.CharField(max_length=50, primary_key=True)
    programa = models.IntegerField(blank=True, null=True)
    pensum = models.IntegerField(blank=True, null=True)
    persona = models.ForeignKey(
        'Persona',
        on_delete=models.DO_NOTHING,
        db_column='persona',
        related_name='registros_estudiantiles',
        blank=True,
        null=True,
    )
    estado = models.CharField(max_length=50, blank=True, null=True)
    periodo_ingreso = models.IntegerField(blank=True, null=True)
    nivel = models.IntegerField(blank=True, null=True)
    fecha_nacimiento = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'estudiante'
        verbose_name = 'Registro académico'
        verbose_name_plural = 'Registros académicos'

    def __str__(self):
        return f'{self.codigo}'


class Programa(models.Model):
    """Tabla 'programa' de la base de datos de produccion (solo lectura).

    El esquema heredado usa 'codigo' como llave primaria (no 'id').
    """

    codigo = models.IntegerField(primary_key=True)
    nombre = models.CharField(max_length=255, blank=True, null=True)
    activo = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'programa'
        verbose_name = 'Programa'
        verbose_name_plural = 'Programas'
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.codigo})'


class Historico(models.Model):
    """Tabla 'historico' de produccion (historial de matricula por periodo).

    Estado: 'M' (matriculado), 'P' (pagado sin matricula real), 'A'.
    Para la deteccion de reintegro solo se consideran matricula real ('M').
    """

    periodo = models.IntegerField(blank=True, null=True)
    programa = models.IntegerField(blank=True, null=True)
    estudiante = models.CharField(max_length=50, blank=True, null=True)
    estado = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'historico'
        verbose_name = 'Historial académico'
        verbose_name_plural = 'Historiales académicos'

    def __str__(self):
        return f'{self.estudiante} - {self.periodo}'
