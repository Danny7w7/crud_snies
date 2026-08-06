import hmac
import time

from django.conf import settings
from django.db import connections
from django.db.models import BooleanField, Case, Exists, OuterRef, Q, Value, When
from rest_framework import viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission

from .models import Departamento, Estudiante, EstudianteSwa, Historico, Municipio, Programa
from .serializers import (
    DepartamentoSerializer,
    EstudianteSerializer,
    MunicipioSerializer,
    PersonaSerializer,
    ProgramaSerializer,
)

# Programas (extension/idiomas) que no deben aparecer en la consulta.
PROGRAMAS_EXCLUIDOS = settings.PROGRAMAS_EXCLUIDOS

# Los datos de 'historico' (tabla SWA) no se actualizan, por lo que el set de
# codigos de reintegro puede cachearse por mucho tiempo en memoria.
_REINTEGRO_CACHE_TTL = 3600
_reintegro_cache = {'timestamp': 0, 'codes': None}


class ConsultaPasswordPermission(BasePermission):
    """Exige la contraseña de la vista de consulta via header X-Consulta-Password.

    El cliente envia el hash SHA-256 de la contraseña, que se compara con el
    hash quemado en settings.CONSULTA_PASSWORD_HASH.
    """

    message = 'Contraseña de consulta inválida'

    def has_permission(self, request, view):
        received = request.headers.get('X-Consulta-Password', '')
        expected = settings.CONSULTA_PASSWORD_HASH or ''
        if not received or not expected:
            return False
        return hmac.compare_digest(received, expected)


class EstudianteViewSet(viewsets.ModelViewSet):
    """CRUD local de estudiantes, sin borrado y solo para estudiantes
    que existen en la base institucional (validado en el serializer).

    Excluye los programas de extension/idiomas (no se listan ni editan)."""

    queryset = Estudiante.objects.exclude(
        programa__in=[str(p) for p in PROGRAMAS_EXCLUIDOS]
    )
    serializer_class = EstudianteSerializer
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']


class MunicipioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Municipio.objects.all()
    serializer_class = MunicipioSerializer


class DepartamentoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Departamento.objects.all()
    serializer_class = DepartamentoSerializer


class PersonaPageNumberPagination(PageNumberPagination):
    page_size = 20


def _reintegro_codes():
    """Codigos de estudiante que son reintegro: hay un gap de >=1 semestre
    entre periodos de matricula REAL (estado 'M') en la tabla historico.

    El estado 'P' (pagado sin matricula) se excluye porque no representa una
    matricula real y no debe influir en la deteccion.

    El resultado se cachea en memoria (_REINTEGRO_CACHE_TTL segundos) porque
    la tabla 'historico' de la base SWA no se actualiza.
    """
    now = time.time()
    cached = _reintegro_cache['codes']
    if cached is not None and now - _reintegro_cache['timestamp'] < _REINTEGRO_CACHE_TTL:
        return cached

    sql = """
        SELECT DISTINCT estudiante FROM (
            SELECT estudiante, periodo,
                   LAG(periodo) OVER (PARTITION BY estudiante ORDER BY periodo) AS prev
            FROM historico
            WHERE estado = 'M'
        ) t
        WHERE t.prev IS NOT NULL
          AND ((t.periodo / 10) * 2 + (t.periodo % 10 - 1))
              - ((t.prev / 10) * 2 + (t.prev % 10 - 1)) > 1
    """
    with connections['produccion'].cursor() as cursor:
        cursor.execute(sql)
        codes = {row[0] for row in cursor.fetchall()}

    _reintegro_cache['timestamp'] = now
    _reintegro_cache['codes'] = codes
    return codes


class PersonaViewSet(viewsets.ReadOnlyModelViewSet):
    """Consulta desde la tabla 'estudiante' de produccion hacia 'persona'."""

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
        reintegro = _reintegro_codes()
        has_matricula = Historico.objects.filter(
            estudiante=OuterRef('codigo'), estado='M'
        )
        qs = (
            self.queryset
            .filter(Exists(has_matricula))
            .annotate(
                es_reintegro=Case(
                    When(codigo__in=reintegro, then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                )
            )
        )

        search = self.request.query_params.get('q', '').strip()
        if search:
            qs = qs.filter(
                Q(codigo__icontains=search)
                | Q(persona__nombre__icontains=search)
                | Q(persona__apellido__icontains=search)
                | Q(persona__identificacion__icontains=search)
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
    """Catalogo de programas activos de la base de produccion.

    Excluye los programas de extension/idiomas que no deben registrarse.
    """

    queryset = (
        Programa.objects.filter(activo=1)
        .exclude(codigo__in=PROGRAMAS_EXCLUIDOS)
    )
    serializer_class = ProgramaSerializer
