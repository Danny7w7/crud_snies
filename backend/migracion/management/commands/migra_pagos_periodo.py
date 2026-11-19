from django.core.management.base import BaseCommand

from migracion.depuracion import PERIODO_CORTE
from migracion.pagos_periodo import GRUPOS, consultar, libro_excel, resumen, texto_filtros


class Command(BaseCommand):
    help = ('Quienes tienen asignaturas en un periodo sin pago comprobado de matricula, con los creditos apartados '
            'y las anomalias de descuentos. Lee SWA directo (solo lectura). '
            'Ej.: migra_pagos_periodo --periodo 20262 --excel pagos_2026_2.xlsx --grupo 3 --grupo 4')

    def add_arguments(self, parser):
        parser.add_argument('--periodo', type=int, default=PERIODO_CORTE, help=f'Periodo AAAAP (por defecto {PERIODO_CORTE})')
        parser.add_argument('--excel', help='Ruta del .xlsx (Resumen, Sin pago comprobado, Créditos, Anomalías, Pago comprobado)')
        parser.add_argument('--programa', type=int, action='append', default=[], help='Código de programa (repetible)')
        parser.add_argument('--grupo', action='append', default=[], choices=list(GRUPOS),
                            help='1 pago comprobado, 2 crédito, 3 sin pago, 4 cubierto sin dinero, 5 otro pago (repetible)')
        parser.add_argument('--veredicto', action='append', default=[], help='Código de veredicto, p. ej. 2D o 4B (repetible)')
        parser.add_argument('--documento', help='Documento de una persona')

    def handle(self, *args, **o):
        filtros = dict(programas=o['programa'], grupos=o['grupo'], veredictos=o['veredicto'], documento=o['documento'])
        datos = consultar(o['periodo'], **filtros)
        por_veredicto, por_anomalia = resumen(datos)
        for veredicto, v in sorted(por_veredicto.items()):
            self.stdout.write(f"{veredicto[:62]:62} {v['casos']:>6} casos {len(v['personas']):>6} personas")
        for (codigo, severidad), v in sorted(por_anomalia.items(), key=lambda kv: (int(kv[0][0][1:]), kv[0][1])):
            self.stdout.write(f"{codigo:4} {severidad:6} {v['casos']:>6} casos {len(v['personas']):>6} personas")
        if o['excel']:
            with open(o['excel'], 'wb') as f:
                f.write(libro_excel(datos, texto_filtros(**filtros)))
            self.stdout.write(f"Excel ({len(datos['casos'])} casos, {len(datos['anomalias'])} anomalías): {o['excel']}")
