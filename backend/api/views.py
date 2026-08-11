import hmac
import time

from django.conf import settings
from django.db import connection, connections
from django.db.models import BooleanField, Case, Exists, OuterRef, Q, Subquery, Value, When
from rest_framework import viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission

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

# Programas (extension/idiomas) que no deben aparecer en la consulta.
PROGRAMAS_EXCLUIDOS = settings.PROGRAMAS_EXCLUIDOS

_REINTEGRO_CACHE_TTL = 3600
_reintegro_cache = {'timestamp': 0, 'personas': None}


class ConsultaPasswordPermission(BasePermission):

    message = 'Contraseña de consulta inválida'

    def has_permission(self, request, view):
        received = request.headers.get('X-Consulta-Password', '')
        expected = settings.CONSULTA_PASSWORD_HASH or ''
        if not received or not expected:
            return False
        return hmac.compare_digest(received, expected)


class EstudianteViewSet(viewsets.ModelViewSet):
    """CRUD de estudiantes: validado contra swa y guardado en la ERP local.

    El listado corresponde a los vinculos tipo 'Estudiante' de la ERP,
    excluyendo los programas de extension/idiomas.
    """

    serializer_class = EstudianteSerializer
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = (
            VinculoPersona.objects.filter(tipo_vinculo__nombre__iexact='Estudiante')
            .select_related('persona')
            .order_by('-id')
        )
        excluidos = [str(p) for p in PROGRAMAS_EXCLUIDOS]
        if excluidos:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT id FROM academico_programa WHERE codigo_interno = ANY(%s)',
                    [excluidos],
                )
                ids = [row[0] for row in cursor.fetchall()]
            if ids:
                qs = qs.exclude(programa_id__in=ids)
        return qs


class MunicipioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Municipio.objects.all()
    serializer_class = MunicipioSerializer


class DepartamentoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Departamento.objects.all()
    serializer_class = DepartamentoSerializer


class PersonaPageNumberPagination(PageNumberPagination):
    page_size = 20


def _reintegro_personas():
    """Ids de persona que son reintegro.

    Combina los periodos de matricula REAL (estado 'M') de TODOS los codigos
    de la persona, los ordena y detecta si hay un gap de >=1 semestre entre
    periodos consecutivos. De esta forma una persona que se re-admite con un
    codigo nuevo (reintegro) queda detectada aunque cada codigo por separado
    no presente gaps.

    El estado 'P' (pagado sin matricula) se excluye porque no representa una
    matricula real. El resultado se cachea en memoria porque la tabla
    'historico' de la base SWA no se actualiza.
    """
    now = time.time()
    cached = _reintegro_cache['personas']
    if cached is not None and now - _reintegro_cache['timestamp'] < _REINTEGRO_CACHE_TTL:
        return cached

    sql = """
        SELECT DISTINCT persona FROM (
            SELECT p.id AS persona, h.periodo,
                   LAG(h.periodo) OVER (PARTITION BY p.id ORDER BY h.periodo) AS prev
            FROM estudiante e
            JOIN persona p ON p.id = e.persona
            JOIN historico h ON h.estudiante = e.codigo
            WHERE h.estado = 'M' AND e.persona IS NOT NULL
        ) t
        WHERE t.prev IS NOT NULL
          AND ((t.periodo / 10) * 2 + (t.periodo % 10 - 1))
              - ((t.prev / 10) * 2 + (t.prev % 10 - 1)) > 1
    """
    with connections['produccion'].cursor() as cursor:
        cursor.execute(sql)
        personas = {row[0] for row in cursor.fetchall()}

    _reintegro_cache['timestamp'] = now
    _reintegro_cache['personas'] = personas
    return personas


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
        reintegro_personas = _reintegro_personas()
        codigos_con_M = Historico.objects.filter(estado='M').values('estudiante')

        # Codigo mas reciente (mayor periodo_ingreso) de cada persona que tenga
        # matricula real (M) y no pertenezca a un programa excluido.
        codigo_mas_reciente = (
            EstudianteSwa.objects
            .filter(persona=OuterRef('persona'))
            .exclude(programa__in=PROGRAMAS_EXCLUIDOS)
            .filter(codigo__in=codigos_con_M)
            .order_by('-periodo_ingreso', '-codigo')
            .values('codigo')[:1]
        )

        qs = (
            self.queryset
            .filter(codigo=Subquery(codigo_mas_reciente))
            .annotate(
                es_reintegro=Case(
                    When(persona_id__in=reintegro_personas, then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                )
            )
        )

        search = self.request.query_params.get('q', '').strip()
        if search:
            codigo_coincide = EstudianteSwa.objects.filter(
                persona=OuterRef('persona'), codigo__icontains=search
            )
            qs = qs.filter(
                Q(codigo__icontains=search)
                | Q(persona__nombre__icontains=search)
                | Q(persona__apellido__icontains=search)
                | Q(persona__identificacion__icontains=search)
                | Exists(codigo_coincide)
            )

        reintegro_filter = self.request.query_params.get('reintegro', '').strip()
        if reintegro_filter == '1':
            qs = qs.filter(es_reintegro=True)
        elif reintegro_filter == '0':
            qs = qs.filter(es_reintegro=False)

        tipo = self.request.query_params.get('tipo_identificacion', '').strip()
        if tipo:
            qs = qs.filter(persona__tipo_identificacion=tipo)

        programa = self.request.query_params.get('programa', '').strip()
        if programa:
            qs = qs.filter(programa=programa)

        return qs.order_by('persona__apellido', 'persona__nombre')


class ProgramaViewSet(viewsets.ReadOnlyModelViewSet):

    queryset = (
        Programa.objects.filter(activo=1)
        .exclude(codigo__in=PROGRAMAS_EXCLUIDOS)
    )
    serializer_class = ProgramaSerializer
