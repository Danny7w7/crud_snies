from django.conf import settings
from rest_framework import serializers

from .models import Departamento, Estudiante, EstudianteSwa, Municipio, Programa


class EstudianteSerializer(serializers.ModelSerializer):
    municipio_detalle = serializers.SerializerMethodField()
    municipio_nacimiento_detalle = serializers.SerializerMethodField()

    class Meta:
        model = Estudiante
        fields = '__all__'

    def validate_codigo_estudiante(self, value):
        if not EstudianteSwa.objects.filter(codigo=value).exists():
            raise serializers.ValidationError(
                'El estudiante no existe en la base institucional. '
                'No se permite guardar información.'
            )
        return value

    def validate_programa(self, value):
        if value is not None and value != '':
            try:
                codigo = int(value)
            except (TypeError, ValueError):
                codigo = None
            if codigo in settings.PROGRAMAS_EXCLUIDOS:
                raise serializers.ValidationError(
                    'El programa seleccionado no está habilitado para registro.'
                )
        return value

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


class ProgramaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Programa
        fields = ['codigo', 'nombre']

class PersonaSerializer(serializers.ModelSerializer):
    """Serializa un registro de 'estudiante' con los datos de su 'persona'.

    Mantiene los mismos nombres de campo que consumia el serializador
    anterior (basado en Persona) para no romper el frontend.
    """

    id = serializers.CharField(source='codigo', read_only=True)
    codigo_estudiante = serializers.CharField(source='codigo', read_only=True)
    es_reintegro = serializers.BooleanField(read_only=True)
    identificacion = serializers.CharField(source='persona.identificacion', default=None, read_only=True)
    tipo_identificacion = serializers.IntegerField(source='persona.tipo_identificacion', default=None, read_only=True)
    nombre = serializers.CharField(source='persona.nombre', default=None, read_only=True)
    apellido = serializers.CharField(source='persona.apellido', default=None, read_only=True)
    direccion = serializers.CharField(source='persona.direccion', default=None, read_only=True)
    telefono = serializers.CharField(source='persona.telefono', default=None, read_only=True)
    email = serializers.CharField(source='persona.email', default=None, read_only=True)
    sexo = serializers.CharField(source='persona.sexo', default=None, read_only=True)

    class Meta:
        model = EstudianteSwa
        fields = [
            'id',
            'identificacion',
            'tipo_identificacion',
            'nombre',
            'apellido',
            'codigo_estudiante',
            'es_reintegro',
            'periodo_ingreso',
            'programa',
            'direccion',
            'telefono',
            'email',
            'sexo',
        ]
