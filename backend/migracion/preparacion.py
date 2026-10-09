"""Checklist de preparacion del ERP antes del envio real (migra.preparacion)."""
from rest_framework import status
from rest_framework.response import Response

from .conexiones import conectar_destino
from .views import MigracionView, _consulta, _uno


class PreparacionView(MigracionView):
    def get(self, request):
        return Response(_consulta('SELECT * FROM migra.preparacion ORDER BY orden'))


class PreparacionItemView(MigracionView):
    def post(self, request, clave):
        if not _uno('SELECT clave FROM migra.preparacion WHERE clave = %s', (clave,)):
            return Response({'detail': 'No existe'}, status=status.HTTP_404_NOT_FOUND)
        with conectar_destino() as conn:
            conn.execute("""
                UPDATE migra.preparacion SET hecho = %s, nota = %s, actualizado_en = now() WHERE clave = %s
            """, (bool(request.data.get('hecho')), request.data.get('nota') or '', clave))
        return Response(_uno('SELECT * FROM migra.preparacion WHERE clave = %s', (clave,)))
