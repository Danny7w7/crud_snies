from dataclasses import FrozenInstanceError
from unittest import TestCase
from unittest.mock import Mock

from psycopg import sql

from .reasignar_ids_persona import (ReferenciaPersona, bloquear_reasignacion,
                                   inventariar_referencias, reasignar_ids_persona)


def _sentencia(consulta):
    return consulta.as_string().strip() if isinstance(consulta, sql.Composable) else consulta.strip()


def _fk(esquema='public', tabla='hija', columna='persona_id', tamaño=1,
        destino='id', diferible=True, accion='a'):
    return esquema, tabla, columna, tamaño, tamaño, destino, diferible, accion, 'hija_persona_fk'


class InventarioReferenciasTest(TestCase):
    def test_incluye_fk_de_otros_esquemas_y_referencias_logicas_sin_duplicarlas(self):
        cur = Mock()
        cur.fetchall.side_effect = [
            [_fk('otro', 'hija', 'responsable_id'), _fk(), _fk(tabla='ubicacion_ubicacion')],
            [('hija', 'persona_id'), ('ubicacion_ubicacion', 'persona_id'),
             ('ubicacion_equivalenciapersonaswa', 'persona_erp_id')],
        ]
        refs = inventariar_referencias(cur)
        self.assertEqual(refs, (
            ReferenciaPersona('otro', 'hija', 'responsable_id'),
            ReferenciaPersona('public', 'hija', 'persona_id'),
            ReferenciaPersona('public', 'ubicacion_equivalenciapersonaswa', 'persona_erp_id'),
            ReferenciaPersona('public', 'ubicacion_ubicacion', 'persona_id'),
        ))
        with self.assertRaises(FrozenInstanceError):
            refs[0].columna = 'otra'

    def test_rechaza_fk_sin_garantias_de_integridad(self):
        casos = (
            _fk(tamaño=2), _fk(destino='nro_documento'), _fk(diferible=False),
            _fk(accion='c'), _fk(accion='r'), _fk(accion='n'), _fk(accion='d'),
            _fk(tabla='auditoria_log_sistema'), _fk('swa_raw'),
        )
        for fk in casos:
            with self.subTest(fk=fk):
                cur = Mock()
                cur.fetchall.return_value = [fk]
                with self.assertRaisesRegex(ValueError, 'FK incompatible'):
                    inventariar_referencias(cur)

    def test_rechaza_columna_logica_desconocida(self):
        cur = Mock()
        cur.fetchall.side_effect = [[], [('nueva', 'persona_erp_id')]]
        with self.assertRaisesRegex(ValueError, 'public.nueva.persona_erp_id'):
            inventariar_referencias(cur)

    def test_locks_incluyen_origen_local_auditoria_catalogo_y_lotes_en_orden(self):
        cur = Mock()
        cur.fetchone.return_value = ('read committed', 'origin')
        cur.fetchall.side_effect = [[_fk('x', 'hija', 'contacto_id')], [], []]
        bloquear_reasignacion(cur, (ReferenciaPersona('x', 'hija', 'contacto_id'),))
        lock = _sentencia(cur.execute.call_args_list[1].args[0])
        self.assertEqual(lock, 'LOCK TABLE ONLY "migra"."lote", ONLY "migra"."mapa", '
                               'ONLY "public"."auditoria_log_sistema", ONLY "public"."erp_persona", '
                               'ONLY "public"."erp_tipodocumentoidentidad", ONLY "swa_raw"."persona", '
                               'ONLY "x"."hija" IN SHARE ROW EXCLUSIVE MODE')

    def test_detecta_nuevas_referencias_al_tomar_los_locks(self):
        cur = Mock()
        cur.fetchone.return_value = ('read committed', 'origin')
        cur.fetchall.side_effect = [[_fk()], [('hija', 'persona_id')]]
        with self.assertRaisesRegex(ValueError, 'inventario de referencias a persona cambió'):
            bloquear_reasignacion(cur, ())

    def test_rechaza_una_actualizacion_indirecta(self):
        cur = Mock()
        cur.fetchone.return_value = ('read committed', 'origin')
        cur.fetchall.side_effect = [[], [], [('public', 'erp_persona', 'id', None, False, True)]]
        with self.assertRaisesRegex(ValueError, 'actualización indirecta'):
            bloquear_reasignacion(cur, ())


class ReasignarIdsPersonaTest(TestCase):
    ref = ReferenciaPersona('erp', 'tabla "nativa"', 'persona id')
    cambios = {9: 21, 10: 22}

    def _cursor(self, lecturas, conteos=()):
        cur = Mock()
        cur.fetchall.side_effect = lecturas
        conteos = iter(conteos)

        def ejecutar(consulta, parametros=()):
            if _sentencia(consulta).startswith('UPDATE ONLY'):
                cur.rowcount = next(conteos)

        cur.execute.side_effect = ejecutar
        return cur

    def test_reasigna_referencias_y_solo_mapa_persona_antes_de_la_pk(self):
        cur = self._cursor([
            [(9, 1), (10, 1)], [(9, 2)], [(10, 1)],
            [(21, 2)], [(22, 1)], [(21, 1), (22, 1)],
        ], [2, 1, 2])
        conteos = reasignar_ids_persona(cur, self.cambios, (self.ref,))
        self.assertEqual(conteos, {self.ref.nombre: 2, 'migra.mapa.erp_id': 1, 'public.erp_persona.id': 2})
        escrituras = [(_sentencia(llamada.args[0]), llamada.args[1:]) for llamada in cur.execute.call_args_list
                      if _sentencia(llamada.args[0]).startswith(('UPDATE', 'SET CONSTRAINTS'))]
        self.assertEqual(escrituras[0], ('SET CONSTRAINTS ALL DEFERRED', ()))
        self.assertIn('UPDATE ONLY "erp"."tabla ""nativa""" AS t SET "persona id"', escrituras[1][0])
        self.assertIn('UPDATE ONLY "migra"."mapa" AS t SET "erp_id"', escrituras[2][0])
        self.assertIn("AND entidad = 'persona'", escrituras[2][0])
        self.assertNotIn('fila_importada', escrituras[2][0])
        self.assertIn('UPDATE ONLY "public"."erp_persona" AS t SET "id"', escrituras[3][0])
        self.assertEqual(escrituras[4], ('SET CONSTRAINTS ALL IMMEDIATE', ()))
        for _, parametros in escrituras[1:4]:
            self.assertEqual(parametros, (([9, 10], [21, 22]),))

    def test_rechaza_colisiones_e_ids_fuera_del_dominio(self):
        for cambios in ({9: 9}, {9: 21, 10: 21}, {9: 10, 10: 9}, {9: True}, {9: 2**63}):
            with self.subTest(cambios=cambios):
                cur = Mock()
                with self.assertRaises(ValueError):
                    reasignar_ids_persona(cur, cambios, (self.ref,))
                cur.execute.assert_not_called()

    def test_detecta_pk_ausente_u_ocupada_antes_de_modificar(self):
        for filas in ([(9, 1)], [(9, 1), (10, 1), (21, 1)]):
            cur = self._cursor([filas])
            with self.assertRaisesRegex(ValueError, 'ID de origen no existe'):
                reasignar_ids_persona(cur, self.cambios, (self.ref,))
            self.assertEqual(cur.execute.call_count, 1)

    def test_detecta_una_referencia_previa_al_destino_antes_de_modificar(self):
        cur = self._cursor([[(9, 1), (10, 1)], [(21, 2)]])
        with self.assertRaisesRegex(ValueError, 'referencias previas a un ID de destino'):
            reasignar_ids_persona(cur, self.cambios, (self.ref,))
        self.assertEqual(cur.execute.call_count, 2)

    def test_rechaza_filas_omitidas_en_la_actualizacion(self):
        cur = self._cursor([[(9, 1), (10, 1)], [(9, 2)], []], [1])
        with self.assertRaisesRegex(ValueError, 'cantidad de filas actualizadas'):
            reasignar_ids_persona(cur, self.cambios, (self.ref,))

    def test_rechaza_referencias_residuales_con_conteo_de_update_correcto(self):
        cur = self._cursor([[(9, 1), (10, 1)], [(9, 2)], [], [(9, 1), (21, 1)]], [2, 2])
        with self.assertRaisesRegex(ValueError, 'referencias antiguas'):
            reasignar_ids_persona(cur, self.cambios, (self.ref,))
