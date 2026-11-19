"""Plan de solo lectura para conciliar las personas creadas por elecciones."""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import NamedTuple

from psycopg.errors import UndefinedColumn, UndefinedTable

from api.codigos_persona import TIPOS_DOCUMENTO, normalizar_nombre


ENTIDAD_REASIGNACION = 'reasignacion_persona_swa'
VENTANAS = (
    ('IMPORTACION_PERSONAL_ELECCIONES_V3', '17:07:17', '17:07:19'),
    ('IMPORTACION_MATRICULADOS_ESTUDIANTES', '17:07:48', '17:07:59'),
    ('IMPORTACION_EGRESADOS_CC', '17:08:15', '17:08:32'),
)
IMPORTACIONES = {
    origen: (datetime.fromisoformat(f'2026-10-28T{inicio}-05:00'),
             datetime.fromisoformat(f'2026-10-28T{fin}-05:00'))
    for origen, inicio, fin in VENTANAS
}
TABLAS_LOCALES = (
    'swa_raw.persona', 'public.erp_persona', 'public.erp_tipodocumentoidentidad',
    'public.auditoria_log_sistema', 'public.erp_vinculopersona', 'migra.mapa', 'migra.lote',
    'public.ubicacion_equivalenciapersonaswa', 'public.ubicacion_equivalenciaestudianteswa',
    'public.ubicacion_asociacionprogramaswa', 'public.ubicacion_ubicacion',
)
DOCUMENTO_NUMERICO = re.compile(r'[0-9]+|[0-9]{1,3}(?P<sep>[., ])[0-9]{3}(?:(?P=sep)[0-9]{3})*')
MAX_BIGINT = (1 << 63) - 1
MAX_ID_SWA = (1 << 31) - 1


@dataclass(frozen=True)
class CasoReasignacion:
    id_erp: int
    id_swa: int | None
    origen: str
    estado: str
    prueba: str


class _Evento(NamedTuple):
    id: int
    modulo: str
    tipo: str
    origen: str | None
    fecha: datetime


class _PersonaErp(NamedTuple):
    id: int
    documento: int
    tipo: int
    primer_nombre: str
    segundo_nombre: str
    primer_apellido: str
    segundo_apellido: str
    fecha: datetime


class _PersonaSwa(NamedTuple):
    id: int
    documento: str
    tipo: int
    nombre: str
    apellido: str


class _Movimiento(NamedTuple):
    clave: str
    id: int
    estado: str | None
    id_registrado: str | None
    origen: str | None
    prueba: str | None


class _Traza(NamedTuple):
    tabla: str
    id_swa: int | None
    id_erp: int | None
    codigo: str | None
    vinculo: int | None
    id_persona_vinculo: int | None


def _entero_positivo(valor, limite=MAX_BIGINT):
    return type(valor) is int and 0 < valor <= limite


def _documento(valor):
    texto = ' '.join(str(valor or '').split())
    if not DOCUMENTO_NUMERICO.fullmatch(texto):
        return None
    digitos = re.sub(r'[., ]', '', texto).lstrip('0')
    if not digitos or len(digitos) > 19:
        return None
    numero = int(digitos)
    return numero if _entero_positivo(numero) else None


def _en_ventana(fecha, origen):
    intervalo = IMPORTACIONES.get(origen)
    return intervalo and fecha is not None and fecha.tzinfo is not None and intervalo[0] <= fecha < intervalo[1]


def _fecha_evidencia(fecha):
    return (fecha.astimezone(timezone.utc) if fecha.tzinfo is not None else fecha).isoformat()


def _agrupar(filas, clave):
    grupos = defaultdict(list)
    for fila in filas:
        grupos[clave(fila)].append(fila)
    return grupos


def _validar_tablas(cur):
    cur.execute("""
        SELECT n.nspname || '.' || c.relname
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r' AND n.nspname || '.' || c.relname = ANY(%s::text[])
          AND NOT pg_catalog.row_security_active(c.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent = c.oid)
    """, (list(TABLAS_LOCALES),))
    faltantes = set(TABLAS_LOCALES) - {nombre for nombre, in cur.fetchall()}
    if faltantes:
        raise ValueError('Faltan tablas locales con lectura completa para conciliar IDs: '
                         + ', '.join(sorted(faltantes)))


def _cargar_auditoria(cur):
    cur.execute("""
        WITH ventanas (origen, inicio, fin) AS (
            VALUES (%s::text, %s::timestamptz, %s::timestamptz),
                   (%s::text, %s::timestamptz, %s::timestamptz),
                   (%s::text, %s::timestamptz, %s::timestamptz)
        ), cohorte AS (
            SELECT DISTINCT a.entidad_id
            FROM public.auditoria_log_sistema a
            JOIN ventanas v ON a.metadata->>'origen' = v.origen
                            AND a.fecha_creacion >= v.inicio AND a.fecha_creacion < v.fin
            WHERE a.modulo = 'elecciones' AND a.entidad = 'PERSONA'
              AND a.tipo_evento = 'CREACION' AND a.resultado = 'EXITO'
        )
        SELECT a.entidad_id, a.log_auditoria_id, a.modulo, a.tipo_evento,
               a.metadata->>'origen', a.fecha_creacion
        FROM public.auditoria_log_sistema a
        JOIN cohorte c ON c.entidad_id = a.entidad_id
        WHERE a.entidad = 'PERSONA' AND a.resultado = 'EXITO'
          AND (a.tipo_evento = 'CREACION' OR (
            a.tipo_evento = 'ACTUALIZACION' AND a.modulo = 'elecciones'
            AND EXISTS (SELECT 1 FROM ventanas v WHERE a.metadata->>'origen' = v.origen
                        OR (a.fecha_creacion >= v.inicio AND a.fecha_creacion < v.fin))
          ))
        ORDER BY a.entidad_id, a.fecha_creacion, a.log_auditoria_id
    """, tuple(valor for origen, horas in IMPORTACIONES.items() for valor in (origen, *horas)))
    auditoria = defaultdict(list)
    for id_erp, *evento in cur.fetchall():
        auditoria[id_erp].append(_Evento(*evento))
    if not auditoria:
        raise ValueError('No se encontraron creaciones de persona verificadas en la auditoría de elecciones.')
    return auditoria


def _cargar_fuente(cur, documentos):
    cur.execute("""
        SELECT id, identificacion, tipo_identificacion, nombre, apellido
        FROM ONLY swa_raw.persona ORDER BY id, identificacion, tipo_identificacion, nombre, apellido
    """)
    filas = [_PersonaSwa(*fila) for fila in cur.fetchall()]
    candidatos = defaultdict(list)
    tiene_identidad = False
    for fila in filas:
        documento = _documento(fila.documento)
        if documento in documentos:
            candidatos[documento].append(fila)
        if documento is not None and _entero_positivo(fila.id, MAX_ID_SWA) and fila.tipo in TIPOS_DOCUMENTO:
            tiene_identidad = True
    if not tiene_identidad:
        raise ValueError('La copia local swa_raw.persona carece de identidades SWA verificables.')
    return candidatos, Counter(fila.id for fila in filas)


def _cargar_trazas(cur, ids_swa, ids_erp):
    cur.execute("""
        WITH trazas AS (
            SELECT 'persona' AS tabla, persona_swa_id, persona_erp_id,
                   NULL::text AS codigo, NULL::bigint AS vinculo
            FROM public.ubicacion_equivalenciapersonaswa
            UNION ALL
            SELECT 'estudiante', persona_swa_id, persona_erp_id, codigo_estudiante, vinculo_erp_id
            FROM public.ubicacion_equivalenciaestudianteswa
            UNION ALL
            SELECT 'programa', persona_swa_id, persona_erp_id, codigo_estudiante, vinculo_erp_id
            FROM public.ubicacion_asociacionprogramaswa
            UNION ALL
            SELECT 'ubicacion', NULL, persona_id, codigo_estudiante, NULL
            FROM public.ubicacion_ubicacion
        ), codigos AS (
            SELECT DISTINCT codigo FROM trazas
            WHERE persona_swa_id = ANY(%s::bigint[]) OR persona_erp_id = ANY(%s::bigint[])
        )
        SELECT t.tabla, t.persona_swa_id, t.persona_erp_id, t.codigo, t.vinculo, v.persona_id
        FROM trazas t
        LEFT JOIN public.erp_vinculopersona v ON v.id = t.vinculo
        WHERE t.persona_swa_id = ANY(%s::bigint[]) OR t.persona_erp_id = ANY(%s::bigint[])
           OR t.codigo IN (SELECT codigo FROM codigos)
    """, (ids_swa, ids_erp, ids_swa, ids_erp))
    trazas = [_Traza(*fila) for fila in cur.fetchall()]
    return (_agrupar(trazas, lambda t: t.id_swa), _agrupar(trazas, lambda t: t.id_erp),
            _agrupar(trazas, lambda t: t.codigo))


def _trazas_persona(indices, id_swa, ids_erp):
    por_swa, por_erp, por_codigo = indices
    trazas = set(por_swa[id_swa])
    for id_erp in ids_erp:
        trazas.update(por_erp[id_erp])
    codigos = {t.codigo for t in trazas if t.codigo}
    for codigo in codigos:
        trazas.update(por_codigo[codigo])
    return sorted(trazas, key=lambda t: tuple('' if campo is None else str(campo) for campo in t))


def _traza_coincide(traza, id_swa, persona_actual):
    if traza.tabla == 'ubicacion':
        return traza.id_erp == persona_actual
    if traza.id_swa != id_swa:
        return False
    if traza.vinculo is not None and traza.id_persona_vinculo != persona_actual:
        return False
    if traza.tabla == 'estudiante' and traza.vinculo is None:
        return False
    return traza.id_erp == persona_actual or (traza.tabla == 'programa' and traza.id_erp is None)


def _planear(cur):
    _validar_tablas(cur)
    auditoria = _cargar_auditoria(cur)
    cur.execute("""
        SELECT m.clave, m.erp_id, l.estado, m.fila_importada->>'id_swa',
               m.fila_importada->>'origen', m.fila_importada->>'prueba'
        FROM migra.mapa m LEFT JOIN migra.lote l ON l.id = m.lote_id
        WHERE m.entidad = %s ORDER BY m.clave, m.erp_id
    """, (ENTIDAD_REASIGNACION,))
    movimientos = [_Movimiento(*fila) for fila in cur.fetchall()]
    por_clave = _agrupar(movimientos, lambda m: m.clave)
    por_destino = _agrupar(movimientos, lambda m: m.id)
    claves_cohorte = {str(id_erp) for id_erp in auditoria}
    ids_erp = sorted(set(auditoria) | {m.id for m in movimientos if m.clave in claves_cohorte})

    cur.execute("""
        SELECT id, nro_documento, tipo_documento_id, primer_nombre, segundo_nombre,
               primer_apellido, segundo_apellido, fecha_creacion
        FROM public.erp_persona WHERE id = ANY(%s::bigint[])
    """, (ids_erp,))
    personas = {fila[0]: _PersonaErp(*fila) for fila in cur.fetchall()}
    documentos = {_documento(p.documento) for p in personas.values()} - {None}
    fuente, cuentas_swa = _cargar_fuente(cur, documentos)
    ids_swa = sorted({p.id for grupo in fuente.values() for p in grupo if _entero_positivo(p.id, MAX_ID_SWA)})
    ids_erp = sorted(set(ids_erp) | set(ids_swa))

    cur.execute('SELECT id, codigo FROM public.erp_tipodocumentoidentidad ORDER BY id')
    tipos = dict(cur.fetchall())
    cuentas_tipos = Counter(tipos.values())
    cur.execute("""
        SELECT id, nro_documento FROM public.erp_persona
        WHERE id = ANY(%s::bigint[]) OR nro_documento = ANY(%s::bigint[]) ORDER BY id
    """, (ids_erp, sorted(documentos)))
    ocupados = dict(cur.fetchall())
    por_documento = defaultdict(list)
    for id_erp, documento in ocupados.items():
        por_documento[documento].append(id_erp)

    cur.execute("SELECT clave, erp_id FROM migra.mapa WHERE entidad = 'persona' ORDER BY clave, erp_id")
    mapa_persona = cur.fetchall()
    mapas_por_id = _agrupar(mapa_persona, lambda m: m[1])
    mapas_por_doc = _agrupar(mapa_persona, lambda m: _documento(m[0]))
    trazas = _cargar_trazas(cur, ids_swa, ids_erp)

    def evaluar(id_erp, eventos):
        creaciones = [e for e in eventos if e.tipo == 'CREACION']
        origenes = {e.origen for e in creaciones if e.modulo == 'elecciones' and _en_ventana(e.fecha, e.origen)}
        origen = next(iter(origenes)) if len(origenes) == 1 else 'INDETERMINADO'
        evidencia = [eventos]

        def caso(estado, id_swa=None):
            contenido = json.dumps([id_erp, id_swa, origen, estado, evidencia], separators=(',', ':'),
                                   default=_fecha_evidencia)
            return CasoReasignacion(id_erp, id_swa, origen, estado, sha256(contenido.encode()).hexdigest())

        if len(creaciones) != 1 or len(origenes) != 1 or not _entero_positivo(id_erp):
            return caso('AUDITORIA_AMBIGUA')
        if any(e.tipo == 'ACTUALIZACION' for e in eventos):
            return caso('AUDITORIA_ACTUALIZACION')

        registros = por_clave[str(id_erp)]
        evidencia.append(registros)
        movimiento = registros[0] if len(registros) == 1 else None
        if registros and (movimiento is None or movimiento.estado != 'COMPLETADO'
                          or not _entero_positivo(movimiento.id) or movimiento.id == id_erp
                          or movimiento.id_registrado != str(movimiento.id) or movimiento.origen != origen
                          or not re.fullmatch('[0-9a-f]{64}', movimiento.prueba or '')
                          or id_erp in personas):
            return caso('BITACORA_INCONSISTENTE')

        actual = movimiento.id if movimiento else id_erp
        persona = personas.get(actual)
        evidencia.append(persona)
        if persona is None:
            return caso('PERSONA_ERP_AUSENTE')
        if not _en_ventana(persona.fecha, origen):
            return caso('FECHA_ERP_INCOMPATIBLE')
        documento = _documento(persona.documento)
        codigo = tipos.get(persona.tipo)
        if documento is None or codigo not in TIPOS_DOCUMENTO.values() or cuentas_tipos[codigo] != 1:
            return caso('DOCUMENTO_ERP_INCOMPATIBLE')

        coincidencias = fuente[documento]
        evidencia.append([codigo, coincidencias])
        if not coincidencias:
            return caso('SIN_COINCIDENCIA_SWA')
        if len(coincidencias) != 1 or cuentas_swa[coincidencias[0].id] != 1:
            return caso('DOCUMENTO_SWA_DUPLICADO')
        swa = coincidencias[0]
        if not _entero_positivo(swa.id, MAX_ID_SWA) or TIPOS_DOCUMENTO.get(swa.tipo) != codigo:
            return caso('TIPO_O_ID_SWA_INCOMPATIBLE')
        nombres_swa = (normalizar_nombre(swa.nombre), normalizar_nombre(swa.apellido))
        primer_nombre = normalizar_nombre(persona.primer_nombre)
        primer_apellido = normalizar_nombre(persona.primer_apellido)
        nombres_erp = (f'{primer_nombre} {normalizar_nombre(persona.segundo_nombre)}'.strip(),
                       f'{primer_apellido} {normalizar_nombre(persona.segundo_apellido)}'.strip())
        if not primer_nombre or not primer_apellido or nombres_swa != nombres_erp:
            return caso('NOMBRE_INCOMPATIBLE', swa.id)

        ids = sorted({id_erp, actual, swa.id})
        destinos_previos = [m for clave in ids for m in por_destino[clave]]
        claves_previas = por_clave[str(swa.id)]
        evidencia.append([por_documento[documento], swa.id in ocupados, ocupados.get(swa.id),
                          destinos_previos, claves_previas])
        if movimiento and (movimiento.id != swa.id or destinos_previos != [movimiento]):
            return caso('BITACORA_INCONSISTENTE', swa.id)
        if swa.id != actual and swa.id in ocupados:
            return caso('ID_SWA_OCUPADO', swa.id)
        if swa.id != actual and (swa.id in auditoria or claves_previas):
            return caso('ID_SWA_CON_HISTORIAL', swa.id)
        if por_documento[documento] != [actual]:
            return caso('DOCUMENTO_ERP_DUPLICADO', swa.id)
        if not movimiento and destinos_previos:
            return caso('BITACORA_INCONSISTENTE', swa.id)

        mapas = sorted({m for clave in ids for m in mapas_por_id[clave]} | set(mapas_por_doc[documento]))
        referencias = _trazas_persona(trazas, swa.id, ids)
        evidencia.append([mapas, referencias])
        if any(_documento(clave) != documento or destino != actual for clave, destino in mapas):
            return caso('MAPA_PERSONA_INCONSISTENTE', swa.id)
        if any(not _traza_coincide(t, swa.id, actual) for t in referencias):
            return caso('TRAZA_UBICACION_INCONSISTENTE', swa.id)
        if movimiento:
            if not any(t.tabla == 'persona' for t in referencias):
                return caso('BITACORA_INCONSISTENTE', swa.id)
            return caso('YA_CORREGIDA', swa.id)
        return caso('COINCIDE' if actual == swa.id else 'CAMBIAR', swa.id)

    return tuple(evaluar(id_erp, auditoria[id_erp]) for id_erp in sorted(auditoria))


def planear_ids_persona(cur) -> tuple[CasoReasignacion, ...]:
    """Planea con SELECT sobre un cursor del destino local previamente verificado."""
    try:
        return _planear(cur)
    except (UndefinedColumn, UndefinedTable) as error:
        raise ValueError('El esquema local no cumple el contrato de conciliación de IDs de persona.') from error
