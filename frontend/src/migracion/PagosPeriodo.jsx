import { useState } from 'react'
import { FileDown, FileSpreadsheet, Loader2, RefreshCw, Search } from 'lucide-react'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import {
  API_MIGRACION,
  Badge,
  Cargando,
  MensajeError,
  fmt,
  Kpi,
  nombrePeriodo,
  SesionExpirada,
  Tabla,
  Tarjeta,
  useApi,
} from './comunes'

const TONO_GRUPO = { 1: 'LISTA', 2: 'BAJA', 3: 'ALTA', 4: 'MEDIA', 5: 'INFO' }
const ESTADOS_RECIBO = { 10: 'pagado', 1: 'financiado', 0: 'sin pagar', 2: 'anulado' }
const SEVERIDADES = { ALTA: 'Alta', MEDIA: 'Media', BAJA: 'Baja', INFO: 'Informativa' }
const POR_PAGINA = 50
const pesos = (n) => (n == null ? '—' : `$${fmt(Math.round(n))}`)
const codigo = (veredicto) => veredicto.split(' ')[0]
// claves de selección: las mismas que usa el backend para el PDF (pagos_pdf.clave_caso / clave_anomalia)
const claveCaso = (c) => `${c.documento}|${c.programa}`
const claveAnomalia = (a) => `${codigo(a.anomalia)}|${a.numero}|${a.documento}`
const IDIOMAS = [51, 52]

function Marca({ checked, onChange, title }) {
  return (
    <input
      type="checkbox"
      className="h-4 w-4 cursor-pointer accent-[var(--primary)]"
      checked={checked}
      onChange={(e) => onChange(e.target.checked)}
      title={title}
    />
  )
}

async function descargar(ruta, getHash, nombrePorDefecto, opciones = {}) {
  const res = await fetch(`${API_MIGRACION}${ruta}`, {
    ...opciones,
    headers: { 'X-Consulta-Password': getHash(), ...(opciones.headers || {}) },
  })
  if (res.status === 403) throw new SesionExpirada('Sesión expirada')
  if (!res.ok) throw new Error(`Error ${res.status}`)
  const nombre = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '')?.[1]
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = nombre || nombrePorDefecto
  a.click()
  URL.revokeObjectURL(url)
}

function Select({ value, onChange, children, className = '' }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`h-10 rounded-xl border border-border bg-input-background px-3 text-sm ${className}`}
    >
      {children}
    </select>
  )
}

function Paginas({ total, pagina, onPagina }) {
  const paginas = Math.max(Math.ceil(total / POR_PAGINA), 1)
  if (paginas === 1) return null
  return (
    <div className="mt-3 flex items-center justify-between text-sm text-muted-foreground">
      <span>
        {fmt(total)} filas · página {pagina} de {paginas}
      </span>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={pagina <= 1} onClick={() => onPagina(pagina - 1)}>
          Anterior
        </Button>
        <Button variant="outline" size="sm" disabled={pagina >= paginas} onClick={() => onPagina(pagina + 1)}>
          Siguiente
        </Button>
      </div>
    </div>
  )
}

/** Quién tiene asignaturas en el periodo sin pago comprobado de matrícula (lee SWA directo, solo lectura). */
export default function PagosPeriodo({ getHash, onSesionExpirada }) {
  // periodo, programa y documento se consultan en SWA; grupo, veredicto y anomalía filtran en pantalla
  const [consulta, setConsulta] = useState({ periodo: '', programa: '', documento: '' })
  const [periodoTexto, setPeriodoTexto] = useState('')
  const [documentoTexto, setDocumentoTexto] = useState('')
  const [grupo, setGrupo] = useState('3')
  const [veredicto, setVeredicto] = useState('')
  const [vista, setVista] = useState('casos')
  const [anomalia, setAnomalia] = useState('')
  const [pagina, setPagina] = useState(1)
  const [exportando, setExportando] = useState('')
  const [errorAccion, setErrorAccion] = useState('')
  const [seleccion, setSeleccion] = useState(() => new Set())

  const params = new URLSearchParams()
  Object.entries(consulta).forEach(([k, v]) => v && params.set(k, v))
  const datos = useApi(getHash, `/pagos-periodo/?${params}`, onSesionExpirada)
  const d = datos.data

  const cambiarConsulta = (k, v) => {
    setConsulta((c) => ({ ...c, [k]: v }))
    setPagina(1)
    setSeleccion(new Set())
  }
  const elegir = (setter) => (v) => {
    setter(v)
    setPagina(1)
  }
  const cambiarVista = (v) => {
    elegir(setVista)(v)
    setSeleccion(new Set())
  }
  const marcar = (claves, valor) =>
    setSeleccion((s) => {
      const n = new Set(s)
      claves.forEach((k) => (valor ? n.add(k) : n.delete(k)))
      return n
    })

  // filtros que el backend aplica igual que la pantalla (grupo/veredicto solo valen para la vista de casos)
  const paramsDescarga = (conGrupo) => {
    const p = new URLSearchParams(params)
    if (conGrupo && veredicto) p.set('veredicto', veredicto)
    else if (conGrupo && grupo) p.set('grupo', grupo)
    return p
  }

  async function ejecutar(tipo, accion) {
    setExportando(tipo)
    setErrorAccion('')
    try {
      await accion()
    } catch (err) {
      if (err instanceof SesionExpirada) onSesionExpirada()
      setErrorAccion(err.message)
    } finally {
      setExportando('')
    }
  }

  const exportar = () =>
    ejecutar('excel', () => descargar(`/pagos-periodo/exportar/?${paramsDescarga(true)}`, getHash, 'pagos_matricula.xlsx'))

  // PDF o Excel de la vista: mismos filtros y, si hay filas marcadas, solo esas
  const exportarVista = (formato, claves) =>
    ejecutar(formato === 'pdf' ? 'pdf' : 'excel-vista', () =>
      descargar(`/pagos-periodo/${formato}/?${paramsDescarga(vista === 'casos')}`, getHash,
        `pagos_matricula.${formato === 'pdf' ? 'pdf' : 'xlsx'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vista, anomalia: vista === 'anomalias' ? anomalia : '', seleccion: claves }),
      }),
    )

  if (datos.cargando && !d) return <Cargando texto="Consultando SWA (unos segundos)..." />
  if (!d) return <MensajeError mensaje={datos.error} />

  const nombreProg = (p) => (p == null ? '—' : `${p} · ${d.nombres_programa[p] || ''}`)
  const casosGrupo = (g) => d.veredictos.filter((v) => v.grupo === g).reduce((s, v) => s + v.casos, 0)
  const sinNingunPago = d.veredictos.filter((v) => ['2D', '2E'].includes(codigo(v.veredicto))).reduce((s, v) => s + v.casos, 0)
  const altas = d.anomalias_resumen.filter((a) => a.severidad === 'ALTA').reduce((s, a) => s + a.casos, 0)

  const casos = d.casos.filter((c) => (veredicto ? codigo(c.veredicto) === veredicto : !grupo || c.veredicto[0] === grupo))
  const anomalias = d.anomalias.filter((a) => !anomalia || codigo(a.anomalia) === anomalia)
  const filas = vista === 'casos' ? casos : anomalias
  const visibles = filas.slice((pagina - 1) * POR_PAGINA, pagina * POR_PAGINA)
  const claveFila = vista === 'casos' ? claveCaso : claveAnomalia
  // solo cuenta lo elegido que sigue visible con los filtros actuales
  const elegidas = filas.map(claveFila).filter((k) => seleccion.has(k))
  const todasElegidas = filas.length > 0 && elegidas.length === filas.length
  const columnaMarca = {
    clave: 'marca',
    titulo: (
      <Marca
        checked={todasElegidas}
        onChange={(v) => marcar(filas.map(claveFila), v)}
        title={`Elegir las ${fmt(filas.length)} filas filtradas (todas las páginas)`}
      />
    ),
    render: (f) => <Marca checked={seleccion.has(claveFila(f))} onChange={(v) => marcar([claveFila(f)], v)} />,
  }
  const personasCarrera = new Set(d.casos.filter((c) => !IDIOMAS.includes(c.programa)).map((c) => c.nid)).size

  return (
    <div className="space-y-5">
      <p className="max-w-4xl text-sm text-muted-foreground">
        Personas con asignaturas vinculadas en el periodo (con o sin nota) y cómo está cubierta su matrícula.{' '}
        <strong>Pago comprobado</strong> = dinero que entró a una cuenta real (Davivienda, Efecty, PayGateway, tarjeta). Los
        saldos a favor, pagos de periodos anteriores, ajustes de menor cuantía, becas y convenios no son dinero. Los
        créditos se apartan en su grupo. Lee SWA directo en solo lectura: los datos son de este momento.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            cambiarConsulta('periodo', periodoTexto)
          }}
        >
          <Input
            className="h-10 w-32"
            placeholder={`Periodo ${d.periodo}`}
            value={periodoTexto}
            onChange={(e) => setPeriodoTexto(e.target.value.replace(/\D/g, '').slice(0, 5))}
            title="Periodo AAAAP (Enter)"
          />
        </form>
        <Select value={consulta.programa} onChange={(v) => cambiarConsulta('programa', v)} className="max-w-[300px]">
          <option value="">Todos los programas</option>
          {d.programas.map((p) => (
            <option key={p} value={p}>
              {nombreProg(p)}
            </option>
          ))}
        </Select>
        <form
          className="relative min-w-[220px] flex-1"
          onSubmit={(e) => {
            e.preventDefault()
            cambiarConsulta('documento', documentoTexto.trim())
          }}
        >
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="h-10 pl-9"
            placeholder="Documento (Enter)"
            value={documentoTexto}
            onChange={(e) => setDocumentoTexto(e.target.value)}
          />
        </form>
        <Button variant="outline" size="sm" disabled={datos.cargando} onClick={datos.recargar}>
          {datos.cargando ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Actualizar
        </Button>
        <Button
          size="sm"
          disabled={!!exportando}
          onClick={exportar}
          title="Excel (Resumen, Sin pago comprobado, Créditos, Anomalías y Pago comprobado) con los filtros actuales"
        >
          {exportando === 'excel' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSpreadsheet className="h-4 w-4" />}
          Exportar Excel
        </Button>
      </div>
      <MensajeError mensaje={datos.error || errorAccion} />
      <p className="text-xs text-muted-foreground">
        Periodo {nombrePeriodo(d.periodo)} · consultado {new Date(d.fecha).toLocaleString('es-CO')}
      </p>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi
          etiqueta="Personas con asignaturas"
          valor={fmt(d.personas)}
          ayuda={`${fmt(personasCarrera)} en carreras · ${fmt(d.personas - personasCarrera)} solo Centro de Idiomas · ${fmt(d.unificados)} registros duplicados unificados · ${fmt(d.casos.length)} casos persona-programa`}
        />
        <Kpi
          etiqueta="Sin pago comprobado"
          valor={fmt(casosGrupo('3') + casosGrupo('4') + casosGrupo('5'))}
          ayuda={`${fmt(casosGrupo('3'))} sin pago · ${fmt(casosGrupo('4'))} cubiertos sin dinero · ${fmt(casosGrupo('5'))} con otro pago`}
          tono="peligro"
        />
        <Kpi
          etiqueta="Con crédito (apartados)"
          valor={fmt(casosGrupo('2'))}
          ayuda={`${fmt(sinNingunPago)} sin ningún pago`}
          tono="alerta"
        />
        <Kpi etiqueta="Anomalías de descuentos altas" valor={fmt(altas)} ayuda={`${fmt(d.anomalias.length)} anomalías en total`} tono="alerta" />
      </div>

      <Tarjeta titulo="Veredictos (clic para filtrar)">
        <Tabla
          columnas={[
            {
              clave: 'veredicto',
              titulo: 'Veredicto',
              render: (v) => (
                <div className="flex items-center gap-2">
                  <Badge tono={TONO_GRUPO[v.grupo]}>{d.grupos[v.grupo]}</Badge>
                  <span className={veredicto === codigo(v.veredicto) ? 'font-semibold text-foreground' : ''}>{v.veredicto}</span>
                </div>
              ),
            },
            { clave: 'casos', titulo: 'Casos', render: (v) => fmt(v.casos) },
            { clave: 'personas', titulo: 'Personas', render: (v) => fmt(v.personas) },
            { clave: 'asignaturas', titulo: 'Asignaturas', render: (v) => `${fmt(v.asignaturas)} (${fmt(v.con_nota)} con nota)` },
            { clave: 'neto', titulo: 'Valor neto', render: (v) => pesos(v.neto) },
            { clave: 'dinero', titulo: 'Pagado en dinero', render: (v) => pesos(v.dinero) },
            { clave: 'sin_dinero', titulo: 'Sin dinero', render: (v) => pesos(v.sin_dinero) },
            { clave: 'faltante', titulo: 'Faltante', render: (v) => pesos(v.faltante) },
          ]}
          filas={d.veredictos.map((v) => ({ ...v, id: v.veredicto }))}
          onFila={(v) => {
            setVista('casos')
            elegir(setVeredicto)(veredicto === codigo(v.veredicto) ? '' : codigo(v.veredicto))
          }}
        />
      </Tarjeta>

      <div className="flex flex-wrap items-center gap-2 border-b border-border pb-3">
        {[
          ['casos', 'Casos'],
          ['anomalias', 'Anomalías de descuentos'],
        ].map(([clave, texto]) => (
          <Button key={clave} size="sm" variant={vista === clave ? 'default' : 'outline'} onClick={() => cambiarVista(clave)}>
            {texto}
          </Button>
        ))}
        <Button
          size="sm"
          variant="outline"
          disabled={!!exportando || !filas.length}
          onClick={() => exportarVista('pdf', elegidas)}
          title={
            elegidas.length
              ? 'PDF solo con las filas marcadas'
              : 'PDF con todas las filas filtradas (marca filas para descargar solo esas)'
          }
        >
          {exportando === 'pdf' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileDown className="h-4 w-4" />}
          {elegidas.length ? `PDF de ${fmt(elegidas.length)} seleccionada/s` : `PDF de lo filtrado (${fmt(filas.length)})`}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!!exportando || !filas.length}
          onClick={() => exportarVista('excel', elegidas)}
          title={
            elegidas.length
              ? 'Excel solo con las filas marcadas (y sus casos o anomalías)'
              : 'Excel con todas las filas filtradas (marca filas para descargar solo esas)'
          }
        >
          {exportando === 'excel-vista' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSpreadsheet className="h-4 w-4" />}
          {elegidas.length ? `Excel de ${fmt(elegidas.length)} seleccionada/s` : `Excel de lo filtrado (${fmt(filas.length)})`}
        </Button>
        {elegidas.length > 0 && (
          <Button size="sm" variant="outline" onClick={() => setSeleccion(new Set())}>
            Quitar selección
          </Button>
        )}
        {vista === 'casos' && (
          <Select
            value={veredicto ? '' : grupo}
            onChange={(v) => {
              setVeredicto('')
              elegir(setGrupo)(v)
            }}
            className="ml-auto"
          >
            <option value="">Todos los grupos</option>
            {Object.entries(d.grupos).map(([g, t]) => (
              <option key={g} value={g}>
                {g}. {t}
              </option>
            ))}
          </Select>
        )}
        {vista === 'casos' && veredicto && (
          <Button size="sm" variant="outline" onClick={() => elegir(setVeredicto)('')}>
            Quitar veredicto {veredicto}
          </Button>
        )}
        {vista === 'anomalias' && (
          <Select value={anomalia} onChange={elegir(setAnomalia)} className="ml-auto max-w-[360px]">
            <option value="">Todas las anomalías</option>
            {[...new Map(d.anomalias_resumen.map((a) => [a.codigo, a])).values()].map((a) => (
              <option key={a.codigo} value={a.codigo}>
                {a.codigo} {a.titulo}
              </option>
            ))}
          </Select>
        )}
      </div>

      {vista === 'anomalias' && (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {d.anomalias_resumen.map((a) => (
            <button
              key={`${a.codigo}-${a.severidad}`}
              onClick={() => elegir(setAnomalia)(anomalia === a.codigo ? '' : a.codigo)}
              className={`rounded-xl border p-3 text-left transition ${
                anomalia === a.codigo ? 'border-accent bg-accent/5' : 'border-border hover:bg-secondary/50'
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-semibold text-foreground">
                  {a.codigo} {a.titulo}
                </p>
                <Badge tono={a.severidad}>{SEVERIDADES[a.severidad]}</Badge>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{a.que}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {fmt(a.casos)} casos · {pesos(a.valor)}
              </p>
            </button>
          ))}
        </div>
      )}

      {datos.cargando ? (
        <Cargando texto="Consultando SWA..." />
      ) : vista === 'casos' ? (
        <Tabla
          columnas={[
            columnaMarca,
            {
              clave: 'veredicto',
              titulo: 'Veredicto',
              render: (c) => (
                <div className="min-w-[180px] space-y-1">
                  <Badge tono={TONO_GRUPO[c.veredicto[0]]}>{codigo(c.veredicto)}</Badge>
                  <p className="text-xs text-muted-foreground">{c.veredicto.slice(codigo(c.veredicto).length + 1)}</p>
                </div>
              ),
            },
            {
              clave: 'persona',
              titulo: 'Estudiante',
              render: (c) => (
                <div className="min-w-[180px]">
                  <p className="font-medium text-foreground">{c.nombre}</p>
                  <p className="text-xs text-muted-foreground">
                    doc. {c.documento} · {c.codigos} · histórico {c.estado_historico || 'sin fila'}
                  </p>
                  {c.documentos_unificados && (
                    <p className="text-xs text-amber-700" title="Registro de la misma persona con otro documento en SWA">
                      Unificado con doc. {c.documentos_unificados}
                    </p>
                  )}
                </div>
              ),
            },
            { clave: 'programa', titulo: 'Programa', render: (c) => <p className="min-w-[160px]">{nombreProg(c.programa)}</p> },
            {
              clave: 'asignaturas',
              titulo: 'Asignaturas',
              render: (c) => (
                <div>
                  <p>
                    {fmt(c.asignaturas)} · {fmt(c.con_nota)} con nota
                  </p>
                  <p className="max-w-[180px] truncate text-xs text-muted-foreground" title={c.inscrito_por}>
                    {c.inscrito_por}
                  </p>
                </div>
              ),
            },
            {
              clave: 'recibo',
              titulo: 'Recibo',
              render: (c) =>
                c.recibo ? (
                  <div>
                    <p>{c.recibo}</p>
                    <p className="text-xs text-muted-foreground">{ESTADOS_RECIBO[c.estado_recibo] ?? c.estado_recibo}</p>
                  </div>
                ) : (
                  'Sin recibo'
                ),
            },
            {
              clave: 'valores',
              titulo: 'Neto / dinero / sin dinero',
              render: (c) => (
                <div className="whitespace-nowrap text-xs">
                  <p>Neto {pesos(c.neto)}</p>
                  <p>Dinero {pesos(c.dinero)}</p>
                  <p>Sin dinero {pesos(c.sin_dinero)}</p>
                  {c.faltante > 0 && <p className="text-rose-600">Falta {pesos(c.faltante)}</p>}
                </div>
              ),
            },
            {
              clave: 'detalle',
              titulo: 'Detalle',
              render: (c) => (
                <div className="max-w-md space-y-1 text-xs text-muted-foreground">
                  {c.creditos && (
                    <p>
                      Crédito {c.creditos}
                      {c.est_vencido > 0 && ` · vencido ${pesos(c.est_vencido)} (${c.est_cuotas_vencidas} cuota/s)`}
                      {c.est_por_vencer > 0 && ` · por vencer ${pesos(c.est_por_vencer)}`}
                      {c.ter_pendiente > 0 && ` · falta giro de ${c.terceros} ${pesos(c.ter_pendiente)}`}
                    </p>
                  )}
                  {c.sin_dinero_detalle && <p>{c.sin_dinero_detalle}</p>}
                  {c.respaldo_en_dinero && <p className="font-medium text-foreground">Respaldo: {c.respaldo_en_dinero}</p>}
                  {c.pago_otra_carrera && <p>Pago en otro programa: {c.pago_otra_carrera}</p>}
                </div>
              ),
            },
          ]}
          filas={visibles.map((c, i) => ({ ...c, id: `${c.documento}-${c.programa}-${i}` }))}
          vacio="Sin casos con estos filtros"
        />
      ) : (
        <Tabla
          columnas={[
            columnaMarca,
            { clave: 'severidad', titulo: 'Sev.', render: (a) => <Badge tono={a.severidad}>{SEVERIDADES[a.severidad]}</Badge> },
            { clave: 'anomalia', titulo: 'Anomalía', render: (a) => <p className="min-w-[200px]">{a.anomalia}</p> },
            {
              clave: 'persona',
              titulo: 'Estudiante',
              render: (a) => (
                <div className="min-w-[180px]">
                  <p className="font-medium text-foreground">{a.nombre}</p>
                  <p className="text-xs text-muted-foreground">
                    doc. {a.documento} · {nombreProg(a.programa)}
                  </p>
                </div>
              ),
            },
            {
              clave: 'recibo',
              titulo: 'Recibo',
              render: (a) => (
                <div className="text-xs">
                  <p>{a.numero}</p>
                  <p className="text-muted-foreground">{a.veredicto_persona}</p>
                </div>
              ),
            },
            {
              clave: 'valores',
              titulo: 'Valores',
              render: (a) => (
                <div className="whitespace-nowrap text-xs">
                  <p>Bruto {pesos(a.bruto)}</p>
                  <p>Neto {pesos(a.neto)}</p>
                  <p>Dinero {pesos(a.dinero)}</p>
                  <p className="font-medium text-foreground">Anomalía {pesos(a.valor_anomalia)}</p>
                </div>
              ),
            },
            { clave: 'explicacion', titulo: 'Explicación', render: (a) => <p className="max-w-lg text-xs text-muted-foreground">{a.explicacion}</p> },
          ]}
          filas={visibles.map((a, i) => ({ ...a, id: `${a.anomalia}-${a.numero}-${i}` }))}
          vacio="Sin anomalías con estos filtros"
        />
      )}
      <Paginas total={filas.length} pagina={pagina} onPagina={setPagina} />
    </div>
  )
}
