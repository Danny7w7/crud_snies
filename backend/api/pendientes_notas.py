"""Consulta de notas pendientes sobre los snapshots guardados en default."""

from django.db.models import Case, Count, OuterRef, Prefetch, Q, Subquery, TextField, Value, When
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from ubicacion.models import AvanceAsignaturaSwa, CalificacionSwa
from .modelos_academicos import AsignaturaErp
from .models import ProgramaErp
from .motivos_notas import MOTIVO_ESTADO_REGISTRO, MOTIVO_ESTADO_REGISTRO_ANTIGUO
from .views import ConsultaPasswordPermission


class FiltrosPendientesSerializer(serializers.Serializer):
    programa = serializers.IntegerField(required=False)
    periodo = serializers.IntegerField(required=False)
    estado = serializers.CharField(required=False, max_length=50)
    destino = serializers.ChoiceField(required=False, choices=['actual', 'historico', 'pendiente'])
    motivo = serializers.CharField(required=False, max_length=1000)
    q = serializers.CharField(required=False, max_length=100)
    sin_interpretar = serializers.BooleanField(required=False)


class PendienteNotaSerializer(serializers.ModelSerializer):
    programa_nombre = serializers.CharField(read_only=True, allow_null=True)
    asignatura_nombre = serializers.CharField(read_only=True, allow_null=True)
    motivo = serializers.CharField(source='motivo_revision', read_only=True)
    persona_swa_id = serializers.IntegerField(source='asociacion.persona_swa_id', read_only=True)
    persona_erp_id = serializers.IntegerField(source='asociacion.persona_erp_id', read_only=True, allow_null=True)

    class Meta:
        model = AvanceAsignaturaSwa
        fields = ['id', 'periodo_swa', 'programa_swa_id', 'programa_nombre', 'asignatura_swa',
                  'asignatura_nombre', 'grupo_swa', 'codigo_estudiante', 'persona_swa_id',
                  'persona_erp_id', 'modulo', 'estado_origen', 'estado_definitiva_origen',
                  'destino_notas', 'definitiva_origen', 'estado_migracion', 'motivo']
        read_only_fields = fields


class CalificacionPendienteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CalificacionSwa
        fields = ['tipo_nota_swa', 'estado_origen', 'nota_origen', 'porcentaje_origen',
                  'datos_origen', 'nota_erp_id']
        read_only_fields = fields


class DetallePendienteNotaSerializer(PendienteNotaSerializer):
    calificaciones = CalificacionPendienteSerializer(many=True, read_only=True)

    class Meta(PendienteNotaSerializer.Meta):
        fields = [*PendienteNotaSerializer.Meta.fields, 'registro_origen', 'calificaciones',
                  'matricula_erp_id', 'inscripcion_actual_erp_id', 'inscripcion_historica_erp_id']
        read_only_fields = fields


class PendientesPagination(PageNumberPagination):
    page_size = 25


class PendienteNotaViewSet(viewsets.ReadOnlyModelViewSet):
    """Lista, detalle y conteos locales; nunca abre la conexion produccion."""

    permission_classes = [ConsultaPasswordPermission]
    authentication_classes = []
    http_method_names = ['get', 'head', 'options']
    serializer_class = PendienteNotaSerializer
    pagination_class = PendientesPagination

    def base_queryset(self):
        # Tambien incluye conflictos posteriores a una proyeccion ya realizada.
        return AvanceAsignaturaSwa.objects.using('default').filter(
            Q(estado_migracion='pendiente') | ~Q(motivo_pendiente='')
        ).annotate(motivo_revision=Case(
            When(motivo_pendiente=MOTIVO_ESTADO_REGISTRO_ANTIGUO, then=Value(MOTIVO_ESTADO_REGISTRO)),
            default='motivo_pendiente',
            output_field=TextField(),
        ))

    def get_queryset(self):
        filtros = FiltrosPendientesSerializer(data=self.request.query_params)
        filtros.is_valid(raise_exception=True)
        datos = filtros.validated_data
        qs = self.base_queryset()
        for parametro, campo in [('programa', 'programa_swa_id'), ('periodo', 'periodo_swa'),
                                 ('estado', 'estado_origen'), ('destino', 'destino_notas'),
                                 ('motivo', 'motivo_revision')]:
            if parametro in datos:
                qs = qs.filter(**{campo: datos[parametro]})
        if datos.get('sin_interpretar'):
            qs = qs.exclude(estado_origen='R')
        if datos.get('q'):
            texto = datos['q']
            busqueda = (Q(codigo_estudiante__icontains=texto) | Q(asignatura_swa__icontains=texto)
                        | Q(grupo_swa__icontains=texto))
            if texto.isdecimal() and int(texto) <= 9223372036854775807:
                busqueda |= Q(asociacion__persona_swa_id=int(texto))
            qs = qs.filter(busqueda)
        if self.action != 'resumen':
            qs = qs.select_related('asociacion').annotate(
                programa_nombre=Subquery(ProgramaErp.objects.using('default').filter(
                    pk=OuterRef('programa_swa_id')).values('nombre')[:1]),
                asignatura_nombre=Subquery(AsignaturaErp.objects.using('default').filter(
                    codigo=OuterRef('asignatura_swa')).values('nombre')[:1]),
            )
            if self.action == 'retrieve':
                qs = qs.prefetch_related(Prefetch('calificaciones', queryset=
                    CalificacionSwa.objects.using('default').order_by('tipo_nota_swa')))
        return qs.order_by('-periodo_swa', 'programa_swa_id', 'codigo_estudiante', 'asignatura_swa', 'id')

    def get_serializer_class(self):
        return DetallePendienteNotaSerializer if self.action == 'retrieve' else PendienteNotaSerializer

    @action(detail=False, methods=['get'])
    def resumen(self, request):
        base = self.base_queryset().order_by()
        qs = self.get_queryset().order_by()
        programas = dict(ProgramaErp.objects.using('default').values_list('id', 'nombre'))
        por_programa = list(base.values('programa_swa_id').annotate(cantidad=Count('id')).order_by('programa_swa_id'))
        for fila in por_programa:
            fila['nombre'] = programas.get(fila['programa_swa_id'])
        return Response({
            'total_pendientes': base.count(),
            'total_filtrado': qs.count(),
            'sin_interpretar': qs.exclude(estado_origen='R').count(),
            'por_destino': list(qs.values('destino_notas').annotate(cantidad=Count('id')).order_by('destino_notas')),
            'por_motivo': list(qs.values('motivo_revision').annotate(cantidad=Count('id')).order_by('-cantidad', 'motivo_revision')),
            'por_estado': list(qs.values('estado_origen').annotate(cantidad=Count('id')).order_by('estado_origen')),
            'filtros': {
                'programas': por_programa,
                'periodos': list(base.values_list('periodo_swa', flat=True).distinct().order_by('-periodo_swa')),
                'estados': list(base.values_list('estado_origen', flat=True).distinct().order_by('estado_origen')),
                'motivos': list(base.values_list('motivo_revision', flat=True).distinct().order_by('motivo_revision')),
            },
        })
