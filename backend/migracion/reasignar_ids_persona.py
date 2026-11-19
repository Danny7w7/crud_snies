"""Reasigna claves de persona dentro de una transacción de la copia verificada del ERP."""

from dataclasses import dataclass

from psycopg import sql


@dataclass(frozen=True, order=True)
class ReferenciaPersona:
    esquema: str
    tabla: str
    columna: str

    @property
    def nombre(self):
        return f'{self.esquema}.{self.tabla}.{self.columna}'


REFERENCIAS_LOGICAS_PERSONA = (
    ('ubicacion_ubicacion', 'persona_id'),
    ('ubicacion_equivalenciapersonaswa', 'persona_erp_id'),
    ('ubicacion_equivalenciaestudianteswa', 'persona_erp_id'),
    ('ubicacion_asociacionprogramaswa', 'persona_erp_id'),
)

_PERSONA = ReferenciaPersona('public', 'erp_persona', 'id')
_MAPA = ReferenciaPersona('migra', 'mapa', 'erp_id')
_AUDITORIA = ('public', 'auditoria_log_sistema')
_TABLAS_REQUERIDAS = {
    (_PERSONA.esquema, _PERSONA.tabla), _AUDITORIA,
    ('public', 'erp_tipodocumentoidentidad'), ('swa_raw', 'persona'),
    (_MAPA.esquema, _MAPA.tabla), ('migra', 'lote'),
}


def inventariar_referencias(cur):
    """Valida las FK a la tabla de persona y agrega las referencias lógicas conocidas."""
    cur.execute("""
        SELECT ns.nspname, hija.relname, origen.attname,
               cardinality(fk.conkey), cardinality(fk.confkey), destino.attname,
               fk.condeferrable, fk.confupdtype::text, fk.conname
        FROM pg_catalog.pg_constraint fk
        JOIN pg_catalog.pg_class hija ON hija.oid = fk.conrelid
        JOIN pg_catalog.pg_namespace ns ON ns.oid = hija.relnamespace
        LEFT JOIN pg_catalog.pg_attribute origen
            ON origen.attrelid = fk.conrelid AND origen.attnum = fk.conkey[1]
        LEFT JOIN pg_catalog.pg_attribute destino
            ON destino.attrelid = fk.confrelid AND destino.attnum = fk.confkey[1]
        WHERE fk.contype = 'f' AND fk.confrelid = 'public.erp_persona'::regclass
        ORDER BY ns.nspname, hija.relname, fk.conname
    """)
    referencias = set()
    for esquema, tabla, columna, tamaño_origen, tamaño_destino, destino, diferible, accion, nombre in cur.fetchall():
        if (tamaño_origen != 1 or tamaño_destino != 1 or destino != 'id' or columna is None
                or not diferible or accion != 'a'):
            raise ValueError(f'FK incompatible con la reasignación: {esquema}.{tabla}.{nombre}.')
        ref = ReferenciaPersona(esquema, tabla, columna)
        if ref in (_PERSONA, _MAPA) or (esquema, tabla) == _AUDITORIA or esquema == 'swa_raw':
            raise ValueError(f'FK incompatible con la conservación de {ref.nombre}.')
        referencias.add(ref)

    cur.execute("""
        SELECT t.relname, a.attname
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class t ON t.oid = a.attrelid
        JOIN pg_catalog.pg_namespace ns ON ns.oid = t.relnamespace
        WHERE ns.nspname = 'public' AND t.relkind IN ('r', 'p', 'f', 'm', 'v')
          AND a.attnum > 0 AND NOT a.attisdropped
          AND a.attname IN ('persona_id', 'persona_erp_id')
        ORDER BY t.relname, a.attname
    """)
    for tabla, columna in cur.fetchall():
        ref = ReferenciaPersona('public', tabla, columna)
        if (tabla, columna) not in REFERENCIAS_LOGICAS_PERSONA and ref not in referencias:
            raise ValueError(f'Referencia de persona sin correspondencia revisada: {ref.nombre}.')
        referencias.add(ref)
    return tuple(sorted(referencias))


def _validar_columnas_editables(cur, referencias):
    # Valida metadatos bajo los locks. No se desactivan FK ni triggers.
    cur.execute("""
        SELECT c.esquema, c.tabla, c.columna,
               CASE
                   WHEN a.attnum IS NULL OR t.relkind <> 'r' OR EXISTS (
                       SELECT 1 FROM pg_catalog.pg_inherits i
                       WHERE t.oid IN (i.inhparent, i.inhrelid)
                   ) THEN 'relación o columna incompatible'
                   WHEN pg_catalog.row_security_active(t.oid) THEN 'filas ocultas por políticas RLS'
                   WHEN t.oid = 'public.erp_persona'::regclass AND c.columna = 'id' THEN
                       CASE WHEN a.atttypid <> 'bigint'::regtype OR NOT EXISTS (
                           SELECT 1 FROM pg_catalog.pg_constraint pk
                           WHERE pk.conrelid = t.oid AND pk.contype = 'p' AND pk.conkey = ARRAY[a.attnum]
                       ) THEN 'se esperaba la clave primaria bigint de persona' END
                   WHEN EXISTS (
                       SELECT 1 FROM pg_catalog.pg_constraint k
                       WHERE (k.conrelid = t.oid AND k.contype = 'p' AND a.attnum = ANY(k.conkey))
                          OR (k.confrelid = t.oid AND k.contype = 'f' AND a.attnum = ANY(k.confkey))
                   ) THEN 'la referencia también forma parte de una clave de la tabla'
               END AS problema_clave,
               EXISTS (
                   SELECT 1 FROM pg_catalog.pg_constraint fk
                   WHERE fk.conrelid = t.oid AND fk.contype = 'f' AND a.attnum = ANY(fk.conkey)
                     AND fk.confrelid <> 'public.erp_persona'::regclass
               ) AS referencia_a_otra_tabla,
               EXISTS (
                   SELECT 1 FROM pg_catalog.pg_trigger tr
                   WHERE tr.tgrelid = t.oid AND NOT tr.tgisinternal AND tr.tgenabled <> 'D'
                     AND (tr.tgtype & 16) <> 0
                     AND (cardinality(tr.tgattr::smallint[]) = 0 OR a.attnum = ANY(tr.tgattr::smallint[]))
               ) OR EXISTS (
                   SELECT 1 FROM pg_catalog.pg_rewrite r
                   WHERE r.ev_class = t.oid AND r.ev_type = '2' AND r.ev_enabled <> 'D'
               ) OR EXISTS (
                   SELECT 1 FROM pg_catalog.pg_attrdef d
                   JOIN pg_catalog.pg_attribute g ON g.attrelid = d.adrelid AND g.attnum = d.adnum
                   JOIN pg_catalog.pg_depend p ON p.classid = 'pg_catalog.pg_attrdef'::regclass AND p.objid = d.oid
                   WHERE d.adrelid = t.oid AND g.attgenerated <> '' AND p.refclassid = 'pg_catalog.pg_class'::regclass
                     AND p.refobjid = t.oid AND p.refobjsubid = a.attnum
               ) AS actualizacion_indirecta
        FROM unnest(%s::text[], %s::text[], %s::text[]) AS c(esquema, tabla, columna)
        LEFT JOIN pg_catalog.pg_namespace ns ON ns.nspname = c.esquema
        LEFT JOIN pg_catalog.pg_class t ON t.relnamespace = ns.oid AND t.relname = c.tabla
        LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid AND a.attname = c.columna
            AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY c.esquema, c.tabla, c.columna
    """, ([ref.esquema for ref in referencias], [ref.tabla for ref in referencias],
          [ref.columna for ref in referencias]))
    for esquema, tabla, columna, problema_clave, referencia_a_otra_tabla, actualizacion_indirecta in cur.fetchall():
        nombre = f'{esquema}.{tabla}.{columna}'
        if problema_clave:
            raise ValueError(f'{nombre}: {problema_clave}.')
        if referencia_a_otra_tabla:
            raise ValueError(f'{nombre} también referencia otra tabla.')
        if actualizacion_indirecta:
            raise ValueError(f'{nombre} tiene una actualización indirecta sin revisión.')


def bloquear_reasignacion(cur, referencias):
    """Excluye escrituras concurrentes en la transacción de la copia local verificada."""
    cur.execute("SELECT current_setting('transaction_isolation'), current_setting('session_replication_role')")
    if cur.fetchone() != ('read committed', 'origin'):
        raise ValueError('La reasignación requiere READ COMMITTED y la validación normal de restricciones.')
    tablas = _TABLAS_REQUERIDAS | {(ref.esquema, ref.tabla) for ref in referencias}
    relaciones = sql.SQL(', ').join(
        sql.SQL('ONLY {}').format(sql.Identifier(*tabla)) for tabla in sorted(tablas)
    )
    cur.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(relaciones))
    if inventariar_referencias(cur) != referencias:
        raise ValueError('El inventario de referencias a persona cambió al tomar los bloqueos.')
    _validar_columnas_editables(cur, (*referencias, _MAPA, _PERSONA))


def _contar(cur, ref, ids):
    cur.execute(sql.SQL("""
        SELECT {columna}, count(*) FROM ONLY {tabla}
        WHERE {columna} = ANY(%s::bigint[]){filtro}
        GROUP BY {columna}
    """).format(tabla=sql.Identifier(ref.esquema, ref.tabla), columna=sql.Identifier(ref.columna),
                filtro=_filtro_mapa(ref)), (ids,))
    return dict(cur.fetchall())


def _filtro_mapa(ref):
    return sql.SQL(" AND entidad = 'persona'") if ref == _MAPA else sql.SQL('')


def reasignar_ids_persona(cur, cambios: dict[int, int], referencias):
    """Actualiza referencias y PK, y valida el resultado antes de que el llamador confirme.

    El llamador debe haber verificado la copia y tomado los locks con
    bloquear_reasignacion en esta misma transacción. Cualquier excepción se debe propagar.
    """
    if not cambios:
        return {}
    origenes = list(cambios)
    destinos = list(cambios.values())
    ids = origenes + destinos
    if any(type(id_) is not int or not -(2**63) <= id_ < 2**63 for id_ in ids):
        raise ValueError('Todos los IDs de la reasignación deben ser bigint enteros.')
    if len(set(ids)) != len(ids):
        raise ValueError('Cada cambio requiere un origen y un destino distintos, sin solapamientos.')
    if _contar(cur, _PERSONA, ids) != dict.fromkeys(origenes, 1):
        raise ValueError('Algún ID de origen no existe o algún ID de destino ya está ocupado.')

    esperados = {}
    for ref in (*referencias, _MAPA):
        existentes = _contar(cur, ref, ids)
        if existentes.keys() - cambios.keys():
            raise ValueError(f'{ref.nombre} contiene referencias previas a un ID de destino.')
        esperados[ref] = {cambios[anterior]: cantidad for anterior, cantidad in existentes.items()}
    esperados[_PERSONA] = dict.fromkeys(destinos, 1)

    cur.execute('SET CONSTRAINTS ALL DEFERRED')
    res = {}
    for ref, esperado in esperados.items():
        cantidad = sum(esperado.values())
        if cantidad:
            cur.execute(sql.SQL("""
                UPDATE ONLY {tabla} AS t SET {columna} = m.nuevo
                FROM unnest(%s::bigint[], %s::bigint[]) AS m(anterior, nuevo)
                WHERE t.{columna} = m.anterior{filtro}
            """).format(tabla=sql.Identifier(ref.esquema, ref.tabla), columna=sql.Identifier(ref.columna),
                        filtro=_filtro_mapa(ref)), (origenes, destinos))
            if cur.rowcount != cantidad:
                raise ValueError(f'La cantidad de filas actualizadas en {ref.nombre} cambió durante la reasignación.')
        res[ref.nombre] = cantidad

    cur.execute('SET CONSTRAINTS ALL IMMEDIATE')
    for ref, esperado in esperados.items():
        if _contar(cur, ref, ids) != esperado:
            raise ValueError(f'{ref.nombre} conserva referencias antiguas o tiene un recuento de destino inesperado.')
    return res
