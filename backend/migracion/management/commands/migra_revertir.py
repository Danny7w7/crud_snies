from django.core.management.base import BaseCommand

from migracion.carga import revertir


class Command(BaseCommand):
    help = 'Revierte del ERP todo lo creado por un lote de migracion.'

    def add_arguments(self, parser):
        parser.add_argument('lote', type=int)

    def handle(self, *args, **options):
        revertir(options['lote'], log=self.stdout.write)
        self.stdout.write(self.style.SUCCESS(f"Lote {options['lote']} revertido."))
