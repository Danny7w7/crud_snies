"""API del reporte de anomalias (vista "Anomalías"). Solo lee `anomalias.*`; generar reconstruye el reporte."""
import re
import unicodedata

from django.http import HttpResponse
from rest_framework.response import Response

from .anomalias import CATALOGO, SEVERIDADES, generar_anomalias, nombre_grupo, resumen_anomalias, texto_caso
from .anomalias_reporte import csv_casos, libro_excel
from .conexiones import conectar_destino
from .views import MigracionView, _consulta, _paginado, _uno

FILTROS = {
    'tipo': 'a.tipo = %(tipo)s',
    'categoria': 'a.categoria = %(categoria)s',
    'severidad': 'a.severidad = %(severidad)s',
    'responsable': 'a.responsable = %(responsable)s',
    'gravedad': 'a.gravedad = %(gravedad)s',
    'estado_migracion': 'a.estado_migracion = %(estado_migracion)s',
    'periodo': 'a.periodo = %(periodo)s',
    'programa': 'a.programa = %(programa)s',
}
ETIQUETAS_FILTRO = {'tipo': 'Regla', 'categoria': 'Categoría', 'severidad': 'Severidad', 'gravedad': 'Gravedad',
                    'responsable': 'Responsable',
                    'estado_migracion': 'Migración', 'periodo': 'Periodo', 'programa': 'Programa', 'q': 'Búsqueda'}
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _para_archivo(valor):
    """'1 Crítica' -> 'critica': nombre de archivo sin tildes ni espacios (el encabezado HTTP lo exige)."""
    sin_tildes = unicodedata.normalize('NFKD', valor).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '_', sin_tildes.lower()).strip('_0123456789')


def _generado():
    return (_uno("SELECT to_regclass('anomalias.anomalia') IS NOT NULL AS ok") or {}).get('ok')


def _donde(params):
    filtros, valores = [], {}
    for clave, sql in FILTROS.items():
        valor = (params.get(clave) or '').strip()
        if valor:
            filtros.append(sql)
            valores[clave] = int(valor) if clave in ('periodo', 'programa') and valor.isdigit() else valor
    if params.get('ocultar_info') == '1' and not params.get('severidad') and not params.get('tipo'):
        filtros.append("a.severidad <> 'INFO'")
    q = (params.get('q') or '').strip()
    if q:
        filtros.append("""(a.estudiante LIKE %(q_ini)s OR a.documento LIKE %(q_ini)s OR a.nid LIKE %(q_ini)s
                           OR unaccent(a.nombre) ILIKE unaccent(%(q_like)s))""")
        valores.update(q_ini=f'{q}%', q_like=f'%{q}%')
    return ('WHERE ' + ' AND '.join(filtros)) if filtros else '', valores


def _texto_filtros(params):
    partes = []
    for clave, etiqueta in ETIQUETAS_FILTRO.items():
        valor = (params.get(clave) or '').strip()
        if not valor:
            continue
        if clave == 'tipo':
            valor = CATALOGO.get(valor, {}).get('titulo', valor)
        elif clave == 'severidad':
            valor = SEVERIDADES.get(valor, valor)
        partes.append(f'{etiqueta}: {valor}')
    if params.get('ocultar_info') == '1' and not params.get('severidad') and not params.get('tipo'):
        partes.append('Sin casos informativos')
    return ' · '.join(partes)


class AnomaliasResumenView(MigracionView):
    def get(self, request):
        if not _generado():
            return Response({'generado': False})
        with conectar_destino() as conn:
            resumen = resumen_anomalias(conn.cursor())
        resumen['generado'] = True
        resumen['fecha'] = (_uno('SELECT max(fecha) AS f FROM anomalias.ejecucion') or {}).get('f')
        resumen['ultima_copia_swa'] = (_uno('SELECT max(fecha) AS f FROM migra.copia_swa_log') or {}).get('f')
        resumen['programas'] = _consulta("""
            SELECT programa, min(programa_nombre) AS nombre, count(*) AS casos
            FROM anomalias.anomalia WHERE programa IS NOT NULL GROUP BY 1 ORDER BY 1
        """)
        return Response(resumen)


class AnomaliasView(MigracionView):
    def get(self, request):
        if not _generado():
            return Response({'count': 0, 'page': 1, 'page_size': 25, 'results': []})
        donde, valores = _donde(request.query_params)
        data = _paginado(f"""
            SELECT a.* FROM anomalias.anomalia a {donde}
            ORDER BY CASE a.severidad WHEN 'ALTA' THEN 0 WHEN 'MEDIA' THEN 1 WHEN 'BAJA' THEN 2 ELSE 3 END,
                     a.gravedad NULLS LAST, a.tipo,
                     a.periodo DESC NULLS LAST, a.id
        """, valores, request)
        for f in data['results']:
            f['hallazgo'] = texto_caso(f['tipo'], f['detalle'])
            f['tipo_nota'] = nombre_grupo(f['grupo'])
        return Response(data)


class AnomaliasExportarView(MigracionView):
    """Excel (Resumen, Casos, Por persona) o, con formato=csv, solo los casos; siempre con los filtros recibidos."""

    def get(self, request):
        donde, valores = _donde(request.query_params)
        filas = _consulta(f'SELECT a.* FROM anomalias.anomalia a {donde}', valores)
        nombre = 'anomalias_swa' + ''.join('_' + _para_archivo(request.query_params[k])
                                           for k in ('severidad', 'gravedad', 'tipo')
                                           if request.query_params.get(k))
        if request.query_params.get('formato') == 'csv':
            respuesta = HttpResponse(csv_casos(filas), content_type='text/csv; charset=utf-8')
            respuesta['Content-Disposition'] = f'attachment; filename="{nombre}.csv"'
            return respuesta
        meta = {
            'fecha': (_uno('SELECT max(fecha) AS f FROM anomalias.ejecucion') or {}).get('f'),
            'copia': (_uno('SELECT max(fecha) AS f FROM migra.copia_swa_log') or {}).get('f'),
            'filtros': _texto_filtros(request.query_params),
        }
        for k in ('fecha', 'copia'):
            if meta[k] is not None:
                meta[k] = meta[k].replace(tzinfo=None)
        respuesta = HttpResponse(libro_excel(filas, meta), content_type=XLSX)
        respuesta['Content-Disposition'] = f'attachment; filename="{nombre}.xlsx"'
        return respuesta


class AnomaliasGenerarView(MigracionView):
    def post(self, request):
        generar_anomalias(log=lambda *_: None)
        return AnomaliasResumenView().get(request)
