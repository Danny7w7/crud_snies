"""Traspaso de notas: snapshot SWA de lectura y escrituras explicitas en default."""

from collections import Counter, defaultdict
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from itertools import chain

from django.db import DatabaseError, connections, transaction

from config.destino_migracion import bloqueo_migracion, verificar_destino
from .catalogos_academicos import (CatalogosAcademicos, VERSIONES_APROBADAS,
    actualizar_propio, periodo_erp, proyeccion)
from .codigos_persona import comprobar_destino, periodo_ingreso_valido, tipos_documento_destino, PROGRAMAS_INGLES
from .esquema_destino import validar_esquema_destino
from .fuente_notas_swa import LectorNotasSwa
from .modelos_academicos import (MODELOS_ACADEMICOS, AsignaturaErp, MatriculaErp,
    InscripcionHistoricaErp, PeriodoErp)
from .models import ProgramaErp, VinculoPersona
from .programas_swa import (MOTIVO_PERIODO_SIN_CIERRE, destino_periodo, guardar_asociacion,
                            resolver_asociacion)
from ubicacion.models import (AsociacionProgramaSwa, AvanceAsignaturaSwa, CalificacionSwa,
    ConfiguracionNotaSwa, EquivalenciaMatriculaSwa, EquivalenciaPensumSwa,
    EquivalenciaPersonaSwa, EquivalenciaEstudianteSwa)


AUXILIARES_NOTAS = (EquivalenciaPersonaSwa, EquivalenciaEstudianteSwa, AsociacionProgramaSwa,
    AvanceAsignaturaSwa, CalificacionSwa, EquivalenciaPensumSwa,
    EquivalenciaMatriculaSwa, ConfiguracionNotaSwa)
CENTESIMA = Decimal('0.01')


def original(valor):
    return None if valor is None else str(valor)


def datos_json(datos):
    return {k: original(v) if isinstance(v, (float, Decimal)) else v for k, v in datos.items()}


def nota_decimal(valor):
    if valor is None:
        return None
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise ValueError('Nota fuente no numerica.') from None
    if not numero.is_finite() or not Decimal('0') <= numero <= Decimal('5'):
        raise ValueError('Nota fuente fuera de la escala 0 a 5.')
    return numero.quantize(CENTESIMA, rounding=ROUND_HALF_UP)


def definitiva_historica(registro, calificaciones_confirmadas=None, esquema_completo=False):
    """Publica la nota final registrada, con respaldo para distinguir el cero de SWA."""
    if registro.get('estado_definitiva') not in ('A', 'R'):
        return None
    definitiva = nota_decimal(registro.get('definitiva'))
    if definitiva == 0 and Decimal(str(registro['definitiva'])) == 0 and (
        not esquema_completo or not calificaciones_confirmadas or any(calificaciones_confirmadas.values())
    ):
        return None
    return definitiva


def clasificar_notas(registro, notas, esquemas, historica=False):
    """La definitiva parcial se conserva en origen; solo una completa se publica."""
    esperados = set(esquemas.get(registro.get('grupo_nota'), []))
    if esperados not in ({0}, {1, 2, 3}) and (not historica or notas):
        raise ValueError('Esquema de notas SWA sin correspondencia.')
    por_tipo = {n['tipo_nota']: n for n in notas}
    if len(por_tipo) != len(notas):
        raise ValueError('Tipos de nota fuente duplicados.')
    if any(n.get('estado') not in (0, 1) for n in notas):
        raise ValueError('Estado de calificacion sin interpretacion aprobada.')
    if set(por_tipo) - esperados:
        raise ValueError('Calificacion ajena al esquema del grupo SWA.')
    confirmadas = {tipo: nota_decimal(n['nota']) for tipo, n in por_tipo.items() if n['estado'] == 1}
    if any(v is None for v in confirmadas.values()):
        raise ValueError('Nota registrada sin valor.')
    completa = esperados <= confirmadas.keys()
    if historica:
        definitiva = definitiva_historica(registro, confirmadas, bool(esperados) and completa)
        completa = definitiva is not None
    else:
        definitiva = nota_decimal(registro.get('definitiva')) if completa else None
        if completa and definitiva is None:
            raise ValueError('Esquema completo sin definitiva fuente.')
    return {'confirmadas': confirmadas, 'definitiva': definitiva,
            'completa': completa, 'directa': esperados == {0}}


@contextmanager
def sesion_destino(aplicar):
    db = connections['default']
    if not aplicar:
        with transaction.atomic(using='default'):
            if db.vendor == 'postgresql':
                with db.cursor() as cur:
                    cur.execute('SET TRANSACTION READ ONLY')
            yield
        return
    with bloqueo_migracion():
        yield


class TraspasoNotas:
    def __init__(self, fuente, tablas):
        self.fuente = fuente
        self.tablas = tablas
        self.estudiantes = fuente['estudiantes']
        self.lista_estudiantes = list(self.estudiantes.values())
        self.materias = {(m['programa'], m['pensum'], m['asignatura']): m for m in fuente['materias']}
        self.historicos = {}
        for h in fuente['historicos']:
            clave = h['periodo'], h['programa'], h['estudiante']
            anterior = self.historicos.setdefault(clave, h)
            if anterior != h:
                self.historicos[clave] = {**h, 'estado': None}
        self.sedes = {p['codigo']: p['sede'] for p in fuente['programas']}
        self.matriculas_m = {k for k, h in self.historicos.items() if h['estado'] == 'M'}
        self.esquemas = defaultdict(list)
        for e in fuente['esquemas']:
            self.esquemas[e['grupo_nota']].append(e['tipo_nota'])
        self.tipos = {t['codigo']: t for t in fuente['tipos']}
        self.catalogos = CatalogosAcademicos(fuente,
            EquivalenciaPensumSwa._meta.db_table in tablas)
        self.aliases = (dict(EquivalenciaPersonaSwa.objects.using('default').values_list(
            'persona_swa_id', 'persona_erp_id')) if EquivalenciaPersonaSwa._meta.db_table in tablas else {})
        self.tipos_doc = tipos_documento_destino() if 'erp_tipodocumentoidentidad' in tablas else {}
        self.identidades = {}
        self.mapa = {p: self.aliases[p] for p, g in fuente['grupos'].items()
                     if p in self.aliases and self.aliases[p] == self.aliases.get(g.persona_id)}
        self.asignaturas = {a.codigo: a for a in AsignaturaErp.objects.using('default').all()}
        self.pensums_destino = {}
        self.pendientes_catalogo = {}
        self.contextos = {}

    def preparar_catalogos(self, aplicar, programa=None):
        necesarias = {(p['programa'], p['pensum']) for p in self.fuente.get('primeros_periodos', [])
                      if destino_periodo(p['periodo']) == 'historico'}
        if programa is not None:
            necesarias = {c for c in necesarias if c[0] == programa}
        orden = sorted(necesarias, key=lambda c: (c[1] != VERSIONES_APROBADAS.get(c[0]), c))
        resumen = Counter()
        for clave in orden:
            try:
                if aplicar:
                    destino = self.catalogos.aplicar(clave)
                else:
                    destino, _ = self.catalogos.comprobar(clave)
                    if destino is None and periodo_ingreso_valido(self.catalogos.primeros.get(clave)) is None:
                        raise ValueError('No hay periodo de uso valido para crear el pensum.')
                    if destino:
                        self.catalogos.reservados[destino.pk] = clave
                self.pensums_destino[clave] = destino
                resumen['preparados'] += 1
            except (ValueError, DatabaseError) as exc:
                motivo = str(exc) if isinstance(exc, ValueError) else 'Conflicto de integridad al actualizar el pensum.'
                self.pendientes_catalogo[clave] = motivo
                resumen['pendientes'] += 1
        # La malla puede haber incorporado asignaturas que antes no existian.
        self.asignaturas = {a.codigo: a for a in AsignaturaErp.objects.using('default').all()}
        return dict(resumen)

    def contexto(self, r):
        clave = (r['periodo'], r['programa'], r['estudiante'])
        if clave in self.contextos:
            return self.contextos[clave]
        est = self.estudiantes.get(r['estudiante'])
        if est is None or est.programa != r['programa']:
            resultado = (est, None, None, 'Estudiante ausente o programa de origen contradictorio.')
        elif not str(est.codigo or '').strip() or len(est.codigo) > 20:
            resultado = (est, None, None, 'Codigo de estudiante incompatible con Matricula ERP.')
        elif clave not in self.historicos:
            resultado = (est, None, None, 'Falta el historico de matricula de origen.')
        else:
            grupo = self.fuente['grupos'].get(est.persona_id)
            persona_id = self.mapa.get(est.persona_id)
            motivo = ''
            if grupo is None or persona_id is None:
                motivo = 'Persona pendiente de migracion o identidad incompatible.'
            elif grupo.persona_id not in self.identidades:
                try:
                    if not all(m._meta.db_table in self.tablas for m in
                               (EquivalenciaPersonaSwa, EquivalenciaEstudianteSwa, AsociacionProgramaSwa)):
                        raise ValueError('Faltan tablas de equivalencias de personas.')
                    persona = comprobar_destino(grupo, self.tipos_doc)
                    if persona is None or persona.pk != persona_id:
                        raise ValueError('La persona destino no existe.')
                    self.identidades[grupo.persona_id] = ''
                except ValueError as exc:
                    self.identidades[grupo.persona_id] = str(exc)
            if not motivo:
                motivo = self.identidades.get(grupo.persona_id, '')
            vinculo = None
            if not motivo:
                vinculo, motivo = resolver_asociacion(est, r['periodo'], persona_id,
                    self.lista_estudiantes, self.mapa, self.sedes, self.matriculas_m)
            resultado = (est, persona_id, vinculo, motivo)
        self.contextos[clave] = resultado
        return resultado

    def motivo(self, r, est, motivo):
        if destino_periodo(r['periodo']) != 'historico':
            return MOTIVO_PERIODO_SIN_CIERRE
        if motivo:
            return motivo
        h = self.historicos[(r['periodo'], r['programa'], est.codigo)]
        if h['estado'] != 'M':
            return 'Matrícula real (histórico M) sin confirmación para este intento.'
        if r['estado'] == 'C':
            return 'Registro C pendiente de interpretacion.'
        if r['estado'] != 'R':
            from .motivos_notas import MOTIVO_ESTADO_REGISTRO
            return MOTIVO_ESTADO_REGISTRO
        clave_pensum = (r['programa'], getattr(est, 'pensum', None))
        if clave_pensum in self.pendientes_catalogo:
            return self.pendientes_catalogo[clave_pensum]
        materia = self.materias.get((*clave_pensum, r['asignatura']))
        if materia is None:
            return 'Materia historica fuera del pensum declarado; revisar la version.'
        if r['programa'] in PROGRAMAS_INGLES and (r.get('nivel') not in range(1, 7)
                or r.get('nivel') != materia['nivel']):
            return 'Modulo de ingles incompatible con la malla fuente.'
        if clave_pensum not in self.pensums_destino:
            return 'Pensum pendiente de preparacion.'
        if len(str(r['grupo'])) > 20:
            return 'Codigo de grupo demasiado largo para ERP.'
        if h['nivel'] is None or not 0 <= h['nivel'] <= 32767:
            return 'Nivel de matricula incompatible con ERP.'
        return ''

    def snapshot(self, r, notas, est, persona_id, vinculo, motivo):
        # Aun los registros sin persona o sin resultado publicable conservan su origen.
        if est is None:
            raise ValueError('No se puede trazar un registro sin estudiante de origen.')
        asociacion = AsociacionProgramaSwa.objects.using('default').filter(
            codigo_estudiante=est.codigo, periodo_swa=r['periodo']).first()
        # Un conflicto nuevo no borra la referencia ya resuelta: queda en el intento.
        if asociacion is None or not motivo:
            asociacion = guardar_asociacion(est, r['periodo'], persona_id, vinculo, motivo)
        campos = dict(periodo_swa=r['periodo'], programa_swa_id=r['programa'],
            asignatura_swa=str(r['asignatura']), grupo_swa=str(r['grupo']), codigo_estudiante=r['estudiante'])
        avance, _ = AvanceAsignaturaSwa.objects.using('default').get_or_create(**campos,
            defaults={'asociacion': asociacion})
        avance = AvanceAsignaturaSwa.objects.using('default').select_for_update().get(pk=avance.pk)
        if avance.asociacion_id != asociacion.pk:
            raise ValueError('El intento ya corresponde a otra asociacion destino.')
        avance.registro_origen = datos_json(r)
        repeticiones = Counter(nota['tipo_nota'] for nota in notas)
        duplicadas = [datos_json(nota) for nota in notas if repeticiones[nota['tipo_nota']] > 1]
        if duplicadas:
            avance.registro_origen['calificaciones_duplicadas'] = duplicadas
        avance.definitiva_origen = original(r.get('definitiva'))
        avance.estado_origen = r.get('estado') or ''
        avance.estado_definitiva_origen = r.get('estado_definitiva') or ''
        avance.destino_notas = destino_periodo(r['periodo'])
        avance.modulo = r.get('nivel') if r['programa'] in PROGRAMAS_INGLES and r.get('nivel') in range(1, 7) else None
        avance.motivo_pendiente = motivo
        avance.save(using='default')
        existentes = {n.tipo_nota_swa: n for n in CalificacionSwa.objects.using('default').filter(avance=avance)}
        tipos = {n['tipo_nota'] for n in notas}
        retiradas = set(existentes) - tipos
        if retiradas:
            avance.motivo_pendiente = motivo or 'SWA retiro calificaciones trazadas; se requiere revision.'
            avance.save(using='default', update_fields=['motivo_pendiente'])
        for tipo in retiradas:
            obj = existentes[tipo]
            obj.datos_origen = {**obj.datos_origen, 'retirada_en_origen': True}
            obj.save(using='default', update_fields=['datos_origen'])
        for n in notas:
            obj = existentes.get(n['tipo_nota']) or CalificacionSwa(avance=avance, tipo_nota_swa=n['tipo_nota'])
            obj.estado_origen = n.get('estado')
            obj.nota_origen = original(n.get('nota'))
            obj.porcentaje_origen = original(self.tipos.get(n['tipo_nota'], {}).get('porcentaje'))
            obj.datos_origen = datos_json(n)
            obj.save(using='default')
            existentes[n['tipo_nota']] = obj
        return avance

    def analizar_intento(self, r, notas, resultado, est, persona_id, vinculo):
        filtros = dict(periodo_swa=r['periodo'], programa_swa_id=r['programa'],
            asignatura_swa=str(r['asignatura']), grupo_swa=str(r['grupo']), codigo_estudiante=r['estudiante'])
        avance = (AvanceAsignaturaSwa.objects.using('default').filter(**filtros).first()
                  if AvanceAsignaturaSwa._meta.db_table in self.tablas else None)
        avance = avance or AvanceAsignaturaSwa(**filtros)
        existentes = (list(CalificacionSwa.objects.using('default').filter(avance_id=avance.pk))
                      if avance.pk and CalificacionSwa._meta.db_table in self.tablas else [])
        actuales = {nota['tipo_nota'] for nota in notas}
        if {n.tipo_nota_swa for n in existentes} - actuales:
            raise ValueError('SWA retiro calificaciones trazadas; se requiere revision.')
        self.proyectar(r, resultado, avance, est, persona_id, vinculo, aplicar=False)

    def consulta(self, modelo, aplicar):
        qs = modelo.objects.using('default')
        return qs.select_for_update() if aplicar else qs

    def matricula(self, r, est, persona_id, pensum, aplicar=True):
        filtros = {'periodo_swa': r['periodo'], 'programa_swa_id': r['programa'], 'codigo_estudiante': est.codigo}
        eq = (self.consulta(EquivalenciaMatriculaSwa, aplicar).filter(**filtros).first()
              if EquivalenciaMatriculaSwa._meta.db_table in self.tablas else None)
        h = self.historicos[(r['periodo'], r['programa'], est.codigo)]
        periodo = (periodo_erp(r['periodo']) if aplicar else PeriodoErp.objects.using('default')
                   .filter(nombre='-'.join(periodo_ingreso_valido(r['periodo']))).first())
        campos = {'persona_id': persona_id, 'programa_id': r['programa'], 'pensum_id': getattr(pensum, 'pk', None),
                  'periodo_id': getattr(periodo, 'pk', None), 'codigo': est.codigo,
                  'semestre_actual': h['nivel'],
                  'estado': False}
        if eq:
            destino = self.consulta(MatriculaErp, aplicar).filter(pk=eq.matricula_erp_id).first()
            if destino is None:
                raise ValueError('La matricula trazada ya no existe en ERP.')
            if any(getattr(destino, c) != campos[c] for c in ('persona_id', 'programa_id', 'pensum_id', 'periodo_id')):
                raise ValueError('La matricula de origen ya tiene otras referencias ERP.')
            campos['codigo'] = destino.codigo
            eq.ultimo_importado = actualizar_propio(destino, campos, eq.ultimo_importado, aplicar)
        else:
            candidatas = list(self.consulta(MatriculaErp, aplicar).filter(
                persona_id=persona_id, programa_id=r['programa'], periodo_id=campos['periodo_id']))
            if len(candidatas) > 1:
                raise ValueError('Varias matriculas ERP corresponden al origen.')
            destino = candidatas[0] if candidatas else None
            if destino and proyeccion(destino, campos) != {c: v for c, v in campos.items()}:
                raise ValueError('La matricula existente difiere del origen; se requiere revision.')
            destino = destino or (MatriculaErp.objects.using('default').create(**campos) if aplicar else MatriculaErp(**campos))
            eq = EquivalenciaMatriculaSwa(**filtros, matricula_erp=destino,
                                        ultimo_importado=proyeccion(destino, campos))
        if aplicar:
            eq.save(using='default')
        return destino

    def proyectar(self, r, resultado, avance, est, persona_id, vinculo, aplicar=True):
        if destino_periodo(r['periodo']) != 'historico':
            raise ValueError(MOTIVO_PERIODO_SIN_CIERRE)
        pensum = self.pensums_destino.get((r['programa'], est.pensum))
        if pensum is None and aplicar:
            raise ValueError('Pensum pendiente de preparacion.')
        if aplicar and est.programa in PROGRAMAS_INGLES and vinculo.programa_id != est.programa:
            vinculo = VinculoPersona.objects.using('default').select_for_update().get(pk=vinculo.pk)
            if vinculo.programa_secundario_id not in (None, est.programa):
                raise ValueError('El vinculo ya tiene otro programa secundario.')
            if vinculo.programa_secundario_id is None:
                vinculo.programa_secundario_id = est.programa
                vinculo.save(using='default', update_fields=['programa_secundario'])
        matricula = self.matricula(r, est, persona_id, pensum, aplicar)
        modelo = InscripcionHistoricaErp
        campo = 'inscripcion_historica_erp_id'
        if avance.inscripcion_actual_erp_id:
            raise ValueError('El corte temporal cambio para una inscripcion ya importada.')
        datos = {'persona_id': persona_id, 'programa_id': r['programa'],
                 'asignatura_id': getattr(self.asignaturas.get(str(r['asignatura'])), 'pk', None),
                 'matricula_id': matricula.pk, 'periodo_id': matricula.periodo_id,
                 'definitiva': resultado['definitiva']}
        pk = avance.inscripcion_historica_erp_id
        if pk:
            if AvanceAsignaturaSwa.objects.using('default').filter(**{campo: pk}).exclude(pk=avance.pk).exists():
                raise ValueError('La inscripcion ERP esta trazada por mas de un intento.')
            inscripcion = self.consulta(modelo, aplicar).filter(pk=pk).first()
            if inscripcion is None:
                raise ValueError('La inscripcion importada fue retirada del ERP.')
            baseline = avance.ultimo_importado.get('campos', avance.ultimo_importado)
            if avance.ultimo_importado.get('grupo_nota_swa', avance.registro_origen.get('grupo_nota')) != r.get('grupo_nota'):
                raise ValueError('El esquema de un intento importado cambio en SWA.')
            if baseline.get('definitiva') is not None and resultado['definitiva'] is None:
                raise ValueError('La definitiva importada volvio a pendiente en SWA; se requiere revision.')
            ultimo = actualizar_propio(inscripcion, datos, baseline, aplicar)
        else:
            # Una inscripcion historica no tiene grupo: no se adopta por coincidencia parcial.
            filtros = {c: v for c, v in datos.items() if c != 'definitiva'}
            candidatas = set(self.consulta(modelo, aplicar).filter(**filtros).values_list('id', flat=True))
            trazadas = (set(AvanceAsignaturaSwa.objects.using('default').filter(
                **{campo + '__in': candidatas}).values_list(campo, flat=True))
                if AvanceAsignaturaSwa._meta.db_table in self.tablas else set())
            if candidatas - trazadas:
                raise ValueError('Existe una inscripcion sin equivalencia del intento; revisar su origen.')
            inscripcion = modelo.objects.using('default').create(**datos) if aplicar else modelo(**datos)
            ultimo = proyeccion(inscripcion, datos)
        avance.inscripcion_historica_erp_id = inscripcion.pk
        avance.matricula_erp = matricula
        avance.ultimo_importado = {'campos': ultimo, 'grupo_nota_swa': r.get('grupo_nota')}
        avance.estado_migracion = 'migrado'
        avance.motivo_pendiente = ''
        if aplicar:
            avance.save(using='default')


def procesar_notas_swa(aplicar=False, programa=None, periodo=None, lector=None):
    if programa is not None and (not isinstance(programa, int) or programa <= 0):
        raise ValueError('Programa invalido.')
    if periodo is not None and periodo_ingreso_valido(periodo) is None:
        raise ValueError('Periodo invalido.')
    verificar_destino()
    db = connections['default']
    with db.cursor() as cur:
        tablas = set(db.introspection.table_names(cur))
    faltantes = [m._meta.db_table for m in AUXILIARES_NOTAS if m._meta.db_table not in tablas]
    validar_esquema_destino('notas', permitir_auxiliares_faltantes=not aplicar)
    nativas_faltantes = [m._meta.db_table for m in MODELOS_ACADEMICOS if m._meta.db_table not in tablas]
    if nativas_faltantes:
        raise ValueError('Faltan tablas del ERP desarrollado: ' + ', '.join(nativas_faltantes))
    lector = lector or LectorNotasSwa(programa, periodo)
    resumen = {'modo': 'aplicar' if aplicar else 'analisis', 'tablas_auxiliares_pendientes': faltantes,
               'registros': 0, 'actuales': 0, 'historicos': 0, 'migrados': 0, 'proyectables': 0, 'pendientes': 0,
               'cortes_confirmados': 0, 'notas_directas': 0, 'redondeos': 0, 'motivos': Counter(),
               'por_programa': Counter()}
    with sesion_destino(aplicar), lector.lectura():
        fuente = lector.catalogos()
        trabajo = TraspasoNotas(fuente, tablas)
        lotes = iter(lector.lotes(500))
        primero = next(lotes, [])  # Valida el SQL de notas antes de escribir catalogos.
        resumen['pensums'] = (trabajo.preparar_catalogos(aplicar, programa)
                              if periodo is None or destino_periodo(periodo) == 'historico' else {})
        for lote in chain([primero], lotes):
            with transaction.atomic(using='default'):
                for r, notas in lote:
                    resumen['registros'] += 1
                    resumen['por_programa'][r['programa']] += 1
                    destino = destino_periodo(r['periodo'])
                    resumen['historicos'] += int(destino == 'historico')
                    est, persona_id, vinculo, motivo = trabajo.contexto(r)
                    motivo = trabajo.motivo(r, est, motivo)
                    resultado = None
                    try:
                        resultado = clasificar_notas(r, notas, trabajo.esquemas,
                                                     historica=destino == 'historico')
                        resumen['cortes_confirmados'] += len(resultado['confirmadas']) if not resultado['directa'] else 0
                        resumen['notas_directas'] += int(resultado['directa'] and resultado['completa'])
                        if not motivo and not resultado['completa']:
                            motivo = 'Definitiva histórica ausente o sin confirmación en SWA.'
                    except ValueError as exc:
                        motivo = motivo or str(exc)
                    for valor in [r.get('definitiva')] + [n.get('nota') for n in notas]:
                        try:
                            if valor is not None:
                                resumen['redondeos'] += int(Decimal(str(valor)) != nota_decimal(valor))
                        except (ValueError, InvalidOperation):
                            pass  # Las pendientes tambien conservan el valor crudo.
                    avance = None
                    if aplicar:
                        try:
                            with transaction.atomic(using='default'):
                                avance = trabajo.snapshot(r, notas, est, persona_id, vinculo, motivo)
                                motivo = motivo or avance.motivo_pendiente
                            if not motivo:
                                try:
                                    with transaction.atomic(using='default'):
                                        trabajo.proyectar(r, resultado, avance, est, persona_id, vinculo)
                                except (ValueError, DatabaseError) as exc:
                                    motivo = str(exc) if isinstance(exc, ValueError) else 'Conflicto de integridad al cargar notas.'
                                    avance.refresh_from_db(using='default')
                            if motivo:
                                avance.motivo_pendiente = motivo
                                avance.estado_migracion = 'pendiente'
                                avance.save(using='default', update_fields=['motivo_pendiente', 'estado_migracion'])
                        except (ValueError, DatabaseError) as exc:
                            motivo = str(exc) if isinstance(exc, ValueError) else 'Conflicto de integridad en la trazabilidad.'
                    elif not motivo:
                        try:
                            trabajo.analizar_intento(r, notas, resultado, est, persona_id, vinculo)
                        except (ValueError, DatabaseError) as exc:
                            motivo = str(exc) if isinstance(exc, ValueError) else 'Conflicto de integridad al analizar notas.'
                    if motivo:
                        resumen['pendientes'] += 1
                        resumen['motivos'][motivo] += 1
                    else:
                        resumen['migrados' if aplicar else 'proyectables'] += 1
    resumen['motivos'] = dict(resumen['motivos'])
    resumen['por_programa'] = dict(resumen['por_programa'])
    return resumen
