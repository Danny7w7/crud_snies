"""Copia cruda de SWA al esquema `swa_raw` de la base de migracion.

SWA es produccion: solo se ejecutan SELECT y COPY ... TO STDOUT sobre una
conexion read_only. Se copian unicamente tablas academicas; `persona` se
filtra a quienes tienen algun codigo de estudiante (sin terceros/proveedores).
Replica estructura (tipos exactos y llave primaria) y datos con COPY en
streaming. Las columnas de contrasenas no se copian.
"""
from .conexiones import conectar_destino, conectar_swa

COLUMNAS_EXCLUIDAS = {'password', 'password_md5'}

# tabla -> filtro WHERE opcional
TABLAS_ACADEMICAS = {
    'persona': 'id IN (SELECT persona FROM public.estudiante)',
    'estudiante': None,
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


def _columnas(cur, tabla):
    cur.execute("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """, (f'public.{tabla}',))
    return [(n, t) for n, t in cur.fetchall() if n not in COLUMNAS_EXCLUIDAS]


def _llave_primaria(cur, tabla):
    cur.execute("""
        SELECT a.attname
        FROM pg_index i
        CROSS JOIN generate_subscripts(i.indkey, 1) AS k(pos)
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[k.pos]
        WHERE i.indrelid = %s::regclass AND i.indisprimary
        ORDER BY k.pos
    """, (f'public.{tabla}',))
    return [r[0] for r in cur.fetchall()]


def copiar_swa(tablas=None, log=print):
    with conectar_swa() as swa, conectar_destino() as dst:
        scur, dcur = swa.cursor(), dst.cursor()
        dcur.execute('CREATE SCHEMA IF NOT EXISTS swa_raw')
        resumen = {}
        for tabla in tablas or list(TABLAS_ACADEMICAS):
            filtro = TABLAS_ACADEMICAS.get(tabla)
            cols = _columnas(scur, tabla)
            pk = [c for c in _llave_primaria(scur, tabla) if c in dict(cols)]
            definicion = ', '.join(f'"{n}" {t}' for n, t in cols)
            if pk:
                definicion += ', PRIMARY KEY (' + ', '.join(f'"{c}"' for c in pk) + ')'
            dcur.execute(f'DROP TABLE IF EXISTS swa_raw."{tabla}"')
            dcur.execute(f'CREATE TABLE swa_raw."{tabla}" ({definicion})')
            lista = ', '.join(f'"{n}"' for n, _ in cols)
            where = f' WHERE {filtro}' if filtro else ''
            with scur.copy(f'COPY (SELECT {lista} FROM public."{tabla}"{where}) TO STDOUT') as origen, \
                    dcur.copy(f'COPY swa_raw."{tabla}" ({lista}) FROM STDIN') as destino:
                for bloque in origen:
                    destino.write(bloque)
            dcur.execute(f'SELECT count(*) FROM swa_raw."{tabla}"')
            resumen[tabla] = dcur.fetchone()[0]
            log(f'{tabla}: {resumen[tabla]}')
        dcur.execute("""
            CREATE TABLE IF NOT EXISTS migra.copia_swa_log (
                id bigserial PRIMARY KEY,
                fecha timestamptz NOT NULL DEFAULT now(),
                tablas jsonb NOT NULL
            )
        """)
        dcur.execute('INSERT INTO migra.copia_swa_log (tablas) VALUES (%s::jsonb)',
                     (__import__('json').dumps(resumen),))
        dst.commit()
    return resumen
