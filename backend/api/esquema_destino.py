"""Comprobaciones de lectura antes de cualquier escritura de la migracion."""

from django.db import connections

from config.destino_migracion import verificar_destino
from ubicacion.models import (EquivalenciaEstudianteSwa, EquivalenciaPersonaSwa,
                              AsociacionProgramaSwa, AvanceAsignaturaSwa)
from .models import DatosSecundarios, PersonaErp, ProgramaErp, SucursalErp, TipoVinculo, VinculoPersona


def validar_esquema_destino(etapa='personas', permitir_auxiliares_faltantes=False):
    modelos = [PersonaErp, EquivalenciaPersonaSwa, EquivalenciaEstudianteSwa, AsociacionProgramaSwa]
    if etapa in ('vinculos', 'registro', 'completo', 'notas'):
        modelos += [VinculoPersona, ProgramaErp, SucursalErp, TipoVinculo, AvanceAsignaturaSwa]
    if etapa in ('datos_secundarios', 'registro', 'completo'):
        modelos.append(DatosSecundarios)
    if etapa in ('asignaturas', 'completo'):
        from .modelos_academicos import AsignaturaErp
        modelos = [AsignaturaErp] if etapa == 'asignaturas' else [*modelos, AsignaturaErp]
    if etapa == 'programas':
        modelos = [SucursalErp, ProgramaErp]
    if etapa == 'notas':
        from .modelos_academicos import MODELOS_ACADEMICOS
        from ubicacion.models import (CalificacionSwa, ConfiguracionNotaSwa,
                                      EquivalenciaMatriculaSwa, EquivalenciaPensumSwa)
        modelos += [*MODELOS_ACADEMICOS, CalificacionSwa, ConfiguracionNotaSwa,
                    EquivalenciaMatriculaSwa, EquivalenciaPensumSwa]
    auxiliares = {m._meta.db_table for m in modelos if m._meta.managed}
    destino = connections['default']
    verificar_destino()
    errores = []
    with destino.cursor() as cursor:
        tablas = set(destino.introspection.table_names(cursor))
        for modelo in modelos:
            tabla = modelo._meta.db_table
            if tabla not in tablas:
                if permitir_auxiliares_faltantes and tabla in auxiliares:
                    continue
                errores.append(f'falta la tabla {tabla}')
                continue
            descripcion = destino.introspection.get_table_description(cursor, tabla)
            columnas = {col.name: col for col in descripcion}
            requeridas = {campo.column for campo in modelo._meta.local_fields}
            faltantes = sorted(requeridas - columnas.keys())
            if faltantes:
                errores.append(f'{tabla}: faltan columnas {", ".join(faltantes)}')
            if destino.vendor == 'postgresql' and not modelo._meta.managed:
                sin_valor = sorted(c.name for c in descripcion if c.name not in requeridas
                                   and not c.null_ok and c.default is None and not c.is_autofield)
                if sin_valor:
                    errores.append(f'{tabla}: columnas obligatorias sin valor modelado: {", ".join(sin_valor)}')
            if modelo is ProgramaErp and destino.vendor == 'postgresql':
                sede = columnas.get('sucursal_id')
                restricciones = destino.introspection.get_constraints(cursor, tabla)
                if sede and (sede.null_ok or not any(r.get('columns') == ['sucursal_id']
                          and r.get('foreign_key') == (SucursalErp._meta.db_table, 'id')
                          for r in restricciones.values())):
                    errores.append('academico_programa.sucursal_id debe referenciar academico_sucursal.id')
            if etapa == 'notas' and not modelo._meta.managed:
                for campo in modelo._meta.local_fields:
                    col = columnas.get(campo.column)
                    if col is None:
                        continue
                    if campo.get_internal_type() == 'DecimalField' and destino.vendor == 'postgresql':
                        if col.scale != campo.decimal_places or col.precision != campo.max_digits:
                            errores.append(f'{tabla}.{campo.column}: precision decimal distinta del ERP')
            if modelo is PersonaErp and 'id' in columnas:
                tipo = destino.introspection.get_field_type(columnas['id'].type_code, columnas['id'])
                # SQLite etiqueta cualquier PK entera como AutoField; verificamos su declaracion SQL.
                es_bigint = (tipo == 'BigIntegerField' or destino.vendor == 'sqlite'
                             and str(columnas['id'].type_code).lower() == 'bigint')
                if not es_bigint:
                    errores.append('erp_persona.id debe ser bigint manual, como en el ERP actual')
                restricciones = destino.introspection.get_constraints(cursor, tabla)
                if not any(r.get('unique') and set(r.get('columns') or []) ==
                           {'tipo_documento_id', 'nro_documento'} for r in restricciones.values()):
                    errores.append('falta la unicidad por tipo y numero de documento en erp_persona')
                estado = columnas.get('estado_general')
                if estado and (estado.null_ok or estado.default is not None or
                        not restricciones.get('persona_estado_general_valido', {}).get('check')):
                    errores.append('erp_persona.estado_general debe ser obligatorio, validado y sin valor por defecto')
                if estado and destino.vendor == 'postgresql' and (
                        estado.type_code != 1043 or estado.display_size != 16):
                    errores.append('erp_persona.estado_general debe ser varchar(16)')
        if PersonaErp in modelos and 'erp_tipodocumentoidentidad' not in tablas:
            errores.append('falta la tabla erp_tipodocumentoidentidad')
    if errores:
        raise ValueError('Destino incompatible; no se escribieron datos: ' + '; '.join(errores))
