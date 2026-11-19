"""Restaura una copia nueva del ERP o exporta la copia local ya conciliada."""
import os
import shutil
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from config.destino_migracion import parametros_destino
from migracion.conexiones import conectar_destino


def _postgres(programa, argumentos, parametros):
    binario = shutil.which(programa)
    if binario is None and os.name == 'nt':
        instalado = Path('C:/Program Files/PostgreSQL/16/bin') / f'{programa}.exe'
        binario = str(instalado) if instalado.is_file() else None
    if binario is None:
        raise CommandError(f'Falta el cliente PostgreSQL {programa}.')
    entorno = {
        clave: valor for clave, valor in os.environ.items()
        if not clave.startswith('PG') or clave == 'PGPASSFILE'
    }
    entorno['PGHOSTADDR'] = parametros['hostaddr']
    if parametros['password']:
        entorno['PGPASSWORD'] = parametros['password']
    destino = ['--host', parametros['host'], '--port', parametros['port'],
               '--username', parametros['user'], '--dbname', parametros['dbname'], '--no-password']
    resultado = subprocess.run([binario, *destino, *argumentos], env=entorno, capture_output=True)
    if resultado.returncode:
        raise CommandError(f'{programa} falló sobre la copia local. Verifica el archivo y los permisos.')


class Command(BaseCommand):
    help = 'Restaura un respaldo en un destino local vacío o exporta el destino conciliado.'

    def add_arguments(self, parser):
        operacion = parser.add_mutually_exclusive_group(required=True)
        operacion.add_argument('--dump-produccion', type=Path, help='Archivo local PostgreSQL CUSTOM del ERP.')
        operacion.add_argument('--salida', type=Path, help='Archivo nuevo para respaldar el ERP local.')
        parser.add_argument('--si', action='store_true', help='Confirmar la operación local.')

    def handle(self, *args, **opciones):
        parametros = parametros_destino()
        origen, salida = opciones['dump_produccion'], opciones['salida']
        with conectar_destino(autocommit=True) as conn:
            existe_erp = conn.execute("SELECT to_regclass('public.erp_persona')").fetchone()[0]
            if origen is not None:
                if not origen.is_file():
                    raise CommandError('Seleccione un archivo de respaldo existente.')
                ocupada = conn.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n
                        ON n.oid = c.relnamespace WHERE n.nspname = 'public'
                    )
                """).fetchone()[0]
                if ocupada:
                    raise CommandError('La restauración requiere una base local nueva con public vacío.')
            elif existe_erp is None:
                raise CommandError('La copia local todavía no contiene el ERP.')
        if not opciones['si'] and input(f'Operación local sobre {parametros["dbname"]}. Escriba SI: ') != 'SI':
            raise CommandError('Operación cancelada.')
        if origen is not None:
            _postgres('pg_restore', ['--single-transaction', '--exit-on-error', '--no-owner',
                                     '--no-privileges', str(origen.resolve())], parametros)
            self.stdout.write(self.style.SUCCESS('Respaldo restaurado en la nueva copia local.'))
        else:
            if salida.exists():
                raise CommandError('El archivo de salida ya existe. Seleccione una ruta nueva.')
            _postgres('pg_dump', ['--format=custom', '--no-owner', '--no-privileges',
                                  '--schema=public', '--file', str(salida.resolve())], parametros)
            self.stdout.write(self.style.SUCCESS('Respaldo de la copia local creado.'))
