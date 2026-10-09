# Migración SWA → ERP

Alcance: **toda persona que tuvo una matrícula en SWA en cualquier periodo** (`historico.estado = 'M'`),
activa hoy o no. Quien solo tiene prematrícula (`P`) o solo notas pasa a decisión en "Análisis de casos".
Quien solo tiene código de estudiante (inscrito/admitido que nunca se matriculó) queda apartado.
Aspirantes, terceros y proveedores no entran. SWA es producción y **solo se lee**
(conexión `read_only`, únicamente `SELECT` / `COPY ... TO STDOUT`).

## Bases

`docker compose up -d` (raíz del repo) levanta `db_migracion` en `127.0.0.1:5434` (`erp_migracion`):

| Esquema    | Contenido |
|------------|-----------|
| `public`   | Copia del ERP de producción (restaurada desde `db_erp_prod_original` del Docker del ERP). Destino final. |
| `swa_raw`  | Copia cruda de las tablas académicas de SWA (sin contraseñas; `persona` solo con estudiantes). |
| `migra`    | Datos depurados, descartes, lotes y mapa SWA → ERP. Se reconstruye en cada depuración. |
| `revision` | `revision.caso`: casos irregulares que requieren decisión. Las decisiones se conservan. |

Recargar la copia del ERP:

```bash
docker exec crud_snies-db_migracion-1 psql -U admin -d erp_migracion -c "drop schema public cascade; create schema public;"
docker exec erp_unicorsalud-db_local-1 pg_dump -U admin -d db_erp_prod_original --no-owner --no-privileges -Fc \
  | docker exec -i crud_snies-db_migracion-1 pg_restore -U admin -d erp_migracion --no-owner --no-privileges
```

## Flujo

1. `python manage.py migra_copiar_swa` → copia SWA a `swa_raw`.
2. `python manage.py migra_depurar` → aplica las reglas y las decisiones; genera casos y métricas.
3. Vista `/migracion/casos` → decidir los casos (individual o "aplicar sugerencia" en bloque).
4. Vista `/migracion` → **Enviar a la copia (ensayo)** (re-depura y carga todo lo LISTA a la copia en una transacción).
   Equivalente: `python manage.py migra_enviar`. Revertir: `python manage.py migra_revertir <lote>`.

## Reglas principales

- **Código:** el ID de la persona en el ERP es su código de estudiante SWA (el de carrera antes que el de idiomas/extensión; entre ellos, el de la matrícula más reciente). Sus demás códigos quedan en cada matrícula (`academico_matricula.codigo`). Solo si se decide en un caso se genera un código nuevo del ERP.
- Identidad por documento normalizado; registros `X…X` de SWA son duplicados anulados y se unifican.
- Nombres: limpieza y separación en primer/segundo nombre y apellidos (partículas DE, DEL, LA…).
- Tipo de documento: una CC nunca se baja a TI; TI → CC cuando la edad lo indica; POR DEFINIR se resuelve por edad o va a revisión.
- Fecha de nacimiento de SWA solo si es plausible frente al primer ingreso (SWA tiene valores de relleno).
- Correos de relleno (`@`, `sinemail@…`, `<codigo>@universidad.edu.co`) se descartan.
- Matrícula = una por persona/programa/periodo (varios códigos se unifican). Notas sin matrícula M en el periodo → caso.
- Notas: grupos negativos de SWA son habilitación/validación/homologación/curso y reemplazan a la nota regular.
- Preinscripciones (`P`) y canceladas (`C`) se apartan en `migra.descarte`.
- Periodo 2026-2 en adelante: matrícula activa y notas en curso (`academico_asignaturamatricula`).
- Inglés (programas 51 y 52): cada módulo aprobado → `academico_homologacioningles` con motivo `MIGRACION_SWA`.

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

## Envío real (ventana de corte)

El ERP de producción nunca se escribe desde aquí. En la ventana de mantenimiento (ERP detenido):

1. `pg_dump -Fc` de producción → guardarlo (es la reversión).
2. `python manage.py migra_corte --dump-produccion <dump> --salida <dump_migrado>`:
   restaura el dump en la copia, re-depura con las decisiones, envía y genera el dump del esquema `public`.
   Pide confirmación antes de reemplazar la copia y antes de generar el dump (revisar con `verificar_migracion.py`).
3. Restaurar `<dump_migrado>` en producción (`pg_restore --clean --if-exists --no-owner`).
4. En el ERP: `python manage.py crear_usuarios_estudiantes --enviar --reporte <csv>`.

Antes: checklist A–K en verde (incluye ensayo general J y rama del ERP desplegada K).
