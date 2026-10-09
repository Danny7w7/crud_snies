"""Enrutador de bases de datos: solo lectura contra la base de produccion."""

PRODUCTION_MODELS = {'persona', 'estudianteswa', 'programa', 'historico'}


class ProductionRouter:
    """Envia los modelos de solo lectura a la base 'produccion'.

    Nunca permite migraciones ni escritura sobre la base de produccion.
    """

    def db_for_read(self, model, **hints):
        if model._meta.model_name in PRODUCTION_MODELS:
            return 'produccion'
        return None

    def db_for_write(self, model, **hints):
        if model._meta.model_name in PRODUCTION_MODELS:
            return 'produccion'
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # 'migracion' es la copia del ERP + esquemas de trabajo: se gestiona con SQL.
        if db in ('produccion', 'migracion'):
            return False
        return None
