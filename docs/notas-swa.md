# Notas y pensums de SWA

La implementacion adapta el migrador al esquema del ERP desarrollado. La ampliacion
aprobada para admitir MIXTA se implementa en ese ERP mediante `academico.0041`.
`produccion` es exclusivamente de lectura; todas las escrituras usan explicitamente
`default`.

## Preparacion y comandos

Antes de una carga autorizada, preparar las tablas auxiliares del migrador en
el destino. `api/0010` y `api/0012` registran el estado de los modelos externos
con `managed=False`. `ubicacion/0004` crea la trazabilidad y amplía el avance existente.
Nunca ejecutar migraciones con `--database=produccion`.

Desde `backend`:

```powershell
..\venv\Scripts\python.exe manage.py migrate --database=default
..\venv\Scripts\python.exe manage.py analizar_migracion_swa --notas
..\venv\Scripts\python.exe manage.py migrar_notas_swa --programa 51 --periodo 20261
```

Los dos comandos analizan sin escribir, crear periodos ni reservar IDs. El
resultado contiene cantidades, motivos y tablas auxiliares faltantes; no nombres,
documentos ni codigos personales. Sin las equivalencias de personas, los registros
se consideran pendientes. Un esquema nativo incompatible detiene el proceso antes
de abrir la fuente. Las tablas auxiliares existentes deben tener sus columnas
actualizadas.

La escritura requiere el indicador explicito:

```powershell
..\venv\Scripts\python.exe manage.py migrar_notas_swa --aplicar --programa 51 --periodo 20261
```

Omitir los filtros incluye todos los programas y períodos, incluidos 50, 51, 52
y 60. `--programa` limita las mallas a ese programa. `--periodo` limita los intentos
y las calificaciones. Para un período sin cierre, el modo aplicar registra
pendientes y pospone la preparación de las mallas.
Las identidades se resuelven con todos los estudiantes, antes de filtrar, para
conservar el ID SWA canonico de la fase de personas.

El lector usa autocommit y pagina por la clave completa en lotes de 500. Los
registros y las calificaciones de cada lote comparten un `SELECT`. Para fijar el
mismo corte de todos los lotes y catálogos, use `MIGRADOR_SWA_LOCAL=1` con una
copia local verificada y estable en `swa_raw`. Una carga obtiene un bloqueo en
PostgreSQL que comparte con el resto de las cargas SWA al destino local. La
escritura de cada intento histórico y sus equivalencias es atómica. Ante un
conflicto se revierte esa proyección y se conserva el registro fuente con su motivo.

## Definitivas históricas y períodos sin cierre

El corte `PERIODO_ACTUAL_MIGRACION_SWA` vale 20262 y marca el primer período sin cierre.
Se proyectan los intentos de períodos anteriores. El corte, los períodos posteriores
y los inválidos quedan pendientes.
Se identifica cada periodo ERP por su nombre `AAAA-S`, sin escoger el primer
periodo activo. Se conservan los indicadores activos existentes; los periodos
nuevos se crean inactivos.

La clave de un intento es `(periodo, programa, asignatura, grupo, estudiante)`.
Cada calificacion agrega `tipo_nota`. Esto conserva intentos repetidos, incluso
cuando la tabla historica del ERP no tiene columna de grupo.

| Origen SWA | Destino |
| --- | --- |
| Período sin cierre | Intento y calificaciones en trazabilidad, pendientes |
| Registro histórico confirmado | `academico_asignaturamatriculahistorico` y su matrícula |
| Cortes históricos | `ubicacion_calificacionswa`, vinculados al intento histórico |
| Tipo 0, DEFINITIVA | Definitiva de la inscripcion; trazabilidad del tipo original |

La carga de un intento histórico guarda la definitiva en su inscripción histórica.
Los cortes se consultan en la trazabilidad del migrador.

`calificacion.estado=0` significa pendiente y `=1` significa registrada.
`registro.estado_definitiva` debe marcar `A` o `R` para publicar el valor histórico
de `registro.definitiva`. Una definitiva positiva y marcada conserva su valor
aunque el SWA ya no tenga los cortes. Para un cero se exige que el esquema
esté confirmado y que sus calificaciones acrediten ese valor. Una definitiva
ausente o sin confirmación queda pendiente. Los valores originales permanecen
en la trazabilidad y la definitiva se toma del registro de origen.

Los valores nativos se redondean a dos decimales con `Decimal` y `ROUND_HALF_UP`.
Por ejemplo, 3.145 pasa a 3.15; se conserva 3.145 en origen. El snapshot conserva
las claves, estados, observaciones, fallas, porcentajes y componentes academicos
seleccionados de la calificacion. No hace modificaciones financieras.

Una inscripción histórica requiere `registro.estado=R` y una fila coincidente de
`historico.estado=M` para ese estudiante, programa y período. Una prematrícula
`P`, los registros `C` o `P` y las inconsistencias de origen quedan pendientes
con su trazabilidad. El informe registra sus motivos.

## Vista de pendientes en CRUD SNIES

La ruta `/pendientes`, accesible desde la navegacion, muestra los intentos pendientes
y los conflictos que aparecen despues de una proyeccion. Usa el mismo acceso de
consulta y su vencimiento por inactividad. La validacion de acceso, el listado,
el detalle y los conteos consultan exclusivamente los snapshots del destino
`default`. La carga exige una copia local identificada por `MIGRADOR_DESTINO_NOMBRE`
y configurada como `DB_NAME`.

La API de lectura `/api/pendientes-notas/` pagina en lotes de 25. Permite filtrar
por programa, periodo, estado de registro, destino de notas, motivo y busqueda por
codigo SWA, asignatura o grupo. `sin_interpretar=1` selecciona estados de registro
distintos de `R`. `/resumen/` muestra cantidades y opciones de filtro; `/<id>/`
incluye todos los cortes conservados, sus estados y valores originales, aunque
no se hayan publicado en las tablas academicas nativas. Cero y NULL siguen siendo
valores distintos. No hay operaciones de aprobacion ni edicion en esta vista.

El mensaje anterior `La asignatura no esta registrada en SWA.` significaba estado
de registro distinto de `R`, no ausencia en el catalogo. Se presenta como
`Estado de registro SWA sin correspondencia aprobada.` tambien para cargas previas.
Los estados desconocidos siguen pendientes hasta confirmar su correspondencia.

## Mallas e identidades

Se conservan los IDs de asignaturas existentes y se identifican por codigo SWA.
La etapa antigua que vaciaba asignaturas se sustituyo por incorporacion incremental.
Los datos existentes de las asignaturas permanecen; las nuevas requieren una
correspondencia valida de tipo, creditos y horas.

Los pensums existentes conservan su ID, pero su codigo cambia al de SWA. La malla
incorpora asignaturas, semestre, prerrequisitos y correquisitos en las tablas
nativas. Las versiones adicionales necesarias se crean con IDs generados por
el destino. Se usan estas correspondencias aprobadas para los pensums genericos:

| Programa | Version SWA que actualiza el pensum existente |
| --- | --- |
| 29, Radiologia | 2903 |
| 32, Gerencia de Sistemas | 3202 |
| 31, Higiene Industrial | 3101 |

Las correspondencias unicas se reutilizan; las ambiguas requieren revision. El
codigo 1 repetido entre programas se representa como `56-1`, `67-1`, `68-1`, porque
el codigo del pensum es unico en el ERP. La trazabilidad conserva programa y codigo
original por separado, asi como el pensum, su malla y sus requisitos fuente.
No se retiran automaticamente materias ni requisitos ya importados.

SWA no guarda el pensum por periodo en `historico`. Se usa el declarado en el
estudiante: una materia historica fuera de esa malla queda pendiente para resolver
la version, sin inventarla. El modulo de ingles viene de `registro.nivel` y se
contrasta con `pensum_asignatura.nivel`; `asignatura.nivel` no existe. Los modulos
validos son 1 a 6, sin inferir avance a partir del numero general de niveles.

El ERP admite asignaturas `TEORICA`, `PRACTICA` y `MIXTA`, correspondientes a los
tipos `T`, `P` y `M` de SWA. Para una mixta, SWA solo proporciona las horas
semanales totales: se conservan en `academico_asignatura.hora_mixta` y las horas
teoricas/practicas quedan en cero, sin inventar un reparto. El ERP suma las tres
columnas en sus cargas, reportes y API. Cuando se conoce el reparto, se distribuye
el total entre teoria y practica y se deja `hora_mixta` en cero para evitar
contarlo dos veces. Las asignaturas existentes conservan sus datos.

La ampliacion nativa corresponde a `academico.0041_asignatura_mixta` del ERP
desarrollado. El migrador solo referencia el campo y su migracion
`api.0011_asignaturaerp_hora_mixta` actualiza el estado sin DDL nativo. Antes de
importar se valida que el destino tenga el campo. Las mallas ya importadas sin
cambios se verifican contra su ultimo snapshot y se reutilizan sin reescrituras;
una edicion posterior del ERP sigue impidiendo su actualizacion automatica.

Ingles conserva su programa academico en matriculas y notas. Si corresponde a
una carrera, la asociacion secundaria requiere una unica carrera matriculada en
la misma sede y periodo. No altera la carrera principal. Extension, programas
50 y 60, conserva sus vinculos propios.

## Repeticion, conflictos y validacion

Las equivalencias conservan los IDs nativos generados. Una repeticion actualiza
solo datos que siguen iguales al ultimo snapshot importado. Las ediciones del
ERP, referencias contradictorias, inscripciones sin origen trazado, retiradas
de notas o una definitiva que vuelve a pendiente requieren revision. El analisis
comparte estas comprobaciones con la carga, sin escribir. Los pendientes no
impiden cargar otros intentos validos. No se renombran personas ni se genera un
ID nuevo de persona.

Validacion local sin bases reales:

```powershell
# Desde la raiz
.\venv\Scripts\python.exe backend/manage.py test api --settings=config.test_settings
.\venv\Scripts\python.exe backend/manage.py makemigrations --check --dry-run --settings=config.test_settings
.\venv\Scripts\python.exe backend/config/tests/run_postgres_codigos.py
.\venv\Scripts\python.exe -m unittest discover -s backend/config/tests -p test_read_only.py -v
```

La configuracion de pruebas no importa settings reales ni `.env`. SWA usa un
backend dummy; las lecturas se sustituyen por mocks. El runner PostgreSQL crea
una instancia propia en el workspace, verifica su directorio y la detiene al
final. Incluye SQL real de lectura sobre tablas TEMP creadas exclusivamente en
`default` temporal, sin columna `asignatura.nivel`, y prueba el bloqueo de cargas
simultaneas. No se realizaron cargas ni migraciones sobre las bases reales para
validar esta implementacion.
