"""Notas y mallas con fuente simulada y ERP creado solo en la BD temporal."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from io import StringIO
from threading import Event
from unittest.mock import MagicMock, patch
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor

from django.core.management import call_command
from django.db import connection, connections, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from api.catalogos_academicos import CatalogosAcademicos
from api.codigos_persona import resolver_grupos
from api.models import PersonaErp, ProgramaErp, SucursalErp, TipoVinculo, VinculoPersona
from api.modelos_academicos import (MODELOS_ACADEMICOS, AsignaturaErp, PeriodoErp,
    PensumErp, PensumAsignaturaErp, RestriccionPensumErp, MatriculaErp,
    InscripcionErp, InscripcionHistoricaErp, NotaErp, CorteNotaErp)
from api.notas_swa import procesar_notas_swa, clasificar_notas, nota_decimal, sesion_destino
from api.test_codigos_persona import persona_swa, estudiante_swa
from ubicacion.models import (AsociacionProgramaSwa, AvanceAsignaturaSwa,
    CalificacionSwa, ConfiguracionNotaSwa, EquivalenciaMatriculaSwa,
    EquivalenciaPensumSwa, EquivalenciaEstudianteSwa, EquivalenciaPersonaSwa)


def intento(periodo=20261, programa=27, estudiante='SWA-A', asignatura=100,
            grupo=1, esquema=1, definitiva=3.145, estado='R', nivel=1):
    r = dict(periodo=periodo, programa=programa, estudiante=estudiante,
        asignatura=asignatura, grupo=grupo, grupo_nota=esquema, definitiva=definitiva,
        estado=estado, estado_definitiva='A', nivel=nivel, creditos=2,
        tipo_credito=1, promediar='S', permanencia='S', bloqueado='N')
    notas = [{**{c:r[c] for c in ('periodo','programa','estudiante','asignatura','grupo')},
              'tipo_nota': t, 'estado': 1, 'nota': definitiva, 'observacion': 'Prueba'}
             for t in ([0] if esquema == 0 else [1,2,3])]
    return r, notas


class FuenteNotas:
    def __init__(self, fuente, registros):
        self.fuente, self.registros = fuente, registros

    @contextmanager
    def lectura(self):
        yield self

    def catalogos(self):
        return self.fuente

    def lotes(self, tamano):
        assert tamano == 500
        for inicio in range(0, len(self.registros), tamano):
            yield self.registros[inicio:inicio+tamano]


class LectorNotasSwaTests(SimpleTestCase):
    def test_lotes_agrupan_notas_y_avanzan_en_autocommit_con_select(self):
        from api.fuente_notas_swa import CAMPOS_CALIFICACION, CAMPOS_REGISTRO, LectorNotasSwa
        from config.db_postgres.source import validar_select

        fuente = MagicMock(in_atomic_block=False)
        fuente.get_autocommit.return_value = True
        cursor = fuente.cursor.return_value.__enter__.return_value
        r1, notas = intento(periodo=20261)
        r2, _ = intento(periodo=20262)
        campos = (*CAMPOS_REGISTRO, 'grupo_nota')
        cursor.fetchall.side_effect = [
            [tuple(r1[c] for c in campos) + tuple(n.get(c) for c in CAMPOS_CALIFICACION) for n in notas],
            [tuple(r2[c] for c in campos) + (None,) * len(CAMPOS_CALIFICACION)],
            [],
        ]
        with patch('api.fuente_notas_swa.connections', {'produccion': fuente}):
            with LectorNotasSwa(programa=27).lectura() as lector:
                lotes = list(lector.lotes(1))
        self.assertEqual([(r['periodo'], len(n)) for lote in lotes for r, n in lote], [(20261, 3), (20262, 0)])
        self.assertEqual(cursor.execute.call_count, 3)
        for ejecucion in cursor.execute.call_args_list:
            validar_select(ejecucion.args[0])
        self.assertEqual(cursor.execute.call_args_list[1].args[1], [27, 20261, 27, 100, 1, 'SWA-A', 1])


class NotasSwaTests(TransactionTestCase):
    databases = {'default'}
    modelos = (SucursalErp, ProgramaErp, PersonaErp, TipoVinculo, VinculoPersona, *MODELOS_ACADEMICOS)
    auxiliares = (CalificacionSwa, ConfiguracionNotaSwa, EquivalenciaMatriculaSwa,
        EquivalenciaPensumSwa, AvanceAsignaturaSwa, AsociacionProgramaSwa,
        EquivalenciaEstudianteSwa, EquivalenciaPersonaSwa)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as editor:
            for modelo in cls.modelos:
                editor.create_model(modelo)
        with connection.cursor() as cur:
            cur.execute('CREATE TABLE erp_tipodocumentoidentidad '
                        '(id integer PRIMARY KEY, codigo varchar(6), nombre varchar(100))')

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as editor:
            for modelo in reversed(cls.modelos):
                editor.delete_model(modelo)
        with connection.cursor() as cur:
            cur.execute('DROP TABLE erp_tipodocumentoidentidad')
        super().tearDownClass()

    def setUp(self):
        for modelo in self.auxiliares:
            modelo.objects.using('default').all().delete()
        for modelo in reversed(self.modelos):
            modelo.objects.using('default').all().delete()
        SucursalErp.objects.bulk_create([
            SucursalErp(id=pk, nombre=f'Sede {pk}', codigo=str(pk), estado=True) for pk in (1, 2)
        ])
        with connection.cursor() as cur:
            cur.execute('DELETE FROM erp_tipodocumentoidentidad')
            cur.execute("INSERT INTO erp_tipodocumentoidentidad VALUES (1,'CC','Cedula')")
        self.tipo = TipoVinculo.objects.using('default').create(nombre='Estudiante', estado=True)
        self.periodo = PeriodoErp.objects.using('default').create(nombre='2026-2', activo=True)
        self.anterior = PeriodoErp.objects.using('default').create(nombre='2026-1', activo=True)
        self.asignatura = AsignaturaErp.objects.using('default').create(codigo='100', nombre='Materia',
            metodologia='Presencial', creditos=2, hora_teorica=2, hora_practica=0, tipo_asignatura='TEORICA')

    def escenario(self, registros=None, alumnos=None):
        registros = registros or [intento()]
        p = persona_swa()
        alumnos = alumnos or [estudiante_swa(p)]
        for e in alumnos:
            e.pensum = e.programa*100+1
        grupos, pendientes = resolver_grupos([p], alumnos)
        persona = PersonaErp.objects.using('default').create(id=7, nro_documento=123456789,
            tipo_documento_id=1, primer_nombre='Persona', segundo_nombre='Prueba',
            primer_apellido='Apellido', segundo_apellido='Prueba', email='', estado_general='INACTIVO')
        EquivalenciaPersonaSwa.objects.create(persona_swa_id=7, persona_erp=persona)
        programas = {e.programa for e in alumnos}
        for pr in programas:
            ProgramaErp.objects.create(id=pr, nombre=str(pr), sucursal_id=2 if pr in (52,60) else 1)
        carrera = next((e for e in alumnos if e.programa not in (50,51,52,60)), None)
        for e in alumnos:
            if carrera and e.programa in (51,52):
                continue
            v = VinculoPersona.objects.create(persona=persona, tipo_vinculo=self.tipo,
                programa_id=e.programa, periodo='2026-2', activo=True, fecha_inicio=date(2026,1,1))
            EquivalenciaEstudianteSwa.objects.create(codigo_estudiante=e.codigo,
                persona_swa_id=7, persona_erp=persona, vinculo_erp=v)
        claves = {(r['programa'], next(e.pensum for e in alumnos if e.codigo == r['estudiante']),
                   r['asignatura']) for r, _ in registros}
        curricula = {(e.programa,e.pensum) for e in alumnos}
        historicos = [dict(periodo=r['periodo'], programa=r['programa'], estudiante=r['estudiante'],
            estado='M', nivel=1) for r,_ in registros]
        fuente = dict(grupos=grupos, personas_pendientes=pendientes,
            estudiantes={e.codigo:e for e in alumnos},
            programas=[dict(codigo=pr,sede=3 if pr in (52,60) else 1) for pr in programas],
            pensums=[dict(programa=pr,codigo=pe,descripcion='Pensum prueba',niveles=6,estado='A')
                     for pr,pe in curricula],
            asignaturas=[dict(programa=pr,codigo=a,nombre=f'Materia {a}',tipo='T',ihs=2)
                         for pr,_,a in claves],
            materias=[dict(programa=pr,pensum=pe,asignatura=a,nivel=1,creditos=2) for pr,pe,a in claves],
            prerrequisitos=[],correquisitos=[],historicos=historicos,
            primeros_periodos=[dict(programa=pr,pensum=pe,periodo=min(r['periodo'] for r,_ in registros
                if r['programa']==pr)) for pr,pe in curricula if any(r['programa']==pr for r,_ in registros)],
            tipos=[dict(codigo=t,descripcion=str(t),porcentaje=pct)
                   for t,pct in ((0,1),(1,.3),(2,.3),(3,.4))],
            esquemas=[dict(grupo_nota=0 if t==0 else 1,tipo_nota=t) for t in (0,1,2,3)])
        return FuenteNotas(fuente, registros)

    def cargar(self, fuente, aplicar=True):
        return procesar_notas_swa(aplicar=aplicar, lector=fuente)

    def test_mixta_conserva_horas_totales_y_permite_cargar_notas(self):
        fuente = self.escenario([intento(asignatura=101)])
        fuente.fuente['asignaturas'][0].update(tipo='M', ihs=5)
        resumen = self.cargar(fuente)
        self.assertEqual(resumen['migrados'], 1)
        materia = AsignaturaErp.objects.get(codigo='101')
        self.assertEqual(materia.tipo_asignatura, 'MIXTA')
        self.assertEqual((materia.hora_teorica, materia.hora_practica, materia.hora_mixta), (0, 0, 5))
        self.assertEqual(InscripcionHistoricaErp.objects.count(), 1)

    def test_malla_sin_cambios_no_reescribe_y_edicion_erp_sigue_protegida(self):
        fuente = self.escenario()
        self.cargar(fuente)
        catalogos = CatalogosAcademicos(fuente.fuente)
        with CaptureQueriesContext(connection) as consultas:
            pensum = catalogos.aplicar((27, 2701))
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE '))
                             for q in consultas.captured_queries))
        materia = PensumAsignaturaErp.objects.get(pensum=pensum)
        materia.semestre = 2
        materia.save(using='default', update_fields=['semestre'])
        with self.assertRaisesMessage(ValueError, 'La malla ERP fue modificada'):
            catalogos.aplicar((27, 2701))

    def test_historico_redondeo_original_e_idempotencia(self):
        fuente = self.escenario()
        primera = self.cargar(fuente)
        ids = list(InscripcionHistoricaErp.objects.values_list('id',flat=True))
        segunda = self.cargar(fuente)
        self.assertEqual((primera['migrados'],segunda['migrados']), (1,1))
        self.assertEqual(ids,list(InscripcionHistoricaErp.objects.values_list('id',flat=True)))
        self.assertEqual(InscripcionHistoricaErp.objects.get().definitiva,Decimal('3.15'))
        self.assertFalse(NotaErp.objects.exists())
        self.assertEqual(CalificacionSwa.objects.first().nota_origen,'3.145')
        self.assertEqual(AvanceAsignaturaSwa.objects.get().definitiva_origen,'3.145')
        self.assertFalse(CorteNotaErp.objects.exists())
        self.assertEqual(PersonaErp.objects.get().pk,7)

    def test_cero_confirmado_es_nota_y_pendiente_no_publica_definitiva(self):
        r, notas = intento(definitiva=0)
        r['estado_definitiva'] = 'R'
        notas[1]['estado'] = notas[2]['estado'] = 0
        fuente = self.escenario([(r,notas)])
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertFalse(InscripcionHistoricaErp.objects.exists())
        self.assertEqual(CalificacionSwa.objects.get(tipo_nota_swa=1).nota_origen, '0')
        self.assertEqual(CalificacionSwa.objects.count(),3)
        notas[1]['estado'] = notas[2]['estado'] = 1
        self.cargar(fuente)
        self.assertEqual(InscripcionHistoricaErp.objects.get().definitiva,Decimal('0.00'))

    def test_historico_conserva_cortes_sin_inscripcion_actual_ficticia(self):
        fuente = self.escenario([intento(periodo=20261)])
        self.assertEqual(self.cargar(fuente)['migrados'],1)
        self.assertEqual(InscripcionHistoricaErp.objects.get().definitiva,Decimal('3.15'))
        self.assertFalse(InscripcionErp.objects.exists())
        self.assertFalse(NotaErp.objects.exists())
        self.assertEqual(CalificacionSwa.objects.count(),3)
        self.anterior.refresh_from_db()
        self.assertTrue(self.anterior.activo)

    def test_dos_intentos_historicos_misma_materia_no_se_mezclan(self):
        fuente = self.escenario([intento(periodo=20261,grupo=1),intento(periodo=20261,grupo=2)])
        self.assertEqual(self.cargar(fuente)['migrados'],2)
        self.assertEqual(self.cargar(fuente)['migrados'],2)
        self.assertEqual(InscripcionHistoricaErp.objects.count(),2)
        self.assertEqual(AvanceAsignaturaSwa.objects.count(),2)

    def test_definitiva_directa_no_crea_cuarto_corte(self):
        fuente = self.escenario([intento(esquema=0)])
        self.assertEqual(self.cargar(fuente)['migrados'],1)
        self.assertFalse(NotaErp.objects.exists())
        self.assertFalse(CorteNotaErp.objects.exists())
        self.assertEqual(CalificacionSwa.objects.get().tipo_nota_swa,0)

    def test_registro_R_e_historico_P_queda_pendiente(self):
        fuente = self.escenario()
        fuente.fuente['historicos'][0]['estado']='P'
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertFalse(MatriculaErp.objects.exists())
        self.assertFalse(InscripcionHistoricaErp.objects.exists())

    def test_periodo_al_corte_queda_pendiente_aun_con_M_y_definitiva(self):
        fuente = self.escenario([intento(periodo=20262)])
        self.assertEqual(self.cargar(fuente)['pendientes'], 1)
        self.assertFalse(MatriculaErp.objects.exists())
        self.assertFalse(InscripcionErp.objects.exists())
        self.assertFalse(InscripcionHistoricaErp.objects.exists())
        self.assertEqual(AvanceAsignaturaSwa.objects.get().destino_notas, 'pendiente')
        self.assertEqual(CalificacionSwa.objects.count(), 3)

    def test_final_historica_marcada_sin_cortes_publica_valor_original(self):
        r, _ = intento()
        fuente = self.escenario([(r, [])])
        self.assertEqual(self.cargar(fuente)['migrados'], 1)
        self.assertEqual(InscripcionHistoricaErp.objects.get().definitiva, Decimal('3.15'))
        self.assertFalse(CalificacionSwa.objects.exists())

    def test_C_pendiente_conserva_todo_sin_notas_nativas(self):
        fuente = self.escenario([intento(estado='C')])
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertFalse(InscripcionErp.objects.exists())
        self.assertEqual(CalificacionSwa.objects.count(),3)
        self.assertIn('Registro C',AvanceAsignaturaSwa.objects.get().motivo_pendiente)

    def test_registro_P_conserva_valores_sin_publicar_notas(self):
        fuente = self.escenario([intento(estado='P', definitiva=0)])
        self.assertEqual(self.cargar(fuente)['pendientes'], 1)
        self.assertFalse(InscripcionErp.objects.exists())
        self.assertFalse(NotaErp.objects.exists())
        avance = AvanceAsignaturaSwa.objects.get()
        self.assertEqual((avance.estado_origen, avance.definitiva_origen), ('P', '0'))
        self.assertEqual(avance.registro_origen['estado'], 'P')
        self.assertEqual(list(CalificacionSwa.objects.values_list('nota_origen', flat=True)), ['0'] * 3)
        self.assertIn('sin correspondencia aprobada', avance.motivo_pendiente)

    def consultar_pendientes(self, accion='list', parametros=None, pk=None, clave='prueba-consulta', metodo='get'):
        from rest_framework.test import APIRequestFactory
        from api.pendientes_notas import PendienteNotaViewSet
        solicitud = getattr(APIRequestFactory(), metodo)('/api/pendientes-notas/', parametros or {},
            HTTP_X_CONSULTA_PASSWORD=clave)
        vista = PendienteNotaViewSet.as_view({metodo: accion})
        with self.settings(CONSULTA_PASSWORD_HASH='prueba-consulta'):
            return vista(solicitud, **({'pk': pk} if pk is not None else {}))

    def test_vista_pendientes_filtra_y_conserva_cortes_sin_leer_SWA(self):
        self.cargar(self.escenario([intento(periodo=20262, estado='P'), intento(periodo=20262, estado='C', grupo=2),
                                   intento(estado='C', grupo=3), intento(grupo=4)]))
        respuesta = self.consultar_pendientes(parametros={'estado': 'P', 'periodo': 20262})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['count'], 1)
        fila = respuesta.data['results'][0]
        self.assertEqual(fila['persona_swa_id'], 7)
        self.assertEqual(fila['asignatura_nombre'], 'Materia')
        detalle = self.consultar_pendientes('retrieve', pk=fila['id'])
        self.assertEqual(len(detalle.data['calificaciones']), 3)
        self.assertEqual(detalle.data['calificaciones'][0]['nota_origen'], '3.145')
        self.assertEqual(detalle.data['registro_origen']['estado'], 'P')
        resumen = self.consultar_pendientes('resumen', {'destino': 'historico'})
        self.assertEqual((resumen.data['total_pendientes'], resumen.data['total_filtrado']), (3, 1))
        self.assertEqual(resumen.data['sin_interpretar'], 1)
        self.assertEqual(resumen.data['filtros']['periodos'], [20262, 20261])

    def test_vista_pendientes_incluye_conflicto_ya_proyectado_y_normaliza_motivo_antiguo(self):
        self.cargar(self.escenario())
        avance = AvanceAsignaturaSwa.objects.get()
        avance.motivo_pendiente = 'Edicion posterior en ERP'
        avance.save(update_fields=['motivo_pendiente'])
        self.assertEqual(self.consultar_pendientes().data['count'], 1)
        self.assertEqual(self.consultar_pendientes('resumen', {'sin_interpretar': '1'}).data['total_filtrado'], 0)
        from api.motivos_notas import MOTIVO_ESTADO_REGISTRO, MOTIVO_ESTADO_REGISTRO_ANTIGUO
        avance.estado_origen = 'P'
        avance.motivo_pendiente = MOTIVO_ESTADO_REGISTRO_ANTIGUO
        avance.save(update_fields=['motivo_pendiente', 'estado_origen'])
        respuesta = self.consultar_pendientes(parametros={'motivo': MOTIVO_ESTADO_REGISTRO})
        self.assertEqual(respuesta.data['count'], 1)
        self.assertEqual(respuesta.data['results'][0]['motivo'], MOTIVO_ESTADO_REGISTRO)

    def test_vista_pendientes_requiere_acceso_rechaza_escritura_y_filtros_invalidos(self):
        self.cargar(self.escenario([intento(estado='C')]))
        self.assertEqual(self.consultar_pendientes(clave='incorrecta').status_code, 403)
        self.assertEqual(self.consultar_pendientes(parametros={'periodo': 'no-numero'}).status_code, 400)
        self.assertEqual(self.consultar_pendientes('list', metodo='post').status_code, 405)
        self.assertEqual(AvanceAsignaturaSwa.objects.get().estado_origen, 'C')

    def test_ERP_editado_no_se_sobreescribe_analisis_detecta_conflicto(self):
        fuente = self.escenario()
        self.cargar(fuente)
        nota = InscripcionHistoricaErp.objects.get()
        InscripcionHistoricaErp.objects.filter(pk=nota.pk).update(definitiva=Decimal('4.90'))
        fuente.registros[0][0]['definitiva']=4.1
        fuente.registros[0][1][0]['nota']=4.1
        self.assertEqual(self.cargar(fuente,False)['pendientes'],1)
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        nota.refresh_from_db()
        self.assertEqual(nota.definitiva,Decimal('4.90'))
        self.assertEqual(CalificacionSwa.objects.get(tipo_nota_swa=1).nota_origen,'4.1')

    def test_nota_retornada_a_pendiente_o_retirada_no_borra_ERP(self):
        fuente = self.escenario()
        self.cargar(fuente)
        fuente.registros[0][0]['estado_definitiva'] = None
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertEqual(InscripcionHistoricaErp.objects.get().definitiva, Decimal('3.15'))
        fuente.registros[0][1].pop()
        self.assertEqual(self.cargar(fuente,False)['pendientes'],1)
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertEqual(CalificacionSwa.objects.count(),3)

    def test_analisis_no_escribe_ni_reserva_identificadores(self):
        fuente=self.escenario()
        with CaptureQueriesContext(connection) as consultas:
            resumen=self.cargar(fuente,False)
        self.assertEqual(resumen['proyectables'],1)
        self.assertEqual(resumen['migrados'],0)
        self.assertFalse(AvanceAsignaturaSwa.objects.exists())
        self.assertFalse(PensumErp.objects.exists())
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(
            ('INSERT','UPDATE','DELETE','ALTER','CREATE','TRUNCATE')) or 'nextval' in q['sql'].lower() for q in consultas))

    def test_pensum_actualizado_preserva_id_y_adopta_codigo_SWA(self):
        fuente=self.escenario()
        pensum=PensumErp.objects.create(programa_id=27,codigo='ALFA',periodo=self.periodo)
        materia=PensumAsignaturaErp.objects.create(pensum=pensum,asignatura=self.asignatura,semestre=2)
        self.assertEqual(self.cargar(fuente)['migrados'],1)
        pensum.refresh_from_db(); materia.refresh_from_db()
        self.assertEqual(pensum.codigo,'2701')
        self.assertEqual(materia.semestre,1)
        self.assertEqual(EquivalenciaPensumSwa.objects.get().pensum_erp_id,pensum.pk)
        self.assertEqual(self.asignatura.pk,AsignaturaErp.objects.get(codigo='100').pk)

    def test_malla_editada_ERP_deja_pendiente(self):
        fuente=self.escenario()
        self.cargar(fuente)
        PensumAsignaturaErp.objects.update(semestre=6)
        self.assertEqual(self.cargar(fuente,False)['pendientes'],1)
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertEqual(PensumAsignaturaErp.objects.get().semestre,6)

    def test_requisitos_y_correquisitos_conservados(self):
        fuente=self.escenario([intento(),intento(asignatura=101)])
        for nombre in ('prerrequisitos','correquisitos'):
            fuente.fuente[nombre]=[dict(programa=27,pensum=2701,asignatura=101,requisito=100)]
        self.cargar(fuente)
        self.assertEqual(set(RestriccionPensumErp.objects.values_list('tipo',flat=True)),
                         {'PRERREQUISITO','CORREQUISITO'})

    def test_materia_fuera_de_malla_no_inventa_version(self):
        fuente=self.escenario()
        fuente.fuente['materias']=[]
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertFalse(InscripcionErp.objects.exists())

    def test_programas_50_y_60_tambien_migran_notas(self):
        p=persona_swa()
        fuente=self.escenario([intento(programa=50,estudiante='EXT50'),intento(programa=60,estudiante='EXT60')],
            [estudiante_swa(p,'EXT50',50),estudiante_swa(p,'EXT60',60)])
        self.assertEqual(self.cargar(fuente)['migrados'],2)
        self.assertEqual(set(InscripcionHistoricaErp.objects.values_list('programa_id',flat=True)),{50,60})

    def test_ingles_secundario_con_notas_en_su_programa(self):
        p=persona_swa()
        fuente=self.escenario([intento(),intento(programa=51,estudiante='ING',asignatura=101)],
            [estudiante_swa(p),estudiante_swa(p,'ING',51)])
        self.assertEqual(self.cargar(fuente)['migrados'],2)
        self.assertEqual(VinculoPersona.objects.get().programa_secundario_id,51)
        self.assertEqual(InscripcionHistoricaErp.objects.get(programa_id=51).persona_id,7)
        self.assertEqual(MatriculaErp.objects.get(programa_id=51).programa_id,51)

    def test_futuro_y_modulo_incompatible_quedan_trazados(self):
        p=persona_swa()
        fuente=self.escenario([intento(periodo=20271,programa=52,nivel=7)],
            [estudiante_swa(p,programa=52)])
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertFalse(InscripcionErp.objects.exists())
        self.assertEqual(CalificacionSwa.objects.count(),3)

    def test_conflicto_identidad_no_reasigna_persona(self):
        fuente=self.escenario()
        EquivalenciaPersonaSwa.objects.update(persona_erp_id=99)
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertFalse(MatriculaErp.objects.exists())

    def test_version_aprobada_y_codigos_colisionados(self):
        fuente=self.escenario()
        ProgramaErp.objects.create(id=29,nombre='Radiologia',sucursal_id=1)
        p=PensumErp.objects.create(programa_id=29,codigo='RAD',periodo=self.periodo)
        fuente.fuente['pensums'] += [dict(programa=29,codigo=c,estado='A') for c in (2902,2903)]
        fuente.fuente['primeros_periodos'] += [dict(programa=29,pensum=c,periodo=20261) for c in (2902,2903)]
        servicio=CatalogosAcademicos(fuente.fuente)
        self.assertEqual(servicio.aplicar((29,2903)).pk,p.pk)
        self.assertNotEqual(servicio.aplicar((29,2902)).pk,p.pk)
        self.assertEqual(PensumErp.objects.get(pk=p.pk).codigo,'2903')
        fuente.fuente['pensums'] += [dict(programa=c,codigo=1,estado='A') for c in (56,67,68)]
        servicio=CatalogosAcademicos(fuente.fuente)
        self.assertEqual([servicio.codigo((c,1)) for c in (56,67,68)],['56-1','67-1','68-1'])

    def test_comandos_solo_aplican_con_flag_explicito(self):
        with patch('api.management.commands.migrar_notas_swa.procesar_notas_swa', return_value={}) as procesar:
            call_command('migrar_notas_swa',stdout=StringIO())
            self.assertFalse(procesar.call_args.kwargs['aplicar'])
            call_command('migrar_notas_swa','--aplicar','--programa','51','--periodo','20262',stdout=StringIO())
            self.assertEqual(procesar.call_args.kwargs,dict(aplicar=True,programa=51,periodo=20262))
        with patch('api.notas_swa.procesar_notas_swa',return_value={}) as procesar:
            call_command('analizar_migracion_swa','--notas','--programa','50',stdout=StringIO())
            self.assertEqual(procesar.call_args.kwargs,dict(programa=50,periodo=None))

    def test_asignaturas_incrementales_sin_vaciar_ni_cambiar_id(self):
        from scripts import migrar_swa
        class Cursor:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self,sql):
                assert sql.startswith('SELECT')
            def fetchall(self): return [(100,'Cambio','T',3,4),(101,'Nueva','T',2,2)]
        with patch.object(migrar_swa.connections['produccion'],'cursor',return_value=Cursor()), CaptureQueriesContext(connection) as consultas:
            resumen=migrar_swa.migrar_asignaturas()
        self.assertEqual(resumen['asignaturas']['creadas'],1)
        self.assertEqual(AsignaturaErp.objects.get(codigo='100').pk,self.asignatura.pk)
        self.assertEqual(AsignaturaErp.objects.get(codigo='100').nombre,'Materia')
        self.assertFalse(any('TRUNCATE' in q['sql'].upper() for q in consultas))

    def test_decimales_invalidos_y_esquemas_desconocidos_no_se_inventan(self):
        for valor in ('nan','inf','no-numero',6,-1):
            with self.assertRaises(ValueError): nota_decimal(valor)
        r,notas=intento()
        notas.append({**notas[0],'tipo_nota':0})
        with self.assertRaises(ValueError): clasificar_notas(r,notas,{1:[1,2,3]})

    def test_preflight_falla_antes_de_abrir_fuente(self):
        with patch('api.notas_swa.validar_esquema_destino',side_effect=ValueError('Esquema incompatible')), \
             patch('api.notas_swa.LectorNotasSwa') as lector:
            with self.assertRaises(ValueError): procesar_notas_swa(aplicar=True)
            lector.assert_not_called()

    def test_cambio_de_esquema_permanece_pendiente_en_reintentos(self):
        fuente=self.escenario()
        self.cargar(fuente)
        fuente.registros[0]=intento(esquema=0)
        for _ in range(2):
            self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertEqual(self.cargar(fuente,False)['pendientes'],1)
        self.assertEqual(InscripcionHistoricaErp.objects.count(),1)

    def test_definitiva_directa_e_historica_no_se_borran_si_vuelven_a_pendiente(self):
        fuente = self.escenario([intento(esquema=0)])
        self.assertEqual(self.cargar(fuente)['migrados'],1)
        fuente.registros[0][0]['definitiva'] = None
        fuente.registros[0][1][0]['estado'] = 0
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertEqual(InscripcionHistoricaErp.objects.get().definitiva,Decimal('3.15'))

    def test_inscripcion_sin_equivalencia_no_se_adopta_por_coincidencia(self):
        fuente=self.escenario()
        self.cargar(fuente)
        avance=AvanceAsignaturaSwa.objects.get()
        AvanceAsignaturaSwa.objects.filter(pk=avance.pk).update(inscripcion_historica_erp_id=None,ultimo_importado={})
        self.assertEqual(self.cargar(fuente,False)['pendientes'],1)
        self.assertEqual(self.cargar(fuente)['pendientes'],1)
        self.assertEqual(InscripcionHistoricaErp.objects.count(),1)

    def test_analisis_con_auxiliares_aun_sin_crear_y_filtros_invalidos(self):
        fuente=self.escenario()
        tablas_original=connection.introspection.table_names
        auxiliares={m._meta.db_table for m in self.auxiliares}
        with patch.object(connection.introspection,'table_names',side_effect=lambda *a,**k:
            [t for t in tablas_original(*a,**k) if t not in auxiliares]):
            resumen=self.cargar(fuente,False)
            self.assertTrue(resumen['tablas_auxiliares_pendientes'])
            self.assertEqual(resumen['pendientes'],1)
        for opciones in (dict(programa=-1),dict(periodo=20263)):
            with patch('api.notas_swa.LectorNotasSwa') as lector:
                with self.assertRaises(ValueError): procesar_notas_swa(**opciones)
                lector.assert_not_called()

    @skipUnless(connection.vendor == 'postgresql', 'Requiere el PostgreSQL temporal')
    def test_bloqueo_de_cargas_simultaneas(self):
        intentando = Event()

        def intentar():
            try:
                intentando.set()
                with sesion_destino(True):
                    return 'admitido'
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with sesion_destino(True):
                segunda = pool.submit(intentar)
                self.assertTrue(intentando.wait(5))
                self.assertFalse(segunda.done())
            self.assertEqual(segunda.result(timeout=15), 'admitido')

    @skipUnless(connection.vendor == 'postgresql', 'Requiere el PostgreSQL temporal')
    def test_SQL_fuente_real_sin_asignatura_nivel_y_lotes_filtrados(self):
        from api.fuente_notas_swa import LectorNotasSwa, CAMPOS_REGISTRO, CAMPOS_CALIFICACION
        from api.programas_swa import cargar_academico
        tablas = {
            'programa':'codigo integer,sede integer',
            'estudiante':'codigo text,pensum integer',
            # Deliberadamente sin nivel: el modulo viene del registro y de la malla.
            'asignatura':'programa integer,codigo integer,nombre text,tipo text,ihs integer',
            'pensum':'programa integer,codigo integer,descripcion text,niveles integer,estado text',
            'pensum_asignatura':'programa integer,pensum integer,asignatura integer,nivel integer,creditos integer',
            'prerequisito':'programa integer,pensum integer,asignatura integer,prerequisito integer',
            'correquisito':'programa integer,pensum integer,asignatura integer,correquisito integer',
            'tipo_nota':'codigo integer,descripcion text,porcentaje double precision',
            'grupo_tipo_nota':'grupo_nota integer,tipo_nota integer',
            'historico':'periodo integer,programa integer,estudiante text,estado text,nivel integer',
            'grupo':'periodo integer,programa integer,asignatura integer,codigo integer,grupo_nota integer',
        }
        textos={'estudiante','estado_definitiva','estado','promediar','permanencia','bloqueado','observacion'}
        def columnas(campos):
            return ','.join(c+' '+('text' if c in textos else 'double precision'
                if c=='definitiva' or c=='nota' or c.startswith(('nota_','porc_')) else 'integer') for c in campos)
        tablas['registro']=columnas(CAMPOS_REGISTRO)
        tablas['calificacion']=columnas(CAMPOS_CALIFICACION)
        registros = [intento(), intento(periodo=20262),
                     intento(periodo=20262, programa=50, estudiante='EXT')]
        try:
            # Son tablas TEMP de la conexion default de pruebas, nunca del alias SWA.
            with connection.cursor() as cur:
                for nombre,campos in tablas.items():
                    cur.execute(f'CREATE TEMP TABLE {nombre} ({campos})')
                cur.execute("INSERT INTO programa VALUES (27,1),(50,1)")
                cur.execute("INSERT INTO estudiante VALUES ('SWA-A',2701),('EXT',5001)")
                cur.execute("INSERT INTO asignatura VALUES (27,100,'Materia','T',2),(50,100,'Materia','T',2)")
                cur.execute("INSERT INTO pensum VALUES (27,2701,'Carrera',6,'A'),(50,5001,'Extension',6,'A')")
                cur.execute('INSERT INTO pensum_asignatura VALUES (27,2701,100,1,2),(50,5001,100,1,2)')
                cur.execute("INSERT INTO tipo_nota VALUES (0,'Definitiva',1),(1,'Primero',.3),(2,'Segundo',.3),(3,'Final',.4)")
                cur.execute('INSERT INTO grupo_tipo_nota VALUES (0,0),(1,1),(1,2),(1,3)')
                for r,notas in registros:
                    cur.execute('INSERT INTO registro ('+','.join(CAMPOS_REGISTRO)+') VALUES ('+
                        ','.join(['%s']*len(CAMPOS_REGISTRO))+')',[r.get(c) for c in CAMPOS_REGISTRO])
                    cur.execute('INSERT INTO historico VALUES (%s,%s,%s,%s,1)',
                        [r['periodo'],r['programa'],r['estudiante'],'M'])
                    cur.execute('INSERT INTO grupo VALUES (%s,%s,%s,%s,1)',
                        [r['periodo'],r['programa'],r['asignatura'],r['grupo']])
                    for n in notas:
                        cur.execute('INSERT INTO calificacion ('+','.join(CAMPOS_CALIFICACION)+') VALUES ('+
                            ','.join(['%s']*len(CAMPOS_CALIFICACION))+')',[n.get(c) for c in CAMPOS_CALIFICACION])
            # Sustituimos toda conexion fuente por el default temporal ya creado.
            with patch('api.fuente_notas_swa.connections',{'produccion':connection}), \
                 patch('api.programas_swa.connections',{'produccion':connection}), \
                 patch('api.fuente_notas_swa.cargar_grupos',return_value=({}, {}, [])):
                lector=LectorNotasSwa()
                with lector.lectura(), CaptureQueriesContext(connection) as consultas:
                    self.assertTrue(connection.get_autocommit())
                    self.assertEqual(len(lector.catalogos()['asignaturas']),2)
                    self.assertEqual(len([r for lote in lector.lotes(1) for r in lote]),3)
                    filtrados=list(LectorNotasSwa(50,20262).lotes(1))
                    self.assertEqual(len(filtrados),1)
                    self.assertEqual(len(filtrados[0][0][1]),3)
                    self.assertEqual(len(cargar_academico()[2]),3)
                self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in consultas))
        finally:
            with connection.cursor() as cur:
                for nombre in reversed(tablas):
                    cur.execute(f'DROP TABLE IF EXISTS pg_temp.{nombre}')
