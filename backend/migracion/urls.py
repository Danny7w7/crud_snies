from django.urls import path

from . import views
from .anomalias_api import AnomaliasExportarView, AnomaliasGenerarView, AnomaliasResumenView, AnomaliasView
from .duplicados import DuplicadosErpView
from .erp_consulta import (
    EstudianteErpDetalleView,
    EstudiantesErpExportarView,
    EstudiantesErpOpcionesView,
    EstudiantesErpView,
)
from .preparacion import PreparacionItemView, PreparacionView

urlpatterns = [
    path('resumen/', views.ResumenView.as_view()),
    path('resultado/', views.ResultadoView.as_view()),
    path('personas/', views.PersonasView.as_view()),
    path('personas/<str:nid>/', views.PersonaDetalleView.as_view()),
    path('casos/', views.CasosView.as_view()),
    path('casos/resumen/', views.CasosResumenView.as_view()),
    path('casos/sugerencia-masiva/', views.CasosSugerenciaMasivaView.as_view()),
    path('casos/<int:caso_id>/decidir/', views.CasoDecisionView.as_view()),
    path('descartes/', views.DescartesView.as_view()),
    path('anomalias/', AnomaliasView.as_view()),
    path('anomalias/resumen/', AnomaliasResumenView.as_view()),
    path('anomalias/exportar/', AnomaliasExportarView.as_view()),
    path('anomalias/generar/', AnomaliasGenerarView.as_view()),
    path('duplicados-erp/', DuplicadosErpView.as_view()),
    path('erp/estudiantes/', EstudiantesErpView.as_view()),
    path('erp/estudiantes/opciones/', EstudiantesErpOpcionesView.as_view()),
    path('erp/estudiantes/exportar/', EstudiantesErpExportarView.as_view()),
    path('erp/estudiantes/<int:persona_id>/', EstudianteErpDetalleView.as_view()),
    path('preparacion/', PreparacionView.as_view()),
    path('preparacion/<str:clave>/', PreparacionItemView.as_view()),
    path('depurar/', views.DepurarView.as_view()),
    path('enviar/', views.EnviarView.as_view()),
    path('lotes/<int:lote_id>/revertir/', views.RevertirLoteView.as_view()),
]
