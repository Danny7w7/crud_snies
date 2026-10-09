import json

from django.core.management.base import BaseCommand

from migracion.carga import enviar


class Command(BaseCommand):
    help = 'Envio a la copia (ensayo): carga lo depurado (estado LISTA) a la copia del ERP en una transaccion.'

    def handle(self, *args, **options):
        resultado = enviar(log=self.stdout.write)
        self.stdout.write(json.dumps(resultado, indent=2, ensure_ascii=False))
