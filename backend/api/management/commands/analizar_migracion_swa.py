"""Analisis previo de lectura; no reserva IDs ni crea catalogos."""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections, transaction

from api.codigos_persona import (ESTADO_PERSONA_PENDIENTE, TIPOS_DOCUMENTO, cargar_grupos,
                                comprobar_destino, resolver_estado_persona, tipos_documento_destino)
from api.esquema_destino import validar_esquema_destino
from api.programas_swa import cargar_academico, destino_periodo


class Command(BaseCommand):
    help = 'Analiza personas y avance SWA sin escribir en ninguna base de datos.'

    def add_arguments(self, parser):
        parser.add_argument('--notas', action='store_true', help='Analizar notas y pensums.')
        parser.add_argument('--programa', type=int)
        parser.add_argument('--periodo', type=int)

    def handle(self, *args, **options):
        if options.get('notas'):
            from api.notas_swa import procesar_notas_swa
            import json
            try:
                resumen = procesar_notas_swa(programa=options.get('programa'), periodo=options.get('periodo'))
            except ValueError as exc:
                raise CommandError(str(exc)) from None
            except DatabaseError:
                raise CommandError('Fallo de base de datos; revisar conectividad y esquema sin cargar datos.') from None
            self.stdout.write(json.dumps(resumen, ensure_ascii=False))
            return
        if options.get('programa') is not None or options.get('periodo') is not None:
            raise CommandError('Los filtros requieren --notas.')
        validar_esquema_destino('completo')
        grupos, pendientes, estudiantes = cargar_grupos()
        sedes, matriculas, registros = cargar_academico()
        resumen = {'personas_nuevas': 0, 'personas_existentes': 0,
                   'personas_pendientes': len(pendientes), 'motivos': Counter(pendientes.values())}
        with transaction.atomic(using='default'):
            with connections['default'].cursor() as cur:
                if connections['default'].vendor == 'postgresql':
                    cur.execute('SET TRANSACTION READ ONLY')
            tipos = tipos_documento_destino()
            for grupo in {g.persona_id: g for g in grupos.values()}.values():
                try:
                    existente = comprobar_destino(grupo, tipos)
                    if existente is None:
                        if TIPOS_DOCUMENTO[grupo.tipo_swa] not in tipos:
                            raise ValueError('Tipo de documento pendiente en el ERP.')
                        if resolver_estado_persona(grupo.personas) is None:
                            raise ValueError(ESTADO_PERSONA_PENDIENTE)
                    resumen['personas_existentes' if existente else 'personas_nuevas'] += 1
                except ValueError as exc:
                    resumen['personas_pendientes'] += len(grupo.personas)
                    resumen['motivos'][str(exc)] += len(grupo.personas)
        resumen['estudiantes_por_programa'] = dict(Counter(e.programa for e in estudiantes))
        resumen['avance_por_destino'] = dict(Counter(destino_periodo(r['periodo']) for r in registros))
        resumen['motivos'] = dict(resumen['motivos'])
        import json
        self.stdout.write(json.dumps(resumen, ensure_ascii=False))
