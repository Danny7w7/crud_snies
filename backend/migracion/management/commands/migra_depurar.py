import json

from django.core.management.base import BaseCommand

from migracion.depuracion import depurar


class Command(BaseCommand):
    help = 'Depura swa_raw hacia migra.* y genera los casos de revision (respeta decisiones previas).'

    def handle(self, *args, **options):
        metricas = depurar(log=self.stdout.write)
        self.stdout.write(json.dumps(metricas, indent=2, ensure_ascii=False, default=str))
