"""API de "Pagos del periodo". A diferencia del resto de la migracion, lee SWA directo (solo lectura, ver
`conectar_swa`): el periodo en curso cambia a diario y la copia local queda desactualizada."""
from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response

from .anomalias_api import XLSX
from .depuracion import PERIODO_CORTE
from .pagos_pdf import filas_vista, pdf_pagos
from .pagos_periodo import ANOMALIAS, GRUPOS, ORDEN_SEVERIDAD, consultar, libro_excel, resumen, texto_filtros
from .views import MigracionView

# columnas de cada caso que usa la pantalla (el Excel lleva el detalle completo)
COLUMNAS_CASO = ('veredicto', 'nid', 'documento', 'documentos_unificados', 'nombre', 'codigos', 'programa', 'estado_historico', 'asignaturas', 'con_nota',
                 'inscrito_por', 'recibo', 'estado_recibo', 'neto', 'dinero', 'sin_dinero', 'faltante', 'creditos',
                 'est_vencido', 'est_cuotas_vencidas', 'est_por_vencer', 'ter_pendiente', 'terceros', 'sin_dinero_detalle',
                 'respaldo_en_dinero', 'pago_otra_carrera')
COLUMNAS_ANOMALIA = ('anomalia', 'severidad', 'documento', 'nombre', 'programa', 'numero', 'veredicto_persona', 'bruto',
                     'neto', 'dinero', 'valor_anomalia', 'explicacion')


def _filtros(params):
    lista = lambda clave: [v for v in params.getlist(clave) if v.strip()]
    return {
        'programas': [int(p) for p in lista('programa') if p.isdigit()],
        'grupos': [g for g in lista('grupo') if g in GRUPOS],
        'veredictos': lista('veredicto'),
        'documento': (params.get('documento') or '').strip() or None,
    }


def _consultar(request):
    periodo = request.query_params.get('periodo') or PERIODO_CORTE
    filtros = _filtros(request.query_params)
    return consultar(periodo, **filtros), filtros


class PagosPeriodoView(MigracionView):
    def get(self, request):
        try:
            datos, _ = _consultar(request)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        por_veredicto, por_anomalia = resumen(datos)
        nombres = datos['programas']
        return Response({
            'periodo': datos['periodo'],
            'fecha': datos['fecha'],
            'grupos': GRUPOS,
            'personas': len({c['nid'] for c in datos['casos']}),
            'unificados': len(datos['unificados']),
            'veredictos': [{'veredicto': v, 'grupo': v[0], 'casos': d['casos'], 'personas': len(d['personas']),
                            'asignaturas': d['asignaturas'], 'con_nota': d['con_nota'], 'neto': d['neto'],
                            'dinero': d['dinero'], 'sin_dinero': d['sin_dinero'], 'faltante': d['faltante']}
                           for v, d in sorted(por_veredicto.items())],
            'anomalias_resumen': [{'codigo': c, 'titulo': t, 'que': q, 'severidad': s, 'casos': d['casos'],
                                   'personas': len(d['personas']), 'valor': d['valor']}
                                  for c, (t, q) in ANOMALIAS.items()
                                  for (cc, s), d in sorted(por_anomalia.items(), key=lambda kv: ORDEN_SEVERIDAD[kv[0][1]])
                                  if cc == c],
            'programas': sorted({c['programa'] for c in datos['casos'] if c['programa'] is not None}),
            'nombres_programa': nombres,
            'casos': [{k: c[k] for k in COLUMNAS_CASO} for c in datos['casos']],
            'anomalias': [{k: a[k] for k in COLUMNAS_ANOMALIA} for a in datos['anomalias']],
        })


class PagosPeriodoExportarView(MigracionView):
    def get(self, request):
        try:
            datos, filtros = _consultar(request)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        nombre = f"pagos_matricula_{datos['periodo'] // 10}_{datos['periodo'] % 10}"
        respuesta = HttpResponse(libro_excel(datos, texto_filtros(**filtros)), content_type=XLSX)
        respuesta['Content-Disposition'] = f'attachment; filename="{nombre}.xlsx"'
        return respuesta


class PagosPeriodoPdfView(MigracionView):
    """PDF de la vista (casos o anomalías) con los filtros de la pantalla; si el cuerpo trae `seleccion`, solo esas
    filas. POST porque la selección puede ser larga."""

    def post(self, request):
        try:
            datos, filtros = _consultar(request)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        vista = 'anomalias' if request.data.get('vista') == 'anomalias' else 'casos'
        seleccion = [str(s) for s in request.data.get('seleccion') or []]
        contenido = pdf_pagos(datos, texto_filtros(**filtros), vista, request.data.get('anomalia') or None, seleccion)
        nombre = f"pagos_{vista}_{datos['periodo'] // 10}_{datos['periodo'] % 10}"
        respuesta = HttpResponse(contenido, content_type='application/pdf')
        respuesta['Content-Disposition'] = f'attachment; filename="{nombre}.pdf"'
        return respuesta


def _datos_vista(datos, vista, anomalia, seleccion):
    """Recorta los datos a lo que muestra la vista: las filas elegidas (o filtradas) y, de las mismas personas, sus
    casos o anomalías y unificaciones, para que el Excel cuadre con el PDF."""
    if not seleccion and (vista == 'casos' or not anomalia):
        return datos
    filas = filas_vista(datos, vista, anomalia, seleccion)
    nids = {f['nid'] for f in filas}
    casos = filas if vista == 'casos' else [c for c in datos['casos'] if c['nid'] in nids]
    anomalias = filas if vista == 'anomalias' else [a for a in datos['anomalias'] if a['nid'] in nids]
    return {**datos, 'casos': casos, 'anomalias': anomalias,
            'unificados': [u for u in datos['unificados'] if u['real'] in nids]}


class PagosPeriodoExcelVistaView(MigracionView):
    """Excel de la vista con los mismos filtros y selección que el PDF (mismo cuerpo que `PagosPeriodoPdfView`)."""

    def post(self, request):
        try:
            datos, filtros = _consultar(request)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        vista = 'anomalias' if request.data.get('vista') == 'anomalias' else 'casos'
        anomalia = request.data.get('anomalia') or None
        seleccion = [str(s) for s in request.data.get('seleccion') or []]
        texto = ' · '.join(x for x in (
            texto_filtros(**filtros), anomalia and vista == 'anomalias' and f'Anomalía: {anomalia}',
            seleccion and f'{len(seleccion)} filas elegidas en pantalla ({vista})') if x)
        contenido = libro_excel(_datos_vista(datos, vista, anomalia if vista == 'anomalias' else None, seleccion), texto)
        nombre = f"pagos_{vista}_{datos['periodo'] // 10}_{datos['periodo'] % 10}"
        respuesta = HttpResponse(contenido, content_type=XLSX)
        respuesta['Content-Disposition'] = f'attachment; filename="{nombre}.xlsx"'
        return respuesta
