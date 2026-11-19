"""Actualizacion incremental de mallas sobre las tablas existentes del ERP."""

from collections import Counter, defaultdict
from decimal import Decimal

from django.db import transaction

from .codigos_persona import periodo_ingreso_valido
from .modelos_academicos import (AsignaturaErp, PensumErp, PeriodoErp,
                                PensumAsignaturaErp, RestriccionPensumErp)
from .models import ProgramaErp, SucursalErp
from ubicacion.models import EquivalenciaPensumSwa


VERSIONES_APROBADAS = {29: 2903, 32: 3202, 31: 3101}


def valor_json(valor):
    if isinstance(valor, Decimal):
        return format(valor, '.2f')
    return valor


def proyeccion(objeto, campos):
    return {c: valor_json(getattr(objeto, c)) for c in campos}


def actualizar_propio(objeto, nuevos, anterior, aplicar=True):
    """Compara bajo bloqueo; una edicion posterior del ERP no se sobreescribe."""
    actual = proyeccion(objeto, nuevos)
    propuesta = {c: valor_json(v) for c, v in nuevos.items()}
    if not anterior:
        if actual != propuesta:
            raise ValueError('Falta una referencia verificable para actualizar los valores del ERP.')
    elif proyeccion(objeto, anterior) != anterior:
        raise ValueError('El destino fue modificado despues de la ultima importacion.')
    cambios = {campo: valor for campo, valor in nuevos.items() if actual[campo] != propuesta[campo]}
    if aplicar and cambios:
        for campo, valor in cambios.items():
            setattr(objeto, campo, valor)
        objeto.save(using='default', update_fields=list(cambios))
    return propuesta


def periodo_erp(periodo):
    partes = periodo_ingreso_valido(periodo)
    if partes is None:
        raise ValueError('Periodo SWA invalido.')
    return PeriodoErp.objects.using('default').get_or_create(
        nombre='-'.join(partes), defaults={'activo': False})[0]


def datos_programa(codigo, nombre, activo, snies, sucursal_id):
    """Payload de alta, con la sede comprobada por el llamador."""
    if type(codigo) is not int or not 0 < codigo <= 9223372036854775807:
        raise ValueError('Código de programa SWA incompatible con el ERP.')
    if type(sucursal_id) is not int or not 0 < sucursal_id <= 9223372036854775807:
        raise ValueError('Sede SWA sin equivalencia ERP.')
    if activo not in (0, 1):
        raise ValueError('Estado de programa SWA sin correspondencia.')
    nombre = str(nombre or '').strip()
    snies = str(snies or '').strip()
    if not nombre or len(nombre) > 150 or len(snies) > 20:
        raise ValueError('Nombre o código SNIES incompatible con el ERP.')
    return {
        'id': codigo, 'nombre': nombre, 'codigo_snies': snies or None,
        'activo': bool(activo), 'sucursal_id': sucursal_id, 'numero_semanas': 0,
        'cupo_limitado': False, 'admision_proceso_fijo': False,
        'requiere_aprobacion_admisiones': True, 'requiere_aprobacion_director': True,
        'requiere_aprobacion_credito': True, 'dirigido_a': '', 'duracion_descripcion': '',
        'inicio_sujeto_a_cupo': False, 'modalidades': [], 'permite_credito': True,
    }


def obtener_o_crear_programa(codigo, nombre, activo, snies, sucursal_id):
    programas = ProgramaErp.objects.using('default')
    existente = programas.filter(pk=codigo).first()
    if existente is not None:
        if existente.sucursal_id != sucursal_id:
            raise ValueError('El programa ERP existente corresponde a otra sede.')
        return existente, False
    datos = datos_programa(codigo, nombre, activo, snies, sucursal_id)
    if not SucursalErp.objects.using('default').filter(pk=sucursal_id).exists():
        raise ValueError('La sucursal ERP asociada al programa no existe.')
    nombre = datos['nombre']
    if programas.filter(nombre=nombre).exists():
        sufijo = f' - {codigo}'
        datos['nombre'] = nombre[:150 - len(sufijo)] + sufijo
        if programas.filter(nombre=datos['nombre']).exists():
            raise ValueError('El nombre del programa coincide con otro programa ERP.')
    return programas.get_or_create(pk=datos.pop('id'), defaults=datos)


def datos_asignatura(fuente, creditos):
    tipo = fuente.get('tipo')
    if tipo not in ('T', 'P', 'M'):
        raise ValueError('Tipo de asignatura nueva sin correspondencia ERP.')
    nombre = str(fuente.get('nombre') or '').strip()
    if not nombre or len(nombre) > 200:
        raise ValueError('Nombre de asignatura incompatible con el ERP.')
    try:
        horas = Decimal(str(fuente.get('ihs') or 0))
        creditos = Decimal(str(creditos))
    except (ValueError, ArithmeticError):
        raise ValueError('Horas o creditos de asignatura incompatibles.') from None
    if (not horas.is_finite() or not creditos.is_finite()
            or horas != horas.to_integral_value() or creditos != creditos.to_integral_value()
            or not 0 <= horas <= 32767 or not 0 <= creditos <= 2147483647):
        raise ValueError('Horas o creditos de asignatura incompatibles.')
    horas = int(horas)
    return {'nombre': nombre, 'metodologia': 'Presencial', 'creditos': int(creditos),
            'hora_teorica': horas if tipo == 'T' else 0,
            'hora_practica': horas if tipo == 'P' else 0,
            'hora_mixta': horas if tipo == 'M' else 0,
            'tipo_asignatura': {'T': 'TEORICA', 'P': 'PRACTICA', 'M': 'MIXTA'}[tipo]}


def obtener_o_crear_asignatura(codigo, fuente, creditos):
    codigo = str(codigo or '').strip()
    if not codigo or len(codigo) > 20:
        raise ValueError('Código de asignatura SWA incompatible con el ERP.')
    asignaturas = AsignaturaErp.objects.using('default')
    existente = asignaturas.filter(codigo=codigo).first()
    if existente is not None:
        return existente, False
    return asignaturas.get_or_create(codigo=codigo, defaults=datos_asignatura(fuente, creditos))


def estado_malla(pensum):
    materias = list(PensumAsignaturaErp.objects.using('default').filter(pensum_id=pensum.pk)
                    .order_by('asignatura_id', 'id').values('id', 'asignatura_id', 'semestre'))
    restricciones = list(RestriccionPensumErp.objects.using('default').filter(
        pensum_asignatura_id__in=[materia['id'] for materia in materias])
        .order_by('pensum_asignatura_id', 'tipo', 'asignatura_id')
        .values('pensum_asignatura_id', 'tipo', 'asignatura_id'))
    return {'campos': proyeccion(pensum, ('programa_id', 'codigo', 'estado', 'periodo_id', 'intensidad_horaria')),
            'materias': materias, 'restricciones': restricciones}


class CatalogosAcademicos:
    def __init__(self, fuente, equivalencias_disponibles=True):
        self.fuente = fuente
        self.equivalencias_disponibles = equivalencias_disponibles
        self.pensums = {(p['programa'], p['codigo']): p for p in fuente['pensums']}
        self.materias = defaultdict(list)
        for m in fuente['materias']:
            self.materias[(m['programa'], m['pensum'])].append(m)
        self.asignaturas = {(a['programa'], a['codigo']): a for a in fuente.get('asignaturas', [])}
        self.restricciones = defaultdict(list)
        for tipo, nombre in (('PRERREQUISITO', 'prerrequisitos'), ('CORREQUISITO', 'correquisitos')):
            for r in fuente.get(nombre, []):
                self.restricciones[(r['programa'], r['pensum'])].append((r['asignatura'], tipo, r['requisito']))
        self.repetidos = Counter(p['codigo'] for p in fuente['pensums'])
        self.versiones = defaultdict(list)
        for programa, codigo in self.pensums:
            self.versiones[programa].append(codigo)
        self.primeros = {(p['programa'], p['pensum']): p['periodo'] for p in fuente.get('primeros_periodos', [])}
        self.reservados = {}  # Simula las correspondencias durante el analisis de lectura.

    def codigo(self, clave):
        programa, codigo = clave
        valor = f'{programa}-{codigo}' if self.repetidos[codigo] > 1 else str(codigo)
        if len(valor) > 20:
            raise ValueError('Codigo de pensum SWA demasiado largo para ERP.')
        return valor

    def _elegir(self, clave, bloquear=False):
        programa, version = clave
        equivalencias = EquivalenciaPensumSwa.objects.using('default')
        pensums = PensumErp.objects.using('default')
        if bloquear:
            equivalencias = equivalencias.select_for_update()
            pensums = pensums.select_for_update()
        eq = (equivalencias.filter(programa_swa_id=programa, pensum_swa_id=version).first()
              if self.equivalencias_disponibles else None)
        if eq:
            destino = pensums.filter(pk=eq.pensum_erp_id).first()
            if destino is None or destino.programa_id != programa:
                raise ValueError('La equivalencia de pensum tiene otro programa o destino inexistente.')
            return destino, eq
        destinos = list(pensums.filter(programa_id=programa))
        por_codigo = pensums.filter(codigo=self.codigo(clave)).first()
        if por_codigo and por_codigo.programa_id != programa:
            raise ValueError('El codigo de pensum ya pertenece a otro programa ERP.')
        destino = por_codigo
        principal = VERSIONES_APROBADAS.get(programa)
        if principal is None and len(self.versiones[programa]) == 1:
            principal = self.versiones[programa][0]
        if destino is None and version == principal:
            libres = [p for p in destinos if p.pk not in self.reservados and (
                      not self.equivalencias_disponibles
                      or not equivalencias.filter(pensum_erp_id=p.pk).exists())]
            if len(libres) > 1:
                raise ValueError('Varias correspondencias posibles para el pensum ERP.')
            if libres:
                destino = libres[0]
                otros_codigos = {self.codigo((programa, v)) for v in self.versiones[programa] if v != version}
                if destino.codigo in otros_codigos:
                    raise ValueError('El pensum ERP ya identifica otra version SWA.')
        if principal is None and destino is None and any(p.pk not in self.reservados and (
                not self.equivalencias_disponibles
                or not equivalencias.filter(pensum_erp_id=p.pk).exists()) for p in destinos):
            raise ValueError('Falta aprobar la version SWA que actualiza el pensum ERP.')
        if destino and self.equivalencias_disponibles and equivalencias.filter(pensum_erp_id=destino.pk).exists():
            raise ValueError('El pensum ERP ya representa otra version SWA.')
        if destino and destino.pk in self.reservados and self.reservados[destino.pk] != clave:
            raise ValueError('El pensum ERP ya representa otra version SWA.')
        return destino, None

    def comprobar(self, clave, bloquear=False):
        if clave not in self.pensums:
            raise ValueError('El pensum declarado no existe en SWA.')
        if not ProgramaErp.objects.using('default').filter(pk=clave[0]).exists():
            raise ValueError('Programa ERP pendiente de migracion.')
        destino, eq = self._elegir(clave, bloquear=bloquear)
        if eq and eq.ultimo_importado != estado_malla(destino):
            raise ValueError('La malla ERP fue modificada despues de la importacion.')
        codigos = [str(m['asignatura']) for m in self.materias[clave]]
        existentes = {a.codigo: a for a in AsignaturaErp.objects.using('default').filter(codigo__in=codigos)}
        for m in self.materias[clave]:
            if m['nivel'] is None or not 0 <= int(m['nivel']) <= 32767:
                raise ValueError('Nivel de materia incompatible con el ERP.')
            if str(m['asignatura']) not in existentes:
                datos_asignatura(self.asignaturas.get((clave[0], m['asignatura']), {}), m['creditos'])
        requisitos = {str(r[2]) for r in self.restricciones[clave]}
        if requisitos - set(AsignaturaErp.objects.using('default').filter(codigo__in=requisitos).values_list('codigo', flat=True)) - set(codigos):
            raise ValueError('Asignatura requisito pendiente de migracion.')
        if destino and eq:
            deseadas = set(codigos)
            antes = set(AsignaturaErp.objects.using('default').filter(
                pk__in=[m['asignatura_id'] for m in eq.ultimo_importado['materias']]).values_list('codigo', flat=True))
            if antes - deseadas:
                raise ValueError('SWA retiro materias importadas; revisar sus referencias antes de actualizar.')
        return destino, eq

    def aplicar(self, clave):
        with transaction.atomic(using='default'):
            destino, eq = self.comprobar(clave, bloquear=True)
            fuente = self.pensums[clave]
            import json
            origen = json.loads(json.dumps({'pensum': fuente, 'materias': self.materias[clave],
                'restricciones': self.restricciones[clave]}, default=str))
            # comprobar ya verifico la malla contra su baseline bajo bloqueo.
            if eq is not None and eq.origen == origen:
                return destino
            if destino is None:
                inicial = self.primeros.get(clave)
                if periodo_ingreso_valido(inicial) is None:
                    raise ValueError('No hay periodo de uso valido para crear el pensum.')
                destino = PensumErp.objects.using('default').create(programa_id=clave[0],
                    codigo=self.codigo(clave), estado=fuente['estado'] == 'A', periodo=periodo_erp(inicial))
            else:
                destino.codigo = self.codigo(clave)
                destino.estado = fuente['estado'] == 'A'
                destino.save(using='default', update_fields=['codigo', 'estado'])
            por_codigo = {}
            for m in self.materias[clave]:
                asignatura, _ = obtener_o_crear_asignatura(
                    m['asignatura'], self.asignaturas.get((clave[0], m['asignatura']), {}), m['creditos'])
                filas = list(PensumAsignaturaErp.objects.using('default').select_for_update()
                             .filter(pensum_id=destino.pk, asignatura_id=asignatura.pk))
                if len(filas) > 1:
                    raise ValueError('La malla ERP tiene materias duplicadas.')
                materia = filas[0] if filas else PensumAsignaturaErp(pensum=destino, asignatura=asignatura)
                materia.semestre = m['nivel']
                materia.save(using='default')
                por_codigo[m['asignatura']] = materia
            deseadas = set()
            for materia_codigo, tipo, requisito_codigo in self.restricciones[clave]:
                materia = por_codigo.get(materia_codigo)
                requisito = AsignaturaErp.objects.using('default').filter(codigo=str(requisito_codigo)).first()
                if materia is None or requisito is None:
                    raise ValueError('Requisito sin materia del pensum o asignatura ERP.')
                deseadas.add((materia.pk, tipo, requisito.pk))
                RestriccionPensumErp.objects.using('default').get_or_create(
                    pensum_asignatura_id=materia.pk, tipo=tipo, asignatura_id=requisito.pk)
            if eq:
                anteriores = {(r['pensum_asignatura_id'], r['tipo'], r['asignatura_id'])
                              for r in eq.ultimo_importado['restricciones']}
                if anteriores - deseadas:
                    raise ValueError('SWA retiro requisitos importados; se requiere revision.')
            if eq is None:
                eq = EquivalenciaPensumSwa(programa_swa_id=clave[0], pensum_swa_id=clave[1], pensum_erp=destino)
            eq.origen = origen
            eq.ultimo_importado = estado_malla(destino)
            eq.save(using='default')
            return destino
