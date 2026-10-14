"""Excel del reporte de anomalias: Resumen por regla, Casos (uno por fila, legible) y Por persona."""
import csv
import io
from collections import Counter, defaultdict
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .anomalias import CATALOGO, CATEGORIAS, SEVERIDADES, nombre_grupo, texto_caso

ORDEN_SEVERIDAD = {'ALTA': 0, 'MEDIA': 1, 'BAJA': 2, 'INFO': 3}
MIGRACION = {'SE_MIGRA': 'Sí', 'EN_REVISION': 'En revisión', 'NO_SE_MIGRA': 'No'}
COLOR_SEVERIDAD = {'ALTA': 'F8D7DA', 'MEDIA': 'FFF3CD', 'BAJA': 'D6EAF8', 'INFO': 'E9ECEF'}
COLOR_GRAVEDAD = {'Crítica': 'E57373', 'Alta': 'F8BBD0', 'Moderada': 'FFF3CD'}
ENCABEZADO = PatternFill('solid', fgColor='1F3A5F')
AJUSTE = Alignment(wrap_text=True, vertical='top')


def _periodo(p):
    return f'{p // 10}-{p % 10}' if p else ''


def _persona(f):
    return f['persona_swa'] or f['estudiante']


def _tabla(hoja, fila, columnas, filas, anchos):
    """Escribe encabezado + filas desde `fila`; columnas de severidad se colorean."""
    for c, titulo in enumerate(columnas, 1):
        celda = hoja.cell(fila, c, titulo)
        celda.font = Font(bold=True, color='FFFFFF')
        celda.fill = ENCABEZADO
        celda.alignment = Alignment(wrap_text=True, vertical='center')
    for i, valores in enumerate(filas, fila + 1):
        for c, v in enumerate(valores, 1):
            celda = hoja.cell(i, c, v)
            celda.alignment = AJUSTE
    for c, ancho in enumerate(anchos, 1):
        hoja.column_dimensions[get_column_letter(c)].width = ancho
    return fila + len(filas)


def _colorear_severidad(hoja, columna, desde, hasta):
    invertido = {v: k for k, v in SEVERIDADES.items()}
    for i in range(desde, hasta + 1):
        celda = hoja.cell(i, columna)
        clave = invertido.get(celda.value)
        if clave:
            celda.fill = PatternFill('solid', fgColor=COLOR_SEVERIDAD[clave])


def _hoja_resumen(hoja, filas, meta):
    hoja.title = 'Resumen'
    anomalias = [f for f in filas if f['severidad'] != 'INFO']
    personas = {_persona(f) for f in anomalias}
    migran = {_persona(f) for f in anomalias if f['estado_migracion'] == 'SE_MIGRA'}
    hoja['A1'] = 'Reporte de anomalías de SWA'
    hoja['A1'].font = Font(bold=True, size=14)
    lineas = [
        ('Generado', meta.get('fecha')),
        ('Copia de SWA', meta.get('copia')),
        ('Filtros', meta.get('filtros') or 'Ninguno (reporte completo)'),
        ('Anomalías (sin informativas)', len(anomalias)),
        ('   de severidad alta', sum(f['severidad'] == 'ALTA' for f in filas)),
        ('Personas con anomalías', len(personas)),
        ('   de ellas se migran al ERP', len(migran)),
        ('Casos informativos (contexto, no son anomalías)', len(filas) - len(anomalias)),
    ]
    for i, (k, v) in enumerate(lineas, 3):
        hoja.cell(i, 1, k).font = Font(bold=True)
        hoja.cell(i, 2, v.strftime('%Y-%m-%d %H:%M') if isinstance(v, datetime) else v)
    notas = [
        'Cómo leerlo: cada caso aparece en una sola regla. Todo se cuenta por persona (sus varios códigos SWA '
        'cuentan una vez); las homologaciones no exigen matrícula, pago ni prerrequisitos.',
        'Hoja "Casos": un caso por fila, con el hallazgo en palabras y quién debe resolverlo. '
        'Hoja "Por persona": todas las anomalías de cada estudiante en una sola fila.',
        'Saldos a favor: "Beneficio por encima del 100 %" es la CAUSA (por recibo) y "Saldo a favor pendiente" el '
        'BALANCE (por persona); una persona puede estar en ambas, así que sus montos NO se suman entre sí.',
    ]
    fila = len(lineas) + 4
    for n in notas:
        hoja.cell(fila, 1, n).alignment = AJUSTE
        hoja.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=8)
        hoja.row_dimensions[fila].height = 30
        fila += 1

    grav = Counter((f['tipo'], f['gravedad']) for f in filas if f['gravedad'])
    if grav:
        hoja.cell(fila + 1, 1, 'Saldos a favor y beneficios por gravedad').font = Font(bold=True)
        datos_g = [[CATALOGO[t]['titulo'], g[2:], n] for (t, g), n in sorted(grav.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
        fila = _tabla(hoja, fila + 2, ['Regla', 'Gravedad', 'Casos'], datos_g, [30, 20, 38]) + 1

    casos = Counter(f['tipo'] for f in filas)
    por_regla = defaultdict(set)
    migran_regla = Counter()
    for f in filas:
        por_regla[f['tipo']].add(_persona(f))
        migran_regla[f['tipo']] += f['estado_migracion'] == 'SE_MIGRA'
    reglas = sorted(CATALOGO.items(), key=lambda kv: (ORDEN_SEVERIDAD[kv[1]['severidad']],
                                                      list(CATEGORIAS).index(kv[1]['categoria']), -casos[kv[0]]))
    datos = [[SEVERIDADES[r['severidad']], CATEGORIAS[r['categoria']], r['titulo'], r['responsable'], casos[t],
              len(por_regla[t]), migran_regla[t], r['que_paso'], r['riesgo'], r['recomendacion']]
             for t, r in reglas]
    inicio = fila + 1
    fin = _tabla(hoja, inicio, ['Severidad', 'Categoría', 'Regla', 'Responsable', 'Casos', 'Personas',
                                'Casos que se migran', 'Qué pasó en SWA', 'Riesgo', 'Qué hacer'],
                 datos, [16, 20, 38, 18, 9, 10, 11, 60, 45, 45])
    _colorear_severidad(hoja, 1, inicio + 1, fin)
    hoja.column_dimensions['A'].width = 30
    hoja.freeze_panes = hoja.cell(inicio + 1, 1)


COLUMNAS_CASOS = ['Severidad', 'Gravedad', 'Responsable', 'Regla', 'Documento', 'Nombre', 'Código SWA', 'Periodo',
                  'Programa', 'Asignatura', 'Tipo de nota', 'Hallazgo', '¿Se migra?', '¿La nota viaja al ERP?',
                  'Qué hacer']


def filas_casos(filas):
    """Un caso por fila, en palabras (hoja "Casos" del Excel y el CSV)."""
    filas = sorted(filas, key=lambda f: (ORDEN_SEVERIDAD[f['severidad']], f['gravedad'] or '9', f['tipo'],
                                         f['nombre'] or '', f['periodo'] or 0))
    datos = []
    for f in filas:
        regla = CATALOGO.get(f['tipo'], {})
        nota = '' if f['nota_migra'] is None else ('Sí' if f['nota_migra'] else 'No')
        datos.append([
            SEVERIDADES.get(f['severidad'], f['severidad']), (f['gravedad'] or '')[2:], f['responsable'],
            regla.get('titulo', f['tipo']),
            f['documento'] or '', f['nombre'] or '', f['estudiante'], _periodo(f['periodo']),
            f"{f['programa'] or ''} · {f['programa_nombre'] or ''}" if f['programa'] else '',
            f['asignatura_nombre'] or (str(f['asignatura']) if f['asignatura'] else ''),
            nombre_grupo(f['grupo']), texto_caso(f['tipo'], f['detalle']),
            MIGRACION.get(f['estado_migracion'], f['estado_migracion']), nota, regla.get('recomendacion', ''),
        ])
    return datos


def csv_casos(filas):
    """CSV (separador ; y BOM, para que Excel lo abra con tildes) con las mismas columnas de la hoja Casos."""
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=';')
    escritor.writerow(COLUMNAS_CASOS)
    escritor.writerows(filas_casos(filas))
    return ('﻿' + salida.getvalue()).encode('utf-8')


def _hoja_casos(hoja, filas):
    hoja.title = 'Casos'
    fin = _tabla(hoja, 1, COLUMNAS_CASOS, filas_casos(filas),
                 [12, 11, 18, 36, 14, 30, 13, 9, 34, 30, 16, 70, 11, 11, 50])
    _colorear_severidad(hoja, 1, 2, fin)
    for i in range(2, fin + 1):
        celda = hoja.cell(i, 2)
        if celda.value:
            celda.fill = PatternFill('solid', fgColor=COLOR_GRAVEDAD.get(celda.value, 'FFFFFF'))
    hoja.freeze_panes = 'G2'
    hoja.auto_filter.ref = f'A1:{get_column_letter(15)}{max(fin, 1)}'


def _hoja_personas(hoja, filas):
    hoja.title = 'Por persona'
    grupos = defaultdict(list)
    for f in filas:
        grupos[_persona(f)].append(f)
    datos = []
    for casos in grupos.values():
        sev = Counter(f['severidad'] for f in casos)
        reglas = Counter(CATALOGO.get(f['tipo'], {}).get('titulo', f['tipo']) for f in casos)
        primero = casos[0]
        datos.append([
            primero['documento'] or '', primero['nombre'] or '',
            ', '.join(sorted({f['estudiante'] for f in casos})),
            MIGRACION.get(primero['estado_migracion'], primero['estado_migracion']),
            len(casos) - sev['INFO'], sev['ALTA'], sev['MEDIA'], sev['BAJA'], sev['INFO'],
            ', '.join(sorted({f['responsable'] for f in casos if f['severidad'] != 'INFO'})),
            '\n'.join(f'{t} ({n})' for t, n in reglas.most_common()),
        ])
    datos.sort(key=lambda d: (-d[5], -d[4], d[1]))
    fin = _tabla(hoja, 1, ['Documento', 'Nombre', 'Códigos SWA', '¿Se migra?', 'Anomalías', 'Alta', 'Media',
                           'Baja', 'Informativas', 'Responsables', 'Reglas (casos)'],
                 datos, [14, 32, 24, 11, 11, 7, 7, 7, 12, 30, 70])
    hoja.freeze_panes = 'C2'
    hoja.auto_filter.ref = f'A1:K{max(fin, 1)}'


def libro_excel(filas, meta):
    """`filas`: filas de anomalias.anomalia (dicts). `meta`: fecha, copia y filtros (texto)."""
    libro = Workbook()
    _hoja_resumen(libro.active, filas, meta)
    _hoja_casos(libro.create_sheet(), filas)
    _hoja_personas(libro.create_sheet(), filas)
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()
