from rest_framework import serializers

from .models import Departamento, Estudiante, Municipio


class EstudianteSerializer(serializers.ModelSerializer):
    municipio_detalle = serializers.SerializerMethodField()
    municipio_nacimiento_detalle = serializers.SerializerMethodField()

    class Meta:
        model = Estudiante
        fields = '__all__'

    def get_municipio_detalle(self, obj):
        if obj.municipio:
            return f'{obj.municipio.nombre} - {obj.municipio.codigo}'
        return None

    def get_municipio_nacimiento_detalle(self, obj):
        if obj.municipio_nacimiento:
            return f'{obj.municipio_nacimiento.nombre} - {obj.municipio_nacimiento.codigo}'
        return None


class MunicipioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Municipio
        fields = '__all__'


class DepartamentoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Departamento
        fields = '__all__'
