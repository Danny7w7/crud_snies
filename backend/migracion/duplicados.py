"""Reporte de personas que estan a la vez en SWA y en el ERP, y duplicados dentro del ERP.

Solo cuenta personas propias del ERP (no las que crearon lotes de esta migracion).
"""
from rest_framework.response import Response

from .views import MigracionView, _consulta, _uno

# Documento normalizado igual que en la depuracion (X...X de SWA = registro anulado).
NID_SQL = r"""regexp_replace(regexp_replace(upper(p.identificacion), '^X(?=[0-9])|(?<=[0-9])X$', '', 'g'),
                             '[^0-9A-Z]', '', 'g')"""
NOMBRE_ERP_SQL = r"""upper(unaccent(regexp_replace(trim(concat_ws(' ', ep.primer_nombre, nullif(ep.segundo_nombre, ''),
                     ep.primer_apellido, nullif(ep.segundo_apellido, ''))), '\s+', ' ', 'g')))"""
NOMBRE_SWA_SQL = r"""upper(unaccent(regexp_replace(trim(p.nombre || ' ' || p.apellido), '\s+', ' ', 'g')))"""
PROPIAS_SQL = """NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'persona' AND m.creado
                                AND m.erp_id = ep.id)"""


def _por_documento():
    return _consulta(f"""
        WITH s AS (
            SELECT p.id AS swa_id, {NID_SQL} AS nid, p.identificacion, p.tipo_identificacion,
                   trim(p.nombre) || ' ' || trim(p.apellido) AS nombre
            FROM swa_raw.persona p
        ),
        cruce AS (
            SELECT ep.id AS erp_id, array_agg(s.swa_id) AS swa_ids,
                   jsonb_agg(jsonb_build_object('swa_id', s.swa_id, 'identificacion', s.identificacion,
                                                'tipo', s.tipo_identificacion, 'nombre', s.nombre)) AS registros_swa,
                   min(s.nid) AS nid
            FROM erp_persona ep JOIN s ON s.nid = ep.nro_documento::text
            WHERE {PROPIAS_SQL}
            GROUP BY ep.id
        )
        SELECT ep.id AS erp_id, ep.tipo_documento_id, ep.nro_documento,
               concat_ws(' ', ep.primer_nombre, nullif(ep.segundo_nombre, ''), ep.primer_apellido,
                         nullif(ep.segundo_apellido, '')) AS nombre_erp,
               ep.fecha_creacion,
               (SELECT string_agg(DISTINCT coalesce(pr.nombre, '-') || ' (' || tv.nombre
                                  || CASE WHEN v.activo THEN ', activo' ELSE '' END || ')', '; ')
                FROM erp_vinculopersona v JOIN erp_tipovinculo tv ON tv.id = v.tipo_vinculo_id
                LEFT JOIN academico_programa pr ON pr.id = v.programa_id
                WHERE v.persona_id = ep.id) AS vinculos_erp,
               (SELECT count(*) FROM academico_matricula m WHERE m.persona_id = ep.id) AS matriculas_erp,
               c.registros_swa,
               (SELECT string_agg(e.codigo || ' ' || coalesce(pr.nombre, e.programa::text), '; ' ORDER BY e.codigo)
                FROM swa_raw.estudiante e LEFT JOIN swa_raw.programa pr ON pr.codigo = e.programa
                WHERE e.persona = ANY(c.swa_ids)) AS codigos_swa,
               (SELECT max(h.periodo) FROM swa_raw.historico h JOIN swa_raw.estudiante e ON e.codigo = h.estudiante
                WHERE h.estado = 'M' AND e.persona = ANY(c.swa_ids)) AS ultima_matricula_swa,
               (SELECT max(r.periodo) FROM swa_raw.registro r JOIN swa_raw.estudiante e ON e.codigo = r.estudiante
                WHERE r.estado = 'R' AND e.persona = ANY(c.swa_ids)) AS ultima_nota_swa,
               (SELECT count(*) FROM swa_raw.historico h JOIN swa_raw.estudiante e ON e.codigo = h.estudiante
                WHERE h.estado = 'M' AND e.persona = ANY(c.swa_ids)) AS matriculas_swa,
               mp.estado AS estado_migracion, mp.accion, mp.codigo_swa,
               (SELECT array_agg(DISTINCT cs.tipo) FROM revision.caso cs
                WHERE cs.vigente AND cs.decision IS NULL AND split_part(cs.clave, '|', 1) = c.nid) AS casos_pendientes,
               (SELECT pf.origen FROM migra.persona_fuente pf WHERE pf.swa_id = ANY(c.swa_ids)
                ORDER BY CASE pf.origen WHEN 'MATRICULA' THEN 0 WHEN 'SOLO_NOTAS' THEN 1 ELSE 2 END LIMIT 1)
                   AS origen_swa
        FROM cruce c
        JOIN erp_persona ep ON ep.id = c.erp_id
        LEFT JOIN migra.persona mp ON mp.nid = c.nid
        ORDER BY CASE mp.estado WHEN 'LISTA' THEN 0 WHEN 'REVISION' THEN 1 ELSE 2 END, ep.id
    """)


def _por_nombre():
    return _consulta(f"""
        WITH s AS (
            SELECT p.id AS swa_id, {NID_SQL} AS nid, p.identificacion, {NOMBRE_SWA_SQL} AS nom
            FROM swa_raw.persona p
        )
        SELECT ep.id AS erp_id, ep.nro_documento AS documento_erp, s.identificacion AS documento_swa,
               s.swa_id, {NOMBRE_ERP_SQL} AS nombre,
               EXISTS (SELECT 1 FROM erp_persona x WHERE x.nro_documento::text = s.nid) AS documento_swa_tambien_en_erp
        FROM erp_persona ep
        JOIN s ON s.nom = {NOMBRE_ERP_SQL} AND s.nid <> ep.nro_documento::text
        WHERE {PROPIAS_SQL}
        ORDER BY nombre
    """)


def _internos_erp():
    return _consulta(f"""
        WITH e AS (SELECT ep.*, {NOMBRE_ERP_SQL} AS nom FROM erp_persona ep WHERE {PROPIAS_SQL})
        SELECT a.id AS erp_id_1, a.tipo_documento_id AS tipo_1, a.nro_documento AS documento_1,
               b.id AS erp_id_2, b.tipo_documento_id AS tipo_2, b.nro_documento AS documento_2, a.nom AS nombre,
               CASE WHEN a.nro_documento = b.nro_documento THEN 'Mismo documento, distinto tipo'
                    ELSE 'Mismo nombre, documento distinto' END AS motivo
        FROM e a JOIN e b ON a.id < b.id AND (a.nom = b.nom OR a.nro_documento = b.nro_documento)
        ORDER BY a.nom
    """)


class DuplicadosErpView(MigracionView):
    def get(self, request):
        por_documento = _por_documento()
        por_nombre = _por_nombre()
        internos = _internos_erp()
        resumen = {
            'personas_erp': _uno(f'SELECT count(*) AS n FROM erp_persona ep WHERE {PROPIAS_SQL}')['n'],
            'por_documento': len(por_documento),
            'se_vinculan': sum(1 for f in por_documento if f['estado_migracion'] == 'LISTA'),
            'en_revision': sum(1 for f in por_documento if f['estado_migracion'] == 'REVISION'),
            'fuera_de_alcance': sum(1 for f in por_documento if f['estado_migracion'] is None),
            'por_nombre_otro_documento': len(por_nombre),
            'duplicados_internos_erp': len(internos),
        }
        return Response({'resumen': resumen, 'por_documento': por_documento, 'por_nombre': por_nombre,
                         'internos_erp': internos})
