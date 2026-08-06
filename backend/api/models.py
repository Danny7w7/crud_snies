from django.db import models


class Departamento(models.Model):
    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=10)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.codigo})'


class Municipio(models.Model):
    departamento = models.ForeignKey(
        Departamento, on_delete=models.CASCADE, related_name='municipios'
    )
    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=10)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.codigo})'


class Estudiante(models.Model):
    TIPO_IDENTIFICACION = [
        ('CC', 'Cédula de Ciudadanía'),
        ('CE', 'Cédula de Extranjería'),
        ('TI', 'Tarjeta de Identidad'),
        ('PA', 'Pasaporte'),
        ('RC', 'Registro Civil'),
    ]

    tipo_identificacion = models.CharField(max_length=2, choices=TIPO_IDENTIFICACION)
    numero_identificacion = models.CharField(max_length=20, unique=True)
    primer_nombre = models.CharField(max_length=50)
    segundo_nombre = models.CharField(max_length=50, blank=True)
    primer_apellido = models.CharField(max_length=50)
    segundo_apellido = models.CharField(max_length=50)
    codigo_estudiante = models.CharField(max_length=20, unique=True)
    programa = models.CharField(max_length=100, blank=True)
    municipio = models.ForeignKey(
        Municipio, on_delete=models.SET_NULL, null=True, blank=True, related_name='estudiantes_residencia'
    )
    pais_nacimiento = models.CharField(max_length=100, blank=True)
    municipio_nacimiento = models.ForeignKey(
        Municipio, on_delete=models.SET_NULL, null=True, blank=True, related_name='estudiantes_nacimiento'
    )
    periodo_anio = models.IntegerField(null=True, blank=True)
    periodo_semestre = models.IntegerField(
        null=True, blank=True, choices=[(1, '1'), (2, '2')]
    )

    class Meta:
        verbose_name = 'Estudiante'
        verbose_name_plural = 'Estudiantes'
        ordering = ['primer_apellido', 'primer_nombre']

    def __str__(self):
        return f'{self.primer_nombre} {self.primer_apellido}'


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
