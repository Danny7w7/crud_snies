"""PDF de "Pagos del periodo": los casos o las anomalías que el usuario filtró o eligió en la pantalla.

Usa los mismos datos de `pagos_periodo.consultar` (SWA directo, solo lectura). Trae documentos y nombres: es de
uso interno y no se guarda en el repositorio.
"""
import io
from collections import defaultdict
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .pagos_periodo import COLOR_GRUPO, ESTADOS_RECIBO, GRUPOS, ORDEN_SEVERIDAD, SEVERIDADES, _periodo

COLOR_SEVERIDAD_PDF = {'ALTA': 'F8D7DA', 'MEDIA': 'FFF3CD', 'BAJA': 'D6EAF8', 'INFO': 'E9ECEF'}
_base = getSampleStyleSheet()
TITULO = ParagraphStyle('titulo', parent=_base['Title'], fontSize=15, alignment=0, spaceAfter=4)
TEXTO = ParagraphStyle('texto', parent=_base['Normal'], fontSize=8.5, leading=11)
CELDA = ParagraphStyle('celda', parent=_base['Normal'], fontSize=7, leading=8.6)
CABECERA = ParagraphStyle('cabecera', parent=CELDA, fontName='Helvetica-Bold', textColor=colors.white)


def clave_caso(c):
    return f"{c['documento']}|{c['programa']}"


def clave_anomalia(a):
    return f"{a['anomalia'].split()[0]}|{a['numero']}|{a['documento']}"


def _p(texto, estilo=CELDA):
    return Paragraph(escape(str(texto if texto is not None else '')).replace('\n', '<br/>'), estilo)


def _pesos(n):
    return '—' if n is None else '$' + f'{round(n):,}'.replace(',', '.')


def _prog(programa, nombres):
    return '' if programa is None else f'{programa} · {nombres.get(programa, "")}'


def _tabla(cabecera, filas, anchos, colores_fila=()):
    datos = [[_p(c, CABECERA) for c in cabecera]] + [[x if isinstance(x, Paragraph) else _p(x) for x in f] for f in filas]
    t = Table(datos, colWidths=[a * cm for a in anchos], repeatRows=1, hAlign='LEFT')
    estilo = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F3A5F')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#C8CDD3')),
        ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]
    for i, color in enumerate(colores_fila, 1):
        if color:
            estilo.append(('BACKGROUND', (0, i), (0, i), colors.HexColor('#' + color)))
    t.setStyle(TableStyle(estilo))
    return t


def _pie(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(colors.grey)
    canvas.drawString(1.2 * cm, 0.8 * cm, 'Uso interno: contiene datos personales. Fuente: SWA producción (solo lectura).')
    canvas.drawRightString(landscape(letter)[0] - 1.2 * cm, 0.8 * cm, f'Página {doc.page}')
    canvas.restoreState()


def _detalle_caso(c):
    partes = []
    if c.get('creditos'):
        t = f"Crédito {c['creditos']}"
        if c.get('est_vencido'):
            t += f" · vencido {_pesos(c['est_vencido'])} ({c['est_cuotas_vencidas']} cuota/s)"
        if c.get('est_por_vencer'):
            t += f" · por vencer {_pesos(c['est_por_vencer'])}"
        if c.get('ter_pendiente'):
            t += f" · falta giro de {c['terceros']} {_pesos(c['ter_pendiente'])}"
        partes.append(t)
    if c.get('sin_dinero_detalle'):
        partes.append(c['sin_dinero_detalle'])
    if c.get('respaldo_en_dinero'):
        partes.append(f"Respaldo: {c['respaldo_en_dinero']}")
    if c.get('pago_otra_carrera'):
        partes.append(f"Pago en otro programa: {c['pago_otra_carrera']}")
    return '\n'.join(partes)


def _casos(casos, nombres):
    resumen = defaultdict(lambda: [0, set(), 0, 0, 0, 0])
    for c in casos:
        r = resumen[c['veredicto']]
        r[0] += 1
        r[1].add(c['documento'])
        for i, k in enumerate(('neto', 'dinero', 'sin_dinero', 'faltante'), 2):
            r[i] += c[k] or 0
    tabla_resumen = _tabla(
        ['Veredicto', 'Casos', 'Personas', 'Valor neto', 'Pagado en dinero', 'Sin dinero', 'Faltante'],
        [[v, r[0], len(r[1]), _pesos(r[2]), _pesos(r[3]), _pesos(r[4]), _pesos(r[5])] for v, r in sorted(resumen.items())],
        [9.5, 1.6, 1.8, 3, 3, 3, 3], [COLOR_GRUPO.get(v[0]) for v in sorted(resumen)])
    filas = []
    for c in casos:
        recibo = 'Sin recibo' if c['recibo'] is None else \
            f"{c['recibo']}\n{ESTADOS_RECIBO.get(c['estado_recibo'], c['estado_recibo'])}"
        filas.append([
            c['veredicto'],
            f"{c['nombre']}\ndoc. {c['documento']} · {c['codigos']}\nhistórico {c['estado_historico'] or 'sin fila'}"
            + (f"\nunificado con doc. {c['documentos_unificados']}" if c.get('documentos_unificados') else ''),
            _prog(c['programa'], nombres),
            f"{c['asignaturas']} · {c['con_nota']} con nota\n{c['inscrito_por'] or ''}",
            recibo,
            f"Neto {_pesos(c['neto'])}\nDinero {_pesos(c['dinero'])}\nSin dinero {_pesos(c['sin_dinero'])}"
            + (f"\nFalta {_pesos(c['faltante'])}" if (c['faltante'] or 0) > 0 else ''),
            _detalle_caso(c),
        ])
    detalle = _tabla(['Veredicto', 'Estudiante', 'Programa', 'Asignaturas', 'Recibo', 'Valores', 'Detalle'], filas,
                     [3.6, 4.6, 3.4, 3.4, 2, 3.2, 5.4], [COLOR_GRUPO.get(c['veredicto'][0]) for c in casos])
    return tabla_resumen, detalle


def _anomalias(anomalias, nombres):
    resumen = defaultdict(lambda: [0, set(), 0])
    for a in anomalias:
        r = resumen[(a['anomalia'], a['severidad'])]
        r[0] += 1
        r[1].add(a['documento'])
        r[2] += a['valor_anomalia'] or 0
    orden = sorted(resumen, key=lambda k: (ORDEN_SEVERIDAD[k[1]], k[0]))
    tabla_resumen = _tabla(['Anomalía', 'Severidad', 'Casos', 'Personas', 'Valor'],
                           [[k[0], SEVERIDADES[k[1]], resumen[k][0], len(resumen[k][1]), _pesos(resumen[k][2])]
                            for k in orden],
                           [10, 2.4, 1.8, 1.8, 3.4], [COLOR_SEVERIDAD_PDF.get(k[1]) for k in orden])
    filas = [[
        SEVERIDADES[a['severidad']], a['anomalia'],
        f"{a['nombre']}\ndoc. {a['documento']}\n{_prog(a['programa'], nombres)}",
        f"{a['numero']}\n{a['veredicto_persona'] or ''}",
        f"Bruto {_pesos(a['bruto'])}\nNeto {_pesos(a['neto'])}\nDinero {_pesos(a['dinero'])}\n"
        f"Anomalía {_pesos(a['valor_anomalia'])}",
        a['explicacion'],
    ] for a in anomalias]
    detalle = _tabla(['Sev.', 'Anomalía', 'Estudiante', 'Recibo', 'Valores', 'Explicación'], filas,
                     [1.6, 3.6, 5, 3.6, 3.2, 8.6], [COLOR_SEVERIDAD_PDF.get(a['severidad']) for a in anomalias])
    return tabla_resumen, detalle


def filas_vista(datos, vista='casos', anomalia=None, seleccion=()):
    """Filas de la vista (casos o anomalías) con el filtro de anomalía; si hay selección, solo esas."""
    elegidas = set(seleccion)
    if vista == 'anomalias':
        filas = [a for a in datos['anomalias'] if not anomalia or a['anomalia'].split()[0] == anomalia]
        return [a for a in filas if clave_anomalia(a) in elegidas] if elegidas else filas
    return [c for c in datos['casos'] if clave_caso(c) in elegidas] if elegidas else datos['casos']


def pdf_pagos(datos, filtros, vista='casos', anomalia=None, seleccion=()):
    """PDF de la vista (casos o anomalías) con los filtros; si hay selección, solo esas filas."""
    nombres = datos['programas']
    elegidas = set(seleccion)
    filas = filas_vista(datos, vista, anomalia, seleccion)
    if vista == 'anomalias':
        titulo = 'Anomalías de descuentos'
        resumen, detalle = _anomalias(filas, nombres)
        if anomalia:
            filtros = ' · '.join(x for x in (filtros, f'Anomalía: {anomalia}') if x)
    else:
        titulo = 'Casos de pago de matrícula'
        resumen, detalle = _casos(filas, nombres)

    salida = io.BytesIO()
    doc = SimpleDocTemplate(salida, pagesize=landscape(letter), leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.2 * cm, bottomMargin=1.4 * cm,
                            title=f"{titulo} {_periodo(datos['periodo'])}", author='Migración SWA → ERP')
    personas = len({f['documento'] for f in filas})
    alcance = f'{len(elegidas)} filas elegidas en pantalla' if elegidas else 'todas las filas con los filtros'
    historia = [
        Paragraph(f"{titulo} — periodo {_periodo(datos['periodo'])}", TITULO),
        _p(f"Consultado {datos['fecha'].strftime('%Y-%m-%d %H:%M')} · Filtros: {filtros or 'ninguno'} · "
           f"Alcance: {alcance} · {len(filas)} filas, {personas} personas", TEXTO),
        _p('Pago comprobado = dinero que entró a una cuenta real (Davivienda, Efecty, PayGateway, tarjeta). Saldos a '
           'favor, pagos de periodos anteriores, ajustes de menor cuantía, becas y convenios no son dinero. Grupos: '
           + '; '.join(f'{g} {t}' for g, t in GRUPOS.items()) + '.', TEXTO),
        Spacer(1, 0.3 * cm), resumen, Spacer(1, 0.4 * cm), detalle,
    ]
    if not filas:
        historia[-1] = _p('Sin filas con estos filtros.', TEXTO)
    doc.build(historia, onFirstPage=_pie, onLaterPages=_pie)
    return salida.getvalue()
