"""Reglas de migracion con destino aislado y fuentes SWA simuladas."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from io import StringIO
from threading import Barrier
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection, connections
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError

from api.codigos_persona import (ESTADO_PERSONA_PENDIENTE, resolver_grupos,
                                obtener_o_crear_persona, registrar_equivalencia)
from api.modelos_academicos import AsignaturaErp
from api.esquema_destino import validar_esquema_destino
from api.models import (DatosSecundarios, Departamento, EstudianteSwa, Historico, Municipio,
                        Persona, PersonaErp, ProgramaErp, SecuenciaCodigoPersona,
                        SucursalErp, TipoVinculo, VinculoPersona)
from api.programas_swa import destino_periodo, preparar_avance
from api.serializers import EstudianteSerializer
from api.views import PersonaViewSet, _reintegro_personas
from scripts import migrar_swa
from ubicacion.models import (AsociacionProgramaSwa, AvanceAsignaturaSwa,
                              EquivalenciaEstudianteSwa, EquivalenciaPersonaSwa, Ubicacion)


class FuenteLectura:
    """No implementa operaciones de escritura, y exige el alias de lectura."""

    def __init__(self, registros):
        self.registros = list(registros)

    def using(self, alias):
        if alias != 'produccion':
            raise AssertionError('La fuente solo se consulta en produccion.')
        return self

    def all(self):
        return self

    def first(self):
        return self.registros[0] if self.registros else None

    def iterator(self):
        return iter(self.registros)

    def __iter__(self):
        return self.iterator()

    def select_related(self, *campos):
        return self

    def filter(self, **valores):
        return FuenteLectura(r for r in self.registros if all(
            (r[k] if isinstance(r, dict) else getattr(r, k)) == v for k, v in valores.items()))

    def values(self, *campos):
        return FuenteLectura([{k: getattr(r, k) for k in campos} for r in self.registros])

    def values_list(self, campo, flat=False):
        return [getattr(r, campo) if flat else (getattr(r, campo),) for r in self.registros]


def persona_swa(pk=7, tipo=1, documento='123456789', nombre='Persona Prueba',
                estado_general='ACTIVO'):
    return SimpleNamespace(id=pk, pk=pk, tipo_identificacion=tipo, identificacion=documento,
        nombre=nombre, apellido='Apellido Prueba', email='prueba@example.com',
        telefono='3001234567', direccion='Calle prueba', sexo='F', estado_civil=1,
        insert_log='2019/01/02 10:11:12 MIGRACION', estado_general=estado_general)


def estudiante_swa(p, codigo='SWA-A', programa=27, periodo=20262):
    return SimpleNamespace(persona_id=p.pk, persona=p, codigo=codigo, programa=programa,
                           periodo_ingreso=periodo, estado='A')


def registro(codigo='SWA-ING', programa=51, periodo=20262, modulo=1, grupo=1, estado='R', definitiva='A'):
    return dict(periodo=periodo, programa=programa, asignatura=programa*100+modulo,
                grupo=grupo, estudiante=codigo, estado=estado,
                estado_definitiva=definitiva, modulo=modulo)


@contextmanager
def fuente_swa(personas, estudiantes, contexto=None):
    contexto = contexto or ({27: 1, 28: 1, 50: 1, 51: 1, 52: 3, 60: 3}, set(), [])
    historicos = [SimpleNamespace(periodo=p, programa=pr, estudiante=c, estado='M')
                  for p, pr, c in contexto[1]]
    with patch.object(Persona, 'objects', FuenteLectura(personas)), \
         patch.object(EstudianteSwa, 'objects', FuenteLectura(estudiantes)), \
         patch.object(Historico, 'objects', FuenteLectura(historicos)), \
         patch('api.codigos_persona.estados_generales_swa', return_value={}), \
         patch.object(migrar_swa, '_formulario_contacto_por_persona', return_value={}), \
         patch.object(migrar_swa, '_escribir_reporte_rechazadas', return_value=None), \
         patch.object(migrar_swa, 'cargar_academico', return_value=contexto), \
         patch('api.programas_swa.cargar_academico', return_value=contexto), \
         patch('api.management.commands.analizar_migracion_swa.cargar_academico', return_value=contexto):
        yield


class IdentidadSwaTests(TransactionTestCase):
    databases = {'default'}
    modelos_erp = (Departamento, Municipio, SucursalErp, ProgramaErp, PersonaErp,
                   SecuenciaCodigoPersona, TipoVinculo, VinculoPersona, DatosSecundarios,
                   AsignaturaErp)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as editor:
            for modelo in cls.modelos_erp:
                editor.create_model(modelo)
        with connection.cursor() as cur:
            cur.execute('CREATE TABLE erp_tipodocumentoidentidad '
                        '(id integer PRIMARY KEY, codigo varchar(6) UNIQUE, nombre varchar(100))')

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as editor:
            for modelo in reversed(cls.modelos_erp):
                editor.delete_model(modelo)
        with connection.cursor() as cur:
            cur.execute('DROP TABLE erp_tipodocumentoidentidad')
        super().tearDownClass()

    def setUp(self):
        for modelo in (AvanceAsignaturaSwa, AsociacionProgramaSwa, EquivalenciaEstudianteSwa,
                       EquivalenciaPersonaSwa, Ubicacion):
            modelo.objects.using('default').all().delete()
        for modelo in reversed(self.modelos_erp):
            modelo.objects.using('default').all().delete()
        SucursalErp.objects.bulk_create([
            SucursalErp(id=pk, nombre=f'Sede {pk}', codigo=str(pk), estado=True) for pk in (1, 2)
        ])
        ProgramaErp.objects.bulk_create([ProgramaErp(id=pk, nombre=str(pk),
            sucursal_id=2 if pk in (52, 60) else 1) for pk in (27, 28, 50, 51, 52, 60)])
        with connection.cursor() as cur:
            cur.execute('DELETE FROM erp_tipodocumentoidentidad')
            cur.execute("INSERT INTO erp_tipodocumentoidentidad VALUES "
                        "(1,'CC','Cedula'),(4,'TI','Tarjeta'),(3,'CE','Extranjeria'),"
                        "(2,'PP','Pasaporte'),(8,'RC','Registro')")
        self.tipo = TipoVinculo.objects.create(nombre='Estudiante', estado=True)

    def crear_destino(self, pk=7, documento=123456789, **datos):
        datos.setdefault('estado_general', 'INACTIVO')
        return PersonaErp.objects.create(id=pk, nro_documento=documento, tipo_documento_id=1,
            primer_nombre='Persona', segundo_nombre='Prueba', primer_apellido='Apellido',
            segundo_apellido='Prueba', email='prueba@example.com', **datos)

    def migrar(self, personas, estudiantes, contexto=None):
        with fuente_swa(personas, estudiantes, contexto):
            personas_resultado = migrar_swa.migrar_personas()
            vinculos = migrar_swa.migrar_vinculos()
        return personas_resultado, vinculos

    def test_conserva_id_swa_repite_sin_secuencia_ni_ddl(self):
        p = persona_swa()
        with fuente_swa([p], [estudiante_swa(p)]), CaptureQueriesContext(connection) as consultas:
            primera = migrar_swa.migrar_personas()
            migrar_swa.migrar_vinculos()
            segunda = migrar_swa.migrar_personas()
            migrar_swa.migrar_vinculos()
        self.assertEqual(PersonaErp.objects.get().pk, 7)
        self.assertEqual(primera['personas']['creadas'], 1)
        self.assertEqual(segunda['personas']['omitidas'], 1)
        self.assertEqual(EquivalenciaPersonaSwa.objects.count(), 1)
        self.assertEqual(VinculoPersona.objects.count(), 1)
        self.assertFalse(SecuenciaCodigoPersona.objects.exists())
        self.assertFalse(any('ALTER TABLE' in q['sql'].upper() or 'TRUNCATE' in q['sql'].upper()
                             or 'erp_secuenciacodigopersona' in q['sql'] for q in consultas))

    def test_ingles_anterior_no_desplaza_id_de_carrera_y_unifica_cc_ti_0(self):
        ingles = persona_swa(2, 4)
        carrera = persona_swa(9)
        extension = persona_swa(3, 0)
        estudiantes = [estudiante_swa(ingles, 'ING', 51, 20191),
                       estudiante_swa(carrera, 'CAR', 27, 20211),
                       estudiante_swa(extension, 'EXT', 50, 20181)]
        self.migrar([ingles, carrera, extension], estudiantes)
        self.assertEqual(PersonaErp.objects.get().pk, 9)
        self.assertEqual(set(EquivalenciaPersonaSwa.objects.values_list('persona_swa_id', flat=True)), {2, 9, 3})
        self.assertEqual(set(VinculoPersona.objects.values_list('programa_id', flat=True)), {27, 50})

    def test_primer_ingreso_carrera_y_empate_por_id(self):
        personas = [persona_swa(12), persona_swa(9), persona_swa(2)]
        estudiantes = [estudiante_swa(personas[0], 'A', 27, 20211),
                       estudiante_swa(personas[1], 'B', 28, 20191),
                       estudiante_swa(personas[2], 'C', 27, 20191)]
        grupos, pendientes = resolver_grupos(personas, estudiantes)
        self.assertFalse(pendientes)
        self.assertEqual(grupos[12].persona_id, 2)

    def test_sin_carrera_menor_id_y_sin_ingreso_no_genera_id(self):
        personas = [persona_swa(8), persona_swa(3)]
        self.migrar(personas, [estudiante_swa(personas[0], programa=50, periodo=None)])
        self.assertEqual(PersonaErp.objects.get().pk, 3)
        self.assertFalse(SecuenciaCodigoPersona.objects.exists())

    def test_nombres_distintos_o_tipos_incompatibles_quedan_pendientes(self):
        for segundo in (persona_swa(8, nombre='Otra Persona'), persona_swa(8, tipo=3)):
            with self.subTest(tipo=segundo.tipo_identificacion):
                grupos, pendientes = resolver_grupos([persona_swa(), segundo], [])
                self.assertFalse(grupos)
                self.assertEqual(set(pendientes), {7, 8})

    def test_desconocido_sin_respaldo_y_documento_invalido(self):
        for p in (persona_swa(tipo=0), persona_swa(documento='12'), persona_swa(documento='9'*30)):
            self.assertFalse(resolver_grupos([p], [])[0])

    def test_existente_otro_id_queda_pendiente_sin_alias_ni_renumeracion(self):
        self.crear_destino(99)
        p = persona_swa()
        resumen, _ = self.migrar([p], [estudiante_swa(p)])
        self.assertEqual(len(resumen['rechazadas']), 1)
        self.assertEqual(PersonaErp.objects.get().pk, 99)
        self.assertFalse(EquivalenciaPersonaSwa.objects.exists())
        self.assertFalse(VinculoPersona.objects.exists())

    def test_id_ocupado_por_otra_persona_no_se_sobrescribe(self):
        self.crear_destino(documento=987654321)
        p = persona_swa()
        self.migrar([p], [estudiante_swa(p)])
        self.assertEqual(PersonaErp.objects.get().nro_documento, 987654321)
        self.assertFalse(EquivalenciaPersonaSwa.objects.exists())

    def test_reutiliza_mismo_id_identidad_y_preserva_contacto(self):
        existente = self.crear_destino(telefono=3109999999)
        p = persona_swa()
        self.migrar([p], [estudiante_swa(p)])
        existente.refresh_from_db()
        self.assertEqual(existente.telefono, 3109999999)
        self.assertEqual(existente.estado_general, 'INACTIVO')
        self.assertEqual(EquivalenciaPersonaSwa.objects.get().persona_erp_id, 7)

    def test_reutiliza_otro_id_solo_con_equivalencia_explicita_y_datos_coincidentes(self):
        existente = self.crear_destino(pk=99)
        EquivalenciaPersonaSwa.objects.create(persona_swa_id=7, persona_erp=existente)
        p = persona_swa()
        resultado, _ = self.migrar([p], [estudiante_swa(p)])
        self.assertEqual(resultado['personas']['omitidas'], 1)
        self.assertEqual(set(PersonaErp.objects.values_list('id', flat=True)), {99})
        self.assertEqual(EquivalenciaEstudianteSwa.objects.get().persona_erp_id, 99)

    def test_estado_general_requiere_evidencia_y_acepta_decision_explicita(self):
        p = persona_swa(estado_general=None)
        alumno = estudiante_swa(p)
        contexto = ({27: 1}, {(20261, 27, alumno.codigo)}, [])
        with fuente_swa([p], [alumno], contexto):
            resultado = migrar_swa.migrar_personas()
            self.assertFalse(PersonaErp.objects.exists())
            self.assertEqual(resultado['rechazadas'], [{'swa_id': 7, 'motivo': ESTADO_PERSONA_PENDIENTE}])
            self.assertEqual(migrar_swa.migrar_personas({7: 'RETIRADO'})['personas']['creadas'], 1)
        self.assertEqual(PersonaErp.objects.get().estado_general, 'RETIRADO')

    def test_save_exige_id_y_rechaza_alias_swa(self):
        with self.assertRaises(ValueError):
            PersonaErp(nro_documento=123456789).save(using='default')
        with self.assertRaises(PermissionError):
            PersonaErp(id=7).save(using='produccion')
        self.assertFalse(SecuenciaCodigoPersona.objects.exists())

    def test_textos_vacios_respetan_no_null_y_conservan_segundo_nombre(self):
        p = persona_swa(nombre='Persona')
        p.email = None
        self.migrar([p], [estudiante_swa(p)])
        erp = PersonaErp.objects.get()
        self.assertEqual(erp.segundo_nombre, '')
        self.assertEqual(erp.email, '')
        self.assertFalse(PersonaErp._meta.get_field('email').null)

    def test_rollback_no_deja_persona_ni_equivalencias(self):
        p = persona_swa()
        grupos, _ = resolver_grupos([p], [])
        with patch('api.codigos_persona.registrar_personas', side_effect=ValueError('Conflicto')):
            with self.assertRaises(ValueError):
                obtener_o_crear_persona(grupos[7], {'CC': 1, 'TI': 4},
                    dict(primer_nombre='Persona', primer_apellido='Apellido', segundo_nombre='',
                         segundo_apellido='', email=''))
        self.assertFalse(PersonaErp.objects.exists())
        self.assertFalse(EquivalenciaPersonaSwa.objects.exists())

    def test_extension_50_y_60_vinculos_propios_misma_persona_sin_avance_inventado(self):
        p = persona_swa()
        self.migrar([p], [estudiante_swa(p), estudiante_swa(p,'EXT50',50),
                          estudiante_swa(p,'EXT60',60)])
        self.assertEqual(set(VinculoPersona.objects.values_list('programa_id', flat=True)), {27,50,60})
        self.assertEqual(PersonaErp.objects.count(), 1)
        self.assertEqual(AsociacionProgramaSwa.objects.count(), 3)
        self.assertFalse(AvanceAsignaturaSwa.objects.exists())

    def escenario_ingles(self, programa=51, carreras=(27,), periodos=(20261,20262)):
        p = persona_swa()
        alumnos = [estudiante_swa(p, f'CAR{c}', c, 20211) for c in carreras]
        alumnos.append(estudiante_swa(p, 'SWA-ING', programa, 20261))
        contexto = ({27:1,28:1,50:1,51:1,52:3,60:3},
                    {(periodo,c,f'CAR{c}') for periodo in periodos for c in carreras},
                    [registro(programa=programa,periodo=periodo) for periodo in periodos])
        return p, alumnos, contexto

    def test_ingles_secundario_por_periodo_con_avance_repetible(self):
        p, alumnos, contexto = self.escenario_ingles()
        self.migrar([p], alumnos, contexto)
        self.migrar([p], alumnos, contexto)
        vinculo = VinculoPersona.objects.get()
        self.assertEqual(vinculo.programa_id, 27)
        self.assertFalse(vinculo.activo)
        self.assertEqual(vinculo.programa_secundario_id, 51)
        self.assertEqual(AvanceAsignaturaSwa.objects.count(), 2)
        self.assertEqual(set(AvanceAsignaturaSwa.objects.values_list('destino_notas', flat=True)),
                         {'pendiente', 'historico'})
        salida = EstudianteSerializer(vinculo).data
        self.assertEqual(salida['programa_secundario'], 51)
        self.assertEqual(salida['codigo_persona'], '7')
        self.assertEqual(set(salida['codigos_estudiante_swa']), {'CAR27','SWA-ING'})

    def test_varias_carreras_o_sede_distinta_dejan_ingles_pendiente(self):
        for programa, carreras in ((51,(27,28)), (52,(27,))):
            with self.subTest(programa=programa):
                p, alumnos, contexto = self.escenario_ingles(programa, carreras)
                self.migrar([p], alumnos, contexto)
                self.assertFalse(AsociacionProgramaSwa.objects.filter(
                    codigo_estudiante='SWA-ING', motivo_pendiente='').exists())
                self.assertFalse(VinculoPersona.objects.exclude(programa_secundario_id=None).exists())
                for modelo in (AvanceAsignaturaSwa,AsociacionProgramaSwa,EquivalenciaEstudianteSwa):
                    modelo.objects.all().delete()
                VinculoPersona.objects.all().delete()

    def test_sin_evidencia_no_asocia_a_carrera_mas_reciente(self):
        p, alumnos, contexto = self.escenario_ingles(periodos=())
        self.migrar([p], alumnos, contexto)
        self.assertTrue(AsociacionProgramaSwa.objects.get(codigo_estudiante='SWA-ING').motivo_pendiente)
        self.assertIsNone(VinculoPersona.objects.get().programa_secundario_id)

    def test_ingles_sin_carrera_es_principal(self):
        p = persona_swa()
        self.migrar([p], [estudiante_swa(p,programa=52)])
        self.assertEqual(VinculoPersona.objects.get().programa_id, 52)
        self.assertIsNone(VinculoPersona.objects.get().programa_secundario_id)

    def test_secundaria_ocupada_no_se_reemplaza(self):
        p, alumnos, contexto = self.escenario_ingles()
        self.migrar([p], alumnos, contexto)
        VinculoPersona.objects.update(programa_secundario_id=52)
        self.migrar([p], alumnos, contexto)
        self.assertEqual(VinculoPersona.objects.get().programa_secundario_id, 52)
        self.assertEqual(AsociacionProgramaSwa.objects.filter(
            codigo_estudiante='SWA-ING', motivo_pendiente='').count(), 1)

    def test_intentos_repetidos_contados_una_vez_y_ceros_no_inventan_aprobacion(self):
        p, alumnos, contexto = self.escenario_ingles()
        contexto[2].extend([registro(periodo=20262,grupo=2), registro(modulo=2,definitiva='R')])
        _, resultado = self.migrar([p], alumnos, contexto)
        self.assertEqual(AvanceAsignaturaSwa.objects.count(), 4)
        self.assertEqual(resultado['preparacion_academica']['modulos_aprobados'], 1)

    def test_corte_explicito_y_futuros_pendientes(self):
        self.assertEqual(destino_periodo(20261), 'historico')
        self.assertEqual(destino_periodo(20262), 'pendiente')
        self.assertEqual(destino_periodo(20271), 'pendiente')
        self.assertEqual(destino_periodo(20263), 'pendiente')

    def datos_formulario(self):
        return {'tipo_identificacion':'CC','numero_identificacion':'123456789',
                'primer_nombre':'Persona','segundo_nombre':'Prueba','primer_apellido':'Apellido',
                'segundo_apellido':'Prueba','email':'prueba@example.com','telefono':3001234567,
                'sexo':'F','codigo_estudiante':'SWA-A','programa':'27',
                'periodo_anio':'2026','periodo_semestre':'2','municipio':None,
                'municipio_nacimiento':None,'pais_nacimiento':'CO',
                'lugar_expedicion_documento':None,'fecha_expedicion_documento':date(2010,1,1)}

    def test_formulario_creacion_edicion_id_swa_y_rollback(self):
        p = persona_swa()
        with fuente_swa([p], [estudiante_swa(p)]):
            s = EstudianteSerializer()
            v = s.create(self.datos_formulario())
            self.assertTrue(v.activo)
            self.assertEqual(s.update(v,self.datos_formulario()).persona_id, 7)
        self.assertEqual(EquivalenciaPersonaSwa.objects.count(), 1)
        self.assertTrue(Ubicacion.objects.exists())
        self.assertFalse(SecuenciaCodigoPersona.objects.exists())

    def test_formulario_error_revierte_persona_alias_datos_y_ubicacion(self):
        p = persona_swa()
        TipoVinculo.objects.all().delete()
        with fuente_swa([p],[estudiante_swa(p)]):
            with self.assertRaises(ValidationError):
                EstudianteSerializer().create(self.datos_formulario())
        self.assertFalse(PersonaErp.objects.exists())
        self.assertFalse(EquivalenciaPersonaSwa.objects.exists())
        self.assertFalse(DatosSecundarios.objects.exists())
        self.assertFalse(Ubicacion.objects.exists())

    def test_datos_secundarios_preservan_direccion_fecha_y_unicidad(self):
        p = persona_swa()
        with fuente_swa([p],[estudiante_swa(p)]), \
             patch.object(migrar_swa,'_fechas_nacimiento_por_persona',return_value={7:date(2000,2,3)}), \
             patch.object(migrar_swa,'_mapa_municipios_divipola',return_value={}), \
             patch.object(migrar_swa,'_expedicion_por_persona',return_value={}), \
             patch.object(migrar_swa,'_lugares_nacimiento_por_persona',return_value={}), \
             patch.object(migrar_swa,'_estratos_por_persona',return_value={}):
            migrar_swa.migrar_personas()
            migrar_swa.migrar_datos_secundarios()
            DatosSecundarios.objects.update(direccion='Direccion ERP')
            PersonaErp.objects.update(fecha_nacimiento=date(1999,1,1))
            p.direccion = 'SIN DIRECCION'
            migrar_swa.migrar_datos_secundarios()
        self.assertEqual(DatosSecundarios.objects.get().direccion,'Direccion ERP')
        self.assertEqual(PersonaErp.objects.get().fecha_nacimiento,date(1999,1,1))
        self.assertEqual(DatosSecundarios.objects.count(),1)

    def test_preflight_antes_de_consultar_swa(self):
        validar_esquema_destino('completo')
        with patch.object(connection.introspection,'table_names',return_value=[]), patch.object(Persona,'objects') as fuente:
            with self.assertRaisesRegex(ValueError,'Destino incompatible'):
                migrar_swa.migrar_personas()
            fuente.using.assert_not_called()

    def test_comando_analisis_no_escribe_ni_reserva(self):
        p = persona_swa()
        with fuente_swa([p],[estudiante_swa(p)]), CaptureQueriesContext(connection) as consultas:
            salida = StringIO()
            call_command('analizar_migracion_swa',stdout=salida)
        self.assertIn('personas_nuevas',salida.getvalue())
        self.assertFalse(PersonaErp.objects.exists())
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT','UPDATE','DELETE','ALTER','CREATE'))
                             for q in consultas))

    def test_consulta_programa_ingles_sin_m_no_oculta_carrera(self):
        p = persona_swa()
        e = [estudiante_swa(p,'CAR',27,20211),estudiante_swa(p,'ING',51,20262),
             estudiante_swa(p,'EXT',60,20262)]
        contexto = ({27:1,51:1,60:3},{(20211,27,'CAR')},[])
        with fuente_swa([p],e,contexto), patch.object(PersonaViewSet,'queryset',FuenteLectura(e)):
            vista = PersonaViewSet()
            vista.request = SimpleNamespace(query_params={'programa':'51'})
            self.assertEqual([r.codigo for r in vista.get_queryset()],['ING'])
            vista.request = SimpleNamespace(query_params={'programa':'27'})
            self.assertEqual([r.codigo for r in vista.get_queryset()],['CAR'])
            vista.request = SimpleNamespace(query_params={'programa':'60'})
            self.assertEqual([r.codigo for r in vista.get_queryset()],['EXT'])

    def test_reintegro_no_mezcla_periodos_de_ingles_y_carrera(self):
        p = persona_swa()
        e = [estudiante_swa(p,'CAR',27),estudiante_swa(p,'ING',51)]
        contexto=({27:1,51:1},{(20251,27,'CAR'),(20261,27,'CAR'),(20252,51,'ING')},[])
        with fuente_swa([p],e,contexto):
            self.assertIn((7,27),_reintegro_personas())
            self.assertNotIn((7,51),_reintegro_personas())

    def test_mismo_codigo_ingles_conserva_carrera_distinta_por_periodo(self):
        p, alumnos, contexto = self.escenario_ingles(carreras=(27,28), periodos=(20252, 20261))
        contexto = (contexto[0], {(20252,27,'CAR27'),(20261,28,'CAR28')}, contexto[2])
        self.migrar([p],alumnos,contexto)
        relaciones = dict(AsociacionProgramaSwa.objects.filter(codigo_estudiante='SWA-ING')
                          .values_list('periodo_swa','vinculo_erp__programa_id'))
        self.assertEqual(relaciones,{20252:27,20261:28})
        self.assertEqual(AvanceAsignaturaSwa.objects.count(),2)
        self.assertFalse(EquivalenciaEstudianteSwa.objects.filter(codigo_estudiante='SWA-ING').exists())

    def test_formulario_ingles_preserva_periodo_y_estado_de_carrera(self):
        p, alumnos, contexto = self.escenario_ingles()
        self.migrar([p],alumnos,contexto)
        VinculoPersona.objects.update(activo=False)
        datos=self.datos_formulario()
        datos['codigo_estudiante']='SWA-ING'
        with fuente_swa([p],alumnos,contexto):
            vinculo=EstudianteSerializer().update(VinculoPersona.objects.get(),datos)
        self.assertEqual(vinculo.periodo,'2021-1')
        self.assertFalse(vinculo.activo)
        self.assertEqual(vinculo.programa_secundario_id,51)
        self.assertTrue(Ubicacion.objects.filter(codigo_estudiante='SWA-ING').exists())

    def test_alias_existente_con_otro_destino_no_se_reasigna(self):
        otro=self.crear_destino(99,987654321)
        EquivalenciaPersonaSwa.objects.create(persona_swa_id=7,persona_erp=otro)
        p=persona_swa()
        resumen,_=self.migrar([p],[estudiante_swa(p)])
        self.assertEqual(EquivalenciaPersonaSwa.objects.get().persona_erp_id,99)
        self.assertFalse(PersonaErp.objects.filter(pk=7).exists())
        self.assertEqual(len(resumen['rechazadas']),1)

    def test_migracion_estado_no_tiene_operaciones_sobre_erp(self):
        from importlib import import_module
        from django.db.migrations.state import ProjectState
        from django.db.migrations.executor import MigrationExecutor
        modulo=import_module('api.migrations.0012_contrato_erp_externo')
        estado=MigrationExecutor(connection).loader.project_state()
        self.assertEqual(estado.apps.get_model('api','VinculoPersona')._meta.get_field(
            'programa_secundario').column,'programa_secundario_id')
        self.assertFalse(estado.apps.get_model('api','ProgramaErp')._meta.managed)
        campo = estado.apps.get_model('api', 'PersonaErp')._meta.get_field('estado_general')
        self.assertEqual(campo.max_length, 16)
        self.assertFalse(campo.has_default())
        self.assertEqual(modulo.Migration.operations[-1].database_operations,[])

    def test_programas_validan_sucursal_y_respetan_valores_existentes(self):
        ProgramaErp.objects.filter(pk=51).delete()
        ProgramaErp.objects.filter(pk=52).update(activo=False, permite_credito=False)
        from unittest.mock import MagicMock
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchall.return_value = [
            (51,'Centro idiomas',1,None,None,1),
            (52,'Nombre diferente',1,None,None,3),
            (53,'Sin sede',1,None,None,999)]
        with patch.object(connections['produccion'],'cursor',return_value=cursor):
            resultado = migrar_swa.migrar_programas()
        self.assertEqual(ProgramaErp.objects.get(pk=51).sucursal_id,1)
        existente = ProgramaErp.objects.get(pk=52)
        self.assertEqual((existente.sucursal_id, existente.nombre), (2, '52'))
        self.assertFalse(existente.activo)
        self.assertFalse(existente.permite_credito)
        self.assertEqual([fila['codigo'] for fila in resultado['rechazadas']], [53])
        self.assertTrue(cursor.execute.call_args.args[0].lstrip().startswith('SELECT'))

    def test_limites_de_nombre_no_relajan_columnas(self):
        p=persona_swa(nombre='A'*101)
        resultado,_=self.migrar([p],[estudiante_swa(p)])
        self.assertFalse(PersonaErp.objects.exists())
        self.assertTrue(resultado['rechazadas'])

    @skipUnless(connection.vendor=='postgresql','Requiere PostgreSQL temporal para concurrencia.')
    def test_importaciones_concurrentes_mismo_id(self):
        p = persona_swa()
        grupos,_ = resolver_grupos([p],[])
        barrera=Barrier(6)
        def crear(_):
            try:
                barrera.wait(timeout=10)
                return obtener_o_crear_persona(grupos[7],{'CC':1,'TI':4},
                    dict(primer_nombre='Persona',segundo_nombre='Prueba',primer_apellido='Apellido',
                         segundo_apellido='Prueba',email='prueba@example.com'))[0].pk
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(set(pool.map(crear,range(6))),{7})
        self.assertEqual(PersonaErp.objects.count(),1)
        self.assertEqual(EquivalenciaPersonaSwa.objects.count(),1)
        self.assertFalse(SecuenciaCodigoPersona.objects.exists())
