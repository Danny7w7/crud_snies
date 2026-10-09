-- Esquemas de trabajo de la migracion SWA -> ERP.
-- migra.*    : se reconstruye completo en cada depuracion.
-- revision.* : conserva las decisiones humanas entre ejecuciones.

CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE SCHEMA IF NOT EXISTS migra;
CREATE SCHEMA IF NOT EXISTS revision;

-- ---------------------------------------------------------------- migra
DROP TABLE IF EXISTS migra.persona_fuente, migra.persona, migra.estudiante,
    migra.periodo, migra.asignatura, migra.pensum, migra.pensum_asignatura,
    migra.prerrequisito, migra.matricula, migra.nota, migra.homologacion_ingles,
    migra.vinculo, migra.descarte CASCADE;

-- Una fila por registro de `persona` de SWA dentro del alcance.
CREATE TABLE migra.persona_fuente (
    swa_id              integer PRIMARY KEY,
    nid                 text NOT NULL,          -- documento normalizado
    identificacion      text,
    tipo_swa            integer,
    nombre              text,
    apellido            text,
    email               text,
    telefono            text,
    celular             text,
    fecha_nacimiento    integer,
    marcada_x           boolean NOT NULL DEFAULT false,  -- SWA marca duplicados anulados con X...X
    ultimo_periodo      integer,
    -- MATRICULA: tuvo al menos una matricula registrada (M) en cualquier periodo
    -- SOLO_NOTAS: nunca tuvo M pero tiene notas calificadas
    -- PREMATRICULA: solo prematricula (P): liquidada y nunca legalizada, sin notas
    origen              text NOT NULL
);

-- Persona depurada: una por documento.
CREATE TABLE migra.persona (
    nid                 text PRIMARY KEY,
    swa_ids             integer[] NOT NULL,
    swa_id_principal    integer NOT NULL,
    tipo_documento_id   bigint,
    nro_documento       bigint,
    primer_nombre       text,
    segundo_nombre      text,
    primer_apellido     text,
    segundo_apellido    text,
    email               text,
    telefono            bigint,
    fecha_nacimiento    date,
    primer_periodo      integer,
    primer_programa     integer,
    prefijo             text,                  -- solo si hay que generar codigo ERP (respaldo)
    codigo_swa          text,                  -- codigo SWA que sera el id de la persona en el ERP
    codigos_swa         text[] NOT NULL DEFAULT '{}',  -- todos sus codigos SWA (mas reciente primero)
    erp_persona_id      bigint,               -- persona ya existente en el ERP
    accion              text,                  -- CREAR | VINCULAR
    estado              text NOT NULL DEFAULT 'LISTA',  -- LISTA | REVISION | EXCLUIDA
    transformaciones    text[] NOT NULL DEFAULT '{}',  -- correcciones aplicadas
    alertas             text[] NOT NULL DEFAULT '{}'   -- calidad: datos faltantes, no se corrigen
);

CREATE TABLE migra.estudiante (
    codigo              text PRIMARY KEY,
    nid                 text NOT NULL,
    programa            integer NOT NULL,
    pensum              integer,
    periodo_ingreso     integer,
    estado_swa          text
);

CREATE TABLE migra.periodo (
    codigo              integer PRIMARY KEY,
    nombre              text NOT NULL,
    fecha_inicio        date,
    fecha_fin           date,
    erp_periodo_id      bigint
);

CREATE TABLE migra.asignatura (
    codigo              integer PRIMARY KEY,
    programa            integer NOT NULL,
    nombre              text NOT NULL,
    creditos            integer NOT NULL DEFAULT 0,
    ihs                 integer NOT NULL DEFAULT 0,
    erp_codigo          text NOT NULL,         -- codigo de la asignatura canonica en el ERP
    canonica            integer,               -- asignatura SWA que la representa (equivalencias)
    grupo_equivalencia  integer                -- grupo de asignaturas equivalentes (tabla SWA)
);

CREATE TABLE migra.pensum (
    programa            integer NOT NULL,
    codigo              integer NOT NULL,
    descripcion         text,
    erp_codigo          text NOT NULL,
    activo              boolean NOT NULL,
    periodo_codigo      integer,
    PRIMARY KEY (programa, codigo)
);

CREATE TABLE migra.pensum_asignatura (
    programa            integer NOT NULL,
    pensum              integer NOT NULL,
    asignatura          integer NOT NULL,
    nivel               integer NOT NULL,
    PRIMARY KEY (programa, pensum, asignatura)
);

CREATE TABLE migra.prerrequisito (
    programa            integer NOT NULL,
    pensum              integer NOT NULL,
    asignatura          integer NOT NULL,
    requisito           integer NOT NULL,
    tipo                text NOT NULL,
    PRIMARY KEY (programa, pensum, asignatura, requisito, tipo)
);

-- Una matricula por (persona, programa, periodo).
CREATE TABLE migra.matricula (
    id                  bigserial PRIMARY KEY,
    nid                 text NOT NULL,
    programa            integer NOT NULL,
    periodo             integer NOT NULL,
    pensum              integer,
    codigo              text NOT NULL,        -- codigo de estudiante SWA usado
    codigos             text[] NOT NULL,
    nivel               integer NOT NULL DEFAULT 0,
    activa              boolean NOT NULL DEFAULT false,
    origen              text NOT NULL,        -- HISTORICO_M | INFERIDA_NOTAS
    estado              text NOT NULL DEFAULT 'LISTA',
    UNIQUE (nid, programa, periodo)
);

CREATE TABLE migra.nota (
    periodo             integer NOT NULL,
    programa            integer NOT NULL,
    asignatura          integer NOT NULL,
    grupo               integer NOT NULL,
    estudiante          text NOT NULL,
    nid                 text NOT NULL,
    definitiva          numeric(5,2),
    estado_definitiva   text,
    en_curso            boolean NOT NULL DEFAULT false,
    estado              text NOT NULL DEFAULT 'LISTA',
    PRIMARY KEY (periodo, programa, asignatura, grupo, estudiante)
);

CREATE TABLE migra.homologacion_ingles (
    nid                 text NOT NULL,
    programa            integer NOT NULL,
    modulo              integer NOT NULL,
    periodo             integer NOT NULL,
    definitiva          numeric(5,2),
    codigo              text NOT NULL,
    origen              text NOT NULL,        -- APROBADO | CENTRO_ACTUAL
    PRIMARY KEY (nid, programa, modulo)
);

CREATE TABLE migra.vinculo (
    nid                 text NOT NULL,
    programa            integer NOT NULL,
    primer_periodo      integer NOT NULL,
    ultimo_periodo      integer NOT NULL,
    activo              boolean NOT NULL,
    PRIMARY KEY (nid, programa)
);

-- Informacion apartada por regla (no se migra) con su motivo.
CREATE TABLE migra.descarte (
    id                  bigserial PRIMARY KEY,
    entidad             text NOT NULL,
    clave               text NOT NULL,
    motivo              text NOT NULL,
    con_valor           boolean NOT NULL,     -- true si se pierde informacion util
    datos               jsonb
);

-- ------------------------------------------------------------- revision
CREATE TABLE IF NOT EXISTS revision.caso (
    id                  bigserial PRIMARY KEY,
    tipo                text NOT NULL,
    clave               text NOT NULL,
    entidad             text NOT NULL,
    severidad           text NOT NULL,        -- ALTA | MEDIA | BAJA
    titulo              text NOT NULL,
    datos               jsonb NOT NULL,
    sugerencia          jsonb,
    opciones            text[] NOT NULL,
    filas_afectadas     integer NOT NULL DEFAULT 1,
    vigente             boolean NOT NULL DEFAULT true,
    decision            text,
    correccion          jsonb,
    comentario          text,
    decidido_en         timestamptz,
    UNIQUE (tipo, clave)
);

-- Lotes enviados al ERP y trazabilidad de lo creado.
CREATE TABLE IF NOT EXISTS migra.lote (
    id                  bigserial PRIMARY KEY,
    inicio              timestamptz NOT NULL DEFAULT now(),
    fin                 timestamptz,
    estado              text NOT NULL DEFAULT 'EN_PROCESO',
    resumen             jsonb,
    error               text
);

CREATE TABLE IF NOT EXISTS migra.mapa (
    entidad             text NOT NULL,
    clave               text NOT NULL,
    erp_id              bigint NOT NULL,
    lote_id             bigint NOT NULL REFERENCES migra.lote(id),
    creado              boolean NOT NULL DEFAULT true,  -- false: ya existia en el ERP (no se revierte)
    PRIMARY KEY (entidad, clave)
);

CREATE TABLE IF NOT EXISTS migra.depuracion_log (
    id                  bigserial PRIMARY KEY,
    fecha               timestamptz NOT NULL DEFAULT now(),
    metricas            jsonb NOT NULL
);

-- Preparacion del ERP antes del envio real (vacios detectados en el codigo del ERP).
CREATE TABLE IF NOT EXISTS migra.preparacion (
    clave               text PRIMARY KEY,
    orden               integer NOT NULL,
    titulo              text NOT NULL,
    detalle             text NOT NULL,
    bloqueante          boolean NOT NULL,
    hecho               boolean NOT NULL DEFAULT false,
    nota                text NOT NULL DEFAULT '',
    actualizado_en      timestamptz
);
INSERT INTO migra.preparacion (clave, orden, titulo, detalle, bloqueante) VALUES
 ('A', 1, 'El ERP muestra el historial de notas migrado',
  'Vistas del estudiante, prerrequisitos y certificado de notas deben leer academico_asignaturamatriculahistorico.', true),
 ('B', 2, 'Formularios públicos no exponen a estudiantes migrados',
  'solicitar_acceso, inscripcion y registrar_aspirante deben exigir verificación a quien ya es estudiante (SWA no trae fecha de expedición).', true),
 ('C', 3, 'Admisiones no se llena de estudiantes antiguos',
  'q_vinculos_gestion_admision debe excluir vínculos Estudiante que no vienen del flujo de admisión.', true),
 ('D', 4, 'Cuentas de usuario para estudiantes migrados',
  'Crear usuarios con rol Estudiante en bloque (asegurar_usuario_estudiante); login por documento.', true),
 ('E', 5, 'Rematrícula 2027-1 de estudiantes antiguos',
  'Volante pagado de un estudiante activo debe crear su matrícula 2027-1 con el semestre siguiente (no semestre 1).', true),
 ('F', 6, 'Notas 2026-2 se migran al cierre del periodo',
  'Al cerrar notas en SWA: re-copiar SWA, cambiar PERIODO_CORTE a 20271, re-depurar y enviar.', true),
 ('G', 7, 'Motivo MIGRACION_SWA en HomologacionIngles',
  'Agregar la opción a MotivoChoices para poder editar homologaciones migradas.', false),
 ('H', 8, 'Crédito público para estudiantes antiguos',
  'La solicitud pública de crédito no debe exigir fecha de expedición a estudiantes migrados.', false),
 ('I', 9, 'Equivalencia de pensum SWA → ERP para estudiantes antiguos',
  'Al rematricularse en 2027-1 quedan en el pensum activo del ERP (ej. TEC-RAD-01) con su semestre siguiente. Academia debe definir la equivalencia/homologación entre el pensum de SWA y el nuevo.', true),
 ('J', 10, 'Ensayo general aprobado',
  'Corte completo (migra_corte) con un dump de producción del día, verificar_migracion.py en verde y revisión en pantallas del ERP.', true),
 ('K', 11, 'Rama del ERP desplegada en producción',
  'feature/migracion-swa-soporte revisada, integrada a develop y desplegada en el ERP de producción antes de la ventana de corte.', true)
ON CONFLICT (clave) DO UPDATE SET orden = EXCLUDED.orden, titulo = EXCLUDED.titulo,
    detalle = EXCLUDED.detalle, bloqueante = EXCLUDED.bloqueante;
