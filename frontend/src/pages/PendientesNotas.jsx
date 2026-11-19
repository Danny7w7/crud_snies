import { useEffect, useRef, useState } from 'react'
import { ClipboardList, RefreshCw, Search, X } from 'lucide-react'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Label } from '../components/ui/label'
import { Checkbox } from '../components/ui/checkbox'
import { SearchableSelect } from '../components/SearchableSelect'

const API = '/api/pendientes-notas/'
const VACIOS = { programa: '', periodo: '', estado: '', destino: '', motivo: '', q: '', sin_interpretar: false }
const numero = (value) => Number(value || 0).toLocaleString('es-CO')
const periodo = (value) => `${String(value).slice(0, 4)}-${String(value).slice(4)}`
const destino = (value) => ({ actual: 'Actual', historico: 'Histórico', pendiente: 'Por revisar' }[value] || value)
const valor = (value) => value === null || value === undefined || value === '' ? '—' : String(value)

export default function PendientesNotas({ getConsultaHash, touchActivity, onUnauthorized }) {
  const [borrador, setBorrador] = useState(VACIOS)
  const [filtros, setFiltros] = useState(VACIOS)
  const [pagina, setPagina] = useState(1)
  const [revision, setRevision] = useState(0)
  const [datos, setDatos] = useState(null)
  const [resumen, setResumen] = useState(null)
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState('')
  const [seleccion, setSeleccion] = useState(null)
  const [detalle, setDetalle] = useState(null)
  const [cargandoDetalle, setCargandoDetalle] = useState(false)
  const [errorDetalle, setErrorDetalle] = useState('')
  const cancelarDetalle = useRef(null)
  const panelDetalle = useRef(null)

  async function solicitar(url, signal) {
    const clave = getConsultaHash()
    if (!clave) { onUnauthorized(); return null }
    touchActivity()
    const respuesta = await fetch(url, { signal, headers: { 'X-Consulta-Password': clave } })
    if (respuesta.status === 401 || respuesta.status === 403) { onUnauthorized(); return null }
    if (!respuesta.ok) throw new Error('No se pudo consultar los pendientes. Intenta actualizar la vista.')
    return respuesta.json()
  }

  useEffect(() => {
    const control = new AbortController()
    const parametros = new URLSearchParams()
    Object.entries(filtros).forEach(([campo, dato]) => {
      if (dato !== '' && dato !== false) parametros.set(campo, dato === true ? '1' : dato)
    })
    setCargando(true)
    setError('')
    setSeleccion(null)
    setDetalle(null)
    cancelarDetalle.current?.abort()
    const listado = new URLSearchParams(parametros)
    listado.set('page', String(pagina))
    Promise.all([solicitar(`${API}?${listado}`, control.signal),
      solicitar(`${API}resumen/?${parametros}`, control.signal)])
      .then(([lista, totales]) => {
        if (!control.signal.aborted && lista && totales) { setDatos(lista); setResumen(totales) }
      })
      .catch((err) => { if (err.name !== 'AbortError') setError(err.message) })
      .finally(() => { if (!control.signal.aborted) setCargando(false) })
    return () => control.abort()
    // Las solicitudes se renuevan al cambiar los filtros, la página o la revisión.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtros, pagina, revision])

  useEffect(() => () => cancelarDetalle.current?.abort(), [])

  function aplicar(event) {
    event.preventDefault()
    setPagina(1)
    setFiltros({ ...borrador, q: borrador.q.trim() })
  }

  async function abrirDetalle(fila) {
    cancelarDetalle.current?.abort()
    const control = new AbortController()
    cancelarDetalle.current = control
    setSeleccion(fila)
    setDetalle(null)
    setErrorDetalle('')
    setCargandoDetalle(true)
    try {
      const nuevo = await solicitar(`${API}${fila.id}/`, control.signal)
      if (!control.signal.aborted && nuevo) {
        setDetalle(nuevo)
        requestAnimationFrame(() => panelDetalle.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
      }
    } catch (err) {
      if (err.name !== 'AbortError') setErrorDetalle(err.message)
    } finally {
      if (!control.signal.aborted) setCargandoDetalle(false)
    }
  }

  const opciones = resumen?.filtros || {}
  const totalPaginas = Math.max(1, Math.ceil((datos?.count || 0) / 25))
  const totalesDestino = Object.fromEntries((resumen?.por_destino || []).map((fila) => [fila.destino_notas, fila.cantidad]))
  const cambiar = (campo, dato) => setBorrador((anterior) => ({ ...anterior, [campo]: dato }))
  const opcionesConTodos = (lista, etiqueta) => [{ value: '', label: etiqueta }, ...lista]
  const campos = [
    ['programa', 'Programa', opcionesConTodos((opciones.programas || []).map((fila) => ({ value: String(fila.programa_swa_id), label: `${fila.programa_swa_id} · ${fila.nombre || 'Nombre pendiente'}` })), 'Todos los programas')],
    ['periodo', 'Período', opcionesConTodos((opciones.periodos || []).map((p) => ({ value: String(p), label: periodo(p) })), 'Todos los períodos')],
    ['estado', 'Estado del registro SWA', opcionesConTodos((opciones.estados || []).filter(Boolean).map((estado) => ({ value: estado, label: estado === 'R' ? 'R · Registrado' : `${estado} · Sin interpretación confirmada` })), 'Todos los estados')],
    ['destino', 'Destino de las notas', opcionesConTodos(['actual', 'historico', 'pendiente'].map((d) => ({ value: d, label: destino(d) })), 'Todos los destinos')],
    ['motivo', 'Motivo pendiente', opcionesConTodos((opciones.motivos || []).filter(Boolean).map((m) => ({ value: m, label: m })), 'Todos los motivos')],
  ]

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="mb-2 flex items-center gap-2 text-accent"><ClipboardList className="h-5 w-5" /><span className="text-sm font-semibold">Revisión de la migración</span></div>
          <h1 className="text-2xl font-semibold text-foreground sm:text-3xl">Notas pendientes</h1>
          <p className="mt-2 max-w-3xl text-sm text-muted-foreground">Aquí conservamos los casos que requieren aclaración: estados desconocidos, identidad, pensum y otras diferencias. Consulta las notas originales para revisar cada caso.</p>
        </div>
        <Button type="button" variant="outline" disabled={cargando} onClick={() => setRevision((n) => n + 1)}><RefreshCw className="h-4 w-4" />Actualizar</Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-live="polite">
        {[
          ['Pendientes encontrados', resumen?.total_filtrado],
          ['Estado sin interpretar', resumen?.sin_interpretar],
          ['Para el histórico', totalesDestino.historico],
          ['Para el período actual', totalesDestino.actual],
        ].map(([etiqueta, cantidad]) => <div key={etiqueta} className="rounded-2xl border border-border bg-secondary/40 p-4"><p className="text-sm text-muted-foreground">{etiqueta}</p><p className="mt-1 text-2xl font-semibold">{cargando ? '…' : numero(cantidad)}</p></div>)}
      </div>

      <form onSubmit={aplicar} className="space-y-4 rounded-2xl border border-border p-4 sm:p-5">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {campos.map(([campo, etiqueta, lista]) => <div key={campo} className="min-w-0 space-y-2"><Label htmlFor={`pendiente-${campo}`}>{etiqueta}</Label><SearchableSelect id={`pendiente-${campo}`} value={borrador[campo]} options={lista} onValueChange={(v) => cambiar(campo, v)} /></div>)}
          <div className="space-y-2"><Label htmlFor="pendiente-busqueda">Código SWA, asignatura o grupo</Label><Input id="pendiente-busqueda" value={borrador.q} maxLength={100} onChange={(e) => cambiar('q', e.target.value)} placeholder="Buscar un caso" /></div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-2"><Checkbox id="pendiente-sin-interpretar" checked={borrador.sin_interpretar} onCheckedChange={(v) => cambiar('sin_interpretar', v === true)} /><Label htmlFor="pendiente-sin-interpretar">Solo estados sin interpretación confirmada</Label></div>
          <div className="flex gap-2"><Button type="button" variant="outline" disabled={cargando} onClick={() => { setBorrador(VACIOS); setFiltros(VACIOS); setPagina(1) }}>Limpiar</Button><Button type="submit" disabled={cargando}><Search className="h-4 w-4" />Buscar</Button></div>
        </div>
      </form>

      {error && <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">{error}</p>}
      {!error && <>
        <p className="text-sm text-muted-foreground" aria-live="polite">{cargando ? 'Consultando pendientes…' : `${numero(datos?.count)} casos con estos filtros · ${numero(resumen?.total_pendientes)} pendientes en total`}</p>
        <div className="overflow-x-auto rounded-2xl border border-border" aria-busy={cargando}>
          <table className="w-full min-w-[960px] text-left text-sm">
            <thead className="bg-secondary/60 text-muted-foreground"><tr>{['Estudiante SWA', 'Período / programa', 'Asignatura', 'Estado SWA', 'Destino', 'Definitiva original', 'Motivo', 'Revisar'].map((titulo) => <th key={titulo} scope="col" className="px-4 py-3 font-medium">{titulo}</th>)}</tr></thead>
            <tbody className="divide-y divide-border">
              {!cargando && !datos?.results?.length && <tr><td colSpan={8} className="px-4 py-10 text-center text-muted-foreground">No hay pendientes con estos filtros.</td></tr>}
              {(datos?.results || []).map((fila) => <tr key={fila.id} className={seleccion?.id === fila.id ? 'bg-secondary/60' : 'hover:bg-secondary/30'}>
                <td className="px-4 py-3"><span className="font-medium">{fila.codigo_estudiante}</span><div className="text-xs text-muted-foreground">Persona SWA: {fila.persona_swa_id}</div></td>
                <td className="max-w-48 px-4 py-3"><span>{periodo(fila.periodo_swa)}</span><div className="text-xs text-muted-foreground">{fila.programa_swa_id} · {fila.programa_nombre || 'Nombre pendiente'}</div></td>
                <td className="max-w-48 px-4 py-3"><span>{fila.asignatura_swa} · {fila.asignatura_nombre || 'Nombre pendiente'}</span><div className="text-xs text-muted-foreground">Grupo {fila.grupo_swa}{fila.modulo ? ` · Módulo ${fila.modulo}` : ''}</div></td>
                <td className="px-4 py-3"><span className="rounded-lg bg-secondary px-2 py-1 font-mono">{valor(fila.estado_origen)}</span></td>
                <td className="px-4 py-3">{destino(fila.destino_notas)}</td>
                <td className="px-4 py-3 font-mono">{valor(fila.definitiva_origen)}</td>
                <td className="max-w-72 px-4 py-3">{fila.motivo || 'Pendiente de revisión'}</td>
                <td className="px-4 py-3"><Button type="button" variant="outline" size="sm" disabled={cargando} aria-label={`Ver notas de ${fila.codigo_estudiante}, asignatura ${fila.asignatura_swa}, grupo ${fila.grupo_swa}`} onClick={() => abrirDetalle(fila)}>Ver notas</Button></td>
              </tr>)}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between gap-3 text-sm"><span>Página {pagina} de {totalPaginas}</span><div className="flex gap-2"><Button type="button" variant="outline" disabled={cargando || pagina <= 1} onClick={() => setPagina((p) => p - 1)}>Anterior</Button><Button type="button" variant="outline" disabled={cargando || !datos?.next} onClick={() => setPagina((p) => p + 1)}>Siguiente</Button></div></div>
      </>}

      {seleccion && <section ref={panelDetalle} className="space-y-4 rounded-2xl border border-border bg-secondary/20 p-4 sm:p-6" aria-labelledby="detalle-pendiente-titulo">
        <div className="flex items-start justify-between gap-3"><div><h2 id="detalle-pendiente-titulo" className="text-lg font-semibold">Notas originales · {seleccion.codigo_estudiante}</h2><p className="mt-1 text-sm text-muted-foreground">{periodo(seleccion.periodo_swa)} · Programa {seleccion.programa_swa_id} · Asignatura {seleccion.asignatura_swa} · Grupo {seleccion.grupo_swa}</p></div><Button type="button" variant="ghost" size="sm" aria-label="Cerrar detalle" onClick={() => { cancelarDetalle.current?.abort(); setSeleccion(null) }}><X className="h-4 w-4" /></Button></div>
        <p className="text-sm">{seleccion.motivo || 'Pendiente de revisión'}</p>
        {cargandoDetalle && <p role="status" className="text-sm text-muted-foreground">Consultando los cortes conservados…</p>}
        {errorDetalle && <p role="alert" className="text-sm text-destructive">{errorDetalle}</p>}
        {detalle && <>
          <dl className="grid gap-3 text-sm sm:grid-cols-3">{[['Estado del registro', valor(detalle.estado_origen)], ['Estado de la definitiva SWA', valor(detalle.estado_definitiva_origen)], ['Definitiva original', valor(detalle.definitiva_origen)]].map(([titulo, dato]) => <div key={titulo}><dt className="text-muted-foreground">{titulo}</dt><dd className="mt-1 font-mono">{dato}</dd></div>)}</dl>
          <div className="overflow-x-auto rounded-xl border border-border"><table className="w-full min-w-[520px] text-left text-sm"><thead className="bg-secondary/60"><tr>{['Tipo de nota SWA', 'Valor original', 'Estado', 'Porcentaje original'].map((t) => <th key={t} scope="col" className="px-4 py-3 font-medium">{t}</th>)}</tr></thead><tbody className="divide-y divide-border">{detalle.calificaciones.map((c) => <tr key={c.tipo_nota_swa}><td className="px-4 py-3">{c.tipo_nota_swa === 0 ? '0 · Definitiva' : `${c.tipo_nota_swa} · ${[1, 2, 3].includes(c.tipo_nota_swa) ? 'Corte' : 'Tipo sin correspondencia'}`}</td><td className="px-4 py-3 font-mono">{valor(c.nota_origen)}</td><td className="px-4 py-3">{c.estado_origen === 1 ? '1 · Confirmada' : c.estado_origen === 0 ? '0 · Pendiente' : `${valor(c.estado_origen)} · Sin interpretación`}</td><td className="px-4 py-3 font-mono">{valor(c.porcentaje_origen)}</td></tr>)}{!detalle.calificaciones.length && <tr><td colSpan={4} className="px-4 py-6 text-muted-foreground">Este registro no contiene calificaciones de origen.</td></tr>}</tbody></table></div>
          <details className="rounded-xl border border-border p-3"><summary className="cursor-pointer text-sm font-medium">Ver datos de origen conservados</summary><pre className="mt-3 max-h-80 overflow-auto rounded-lg bg-secondary p-3 text-xs">{JSON.stringify({ registro: detalle.registro_origen, calificaciones: detalle.calificaciones.map((c) => c.datos_origen) }, null, 2)}</pre></details>
          <p className="text-xs text-muted-foreground">Esta consulta conserva los valores de SWA sin redondear. El significado de los estados C, P y otros estados desconocidos queda pendiente de confirmación.</p>
        </>}
      </section>}
    </div>
  )
}
