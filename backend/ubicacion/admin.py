from django.contrib import admin

from .models import Ubicacion


@admin.register(Ubicacion)
class UbicacionAdmin(admin.ModelAdmin):
    list_display = ('codigo_estudiante', 'persona_id', 'fecha_ubicacion')
    search_fields = ('codigo_estudiante',)
