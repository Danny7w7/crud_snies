import hmac
import logging
import time

from django.conf import settings
from .codigos_persona import periodo_ingreso_valido
from django.db import connections
from django.db.models import BooleanField, Case, Exists, OuterRef, Q, Subquery, Value, When
from rest_framework import status, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from scripts.migrar_swa import (
    ejecutar_migracion,
    migrar_asignaturas,
    migrar_datos_secundarios,
    migrar_personas,
    migrar_programas,
    migrar_vinculos,
)

# Etapas de migracion expuestas como endpoints propios.
ETAPAS_MIGRACION = {
    'personas': migrar_personas,
    'datos-secundarios': migrar_datos_secundarios,
    'programas': migrar_programas,
    'asignaturas': migrar_asignaturas,
    'vinculos': migrar_vinculos,
}

from .models import (
    Departamento,
    EstudianteSwa,
    Historico,
    Municipio,
    Programa,
    VinculoPersona,
)
from .serializers import (
    DepartamentoSerializer,
    EstudianteSerializer,
    MunicipioSerializer,
    PersonaSerializer,
    ProgramaSerializer,
)

# Programas (configurados como excluidos) que no deben aparecer en la consulta.
PROGRAMAS_EXCLUIDOS = settings.PROGRAMAS_EXCLUIDOS

_REINTEGRO_CACHE_TTL = 3600
_reintegro_cache = {'timestamp': 0, 'personas': None}
logger = logging.getLogger(__name__)


class ConsultaPasswordPermission(BasePermission):

    message = 'Contraseña de consulta inválida'

    def has_permission(self, request, view):
        received = request.headers.get('X-Consulta-Password', '')
        expected = getattr(settings, 'CONSULTA_PASSWORD_HASH', '') or ''
        if not received or not expected:
            return False
        return hmac.compare_digest(received, expected)


class MigracionView(APIView):
    """Ejecuta la migración al ERP local mediante una solicitud autorizada."""

    permission_classes = [ConsultaPasswordPermission]
    authentication_classes = []
    etapa = None

    def post(self, request):
        try:
            if self.etapa is None:
                resumen = ejecutar_migracion()
            else:
                funcion = ETAPAS_MIGRACION.get(self.etapa)
                if funcion is None:
                    return Response(
                        {
                            'ok': False,
                            'error': f'Etapa desconocida: {self.etapa}',
                            'etapas': list(ETAPAS_MIGRACION),
                        },
                        status=status.HTTP_404_NOT_FOUND,
                    )
                resumen = funcion()
        except Exception:
            logger.error('Falló la etapa de migración %s.', self.etapa or 'completa')
            return Response(
                {'ok': False, 'error': 'La migración no pudo completarse. Revisa el destino y los datos de origen.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({'ok': True, 'migracion': resumen})


class EstudianteViewSet(viewsets.ModelViewSet):
    """CRUD de estudiantes: validado contra swa y guardado en la ERP local.

    El listado corresponde a los vinculos tipo 'Estudiante' de la ERP,
    incluyendo carreras, extension e ingles.
    """

    serializer_class = EstudianteSerializer
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']

    def get_queryset(self):
        # El id de academico_programa ES el codigo del programa en el SWA,
        # asi que programa_id se compara directo con PROGRAMAS_EXCLUIDOS.
        qs = (
            VinculoPersona.objects.filter(tipo_vinculo__nombre__iexact='Estudiante')
            .select_related('persona')
            .prefetch_related('equivalencias_swa', 'programas_swa')
            .order_by('-id')
        )
        if PROGRAMAS_EXCLUIDOS:
            qs = qs.exclude(programa_id__in=PROGRAMAS_EXCLUIDOS)
        return qs


class MunicipioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Municipio.objects.all()
    serializer_class = MunicipioSerializer


class DepartamentoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Departamento.objects.all()
    serializer_class = DepartamentoSerializer


class PersonaPageNumberPagination(PageNumberPagination):
    page_size = 20


def _reintegro_personas(grupos=None, estudiantes=None):
    """Interrupciones de matricula por persona canonica y programa."""
    from .codigos_persona import cargar_grupos
    if grupos is None:
        grupos, _, estudiantes = cargar_grupos()
    por_codigo = {e.codigo: e for e in estudiantes}
    periodos = {}
    for h in Historico.objects.using('produccion').filter(estado='M').values(
            'periodo', 'programa', 'estudiante'):
        e = por_codigo.get(h['estudiante'])
        if e is None or e.programa != h['programa']:
            continue
        if not periodo_ingreso_valido(h['periodo']):
            continue
        pk = grupos[e.persona_id].persona_id if e.persona_id in grupos else e.persona_id
        periodos.setdefault((pk, e.programa), set()).add(h['periodo'])
    resultado = set()
    for clave, valores in periodos.items():
        semestres = sorted((p // 10) * 2 + p % 10 - 1 for p in valores)
        if any(b - a > 1 for a, b in zip(semestres, semestres[1:])):
            resultado.add(clave)
    return resultado


class PersonaViewSet(viewsets.ReadOnlyModelViewSet):

    queryset = (
        EstudianteSwa.objects.select_related('persona')
        .exclude(
            Q(persona__isnull=True)
            | Q(codigo__isnull=True)
            | Q(codigo='')
            | Q(programa__in=PROGRAMAS_EXCLUIDOS)
        )
    )
    serializer_class = PersonaSerializer
    permission_classes = [ConsultaPasswordPermission]
    pagination_class = PersonaPageNumberPagination

    def get_queryset(self):
        from .codigos_persona import cargar_grupos, PROGRAMAS_COMPLEMENTARIOS, PROGRAMAS_INGLES
        from ubicacion.models import AsociacionProgramaSwa
        grupos, _, estudiantes = cargar_grupos()
        reintegros = _reintegro_personas(grupos, estudiantes)
        con_m = set(Historico.objects.using('produccion').filter(estado='M')
                    .values_list('estudiante', flat=True))
        relaciones = {}
        for a in AsociacionProgramaSwa.objects.using('default').filter(
                motivo_pendiente='', vinculo_erp__isnull=False).select_related('vinculo_erp').order_by('periodo_swa'):
            relaciones[a.codigo_estudiante] = a
        aliases = {}
        for e in estudiantes:
            pk = grupos[e.persona_id].persona_id if e.persona_id in grupos else e.persona_id
            aliases.setdefault(pk, []).append(e.codigo)
        elegidos = {}
        programa = self.request.query_params.get('programa', '').strip()
        tipo = self.request.query_params.get('tipo_identificacion', '').strip()
        search = self.request.query_params.get('q', '').strip().casefold()
        reintegro = self.request.query_params.get('reintegro', '').strip()
        for e in self.queryset.using('produccion'):
            if programa and str(e.programa) != programa:
                continue
            if e.programa not in PROGRAMAS_COMPLEMENTARIOS and e.codigo not in con_m:
                continue
            grupo = grupos.get(e.persona_id)
            pk = grupo.persona_id if grupo else e.persona_id
            if tipo and str(grupo.tipo_swa if grupo else e.persona.tipo_identificacion) != tipo:
                continue
            textos = [e.persona.nombre, e.persona.apellido, e.persona.identificacion, *aliases.get(pk, [])]
            if search and not any(search in str(t or '').casefold() for t in textos):
                continue
            e.es_reintegro = (pk, e.programa) in reintegros
            if reintegro in ('0', '1') and e.es_reintegro != (reintegro == '1'):
                continue
            e.codigo_persona = str(pk)
            e.tipo_identificacion_canonico = grupo.tipo_swa if grupo else e.persona.tipo_identificacion
            e.codigos_estudiante_swa = sorted(set(aliases.get(pk, [e.codigo])))
            a = relaciones.get(e.codigo)
            e.programa_principal = a.vinculo_erp.programa_id if a else e.programa
            e.programa_secundario = a.vinculo_erp.programa_secundario_id if a else None
            e.periodo_contexto = a.periodo_swa if a else e.periodo_ingreso
            clave = (pk, e.programa)
            anterior = elegidos.get(clave)
            if anterior is None or (e.periodo_ingreso or 0, e.codigo) > (anterior.periodo_ingreso or 0, anterior.codigo):
                elegidos[clave] = e
        return sorted(elegidos.values(), key=lambda e: (
            e.persona.apellido or '', e.persona.nombre or '', e.programa or 0))


class ProgramaViewSet(viewsets.ReadOnlyModelViewSet):

    queryset = (
        Programa.objects.filter(activo=1)
        .exclude(codigo__in=PROGRAMAS_EXCLUIDOS)
    )
    serializer_class = ProgramaSerializer
