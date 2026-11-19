# Migración SWA → ERP

Alcance: **matrículas SWA confirmadas en periodos anteriores a 2026-2**
(`historico.estado = 'M'`), incluidas las de estudiantes que ya no están activos.
Quien solo tiene prematrícula (`P`) o notas pasa a decisión en "Análisis de casos".
Quien solo tiene código de estudiante (inscrito/admitido que nunca se matriculó) queda apartado.
Aspirantes, terceros y proveedores no entran. SWA es producción y **solo admite
`SELECT` sin efectos secundarios**, en autocommit y con lectura protegida desde el
inicio de la conexión.

## Bases

Configura `default` con `DB_HOST` igual a `127.0.0.1` o `::1`, puerto y
credenciales locales. `DB_NAME` y `MIGRADOR_DESTINO_NOMBRE` deben identificar la
misma copia nueva. Usa `MIGRADOR_SIN_DOTENV=1` para un perfil local controlado
y `MIGRADOR_SWA_LOCAL=1` al consultar y cargar desde la instantánea.

| Esquema    | Contenido |
|------------|-----------|
| `public`   | Copia local del ERP y tablas auxiliares `ubicacion_*`. |
| `swa_raw`  | Copia cruda de las tablas académicas de SWA (sin contraseñas; `persona` solo con estudiantes). |
| `migra`    | Datos depurados, descartes, lotes y mapa SWA → ERP. Se reconstruye en cada depuración. |
| `revision` | `revision.caso`: casos irregulares que requieren decisión. Las decisiones se conservan. |

Respaldar la base anterior y restaurar el dump en una base local nueva con
`public` vacío:

```bash
python manage.py migra_corte --dump-produccion <archivo.dump>
```

## Flujo

1. Preparar la instantánea local `swa_raw`. Para una extracción SWA autorizada,
   `python manage.py migra_copiar_swa --periodo-hasta 20262 <tablas>` usa `SELECT`
   protegido en el origen y `COPY FROM` en el destino local.
2. `python manage.py migra_depurar` → aplica las reglas y las decisiones; genera casos y métricas.
3. Vista `/migracion/casos` → decidir los casos (individual o "aplicar sugerencia" en bloque).
4. Vista `/migracion` → **Enviar a la copia (ensayo)** para depurar y cargar.
   Para un lote de un solo periodo, ejecuta
   `python manage.py migra_enviar --periodo 20261`. Registra y concilia los conteos
   del lote antes de pasar al siguiente. Revertir: `python manage.py migra_revertir <lote>`.

## Reglas principales

- **Código:** una persona nueva conserva su ID canónico de persona SWA, elegido por el primer ingreso de carrera.
  Los códigos estudiantiles se conservan como equivalencias. El alta requiere
  evidencia de `estado_general` válido. Las identidades en conflicto pasan a revisión.
- Identidad por documento normalizado; registros `X…X` de SWA son duplicados anulados y se unifican.
- Nombres: limpieza y separación en primer/segundo nombre y apellidos (partículas DE, DEL, LA…).
- Tipo de documento: conciliar el código de origen con el catálogo del ERP y revisar los tipos incompatibles.
- Fecha de nacimiento de SWA solo si es plausible frente al primer ingreso (SWA tiene valores de relleno).
- Correos de relleno (`@`, `sinemail@…`, `<codigo>@universidad.edu.co`) se descartan.
- Matrícula = una por persona/programa/periodo (varios códigos se unifican). Notas sin matrícula M en el periodo → caso.
- Notas: conservar la definitiva confirmada del SWA, incluido cero con respaldo de calificaciones.
  Si compiten varios intentos de la misma asignatura, conservar la fuente y revisar cuál resultado se publica.
- Preinscripciones (`P`) y canceladas (`C`) se apartan en `migra.descarte`.
- Periodo 2026-2 en adelante: matrículas y notas en espera del cierre.
- Inglés (programas 51 y 52): trazar la asociación y el módulo de origen.
  Las homologaciones requieren un motivo reconocido por el ERP.

## Reporte de anomalías

`python manage.py migra_anomalias [--excel ruta.xlsx] [--severidad ALTA] [--gravedad ...] [--responsable ...] [--sin-regenerar]`
(o el botón de la vista `/migracion/anomalias`, que exporta con los filtros de la pantalla) documenta
todo lo que SWA permitió y no debería: registros C/P, notas que no cuadran, aprobaciones sin matrícula legalizada,
sin pago o sin prerrequisito, graduados con pendientes y módulos de inglés sin soporte. Requiere haber depurado
(usa `migra` para marcar si cada caso viaja al ERP). Solo lee; no cambia la depuración ni la carga.

- Resultado en el esquema `anomalias` (`anomalia`: un caso por fila; `ejecucion`: historial). Las tablas `aux_*`
  son auxiliares y se reconstruyen.
- El catálogo (qué pasó, riesgo, recomendación, responsable y la frase legible de cada caso) está en
  `anomalias.py`. Una regla sin casos también se reporta como verificada.
- Sin doble conteo: todo se evalúa por persona (varios códigos SWA cuentan una vez y los pagos de cualquiera de
  ellos valen), las reglas de pensum usan la nota final del periodo, las homologaciones no exigen matrícula, pago
  ni prerrequisitos y cada situación cae en una sola regla. Severidad `INFO` = contexto, no cuenta como anomalía.
- Exportación: Excel con hojas Resumen (por regla), Casos (un caso por fila, en palabras) y Por persona.
- Pagos: un recibo está cubierto si su estado es pagado/financiado, si tiene pagos en `pago_recibo`, si las cuotas
  de su crédito se pagaron o si un beneficio cubrió el 100 % (`aux_recibo.cubierto_por`). En SWA el crédito vale
  el neto del recibo y sus cuotas dicen quién lo cubrió (`tipo_cuota`: estudiante, ICETEX/convenios/cajas,
  descuentos, 7 = saldo a favor); ver `CLASES_CUOTA`. Vacacional y validación de inglés no pagan la matrícula.
- Saldos a favor (categoría SALDOS, todo ALTA con gravedad 1 Crítica ≥ $2M, 2 Alta ≥ $500.000, 3 Moderada):
  libro por persona en `aux_saldo` (generado − usado − devuelto). SWA registra cada saldo dos veces (cuota 7 y
  `pago_recibo.saldo_a_favor`; uso en cuota 7 positiva/auxilio SF y `bajar_saldo_a_favor`): se toma el mayor.
- Verificación independiente de pagos: `sql/verificar_pagos.sql` (solo lee `swa_raw`; su PARTE 5 cruza con el
  reporte y lista los casos sin pago que el reporte deje escapar).
- Requiere en `swa_raw` los catálogos `auxilio`, `grupo_auxilio`, `tipo_cuota` y `novedad_devolucion`
  (incluidos en `copia_swa.py`).
- Pagos: usa las tablas financieras de SWA copiadas a `swa_raw` (`recibo_swa`, `pago_recibo`, `credito`,
  `cuota`, `concepto_cambio`…). Los recibos existen desde 2017-1; antes no hay soporte financiero.
  Estados inferidos de los datos: recibo 10 pagado, 1 financiado, 0 sin pagar, 2 anulado.

## Pagos del periodo (SWA directo)

`python manage.py migra_pagos_periodo [--periodo 20262] [--excel ruta.xlsx] [--programa 29] [--grupo 3] [--veredicto 2D] [--documento ...]`
(o la vista `/migracion/pagos`, que exporta con los filtros de la pantalla): quién tiene asignaturas vinculadas en
el periodo (con o sin nota) sin pago comprobado de matrícula, con los créditos apartados y las anomalías de los
descuentos. El informe diario consulta SWA con la conexión de solo lectura del proyecto.
La consulta y los veredictos están documentados en `sql/pagos_periodo.sql`.

- Pago comprobado = dinero en una cuenta real. `pago_recibo` en las cuentas del banco 900 "VALORES X CONCEPTOS"
  (9001 saldos a favor, 9003 periodos anteriores, 9004 descuentos, 9005 menor cuantía, 9006 convenios, 9007 becas)
  no es dinero, como tampoco las cuotas de ajuste (18) ni de saldo a favor (7).
- Misma persona con otro documento (`unificar`): el Centro de Idiomas crea al estudiante de carrera con un documento
  ficticio (4 dígitos, tipo 0) o mal digitado. Se une con su registro de carrera si el nombre coincide (o casi, o con
  el mismo correo) y el parecido es único; queda en `documentos_unificados` y en la hoja "Personas unificadas".
- Grupos: 1 pago comprobado, 2 crédito (apartado; 2D/2E sin ningún pago), 3 sin pago comprobado, 4 cubierto sin
  dinero (con "respaldo en dinero" del año anterior), 5 sin pago propio pero con otro pago.
- PDF (`POST pagos-periodo/pdf/`, `pagos_pdf.py`, requiere `reportlab`): la vista de casos o de anomalías con los
  filtros de la pantalla; si se marcan filas, solo esas (`seleccion`: `documento|programa` o `anomalía|recibo|documento`).
- El Excel y el PDF traen documentos y nombres: no se guardan en el repositorio.

## Corte por periodo

- `PERIODO_CORTE` (`depuracion.py`, hoy `20262`): las notas de ese periodo en adelante se **aplazan**
  (SWA sigue calificando). Al cerrar notas: `migra_copiar_swa`, cambiar a `20271`, `migra_depurar`, enviar.
- `PERIODO_ACTIVO_ERP` (`20271`): solo las matrículas desde ese periodo quedan vigentes en el ERP.
  La matrícula 2027-1 de los antiguos la crea el ERP (rematrícula al pagar el volante).
- Los pensums de SWA se crean **inactivos** para no interferir con la elección de pensum del ERP.

## Verificación con el código del ERP

`docker/erp_local/verificar_migracion.py` ejecuta el código del ERP contra la copia (en una
transacción que se deshace): historial, certificado, privacidad del enlace público, embudo de
Admisiones, cuentas de usuario, rematrícula y módulos de inglés. Requiere la rama del ERP
`feature/migracion-swa-soporte` y un lote enviado a la copia:

```bash
# desde ERP_Unicorsalud
docker compose -f docker-compose.yml \
  -f ../../../Pictures/crud_snies/docker/erp_local/docker-compose.verificacion.yml \
  run --rm --no-deps web python manage.py shell -c \
  "exec(open('/opt/erp_override/verificar_migracion.py', encoding='utf-8').read())"
```

## Respaldo de la copia local

Después de conciliar los lotes puedes generar un archivo nuevo con
`python manage.py migra_corte --salida <respaldo_local.dump>`. Mantén el
respaldo anterior y los reportes con datos personales en almacenamiento privado.
