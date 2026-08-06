import hmac

from django.conf import settings
from django.db.models import OuterRef, Q, Subquery
from rest_framework import viewsets
from rest_framework.permissions import BasePermission

from .models import Departamento, Estudiante, EstudianteSwa, Municipio, Persona
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


class PersonaViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Persona.objects.all()
    serializer_class = PersonaSerializer
    permission_classes = [ConsultaPasswordPermission]

    def get_queryset(self):
        personas_estudiantes = EstudianteSwa.objects.values('persona')
        codigos = (
            EstudianteSwa.objects.filter(persona=OuterRef('pk'))
            .exclude(Q(codigo__isnull=True) | Q(codigo=''))
            .values('codigo')[:1]
        )
        qs = Persona.objects.filter(id__in=personas_estudiantes).annotate(
            codigo_estudiante=Subquery(codigos)
        )

        search = self.request.query_params.get('q', '').strip()
        if not search:
            return qs.order_by('apellido', 'nombre')

        return qs.filter(
            Q(nombre__icontains=search)
            | Q(apellido__icontains=search)
            | Q(identificacion__icontains=search)
            | Q(codigo_estudiante__icontains=search)
        ).order_by('apellido', 'nombre')
