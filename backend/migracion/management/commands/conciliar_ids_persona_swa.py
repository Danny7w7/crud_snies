"""Previsualiza y ensaya la restauración del ID original de persona SWA."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict

import psycopg
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections, transaction
from psycopg import sql

from config.destino_migracion import (bloqueo_migracion, cursor_migracion,
                                     parametros_destino, verificar_cursor_destino)
from migracion.conciliacion_ids import ENTIDAD_REASIGNACION, planear_ids_persona
from migracion.reasignar_ids_persona import (bloquear_reasignacion, inventariar_referencias,
                                             reasignar_ids_persona)


NOMBRE_INTERMEDIO = re.compile(r'erp_ids_persona_[a-z0-9_]{1,46}')
HUELLA = re.compile(r'[a-f0-9]{64}')


def _resumen_plan(cur, destino, casos, referencias):
    base = {campo: destino[campo] for campo in ('host', 'port', 'dbname')}
    ids = [caso.id_erp for caso in casos if caso.estado == 'CAMBIAR']
    afectadas = {}
    if ids:
        for ref in referencias:
            cur.execute(sql.SQL("""
                SELECT count(*) FROM {} WHERE {} = ANY(%s::bigint[])
            """).format(sql.Identifier(ref.esquema, ref.tabla), sql.Identifier(ref.columna)), (ids,))
            filas = cur.fetchone()[0]
            if filas:
                afectadas[f'{ref.esquema}.{ref.tabla}.{ref.columna}'] = filas
    huella = hashlib.sha256(json.dumps({
        'version': 1,
        'destino': base,
        'casos': [asdict(caso) for caso in casos],
        'referencias': [asdict(ref) for ref in referencias],
        'filas_referenciadas': afectadas,
    }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    por_origen = defaultdict(Counter)
    for caso in casos:
        por_origen[caso.origen][caso.estado] += 1
    conteos = Counter(caso.estado for caso in casos)
    return {
        'operacion': ENTIDAD_REASIGNACION,
        'destino': base,
        'huella': huella,
        'total_auditado': len(casos),
        'por_estado': dict(sorted(conteos.items())),
        'por_origen': {origen: dict(sorted(estados.items())) for origen, estados in sorted(por_origen.items())},
        'referencias_inventariadas': len(referencias),
        'filas_referenciadas': afectadas,
    }


def _registrar_lote(cur, casos, resumen):
    cur.execute("INSERT INTO migra.lote DEFAULT VALUES RETURNING id")
    lote = cur.fetchone()[0]
    cur.executemany("""
        INSERT INTO migra.mapa (entidad, clave, erp_id, lote_id, creado, fila_importada)
        VALUES (%s, %s, %s, %s, false, %s::jsonb)
    """, [
        (ENTIDAD_REASIGNACION, str(caso.id_erp), caso.id_swa, lote, json.dumps({
            'id_swa': caso.id_swa, 'origen': caso.origen, 'prueba': caso.prueba,
        }))
        for caso in casos
    ])
    cur.execute("""
        UPDATE migra.lote SET estado = 'COMPLETADO', fin = now(), resumen = %s::jsonb
        WHERE id = %s
    """, (json.dumps(resumen), lote))
    return lote


def _registrar_equivalencias(cur, ids_swa):
    cur.execute("""
        INSERT INTO public.ubicacion_equivalenciapersonaswa (persona_swa_id, persona_erp_id)
        SELECT id_swa, id_swa FROM unnest(%s::bigint[]) AS ids(id_swa)
        ON CONFLICT (persona_swa_id) DO NOTHING
    """, (ids_swa,))
    cur.execute("""
        SELECT count(*) FROM public.ubicacion_equivalenciapersonaswa
        WHERE persona_swa_id = ANY(%s::bigint[]) AND persona_erp_id = persona_swa_id
    """, (ids_swa,))
    if cur.fetchone()[0] != len(ids_swa):
        raise ValueError('Hay una equivalencia SWA incompatible con el ID corregido.')


class Command(BaseCommand):
    help = 'Conciliar IDs de persona del lote electoral con la instantánea local de SWA.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Aplicar el plan verificado en una copia intermedia.')
        parser.add_argument('--base-intermedia', help='Nombre exacto de la copia erp_ids_persona_* instalada en default.')
        parser.add_argument('--huella', help='Huella de la previsualización ejecutada sobre esa misma copia.')

    def handle(self, *args, **opciones):
        if not getattr(settings, 'MIGRADOR_SWA_LOCAL', False):
            raise CommandError('Activa MIGRADOR_SWA_LOCAL=1 y prepara swa_raw.persona en la copia local.')
        try:
            destino = parametros_destino()
            if opciones['aplicar']:
                resultado = self._aplicar(destino, opciones['base_intermedia'], opciones['huella'])
            else:
                if opciones['base_intermedia'] or opciones['huella']:
                    raise CommandError('--base-intermedia y --huella se usan al aplicar.')
                resultado = self._previsualizar(destino)
        except (ValueError, ImproperlyConfigured) as exc:
            raise CommandError(str(exc)) from None
        except (psycopg.Error, DatabaseError):
            raise CommandError('La conciliación falló en la copia local. Los cambios de la operación se revirtieron.') from None
        self.stdout.write(json.dumps(resultado, indent=2, ensure_ascii=False))

    def _previsualizar(self, destino):
        with transaction.atomic(using='default'), cursor_migracion(connections['default']) as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            verificar_cursor_destino(cur, destino)
            casos = planear_ids_persona(cur)
            referencias = inventariar_referencias(cur)
            return {**_resumen_plan(cur, destino, casos, referencias), 'modo': 'previsualizacion'}

    def _aplicar(self, destino, base, huella):
        if base != destino['dbname'] or not NOMBRE_INTERMEDIO.fullmatch(base or ''):
            raise CommandError('Selecciona la copia erp_ids_persona_* de default en --base-intermedia.')
        huella = (huella or '').lower()
        if not HUELLA.fullmatch(huella):
            raise CommandError('Ingresa una huella válida obtenida en la previsualización de esa copia.')
        with bloqueo_migracion() as conexion, cursor_migracion(conexion) as cur:
            referencias = inventariar_referencias(cur)
            bloquear_reasignacion(cur, referencias)
            casos = planear_ids_persona(cur)
            resultado = {**_resumen_plan(cur, destino, casos, referencias), 'modo': 'aplicacion'}
            if resultado['huella'] != huella:
                raise CommandError('La base o el plan cambiaron. Genera y revisa una previsualización nueva.')
            aplicar = tuple(caso for caso in casos if caso.estado == 'CAMBIAR')
            resultado['personas_corregidas'] = len(aplicar)
            if aplicar:
                cambios = {caso.id_erp: caso.id_swa for caso in aplicar}
                resultado['referencias_actualizadas'] = reasignar_ids_persona(cur, cambios, referencias)
                _registrar_equivalencias(cur, list(cambios.values()))
                resultado['lote'] = _registrar_lote(cur, aplicar, resultado)
            return resultado
