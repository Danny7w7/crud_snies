"""Copia cruda de SWA al esquema `swa_raw` de la base de migracion.

Lee SWA con SELECT en autocommit y carga las filas por lotes en el destino
local. `persona` se filtra a quienes tienen algún código de estudiante.
"""
import json

from psycopg import capabilities, sql

from config.db_postgres.source import esquema_swa

from .conexiones import conectar_destino, conectar_swa

COLUMNAS_EXCLUIDAS = {
    'access_token', 'api_key', 'clave', 'contrasena', 'contraseña', 'passwd', 'password',
    'pwd', 'secret', 'token',
}
FORMULARIO_COLUMNAS = {
    'codigo', 'codigo_estudiante', 'periodo', 'direccion', 'celular', 'email',
    'fecha_nacimiento', 'fecha_expedicion', 'documento_expedido', 'lugar_nacimiento', 'estrato',
}

# tabla -> filtro WHERE opcional
TABLAS_ACADEMICAS = {
    'persona': 'id IN (SELECT persona FROM ONLY public.estudiante)',
    'estudiante': None,
    'formulario': 'codigo_estudiante IN (SELECT codigo FROM ONLY public.estudiante)',
    'historico': None,
    'registro': None,
    'calificacion': None,
    'cambio_nota': None,
    'grupo': None,
    'grado': None,
    'retiro': None,
    'reintegro': None,
    'programa': None,
    'pensum': None,
    'asignatura': None,
    'pensum_asignatura': None,
    'prerequisito': None,
    'correquisito': None,
    'equivalencia': None,
    'periodo': None,
    'tipo_identificacion': None,
    'tipo_nota': None,
    'grupo_tipo_nota': None,
    'tipo_retiro': None,
    'tipo_opt_grado': None,
    'opt_grado': None,
    'nivel_estudio': None,
    'estado_civil': None,
    'ciudad': None,
    'departamento': None,
    'pais': None,
    'sede': None,
    'facultad': None,
    'area': None,
}

# Tablas financieras: solo se usan para el reporte de anomalias (pagos vs matricula/notas).
TABLAS_FINANCIERAS = {
    'recibo_swa': None,
    'pago_recibo': None,
    'credito': None,
    'cuota': None,
    'concepto_cambio': None,
    'cambio_recibo': None,
    'novedad_descuento': None,
    'novedad_devolucion': None,
    'auxilio': None,
    'grupo_auxilio': None,
    'tipo_cuota': None,
}
TABLAS_ACADEMICAS.update(TABLAS_FINANCIERAS)


def _columnas(cur, tabla):
    cur.execute("""
        SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), n.nspname
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
        JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
        WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """, (f'public.{tabla}',))
    columnas = [(nombre, tipo, esquema) for nombre, tipo, esquema in cur.fetchall()
                if not any(nombre.lower() == c or nombre.lower().startswith(c + '_') for c in COLUMNAS_EXCLUIDAS)
                and (tabla != 'formulario' or nombre in FORMULARIO_COLUMNAS)]
    if any(esquema != 'pg_catalog' for _, _, esquema in columnas):
        raise PermissionError(f'{tabla} tiene columnas seleccionadas de tipos propios del SWA.')
    return [(nombre, tipo) for nombre, tipo, _ in columnas]


def _llave_primaria(cur, tabla):
    cur.execute("""
        SELECT a.attname
        FROM pg_index i
        CROSS JOIN generate_subscripts(i.indkey, 1) AS pos
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[pos]
        WHERE i.indrelid = %s::regclass AND i.indisprimary
        ORDER BY pos
    """, (f'public.{tabla}',))
    return [r[0] for r in cur.fetchall()]


def _filtro_tabla(tabla, columnas, periodo_hasta):
    filtro = TABLAS_ACADEMICAS[tabla]
    if periodo_hasta is None:
        return filtro, None
    partes, parametros = [], []
    if filtro:
        partes.append(filtro[:-1] + ' WHERE periodo_ingreso <= %s OR periodo_ingreso IS NULL)')
        parametros.append(periodo_hasta)
    campo = {'estudiante': 'periodo_ingreso', 'periodo': 'codigo'}.get(tabla, 'periodo')
    if campo in dict(columnas):
        partes.append(f'({campo} <= %s OR {campo} IS NULL)')
        parametros.append(periodo_hasta)
    return ' AND '.join(partes), parametros or None


def copiar_swa(tablas=None, log=print, periodo_hasta=None):
    if esquema_swa() == 'swa_raw':
        raise ValueError('La copia requiere el origen SWA configurado y un destino local separado.')
    tablas = list(dict.fromkeys(tablas or TABLAS_ACADEMICAS))
    desconocidas = set(tablas) - TABLAS_ACADEMICAS.keys()
    if desconocidas:
        raise ValueError('Tablas fuera del alcance de la copia: ' + ', '.join(sorted(desconocidas)))
    tamano_lote = 1000 if capabilities.has_stream_chunked() else 1
    tablas_origen = sorted(set(tablas) | ({'estudiante'} if {'persona', 'formulario'} & set(tablas) else set()))
    with (conectar_destino() as dst, conectar_swa(tablas_copia=tablas_origen) as swa,
          dst.cursor() as dcur, swa.cursor() as scur):
        if (swa.info.hostaddr, swa.info.port, swa.info.dbname) == (dst.info.hostaddr, dst.info.port, dst.info.dbname):
            raise PermissionError('La fuente SWA y el destino de la copia deben ser bases distintas.')
        columnas = {tabla: _columnas(scur, tabla) for tabla in tablas_origen}
        if any(not cols for cols in columnas.values()):
            raise ValueError('Una tabla SWA requerida carece de columnas autorizadas para la copia.')
        llaves = {tabla: _llave_primaria(scur, tabla) for tabla in tablas}
        dcur.execute('CREATE SCHEMA IF NOT EXISTS swa_raw')
        dcur.execute('CREATE SCHEMA IF NOT EXISTS migra')
        resumen = {}
        for tabla in tablas:
            cols = columnas[tabla]
            filtro, parametros = _filtro_tabla(tabla, cols, periodo_hasta)
            pk = llaves[tabla]
            definicion = [sql.SQL('{} {}').format(sql.Identifier(n), sql.SQL(t)) for n, t in cols]
            if pk and set(pk).issubset(dict(cols)):
                definicion.append(sql.SQL('PRIMARY KEY ({})').format(sql.SQL(', ').join(map(sql.Identifier, pk))))
            destino_tabla = sql.Identifier('swa_raw', tabla)
            dcur.execute(sql.SQL('DROP TABLE IF EXISTS {}').format(destino_tabla))
            dcur.execute(sql.SQL('CREATE TABLE {} ({})').format(destino_tabla, sql.SQL(', ').join(definicion)))
            lista = sql.SQL(', ').join(sql.Identifier(n) for n, _ in cols)
            # El formato de texto de PostgreSQL conserva valores que psycopg no
            # adaptaría directamente para COPY, como json, arrays o tipos propios.
            columnas_texto = sql.SQL(', ').join(sql.SQL('{}::text').format(sql.Identifier(n)) for n, _ in cols)
            consulta = sql.SQL('SELECT {} FROM ONLY {}').format(columnas_texto, sql.Identifier('public', tabla))
            if filtro:
                consulta += sql.SQL(' WHERE ') + sql.SQL(filtro)
            total = 0
            with dcur.copy(sql.SQL('COPY {} ({}) FROM STDIN').format(destino_tabla, lista)) as destino:
                for fila in scur.stream(consulta, parametros, size=tamano_lote):
                    destino.write_row(fila)
                    total += 1
            resumen[tabla] = total
            log(f'{tabla}: {resumen[tabla]}')
        dcur.execute("""
            CREATE TABLE IF NOT EXISTS migra.copia_swa_log (
                id bigserial PRIMARY KEY,
                fecha timestamptz NOT NULL DEFAULT now(),
                tablas jsonb NOT NULL
            )
        """)
        dcur.execute('INSERT INTO migra.copia_swa_log (tablas) VALUES (%s::jsonb)',
                     (json.dumps(resumen),))
    return resumen
