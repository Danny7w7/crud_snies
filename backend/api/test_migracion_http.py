from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from .views import MigracionView


@override_settings(CONSULTA_PASSWORD_HASH='autorizacion-sintetica')
class MigracionHttpTests(SimpleTestCase):
    def setUp(self):
        self.solicitudes = APIRequestFactory()
        self.vista = MigracionView.as_view()
        self.url = '/api/migrar/'
        self.sesion = {'HTTP_X_CONSULTA_PASSWORD': 'autorizacion-sintetica'}

    @patch('api.views.ejecutar_migracion')
    def test_get_y_post_sin_autorizacion_no_aplican(self, ejecutar):
        get = self.vista(self.solicitudes.get(self.url, **self.sesion))
        post = self.vista(self.solicitudes.post(self.url))
        self.assertEqual(get.status_code, 405)
        self.assertEqual(post.status_code, 403)
        ejecutar.assert_not_called()

    @patch('api.views.ejecutar_migracion', return_value={'personas': 1})
    def test_post_autorizado_aplica_una_vez(self, ejecutar):
        respuesta = self.vista(self.solicitudes.post(self.url, **self.sesion))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data, {'ok': True, 'migracion': {'personas': 1}})
        ejecutar.assert_called_once_with()
