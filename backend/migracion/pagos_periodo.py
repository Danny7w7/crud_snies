"""Pago de matrícula de quienes tienen asignaturas en un periodo: consulta directa a SWA (solo lectura) y Excel.

La consulta, los veredictos y las anomalías están documentados en sql/pagos_periodo.sql. Pago comprobado = dinero
que entró a una cuenta real; los créditos se apartan y los descuentos se revisan (anomalías A1-A10).
La fuente se configura en el alias `produccion`; en operación diaria se consulta SWA.
"""
import io
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from psycopg.rows import dict_row

from .anomalias_reporte import AJUSTE, COLOR_SEVERIDAD, _tabla
from .conexiones import conectar_swa

SQL = (Path(__file__).parent / 'sql' / 'pagos_periodo.sql').read_text(encoding='utf-8')
CTES = SQL.split('-- ==== CONSULTA FINAL')[0]
MARCA_PERIODO = '/*PERIODO*/20262'
MARCA_MAPA = '/*MAPA*/ARRAY[]::text[], ARRAY[]::text[]'
IDIOMAS = {51, 52}

GRUPOS = {
    '1': 'Pago comprobado en dinero',
    '2': 'Crédito (apartado)',
    '3': 'Sin pago comprobado',
    '4': 'Cubierto sin dinero',
    '5': 'Sin pago propio, con otro pago',
}
COLOR_GRUPO = {'1': 'D4EDDA', '2': 'D6EAF8', '3': 'F8D7DA', '4': 'FFF3CD', '5': 'E9ECEF'}
# orden de los créditos: primero los que no tienen ningún pago
ORDEN_VEREDICTO = {'2D': 0, '2E': 1, '2C': 2, '2F': 3, '2B': 4, '2A': 5}
ESTADOS_RECIBO = {10: 'pagado', 1: 'financiado', 0: 'sin pagar', 2: 'anulado'}
SEVERIDADES = {'ALTA': 'Alta', 'MEDIA': 'Media', 'BAJA': 'Baja', 'INFO': 'Informativa'}
ORDEN_SEVERIDAD = {'ALTA': 0, 'MEDIA': 1, 'BAJA': 2, 'INFO': 3}
ANOMALIAS = {
    'A1': ('Descuento doble', 'El mismo beneficio está en el recibo y otra vez como cuota de descuento del crédito.'),
    'A2': ('Descuentos del 100 % o más', 'La matrícula quedó cubierta solo con descuentos: verificar la autorización.'),
    'A3': ('Porcentaje que no cuadra', 'El valor del descuento no es el porcentaje que dice su nombre sobre el bruto.'),
    'A4': ('Ajuste manual sin dinero', 'Cuota de ajuste (18) o cuenta de menor cuantía que da por pagada la matrícula.'),
    'A5': ('Saldo a favor aplicado', 'Se usó saldo a favor; Alta si el año anterior no quedó dinero sin consumir que lo respalde.'),
    'A6': ('Pago de un periodo anterior', 'Se aplicó un pago de otro periodo; Alta si no hay dinero sin consumir que lo respalde.'),
    'A7': ('Beca o convenio sin dinero', 'Cubierto con las cuentas de becas o convenios (no entra dinero).'),
    'A8': ('Cuotas que no suman el crédito', 'Las cuotas vigentes del crédito no suman el valor del crédito.'),
    'A9': ('Valor menor al habitual', 'Valor bruto menor al más común del programa (¿media matrícula?).'),
    'A10': ('Generó saldo a favor', 'Lo cubierto superó el valor de la matrícula y quedó saldo a favor.'),
}
MONEDA = '"$"#,##0'


def _nid(documento):
    """Documento normalizado como en la consulta (sin X de duplicado anulado ni separadores)."""
    doc = re.sub(r'^X(?=\d)|(?<=\d)X$', '', documento.strip().upper())
    return re.sub(r'[^0-9A-Z]', '', doc)


# ------------------------------------------------------------------------- misma persona con otro documento
# El Centro de Idiomas crea al estudiante de carrera como otra persona: con un documento ficticio (4 dígitos, tipo 0,
# fecha de nacimiento de relleno) o con el documento mal digitado. Sin unificarlos, la persona cuenta dos veces y el
# pago de su carrera no se cruza con su inglés.
UNIVERSO_PERSONAS = r"""
SELECT DISTINCT regexp_replace(regexp_replace(regexp_replace(upper(p.identificacion), '^X([0-9])', '\1'),
                                              '([0-9])X$', '\1'), '[^0-9A-Z]', '', 'g') AS nid,
       p.identificacion, concat_ws(' ', p.nombre, p.apellido) AS nombre, p.email AS email_persona,
       e.email AS email_estudiante, r.programa
FROM registro r JOIN estudiante e ON e.codigo = r.estudiante JOIN persona p ON p.id = e.persona
WHERE r.periodo = %(periodo)s AND r.estado IN ('R', 'P')"""
CORREO_RELLENO = re.compile(r'^(|@|SINEMAIL.*|.*@UNIVERSIDAD\.EDU\.CO|NO ?TIENE.*)$')


def _palabras(nombre):
    s = unicodedata.normalize('NFKD', (nombre or '').upper())
    return frozenset(re.findall(r'[A-Z]+', ''.join(ch for ch in s if not unicodedata.combining(ch))))


def _un_cambio(a, b):
    """True si los documentos difieren en un solo carácter (cambiado, sobrante o faltante)."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    corto, largo = sorted((a, b), key=len)
    return any(largo[:i] + largo[i + 1:] == corto for i in range(len(largo)))


def _parecido(pa, pb):
    """Qué tanto se parecen los nombres (0 = nada): mismas palabras, mismas letras (DEJESUS / DE JESUS), uno
    contenido en el otro con 3+ palabras, o 3+ palabras en común con a lo sumo una distinta."""
    mejor = 0
    for a in pa['nombres']:
        for b in pb['nombres']:
            comun = len(a & b)
            if a == b or sorted(''.join(a)) == sorted(''.join(b)):
                mejor = max(mejor, 1)
            elif comun >= 3 and comun == min(len(a), len(b)):
                mejor = max(mejor, 0.9)
            elif comun >= 3 and comun >= max(len(a), len(b)) - 1:
                mejor = max(mejor, 0.8)
            elif comun >= 2 and pa['correos'] & pb['correos']:
                mejor = max(mejor, 0.85)
    return mejor


def unificar(cur, periodo):
    """Registros de idiomas que son la misma persona que un estudiante de carrera del periodo, con otro documento.

    Devuelve [{dup, real, documento_dup, documento_real, nombre, motivo}]. Solo une un registro que esté
    únicamente en el Centro de Idiomas con una persona de carrera, y solo si el parecido es claro y único.
    """
    cur.execute(UNIVERSO_PERSONAS, {'periodo': int(periodo)})
    per = defaultdict(lambda: {'nombres': set(), 'correos': set(), 'progs': set(), 'doc': None, 'nombre': None})
    for f in cur.fetchall():
        p = per[f['nid']]
        p['nombres'].add(_palabras(f['nombre']))
        p['progs'].add(f['programa'])
        p['doc'], p['nombre'] = f['identificacion'], f['nombre']
        for correo in (f['email_persona'], f['email_estudiante']):
            correo = (correo or '').strip().upper()
            if not CORREO_RELLENO.match(correo):
                p['correos'].add(correo)
    carrera = [n for n, p in per.items() if p['progs'] - IDIOMAS]
    unidos = []
    for nid, p in per.items():
        if p['progs'] - IDIOMAS:
            continue
        ficticio = len(nid) < 7
        candidatos = sorted(((_parecido(p, per[c]), c) for c in carrera
                             if ficticio or (_un_cambio(nid, c) and _parecido(p, per[c]) == 1)), reverse=True)
        candidatos = [x for x in candidatos if x[0] >= 0.8]
        if not candidatos or (len(candidatos) > 1 and candidatos[1][0] == candidatos[0][0]):
            continue
        puntaje, real = candidatos[0]
        motivo = ('documento ficticio del Centro de Idiomas' if ficticio else 'documento mal digitado (un dígito)') + \
            (', mismo nombre' if puntaje == 1 else ', nombre casi igual')
        unidos.append({'dup': nid, 'real': real, 'documento_dup': p['doc'], 'documento_real': per[real]['doc'],
                       'nombre_dup': p['nombre'], 'nombre_real': per[real]['nombre'], 'motivo': motivo})
    return unidos


def consultar(periodo, programas=(), grupos=(), veredictos=(), documento=None):
    """Casos (persona y programa) y anomalías de descuentos del periodo, con los filtros dados."""
    if not re.fullmatch(r'\d{4}[1-3]', str(periodo)):
        raise ValueError(f'Periodo inválido: {periodo} (formato AAAAP, p. ej. 20262)')
    # psycopg usa % para los parámetros: los % literales de la consulta se escapan
    ctes = CTES.replace(MARCA_PERIODO, str(int(periodo))).replace('%', '%%')
    casos_f, anom_f, valores = [], [], {}
    if programas:
        # los dos centros de idiomas (51 y 52) son uno solo en la consulta
        valores['progs'] = sorted({51 if int(p) in (51, 52) else int(p) for p in programas})
        casos_f.append('prog = ANY(%(progs)s)')
        anom_f.append('prog = ANY(%(progs)s)')
    if grupos:
        valores['grupos'] = [str(g)[0] for g in grupos]
        casos_f.append('left(veredicto, 1) = ANY(%(grupos)s)')
        anom_f.append('left(veredicto_persona, 1) = ANY(%(grupos)s)')
    if veredictos:
        valores['veredictos'] = [v.upper() for v in veredictos]
        casos_f.append("split_part(veredicto, ' ', 1) = ANY(%(veredictos)s)")
        anom_f.append("split_part(veredicto_persona, ' ', 1) = ANY(%(veredictos)s)")
    if documento:
        valores['nid'] = _nid(documento)  # si es un documento unificado, se cambia por el real más abajo
        casos_f.append('nid = %(nid)s')
        anom_f.append('nid = %(nid)s')
    donde_casos = ('WHERE ' + ' AND '.join(casos_f)) if casos_f else ''
    donde_anom = ('WHERE ' + ' AND '.join(anom_f)) if anom_f else ''
    with conectar_swa() as conn:
        cur = conn.cursor(row_factory=dict_row)
        unificados = unificar(cur, periodo)
        valores['dups'] = [u['dup'] for u in unificados]
        valores['reales'] = [u['real'] for u in unificados]
        if 'nid' in valores:
            valores['nid'] = next((u['real'] for u in unificados if u['dup'] == valores['nid']), valores['nid'])
        ctes = ctes.replace(MARCA_MAPA, '%(dups)s::text[], %(reales)s::text[]')
        cur.execute(f'{ctes} SELECT * FROM fin {donde_casos} ORDER BY veredicto, programa, nombre', valores)
        casos = cur.fetchall()
        cur.execute(f'{ctes} SELECT * FROM anom {donde_anom} ORDER BY anomalia, severidad, programa, nombre', valores)
        anomalias = cur.fetchall()
        cur.execute('SELECT codigo, trim(nombre) AS nombre FROM programa')
        nombres = {f['codigo']: f['nombre'] for f in cur.fetchall()}
    return {'periodo': int(periodo), 'fecha': datetime.now(), 'casos': casos, 'anomalias': anomalias,
            'programas': nombres, 'unificados': unificados}


def resumen(datos):
    """Conteos por veredicto y por anomalía (para la consola y la hoja Resumen)."""
    por_veredicto = defaultdict(lambda: {'casos': 0, 'personas': set(), 'asignaturas': 0, 'con_nota': 0,
                                         'matriculados': 0, 'neto': 0, 'dinero': 0, 'sin_dinero': 0, 'faltante': 0})
    for c in datos['casos']:
        v = por_veredicto[c['veredicto']]
        v['casos'] += 1
        v['personas'].add(c['nid'])
        v['asignaturas'] += c['asignaturas']
        v['con_nota'] += c['con_nota']
        v['matriculados'] += 'M' in (c['estado_historico'] or '')
        for k in ('neto', 'dinero', 'sin_dinero', 'faltante'):
            v[k] += c[k] or 0
    por_anomalia = defaultdict(lambda: {'casos': 0, 'personas': set(), 'valor': 0})
    for a in datos['anomalias']:
        v = por_anomalia[(a['anomalia'].split()[0], a['severidad'])]
        v['casos'] += 1
        v['personas'].add(a['nid'])
        v['valor'] += a['valor_anomalia'] or 0
    return por_veredicto, por_anomalia


# ------------------------------------------------------------------------------------------- Excel
def _periodo(p):
    return f'{p // 10}-{p % 10}'


def _fecha(f):
    """20260810 -> 2026-08-10 (fechas enteras de SWA)."""
    s = str(f or '')
    return f'{s[:4]}-{s[4:6]}-{s[6:]}' if len(s) == 8 else s


def _programa(c, nombres):
    return f"{c['programa']} · {nombres.get(c['programa'], '')}" if c['programa'] is not None else ''


def _recibo(c):
    if c['recibo'] is None:
        return '', ''
    return c['recibo'], f"{ESTADOS_RECIBO.get(c['estado_recibo'], c['estado_recibo'])} ({c['concepto_nombre'] or ''})"


def _pintar(hoja, columna, desde, hasta, colores, clave):
    for i in range(desde, hasta + 1):
        celda = hoja.cell(i, columna)
        color = colores.get(clave(celda.value)) if celda.value else None
        if color:
            celda.fill = PatternFill('solid', fgColor=color)


def _formato_moneda(hoja, columnas, desde, hasta):
    for c in columnas:
        for i in range(desde, hasta + 1):
            hoja.cell(i, c).number_format = MONEDA


def _detalle(hoja, titulo, columnas, anchos, filas, moneda, congelar):
    hoja.title = titulo
    fin = _tabla(hoja, 1, columnas, filas, anchos)
    _pintar(hoja, 1, 2, fin, COLOR_GRUPO, lambda v: str(v)[0])
    _formato_moneda(hoja, moneda, 2, fin)
    hoja.freeze_panes = congelar
    hoja.auto_filter.ref = f'A1:{hoja.cell(1, len(columnas)).column_letter}{max(fin, 1)}'


def _hoja_resumen(hoja, datos, filtros):
    hoja.title = 'Resumen'
    por_veredicto, por_anomalia = resumen(datos)
    casos = datos['casos']
    hoja['A1'] = f"Pago de matrícula de quienes tienen asignaturas — periodo {_periodo(datos['periodo'])}"
    hoja['A1'].font = Font(bold=True, size=14)
    sin_pago = [c for c in casos if c['veredicto'][0] in '345']
    sin_ningun_pago = [c for c in casos if c['veredicto'][:2] in ('2D', '2E')]
    lineas = [
        ('Consultado', datos['fecha'].strftime('%Y-%m-%d %H:%M')),
        ('Fuente', 'SWA producción, directo y en solo lectura (no la copia local)'),
        ('Filtros', filtros or 'Ninguno (periodo completo)'),
        ('Personas con asignaturas vinculadas', len({c['nid'] for c in casos})),
        ('   registros de la misma persona con otro documento, unificados (hoja Personas unificadas)',
         len(datos.get('unificados') or [])),
        ('Casos (persona y programa; los dos centros de idiomas cuentan como uno)', len(casos)),
        ('   Sin la matrícula pagada por completo en dinero (grupos 3, 4 y 5)', len(sin_pago)),
        ('   Con crédito (apartados, grupo 2)', sum(c['veredicto'][0] == '2' for c in casos)),
        ('      de ellos sin ningún pago (2D y 2E)', len(sin_ningun_pago)),
        ('Anomalías de descuentos (sin informativas)', sum(a['severidad'] != 'INFO' for a in datos['anomalias'])),
        ('   de severidad alta', sum(a['severidad'] == 'ALTA' for a in datos['anomalias'])),
    ]
    for i, (k, v) in enumerate(lineas, 3):
        hoja.cell(i, 1, k).font = Font(bold=True)
        hoja.cell(i, 2, v)
    notas = [
        'Pago comprobado = dinero que entró a una cuenta real (Davivienda, Efecty, PayGateway, tarjeta...). Lo '
        'registrado en las cuentas "VALORES X CONCEPTOS" (saldos a favor, pagos de periodos anteriores, descuentos, '
        'menor cuantía, convenios, becas) y las cuotas de ajuste o de saldo a favor NO son dinero.',
        'Universo: toda persona con asignaturas vinculadas en el periodo (inscritas o preinscritas, con o sin nota), '
        'por programa. Una persona con varios códigos SWA cuenta una vez y sus pagos con cualquier código valen.',
        'Grupo 2 (créditos) se aparta: el detalle de cuotas pagadas, vencidas y por vencer está en la hoja Créditos. '
        'Grupo 4: "Respaldo en dinero" indica si el año anterior quedó dinero sin consumir (pagó y no cursó con nota).',
    ]
    fila = len(lineas) + 4
    for n in notas:
        hoja.cell(fila, 1, n).alignment = AJUSTE
        hoja.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=11)
        hoja.row_dimensions[fila].height = 32
        fila += 1

    datos_v = []
    for veredicto in sorted(por_veredicto, key=lambda v: (v[0], ORDEN_VEREDICTO.get(v[:2], 9), v)):
        v = por_veredicto[veredicto]
        datos_v.append([GRUPOS[veredicto[0]], veredicto, v['casos'], len(v['personas']), v['asignaturas'],
                        v['con_nota'], v['matriculados'], round(v['neto']), round(v['dinero']),
                        round(v['sin_dinero']), round(v['faltante'])])
    inicio = fila + 1
    fin = _tabla(hoja, inicio, ['Grupo', 'Veredicto', 'Casos', 'Personas', 'Asignaturas', 'Con nota',
                                'Matriculados (M)', 'Valor neto', 'Pagado en dinero', 'Cubierto sin dinero', 'Faltante'],
                 datos_v, [44, 58, 9, 10, 12, 10, 13, 16, 16, 16, 14])
    _pintar(hoja, 1, inicio + 1, fin, {v: COLOR_GRUPO[k] for k, v in GRUPOS.items()}, lambda v: v)
    _formato_moneda(hoja, range(8, 12), inicio + 1, fin)

    datos_a = []
    for codigo, (titulo, que) in ANOMALIAS.items():
        severidades = sorted((s for c, s in por_anomalia if c == codigo), key=ORDEN_SEVERIDAD.get) or [None]
        for s in severidades:
            v = por_anomalia.get((codigo, s), {'casos': 0, 'personas': set(), 'valor': 0})
            datos_a.append([codigo, titulo, SEVERIDADES.get(s, '—'), v['casos'], len(v['personas']),
                            round(v['valor']), que])
    inicio = fin + 3
    hoja.cell(inicio - 1, 1, 'Anomalías de los descuentos (una regla sin casos también quedó verificada)').font = Font(bold=True)
    fin = _tabla(hoja, inicio, ['Código', 'Anomalía', 'Severidad', 'Casos', 'Personas', 'Valor', 'Qué significa'],
                 datos_a, [44, 58, 9, 10, 12, 10, 60])
    invertido = {v: k for k, v in SEVERIDADES.items()}
    _pintar(hoja, 3, inicio + 1, fin, COLOR_SEVERIDAD, lambda v: invertido.get(v))
    _formato_moneda(hoja, [6], inicio + 1, fin)


def _hoja_sin_pago(hoja, casos, nombres):
    filas = []
    for c in casos:
        recibo, estado = _recibo(c)
        filas.append([
            c['veredicto'], c['documento'], c['nombre'], c['codigos'], _programa(c, nombres), c['estado_historico'] or '',
            c['asignaturas'], c['con_nota'], c['homologadas'], c['preinscritas'], c['nombres_asignaturas'],
            c['inscrito_por'], recibo, estado, c['bruto'], (c['desc_recibo'] or 0) + (c['descuento'] or 0) if c['recibo'] else None,
            c['neto'], c['dinero'], c['sin_dinero'], c['faltante'], c['sin_dinero_detalle'] or '',
            c['respaldo_en_dinero'] or '', c['periodos_anteriores'] or '', c['recibos_del_periodo'] or '',
            '; '.join(x for x in (c['pago_otra_carrera'], c['pago_otro_concepto']) if x),
            ' '.join(x for x in (c['recibo_legalizacion'], c['fecha_legalizacion']) if x),
        ])
    _detalle(hoja, 'Sin pago comprobado',
             ['Veredicto', 'Documento', 'Nombre', 'Códigos SWA', 'Programa', 'Histórico', 'Asignaturas', 'Con nota',
              'Homologadas', 'Preinscritas', 'Asignaturas inscritas', 'Inscritas por (usuario y fecha)', 'Recibo',
              'Estado del recibo', 'Valor bruto',
              'Descuentos', 'Valor neto', 'Pagado en dinero', 'Cubierto sin dinero', 'Faltante',
              'Cómo se cubrió sin dinero (cuenta, descripción y usuario)', 'Respaldo en dinero del año anterior',
              'Año anterior', 'Recibos del periodo', 'Pago en otro programa / otro concepto',
              'Recibo con que se legalizó (histórico)'],
             [40, 14, 30, 24, 34, 9, 11, 9, 11, 11, 50, 30, 10, 24, 14, 14, 14, 14, 14, 12, 70, 34, 60, 60, 40, 24],
             filas, [15, 16, 17, 18, 19, 20], 'D2')


def _hoja_creditos(hoja, casos, nombres):
    casos = sorted(casos, key=lambda c: (ORDEN_VEREDICTO.get(c['veredicto'][:2], 9), c['programa'], c['nombre']))
    filas = []
    for c in casos:
        recibo, estado = _recibo(c)
        filas.append([
            c['veredicto'], c['documento'], c['nombre'], c['codigos'], _programa(c, nombres), c['estado_historico'] or '',
            c['asignaturas'], c['con_nota'], recibo, estado, c['creditos'], c['neto'],
            (c['desc_recibo'] or 0) + (c['descuento'] or 0), c['dinero'], c['est_valor'], c['est_pagado'],
            c['est_vencido'], c['est_cuotas_vencidas'], _fecha(c['vencida_desde']), c['est_por_vencer'],
            c['terceros'] or '', c['ter_valor'], c['ter_pagado'], c['ter_pendiente'], c['sin_dinero'],
            c['sin_dinero_detalle'] or '',
            '; '.join(x for x in (c['desc_recibo_detalle'], c['desc_cuotas_detalle']) if x),
            _fecha(c['ultimo_pago']),
        ])
    _detalle(hoja, 'Créditos',
             ['Veredicto', 'Documento', 'Nombre', 'Códigos SWA', 'Programa', 'Histórico', 'Asignaturas', 'Con nota',
              'Recibo', 'Estado del recibo', 'Crédito(s)', 'Valor neto', 'Descuentos', 'Pagado en dinero',
              'Cuotas del estudiante', 'Pagado por el estudiante', 'Vencido sin pagar', 'Cuotas vencidas',
              'Vencida desde', 'Por vencer', 'Terceros (ICETEX, convenio, caja)', 'A cargo de terceros',
              'Pagado por terceros', 'Pendiente de terceros', 'Cubierto sin dinero', 'Detalle sin dinero',
              'Descuentos (detalle)', 'Último pago en dinero'],
             [44, 14, 30, 24, 34, 9, 11, 9, 10, 24, 12, 14, 14, 14, 14, 14, 14, 9, 12, 14, 34, 14, 14, 14, 14, 60, 60, 12],
             filas, [12, 13, 14, 15, 16, 17, 20, 22, 23, 24, 25], 'D2')


def _hoja_anomalias(hoja, anomalias, nombres):
    anomalias = sorted(anomalias, key=lambda a: (ORDEN_SEVERIDAD[a['severidad']], int(a['anomalia'].split()[0][1:]),
                                                 a['programa'], a['nombre']))
    filas = []
    for a in anomalias:
        filas.append([
            SEVERIDADES[a['severidad']], a['anomalia'], a['documento'], a['nombre'], _programa(a, nombres),
            a['numero'], f"{ESTADOS_RECIBO.get(a['estado'], a['estado'])} ({a['concepto_nombre'] or ''})",
            a['veredicto_persona'], a['bruto'], a['desc_recibo'] + a['descuento'], a['neto'], a['dinero'],
            a['valor_anomalia'], a['explicacion'] or '', a['dinero_sin_consumir_anterior'], a['periodos_anteriores'] or '',
        ])
    hoja.title = 'Anomalías descuentos'
    fin = _tabla(hoja, 1, ['Severidad', 'Anomalía', 'Documento', 'Nombre', 'Programa', 'Recibo', 'Estado del recibo',
                           'Veredicto de pago', 'Valor bruto', 'Descuentos', 'Valor neto', 'Pagado en dinero',
                           'Valor de la anomalía', 'Explicación (cuenta, descripción y usuario)',
                           'Dinero sin consumir del año anterior', 'Año anterior'],
                 filas, [11, 46, 14, 30, 34, 10, 24, 40, 14, 14, 14, 14, 14, 80, 16, 60])
    invertido = {v: k for k, v in SEVERIDADES.items()}
    _pintar(hoja, 1, 2, fin, COLOR_SEVERIDAD, lambda v: invertido.get(v))
    _formato_moneda(hoja, [9, 10, 11, 12, 13, 15], 2, fin)
    hoja.freeze_panes = 'E2'
    hoja.auto_filter.ref = f'A1:P{max(fin, 1)}'


def _hoja_pagados(hoja, casos, nombres):
    filas = []
    for c in casos:
        recibo, estado = _recibo(c)
        filas.append([c['veredicto'], c['documento'], c['nombre'], c['codigos'], _programa(c, nombres),
                      c['estado_historico'] or '', c['asignaturas'], c['con_nota'], recibo, estado, c['neto'],
                      c['dinero'], c['pago_directo_detalle'] or ''])
    _detalle(hoja, 'Pago comprobado',
             ['Veredicto', 'Documento', 'Nombre', 'Códigos SWA', 'Programa', 'Histórico', 'Asignaturas', 'Con nota',
              'Recibo', 'Estado del recibo', 'Valor neto', 'Pagado en dinero', 'Pago (fecha, cuenta, usuario)'],
             [32, 14, 30, 24, 34, 9, 11, 9, 10, 24, 14, 14, 80], filas, [11, 12], 'D2')


def _hoja_unificados(hoja, unificados):
    hoja.title = 'Personas unificadas'
    fin = _tabla(hoja, 1, ['Documento en SWA (duplicado)', 'Nombre en ese registro', 'Se unió con el documento',
                           'Nombre de la persona', 'Motivo'],
                 [[u['documento_dup'], u['nombre_dup'], u['documento_real'], u['nombre_real'], u['motivo']]
                  for u in unificados], [22, 36, 22, 36, 50])
    hoja.freeze_panes = 'A2'
    hoja.auto_filter.ref = f'A1:E{max(fin, 1)}'


def libro_excel(datos, filtros=''):
    """Resumen, Sin pago comprobado (grupos 3-5), Créditos (2), Anomalías descuentos y Pago comprobado (1)."""
    casos, nombres = datos['casos'], datos['programas']
    libro = Workbook()
    _hoja_resumen(libro.active, datos, filtros)
    _hoja_sin_pago(libro.create_sheet(), [c for c in casos if c['veredicto'][0] in '345'], nombres)
    _hoja_creditos(libro.create_sheet(), [c for c in casos if c['veredicto'][0] == '2'], nombres)
    _hoja_anomalias(libro.create_sheet(), datos['anomalias'], nombres)
    _hoja_pagados(libro.create_sheet(), [c for c in casos if c['veredicto'][0] == '1'], nombres)
    _hoja_unificados(libro.create_sheet(), datos.get('unificados') or [])
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


def texto_filtros(programas=(), grupos=(), veredictos=(), documento=None):
    partes = []
    if programas:
        partes.append('Programa: ' + ', '.join(str(p) for p in programas))
    if grupos:
        partes.append('Grupo: ' + ', '.join(GRUPOS.get(str(g)[0], str(g)) for g in grupos))
    if veredictos:
        partes.append('Veredicto: ' + ', '.join(veredictos))
    if documento:
        partes.append(f'Documento: {documento}')
    return ' · '.join(partes)
