"""Identidad y exclusión mutua del destino local de las escrituras SWA → ERP."""
import json
import os
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections, transaction
from psycopg.types.json import set_json_loads


ALIAS = 'default'
LLAVE_MIGRACION = 72620262
HOSTS_LOCALES = ('127.0.0.1', '::1')


def parametros_destino():
    configuracion = settings.DATABASES[ALIAS]
    host = configuracion.get('HOST')
    nombre = configuracion.get('NAME')
    esperado = getattr(settings, 'MIGRADOR_DESTINO_NOMBRE', '') or os.getenv('MIGRADOR_DESTINO_NOMBRE')
    if (getattr(settings, 'MIGRADOR_PRUEBAS_AISLADAS', False)
            and str(configuracion.get('PORT')) == '55439' and nombre == 'test_codigos_persona'):
        esperado = nombre
    if configuracion['ENGINE'] != 'django.db.backends.postgresql' or host not in HOSTS_LOCALES:
        raise ImproperlyConfigured('El destino default debe ser PostgreSQL en una dirección loopback explícita.')
    if not esperado or nombre != esperado:
        raise ImproperlyConfigured('MIGRADOR_DESTINO_NOMBRE debe identificar la copia local configurada en default.')
    origen = settings.DATABASES.get('produccion', {})
    if not getattr(settings, 'MIGRADOR_SWA_LOCAL', False) and nombre == origen.get('NAME'):
        if (origen.get('HOST'), str(origen.get('PORT') or '5432')) in (
            (host, str(configuracion.get('PORT') or '5432')),
            ('localhost', str(configuracion.get('PORT') or '5432')),
        ):
            raise ImproperlyConfigured('default y la fuente SWA apuntan a la misma base.')
    return {
        'host': host,
        'hostaddr': host,
        'port': str(configuracion.get('PORT') or '5432'),
        'dbname': nombre,
        'user': configuracion.get('USER'),
        'password': configuracion.get('PASSWORD'),
        'connect_timeout': 15,
    }


def verificar_cursor_destino(cursor, parametros):
    cursor.execute('SELECT current_database(), host(inet_server_addr()), inet_server_port()')
    nombre, host, puerto = cursor.fetchone()
    if (nombre, host, str(puerto)) != (parametros['dbname'], parametros['host'], parametros['port']):
        raise ImproperlyConfigured('La identidad de la conexión no coincide con el destino local configurado.')


def verificar_destino():
    conexion = connections[ALIAS]
    if conexion.vendor == 'sqlite' and getattr(settings, 'MIGRADOR_PRUEBAS_AISLADAS', False):
        return
    parametros = parametros_destino()
    with conexion.cursor() as cursor:
        verificar_cursor_destino(cursor, parametros)


@contextmanager
def cursor_migracion(conexion):
    with conexion.cursor() as wrapper:
        cursor = wrapper.cursor
        # Django difiere la deserialización de JSON a los campos ORM.
        set_json_loads(json.loads, cursor)
        yield cursor


@contextmanager
def bloqueo_migracion():
    verificar_destino()
    with transaction.atomic(using=ALIAS):
        conexion = connections[ALIAS]
        if conexion.vendor == 'postgresql':
            with conexion.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', (LLAVE_MIGRACION,))
        yield conexion
