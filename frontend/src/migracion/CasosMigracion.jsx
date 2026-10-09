import { useEffect, useState } from 'react'
import { CheckCheck, ChevronDown, ChevronUp, Loader2, Search, Undo2, UserSearch } from 'lucide-react'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import {
  apiMigracion,
  Badge,
  Cargando,
  MensajeError,
  fmt,
  Modal,
  nombrePeriodo,
  Paginacion,
  SesionExpirada,
  Tabla,
  Tarjeta,
  TIPOS_DOCUMENTO,
  TIPOS_SWA,
  useApi,
} from './comunes'
import { PersonaDetalle } from './PersonasMigracion'

const nombreCompleto = (c) =>
  [c.primer_nombre, c.segundo_nombre, c.primer_apellido, c.segundo_apellido].filter(Boolean).join(' ')

function FilasSwa({ filas }) {
  return (
    <Tabla
      columnas={[
        { clave: 'swa_id', titulo: 'ID SWA' },
        { clave: 'identificacion', titulo: 'Documento SWA' },
        { clave: 'tipo_swa', titulo: 'Tipo', render: (f) => TIPOS_SWA[f.tipo_swa] ?? f.tipo_swa },
        { clave: 'nombre', titulo: 'Nombre' },
        { clave: 'apellido', titulo: 'Apellido' },
        { clave: 'email', titulo: 'Correo' },
        { clave: 'celular', titulo: 'Celular', render: (f) => f.celular || f.telefono || '—' },
        { clave: 'ultimo_periodo', titulo: 'Último periodo', render: (f) => nombrePeriodo(f.ultimo_periodo) },
        { clave: 'marcada_x', titulo: 'Anulada X', render: (f) => (f.marcada_x ? 'Sí' : 'No') },
      ]}
      filas={filas}
    />
  )
}

/** Una frase que explica el caso concreto. */
function resumenCaso(caso, programas) {
  const d = caso.datos || {}
  const prog = (p) => programas?.[String(p)] || `programa ${p}`
  switch (caso.tipo) {
    case 'NOTAS_SIN_MATRICULA':
      return `Cursó ${fmt(d.notas)} asignatura(s) en ${nombrePeriodo(d.periodo)} en ${prog(d.programa)}, pero SWA no tiene su matrícula de ese periodo.`
    case 'PREMATRICULA_SIN_LEGALIZAR':
      return `Última prematrícula en ${nombrePeriodo(d.ultima_prematricula)}. Nunca quedó matriculado ni tiene notas.`
    case 'PERSONA_SIN_MATRICULA':
      return 'Tiene notas en SWA pero ningún periodo con matrícula registrada.'
    case 'INGLES_SECUENCIA_IRREGULAR':
      return `${prog(d.programa)}: aprobó los módulos ${(d.modulos || []).join(', ')}.`
    case 'DOCUMENTO_INVALIDO':
      return `Documento en SWA: ${(d.filas_swa || []).map((f) => f.identificacion).join(' / ')}`
    case 'DOCUMENTO_COMPARTIDO':
      return `${(d.filas_swa || []).length} registros con el mismo documento y nombres distintos.`
    case 'CONFLICTO_PERSONA_ERP':
      return `En SWA: ${d.swa?.nombre} ${d.swa?.apellido}. En el ERP: ${(d.erp || []).map((p) => `${p[2]} ${p[3]}`).join(' / ')}`
    default:
      return caso.titulo
  }
}

function DatosCaso({ caso }) {
  const d = caso.datos || {}
  if (d.filas_swa) return <FilasSwa filas={d.filas_swa} />
  if (caso.tipo === 'CONFLICTO_PERSONA_ERP') {
    return (
      <div className="space-y-2 text-sm">
        <p>
          En SWA: <b>{d.swa?.nombre} {d.swa?.apellido}</b>
        </p>
        <Tabla
          columnas={[
            { clave: 0, titulo: 'ID ERP', render: (f) => f[0] },
            { clave: 1, titulo: 'Tipo', render: (f) => TIPOS_DOCUMENTO[f[1]] },
            { clave: 2, titulo: 'Nombres', render: (f) => f[2] },
            { clave: 3, titulo: 'Apellidos', render: (f) => f[3] },
          ]}
          filas={d.erp}
        />
      </div>
    )
  }
  if (caso.tipo === 'NOTAS_SIN_MATRICULA') {
    return (
      <p className="text-sm text-muted-foreground">
        Programa <b className="text-foreground">{d.programa}</b> · periodo{' '}
        <b className="text-foreground">{nombrePeriodo(d.periodo)}</b> · {fmt(d.notas)} notas · códigos{' '}
        <b className="text-foreground">{(d.codigos || []).join(', ')}</b>
      </p>
    )
  }
  if (caso.tipo === 'INGLES_SECUENCIA_IRREGULAR') {
    return (
      <p className="text-sm text-muted-foreground">
        Centro {d.programa} · módulos aprobados: <b className="text-foreground">{(d.modulos || []).join(', ')}</b>
      </p>
    )
  }
  return <pre className="overflow-x-auto rounded-lg bg-secondary p-3 text-xs">{JSON.stringify(d, null, 2)}</pre>
}

function CamposCorreccion({ caso, decision, correccion, setCorreccion }) {
  const set = (k) => (e) => setCorreccion({ ...correccion, [k]: e.target.value })
  if (caso.tipo === 'DOCUMENTO_INVALIDO' && decision === 'CORREGIR') {
    return <Input placeholder="Número de documento corregido" value={correccion.nro_documento ?? ''} onChange={set('nro_documento')} />
  }
  if (caso.tipo === 'TIPO_DOCUMENTO_INDEFINIDO' && decision === 'CORREGIR') {
    return (
      <select
        className="h-11 w-full rounded-xl border border-border bg-input-background px-3 text-sm"
        value={correccion.tipo_documento_id ?? ''}
        onChange={set('tipo_documento_id')}
      >
        <option value="">Seleccione el tipo</option>
        {Object.entries(TIPOS_DOCUMENTO).map(([id, n]) => (
          <option key={id} value={id}>{n}</option>
        ))}
      </select>
    )
  }
  if ((caso.tipo === 'NOMBRE_INCOMPLETO' && decision === 'CORREGIR') || (caso.tipo === 'DOCUMENTO_COMPARTIDO' && decision === 'UNIR')) {
    return (
      <div className="grid gap-2 sm:grid-cols-2">
        <Input placeholder="Nombres" value={correccion.nombre ?? ''} onChange={set('nombre')} />
        <Input placeholder="Apellidos" value={correccion.apellido ?? ''} onChange={set('apellido')} />
      </div>
    )
  }
  if (caso.tipo === 'CONFLICTO_PERSONA_ERP' && decision === 'VINCULAR') {
    return (
      <select
        className="h-11 w-full rounded-xl border border-border bg-input-background px-3 text-sm"
        value={correccion.erp_persona_id ?? ''}
        onChange={set('erp_persona_id')}
      >
        {(caso.datos?.erp || []).map((p) => (
          <option key={p[0]} value={p[0]}>{p[0]} · {p[2]} {p[3]}</option>
        ))}
      </select>
    )
  }
  return null
}

function TarjetaCaso({ caso, info, programas, getHash, onCambio, onVerPersona }) {
  const [abierto, setAbierto] = useState(false)
  const [decision, setDecision] = useState(caso.decision || caso.sugerencia?.decision || '')
  const [correccion, setCorreccion] = useState(caso.correccion || caso.sugerencia || {})
  const [comentario, setComentario] = useState(caso.comentario || '')
  const [guardando, setGuardando] = useState(false)
  const [error, setError] = useState('')
  const nid = caso.clave.split('|')[0]

  async function guardar(valor) {
    setGuardando(true)
    setError('')
    try {
      await apiMigracion(getHash, `/casos/${caso.id}/decidir/`, {
        method: 'POST',
        body: JSON.stringify({ decision: valor, correccion, comentario }),
      })
      onCambio()
    } catch (err) {
      setError(err.message)
    } finally {
      setGuardando(false)
    }
  }

  return (
    <div className="rounded-xl border border-border bg-card">
      <button className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left" onClick={() => setAbierto((v) => !v)}>
        <div className="min-w-0">
          <p className="truncate font-medium text-foreground">{nombreCompleto(caso) || nid}</p>
          <p className="text-xs text-muted-foreground">
            Doc. {nid} · {resumenCaso(caso, programas)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {caso.decision ? <Badge tono="LISTA">{caso.decision}</Badge> : <Badge tono="REVISION">Pendiente</Badge>}
          {abierto ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </div>
      </button>
      {abierto && (
        <div className="space-y-4 border-t border-border px-4 py-4">
          <DatosCaso caso={caso} />
          <div className="flex flex-wrap gap-2">
            {caso.opciones.map((op) => (
              <Button key={op} size="sm" variant={decision === op ? 'default' : 'outline'} onClick={() => setDecision(op)} title={info?.opciones?.[op]}>
                {op}
              </Button>
            ))}
            <Button size="sm" variant="ghost" onClick={() => onVerPersona(nid)}>
              <UserSearch className="h-4 w-4" /> Ver antes / después
            </Button>
          </div>
          {decision && info?.opciones?.[decision] && (
            <p className="text-xs text-muted-foreground">
              <b className="text-foreground">{decision}:</b> {info.opciones[decision]}
              {caso.sugerencia?.decision === decision && ' (sugerido)'}
            </p>
          )}
          <CamposCorreccion caso={caso} decision={decision} correccion={correccion} setCorreccion={setCorreccion} />
          <Input placeholder="Comentario / soporte de la decisión (opcional)" value={comentario} onChange={(e) => setComentario(e.target.value)} />
          <MensajeError mensaje={error} />
          <div className="flex justify-end gap-2">
            {caso.decision && (
              <Button size="sm" variant="outline" disabled={guardando} onClick={() => guardar(null)}>
                <Undo2 className="h-4 w-4" /> Deshacer decisión
              </Button>
            )}
            <Button size="sm" disabled={!decision || guardando} onClick={() => guardar(decision)}>
              {guardando ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCheck className="h-4 w-4" />} Guardar decisión
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function CasosMigracion({ getHash, onSesionExpirada }) {
  const resumen = useApi(getHash, '/casos/resumen/', onSesionExpirada)
  const [tipo, setTipo] = useState('')
  const [filtro, setFiltro] = useState('1')
  const [q, setQ] = useState('')
  const [busqueda, setBusqueda] = useState('')
  const [pagina, setPagina] = useState(1)
  const [lista, setLista] = useState({ data: null, cargando: false, error: '' })
  const [personaNid, setPersonaNid] = useState(null)
  const [masivo, setMasivo] = useState(false)

  const tipoActivo = tipo || resumen.data?.[0]?.tipo || ''
  const info = resumen.data?.find((t) => t.tipo === tipoActivo)

  async function cargarLista() {
    if (!tipoActivo) return
    setLista((l) => ({ ...l, cargando: true, error: '' }))
    try {
      const params = new URLSearchParams({ tipo: tipoActivo, page: String(pagina) })
      if (filtro) params.set('pendientes', filtro)
      if (busqueda) params.set('q', busqueda)
      const data = await apiMigracion(getHash, `/casos/?${params}`)
      setLista({ data, cargando: false, error: '' })
    } catch (err) {
      if (err instanceof SesionExpirada) onSesionExpirada()
      setLista({ data: null, cargando: false, error: err.message })
    }
  }

  useEffect(() => {
    cargarLista()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tipoActivo, filtro, busqueda, pagina])

  async function aplicarSugerencia() {
    setMasivo(true)
    try {
      await apiMigracion(getHash, '/casos/sugerencia-masiva/', { method: 'POST', body: JSON.stringify({ tipo: tipoActivo }) })
      await Promise.all([resumen.recargar(), cargarLista()])
    } catch (err) {
      setLista((l) => ({ ...l, error: err.message }))
    } finally {
      setMasivo(false)
    }
  }

  const refrescar = () => Promise.all([resumen.recargar(), cargarLista()])

  if (resumen.cargando && !resumen.data) return <Cargando />
  return (
    <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
      <div className="space-y-3">
        <MensajeError mensaje={resumen.error} />
        {resumen.data?.length === 0 && <p className="text-sm text-muted-foreground">No hay casos que requieran análisis.</p>}
        {resumen.data?.map((t) => (
          <button
            key={t.tipo}
            onClick={() => { setTipo(t.tipo); setPagina(1) }}
            className={`w-full rounded-2xl border p-4 text-left transition ${
              t.tipo === tipoActivo ? 'border-accent bg-accent/5' : 'border-border bg-card hover:bg-secondary/60'
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <Badge>{t.severidad}</Badge>
              <span className="text-xs text-muted-foreground">{t.entidad}</span>
            </div>
            <p className="mt-2 font-semibold text-foreground">{t.titulo}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {fmt(t.pendientes)} pendientes de {fmt(t.casos)}
              {t.recomendacion ? ` · Recomendado: ${t.recomendacion.split('.')[0].toLowerCase()}` : ''}
            </p>
          </button>
        ))}
      </div>

      <div className="space-y-4">
        {info && (
          <Tarjeta
            titulo={info.titulo}
            accion={
              info.con_sugerencia > 0 && (
                <Button size="sm" variant="outline" disabled={masivo} onClick={aplicarSugerencia}>
                  {masivo ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCheck className="h-4 w-4" />}
                  Aplicar sugerencia a {fmt(info.con_sugerencia)} pendientes
                </Button>
              )
            }
          >
            <dl className="grid gap-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Qué pasó</dt>
                <dd className="mt-1 text-foreground">{info.que_paso}</dd>
              </div>
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Por qué importa</dt>
                <dd className="mt-1 text-foreground">{info.por_que_importa}</dd>
              </div>
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Recomendación</dt>
                <dd className="mt-1 font-medium text-emerald-700 dark:text-emerald-300">{info.recomendacion}</dd>
              </div>
            </dl>
            <ul className="mt-4 space-y-1 border-t border-border pt-3 text-sm">
              {Object.entries(info.opciones || {}).map(([op, texto]) => (
                <li key={op}>
                  <b className="text-foreground">{op}:</b> <span className="text-muted-foreground">{texto}</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-muted-foreground">
              {fmt(info.pendientes)} pendientes de {fmt(info.casos)} casos ({fmt(info.filas)} registros afectados). Las
              decisiones se aplican con «Aplicar decisiones» en el resumen o al enviar a la copia.
            </p>
          </Tarjeta>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <form
            className="relative min-w-[220px] flex-1"
            onSubmit={(e) => { e.preventDefault(); setPagina(1); setBusqueda(q.trim()) }}
          >
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input className="pl-9" placeholder="Buscar por documento o nombre y presionar Enter" value={q} onChange={(e) => setQ(e.target.value)} />
          </form>
          {[['1', 'Pendientes'], ['0', 'Decididos'], ['', 'Todos']].map(([v, t]) => (
            <Button key={v} size="sm" variant={filtro === v ? 'default' : 'outline'} onClick={() => { setFiltro(v); setPagina(1) }}>
              {t}
            </Button>
          ))}
        </div>

        <MensajeError mensaje={lista.error} />
        {lista.cargando && !lista.data ? (
          <Cargando />
        ) : (
          <div className="space-y-2">
            {lista.data?.results.length === 0 && <p className="py-6 text-center text-sm text-muted-foreground">Sin casos en este filtro.</p>}
            {lista.data?.results.map((c) => (
              <TarjetaCaso key={`${c.id}-${c.decision}`} caso={c} info={info} programas={lista.data?.programas} getHash={getHash} onCambio={refrescar} onVerPersona={setPersonaNid} />
            ))}
            <Paginacion data={lista.data} pagina={pagina} onPagina={setPagina} />
          </div>
        )}
      </div>

      <Modal abierto={!!personaNid} onCerrar={() => setPersonaNid(null)} ancho="max-w-6xl">
        {personaNid && <PersonaDetalle nid={personaNid} getHash={getHash} onSesionExpirada={onSesionExpirada} />}
      </Modal>
    </div>
  )
}
