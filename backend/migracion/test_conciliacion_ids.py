"""Casos del planificador con un cursor simulado, sin conexiones a bases."""

from datetime import timedelta, timezone
from unittest import TestCase
from unittest.mock import Mock

from .conciliacion_ids import IMPORTACIONES, TABLAS_LOCALES, planear_ids_persona


ORIGEN, (INICIO, FIN) = next(iter(IMPORTACIONES.items()))
AUDITORIA = (700, 1, 'elecciones', 'CREACION', ORIGEN, INICIO)
PERSONA = (700, 123456, 91, 'Marta', '', 'Zúñiga', '', INICIO)
PERSONA_SWA = (300, '000.123.456', 1, 'marta', 'ZUNIGA')
OTRA_PERSONA_SWA = (301, '266000', 1, 'nombre', 'apellido')


def _cursor(*, tablas=None, auditoria=(AUDITORIA,), movimientos=(), personas=(PERSONA,),
            fuente=(PERSONA_SWA,), tipos=((91, 'CC'), (92, 'TI')), ocupados=((700, 123456),),
            mapa=(), trazas=()):
    cur = Mock()
    cur.fetchall.side_effect = [tablas if tablas is not None else [(t,) for t in TABLAS_LOCALES],
                                auditoria, movimientos, personas, fuente, tipos, ocupados, mapa, trazas]
    return cur


class PlanearIdsPersonaTests(TestCase):
    def test_propone_solo_identidad_comprobada_y_prueba_privada_determinista(self):
        cur = _cursor(fuente=((300, '123.456', 1, 'MARTA', 'ZUNIGA'),), mapa=(('00123456', 700),))
        caso, = planear_ids_persona(cur)
        self.assertEqual((caso.id_erp, caso.id_swa, caso.origen, caso.estado), (700, 300, ORIGEN, 'CAMBIAR'))
        self.assertRegex(caso.prueba, r'^[0-9a-f]{64}$')
        self.assertNotIn('123456', repr(caso))
        self.assertNotIn('Marta', repr(caso))
        self.assertTrue(all(accion.args[0].lstrip().startswith(('SELECT', 'WITH'))
                            for accion in cur.execute.call_args_list))
        hora_utc = INICIO.astimezone(timezone.utc)
        caso_utc, = planear_ids_persona(_cursor(
            auditoria=((*AUDITORIA[:-1], hora_utc),), personas=((*PERSONA[:-1], hora_utc),),
            fuente=((300, '123.456', 1, 'MARTA', 'ZUNIGA'),), mapa=(('00123456', 700),)))
        self.assertEqual(caso, caso_utc)

    def test_una_fecha_previa_o_en_limite_superior_va_a_revision(self):
        for fecha in (INICIO - timedelta(microseconds=1), FIN, None):
            with self.subTest(fecha=fecha):
                caso, = planear_ids_persona(_cursor(personas=((*PERSONA[:-1], fecha),)))
                self.assertEqual(caso.estado, 'FECHA_ERP_INCOMPATIBLE')

    def test_revision_por_auditoria_ambigua_o_actualizacion(self):
        for evento, estado in (('CREACION', 'AUDITORIA_AMBIGUA'), ('ACTUALIZACION', 'AUDITORIA_ACTUALIZACION')):
            with self.subTest(evento=evento):
                caso, = planear_ids_persona(_cursor(auditoria=(AUDITORIA, (700, 2, 'elecciones', evento, ORIGEN, INICIO))))
                self.assertEqual(caso.estado, estado)

    def test_no_infiere_alias_alternativo_ni_elige_entre_duplicados(self):
        casos = (
            (((300, 'X123456', 1, 'MARTA', 'ZUNIGA'), OTRA_PERSONA_SWA), 'SIN_COINCIDENCIA_SWA'),
            (((300, '123456', 4, 'MARTA', 'ZUNIGA'),), 'TIPO_O_ID_SWA_INCOMPATIBLE'),
            (((1 << 31, '123456', 1, 'MARTA', 'ZUNIGA'), OTRA_PERSONA_SWA), 'TIPO_O_ID_SWA_INCOMPATIBLE'),
            (((300, '123456', 1, 'MARTA', 'ZUNIGA'), (301, '123456', 4, 'MARTA', 'ZUNIGA')),
             'DOCUMENTO_SWA_DUPLICADO'),
            (((300, '123456', 1, 'OTRO', 'NOMBRE'),), 'NOMBRE_INCOMPATIBLE'),
        )
        for fuente, estado in casos:
            with self.subTest(estado=estado):
                caso, = planear_ids_persona(_cursor(fuente=fuente))
                self.assertEqual(caso.estado, estado)

    def test_protege_destinos_ocupados_y_trazas_contradictorias(self):
        casos = (
            ({'ocupados': ((300, 900901), (700, 123456))}, 'ID_SWA_OCUPADO'),
            ({'mapa': (('00123456', 702),)}, 'MAPA_PERSONA_INCONSISTENTE'),
            ({'trazas': (('persona', 300, 702, None, None, None),)}, 'TRAZA_UBICACION_INCONSISTENTE'),
            ({'trazas': (('persona', 350, 700, None, None, None),)}, 'TRAZA_UBICACION_INCONSISTENTE'),
            ({'trazas': (('estudiante', 300, 700, 'RESERVADO', 42, 702),)}, 'TRAZA_UBICACION_INCONSISTENTE'),
            ({'trazas': (('estudiante', 300, 700, 'RESERVADO', 42, 700),
                         ('ubicacion', None, 702, 'RESERVADO', None, None))}, 'TRAZA_UBICACION_INCONSISTENTE'),
        )
        for opciones, estado in casos:
            with self.subTest(opciones=tuple(opciones)):
                caso, = planear_ids_persona(_cursor(**opciones))
                self.assertEqual(caso.estado, estado)

    def test_distingue_coincidencia_previa_de_reasignacion_completada(self):
        opciones = {'fuente': ((300, '123456', 1, 'MARTA', 'ZUNIGA'),),
                    'personas': ((300, *PERSONA[1:]),), 'ocupados': ((300, 123456),),
                    'trazas': (('persona', 300, 300, None, None, None),)}
        coincide, = planear_ids_persona(_cursor(auditoria=((300, *AUDITORIA[1:]),), **opciones))
        self.assertEqual(coincide.estado, 'COINCIDE')
        prueba, = planear_ids_persona(_cursor(fuente=opciones['fuente']))
        movimientos = (('700', 300, 'COMPLETADO', '300', ORIGEN, prueba.prueba),)
        corregida, = planear_ids_persona(_cursor(movimientos=movimientos, **opciones))
        self.assertEqual((corregida.id_erp, corregida.id_swa, corregida.estado), (700, 300, 'YA_CORREGIDA'))
        incompleta, = planear_ids_persona(_cursor(movimientos=movimientos, **{**opciones, 'trazas': ()}))
        self.assertEqual(incompleta.estado, 'BITACORA_INCONSISTENTE')

    def test_estado_sin_fuente_o_sin_auditoria_produce_error(self):
        with self.assertRaisesRegex(ValueError, 'Faltan tablas locales'):
            planear_ids_persona(_cursor(tablas=[]))
        with self.assertRaisesRegex(ValueError, 'No se encontraron creaciones'):
            planear_ids_persona(_cursor(auditoria=()))
        with self.assertRaisesRegex(ValueError, 'carece de identidades'):
            planear_ids_persona(_cursor(fuente=()))
