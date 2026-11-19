"""Conexiones directas (psycopg) usadas por el proceso de migracion.

- SWA: base origen en produccion. Siempre en modo solo lectura.
- Destino: copia local del ERP configurada en `default`.
"""
import psycopg

from config.db_postgres.source import (abrir_swa, esquema_swa, fijar_direccion_origen,
                                       parametros_swa, verificar_origen_copia)
from config.destino_migracion import parametros_destino, verificar_cursor_destino


def conectar_swa(tablas_copia=None):
    parametros = parametros_swa()
    if tablas_copia is None:
        return abrir_swa(parametros)
    if esquema_swa() != 'public':
        raise ValueError('La extracción requiere una conexión a la fuente SWA.')
    parametros = fijar_direccion_origen(parametros)
    conexion = abrir_swa(parametros)
    try:
        verificar_origen_copia(conexion, parametros, tablas_copia)
    except BaseException:
        conexion.close()
        raise
    return conexion


def conectar_destino(autocommit=False):
    parametros = parametros_destino()
    if not parametros.get('password'):
        parametros.pop('password', None)
    conexion = psycopg.connect(**parametros, autocommit=True, options='', target_session_attrs='any')
    try:
        with conexion.cursor() as cursor:
            verificar_cursor_destino(cursor, parametros)
        conexion.autocommit = autocommit
    except BaseException:
        conexion.close()
        raise
    return conexion
