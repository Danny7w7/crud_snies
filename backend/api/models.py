from django.db import models


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

    class Meta:
        verbose_name = 'Estudiante'
        verbose_name_plural = 'Estudiantes'
        ordering = ['primer_apellido', 'primer_nombre']

    def __str__(self):
        return f'{self.primer_nombre} {self.primer_apellido}'
