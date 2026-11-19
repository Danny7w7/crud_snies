"""Carga ("Enviar a la copia (ensayo)" y migra_corte) de lo depurado en `migra.*` hacia la copia del ERP (`public`).

- Solo se envian filas en estado LISTA; lo que esta en REVISION o EXCLUIDA no viaja.
- Todo ocurre en una sola transaccion: si algo falla, el ERP queda intacto.
- Cada fila creada queda en `migra.mapa` (entidad, clave SWA -> id ERP, lote). Por eso
  la carga se puede repetir (solo envia lo nuevo) y un lote se puede revertir.
- Los IDs nuevos de persona siguen la identidad canónica del SWA.
"""
import json
import time

from types import SimpleNamespace

from django.db import transaction
from psycopg import sql

from api.catalogos_academicos import estado_malla, obtener_o_crear_asignatura, proyeccion
from api.codigos_persona import (GrupoPersona, TIPOS_DOCUMENTO, normalizar_nombre,
                                 obtener_o_crear_persona, registrar_equivalencia, registrar_personas)
from api.esquema_destino import validar_esquema_destino
from api.models import PersonaErp, TipoVinculo, VinculoPersona
from api.modelos_academicos import InscripcionHistoricaErp, MatriculaErp, PensumErp
from api.programas_swa import guardar_asociacion, resolver_vinculo_ingles, tiene_carrera, vinculo_principal
from config.destino_migracion import bloqueo_migracion, cursor_migracion
from ubicacion.models import (AsociacionProgramaSwa, AvanceAsignaturaSwa, CalificacionSwa, EquivalenciaEstudianteSwa,
                             EquivalenciaMatriculaSwa, EquivalenciaPensumSwa, EquivalenciaPersonaSwa)

from .conciliacion_ids import ENTIDAD_REASIGNACION
from .depuracion import PERIODO_CORTE, _depurar_en_cursor
from .reasignar_ids_persona import REFERENCIAS_LOGICAS_PERSONA

# Orden inverso de dependencias, usado para revertir.
ENTIDADES = [
    ('calificacion', 'ubicacion_calificacionswa'),
    ('avance', 'ubicacion_avanceasignaturaswa'),
    ('asociacion_programa', 'ubicacion_asociacionprogramaswa'),
    ('equivalencia_estudiante', 'ubicacion_equivalenciaestudianteswa'),
    ('equivalencia_matricula', 'ubicacion_equivalenciamatriculaswa'),
    ('equivalencia_pensum', 'ubicacion_equivalenciapensumswa'),
    ('equivalencia_persona', 'ubicacion_equivalenciapersonaswa'),
    ('historial_estado', 'erp_historialestadopersona'),
    ('vinculo', 'erp_vinculopersona'),
    ('homologacion_ingles', 'academico_homologacioningles'),
    ('nota_en_curso', 'academico_asignaturamatricula'),
    ('nota_historica', 'academico_asignaturamatriculahistorico'),
    ('matricula', 'academico_matricula'),
    ('datos_secundarios', 'erp_datossecundarios'),
    ('persona', 'erp_persona'),
    ('requisito', 'academico_pensumasignaturarestriccion'),
    ('pensum_asignatura', 'academico_pensumasignatura'),
    ('pensum', 'academico_pensum'),
    ('asignatura', 'academico_asignatura'),
    ('periodo', 'academico_periodoacademico'),
]


def _mapear_fila(cur, entidad, clave, erp_id, lote, creada):
    cur.execute("""
        INSERT INTO migra.mapa (entidad, clave, erp_id, lote_id, creado)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (entidad, clave) DO NOTHING
    """, (entidad, str(clave), erp_id, lote, creada))
    if not cur.rowcount:
        cur.execute('SELECT erp_id FROM migra.mapa WHERE entidad = %s AND clave = %s',
                    (entidad, str(clave)))
        if cur.fetchone()[0] != erp_id:
            raise ValueError('Una equivalencia del migrador identifica otra fila del ERP.')


def _mapear(cur, entidad, lote, sql_claves, creado=True):
    """Inserta en migra.mapa las (clave, erp_id) que devuelve sql_claves.

    creado=False marca filas que ya existian en el ERP: se enlazan pero nunca se revierten.
    """
    cur.execute(f"""
        SELECT 1 FROM ({sql_claves}) x
        JOIN migra.mapa m ON m.entidad = %s AND m.clave = x.clave
        WHERE m.erp_id <> x.erp_id LIMIT 1
    """, (entidad,))
    if cur.fetchone():
        raise ValueError('El origen ya tiene un destino diferente en el mapa.')
    cur.execute(f"""
        INSERT INTO migra.mapa (entidad, clave, erp_id, lote_id, creado)
        SELECT %s, clave, erp_id, %s, %s FROM ({sql_claves}) x
        ON CONFLICT DO NOTHING
    """, (entidad, lote, creado))
    return cur.rowcount


def _nuevos_ids(cur, tabla, tmp):
    """Asigna ids de la secuencia de `tabla` a las filas de la tabla temporal `tmp`."""
    cur.execute(f"UPDATE {tmp} SET erp_id = nextval(pg_get_serial_sequence('public.{tabla}', 'id'))")


def _cargar_catalogos(cur, lote, res):
    # Periodos: se reutilizan los que ya existen por nombre; los historicos se crean inactivos.
    res['periodo'] = _mapear(cur, 'periodo', lote, """
        SELECT mp.codigo::text AS clave, pa.id AS erp_id
        FROM migra.periodo mp JOIN public.academico_periodoacademico pa ON pa.nombre = mp.nombre
        WHERE EXISTS (SELECT 1 FROM t_alcance a WHERE a.periodo = mp.codigo)
           OR EXISTS (SELECT 1 FROM t_alcance a JOIN migra.pensum p
                      ON p.programa = a.programa AND p.codigo = a.pensum
                      WHERE p.periodo_codigo = mp.codigo)
    """, creado=False)
    cur.execute("""
        CREATE TEMP TABLE t_periodo ON COMMIT DROP AS
        SELECT mp.*, NULL::bigint AS erp_id FROM migra.periodo mp
        WHERE NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'periodo' AND m.clave = mp.codigo::text)
          AND (EXISTS (SELECT 1 FROM t_alcance a WHERE a.periodo = mp.codigo)
               OR EXISTS (SELECT 1 FROM t_alcance a JOIN migra.pensum p
                          ON p.programa = a.programa AND p.codigo = a.pensum
                          WHERE p.periodo_codigo = mp.codigo))
    """)
    _nuevos_ids(cur, 'academico_periodoacademico', 't_periodo')
    cur.execute("""
        INSERT INTO public.academico_periodoacademico (id, nombre, activo, fecha_inicio_general, fecha_fin_general)
        SELECT erp_id, nombre, false, fecha_inicio, fecha_fin FROM t_periodo
    """)
    res['periodo'] += _mapear(cur, 'periodo', lote, 'SELECT codigo::text AS clave, erp_id FROM t_periodo')

    # Asignaturas (codigo SWA = codigo ERP)
    res['asignatura'] = _mapear(cur, 'asignatura', lote, """
        SELECT ma.codigo::text AS clave, a.id AS erp_id
        FROM migra.asignatura ma JOIN public.academico_asignatura a ON a.codigo = ma.erp_codigo
    """, creado=False)
    # La misma proyección de horas y metodología se usa en ambas rutas.
    cur.execute("""
        SELECT ma.codigo, ma.erp_codigo, ma.nombre, ma.tipo, ma.ihs, ma.creditos
        FROM migra.asignatura ma
        WHERE ma.erp_codigo = ma.codigo::text
          AND NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'asignatura' AND m.clave = ma.codigo::text)
          AND (EXISTS (
              SELECT 1 FROM t_alcance a JOIN migra.pensum_asignatura pa
                ON pa.programa = a.programa AND pa.pensum = a.pensum
              JOIN migra.asignatura s ON s.codigo = pa.asignatura
              WHERE s.erp_codigo = ma.erp_codigo
          ) OR EXISTS (
              SELECT 1 FROM t_alcance a JOIN migra.nota n
                ON n.nid = a.nid AND n.programa = a.programa AND n.periodo = a.periodo
              JOIN migra.asignatura s ON s.codigo = n.asignatura
              WHERE n.estado = 'LISTA' AND s.erp_codigo = ma.erp_codigo
          ))
    """)
    res['asignatura_pendiente'] = 0
    for codigo, erp_codigo, nombre, tipo, horas, creditos in cur.fetchall():
        try:
            materia, creada = obtener_o_crear_asignatura(
                erp_codigo, {'nombre': nombre, 'tipo': tipo, 'ihs': horas}, creditos)
        except ValueError:
            res['asignatura_pendiente'] += 1
        else:
            _mapear_fila(cur, 'asignatura', codigo, materia.pk, lote, creada)
            res['asignatura'] += 1
    res['asignatura_equivalente'] = _mapear(cur, 'asignatura', lote, """
        SELECT ma.codigo::text AS clave, a.id AS erp_id
        FROM migra.asignatura ma JOIN public.academico_asignatura a ON a.codigo = ma.erp_codigo
        WHERE ma.erp_codigo <> ma.codigo::text
    """, creado=False)

    # Los pensums ya conciliados por cualquiera de las dos rutas conservan su ID ERP.
    _mapear(cur, 'pensum', lote, """
        SELECT mp.programa || '|' || mp.codigo AS clave, pe.id AS erp_id
        FROM migra.pensum mp
        LEFT JOIN public.ubicacion_equivalenciapensumswa eq
          ON eq.programa_swa_id = mp.programa AND eq.pensum_swa_id = mp.codigo
        JOIN public.academico_pensum pe ON pe.programa_id = mp.programa
          AND (pe.id = eq.pensum_erp_id OR eq.id IS NULL AND pe.codigo = mp.erp_codigo)
    """, creado=False)
    cur.execute("""
        CREATE TEMP TABLE t_pensum ON COMMIT DROP AS
        SELECT mp.*, per.erp_id AS periodo_id, NULL::bigint AS erp_id
        FROM migra.pensum mp
        JOIN migra.mapa per ON per.entidad = 'periodo' AND per.clave = mp.periodo_codigo::text
        WHERE EXISTS (SELECT 1 FROM public.academico_programa ap WHERE ap.id = mp.programa)
          AND EXISTS (SELECT 1 FROM t_alcance a WHERE a.programa = mp.programa AND a.pensum = mp.codigo)
          AND length(mp.erp_codigo) BETWEEN 1 AND 20
          AND NOT EXISTS (SELECT 1 FROM public.academico_pensum pe WHERE pe.codigo = mp.erp_codigo)
          AND NOT EXISTS (SELECT 1 FROM public.ubicacion_equivalenciapensumswa eq
                          WHERE eq.programa_swa_id = mp.programa AND eq.pensum_swa_id = mp.codigo)
          AND NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'pensum'
                          AND m.clave = mp.programa || '|' || mp.codigo)
    """)
    _nuevos_ids(cur, 'academico_pensum', 't_pensum')
    cur.execute("""
        INSERT INTO public.academico_pensum (id, codigo, estado, periodo_id, programa_id, intensidad_horaria)
        SELECT erp_id, erp_codigo, false, periodo_id, programa, 0 FROM t_pensum
    """)
    res['pensum'] = _mapear(cur, 'pensum', lote, "SELECT programa || '|' || codigo AS clave, erp_id FROM t_pensum")
    cur.execute("""
        SELECT p.programa, p.codigo, m.erp_id FROM migra.pensum p
        JOIN migra.mapa m ON m.entidad = 'pensum' AND m.clave = p.programa || '|' || p.codigo
    """)
    nuevas_equivalencias = []
    for programa, codigo, destino in cur.fetchall():
        eq, creada = EquivalenciaPensumSwa.objects.using('default').get_or_create(
            programa_swa_id=programa, pensum_swa_id=codigo, defaults={'pensum_erp_id': destino},
        )
        if eq.pensum_erp_id != destino:
            raise ValueError('El pensum ya tiene una equivalencia diferente.')
        _mapear_fila(cur, 'equivalencia_pensum', f'{programa}|{codigo}', eq.pk, lote, creada)
        if creada:
            nuevas_equivalencias.append(eq)

    cur.execute("""
        CREATE TEMP TABLE t_pa ON COMMIT DROP AS
        SELECT mpa.*, pe.erp_id AS pensum_id, asg.erp_id AS asignatura_id, NULL::bigint AS erp_id
        FROM migra.pensum_asignatura mpa
        JOIN migra.mapa pe ON pe.entidad = 'pensum' AND pe.clave = mpa.programa || '|' || mpa.pensum
        JOIN migra.mapa asg ON asg.entidad = 'asignatura' AND asg.clave = mpa.asignatura::text
        WHERE NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'pensum_asignatura'
                          AND m.clave = mpa.programa || '|' || mpa.pensum || '|' || mpa.asignatura)
    """)
    # Con equivalencias unificadas, dos filas de SWA pueden quedar en la misma (pensum, asignatura):
    # se crea una sola y ambas claves apuntan a ella.
    cur.execute("""
        UPDATE t_pa t SET erp_id = nextval(pg_get_serial_sequence('academico_pensumasignatura', 'id'))
        FROM (SELECT ctid, row_number() OVER (PARTITION BY pensum_id, asignatura_id ORDER BY nivel, asignatura) AS rn
              FROM t_pa) d
        WHERE t.ctid = d.ctid AND d.rn = 1
          AND NOT EXISTS (SELECT 1 FROM public.academico_pensumasignatura x
                          WHERE x.pensum_id = t.pensum_id AND x.asignatura_id = t.asignatura_id)
    """)
    cur.execute("""
        INSERT INTO public.academico_pensumasignatura (id, semestre, asignatura_id, pensum_id)
        SELECT erp_id, greatest(nivel, 0), asignatura_id, pensum_id FROM t_pa WHERE erp_id IS NOT NULL
    """)
    res['pensum_asignatura'] = _mapear(cur, 'pensum_asignatura', lote, """
        SELECT t.programa || '|' || t.pensum || '|' || t.asignatura AS clave, x.id AS erp_id
        FROM t_pa t JOIN public.academico_pensumasignatura x
             ON x.pensum_id = t.pensum_id AND x.asignatura_id = t.asignatura_id
        WHERE t.erp_id IS NOT NULL
    """)
    _mapear(cur, 'pensum_asignatura', lote, """
        SELECT t.programa || '|' || t.pensum || '|' || t.asignatura AS clave, x.id AS erp_id
        FROM t_pa t JOIN public.academico_pensumasignatura x
             ON x.pensum_id = t.pensum_id AND x.asignatura_id = t.asignatura_id
        WHERE t.erp_id IS NULL
    """, creado=False)

    cur.execute("""
        CREATE TEMP TABLE t_req ON COMMIT DROP AS
        SELECT r.*, pa.erp_id AS pensum_asignatura_id, asg.erp_id AS asignatura_id, NULL::bigint AS erp_id
        FROM migra.prerrequisito r
        JOIN migra.mapa pa ON pa.entidad = 'pensum_asignatura'
             AND pa.clave = r.programa || '|' || r.pensum || '|' || r.asignatura
        JOIN migra.mapa asg ON asg.entidad = 'asignatura' AND asg.clave = r.requisito::text
        WHERE NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'requisito'
                          AND m.clave = concat_ws('|', r.programa, r.pensum, r.asignatura, r.requisito, r.tipo))
    """)
    _nuevos_ids(cur, 'academico_pensumasignaturarestriccion', 't_req')
    cur.execute("""
        CREATE TEMP TABLE t_req_creadas ON COMMIT DROP AS
        WITH nuevas AS (
            INSERT INTO public.academico_pensumasignaturarestriccion (id, tipo, asignatura_id, pensum_asignatura_id)
            SELECT erp_id, tipo, asignatura_id, pensum_asignatura_id FROM t_req
            ON CONFLICT (pensum_asignatura_id, tipo, asignatura_id) DO NOTHING
            RETURNING id
        )
        SELECT id FROM nuevas
    """)
    mapeo_restricciones = """
        SELECT concat_ws('|', t.programa, t.pensum, t.asignatura, t.requisito, t.tipo) AS clave,
               r.id AS erp_id
        FROM t_req t
        JOIN public.academico_pensumasignaturarestriccion r
          USING (pensum_asignatura_id, tipo, asignatura_id)
    """
    res['requisito'] = _mapear(cur, 'requisito', lote, """
        SELECT m.clave, m.erp_id FROM (""" + mapeo_restricciones + """) m
        JOIN t_req_creadas n ON n.id = m.erp_id
    """)
    _mapear(cur, 'requisito', lote, mapeo_restricciones + """
        WHERE NOT EXISTS (SELECT 1 FROM t_req_creadas n WHERE n.id = r.id)
    """, creado=False)
    for eq in nuevas_equivalencias:
        pensum = PensumErp.objects.using('default').get(pk=eq.pensum_erp_id)
        eq.ultimo_importado = estado_malla(pensum)
        eq.save(using='default', update_fields=['ultimo_importado'])


def _cargar_personas(cur, lote, res):
    cur.execute("""
        SELECT codigo, min(id) FROM public.erp_tipodocumentoidentidad
        GROUP BY codigo HAVING count(*) = 1
    """)
    tipos = dict(cur.fetchall())
    swa_por_tipo = {tipos.get(codigo): origen for origen, codigo in TIPOS_DOCUMENTO.items() if codigo in tipos}
    cur.execute("""
        SELECT p.nid, p.swa_ids, p.codigo_swa, p.tipo_documento_id, p.nro_documento,
               p.primer_nombre, p.segundo_nombre, p.primer_apellido, p.segundo_apellido,
               p.email, p.telefono, p.fecha_nacimiento, p.estado_general, p.erp_persona_id
        FROM migra.persona p
        WHERE p.estado = 'LISTA' AND EXISTS (SELECT 1 FROM t_alcance a WHERE a.nid = p.nid)
        ORDER BY p.nid
    """)
    res['persona_creada'] = res['persona_vinculada'] = 0
    res['persona_pendiente'] = 0
    for (nid, ids, codigo, tipo, documento, n1, n2, a1, a2, email, telefono,
         nacimiento, estado_general, erp_id) in cur.fetchall():
        miembros = tuple(SimpleNamespace(pk=identidad) for identidad in ids)
        nombre = normalizar_nombre(f'{n1 or ""} {n2 or ""} {a1 or ""} {a2 or ""}')
        grupo = GrupoPersona(int(codigo), miembros, swa_por_tipo[tipo], documento, nombre)
        datos = {'primer_nombre': n1, 'segundo_nombre': n2 or '', 'primer_apellido': a1,
                 'segundo_apellido': a2 or '', 'email': email or '', 'telefono': telefono,
                 'fecha_nacimiento': nacimiento, 'estado_general': estado_general}
        try:
            with transaction.atomic(using='default'):
                trazas = EquivalenciaPersonaSwa.objects.using('default').filter(persona_swa_id__in=ids)
                anteriores = set(trazas.values_list('pk', flat=True))
                if erp_id and erp_id != grupo.persona_id:
                    persona = PersonaErp.objects.using('default').select_for_update().get(pk=erp_id)
                    if (persona.nro_documento != documento
                            or persona.tipo_documento_id not in ({tipos['CC'], tipos['TI']}
                                if grupo.tipo_swa in (1, 4) else {tipo})
                            or normalizar_nombre(f'{persona.primer_nombre} {persona.segundo_nombre} '
                                               f'{persona.primer_apellido} {persona.segundo_apellido}') != nombre):
                        raise ValueError('La persona adoptada cambió desde la revisión.')
                    registrar_personas(grupo, persona)
                    creada = False
                else:
                    persona, creada = obtener_o_crear_persona(grupo, tipos, datos)
                _mapear_fila(cur, 'persona', nid, persona.pk, lote, creada)
                for pk, origen in trazas.values_list('pk', 'persona_swa_id'):
                    _mapear_fila(cur, 'equivalencia_persona', origen, pk, lote, pk not in anteriores)
        except (PersonaErp.DoesNotExist, ValueError):
            cur.execute("""
                UPDATE migra.persona SET estado = 'REVISION',
                       alertas = array_append(alertas, 'CONFLICTO_PERSISTENCIA_PERSONA')
                WHERE nid = %s
            """, (nid,))
            res['persona_pendiente'] += 1
        else:
            res['persona_creada' if creada else 'persona_vinculada'] += 1

    # Datos secundarios minimos con lo que trae SWA (sexo, estado civil, direccion). Solo para
    # personas que aun no los tienen: nunca se modifican fichas existentes del ERP.
    # SWA no tiene fecha de expedicion del documento: queda NULL.
    cur.execute("""
        CREATE TEMP TABLE t_datos ON COMMIT DROP AS
        SELECT mp.nid, x.erp_id AS persona_id, sp.sexo, sp.estado_civil,
               nullif(left(trim(regexp_replace(coalesce(sp.direccion, ''), '\\s+', ' ', 'g')), 200), '') AS direccion,
               NULL::bigint AS erp_id
        FROM migra.persona mp
        JOIN migra.mapa x ON x.entidad = 'persona' AND x.clave = mp.nid
        JOIN swa_raw.persona sp ON sp.id = mp.swa_id_principal
        WHERE mp.estado = 'LISTA' AND EXISTS (SELECT 1 FROM t_alcance a WHERE a.nid = mp.nid)
          AND NOT EXISTS (SELECT 1 FROM public.erp_datossecundarios d WHERE d.persona_id = x.erp_id)
          AND NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'datos_secundarios' AND m.clave = mp.nid)
    """)
    _nuevos_ids(cur, 'erp_datossecundarios', 't_datos')
    cur.execute("""
        INSERT INTO public.erp_datossecundarios (id, persona_id, barrio, direccion, direccion_institucional,
                                                 telefono_fijo, sexo_biologico, estado_civil, fecha_registro,
                                                 pais_nacimiento)
        SELECT erp_id, persona_id, '',
               CASE WHEN direccion IN ('.', '0', '-') THEN '' ELSE coalesce(direccion, '') END, '', NULL,
               CASE sexo WHEN 'M' THEN 1 WHEN 'F' THEN 2 END,
               CASE estado_civil WHEN 1 THEN 1 WHEN 2 THEN 2 WHEN 3 THEN 3 WHEN 4 THEN 4 WHEN 5 THEN 5
                                 WHEN 7 THEN 1 END,
               now(), ''
        FROM t_datos
    """)
    res['datos_secundarios'] = _mapear(cur, 'datos_secundarios', lote, 'SELECT nid AS clave, erp_id FROM t_datos')


def _cargar_academico(cur, lote, res):
    cur.execute("""
        CREATE TEMP TABLE t_matricula ON COMMIT DROP AS
        SELECT m.*, per.erp_id AS persona_id, pe.erp_id AS pensum_id, pr.erp_id AS periodo_id,
               mp.fecha_inicio, mp.fecha_fin, NULL::bigint AS erp_id
        FROM t_alcance m
        JOIN migra.persona p ON p.nid = m.nid AND p.estado = 'LISTA'
        JOIN migra.mapa per ON per.entidad = 'persona' AND per.clave = m.nid
        JOIN migra.mapa pe ON pe.entidad = 'pensum' AND pe.clave = m.programa || '|' || m.pensum
        JOIN migra.mapa pr ON pr.entidad = 'periodo' AND pr.clave = m.periodo::text
        JOIN migra.periodo mp ON mp.codigo = m.periodo
        WHERE m.estado = 'LISTA' AND length(m.codigo) BETWEEN 1 AND 20
    """)
    cur.execute("""
        CREATE TEMP TABLE t_mat_existente ON COMMIT DROP AS
        SELECT t.id AS origen_id, count(e.id) AS candidatos,
               min(e.id) FILTER (WHERE e.pensum_id = t.pensum_id
                    AND (e.codigo = ANY(t.codigos) OR EXISTS (
                        SELECT 1 FROM public.ubicacion_equivalenciamatriculaswa eq
                        WHERE eq.periodo_swa = t.periodo AND eq.programa_swa_id = t.programa
                            AND eq.codigo_estudiante = ANY(t.codigos) AND eq.matricula_erp_id = e.id
                    ))) AS compatible
        FROM t_matricula t
        JOIN public.academico_matricula e ON e.persona_id = t.persona_id
             AND e.programa_id = t.programa AND e.periodo_id = t.periodo_id
        GROUP BY t.id
    """)
    _mapear(cur, 'matricula', lote, """
        SELECT t.nid || '|' || t.programa || '|' || t.periodo AS clave, e.compatible AS erp_id
        FROM t_matricula t JOIN t_mat_existente e ON e.origen_id = t.id
        WHERE e.candidatos = 1 AND e.compatible IS NOT NULL
    """, creado=False)
    cur.execute("""
        UPDATE t_matricula t SET erp_id = nextval(pg_get_serial_sequence('public.academico_matricula', 'id'))
        WHERE NOT EXISTS (SELECT 1 FROM t_mat_existente e WHERE e.origen_id = t.id)
          AND NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'matricula'
                          AND m.clave = t.nid || '|' || t.programa || '|' || t.periodo)
    """)
    cur.execute("""
        INSERT INTO public.academico_matricula (id, estado, semestre_actual, fecha_inicio, fecha_fin, codigo,
                                                persona_id, pensum_id, periodo_id, programa_id)
        SELECT erp_id, activa, greatest(nivel, 0), coalesce(fecha_inicio::timestamptz, now()),
               CASE WHEN activa THEN NULL ELSE fecha_fin::timestamptz END,
               codigo, persona_id, pensum_id, periodo_id, programa
        FROM t_matricula WHERE erp_id IS NOT NULL
    """)
    res['matricula'] = _mapear(cur, 'matricula', lote, """
        SELECT nid || '|' || programa || '|' || periodo AS clave, erp_id FROM t_matricula WHERE erp_id IS NOT NULL
    """)
    cur.execute("""
        SELECT t.periodo, t.programa, unnest(t.codigos), m.erp_id
        FROM t_matricula t JOIN migra.mapa m
          ON m.entidad = 'matricula' AND m.clave = t.nid || '|' || t.programa || '|' || t.periodo
        WHERE t.erp_id IS NOT NULL OR EXISTS (
            SELECT 1 FROM t_mat_existente e
            WHERE e.origen_id = t.id AND e.candidatos = 1 AND e.compatible = m.erp_id
        )
    """)
    for periodo, programa, codigo, erp_id in cur.fetchall():
        campos = ('persona_id', 'programa_id', 'pensum_id', 'periodo_id', 'codigo', 'semestre_actual', 'estado')
        destino = MatriculaErp.objects.using('default').get(pk=erp_id)
        eq, creada = EquivalenciaMatriculaSwa.objects.using('default').get_or_create(
            periodo_swa=periodo, programa_swa_id=programa, codigo_estudiante=codigo,
            defaults={'matricula_erp_id': erp_id, 'ultimo_importado': proyeccion(destino, campos)},
        )
        if eq.matricula_erp_id != erp_id:
            raise ValueError('Un código de estudiante ya tiene otra matrícula de destino.')
        _mapear_fila(cur, 'equivalencia_matricula', f'{periodo}|{programa}|{codigo}', eq.pk, lote, creada)

    cur.execute("""
        UPDATE migra.matricula m SET estado = 'REVISION'
        FROM t_alcance a
        WHERE a.id = m.id AND NOT EXISTS (
            SELECT 1 FROM public.ubicacion_equivalenciamatriculaswa eq
            WHERE eq.periodo_swa = a.periodo AND eq.programa_swa_id = a.programa
              AND eq.codigo_estudiante = ANY(a.codigos)
        )
    """)


def _cargar_notas(cur, lote, res):
    cur.execute("""
        CREATE TEMP TABLE t_nota ON COMMIT DROP AS
        SELECT n.*, concat_ws('|', n.periodo, n.programa, n.asignatura, n.grupo, n.estudiante) AS clave,
               mat.erp_id AS matricula_id, per.erp_id AS persona_id, asg.erp_id AS asignatura_id,
               pr.erp_id AS periodo_id, asociacion.id AS asociacion_id, NULL::bigint AS erp_id
        FROM migra.nota n
        JOIN t_matricula t ON t.nid = n.nid AND t.programa = n.programa AND t.periodo = n.periodo
        JOIN migra.mapa mat ON mat.entidad = 'matricula' AND mat.clave = n.nid || '|' || n.programa || '|' || n.periodo
        JOIN migra.mapa per ON per.entidad = 'persona' AND per.clave = n.nid
        JOIN migra.mapa asg ON asg.entidad = 'asignatura' AND asg.clave = n.asignatura::text
        JOIN migra.mapa pr ON pr.entidad = 'periodo' AND pr.clave = n.periodo::text
        JOIN public.ubicacion_asociacionprogramaswa asociacion ON asociacion.periodo_swa = n.periodo
          AND asociacion.codigo_estudiante = n.estudiante AND asociacion.programa_swa_id = n.programa
          AND asociacion.persona_erp_id = per.erp_id AND asociacion.vinculo_erp_id IS NOT NULL
          AND asociacion.motivo_pendiente = ''
        WHERE n.estado = 'LISTA' AND NOT n.en_curso
          AND (t.erp_id = mat.erp_id OR EXISTS (
              SELECT 1 FROM t_mat_existente e
              WHERE e.origen_id = t.id AND e.candidatos = 1 AND e.compatible = mat.erp_id
          ))
          AND NOT EXISTS (
              SELECT 1 FROM public.academico_asignaturamatriculahistorico h
              WHERE h.matricula_id = mat.erp_id AND h.asignatura_id = asg.erp_id
          )
          AND NOT EXISTS (SELECT 1 FROM migra.mapa x WHERE x.entidad IN ('nota_historica', 'nota_en_curso')
                          AND x.clave = concat_ws('|', n.periodo, n.programa, n.asignatura, n.grupo, n.estudiante))
    """)
    _nuevos_ids(cur, 'academico_asignaturamatriculahistorico', 't_nota')
    cur.execute("""
        INSERT INTO public.academico_asignaturamatriculahistorico (id, definitiva, asignatura_id, persona_id,
                                                                   matricula_id, periodo_id, programa_id)
        SELECT erp_id, definitiva, asignatura_id, persona_id, matricula_id, periodo_id, programa
        FROM t_nota WHERE NOT en_curso
    """)
    res['nota_historica'] = _mapear(cur, 'nota_historica', lote, 'SELECT clave, erp_id FROM t_nota WHERE NOT en_curso')
    cur.execute("""
        SELECT t.clave, t.periodo, t.programa, t.asignatura, t.grupo, t.estudiante,
               t.asociacion_id, t.erp_id, t.matricula_id, to_jsonb(r), g.grupo_nota,
               coalesce(c.calificaciones, '[]'::jsonb)
        FROM t_nota t
        JOIN swa_raw.registro r ON (r.periodo, r.programa, r.asignatura, r.grupo, r.estudiante)
                                = (t.periodo, t.programa, t.asignatura, t.grupo, t.estudiante)
        LEFT JOIN swa_raw.grupo g ON (g.periodo, g.programa, g.asignatura, g.codigo)
                                  = (t.periodo, t.programa, t.asignatura, t.grupo)
        LEFT JOIN LATERAL (
            SELECT jsonb_agg(to_jsonb(c)) AS calificaciones
            FROM swa_raw.calificacion c
            WHERE (c.periodo, c.programa, c.asignatura, c.grupo, c.estudiante)
                = (t.periodo, t.programa, t.asignatura, t.grupo, t.estudiante)
        ) c ON true
        ORDER BY t.clave
    """)
    for (clave, periodo, programa, asignatura, grupo, codigo, asociacion, erp_id,
         matricula_id, registro, grupo_nota, calificaciones) in cur.fetchall():
        registro['grupo_nota'] = grupo_nota
        destino = InscripcionHistoricaErp.objects.using('default').get(pk=erp_id)
        campos = ('persona_id', 'programa_id', 'asignatura_id', 'matricula_id', 'periodo_id', 'definitiva')
        datos = {'asociacion_id': asociacion, 'matricula_erp_id': matricula_id,
                 'destino_notas': 'historico', 'estado_migracion': 'migrado',
                 'estado_origen': registro.get('estado') or '',
                 'estado_definitiva_origen': registro.get('estado_definitiva') or '',
                 'definitiva_origen': str(registro['definitiva']),
                 'registro_origen': registro, 'inscripcion_historica_erp_id': erp_id,
                 'ultimo_importado': {'campos': proyeccion(destino, campos), 'grupo_nota_swa': grupo_nota}}
        avance, creado = AvanceAsignaturaSwa.objects.using('default').get_or_create(
            periodo_swa=periodo, programa_swa_id=programa, asignatura_swa=str(asignatura),
            grupo_swa=str(grupo), codigo_estudiante=codigo, defaults=datos,
        )
        if not creado:
            if (avance.asociacion_id != asociacion or avance.inscripcion_actual_erp_id
                    or avance.inscripcion_historica_erp_id not in (None, erp_id)):
                raise ValueError('El intento histórico ya tiene un destino incompatible.')
            for campo, valor in datos.items():
                setattr(avance, campo, valor)
            avance.save(using='default', update_fields=list(datos))
        _mapear_fila(cur, 'avance', clave, avance.pk, lote, creado)
        for calificacion in calificaciones:
            tipo = calificacion['tipo_nota']
            nota, creada = CalificacionSwa.objects.using('default').get_or_create(
                avance=avance, tipo_nota_swa=tipo,
                defaults={'estado_origen': calificacion['estado'],
                          'nota_origen': str(calificacion['nota']) if calificacion['nota'] is not None else None,
                          'datos_origen': calificacion},
            )
            _mapear_fila(cur, 'calificacion', f'{clave}|{tipo}', nota.pk, lote, creada)
    cur.execute("""
        UPDATE migra.nota n SET estado = 'REVISION'
        WHERE n.estado = 'LISTA' AND EXISTS (
            SELECT 1 FROM t_alcance a WHERE a.nid = n.nid AND a.programa = n.programa AND a.periodo = n.periodo
        ) AND NOT EXISTS (
            SELECT 1 FROM public.ubicacion_avanceasignaturaswa v
            WHERE (v.periodo_swa, v.programa_swa_id, v.asignatura_swa, v.grupo_swa, v.codigo_estudiante)
                = (n.periodo, n.programa, n.asignatura::text, n.grupo::text, n.estudiante)
              AND v.inscripcion_historica_erp_id IS NOT NULL
        )
    """)
    res['nota_en_curso'] = 0


def _cargar_ingles_y_vinculos(cur, lote, res):
    # El contrato ERP aún no admite un motivo de homologación de esta migración.
    cur.execute("""
        SELECT count(*) FROM migra.homologacion_ingles h
        WHERE EXISTS (SELECT 1 FROM t_alcance a
                      WHERE a.nid = h.nid AND a.programa = h.programa AND a.periodo = h.periodo)
    """)
    res['homologacion_ingles_pendiente'] = cur.fetchone()[0]
    res['homologacion_ingles'] = 0
    res['historial_estado'] = 0
    res['vinculo'] = res['vinculo_pendiente'] = 0

    cur.execute("""
        SELECT t.nid, t.persona_id, t.programa, t.periodo, s.codigo,
               s.persona, s.periodo_ingreso
        FROM t_matricula t
        JOIN public.ubicacion_equivalenciamatriculaswa eq ON eq.periodo_swa = t.periodo
          AND eq.programa_swa_id = t.programa AND eq.codigo_estudiante = ANY(t.codigos)
        JOIN migra.mapa m ON m.entidad = 'matricula' AND m.clave = t.nid || '|' || t.programa || '|' || t.periodo
          AND m.erp_id = eq.matricula_erp_id
        JOIN swa_raw.estudiante s ON s.codigo = eq.codigo_estudiante AND s.programa = t.programa
        ORDER BY t.nid, t.programa IN (51, 52), t.programa, t.periodo, s.codigo
    """)
    candidatos = cur.fetchall()
    if not candidatos:
        return
    tipos = list(TipoVinculo.objects.using('default').filter(nombre__iexact='Estudiante'))
    if len(tipos) != 1:
        raise ValueError('Se requiere un único tipo de vínculo Estudiante en el ERP.')
    tipo = tipos[0]
    estudiantes = [SimpleNamespace(programa=p, codigo=c, persona_id=s, periodo_ingreso=i, estado='H')
                   for _, _, p, _, c, s, i in candidatos]
    mapa = {s: persona for _, persona, _, _, _, s, _ in candidatos}
    matriculas = {(per, p, c) for _, _, p, per, c, _, _ in candidatos}
    cur.execute('SELECT codigo, sede FROM swa_raw.programa')
    sedes = dict(cur.fetchall())
    vistos = set()
    for (nid, persona_id, programa, periodo, codigo, origen, ingreso), est in zip(candidatos, estudiantes):
        try:
            with transaction.atomic(using='default'):
                anteriores = EquivalenciaEstudianteSwa.objects.using('default').filter(codigo_estudiante=codigo)
                existente = anteriores.select_related('vinculo_erp').first()
                vinculo, creado = None, False
                if programa in (51, 52) and tiene_carrera(persona_id, estudiantes, mapa):
                    vinculo, motivo = resolver_vinculo_ingles(
                        est, periodo, persona_id, estudiantes, mapa, sedes, matriculas)
                    if vinculo is None:
                        raise ValueError(motivo)
                elif existente is not None:
                    vinculo = existente.vinculo_erp
                    if vinculo.persona_id != persona_id or vinculo.programa_id != programa:
                        raise ValueError('El código SWA tiene otro vínculo ERP.')
                else:
                    vinculo, creado = vinculo_principal(est, persona_id, tipo)
                eq = registrar_equivalencia(est, vinculo)
                asociacion_previa = AsociacionProgramaSwa.objects.using('default').filter(
                    codigo_estudiante=codigo, periodo_swa=periodo,
                ).first()
                asociacion = guardar_asociacion(est, periodo, persona_id, vinculo, '')
                _mapear_fila(cur, 'vinculo', f'{nid}|{vinculo.programa_id}|{vinculo.pk}', vinculo.pk, lote, creado)
                _mapear_fila(cur, 'equivalencia_estudiante', codigo, eq.pk, lote, existente is None)
                _mapear_fila(cur, 'asociacion_programa', f'{codigo}|{periodo}', asociacion.pk, lote,
                             asociacion_previa is None)
        except (VinculoPersona.DoesNotExist, ValueError):
            res['vinculo_pendiente'] += 1
        else:
            if vinculo.pk not in vistos:
                res['vinculo'] += int(creado)
                vistos.add(vinculo.pk)


def _pendientes(cur):
    cur.execute("""
        SELECT
          (SELECT count(*) FROM migra.persona WHERE estado = 'REVISION'),
          (SELECT count(*) FROM migra.matricula WHERE estado = 'REVISION'),
          (SELECT count(*) FROM migra.nota WHERE estado = 'REVISION'),
          (SELECT count(*) FROM revision.caso WHERE vigente AND decision IS NULL)
    """)
    return dict(zip(['personas', 'matriculas', 'notas', 'casos'], cur.fetchone()))


def _guardar_filas_importadas(cur, lote):
    for entidad, tabla in ENTIDADES:
        cur.execute(sql.SQL("""
            UPDATE migra.mapa m SET fila_importada = to_jsonb(t) FROM public.{tabla} t
            WHERE m.entidad = %s AND m.lote_id = %s AND m.creado AND m.erp_id = t.id
        """).format(tabla=sql.Identifier(tabla)), (entidad, lote))


def enviar(log=print, periodo=None, redepurar=True):
    """Envía matrículas históricas de una instantánea local, opcionalmente por periodo."""
    if periodo is not None and not (10001 <= periodo < PERIODO_CORTE and periodo % 10 in (1, 2)):
        raise ValueError('Seleccione un periodo cerrado anterior al corte.')
    error = None
    with bloqueo_migracion() as conexion, cursor_migracion(conexion) as cur:
        validar_esquema_destino('completo')
        validar_esquema_destino('notas')
        metricas = _depurar_en_cursor(cur, log) if redepurar else None
        cur.execute('INSERT INTO migra.lote DEFAULT VALUES RETURNING id')
        lote = cur.fetchone()[0]
        try:
            with transaction.atomic(using='default'):
                cur.execute("""
                    CREATE TEMP TABLE t_alcance ON COMMIT DROP AS
                    SELECT * FROM migra.matricula
                    WHERE estado = 'LISTA' AND periodo < %s
                      AND (%s::integer IS NULL OR periodo = %s)
                """, (PERIODO_CORTE, periodo, periodo))
                res = {}
                for nombre, paso in [('Catálogos', _cargar_catalogos), ('Personas', _cargar_personas),
                                     ('Matrículas', _cargar_academico),
                                     ('Inglés y vínculos', _cargar_ingles_y_vinculos), ('Notas', _cargar_notas)]:
                    inicio = time.monotonic()
                    paso(cur, lote, res)
                    log(f'- {nombre}: {time.monotonic() - inicio:.1f}s')
                res['pendientes_no_enviados'] = _pendientes(cur)
                _guardar_filas_importadas(cur, lote)
                cur.execute("""
                    UPDATE migra.lote SET fin = now(), estado = 'COMPLETADO', resumen = %s::jsonb
                    WHERE id = %s
                """, (json.dumps(res), lote))
        except Exception as exc:
            cur.execute("""
                UPDATE migra.lote SET fin = now(), estado = 'FALLIDO', error = %s WHERE id = %s
            """, (type(exc).__name__, lote))
            error = exc
    if error is not None:
        raise error
    resultado = {'lote': lote, 'estado': 'COMPLETADO', 'resumen': res}
    if metricas is not None:
        resultado['metricas'] = metricas
    return resultado


REFERENCIAS_LOGICAS = {
    'erp_persona': REFERENCIAS_LOGICAS_PERSONA,
    'erp_vinculopersona': [
        ('ubicacion_equivalenciaestudianteswa', 'vinculo_erp_id'),
        ('ubicacion_asociacionprogramaswa', 'vinculo_erp_id'),
    ],
    'academico_pensum': [('ubicacion_equivalenciapensumswa', 'pensum_erp_id')],
    'academico_matricula': [
        ('ubicacion_equivalenciamatriculaswa', 'matricula_erp_id'),
        ('ubicacion_avanceasignaturaswa', 'matricula_erp_id'),
    ],
    'academico_asignaturamatriculahistorico': [
        ('ubicacion_avanceasignaturaswa', 'inscripcion_historica_erp_id'),
    ],
    'academico_asignaturamatricula': [
        ('ubicacion_avanceasignaturaswa', 'inscripcion_actual_erp_id'),
    ],
}


def _comprobar_dependencias(cur, entidad, tabla, lote):
    cur.execute("""
        SELECT ns.nspname, hija.relname, array_agg(a.attname ORDER BY k.pos),
               array_agg(b.attname ORDER BY k.pos)
        FROM pg_catalog.pg_constraint fk
        JOIN pg_catalog.pg_class hija ON hija.oid = fk.conrelid
        JOIN pg_catalog.pg_namespace ns ON hija.relnamespace = ns.oid
        CROSS JOIN LATERAL unnest(fk.conkey, fk.confkey) WITH ORDINALITY AS k(ref, dato, pos)
        JOIN pg_catalog.pg_attribute a ON a.attrelid = hija.oid AND a.attnum = k.ref
        JOIN pg_catalog.pg_attribute b ON b.attrelid = fk.confrelid AND b.attnum = k.dato
        WHERE fk.contype = 'f' AND fk.confrelid = %s::regclass
        GROUP BY fk.oid, ns.nspname, hija.relname
    """, (f'public.{tabla}',))
    referencias = cur.fetchall()
    referencias.extend(('public', origen, [campo], ['id'])
                       for origen, campo in REFERENCIAS_LOGICAS.get(tabla, ()))
    for esquema, origen, campos, destinos in referencias:
        claves = sql.SQL(' AND ').join(
            sql.SQL('r.{} = t.{}').format(sql.Identifier(campo), sql.Identifier(destino))
            for campo, destino in zip(campos, destinos)
        )
        cur.execute(sql.SQL("""
            SELECT 1 FROM {origen} r JOIN public.{tabla} t ON {claves}
            JOIN migra.mapa m ON m.erp_id = t.id
            WHERE m.entidad = %s AND m.lote_id = %s AND m.creado LIMIT 1
        """).format(origen=sql.Identifier(esquema, origen), tabla=sql.Identifier(tabla),
                    claves=claves), (entidad, lote))
        if cur.fetchone():
            raise ValueError('El lote tiene filas referenciadas por otros datos.')


def revertir(lote, log=print):
    """Revierte filas del lote que conservan el estado y la propiedad verificados."""
    with bloqueo_migracion() as conexion, cursor_migracion(conexion) as cur:
        cur.execute("""
            SELECT estado, resumen->>'operacion' FROM migra.lote WHERE id = %s FOR UPDATE
        """, (lote,))
        registro = cur.fetchone()
        if registro is None or registro[0] != 'COMPLETADO':
            raise ValueError('Solo se pueden revertir lotes completados.')
        if registro[1] == ENTIDAD_REASIGNACION:
            raise ValueError('Para revertir una reasignación de IDs restaura el respaldo previo de esa copia.')
        borrados = {}
        for entidad, tabla in ENTIDADES:
            relacion = sql.Identifier(tabla)
            cur.execute(sql.SQL("""
                SELECT t.id FROM public.{tabla} t JOIN migra.mapa m ON m.erp_id = t.id
                WHERE m.entidad = %s AND m.lote_id = %s AND m.creado FOR UPDATE OF t
            """).format(tabla=relacion), (entidad, lote))
            cur.fetchall()
            cur.execute(sql.SQL("""
                SELECT 1 FROM migra.mapa m LEFT JOIN public.{tabla} t ON t.id = m.erp_id
                WHERE m.entidad = %s AND m.lote_id = %s AND m.creado AND (
                    m.fila_importada IS NULL OR t.id IS NULL OR to_jsonb(t) <> m.fila_importada
                    OR EXISTS (SELECT 1 FROM migra.mapa otro WHERE otro.entidad = m.entidad
                               AND otro.erp_id = m.erp_id AND otro.lote_id <> m.lote_id)
                ) LIMIT 1
            """).format(tabla=relacion), (entidad, lote))
            if cur.fetchone():
                raise ValueError('El lote tiene filas modificadas, sin trazabilidad o usadas por otro lote.')
            _comprobar_dependencias(cur, entidad, tabla, lote)
            cur.execute(sql.SQL("""
                DELETE FROM public.{tabla} t USING migra.mapa m
                WHERE m.entidad = %s AND m.lote_id = %s AND m.creado AND t.id = m.erp_id
            """).format(tabla=relacion), (entidad, lote))
            borrados[entidad] = cur.rowcount
            log(f'{entidad}: {cur.rowcount}')
        cur.execute('DELETE FROM migra.mapa WHERE lote_id = %s', (lote,))
        cur.execute("UPDATE migra.lote SET estado = 'REVERTIDO' WHERE id = %s", (lote,))
    return borrados
