from rest_framework import viewsets

from .models import Ubicacion
from .serializers import UbicacionSerializer


class UbicacionViewSet(viewsets.ReadOnlyModelViewSet):
    """Registro de estudiantes ya ubicados. Filtra por ?codigo=XXXX."""

    queryset = Ubicacion.objects.all()
    serializer_class = UbicacionSerializer

    def get_queryset(self):
        qs = self.queryset
        codigo = self.request.query_params.get('codigo', '').strip()
        if codigo:
            qs = qs.filter(codigo_estudiante=codigo)
        return qs
