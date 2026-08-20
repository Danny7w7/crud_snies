from django.db import models


class PersonaErp(models.Model):
    """Tabla 'erp_persona' de la ERP local: aqui se guarda la informacion."""

    id = models.BigAutoField(primary_key=True)
    nro_documento = models.BigIntegerField()
    primer_nombre = models.CharField(max_length=100)
    primer_apellido = models.CharField(max_length=100)
    segundo_nombre = models.CharField(max_length=100)
    segundo_apellido = models.CharField(max_length=100)
    email = models.CharField(max_length=254)
    telefono = models.BigIntegerField(null=True, blank=True)
    direccion = models.CharField(max_length=200)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    tipo_documento_id = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'erp_persona'
        verbose_name = 'Persona (ERP)'
        verbose_name_plural = 'Personas (ERP)'

    def __str__(self):
        return f'{self.primer_nombre} {self.primer_apellido}'.strip()


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
    direccion = models.CharField(max_length=200)
    telefono_fijo = models.CharField(max_length=15, null=True, blank=True)
    sexo_biologico = models.SmallIntegerField(null=True, blank=True)
    estado_civil = models.SmallIntegerField(null=True, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)
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
    fecha_nacimiento = models.DateField(null=True, blank=True)
    afp_id = models.IntegerField(null=True, blank=True)
    arl_id = models.IntegerField(null=True, blank=True)
    caja_compensacion_id = models.IntegerField(null=True, blank=True)
    eps_id = models.IntegerField(null=True, blank=True)
    municipio_id = models.IntegerField(null=True, blank=True)
    persona = models.ForeignKey(
        PersonaErp,
        on_delete=models.DO_NOTHING,
        db_column='persona_id',
        related_name='datos_secundarios',
    )
    sede_id = models.IntegerField(null=True, blank=True)

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


class VinculoPersona(models.Model):
    """Tabla 'erp_vinculopersona' de la ERP local."""

    id = models.BigAutoField(primary_key=True)
    activo = models.BooleanField()
    periodo = models.CharField(max_length=20)
    codigo_aspirante = models.CharField(max_length=50, null=True, blank=True)
    programa_secundario = models.SmallIntegerField(null=True, blank=True)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    persona = models.ForeignKey(
        PersonaErp,
        on_delete=models.DO_NOTHING,
        db_column='persona_id',
        related_name='vinculos',
    )
    programa_id = models.IntegerField(null=True, blank=True)
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
