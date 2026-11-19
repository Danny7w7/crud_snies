-- =====================================================================================================
-- PAGO DE MATRICULA DE QUIENES TIENEN ASIGNATURAS EN UN PERIODO (SWA, solo lectura)
--
-- Responde: en el periodo, ¿quien tiene asignaturas vinculadas (con o sin nota) SIN un pago comprobado de la
-- matricula? Aparta los creditos (financiaciones) y marca las anomalias de los descuentos.
-- Se ejecuta directo sobre SWA (produccion) y solo hace SELECT. Lo usa `pagos_periodo.py`
-- (comando `migra_pagos_periodo`), que abre y verifica una conexión de solo lectura con autocommit.
--
-- PAGO COMPROBADO = dinero que entro a una cuenta real (pago_recibo.estado = 1 en Davivienda, Efecty, tarjeta,
-- PayGateway...). Las cuentas del banco 900 "VALORES X CONCEPTOS" NO son dinero: 9001 saldos a favor, 9003 valor
-- pagado en periodos anteriores, 9004 descuentos, 9005 menor cuantia (ajustes), 9006 convenios, 9007 becas.
-- pago_recibo.recibo es unico y comparte numeracion con recibo_swa.numero (tipo_recibo 0) y cuota.numero (1).
-- Las cuotas de AJUSTE (18) y de SALDO A FAVOR (7) nunca cuentan como dinero, aunque se registren en un banco.
--
-- Veredicto por persona (documento normalizado: varios codigos SWA = una persona) y programa (51 y 52 = uno):
--   1   PAGO COMPROBADO EN DINERO: el dinero cubre todo lo que no es descuento.
--   2x  CREDITO (apartado): 2A pagado en su totalidad, 2B al dia, 2C en mora, 2D sin ningun pago y con cuotas
--       vencidas, 2E sin ningun pago y sin cuotas vencidas, 2F solo falta el giro del tercero (ICETEX, convenio).
--   3x  SIN PAGO COMPROBADO: 3A sin recibo de matricula, 3B solo recibos anulados, 3C recibo sin pagar,
--       3D marcado pagado/financiado sin soporte, 3E pago parcial.
--   4x  CUBIERTO SIN DINERO: 4A descuento/beca del 100 %, 4B saldo a favor, 4C pago de un periodo anterior,
--       4D ajuste manual, 4E beca/convenio u otro concepto. `respaldo_en_dinero` dice si el año anterior quedo
--       dinero sin consumir (pago y no curso con nota) que alcance a cubrirlo.
--   5x  SIN PAGO PROPIO PERO CON OTRO PAGO: 5A matricula pagada en otro programa, 5B ingles con la carrera
--       pagada, 5C pago otro concepto academico (diplomado, creditos adicionales, vacacional).
-- Anomalias de descuentos (CTE `anom`): A1 descuento doble, A2 descuentos del 100 % o mas, A3 porcentaje que no
-- cuadra, A4 ajuste manual, A5 saldo a favor aplicado, A6 pago de periodo anterior, A7 beca/convenio sin dinero,
-- A8 cuotas que no suman el credito, A9 valor menor al habitual del programa, A10 genero saldo a favor.
-- =====================================================================================================
WITH
par AS (SELECT /*PERIODO*/20262::int AS periodo, to_char(current_date, 'YYYYMMDD')::int AS hoy),
-- registros de la misma persona con otro documento (el Centro de Idiomas crea al estudiante de carrera con un
-- documento ficticio de 4 digitos o mal digitado): documento normalizado del duplicado -> el real. Lo calcula
-- `pagos_periodo.unificar`; sin ese mapa cada documento representa una persona.
mapa(dup, real) AS (SELECT * FROM unnest(/*MAPA*/ARRAY[]::text[], ARRAY[]::text[])),
-- codigo -> persona: documento normalizado (mismo criterio que la depuracion; X...X son duplicados anulados)
est0 AS (
    SELECT e.codigo,
           regexp_replace(regexp_replace(regexp_replace(upper(p.identificacion), '^X([0-9])', '\1'), '([0-9])X$', '\1'),
                          '[^0-9A-Z]', '', 'g') AS nid,
           p.identificacion, regexp_replace(trim(concat_ws(' ', p.nombre, p.apellido)), '\s+', ' ', 'g') AS nombre
    FROM estudiante e JOIN persona p ON p.id = e.persona),
est AS (
    SELECT e.codigo, coalesce(m.real, e.nid) AS nid, coalesce(r.identificacion, e.identificacion) AS identificacion,
           coalesce(r.nombre, e.nombre) AS nombre, CASE WHEN m.dup IS NOT NULL THEN e.identificacion END AS doc_unificado
    FROM est0 e
    LEFT JOIN mapa m ON m.dup = e.nid
    LEFT JOIN (SELECT DISTINCT ON (nid) nid, identificacion, nombre FROM est0 WHERE nid IN (SELECT real FROM mapa)
               ORDER BY nid, identificacion) r ON r.nid = m.real),
-- pagos validos con su categoria: DINERO o el concepto sin dinero de la cuenta 900
pagx AS (
    SELECT p.recibo, p.tipo_recibo, p.valor, coalesce(p.saldo_a_favor, 0) AS saldo_generado,
           CASE WHEN bc.banco <> 900 AND bc.codigo <> 0 THEN 'DINERO'
                WHEN bc.codigo = 9001 THEN 'SALDO A FAVOR' WHEN bc.codigo = 9003 THEN 'PERIODO ANTERIOR'
                WHEN bc.codigo = 9004 THEN 'DESCUENTO' WHEN bc.codigo = 9005 THEN 'AJUSTE'
                WHEN bc.codigo = 9006 THEN 'CONVENIO' WHEN bc.codigo = 9007 THEN 'BECA'
                ELSE 'OTRO CONCEPTO' END AS categoria,
           p.fecha || ' ' || coalesce(nullif(trim(coalesce(b.nombre, '') || ' ' || coalesce(bc.nombre, '')), ''),
                                      'cuenta ' || p.banco_cuenta)
               || ' $' || round(p.valor::numeric) || coalesce(' "' || nullif(trim(p.descripcion), '') || '"', '')
               || ' (' || coalesce(nullif(split_part(p.log_insert, ';', 2), ''), 'sin usuario') || ')' AS detalle
    FROM pago_recibo p
    LEFT JOIN banco_cuenta bc ON bc.codigo = p.banco_cuenta
    LEFT JOIN banco b ON b.codigo = bc.banco
    WHERE p.estado = 1 AND p.tipo_recibo IN (0, 1)),
-- clase de cada tipo de cuota (catalogo tipo_cuota)
clase_cuota(tipo, clase) AS (
    SELECT t, 'ESTUDIANTE' FROM unnest(ARRAY[1, 2, 3, 4, 5, 8, 10, 11, 12, 13, 14, 15, 16, 17]) t
    UNION ALL SELECT t, 'TERCERO' FROM unnest(ARRAY[6, 38, 39, 40, 41, 44, 45, 46, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59,
                                                    60, 61, 62, 63, 64, 80, 81, 82, 86, 87, 88, 89, 90]) t
    UNION ALL SELECT t, 'DESCUENTO' FROM unnest(ARRAY[9, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34,
                                                      35, 36, 37, 83, 84, 85]) t
    UNION ALL SELECT 7, 'SALDO A FAVOR' UNION ALL SELECT 18, 'AJUSTE'),
-- conceptos de recibo que pagan una matricula (carrera e ingles) y otros cursos academicos
concepto_tipo(concepto, tipo) AS (
    SELECT c, 'MATRICULA' FROM unnest(ARRAY[0, 35, 41, 108, 109, 110, 14, 15, 16, 28, 40, 45, 29, 30, 31, 32, 33, 34,
                                            46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 18, 19]) c
    UNION ALL SELECT c, 'OTRO_ACADEMICO' FROM unnest(ARRAY[17, 20, 23, 36, 37, 38, 39, 100, 101, 102, 111, 112, 113, 114]) c),
-- UNIVERSO: asignaturas vinculadas en el periodo (R o P, con o sin nota) por persona y programa
reg AS (
    SELECT s.nid, CASE WHEN r.programa IN (51, 52) THEN 51 ELSE r.programa END AS prog,
           min(s.identificacion) AS documento, min(s.nombre) AS nombre,
           min(r.programa) AS programa, string_agg(DISTINCT r.estudiante, ', ') AS codigos,
           string_agg(DISTINCT s.doc_unificado, ', ') AS documentos_unificados,
           count(*) AS asignaturas,
           count(*) FILTER (WHERE r.estado = 'R' AND r.grupo >= 0) AS regulares,
           count(*) FILTER (WHERE r.estado = 'P') AS preinscritas,
           count(*) FILTER (WHERE r.grupo < 0) AS homologadas,
           count(*) FILTER (WHERE coalesce(r.definitiva, 0) > 0 OR EXISTS (
               SELECT 1 FROM calificacion c WHERE c.periodo = r.periodo AND c.programa = r.programa
                 AND c.asignatura = r.asignatura AND c.grupo = r.grupo AND c.estudiante = r.estudiante
                 AND coalesce(c.nota, 0) > 0)) AS con_nota,
           sum(coalesce(r.creditos, 0)) AS creditos_academicos,
           string_agg(DISTINCT a.nombre, ', ') AS nombres_asignaturas,
           -- quien inscribio (log_insert = fecha;usuario;ip; el usuario es el codigo si se inscribio el estudiante)
           string_agg(DISTINCT split_part(r.log_insert, ';', 2) || ' ' || left(r.log_insert, 10), ', ') AS inscrito_por
    FROM registro r JOIN est s ON s.codigo = r.estudiante
    LEFT JOIN asignatura a ON a.codigo = r.asignatura
    WHERE r.periodo = (SELECT periodo FROM par) AND r.estado IN ('R', 'P')
    GROUP BY 1, 2),
hist AS (
    SELECT s.nid, CASE WHEN h.programa IN (51, 52) THEN 51 ELSE h.programa END AS prog,
           string_agg(DISTINCT h.estado, '/') AS estado_historico,
           string_agg(DISTINCT nullif(h.recibo_pago, ''), ', ') AS recibo_legalizacion,
           max(h.fecha_hora) AS fecha_legalizacion
    FROM historico h JOIN est s ON s.codigo = h.estudiante
    WHERE h.periodo = (SELECT periodo FROM par)
    GROUP BY 1, 2),
-- recibos de las personas del universo en el periodo y en el año anterior (evidencia de periodos anteriores)
recx AS (
    SELECT s.nid, x.*, CASE WHEN x.programa IN (51, 52) THEN 51 ELSE x.programa END AS prog,
           coalesce(ct.tipo, 'OTRO') AS tipo_concepto
    FROM recibo_swa x
    JOIN est s ON s.codigo = x.estudiante
    LEFT JOIN concepto_tipo ct ON ct.concepto = x.concepto_cambio
    WHERE x.periodo BETWEEN (SELECT periodo FROM par) - 10 AND (SELECT periodo FROM par)
      AND s.nid IN (SELECT nid FROM reg)),
-- cada cuota de esos recibos y como quedo cubierta: dinero real o el concepto que la cubrio sin dinero
cob AS (
    SELECT cr.recibo, cr.numero AS credito, q.numero, q.tipo, coalesce(cc.clase, 'OTRO') AS clase, tc.descripcion,
           q.estado, q.fecha, coalesce(q.valor, 0) AS valor, trim(q.observacion) AS observacion, q.ingresado_por,
           p.categoria, p.detalle, coalesce(p.saldo_generado, 0) AS pago_saldo_generado,
           CASE WHEN coalesce(cc.clase, 'OTRO') IN ('ESTUDIANTE', 'TERCERO', 'OTRO') AND p.categoria = 'DINERO'
                THEN p.valor ELSE 0 END AS cubre_dinero,
           CASE WHEN cc.clase = 'DESCUENTO' AND q.estado <> 2 AND q.valor > 0 THEN q.valor
                WHEN cc.clase IN ('SALDO A FAVOR', 'AJUSTE') AND q.valor > 0 THEN coalesce(p.valor, 0)
                WHEN p.categoria <> 'DINERO' THEN p.valor ELSE 0 END AS cubre_sin_dinero,
           CASE WHEN cc.clase IN ('DESCUENTO', 'SALDO A FAVOR', 'AJUSTE') THEN cc.clase ELSE p.categoria END AS concepto,
           coalesce(cc.clase, 'OTRO') IN ('ESTUDIANTE', 'TERCERO') AND q.estado NOT IN (2, 10) AND q.valor > 0 AS pendiente
    FROM credito cr
    JOIN recx x ON x.numero = cr.recibo
    JOIN cuota q ON q.credito = cr.numero
    LEFT JOIN clase_cuota cc ON cc.tipo = q.tipo
    LEFT JOIN tipo_cuota tc ON tc.codigo = q.tipo
    LEFT JOIN pagx p ON p.recibo = q.numero AND p.tipo_recibo = 1),
cred AS (
    SELECT recibo,
           string_agg(DISTINCT credito::text, ', ') AS creditos,
           bool_or(clase IN ('ESTUDIANTE', 'TERCERO') AND estado <> 2 AND valor > 0) AS credito_vigente,
           sum(cubre_dinero) AS cuotas_dinero,
           sum(cubre_sin_dinero) FILTER (WHERE concepto = 'DESCUENTO') AS sd_descuento,
           sum(cubre_sin_dinero) FILTER (WHERE concepto = 'SALDO A FAVOR') AS sd_saldo,
           sum(cubre_sin_dinero) FILTER (WHERE concepto = 'PERIODO ANTERIOR') AS sd_anterior,
           sum(cubre_sin_dinero) FILTER (WHERE concepto = 'AJUSTE') AS sd_ajuste,
           sum(cubre_sin_dinero) FILTER (WHERE concepto IN ('BECA', 'CONVENIO', 'OTRO CONCEPTO')) AS sd_otro,
           sum(valor) FILTER (WHERE clase = 'ESTUDIANTE' AND estado <> 2) AS est_valor,
           sum(cubre_dinero) FILTER (WHERE clase = 'ESTUDIANTE') AS est_pagado,
           sum(valor) FILTER (WHERE pendiente AND clase = 'ESTUDIANTE' AND fecha < (SELECT hoy FROM par)) AS est_vencido,
           count(*) FILTER (WHERE pendiente AND clase = 'ESTUDIANTE' AND fecha < (SELECT hoy FROM par)) AS est_cuotas_vencidas,
           min(fecha) FILTER (WHERE pendiente AND clase = 'ESTUDIANTE' AND fecha < (SELECT hoy FROM par)) AS vencida_desde,
           sum(valor) FILTER (WHERE pendiente AND clase = 'ESTUDIANTE' AND fecha >= (SELECT hoy FROM par)) AS est_por_vencer,
           sum(valor) FILTER (WHERE clase = 'TERCERO' AND estado <> 2) AS ter_valor,
           sum(cubre_dinero) FILTER (WHERE clase = 'TERCERO') AS ter_pagado,
           sum(valor) FILTER (WHERE pendiente AND clase = 'TERCERO') AS ter_pendiente,
           string_agg(DISTINCT descripcion, ', ') FILTER (WHERE clase = 'TERCERO' AND estado <> 2) AS terceros,
           sum(valor) FILTER (WHERE clase = 'DESCUENTO' AND estado <> 2) AS desc_cuotas,
           string_agg(DISTINCT descripcion || ' $' || round(valor::numeric) || ' (' || coalesce(ingresado_por, 'sin usuario')
                               || coalesce(', "' || nullif(observacion, '') || '"', '') || ')', '; ')
               FILTER (WHERE clase = 'DESCUENTO' AND estado <> 2) AS desc_cuotas_detalle,
           sum(valor) FILTER (WHERE clase = 'DESCUENTO' AND estado <> 2 AND descripcion ~ '\d+%') AS desc_cuotas_pct_valor,
           sum(substring(descripcion FROM '(\d+)%')::numeric) FILTER (WHERE clase = 'DESCUENTO' AND estado <> 2) AS desc_cuotas_pct,
           -- el saldo a favor se registra dos veces (cuota 7 negativa y pago_recibo.saldo_a_favor): el mayor
           greatest(-coalesce(sum(valor) FILTER (WHERE clase = 'SALDO A FAVOR' AND estado <> 2 AND valor < 0), 0),
                    coalesce(sum(pago_saldo_generado), 0)) AS saldo_generado,
           string_agg(DISTINCT CASE WHEN cubre_sin_dinero > 0 AND clase <> 'DESCUENTO' THEN
                          coalesce(concepto, '?') || ' $' || round(cubre_sin_dinero::numeric)
                          || coalesce(' "' || nullif(observacion, '') || '"', '') || ' [' || coalesce(detalle, 'sin pago registrado') || ']' END,
                      '; ') AS sin_dinero_detalle,
           max(left(detalle, 8)) FILTER (WHERE cubre_dinero > 0) AS ultimo_pago,
           sum(valor) FILTER (WHERE estado <> 2) AS suma_cuotas
    FROM cob GROUP BY recibo),
-- cada recibo con toda la evidencia de pago (pago directo del recibo + cuotas de su credito)
rec AS (
    SELECT x.nid, x.periodo, x.numero, x.estudiante AS codigo, x.programa, x.prog, x.concepto_cambio AS concepto,
           k.nombre AS concepto_nombre, x.tipo_concepto, x.estado, coalesce(x.valor, 0) AS bruto,
           coalesce(x.valor_ordinaria, 0) AS neto, x.fecha AS fecha_recibo, x.ingresado_por AS recibo_generado_por,
           -- auxilios del recibo: los "SALDO A FAVOR*" son uso de saldo, no beneficio
           coalesce(CASE WHEN a1.nombre ILIKE 'SALDO A FAVOR%' THEN 0 ELSE x.valor_auxilio1 END, 0)
         + coalesce(CASE WHEN a2.nombre ILIKE 'SALDO A FAVOR%' THEN 0 ELSE x.valor_auxilio2 END, 0)
         + coalesce(CASE WHEN a3.nombre ILIKE 'SALDO A FAVOR%' THEN 0 ELSE x.valor_auxilio3 END, 0) AS desc_recibo,
           coalesce(CASE WHEN a1.nombre ILIKE 'SALDO A FAVOR%' THEN x.valor_auxilio1 END, 0)
         + coalesce(CASE WHEN a2.nombre ILIKE 'SALDO A FAVOR%' THEN x.valor_auxilio2 END, 0)
         + coalesce(CASE WHEN a3.nombre ILIKE 'SALDO A FAVOR%' THEN x.valor_auxilio3 END, 0) AS sf_recibo,
           concat_ws('; ', CASE WHEN coalesce(x.valor_auxilio1, 0) <> 0 THEN a1.nombre || ' $' || round(x.valor_auxilio1::numeric) END,
                           CASE WHEN coalesce(x.valor_auxilio2, 0) <> 0 THEN a2.nombre || ' $' || round(x.valor_auxilio2::numeric) END,
                           CASE WHEN coalesce(x.valor_auxilio3, 0) <> 0 THEN a3.nombre || ' $' || round(x.valor_auxilio3::numeric) END)
               AS desc_recibo_detalle,
           -- auxilio porcentual del recibo cuyo valor no corresponde al porcentaje del catalogo (sobre el bruto)
           coalesce((a1.tipo_valor = 1 AND a1.valor > 0 AND abs(coalesce(x.valor_auxilio1, 0) - a1.valor / 100 * x.valor) > 1000)
                 OR (a2.tipo_valor = 1 AND a2.valor > 0 AND abs(coalesce(x.valor_auxilio2, 0) - a2.valor / 100 * x.valor) > 1000)
                 OR (a3.tipo_valor = 1 AND a3.valor > 0 AND abs(coalesce(x.valor_auxilio3, 0) - a3.valor / 100 * x.valor) > 1000),
                    false) AS aux_pct_no_cuadra,
           CASE WHEN p.categoria = 'DINERO' THEN p.valor ELSE 0 END AS pago_directo,
           CASE WHEN p.categoria <> 'DINERO' THEN p.categoria END AS pago_directo_concepto,
           CASE WHEN p.categoria <> 'DINERO' THEN p.valor ELSE 0 END AS pago_directo_sin_dinero,
           p.detalle AS pago_directo_detalle, coalesce(p.saldo_generado, 0) AS pago_directo_saldo_generado,
           (SELECT sum(c2.valor) FROM credito c2 WHERE c2.recibo = x.numero
              AND EXISTS (SELECT 1 FROM cuota q2 WHERE q2.credito = c2.numero AND q2.estado <> 2)) AS valor_creditos,
           c.*
    FROM recx x
    LEFT JOIN concepto_cambio k ON k.codigo = x.concepto_cambio
    LEFT JOIN auxilio a1 ON a1.codigo = nullif(x.tipo_auxilio1, 0)
    LEFT JOIN auxilio a2 ON a2.codigo = nullif(x.tipo_auxilio2, 0)
    LEFT JOIN auxilio a3 ON a3.codigo = nullif(x.tipo_auxilio3, 0)
    LEFT JOIN pagx p ON p.recibo = x.numero AND p.tipo_recibo = 0
    LEFT JOIN cred c ON c.recibo = x.numero),
-- cobertura y veredicto de cada recibo
rv AS (
    SELECT r.*, v.*,
           CASE
             WHEN r.estado = 2 AND NOT coalesce(r.credito_vigente, false) AND v.dinero + v.sin_dinero = 0 THEN '9 ANULADO'
             WHEN r.neto <= 1000 OR v.dinero + v.sin_dinero >= r.neto - 1000 THEN
               CASE WHEN v.dinero >= 1000 AND v.dinero >= v.a_pagar_en_dinero - 1000 AND r.creditos IS NOT NULL
                        THEN '2A CRÉDITO PAGADO EN SU TOTALIDAD'
                    WHEN v.dinero >= 1000 AND v.dinero >= v.a_pagar_en_dinero - 1000 THEN '1 PAGO COMPROBADO EN DINERO'
                    WHEN v.a_pagar_en_dinero <= 1000 THEN '4A CUBIERTO 100 % POR DESCUENTO/BECA'
                    WHEN v.ajuste > 0 THEN '4D CUBIERTO POR AJUSTE MANUAL'
                    WHEN v.anterior > 0 THEN '4C CUBIERTO CON PAGO DE UN PERIODO ANTERIOR'
                    WHEN v.saldo > 0 THEN '4B CUBIERTO CON SALDO A FAVOR'
                    ELSE '4E CUBIERTO POR CONVENIO/BECA/OTRO CONCEPTO' END
             WHEN coalesce(r.credito_vigente, false) THEN
               CASE WHEN v.dinero < 1000 AND coalesce(r.est_vencido, 0) > 0 THEN '2D CRÉDITO SIN NINGÚN PAGO, CON CUOTAS VENCIDAS'
                    WHEN v.dinero < 1000 AND coalesce(r.est_por_vencer, 0) > 0 THEN '2E CRÉDITO SIN NINGÚN PAGO, CUOTAS AÚN SIN VENCER'
                    WHEN coalesce(r.est_vencido, 0) > 0 THEN '2C CRÉDITO EN MORA (pagó una parte)'
                    WHEN coalesce(r.est_por_vencer, 0) > 0 THEN '2B CRÉDITO AL DÍA (cuotas por vencer)'
                    WHEN coalesce(r.ter_pendiente, 0) > 0 THEN '2F CRÉDITO: FALTA EL GIRO DEL TERCERO (ICETEX/convenio/caja)'
                    ELSE '3E PAGO PARCIAL' END
             WHEN v.dinero >= 1000 THEN '3E PAGO PARCIAL'
             WHEN r.estado IN (1, 10) THEN '3D MARCADO PAGADO/FINANCIADO SIN SOPORTE DE PAGO'
             ELSE '3C RECIBO GENERADO SIN PAGAR' END AS veredicto_recibo
    FROM rec r
    CROSS JOIN LATERAL (
        SELECT d.*, d.descuento + d.saldo + d.anterior + d.ajuste + d.otro AS sin_dinero,
               r.neto - d.descuento AS a_pagar_en_dinero
        FROM (SELECT r.pago_directo + coalesce(r.cuotas_dinero, 0) AS dinero,
                     coalesce(r.sd_descuento, 0) + CASE WHEN r.pago_directo_concepto = 'DESCUENTO' THEN r.pago_directo_sin_dinero ELSE 0 END AS descuento,
                     coalesce(r.sd_saldo, 0) + CASE WHEN r.pago_directo_concepto = 'SALDO A FAVOR' THEN r.pago_directo_sin_dinero ELSE 0 END AS saldo,
                     coalesce(r.sd_anterior, 0) + CASE WHEN r.pago_directo_concepto = 'PERIODO ANTERIOR' THEN r.pago_directo_sin_dinero ELSE 0 END AS anterior,
                     coalesce(r.sd_ajuste, 0) + CASE WHEN r.pago_directo_concepto = 'AJUSTE' THEN r.pago_directo_sin_dinero ELSE 0 END AS ajuste,
                     coalesce(r.sd_otro, 0) + CASE WHEN r.pago_directo_concepto IN ('BECA', 'CONVENIO', 'OTRO CONCEPTO')
                                                   THEN r.pago_directo_sin_dinero ELSE 0 END AS otro,
                     greatest(coalesce(r.saldo_generado, 0), r.pago_directo_saldo_generado) AS saldo_generado_recibo) d) v),
-- año anterior por persona, programa y periodo: dinero real, matricula neta, saldo generado y lo cursado con nota
-- (una asignatura inscrita con nota 0 no se curso: p. ej. el ingles que no se pudo tomar)
cursado_ant AS (
    SELECT s.nid, CASE WHEN g.programa IN (51, 52) THEN 51 ELSE g.programa END AS prog, g.periodo,
           count(*) AS cursadas, count(*) FILTER (WHERE coalesce(g.definitiva, 0) > 0) AS con_nota
    FROM registro g JOIN est s ON s.codigo = g.estudiante
    WHERE g.periodo BETWEEN (SELECT periodo FROM par) - 10 AND (SELECT periodo FROM par) - 1
      AND g.estado = 'R' AND g.grupo >= 0 AND s.nid IN (SELECT nid FROM reg)
    GROUP BY 1, 2, 3),
ant AS (
    SELECT a.*, coalesce(c.cursadas, 0) AS cursadas, coalesce(c.con_nota, 0) AS con_nota,
           greatest(a.dinero - CASE WHEN c.con_nota > 0 THEN coalesce(a.neto_matricula, 0) ELSE 0 END, 0) AS sin_consumir
    FROM (SELECT nid, prog, periodo, sum(dinero) AS dinero,
                 sum(neto) FILTER (WHERE tipo_concepto = 'MATRICULA' AND estado <> 2) AS neto_matricula,
                 sum(saldo_generado_recibo) AS saldo_generado
          FROM rv WHERE periodo < (SELECT periodo FROM par) GROUP BY 1, 2, 3) a
    LEFT JOIN cursado_ant c ON c.nid = a.nid AND c.prog = a.prog AND c.periodo = a.periodo),
ant_txt AS (
    SELECT nid, prog, sum(sin_consumir) AS dinero_sin_consumir_anterior,
           string_agg((periodo / 10) || '-' || (periodo % 10) || ': pagó $' || round(dinero::numeric)
                      || ', matrícula $' || coalesce(round(neto_matricula::numeric), 0)
                      || ', inscribió ' || cursadas || ' asignatura(s), ' || con_nota || ' con nota'
                      || CASE WHEN saldo_generado > 0 THEN ', generó saldo a favor $' || round(saldo_generado::numeric) ELSE '' END,
                      '; ' ORDER BY periodo) AS periodos_anteriores
    FROM ant GROUP BY 1, 2),
-- recibos del periodo en texto: los de matricula por programa y los demas por persona
rvp AS (SELECT * FROM rv WHERE periodo = (SELECT periodo FROM par)),
txt_matricula AS (
    SELECT nid, prog, string_agg(numero || ' ' || coalesce(concepto_nombre, '') || ' (prog ' || programa || '): '
                                 || veredicto_recibo, '; ' ORDER BY numero) AS txt
    FROM rvp WHERE tipo_concepto = 'MATRICULA' GROUP BY 1, 2),
txt_otros AS (
    SELECT nid, string_agg(numero || ' ' || coalesce(concepto_nombre, '') || ' (prog ' || programa || '): '
                           || veredicto_recibo, '; ' ORDER BY numero) AS txt,
           string_agg(CASE WHEN tipo_concepto = 'OTRO_ACADEMICO' AND veredicto_recibo ~ '^(1|2A|2B|2C|2F)'
                           THEN numero || ' ' || concepto_nombre || ': ' || veredicto_recibo END, '; ') AS pago_otro_concepto
    FROM rvp WHERE tipo_concepto <> 'MATRICULA' GROUP BY 1),
-- matriculas de carrera pagadas o financiadas (para quien no tiene pago en su programa)
carrera_pagada AS (
    SELECT nid, prog, string_agg(numero || ' (prog ' || programa || '): ' || veredicto_recibo, '; ') AS txt
    FROM rvp WHERE tipo_concepto = 'MATRICULA' AND prog <> 51 AND veredicto_recibo ~ '^(1|2A|2B|2C|2F)'
    GROUP BY 1, 2),
-- mejor recibo de matricula por persona y programa en el periodo (el mas cubierto)
mejor AS (
    SELECT DISTINCT ON (nid, prog) *
    FROM rvp WHERE tipo_concepto = 'MATRICULA'
    ORDER BY nid, prog,
             CASE split_part(veredicto_recibo, ' ', 1)
                 WHEN '1' THEN 1 WHEN '2A' THEN 2 WHEN '2B' THEN 3 WHEN '2F' THEN 4 WHEN '2C' THEN 5 WHEN '2E' THEN 6
                 WHEN '2D' THEN 7 WHEN '4A' THEN 8 WHEN '4B' THEN 9 WHEN '4C' THEN 10 WHEN '4E' THEN 11 WHEN '4D' THEN 12
                 WHEN '3E' THEN 13 WHEN '3D' THEN 14 WHEN '3C' THEN 15 ELSE 16 END,
             numero DESC),
base AS (
    SELECT g.*, h.estado_historico, h.recibo_legalizacion, h.fecha_legalizacion,
           m.numero AS recibo, m.codigo AS codigo_recibo, m.veredicto_recibo, m.estado AS estado_recibo, m.concepto_nombre,
           m.fecha_recibo, m.recibo_generado_por, m.bruto, m.neto, m.desc_recibo, m.desc_cuotas, m.descuento,
           m.desc_recibo_detalle, m.desc_cuotas_detalle, m.a_pagar_en_dinero, m.dinero, m.sin_dinero,
           greatest(m.neto - m.dinero - m.sin_dinero, 0) AS faltante, m.saldo, m.anterior, m.ajuste, m.otro, m.sf_recibo,
           m.pago_directo_detalle, m.sin_dinero_detalle, m.ultimo_pago, m.saldo_generado_recibo,
           m.creditos, m.est_valor, m.est_pagado, m.est_vencido, m.est_cuotas_vencidas, m.vencida_desde, m.est_por_vencer,
           m.ter_valor, m.ter_pagado, m.ter_pendiente, m.terceros,
           concat_ws('; ', tm.txt, tx.txt) AS recibos_del_periodo,
           (SELECT string_agg(c.txt, '; ') FROM carrera_pagada c WHERE c.nid = g.nid AND c.prog <> g.prog) AS pago_otra_carrera,
           tx.pago_otro_concepto, a.periodos_anteriores, a.dinero_sin_consumir_anterior
    FROM reg g
    LEFT JOIN hist h ON h.nid = g.nid AND h.prog = g.prog
    LEFT JOIN mejor m ON m.nid = g.nid AND m.prog = g.prog
    LEFT JOIN txt_matricula tm ON tm.nid = g.nid AND tm.prog = g.prog
    LEFT JOIN txt_otros tx ON tx.nid = g.nid
    LEFT JOIN ant_txt a ON a.nid = g.nid AND a.prog = g.prog),
-- veredicto final por persona y programa
fin AS (
    SELECT CASE
             WHEN b.veredicto_recibo IS NULL OR b.veredicto_recibo ~ '^(3|9)' THEN
               CASE WHEN b.prog <> 51 AND b.pago_otra_carrera IS NOT NULL THEN '5A MATRÍCULA PAGADA EN OTRO PROGRAMA'
                    WHEN b.prog = 51 AND b.pago_otra_carrera IS NOT NULL THEN '5B INGLÉS SIN PAGO PROPIO, CARRERA PAGADA'
                    WHEN b.pago_otro_concepto IS NOT NULL THEN '5C PAGÓ OTRO CONCEPTO ACADÉMICO (diplomado, créditos adic., vacacional)'
                    WHEN b.veredicto_recibo IS NULL THEN '3A SIN RECIBO DE MATRÍCULA'
                    WHEN b.veredicto_recibo ~ '^9' THEN '3B SOLO RECIBOS ANULADOS'
                    ELSE b.veredicto_recibo END
             ELSE b.veredicto_recibo END AS veredicto,
           CASE WHEN b.veredicto_recibo ~ '^4[BCDE]' THEN
                  CASE WHEN coalesce(b.dinero_sin_consumir_anterior, 0) >= b.sin_dinero - b.descuento - 1000
                         THEN 'SÍ: quedó dinero sin consumir el año anterior que alcanza a cubrirlo'
                       WHEN coalesce(b.dinero_sin_consumir_anterior, 0) > 1000
                         THEN 'PARCIAL: el dinero sin consumir del año anterior no alcanza a cubrirlo'
                       ELSE 'NO: no quedó dinero sin consumir el año anterior' END
           END AS respaldo_en_dinero,
           b.*
    FROM base b),
-- ------------------------------------------------------------------------- anomalias de los descuentos
moda AS (
    SELECT programa, mode() WITHIN GROUP (ORDER BY bruto) AS bruto_habitual
    FROM rvp WHERE concepto = 0 AND veredicto_recibo NOT LIKE '9%' GROUP BY 1),
mat AS (
    SELECT r.*, f.documento, f.nombre, f.codigos, f.veredicto AS veredicto_persona, f.asignaturas, f.con_nota,
           f.creditos_academicos, f.periodos_anteriores,
           coalesce(f.dinero_sin_consumir_anterior, 0) AS dinero_sin_consumir_anterior, m.bruto_habitual
    FROM rvp r
    JOIN fin f ON f.nid = r.nid AND f.prog = r.prog
    LEFT JOIN moda m ON m.programa = r.programa
    WHERE r.tipo_concepto = 'MATRICULA' AND r.veredicto_recibo NOT LIKE '9%'),
anom AS (
    SELECT 'A1 DESCUENTO DOBLE (en el recibo y otra vez como cuota del crédito)' AS anomalia,
           CASE WHEN desc_recibo + descuento >= bruto - 1000 THEN 'ALTA' ELSE 'MEDIA' END AS severidad, mat.*,
           desc_recibo + descuento AS valor_anomalia,
           'Recibo: ' || desc_recibo_detalle || ' | Crédito: ' || desc_cuotas_detalle
               || ' | Total descuentos ' || round(100 * (desc_recibo + descuento) / nullif(bruto, 0)) || ' % del valor bruto' AS explicacion
    FROM mat WHERE desc_recibo > 0 AND coalesce(desc_cuotas, 0) > 0
    UNION ALL
    SELECT 'A2 DESCUENTOS QUE CUBREN EL 100 % O MÁS DEL VALOR', 'ALTA', mat.*, desc_recibo + descuento,
           'Descuentos ' || round(100 * (desc_recibo + descuento) / nullif(bruto, 0)) || ' % del valor bruto. Recibo: '
               || coalesce(nullif(desc_recibo_detalle, ''), 'ninguno') || ' | Crédito: ' || coalesce(desc_cuotas_detalle, 'ninguno')
    FROM mat WHERE bruto > 0 AND desc_recibo + descuento >= bruto - 1000
    UNION ALL
    SELECT 'A3 PORCENTAJE DEL DESCUENTO NO CUADRA CON SU NOMBRE', 'MEDIA', mat.*, coalesce(desc_cuotas_pct_valor, desc_recibo),
           CASE WHEN aux_pct_no_cuadra THEN 'Auxilio del recibo: ' || desc_recibo_detalle || ' sobre un bruto de $'
                                            || round(bruto::numeric) || '. ' ELSE '' END
           || CASE WHEN desc_cuotas_pct_valor IS NOT NULL AND abs(desc_cuotas_pct_valor - desc_cuotas_pct / 100 * bruto) > 1000
                   THEN 'Cuota de descuento: ' || desc_cuotas_detalle || '; el ' || desc_cuotas_pct || ' % del bruto ($'
                        || round(bruto::numeric) || ') sería $' || round((desc_cuotas_pct / 100 * bruto)::numeric) ELSE '' END
    FROM mat WHERE aux_pct_no_cuadra
                OR (desc_cuotas_pct_valor IS NOT NULL AND abs(desc_cuotas_pct_valor - desc_cuotas_pct / 100 * bruto) > 1000)
    UNION ALL
    SELECT 'A4 AJUSTE MANUAL QUE CUBRE LA MATRÍCULA SIN DINERO',
           CASE WHEN ajuste > dinero_sin_consumir_anterior + 1000 AND ajuste >= neto - 1000 THEN 'ALTA'
                WHEN ajuste > dinero_sin_consumir_anterior + 1000 THEN 'MEDIA' ELSE 'BAJA' END, mat.*, ajuste, sin_dinero_detalle
    FROM mat WHERE ajuste > 0
    UNION ALL
    SELECT 'A5 SALDO A FAVOR APLICADO A LA MATRÍCULA',
           CASE WHEN saldo + sf_recibo > dinero_sin_consumir_anterior + 1000 THEN 'ALTA' ELSE 'BAJA' END, mat.*, saldo + sf_recibo,
           coalesce(sin_dinero_detalle, 'Auxilio de saldo a favor en el recibo $' || round(sf_recibo::numeric))
    FROM mat WHERE saldo + sf_recibo > 0
    UNION ALL
    SELECT 'A6 PAGO DE UN PERIODO ANTERIOR APLICADO',
           CASE WHEN anterior > dinero_sin_consumir_anterior + 1000 THEN 'ALTA' ELSE 'BAJA' END, mat.*, anterior, sin_dinero_detalle
    FROM mat WHERE anterior > 0
    UNION ALL
    SELECT 'A7 BECA O CONVENIO SIN DINERO (cuentas de becas/convenios)', 'MEDIA', mat.*, otro, sin_dinero_detalle
    FROM mat WHERE otro > 0
    UNION ALL
    SELECT 'A8 LAS CUOTAS DEL CRÉDITO NO SUMAN EL VALOR DEL CRÉDITO', 'MEDIA', mat.*, suma_cuotas - valor_creditos,
           'Crédito(s) ' || creditos || ' por $' || round(valor_creditos::numeric) || '; sus cuotas vigentes suman $'
               || round(suma_cuotas::numeric)
    FROM mat WHERE creditos IS NOT NULL AND abs(coalesce(suma_cuotas, 0) - coalesce(valor_creditos, 0)) > 1000
    UNION ALL
    SELECT 'A9 VALOR DE MATRÍCULA MENOR AL HABITUAL DEL PROGRAMA', 'INFO', mat.*, bruto_habitual - bruto,
           'Valor bruto $' || round(bruto::numeric) || ' frente a $' || round(bruto_habitual::numeric) || ' habitual del programa ('
               || round(100 * bruto / nullif(bruto_habitual, 0)) || ' %). Créditos académicos inscritos: ' || creditos_academicos
    FROM mat WHERE concepto = 0 AND bruto > 0 AND bruto < bruto_habitual - 1000
    UNION ALL
    SELECT 'A10 GENERÓ SALDO A FAVOR EN EL PERIODO (lo cubierto superó el valor)', 'BAJA', mat.*, saldo_generado_recibo,
           'Saldo a favor generado $' || round(saldo_generado_recibo::numeric) || '. Cubierto con dinero $' || round(dinero::numeric)
               || ', sin dinero $' || round(sin_dinero::numeric) || ' sobre un neto de $' || round(neto::numeric)
    FROM mat WHERE saldo_generado_recibo >= 1000)
-- ==== CONSULTA FINAL (para ver las anomalías de descuentos cambie "fin" por "anom") ====
SELECT * FROM fin ORDER BY veredicto, programa, nombre
