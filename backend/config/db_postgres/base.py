"""Backend PostgreSQL compatible con la base de produccion (PostgreSQL 9.4).

Django 5.x exige PostgreSQL 14+, pero la base de produccion es 9.4.26.
Este backend reutiliza la implementacion estandar de Django y solo omite
el chequeo de version, ya que el driver psycopg opera sin problemas contra 9.4.
No altera ningun comportamiento de escritura: la base de produccion sigue
siendo de solo lectura por configuracion (router + modelos managed=False).
"""

from django.db.backends.postgresql.base import DatabaseWrapper as PostgresDatabaseWrapper


class DatabaseWrapper(PostgresDatabaseWrapper):
    def check_database_version_supported(self):
        """La base de produccion corre PostgreSQL 9.4: omitimos la validacion."""
        return None
