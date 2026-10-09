"""Depuracion de la copia cruda de SWA (swa_raw) hacia el esquema `migra`.

Alcance: toda persona que tuvo una matricula en SWA en cualquier periodo, este hoy
activa o no (historico.estado = 'M'). Quien solo tiene prematricula (P) o solo notas
pasa a revision. Quien solo tiene un codigo de estudiante (inscrito/admitido que nunca
se matriculo), aspirantes, terceros y proveedores quedan fuera.

Cada ejecucion reconstruye `migra.*` desde `swa_raw` aplicando las reglas y
las decisiones humanas guardadas en `revision.caso`. Los casos que necesitan
una decision quedan en estado REVISION y no se envian al ERP.
"""
import difflib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from .conexiones import conectar_destino

SQL_DIR = Path(__file__).resolve().parent / 'sql'

CODIGO_SWA_RE = re.compile(r'^[0-9]{5,15}$')
# Primer periodo cuyas notas aun no cierran en SWA: sus notas se aplazan y se migran al cierre
# (al cerrar 2026-2 se cambia a 20271, se vuelve a copiar SWA y se re-depura).
PERIODO_CORTE = 20262
# Periodo activo del ERP: solo las matriculas desde aqui quedan vigentes (estado True) en el ERP.
PERIODO_ACTIVO_ERP = 20271
CENTROS_IDIOMAS = (51, 52)
# Idiomas y educacion continua: su codigo solo se usa como id si la persona no tiene uno de carrera
PROGRAMAS_EXTENSION = (50, 51, 52, 57, 60)
HOY = date.today()

# SWA tipo_identificacion -> erp_tipodocumentoidentidad.id
TIPO_SWA_A_ERP = {1: 1, 3: 2, 4: 3, 2: 7, 5: 6}
TIPO_CC, TIPO_TI = 1, 3
NOMBRE_TIPO_ERP = {1: 'CC', 2: 'CE', 3: 'TI', 4: 'PPT', 5: 'DE', 6: 'CA', 7: 'PS'}

PARTICULAS = {'DE', 'DEL', 'LA', 'LAS', 'LOS', 'SAN', 'SANTA', 'DA', 'DI', 'VAN', 'VON', 'Y', 'MC'}
EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')
# Correos de relleno usados en SWA (no son de la persona)
EMAILS_RELLENO = {'sinemail@corsalud.edu.co', 'sindefnir@corsalud.edu.co'}
DOMINIOS_RELLENO = ('@universidad.edu.co',)


# ------------------------------------------------------------------ helpers

def _sin_tildes(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')


def limpiar_nombre(texto):
    """Mayusculas, sin espacios dobles/tabs, apostrofos unificados, sin digitos."""
    if not texto:
        return ''
    texto = texto.replace(' ', ' ').replace('\t', ' ')
    texto = re.sub(r"[´`’‘]", "'", texto)
    texto = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ' \-]", ' ', texto)
    return re.sub(r'\s+', ' ', texto).strip().upper()


def _clave_nombre(nombre, apellido):
    return sorted(_sin_tildes(f'{nombre} {apellido}').replace("'", '').split())


def nombres_compatibles(a, b):
    """True si dos (nombre, apellido) describen razonablemente a la misma persona."""
    ta, tb = _clave_nombre(*a), _clave_nombre(*b)
    if not ta or not tb:
        return False
    if set(ta) <= set(tb) or set(tb) <= set(ta):
        return True
    return difflib.SequenceMatcher(None, ' '.join(ta), ' '.join(tb)).ratio() >= 0.85


def separar_apellidos(apellido):
    unidades, actual = [], []
    for token in apellido.split():
        actual.append(token)
        if token not in PARTICULAS:
            unidades.append(' '.join(actual))
            actual = []
    if actual:
        if unidades:
            unidades[-1] += ' ' + ' '.join(actual)
        else:
            unidades.append(' '.join(actual))
    if not unidades:
        return '', ''
    return unidades[0], ' '.join(unidades[1:])


def separar_nombres(nombre):
    partes = nombre.split()
    if not partes:
        return '', ''
    return partes[0], ' '.join(partes[1:])


def fecha_desde_entero(valor):
    if not valor or valor < 19300101 or valor > 20151231:
        return None
    try:
        return date(valor // 10000, valor // 100 % 100, valor % 100)
    except ValueError:
        return None


def edad(fecha):
    return HOY.year - fecha.year - ((HOY.month, HOY.day) < (fecha.month, fecha.day))


def telefono_valido(*valores):
    for valor in valores:
        digitos = re.sub(r'\D', '', valor or '')
        if digitos.startswith('57') and len(digitos) == 12:
            digitos = digitos[2:]
        if 7 <= len(digitos) <= 10 and len(set(digitos)) > 1:
            return int(digitos)
    return None


def email_valido(*valores):
    for valor in valores:
        valor = (valor or '').strip().lower().lstrip('@')
        if valor in EMAILS_RELLENO or valor.endswith(DOMINIOS_RELLENO):
            continue
        if EMAIL_RE.match(valor):
            return valor
    return ''


def nombre_periodo(codigo):
    return f'{codigo // 10}-{codigo % 10}'


# ----------------------------------------------------------- casos/revision

class Revision:
    """Registra casos en revision.caso conservando decisiones previas."""

    def __init__(self, cur):
        self.cur = cur
        cur.execute('UPDATE revision.caso SET vigente = false')
        cur.execute('SELECT tipo, clave, decision, correccion FROM revision.caso WHERE decision IS NOT NULL')
        self.decisiones = {(t, c): (d, corr or {}) for t, c, d, corr in cur.fetchall()}

    def decision(self, tipo, clave):
        return self.decisiones.get((tipo, str(clave)), (None, {}))

    def caso(self, tipo, clave, entidad, severidad, titulo, datos, opciones, sugerencia=None, filas=1):
        self.cur.execute("""
            INSERT INTO revision.caso (tipo, clave, entidad, severidad, titulo, datos,
                                       sugerencia, opciones, filas_afectadas, vigente)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, true)
            ON CONFLICT (tipo, clave) DO UPDATE SET
                entidad = EXCLUDED.entidad, severidad = EXCLUDED.severidad,
                titulo = EXCLUDED.titulo, datos = EXCLUDED.datos,
                sugerencia = EXCLUDED.sugerencia, opciones = EXCLUDED.opciones,
                filas_afectadas = EXCLUDED.filas_afectadas, vigente = true
        """, (tipo, str(clave), entidad, severidad, titulo, json.dumps(datos, default=str),
              json.dumps(sugerencia, default=str) if sugerencia is not None else None,
              opciones, filas))
        return self.decision(tipo, clave)


# ------------------------------------------------------------------- pasos

def _paso_persona_fuente(cur):
    cur.execute(r"""
        WITH matriculados AS (
            SELECT e.persona, max(h.periodo) AS ultimo
            FROM swa_raw.estudiante e
            JOIN swa_raw.historico h ON h.estudiante = e.codigo AND h.estado = 'M'
            GROUP BY e.persona
        ),
        solo_notas AS (
            SELECT e.persona, max(r.periodo) AS ultimo
            FROM swa_raw.estudiante e
            JOIN swa_raw.registro r ON r.estudiante = e.codigo AND r.estado = 'R'
            WHERE e.persona NOT IN (SELECT persona FROM matriculados)
            GROUP BY e.persona
        ),
        prematriculados AS (
            SELECT e.persona, max(h.periodo) AS ultimo
            FROM swa_raw.estudiante e
            JOIN swa_raw.historico h ON h.estudiante = e.codigo AND h.estado = 'P'
            WHERE e.persona NOT IN (SELECT persona FROM matriculados)
              AND e.persona NOT IN (SELECT persona FROM solo_notas)
            GROUP BY e.persona
        ),
        alcance AS (
            SELECT persona, ultimo, 'MATRICULA' AS origen FROM matriculados
            UNION ALL
            SELECT persona, ultimo, 'SOLO_NOTAS' FROM solo_notas
            UNION ALL
            SELECT persona, ultimo, 'PREMATRICULA' FROM prematriculados
        )
        INSERT INTO migra.persona_fuente
            (swa_id, nid, identificacion, tipo_swa, nombre, apellido, email, telefono,
             celular, fecha_nacimiento, marcada_x, ultimo_periodo, origen)
        SELECT p.id,
               regexp_replace(regexp_replace(upper(p.identificacion), '^X(?=[0-9])|(?<=[0-9])X$', '', 'g'),
                              '[^0-9A-Z]', '', 'g'),
               p.identificacion, p.tipo_identificacion, p.nombre, p.apellido,
               coalesce(nullif(trim(ult.email), ''), p.email), p.telefono, ult.celular,
               ult.fecha_nacimiento, upper(p.identificacion) ~ 'X', a.ultimo, a.origen
        FROM alcance a
        JOIN swa_raw.persona p ON p.id = a.persona
        LEFT JOIN LATERAL (
            SELECT e.email, e.celular, e.fecha_nacimiento
            FROM swa_raw.estudiante e
            WHERE e.persona = p.id
            ORDER BY e.periodo_ingreso DESC, e.codigo DESC
            LIMIT 1
        ) ult ON true
    """)
    # Fuera del alcance pero visibles en "Informacion apartada": solo tienen codigo de estudiante.
    cur.execute("""
        INSERT INTO migra.descarte (entidad, clave, motivo, con_valor, datos)
        SELECT 'persona', p.id::text, 'SOLO_CODIGO_SIN_MATRICULA', false,
               jsonb_build_object('swa_id', p.id, 'identificacion', p.identificacion,
                                  'nombre', p.nombre, 'apellido', p.apellido,
                                  'codigos', (SELECT jsonb_agg(jsonb_build_object(
                                                  'codigo', e.codigo, 'programa', e.programa,
                                                  'periodo_ingreso', e.periodo_ingreso))
                                              FROM swa_raw.estudiante e WHERE e.persona = p.id))
        FROM swa_raw.persona p
        WHERE p.id NOT IN (SELECT swa_id FROM migra.persona_fuente)
    """)


def _paso_personas(cur, rev):
    cur.execute("""
        SELECT swa_id, nid, identificacion, tipo_swa, nombre, apellido, email, telefono,
               celular, fecha_nacimiento, marcada_x, ultimo_periodo, origen
        FROM migra.persona_fuente ORDER BY nid, swa_id
    """)
    columnas = [d.name for d in cur.description]
    grupos = {}
    for fila in cur.fetchall():
        f = dict(zip(columnas, fila))
        grupos.setdefault(f['nid'], []).append(f)

    # primer periodo/programa con matricula M (para el prefijo del codigo ERP)
    cur.execute("""
        SELECT DISTINCT ON (e.persona) e.persona, h.periodo, e.programa
        FROM swa_raw.estudiante e
        JOIN swa_raw.historico h ON h.estudiante = e.codigo AND h.estado = 'M'
        ORDER BY e.persona, h.periodo, e.programa
    """)
    primero = {p: (per, prog) for p, per, prog in cur.fetchall()}
    cur.execute("""
        SELECT DISTINCT ON (e.persona) e.persona, r.periodo, e.programa
        FROM swa_raw.estudiante e JOIN swa_raw.registro r ON r.estudiante = e.codigo AND r.estado = 'R'
        ORDER BY e.persona, r.periodo, e.programa
    """)
    for p, per, prog in cur.fetchall():
        primero.setdefault(p, (per, prog))
    cur.execute("""
        SELECT DISTINCT ON (e.persona) e.persona, h.periodo, e.programa
        FROM swa_raw.estudiante e JOIN swa_raw.historico h ON h.estudiante = e.codigo AND h.estado = 'P'
        ORDER BY e.persona, h.periodo, e.programa
    """)
    for p, per, prog in cur.fetchall():
        primero.setdefault(p, (per, prog))

    # personas propias del ERP (las creadas por lotes anteriores de esta migracion no cuentan)
    cur.execute('SELECT id, tipo_documento_id, nro_documento, primer_nombre, segundo_nombre, '
                'primer_apellido, segundo_apellido FROM public.erp_persona p '
                "WHERE NOT EXISTS (SELECT 1 FROM migra.mapa m WHERE m.entidad = 'persona' "
                'AND m.creado AND m.erp_id = p.id)')
    erp = {}
    ids_erp = set()
    for pid, tipo, nro, n1, n2, a1, a2 in cur.fetchall():
        erp.setdefault(nro, []).append((pid, tipo, f'{n1} {n2}'.strip(), f'{a1} {a2}'.strip()))
        ids_erp.add(pid)

    # Codigos de estudiante SWA por persona con lo necesario para elegir el principal.
    cur.execute("""
        SELECT e.persona, e.codigo, e.programa, h.ult_m, h.ult_h, e.periodo_ingreso
        FROM swa_raw.estudiante e
        LEFT JOIN LATERAL (
            SELECT max(h.periodo) FILTER (WHERE h.estado = 'M') AS ult_m, max(h.periodo) AS ult_h
            FROM swa_raw.historico h WHERE h.estudiante = e.codigo
        ) h ON true
    """)
    codigos_por_swa = {}
    for persona, codigo, programa, ult_m, ult_h, ingreso in cur.fetchall():
        codigos_por_swa.setdefault(persona, []).append(
            ((codigo or '').strip(), programa, ult_m or 0, ult_h or 0, ingreso or 0))

    filas = []
    for nid, filas_swa in grupos.items():
        transformaciones, alertas, estado = [], [], 'LISTA'
        origenes = {f['origen'] for f in filas_swa}
        origen = next(o for o in ('MATRICULA', 'SOLO_NOTAS', 'PREMATRICULA') if o in origenes)
        decision = None
        if origen == 'SOLO_NOTAS':
            decision, _ = rev.caso(
                'PERSONA_SIN_MATRICULA', nid, 'persona', 'MEDIA',
                'Tiene notas en SWA pero ninguna matricula registrada (M) en ningun periodo',
                {'filas_swa': filas_swa}, ['INCLUIR', 'EXCLUIR'], {'decision': 'INCLUIR'})
        elif origen == 'PREMATRICULA':
            ultimo = max(f['ultimo_periodo'] or 0 for f in filas_swa)
            decision, _ = rev.caso(
                'PREMATRICULA_SIN_LEGALIZAR', nid, 'persona', 'MEDIA',
                f'Solo tiene prematricula (ultima en {nombre_periodo(ultimo)}): nunca legalizo matricula ni tiene notas',
                {'filas_swa': filas_swa, 'ultima_prematricula': ultimo}, ['INCLUIR', 'EXCLUIR'],
                {'decision': 'INCLUIR' if ultimo >= PERIODO_CORTE else 'EXCLUIR'})
        if origen != 'MATRICULA':
            if decision == 'EXCLUIR':
                estado = 'EXCLUIDA'
            elif decision != 'INCLUIR':
                estado = 'REVISION'

        validas = [f for f in filas_swa if not f['marcada_x']] or filas_swa
        principal = max(validas, key=lambda f: (f['ultimo_periodo'] or 0, f['swa_id']))
        if len(filas_swa) > 1:
            transformaciones.append(f'UNIFICADA_{len(filas_swa)}_REGISTROS')
        if any(f['marcada_x'] for f in filas_swa):
            transformaciones.append('REGISTRO_X_ANULADO_UNIFICADO')

        nombre, apellido = limpiar_nombre(principal['nombre']), limpiar_nombre(principal['apellido'])
        if (nombre, apellido) != ((principal['nombre'] or '').strip(), (principal['apellido'] or '').strip()):
            transformaciones.append('NOMBRE_NORMALIZADO')

        # nombres en conflicto dentro del mismo documento
        distintos = [f for f in filas_swa if f is not principal and not nombres_compatibles(
            (nombre, apellido), (limpiar_nombre(f['nombre']), limpiar_nombre(f['apellido'])))]
        if distintos:
            decision, corr = rev.caso(
                'DOCUMENTO_COMPARTIDO', nid, 'persona', 'ALTA',
                'Mismo documento con nombres distintos en SWA',
                {'filas_swa': filas_swa, 'principal': principal['swa_id']},
                ['UNIR', 'EXCLUIR'], {'decision': 'UNIR', 'nombre': nombre, 'apellido': apellido},
                filas=len(filas_swa))
            if decision == 'UNIR':
                nombre = limpiar_nombre(corr.get('nombre') or nombre)
                apellido = limpiar_nombre(corr.get('apellido') or apellido)
                transformaciones.append('DOCUMENTO_COMPARTIDO_RESUELTO')
            elif decision == 'EXCLUIR':
                estado = 'EXCLUIDA'
            elif estado == 'LISTA':
                estado = 'REVISION'

        primer_nombre, segundo_nombre = separar_nombres(nombre)
        primer_apellido, segundo_apellido = separar_apellidos(apellido)
        if not primer_nombre or not primer_apellido:
            decision, corr = rev.caso(
                'NOMBRE_INCOMPLETO', nid, 'persona', 'ALTA', 'Falta nombre o apellido',
                {'filas_swa': filas_swa}, ['CORREGIR', 'EXCLUIR'],
                {'nombre': nombre, 'apellido': apellido})
            if decision == 'CORREGIR' and corr.get('nombre') and corr.get('apellido'):
                primer_nombre, segundo_nombre = separar_nombres(limpiar_nombre(corr['nombre']))
                primer_apellido, segundo_apellido = separar_apellidos(limpiar_nombre(corr['apellido']))
                transformaciones.append('NOMBRE_CORREGIDO_MANUAL')
            elif decision == 'EXCLUIR':
                estado = 'EXCLUIDA'
            elif estado == 'LISTA':
                estado = 'REVISION'

        # documento
        nro = int(nid) if nid.isdigit() and len(nid) <= 15 else None
        if nro is None or len(nid) < 6 or len(nid) > 11:
            decision, corr = rev.caso(
                'DOCUMENTO_INVALIDO', nid, 'persona', 'ALTA' if nro is None else 'MEDIA',
                'Numero de documento no numerico o con longitud atipica',
                {'filas_swa': filas_swa}, ['ACEPTAR', 'CORREGIR', 'EXCLUIR'],
                {'nro_documento': nro})
            if decision == 'CORREGIR' and str(corr.get('nro_documento', '')).isdigit():
                nro = int(corr['nro_documento'])
                transformaciones.append('DOCUMENTO_CORREGIDO_MANUAL')
            elif decision == 'ACEPTAR' and nro is not None:
                pass
            elif decision == 'EXCLUIR':
                estado = 'EXCLUIDA'
            elif estado == 'LISTA':
                estado = 'REVISION'

        # fecha de nacimiento: SWA tiene valores de relleno; solo se acepta si es plausible
        # frente al primer ingreso (entre 10 y 80 anos de edad al matricularse).
        fecha_nac = None
        primer_anio = (primero.get(principal['swa_id']) or (None,))[0]
        for f in sorted(filas_swa, key=lambda f: f['ultimo_periodo'] or 0, reverse=True):
            candidata = fecha_desde_entero(f['fecha_nacimiento'])
            if candidata and primer_anio and 10 <= primer_anio // 10 - candidata.year <= 80:
                fecha_nac = candidata
                break
        if not fecha_nac and any(f['fecha_nacimiento'] for f in filas_swa):
            alertas.append('FECHA_NACIMIENTO_NO_PLAUSIBLE')

        # tipo de documento (nunca se baja una CC a TI)
        ordenadas = sorted(filas_swa, key=lambda f: f['ultimo_periodo'] or 0, reverse=True)
        tipos = [TIPO_SWA_A_ERP.get(f['tipo_swa']) for f in ordenadas]
        tipo_principal = TIPO_SWA_A_ERP.get(principal['tipo_swa'])
        especial = next((t for t in tipos if t not in (None, TIPO_CC, TIPO_TI)), None)
        regla_tipo = None
        if especial:
            tipo = especial
        elif TIPO_CC in tipos:
            tipo = TIPO_CC
        elif TIPO_TI in tipos:
            ultimo_ti = max((f['ultimo_periodo'] or 0) for f, t in zip(ordenadas, tipos) if t == TIPO_TI)
            if fecha_nac:
                tipo = TIPO_CC if edad(fecha_nac) >= 18 else TIPO_TI
            else:
                # una TI se expide hasta los 17 anos: 11 anos despues ya es mayor de edad
                tipo = TIPO_CC if HOY.year - ultimo_ti // 10 >= 11 else TIPO_TI
            if tipo == TIPO_CC:
                regla_tipo = 'TI_ACTUALIZADA_A_CC_POR_EDAD'
        elif fecha_nac:
            tipo = TIPO_CC if edad(fecha_nac) >= 18 else TIPO_TI
            regla_tipo = 'TIPO_INDEFINIDO_RESUELTO_POR_EDAD'
        else:
            tipo = None
        if regla_tipo:
            transformaciones.append(regla_tipo)
        elif tipo is not None and tipo != tipo_principal:
            transformaciones.append('TIPO_DOCUMENTO_DE_OTRO_REGISTRO')
        if tipo is None:
            decision, corr = rev.caso(
                'TIPO_DOCUMENTO_INDEFINIDO', nid, 'persona', 'MEDIA',
                'Tipo de documento POR DEFINIR (o sin equivalente en el ERP) y sin fecha de nacimiento valida',
                {'filas_swa': filas_swa}, ['CORREGIR', 'EXCLUIR'], {'tipo_documento_id': TIPO_CC})
            if decision == 'CORREGIR' and corr.get('tipo_documento_id'):
                tipo = int(corr['tipo_documento_id'])
                transformaciones.append('TIPO_DOCUMENTO_CORREGIDO_MANUAL')
            elif decision == 'EXCLUIR':
                estado = 'EXCLUIDA'
            elif estado == 'LISTA':
                estado = 'REVISION'

        email = email_valido(*[f['email'] for f in sorted(filas_swa, key=lambda f: f['ultimo_periodo'] or 0, reverse=True)])
        if not email:
            alertas.append('SIN_EMAIL_VALIDO')
        telefono = telefono_valido(principal['celular'], principal['telefono'],
                                   *[f['celular'] for f in filas_swa], *[f['telefono'] for f in filas_swa])
        if not telefono:
            alertas.append('SIN_TELEFONO_VALIDO')

        per, prog = min((primero[f['swa_id']] for f in filas_swa if f['swa_id'] in primero), default=(None, None))
        prefijo = f'{per // 10}{per % 10}{prog}' if per else None

        # El id de la persona en el ERP sera su codigo SWA mas reciente (papeleria y gestion).
        # Sus demas codigos se conservan en cada matricula.
        # Prioridad: codigo de carrera antes que de idiomas/extension; luego el de la matricula
        # mas reciente, la ultima prematricula y el ultimo ingreso.
        candidatos_codigo = sorted(
            (c for f in filas_swa for c in codigos_por_swa.get(f['swa_id'], [])),
            key=lambda c: (c[1] in PROGRAMAS_EXTENSION, -c[2], -c[3], -c[4], c[0]), reverse=False)
        codigos = []
        for c in candidatos_codigo:
            if c[0] not in codigos:
                codigos.append(c[0])
        validos = [c for c in codigos if CODIGO_SWA_RE.match(c)]
        codigo_swa = validos[0] if validos else None
        if codigo_swa and int(codigo_swa) in ids_erp:
            decision, corr = rev.caso(
                'CODIGO_SWA_EN_USO', nid, 'persona', 'ALTA',
                f'El codigo SWA {codigo_swa} ya es el id de otra persona en el ERP',
                {'codigo_swa': codigo_swa, 'codigos_swa': codigos}, ['CORREGIR', 'GENERAR_CODIGO_ERP'],
                {'codigo': next((c for c in validos[1:] if int(c) not in ids_erp), '')})
            codigo_swa = None
            if decision == 'CORREGIR' and CODIGO_SWA_RE.match(str(corr.get('codigo', ''))):
                codigo_swa = str(corr['codigo'])
            elif decision != 'GENERAR_CODIGO_ERP' and estado == 'LISTA':
                estado = 'REVISION'
        elif not codigo_swa:
            decision, corr = rev.caso(
                'CODIGO_SWA_INVALIDO', nid, 'persona', 'MEDIA',
                'Ningun codigo de estudiante SWA valido para usarlo como codigo en el ERP',
                {'codigos_swa': codigos}, ['CORREGIR', 'GENERAR_CODIGO_ERP'],
                {'codigo': re.sub(r'\D', '', codigos[0]) if codigos else ''})
            if decision == 'CORREGIR' and CODIGO_SWA_RE.match(str(corr.get('codigo', ''))):
                codigo_swa = str(corr['codigo'])
                transformaciones.append('CODIGO_SWA_CORREGIDO_MANUAL')
            elif decision != 'GENERAR_CODIGO_ERP' and estado == 'LISTA':
                estado = 'REVISION'
        if len(codigos) > 1:
            transformaciones.append('VARIOS_CODIGOS_SWA')

        # cruce con personas que ya existen en el ERP
        erp_id, accion = None, 'CREAR'
        candidatos = erp.get(nro, []) if nro else []
        if candidatos:
            compatibles = [c for c in candidatos if nombres_compatibles((c[2], c[3]), (nombre, apellido))]
            if len(compatibles) == 1:
                erp_id, accion = compatibles[0][0], 'VINCULAR'
                transformaciones.append('VINCULADA_A_PERSONA_ERP')
            else:
                decision, corr = rev.caso(
                    'CONFLICTO_PERSONA_ERP', nid, 'persona', 'ALTA',
                    'El documento ya existe en el ERP con otro nombre',
                    {'erp': candidatos, 'swa': {'nombre': nombre, 'apellido': apellido}},
                    ['VINCULAR', 'EXCLUIR'], {'erp_persona_id': candidatos[0][0]})
                if decision == 'VINCULAR':
                    erp_id, accion = int(corr.get('erp_persona_id') or candidatos[0][0]), 'VINCULAR'
                    transformaciones.append('VINCULADA_A_PERSONA_ERP_MANUAL')
                elif decision == 'EXCLUIR':
                    estado = 'EXCLUIDA'
                elif estado == 'LISTA':
                    estado = 'REVISION'

        filas.append((nid, [f['swa_id'] for f in filas_swa], principal['swa_id'], tipo, nro,
                      primer_nombre, segundo_nombre, primer_apellido, segundo_apellido, email,
                      telefono, fecha_nac, per, prog, prefijo, codigo_swa, codigos, erp_id, accion, estado,
                      transformaciones, alertas))

    with cur.copy('COPY migra.persona (nid, swa_ids, swa_id_principal, tipo_documento_id, nro_documento, '
                  'primer_nombre, segundo_nombre, primer_apellido, segundo_apellido, email, telefono, '
                  'fecha_nacimiento, primer_periodo, primer_programa, prefijo, codigo_swa, codigos_swa, '
                  'erp_persona_id, accion, '
                  'estado, transformaciones, alertas) FROM STDIN') as copy:
        for fila in filas:
            copy.write_row(fila)

    # un mismo codigo SWA elegido por dos personas (no deberia pasar; se verifica)
    cur.execute("""
        SELECT codigo_swa, array_agg(nid) FROM migra.persona
        WHERE codigo_swa IS NOT NULL AND estado <> 'EXCLUIDA' GROUP BY 1 HAVING count(*) > 1
    """)
    for codigo, nids in cur.fetchall():
        rev.caso('CODIGO_SWA_REPETIDO', codigo, 'persona', 'ALTA',
                 f'El codigo SWA {codigo} quedo asignado a varias personas', {'nids': nids},
                 ['EXCLUIR'], None, filas=len(nids))
        cur.execute("UPDATE migra.persona SET estado = 'REVISION' WHERE nid = ANY(%s) AND estado = 'LISTA'", (nids,))

    # dos documentos distintos que terminan con el mismo (tipo, numero) en el ERP
    cur.execute("""
        SELECT tipo_documento_id, nro_documento, array_agg(nid)
        FROM migra.persona WHERE estado <> 'EXCLUIDA' AND nro_documento IS NOT NULL
        GROUP BY 1, 2 HAVING count(*) > 1
    """)
    for tipo, nro, nids in cur.fetchall():
        rev.caso('DOCUMENTO_REPETIDO_NORMALIZADO', f'{tipo}-{nro}', 'persona', 'ALTA',
                 'Documentos distintos en SWA quedan iguales al normalizarse',
                 {'nids': nids}, ['EXCLUIR'], None, filas=len(nids))
        cur.execute("UPDATE migra.persona SET estado = 'REVISION' WHERE nid = ANY(%s) AND estado = 'LISTA'", (nids,))


def _paso_catalogos(cur):
    cur.execute("""
        INSERT INTO migra.estudiante (codigo, nid, programa, pensum, periodo_ingreso, estado_swa)
        SELECT e.codigo, pf.nid, e.programa, e.pensum, e.periodo_ingreso, e.estado
        FROM swa_raw.estudiante e JOIN migra.persona_fuente pf ON pf.swa_id = e.persona;

        INSERT INTO migra.periodo (codigo, nombre, fecha_inicio, fecha_fin)
        SELECT p.codigo, (p.codigo / 10) || '-' || (p.codigo % 10),
               to_date(nullif(min(p.fecha_inicial), 0)::text, 'YYYYMMDD'),
               to_date(nullif(max(p.fecha_final), 0)::text, 'YYYYMMDD')
        FROM swa_raw.periodo p
        WHERE p.codigo IN (SELECT periodo FROM swa_raw.historico UNION SELECT periodo FROM swa_raw.registro)
          AND p.codigo % 10 IN (1, 2)
        GROUP BY p.codigo;
        UPDATE migra.periodo mp SET erp_periodo_id = pa.id
        FROM public.academico_periodoacademico pa WHERE pa.nombre = mp.nombre;

        INSERT INTO migra.asignatura (codigo, programa, nombre, creditos, ihs, erp_codigo)
        SELECT a.codigo, a.programa, trim(regexp_replace(a.nombre, '\\s+', ' ', 'g')),
               coalesce((SELECT max(pa.creditos) FROM swa_raw.pensum_asignatura pa
                         WHERE pa.programa = a.programa AND pa.asignatura = a.codigo), 0),
               coalesce(a.ihs, 0), a.codigo::text
        FROM swa_raw.asignatura a;

        INSERT INTO migra.pensum (programa, codigo, descripcion, erp_codigo, activo, periodo_codigo)
        SELECT p.programa, p.codigo, p.descripcion, 'SWA-' || p.programa || '-' || p.codigo,
               p.estado = 'A',
               coalesce((SELECT min(h.periodo) FROM swa_raw.estudiante e
                         JOIN swa_raw.historico h ON h.estudiante = e.codigo AND h.estado = 'M'
                         WHERE e.programa = p.programa AND e.pensum = p.codigo),
                        (SELECT min(codigo) FROM migra.periodo))
        FROM swa_raw.pensum p;

        INSERT INTO migra.pensum_asignatura (programa, pensum, asignatura, nivel)
        SELECT programa, pensum, asignatura, coalesce(nivel, 0) FROM swa_raw.pensum_asignatura;

        INSERT INTO migra.prerrequisito (programa, pensum, asignatura, requisito, tipo)
        SELECT programa, pensum, asignatura, prerequisito, 'PRERREQUISITO' FROM swa_raw.prerequisito
        WHERE (programa, pensum, asignatura) IN (SELECT programa, pensum, asignatura FROM migra.pensum_asignatura)
          AND (programa, pensum, prerequisito) IN (SELECT programa, pensum, asignatura FROM migra.pensum_asignatura)
        ON CONFLICT DO NOTHING;
    """)
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='swa_raw' AND table_name='correquisito'")
    cols = {r[0] for r in cur.fetchall()}
    if {'programa', 'pensum', 'asignatura', 'correquisito'} <= cols:
        cur.execute("""
            INSERT INTO migra.prerrequisito (programa, pensum, asignatura, requisito, tipo)
            SELECT programa, pensum, asignatura, correquisito, 'CORREQUISITO' FROM swa_raw.correquisito
            WHERE (programa, pensum, asignatura) IN (SELECT programa, pensum, asignatura FROM migra.pensum_asignatura)
              AND (programa, pensum, correquisito) IN (SELECT programa, pensum, asignatura FROM migra.pensum_asignatura)
            ON CONFLICT DO NOTHING
        """)


def _paso_equivalencias(cur):
    """Unifica las asignaturas equivalentes de SWA (tabla `equivalencia`) en una sola.

    En el ERP la Asignatura es global y `PensumAsignatura` la enlaza a cada pensum: dos materias
    equivalentes pasan a ser UNA asignatura enlazada a sus pensums, y aprobarla en uno la deja
    aprobada en el otro. Se unifican las 14 equivalencias (las de un solo sentido quedan
    reciprocas, por decision del equipo).

    Asignatura canonica de cada grupo: dentro de un mismo programa, la del pensum mas reciente;
    entre programas distintos, la mas cursada.
    """
    cur.execute('SELECT asignatura, equivale_asignatura FROM swa_raw.equivalencia')
    padre = {}

    def raiz(x):
        padre.setdefault(x, x)
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    for a, b in cur.fetchall():
        padre[raiz(a)] = raiz(b)
    grupos = {}
    for nodo in list(padre):
        grupos.setdefault(raiz(nodo), []).append(nodo)

    cur.execute("""
        SELECT a.codigo, a.programa, coalesce(max(pa.pensum), 0) AS pensum,
               (SELECT count(*) FROM swa_raw.registro r WHERE r.programa = a.programa
                  AND r.asignatura = a.codigo AND r.estado = 'R') AS uso
        FROM swa_raw.asignatura a
        LEFT JOIN swa_raw.pensum_asignatura pa ON pa.programa = a.programa AND pa.asignatura = a.codigo
        WHERE a.codigo = ANY(%s)
        GROUP BY a.codigo, a.programa
    """, (list(padre),))
    info = {codigo: (programa, pensum, uso) for codigo, programa, pensum, uso in cur.fetchall()}

    for numero, miembros in enumerate(sorted(grupos.values(), key=min), start=1):
        miembros = [m for m in miembros if m in info]
        if len(miembros) < 2:
            continue
        mismo_programa = len({info[m][0] for m in miembros}) == 1
        canonica = max(miembros, key=lambda m: (info[m][1], info[m][2]) if mismo_programa
                       else (info[m][2], info[m][1]))
        cur.execute("""
            UPDATE migra.asignatura SET canonica = %s, grupo_equivalencia = %s, erp_codigo = %s::text
            WHERE codigo = ANY(%s)
        """, (canonica, numero, canonica, miembros))


def _paso_matriculas(cur, rev):
    # Matriculas comprobadas (historico M). Si una persona tiene varios codigos en el
    # mismo programa/periodo se unifica y se conserva el codigo que tiene notas.
    cur.execute("""
        WITH base AS (
            SELECT me.nid, h.programa, h.periodo, h.estudiante, coalesce(h.nivel, 0) AS nivel, me.pensum,
                   (SELECT count(*) FROM swa_raw.registro r WHERE r.estudiante = h.estudiante
                      AND r.periodo = h.periodo AND r.programa = h.programa) AS notas
            FROM swa_raw.historico h
            JOIN migra.estudiante me ON me.codigo = h.estudiante
            WHERE h.estado = 'M'
        )
        INSERT INTO migra.matricula (nid, programa, periodo, pensum, codigo, codigos, nivel, activa, origen, estado)
        SELECT b.nid, b.programa, b.periodo,
               (array_agg(b.pensum ORDER BY b.notas DESC, b.estudiante DESC))[1],
               (array_agg(b.estudiante ORDER BY b.notas DESC, b.estudiante DESC))[1],
               array_agg(b.estudiante ORDER BY b.estudiante), max(b.nivel),
               b.periodo >= %(curso)s, 'HISTORICO_M', mp.estado
        FROM base b JOIN migra.persona mp ON mp.nid = b.nid
        GROUP BY b.nid, b.programa, b.periodo, mp.estado
    """, {'curso': PERIODO_ACTIVO_ERP})

    # Personas que solo tienen prematricula (P) y se decidio incluir: sus prematriculas se
    # migran como matriculas no vigentes (estado False en el ERP).
    cur.execute("""
        INSERT INTO migra.matricula (nid, programa, periodo, pensum, codigo, codigos, nivel, activa, origen, estado)
        SELECT me.nid, h.programa, h.periodo, (array_agg(me.pensum ORDER BY h.estudiante DESC))[1],
               max(h.estudiante), array_agg(h.estudiante ORDER BY h.estudiante), max(coalesce(h.nivel, 0)),
               false, 'PREMATRICULA', mp.estado
        FROM swa_raw.historico h
        JOIN migra.estudiante me ON me.codigo = h.estudiante
        JOIN migra.persona mp ON mp.nid = me.nid
        WHERE h.estado = 'P'
          AND me.nid IN (SELECT nid FROM migra.persona_fuente GROUP BY nid
                         HAVING bool_and(origen = 'PREMATRICULA'))
        GROUP BY me.nid, h.programa, h.periodo, mp.estado
        ON CONFLICT (nid, programa, periodo) DO NOTHING
    """)

    # Notas calificadas en un periodo sin matricula M: matricula inferida (requiere decision).
    cur.execute("""
        SELECT me.nid, r.programa, r.periodo, array_agg(DISTINCT r.estudiante), count(*),
               (array_agg(me.pensum))[1], mp.estado
        FROM swa_raw.registro r
        JOIN migra.estudiante me ON me.codigo = r.estudiante
        JOIN migra.persona mp ON mp.nid = me.nid
        WHERE r.estado = 'R'
          AND NOT EXISTS (SELECT 1 FROM migra.matricula m
                          WHERE m.nid = me.nid AND m.programa = r.programa AND m.periodo = r.periodo)
        GROUP BY me.nid, r.programa, r.periodo, mp.estado
    """)
    for nid, programa, periodo, codigos, n, pensum, estado_persona in cur.fetchall():
        clave = f'{nid}|{programa}|{periodo}'
        decision, _ = rev.caso(
            'NOTAS_SIN_MATRICULA', clave, 'matricula', 'MEDIA',
            f'{n} notas en {nombre_periodo(periodo)} sin matricula registrada (M) en ese periodo',
            {'nid': nid, 'programa': programa, 'periodo': periodo, 'codigos': codigos, 'notas': n},
            ['INCLUIR', 'EXCLUIR'], {'decision': 'INCLUIR'}, filas=n)
        estado = {'INCLUIR': 'LISTA', 'EXCLUIR': 'EXCLUIDA'}.get(decision, 'REVISION')
        if estado_persona != 'LISTA' and estado != 'EXCLUIDA':
            estado = estado_persona
        cur.execute("""
            INSERT INTO migra.matricula (nid, programa, periodo, pensum, codigo, codigos, nivel, activa, origen, estado)
            VALUES (%s, %s, %s, %s, %s, %s, 0, %s, 'INFERIDA_NOTAS', %s)
        """, (nid, programa, periodo, pensum, sorted(codigos)[-1], sorted(codigos),
              periodo >= PERIODO_ACTIVO_ERP, estado))

    # Semestre de la matricula. SWA a veces guarda el nivel en 0: se toma el nivel mas alto de
    # las asignaturas inscritas en ese periodo (tambien para las inferidas) y, si aun queda en
    # 0, el de la matricula anterior del mismo programa.
    cur.execute("""
        UPDATE migra.matricula m SET nivel = sub.nivel
        FROM (SELECT me.nid, r.programa, r.periodo, max(pa.nivel) AS nivel
              FROM swa_raw.registro r
              JOIN migra.estudiante me ON me.codigo = r.estudiante
              JOIN migra.pensum_asignatura pa ON pa.programa = r.programa AND pa.pensum = me.pensum
                                             AND pa.asignatura = r.asignatura
              GROUP BY 1, 2, 3) sub
        WHERE (m.origen = 'INFERIDA_NOTAS' OR m.nivel = 0)
          AND m.nid = sub.nid AND m.programa = sub.programa AND m.periodo = sub.periodo
    """)
    cur.execute("""
        UPDATE migra.matricula m SET nivel = (
            SELECT max(p.nivel) FROM migra.matricula p
            WHERE p.nid = m.nid AND p.programa = m.programa AND p.periodo < m.periodo)
        WHERE m.nivel = 0
          AND EXISTS (SELECT 1 FROM migra.matricula p
                      WHERE p.nid = m.nid AND p.programa = m.programa AND p.periodo < m.periodo AND p.nivel > 0)
    """)


def _paso_notas(cur):
    cur.execute("""
        INSERT INTO migra.descarte (entidad, clave, motivo, con_valor, datos)
        SELECT 'nota', concat_ws('|', r.periodo, r.programa, r.asignatura, r.grupo, r.estudiante),
               CASE r.estado WHEN 'P' THEN 'PREINSCRIPCION_SIN_CURSAR' ELSE 'ASIGNATURA_CANCELADA' END,
               coalesce(r.definitiva, 0) > 0, to_jsonb(r)
        FROM swa_raw.registro r JOIN migra.estudiante me ON me.codigo = r.estudiante
        WHERE r.estado <> 'R';

        INSERT INTO migra.nota (periodo, programa, asignatura, grupo, estudiante, nid, definitiva,
                                estado_definitiva, en_curso, estado)
        SELECT r.periodo, r.programa, r.asignatura, r.grupo, r.estudiante, me.nid,
               round(r.definitiva::numeric, 2), r.estado_definitiva, r.periodo >= %(curso)s, m.estado
        FROM swa_raw.registro r
        JOIN migra.estudiante me ON me.codigo = r.estudiante
        JOIN migra.matricula m ON m.nid = me.nid AND m.programa = r.programa AND m.periodo = r.periodo
        WHERE r.estado = 'R';

        -- Misma asignatura varias veces en el mismo periodo. Los grupos negativos de SWA son
        -- instancias especiales (-1/-5 homologacion, -2 validacion, -3 habilitacion, -4 curso):
        -- su nota es el resultado final y reemplaza a la del grupo regular. Entre varias del
        -- mismo tipo se conserva la mayor.
        WITH dup AS (
            SELECT ctid,
                   row_number() OVER w AS rn,
                   first_value(grupo) OVER w AS grupo_final
            FROM migra.nota
            WINDOW w AS (PARTITION BY nid, periodo, programa, asignatura
                         ORDER BY (grupo < 0) DESC, definitiva DESC NULLS LAST, estudiante)
        ),
        borradas AS (
            DELETE FROM migra.nota n USING dup WHERE n.ctid = dup.ctid AND dup.rn > 1
            RETURNING n.*, dup.grupo_final
        )
        INSERT INTO migra.descarte (entidad, clave, motivo, con_valor, datos)
        SELECT 'nota', concat_ws('|', periodo, programa, asignatura, grupo, estudiante),
               CASE WHEN grupo_final = -3 THEN 'REEMPLAZADA_POR_HABILITACION'
                    WHEN grupo_final = -2 THEN 'REEMPLAZADA_POR_VALIDACION'
                    WHEN grupo_final IN (-1, -5) THEN 'REEMPLAZADA_POR_HOMOLOGACION'
                    WHEN grupo_final = -4 THEN 'REEMPLAZADA_POR_CURSO_ESPECIAL'
                    ELSE 'NOTA_DUPLICADA_MISMO_PERIODO' END,
               false, to_jsonb(borradas) - 'grupo_final' FROM borradas;

        -- Notas de periodos sin cierre: se apartan y se migran cuando SWA cierre el periodo
        -- (los docentes siguen calificando en SWA; en el ERP no tendrian grupo ni cortes).
        WITH aplazadas AS (
            DELETE FROM migra.nota WHERE en_curso RETURNING *
        )
        INSERT INTO migra.descarte (entidad, clave, motivo, con_valor, datos)
        SELECT 'nota', concat_ws('|', periodo, programa, asignatura, grupo, estudiante),
               'PERIODO_EN_CURSO_SE_MIGRA_AL_CIERRE', false, to_jsonb(aplazadas) FROM aplazadas;
    """.replace('%(curso)s', str(PERIODO_CORTE)))


def _paso_ingles(cur, rev):
    cur.execute("""
        INSERT INTO migra.homologacion_ingles (nid, programa, modulo, periodo, definitiva, codigo, origen)
        SELECT DISTINCT ON (n.nid, n.programa, pa.nivel)
               n.nid, n.programa, pa.nivel, n.periodo, n.definitiva, n.estudiante, 'APROBADO'
        FROM migra.nota n
        JOIN (SELECT DISTINCT programa, asignatura, nivel FROM migra.pensum_asignatura) pa
             ON pa.programa = n.programa AND pa.asignatura = n.asignatura
        WHERE n.programa = ANY(%(centros)s) AND n.estado_definitiva = 'A' AND NOT n.en_curso
          AND n.estado = 'LISTA' AND pa.nivel BETWEEN 1 AND 6
        ORDER BY n.nid, n.programa, pa.nivel, n.periodo;

        -- quien estudio en los dos centros: sus modulos tambien quedan en el centro actual
        INSERT INTO migra.homologacion_ingles (nid, programa, modulo, periodo, definitiva, codigo, origen)
        SELECT h.nid, actual.programa, h.modulo, h.periodo, h.definitiva, h.codigo, 'CENTRO_ACTUAL'
        FROM migra.homologacion_ingles h
        JOIN LATERAL (SELECT m.programa FROM migra.matricula m
                      WHERE m.nid = h.nid AND m.programa = ANY(%(centros)s) AND m.estado = 'LISTA'
                      ORDER BY m.periodo DESC LIMIT 1) actual ON actual.programa <> h.programa
        ON CONFLICT DO NOTHING;
    """.replace('%(centros)s', 'ARRAY' + str(list(CENTROS_IDIOMAS))))

    cur.execute("""
        SELECT nid, programa, array_agg(modulo ORDER BY modulo)
        FROM migra.homologacion_ingles WHERE origen = 'APROBADO'
        GROUP BY 1, 2
        HAVING min(modulo) <> 1 OR count(*) <> max(modulo) - min(modulo) + 1
    """)
    for nid, programa, modulos in cur.fetchall():
        rev.caso('INGLES_SECUENCIA_IRREGULAR', f'{nid}|{programa}', 'ingles', 'BAJA',
                 f'Modulos aprobados no consecutivos: {modulos}',
                 {'nid': nid, 'programa': programa, 'modulos': modulos},
                 ['ACEPTAR'], {'decision': 'ACEPTAR'})


def _paso_vinculos(cur):
    cur.execute("""
        INSERT INTO migra.vinculo (nid, programa, primer_periodo, ultimo_periodo, activo)
        SELECT nid, programa, min(periodo), max(periodo), max(periodo) >= %s
        FROM migra.matricula WHERE estado = 'LISTA'
        GROUP BY nid, programa
    """, (PERIODO_CORTE,))


def depurar(log=print):
    with conectar_destino() as conn:
        cur = conn.cursor()
        cur.execute((SQL_DIR / '01_esquema.sql').read_text(encoding='utf-8'))
        rev = Revision(cur)
        pasos = [
            ('Personas fuente', lambda: _paso_persona_fuente(cur)),
            ('Personas depuradas', lambda: _paso_personas(cur, rev)),
            ('Catalogos academicos', lambda: _paso_catalogos(cur)),
            ('Equivalencias de asignaturas', lambda: _paso_equivalencias(cur)),
            ('Matriculas', lambda: _paso_matriculas(cur, rev)),
            ('Notas', lambda: _paso_notas(cur)),
            ('Ingles', lambda: _paso_ingles(cur, rev)),
            ('Vinculos', lambda: _paso_vinculos(cur)),
        ]
        for nombre, paso in pasos:
            log(f'- {nombre}...')
            paso()
        from .metricas import calcular_metricas
        metricas = calcular_metricas(cur)
        cur.execute('INSERT INTO migra.depuracion_log (metricas) VALUES (%s::jsonb)',
                    (json.dumps(metricas, default=str),))
        conn.commit()
    return metricas
