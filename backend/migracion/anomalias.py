"""Reporte de anomalias de SWA: cabos sueltos academicos y financieros.

Lee solo la copia local (`swa_raw`, mas `migra` para saber que viaja al ERP) y deja cada caso
en `anomalias.anomalia`. No cambia la depuracion ni la carga: documenta lo que SWA permitio
aunque no deberia (aprobar sin matricula legalizada, sin pago, sin prerrequisito, etc.).

Cada regla del CATALOGO devuelve filas (estudiante, periodo, programa, asignatura, grupo, detalle).
Una regla sin filas tambien queda en el reporte: es la prueba de que se verifico.

Reglas para no inflar los conteos:
- Todo se evalua por PERSONA, no por codigo: una persona con varios codigos SWA cuenta una vez,
  y un pago o una nota hechos con otro de sus codigos cuentan como suyos. La persona es la unificada por
  la depuracion (nid): SWA repite personas en varios registros con el mismo documento (`aux_estudiante`).
- Las reglas de plan de estudios usan la nota final de cada asignatura y periodo (`aux_final`, mismo
  criterio que la depuracion: la habilitacion/validacion/homologacion reemplaza al grupo regular).
- Las homologaciones (grupos -1 y -5) no exigen matricula, pago ni prerrequisitos.
- Cada situacion cae en una sola regla: el Centro de Idiomas tiene sus propias reglas de pago y no
  se repite en las de matricula; un periodo sin pago se reporta segun su estado en historico
  (M -> PAGOS, P -> MATRICULA, sin fila -> NOTAS_SIN_HISTORICO).
- Severidad INFO = informativa: se documenta pero no cuenta como anomalia en los indicadores.

Estados financieros de SWA (inferidos de los datos, no hay tabla de estados):
- recibo_swa.estado: 10 pagado, 1 financiado con credito, 0 generado sin pagar, 2 anulado.
- cuota.estado: 10 pagada, 0 pendiente, 2 anulada/refinanciada.
Los recibos existen de forma sistematica desde 2017-1; antes no hay soporte financiero.
"""
import json

from .conexiones import conectar_destino
from .depuracion import CENTROS_IDIOMAS, PERIODO_CORTE

PRIMER_PERIODO_FINANCIERO = 20171
HOY = 'to_char(current_date, \'YYYYMMDD\')::int'
CENTROS = 'ARRAY' + str(list(CENTROS_IDIOMAS))
HOMOLOGACION = '(-1, -5)'

# Conceptos de recibo que pagan una matricula (carrera y modulos de ingles). Nivel de ingles que paga cada
# concepto (None = matricula general). VACACIONAL (17) y VALIDACION (INGLES) (24) no pagan la matricula del
# periodo: son otro derecho (24 se revisa en VALIDACION_SIN_PAGO).
CONCEPTOS_MATRICULA = {
    0: None, 41: None, 108: None, 109: None, 110: None, 35: None,
    14: 1, 15: 2, 16: 3, 28: 4, 40: 5, 45: 6,          # INGLES REG.(I..VI)
    29: 1, 30: 2, 31: 3, 32: 4, 33: 5, 34: 6,          # INGLES INT.I..VI
    46: 1, 47: 2, 48: 3, 49: 4, 50: 5, 51: 6,          # INGLES INT.I..VI (concepto adicional)
    52: 1, 53: 2, 54: 3, 55: 4, 56: 5, 57: 6,          # INGLES REG.(I..VI) (concepto adicional)
    18: 1, 19: 2,                                      # VACACIONAL INGLES I / II
}
CONCEPTO_HABILITACION = (6,)
CONCEPTOS_VALIDACION = (7, 24)

# En SWA el credito vale el neto del recibo y sus cuotas dicen QUIEN cubrio ese neto (catalogo tipo_cuota).
# La cuota 7 negativa es saldo a favor generado; positiva, saldo usado.
CLASES_CUOTA = {
    'ESTUDIANTE': (1, 2, 3, 4, 5, 8, 11, 12, 13, 14, 15, 16),
    'TERCERO': (6, 38, 39, 40, 41, 44, 45, 46, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64,
                80, 81, 82, 86, 87, 88, 89),                       # ICETEX, convenios, alcaldias, cajas
    'DESCUENTO': (9, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 83, 84, 85),
    'SALDO_A_FAVOR': (7,),
    'AJUSTE': (18,),
}
# Saldo a favor pendiente: gravedad por monto (todo saldo es ALTA; la gravedad separa los casos)
GRAVEDAD_MONTO = ((2_000_000, '1 Crítica'), (500_000, '2 Alta'), (1_000, '3 Moderada'))

CATEGORIAS = {
    'ESTADO_REGISTRO': 'Estados C / P en registro',
    'NOTAS': 'Notas inconsistentes',
    'PLAN_ESTUDIOS': 'Pensum y prerrequisitos',
    'MATRICULA': 'Matrícula (historico)',
    'PAGOS': 'Pagos y cartera',
    'INGLES': 'Módulos de inglés',
    'GRADO': 'Grados',
    'SALDOS': 'Saldos a favor y beneficios',
}
SEVERIDADES = {'ALTA': 'Alta', 'MEDIA': 'Media', 'BAJA': 'Baja', 'INFO': 'Informativa'}
REGISTRO, FINANCIERA, IDIOMAS = 'Registro académico', 'Financiera', 'Centro de Idiomas'

GRUPOS = {-1: 'Homologación', -5: 'Homologación', -2: 'Validación', -3: 'Habilitación', -4: 'Curso especial'}
ESTADOS_RECIBO = {10: 'pagado', 1: 'financiado', 0: 'sin pagar', 2: 'anulado'}
COBERTURAS = {'PAGO_REGISTRADO': 'un pago registrado', 'CREDITO': 'el crédito', 'BENEFICIO': 'un beneficio del 100 %'}


# ------------------------------------------------------------------------- texto legible del detalle
def _nota(v):
    return '—' if v is None else f'{float(v):.2f}'.replace('.', ',')


def _pesos(v):
    return '—' if v is None else '$' + f'{float(v):,.0f}'.replace(',', '.')


def _periodo(p):
    return f'{int(p) // 10}-{int(p) % 10}' if p else '—'


def _lista(valores, maximo=8):
    valores = [str(v) for v in (valores or []) if v is not None]
    if len(valores) > maximo:
        return ', '.join(valores[:maximo]) + f' y {len(valores) - maximo} más'
    return ', '.join(valores)


def _recibos(recibos):
    if not recibos:
        return 'ninguno'

    def uno(r):
        estado = ESTADOS_RECIBO.get(r.get('estado'), str(r.get('estado')))
        if r.get('cubierto_por') and r['cubierto_por'] != 'ESTADO':
            partes = [f"en SWA figura {estado}, pero lo cubrió {COBERTURAS.get(r['cubierto_por'], r['cubierto_por'])}"]
        else:
            partes = [estado]
        if r.get('programa'):
            partes.append(f"programa {r['programa']}")
        if r.get('terceros'):
            partes.append('pagó ' + _lista(r['terceros'], 2))
        if r.get('valor_pagado'):
            partes.append(_pesos(r['valor_pagado']))
        if r.get('beneficios'):
            partes.append('beneficios: ' + _lista(r['beneficios'], 3))
        return f"N.º {r.get('numero')} {(r.get('concepto_nombre') or r.get('concepto') or '').strip()} ({', '.join(partes)})"
    return '; '.join(uno(r) for r in recibos)


def _monto(d):
    return f"{_pesos(d['generado'])} generado, {_pesos(d['usado'])} usado, {_pesos(d['devuelto'])} devuelto"


def _aprobado(estado):
    return 'aprobada' if estado == 'A' else 'reprobada'


def _pago(d):
    pago = d.get('pago')
    if pago == 'SIN_DATOS':
        return 'Sin soporte financiero (antes de 2017-1).'
    if pago == 'SI':
        return f"Pagó la matrícula: {_recibos(d.get('recibos'))}."
    return f"Sin pago de matrícula. Recibos: {_recibos(d.get('recibos'))}."


def _cambios(cambios):
    return '; '.join(f"{c.get('fecha') or '¿fecha?'}: {_nota(c.get('nota'))} → {_nota(c.get('nueva_nota'))}"
                     f" ({c.get('por') or 'sin usuario'}{', ' + ' '.join(c['descripcion'].split()) if c.get('descripcion') else ''})"
                     for c in cambios or [])


def texto_caso(tipo, detalle):
    """Frase legible del hallazgo (lo que va en la tabla y en el Excel en vez del JSON)."""
    regla = CATALOGO.get(tipo)
    d = detalle or {}
    if regla and regla.get('texto'):
        try:
            return regla['texto'](d)
        except (KeyError, TypeError, ValueError):
            pass
    return '; '.join(f"{k.replace('_', ' ')}: {v}" for k, v in d.items())


def nombre_grupo(grupo):
    if grupo is None:
        return ''
    return GRUPOS.get(grupo, f'Regular (grupo {grupo})')


# CASE de gravedad por monto sobre la columna `col` (todo saldo es ALTA; la gravedad lo separa)
GRAVEDAD_SQL = ('CASE ' + ' '.join(f"WHEN {{col}} >= {m} THEN '{g}'" for m, g in GRAVEDAD_MONTO[:-1])
                + f" ELSE '{GRAVEDAD_MONTO[-1][1]}' END")

# Periodos que la persona CURSO (grupo regular) sin ninguna evidencia de pago y sin estar en M (eso lo cubren
# MATRICULA_SIN_RECIBO / MATRICULA_RECIBO_SIN_PAGAR). Los dos centros de idiomas cuentan como uno. Justifican el
# periodo: un recibo de matricula cubierto (para ingles, de cualquier programa: puede venir con la carrera; para
# carrera, de cualquier carrera: programas renombrados) o un curso pagado por otro concepto academico.
# `condicion` separa periodos cerrados sin aprobadas de periodos en curso.
CONCEPTOS_CURSO = (17, 20, 23, 36, 37, 38, 39, 100, 101, 102, 111, 112, 113, 114)  # vacacional, nivelacion,
                                                                                  # creditos adic., diplomados
SQL_CURSADO_SIN_PAGO = f"""
    SELECT estudiante, periodo, programa, asignatura, grupo, detalle FROM (
        SELECT min(r.estudiante) AS estudiante, r.periodo, min(r.programa) AS programa,
               NULL::int AS asignatura, NULL::int AS grupo,
               jsonb_build_object('cursadas', count(*), 'aprobadas', count(*) FILTER (WHERE r.estado_definitiva = 'A'),
                                  'asignaturas', jsonb_agg(a.nombre ORDER BY a.nombre),
                                  'historico', (SELECT string_agg(DISTINCT h.estado, '/') FROM swa_raw.historico h
                                                JOIN anomalias.aux_estudiante e ON e.codigo = h.estudiante
                                                WHERE e.persona = r.persona AND h.periodo = r.periodo
                                                  AND h.programa IN (min(r.programa), max(r.programa))),
                                  'recibos', (SELECT jsonb_agg(jsonb_build_object('numero', x.numero, 'concepto_nombre',
                                                  x.concepto_nombre, 'estado', x.estado, 'programa', x.programa)
                                                  ORDER BY x.numero)
                                              FROM anomalias.aux_recibo x WHERE x.persona = r.persona
                                                AND x.periodo = r.periodo)) AS detalle,
               r.persona, bool_or(r.programa = ANY({CENTROS})) AS es_ingles
        FROM anomalias.aux_reg r
        LEFT JOIN swa_raw.asignatura a ON a.codigo = r.asignatura
        WHERE r.estado = 'R' AND r.grupo >= 0 AND r.periodo >= {PRIMER_PERIODO_FINANCIERO}
        GROUP BY r.persona, r.periodo, CASE WHEN r.programa = ANY({CENTROS}) THEN 0 ELSE r.programa END
    ) c
    WHERE {{condicion}}
      AND NOT EXISTS (SELECT 1 FROM anomalias.aux_hist h WHERE h.persona = c.persona AND h.periodo = c.periodo
                      AND h.matriculado AND (h.programa = c.programa
                                             OR (c.es_ingles AND h.programa = ANY({CENTROS}))))
      AND NOT EXISTS (SELECT 1 FROM anomalias.aux_pago pg WHERE pg.persona = c.persona AND pg.periodo = c.periodo
                      AND pg.pagado AND (c.es_ingles OR pg.programa <> ALL({CENTROS})))
      AND NOT EXISTS (SELECT 1 FROM anomalias.aux_recibo x WHERE x.persona = c.persona AND x.periodo = c.periodo
                      AND x.cubierto_por IS NOT NULL AND x.concepto IN {CONCEPTOS_CURSO})
"""


# Prematricula (P) con asignaturas aprobadas; `condicion` separa con pago ('SI') de sin pago / sin datos.
SQL_PREMATRICULA = f"""
    SELECT * FROM (
        SELECT h.estudiante, h.periodo, h.programa, NULL::int AS asignatura, NULL::int AS grupo,
               jsonb_build_object('aprobadas', count(*), 'asignaturas', jsonb_agg(a.nombre ORDER BY a.nombre),
                                  'pago', CASE WHEN bool_or(pg.pagado) THEN 'SI'
                                               WHEN h.periodo < {PRIMER_PERIODO_FINANCIERO} THEN 'SIN_DATOS'
                                               ELSE 'NO' END,
                                  'recibos', min(pg.recibos::text)::jsonb) AS detalle
        FROM swa_raw.historico h
        JOIN anomalias.aux_estudiante e ON e.codigo = h.estudiante
        JOIN swa_raw.registro r ON r.periodo = h.periodo AND r.programa = h.programa AND r.estudiante = h.estudiante
        JOIN anomalias.aux_hist ah ON ah.persona = e.persona AND ah.periodo = h.periodo AND ah.programa = h.programa
        LEFT JOIN anomalias.aux_pago pg ON pg.persona = e.persona AND pg.periodo = h.periodo
                                       AND pg.programa = h.programa
        LEFT JOIN swa_raw.asignatura a ON a.codigo = r.asignatura
        WHERE h.estado = 'P' AND NOT ah.matriculado AND h.programa <> ALL({CENTROS})
          AND r.estado = 'R' AND r.estado_definitiva = 'A' AND r.grupo >= 0
        GROUP BY h.estudiante, h.periodo, h.programa
    ) x WHERE x.detalle->>'pago' {{condicion}}
"""


# ---------------------------------------------------------------------------------------- catalogo
# Columnas que devuelve cada sql: estudiante, periodo, programa, asignatura, grupo, detalle (jsonb).
CATALOGO = {
    # -------------------------------------------------------------- estados C / P de registro
    'CANCELADA_CON_APROBACION': {
        'categoria': 'ESTADO_REGISTRO', 'severidad': 'ALTA', 'responsable': REGISTRO,
        'titulo': 'Asignatura cancelada (C) pero aprobada',
        'que_paso': 'En registro la asignatura está en estado C (cancelada) y aun así tiene estado_definitiva A '
                    'con nota aprobatoria.',
        'riesgo': 'No se sabe si la cursó o la canceló. La depuración la aparta (no viaja al ERP); si en realidad '
                  'la aprobó, el estudiante pierde un crédito aprobado.',
        'recomendacion': 'Registro académico confirma con el acta del docente: si la cursó, corregir el estado a R '
                         'en SWA antes del corte; si la canceló, la nota debe quedar en 0.',
        'texto': lambda d: f"Cancelada (C) con definitiva {_nota(d['definitiva'])} marcada como aprobada.",
        'sql': """
            SELECT estudiante, periodo, programa, asignatura, grupo,
                   jsonb_build_object('definitiva', definitiva, 'log_update', log_update)
            FROM swa_raw.registro WHERE estado = 'C' AND estado_definitiva = 'A'
        """,
    },
    'CANCELADA_CON_NOTA': {
        'categoria': 'ESTADO_REGISTRO', 'severidad': 'MEDIA', 'responsable': REGISTRO,
        'titulo': 'Asignatura cancelada (C) con nota',
        'que_paso': 'Estado C (cancelada), no aprobada, pero con una nota mayor que cero.',
        'riesgo': 'Una cancelación no debería tener calificación: o se canceló tarde o el estado está mal.',
        'recomendacion': 'Verificar la fecha de cancelación frente al calendario académico.',
        'texto': lambda d: f"Cancelada (C) con definitiva {_nota(d['definitiva'])}.",
        'sql': """
            SELECT estudiante, periodo, programa, asignatura, grupo,
                   jsonb_build_object('definitiva', definitiva, 'log_update', log_update)
            FROM swa_raw.registro
            WHERE estado = 'C' AND estado_definitiva IS DISTINCT FROM 'A' AND coalesce(definitiva, 0) > 0
        """,
    },
    'CANCELADA_SIN_NOTA': {
        'categoria': 'ESTADO_REGISTRO', 'severidad': 'INFO', 'responsable': REGISTRO,
        'titulo': 'Asignatura cancelada (C) sin nota',
        'que_paso': 'Cancelación normal: estado C y nota 0.',
        'riesgo': 'Ninguno; queda documentada porque no viaja al ERP (se aparta en la depuración).',
        'recomendacion': 'Solo informativo.',
        'texto': lambda d: 'Cancelación normal, sin nota.',
        'sql': """
            SELECT estudiante, periodo, programa, asignatura, grupo,
                   jsonb_build_object('log_update', log_update)
            FROM swa_raw.registro
            WHERE estado = 'C' AND estado_definitiva IS DISTINCT FROM 'A' AND coalesce(definitiva, 0) = 0
        """,
    },
    'PREINSCRITA_SIN_CURSAR': {
        'categoria': 'ESTADO_REGISTRO', 'severidad': 'INFO', 'responsable': REGISTRO,
        'titulo': 'Asignatura preinscrita (P) en un periodo cerrado',
        'que_paso': 'La asignatura quedó en estado P (preinscrita) y el periodo ya cerró: nunca se calificó ni se '
                    'retiró.',
        'riesgo': 'Es un cabo suelto: no hay nota ni cancelación. No viaja al ERP.',
        'recomendacion': 'Informativo; si el estudiante sí la cursó, Registro debe cargar la nota en SWA.',
        'texto': lambda d: 'Quedó preinscrita (P): el periodo cerró sin nota ni retiro.',
        'sql': f"""
            SELECT estudiante, periodo, programa, asignatura, grupo,
                   jsonb_build_object('definitiva', definitiva, 'log_insert', log_insert)
            FROM swa_raw.registro WHERE estado = 'P' AND periodo < {PERIODO_CORTE}
        """,
    },
    'PREINSCRITA_PERIODO_ACTUAL': {
        'categoria': 'ESTADO_REGISTRO', 'severidad': 'BAJA', 'responsable': REGISTRO,
        'titulo': 'Asignatura preinscrita (P) en el periodo en curso',
        'que_paso': f'Preinscripción del periodo {PERIODO_CORTE} o posterior que aún no pasa a R.',
        'riesgo': 'Si no se legaliza antes del corte, el estudiante queda sin esa asignatura en el ERP.',
        'recomendacion': 'Revisar antes de la ventana de corte.',
        'texto': lambda d: 'Preinscrita (P), aún sin legalizar.',
        'sql': f"""
            SELECT estudiante, periodo, programa, asignatura, grupo, jsonb_build_object('log_insert', log_insert)
            FROM swa_raw.registro WHERE estado = 'P' AND periodo >= {PERIODO_CORTE}
        """,
    },
    # ------------------------------------------------------------------------------ notas
    'ESTADO_DEFINITIVA_INCOHERENTE': {
        'categoria': 'NOTAS', 'severidad': 'ALTA', 'responsable': REGISTRO,
        'titulo': 'Aprobada con nota reprobatoria (o al revés)',
        'que_paso': 'estado_definitiva no corresponde a la nota según la nota mínima del programa '
                    '(3,0 en pregrado; 3,5 en especializaciones).',
        'riesgo': 'El ERP calcula aprobado/reprobado por la nota: el estado cambiaría al migrar.',
        'recomendacion': 'Registro confirma cuál es el dato correcto (nota o estado) y lo corrige en SWA.',
        'texto': lambda d: (f"Definitiva {_nota(d['definitiva'])} marcada como {_aprobado(d['estado_definitiva'])}; "
                            f"la nota mínima del programa es {_nota(d['nota_minima'])}."),
        'sql': """
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('definitiva', r.definitiva, 'estado_definitiva', r.estado_definitiva,
                                      'nota_minima', p.nma, 'log_update', r.log_update)
            FROM swa_raw.registro r JOIN swa_raw.programa p ON p.codigo = r.programa
            WHERE r.estado = 'R'
              AND ((r.estado_definitiva = 'A' AND r.definitiva < p.na)
                   OR (r.estado_definitiva = 'R' AND r.definitiva >= p.na))
        """,
    },
    'DEFINITIVA_NO_CUADRA_CON_CORTES': {
        'categoria': 'NOTAS', 'severidad': 'ALTA', 'responsable': REGISTRO,
        'titulo': 'La definitiva no cuadra con los cortes',
        'que_paso': 'La asignatura tiene los tres cortes (30 % + 30 % + 40 %) pero la definitiva registrada cambia '
                    'el resultado: los cortes dan reprobado y la definitiva aprobado, o al revés.',
        'riesgo': 'Aprobación sin soporte en las notas parciales.',
        'recomendacion': 'Revisar el cambio de nota o el acta del docente.',
        'texto': lambda d: (f"Cortes {_nota(d['corte1'])} / {_nota(d['corte2'])} / {_nota(d['corte3'])} dan "
                            f"{_nota(d['calculada'])}, pero la definitiva registrada es {_nota(d['definitiva'])} "
                            f"({_aprobado(d['estado_definitiva'])})."),
        'sql': """
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('definitiva', r.definitiva, 'calculada', round(c.calc::numeric, 2),
                                      'corte1', c.n1, 'corte2', c.n2, 'corte3', c.n3, 'estado_definitiva',
                                      r.estado_definitiva, 'log_update', r.log_update)
            FROM swa_raw.registro r
            JOIN swa_raw.programa p ON p.codigo = r.programa
            JOIN (SELECT periodo, programa, asignatura, grupo, estudiante,
                         max(nota) FILTER (WHERE tipo_nota = 1) AS n1, max(nota) FILTER (WHERE tipo_nota = 2) AS n2,
                         max(nota) FILTER (WHERE tipo_nota = 3) AS n3,
                         sum(coalesce(nota, 0) * CASE tipo_nota WHEN 1 THEN .3 WHEN 2 THEN .3 WHEN 3 THEN .4 END) AS calc
                  FROM swa_raw.calificacion WHERE tipo_nota IN (1, 2, 3)
                  GROUP BY 1, 2, 3, 4, 5 HAVING count(*) = 3) c
              USING (periodo, programa, asignatura, grupo, estudiante)
            WHERE r.estado = 'R' AND (c.calc >= p.na) <> (r.definitiva >= p.na)
        """,
    },
    'APROBADA_TRAS_CAMBIO_DE_NOTA': {
        'categoria': 'NOTAS', 'severidad': 'MEDIA', 'responsable': REGISTRO,
        'titulo': 'Aprobada después de un cambio de nota',
        'que_paso': 'La asignatura terminó aprobada y en cambio_nota hay al menos un cambio que pasó la nota de '
                    'reprobatoria a aprobatoria.',
        'riesgo': 'Es válido si hay soporte (acta de corrección); sin soporte es una aprobación dudosa.',
        'recomendacion': 'Registro valida que cada cambio tenga soporte. El detalle trae quién y cuándo lo hizo.',
        'texto': lambda d: f"Definitiva {_nota(d['definitiva'])}. Cambios: {_cambios(d['cambios'])}.",
        'sql': """
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('definitiva', r.definitiva, 'cambios', jsonb_agg(jsonb_build_object(
                       'fecha', cn.fecha, 'tipo_nota', cn.tipo_nota, 'nota', cn.nota, 'nueva_nota', cn.nueva_nota,
                       'descripcion', cn.descripcion, 'por', cn.ingresado_por) ORDER BY cn.codigo))
            FROM swa_raw.registro r
            JOIN swa_raw.programa p ON p.codigo = r.programa
            JOIN swa_raw.cambio_nota cn USING (periodo, programa, asignatura, grupo, estudiante)
            WHERE r.estado = 'R' AND r.estado_definitiva = 'A'
            GROUP BY r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo, r.definitiva
            HAVING bool_or(cn.nota < p.na AND cn.nueva_nota >= p.na)
        """,
    },
    'CAMBIO_NOTA_NO_REFLEJADO': {
        'categoria': 'NOTAS', 'severidad': 'ALTA', 'responsable': REGISTRO,
        'titulo': 'Cambio de nota que no quedó en la definitiva',
        'que_paso': 'El último cambio registrado sobre la definitiva (cambio_nota, tipo 0) dice una nota distinta '
                    'a la que quedó en registro.',
        'riesgo': 'No se sabe cuál nota es la real; al ERP viaja la de registro.',
        'recomendacion': 'Confirmar la nota correcta con Registro.',
        'texto': lambda d: (f"El último cambio ({d.get('fecha_cambio') or '¿fecha?'}, por {d.get('por') or 'sin usuario'}) "
                            f"dejó {_nota(d['nueva_nota_cambio'])}, pero en registro quedó {_nota(d['definitiva'])}."),
        'sql': """
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('definitiva', r.definitiva, 'nueva_nota_cambio', u.nueva_nota,
                                      'fecha_cambio', u.fecha, 'descripcion', u.descripcion, 'por', u.ingresado_por)
            FROM swa_raw.registro r
            JOIN (SELECT DISTINCT ON (periodo, programa, asignatura, grupo, estudiante) *
                  FROM swa_raw.cambio_nota WHERE tipo_nota = 0
                  ORDER BY periodo, programa, asignatura, grupo, estudiante, codigo DESC) u
              USING (periodo, programa, asignatura, grupo, estudiante)
            WHERE r.estado = 'R' AND abs(coalesce(r.definitiva, 0) - u.nueva_nota) > 0.05
        """,
    },
    'NOTA_CERO_PERIODO_CERRADO': {
        'categoria': 'NOTAS', 'severidad': 'INFO', 'responsable': REGISTRO,
        'titulo': 'Reprobada con 0 en un periodo cerrado',
        'que_paso': 'La nota final de la asignatura (grupo regular, sin habilitación ni validación que la '
                    'reemplace) es 0 en un periodo ya cerrado.',
        'riesgo': 'Casi siempre es abandono sin retiro formal; en el ERP queda como reprobada con 0.',
        'recomendacion': 'Informativo. Si fue un retiro, Registro debe cancelarla en SWA.',
        'texto': lambda d: 'Reprobada con 0 (posible abandono sin retiro formal).',
        'sql': f"""
            SELECT estudiante, periodo, programa, asignatura, grupo, jsonb_build_object('log_update', log_update)
            FROM anomalias.aux_final
            WHERE estado_definitiva = 'R' AND coalesce(definitiva, 0) = 0
              AND grupo >= 0 AND periodo < {PERIODO_CORTE}
        """,
    },
    'HABILITACION_SIN_REPROBADA': {
        'categoria': 'NOTAS', 'severidad': 'MEDIA', 'responsable': REGISTRO,
        'titulo': 'Habilitación sin haber reprobado',
        'que_paso': 'Hay una habilitación (grupo -3) y la persona no tiene, con ninguno de sus códigos, la misma '
                    'asignatura (o su equivalente) reprobada en grupo regular en ese periodo.',
        'riesgo': 'Una habilitación solo procede sobre una asignatura reprobada.',
        'recomendacion': 'Verificar el origen de la habilitación.',
        'texto': lambda d: (f"Habilitación con {_nota(d['definitiva'])}; en el grupo regular del periodo "
                            + (f"la asignatura figura {d['grupo_regular']}." if d.get('grupo_regular')
                               else 'no hay ninguna nota de esa asignatura.')),
        'sql': """
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('definitiva', r.definitiva, 'estado_definitiva', r.estado_definitiva,
                                      'grupo_regular', (SELECT string_agg(CASE WHEN x.estado_definitiva = 'A'
                                                                THEN 'aprobada' ELSE 'reprobada' END
                                                                || ' con ' || x.definitiva, ', ')
                                                        FROM anomalias.aux_reg x
                                                        WHERE x.persona = r.persona AND x.periodo = r.periodo
                                                          AND x.asig_c = r.asig_c AND x.grupo >= 0 AND x.estado = 'R'))
            FROM anomalias.aux_reg r
            WHERE r.grupo = -3 AND r.estado = 'R'
              AND NOT EXISTS (SELECT 1 FROM anomalias.aux_reg x
                              WHERE x.persona = r.persona AND x.periodo = r.periodo AND x.asig_c = r.asig_c
                                AND x.grupo >= 0 AND x.estado = 'R' AND x.estado_definitiva = 'R')
        """,
    },
    'APROBADA_MAS_DE_UNA_VEZ': {
        'categoria': 'NOTAS', 'severidad': 'INFO', 'responsable': REGISTRO,
        'titulo': 'Misma asignatura aprobada en varios periodos',
        'que_paso': 'La persona volvió a cursar y aprobar (no homologar) una asignatura, o su equivalente, que ya '
                    'tenía aprobada; se lista cada aprobación posterior a la primera.',
        'riesgo': 'No debió matricularla de nuevo (o pagó dos veces). En el ERP quedan ambos intentos.',
        'recomendacion': 'Informativo. Las homologaciones por cambio de programa no se cuentan.',
        'texto': lambda d: (f"Aprobada de nuevo con {_nota(d['definitiva'])}; ya la había aprobado en "
                            f"{_periodo(d['primera_aprobacion'])}."),
        'sql': f"""
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('definitiva', r.definitiva, 'primera_aprobacion', a.primer)
            FROM anomalias.aux_final r JOIN anomalias.aux_aprob a ON a.persona = r.persona AND a.asig_c = r.asig_c
            WHERE r.estado_definitiva = 'A' AND r.periodo > a.primer AND r.grupo NOT IN {HOMOLOGACION}
        """,
    },
    # ---------------------------------------------------------------------- plan de estudios
    'ASIGNATURA_FUERA_DEL_PENSUM': {
        'categoria': 'PLAN_ESTUDIOS', 'severidad': 'MEDIA', 'responsable': REGISTRO,
        'titulo': 'Asignatura que no está en el pensum del estudiante',
        'que_paso': 'Cursó asignaturas que no pertenecen al pensum de su código (ni por equivalencia). Casi '
                    'siempre es el código con el pensum equivocado, así que va una fila por estudiante y programa '
                    '(periodo = el primero) con todas las asignaturas.',
        'riesgo': 'En el ERP no contarán para su plan de estudios; puede ser un cambio de pensum sin registrar.',
        'recomendacion': 'Registro confirma el pensum real del estudiante.',
        'texto': lambda d: (f"{d['cantidad']} asignatura(s) fuera del pensum {d['pensum_estudiante']}, entre "
                            f"{_periodo(d['desde'])} y {_periodo(d['hasta'])}: {_lista(d['asignaturas'], 6)}."),
        'sql': """
            SELECT r.estudiante, min(r.periodo), r.programa, NULL::int, NULL::int,
                   jsonb_build_object('pensum_estudiante', r.pensum_est, 'cantidad', count(*),
                                      'desde', min(r.periodo), 'hasta', max(r.periodo),
                                      'asignaturas', jsonb_agg(coalesce(a.nombre, r.asignatura::text)
                                                               ORDER BY r.periodo, a.nombre))
            FROM anomalias.aux_final r
            LEFT JOIN swa_raw.asignatura a ON a.codigo = r.asignatura
            WHERE NOT EXISTS (SELECT 1 FROM swa_raw.pensum_asignatura pa
                              LEFT JOIN migra.asignatura ma ON ma.codigo = pa.asignatura
                              WHERE pa.programa = r.programa AND pa.pensum = r.pensum_est
                                AND coalesce(ma.canonica, pa.asignatura) = r.asig_c)
            GROUP BY r.estudiante, r.programa, r.pensum_est
        """,
    },
    'PRERREQUISITO_NO_APROBADO': {
        'categoria': 'PLAN_ESTUDIOS', 'severidad': 'ALTA', 'responsable': REGISTRO,
        'titulo': 'Aprobada sin haber aprobado su prerrequisito',
        'que_paso': 'Cursó y aprobó la asignatura (no por homologación) y nunca aprobó, en ningún periodo, uno o '
                    'más de sus prerrequisitos del pensum. Una fila por asignatura, con todos los que faltan. Los '
                    'módulos de inglés van en "Módulo de inglés aprobado sin el módulo anterior".',
        'riesgo': 'SWA permitió inscribirla sin el requisito; el historial queda incoherente.',
        'recomendacion': 'Registro valida si hubo homologación o autorización no registrada.',
        'texto': lambda d: f"Nunca aprobó: {_lista(d['prerrequisitos_faltantes'])}.",
        'sql': f"""
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('definitiva', r.definitiva, 'prerrequisitos_faltantes',
                                      jsonb_agg(DISTINCT coalesce(a.nombre, pr.prerequisito::text)))
            FROM anomalias.aux_final r
            JOIN swa_raw.prerequisito pr ON pr.programa = r.programa AND pr.pensum = r.pensum_est
                                        AND pr.asignatura = r.asignatura
            LEFT JOIN migra.asignatura ma ON ma.codigo = pr.prerequisito
            LEFT JOIN swa_raw.asignatura a ON a.codigo = pr.prerequisito
            LEFT JOIN anomalias.aux_aprob ap ON ap.persona = r.persona
                                            AND ap.asig_c = coalesce(ma.canonica, pr.prerequisito)
            WHERE r.estado_definitiva = 'A' AND r.grupo NOT IN {HOMOLOGACION} AND ap.persona IS NULL
              AND r.programa <> ALL({CENTROS})
            GROUP BY r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo, r.definitiva
        """,
    },
    'PRERREQUISITO_APROBADO_DESPUES': {
        'categoria': 'PLAN_ESTUDIOS', 'severidad': 'INFO', 'responsable': REGISTRO,
        'titulo': 'Prerrequisito aprobado después',
        'que_paso': 'Cursó y aprobó la asignatura (no por homologación) antes que su prerrequisito, que aprobó en '
                    'un periodo posterior.',
        'riesgo': 'Secuencia irregular; el requisito quedó cumplido pero tarde.',
        'recomendacion': 'Informativo.',
        'texto': lambda d: 'Prerrequisito aprobado después: ' + _lista(
            [f"{p['nombre']} ({_periodo(p['aprobado_en'])})" for p in d['prerrequisitos']]) + '.',
        'sql': f"""
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('prerrequisitos', jsonb_agg(jsonb_build_object(
                       'nombre', coalesce(a.nombre, pr.prerequisito::text), 'aprobado_en', ap.primer)))
            FROM anomalias.aux_final r
            JOIN swa_raw.prerequisito pr ON pr.programa = r.programa AND pr.pensum = r.pensum_est
                                        AND pr.asignatura = r.asignatura
            LEFT JOIN migra.asignatura ma ON ma.codigo = pr.prerequisito
            LEFT JOIN swa_raw.asignatura a ON a.codigo = pr.prerequisito
            JOIN anomalias.aux_aprob ap ON ap.persona = r.persona
                                       AND ap.asig_c = coalesce(ma.canonica, pr.prerequisito)
            WHERE r.estado_definitiva = 'A' AND r.grupo NOT IN {HOMOLOGACION} AND ap.primer > r.periodo
            GROUP BY r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo
        """,
    },
    # ------------------------------------------------------------------------------ grados
    'GRADUADO_CON_PENDIENTES': {
        'categoria': 'GRADO', 'severidad': 'ALTA', 'responsable': REGISTRO,
        'titulo': 'Graduado con asignaturas del pensum sin aprobar',
        'que_paso': 'Tiene grado registrado, tiene historial en SWA y hay asignaturas de su pensum que nunca aprobó '
                    '(ni por equivalencia). Muchos pendientes suelen indicar el pensum equivocado en el código.',
        'riesgo': 'El ERP mostrará un plan incompleto para un egresado.',
        'recomendacion': 'Registro confirma si hubo homologaciones u opciones de grado no registradas.',
        'texto': lambda d: (f"{'Acta ' + str(d['acta']) if d.get('acta') else 'Sin acta'}. "
                            f"{d['cantidad']} sin aprobar: {_lista(d['pendientes'])}."),
        'sql': """
            SELECT g.estudiante, g.periodo, g.programa, NULL::int, NULL::int,
                   jsonb_build_object('acta', g.acta, 'pendientes', jsonb_agg(a.nombre ORDER BY pa.nivel, a.nombre),
                                      'cantidad', count(*))
            FROM swa_raw.grado g
            JOIN anomalias.aux_estudiante e ON e.codigo = g.estudiante
            JOIN swa_raw.pensum_asignatura pa ON pa.programa = e.programa AND pa.pensum = e.pensum
            JOIN swa_raw.asignatura a ON a.codigo = pa.asignatura
            LEFT JOIN migra.asignatura ma ON ma.codigo = pa.asignatura
            WHERE NOT EXISTS (SELECT 1 FROM anomalias.aux_aprob ap
                              WHERE ap.persona = e.persona AND ap.asig_c = coalesce(ma.canonica, pa.asignatura))
              AND EXISTS (SELECT 1 FROM anomalias.aux_reg r WHERE r.persona = e.persona AND r.estado = 'R')
            GROUP BY g.estudiante, g.periodo, g.programa, g.acta
        """,
    },
    'GRADUADO_SIN_HISTORIAL': {
        'categoria': 'GRADO', 'severidad': 'ALTA', 'responsable': REGISTRO,
        'titulo': 'Graduado sin ninguna nota en SWA',
        'que_paso': 'Tiene grado registrado y la persona no tiene ni una sola asignatura calificada en SWA con '
                    'ninguno de sus códigos: el grado no tiene soporte académico en el sistema.',
        'riesgo': 'El ERP recibe un egresado sin historial. O el historial está en otro sistema (anterior a SWA, '
                  'transferencia) o el grado se registró sin soporte.',
        'recomendacion': 'Registro ubica el acta y el historial físico del egresado.',
        'texto': lambda d: (f"{'Acta ' + str(d['acta']) if d.get('acta') else 'Sin acta'}; "
                            f"histórico {d.get('historico') or 'sin fila'}; ninguna nota en SWA."),
        'sql': """
            SELECT g.estudiante, g.periodo, g.programa, NULL::int, NULL::int,
                   jsonb_build_object('acta', g.acta,
                                      'historico', (SELECT string_agg(DISTINCT h.estado, '/') FROM swa_raw.historico h
                                                    WHERE h.estudiante = g.estudiante))
            FROM swa_raw.grado g
            JOIN anomalias.aux_estudiante e ON e.codigo = g.estudiante
            WHERE NOT EXISTS (SELECT 1 FROM anomalias.aux_reg r WHERE r.persona = e.persona AND r.estado = 'R')
        """,
    },
    'NOTA_DESPUES_DEL_GRADO': {
        'categoria': 'GRADO', 'severidad': 'MEDIA', 'responsable': REGISTRO,
        'titulo': 'Notas en periodos posteriores al grado',
        'que_paso': 'Tiene asignaturas calificadas en el mismo programa en periodos posteriores al de su grado. '
                    'Una fila por graduado (periodo = el primero posterior al grado).',
        'riesgo': 'O el periodo de grado está mal o son notas cargadas al código equivocado.',
        'recomendacion': 'Verificar el periodo de grado.',
        'texto': lambda d: (f"Se graduó en {_periodo(d['periodo_grado'])} y tiene {d['cantidad']} nota(s) "
                            f"posteriores: {_lista(d['asignaturas'], 6)}."),
        'sql': """
            SELECT r.estudiante, min(r.periodo), r.programa, NULL::int, NULL::int,
                   jsonb_build_object('periodo_grado', g.periodo, 'cantidad', count(*),
                                      'asignaturas', jsonb_agg(coalesce(a.nombre, r.asignatura::text)
                                                               || ' (' || (r.periodo / 10) || '-' || mod(r.periodo, 10) || ')'
                                                               ORDER BY r.periodo, a.nombre))
            FROM swa_raw.registro r
            JOIN swa_raw.grado g ON g.estudiante = r.estudiante AND g.programa = r.programa
            LEFT JOIN swa_raw.asignatura a ON a.codigo = r.asignatura
            WHERE r.estado = 'R' AND r.periodo > g.periodo
            GROUP BY r.estudiante, r.programa, g.periodo
        """,
    },
    # ---------------------------------------------------------------------- matricula (historico)
    'PREMATRICULA_CON_NOTAS_APROBADAS': {
        'categoria': 'MATRICULA', 'severidad': 'ALTA', 'responsable': REGISTRO,
        'titulo': 'Aprobó asignaturas sin matrícula legalizada ni pago',
        'que_paso': 'En historico el periodo quedó en P (prematrícula, nunca pasó a M, tampoco con otro código de '
                    'la persona), aprobó asignaturas cursadas en grupo regular y no hay ningún pago de la matrícula '
                    '(ni por recibo, ni por crédito, ni beneficio). Antes de 2017-1 no hay soporte financiero. '
                    'No incluye el Centro de Idiomas (tiene sus propias reglas) ni homologaciones.',
        'riesgo': 'Estudió sin matrícula legalizada y sin pagar. La migración crea la matrícula si se decide incluir '
                  '(caso "Notas en un periodo sin matrícula").',
        'recomendacion': 'Financiera y Registro deciden si se reconoce el periodo.',
        'texto': lambda d: (f"{d['aprobadas']} aprobada(s) con el periodo en prematrícula: {_lista(d['asignaturas'], 5)}. "
                            + _pago(d)),
        'sql': SQL_PREMATRICULA.format(condicion="<> 'SI'"),
    },
    'PREMATRICULA_PAGADA_SIN_LEGALIZAR': {
        'categoria': 'MATRICULA', 'severidad': 'MEDIA', 'responsable': REGISTRO,
        'titulo': 'Pagó y aprobó, pero la matrícula no se legalizó',
        'que_paso': 'En historico el periodo quedó en P (prematrícula) y aprobó asignaturas en grupo regular, pero la '
                    'matrícula sí se pagó (recibo, crédito o beneficio). Es un error administrativo: faltó pasar a M.',
        'riesgo': 'Bajo: hay soporte de pago. La migración crea la matrícula si se decide incluir.',
        'recomendacion': 'Registro legaliza la matrícula (pasar a M) antes del corte.',
        'texto': lambda d: (f"{d['aprobadas']} aprobada(s) con el periodo en prematrícula: {_lista(d['asignaturas'], 5)}. "
                            + _pago(d)),
        'sql': SQL_PREMATRICULA.format(condicion="= 'SI'"),
    },
    'MATRICULA_SIN_NOTAS': {
        'categoria': 'MATRICULA', 'severidad': 'MEDIA', 'responsable': REGISTRO,
        'titulo': 'Matriculado (M) sin ninguna asignatura',
        'que_paso': 'historico dice M (matriculado) y la persona no tiene ninguna fila en registro para ese periodo '
                    'y programa con ninguno de sus códigos.',
        'riesgo': 'Matrícula vacía: pagó/legalizó y no tiene carga académica (o se borró).',
        'recomendacion': 'Registro confirma si cursó; si se retiró, documentar el retiro.',
        'texto': lambda d: 'Matriculado sin ninguna asignatura. ' + _pago(d),
        'sql': f"""
            SELECT DISTINCT ON (e.persona, h.periodo, h.programa) h.estudiante, h.periodo, h.programa,
                   NULL::int, NULL::int,
                   jsonb_build_object('pago', CASE WHEN h.periodo < {PRIMER_PERIODO_FINANCIERO} THEN 'SIN_DATOS'
                                                   WHEN pg.pagado THEN 'SI' ELSE 'NO' END,
                                      'recibos', pg.recibos)
            FROM swa_raw.historico h
            JOIN anomalias.aux_estudiante e ON e.codigo = h.estudiante
            LEFT JOIN anomalias.aux_pago pg ON pg.persona = e.persona AND pg.periodo = h.periodo
                                           AND pg.programa = h.programa
            WHERE h.estado = 'M'
              AND NOT EXISTS (SELECT 1 FROM anomalias.aux_reg r WHERE r.persona = e.persona
                              AND r.periodo = h.periodo AND r.programa = h.programa)
            ORDER BY e.persona, h.periodo, h.programa, h.estudiante
        """,
    },
    'NOTAS_SIN_HISTORICO': {
        'categoria': 'MATRICULA', 'severidad': 'ALTA', 'responsable': REGISTRO,
        'titulo': 'Notas sin ningún registro en historico',
        'que_paso': 'Hay asignaturas calificadas en grupo regular en un periodo en que la persona no tiene fila en '
                    'historico (ni M ni P) con ninguno de sus códigos.',
        'riesgo': 'Notas sin matrícula de ningún tipo.',
        'recomendacion': 'Registro valida el periodo.',
        'texto': lambda d: (f"{d['asignaturas']} asignatura(s) calificada(s), {d['aprobadas']} aprobada(s), sin "
                            f"matrícula ni prematrícula. " + _pago(d)),
        'sql': f"""
            SELECT min(r.estudiante), r.periodo, r.programa, NULL::int, NULL::int,
                   jsonb_build_object('asignaturas', count(*),
                                      'aprobadas', count(*) FILTER (WHERE r.estado_definitiva = 'A'),
                                      'pago', CASE WHEN r.periodo < {PRIMER_PERIODO_FINANCIERO} THEN 'SIN_DATOS'
                                                   WHEN bool_or(pg.pagado) THEN 'SI' ELSE 'NO' END,
                                      'recibos', min(pg.recibos::text)::jsonb)
            FROM anomalias.aux_reg r
            LEFT JOIN anomalias.aux_pago pg ON pg.persona = r.persona AND pg.periodo = r.periodo
                                           AND pg.programa = r.programa
            WHERE r.estado = 'R' AND r.grupo >= 0
              AND NOT EXISTS (SELECT 1 FROM anomalias.aux_hist h WHERE h.persona = r.persona
                              AND h.periodo = r.periodo AND h.programa = r.programa)
            GROUP BY r.persona, r.periodo, r.programa
        """,
    },
    'HISTORICO_ESTADO_DESCONOCIDO': {
        'categoria': 'MATRICULA', 'severidad': 'BAJA', 'responsable': REGISTRO,
        'titulo': 'Estado de matrícula no documentado',
        'que_paso': 'historico tiene un estado distinto de M (matriculado) y P (prematrícula).',
        'riesgo': 'No se sabe qué significa; la depuración no lo toma como matrícula.',
        'recomendacion': 'Confirmar el significado con Registro.',
        'texto': lambda d: (f"Estado «{d.get('estado') or 'vacío'}» en historico."
                            + (f" Observación: {d['observacion']}" if d.get('observacion') else '')),
        'sql': """
            SELECT estudiante, periodo, programa, NULL::int, NULL::int,
                   jsonb_build_object('estado', estado, 'observacion', observacion)
            FROM swa_raw.historico WHERE estado NOT IN ('M', 'P') OR estado IS NULL
        """,
    },
    'MATRICULA_DUPLICADA_MISMO_PERIODO': {
        'categoria': 'MATRICULA', 'severidad': 'MEDIA', 'responsable': FINANCIERA,
        'titulo': 'Dos matrículas en el mismo programa y periodo',
        'que_paso': 'La misma persona quedó matriculada (M) varias veces en el mismo programa y periodo con códigos '
                    'distintos. Una fila por persona y periodo.',
        'riesgo': 'Doble cobro o doble código; la migración las unifica en una matrícula.',
        'recomendacion': 'Financiera verifica que no haya doble cobro.',
        'texto': lambda d: f"Matriculado con {len(d['codigos'])} códigos: {_lista(d['codigos'])}.",
        'sql': """
            SELECT min(h.estudiante), h.periodo, h.programa, NULL::int, NULL::int,
                   jsonb_build_object('codigos', jsonb_agg(h.estudiante ORDER BY h.estudiante))
            FROM swa_raw.historico h JOIN anomalias.aux_estudiante e ON e.codigo = h.estudiante
            WHERE h.estado = 'M'
            GROUP BY e.persona, h.periodo, h.programa HAVING count(*) > 1
        """,
    },
    # ------------------------------------------------------------------------------ pagos
    'MATRICULA_SIN_RECIBO': {
        'categoria': 'PAGOS', 'severidad': 'ALTA', 'responsable': FINANCIERA,
        'titulo': 'Matriculado sin recibo de matrícula',
        'que_paso': f'historico dice M y la persona no tiene ningún recibo de matrícula de ese programa en el '
                    f'periodo, con ninguno de sus códigos, ni pagó la matrícula de otro programa ese periodo (se '
                    f'revisa desde {_periodo(PRIMER_PERIODO_FINANCIERO)}, cuando SWA empezó a registrar recibos).',
        'riesgo': 'Matrícula sin soporte de pago. Si aprobó asignaturas, quedan en el ERP sin soporte financiero.',
        'recomendacion': 'Financiera confirma si fue beca, convenio o exoneración no registrada.',
        'texto': lambda d: (f"Sin recibo de matrícula del programa. Aprobó {d['aprobadas']} asignatura(s)."
                            + (f" Recibos de la persona en otros programas ese periodo: {_recibos(d['otros_recibos'])}."
                               if d.get('otros_recibos') else '')),
        'sql': f"""
            SELECT DISTINCT ON (e.persona, h.periodo, h.programa) h.estudiante, h.periodo, h.programa,
                   NULL::int, NULL::int,
                   jsonb_build_object(
                       'aprobadas', (SELECT count(*) FROM anomalias.aux_final f WHERE f.persona = e.persona
                                     AND f.periodo = h.periodo AND f.programa = h.programa
                                     AND f.estado_definitiva = 'A' AND f.grupo NOT IN {HOMOLOGACION}),
                       'otros_recibos', (SELECT jsonb_agg(x ORDER BY (x->>'numero')::int)
                                         FROM anomalias.aux_pago o, jsonb_array_elements(o.recibos) x
                                         WHERE o.persona = e.persona AND o.periodo = h.periodo))
            FROM swa_raw.historico h
            JOIN anomalias.aux_estudiante e ON e.codigo = h.estudiante
            WHERE h.estado = 'M' AND h.periodo >= {PRIMER_PERIODO_FINANCIERO}
              AND NOT EXISTS (SELECT 1 FROM anomalias.aux_pago pg WHERE pg.persona = e.persona
                              AND pg.periodo = h.periodo AND pg.programa = h.programa)
              AND NOT EXISTS (SELECT 1 FROM anomalias.aux_pago o WHERE o.persona = e.persona AND o.periodo = h.periodo
                              AND o.programa <> h.programa AND o.programa <> ALL({CENTROS}) AND o.pagado)
            ORDER BY e.persona, h.periodo, h.programa, h.estudiante
        """,
    },
    'MATRICULA_RECIBO_SIN_PAGAR': {
        'categoria': 'PAGOS', 'severidad': 'ALTA', 'responsable': FINANCIERA,
        'titulo': 'Matriculado con el recibo sin pagar o anulado',
        'que_paso': 'historico dice M y hay recibo de matrícula, pero ninguno de la persona en ese programa y '
                    'periodo quedó cubierto: ni pagado por estado, ni con pagos registrados, ni por cuotas de '
                    'crédito, ni por beneficio del 100 %; tampoco pagó la matrícula de otro programa ese periodo.',
        'riesgo': 'Quedó matriculado sin pagar. Si aprobó asignaturas, quedan en el ERP sin soporte financiero.',
        'recomendacion': 'Financiera revisa la cartera de ese periodo.',
        'texto': lambda d: f"Recibos: {_recibos(d['recibos'])}. Aprobó {d['aprobadas']} asignatura(s).",
        'sql': f"""
            SELECT DISTINCT ON (e.persona, h.periodo, h.programa) h.estudiante, h.periodo, h.programa,
                   NULL::int, NULL::int,
                   jsonb_build_object(
                       'recibos', pg.recibos,
                       'aprobadas', (SELECT count(*) FROM anomalias.aux_final f WHERE f.persona = e.persona
                                     AND f.periodo = h.periodo AND f.programa = h.programa
                                     AND f.estado_definitiva = 'A' AND f.grupo NOT IN {HOMOLOGACION}))
            FROM swa_raw.historico h
            JOIN anomalias.aux_estudiante e ON e.codigo = h.estudiante
            JOIN anomalias.aux_pago pg ON pg.persona = e.persona AND pg.periodo = h.periodo
                                      AND pg.programa = h.programa
            WHERE h.estado = 'M' AND h.periodo >= {PRIMER_PERIODO_FINANCIERO} AND NOT pg.pagado
              AND NOT EXISTS (SELECT 1 FROM anomalias.aux_pago o WHERE o.persona = e.persona AND o.periodo = h.periodo
                              AND o.programa <> h.programa AND o.programa <> ALL({CENTROS}) AND o.pagado)
            ORDER BY e.persona, h.periodo, h.programa, h.estudiante
        """,
    },
    'MATRICULA_PAGADA_EN_OTRO_PROGRAMA': {
        'categoria': 'PAGOS', 'severidad': 'BAJA', 'responsable': FINANCIERA,
        'titulo': 'Matrícula pagada con el recibo de otro programa',
        'que_paso': 'historico dice M en un programa sin recibo cubierto, pero ese periodo la persona pagó la '
                    'matrícula de otro programa. Casi siempre es un programa renombrado (p. ej. 30 Gerencia SST '
                    'pagado como 26 Gerencia Salud Ocupacional; 22 pagado como 28) o inglés pagado con la carrera.',
        'riesgo': 'Bajo si es el mismo programa con otro código; si son dos carreras distintas, falta un pago.',
        'recomendacion': 'Financiera confirma que el recibo del otro programa corresponde a esta matrícula.',
        'texto': lambda d: f"Pagó con el recibo de otro programa: {_recibos(d['recibos_otro_programa'])}.",
        'sql': f"""
            SELECT DISTINCT ON (e.persona, h.periodo, h.programa) h.estudiante, h.periodo, h.programa,
                   NULL::int, NULL::int,
                   jsonb_build_object('recibos_otro_programa',
                       (SELECT jsonb_agg(x || jsonb_build_object('programa', o.programa) ORDER BY (x->>'numero')::int)
                        FROM anomalias.aux_pago o, jsonb_array_elements(o.recibos) x
                        WHERE o.persona = e.persona AND o.periodo = h.periodo AND o.programa <> h.programa
                          AND o.programa <> ALL({CENTROS}) AND x->>'cubierto_por' IS NOT NULL))
            FROM swa_raw.historico h
            JOIN anomalias.aux_estudiante e ON e.codigo = h.estudiante
            WHERE h.estado = 'M' AND h.periodo >= {PRIMER_PERIODO_FINANCIERO}
              AND NOT EXISTS (SELECT 1 FROM anomalias.aux_pago pg WHERE pg.persona = e.persona
                              AND pg.periodo = h.periodo AND pg.programa = h.programa AND pg.pagado)
              AND NOT NOT EXISTS (SELECT 1 FROM anomalias.aux_pago o WHERE o.persona = e.persona AND o.periodo = h.periodo
                              AND o.programa <> h.programa AND o.programa <> ALL({CENTROS}) AND o.pagado)
            ORDER BY e.persona, h.periodo, h.programa, h.estudiante
        """,
    },
    'CURSO_Y_REPROBO_SIN_PAGO': {
        'categoria': 'PAGOS', 'severidad': 'MEDIA', 'responsable': FINANCIERA,
        'titulo': 'Cursó y reprobó sin pagar',
        'que_paso': 'En un periodo cerrado la persona cursó asignaturas o un módulo de inglés en grupo regular, no '
                    'aprobó ninguna y no hay ningún pago: ni recibo de matrícula cubierto (por estado, pago, crédito '
                    'o beneficio; para inglés vale también el de la carrera), ni un curso pagado por otro concepto '
                    '(vacacional, nivelación, créditos adicionales, diplomado). Quien está en M va en "Matriculado '
                    'sin recibo"; quien aprobó, en "Inglés sin pago" o en "Prematrícula".',
        'riesgo': 'Ocupó un cupo sin pagar. No llega al ERP como aprobado, pero es cartera no cobrada.',
        'recomendacion': 'Financiera decide si se cobra; Registro confirma si realmente asistió.',
        'texto': lambda d: (f"Cursó {d['cursadas']} sin aprobar ({_lista(d['asignaturas'], 4)}); histórico "
                            f"{d.get('historico') or 'sin fila'}. Recibos del periodo: {_recibos(d.get('recibos'))}."),
        'sql': SQL_CURSADO_SIN_PAGO.format(
            condicion=f"c.periodo < {PERIODO_CORTE} AND (c.detalle->>'aprobadas')::int = 0"),
    },
    'CURSANDO_SIN_PAGO_PERIODO_ACTUAL': {
        'categoria': 'PAGOS', 'severidad': 'MEDIA', 'responsable': FINANCIERA,
        'titulo': 'Cursando el periodo actual sin pago',
        'que_paso': f'En el periodo en curso ({_periodo(PERIODO_CORTE)}) la persona ya tiene asignaturas o módulo de '
                    f'inglés inscritos en grupo regular, no está en M y no hay ningún pago (mismos criterios que '
                    f'"Cursó y reprobó sin pagar").',
        'riesgo': 'Si no paga antes del corte, entra al ERP cursando sin matrícula pagada.',
        'recomendacion': 'Financiera hace seguimiento antes de la ventana de corte.',
        'texto': lambda d: (f"Inscrito en {d['cursadas']} ({_lista(d['asignaturas'], 4)}); histórico "
                            f"{d.get('historico') or 'sin fila'}. Recibos del periodo: {_recibos(d.get('recibos'))}."),
        'sql': SQL_CURSADO_SIN_PAGO.format(condicion=f"c.periodo >= {PERIODO_CORTE}"),
    },
    'MATRICULA_PAGO_PARCIAL': {
        'categoria': 'PAGOS', 'severidad': 'MEDIA', 'responsable': FINANCIERA,
        'titulo': 'Recibo de matrícula marcado pagado con un valor menor',
        'que_paso': 'El recibo de matrícula está pagado (estado 10) pero el valor pagado es menor al 90 % del valor '
                    'a pagar (el menor entre pronto pago y ordinario).',
        'riesgo': 'Saldo pendiente no registrado como deuda.',
        'recomendacion': 'Financiera valida si hubo descuento o abono no registrado.',
        'texto': lambda d: (f"Recibo N.º {d['recibo']}: ordinario {_pesos(d['valor_ordinaria'])}, pronto pago "
                            f"{_pesos(d['valor_pronto'])}, pagado {_pesos(d['valor_pagado'])}."),
        'sql': f"""
            SELECT x.estudiante, x.periodo, x.programa, NULL::int, NULL::int,
                   jsonb_build_object('recibo', x.numero, 'valor_ordinaria', x.valor_ordinaria,
                                      'valor_pronto', x.valor_pronto, 'valor_pagado', x.valor_pagado)
            FROM swa_raw.recibo_swa x
            JOIN anomalias.aux_conc c ON c.concepto = x.concepto_cambio
            WHERE x.estado = 10 AND x.periodo >= {PRIMER_PERIODO_FINANCIERO}
              AND x.valor_pagado < 0.9 * least(x.valor_ordinaria, coalesce(nullif(x.valor_pronto, 0), x.valor_ordinaria))
              AND EXISTS (SELECT 1 FROM swa_raw.historico h WHERE h.periodo = x.periodo AND h.programa = x.programa
                          AND h.estudiante = x.estudiante AND h.estado = 'M')
        """,
    },
    'MATRICULA_FINANCIADA_CON_SALDO': {
        'categoria': 'PAGOS', 'severidad': 'MEDIA', 'responsable': FINANCIERA,
        'titulo': 'Matrícula financiada con cuotas vencidas sin pagar',
        'que_paso': 'La matrícula se financió con crédito (recibo en estado 1, o en 0 con crédito) y a las cuotas '
                    'de dinero ya vencidas (del estudiante o de un tercero: ICETEX, convenio, caja) les falta más de '
                    '$50.000 y más del 10 % de su valor. Las cuotas de descuento, saldo a favor y ajuste no son '
                    'cartera y no se cuentan.',
        'riesgo': 'Cartera pendiente. El ERP lo verá como matrícula pagada.',
        'recomendacion': 'Validar con Financiera (los estados de cuota se infirieron de los datos).',
        'texto': lambda d: (f"Recibo N.º {d['recibo']} financiado (crédito {_lista(d['creditos'])}): cuotas vencidas "
                            f"{_pesos(d['vencido'])}, pagado {_pesos(d['pagado'])}, saldo {_pesos(d['saldo'])}."),
        'sql': f"""
            SELECT x.estudiante, x.periodo, x.programa, NULL::int, NULL::int,
                   jsonb_build_object('recibo', x.numero, 'creditos', s.creditos,
                                      'vencido', s.vencido, 'pagado', s.pagado, 'saldo', s.vencido - s.pagado)
            FROM swa_raw.recibo_swa x
            JOIN anomalias.aux_conc c ON c.concepto = x.concepto_cambio
            JOIN (SELECT cr.recibo, jsonb_agg(DISTINCT cr.numero) AS creditos,
                         sum(q.valor) FILTER (WHERE q.estado <> 2 AND q.fecha < {HOY}) AS vencido,
                         sum(coalesce(q.valor_pagado, 0)) FILTER (WHERE q.fecha < {HOY}) AS pagado
                  FROM swa_raw.credito cr JOIN swa_raw.cuota q ON q.credito = cr.numero
                  JOIN anomalias.aux_clase_cuota cc ON cc.tipo = q.tipo AND cc.clase IN ('ESTUDIANTE', 'TERCERO')
                  GROUP BY cr.recibo) s ON s.recibo = x.numero
            WHERE x.estado IN (0, 1) AND s.vencido - s.pagado > greatest(50000, 0.1 * s.vencido)
              AND EXISTS (SELECT 1 FROM swa_raw.historico h WHERE h.periodo = x.periodo AND h.programa = x.programa
                          AND h.estudiante = x.estudiante AND h.estado = 'M')
        """,
    },
    'HABILITACION_SIN_PAGO': {
        'categoria': 'PAGOS', 'severidad': 'MEDIA', 'responsable': FINANCIERA,
        'titulo': 'Habilitación sin recibo pagado',
        'que_paso': 'Tiene una habilitación (grupo -3) y la persona no tiene recibo de HABILITACION pagado en ese '
                    'periodo con ninguno de sus códigos.',
        'riesgo': 'Derecho académico sin pagar.',
        'recomendacion': 'Financiera valida.',
        'texto': lambda d: (f"Habilitación con {_nota(d['definitiva'])} ({_aprobado(d['estado_definitiva'])}) sin "
                            f"recibo de habilitación pagado en el periodo."),
        'sql': f"""
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('definitiva', r.definitiva, 'estado_definitiva', r.estado_definitiva)
            FROM anomalias.aux_reg r
            WHERE r.grupo = -3 AND r.estado = 'R' AND r.periodo >= {PRIMER_PERIODO_FINANCIERO}
              AND NOT EXISTS (SELECT 1 FROM swa_raw.recibo_swa x JOIN anomalias.aux_estudiante e ON e.codigo = x.estudiante
                              WHERE e.persona = r.persona AND x.periodo = r.periodo AND x.estado IN (1, 10)
                                AND x.concepto_cambio IN {CONCEPTO_HABILITACION + (-99,)})
        """,
    },
    'VALIDACION_SIN_PAGO': {
        'categoria': 'PAGOS', 'severidad': 'MEDIA', 'responsable': FINANCIERA,
        'titulo': 'Validación sin recibo pagado',
        'que_paso': 'Tiene una validación (grupo -2) y la persona no tiene recibo de VALIDACION pagado en ese '
                    'periodo con ninguno de sus códigos.',
        'riesgo': 'Derecho académico sin pagar.',
        'recomendacion': 'Financiera valida.',
        'texto': lambda d: (f"Validación con {_nota(d['definitiva'])} ({_aprobado(d['estado_definitiva'])}) sin "
                            f"recibo de validación pagado en el periodo."),
        'sql': f"""
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('definitiva', r.definitiva, 'estado_definitiva', r.estado_definitiva)
            FROM anomalias.aux_reg r
            WHERE r.grupo = -2 AND r.estado = 'R' AND r.periodo >= {PRIMER_PERIODO_FINANCIERO}
              AND NOT EXISTS (SELECT 1 FROM swa_raw.recibo_swa x JOIN anomalias.aux_estudiante e ON e.codigo = x.estudiante
                              WHERE e.persona = r.persona AND x.periodo = r.periodo AND x.estado IN (1, 10)
                                AND x.concepto_cambio IN {CONCEPTOS_VALIDACION})
        """,
    },
    # ------------------------------------------------------------------------------ ingles
    'INGLES_MODULO_SIN_ANTERIOR': {
        'categoria': 'INGLES', 'severidad': 'ALTA', 'responsable': IDIOMAS,
        'titulo': 'Módulo de inglés aprobado sin el módulo anterior',
        'que_paso': 'Aprobó el módulo N del Centro de Idiomas sin haber aprobado (ni homologado) el módulo N-1 en '
                    'ese periodo o antes (en cualquiera de las dos sedes).',
        'riesgo': 'Salto de nivel sin soporte (¿examen de clasificación no registrado?).',
        'recomendacion': 'Centro de Idiomas confirma si hubo prueba de clasificación; si la hubo, se acepta.',
        'texto': lambda d: (f"Aprobó el módulo {d['modulo']} con {_nota(d['definitiva'])} sin tener el módulo "
                            f"{d['modulo'] - 1}."),
        'sql': """
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('modulo', r.modulo, 'definitiva', r.definitiva)
            FROM anomalias.aux_ingles r
            WHERE r.aprobada AND r.modulo > 1
              AND NOT EXISTS (SELECT 1 FROM anomalias.aux_ingles x WHERE x.persona = r.persona AND x.aprobada
                              AND x.modulo = r.modulo - 1 AND x.periodo <= r.periodo)
        """,
    },
    'INGLES_MODULO_SIN_PAGO': {
        'categoria': 'INGLES', 'severidad': 'ALTA', 'responsable': IDIOMAS,
        'titulo': 'Módulo de inglés aprobado sin pago',
        'que_paso': f'Cursó y aprobó (no homologó) un módulo del Centro de Idiomas en un periodo en que la persona '
                    f'no tiene ningún recibo de matrícula pagado ni financiado, ni del Centro ni de una carrera, con '
                    f'ninguno de sus códigos (desde {_periodo(PRIMER_PERIODO_FINANCIERO)}).',
        'riesgo': 'El ERP lo dará por completado (homologación MIGRACION_SWA) sin soporte de pago.',
        'recomendacion': 'Centro de Idiomas y Financiera confirman beca o convenio.',
        'texto': lambda d: (f"Módulo {d['modulo']} aprobado con {_nota(d['definitiva'])} sin pago en el periodo. "
                            f"Recibos del Centro: {_recibos(d.get('recibos'))}."),
        'sql': f"""
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('modulo', r.modulo, 'definitiva', r.definitiva, 'recibos', pi.recibos)
            FROM anomalias.aux_ingles r
            LEFT JOIN anomalias.aux_pago_ingles pi ON pi.persona = r.persona AND pi.periodo = r.periodo
            WHERE r.aprobada AND NOT r.homologada AND r.grupo <> -2 AND r.periodo >= {PRIMER_PERIODO_FINANCIERO}
              AND NOT coalesce(pi.pagado, false)
              AND NOT EXISTS (SELECT 1 FROM anomalias.aux_pago_carrera pc
                              WHERE pc.persona = r.persona AND pc.periodo = r.periodo)
        """,
    },
    'INGLES_PAGO_CON_MATRICULA_DE_CARRERA': {
        'categoria': 'INGLES', 'severidad': 'BAJA', 'responsable': IDIOMAS,
        'titulo': 'Módulo de inglés sin recibo del Centro, con carrera pagada',
        'que_paso': 'Cursó y aprobó (no homologó) un módulo del Centro de Idiomas sin recibo de inglés pagado, pero '
                    'ese periodo pagó la matrícula de su carrera. Suele ser inglés incluido en la carrera; el periodo '
                    'en el Centro casi siempre queda en prematrícula (P).',
        'riesgo': 'Bajo, si el inglés estaba incluido. Si no, es un módulo sin pagar.',
        'recomendacion': 'Centro de Idiomas confirma la política (inglés incluido en la matrícula de carrera).',
        'texto': lambda d: (f"Módulo {d['modulo']} aprobado con {_nota(d['definitiva'])}, sin recibo del Centro. "
                            f"Matrícula de carrera pagada: {_recibos(d['pagos_carrera'])}."),
        'sql': f"""
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('modulo', r.modulo, 'definitiva', r.definitiva, 'pagos_carrera', pc.recibos)
            FROM anomalias.aux_ingles r
            LEFT JOIN anomalias.aux_pago_ingles pi ON pi.persona = r.persona AND pi.periodo = r.periodo
            JOIN anomalias.aux_pago_carrera pc ON pc.persona = r.persona AND pc.periodo = r.periodo
            WHERE r.aprobada AND NOT r.homologada AND r.grupo <> -2 AND r.periodo >= {PRIMER_PERIODO_FINANCIERO}
              AND NOT coalesce(pi.pagado, false)
        """,
    },
    'INGLES_PAGO_DE_OTRO_MODULO': {
        'categoria': 'INGLES', 'severidad': 'INFO', 'responsable': IDIOMAS,
        'titulo': 'Pagó un módulo de inglés distinto al que cursó',
        'que_paso': 'En el periodo la persona tiene recibo de inglés pagado, pero ningún concepto corresponde al '
                    'nivel que cursó (p. ej. pagó INGLES I y cursó INGLES III). No incluye homologaciones.',
        'riesgo': 'El recibo o la inscripción quedaron mal; pudo haber diferencia de valor.',
        'recomendacion': 'Informativo: Financiera reutiliza conceptos (p. ej. INGLES REG.(VI) para otros niveles), '
                         'así que el concepto no siempre indica el nivel.',
        'texto': lambda d: (f"Cursó el módulo {d['modulo_cursado']} y pagó el/los módulo(s) "
                            f"{_lista(d['modulos_pagados'])}."),
        'sql': """
            SELECT r.estudiante, r.periodo, r.programa, r.asignatura, r.grupo,
                   jsonb_build_object('modulo_cursado', r.modulo, 'modulos_pagados', pi.niveles_ingles)
            FROM anomalias.aux_ingles r
            JOIN anomalias.aux_pago_ingles pi ON pi.persona = r.persona AND pi.periodo = r.periodo
            WHERE NOT r.homologada AND r.grupo <> -2 AND pi.pagado AND cardinality(pi.niveles_ingles) > 0
              AND NOT r.modulo = ANY(pi.niveles_ingles)
        """,
    },
    # ----------------------------------------------------------------- saldos a favor y beneficios
    'SALDO_POR_BENEFICIO': {
        'categoria': 'SALDOS', 'severidad': 'ALTA', 'responsable': FINANCIERA,
        'titulo': 'Beneficio que dejó la matrícula cubierta por encima del 100 %',
        'que_paso': 'El recibo tiene beneficios (auxilios del recibo y/o cuotas de descuento del crédito) y lo '
                    'cubierto superó el valor de la matrícula, generando saldo a favor. Gravedad: 1 = los beneficios '
                    'solos suman más del 100 %; 2 = beneficio del 100 % y además alguien pagó; 3 = beneficio parcial y '
                    'luego un pago por más del neto (p. ej. el estudiante o ICETEX pagó el valor completo). Uno por '
                    'recibo; el saldo resultante de la persona está en "Saldo a favor pendiente".',
        'riesgo': 'Beneficio asignado de más o pago que no tuvo en cuenta el beneficio: el saldo a favor llega al ERP '
                  'como deuda de la institución con el estudiante.',
        'recomendacion': 'Financiera valida si el beneficio era procedente y si el saldo se devolvió o se cruzó.',
        'texto': lambda d: (f"Matrícula {_pesos(d['bruto'])}; beneficios {_pesos(d['beneficios'])} "
                            f"({d['pct_beneficio']} %: {_lista(d['nombres'], 4)}); pagado {_pesos(d['pagado'])}"
                            + (f" (incluye {_lista(d['terceros'], 3)})" if d.get('terceros') else '')
                            + f"; saldo a favor generado {_pesos(d['saldo_generado'])}."),
        'sql': f"""
            SELECT r.estudiante, r.periodo, r.programa, NULL::int, NULL::int,
                   jsonb_build_object('recibo', r.numero, 'bruto', r.bruto,
                                      'beneficios', r.desc_recibo + r.desc_cuotas,
                                      'pct_beneficio', round(100 * (r.desc_recibo + r.desc_cuotas) / r.bruto),
                                      'nombres', to_jsonb(r.beneficios_recibo || r.desc_cuotas_nombres),
                                      'terceros', to_jsonb(r.terceros_nombres),
                                      'pagado', r.pago_directo + r.pagado_cuotas, 'saldo_generado', r.saldo_generado,
                                      'gravedad', CASE WHEN r.desc_recibo + r.desc_cuotas > r.bruto + 1000 THEN '1 Crítica'
                                                       WHEN r.desc_cuotas > 0 AND r.desc_recibo + r.desc_cuotas >= r.bruto - 1000
                                                           THEN '2 Alta' ELSE '3 Moderada' END)
            FROM anomalias.aux_recibo r
            WHERE r.bruto > 0 AND r.desc_recibo + r.desc_cuotas > 0
              AND (r.saldo_generado >= 1000
                   OR (r.estado <> 2 AND r.desc_recibo + r.desc_cuotas > r.bruto + 1000))
        """,
    },
    'SALDO_A_FAVOR_PENDIENTE': {
        'categoria': 'SALDOS', 'severidad': 'ALTA', 'responsable': FINANCIERA,
        'titulo': 'Saldo a favor pendiente',
        'que_paso': 'Lo que el estudiante (o un tercero, o un beneficio) cubrió superó lo que debía pagar y el saldo '
                    'a favor resultante no se ha usado ni devuelto según SWA. Una fila por persona: generado '
                    '(pagos, cuotas tipo 7 y novedades) − usado − devuelto. SWA registra cada saldo dos veces (cuota '
                    '7 y pago_recibo): se toma el mayor, nunca la suma. Gravedad por monto: 1 ≥ $2.000.000, '
                    '2 ≥ $500.000, 3 el resto (desde $1.000).',
        'riesgo': 'Deuda de la institución con el estudiante que viaja al ERP. Si se cruzó o devolvió por '
                  'contabilidad fuera de SWA, aquí no se ve.',
        'recomendacion': 'Financiera concilia cada saldo: devolver, cruzar con la próxima matrícula o anular si fue un '
                         'error de registro.',
        'texto': lambda d: (f"Saldo pendiente {_pesos(d['pendiente'])} ({_monto(d)}). Origen: "
                            f"{_lista([o[2:] for o in (d.get('origenes') or [])], 4) or 'novedades de descuento'}."),
        'sql': f"""
            SELECT s.codigo, coalesce(s.ultimo_generado, s.ultimo_usado), e.programa, NULL::int, NULL::int,
                   jsonb_build_object('pendiente', round(s.pendiente), 'generado', round(s.generado),
                                      'usado', round(s.usado), 'devuelto', round(s.devuelto),
                                      'origenes', to_jsonb(s.origenes), 'novedades', s.novedades,
                                      'movimientos', s.movimientos,
                                      'gravedad', {GRAVEDAD_SQL.format(col='s.pendiente')})
            FROM anomalias.aux_saldo s JOIN anomalias.aux_estudiante e ON e.codigo = s.codigo
            WHERE s.pendiente >= 1000
        """,
    },
    'SALDO_USADO_SIN_ORIGEN': {
        'categoria': 'SALDOS', 'severidad': 'ALTA', 'responsable': FINANCIERA,
        'titulo': 'Usó más saldo a favor del que tenía registrado',
        'que_paso': 'La persona aplicó saldo a favor (descuento "SALDO A FAVOR" en un recibo, cuota tipo 7 positiva o '
                    'bajar_saldo_a_favor) por más de lo que SWA registra como generado, novedades incluidas. Una '
                    'fila por persona; gravedad por monto como en "Saldo a favor pendiente".',
        'riesgo': 'Un descuento sin soporte: o el saldo nació fuera de SWA (o antes de 2017) o se aplicó un beneficio '
                  'disfrazado de saldo a favor.',
        'recomendacion': 'Financiera ubica el soporte del saldo usado; sin soporte, es un beneficio no autorizado.',
        'texto': lambda d: f"Usó {_pesos(-d['pendiente'])} más de lo que tenía ({_monto(d)}).",
        'sql': f"""
            SELECT s.codigo, coalesce(s.ultimo_usado, s.ultimo_generado), e.programa, NULL::int, NULL::int,
                   jsonb_build_object('pendiente', round(s.pendiente), 'generado', round(s.generado),
                                      'usado', round(s.usado), 'devuelto', round(s.devuelto),
                                      'movimientos', s.movimientos,
                                      'gravedad', {GRAVEDAD_SQL.format(col='-s.pendiente')})
            FROM anomalias.aux_saldo s JOIN anomalias.aux_estudiante e ON e.codigo = s.codigo
            WHERE s.pendiente <= -1000
        """,
    },
}


def _preparar(cur):
    """Tablas auxiliares indexadas (las reglas cruzan registro x persona x pagos)."""
    conceptos = ', '.join(f'({c}, {"NULL" if n is None else n})' for c, n in CONCEPTOS_MATRICULA.items())
    clases = ', '.join(f"({t}, '{clase}')" for clase, tipos in CLASES_CUOTA.items() for t in tipos)
    cur.execute(f"""
        CREATE SCHEMA IF NOT EXISTS anomalias;
        DROP TABLE IF EXISTS anomalias.aux_reg, anomalias.aux_final, anomalias.aux_aprob, anomalias.aux_conc,
                             anomalias.aux_hist, anomalias.aux_pago, anomalias.aux_pago_persona,
                             anomalias.aux_pago_carrera, anomalias.aux_pago_ingles, anomalias.aux_ingles,
                             anomalias.aux_clase_cuota, anomalias.aux_aux_sf, anomalias.aux_recibo,
                             anomalias.aux_saldo, anomalias.aux_estudiante;

        -- Identidad de la persona: SWA tiene la misma persona en varios registros (mismo documento, distinto id;
        -- 7.769 registros para 7.309 documentos). Se usa la unificacion de la depuracion (migra.estudiante.nid):
        -- todos los codigos de un mismo nid quedan con el mismo `persona`, asi un pago o una nota hechos bajo el
        -- otro registro cuentan como suyos.
        CREATE TABLE anomalias.aux_estudiante AS SELECT * FROM swa_raw.estudiante;
        UPDATE anomalias.aux_estudiante a SET persona = k.clave
        FROM (SELECT me.codigo, min(e.persona) OVER (PARTITION BY me.nid) AS clave
              FROM migra.estudiante me JOIN swa_raw.estudiante e ON e.codigo = me.codigo) k
        WHERE k.codigo = a.codigo AND a.persona <> k.clave;
        CREATE UNIQUE INDEX ON anomalias.aux_estudiante (codigo);
        CREATE INDEX ON anomalias.aux_estudiante (persona);

        CREATE TABLE anomalias.aux_conc (concepto int PRIMARY KEY, nivel_ingles int);
        INSERT INTO anomalias.aux_conc VALUES {conceptos};
        CREATE TABLE anomalias.aux_clase_cuota (tipo int PRIMARY KEY, clase text NOT NULL);
        INSERT INTO anomalias.aux_clase_cuota VALUES {clases};
        -- auxilios "SALDO A FAVOR*": en un recibo son uso de saldo, no beneficio
        CREATE TABLE anomalias.aux_aux_sf AS SELECT codigo FROM swa_raw.auxilio WHERE nombre ILIKE 'SALDO A FAVOR%';

        CREATE TABLE anomalias.aux_reg AS
            SELECT r.*, e.persona, e.pensum AS pensum_est, coalesce(ma.canonica, r.asignatura) AS asig_c
            FROM swa_raw.registro r
            JOIN anomalias.aux_estudiante e ON e.codigo = r.estudiante
            LEFT JOIN migra.asignatura ma ON ma.codigo = r.asignatura;
        CREATE INDEX ON anomalias.aux_reg (persona, asig_c);
        CREATE INDEX ON anomalias.aux_reg (persona, periodo, programa);

        -- nota final por persona, periodo, programa y asignatura (criterio de la depuracion: la instancia
        -- especial reemplaza al grupo regular; entre varias, la mayor). Quita duplicados por varios codigos.
        CREATE TABLE anomalias.aux_final AS
            SELECT DISTINCT ON (persona, periodo, programa, asig_c) *
            FROM anomalias.aux_reg WHERE estado = 'R'
            ORDER BY persona, periodo, programa, asig_c, (grupo < 0) DESC, definitiva DESC NULLS LAST, estudiante;
        CREATE INDEX ON anomalias.aux_final (persona, periodo, programa);

        CREATE TABLE anomalias.aux_aprob AS
            SELECT persona, asig_c, min(periodo) AS primer
            FROM anomalias.aux_final WHERE estado_definitiva = 'A' GROUP BY 1, 2;
        CREATE UNIQUE INDEX ON anomalias.aux_aprob (persona, asig_c);

        -- historico por persona (cualquiera de sus codigos)
        CREATE TABLE anomalias.aux_hist AS
            SELECT e.persona, h.periodo, h.programa, bool_or(h.estado = 'M') AS matriculado
            FROM swa_raw.historico h JOIN anomalias.aux_estudiante e ON e.codigo = h.estudiante
            GROUP BY 1, 2, 3;
        CREATE UNIQUE INDEX ON anomalias.aux_hist (persona, periodo, programa);

        -- Cobertura de cada recibo con toda la evidencia (el estado del recibo no basta: miles quedaron en 0/2
        -- y se pagaron por credito o tienen pagos registrados). El saldo a favor esta DOBLE registrado
        -- (cuota 7 y pago_recibo.saldo_a_favor; cuota 7 positiva / auxilio SF y bajar_saldo_a_favor): se toma
        -- el mayor de cada par, nunca la suma. pago_recibo.tipo_recibo: 0 = recibo, 1 = cuota.
        CREATE TABLE anomalias.aux_recibo AS
        WITH pr AS (SELECT recibo, sum(valor) AS valor, sum(saldo_a_favor) AS saf, sum(bajar_saldo_a_favor) AS bajar
                    FROM swa_raw.pago_recibo WHERE tipo_recibo = 0 AND estado = 1 GROUP BY 1),
             pq AS (SELECT recibo, sum(saldo_a_favor) AS saf, sum(bajar_saldo_a_favor) AS bajar
                    FROM swa_raw.pago_recibo WHERE tipo_recibo = 1 AND estado = 1 GROUP BY 1),
             cu AS (SELECT cr.recibo,
                           sum(q.valor_pagado) FILTER (WHERE cc.clase IN ('ESTUDIANTE', 'TERCERO')
                                                         AND q.valor_pagado > 0) AS pagado_cuotas,
                           sum(q.valor) FILTER (WHERE cc.clase = 'DESCUENTO' AND q.estado <> 2) AS desc_cuotas,
                           sum(q.valor) FILTER (WHERE cc.clase = 'TERCERO' AND q.estado <> 2) AS terceros,
                           -sum(q.valor) FILTER (WHERE q.tipo = 7 AND q.valor < 0 AND q.estado <> 2) AS cuota7_neg,
                           sum(q.valor) FILTER (WHERE q.tipo = 7 AND q.valor > 0 AND q.estado <> 2) AS cuota7_pos,
                           sum(pq.saf) AS saf, sum(pq.bajar) AS bajar,
                           array_agg(DISTINCT tc.descripcion) FILTER (WHERE cc.clase = 'DESCUENTO'
                                                                        AND q.estado <> 2) AS desc_cuotas_nombres,
                           array_agg(DISTINCT tc.descripcion) FILTER (WHERE cc.clase = 'TERCERO'
                                                                        AND q.estado <> 2) AS terceros_nombres
                    FROM swa_raw.credito cr
                    JOIN swa_raw.cuota q ON q.credito = cr.numero
                    LEFT JOIN anomalias.aux_clase_cuota cc ON cc.tipo = q.tipo
                    LEFT JOIN swa_raw.tipo_cuota tc ON tc.codigo = q.tipo
                    LEFT JOIN pq ON pq.recibo = q.numero
                    GROUP BY 1)
        SELECT x.numero, x.periodo, x.programa, x.estudiante, e.persona, x.concepto_cambio AS concepto,
               cc.nombre AS concepto_nombre, x.estado, coalesce(x.valor, 0) AS bruto,
               coalesce(x.valor_ordinaria, 0) AS neto, x.valor_pagado,
               coalesce(CASE WHEN x.tipo_auxilio1 IN (SELECT codigo FROM anomalias.aux_aux_sf) THEN 0 ELSE x.valor_auxilio1 END, 0)
             + coalesce(CASE WHEN x.tipo_auxilio2 IN (SELECT codigo FROM anomalias.aux_aux_sf) THEN 0 ELSE x.valor_auxilio2 END, 0)
             + coalesce(CASE WHEN x.tipo_auxilio3 IN (SELECT codigo FROM anomalias.aux_aux_sf) THEN 0 ELSE x.valor_auxilio3 END, 0)
                   AS desc_recibo,
               CASE WHEN x.estado IN (1, 10) THEN
                   coalesce(CASE WHEN x.tipo_auxilio1 IN (SELECT codigo FROM anomalias.aux_aux_sf) THEN x.valor_auxilio1 END, 0)
                 + coalesce(CASE WHEN x.tipo_auxilio2 IN (SELECT codigo FROM anomalias.aux_aux_sf) THEN x.valor_auxilio2 END, 0)
                 + coalesce(CASE WHEN x.tipo_auxilio3 IN (SELECT codigo FROM anomalias.aux_aux_sf) THEN x.valor_auxilio3 END, 0)
               ELSE 0 END AS sf_en_recibo,
               array_remove(ARRAY[a1.nombre, a2.nombre, a3.nombre], NULL) AS beneficios_recibo,
               coalesce(cu.desc_cuotas_nombres, '{{}}') AS desc_cuotas_nombres,
               coalesce(cu.terceros_nombres, '{{}}') AS terceros_nombres,
               coalesce(pr.valor, 0) AS pago_directo, coalesce(cu.pagado_cuotas, 0) AS pagado_cuotas,
               coalesce(cu.desc_cuotas, 0) AS desc_cuotas, coalesce(cu.terceros, 0) AS terceros,
               greatest(coalesce(cu.cuota7_neg, 0), coalesce(pr.saf, 0) + coalesce(cu.saf, 0)) AS saldo_generado,
               greatest(coalesce(cu.cuota7_pos, 0), coalesce(pr.bajar, 0) + coalesce(cu.bajar, 0)) AS saldo_usado_pagos
        FROM swa_raw.recibo_swa x
        JOIN anomalias.aux_estudiante e ON e.codigo = x.estudiante
        LEFT JOIN swa_raw.concepto_cambio cc ON cc.codigo = x.concepto_cambio
        LEFT JOIN swa_raw.auxilio a1 ON a1.codigo = nullif(x.tipo_auxilio1, 0)
                                    AND a1.codigo NOT IN (SELECT codigo FROM anomalias.aux_aux_sf)
        LEFT JOIN swa_raw.auxilio a2 ON a2.codigo = nullif(x.tipo_auxilio2, 0)
                                    AND a2.codigo NOT IN (SELECT codigo FROM anomalias.aux_aux_sf)
        LEFT JOIN swa_raw.auxilio a3 ON a3.codigo = nullif(x.tipo_auxilio3, 0)
                                    AND a3.codigo NOT IN (SELECT codigo FROM anomalias.aux_aux_sf)
        LEFT JOIN pr ON pr.recibo = x.numero
        LEFT JOIN cu ON cu.recibo = x.numero;
        ALTER TABLE anomalias.aux_recibo ADD COLUMN cubierto_por text, ADD COLUMN saldo_usado numeric;
        -- uso de saldo: cuota 7 positiva + auxilio SF del recibo vs bajar_saldo_a_favor (el mismo movimiento)
        UPDATE anomalias.aux_recibo SET saldo_usado = greatest(saldo_usado_pagos, sf_en_recibo),
            cubierto_por = CASE WHEN estado IN (1, 10) THEN 'ESTADO'
                                WHEN pago_directo > 0 THEN 'PAGO_REGISTRADO'
                                WHEN pagado_cuotas > 0 THEN 'CREDITO'
                                WHEN estado <> 2 AND bruto > 0 AND (neto <= 0 OR desc_cuotas >= neto - 1000)
                                    THEN 'BENEFICIO' END;
        -- por que el recibo genero saldo a favor (beneficios = auxilios del recibo + cuotas de descuento)
        ALTER TABLE anomalias.aux_recibo ADD COLUMN origen_saldo text;
        UPDATE anomalias.aux_recibo SET origen_saldo = CASE
            WHEN bruto > 0 AND desc_recibo + desc_cuotas > bruto + 1000 THEN '1 Beneficios suman más del 100 % del valor'
            WHEN desc_cuotas > 0 AND desc_recibo + desc_cuotas >= bruto - 1000 THEN '2 Beneficio del 100 % y además hubo pago'
            WHEN desc_recibo + desc_cuotas > 0 THEN '3 Beneficio parcial y pago por más del neto'
            WHEN terceros > 0 THEN '4 Tercero (ICETEX, convenio, caja) pagó de más'
            ELSE '5 Estudiante pagó de más' END
        WHERE saldo_generado >= 1000;
        CREATE INDEX ON anomalias.aux_recibo (persona, periodo);
        CREATE INDEX ON anomalias.aux_recibo (numero);

        -- recibos de matricula por persona, periodo y programa: pagado = alguno cubierto (estado, pago
        -- registrado, cuotas de credito pagadas o beneficio del 100 %)
        CREATE TABLE anomalias.aux_pago AS
            SELECT r.persona, r.periodo, r.programa,
                   bool_or(r.cubierto_por IS NOT NULL) AS pagado,
                   jsonb_agg(jsonb_build_object('numero', r.numero, 'codigo', r.estudiante,
                                                'concepto', r.concepto, 'concepto_nombre', r.concepto_nombre,
                                                'estado', r.estado, 'valor_pagado', r.valor_pagado,
                                                'cubierto_por', r.cubierto_por,
                                                'beneficios', r.beneficios_recibo || r.desc_cuotas_nombres,
                                                'terceros', r.terceros_nombres)
                             ORDER BY r.numero) AS recibos
            FROM anomalias.aux_recibo r
            JOIN anomalias.aux_conc c ON c.concepto = r.concepto
            GROUP BY 1, 2, 3;
        CREATE UNIQUE INDEX ON anomalias.aux_pago (persona, periodo, programa);

        -- matriculas de carrera pagadas por la persona en el periodo (fuera de los centros de idiomas)
        CREATE TABLE anomalias.aux_pago_carrera AS
            SELECT pg.persona, pg.periodo, jsonb_agg(x ORDER BY (x->>'numero')::int) AS recibos
            FROM anomalias.aux_pago pg, jsonb_array_elements(pg.recibos) x
            WHERE pg.pagado AND pg.programa <> ALL({CENTROS}) AND x->>'cubierto_por' IS NOT NULL
            GROUP BY 1, 2;
        CREATE UNIQUE INDEX ON anomalias.aux_pago_carrera (persona, periodo);

        -- recibos de los centros de idiomas por persona y periodo (las dos sedes juntas)
        CREATE TABLE anomalias.aux_pago_ingles AS
            SELECT pg.persona, pg.periodo, bool_or(pg.pagado) AS pagado,
                   jsonb_agg(x ORDER BY (x->>'numero')::int) AS recibos,
                   coalesce(array_agg(DISTINCT c.nivel_ingles) FILTER (WHERE c.nivel_ingles IS NOT NULL
                                                                     AND x->>'cubierto_por' IS NOT NULL),
                            '{{}}') AS niveles_ingles
            FROM anomalias.aux_pago pg, jsonb_array_elements(pg.recibos) x
            JOIN anomalias.aux_conc c ON c.concepto = (x->>'concepto')::int
            WHERE pg.programa = ANY({CENTROS})
            GROUP BY 1, 2;
        CREATE UNIQUE INDEX ON anomalias.aux_pago_ingles (persona, periodo);

        -- libro de saldos a favor por persona: generado (pagos + novedades) - usado - devuelto
        CREATE TABLE anomalias.aux_saldo AS
        WITH m AS (SELECT persona, sum(saldo_generado) AS generado, sum(saldo_usado) AS usado,
                          max(periodo) FILTER (WHERE saldo_generado >= 1000) AS ultimo_generado,
                          max(periodo) FILTER (WHERE saldo_usado >= 1000) AS ultimo_usado,
                          (array_agg(estudiante ORDER BY periodo DESC))[1] AS codigo,
                          jsonb_agg(jsonb_build_object('numero', numero, 'periodo', periodo,
                                                       'concepto', concepto_nombre, 'generado', saldo_generado,
                                                       'usado', saldo_usado) ORDER BY periodo, numero)
                              FILTER (WHERE saldo_generado >= 1000 OR saldo_usado >= 1000) AS movimientos,
                          array_agg(DISTINCT origen_saldo) FILTER (WHERE origen_saldo IS NOT NULL) AS origenes
                   FROM anomalias.aux_recibo GROUP BY 1),
             n AS (SELECT e.persona, sum(n.valor) AS novedad,
                          jsonb_agg(coalesce(nullif(trim(n.descripcion), ''), a.nombre) || ' ' || n.valor) AS novedades
                   FROM swa_raw.novedad_descuento n JOIN anomalias.aux_estudiante e ON e.codigo = n.estudiante
                   LEFT JOIN swa_raw.auxilio a ON a.codigo = n.auxilio
                   WHERE n.estado = 1 AND n.auxilio IN (SELECT codigo FROM anomalias.aux_aux_sf) GROUP BY 1),
             d AS (SELECT e.persona, sum(v.valor) AS devuelto
                   FROM swa_raw.novedad_devolucion v JOIN anomalias.aux_estudiante e ON e.codigo = v.estudiante
                   WHERE v.estado = 1 GROUP BY 1)
        SELECT m.persona, m.codigo, m.ultimo_generado, m.ultimo_usado, m.movimientos, m.origenes, n.novedades,
               m.generado + coalesce(n.novedad, 0) AS generado, m.usado, coalesce(d.devuelto, 0) AS devuelto,
               m.generado + coalesce(n.novedad, 0) - m.usado - coalesce(d.devuelto, 0) AS pendiente
        FROM m LEFT JOIN n USING (persona) LEFT JOIN d USING (persona)
        WHERE m.generado >= 1 OR m.usado >= 1 OR n.novedad IS NOT NULL;

        CREATE TABLE anomalias.aux_ingles AS
            SELECT r.*, pa.nivel AS modulo, (r.estado_definitiva = 'A') AS aprobada,
                   r.grupo IN {HOMOLOGACION} AS homologada
            FROM anomalias.aux_reg r
            JOIN (SELECT DISTINCT programa, asignatura, nivel FROM swa_raw.pensum_asignatura) pa
              ON pa.programa = r.programa AND pa.asignatura = r.asignatura
            WHERE r.programa = ANY({CENTROS}) AND r.estado = 'R' AND pa.nivel BETWEEN 1 AND 6;
        CREATE INDEX ON anomalias.aux_ingles (persona, modulo);

        ANALYZE anomalias.aux_reg; ANALYZE anomalias.aux_final; ANALYZE anomalias.aux_aprob;
        ANALYZE anomalias.aux_hist; ANALYZE anomalias.aux_pago; ANALYZE anomalias.aux_ingles;
    """)


def _crear_tablas(cur):
    cur.execute("""
        CREATE SCHEMA IF NOT EXISTS anomalias;
        DROP TABLE IF EXISTS anomalias.anomalia;
        CREATE TABLE anomalias.anomalia (
            id bigserial PRIMARY KEY,
            tipo text NOT NULL,
            categoria text NOT NULL,
            severidad text NOT NULL,
            responsable text NOT NULL,
            estudiante text,
            periodo int,
            programa int,
            asignatura int,
            grupo int,
            detalle jsonb,
            persona_swa int,
            documento text,
            nombre text,
            programa_nombre text,
            asignatura_nombre text,
            nid text,
            estado_migracion text,
            nota_migra boolean,
            gravedad text
        );
        CREATE TABLE IF NOT EXISTS anomalias.ejecucion (
            id bigserial PRIMARY KEY,
            fecha timestamptz NOT NULL DEFAULT now(),
            resumen jsonb NOT NULL
        );
    """)


def _enriquecer(cur):
    """Persona, nombres y si el caso viaja al ERP (persona LISTA / en revision / fuera)."""
    cur.execute("""
        UPDATE anomalias.anomalia a SET
            persona_swa = e.persona,
            documento = p.identificacion,
            nombre = trim(concat_ws(' ', p.nombre, p.apellido))
        FROM anomalias.aux_estudiante e JOIN swa_raw.persona p ON p.id = e.persona
        WHERE e.codigo = a.estudiante;

        UPDATE anomalias.anomalia a SET programa_nombre = pr.nombre
        FROM swa_raw.programa pr WHERE pr.codigo = a.programa;

        UPDATE anomalias.anomalia a SET asignatura_nombre = s.nombre
        FROM swa_raw.asignatura s WHERE s.codigo = a.asignatura;

        UPDATE anomalias.anomalia a SET nid = me.nid,
            estado_migracion = CASE mp.estado WHEN 'LISTA' THEN 'SE_MIGRA'
                                              WHEN 'REVISION' THEN 'EN_REVISION' ELSE 'NO_SE_MIGRA' END
        FROM migra.estudiante me LEFT JOIN migra.persona mp ON mp.nid = me.nid
        WHERE me.codigo = a.estudiante;
        UPDATE anomalias.anomalia SET estado_migracion = 'NO_SE_MIGRA' WHERE estado_migracion IS NULL;

        UPDATE anomalias.anomalia a SET nota_migra = EXISTS (
            SELECT 1 FROM migra.nota n WHERE n.periodo = a.periodo AND n.programa = a.programa
              AND n.asignatura = a.asignatura AND n.grupo = a.grupo AND n.estudiante = a.estudiante)
        WHERE a.asignatura IS NOT NULL;

        UPDATE anomalias.anomalia SET gravedad = detalle->>'gravedad' WHERE detalle ? 'gravedad';

        CREATE INDEX ON anomalias.anomalia (tipo);
        CREATE INDEX ON anomalias.anomalia (estudiante);
        CREATE INDEX ON anomalias.anomalia (nid);
        CREATE INDEX ON anomalias.anomalia (persona_swa);
    """)


def generar_anomalias(log=print):
    with conectar_destino() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('migra.estudiante') IS NOT NULL")
        if not cur.fetchone()[0]:
            raise RuntimeError('Primero ejecuta la depuración (migra_depurar).')
        log('- Preparando tablas auxiliares...')
        _preparar(cur)
        _crear_tablas(cur)
        for tipo, regla in CATALOGO.items():
            cur.execute(f"""
                INSERT INTO anomalias.anomalia (tipo, categoria, severidad, responsable, estudiante, periodo,
                                                programa, asignatura, grupo, detalle)
                SELECT %s, %s, %s, %s, x.* FROM ({regla['sql']}) x
            """, (tipo, regla['categoria'], regla['severidad'], regla['responsable']))
            log(f'- {tipo}: {cur.rowcount}')
        log('- Enriqueciendo con persona y estado de migración...')
        _enriquecer(cur)
        resumen = resumen_anomalias(cur)
        cur.execute('INSERT INTO anomalias.ejecucion (resumen) VALUES (%s::jsonb)',
                    (json.dumps(resumen, default=str),))
        conn.commit()
    return resumen


def resumen_anomalias(cur):
    cur.execute("""
        SELECT tipo, count(*), count(DISTINCT coalesce(persona_swa::text, estudiante)),
               count(*) FILTER (WHERE estado_migracion = 'SE_MIGRA'),
               count(*) FILTER (WHERE nota_migra)
        FROM anomalias.anomalia GROUP BY 1
    """)
    conteos = {t: (f, p, m, n) for t, f, p, m, n in cur.fetchall()}
    # indicadores sin las reglas informativas (son contexto, no anomalias)
    cur.execute("""
        SELECT count(*) FILTER (WHERE severidad <> 'INFO'),
               count(*) FILTER (WHERE severidad = 'INFO'),
               count(*) FILTER (WHERE severidad = 'ALTA'),
               count(DISTINCT coalesce(persona_swa::text, estudiante)) FILTER (WHERE severidad <> 'INFO'),
               count(DISTINCT coalesce(persona_swa::text, estudiante))
                   FILTER (WHERE severidad <> 'INFO' AND estado_migracion = 'SE_MIGRA'),
               count(DISTINCT coalesce(persona_swa::text, estudiante)) FILTER (WHERE severidad = 'ALTA')
        FROM anomalias.anomalia
    """)
    total, informativos, altas, personas, personas_migran, personas_alta = cur.fetchone()
    tipos = []
    for tipo, regla in CATALOGO.items():
        f, p, m, n = conteos.get(tipo, (0, 0, 0, 0))
        tipos.append({
            'tipo': tipo, 'categoria': regla['categoria'], 'severidad': regla['severidad'],
            'responsable': regla['responsable'],
            'titulo': regla['titulo'], 'que_paso': regla['que_paso'], 'riesgo': regla['riesgo'],
            'recomendacion': regla['recomendacion'],
            'casos': f, 'personas': p, 'casos_se_migran': m, 'notas_que_viajan': n,
        })
    return {
        'total_casos': total, 'casos_informativos': informativos, 'casos_alta': altas,
        'personas': personas, 'personas_que_se_migran': personas_migran, 'personas_alta': personas_alta,
        'categorias': CATEGORIAS, 'severidades': SEVERIDADES, 'gravedades': [g for _, g in GRAVEDAD_MONTO],
        'responsables': [REGISTRO, FINANCIERA, IDIOMAS], 'tipos': tipos,
        'primer_periodo_financiero': PRIMER_PERIODO_FINANCIERO,
    }
