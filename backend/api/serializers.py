from datetime import date

from django.db import connection
from rest_framework import serializers

from .models import (
    DatosSecundarios,
    Departamento,
    EstudianteSwa,
    Municipio,
    PersonaErp,
    Programa,
    TipoVinculo,
    VinculoPersona,
)
from ubicacion.models import Ubicacion

# Codigos de tipo de documento del frontend -> codigo en erp_tipodocumentoidentidad
TIPO_DOCUMENTO_ERP = {'CC': 'CC', 'CE': 'CE', 'TI': 'TI', 'PA': 'PP', 'RC': 'RC'}

# pais_nacimiento: el frontend usa ISO-2, la ERP guarda ISO-3.
ISO2_TO_ISO3 = {
    'AR': 'ARG', 'AU': 'AUS', 'BE': 'BEL', 'BO': 'BOL', 'BR': 'BRA',
    'CA': 'CAN', 'CH': 'CHE', 'CL': 'CHL', 'CN': 'CHN', 'CO': 'COL',
    'CR': 'CRI', 'CU': 'CUB', 'DE': 'DEU', 'DK': 'DNK', 'DO': 'DOM',
    'EC': 'ECU', 'EG': 'EGY', 'ES': 'ESP', 'FI': 'FIN', 'FR': 'FRA',
    'GB': 'GBR', 'GT': 'GTM', 'HN': 'HND', 'HT': 'HTI', 'IN': 'IND',
    'IT': 'ITA', 'JP': 'JPN', 'KR': 'KOR', 'MX': 'MEX', 'NI': 'NIC',
    'NL': 'NLD', 'NO': 'NOR', 'NZ': 'NZL', 'PA': 'PAN', 'PE': 'PER',
    'PH': 'PHL', 'PL': 'POL', 'PT': 'PRT', 'PY': 'PRY', 'RO': 'ROU',
    'RU': 'RUS', 'SE': 'SWE', 'SV': 'SLV', 'TR': 'TUR', 'UY': 'URY',
    'US': 'USA', 'VE': 'VEN', 'ZA': 'ZAF',
}
ISO3_TO_ISO2 = {v: k for k, v in ISO2_TO_ISO3.items()}


def _tipo_documento_erp_id(codigo):
    codigo_erp = TIPO_DOCUMENTO_ERP.get(codigo, codigo)
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT id FROM erp_tipodocumentoidentidad WHERE codigo = %s', [codigo_erp]
        )
        row = cursor.fetchone()
    return row[0] if row else None


def _tipo_documento_front(tipo_documento_id):
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT codigo FROM erp_tipodocumentoidentidad WHERE id = %s',
            [tipo_documento_id],
        )
        row = cursor.fetchone()
    if not row:
        return None
    codigo_erp = row[0]
    for front, erp in TIPO_DOCUMENTO_ERP.items():
        if erp == codigo_erp:
            return front
    return codigo_erp


def _programa_erp_id(codigo):
    if not codigo:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT id FROM academico_programa WHERE codigo_interno = %s',
            [str(codigo)],
        )
        row = cursor.fetchone()
    return row[0] if row else None


def _programa_codigo_front(programa_id):
    if not programa_id:
        return ''
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT codigo_interno FROM academico_programa WHERE id = %s', [programa_id]
        )
        row = cursor.fetchone()
    return row[0] if row else ''


def _municipio_id_por_lugar(lugar):
    """El lugar de nacimiento se guarda como 'NOMBRE - CODIGO'; se intenta
    devolver el id del municipio ERP que coincide por nombre."""
    if not lugar:
        return None
    nombre = lugar.split(' - ')[0].strip() if ' - ' in lugar else lugar.strip()
    if not nombre:
        return None
    municipio = Municipio.objects.filter(nombre__iexact=nombre).first()
    return municipio.id if municipio else None


def _periodo_anio_semestre(periodo):
    if not periodo or '-' not in periodo:
        return '', ''
    anio, semestre = periodo.split('-', 1)
    if semestre not in ('1', '2'):
        return '', ''
    return anio, semestre


class EstudianteSerializer(serializers.ModelSerializer):
    """CRUD de estudiantes: la consulta valida contra swa y el guardado
    se hace en la ERP local (erp_persona + erp_datossecundarios +
    erp_vinculopersona con vinculo 'Estudiante')."""

    id = serializers.IntegerField(read_only=True)
    tipo_identificacion = serializers.CharField()
    numero_identificacion = serializers.CharField()
    fecha_expedicion_documento = serializers.DateField(required=True)
    primer_nombre = serializers.CharField()
    segundo_nombre = serializers.CharField(required=False, allow_blank=True, default='')
    primer_apellido = serializers.CharField()
    segundo_apellido = serializers.CharField()
    codigo_estudiante = serializers.CharField()
    programa = serializers.CharField()
    municipio = serializers.IntegerField(required=True)
    pais_nacimiento = serializers.CharField()
    municipio_nacimiento = serializers.IntegerField(required=True)
    periodo_anio = serializers.CharField()
    periodo_semestre = serializers.CharField()

    class Meta:
        model = VinculoPersona
        fields = [
            'id',
            'tipo_identificacion',
            'numero_identificacion',
            'fecha_expedicion_documento',
            'primer_nombre',
            'segundo_nombre',
            'primer_apellido',
            'segundo_apellido',
            'codigo_estudiante',
            'programa',
            'municipio',
            'pais_nacimiento',
            'municipio_nacimiento',
            'periodo_anio',
            'periodo_semestre',
        ]

    def validate_codigo_estudiante(self, value):
        if not EstudianteSwa.objects.filter(codigo=value).exists():
            raise serializers.ValidationError(
                'El estudiante no existe en la base institucional. '
                'No se permite guardar información.'
            )
        return value

    def validate_tipo_identificacion(self, value):
        if value not in TIPO_DOCUMENTO_ERP:
            raise serializers.ValidationError('Tipo de identificación no válido')
        return value

    def validate_programa(self, value):
        if not value:
            return value
        if _programa_erp_id(value) is None:
            raise serializers.ValidationError(
                'No se encontró el programa de esa persona en la ERP.'
            )
        return value

    def _datos_validados(self, data):
        try:
            nro_documento = int(data['numero_identificacion'])
        except (TypeError, ValueError):
            raise serializers.ValidationError(
                {'numero_identificacion': 'Debe ser un número válido'}
            )
        tipo_documento_id = _tipo_documento_erp_id(data['tipo_identificacion'])
        if tipo_documento_id is None:
            raise serializers.ValidationError(
                {'tipo_identificacion': 'Tipo de documento no encontrado en la ERP'}
            )
        return tipo_documento_id, nro_documento

    def _aplicar_persona(self, persona, data, tipo_documento_id, nro_documento):
        persona.nro_documento = nro_documento
        persona.tipo_documento_id = tipo_documento_id
        persona.primer_nombre = data['primer_nombre']
        persona.segundo_nombre = data.get('segundo_nombre', '')
        persona.primer_apellido = data['primer_apellido']
        persona.segundo_apellido = data.get('segundo_apellido', '')
        persona.email = persona.email or ''
        persona.direccion = persona.direccion or ''
        persona.save()
        return persona

    def _aplicar_datos_secundarios(self, persona, data):
        municipio_id = data.get('municipio')
        municipio_nacimiento = data.get('municipio_nacimiento')
        lugar_nacimiento = ''
        if municipio_nacimiento:
            municipio = Municipio.objects.filter(id=municipio_nacimiento).first()
            if municipio:
                lugar_nacimiento = f'{municipio.nombre} - {municipio.codigo}'
        pais = data.get('pais_nacimiento', '').strip().upper()
        pais_iso3 = ISO2_TO_ISO3.get(pais, pais or 'XXX')

        datos, _ = DatosSecundarios.objects.get_or_create(persona=persona)
        datos.barrio = datos.barrio or ''
        datos.direccion = datos.direccion or ''
        fecha_expedicion = data.get('fecha_expedicion_documento')
        if fecha_expedicion:
            datos.fecha_expedicion_documento = fecha_expedicion
        datos.municipio_id = municipio_id if municipio_id else datos.municipio_id
        datos.lugar_nacimiento = lugar_nacimiento or datos.lugar_nacimiento
        datos.pais_nacimiento = pais_iso3 if pais else datos.pais_nacimiento
        datos.save()
        return datos

    def _aplicar_vinculo(self, vinculo, persona, data):
        programa_id = _programa_erp_id(data.get('programa', ''))
        anio = data.get('periodo_anio', '')
        semestre = data.get('periodo_semestre', '')
        if anio and semestre:
            periodo = f'{anio}-{semestre}'
        elif anio:
            periodo = f'{anio}-1'
        else:
            periodo = vinculo.periodo or f'{date.today().year}-1'
        vinculo.persona = persona
        vinculo.activo = True
        vinculo.periodo = periodo
        vinculo.codigo_aspirante = data['codigo_estudiante']
        vinculo.programa_id = programa_id
        if anio:
            vinculo.fecha_inicio = date(int(anio), 1, 1)
        return vinculo

    def create(self, validated_data):
        tipo_documento_id, nro_documento = self._datos_validados(validated_data)
        persona, creada = PersonaErp.objects.get_or_create(
            tipo_documento_id=tipo_documento_id,
            nro_documento=nro_documento,
            defaults={
                'primer_nombre': validated_data['primer_nombre'],
                'segundo_nombre': validated_data.get('segundo_nombre', ''),
                'primer_apellido': validated_data['primer_apellido'],
                'segundo_apellido': validated_data.get('segundo_apellido', ''),
                'email': '',
                'direccion': '',
                'telefono': None,
            },
        )
        self._aplicar_persona(persona, validated_data, tipo_documento_id, nro_documento)
        self._aplicar_datos_secundarios(persona, validated_data)

        tipo_estudiante = TipoVinculo.objects.filter(nombre__iexact='Estudiante').first()
        if tipo_estudiante is None:
            raise serializers.ValidationError(
                {'codigo_estudiante': 'No existe el vínculo "Estudiante" en la ERP'}
            )
        vinculo = (
            VinculoPersona.objects.filter(persona=persona, tipo_vinculo=tipo_estudiante)
            .order_by('-id')
            .first()
        )
        if vinculo is None:
            vinculo = VinculoPersona(persona=persona, tipo_vinculo=tipo_estudiante)
            vinculo.fecha_inicio = date.today()
        vinculo = self._aplicar_vinculo(vinculo, persona, validated_data)
        vinculo.save()
        self._marcar_ubicado(persona, validated_data['codigo_estudiante'])
        return vinculo

    def _marcar_ubicado(self, persona, codigo_estudiante):
        """Registra en la tabla managed que el estudiante ya se ubico."""
        Ubicacion.objects.update_or_create(
            codigo_estudiante=codigo_estudiante,
            defaults={'persona_id': persona.id},
        )

    def update(self, instance, validated_data):
        tipo_documento_id, nro_documento = self._datos_validados(validated_data)
        persona, creada = PersonaErp.objects.get_or_create(
            tipo_documento_id=tipo_documento_id,
            nro_documento=nro_documento,
            defaults={
                'primer_nombre': validated_data['primer_nombre'],
                'segundo_nombre': validated_data.get('segundo_nombre', ''),
                'primer_apellido': validated_data['primer_apellido'],
                'segundo_apellido': validated_data.get('segundo_apellido', ''),
                'email': '',
                'direccion': '',
                'telefono': None,
            },
        )
        self._aplicar_persona(persona, validated_data, tipo_documento_id, nro_documento)
        self._aplicar_datos_secundarios(persona, validated_data)
        instance = self._aplicar_vinculo(instance, persona, validated_data)
        instance.save()
        self._marcar_ubicado(persona, validated_data['codigo_estudiante'])
        return instance

    def to_representation(self, obj):
        persona = obj.persona
        datos = None
        try:
            datos = persona.datos_secundarios.get()
        except DatosSecundarios.DoesNotExist:
            datos = None

        anio, semestre = _periodo_anio_semestre(obj.periodo)
        pais = ''
        if datos and datos.pais_nacimiento:
            pais = ISO3_TO_ISO2.get(datos.pais_nacimiento.upper(), datos.pais_nacimiento)
        return {
            'id': obj.id,
            'tipo_identificacion': _tipo_documento_front(persona.tipo_documento_id),
            'numero_identificacion': str(persona.nro_documento),
            'fecha_expedicion_documento': (
                datos.fecha_expedicion_documento.isoformat()
                if datos and datos.fecha_expedicion_documento
                else None
            ),
            'primer_nombre': persona.primer_nombre,
            'segundo_nombre': persona.segundo_nombre,
            'primer_apellido': persona.primer_apellido,
            'segundo_apellido': persona.segundo_apellido,
            'codigo_estudiante': obj.codigo_aspirante or '',
            'programa': _programa_codigo_front(obj.programa_id),
            'municipio': datos.municipio_id if datos else None,
            'municipio_nacimiento': (
                _municipio_id_por_lugar(datos.lugar_nacimiento) if datos else None
            ),
            'pais_nacimiento': pais,
            'periodo_anio': anio,
            'periodo_semestre': semestre,
        }


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
