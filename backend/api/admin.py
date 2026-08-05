from django.contrib import admin

from .models import Estudiante


@admin.register(Estudiante)
class EstudianteAdmin(admin.ModelAdmin):
    list_display = (
        'codigo_estudiante',
        'primer_nombre',
        'primer_apellido',
        'tipo_identificacion',
        'numero_identificacion',
    )
    search_fields = ('codigo_estudiante', 'numero_identificacion', 'primer_nombre', 'primer_apellido')
