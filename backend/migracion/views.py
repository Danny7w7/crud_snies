"""API de la migracion SWA -> ERP.

Todas las vistas leen/escriben la base local de migracion (nunca SWA) y usan la
misma contrasena de consulta que el resto del sistema.
"""
import json

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.views import ConsultaPasswordPermission

from .carga import enviar, revertir
from .casos import CATALOGO_CASOS
from .conexiones import conectar_destino
from .depuracion import depurar
from .metricas import calcular_metricas

TAMANO_PAGINA = 25


def _consulta(sql, params=None):
    with conectar_destino() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        columnas = [d.name for d in cur.description]
        return [dict(zip(columnas, fila)) for fila in cur.fetchall()]


def _uno(sql, params=None):
    filas = _consulta(sql, params)
    return filas[0] if filas else None


def _pagina(request):
    try:
        return max(int(request.query_params.get('page', 1)), 1)
    except ValueError:
        return 1


def _paginado(sql, params, request):
    pagina = _pagina(request)
    total = _uno(f'SELECT count(*) AS n FROM ({sql}) x', params)['n']
    filas = _consulta(f'{sql} LIMIT {TAMANO_PAGINA} OFFSET {(pagina - 1) * TAMANO_PAGINA}', params)
    return {'count': total, 'page': pagina, 'page_size': TAMANO_PAGINA, 'results': filas}


class MigracionView(APIView):
    permission_classes = [ConsultaPasswordPermission]


class ResumenView(MigracionView):
    """Metricas antes/despues, % depuracion, % probabilidad de perdida y lotes."""

    def get(self, request):
        with conectar_destino() as conn:
            metricas = calcular_metricas(conn.cursor())
        metricas['ultima_depuracion'] = (_uno('SELECT max(fecha) AS f FROM migra.depuracion_log') or {}).get('f')
        metricas['ultima_copia_swa'] = (_uno('SELECT max(fecha) AS f FROM migra.copia_swa_log') or {}).get('f')
        metricas['lotes'] = _consulta('SELECT id, inicio, fin, estado, resumen, error FROM migra.lote ORDER BY id DESC LIMIT 20')
        return Response(metricas)


class PersonasView(MigracionView):
    def get(self, request):
        q = (request.query_params.get('q') or '').strip()
        estado = request.query_params.get('estado') or ''
        filtros, params = [], {}
        if q:
            filtros.append("""(p.nid LIKE %(q_ini)s OR %(q)s = ANY(p.codigos_swa) OR unaccent(concat_ws(' ', p.primer_nombre, p.segundo_nombre,
                              p.primer_apellido, p.segundo_apellido)) ILIKE unaccent(%(q_like)s))""")
            params.update(q=q, q_ini=f'{q}%', q_like=f'%{q}%')
        if estado:
            filtros.append('p.estado = %(estado)s')
            params['estado'] = estado
        donde = ('WHERE ' + ' AND '.join(filtros)) if filtros else ''
        sql = f"""
            SELECT p.nid, p.tipo_documento_id, p.nro_documento, p.primer_nombre, p.segundo_nombre,
                   p.primer_apellido, p.segundo_apellido, p.estado, p.accion, p.transformaciones, p.alertas,
                   p.codigo_swa, p.codigos_swa,
                   cardinality(p.swa_ids) AS registros_swa,
                   (SELECT count(*) FROM migra.matricula m WHERE m.nid = p.nid) AS matriculas,
                   (SELECT erp_id FROM migra.mapa x WHERE x.entidad = 'persona' AND x.clave = p.nid) AS erp_id
            FROM migra.persona p {donde}
            ORDER BY p.primer_apellido, p.segundo_apellido, p.primer_nombre
        """
        return Response(_paginado(sql, params, request))


class PersonaDetalleView(MigracionView):
    """Antes (SWA crudo) y despues (depurado/ERP) de una persona."""

    def get(self, request, nid):
        persona = _uno('SELECT * FROM migra.persona WHERE nid = %s', (nid,))
        if not persona:
            return Response({'detail': 'No encontrada'}, status=status.HTTP_404_NOT_FOUND)
        ids = persona['swa_ids']
        antes = {
            'personas': _consulta('SELECT * FROM swa_raw.persona WHERE id = ANY(%s) ORDER BY id', (ids,)),
            'codigos': _consulta("""
                SELECT e.codigo, e.programa, pr.nombre AS programa_nombre, e.pensum, e.estado, e.periodo_ingreso,
                       e.nivel, e.email, e.celular, e.fecha_nacimiento
                FROM swa_raw.estudiante e LEFT JOIN swa_raw.programa pr ON pr.codigo = e.programa
                WHERE e.persona = ANY(%s) ORDER BY e.periodo_ingreso, e.codigo
            """, (ids,)),
            'historico': _consulta("""
                SELECT h.periodo, h.programa, h.estudiante, h.estado, h.nivel
                FROM swa_raw.historico h JOIN swa_raw.estudiante e ON e.codigo = h.estudiante
                WHERE e.persona = ANY(%s) ORDER BY h.periodo, h.programa
            """, (ids,)),
            'registros': _consulta("""
                SELECT r.periodo, r.programa, r.asignatura, a.nombre AS asignatura_nombre, r.grupo, r.estudiante,
                       r.definitiva, r.estado, r.estado_definitiva
                FROM swa_raw.registro r JOIN swa_raw.estudiante e ON e.codigo = r.estudiante
                LEFT JOIN swa_raw.asignatura a ON a.codigo = r.asignatura
                WHERE e.persona = ANY(%s) ORDER BY r.periodo, r.programa, a.nombre
            """, (ids,)),
        }
        despues = {
            'persona': persona,
            'erp_id': (_uno("SELECT erp_id FROM migra.mapa WHERE entidad = 'persona' AND clave = %s", (nid,)) or {}).get('erp_id'),
            'matriculas': _consulta("""
                SELECT m.*, pe.nombre AS periodo_nombre, x.erp_id
                FROM migra.matricula m JOIN migra.periodo pe ON pe.codigo = m.periodo
                LEFT JOIN migra.mapa x ON x.entidad = 'matricula' AND x.clave = m.nid || '|' || m.programa || '|' || m.periodo
                WHERE m.nid = %s ORDER BY m.periodo, m.programa
            """, (nid,)),
            'notas': _consulta("""
                SELECT n.*, a.nombre AS asignatura_nombre
                FROM migra.nota n LEFT JOIN migra.asignatura a ON a.codigo = n.asignatura
                WHERE n.nid = %s ORDER BY n.periodo, n.programa, a.nombre
            """, (nid,)),
            'homologaciones_ingles': _consulta(
                'SELECT * FROM migra.homologacion_ingles WHERE nid = %s ORDER BY programa, modulo', (nid,)),
            'vinculos': _consulta('SELECT * FROM migra.vinculo WHERE nid = %s ORDER BY programa', (nid,)),
            'descartes': _consulta("""
                SELECT d.motivo, d.con_valor, d.datos FROM migra.descarte d
                WHERE d.datos->>'estudiante' IN (SELECT codigo FROM migra.estudiante WHERE nid = %s)
                ORDER BY d.clave
            """, (nid,)),
        }
        casos = _consulta("""
            SELECT id, tipo, severidad, titulo, decision, sugerencia, opciones
            FROM revision.caso WHERE vigente AND (clave = %s OR clave LIKE %s) ORDER BY id
        """, (nid, f'{nid}|%'))
        return Response({'antes': antes, 'despues': despues, 'casos': casos})


class CasosResumenView(MigracionView):
    def get(self, request):
        filas = _consulta("""
            SELECT tipo, entidad,
                   (array_agg(severidad ORDER BY CASE severidad WHEN 'ALTA' THEN 0 WHEN 'MEDIA' THEN 1 ELSE 2 END))[1]
                       AS severidad,
                   count(*) AS casos,
                   count(*) FILTER (WHERE decision IS NULL) AS pendientes, sum(filas_afectadas) AS filas,
                   count(*) FILTER (WHERE decision IS NULL AND sugerencia ? 'decision') AS con_sugerencia
            FROM revision.caso WHERE vigente
            GROUP BY tipo, entidad
            ORDER BY min(CASE severidad WHEN 'ALTA' THEN 0 WHEN 'MEDIA' THEN 1 ELSE 2 END), count(*) DESC
        """)
        for fila in filas:
            fila.update(CATALOGO_CASOS.get(fila['tipo'], {'titulo': fila['tipo'], 'descripcion': '', 'opciones': {}}))
        return Response(filas)


class CasosView(MigracionView):
    def get(self, request):
        filtros, params = ['c.vigente'], {}
        if request.query_params.get('tipo'):
            filtros.append('c.tipo = %(tipo)s')
            params['tipo'] = request.query_params['tipo']
        pendientes = request.query_params.get('pendientes')
        if pendientes == '1':
            filtros.append('c.decision IS NULL')
        elif pendientes == '0':
            filtros.append('c.decision IS NOT NULL')
        if request.query_params.get('q'):
            filtros.append('(c.clave ILIKE %(q)s OR c.datos::text ILIKE %(q)s)')
            params['q'] = f"%{request.query_params['q']}%"
        sql = f"""
            SELECT c.*, p.primer_nombre, p.segundo_nombre, p.primer_apellido, p.segundo_apellido
            FROM revision.caso c
            LEFT JOIN migra.persona p ON p.nid = split_part(c.clave, '|', 1)
            WHERE {' AND '.join(filtros)}
            ORDER BY c.decision IS NOT NULL, c.filas_afectadas DESC, c.id
        """
        data = _paginado(sql, params, request)
        data['programas'] = {str(p['codigo']): p['nombre'] for p in
                             _consulta('SELECT codigo, nombre FROM swa_raw.programa')}
        return Response(data)


class CasoDecisionView(MigracionView):
    def post(self, request, caso_id):
        caso = _uno('SELECT id, opciones FROM revision.caso WHERE id = %s', (caso_id,))
        if not caso:
            return Response({'detail': 'Caso no encontrado'}, status=status.HTTP_404_NOT_FOUND)
        decision = request.data.get('decision')
        if decision is not None and decision not in caso['opciones']:
            return Response({'detail': f'Decision invalida. Opciones: {caso["opciones"]}'},
                            status=status.HTTP_400_BAD_REQUEST)
        correccion = request.data.get('correccion') or {}
        with conectar_destino() as conn:
            conn.execute("""
                UPDATE revision.caso SET decision = %s, correccion = %s::jsonb, comentario = %s,
                       decidido_en = CASE WHEN %s::text IS NULL THEN NULL ELSE now() END
                WHERE id = %s
            """, (decision, json.dumps(correccion), request.data.get('comentario') or '', decision, caso_id))
        return Response(_uno('SELECT * FROM revision.caso WHERE id = %s', (caso_id,)))


class CasosSugerenciaMasivaView(MigracionView):
    """Aplica la decision sugerida a todos los casos pendientes de un tipo (o a los ids dados)."""

    def post(self, request):
        tipo = request.data.get('tipo')
        ids = request.data.get('ids') or []
        if not tipo and not ids:
            return Response({'detail': 'Indique tipo o ids'}, status=status.HTTP_400_BAD_REQUEST)
        filtros, params = ['vigente', 'decision IS NULL', "sugerencia ? 'decision'"], []
        if tipo:
            filtros.append('tipo = %s')
            params.append(tipo)
        if ids:
            filtros.append('id = ANY(%s)')
            params.append(ids)
        with conectar_destino() as conn:
            cur = conn.execute(f"""
                UPDATE revision.caso SET decision = sugerencia->>'decision', correccion = sugerencia,
                       comentario = 'Sugerencia aplicada en bloque', decidido_en = now()
                WHERE {' AND '.join(filtros)} AND (sugerencia->>'decision') = ANY(opciones)
            """, params)
            actualizados = cur.rowcount
        return Response({'actualizados': actualizados})


class DescartesView(MigracionView):
    def get(self, request):
        filtros, params = [], {}
        if request.query_params.get('motivo'):
            filtros.append('motivo = %(motivo)s')
            params['motivo'] = request.query_params['motivo']
        donde = ('WHERE ' + ' AND '.join(filtros)) if filtros else ''
        data = _paginado(f'SELECT * FROM migra.descarte {donde} ORDER BY con_valor DESC, id', params, request)
        data['motivos'] = _consulta("""
            SELECT motivo, entidad, count(*) AS filas, count(*) FILTER (WHERE con_valor) AS con_valor
            FROM migra.descarte GROUP BY 1, 2 ORDER BY 3 DESC
        """)
        return Response(data)


class DepurarView(MigracionView):
    def post(self, request):
        return Response(depurar(log=lambda *_: None))


class EnviarView(MigracionView):
    """Enviar a la copia (ensayo): re-depura con las decisiones vigentes y carga lo LISTA a la copia del ERP."""

    def post(self, request):
        metricas = depurar(log=lambda *_: None)
        try:
            resultado = enviar(log=lambda *_: None)
        except Exception as exc:  # el lote queda FALLIDO y el ERP intacto (transaccion)
            return Response({'detail': f'La carga fallo y no se aplico ningun cambio: {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        resultado['metricas'] = metricas
        return Response(resultado)


class RevertirLoteView(MigracionView):
    def post(self, request, lote_id):
        lote = _uno('SELECT estado FROM migra.lote WHERE id = %s', (lote_id,))
        if not lote or lote['estado'] != 'COMPLETADO':
            return Response({'detail': 'Solo se pueden revertir lotes completados'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'lote': lote_id, 'borrados': revertir(lote_id, log=lambda *_: None)})


class ResultadoView(MigracionView):
    """Resultado final en la copia del ERP: lo creado por la migracion vs lo que ya existia."""

    def get(self, request):
        tablas = {
            'persona': 'erp_persona', 'matricula': 'academico_matricula',
            'nota_historica': 'academico_asignaturamatriculahistorico',
            'nota_en_curso': 'academico_asignaturamatricula',
            'homologacion_ingles': 'academico_homologacioningles', 'vinculo': 'erp_vinculopersona',
            'periodo': 'academico_periodoacademico', 'asignatura': 'academico_asignatura',
            'pensum': 'academico_pensum', 'pensum_asignatura': 'academico_pensumasignatura',
        }
        filas = []
        for entidad, tabla in tablas.items():
            fila = _uno(f"""
                SELECT %s AS entidad,
                       (SELECT count(*) FROM public.{tabla}) AS total_erp,
                       (SELECT count(*) FROM migra.mapa WHERE entidad = %s AND creado) AS creados_migracion,
                       (SELECT count(*) FROM migra.mapa WHERE entidad = %s AND NOT creado) AS enlazados_existentes
            """, (entidad, entidad, entidad))
            filas.append(fila)
        return Response({'entidades': filas})
