"""Verifica, con el código del ERP, que los datos migrados de SWA funcionan en el ERP.

Se ejecuta dentro del contenedor del ERP apuntando a la copia `erp_migracion` (ver
docker-compose.verificacion.yml). Todo corre en una transacción que se deshace al final:
la copia queda intacta.

    python manage.py shell < /opt/erp_override/verificar_migracion.py
"""
from django.db import transaction
from rest_framework.test import APIRequestFactory, force_authenticate

from academico.models import HomologacionIngles, Matricula
from academico.serializers import HomologacionInglesSerializer
from academico.services import asignaturas_aprobadas
from academico.views.estudiante_views import EstudianteViewSet
from admisiones.services import asegurar_matricula_academica, asegurar_usuario_estudiante
from erp.models import Persona, VinculoPersona
from erp.pdf_taf_services import FinancialService, PDFGeneratorService
from erp.services import PersonaService, es_estudiante_registrado
from erp.views.persona_views import PersonaViewSet

ESTUDIANTE_CARRERA = 2920142024   # Radiología, varias matrículas
ESTUDIANTE_INGLES = 2420202018    # Centro de Idiomas, módulos aprobados
ESTUDIANTE_EQUIVALENCIA = 2920212007  # aprobó Bioquímica (2902) y cursa el pensum 2903

resultados = []


def check(clave, ok, detalle):
    resultados.append((clave, ok, detalle))
    print(f"[{'OK ' if ok else 'FALLA'}] {clave}: {detalle}")


factory = APIRequestFactory()


def llamar(viewset, accion, user=None, pk=None, metodo='get', **params):
    vista = viewset.as_view({metodo: accion})
    request = factory.get('/', params)
    if user:
        force_authenticate(request, user=user)
    return vista(request, pk=pk) if pk is not None else vista(request)


with transaction.atomic():
    p = Persona.objects.get(id=ESTUDIANTE_CARRERA)
    print(f'Estudiante: {p.id} {p.nombre_completo}')

    # D. Cuenta de usuario
    info = asegurar_usuario_estudiante(p)
    user = info['user']
    check('D usuario estudiante', user.roles.filter(nombre_rol__iexact='Estudiante').exists(),
          f'username={user.username}, creado={info["usuario_creado"]}')

    # A. Historial
    r = llamar(EstudianteViewSet, 'matriculas', user, historial=1)
    mats = r.data['data']
    check('A matriculas con historial', r.status_code == 200 and len(mats) > 0,
          f'{len(mats)} programa(s): {[m["programa_nombre"] for m in mats]}')
    r_activas = llamar(EstudianteViewSet, 'matriculas', user)
    check('A matriculas activas (sin cambio de contrato)', r_activas.status_code == 200,
          f'{len(r_activas.data["data"])} activas')
    mat_id = mats[0]['id']
    r = llamar(EstudianteViewSet, 'periodos', user, pk=mat_id)
    periodos = r.data['data']
    check('A periodos del programa', len(periodos) > 1, [x['periodo_nombre'] for x in periodos])
    total_notas = 0
    for per in periodos:
        r = llamar(EstudianteViewSet, 'historico_notas', user, pk=mat_id, periodo_id=per['periodo_id'])
        total_notas += len(r.data['data'])
    ejemplo = r.data['data'][:2]
    check('A notas historicas visibles', total_notas > 0,
          f'{total_notas} notas en {len(periodos)} periodos; ej: '
          f'{[(e["asignatura_nombre"], e["definitiva"], e["semestre"]) for e in ejemplo]}')
    aprobadas = asignaturas_aprobadas(p.id, mats[0]['programa_id'])
    check('A prerrequisitos (aprobadas en todo el historial)', len(aprobadas) > 0, f'{len(aprobadas)} asignaturas')
    pdf = PDFGeneratorService.generar_certificado_notas(p).getvalue()
    check('A certificado de notas', len(pdf) > 3000, f'{len(pdf)} bytes')

    # B. Privacidad en enlace publico
    check('B es estudiante registrado', es_estudiante_registrado(p), 'True')
    r = llamar(PersonaViewSet, 'solicitar_acceso', pk=p.id)
    expone = any(k in r.data for k in ('primer_nombre', 'nro_documento', 'email'))
    check('B solicitar-acceso no expone datos', r.data.get('requires_verification') and not expone,
          {k: r.data.get(k) for k in ('requires_verification', 'verificacion_disponible')})

    # C. Admisiones
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("SELECT erp_id FROM migra.mapa WHERE entidad = 'persona' AND creado")
        migrados = [fila[0] for fila in cur.fetchall()]
    en_admisiones = VinculoPersona.objects.filter(
        PersonaService.q_vinculos_gestion_admision(), activo=True,
        persona_id__in=migrados, tipo_vinculo__nombre__iexact='Estudiante')
    check('C Admisiones sin estudiantes migrados', en_admisiones.count() == 0, f'{en_admisiones.count()} en el embudo')

    # E. Rematricula 2027-1
    ultima = Matricula.objects.filter(persona=p, programa_id=29).order_by('-periodo__nombre').first()
    sem = FinancialService._calcular_semestre(p, ultima.programa)
    nueva = asegurar_matricula_academica(persona=p, programa=ultima.programa)
    from django.db.models import Max
    from academico.models import PensumAsignatura as PA
    tope = PA.objects.filter(pensum_id=ultima.pensum_id).aggregate(m=Max('semestre'))['m'] or 99
    esperado = min(ultima.semestre_actual + 1, tope)
    check('E semestre del volante', sem == esperado,
          f'ultima {ultima.periodo.nombre} sem {ultima.semestre_actual} (pensum hasta {tope}) -> volante sem {sem}')
    check('E matricula 2027-1 en su pensum de SWA', nueva is not None and nueva.pensum_id == ultima.pensum_id,
          f'pensum anterior {ultima.pensum.codigo} -> nuevo {nueva.pensum.codigo if nueva else None}')
    check('E matricula 2027-1 con semestre siguiente',
          nueva is not None and nueva.semestre_actual == esperado,
          f'{nueva.periodo.nombre if nueva else None} sem {nueva.semestre_actual if nueva else None}, '
          f'pensum {nueva.pensum.codigo if nueva else None}')

    # Equivalencias SWA unificadas: Bioquímica (2902) = Biología Celular (2903)
    from academico.models import Asignatura, PensumAsignatura
    pe = Persona.objects.get(id=ESTUDIANTE_EQUIVALENCIA)
    canonica = Asignatura.objects.get(codigo='29030112')
    en_pensums = sorted(PensumAsignatura.objects.filter(asignatura=canonica).values_list('pensum__codigo', flat=True))
    check('EQ asignatura unificada en ambos pensums', set(en_pensums) >= {'SWA-29-2902', 'SWA-29-2903'},
          f'{canonica.codigo} {canonica.nombre} -> {en_pensums}')
    check('EQ codigo equivalente no se crea aparte', not Asignatura.objects.filter(codigo='29020121').exists(), '29020121 no existe')
    check('EQ aprobada cuenta en el pensum 2903', canonica.id in asignaturas_aprobadas(pe.id),
          'Bioquímica aprobada = Biología Celular aprobada')

    # G. Ingles
    pi = Persona.objects.get(id=ESTUDIANTE_INGLES)
    homs = HomologacionIngles.objects.filter(persona=pi)
    check('G homologaciones serializan', all(HomologacionInglesSerializer(h).data['motivo'] == 'MIGRACION_SWA' for h in homs),
          f'{homs.count()} modulos')
    disp = FinancialService.ingles_disponible(pi.id)
    estados = [(m['modulo'], m['estado']) for m in (disp or {}).get('modulos', [])] if isinstance(disp, dict) else disp
    check('G ingles_disponible reconoce modulos', bool(disp), estados)

    transaction.set_rollback(True)

print('\nResumen:', sum(1 for r in resultados if r[1]), 'OK de', len(resultados))
