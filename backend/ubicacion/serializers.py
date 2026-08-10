from rest_framework import serializers

from .models import Ubicacion


class UbicacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ubicacion
        fields = ['id', 'codigo_estudiante', 'persona_id', 'fecha_ubicacion']
