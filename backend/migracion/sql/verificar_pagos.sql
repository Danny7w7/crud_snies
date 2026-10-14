-- =====================================================================================================
-- VERIFICACION INDEPENDIENTE DE PAGOS DE MATRICULA Y MODULOS DE INGLES (SWA)
--
-- Responde: ¿hay personas que estudiaron (carrera o modulo de ingles) sin ningun pago que lo justifique?
-- Solo lee `swa_raw` (copia de SWA). NO usa las tablas del reporte (`anomalias.*`) salvo en la PARTE 5,
-- que cruza el resultado con el reporte para ver si este deja escapar casos.
-- Crea tablas TEMPORALES (desaparecen al cerrar la sesion).
--
-- Uso:  docker exec -i crud_snies-db_migracion-1 psql -U admin -d erp_migracion < verificar_pagos.sql
--       (o en DBeaver/pgAdmin ejecutandolo completo como script, en una sola sesion)
-- Para ver la ficha de un caso, cambie el documento en la PARTE 0.
--
-- Como sabe SWA si una matricula se pago (inferido de los datos; validar con Financiera):
--   * recibo_swa.estado: 10 pagado, 1 financiado, 0 generado sin pagar, 2 anulado. NO BASTA: miles de recibos
--     quedaron en 0/2 y se pagaron por credito o tienen pagos registrados.
--   * pago_recibo: pagos; tipo_recibo 0 = pago de un recibo, 1 = pago de una cuota de credito.
--   * credito.valor = neto del recibo; sus CUOTAS dicen quien cubrio el neto (catalogo tipo_cuota):
--     estudiante, terceros (ICETEX, convenios, alcaldias, cajas), descuentos, 7 = saldo a favor.
--   * valor_ordinaria = neto ya descontados los auxilios del recibo (becas/descuentos).
-- Los recibos existen de forma sistematica desde 2017-1: antes de eso no se puede verificar el pago.
--
-- Identidad: la persona es su DOCUMENTO normalizado. SWA repite personas en varios registros de `persona`
-- (mismo documento, distinto id: 7.769 registros para 7.309 documentos); cruzar por el id de SWA haria ver
-- "sin pago" a quien pago bajo su otro registro.
-- =====================================================================================================

-- ------------------------------------------------------------------ PARTE 0: parametros (editar aqui)
DROP TABLE IF EXISTS p_param;
CREATE TEMP TABLE p_param AS SELECT
    '1006790251'::text AS documento,     -- documento de la persona para la ficha (PARTE 4)
    20171::int        AS desde,          -- primer periodo con soporte financiero en SWA
    20262::int        AS periodo_actual; -- periodo en curso (sus casos se marcan como en curso)

-- ------------------------------------------------------------------ PARTE 1: base de calculo
-- codigo de estudiante -> persona (documento normalizado)
DROP TABLE IF EXISTS p_est;
CREATE TEMP TABLE p_est AS
SELECT e.codigo,
       coalesce(nullif(upper(regexp_replace(ps.identificacion, '[^0-9A-Za-z]', '', 'g')), ''), 'ID' || e.persona) AS persona,
       ps.identificacion AS documento, trim(concat_ws(' ', ps.nombre, ps.apellido)) AS nombre
FROM swa_raw.estudiante e JOIN swa_raw.persona ps ON ps.id = e.persona;
CREATE UNIQUE INDEX ON p_est (codigo);
CREATE INDEX ON p_est (persona);
ALTER TABLE p_param ADD COLUMN clave text;
UPDATE p_param SET clave = upper(regexp_replace(documento, '[^0-9A-Za-z]', '', 'g'));

-- Conceptos de recibo que pagan una matricula. VACACIONAL (17) y VALIDACION INGLES (24) NO pagan la
-- matricula del periodo: son otros derechos.
DROP TABLE IF EXISTS p_concepto;
CREATE TEMP TABLE p_concepto (concepto int PRIMARY KEY, tipo text NOT NULL);
INSERT INTO p_concepto
SELECT c, 'CARRERA' FROM unnest(ARRAY[0, 35, 41, 108, 109, 110]) c
UNION ALL
SELECT c, 'INGLES' FROM unnest(ARRAY[14, 15, 16, 28, 40, 45, 29, 30, 31, 32, 33, 34, 46, 47, 48, 49, 50, 51,
                                    52, 53, 54, 55, 56, 57, 18, 19]) c;

-- Clase de cada tipo de cuota (catalogo tipo_cuota de SWA)
DROP TABLE IF EXISTS p_clase_cuota;
CREATE TEMP TABLE p_clase_cuota (tipo int PRIMARY KEY, clase text NOT NULL);
INSERT INTO p_clase_cuota
SELECT t, 'ESTUDIANTE' FROM unnest(ARRAY[1, 2, 3, 4, 5, 8, 11, 12, 13, 14, 15, 16]) t
UNION ALL SELECT t, 'TERCERO' FROM unnest(ARRAY[6, 38, 39, 40, 41, 44, 45, 46, 50, 51, 52, 53, 54, 55, 56, 57, 58,
                                              59, 60, 61, 62, 63, 64, 80, 81, 82, 86, 87, 88, 89]) t
UNION ALL SELECT t, 'DESCUENTO' FROM unnest(ARRAY[9, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34,
                                                35, 36, 83, 84, 85]) t
UNION ALL SELECT 7, 'SALDO_A_FAVOR' UNION ALL SELECT 18, 'AJUSTE';

-- Cada recibo de matricula con TODA la evidencia de pago y la via por la que quedo cubierto
DROP TABLE IF EXISTS p_recibo;
CREATE TEMP TABLE p_recibo AS
SELECT x.numero, x.periodo, x.programa, x.estudiante, e.persona, x.concepto_cambio AS concepto, k.tipo,
       cc.nombre AS concepto_nombre, x.estado, x.valor AS bruto, x.valor_ordinaria AS neto,
       coalesce(pr.pagado, 0) AS pagos_registrados,
       coalesce(cu.cuotas_pagadas, 0) AS cuotas_pagadas,
       coalesce(cu.descuentos_credito, 0) AS descuentos_credito,
       cu.quien_pago_cuotas,
       CASE WHEN x.estado IN (1, 10) THEN 'ESTADO DEL RECIBO (' || CASE x.estado WHEN 10 THEN 'pagado' ELSE 'financiado' END || ')'
            WHEN coalesce(pr.pagado, 0) > 0 THEN 'PAGO REGISTRADO EN pago_recibo'
            WHEN coalesce(cu.cuotas_pagadas, 0) > 0 THEN 'CUOTAS DE CREDITO PAGADAS'
            WHEN x.estado <> 2 AND x.valor > 0
                 AND (x.valor_ordinaria <= 0 OR coalesce(cu.descuentos_credito, 0) >= x.valor_ordinaria - 1000)
                THEN 'BENEFICIO DEL 100 %'
       END AS cubierto_por
FROM swa_raw.recibo_swa x
JOIN p_concepto k ON k.concepto = x.concepto_cambio
JOIN p_est e ON e.codigo = x.estudiante
LEFT JOIN swa_raw.concepto_cambio cc ON cc.codigo = x.concepto_cambio
LEFT JOIN (SELECT recibo, sum(valor) AS pagado FROM swa_raw.pago_recibo
           WHERE tipo_recibo = 0 AND estado = 1 GROUP BY 1) pr ON pr.recibo = x.numero
LEFT JOIN (SELECT cr.recibo,
                  sum(q.valor_pagado) FILTER (WHERE c.clase IN ('ESTUDIANTE', 'TERCERO') AND q.valor_pagado > 0) AS cuotas_pagadas,
                  sum(q.valor) FILTER (WHERE c.clase = 'DESCUENTO' AND q.estado <> 2) AS descuentos_credito,
                  string_agg(DISTINCT tc.descripcion, ', ') FILTER (WHERE q.valor_pagado > 0) AS quien_pago_cuotas
           FROM swa_raw.credito cr
           JOIN swa_raw.cuota q ON q.credito = cr.numero
           LEFT JOIN p_clase_cuota c ON c.tipo = q.tipo
           LEFT JOIN swa_raw.tipo_cuota tc ON tc.codigo = q.tipo
           GROUP BY 1) cu ON cu.recibo = x.numero;
CREATE INDEX ON p_recibo (persona, periodo);

-- Todos los recibos de la persona (cualquier concepto) para la evidencia
DROP TABLE IF EXISTS p_todos;
CREATE TEMP TABLE p_todos AS
SELECT e.persona, x.* FROM swa_raw.recibo_swa x JOIN p_est e ON e.codigo = x.estudiante;
CREATE INDEX ON p_todos (persona, periodo);

-- Universo: persona + periodo + programa en que ESTUDIO (curso asignaturas en grupo regular, aprobadas o no)
-- o quedo MATRICULADA (M). Homologaciones/validaciones/habilitaciones (grupos negativos) no cuentan como
-- cursar. Los dos centros de idiomas (51, 52) se tratan como uno solo.
DROP TABLE IF EXISTS p_periodo;
CREATE TEMP TABLE p_periodo AS
WITH notas AS (
    SELECT e.persona, r.periodo, r.programa, count(*) AS cursadas,
           count(*) FILTER (WHERE r.estado_definitiva = 'A') AS aprobadas,
           string_agg(DISTINCT a.nombre, ', ') AS asignaturas, min(r.estudiante) AS codigo
    FROM swa_raw.registro r
    JOIN p_est e ON e.codigo = r.estudiante
    LEFT JOIN swa_raw.asignatura a ON a.codigo = r.asignatura
    WHERE r.estado = 'R' AND r.grupo >= 0
    GROUP BY 1, 2, 3),
hist AS (
    SELECT e.persona, h.periodo, h.programa, string_agg(DISTINCT h.estado, '/') AS estados_historico,
           min(h.estudiante) AS codigo
    FROM swa_raw.historico h JOIN p_est e ON e.codigo = h.estudiante
    GROUP BY 1, 2, 3)
SELECT coalesce(n.persona, h.persona) AS persona, coalesce(n.periodo, h.periodo) AS periodo,
       coalesce(n.programa, h.programa) AS programa, coalesce(n.codigo, h.codigo) AS codigo,
       coalesce(n.programa, h.programa) IN (51, 52) AS es_ingles,
       coalesce(n.cursadas, 0) AS cursadas, coalesce(n.aprobadas, 0) AS aprobadas, n.asignaturas,
       h.estados_historico
FROM notas n
FULL JOIN hist h ON h.persona = n.persona AND h.periodo = n.periodo AND h.programa = n.programa
WHERE coalesce(n.periodo, h.periodo) >= (SELECT desde FROM p_param)
  AND (n.cursadas > 0 OR h.estados_historico LIKE '%M%');

-- Veredicto por periodo (el primero que se cumpla)
DROP TABLE IF EXISTS p_veredicto;
CREATE TEMP TABLE p_veredicto AS
SELECT p.*,
       CASE
         WHEN EXISTS (SELECT 1 FROM p_recibo r WHERE r.persona = p.persona AND r.periodo = p.periodo
                      AND r.cubierto_por IS NOT NULL
                      AND (r.programa = p.programa OR (p.es_ingles AND r.programa IN (51, 52))))
           THEN '1 PAGADO'
         WHEN NOT p.es_ingles AND EXISTS (SELECT 1 FROM p_recibo r WHERE r.persona = p.persona AND r.periodo = p.periodo
                      AND r.cubierto_por IS NOT NULL AND r.tipo = 'CARRERA' AND r.programa NOT IN (51, 52))
           THEN '2 PAGADO CON EL RECIBO DE OTRO PROGRAMA (programa renombrado?)'
         WHEN p.es_ingles AND EXISTS (SELECT 1 FROM p_recibo r WHERE r.persona = p.persona AND r.periodo = p.periodo
                      AND r.cubierto_por IS NOT NULL AND r.tipo = 'CARRERA' AND r.programa NOT IN (51, 52))
           THEN '3 INGLES SIN RECIBO PROPIO, CARRERA PAGADA (¿incluido en la carrera?)'
         WHEN p.cursadas = 0
           THEN '4 MATRICULADO SIN PAGO Y SIN CURSAR NADA'
         -- pago el curso por OTRO concepto academico (no es matricula, pero cubre cursar 1-2 asignaturas)
         WHEN EXISTS (SELECT 1 FROM p_todos x WHERE x.persona = p.persona AND x.periodo = p.periodo
                        AND x.estado IN (1, 10)
                        AND x.concepto_cambio IN (17, 20, 23, 36, 37, 38, 39, 100, 101, 102, 111, 112, 113, 114))
           THEN '5 SIN MATRICULA, PAGO OTRO CONCEPTO ACADEMICO (vacacional, nivelacion, creditos adic., diplomado)'
         WHEN EXISTS (SELECT 1 FROM p_todos x WHERE x.persona = p.persona AND x.periodo = p.periodo
                        AND x.estado IN (1, 10) AND x.concepto_cambio IN (21, 22))
           THEN '6 NO JUSTIFICADO, pero pago derechos de grado ese periodo (¿ingles/curso de grado?)'
         ELSE '7 NO JUSTIFICADO: ESTUDIO SIN NINGUN PAGO'
       END AS veredicto,
       p.periodo >= (SELECT periodo_actual FROM p_param) AS en_curso
FROM p_periodo p;

-- ------------------------------------------------------------------ PARTE 2: resumen
\echo '=== PARTE 2. Periodos estudiados desde 2017-1, por veredicto ==='
SELECT veredicto, CASE WHEN es_ingles THEN 'Modulo de ingles' ELSE 'Carrera' END AS que,
       count(*) AS periodos, count(DISTINCT persona) AS personas,
       count(*) FILTER (WHERE aprobadas > 0) AS con_aprobadas, count(*) FILTER (WHERE en_curso) AS en_curso
FROM p_veredicto GROUP BY 1, 2 ORDER BY 1, 2;

-- ------------------------------------------------------------------ PARTE 3: casos sin pago
\echo '=== PARTE 3. NO JUSTIFICADOS (7 y 6) y pagados con otro concepto (5), con la evidencia ==='
SELECT left(v.veredicto, 1) AS ver, ps.documento, ps.nombre, v.codigo, v.periodo, v.programa,
       pg.nombre AS programa_nombre, v.estados_historico AS historico, v.cursadas, v.aprobadas,
       left(v.asignaturas, 80) AS asignaturas,
       -- evidencia: todo lo que la persona tiene ese periodo (cualquier concepto, cualquier programa)
       (SELECT string_agg('N.' || x.numero || ' ' || coalesce(c.nombre, x.concepto_cambio::text) || ' prog ' || x.programa
                          || ' estado ' || x.estado || ' $' || round(x.valor_ordinaria::numeric), '; ' ORDER BY x.numero)
        FROM p_todos x LEFT JOIN swa_raw.concepto_cambio c ON c.codigo = x.concepto_cambio
        WHERE x.persona = v.persona AND x.periodo = v.periodo) AS recibos_del_periodo,
       (SELECT count(*) FROM p_todos x JOIN swa_raw.credito cr ON cr.recibo = x.numero
        WHERE x.persona = v.persona AND x.periodo = v.periodo) AS creditos,
       v.en_curso
FROM p_veredicto v
JOIN p_est ps ON ps.codigo = v.codigo
LEFT JOIN swa_raw.programa pg ON pg.codigo = v.programa
WHERE v.veredicto ~ '^[567]'
ORDER BY v.veredicto DESC, v.es_ingles, v.periodo, ps.nombre;

-- ------------------------------------------------------------------ PARTE 4: ficha de un caso
\echo '=== PARTE 4a. Ficha: periodos y veredicto de la persona del parametro ==='
SELECT v.periodo, v.programa, v.codigo, v.estados_historico, v.cursadas, v.aprobadas, v.veredicto, v.asignaturas
FROM p_veredicto v
WHERE v.persona = (SELECT clave FROM p_param)
ORDER BY v.periodo, v.programa;

\echo '=== PARTE 4b. Ficha: TODOS sus recibos (cualquier concepto) con la evidencia de pago ==='
SELECT x.numero, x.periodo, x.programa, x.estudiante AS codigo, c.nombre AS concepto,
       CASE x.estado WHEN 10 THEN 'pagado' WHEN 1 THEN 'financiado' WHEN 0 THEN 'sin pagar' WHEN 2 THEN 'anulado'
                     ELSE x.estado::text END AS estado_recibo,
       round(x.valor::numeric) AS bruto, round(x.valor_ordinaria::numeric) AS neto,
       concat_ws(', ', a1.nombre, a2.nombre, a3.nombre) AS beneficios_en_recibo,
       (SELECT round(sum(p.valor)::numeric) FROM swa_raw.pago_recibo p
        WHERE p.tipo_recibo = 0 AND p.recibo = x.numero AND p.estado = 1) AS pagos_registrados,
       r.cuotas_pagadas, r.quien_pago_cuotas, r.cubierto_por
FROM p_todos x
LEFT JOIN swa_raw.concepto_cambio c ON c.codigo = x.concepto_cambio
LEFT JOIN swa_raw.auxilio a1 ON a1.codigo = nullif(x.tipo_auxilio1, 0)
LEFT JOIN swa_raw.auxilio a2 ON a2.codigo = nullif(x.tipo_auxilio2, 0)
LEFT JOIN swa_raw.auxilio a3 ON a3.codigo = nullif(x.tipo_auxilio3, 0)
LEFT JOIN p_recibo r ON r.numero = x.numero
WHERE x.persona = (SELECT clave FROM p_param)
ORDER BY x.periodo, x.numero;

\echo '=== PARTE 4c. Ficha: cuotas de sus creditos (quien pago y cuanto) ==='
SELECT cr.recibo, q.numero AS cuota, tc.descripcion AS tipo_cuota, cl.clase, q.estado,
       round(q.valor::numeric) AS valor, round(q.valor_pagado::numeric) AS pagado, q.fecha_pago
FROM p_todos x
JOIN swa_raw.credito cr ON cr.recibo = x.numero
JOIN swa_raw.cuota q ON q.credito = cr.numero
LEFT JOIN swa_raw.tipo_cuota tc ON tc.codigo = q.tipo
LEFT JOIN p_clase_cuota cl ON cl.tipo = q.tipo
WHERE x.persona = (SELECT clave FROM p_param)
ORDER BY cr.recibo, q.numero;

\echo '=== PARTE 4d. Ficha: beneficios en la ficha del estudiante, novedades y devoluciones ==='
SELECT 'ficha' AS fuente, e.codigo, concat_ws(', ', a1.nombre, a2.nombre, a3.nombre) AS detalle, NULL::numeric AS valor
FROM swa_raw.estudiante e JOIN p_est k ON k.codigo = e.codigo
LEFT JOIN swa_raw.auxilio a1 ON a1.codigo = nullif(e.tipo_auxilio1, 0)
LEFT JOIN swa_raw.auxilio a2 ON a2.codigo = nullif(e.tipo_auxilio2, 0)
LEFT JOIN swa_raw.auxilio a3 ON a3.codigo = nullif(e.tipo_auxilio3, 0)
WHERE k.persona = (SELECT clave FROM p_param)
UNION ALL
SELECT 'novedad_descuento', n.estudiante, coalesce(a.nombre, '') || ' ' || coalesce(n.descripcion, ''), round(n.valor::numeric)
FROM swa_raw.novedad_descuento n JOIN p_est k ON k.codigo = n.estudiante
LEFT JOIN swa_raw.auxilio a ON a.codigo = n.auxilio
WHERE k.persona = (SELECT clave FROM p_param)
UNION ALL
SELECT 'novedad_devolucion', d.estudiante, d.descripcion, round(d.valor::numeric)
FROM swa_raw.novedad_devolucion d JOIN p_est k ON k.codigo = d.estudiante
WHERE k.persona = (SELECT clave FROM p_param);

-- ------------------------------------------------------------------ PARTE 5: cruce con el reporte
-- Solo si ya se genero el reporte de anomalias. Un caso NO JUSTIFICADO (6/7) que no aparezca en ninguna
-- regla de pago del reporte es un caso que el reporte deja escapar.
\echo '=== PARTE 5a. No justificados (6 y 7) vs reporte de anomalias ==='
SELECT v.veredicto, CASE WHEN v.es_ingles THEN 'Modulo de ingles' ELSE 'Carrera' END AS que, count(*) AS periodos,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM anomalias.anomalia a JOIN p_est k ON k.codigo = a.estudiante
           WHERE k.persona = v.persona AND a.periodo = v.periodo
             AND a.tipo IN ('MATRICULA_SIN_RECIBO', 'MATRICULA_RECIBO_SIN_PAGAR', 'PREMATRICULA_CON_NOTAS_APROBADAS',
                            'NOTAS_SIN_HISTORICO', 'INGLES_MODULO_SIN_PAGO',
                            'CURSO_Y_REPROBO_SIN_PAGO', 'CURSANDO_SIN_PAGO_PERIODO_ACTUAL'))) AS en_el_reporte,
       count(*) FILTER (WHERE v.aprobadas = 0) AS solo_reprobadas,
       count(*) FILTER (WHERE v.en_curso) AS en_curso
FROM p_veredicto v WHERE v.veredicto ~ '^[67]' GROUP BY 1, 2 ORDER BY 1, 2;

\echo '=== PARTE 5b. Casos que el reporte deja escapar ==='
SELECT ps.documento, ps.nombre, v.codigo, v.periodo, v.programa, v.estados_historico AS historico,
       v.cursadas, v.aprobadas, left(v.asignaturas, 60) AS asignaturas, v.en_curso
FROM p_veredicto v JOIN p_est ps ON ps.codigo = v.codigo
WHERE v.veredicto ~ '^[67]'
  AND NOT EXISTS (SELECT 1 FROM anomalias.anomalia a JOIN p_est k ON k.codigo = a.estudiante
                  WHERE k.persona = v.persona AND a.periodo = v.periodo
                    AND a.tipo IN ('MATRICULA_SIN_RECIBO', 'MATRICULA_RECIBO_SIN_PAGAR', 'PREMATRICULA_CON_NOTAS_APROBADAS',
                                   'NOTAS_SIN_HISTORICO', 'INGLES_MODULO_SIN_PAGO',
                            'CURSO_Y_REPROBO_SIN_PAGO', 'CURSANDO_SIN_PAGO_PERIODO_ACTUAL'))
ORDER BY v.es_ingles DESC, v.periodo;
