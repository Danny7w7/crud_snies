from datetime import date

from django.core.validators import MaxValueValidator, MinValueValidator
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
from config.destino_migracion import bloqueo_migracion
from .codigos_persona import (
    TIPOS_DOCUMENTO, tipos_documento_destino, obtener_o_crear_persona, registrar_equivalencia,
)
from .esquema_destino import validar_esquema_destino

# Codigos de tipo de documento del frontend -> codigo en erp_tipodocumentoidentidad
TIPO_DOCUMENTO_ERP = {'CC': 'CC', 'CE': 'CE', 'TI': 'TI', 'PA': 'PP', 'RC': 'RC'}

# Correos institucionales de relleno que no se permiten guardar.
EMAILS_PLACEHOLDER = {'sinemail@corsalud.edu.co', 'sindefnir@corsalud.edu.co'}

# Sexo SWA (M/F) -> codigo sexo_biologico de la ERP.
SEXO_MAP = {'M': 1, 'F': 2}

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
    """Valida que el codigo (SWA) exista como id en academico_programa."""
    if not codigo:
        return None
    try:
        programa_id = int(codigo)
    except (TypeError, ValueError):
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT 1 FROM academico_programa WHERE id = %s',
            [programa_id],
        )
        row = cursor.fetchone()
    return programa_id if row else None


def _programa_codigo_front(programa_id):
    """El id de academico_programa ES el codigo del programa en el SWA."""
    if not programa_id:
        return ''
    return str(programa_id)


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
    codigo_persona = serializers.CharField(source='persona_id', read_only=True)
    estado_general = serializers.ChoiceField(
        choices=PersonaErp.EstadoGeneral.choices, required=False,
    )
    codigos_estudiante_swa = serializers.ListField(
        child=serializers.CharField(), read_only=True,
    )
    tipo_identificacion = serializers.CharField()
    numero_identificacion = serializers.CharField()
    telefono = serializers.IntegerField(
        required=True,
        validators=[
            MinValueValidator(1000000000),
            MaxValueValidator(9999999999),
        ],
    )
    email = serializers.EmailField(required=True, max_length=254)
    sexo = serializers.ChoiceField(choices=[('M', 'M'), ('F', 'F')], required=True)
    fecha_expedicion_documento = serializers.DateField(required=True)
    lugar_expedicion_documento = serializers.IntegerField(required=True)
    primer_nombre = serializers.CharField()
    segundo_nombre = serializers.CharField(required=False, allow_blank=True, default='')
    primer_apellido = serializers.CharField()
    segundo_apellido = serializers.CharField()
    codigo_estudiante = serializers.CharField()
    programa = serializers.CharField()
    programa_secundario = serializers.IntegerField(source='programa_secundario_id', read_only=True)
    municipio = serializers.IntegerField(required=True)
    pais_nacimiento = serializers.CharField()
    municipio_nacimiento = serializers.IntegerField(required=True)
    periodo_anio = serializers.CharField()
    periodo_semestre = serializers.CharField()

    class Meta:
        model = VinculoPersona
        fields = [
            'id',
            'codigo_persona',
            'estado_general',
            'codigos_estudiante_swa',
            'programa_secundario',
            'tipo_identificacion',
            'numero_identificacion',
            'telefono',
            'email',
            'sexo',
            'fecha_expedicion_documento',
            'lugar_expedicion_documento',
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

    def validate_email(self, value):
        email = (value or '').strip().lower()
        if email in EMAILS_PLACEHOLDER:
            raise serializers.ValidationError(
                'Correo institucional de relleno no permitido'
            )
        return email

    def validate_programa(self, value):
        if not value:
            return value
        if _programa_erp_id(value) is None:
            raise serializers.ValidationError(
                'No se encontró el programa de esa persona en la ERP.'
            )
        return value

    def _datos_validados(self, data, tipos):
        try:
            nro_documento = int(data['numero_identificacion'])
        except (TypeError, ValueError):
            raise serializers.ValidationError(
                {'numero_identificacion': 'Debe ser un número válido'}
            )
        tipo_documento_id = tipos.get(TIPO_DOCUMENTO_ERP.get(data['tipo_identificacion']))
        if tipo_documento_id is None:
            raise serializers.ValidationError(
                {'tipo_identificacion': 'Tipo de documento no encontrado en la ERP'}
            )
        return tipo_documento_id, nro_documento

    def _rescatar_direccion_swa(self, codigo_estudiante):
        """Direccion de la persona en SWA, limitada al tamano de la ERP (200)."""
        estudiante = (
            EstudianteSwa.objects.select_related('persona')
            .filter(codigo=codigo_estudiante)
            .first()
        )
        direccion = ''
        if estudiante and estudiante.persona and estudiante.persona.direccion:
            direccion = estudiante.persona.direccion[:200]
        return direccion

    def _actualizar_contacto_persona(self, persona, data):
        cambios = {campo: data[campo] for campo in ('email', 'telefono')
                   if campo in data and getattr(persona, campo) != data[campo]}
        if cambios:
            PersonaErp.objects.using('default').filter(pk=persona.pk).update(**cambios)

    def _aplicar_datos_secundarios(self, persona, data, editar=False):
        pais = data.get('pais_nacimiento', '').strip().upper()
        datos, _ = DatosSecundarios.objects.using('default').select_for_update().get_or_create(persona=persona)
        campos = {
            'lugar_expedicion_documento_id': data.get('lugar_expedicion_documento'),
            'fecha_expedicion_documento': data.get('fecha_expedicion_documento'),
            'municipio_id': data.get('municipio'),
            'lugar_nacimiento_id': data.get('municipio_nacimiento'),
            'sexo_biologico': SEXO_MAP[data['sexo']],
            'pais_nacimiento': ISO2_TO_ISO3.get(pais, pais),
        }
        direccion_swa = self._rescatar_direccion_swa(data['codigo_estudiante'])
        from scripts.migrar_swa import _es_direccion_invalida
        if not datos.direccion and direccion_swa and not _es_direccion_invalida(direccion_swa):
            campos['direccion'] = direccion_swa
        cambios = {campo: valor for campo, valor in campos.items() if valor not in (None, '')
                   and (editar or getattr(datos, campo) in (None, '')) and getattr(datos, campo) != valor}
        if cambios:
            DatosSecundarios.objects.using('default').filter(pk=datos.pk).update(**cambios)

    def _preparar_persona(self, validated_data):
        from .codigos_persona import cargar_grupos, nombre_persona
        try:
            validar_esquema_destino('registro')
            grupos, _, estudiantes = cargar_grupos()
            estudiante = next((e for e in estudiantes if e.codigo == validated_data['codigo_estudiante']), None)
            if estudiante is None or estudiante.persona_id not in grupos:
                raise ValueError('Identidad SWA pendiente de revision.')
            grupo = grupos[estudiante.persona_id]
            tipos = tipos_documento_destino()
            tipo_id, documento = self._datos_validados(validated_data, tipos)
            if TIPOS_DOCUMENTO[grupo.tipo_swa] not in tipos:
                raise ValueError('Tipo de documento pendiente en el ERP.')
            permitidos = {tipos[c] for c in ('CC', 'TI') if c in tipos} if grupo.tipo_swa in (1, 4) else {tipos[TIPOS_DOCUMENTO[grupo.tipo_swa]]}
            nombre = PersonaErp(primer_nombre=validated_data['primer_nombre'],
                segundo_nombre=validated_data.get('segundo_nombre', ''),
                primer_apellido=validated_data['primer_apellido'],
                segundo_apellido=validated_data.get('segundo_apellido', ''))
            if documento != grupo.documento or tipo_id not in permitidos or nombre_persona(nombre) != grupo.nombre:
                raise ValueError('Los datos del formulario no corresponden a la identidad SWA.')
            persona, _ = obtener_o_crear_persona(grupo, tipos, {
                'primer_nombre': validated_data['primer_nombre'],
                'segundo_nombre': validated_data.get('segundo_nombre', ''),
                'primer_apellido': validated_data['primer_apellido'],
                'segundo_apellido': validated_data.get('segundo_apellido', ''),
                'email': validated_data['email'], 'telefono': validated_data.get('telefono'),
                'estado_general': validated_data.get('estado_general'),
            })
        except ValueError as exc:
            raise serializers.ValidationError({'codigo_estudiante': str(exc)}) from exc
        mapa = {pk: persona.pk for pk, identidad in grupos.items() if identidad.persona_id == grupo.persona_id}
        return persona, estudiante, estudiantes, mapa

    def _guardar_vinculo_fuente(self, persona, estudiante, data, estudiantes, mapa, instance=None):
        from .codigos_persona import PROGRAMAS_INGLES
        from .programas_swa import (cargar_academico, resolver_vinculo_ingles,
                                   tiene_carrera, guardar_asociacion, vinculo_principal)
        tipo = TipoVinculo.objects.using('default').filter(nombre__iexact='Estudiante').first()
        if tipo is None:
            raise serializers.ValidationError({'codigo_estudiante': 'No existe el vinculo Estudiante.'})
        try:
            if estudiante.programa in PROGRAMAS_INGLES and tiene_carrera(persona.pk, estudiantes, mapa):
                periodo = int(f"{data['periodo_anio']}{data['periodo_semestre']}")
                sedes, matriculas, _ = cargar_academico()
                vinculo, motivo = resolver_vinculo_ingles(estudiante, periodo, persona.pk,
                                                         estudiantes, mapa, sedes, matriculas)
                if vinculo is None:
                    raise ValueError(motivo)
                vinculo = VinculoPersona.objects.using('default').select_for_update().get(pk=vinculo.pk)
                if vinculo.programa_secundario_id not in (None, estudiante.programa):
                    raise ValueError('El vinculo ya tiene otro programa secundario.')
                if instance is not None and instance.pk != vinculo.pk:
                    raise ValueError('El formulario no corresponde al vinculo existente.')
                if data.get('programa') and int(data['programa']) != vinculo.programa_id:
                    raise ValueError('La carrera del formulario no corresponde a la asociacion de ingles.')
                vinculo.programa_secundario_id = estudiante.programa
                vinculo.save(using='default', update_fields=['programa_secundario'])
                guardar_asociacion(estudiante, periodo, persona.pk, vinculo, '')
            else:
                if data.get('programa') and int(data['programa']) != estudiante.programa:
                    raise ValueError('El programa debe corresponder al registro SWA.')
                vinculo, _ = vinculo_principal(estudiante, persona.pk, tipo, alta_manual=True)
                if instance is not None and instance.pk != vinculo.pk:
                    raise ValueError('No se puede reasignar el vinculo existente.')
                registrar_equivalencia(estudiante, vinculo)
        except (ValueError, TypeError) as exc:
            raise serializers.ValidationError({'codigo_estudiante': str(exc)}) from exc
        self._marcar_ubicado(persona, estudiante.codigo)
        return vinculo

    @bloqueo_migracion()
    def create(self, validated_data):
        persona, estudiante, estudiantes, mapa = self._preparar_persona(validated_data)
        self._aplicar_datos_secundarios(persona, validated_data)
        return self._guardar_vinculo_fuente(persona, estudiante, validated_data, estudiantes, mapa)

    def _marcar_ubicado(self, persona, codigo_estudiante):
        """Registra en la tabla managed que el estudiante ya se ubico."""
        Ubicacion.objects.using('default').update_or_create(
            codigo_estudiante=codigo_estudiante,
            defaults={'persona_id': persona.id},
        )

    @bloqueo_migracion()
    def update(self, instance, validated_data):
        persona, estudiante, estudiantes, mapa = self._preparar_persona(validated_data)
        if instance.persona_id != persona.pk:
            raise serializers.ValidationError({'codigo_estudiante': 'La persona existente tiene otro ID.'})
        self._actualizar_contacto_persona(persona, validated_data)
        self._aplicar_datos_secundarios(persona, validated_data, editar=True)
        return self._guardar_vinculo_fuente(persona, estudiante, validated_data, estudiantes, mapa, instance)

    def to_representation(self, obj):
        persona = obj.persona
        codigos = sorted({e.codigo_estudiante for e in obj.equivalencias_swa.all()} |
                         {a.codigo_estudiante for a in obj.programas_swa.all() if not a.motivo_pendiente})
        datos = None
        try:
            datos = persona.datos_secundarios
        except DatosSecundarios.DoesNotExist:
            datos = None

        anio, semestre = _periodo_anio_semestre(obj.periodo)
        pais = ''
        if datos and datos.pais_nacimiento:
            pais = ISO3_TO_ISO2.get(datos.pais_nacimiento.upper(), datos.pais_nacimiento)
        return {
            'id': obj.id,
            'codigo_persona': str(persona.pk),
            'estado_general': persona.estado_general,
            'programa_secundario': obj.programa_secundario_id,
            'codigos_estudiante_swa': codigos,
            'tipo_identificacion': _tipo_documento_front(persona.tipo_documento_id),
            'numero_identificacion': str(persona.nro_documento),
            'fecha_expedicion_documento': (
                datos.fecha_expedicion_documento.isoformat()
                if datos and datos.fecha_expedicion_documento
                else None
            ),
            'lugar_expedicion_documento': (
                datos.lugar_expedicion_documento_id if datos else None
            ),
            'primer_nombre': persona.primer_nombre,
            'segundo_nombre': persona.segundo_nombre,
            'primer_apellido': persona.primer_apellido,
            'segundo_apellido': persona.segundo_apellido,
            'codigo_estudiante': codigos[0] if codigos else '',
            'programa': _programa_codigo_front(obj.programa_id),
            'municipio': datos.municipio_id if datos else None,
            'municipio_nacimiento': datos.lugar_nacimiento_id if datos else None,
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
    codigo_persona = serializers.CharField(read_only=True, default=None)
    codigos_estudiante_swa = serializers.ListField(child=serializers.CharField(), read_only=True, default=list)
    programa_principal = serializers.IntegerField(read_only=True, allow_null=True, default=None)
    programa_secundario = serializers.IntegerField(read_only=True, allow_null=True, default=None)
    periodo_contexto = serializers.IntegerField(read_only=True, allow_null=True, default=None)
    codigo_estudiante = serializers.CharField(source='codigo', read_only=True)
    es_reintegro = serializers.BooleanField(read_only=True)
    identificacion = serializers.CharField(source='persona.identificacion', default=None, read_only=True)
    tipo_identificacion = serializers.IntegerField(source='tipo_identificacion_canonico', default=None, read_only=True)
    nombre = serializers.CharField(source='persona.nombre', default=None, read_only=True)
    apellido = serializers.CharField(source='persona.apellido', default=None, read_only=True)
    direccion = serializers.CharField(source='persona.direccion', default=None, read_only=True)
    telefono = serializers.CharField(source='persona.telefono', default=None, read_only=True)
    email = serializers.CharField(source='persona.email', default=None, read_only=True)
    sexo = serializers.CharField(source='persona.sexo', default=None, read_only=True)

    class Meta:
        model = EstudianteSwa
        fields = [
            'id', 'codigo_persona', 'codigos_estudiante_swa',
            'programa_principal', 'programa_secundario', 'periodo_contexto',
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
