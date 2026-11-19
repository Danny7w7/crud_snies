"""Identidad de origen: importa IDs SWA, sin consumir la secuencia del ERP."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from django.db import IntegrityError, connections, transaction
from django.db.models import Q

from config.db_postgres.source import estados_generales_swa
from ubicacion.models import EquivalenciaEstudianteSwa, EquivalenciaPersonaSwa, AsociacionProgramaSwa
from .models import EstudianteSwa, Persona, PersonaErp

PROGRAMAS_INGLES = frozenset({51, 52})
PROGRAMAS_EXTENSION = frozenset({50, 60})
PROGRAMAS_COMPLEMENTARIOS = PROGRAMAS_INGLES | PROGRAMAS_EXTENSION
TIPOS_DOCUMENTO = {1: 'CC', 2: 'PP', 3: 'CE', 4: 'TI', 8: 'RC'}
ESTADO_PERSONA_PENDIENTE = 'Estado general de persona pendiente de evidencia explícita.'


def _estado_general(valor):
    estado = str(valor or '').strip().upper()
    if estado not in PersonaErp.EstadoGeneral.values:
        raise ValueError(ESTADO_PERSONA_PENDIENTE)
    return estado


def resolver_estado_persona(personas, estado_aprobado=None):
    """Un estado explícito del origen o una decisión; las matrículas no aportan evidencia."""
    if estado_aprobado is not None:
        return _estado_general(estado_aprobado)
    datos = [getattr(persona, 'estado_general', None) for persona in personas]
    estados = {_estado_general(valor) for valor in datos if str(valor or '').strip()}
    if len(estados) > 1:
        raise ValueError('Estados generales contradictorios entre personas SWA verificadas.')
    return next(iter(estados), None)


def validar_payload_persona(datos):
    resultado = {**datos, 'estado_general': _estado_general(datos.get('estado_general'))}
    for campo in ('primer_nombre', 'segundo_nombre', 'primer_apellido', 'segundo_apellido', 'email'):
        valor = resultado.get(campo)
        campo_erp = PersonaErp._meta.get_field(campo)
        if (not isinstance(valor, str) or len(valor) > campo_erp.max_length
                or campo in ('primer_nombre', 'primer_apellido') and not valor.strip()):
            raise ValueError(f'{campo} incompatible con el ERP.')
    return resultado


def tipos_documento_destino():
    with connections['default'].cursor() as cursor:
        cursor.execute(
            'SELECT codigo, MIN(id) FROM erp_tipodocumentoidentidad '
            'GROUP BY codigo HAVING COUNT(*) = 1'
        )
        return dict(cursor.fetchall())


def periodo_ingreso_valido(valor):
    texto = str(valor or '')
    if not re.fullmatch(r'[0-9]{4}[12]', texto) or int(texto[:4]) == 0:
        return None
    return texto[:4], texto[4]


def normalizar_nombre(valor):
    texto = unicodedata.normalize('NFD', str(valor or ''))
    texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
    return ' '.join(texto.upper().split())


def nombre_persona(persona):
    if hasattr(persona, 'nombre'):
        partes = [persona.nombre, persona.apellido]
    else:
        partes = [persona.primer_nombre, persona.segundo_nombre,
                  persona.primer_apellido, persona.segundo_apellido]
    return normalizar_nombre(' '.join(str(p or '') for p in partes))


def documento_normalizado(valor):
    digitos = re.sub(r'[^0-9]', '', str(valor or ''))
    return int(digitos) if digitos else None


@dataclass(frozen=True)
class GrupoPersona:
    persona_id: int
    personas: tuple
    tipo_swa: int
    documento: int
    nombre: str

    @property
    def representante(self):
        return next(p for p in self.personas if p.pk == self.persona_id)


def resolver_grupos(personas, estudiantes):
    """Agrupa sin escribir; nombres o tipos incompatibles nunca se fusionan."""
    documentos = defaultdict(list)
    pendientes = {}
    carreras = defaultdict(list)
    for e in estudiantes:
        if isinstance(e.programa, int) and e.programa > 0 and e.programa not in PROGRAMAS_COMPLEMENTARIOS:
            carreras[e.persona_id].append(e.periodo_ingreso)
    for p in personas:
        doc = documento_normalizado(p.identificacion)
        tipo = p.tipo_identificacion
        if (doc is None or len(str(doc)) < 6 or doc > 9223372036854775807
                or tipo in (0, 1, 4) and len(str(doc)) > 10
                or tipo not in {0, *TIPOS_DOCUMENTO}
                or not str(p.nombre or '').strip() or not str(p.apellido or '').strip()):
            pendientes[p.pk] = 'Identidad incompleta o documento incompatible.'
            continue
        documentos[doc].append(p)
    mapa = {}
    for doc, miembros in documentos.items():
        nombres = {nombre_persona(p) for p in miembros}
        tipos = {p.tipo_identificacion for p in miembros}
        compatibles = tipos <= {0, 1, 4} or len(tipos) == 1 and 0 not in tipos
        if len(nombres) != 1 or not compatibles or tipos == {0}:
            for p in miembros:
                pendientes[p.pk] = 'Mismo documento con nombres o tipos incompatibles.'
            continue
        de_carrera = [p for p in miembros if p.pk in carreras]
        con_ingreso = [(int(''.join(periodo)), p.pk) for p in de_carrera
                       for valor in carreras[p.pk] if (periodo := periodo_ingreso_valido(valor))]
        elegido = (min(con_ingreso)[1] if con_ingreso else
                   min(p.pk for p in de_carrera or miembros))
        tipo = 1 if 1 in tipos else 4 if 4 in tipos else next(iter(tipos - {0}))
        grupo = GrupoPersona(elegido, tuple(sorted(miembros, key=lambda p: p.pk)),
                             tipo, doc, next(iter(nombres)))
        for p in miembros:
            mapa[p.pk] = grupo
    return mapa, pendientes


def cargar_grupos():
    personas = list(Persona.objects.using('produccion').all().iterator())
    estudiantes = list(EstudianteSwa.objects.using('produccion').all().iterator())
    estados = estados_generales_swa()
    for persona in personas:
        if persona.pk in estados:
            persona.estado_general = estados[persona.pk]
    mapa, pendientes = resolver_grupos(personas, estudiantes)
    return mapa, pendientes, estudiantes


def comprobar_destino(grupo, tipos_doc):
    """Valida el ID canónico o el destino ERP ya verificado por equivalencia."""
    ids_tipo = {tipos_doc[TIPOS_DOCUMENTO[t]] for t in ({1, 4} if grupo.tipo_swa in (1, 4)
                else {grupo.tipo_swa}) if TIPOS_DOCUMENTO[t] in tipos_doc}
    origenes = [p.pk for p in grupo.personas]
    equivalencias = dict(EquivalenciaPersonaSwa.objects.using('default').filter(
        persona_swa_id__in=origenes).values_list('persona_swa_id', 'persona_erp_id'))
    destino_id = equivalencias.get(grupo.persona_id, grupo.persona_id)
    if any(id_erp != destino_id for id_erp in equivalencias.values()):
        raise ValueError('Una equivalencia de persona ya tiene otro destino.')
    candidatos = list(PersonaErp.objects.using('default').filter(
        Q(pk=destino_id) | Q(nro_documento=grupo.documento)))
    por_id = next((persona for persona in candidatos if persona.pk == destino_id), None)
    if por_id is None and equivalencias:
        raise ValueError('La persona ERP trazada no existe.')
    if por_id is not None and (por_id.nro_documento != grupo.documento
            or por_id.tipo_documento_id not in ids_tipo or nombre_persona(por_id) != grupo.nombre):
        raise ValueError('El ID SWA esta ocupado por otra identidad en el ERP.')
    if any(p.pk != destino_id for p in candidatos):
        raise ValueError('La identidad ya existe en el ERP con otro ID o datos incompatibles.')
    if EquivalenciaEstudianteSwa.objects.using('default').filter(
            persona_swa_id__in=origenes).exclude(persona_erp_id=destino_id).exists():
        raise ValueError('Una equivalencia de estudiante ya tiene otro destino.')
    if AsociacionProgramaSwa.objects.using('default').filter(
            persona_swa_id__in=origenes, persona_erp_id__isnull=False).exclude(
                persona_erp_id=destino_id).exists():
        raise ValueError('Una asociacion de programa ya tiene otra persona destino.')
    return por_id


def registrar_personas(grupo, persona):
    for origen in grupo.personas:
        eq, _ = EquivalenciaPersonaSwa.objects.using('default').get_or_create(
            persona_swa_id=origen.pk, defaults={'persona_erp_id': persona.pk})
        if eq.persona_erp_id != persona.pk:
            raise ValueError('La equivalencia de persona no se puede reasignar.')


def obtener_o_crear_persona(grupo, tipos_doc, defaults):
    """Inserta el ID canónico SWA y sus alias en una transacción."""
    try:
        with transaction.atomic(using='default'):
            persona = comprobar_destino(grupo, tipos_doc)
            creada = persona is None
            if creada:
                tipo = tipos_doc.get(TIPOS_DOCUMENTO[grupo.tipo_swa])
                if tipo is None:
                    raise ValueError('Tipo de documento pendiente en el ERP.')
                estado = resolver_estado_persona(grupo.personas, defaults.get('estado_general'))
                datos = validar_payload_persona({**defaults, 'estado_general': estado})
                persona = PersonaErp(id=grupo.persona_id,
                    tipo_documento_id=tipo, nro_documento=grupo.documento, **datos)
                persona.save(using='default', force_insert=True)
            registrar_personas(grupo, persona)
            return persona, creada
    except IntegrityError:
        with transaction.atomic(using='default'):
            persona = comprobar_destino(grupo, tipos_doc)
            if persona is None:
                raise
            registrar_personas(grupo, persona)
            return persona, False


def registrar_equivalencia(estudiante, vinculo):
    codigo = (estudiante.codigo or '').strip()
    if not codigo or estudiante.persona_id is None:
        raise ValueError('El registro SWA requiere codigo y persona de origen.')
    equivalencia, creada = EquivalenciaEstudianteSwa.objects.using('default').get_or_create(
        codigo_estudiante=codigo,
        defaults={'persona_swa_id': estudiante.persona_id,
                  'persona_erp_id': vinculo.persona_id, 'vinculo_erp_id': vinculo.pk},
    )
    if not creada and (equivalencia.persona_swa_id != estudiante.persona_id
                      or equivalencia.persona_erp_id != vinculo.persona_id
                      or equivalencia.vinculo_erp_id != vinculo.pk):
        raise ValueError('El codigo SWA ya tiene otra equivalencia ERP.')
    return equivalencia
