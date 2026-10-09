"""Carga ("Enviar a la copia (ensayo)" y migra_corte) de lo depurado en `migra.*` hacia la copia del ERP (`public`).

- Solo se envian filas en estado LISTA; lo que esta en REVISION o EXCLUIDA no viaja.
- Todo ocurre en una sola transaccion: si algo falla, el ERP queda intacto.
- Cada fila creada queda en `migra.mapa` (entidad, clave SWA -> id ERP, lote). Por eso
  la carga se puede repetir (solo envia lo nuevo) y un lote se puede revertir.
- Los ids se toman de las secuencias del ERP, salvo el de persona: se conserva su codigo
  de estudiante SWA mas reciente (papeleria y gestion). Solo si se decidio generar uno nuevo
  se usa la regla del ERP <anio><periodo><programa><consecutivo> (`erp_secuenciacodigopersona`).
"""
import json
import time

from .conexiones import conectar_destino

TIPO_VINCULO_ESTUDIANTE = 'Estudiante'
ESTADO_MATRICULADO = 'MATRICULADO'
MOTIVO_HOMOLOGACION = 'MIGRACION_SWA'

# Orden inverso de dependencias, usado para revertir.
ENTIDADES = [
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


def _mapear(cur, entidad, lote, sql_claves, creado=True):
    """Inserta en migra.mapa las (clave, erp_id) que devuelve sql_claves.

    creado=False marca filas que ya existian en el ERP: se enlazan pero nunca se revierten.
    """
    cur.execute(f"""
        INSERT INTO migra.mapa (entidad, clave, erp_id, lote_id, creado)
        SELECT %s, clave, erp_id, %s, %s FROM ({sql_claves}) x
        ON CONFLICT DO NOTHING
    """, (entidad, lote, creado))
    return cur.rowcount


def _nuevos_ids(cur, tabla, tmp):
    """Asigna ids de la secuencia de `tabla` a las filas de la tabla temporal `tmp`."""
    cur.execute(f"UPDATE {tmp} SET erp_id = nextval(pg_get_serial_sequence('{tabla}', 'id'))")


def _cargar_catalogos(cur, lote, res):
    # Periodos: se reutilizan los que ya existen por nombre; los historicos se crean inactivos.
    res['periodo'] = _mapear(cur, 'periodo', lote, """
        SELECT mp.codigo::text AS clave, pa.id AS erp_id
        FROM migra.periodo mp JOIN public.academico_periodoacademico pa ON pa.nombre = mp.nombre
    """, creado=False)
    cur.execute("""
        CREATE TEMP TABLE t_periodo ON COMMIT DROP AS
        SELECT mp.*, NULL::bigint AS erp_id FROM migra.periodo mp
        WHERE NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'periodo' AND m.clave = mp.codigo::text)
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
    # Solo se crean las asignaturas canonicas: las equivalentes de SWA se enlazan a ella.
    cur.execute("""
        CREATE TEMP TABLE t_asignatura ON COMMIT DROP AS
        SELECT ma.*, NULL::bigint AS erp_id FROM migra.asignatura ma
        WHERE ma.erp_codigo = ma.codigo::text
          AND NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'asignatura' AND m.clave = ma.codigo::text)
    """)
    _nuevos_ids(cur, 'academico_asignatura', 't_asignatura')
    cur.execute("""
        INSERT INTO public.academico_asignatura (id, codigo, nombre, metodologia, creditos, hora_teorica,
                                                 hora_practica, tipo_asignatura)
        SELECT erp_id, erp_codigo, left(nombre, 200), 'PRESENCIAL', creditos, least(ihs, 32767), 0, 'TEORICA'
        FROM t_asignatura
    """)
    res['asignatura'] += _mapear(cur, 'asignatura', lote, 'SELECT codigo::text AS clave, erp_id FROM t_asignatura')
    res['asignatura_equivalente'] = _mapear(cur, 'asignatura', lote, """
        SELECT ma.codigo::text AS clave, a.id AS erp_id
        FROM migra.asignatura ma JOIN public.academico_asignatura a ON a.codigo = ma.erp_codigo
        WHERE ma.erp_codigo <> ma.codigo::text
    """, creado=False)

    # Pensums historicos de SWA
    cur.execute("""
        CREATE TEMP TABLE t_pensum ON COMMIT DROP AS
        SELECT mp.*, per.erp_id AS periodo_id, NULL::bigint AS erp_id
        FROM migra.pensum mp
        JOIN migra.mapa per ON per.entidad = 'periodo' AND per.clave = mp.periodo_codigo::text
        WHERE EXISTS (SELECT 1 FROM public.academico_programa ap WHERE ap.id = mp.programa)
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
        INSERT INTO public.academico_pensumasignaturarestriccion (id, tipo, asignatura_id, pensum_asignatura_id)
        SELECT erp_id, tipo, asignatura_id, pensum_asignatura_id FROM t_req
        ON CONFLICT DO NOTHING
    """)
    res['requisito'] = _mapear(cur, 'requisito', lote, """
        SELECT concat_ws('|', programa, pensum, asignatura, requisito, tipo) AS clave, erp_id FROM t_req
    """)


def _cargar_personas(cur, lote, res):
    res['persona_vinculada'] = _mapear(cur, 'persona', lote, """
        SELECT nid AS clave, erp_persona_id AS erp_id FROM migra.persona
        WHERE estado = 'LISTA' AND accion = 'VINCULAR'
    """, creado=False)
    cur.execute("""
        SELECT nid, prefijo, codigo_swa FROM migra.persona p
        WHERE estado = 'LISTA' AND accion = 'CREAR'
          AND NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'persona' AND m.clave = p.nid)
        ORDER BY prefijo, primer_apellido, segundo_apellido, primer_nombre, nid
    """)
    pendientes = cur.fetchall()
    cur.execute('SELECT prefijo, consecutivo FROM public.erp_secuenciacodigopersona FOR UPDATE')
    secuencias = dict(cur.fetchall())
    usadas_secuencias = set()
    cur.execute('SELECT id FROM public.erp_persona')
    usados = {r[0] for r in cur.fetchall()}
    asignados = []
    res['persona_codigo_swa'] = res['persona_codigo_generado'] = 0
    for nid, prefijo, codigo_swa in pendientes:
        if codigo_swa and int(codigo_swa) not in usados:
            usados.add(int(codigo_swa))
            asignados.append((nid, int(codigo_swa)))
            res['persona_codigo_swa'] += 1
            continue
        if codigo_swa:
            raise ValueError(f'El codigo SWA {codigo_swa} ya existe en el ERP (persona {nid}); vuelva a depurar.')
        usadas_secuencias.add(prefijo)
        res['persona_codigo_generado'] += 1
        consecutivo = secuencias.get(prefijo, 0)
        while True:
            consecutivo += 1
            nuevo = int(f'{prefijo}{consecutivo:03d}')
            if nuevo not in usados:
                break
        secuencias[prefijo] = consecutivo
        usados.add(nuevo)
        asignados.append((nid, nuevo))
    cur.execute('CREATE TEMP TABLE t_persona_id (nid text PRIMARY KEY, erp_id bigint) ON COMMIT DROP')
    with cur.copy('COPY t_persona_id (nid, erp_id) FROM STDIN') as copy:
        for fila in asignados:
            copy.write_row(fila)
    for prefijo in usadas_secuencias:
        consecutivo = secuencias[prefijo]
        cur.execute("""
            INSERT INTO public.erp_secuenciacodigopersona (prefijo, consecutivo) VALUES (%s, %s)
            ON CONFLICT (prefijo) DO UPDATE SET consecutivo = greatest(
                public.erp_secuenciacodigopersona.consecutivo, EXCLUDED.consecutivo)
        """, (prefijo, consecutivo))
    cur.execute("""
        INSERT INTO public.erp_persona (id, nro_documento, primer_nombre, primer_apellido, segundo_nombre,
                                        segundo_apellido, email, telefono, fecha_creacion, tipo_documento_id,
                                        fecha_nacimiento)
        SELECT t.erp_id, p.nro_documento, left(p.primer_nombre, 100), left(p.primer_apellido, 100),
               left(coalesce(p.segundo_nombre, ''), 100), left(coalesce(p.segundo_apellido, ''), 100),
               coalesce(p.email, ''), p.telefono, now(), p.tipo_documento_id, p.fecha_nacimiento
        FROM t_persona_id t JOIN migra.persona p ON p.nid = t.nid
    """)
    res['persona_creada'] = _mapear(cur, 'persona', lote, 'SELECT nid AS clave, erp_id FROM t_persona_id')

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
        WHERE NOT EXISTS (SELECT 1 FROM public.erp_datossecundarios d WHERE d.persona_id = x.erp_id)
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
        FROM migra.matricula m
        JOIN migra.mapa per ON per.entidad = 'persona' AND per.clave = m.nid
        JOIN migra.mapa pe ON pe.entidad = 'pensum' AND pe.clave = m.programa || '|' || m.pensum
        JOIN migra.mapa pr ON pr.entidad = 'periodo' AND pr.clave = m.periodo::text
        JOIN migra.periodo mp ON mp.codigo = m.periodo
        WHERE m.estado = 'LISTA'
          AND NOT EXISTS (SELECT 1 FROM migra.mapa x WHERE x.entidad = 'matricula'
                          AND x.clave = m.nid || '|' || m.programa || '|' || m.periodo)
    """)
    _nuevos_ids(cur, 'academico_matricula', 't_matricula')
    cur.execute("""
        INSERT INTO public.academico_matricula (id, estado, semestre_actual, fecha_inicio, fecha_fin, codigo,
                                                persona_id, pensum_id, periodo_id, programa_id)
        SELECT erp_id, activa, greatest(nivel, 0), coalesce(fecha_inicio::timestamptz, now()),
               CASE WHEN activa THEN NULL ELSE fecha_fin::timestamptz END,
               left(codigo, 20), persona_id, pensum_id, periodo_id, programa
        FROM t_matricula
    """)
    res['matricula'] = _mapear(cur, 'matricula', lote, """
        SELECT nid || '|' || programa || '|' || periodo AS clave, erp_id FROM t_matricula
    """)

    cur.execute("""
        CREATE TEMP TABLE t_nota ON COMMIT DROP AS
        SELECT n.*, concat_ws('|', n.periodo, n.programa, n.asignatura, n.grupo, n.estudiante) AS clave,
               mat.erp_id AS matricula_id, per.erp_id AS persona_id, asg.erp_id AS asignatura_id,
               pr.erp_id AS periodo_id, NULL::bigint AS erp_id
        FROM migra.nota n
        JOIN migra.mapa mat ON mat.entidad = 'matricula' AND mat.clave = n.nid || '|' || n.programa || '|' || n.periodo
        JOIN migra.mapa per ON per.entidad = 'persona' AND per.clave = n.nid
        JOIN migra.mapa asg ON asg.entidad = 'asignatura' AND asg.clave = n.asignatura::text
        JOIN migra.mapa pr ON pr.entidad = 'periodo' AND pr.clave = n.periodo::text
        WHERE n.estado = 'LISTA'
          AND NOT EXISTS (SELECT 1 FROM migra.mapa x WHERE x.entidad IN ('nota_historica', 'nota_en_curso')
                          AND x.clave = concat_ws('|', n.periodo, n.programa, n.asignatura, n.grupo, n.estudiante))
    """)
    cur.execute("UPDATE t_nota SET erp_id = nextval(pg_get_serial_sequence('academico_asignaturamatriculahistorico', 'id')) WHERE NOT en_curso")
    cur.execute("UPDATE t_nota SET erp_id = nextval(pg_get_serial_sequence('academico_asignaturamatricula', 'id')) WHERE en_curso")
    cur.execute("""
        INSERT INTO public.academico_asignaturamatriculahistorico (id, definitiva, asignatura_id, persona_id,
                                                                   matricula_id, periodo_id, programa_id)
        SELECT erp_id, definitiva, asignatura_id, persona_id, matricula_id, periodo_id, programa
        FROM t_nota WHERE NOT en_curso
    """)
    cur.execute("""
        INSERT INTO public.academico_asignaturamatricula (id, codigo_grupo, definitiva, asignatura_id, persona_id,
                                                          carga_id, matricula_id, periodo_id, programa_id)
        SELECT erp_id, grupo::text, nullif(definitiva, 0), asignatura_id, persona_id, NULL, matricula_id,
               periodo_id, programa
        FROM t_nota WHERE en_curso
    """)
    res['nota_historica'] = _mapear(cur, 'nota_historica', lote, 'SELECT clave, erp_id FROM t_nota WHERE NOT en_curso')
    res['nota_en_curso'] = _mapear(cur, 'nota_en_curso', lote, 'SELECT clave, erp_id FROM t_nota WHERE en_curso')


def _cargar_ingles_y_vinculos(cur, lote, res):
    cur.execute("""
        CREATE TEMP TABLE t_homologacion ON COMMIT DROP AS
        SELECT h.*, per.erp_id AS persona_id, NULL::bigint AS erp_id
        FROM migra.homologacion_ingles h
        JOIN migra.mapa per ON per.entidad = 'persona' AND per.clave = h.nid
        WHERE NOT EXISTS (SELECT 1 FROM migra.mapa x WHERE x.entidad = 'homologacion_ingles'
                          AND x.clave = h.nid || '|' || h.programa || '|' || h.modulo)
          AND NOT EXISTS (SELECT 1 FROM public.academico_homologacioningles e
                          WHERE e.persona_id = per.erp_id AND e.programa_id = h.programa AND e.modulo = h.modulo)
    """)
    _nuevos_ids(cur, 'academico_homologacioningles', 't_homologacion')
    cur.execute("""
        INSERT INTO public.academico_homologacioningles (id, modulo, motivo, acto, observacion, activo,
                                                         fecha_registro, persona_id, programa_id)
        SELECT erp_id, modulo, %s, left('SWA ' || codigo || '/' || periodo, 60),
               left(format('Migrado de SWA. Aprobado en %%s con %%s (programa SWA %%s)%%s',
                           periodo, definitiva, programa,
                           CASE WHEN origen = 'CENTRO_ACTUAL' THEN '; copiado al centro actual' ELSE '' END), 500),
               true, now(), persona_id, programa
        FROM t_homologacion
    """, (MOTIVO_HOMOLOGACION,))
    res['homologacion_ingles'] = _mapear(cur, 'homologacion_ingles', lote, """
        SELECT nid || '|' || programa || '|' || modulo AS clave, erp_id FROM t_homologacion
    """)

    cur.execute('SELECT id FROM public.erp_tipovinculo WHERE nombre ILIKE %s', (TIPO_VINCULO_ESTUDIANTE,))
    tipo_vinculo = cur.fetchone()[0]
    cur.execute('SELECT id FROM public.erp_tipoestado WHERE codigo = %s', (ESTADO_MATRICULADO,))
    estado_matriculado = cur.fetchone()[0]
    cur.execute("""
        CREATE TEMP TABLE t_vinculo ON COMMIT DROP AS
        SELECT v.*, per.erp_id AS persona_id, p1.fecha_inicio, pu.fecha_fin, pu.nombre AS periodo_nombre,
               NULL::bigint AS erp_id
        FROM migra.vinculo v
        JOIN migra.mapa per ON per.entidad = 'persona' AND per.clave = v.nid
        JOIN migra.periodo p1 ON p1.codigo = v.primer_periodo
        JOIN migra.periodo pu ON pu.codigo = v.ultimo_periodo
        WHERE NOT EXISTS (SELECT 1 FROM migra.mapa x WHERE x.entidad = 'vinculo' AND x.clave = v.nid || '|' || v.programa)
    """)
    _nuevos_ids(cur, 'erp_vinculopersona', 't_vinculo')
    cur.execute("""
        INSERT INTO public.erp_vinculopersona (id, activo, periodo, fecha_inicio, fecha_fin, persona_id, programa_id,
                                               programa_secundario_id, tipo_vinculo_id)
        SELECT erp_id, activo, periodo_nombre, coalesce(fecha_inicio, current_date),
               CASE WHEN activo THEN NULL ELSE fecha_fin END, persona_id, programa, NULL, %s
        FROM t_vinculo
    """, (tipo_vinculo,))
    res['vinculo'] = _mapear(cur, 'vinculo', lote, "SELECT nid || '|' || programa AS clave, erp_id FROM t_vinculo")

    cur.execute("""
        CREATE TEMP TABLE t_historial ON COMMIT DROP AS
        SELECT t.nid || '|' || t.programa AS clave, t.persona_id, t.erp_id AS vinculo_id, NULL::bigint AS erp_id
        FROM t_vinculo t WHERE t.activo
    """)
    _nuevos_ids(cur, 'erp_historialestadopersona', 't_historial')
    cur.execute("""
        INSERT INTO public.erp_historialestadopersona (id, activo, fecha_inicio, fecha_fin, persona_id, estado_id,
                                                      vinculo_id)
        SELECT erp_id, true, now(), NULL, persona_id, %s, vinculo_id FROM t_historial
    """, (estado_matriculado,))
    res['historial_estado'] = _mapear(cur, 'historial_estado', lote, 'SELECT clave, erp_id FROM t_historial')


def _sincronizar_secuencias(cur):
    """Las copias restauradas del ERP pueden traer secuencias atrasadas frente a max(id)."""
    for _, tabla in ENTIDADES:
        cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (f'public.{tabla}',))
        secuencia = cur.fetchone()[0]
        if secuencia:
            cur.execute(f"""
                SELECT setval(%s, greatest((SELECT coalesce(max(id), 0) FROM public.{tabla}),
                                           (SELECT last_value FROM {secuencia})), true)
            """, (secuencia,))


def _pendientes(cur):
    cur.execute("""
        SELECT
          (SELECT count(*) FROM migra.persona WHERE estado = 'REVISION'),
          (SELECT count(*) FROM migra.matricula WHERE estado = 'REVISION'),
          (SELECT count(*) FROM migra.nota WHERE estado = 'REVISION'),
          (SELECT count(*) FROM revision.caso WHERE vigente AND decision IS NULL)
    """)
    return dict(zip(['personas', 'matriculas', 'notas', 'casos'], cur.fetchone()))


def enviar(log=print):
    """Envia todo lo LISTA a la copia del ERP. Devuelve el resumen del lote."""
    with conectar_destino(autocommit=True) as control:
        ccur = control.cursor()
        ccur.execute('INSERT INTO migra.lote DEFAULT VALUES RETURNING id')
        lote = ccur.fetchone()[0]
        try:
            with conectar_destino() as conn:
                cur = conn.cursor()
                res = {}
                _sincronizar_secuencias(cur)
                for nombre, paso in [('Catalogos', _cargar_catalogos), ('Personas', _cargar_personas),
                                     ('Matriculas y notas', _cargar_academico),
                                     ('Ingles y vinculos', _cargar_ingles_y_vinculos)]:
                    inicio = time.monotonic()
                    paso(cur, lote, res)
                    log(f'- {nombre}: {time.monotonic() - inicio:.1f}s')
                res['pendientes_no_enviados'] = _pendientes(cur)
                conn.commit()
            ccur.execute("UPDATE migra.lote SET fin = now(), estado = 'COMPLETADO', resumen = %s::jsonb WHERE id = %s",
                         (json.dumps(res), lote))
            return {'lote': lote, 'estado': 'COMPLETADO', 'resumen': res}
        except Exception as exc:
            ccur.execute("UPDATE migra.lote SET fin = now(), estado = 'FALLIDO', error = %s WHERE id = %s",
                         (str(exc), lote))
            raise


def revertir(lote, log=print):
    """Elimina del ERP todo lo que creo un lote (en orden inverso de dependencias)."""
    with conectar_destino() as conn:
        cur = conn.cursor()
        borrados = {}
        for entidad, tabla in ENTIDADES:
            cur.execute(f"""
                DELETE FROM public.{tabla} t USING migra.mapa m
                WHERE m.entidad = %s AND m.lote_id = %s AND m.creado AND t.id = m.erp_id
            """, (entidad, lote))
            borrados[entidad] = cur.rowcount
            log(f'{entidad}: {cur.rowcount}')
        # el consecutivo de cada prefijo vuelve al mayor codigo de persona que sigue existiendo
        cur.execute("""
            UPDATE public.erp_secuenciacodigopersona s
            SET consecutivo = coalesce((SELECT max(substr(p.id::text, length(s.prefijo) + 1)::int)
                                        FROM public.erp_persona p
                                        WHERE p.id::text LIKE s.prefijo || '%%'
                                          AND length(p.id::text) = length(s.prefijo) + 3), 0)
            WHERE s.prefijo IN (SELECT DISTINCT prefijo FROM migra.persona
                                WHERE prefijo IS NOT NULL AND codigo_swa IS NULL)
        """)
        cur.execute('DELETE FROM migra.mapa WHERE lote_id = %s', (lote,))
        cur.execute("UPDATE migra.lote SET estado = 'REVERTIDO' WHERE id = %s", (lote,))
        conn.commit()
    return borrados
