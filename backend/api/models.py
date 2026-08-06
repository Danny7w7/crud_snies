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
    periodo_primer_semestre = models.CharField(max_length=20, blank=True)

    class Meta:
        verbose_name = 'Estudiante'
        verbose_name_plural = 'Estudiantes'
        ordering = ['primer_apellido', 'primer_nombre']

    def __str__(self):
        return f'{self.primer_nombre} {self.primer_apellido}'
