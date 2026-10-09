"""Conexiones directas (psycopg) usadas por el proceso de migracion.

- SWA: base origen en produccion. Siempre en modo solo lectura.
- Destino: base local de migracion (docker-compose.yml) que contiene la copia
  del ERP en `public` y los esquemas de trabajo `swa_raw`, `migra` y `revision`.
"""
import os

import psycopg


def conectar_swa():
    conn = psycopg.connect(
        host=os.getenv('DB_PROD_HOST'),
        port=os.getenv('DB_PROD_PORT', '5432'),
        dbname=os.getenv('DB_PROD_NAME'),
        user=os.getenv('DB_PROD_USER'),
        password=os.getenv('DB_PROD_PASSWORD'),
        connect_timeout=15,
    )
    conn.read_only = True
    return conn


def conectar_destino(autocommit=False):
    return psycopg.connect(
        host=os.getenv('MIGRA_DB_HOST', '127.0.0.1'),
        port=os.getenv('MIGRA_DB_PORT', '5434'),
        dbname=os.getenv('MIGRA_DB_NAME', 'erp_migracion'),
        user=os.getenv('MIGRA_DB_USER', 'admin'),
        password=os.getenv('MIGRA_DB_PASSWORD', 'admin'),
        autocommit=autocommit,
    )
