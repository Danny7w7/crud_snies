"""Corte de la migración: prepara la base del ERP de producción con lo migrado de SWA.

Flujo (ventana de mantenimiento, ERP de producción detenido):
  1. Recibe el dump de producción (pg_dump -Fc) tomado al inicio de la ventana.
  2. Lo restaura en el esquema `public` de la copia local (erp_migracion).
  3. Re-depura (aplica las decisiones de revisión) y envía lo LISTA a la copia.
  4. Genera el dump del esquema `public` resultante: es el que se restaura en producción.

Este comando NUNCA se conecta ni escribe en producción: entrega un archivo. La
restauración en producción la hace quien opera la ventana, con el dump original
guardado como reversión.

Uso:
    python manage.py migra_corte --dump-produccion C:/corte/prod_inicio.dump \
                                 --salida C:/corte/prod_con_migracion.dump
"""
import json
import os
import subprocess
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from migracion.carga import enviar
from migracion.conexiones import conectar_destino
from migracion.depuracion import depurar

CONTENEDOR = os.getenv('MIGRA_DB_CONTAINER', 'crud_snies-db_migracion-1')


def _psql_docker(args, entrada=None, salida=None):
    """Ejecuta un binario de PostgreSQL dentro del contenedor de la base de migración."""
    usuario = os.getenv('MIGRA_DB_USER', 'admin')
    base = os.getenv('MIGRA_DB_NAME', 'erp_migracion')
    comando = ['docker', 'exec', '-i', CONTENEDOR, *args[:1], '-U', usuario, '-d', base, *args[1:]]
    resultado = subprocess.run(comando, stdin=entrada, stdout=salida or subprocess.PIPE,
                               stderr=subprocess.PIPE)
    if resultado.returncode != 0:
        raise CommandError(f'{args[0]} falló: {resultado.stderr.decode(errors="replace")[-2000:]}')
    return resultado


class Command(BaseCommand):
    help = 'Prepara el dump del ERP de producción con la migración aplicada (no escribe en producción).'

    def add_arguments(self, parser):
        parser.add_argument('--dump-produccion', required=True,
                            help='Dump (pg_dump -Fc) del ERP de producción tomado al inicio de la ventana.')
        parser.add_argument('--salida', required=True,
                            help='Archivo donde se escribe el dump del esquema public con la migración.')
        parser.add_argument('--si', action='store_true', help='No pedir confirmaciones.')

    def _confirmar(self, mensaje, opciones):
        if opciones['si']:
            return
        respuesta = input(f'{mensaje} Escriba SI para continuar: ').strip()
        if respuesta != 'SI':
            raise CommandError('Corte cancelado por el operador.')

    def _paso(self, titulo):
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n== {titulo}'))
        return time.monotonic()

    def handle(self, *args, **opciones):
        origen = Path(opciones['dump_produccion'])
        salida = Path(opciones['salida'])
        if not origen.is_file():
            raise CommandError(f'No existe el dump de producción: {origen}')
        if salida.exists():
            raise CommandError(f'El archivo de salida ya existe (no se sobrescribe): {salida}')

        with conectar_destino() as conn:
            pendientes = conn.execute(
                'SELECT count(*) FROM revision.caso WHERE vigente AND decision IS NULL').fetchone()[0]
        self.stdout.write(f'Casos de revisión sin decidir: {pendientes} (esa información no viaja).')
        self._confirmar(
            f'Se REEMPLAZA el esquema public de la copia local ({CONTENEDOR}) con {origen.name}.',
            opciones)

        inicio = self._paso('1. Restaurar el dump de producción en la copia local')
        _psql_docker(['psql', '-q', '-c', 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'])
        with conectar_destino(autocommit=True) as conn:
            # La copia anterior ya no existe: sus lotes y su mapa SWA -> ERP dejan de aplicar.
            conn.execute('DELETE FROM migra.mapa')
            conn.execute('DELETE FROM migra.lote')
            conn.execute("SELECT setval('migra.lote_id_seq', 1, false)")
        with origen.open('rb') as archivo:
            _psql_docker(['pg_restore', '--no-owner', '--no-privileges'], entrada=archivo)
        self.stdout.write(f'   restaurado en {time.monotonic() - inicio:.0f}s')

        inicio = self._paso('2. Depurar con las decisiones vigentes')
        metricas = depurar(log=lambda m: self.stdout.write(f'   {m}'))
        self.stdout.write(f'   {json.dumps(metricas["global"], ensure_ascii=False)}')

        inicio = self._paso('3. Enviar lo LISTA a la copia')
        resultado = enviar(log=lambda m: self.stdout.write(f'   {m}'))
        self.stdout.write(f'   lote {resultado["lote"]}: {json.dumps(resultado["resumen"], ensure_ascii=False)}')
        self.stdout.write(f'   enviado en {time.monotonic() - inicio:.0f}s')

        self._confirmar(
            'Revise ahora la copia (verificar_migracion.py y pantallas del ERP apuntando a la copia).', opciones)

        inicio = self._paso('4. Generar el dump para producción (solo esquema public)')
        salida.parent.mkdir(parents=True, exist_ok=True)
        with salida.open('wb') as archivo:
            _psql_docker(['pg_dump', '--no-owner', '--no-privileges', '-Fc', '-n', 'public'], salida=archivo)
        self.stdout.write(f'   {salida} ({salida.stat().st_size / 1_048_576:.1f} MB) en {time.monotonic() - inicio:.0f}s')

        self.stdout.write(self.style.SUCCESS(
            '\nListo. Para aplicarlo en producción (ERP detenido):\n'
            '  pg_restore --clean --if-exists --no-owner -d <base_produccion> ' + str(salida) + '\n'
            'Reversión: restaurar el dump original ' + str(origen) + '\n'
            'Después: python manage.py crear_usuarios_estudiantes --enviar --reporte <csv> (en el ERP).'
        ))
