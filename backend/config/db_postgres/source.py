"""Acceso común al origen SWA para Django y los procesos con psycopg."""

import ipaddress
import os
import re
import socket

import psycopg
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from config.destino_migracion import parametros_destino, verificar_cursor_destino


ALIAS = 'produccion'
SOLO_LECTURA = 'SWA solo admite consultas SELECT sin efectos secundarios.'
_OPCIONES = ('-c default_transaction_read_only=on -c statement_timeout=300000 '
             '-c standard_conforming_strings=on -c extra_float_digits=3')
_COMPROBACIONES = (
    "SELECT current_setting('transaction_read_only')",
    "SELECT current_setting('default_transaction_read_only')",
)
_IDENTIDAD_ORIGEN = '''
    SELECT pg_catalog.current_database(), pg_catalog.host(pg_catalog.inet_server_addr()),
           pg_catalog.inet_server_port(), current_user, session_user,
           EXISTS (
               SELECT 1 FROM pg_catalog.pg_roles rol
               WHERE (rol.rolsuper OR rol.rolcreaterole OR rol.rolcreatedb OR rol.rolreplication)
                 AND pg_catalog.pg_has_role(rol.oid, 'MEMBER')
           ),
           EXISTS (
               SELECT 1 FROM pg_catalog.pg_class c
               JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND CASE
                   WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
                       pg_catalog.pg_has_role(c.relowner, 'MEMBER')
                       OR pg_catalog.has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                       OR pg_catalog.has_any_column_privilege(c.oid, 'INSERT,UPDATE,REFERENCES')
                   WHEN c.relkind = 'S' THEN pg_catalog.has_sequence_privilege(c.oid, 'USAGE,UPDATE')
                   ELSE false
               END
           )
'''
_TABLAS_ORIGEN = '''
    SELECT c.relname, c.relkind, c.relpersistence, c.relam, am.amname,
           c.relhasrules, pg_catalog.has_table_privilege(c.oid, 'SELECT'),
           EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent = c.oid)
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_catalog.pg_am am ON am.oid = c.relam
    WHERE n.nspname = 'public' AND c.relname = ANY(%s)
'''
_TOKENS = re.compile(
    r'(?P<ignorar>[ \t\r\n\f\v]+|--[^\r\n]*)|(?P<comentario>/\*(?:[^*]|\*(?!/))*\*/)'
    r'|(?P<escapado>[Ee]\'|[Uu]&[\'"])'
    r'|(?P<literal>\'(?:[^\']|\'\')*\')|(?P<ident>"(?:[^"]|"")*")'
    r'|(?P<parametro>%\([a-zA-Z_][a-zA-Z_0-9]*\)[sbt]|%[sbt]|%%)'
    r'|(?P<palabra>[a-zA-Z_][a-zA-Z_0-9$]*)|(?P<numero>[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?)'
    r'|(?P<simbolo>::|[(),.;\[\]+*/^%|<>=~!-])|(?P<desconocido>.)',
)
_ORDENES = frozenset('''
    alter analyze begin call checkpoint close cluster comment commit copy create deallocate
    declare delete discard do drop execute explain fetch grant import insert into listen
    load lock merge move notify operator prepare reassign refresh reindex release reset
    revoke rollback savepoint security set share show start truncate unlisten update vacuum
'''.split())
_FUNCIONES = frozenset('''
    abs bool_or coalesce concat_ws count current_database current_setting format_type
    generate_subscripts greatest has_any_column_privilege has_sequence_privilege
    has_table_privilege host inet_server_addr inet_server_port left length lower
    max min mode nullif pg_has_role regexp_replace round split_part string_agg
    substring sum to_char trim unnest upper
'''.split())
_SINTAXIS = frozenset('''
    and any as between by distinct else exists filter from group having in join lateral
    not on or over select then when where within
'''.split())
_TIPOS = frozenset('bool date int int2 int4 int8 integer numeric regclass text varchar'.split())
_TIPOS_SQL = frozenset(('palabra', tipo) for tipo in _TIPOS)
_LITERAL_DESPUES_DE = _SINTAXIS | _TIPOS | {'ilike', 'is', 'like', 'of'}


def esquema_swa():
    return 'swa_raw' if getattr(settings, 'MIGRADOR_SWA_LOCAL', False) else 'public'


def parametros_swa(zona='UTC'):
    """Construye parámetros de inicio con un destino explícito y sin servicios libpq."""
    if os.getenv('PGSERVICE') or os.getenv('PGHOSTADDR'):
        raise ImproperlyConfigured('Quite PGSERVICE y PGHOSTADDR para identificar el servidor SWA.')
    esquema = esquema_swa()
    if esquema == 'swa_raw':
        parametros = parametros_destino()
    else:
        config = settings.DATABASES[ALIAS]
        host, nombre, usuario = (config.get(k) for k in ('HOST', 'NAME', 'USER'))
        puerto = str(config.get('PORT') or '5432')
        if not (host and nombre and usuario):
            raise ImproperlyConfigured('Indique DB_PROD_HOST, DB_PROD_NAME y DB_PROD_USER para leer el SWA.')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,63}', nombre) or not re.fullmatch(r'[0-9]{1,5}', puerto):
            raise ImproperlyConfigured('DB_PROD_NAME y DB_PROD_PORT deben identificar una base SWA concreta.')
        if not 1 <= int(puerto) <= 65535:
            raise ImproperlyConfigured('DB_PROD_PORT está fuera del rango válido.')
        parametros = {'host': host, 'port': str(int(puerto)), 'dbname': nombre,
                      'user': usuario, 'password': config.get('PASSWORD'), 'connect_timeout': 15}
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', host):
                raise ImproperlyConfigured('DB_PROD_HOST debe ser una dirección IP o un nombre DNS único.') from None
        else:
            parametros['hostaddr'] = str(ip)
    if not re.fullmatch(r'[A-Za-z0-9_+/-]+', zona):
        raise ImproperlyConfigured('Zona horaria inválida para la conexión de origen.')
    parametros.update(autocommit=True, prepare_threshold=None, client_encoding='UTF8', target_session_attrs='any',
                      options=f'{_OPCIONES} -c TimeZone={zona} -c search_path=pg_catalog,{esquema}')
    return parametros


def fijar_direccion_origen(parametros):
    """Identifica un único endpoint TCP antes de abrir la conexión de extracción."""
    if 'hostaddr' not in parametros:
        try:
            resoluciones = socket.getaddrinfo(
                parametros['host'], parametros['port'], type=socket.SOCK_STREAM,
            )
            direcciones = {str(ipaddress.ip_address(r[4][0])) for r in resoluciones}
        except (OSError, ValueError):
            raise ImproperlyConfigured('No se pudo resolver la dirección del origen SWA.') from None
        if len(direcciones) != 1:
            raise ImproperlyConfigured('El origen SWA debe resolver a una única dirección para la extracción.')
        return {**parametros, 'hostaddr': direcciones.pop()}
    return parametros


def verificar_origen_copia(conexion, parametros, tablas):
    """Confirma identidad, permisos y relaciones mediante SELECT sobre la conexión protegida."""
    with conexion.cursor() as cursor:
        cursor.execute(_IDENTIDAD_ORIGEN)
        nombre, direccion, puerto, rol, rol_sesion, privilegios_admin, privilegios_escritura = cursor.fetchone()
        esperado = (parametros['dbname'], parametros['hostaddr'], parametros['port'], parametros['user'])
        if (nombre, direccion, str(puerto), rol) != esperado or rol != rol_sesion:
            raise PermissionError('La identidad del servidor o del rol no coincide con el origen SWA configurado.')
        if privilegios_admin or privilegios_escritura:
            raise PermissionError('El rol SWA tiene privilegios administrativos o de escritura en el esquema público.')
        cursor.execute(_TABLAS_ORIGEN, (list(tablas),))
        elegibles = {tabla for tabla, clase, persistencia, metodo, nombre_metodo, reglas, lectura, hijas
                    in cursor.fetchall() if clase == 'r' and persistencia in ('p', 'u')
                    and (metodo == 0 or nombre_metodo == 'heap') and lectura and not reglas and not hijas}
        faltantes = set(tablas) - elegibles
        if faltantes:
            raise PermissionError('Relaciones SWA sin lectura verificada como tablas ordinarias: '
                                  + ', '.join(sorted(faltantes)))


def _lista_de_columnas_cte(tokens, inicio):
    """Reconoce la declaración WITH nombre(columna, ...) AS (...)."""
    for indice, (tipo, valor) in enumerate(tokens[inicio + 2:], start=inicio + 2):
        if valor == ')':
            return tokens[indice + 1:indice + 3] == [('palabra', 'as'), ('simbolo', '(')]
        if tipo not in ('palabra', 'ident') and valor != ',':
            break
    return False


def validar_select(consulta, conexion=None):
    """Limita nuestros cursores a un SELECT y a las funciones que usa el proyecto."""
    if isinstance(consulta, psycopg.sql.Composable):
        consulta = consulta.as_string(conexion)
    if not isinstance(consulta, str) or '\x00' in consulta:
        raise PermissionError(SOLO_LECTURA)
    tokens = []
    for coincidencia in _TOKENS.finditer(consulta):
        tipo, valor = coincidencia.lastgroup, coincidencia[0]
        if tipo == 'comentario' and '/*' in valor[2:-2]:
            raise PermissionError(SOLO_LECTURA)
        if tipo not in ('comentario', 'ignorar'):
            tokens.append((tipo, valor.lower() if tipo == 'palabra' else valor))
    if tokens and tokens[-1] == ('simbolo', ';'):
        tokens.pop()
    if not tokens or tokens[0] not in (('palabra', 'select'), ('palabra', 'with')):
        raise PermissionError(SOLO_LECTURA)
    declaracion_cte, profundidad = tokens[0][1] == 'with', 0
    for i, (tipo, valor) in enumerate(tokens):
        if tipo in ('escapado', 'desconocido') or tipo == 'palabra' and valor in _ORDENES or valor == ';':
            raise PermissionError(SOLO_LECTURA)
        if valor == '(':
            profundidad += 1
        elif valor == ')':
            profundidad -= 1
        elif tipo == 'palabra' and valor == 'select' and profundidad == 0:
            declaracion_cte = False
        siguiente = tokens[i + 1] if i + 1 < len(tokens) else (None, None)
        calificado = i and tokens[i - 1] == ('simbolo', '.')
        if valor == '::' and (siguiente not in _TIPOS_SQL or tokens[i + 2:i + 3] == [('simbolo', '.')]):
            raise PermissionError(SOLO_LECTURA)
        if tipo not in ('palabra', 'ident'):
            continue
        if siguiente[0] == 'literal' and (tipo != 'palabra' or calificado or valor not in _LITERAL_DESPUES_DE):
            raise PermissionError(SOLO_LECTURA)
        if siguiente != ('simbolo', '('):
            continue
        if calificado and (i < 2 or tokens[i - 2] not in (('palabra', 'pg_catalog'), ('ident', '"pg_catalog"'))):
            raise PermissionError(SOLO_LECTURA)
        nombre = valor if tipo == 'palabra' else valor[1:-1].replace('""', '"')
        if tipo == 'palabra' and not calificado and nombre in _SINTAXIS:
            continue
        if declaracion_cte and profundidad == 0 and i and tokens[i - 1][1] in ('with', ','):
            if _lista_de_columnas_cte(tokens, i):
                continue
        if nombre not in _FUNCIONES:
            raise PermissionError(SOLO_LECTURA)
    if declaracion_cte:
        raise PermissionError(SOLO_LECTURA)


class CursorSwa(psycopg.ClientCursor):
    def execute(self, query, params=None, **kwargs):
        validar_select(query, self.connection)
        return super().execute(query, params, **kwargs)

    def stream(self, query, params=None, **kwargs):
        validar_select(query, self.connection)
        return super().stream(query, params, **kwargs)

    def copy(self, *args, **kwargs):
        raise PermissionError(SOLO_LECTURA)

    def executemany(self, *args, **kwargs):
        raise PermissionError(SOLO_LECTURA)


class ConexionSwa(psycopg.Connection):
    def cursor(self, name='', **kwargs):
        if name:
            raise PermissionError(SOLO_LECTURA)
        return super().cursor(**kwargs)

    def _set_autocommit(self, value):
        if not value:
            raise PermissionError(SOLO_LECTURA)
        super()._set_autocommit(value)

    def transaction(self, *args, **kwargs):
        raise PermissionError(SOLO_LECTURA)

    def commit(self):
        raise PermissionError(SOLO_LECTURA)

    def rollback(self):
        raise PermissionError(SOLO_LECTURA)

    def __exit__(self, *exc):
        self.close()


def abrir_swa(parametros):
    conexion = ConexionSwa.connect(cursor_factory=CursorSwa, **parametros)
    try:
        if not conexion.autocommit:
            raise PermissionError(SOLO_LECTURA)
        if (conexion.info.host, str(conexion.info.port), conexion.info.dbname) != (
                parametros['host'], parametros['port'], parametros['dbname']):
            raise ImproperlyConfigured('La conexión no coincide con el origen SWA configurado.')
        with conexion.cursor() as cursor:
            for consulta in _COMPROBACIONES:
                cursor.execute(consulta)
                if cursor.fetchone() != ('on',):
                    raise PermissionError(SOLO_LECTURA)
            if esquema_swa() == 'swa_raw':
                verificar_cursor_destino(cursor, parametros)
    except BaseException:
        conexion.close()
        raise
    return conexion


def estados_generales_swa():
    """Lee el estado general si la columna está presente en el esquema de origen."""
    with connections[ALIAS].cursor() as cursor:
        cursor.execute('''
            SELECT a.attname
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = 'persona' AND a.attname = 'estado_general'
              AND a.attnum > 0 AND NOT a.attisdropped
        ''', (esquema_swa(),))
        if cursor.fetchone() is None:
            return {}
        cursor.execute('SELECT id, estado_general FROM persona WHERE estado_general IS NOT NULL')
        return dict(cursor.fetchall())
