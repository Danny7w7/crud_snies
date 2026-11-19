"""Lectura paginada de SWA con un SELECT por lote de registros y notas."""

from contextlib import contextmanager
from itertools import groupby

from django.db import connections

from .codigos_persona import cargar_grupos


CLAVE_REGISTRO = ('periodo', 'programa', 'asignatura', 'grupo', 'estudiante')
CAMPOS_REGISTRO = CLAVE_REGISTRO + ('definitiva', 'estado_definitiva', 'estado',
    'nivel', 'creditos', 'tipo_credito', 'promediar', 'permanencia', 'bloqueado')
CAMPOS_CALIFICACION = CLAVE_REGISTRO + ('tipo_nota', 'estado', 'nota', 'observacion',
    'fallas', 'porc_racional', 'porc_operativa', 'porc_emocional', 'porc_conocimientos',
    'nota_racional', 'nota_operativa', 'nota_emocional', 'nota_conocimientos')


def clave_registro(registro):
    return tuple(registro[c] for c in CLAVE_REGISTRO)


class LectorNotasSwa:
    def __init__(self, programa=None, periodo=None):
        self.programa = programa
        self.periodo = periodo

    @contextmanager
    def lectura(self):
        fuente = connections['produccion']
        if fuente.in_atomic_block or not fuente.get_autocommit():
            raise PermissionError('La lectura de notas de SWA requiere autocommit.')
        yield self

    def _consultar(self, sql, campos, parametros=None):
        with connections['produccion'].cursor() as cursor:
            cursor.execute(sql, parametros or [])
            return [dict(zip(campos, fila)) for fila in cursor.fetchall()]

    def catalogos(self):
        grupos, pendientes, estudiantes = cargar_grupos()
        datos = {'grupos': grupos, 'personas_pendientes': pendientes,
                 'estudiantes': {e.codigo: e for e in estudiantes}}
        consultas = (
            ('programas', 'SELECT codigo, sede FROM programa', ('codigo', 'sede')),
            ('asignaturas', 'SELECT programa,codigo,nombre,tipo,ihs FROM asignatura',
                ('programa', 'codigo', 'nombre', 'tipo', 'ihs')),
            ('pensums', 'SELECT programa,codigo,descripcion,niveles,estado FROM pensum',
                ('programa', 'codigo', 'descripcion', 'niveles', 'estado')),
            ('materias', 'SELECT programa,pensum,asignatura,nivel,creditos FROM pensum_asignatura',
                ('programa', 'pensum', 'asignatura', 'nivel', 'creditos')),
            ('prerrequisitos', 'SELECT programa,pensum,asignatura,prerequisito FROM prerequisito',
                ('programa', 'pensum', 'asignatura', 'requisito')),
            ('correquisitos', 'SELECT programa,pensum,asignatura,correquisito FROM correquisito',
                ('programa', 'pensum', 'asignatura', 'requisito')),
            ('tipos', 'SELECT codigo,descripcion,porcentaje FROM tipo_nota',
                ('codigo', 'descripcion', 'porcentaje')),
            ('esquemas', 'SELECT grupo_nota,tipo_nota FROM grupo_tipo_nota ORDER BY tipo_nota',
                ('grupo_nota', 'tipo_nota')),
            ('historicos', 'SELECT periodo,programa,estudiante,estado,nivel FROM historico',
                ('periodo', 'programa', 'estudiante', 'estado', 'nivel')),
            ('primeros_periodos', 'SELECT r.programa,e.pensum,MIN(r.periodo) FROM registro r '
                'JOIN estudiante e ON e.codigo=r.estudiante GROUP BY r.programa,e.pensum',
                ('programa', 'pensum', 'periodo')),
        )
        for nombre, sql, campos in consultas:
            datos[nombre] = self._consultar(sql, campos)
        return datos

    def _filtros(self, alias):
        partes, parametros = [], []
        for campo, valor in (('programa', self.programa), ('periodo', self.periodo)):
            if valor is not None:
                partes.append(f'{alias}.{campo}=%s')
                parametros.append(valor)
        return partes, parametros

    def lotes(self, tamano=500):
        ultima = None
        campos = ','.join(f'r.{c}' for c in CAMPOS_REGISTRO)
        clave_r = '(' + ','.join(f'r.{c}' for c in CLAVE_REGISTRO) + ')'
        orden = ','.join(f'r.{c}' for c in CLAVE_REGISTRO)
        campos_registro = CAMPOS_REGISTRO + ('grupo_nota',)
        campos_notas = ','.join(f'c.{c}' for c in CAMPOS_CALIFICACION)
        vinculo = ' AND '.join(f'r.{c}=c.{c}' for c in CLAVE_REGISTRO)
        while True:
            filtros, parametros = self._filtros('r')
            if ultima is not None:
                filtros.append(clave_r + '>(%s,%s,%s,%s,%s)')
                parametros.extend(ultima)
            where = ' WHERE ' + ' AND '.join(filtros) if filtros else ''
            with connections['produccion'].cursor() as cursor:
                cursor.execute(
                    f'SELECT r.*,{campos_notas} FROM ('
                    f'SELECT {campos},g.grupo_nota FROM registro r LEFT JOIN grupo g '
                    'ON g.periodo=r.periodo AND g.programa=r.programa AND g.asignatura=r.asignatura '
                    f'AND g.codigo=r.grupo{where} ORDER BY {orden} LIMIT %s'
                    f') r LEFT JOIN calificacion c ON {vinculo} ORDER BY {orden},c.tipo_nota',
                    parametros + [tamano],
                )
                filas = cursor.fetchall()
            if not filas:
                return
            lote = []
            for ultima, filas_registro in groupby(filas, lambda fila: fila[:len(CLAVE_REGISTRO)]):
                fila = next(filas_registro)
                registro = dict(zip(campos_registro, fila))
                corte = len(campos_registro)
                notas = []
                if fila[corte] is not None:
                    notas.append(dict(zip(CAMPOS_CALIFICACION, fila[corte:])))
                    notas.extend(dict(zip(CAMPOS_CALIFICACION, f[corte:])) for f in filas_registro)
                lote.append((registro, notas))
            yield lote
