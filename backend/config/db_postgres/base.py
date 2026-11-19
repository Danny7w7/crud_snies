"""Backend Django de solo lectura compatible con SWA (PostgreSQL 9.4)."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.asyncio import async_unsafe

from django.db.backends.postgresql.base import DatabaseWrapper as PostgresDatabaseWrapper
from django.db.backends.postgresql.psycopg_any import get_adapters_template

from .source import ALIAS, SOLO_LECTURA, abrir_swa, parametros_swa


class DatabaseWrapper(PostgresDatabaseWrapper):
    def get_connection_params(self):
        if (self.alias != ALIAS or self.settings_dict.get('AUTOCOMMIT') is not True
                or self.settings_dict.get('ATOMIC_REQUESTS')
                or self.settings_dict.get('OPTIONS')):
            raise ImproperlyConfigured('El origen SWA requiere autocommit y sus opciones de inicio protegidas.')
        parametros = parametros_swa(self.timezone_name or 'UTC')
        parametros['context'] = get_adapters_template(settings.USE_TZ, self.timezone)
        return parametros

    @async_unsafe
    def get_new_connection(self, conn_params):
        return abrir_swa(conn_params)

    def init_connection_state(self):
        # TimeZone y solo lectura se fijan y verifican al abrir la conexión.
        pass

    def ensure_timezone(self):
        return False

    def set_autocommit(self, autocommit, force_begin_transaction_with_broken_autocommit=False):
        if not autocommit:
            raise PermissionError(SOLO_LECTURA)
        return super().set_autocommit(autocommit, force_begin_transaction_with_broken_autocommit)

    @async_unsafe
    def chunked_cursor(self):
        return self.cursor()

    @async_unsafe
    def create_cursor(self, name=None):
        if name is not None:
            raise PermissionError(SOLO_LECTURA)
        return super().create_cursor()

    def _nodb_cursor(self):
        raise PermissionError(SOLO_LECTURA)

    def schema_editor(self, *args, **kwargs):
        raise PermissionError(SOLO_LECTURA)

    def check_database_version_supported(self):
        """La base de produccion corre PostgreSQL 9.4: omitimos la validacion."""
        return None
