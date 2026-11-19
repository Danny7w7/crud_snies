"""Prueba PostgreSQL en una instancia temporal propia, nunca en las bases reales."""

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg


def main():
    raiz = Path(__file__).resolve().parents[3]
    binarios = Path(r'C:\Program Files\PostgreSQL\16\bin')
    for nombre in ('initdb.exe', 'pg_ctl.exe'):
        if not (binarios / nombre).is_file():
            raise RuntimeError(f'No se encontro PostgreSQL 16: {binarios / nombre}')
    cache = (raiz / '.cache').resolve()
    cache.mkdir(exist_ok=True)
    datos = Path(tempfile.mkdtemp(prefix='pg-codigos-', dir=cache)).resolve()
    datos.relative_to(cache)
    cache.relative_to(raiz)
    oculto = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    registro_control = cache / f'{datos.name}.control.log'

    def ejecutar(argumentos):
        # En Windows, el servidor puede heredar un PIPE de pg_ctl y mantenerlo
        # abierto incluso cuando pg_ctl termina. Un archivo evita ese bloqueo.
        with registro_control.open('a', encoding='utf-8') as registro:
            resultado = subprocess.run(
                argumentos, creationflags=oculto, stdout=registro,
                stderr=subprocess.STDOUT, timeout=60,
            )
        if resultado.returncode:
            raise RuntimeError(registro_control.read_text(encoding='utf-8', errors='replace')[-4000:])

    try:
        # Reservar el puerto durante initdb; si esta ocupado, no tocar el servidor existente.
        with socket.socket() as reserva:
            reserva.bind(('127.0.0.1', 55439))
            ejecutar([str(binarios / 'initdb.exe'), '-D', str(datos), '-U', 'crud_pruebas',
                      '-A', 'trust', '--no-locale', '-E', 'UTF8'])
        ejecutar([str(binarios / 'pg_ctl.exe'), '-D', str(datos), '-l', str(datos / 'pruebas.log'),
                  '-o', '-h 127.0.0.1 -p 55439 -F', '-w', 'start'])
        with psycopg.connect(host='127.0.0.1', port=55439, user='crud_pruebas',
                             dbname='postgres', connect_timeout=5) as db:
            servidor = Path(db.execute('SHOW data_directory').fetchone()[0]).resolve()
            if servidor != datos:
                raise RuntimeError('La instancia no pertenece a estas pruebas; se aborta.')
        entorno = {**os.environ, 'MIGRADOR_TEST_POSTGRES': '1'}
        registro_pruebas = cache / f'{datos.name}.tests.log'
        with registro_pruebas.open('w', encoding='utf-8') as registro:
            pruebas = subprocess.run(
                [sys.executable, str(raiz / 'backend/manage.py'), 'test', 'api',
                 '--settings=config.test_settings', '--verbosity', '1', '--noinput'],
                cwd=raiz, env=entorno, creationflags=oculto, stdout=registro,
                stderr=subprocess.STDOUT, timeout=120,
            )
        print(registro_pruebas.read_text(encoding='utf-8', errors='replace'), flush=True)
        return pruebas.returncode
    finally:
        # Tambien limpiar si pg_ctl agota el tiempo despues de arrancar.
        # El directorio fue creado por este proceso dentro del workspace.
        if (datos / 'postmaster.pid').is_file():
            ejecutar([str(binarios / 'pg_ctl.exe'), '-D', str(datos), '-m', 'fast', '-w', 'stop'])
            print('PostgreSQL temporal detenido. Ninguna base configurada del proyecto fue utilizada.')


if __name__ == '__main__':
    raise SystemExit(main())
