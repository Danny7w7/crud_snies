from django.contrib import admin

from .models import DatosSecundarios, PersonaErp, TipoVinculo, VinculoPersona


@admin.register(PersonaErp)
class PersonaErpAdmin(admin.ModelAdmin):
    list_display = (
        'nro_documento',
        'primer_nombre',
        'primer_apellido',
        'tipo_documento_id',
    )
    search_fields = ('nro_documento', 'primer_nombre', 'primer_apellido')


@admin.register(DatosSecundarios)
class DatosSecundariosAdmin(admin.ModelAdmin):
    list_display = ('persona', 'municipio_id', 'pais_nacimiento')
    search_fields = ('persona__primer_nombre', 'persona__primer_apellido')


@admin.register(VinculoPersona)
class VinculoPersonaAdmin(admin.ModelAdmin):
    list_display = ('persona', 'tipo_vinculo', 'periodo', 'activo')
    list_filter = ('tipo_vinculo', 'activo')


@admin.register(TipoVinculo)
class TipoVinculoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'estado')
