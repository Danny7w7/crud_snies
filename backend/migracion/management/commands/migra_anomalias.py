from django.core.management.base import BaseCommand

from migracion.anomalias import generar_anomalias, resumen_anomalias
from migracion.anomalias_api import _donde, _texto_filtros
from migracion.anomalias_reporte import libro_excel
from migracion.conexiones import conectar_destino
from migracion.views import _consulta, _uno

FILTROS = ('severidad', 'gravedad', 'responsable', 'tipo', 'categoria', 'estado_migracion', 'periodo', 'programa')


class Command(BaseCommand):
    help = ('Genera el reporte de anomalias de SWA (anomalias.anomalia) y, con --excel, lo exporta. '
            'Ej.: migra_anomalias --excel anomalias_ALTA.xlsx --severidad ALTA')

    def add_arguments(self, parser):
        parser.add_argument('--excel', help='Ruta del .xlsx (hojas Resumen, Casos y Por persona)')
        parser.add_argument('--sin-regenerar', action='store_true',
                            help='Exporta lo ya generado sin volver a calcular las reglas')
        parser.add_argument('--incluir-informativas', action='store_true',
                            help='Incluye los casos informativos (por defecto se excluyen)')
        for clave in FILTROS:
            parser.add_argument(f'--{clave.replace("_", "-")}', dest=clave, help=f'Filtro por {clave}')

    def handle(self, *args, **options):
        if options['sin_regenerar']:
            with conectar_destino() as conn:
                resumen = resumen_anomalias(conn.cursor())
        else:
            resumen = generar_anomalias(log=self.stdout.write)
            for t in resumen['tipos']:
                self.stdout.write(f"{t['severidad']:5} {t['tipo']:36} {t['casos']:>7} casos  "
                                  f"{t['personas']:>6} personas")
        self.stdout.write(f"Anomalías: {resumen['total_casos']} (alta {resumen['casos_alta']}) · "
                          f"informativos: {resumen['casos_informativos']} · personas: {resumen['personas']}")
        if not options['excel']:
            return
        params = {k: str(options[k]) for k in FILTROS if options.get(k)}
        if not options['incluir_informativas']:
            params['ocultar_info'] = '1'
        donde, valores = _donde(params)
        filas = _consulta(f'SELECT a.* FROM anomalias.anomalia a {donde}', valores)
        meta = {
            'fecha': (_uno('SELECT max(fecha) AS f FROM anomalias.ejecucion') or {}).get('f'),
            'copia': (_uno('SELECT max(fecha) AS f FROM migra.copia_swa_log') or {}).get('f'),
            'filtros': _texto_filtros(params),
        }
        for k in ('fecha', 'copia'):
            if meta[k] is not None:
                meta[k] = meta[k].replace(tzinfo=None)
        with open(options['excel'], 'wb') as f:
            f.write(libro_excel(filas, meta))
        self.stdout.write(f"Excel ({len(filas)} casos): {options['excel']}")
