"""Entorno aislado: nunca importa settings.py, .env ni credenciales del SWA."""

import os

SECRET_KEY = 'solo-pruebas-aisladas'
MIGRADOR_PRUEBAS_AISLADAS = True
MIGRADOR_DESTINO_NOMBRE = 'migrador_pruebas'
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes',
                  'rest_framework', 'api', 'ubicacion']
DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
    # Cualquier intento de abrir SWA falla; solo se sustituyen sus lecturas con mocks.
    'produccion': {'ENGINE': 'django.db.backends.dummy', 'NAME': 'NO_CONECTAR_SWA'},
}
if os.getenv('MIGRADOR_TEST_POSTGRES') == '1':
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': 'migrador_pruebas',
        'HOST': '127.0.0.1', 'PORT': '55439', 'USER': 'crud_pruebas',
        'TEST': {'NAME': 'test_codigos_persona'},
    }
DATABASE_ROUTERS = ['config.routers.ProductionRouter']
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'America/Bogota'
PROGRAMAS_EXCLUIDOS = ()
ROOT_URLCONF = 'config.test_urls'
MIDDLEWARE = []

# Corte de la migracion, independiente de los multiples periodos activos.
PERIODO_ACTUAL_MIGRACION_SWA = 20262
SEDES_SWA_A_ERP = {1: 1, 3: 2}
