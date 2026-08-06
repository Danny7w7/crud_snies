import hmac

from django.conf import settings
from django.db.models import Q
from rest_framework import viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission

from .models import Departamento, Estudiante, EstudianteSwa, Municipio
from .serializers import (
    DepartamentoSerializer,
    EstudianteSerializer,
    MunicipioSerializer,
    PersonaSerializer,
)


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
    queryset = Estudiante.objects.all()
    serializer_class = EstudianteSerializer


class MunicipioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Municipio.objects.all()
    serializer_class = MunicipioSerializer


class DepartamentoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Departamento.objects.all()
    serializer_class = DepartamentoSerializer


class PersonaPageNumberPagination(PageNumberPagination):
    page_size = 20


class PersonaViewSet(viewsets.ReadOnlyModelViewSet):
    """Consulta desde la tabla 'estudiante' de produccion hacia 'persona'."""

    queryset = (
        EstudianteSwa.objects.select_related('persona')
        .exclude(Q(persona__isnull=True) | Q(codigo__isnull=True) | Q(codigo=''))
    )
    serializer_class = PersonaSerializer
    permission_classes = [ConsultaPasswordPermission]
    pagination_class = PersonaPageNumberPagination

    def get_queryset(self):
        search = self.request.query_params.get('q', '').strip()
        if not search:
            return self.queryset.order_by('persona__apellido', 'persona__nombre')

        return self.queryset.filter(
            Q(codigo__icontains=search)
            | Q(persona__nombre__icontains=search)
            | Q(persona__apellido__icontains=search)
            | Q(persona__identificacion__icontains=search)
        ).order_by('persona__apellido', 'persona__nombre')
