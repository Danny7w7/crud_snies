"""Carga independiente del endpoint general; el modo inicial es solo analisis."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from api.notas_swa import procesar_notas_swa


class Command(BaseCommand):
    help = 'Analiza las notas SWA; --aplicar escribe exclusivamente en default.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true')
        parser.add_argument('--programa', type=int)
        parser.add_argument('--periodo', type=int)

    def handle(self, *args, **options):
        try:
            resumen = procesar_notas_swa(aplicar=options['aplicar'],
                programa=options.get('programa'), periodo=options.get('periodo'))
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        except DatabaseError:
            raise CommandError('Fallo de base de datos; revisar conectividad y esquema sin cargar datos.') from None
        self.stdout.write(json.dumps(resumen, ensure_ascii=False))
