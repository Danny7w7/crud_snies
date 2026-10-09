"""Metricas de la migracion: antes (SWA), despues (migra), % depuracion y riesgo de perdida.

Definiciones por entidad:
- antes:      filas en SWA dentro del alcance.
- tratadas:   filas corregidas, unificadas o descartadas por una regla.
- perdida:    filas con informacion util que NO llegaran al ERP (excluidas o descartadas con valor).
- revision:   filas que esperan una decision humana (pueden perderse si se excluyen).
- % depuracion           = tratadas / antes
- % perdida confirmada   = perdida / antes
- % probabilidad perdida = (perdida + revision) / antes   (peor caso)
"""


def _uno(cur, sql, params=None):
    cur.execute(sql, params)
    fila = cur.fetchone()
    return fila[0] if fila and len(fila) == 1 else fila


def _pct(parte, total):
    return round(100.0 * parte / total, 2) if total else 0.0


def _entidad(nombre, antes, despues, tratadas, perdida, revision, detalle=None):
    return {
        'entidad': nombre,
        'antes': antes,
        'despues': despues,
        'tratadas': tratadas,
        'perdida': perdida,
        'revision': revision,
        'pct_depuracion': _pct(tratadas, antes),
        'pct_perdida_confirmada': _pct(perdida, antes),
        'pct_probabilidad_perdida': _pct(perdida + revision, antes),
        'detalle': detalle or {},
    }


def calcular_metricas(cur):
    # ---------------------------------------------------------------- personas
    swa_total_personas = _uno(cur, 'SELECT count(*) FROM swa_raw.persona')
    p_antes = _uno(cur, 'SELECT count(*) FROM migra.persona_fuente')
    origenes = dict(_filas(cur, 'SELECT origen, count(*) FROM migra.persona_fuente GROUP BY 1'))
    solo_codigo = _uno(cur, "SELECT count(*) FROM migra.descarte WHERE motivo = 'SOLO_CODIGO_SIN_MATRICULA'")
    unicas, lista, rev, excl, depuradas = _uno(cur, """
        SELECT count(*), count(*) FILTER (WHERE estado = 'LISTA'),
               count(*) FILTER (WHERE estado = 'REVISION'), count(*) FILTER (WHERE estado = 'EXCLUIDA'),
               count(*) FILTER (WHERE transformaciones <> '{}')
        FROM migra.persona
    """)
    cur.execute("""
        SELECT t, count(*) FROM migra.persona, unnest(transformaciones) t GROUP BY t ORDER BY 2 DESC
    """)
    transformaciones = dict(cur.fetchall())
    cur.execute("""
        SELECT a, count(*) FROM migra.persona, unnest(alertas) a GROUP BY a ORDER BY 2 DESC
    """)
    alertas = dict(cur.fetchall())
    acciones = dict(_filas(cur, "SELECT accion, count(*) FROM migra.persona WHERE estado='LISTA' GROUP BY 1"))
    filas_tratadas = _uno(cur, "SELECT coalesce(sum(cardinality(swa_ids)), 0) FROM migra.persona WHERE transformaciones <> '{}'")
    filas_rev = _uno(cur, "SELECT coalesce(sum(cardinality(swa_ids)), 0) FROM migra.persona WHERE estado = 'REVISION'")
    filas_excl = _uno(cur, "SELECT coalesce(sum(cardinality(swa_ids)), 0) FROM migra.persona WHERE estado = 'EXCLUIDA'")
    personas = _entidad(
        'Personas', p_antes, lista, filas_tratadas, filas_excl, filas_rev, {
            'personas_swa_total': swa_total_personas,
            'con_matricula': origenes.get('MATRICULA', 0),
            'solo_notas_sin_matricula': origenes.get('SOLO_NOTAS', 0),
            'solo_prematricula': origenes.get('PREMATRICULA', 0),
            'solo_codigo_fuera_de_alcance': solo_codigo,
            'personas_unicas': unicas,
            'personas_corregidas': depuradas,
            'unificadas': p_antes - unicas,
            'en_revision': rev,
            'excluidas': excl,
            'acciones': acciones,
            'transformaciones': transformaciones,
            'alertas_calidad': alertas,
        })

    # -------------------------------------------------------------- matriculas
    m_antes = _uno(cur, """
        SELECT count(*) FROM swa_raw.historico h JOIN migra.estudiante e ON e.codigo = h.estudiante
        WHERE h.estado = 'M' OR (h.estado = 'P' AND e.nid IN (
            SELECT nid FROM migra.persona_fuente GROUP BY nid HAVING bool_and(origen = 'PREMATRICULA')))
    """)
    m = dict(_filas(cur, 'SELECT origen || \'_\' || estado, count(*) FROM migra.matricula GROUP BY 1'))
    m_hist = sum(v for k, v in m.items() if k.startswith(('HISTORICO_M', 'PREMATRICULA')))
    m_inferidas = sum(v for k, v in m.items() if k.startswith('INFERIDA'))
    m_lista = sum(v for k, v in m.items() if k.endswith('_LISTA'))
    m_rev = _uno(cur, "SELECT count(*) FROM migra.matricula WHERE estado = 'REVISION' AND origen <> 'INFERIDA_NOTAS'")
    m_excl = _uno(cur, "SELECT count(*) FROM migra.matricula WHERE estado = 'EXCLUIDA' AND origen <> 'INFERIDA_NOTAS'")
    activas = _uno(cur, "SELECT count(*) FROM migra.matricula WHERE activa AND estado = 'LISTA'")
    matriculas = _entidad(
        'Matriculas', m_antes, m_lista, (m_antes - m_hist) + m_inferidas, m_excl, m_rev, {
            'codigos_unificados': m_antes - m_hist,
            'inferidas_desde_notas': m_inferidas,
            'vigentes_en_erp': activas,
            'por_origen_estado': m,
        })

    # ------------------------------------------------------------------- notas
    n_antes = _uno(cur, 'SELECT count(*) FROM swa_raw.registro r JOIN migra.estudiante e ON e.codigo = r.estudiante')
    n_lista, n_rev, n_excl, n_curso = _uno(cur, """
        SELECT count(*) FILTER (WHERE estado = 'LISTA'), count(*) FILTER (WHERE estado = 'REVISION'),
               count(*) FILTER (WHERE estado = 'EXCLUIDA'), count(*) FILTER (WHERE en_curso AND estado = 'LISTA')
        FROM migra.nota
    """)
    descartes = dict(_filas(cur, "SELECT motivo, count(*) FROM migra.descarte WHERE entidad = 'nota' GROUP BY 1"))
    desc_valor = _uno(cur, "SELECT count(*) FROM migra.descarte WHERE entidad = 'nota' AND con_valor")
    sin_destino = n_antes - n_lista - n_rev - n_excl - sum(descartes.values())
    notas = _entidad(
        'Notas', n_antes, n_lista, sum(descartes.values()), n_excl + desc_valor + max(sin_destino, 0), n_rev, {
            'aplazadas_periodo_en_curso': descartes.get('PERIODO_EN_CURSO_SE_MIGRA_AL_CIERRE', 0),
            'historicas': n_lista - n_curso,
            'descartes_por_regla': descartes,
            'descartes_con_valor': desc_valor,
            'sin_matricula_asociada': max(sin_destino, 0),
        })

    # ------------------------------------------------------------------ ingles
    ingles = dict(zip(['homologaciones', 'personas', 'centro_actual'], _uno(cur, """
        SELECT count(*), count(DISTINCT nid), count(*) FILTER (WHERE origen = 'CENTRO_ACTUAL')
        FROM migra.homologacion_ingles
    """)))
    cur.execute("""
        SELECT modulo, count(*) FROM migra.homologacion_ingles WHERE origen = 'APROBADO' GROUP BY 1 ORDER BY 1
    """)
    ingles['por_modulo'] = {str(k): v for k, v in cur.fetchall()}

    # -------------------------------------------------------------- catalogos
    catalogos = dict(zip(['periodos', 'periodos_existentes_erp', 'asignaturas', 'pensums', 'pensum_asignaturas',
                          'requisitos', 'vinculos', 'vinculos_activos'], _uno(cur, """
        SELECT (SELECT count(*) FROM migra.periodo),
               (SELECT count(*) FROM migra.periodo WHERE erp_periodo_id IS NOT NULL),
               (SELECT count(*) FROM migra.asignatura), (SELECT count(*) FROM migra.pensum),
               (SELECT count(*) FROM migra.pensum_asignatura), (SELECT count(*) FROM migra.prerrequisito),
               (SELECT count(*) FROM migra.vinculo), (SELECT count(*) FROM migra.vinculo WHERE activo)
    """)))

    # --------------------------------------------------------------- revision
    cur.execute("""
        SELECT tipo, entidad, severidad, count(*), count(*) FILTER (WHERE decision IS NULL),
               sum(filas_afectadas)
        FROM revision.caso WHERE vigente GROUP BY 1, 2, 3 ORDER BY 3, 1
    """)
    revision = [dict(zip(['tipo', 'entidad', 'severidad', 'casos', 'pendientes', 'filas'], f))
                for f in cur.fetchall()]

    # Que se crea o se enlaza en cada tabla del ERP (solo lo que esta LISTA)
    v = dict(zip(
        ['crear_personas', 'vincular_personas', 'matriculas', 'matriculas_vigentes', 'notas_historicas',
         'notas_aplazadas', 'modulos_ingles', 'vinculos', 'vinculos_activos', 'personas_varios_codigos',
         'periodos_nuevos',
         'periodos_existentes', 'asignaturas', 'pensums', 'pensum_asignaturas', 'requisitos', 'equivalentes'],
        _uno(cur, """
            SELECT (SELECT count(*) FROM migra.persona WHERE estado = 'LISTA' AND accion = 'CREAR'),
                   (SELECT count(*) FROM migra.persona WHERE estado = 'LISTA' AND accion = 'VINCULAR'),
                   (SELECT count(*) FROM migra.matricula WHERE estado = 'LISTA'),
                   (SELECT count(*) FROM migra.matricula WHERE estado = 'LISTA' AND activa),
                   (SELECT count(*) FROM migra.nota WHERE estado = 'LISTA' AND NOT en_curso),
                   (SELECT count(*) FROM migra.descarte WHERE motivo = 'PERIODO_EN_CURSO_SE_MIGRA_AL_CIERRE'),
                   (SELECT count(*) FROM migra.homologacion_ingles),
                   (SELECT count(*) FROM migra.vinculo), (SELECT count(*) FROM migra.vinculo WHERE activo),
                   (SELECT count(*) FROM migra.persona WHERE estado = 'LISTA' AND cardinality(codigos_swa) > 1),
                   (SELECT count(*) FROM migra.periodo WHERE erp_periodo_id IS NULL),
                   (SELECT count(*) FROM migra.periodo WHERE erp_periodo_id IS NOT NULL),
                   (SELECT count(*) FROM migra.asignatura WHERE erp_codigo = codigo::text),
                   (SELECT count(*) FROM migra.pensum),
                   (SELECT count(*) FROM migra.pensum_asignatura), (SELECT count(*) FROM migra.prerrequisito),
                   (SELECT count(*) FROM migra.asignatura WHERE erp_codigo <> codigo::text)
        """)))
    vinculacion = [
        {'item': 'Personas', 'tabla': 'erp_persona', 'cantidad': v['crear_personas'],
         'como': f"Su ID en el ERP es su código de estudiante de SWA más reciente (se conserva para papelería). "
                 f"{v['personas_varios_codigos']} tienen varios códigos: los demás quedan en cada matrícula."},
        {'item': 'Personas que ya existen en el ERP', 'tabla': 'erp_persona', 'cantidad': v['vincular_personas'],
         'como': 'No se crean: el historial se enlaza a la ficha existente, buscada por número de documento.'},
        {'item': 'Matrículas', 'tabla': 'academico_matricula', 'cantidad': v['matriculas'],
         'como': f"Una por persona + programa + periodo, enlazada a su pensum y con el código SWA con que se "
                 f"hizo. Todas quedan como históricas ({v['matriculas_vigentes']} vigentes del periodo activo del ERP, 2027-1); "
                 'la matrícula 2027-1 la hace el ERP.'},
        {'item': 'Notas históricas', 'tabla': 'academico_asignaturamatriculahistorico', 'cantidad': v['notas_historicas'],
         'como': 'Nota definitiva por asignatura, enlazada a la matrícula del periodo.'},
        {'item': 'Datos secundarios', 'tabla': 'erp_datossecundarios', 'cantidad': v['crear_personas'],
         'como': 'Sexo, estado civil y dirección que trae SWA. La fecha de expedición del documento no existe en SWA '
                 '(queda vacía).'},
        {'item': 'Notas 2026-2 (aplazadas)', 'tabla': '—', 'cantidad': v['notas_aplazadas'],
         'como': 'El periodo aún no cierra en SWA: estas notas se migran cuando cierre (re-copia y re-depuración).'},
        {'item': 'Módulos de inglés aprobados', 'tabla': 'academico_homologacioningles', 'cantidad': v['modulos_ingles'],
         'como': 'Cada módulo aprobado en el Centro de Idiomas (51/52) queda como completado (motivo MIGRACION_SWA).'},
        {'item': 'Vínculos de estudiante', 'tabla': 'erp_vinculopersona', 'cantidad': v['vinculos'],
         'como': f"Uno por persona y programa (tipo Estudiante). {v['vinculos_activos']} activos (matriculados en 2026-2 "
                 'o después) con estado MATRICULADO.'},
        {'item': 'Periodos académicos', 'tabla': 'academico_periodoacademico',
         'cantidad': v['periodos_nuevos'],
         'como': f"Periodos históricos que no existen (se crean inactivos). {v['periodos_existentes']} ya existían y se reutilizan."},
        {'item': 'Asignaturas, pensums y requisitos', 'tabla': 'academico_asignatura / pensum / pensumasignatura',
         'cantidad': v['asignaturas'] + v['pensums'] + v['pensum_asignaturas'] + v['requisitos'],
         'como': f"{v['asignaturas']} asignaturas ({v['equivalentes']} de SWA unificadas por equivalencia), "
                 f"{v['pensums']} pensums de SWA (incluye inactivos), "
                 f"{v['pensum_asignaturas']} asignaturas por pensum y {v['requisitos']} prerrequisitos."},
    ]

    entidades = [personas, matriculas, notas]
    antes = sum(e['antes'] for e in entidades)
    globales = {
        'antes': antes,
        'despues': sum(e['despues'] for e in entidades),
        'pct_depuracion': _pct(sum(e['tratadas'] for e in entidades), antes),
        'pct_perdida_confirmada': _pct(sum(e['perdida'] for e in entidades), antes),
        'pct_probabilidad_perdida': _pct(sum(e['perdida'] + e['revision'] for e in entidades), antes),
        'casos_pendientes': sum(r['pendientes'] for r in revision),
        'casos_total': sum(r['casos'] for r in revision),
    }
    return {
        'global': globales,
        'entidades': entidades,
        'ingles': ingles,
        'catalogos': catalogos,
        'revision': revision,
        'vinculacion': vinculacion,
    }


def _filas(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchall()
