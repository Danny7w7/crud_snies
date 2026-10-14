import { useState } from 'react'
import { FileSpreadsheet, FileText, Loader2, RefreshCw, Search, ShieldAlert } from 'lucide-react'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import {
  API_MIGRACION,
  apiMigracion,
  Badge,
  Cargando,
  MensajeError,
  fmt,
  Kpi,
  nombrePeriodo,
  Paginacion,
  SesionExpirada,
  Tabla,
  Tarjeta,
  useApi,
} from './comunes'

const MIGRACION = {
  SE_MIGRA: ['Se migra', 'LISTA'],
  EN_REVISION: ['En revisión', 'REVISION'],
  NO_SE_MIGRA: ['No se migra', 'REVERTIDO'],
}
const fechaHora = (f) => (f ? new Date(f).toLocaleString('es-CO') : '—')

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


/** Reporte de anomalias de SWA: cabos sueltos academicos y financieros, documentados caso por caso. */
export default function AnomaliasMigracion({ getHash, onSesionExpirada }) {
  const [filtros, setFiltros] = useState({
    tipo: '',
    severidad: '',
    gravedad: '',
    responsable: '',
    estado_migracion: '',
    programa: '',
    periodo: '',
    q: '',
    ocultar_info: '1',
  })
  const [texto, setTexto] = useState('')
  const [pagina, setPagina] = useState(1)
  const [ocupado, setOcupado] = useState('')
  const [errorAccion, setErrorAccion] = useState('')
  const resumen = useApi(getHash, '/anomalias/resumen/', onSesionExpirada)

  const params = new URLSearchParams({ page: String(pagina) })
  Object.entries(filtros).forEach(([k, v]) => v && params.set(k, v))
  const casos = useApi(getHash, resumen.data?.generado ? `/anomalias/?${params}` : null, onSesionExpirada)

  const cambiar = (k) => (v) => {
    setFiltros((f) => ({ ...f, [k]: v }))
    setPagina(1)
  }

  async function generar() {
    setOcupado('generar')
    setErrorAccion('')
    try {
      await apiMigracion(getHash, '/anomalias/generar/', { method: 'POST', body: '{}' })
      await resumen.recargar()
      await casos.recargar()
    } catch (err) {
      if (err instanceof SesionExpirada) onSesionExpirada()
      setErrorAccion(err.message)
    } finally {
      setOcupado('')
    }
  }

  /** 'alta': Excel solo con severidad ALTA (ignora los filtros); 'excel' / 'csv': con los filtros de abajo. */
  async function exportar(modo) {
    setOcupado(modo)
    setErrorAccion('')
    try {
      const p = new URLSearchParams()
      if (modo === 'alta') {
        p.set('severidad', 'ALTA')
      } else {
        Object.entries(filtros).forEach(([k, v]) => v && p.set(k, v))
        if (modo === 'csv') p.set('formato', 'csv')
      }
      const res = await fetch(`${API_MIGRACION}/anomalias/exportar/?${p}`, {
        headers: { 'X-Consulta-Password': getHash() },
      })
      if (res.status === 403) throw new SesionExpirada('Sesión expirada')
      if (!res.ok) throw new Error(`Error ${res.status}`)
      const nombre = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '')?.[1]
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a')
      a.href = url
      a.download = nombre || `anomalias_swa.${modo === 'csv' ? 'csv' : 'xlsx'}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      if (err instanceof SesionExpirada) onSesionExpirada()
      setErrorAccion(err.message)
    } finally {
      setOcupado('')
    }
  }

  if (resumen.cargando && !resumen.data) return <Cargando />
  const r = resumen.data
  const botonGenerar = (
    <Button variant="outline" size="sm" disabled={!!ocupado} onClick={generar}>
      {ocupado === 'generar' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
      {r?.generado ? 'Regenerar reporte' : 'Generar reporte'}
    </Button>
  )
  if (!r?.generado) {
    return (
      <Tarjeta titulo="Reporte de anomalías" accion={botonGenerar}>
        <MensajeError mensaje={resumen.error || errorAccion} />
        <p className="text-sm text-muted-foreground">
          Aún no se ha generado. El reporte lee la copia de SWA y tarda alrededor de un minuto.
        </p>
      </Tarjeta>
    )
  }

  const tipoSel = r.tipos.find((t) => t.tipo === filtros.tipo)
  const sev = (s) => r.severidades?.[s] || s

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-3xl text-sm text-muted-foreground">
          Todo lo que SWA permitió y no debería: estados C/P en registro, notas que no cuadran, aprobaciones sin
          matrícula legalizada, sin pago o sin prerrequisito, y módulos de inglés sin soporte. Cada caso cae en una
          sola regla y se cuenta por persona (sus varios códigos SWA cuentan una vez). Solo lectura sobre la copia
          de SWA; no cambia la depuración.
        </p>
        <div className="flex gap-2">
          {botonGenerar}
          <Button
            size="sm"
            disabled={!!ocupado}
            onClick={() => exportar('alta')}
            title="Excel con todos los casos de severidad alta (Resumen, Casos y Por persona), sin aplicar los filtros"
          >
            {ocupado === 'alta' ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldAlert className="h-4 w-4" />}
            Reporte ALTA ({fmt(r.casos_alta)})
          </Button>
        </div>
      </div>
      <MensajeError mensaje={resumen.error || errorAccion} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi
          etiqueta="Anomalías"
          valor={fmt(r.total_casos)}
          ayuda={`${r.tipos.length} reglas · además ${fmt(r.casos_informativos)} casos informativos`}
        />
        <Kpi etiqueta="Severidad alta" valor={fmt(r.casos_alta)} ayuda={`${fmt(r.personas_alta)} personas`} tono="peligro" />
        <Kpi etiqueta="Personas con anomalías" valor={fmt(r.personas)} tono="alerta" />
        <Kpi etiqueta="De ellas se migran" valor={fmt(r.personas_que_se_migran)} ayuda="Llegan al ERP con el cabo suelto" />
      </div>
      <p className="text-xs text-muted-foreground">
        Generado {fechaHora(r.fecha)} · copia de SWA del {fechaHora(r.ultima_copia_swa)} · pagos revisados desde{' '}
        {nombrePeriodo(r.primer_periodo_financiero)} (antes SWA no registraba recibos). Las reglas informativas
        documentan contexto y no cuentan como anomalías. Una misma persona puede aparecer en varias reglas.
      </p>

      <Tarjeta titulo="Reglas">
        <div className="space-y-4">
          {Object.entries(r.categorias).map(([cat, nombre]) => (
            <div key={cat}>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{nombre}</p>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {r.tipos
                  .filter((t) => t.categoria === cat)
                  .map((t) => (
                    <button
                      key={t.tipo}
                      onClick={() => cambiar('tipo')(filtros.tipo === t.tipo ? '' : t.tipo)}
                      className={`rounded-xl border p-3 text-left transition ${
                        filtros.tipo === t.tipo ? 'border-accent bg-accent/5' : 'border-border hover:bg-secondary/50'
                      } ${t.casos === 0 ? 'opacity-60' : ''}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-sm font-semibold text-foreground">{t.titulo}</p>
                        <Badge tono={t.severidad}>{sev(t.severidad)}</Badge>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{t.responsable}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {t.casos === 0
                          ? 'Verificada: sin casos'
                          : `${fmt(t.casos)} casos · ${fmt(t.personas)} personas · ${fmt(t.casos_se_migran)} se migran`}
                      </p>
                    </button>
                  ))}
              </div>
            </div>
          ))}
        </div>
      </Tarjeta>

      {tipoSel && (
        <Tarjeta titulo={tipoSel.titulo} accion={<Badge tono={tipoSel.severidad}>{sev(tipoSel.severidad)}</Badge>}>
          <dl className="grid gap-3 text-sm md:grid-cols-3">
            <div>
              <dt className="font-semibold text-foreground">Qué pasó en SWA</dt>
              <dd className="mt-1 text-muted-foreground">{tipoSel.que_paso}</dd>
            </div>
            <div>
              <dt className="font-semibold text-foreground">Riesgo para la migración</dt>
              <dd className="mt-1 text-muted-foreground">{tipoSel.riesgo}</dd>
            </div>
            <div>
              <dt className="font-semibold text-foreground">Qué hacer ({tipoSel.responsable})</dt>
              <dd className="mt-1 text-muted-foreground">{tipoSel.recomendacion}</dd>
            </div>
          </dl>
          {tipoSel.notas_que_viajan > 0 && (
            <p className="mt-3 text-xs text-muted-foreground">
              {fmt(tipoSel.notas_que_viajan)} de estas notas viajan hoy al ERP tal como están en SWA.
            </p>
          )}
        </Tarjeta>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <form className="relative min-w-[240px] flex-1" onSubmit={(e) => { e.preventDefault(); cambiar('q')(texto.trim()) }}>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="h-10 pl-9" placeholder="Código, documento o nombre (Enter)" value={texto} onChange={(e) => setTexto(e.target.value)} />
        </form>
        <Select value={filtros.severidad} onChange={cambiar('severidad')}>
          <option value="">Toda severidad</option>
          {Object.entries(r.severidades || {}).map(([k, t]) => (
            <option key={k} value={k}>{t}</option>
          ))}
        </Select>
        <Select value={filtros.gravedad} onChange={cambiar('gravedad')}>
          <option value="">Toda gravedad</option>
          {(r.gravedades || []).map((g) => (
            <option key={g} value={g}>{g.slice(2)}</option>
          ))}
        </Select>
        <Select value={filtros.responsable} onChange={cambiar('responsable')}>
          <option value="">Todo responsable</option>
          {(r.responsables || []).map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </Select>
        <Select value={filtros.estado_migracion} onChange={cambiar('estado_migracion')}>
          <option value="">Migración: todas</option>
          {Object.entries(MIGRACION).map(([k, [t]]) => (
            <option key={k} value={k}>{t}</option>
          ))}
        </Select>
        <Select value={filtros.programa} onChange={cambiar('programa')} className="max-w-[260px]">
          <option value="">Todos los programas</option>
          {r.programas.map((p) => (
            <option key={p.programa} value={p.programa}>
              {p.programa} · {p.nombre} ({fmt(p.casos)})
            </option>
          ))}
        </Select>
        <Input
          className="h-10 w-28"
          placeholder="Periodo"
          value={filtros.periodo}
          onChange={(e) => cambiar('periodo')(e.target.value.replace(/\D/g, '').slice(0, 5))}
        />
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            checked={filtros.ocultar_info === '1'}
            onChange={(e) => cambiar('ocultar_info')(e.target.checked ? '1' : '')}
          />
          Ocultar informativas
        </label>
        <div className="ml-auto flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!!ocupado || !casos.data?.count}
            onClick={() => exportar('csv')}
            title="CSV con los casos que muestran los filtros actuales"
          >
            {ocupado === 'csv' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
            Exportar CSV
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!!ocupado || !casos.data?.count}
            onClick={() => exportar('excel')}
            title="Excel (Resumen, Casos y Por persona) con los filtros actuales"
          >
            {ocupado === 'excel' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSpreadsheet className="h-4 w-4" />}
            Exportar Excel
          </Button>
        </div>
      </div>
      {casos.data && (
        <p className="text-xs text-muted-foreground">
          {fmt(casos.data.count)} casos con los filtros actuales · CSV y Excel exportan exactamente estos casos.
        </p>
      )}

      <MensajeError mensaje={casos.error} />
      {casos.cargando && !casos.data ? (
        <Cargando />
      ) : (
        <>
          <Tabla
            columnas={[
              {
                clave: 'severidad',
                titulo: 'Sev.',
                render: (f) => (
                  <div className="space-y-1">
                    <Badge tono={f.severidad}>{sev(f.severidad)}</Badge>
                    {f.gravedad && <p className="text-xs text-muted-foreground">Gravedad {f.gravedad.slice(2).toLowerCase()}</p>}
                  </div>
                ),
              },
              {
                clave: 'tipo',
                titulo: 'Anomalía',
                render: (f) => (
                  <div className="min-w-[180px]">
                    <p>{r.tipos.find((t) => t.tipo === f.tipo)?.titulo || f.tipo}</p>
                    <p className="text-xs text-muted-foreground">{f.responsable}</p>
                  </div>
                ),
              },
              {
                clave: 'persona',
                titulo: 'Estudiante',
                render: (f) => (
                  <div className="min-w-[180px]">
                    <p className="font-medium text-foreground">{f.nombre || '—'}</p>
                    <p className="text-xs text-muted-foreground">
                      {f.estudiante} · doc. {f.documento || '—'}
                    </p>
                  </div>
                ),
              },
              { clave: 'periodo', titulo: 'Periodo', render: (f) => nombrePeriodo(f.periodo) },
              {
                clave: 'programa',
                titulo: 'Programa / asignatura',
                render: (f) => (
                  <div className="min-w-[180px]">
                    <p>{f.programa_nombre || f.programa || '—'}</p>
                    {f.asignatura && (
                      <p className="text-xs text-muted-foreground">
                        {f.asignatura_nombre || f.asignatura} · {f.tipo_nota}
                      </p>
                    )}
                  </div>
                ),
              },
              {
                clave: 'hallazgo',
                titulo: 'Hallazgo',
                render: (f) => <p className="max-w-md text-sm text-muted-foreground">{f.hallazgo}</p>,
              },
              {
                clave: 'estado_migracion',
                titulo: 'Migración',
                render: (f) => {
                  const [t, tono] = MIGRACION[f.estado_migracion] || [f.estado_migracion, null]
                  return (
                    <div className="space-y-1">
                      <Badge tono={tono}>{t}</Badge>
                      {f.nota_migra !== null && (
                        <p className="text-xs text-muted-foreground">{f.nota_migra ? 'La nota viaja' : 'La nota no viaja'}</p>
                      )}
                    </div>
                  )
                },
              },
            ]}
            filas={casos.data?.results}
            vacio="Sin casos con estos filtros"
          />
          <Paginacion data={casos.data} pagina={pagina} onPagina={setPagina} />
        </>
      )}
    </div>
  )
}
