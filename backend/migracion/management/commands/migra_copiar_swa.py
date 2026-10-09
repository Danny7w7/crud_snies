from django.core.management.base import BaseCommand

from migracion.copia_swa import copiar_swa


class Command(BaseCommand):
    help = 'Copia cruda de todas las tablas de SWA al esquema swa_raw de la base de migracion.'

    def add_arguments(self, parser):
        parser.add_argument('tablas', nargs='*', help='Tablas a copiar (por defecto todas).')

    def handle(self, *args, **options):
        resumen = copiar_swa(options['tablas'] or None, log=self.stdout.write)
        total = sum(resumen.values())
        self.stdout.write(self.style.SUCCESS(f'{len(resumen)} tablas, {total} filas copiadas.'))
