from django.core.management.base import BaseCommand

from migracion.copia_swa import copiar_swa


class Command(BaseCommand):
    help = 'Copia cruda de todas las tablas de SWA al esquema swa_raw de la base de migracion.'

    def add_arguments(self, parser):
        parser.add_argument('tablas', nargs='*', help='Tablas a copiar (por defecto todas).')
        parser.add_argument('--periodo-hasta', type=int, help='Último periodo AAAAP de la instantánea.')

    def handle(self, *args, **options):
        resumen = copiar_swa(options['tablas'] or None, log=self.stdout.write,
                             periodo_hasta=options['periodo_hasta'])
        total = sum(resumen.values())
        self.stdout.write(self.style.SUCCESS(f'{len(resumen)} tablas, {total} filas copiadas.'))
