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
