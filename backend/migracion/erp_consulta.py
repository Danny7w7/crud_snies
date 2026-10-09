"""Consulta de estudiantes TAL COMO ESTÁN EN EL ERP (copia `erp_migracion`, esquema public).

Lee solo tablas del ERP (erp_*, academico_*, auth_app_*). Lo único que se toma del
esquema `migra` es la marca de origen (creado por la migración de SWA o nativo del ERP).
No escribe nada.
"""
import csv
import io

from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response

from .views import TAMANO_PAGINA, MigracionView, _consulta, _uno

ES_ESTUDIANTE = """EXISTS (SELECT 1 FROM erp_vinculopersona v JOIN erp_tipovinculo tv ON tv.id = v.tipo_vinculo_id
                         WHERE v.persona_id = p.id AND tv.nombre ILIKE 'estudiante'{extra})"""
DE_SWA = """EXISTS (SELECT 1 FROM migra.mapa x WHERE x.entidad = 'persona' AND x.creado AND x.erp_id = p.id)"""


def _filtros(params):
    condiciones, valores = [ES_ESTUDIANTE.format(extra='')], {}
    q = (params.get('q') or '').strip()
    if q:
        condiciones.append("""(p.id::text = %(q)s OR p.nro_documento::text LIKE %(q_ini)s
            OR EXISTS (SELECT 1 FROM academico_matricula m WHERE m.persona_id = p.id AND m.codigo = %(q)s)
            OR unaccent(concat_ws(' ', p.primer_nombre, p.segundo_nombre, p.primer_apellido, p.segundo_apellido))
               ILIKE unaccent(%(q_like)s))""")
        valores.update(q=q, q_ini=f'{q}%', q_like=f'%{q}%')
    if params.get('programa'):
        condiciones.append(ES_ESTUDIANTE.format(extra=' AND v.programa_id = %(programa)s'))
        valores['programa'] = int(params['programa'])
    if params.get('sede'):
        condiciones.append(ES_ESTUDIANTE.format(
            extra=' AND v.programa_id IN (SELECT id FROM academico_programa WHERE sucursal_id = %(sede)s)'))
        valores['sede'] = int(params['sede'])
    if params.get('periodo'):
        extra = ' AND m.programa_id = %(programa)s' if params.get('programa') else ''
        condiciones.append(f"""EXISTS (SELECT 1 FROM academico_matricula m
                                       WHERE m.persona_id = p.id AND m.periodo_id = %(periodo)s{extra})""")
        valores['periodo'] = int(params['periodo'])
    estado = params.get('estado')
    if estado == 'activo':
        condiciones.append(ES_ESTUDIANTE.format(extra=' AND v.activo'))
    elif estado == 'inactivo':
        condiciones.append('NOT ' + ES_ESTUDIANTE.format(extra=' AND v.activo'))
    origen = params.get('origen')
    if origen == 'swa':
        condiciones.append(DE_SWA)
    elif origen == 'erp':
        condiciones.append('NOT ' + DE_SWA)
    return ' AND '.join(condiciones), valores


def _sql_listado(donde):
    return f"""
        SELECT p.id AS codigo, td.codigo AS tipo_documento, p.nro_documento,
               concat_ws(' ', p.primer_nombre, nullif(p.segundo_nombre, ''), p.primer_apellido,
                         nullif(p.segundo_apellido, '')) AS nombre,
               p.email, p.telefono,
               (SELECT jsonb_agg(jsonb_build_object('id', pr.id, 'nombre', pr.nombre, 'activo', v.activo,
                                                    'sede', s.codigo) ORDER BY v.activo DESC, pr.nombre)
                FROM erp_vinculopersona v JOIN erp_tipovinculo tv ON tv.id = v.tipo_vinculo_id
                JOIN academico_programa pr ON pr.id = v.programa_id
                LEFT JOIN academico_sucursal s ON s.id = pr.sucursal_id
                WHERE v.persona_id = p.id AND tv.nombre ILIKE 'estudiante') AS programas,
               (SELECT pa.nombre FROM academico_matricula m JOIN academico_periodoacademico pa ON pa.id = m.periodo_id
                WHERE m.persona_id = p.id ORDER BY pa.nombre DESC LIMIT 1) AS ultimo_periodo,
               (SELECT count(*) FROM academico_matricula m WHERE m.persona_id = p.id) AS matriculas,
               (SELECT count(*) FROM academico_asignaturamatriculahistorico h WHERE h.persona_id = p.id)
                 + (SELECT count(*) FROM academico_asignaturamatricula a WHERE a.persona_id = p.id) AS notas,
               (SELECT count(*) FROM academico_homologacioningles hi WHERE hi.persona_id = p.id) AS modulos_ingles,
               EXISTS (SELECT 1 FROM auth_app_user u WHERE u.persona_id = p.id) AS tiene_usuario,
               {DE_SWA} AS de_swa
        FROM erp_persona p
        JOIN erp_tipodocumentoidentidad td ON td.id = p.tipo_documento_id
        WHERE {donde}
        ORDER BY p.primer_apellido, p.segundo_apellido, p.primer_nombre, p.id
    """


class EstudiantesErpOpcionesView(MigracionView):
    def get(self, request):
        return Response({
            'programas': _consulta("""
                SELECT pr.id, pr.nombre, s.codigo AS sede, count(DISTINCT v.persona_id) AS estudiantes
                FROM academico_programa pr
                JOIN erp_vinculopersona v ON v.programa_id = pr.id
                JOIN erp_tipovinculo tv ON tv.id = v.tipo_vinculo_id AND tv.nombre ILIKE 'estudiante'
                LEFT JOIN academico_sucursal s ON s.id = pr.sucursal_id
                GROUP BY pr.id, pr.nombre, s.codigo ORDER BY pr.nombre
            """),
            'periodos': _consulta("""
                SELECT pa.id, pa.nombre, pa.activo, count(m.id) AS matriculas
                FROM academico_periodoacademico pa LEFT JOIN academico_matricula m ON m.periodo_id = pa.id
                GROUP BY pa.id ORDER BY pa.nombre DESC
            """),
            'sedes': _consulta('SELECT id, codigo, nombre FROM academico_sucursal ORDER BY nombre'),
            'totales': _uno(f"""
                SELECT count(*) AS estudiantes,
                       count(*) FILTER (WHERE {ES_ESTUDIANTE.format(extra=' AND v.activo')}) AS activos,
                       count(*) FILTER (WHERE {DE_SWA}) AS de_swa
                FROM erp_persona p WHERE {ES_ESTUDIANTE.format(extra='')}
            """),
        })


class EstudiantesErpView(MigracionView):
    def get(self, request):
        donde, valores = _filtros(request.query_params)
        sql = _sql_listado(donde)
        try:
            pagina = max(int(request.query_params.get('page', 1)), 1)
        except ValueError:
            pagina = 1
        total = _uno(f'SELECT count(*) AS n FROM erp_persona p WHERE {donde}', valores)['n']
        filas = _consulta(f'{sql} LIMIT {TAMANO_PAGINA} OFFSET {(pagina - 1) * TAMANO_PAGINA}', valores)
        return Response({'count': total, 'page': pagina, 'page_size': TAMANO_PAGINA, 'results': filas})


class EstudiantesErpExportarView(MigracionView):
    def get(self, request):
        donde, valores = _filtros(request.query_params)
        filas = _consulta(_sql_listado(donde), valores)
        salida = io.StringIO()
        escritor = csv.writer(salida, delimiter=';')
        escritor.writerow(['Código', 'Tipo doc.', 'Documento', 'Nombre', 'Correo', 'Teléfono', 'Programas',
                           'Último periodo', 'Matrículas', 'Notas', 'Módulos inglés', 'Usuario ERP', 'Origen'])
        for f in filas:
            programas = '; '.join(
                f"{p['nombre']}{' (activo)' if p['activo'] else ''}" for p in (f['programas'] or []))
            escritor.writerow([f['codigo'], f['tipo_documento'], f['nro_documento'], f['nombre'], f['email'],
                               f['telefono'] or '', programas, f['ultimo_periodo'] or '', f['matriculas'],
                               f['notas'], f['modulos_ingles'], 'Sí' if f['tiene_usuario'] else 'No',
                               'SWA' if f['de_swa'] else 'ERP'])
        respuesta = HttpResponse('﻿' + salida.getvalue(), content_type='text/csv; charset=utf-8')
        respuesta['Content-Disposition'] = 'attachment; filename="estudiantes_erp.csv"'
        return respuesta


class EstudianteErpDetalleView(MigracionView):
    """Ficha del estudiante con todo lo que el ERP tiene de él."""

    def get(self, request, persona_id):
        persona = _uno("""
            SELECT p.id AS codigo, td.codigo AS tipo_documento, td.nombre AS tipo_documento_nombre, p.nro_documento,
                   p.primer_nombre, p.segundo_nombre, p.primer_apellido, p.segundo_apellido, p.email, p.telefono,
                   p.fecha_nacimiento, p.fecha_creacion,
                   d.direccion, d.barrio, d.sexo_biologico, d.estado_civil, d.fecha_expedicion_documento,
                   EXISTS (SELECT 1 FROM migra.mapa x WHERE x.entidad = 'persona' AND x.creado AND x.erp_id = p.id) AS de_swa
            FROM erp_persona p
            JOIN erp_tipodocumentoidentidad td ON td.id = p.tipo_documento_id
            LEFT JOIN erp_datossecundarios d ON d.persona_id = p.id
            WHERE p.id = %s
        """, (persona_id,))
        if not persona:
            return Response({'detail': 'No existe en el ERP'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'persona': persona,
            'usuario': _uno("""
                SELECT u.username, u.is_active, u.must_change_password, u.last_login,
                       (SELECT string_agg(r.nombre_rol, ', ') FROM auth_app_userrol ur
                        JOIN auth_app_rol r ON r.id = ur.rol_id WHERE ur.user_id = u.id) AS roles
                FROM auth_app_user u WHERE u.persona_id = %s
            """, (persona_id,)),
            'vinculos': _consulta("""
                SELECT tv.nombre AS tipo, pr.nombre AS programa, s.codigo AS sede, v.activo, v.periodo,
                       v.fecha_inicio, v.fecha_fin,
                       (SELECT te.nombre FROM erp_historialestadopersona h JOIN erp_tipoestado te ON te.id = h.estado_id
                        WHERE h.vinculo_id = v.id AND h.activo ORDER BY h.fecha_inicio DESC LIMIT 1) AS estado
                FROM erp_vinculopersona v JOIN erp_tipovinculo tv ON tv.id = v.tipo_vinculo_id
                LEFT JOIN academico_programa pr ON pr.id = v.programa_id
                LEFT JOIN academico_sucursal s ON s.id = pr.sucursal_id
                WHERE v.persona_id = %s ORDER BY v.activo DESC, v.fecha_inicio DESC
            """, (persona_id,)),
            'matriculas': _consulta("""
                SELECT m.id, pa.nombre AS periodo, pr.nombre AS programa, pe.codigo AS pensum, pe.estado AS pensum_abierto,
                       m.semestre_actual, m.estado AS vigente, m.codigo,
                       (SELECT count(*) FROM academico_asignaturamatriculahistorico h WHERE h.matricula_id = m.id)
                         + (SELECT count(*) FROM academico_asignaturamatricula a WHERE a.matricula_id = m.id) AS notas
                FROM academico_matricula m
                JOIN academico_periodoacademico pa ON pa.id = m.periodo_id
                JOIN academico_programa pr ON pr.id = m.programa_id
                JOIN academico_pensum pe ON pe.id = m.pensum_id
                WHERE m.persona_id = %s ORDER BY pa.nombre DESC, pr.nombre
            """, (persona_id,)),
            'notas': _consulta("""
                SELECT pa.nombre AS periodo, pr.nombre AS programa, a.codigo AS asignatura_codigo,
                       a.nombre AS asignatura, a.creditos, n.definitiva, n.origen,
                       (SELECT ps.semestre FROM academico_pensumasignatura ps
                        WHERE ps.pensum_id = m.pensum_id AND ps.asignatura_id = n.asignatura_id LIMIT 1) AS semestre
                FROM (SELECT asignatura_id, periodo_id, programa_id, matricula_id, definitiva, 'Histórico' AS origen
                      FROM academico_asignaturamatriculahistorico WHERE persona_id = %(p)s
                      UNION ALL
                      SELECT asignatura_id, periodo_id, programa_id, matricula_id, definitiva, 'Inscrita en el ERP'
                      FROM academico_asignaturamatricula WHERE persona_id = %(p)s) n
                JOIN academico_asignatura a ON a.id = n.asignatura_id
                JOIN academico_periodoacademico pa ON pa.id = n.periodo_id
                JOIN academico_programa pr ON pr.id = n.programa_id
                JOIN academico_matricula m ON m.id = n.matricula_id
                ORDER BY pa.nombre DESC, pr.nombre, a.nombre
            """, {'p': persona_id}),
            'ingles': _consulta("""
                SELECT pr.nombre AS centro, h.modulo, h.motivo, h.acto, h.observacion, h.activo
                FROM academico_homologacioningles h JOIN academico_programa pr ON pr.id = h.programa_id
                WHERE h.persona_id = %s ORDER BY pr.nombre, h.modulo
            """, (persona_id,)),
        })
