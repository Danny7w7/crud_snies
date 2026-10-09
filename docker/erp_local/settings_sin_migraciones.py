"""Settings temporales para levantar el backend del ERP en local.

Las carpetas migrations/ del repo estan vacias y la base (restauracion de produccion)
ya tiene el esquema completo. Se desactivan las migraciones de las apps propias del
proyecto para que runserver no las valide. No cambia el esquema ni el repo.
"""
from pathlib import Path

from ERP_UNICORSALUD.settings import *  # noqa: F401,F403
from ERP_UNICORSALUD.settings import BASE_DIR, INSTALLED_APPS

MIGRATION_MODULES = {
    app.split('.')[0]: None
    for app in INSTALLED_APPS
    if (Path(BASE_DIR) / app.split('.')[0]).is_dir()
}

# En la verificacion contra la copia no hay Redis: cache en memoria (throttling, etc.).
import os  # noqa: E402

if os.getenv('CACHE_LOCAL') == '1':
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
