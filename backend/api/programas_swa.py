"""Asociaciones por periodo y preparacion del avance; SWA solo se consulta."""

from collections import defaultdict
from datetime import date

from django.conf import settings
from django.db import connections, transaction

from ubicacion.models import (AsociacionProgramaSwa, AvanceAsignaturaSwa,
                              EquivalenciaEstudianteSwa)
from .codigos_persona import PROGRAMAS_COMPLEMENTARIOS, PROGRAMAS_INGLES, periodo_ingreso_valido
from .models import ProgramaErp, VinculoPersona


MOTIVO_PERIODO_SIN_CIERRE = 'Periodo inválido o sin cierre confirmado al corte de migración.'


def cargar_academico():
    with connections['produccion'].cursor() as cur:
        cur.execute('SELECT codigo, sede FROM programa')
        sedes = dict(cur.fetchall())
        cur.execute('SELECT periodo, programa, estudiante FROM historico WHERE estado = %s', ['M'])
        matriculas = set(cur.fetchall())
        cur.execute(
            'SELECT r.periodo, r.programa, r.asignatura, r.grupo, r.estudiante, '
            'r.estado, r.estado_definitiva, r.nivel, pa.nivel '
            'FROM registro r LEFT JOIN estudiante e ON e.codigo = r.estudiante '
            'LEFT JOIN pensum_asignatura pa '
            'ON pa.programa = r.programa AND pa.pensum = e.pensum '
            'AND pa.asignatura = r.asignatura')
        registros = [dict(zip(('periodo', 'programa', 'asignatura', 'grupo', 'estudiante',
                               'estado', 'estado_definitiva', 'modulo', 'modulo_pensum'), fila))
                     for fila in cur.fetchall()]
        for registro in registros:
            if (registro['modulo_pensum'] is not None
                    and registro['modulo'] != registro['modulo_pensum']):
                registro['modulo'] = None
    return sedes, matriculas, registros


def _periodo_corte():
    return getattr(settings, 'PERIODO_ACTUAL_MIGRACION_SWA', 20262)


def destino_periodo(periodo):
    partes = periodo_ingreso_valido(periodo)
    if partes is None:
        return 'pendiente'
    return 'historico' if int(''.join(partes)) < _periodo_corte() else 'pendiente'


def tiene_carrera(persona_id, estudiantes, mapa):
    return any(mapa.get(e.persona_id) == persona_id and e.programa
               and e.programa not in PROGRAMAS_COMPLEMENTARIOS for e in estudiantes)


def resolver_vinculo_ingles(est, periodo, persona_id, estudiantes, mapa, sedes, matriculas):
    """Consulta vinculos ya creados; nunca inventa una carrera por proximidad."""
    if not tiene_carrera(persona_id, estudiantes, mapa):
        return None, ''
    sede = sedes.get(est.programa)
    equivalencia_sedes = getattr(settings, 'SEDES_SWA_A_ERP', {1: 1, 3: 2})
    if sede not in equivalencia_sedes:
        return None, 'Sede SWA sin equivalencia ERP.'
    codigos = [e.codigo for e in estudiantes
               if mapa.get(e.persona_id) == persona_id
               and e.programa not in PROGRAMAS_COMPLEMENTARIOS
               and sedes.get(e.programa) == sede
               and (periodo, e.programa, e.codigo) in matriculas]
    candidatos = {eq.vinculo_erp_id: eq.vinculo_erp for eq in
                  EquivalenciaEstudianteSwa.objects.using('default')
                  .filter(codigo_estudiante__in=codigos, persona_erp_id=persona_id)
                  .select_related('vinculo_erp')}
    if len(candidatos) != 1:
        return None, ('Varias carreras candidatas en sede y periodo.' if candidatos
                      else 'Sin carrera con matricula en la misma sede y periodo.')
    vinculo = next(iter(candidatos.values()))
    sucursal = equivalencia_sedes[sede]
    destinos = ProgramaErp.objects.using('default').filter(
        pk__in=[vinculo.programa_id, est.programa], sucursal_id=sucursal).count()
    if destinos != 2:
        return None, 'Los programas ERP no corresponden a la sede de origen.'
    if vinculo.programa_secundario_id not in (None, est.programa):
        return None, 'El vinculo ya tiene otro programa secundario.'
    if EquivalenciaEstudianteSwa.objects.using('default').filter(
            codigo_estudiante=est.codigo).exclude(vinculo_erp_id=vinculo.pk).exists():
        return None, 'El codigo de ingles ya esta asociado a otro vinculo.'
    return vinculo, ''


def vinculo_principal(est, persona_id, tipo, alta_manual=False):
    periodo = periodo_ingreso_valido(est.periodo_ingreso)
    if periodo is None:
        raise ValueError('Periodo de ingreso invalido.')
    anio, semestre = periodo
    activo = alta_manual and int(''.join(periodo)) == _periodo_corte() and est.estado == 'A'
    vinculo, creado = VinculoPersona.objects.using('default').get_or_create(
        persona_id=persona_id, tipo_vinculo_id=tipo.pk, programa_id=est.programa,
        periodo=f'{anio}-{semestre}',
        defaults={'activo': activo, 'fecha_inicio': date(int(anio), 1, 1)})
    return vinculo, creado


def guardar_asociacion(est, periodo, persona_id, vinculo, motivo):
    asociacion, _ = AsociacionProgramaSwa.objects.using('default').get_or_create(
        codigo_estudiante=est.codigo, periodo_swa=periodo,
        defaults={'persona_swa_id': est.persona_id, 'programa_swa_id': est.programa})
    if (asociacion.persona_swa_id != est.persona_id or asociacion.programa_swa_id != est.programa
            or asociacion.persona_erp_id not in (None, persona_id)
            or asociacion.vinculo_erp_id is not None
            and asociacion.vinculo_erp_id != getattr(vinculo, 'pk', None)):
        raise ValueError('La asociacion de origen ya tiene otro destino.')
    asociacion.persona_erp_id = persona_id
    asociacion.vinculo_erp = vinculo
    asociacion.motivo_pendiente = motivo
    asociacion.save(using='default')
    return asociacion


def resolver_asociacion(est, periodo, persona_id, estudiantes, mapa, sedes, matriculas):
    """Resuelve la referencia sin escribir; tambien sirve al analisis de notas."""
    if persona_id is None:
        return None, 'Persona pendiente de migracion.'
    if destino_periodo(periodo) == 'pendiente':
        return None, MOTIVO_PERIODO_SIN_CIERRE
    if est.programa in PROGRAMAS_INGLES and tiene_carrera(persona_id, estudiantes, mapa):
        return resolver_vinculo_ingles(est, periodo, persona_id, estudiantes, mapa, sedes, matriculas)
    eq = EquivalenciaEstudianteSwa.objects.using('default').filter(
        codigo_estudiante=est.codigo, persona_erp_id=persona_id).select_related('vinculo_erp').first()
    vinculo = eq.vinculo_erp if eq else None
    if vinculo is None:
        return None, 'Vinculo principal pendiente de migracion.'
    if vinculo.programa_id != est.programa:
        return None, 'El vinculo principal corresponde a otro programa.'
    return vinculo, ''


def preparar_avance(estudiantes, mapa, tipo, contexto):
    sedes, matriculas, registros = contexto
    por_codigo = {e.codigo: e for e in estudiantes if e.programa}
    periodos = defaultdict(set)
    for e in por_codigo.values():
        if periodo_ingreso_valido(e.periodo_ingreso):
            periodos[e.codigo].add(e.periodo_ingreso)
    for periodo, programa, codigo in matriculas:
        if codigo in por_codigo and por_codigo[codigo].programa == programa:
            periodos[codigo].add(periodo)
    for r in registros:
        if r['estudiante'] in por_codigo:
            periodos[r['estudiante']].add(r['periodo'])
    resumen = {'asociaciones': 0, 'pendientes': [], 'avances': 0, 'actual': 0,
               'historico': 0, 'modulos_aprobados': 0}
    asociaciones = {}
    for codigo, valores in periodos.items():
        est = por_codigo[codigo]
        persona_id = mapa.get(est.persona_id)
        for periodo in sorted(valores, key=lambda valor: (valor is None, valor or 0)):
            if periodo is None:
                resumen['pendientes'].append({'codigo': codigo, 'periodo': None,
                                              'motivo': MOTIVO_PERIODO_SIN_CIERRE})
                continue
            try:
                with transaction.atomic(using='default'):
                    vinculo, motivo = resolver_asociacion(
                        est, periodo, persona_id, estudiantes, mapa, sedes, matriculas)
                    if vinculo is not None and est.programa in PROGRAMAS_INGLES and vinculo.programa_id != est.programa:
                        vinculo = VinculoPersona.objects.using('default').select_for_update().get(pk=vinculo.pk)
                        if vinculo.programa_secundario_id not in (None, est.programa):
                            raise ValueError('El vinculo ya tiene otro programa secundario.')
                        vinculo.programa_secundario_id = est.programa
                        vinculo.save(using='default', update_fields=['programa_secundario'])
                    asociacion = guardar_asociacion(est, periodo, persona_id, vinculo, motivo)
                    asociaciones[(codigo, periodo)] = asociacion
                resumen['asociaciones'] += 1
                if motivo:
                    resumen['pendientes'].append({'codigo': codigo, 'periodo': periodo, 'motivo': motivo})
            except ValueError as exc:
                resumen['pendientes'].append({'codigo': codigo, 'periodo': periodo, 'motivo': str(exc)})
    aprobados = set()
    for r in registros:
        asociacion = asociaciones.get((r['estudiante'], r['periodo']))
        if asociacion is None or asociacion.programa_swa_id != r['programa']:
            continue
        modulo = r['modulo'] if r['programa'] in PROGRAMAS_INGLES and r['modulo'] in range(1, 7) else None
        destino = destino_periodo(r['periodo'])
        AvanceAsignaturaSwa.objects.using('default').update_or_create(
            periodo_swa=r['periodo'], programa_swa_id=r['programa'],
            asignatura_swa=str(r['asignatura']), grupo_swa=str(r['grupo']),
            codigo_estudiante=r['estudiante'],
            defaults={'asociacion': asociacion, 'modulo': modulo,
                      'estado_origen': r['estado'] or '',
                      'estado_definitiva_origen': r['estado_definitiva'] or '',
                      'destino_notas': destino})
        resumen['avances'] += 1
        if destino in ('actual', 'historico'):
            resumen[destino] += 1
        if asociacion.persona_erp_id is not None and not asociacion.motivo_pendiente and modulo is not None and r['estado'] == 'R' and r['estado_definitiva'] == 'A':
            aprobados.add((asociacion.persona_erp_id, modulo))
    resumen['modulos_aprobados'] = len(aprobados)
    return resumen
