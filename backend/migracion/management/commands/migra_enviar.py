import json

from django.core.management.base import BaseCommand, CommandError

from migracion.carga import enviar


class Command(BaseCommand):
    help = 'Carga el histórico elegible en la copia local del ERP.'

    def add_arguments(self, parser):
        parser.add_argument('--periodo', type=int, help='Limitar este lote a un periodo cerrado AAAAP.')

    def handle(self, *args, **options):
        try:
            resultado = enviar(log=self.stdout.write, periodo=options['periodo'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(resultado, indent=2, ensure_ascii=False))
