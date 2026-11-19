"""Migracion de datos del SWA (base de produccion) hacia la ERP local.

Segmentada por etapas, cada una expuesta como endpoint propio:

  migrar_programas()          programa (SWA) -> academico_programa
  migrar_asignaturas()        asignatura (SWA) -> academico_asignatura
  migrar_personas()           persona (SWA) -> erp_persona
  migrar_datos_secundarios()  erp_datossecundarios (1:1 con persona)
  migrar_vinculos()           estudiante (SWA) -> erp_vinculopersona

Cada etapa es idempotente (no duplica registros ya migrados) y devuelve
un resumen con los conteos, la lista de registros rechazados con su
motivo y los errores de base de datos por fila.

Dependencias entre etapas:
  datos_secundarios requiere personas
  vinculos requiere personas y programas
  asignaturas incorpora codigos nuevos y conserva los IDs y referencias existentes
"""

import csv
import re
import time
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import DatabaseError, connection, connections, transaction

from api.catalogos_academicos import obtener_o_crear_asignatura, obtener_o_crear_programa
from api.codigos_persona import (
    cargar_grupos, comprobar_destino, PROGRAMAS_INGLES, TIPOS_DOCUMENTO,
    obtener_o_crear_persona,
    registrar_equivalencia, tipos_documento_destino,
)
from api.esquema_destino import validar_esquema_destino
from api.programas_swa import cargar_academico, preparar_avance, tiene_carrera, vinculo_principal
from config.destino_migracion import bloqueo_migracion
from ubicacion.models import EquivalenciaPersonaSwa

from api.models import (
    DatosSecundarios,
    EstudianteSwa,
    PersonaErp,
    TipoVinculo,
)

SEXO_A_BIOLOGICO = {'M': 1, 'F': 2}

EMAILS_PLACEHOLDER = {'sinemail@corsalud.edu.co', 'sindefnir@corsalud.edu.co'}

# Emails de relleno detectados por el local-part (sinemail@..., sindefnir@...)
# sin importar el dominio (corsalud, unicorsalud, etc.).
EMAIL_PLACEHOLDER_RE = re.compile(r'^(sine?mail|sindefnir)\b')

EMAIL_RE = re.compile(r'^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$')

# Direcciones que no son una direccion real (se guardan como NULL).
DIRECCIONES_INVALIDAS = {'SINDIRECCION', 'SINDEFINIR', 'NA'}

# Minimo de digitos para considerar valido un numero de documento
# (cedulas, tarjetas de identidad, registros civiles, etc.).
MIN_DIGITOS_DOCUMENTO = 6

# Valor usado por el SWA para indicar una fecha de expedicion ficticia.
FECHA_EXPEDICION_IGNORADA = 19900101

# Maximo de digitos solo para cedula de ciudadania (1) y tarjeta de
# identidad (4); los demas tipos (CE, pasaporte, RC) no tienen tope.
MAX_DIGITOS_DOCUMENTO = 10
TIPO_DOCUMENTO_CON_MAXIMO = {1, 4}


def _partir_nombres(valor):
    partes = (valor or '').strip().split()
    return (partes[0], ' '.join(partes[1:])) if partes else ('', '')


# Particulas que forman parte del primer apellido cuando van al inicio
# (ej. "De La Hoz Ospino" -> primer apellido "De La Hoz", segundo "Ospino").
PARTICULAS_APELLIDO = {
    'DE', 'DEL', 'LA', 'LAS', 'LOS',
    'SAN', 'SANTA', 'SANTO', 'EL',
    'MAC', 'MC', 'VAN', 'VON',
}


def _partir_apellidos(valor):
    """Separa apellidos agrupando las particulas iniciales con el primero."""
    partes = (valor or '').strip().split()
    if not partes:
        return '', ''
    corte = 1
    while corte < len(partes) and partes[corte - 1].upper() in PARTICULAS_APELLIDO:
        corte += 1
    return ' '.join(partes[:corte]), ' '.join(partes[corte:])


# Prefijo comun de insert_log: 'YYYY/MM/DD HH:MM:SS <sufijo variable>'.
_PATRON_FECHA_CREACION = re.compile(r'^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})')


def _parsear_fecha_creacion(valor):
    """Convierte insert_log a datetime en zona de Bogota o None si no parsea.

    Ej: '2016/06/29 10:21:04 MIGRACION' -> 2016-06-29 10:21:04 -05:00
    """
    m = _PATRON_FECHA_CREACION.match(valor or '')
    if not m:
        return None
    try:
        naive = datetime.strptime(m.group(1), '%Y/%m/%d %H:%M:%S')
    except ValueError:
        return None
    return naive.replace(tzinfo=ZoneInfo('America/Bogota'))


def _limpiar_telefono(raw):
    """Quita puntos, comas, espacios y +57/57; debe quedar de 10 digitos."""
    digitos = ''.join(c for c in str(raw or '') if c.isdigit())
    if len(digitos) == 12 and digitos.startswith('57'):
        digitos = digitos[2:]
    if len(digitos) == 10 and digitos[0] != '0':
        return int(digitos)
    return None


def _limpiar_email(raw):
    """Devuelve el email si es valido, o None si esta vacio o no es valido."""
    texto = str(raw or '').strip().lower()
    if texto.startswith('@'):
        texto = texto[1:]
    local, _, _resto = texto.partition('@')
    if not texto or texto in EMAILS_PLACEHOLDER or EMAIL_PLACEHOLDER_RE.match(local):
        return None
    if not EMAIL_RE.match(texto):
        return None
    return texto


def _periodo_desde_ingreso(periodo_ingreso):
    """Convierte 20252 -> ('2025', '2'). Devuelve None si no es valido."""
    texto = str(periodo_ingreso or '').strip()
    if len(texto) != 5 or texto[4] not in ('1', '2'):
        return None
    return texto[:4], texto[4]


def _sembrar_tipo_vinculo(resumen):
    try:
        with transaction.atomic(using='default'):
            TipoVinculo.objects.get_or_create(
                nombre='Estudiante',
                defaults={'estado': True},
            )
    except Exception as exc:  # noqa: BLE001
        resumen['errores'].append(
            {'etapa': 'tipos_vinculo', 'clave': 'Estudiante', 'error': str(exc)}
        )


def _es_direccion_invalida(valor):
    """True si la direccion es vacia o un placeholder (SIN DIRECCION, etc.)."""
    texto = unicodedata.normalize('NFD', (valor or '').upper())
    texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
    texto = re.sub(r'[^A-Z0-9]', '', texto)
    return not texto or texto in DIRECCIONES_INVALIDAS


def _parsear_fecha_formulario(valor):
    """Parsea '2010/11/09' (YYYY/MM/DD) o '28/02/2008' (DD/MM/YYYY)."""
    texto = (valor or '').strip()
    if not texto:
        return None
    for formato in ('%Y/%m/%d', '%d/%m/%Y'):
        try:
            fecha = datetime.strptime(texto, formato).date()
        except ValueError:
            continue
        if 1900 <= fecha.year <= 2100:
            return fecha
    return None


def _persona_swa_validacion(persona, tipos_doc, tipo_identificacion=None):
    """Valida una persona del SWA para la migracion.

    Devuelve (dict, None) con los datos ya limpios si es migrable, o
    (None, motivo) con el motivo del rechazo si no lo es.
    """
    tipo = persona.tipo_identificacion if tipo_identificacion is None else tipo_identificacion
    codigo_erp = TIPOS_DOCUMENTO.get(tipo)
    tipo_doc_id = tipos_doc.get(codigo_erp) if codigo_erp else None
    if tipo_doc_id is None:
        return None, (
            f'tipo de documento sin equivalente en la ERP '
            f'({persona.tipo_identificacion})'
        )
    digitos = ''.join(c for c in str(persona.identificacion or '') if c.isdigit())
    if not digitos:
        return None, f'documento sin numeros ({persona.identificacion!r})'
    try:
        nro_documento = int(digitos)
    except (TypeError, ValueError):
        return None, f'documento no numerico ({persona.identificacion!r})'
    cantidad = len(str(nro_documento))
    if cantidad < MIN_DIGITOS_DOCUMENTO:
        return None, (
            f'documento con menos de {MIN_DIGITOS_DOCUMENTO} digitos '
            f'({nro_documento})'
        )
    if tipo in TIPO_DOCUMENTO_CON_MAXIMO and cantidad > MAX_DIGITOS_DOCUMENTO:
        return None, (
            f'documento con mas de {MAX_DIGITOS_DOCUMENTO} digitos '
            f'({nro_documento})'
        )

    primer_nombre, segundo_nombre = _partir_nombres(persona.nombre)
    primer_apellido, segundo_apellido = _partir_apellidos(persona.apellido)
    if not primer_nombre or not primer_apellido:
        return None, 'nombre o apellido incompleto'
    if any(len(valor) > 100 for valor in (
            primer_nombre, segundo_nombre, primer_apellido, segundo_apellido)):
        return None, 'nombre o apellido excede el limite de 100 caracteres del ERP'

    direccion = (persona.direccion or '')[:200]
    if _es_direccion_invalida(persona.direccion):
        direccion = ''

    return {
        'tipo_doc_id': tipo_doc_id,
        'nro_documento': nro_documento,
        'primer_nombre': primer_nombre,
        'segundo_nombre': segundo_nombre,
        'primer_apellido': primer_apellido,
        'segundo_apellido': segundo_apellido,
        'direccion': direccion,
        'email': _limpiar_email(persona.email),
        'telefono': _limpiar_telefono(persona.telefono),
        'estado_civil': persona.estado_civil or None,
        'sexo_biologico': SEXO_A_BIOLOGICO.get(persona.sexo),
        'fecha_creacion': _parsear_fecha_creacion(persona.insert_log),
    }, None


def _mapa_personas(tipos_doc):
    """Solo reutiliza equivalencias coherentes con la seleccion actual de IDs."""
    grupos, _, _ = cargar_grupos()
    aliases = dict(EquivalenciaPersonaSwa.objects.using('default')
                   .values_list('persona_swa_id', 'persona_erp_id'))
    resultado = {}
    for grupo in {g.persona_id: g for g in grupos.values()}.values():
        try:
            persona = comprobar_destino(grupo, tipos_doc)
        except ValueError:
            continue
        if persona is not None:
            for p in grupo.personas:
                if aliases.get(p.pk) == persona.pk:
                    resultado[p.pk] = persona.pk
    return resultado


def _programas_migrados():
    """Ids de los programas ya migrados a academico_programa (id = codigo SWA)."""
    with connection.cursor() as cur:
        cur.execute('SELECT id FROM academico_programa')
        return {row[0] for row in cur.fetchall()}


def _escribir_reporte_rechazadas(resumen, prefijo):
    """Guarda rechazadas y errores en un CSV legible y devuelve su ruta."""
    if not resumen['rechazadas'] and not resumen['errores']:
        return None
    reportes_dir = settings.BASE_DIR / 'reportes'
    reportes_dir.mkdir(exist_ok=True)
    nombre = f'{prefijo}_rechazadas_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    ruta = reportes_dir / nombre
    columnas = ['swa_id', 'tipo_identificacion', 'identificacion', 'nombre', 'apellido', 'motivo']
    filas = [
        {
            'swa_id': r.get('swa_id'),
            'tipo_identificacion': r.get('tipo_identificacion', ''),
            'identificacion': r.get('identificacion', ''),
            'nombre': r.get('nombre', ''),
            'apellido': r.get('apellido', ''),
            'motivo': r.get('motivo', ''),
        }
        for r in resumen['rechazadas']
    ]
    for err in resumen['errores']:
        filas.append(
            {
                'swa_id': err.get('clave'),
                'tipo_identificacion': '',
                'identificacion': '',
                'nombre': '',
                'apellido': '',
                'motivo': f'ERROR: {err.get("error")}',
            }
        )
    with open(ruta, 'w', newline='', encoding='utf-8-sig') as archivo:
        writer = csv.DictWriter(archivo, fieldnames=columnas)
        writer.writeheader()
        writer.writerows(filas)
    return str(ruta)


@bloqueo_migracion()
def migrar_programas():
    """Etapa: programa (SWA) -> academico_programa."""
    validar_esquema_destino('programas')
    inicio = time.time()
    resumen = {
        'programas': {'creados': 0, 'omitidos': 0, 'errores': 0},
        'rechazadas': [],
    }
    with connections['produccion'].cursor() as cur_swa:
        cur_swa.execute(
            'SELECT codigo, nombre, activo, snies_diurno, snies_nocturno, sede '
            'FROM programa ORDER BY codigo'
        )
        filas = cur_swa.fetchall()

    sedes = getattr(settings, 'SEDES_SWA_A_ERP', {1: 1, 3: 2})
    for codigo, nombre, activo, snies_diurno, snies_nocturno, sede in filas:
        try:
            with transaction.atomic(using='default'):
                _, creado = obtener_o_crear_programa(
                    codigo, nombre, activo, snies_diurno or snies_nocturno, sedes.get(sede))
        except ValueError as exc:
            resumen['rechazadas'].append({'codigo': codigo, 'motivo': str(exc)})
        except DatabaseError:
            resumen['programas']['errores'] += 1
        else:
            resumen['programas']['creados' if creado else 'omitidos'] += 1
    resumen['tiempo_segundos'] = round(time.time() - inicio, 2)
    return resumen


def _formulario_contacto_por_persona():
    """{swa_persona_id: {campo: raw}} con el registro mas reciente por campo.

    Usado como respaldo para direccion, celular, email y fecha de nacimiento.
    """
    resultado = {}
    campos = [
        ('direccion', 'f.direccion', "NULLIF(trim(f.direccion), '') IS NOT NULL"),
        ('celular', 'f.celular', "NULLIF(trim(f.celular), '') IS NOT NULL"),
        ('email', 'f.email', "NULLIF(trim(f.email), '') IS NOT NULL"),
        (
            'fecha_nacimiento',
            'f.fecha_nacimiento',
            "NULLIF(trim(f.fecha_nacimiento), '') IS NOT NULL",
        ),
    ]
    with connections['produccion'].cursor() as cur:
        for campo, columna, filtro in campos:
            cur.execute(
                f'SELECT DISTINCT ON (e.persona) e.persona, {columna} '
                'FROM formulario f '
                'JOIN estudiante e ON e.codigo = f.codigo_estudiante '
                f'WHERE e.persona IS NOT NULL AND {filtro} '
                'ORDER BY e.persona, f.periodo DESC NULLS LAST, '
                'f.codigo DESC NULLS LAST'
            )
            for persona_id, raw in cur.fetchall():
                resultado.setdefault(persona_id, {})[campo] = raw
    return resultado


@bloqueo_migracion()
def migrar_personas(estados_aprobados=None):
    """Importa una persona por grupo, conservando el ID canonico de SWA."""
    validar_esquema_destino('personas')
    inicio = time.time()
    resumen = {'personas': {'creadas': 0, 'omitidas': 0, 'errores': 0},
               'rechazadas': [], 'errores': []}
    mapa, pendientes, _ = cargar_grupos()
    resumen['rechazadas'] = [{'swa_id': pk, 'motivo': motivo} for pk, motivo in pendientes.items()]
    tipos_doc = tipos_documento_destino()
    estados_aprobados = estados_aprobados or {}
    contacto_formulario = _formulario_contacto_por_persona()
    grupos = {g.persona_id: g for g in mapa.values()}
    for grupo in grupos.values():
        persona = grupo.representante
        validacion, motivo = _persona_swa_validacion(persona, tipos_doc, grupo.tipo_swa)
        if validacion is None:
            resumen['rechazadas'].append({'swa_id': persona.pk, 'motivo': motivo})
            continue
        contacto = contacto_formulario.get(persona.pk, {})
        defaults = {
            'primer_nombre': validacion['primer_nombre'],
            'segundo_nombre': validacion['segundo_nombre'],
            'primer_apellido': validacion['primer_apellido'],
            'segundo_apellido': validacion['segundo_apellido'],
            'email': validacion['email'] or _limpiar_email(contacto.get('email')) or '',
            'telefono': validacion['telefono'] or _limpiar_telefono(contacto.get('celular')),
        }
        if validacion['fecha_creacion'] is not None:
            defaults['fecha_creacion'] = validacion['fecha_creacion']
        if grupo.persona_id in estados_aprobados:
            defaults['estado_general'] = estados_aprobados[grupo.persona_id]
        try:
            _, creada = obtener_o_crear_persona(grupo, tipos_doc, defaults)
            resumen['personas']['creadas' if creada else 'omitidas'] += 1
        except ValueError as exc:
            resumen['rechazadas'].append({'swa_id': grupo.persona_id, 'motivo': str(exc)})
        except Exception as exc:
            resumen['errores'].append({'etapa': 'personas', 'clave': grupo.persona_id,
                                      'error': type(exc).__name__})
    resumen['personas']['errores'] = len(resumen['errores'])
    resumen['reporte'] = _escribir_reporte_rechazadas(resumen, 'personas')
    del resumen['errores']
    resumen['tiempo_segundos'] = round(time.time() - inicio, 2)
    return resumen


def _parsear_fecha_yyyymmdd(valor):
    """Convierte 20070910 (int YYYYMMDD) a date o None si no es valido."""
    if not valor:
        return None
    try:
        n = int(valor)
        if n < 19000101 or n > 21001231:
            return None
        anio, resto = divmod(n, 10000)
        mes, dia = divmod(resto, 100)
        return date(anio, mes, dia)
    except (TypeError, ValueError):
        return None


def _fechas_nacimiento_por_persona():
    """{swa_persona_id: date} con la fecha del estudiante mas reciente."""
    fechas = {}
    with connections['produccion'].cursor() as cur:
        cur.execute(
            'SELECT DISTINCT ON (persona) persona, fecha_nacimiento '
            'FROM estudiante '
            'WHERE persona IS NOT NULL AND fecha_nacimiento IS NOT NULL '
            '  AND fecha_nacimiento > 0 '
            'ORDER BY persona, periodo_ingreso DESC, codigo DESC'
        )
        for persona_id, raw in cur.fetchall():
            fecha = _parsear_fecha_yyyymmdd(raw)
            if fecha is not None:
                fechas[persona_id] = fecha
    return fechas


def _mapa_municipios_divipola():
    """{codigo divipola sin ceros iniciales: id} de erp_municipio."""
    with connection.cursor() as cur:
        cur.execute('SELECT id, codigo FROM erp_municipio')
        return {str(r[1]).strip().lstrip('0'): r[0] for r in cur.fetchall()}


def _expedicion_por_persona(mapa_divipola):
    """{swa_persona_id: {'fecha': date|None, 'lugar_id': int|None}}.

    La fecha 19900101 se usa como valor ficticio en el SWA y se ignora.
    El lugar de expedicion se rescata aunque la fecha sea ficticia.
    """
    resultado = {}
    with connections['produccion'].cursor() as cur:
        cur.execute(
            'SELECT DISTINCT ON (e.persona) e.persona, f.fecha_expedicion, '
            '       f.documento_expedido '
            'FROM formulario f '
            'JOIN estudiante e ON e.codigo = f.codigo_estudiante '
            'WHERE e.persona IS NOT NULL '
            '  AND f.fecha_expedicion IS NOT NULL '
            '  AND f.fecha_expedicion > 0 '
            '  AND f.fecha_expedicion <> %s '
            'ORDER BY e.persona, f.periodo DESC NULLS LAST, f.codigo DESC NULLS LAST',
            [FECHA_EXPEDICION_IGNORADA],
        )
        for persona_id, raw_fecha, raw_divipola in cur.fetchall():
            fecha = _parsear_fecha_yyyymmdd(raw_fecha)
            if fecha is None:
                continue
            lugar = None
            if raw_divipola:
                lugar = mapa_divipola.get(
                    str(raw_divipola).strip().lstrip('0')
                )
            resultado[persona_id] = {'fecha': fecha, 'lugar_id': lugar}

        cur.execute(
            'SELECT DISTINCT ON (e.persona) e.persona, f.documento_expedido '
            'FROM formulario f '
            'JOIN estudiante e ON e.codigo = f.codigo_estudiante '
            'WHERE e.persona IS NOT NULL AND f.documento_expedido > 0 '
            'ORDER BY e.persona, f.periodo DESC NULLS LAST, f.codigo DESC NULLS LAST'
        )
        for persona_id, raw_divipola in cur.fetchall():
            if persona_id in resultado:
                continue
            lugar = mapa_divipola.get(str(raw_divipola).strip().lstrip('0'))
            if lugar is not None:
                resultado[persona_id] = {'fecha': None, 'lugar_id': lugar}
    return resultado


def _lugares_nacimiento_por_persona(mapa_divipola):
    """{swa_persona_id: municipio_id} con el lugar del registro mas reciente."""
    lugares = {}
    with connections['produccion'].cursor() as cur:
        cur.execute(
            'SELECT DISTINCT ON (e.persona) e.persona, f.lugar_nacimiento '
            'FROM formulario f '
            'JOIN estudiante e ON e.codigo = f.codigo_estudiante '
            'WHERE e.persona IS NOT NULL AND f.lugar_nacimiento > 0 '
            'ORDER BY e.persona, f.periodo DESC NULLS LAST, f.codigo DESC NULLS LAST'
        )
        for persona_id, raw_divipola in cur.fetchall():
            lugar = mapa_divipola.get(str(raw_divipola).strip().lstrip('0'))
            if lugar is not None:
                lugares[persona_id] = lugar
    return lugares


def _estratos_por_persona():
    """{swa_persona_id: estrato int} del registro mas reciente.

    El SWA guarda el estrato como '1.Bajo-Bajo', '2.Bajo', etc.
    """
    estratos = {}
    with connections['produccion'].cursor() as cur:
        cur.execute(
            'SELECT DISTINCT ON (e.persona) e.persona, f.estrato '
            'FROM formulario f '
            'JOIN estudiante e ON e.codigo = f.codigo_estudiante '
            'WHERE e.persona IS NOT NULL AND f.estrato ~ \'^[1-6]\\.\' '
            'ORDER BY e.persona, f.periodo DESC NULLS LAST, f.codigo DESC NULLS LAST'
        )
        for persona_id, raw in cur.fetchall():
            try:
                estrato = int(str(raw).split('.', 1)[0])
            except (TypeError, ValueError):
                continue
            if 1 <= estrato <= 6:
                estratos[persona_id] = estrato
    return estratos


@bloqueo_migracion()
def migrar_datos_secundarios():
    """Completa datos vacios; no altera columnas ni borra informacion existente."""
    validar_esquema_destino('datos_secundarios')
    inicio = time.time()
    resumen = {'datos_secundarios': {'creados': 0, 'omitidos': 0, 'errores': 0},
               'rechazadas': [], 'errores': []}
    tipos_doc = tipos_documento_destino()
    mapa = _mapa_personas(tipos_doc)
    grupos, _, _ = cargar_grupos()
    fechas = _fechas_nacimiento_por_persona()
    expediciones = _expedicion_por_persona(_mapa_municipios_divipola())
    lugares = _lugares_nacimiento_por_persona(_mapa_municipios_divipola())
    estratos = _estratos_por_persona()
    contactos = _formulario_contacto_por_persona()
    for grupo in {g.persona_id: g for g in grupos.values()}.values():
        persona_erp_id = mapa.get(grupo.persona_id)
        if persona_erp_id is None:
            resumen['rechazadas'].append({'swa_id': grupo.persona_id, 'motivo': 'Persona pendiente.'})
            continue
        # El registro de carrera tiene prioridad; los alias solo completan vacios.
        fuentes = [grupo.representante] + [p for p in grupo.personas if p.pk != grupo.persona_id]
        defaults = {'barrio': '', 'direccion': '', 'direccion_institucional': '', 'pais_nacimiento': ''}
        fecha_nacimiento = None
        for p in fuentes:
            valores, _ = _persona_swa_validacion(p, tipos_doc, grupo.tipo_swa)
            if valores is None:
                continue
            contacto = contactos.get(p.pk, {})
            direccion = valores['direccion'] or contacto.get('direccion') or ''
            if not _es_direccion_invalida(direccion) and not defaults['direccion']:
                defaults['direccion'] = direccion[:200]
            for campo, valor in {
                'sexo_biologico': valores['sexo_biologico'], 'estado_civil': valores['estado_civil'],
                'fecha_expedicion_documento': expediciones.get(p.pk, {}).get('fecha'),
                'lugar_expedicion_documento_id': expediciones.get(p.pk, {}).get('lugar_id'),
                'lugar_nacimiento_id': lugares.get(p.pk), 'estrato': estratos.get(p.pk),
            }.items():
                if defaults.get(campo) is None and valor is not None:
                    defaults[campo] = valor
            fecha_nacimiento = fecha_nacimiento or fechas.get(p.pk) or _parsear_fecha_formulario(
                contacto.get('fecha_nacimiento'))
        try:
            with transaction.atomic(using='default'):
                if fecha_nacimiento is not None:
                    PersonaErp.objects.using('default').filter(
                        pk=persona_erp_id, fecha_nacimiento__isnull=True).update(fecha_nacimiento=fecha_nacimiento)
                datos, creada = DatosSecundarios.objects.using('default').select_for_update().get_or_create(
                    persona_id=persona_erp_id, defaults=defaults)
                if not creada:
                    cambios = {k: v for k, v in defaults.items()
                               if v not in (None, '') and getattr(datos, k) in (None, '')}
                    if cambios:
                        DatosSecundarios.objects.using('default').filter(pk=datos.pk).update(**cambios)
            resumen['datos_secundarios']['creados' if creada else 'omitidos'] += 1
        except Exception as exc:
            resumen['errores'].append({'clave': grupo.persona_id, 'error': type(exc).__name__})
    resumen['datos_secundarios']['errores'] = len(resumen['errores'])
    del resumen['errores']
    resumen['tiempo_segundos'] = round(time.time() - inicio, 2)
    return resumen


@bloqueo_migracion()
def migrar_vinculos():
    """Carreras y extension como principales; ingles contextual como secundario."""
    validar_esquema_destino('vinculos')
    inicio = time.time()
    resumen = {'vinculos': {'creados': 0, 'omitidos': 0, 'errores': 0},
               'rechazadas': [], 'errores': []}
    _sembrar_tipo_vinculo(resumen)
    tipos_doc = tipos_documento_destino()
    mapa = _mapa_personas(tipos_doc)
    estudiantes = list(EstudianteSwa.objects.using('produccion').all().iterator())
    contexto = cargar_academico()
    programas = _programas_migrados()
    tipo = TipoVinculo.objects.using('default').filter(nombre__iexact='Estudiante').first()
    if tipo is None:
        raise ValueError('No existe el vinculo Estudiante en el ERP.')
    for est in estudiantes:
        codigo = (est.codigo or '').strip()
        persona_id = mapa.get(est.persona_id)
        if not codigo or persona_id is None or est.programa not in programas:
            resumen['rechazadas'].append({'codigo': codigo, 'motivo': 'Persona o programa pendiente.'})
            continue
        if est.programa in PROGRAMAS_INGLES and tiene_carrera(persona_id, estudiantes, mapa):
            continue
        try:
            with transaction.atomic(using='default'):
                vinculo, creado = vinculo_principal(est, persona_id, tipo)
                registrar_equivalencia(est, vinculo)
            resumen['vinculos']['creados' if creado else 'omitidos'] += 1
        except ValueError as exc:
            resumen['rechazadas'].append({'codigo': codigo, 'motivo': str(exc)})
        except Exception as exc:
            resumen['errores'].append({'codigo': codigo, 'error': type(exc).__name__})
    resumen['preparacion_academica'] = preparar_avance(estudiantes, mapa, tipo, contexto)
    resumen['vinculos']['errores'] = len(resumen['errores'])
    del resumen['errores']
    resumen['tiempo_segundos'] = round(time.time() - inicio, 2)
    return resumen


@bloqueo_migracion()
def migrar_asignaturas():
    """Incorpora asignaturas por codigo, conservando IDs y referencias del ERP."""
    from collections import Counter

    validar_esquema_destino('asignaturas')
    inicio = time.time()
    resumen = {'asignaturas': {'examined': 0, 'creadas': 0, 'existentes': 0,
                               'sin_pensum': 0, 'errores': 0}}
    with connections['produccion'].cursor() as cur_swa:
        cur_swa.execute(
            'SELECT a.codigo,a.nombre,a.tipo,COALESCE(a.ihs,0),COALESCE(MAX(pa.creditos),0) '
            'FROM asignatura a LEFT JOIN pensum_asignatura pa '
            'ON pa.programa=a.programa AND pa.asignatura=a.codigo '
            'GROUP BY a.programa,a.codigo,a.nombre,a.tipo,a.ihs ORDER BY a.codigo')
        filas = cur_swa.fetchall()
    repetidos = Counter(str(f[0]) for f in filas)
    for codigo, nombre, tipo, ihs, creditos in filas:
        resumen['asignaturas']['examined'] += 1
        try:
            codigo = str(codigo or '').strip()
            if not codigo or len(codigo) > 20 or repetidos[codigo] != 1:
                raise ValueError('Codigo de asignatura ambiguo o incompatible.')
            with transaction.atomic(using='default'):
                _, creada = obtener_o_crear_asignatura(
                    codigo, {'nombre': nombre, 'tipo': tipo, 'ihs': ihs}, creditos)
            resumen['asignaturas']['creadas' if creada else 'existentes'] += 1
            resumen['asignaturas']['sin_pensum'] += int(creada and creditos == 0)
        except (ValueError, DatabaseError):
            resumen['asignaturas']['errores'] += 1
    resumen['tiempo_segundos'] = round(time.time() - inicio, 2)
    return resumen


@bloqueo_migracion()
def ejecutar_migracion():
    """Ejecuta todas las etapas en orden (endpoint completo)."""
    validar_esquema_destino('completo')
    return {
        'programas': migrar_programas(),
        'personas': migrar_personas(),
        'datos_secundarios': migrar_datos_secundarios(),
        'asignaturas': migrar_asignaturas(),
        'vinculos': migrar_vinculos(),
    }
