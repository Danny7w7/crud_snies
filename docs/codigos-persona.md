# Identidad SWA, programas y preparacion academica

El ERP desarrollado es la referencia del esquema. Este migrador adapta sus
modelos externos y datos a ese ERP; no cambia las tablas que administra el ERP.
SWA (`produccion`) permanece siempre en lectura. Las escrituras van a `default`.

## ID de persona

- Cada persona nueva recibe un `Persona.id` que ya existe en SWA. No se generan
  prefijos ni se consume `erp_secuenciacodigopersona`. El ERP conserva su generador
  para sus propias altas; esa logica no participa en esta importacion.
- Los duplicados se unifican si coinciden numero normalizado y nombre completo
  (mayusculas, tildes y espacios normalizados). Se permiten diferencias CC/TI/0.
  El tipo 0 requiere otro registro coincidente con tipo conocido. Otros tipos
  incompatibles, documentos invalidos y nombres diferentes quedan pendientes.
- Se elige el ID de la persona asociada al primer ingreso valido de carrera.
  En empates se elige el menor ID; si no hay ingreso valido, el menor ID asociado
  a carrera. Sin carrera se elige el menor ID SWA del grupo.
- Todos los IDs originales se conservan en `ubicacion_equivalenciapersonaswa`.
  La tabla existente de equivalencias de estudiantes conserva los codigos SWA
  y sus vinculos principales. Las asociaciones por periodo conservan los
  codigos de ingles cuando su carrera cambia a lo largo del tiempo.
- La reutilización de un ID distinto requiere una equivalencia ya validada para
  el ID canónico del SWA, además de la coincidencia de documento, tipo y nombre.
  Un ID ocupado por otra identidad, una equivalencia contradictoria o un candidato
  duplicado quedan pendientes. Se conservan los IDs y las referencias del ERP.
- Para una persona nueva, `erp_persona.estado_general` exige `ACTIVO`, `INACTIVO`
  o `RETIRADO`. El valor debe constar en la fuente o proceder de una decisión
  explícita. El histórico de matrícula M sirve para comprobar la matrícula
  y no determina el estado general de la persona. La carga conserva el estado
  de las personas que ya existen en el ERP.
- Creacion de persona y equivalencias pertenecen a una transaccion en
  `default`. La misma regla se aplica a la carga masiva y al formulario.

## Programas y avance

Los programas 50, 51, 52 y 60 estan incluidos, aun sin historico de matricula M.

- 50 y 60 (extension): vinculos principales propios sobre la misma persona.
- 51 y 52 (ingles): secundarios de carrera cuando existe una unica carrera
  con matricula M en la misma sede y periodo. Se usa la equivalencia SWA 1 ->
  ERP 1 y SWA 3 -> ERP 2, configurable mediante `SEDES_SWA_A_ERP`.
- Sin carrera, ingles tiene un vinculo principal propio. Varias carreras,
  falta de evidencia, secundaria ocupada o equivalencias contradictorias
  dejan la asociacion pendiente. Ingles no cambia el estado de la carrera.
- `ubicacion_asociacionprogramaswa` conserva codigo, persona y programa SWA,
  periodo, destino y motivo de pendiente.
- `ubicacion_avanceasignaturaswa` prepara el registro de cada asignatura con
  la clave original (periodo, programa, asignatura, grupo, estudiante).
  Conserva modulo de ingles y estados de origen, sin importar notas ni cortes.
  Las aprobaciones usan estado R/A de origen; los intentos se conservan y
  un modulo aprobado se cuenta una sola vez por persona.
- `PERIODO_ACTUAL_MIGRACION_SWA = 20262` marca el primer período sin cierre.
  Los anteriores son históricos. El corte, los períodos posteriores y los
  inválidos quedan pendientes. Los vínculos históricos nuevos se crean
  inactivos. La opción Ubicar puede activar un alta manual del período de corte
  con estado `A` del registro estudiantil.
- Una ausencia de registros no inventa avance. No se crean pagos ni
  homologaciones. La carga de notas y su visualizacion son una fase posterior.

La etapa de vinculos prepara tambien asociaciones y avance. La API mantiene
`codigo_persona` como texto, expone `programa_secundario` y los codigos SWA.
Las consultas filtran por programa antes de elegir un registro representativo.
Los reintegros se calculan por persona canonica y programa, solo con matricula M.

## Preparacion y analisis previo

`api/0012` alinea el estado de migraciones con el contrato de personas,
programas e inscripciones. La migración solo cambia el estado de Django y
mantiene las tablas del ERP administradas externamente.
`ubicacion/0003` crea solamente las tablas auxiliares del migrador en el destino.
No ejecutar ninguna migracion con `--database=produccion`.

Antes de una carga autorizada, preparar las tablas auxiliares en el destino:

```powershell
cd backend
..\venv\Scripts\python.exe manage.py migrate --database=default
..\venv\Scripts\python.exe manage.py analizar_migracion_swa
```

El analisis no escribe, crea catalogos ni reserva IDs. Presenta cantidades,
motivos de pendientes y clasificacion temporal; no publica nombres ni documentos.
Los preflight verifican tablas, columnas y unicidad por documento antes de escribir.
Los textos ausentes admitidos se representan con cadenas vacias donde el ERP
exige NOT NULL, sin ALTER TABLE ni datos de contacto inventados.

Los programas nuevos requieren una sucursal ERP verificada y los campos
obligatorios del catálogo actual. Los programas ya instalados conservan sus datos.
La etapa de asignaturas ahora incorpora codigos nuevos de forma incremental y
conserva los IDs, los datos y las referencias existentes del ERP. No vacia tablas.
Las notas se cargan mediante un comando independiente del endpoint completo:
ver [notas y pensums](notas-swa.md).

## Pruebas aisladas

Estas pruebas nunca importan settings de produccion, .env ni credenciales reales.
La fuente `produccion` usa un backend dummy y consultas simuladas.

```powershell
cd backend
..\venv\Scripts\python.exe manage.py test api --settings=config.test_settings
..\venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Desde la raiz, concurrencia real contra una instancia PostgreSQL temporal propia:

```powershell
.\venv\Scripts\python.exe backend/config/tests/run_postgres_codigos.py
.\venv\Scripts\python.exe -m unittest discover -s backend/config/tests -v
```

El helper exige PostgreSQL 16 instalado, crea su cluster dentro de .cache,
verifica su propiedad, usa localhost:55439 y lo detiene al finalizar. Nunca
utiliza las bases configuradas del proyecto ni elimina servidores ajenos.
