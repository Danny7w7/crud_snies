"""Pruebas sin conexiones reales ni carga del .env del proyecto."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from django.core.exceptions import ImproperlyConfigured

from config.db_postgres.base import DatabaseWrapper
from config.db_postgres.source import (_IDENTIDAD_ORIGEN, _TABLAS_ORIGEN, CursorSwa,
                                       estados_generales_swa, validar_select)
from config.routers import PRODUCTION_MODELS, ProductionRouter
from migracion.conexiones import conectar_swa
from migracion.copia_swa import copiar_swa


ORIGEN = {'NAME': 'swa_ficticio', 'HOST': 'swa.example.test', 'PORT': '5432', 'USER': 'lector'}
COMPROBACIONES = (
    "SELECT current_setting('transaction_read_only')",
    "SELECT current_setting('default_transaction_read_only')",
)


class ProductionRouterTests(unittest.TestCase):
    def setUp(self):
        self.router = ProductionRouter()

    def test_swa_models_are_read_from_production_and_cannot_be_written(self):
        for name in PRODUCTION_MODELS:
            with self.subTest(model=name):
                model = SimpleNamespace(_meta=SimpleNamespace(model_name=name))
                self.assertEqual(self.router.db_for_read(model), 'produccion')
                with self.assertRaises(PermissionError):
                    self.router.db_for_write(model)

    def test_destination_writes_keep_default_routing(self):
        for name in ('personaerp', 'datossecundarios', 'vinculopersona'):
            with self.subTest(model=name):
                model = SimpleNamespace(_meta=SimpleNamespace(model_name=name))
                self.assertIsNone(self.router.db_for_write(model))

    def test_no_app_can_migrate_the_source_database(self):
        for app in ('api', 'auth', 'sessions'):
            with self.subTest(app=app):
                self.assertFalse(self.router.allow_migrate('produccion', app))
                self.assertIsNone(self.router.allow_migrate('default', app))


class ReadOnlyConnectionTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(DATABASES={'produccion': dict(ORIGEN)})
        self.enterContext(patch('config.db_postgres.source.settings', self.settings))
        self.enterContext(patch.dict('os.environ', {'PGSERVICE': '', 'PGHOSTADDR': ''}))
        self.connect = self.enterContext(patch('config.db_postgres.source.ConexionSwa.connect'))
        self.conexion = self.connect.return_value
        self.conexion.autocommit = True
        self.conexion.info = SimpleNamespace(host=ORIGEN['HOST'], port=5432, dbname=ORIGEN['NAME'])
        self.cursor = self.conexion.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.side_effect = [('on',), ('on',)]

    def test_direct_connection_checks_both_flags_in_autocommit(self):
        self.assertIs(conectar_swa(), self.conexion)
        params = self.connect.call_args.kwargs
        self.assertTrue(params['autocommit'])
        self.assertEqual(params['target_session_attrs'], 'any')
        self.assertEqual(params['cursor_factory'], CursorSwa)
        self.assertIn('-c default_transaction_read_only=on', params['options'])
        self.assertIn('-c statement_timeout=300000', params['options'])
        self.assertIn('-c TimeZone=UTC', params['options'])
        self.assertIn('-c search_path=pg_catalog,public', params['options'])
        self.assertEqual(self.cursor.execute.call_args_list, [call(q) for q in COMPROBACIONES])

    def test_django_backend_uses_the_same_connection_without_initialization_sql(self):
        wrapper = DatabaseWrapper({'AUTOCOMMIT': True}, alias='produccion')
        with patch.object(DatabaseWrapper, 'timezone_name', 'America/Bogota'), \
             patch.object(DatabaseWrapper, 'timezone', None), \
             patch('config.db_postgres.base.settings', SimpleNamespace(USE_TZ=True)):
            params = wrapper.get_connection_params()
            wrapper.connection = wrapper.get_new_connection(params)
        wrapper.init_connection_state()
        self.assertFalse(wrapper.ensure_timezone())
        self.assertIn('-c TimeZone=America/Bogota', params['options'])
        self.assertEqual(self.cursor.execute.call_args_list, [call(q) for q in COMPROBACIONES])
        with self.assertRaises(PermissionError):
            wrapper.set_autocommit(False)
        with self.assertRaises(PermissionError):
            wrapper.create_cursor('cursor_nominal')

    def test_missing_or_ambiguous_target_is_rejected_before_connect(self):
        origen = self.settings.DATABASES['produccion']
        for key, value in (('HOST', ''), ('HOST', 'localhost,swa.example.test'),
                           ('PORT', '5432,5433'), ('NAME', 'host=otro'), ('USER', '')):
            with self.subTest(key=key, value=value), patch.dict(origen, {key: value}):
                with self.assertRaises(ImproperlyConfigured):
                    conectar_swa()
        for key in ('PGHOSTADDR', 'PGSERVICE'):
            with self.subTest(key=key), patch.dict('os.environ', {key: 'otro_destino'}):
                with self.assertRaises(ImproperlyConfigured):
                    conectar_swa()
        self.connect.assert_not_called()

    def test_read_only_flag_off_closes_connection_before_data_queries(self):
        self.cursor.fetchone.side_effect = [('on',), ('off',)]
        with self.assertRaises(PermissionError):
            conectar_swa()
        self.conexion.close.assert_called_once_with()
        self.assertEqual(self.cursor.execute.call_args_list, [call(q) for q in COMPROBACIONES])

    def test_local_mode_uses_verified_destination_with_swa_raw_search_path(self):
        self.settings.MIGRADOR_SWA_LOCAL = True
        destino = {'host': '127.0.0.1', 'hostaddr': '127.0.0.1', 'port': '5434', 'dbname': 'copia'}
        self.conexion.info = SimpleNamespace(host='127.0.0.1', port=5434, dbname='copia')
        with patch('config.db_postgres.source.parametros_destino', return_value=destino), \
             patch('config.db_postgres.source.verificar_cursor_destino') as verificar:
            self.assertIs(conectar_swa(), self.conexion)
        self.assertIn('-c search_path=pg_catalog,swa_raw', destino['options'])
        verificar.assert_called_once_with(self.cursor, destino)

    def test_source_table_is_queried_only_when_status_column_exists(self):
        with patch('config.db_postgres.source.connections', {'produccion': self.conexion}):
            self.cursor.fetchone.side_effect = [None, ('estado_general',)]
            self.assertEqual(estados_generales_swa(), {})
            self.assertEqual(self.cursor.execute.call_count, 1)
            self.cursor.fetchall.return_value = [(7, 'A')]
            self.assertEqual(estados_generales_swa(), {7: 'A'})
            self.cursor.execute.assert_called_with(
                'SELECT id, estado_general FROM persona WHERE estado_general IS NOT NULL')

    def preparar_copia(self, direccion='192.0.2.19', admin=False, escritura=False):
        self.enterContext(patch('config.db_postgres.source.socket.getaddrinfo', return_value=[
            (2, 1, 6, '', ('192.0.2.19', 5432)),
        ]))
        self.cursor.fetchone.side_effect = [
            ('on',), ('on',), (ORIGEN['NAME'], direccion, 5432, ORIGEN['USER'], ORIGEN['USER'], admin, escritura),
        ]
        self.cursor.fetchall.return_value = [('persona', 'r', 'p', 0, None, False, True, False)]

    def test_extraction_pins_dns_and_checks_same_connection_before_reading_tables(self):
        self.preparar_copia()
        self.assertIs(conectar_swa(tablas_copia=['persona']), self.conexion)
        self.assertEqual(self.connect.call_args.kwargs['hostaddr'], '192.0.2.19')
        self.assertEqual(self.cursor.execute.call_args_list,
                         [*(call(q) for q in COMPROBACIONES), call(_IDENTIDAD_ORIGEN),
                          call(_TABLAS_ORIGEN, (['persona'],))])
        for ejecucion in self.cursor.execute.call_args_list:
            validar_select(ejecucion.args[0])

    def test_extraction_rejects_ambiguous_dns_before_connect(self):
        with patch('config.db_postgres.source.socket.getaddrinfo', return_value=[
            (2, 1, 6, '', ('192.0.2.19', 5432)), (2, 1, 6, '', ('192.0.2.20', 5432)),
        ]), self.assertRaises(ImproperlyConfigured):
            conectar_swa(tablas_copia=['persona'])
        self.connect.assert_not_called()

    def test_extraction_rejects_nonmatching_server(self):
        self.preparar_copia(direccion='192.0.2.20')
        with self.assertRaises(PermissionError):
            conectar_swa(tablas_copia=['persona'])
        self.conexion.close.assert_called_once_with()
        self.assertEqual(self.cursor.execute.call_args_list,
                         [*(call(q) for q in COMPROBACIONES), call(_IDENTIDAD_ORIGEN)])

    def test_extraction_rejects_role_with_any_write_permission(self):
        self.preparar_copia(escritura=True)
        with self.assertRaises(PermissionError):
            conectar_swa(tablas_copia=['persona'])
        self.conexion.close.assert_called_once_with()
        self.assertEqual(self.cursor.execute.call_args_list,
                         [*(call(q) for q in COMPROBACIONES), call(_IDENTIDAD_ORIGEN)])

    def test_extraction_rejects_administrative_role(self):
        self.preparar_copia(admin=True)
        with self.assertRaises(PermissionError):
            conectar_swa(tablas_copia=['persona'])
        self.conexion.close.assert_called_once_with()

    def test_extraction_rejects_view_even_when_role_can_read_it(self):
        self.preparar_copia()
        self.cursor.fetchall.return_value = [('persona', 'v', 'p', 0, None, False, True, False)]
        with self.assertRaises(PermissionError):
            conectar_swa(tablas_copia=['persona'])
        self.conexion.close.assert_called_once_with()


class SourceSqlTests(unittest.TestCase):
    def test_rejects_statements_with_side_effects(self):
        for consulta in (
            'BEGIN READ ONLY', 'ROLLBACK', 'SET statement_timeout=100', 'SHOW transaction_read_only',
            'COPY persona TO STDOUT', 'SELECT id INTO copia FROM persona',
            'SELECT id FROM persona FOR UPDATE', 'SELECT id FROM persona FOR KEY SHARE',
            'WITH datos AS (DELETE FROM persona RETURNING *) SELECT * FROM datos',
            'SELECT 1; SELECT 2', 'SELECT pg_advisory_lock(1)', 'SELECT "set_config"(a,b,c)',
            "SELECT nextval('secuencia')", 'SELECT public.count(id) FROM persona',
            'SELECT * FROM public.funcion(columna) AS (valor text)',
        ):
            with self.subTest(consulta=consulta), self.assertRaises(PermissionError):
                validar_select(consulta)

    def test_accepts_queries_used_for_payment_reports(self):
        ruta = Path(__file__).resolve().parents[2] / 'migracion/sql/pagos_periodo.sql'
        ctes = ruta.read_text(encoding='utf-8').split('-- ==== CONSULTA FINAL')[0]
        ctes = ctes.replace('/*MAPA*/ARRAY[]::text[], ARRAY[]::text[]', '%(dups)s::text[], %(reales)s::text[]')
        validar_select(ctes + ' SELECT * FROM fin WHERE prog = ANY(%(progs)s)')
        validar_select(ctes + ' SELECT * FROM anom')

    def test_raw_copy_streams_select_and_copies_only_in_destination(self):
        origen, destino, reporte = MagicMock(), MagicMock(), MagicMock()
        scur = origen.__enter__.return_value.cursor.return_value.__enter__.return_value
        dcur = destino.__enter__.return_value.cursor.return_value.__enter__.return_value
        scur.fetchall.side_effect = [
            [('codigo', 'text', 'pg_catalog')],
            [('id', 'integer', 'pg_catalog'), ('password', 'text', 'pg_catalog'),
             ('Clave_hash', 'text', 'pg_catalog')],
            [('id',)],
        ]
        scur.stream.return_value = iter([('1',), ('2',)])
        with patch('migracion.copia_swa.esquema_swa', return_value='public'), \
             patch('migracion.copia_swa.conectar_swa', return_value=origen), \
             patch('migracion.copia_swa.conectar_destino', return_value=destino):
            self.assertEqual(copiar_swa(['persona'], log=reporte, periodo_hasta=20262), {'persona': 2})
        for ejecucion in scur.execute.call_args_list:
            validar_select(ejecucion.args[0])
        consulta = scur.stream.call_args.args[0]
        validar_select(consulta)
        self.assertEqual(scur.stream.call_args.args[1], [20262])
        self.assertNotIn('password', consulta.as_string())
        self.assertNotIn('Clave_hash', consulta.as_string())
        scur.copy.assert_not_called()
        self.assertTrue(dcur.copy.call_args.args[0].as_string().startswith('COPY "swa_raw"."persona"'))
        carga = dcur.copy.return_value.__enter__.return_value
        self.assertEqual(carga.write_row.call_args_list, [call(('1',)), call(('2',))])


if __name__ == '__main__':
    unittest.main()
