import { useMemo, useState } from 'react'
import { Download, Loader2, Search } from 'lucide-react'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import {
  API_MIGRACION,
  Badge,
  Cargando,
  MensajeError,
  fmt,
  Kpi,
  Modal,
  Paginacion,
  SesionExpirada,
  Tabla,
  Tarjeta,
  useApi,
} from './comunes'

const SEXO = { 1: 'Masculino', 2: 'Femenino', 3: 'Otro' }
const ESTADO_CIVIL = { 1: 'Soltero(a)', 2: 'Casado(a)', 3: 'Divorciado(a)', 4: 'Viudo(a)', 5: 'Unión libre' }
const fecha = (f) => (f ? new Date(f).toLocaleDateString('es-CO') : '—')

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

function FichaEstudiante({ codigo, getHash, onSesionExpirada }) {
  const { data, error, cargando } = useApi(getHash, `/erp/estudiantes/${codigo}/`, onSesionExpirada)
  const [periodo, setPeriodo] = useState('')
  const periodosNotas = useMemo(() => [...new Set((data?.notas || []).map((n) => n.periodo))], [data])
  if (cargando && !data) return <Cargando />
  if (error) return <MensajeError mensaje={error} />
  const p = data.persona
  const notas = periodo ? data.notas.filter((n) => n.periodo === periodo) : data.notas
  const aprobadas = data.notas.filter((n) => n.definitiva !== null && Number(n.definitiva) >= 3).length

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-xl font-semibold text-foreground">
            {[p.primer_nombre, p.segundo_nombre, p.primer_apellido, p.segundo_apellido].filter(Boolean).join(' ')}
          </h3>
          <p className="text-sm text-muted-foreground">
            Código <b className="text-foreground">{p.codigo}</b> · {p.tipo_documento} {p.nro_documento}
          </p>
        </div>
        <div className="flex gap-2">
          <Badge tono={p.de_swa ? 'BAJA' : 'LISTA'}>{p.de_swa ? 'Migrado de SWA' : 'Nativo del ERP'}</Badge>
          {data.usuario ? <Badge tono="LISTA">Usuario: {data.usuario.username}</Badge> : <Badge tono="REVISION">Sin usuario</Badge>}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Tarjeta titulo="Datos personales (erp_persona / erp_datossecundarios)">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
            {[
              ['Correo', p.email || '—'],
              ['Teléfono', p.telefono || '—'],
              ['Nacimiento', p.fecha_nacimiento || '—'],
              ['Sexo', SEXO[p.sexo_biologico] || '—'],
              ['Estado civil', ESTADO_CIVIL[p.estado_civil] || '—'],
              ['Dirección', p.direccion || '—'],
              ['Expedición documento', p.fecha_expedicion_documento || 'No registrada'],
              ['Creado en el ERP', fecha(p.fecha_creacion)],
              ['Roles del usuario', data.usuario?.roles || '—'],
            ].map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="font-medium text-foreground">{v}</dd>
              </div>
            ))}
          </dl>
        </Tarjeta>
        <Tarjeta titulo="Vínculos (erp_vinculopersona)">
          <Tabla
            columnas={[
              { clave: 'tipo', titulo: 'Tipo' },
              { clave: 'programa', titulo: 'Programa', className: 'text-xs' },
              { clave: 'sede', titulo: 'Sede' },
              { clave: 'periodo', titulo: 'Periodo' },
              { clave: 'activo', titulo: 'Activo', render: (f) => (f.activo ? 'Sí' : 'No') },
              { clave: 'estado', titulo: 'Estado' },
            ]}
            filas={data.vinculos}
          />
        </Tarjeta>
      </div>

      <Tarjeta titulo={`Matrículas (academico_matricula) · ${data.matriculas.length}`}>
        <Tabla
          columnas={[
            { clave: 'periodo', titulo: 'Periodo' },
            { clave: 'programa', titulo: 'Programa', className: 'text-xs' },
            { clave: 'codigo', titulo: 'Código' },
            { clave: 'pensum', titulo: 'Pensum', render: (f) => `${f.pensum}${f.pensum_abierto ? '' : ' (histórico)'}` },
            { clave: 'semestre_actual', titulo: 'Semestre', className: 'text-right' },
            { clave: 'vigente', titulo: 'Vigente', render: (f) => (f.vigente ? 'Sí' : 'No') },
            { clave: 'notas', titulo: 'Notas', className: 'text-right' },
          ]}
          filas={data.matriculas}
        />
      </Tarjeta>

      <Tarjeta
        titulo={`Notas · ${fmt(data.notas.length)} (${fmt(aprobadas)} aprobadas)`}
        accion={
          <Select value={periodo} onChange={setPeriodo}>
            <option value="">Todos los periodos</option>
            {periodosNotas.map((x) => (
              <option key={x} value={x}>{x}</option>
            ))}
          </Select>
        }
      >
        <Tabla
          columnas={[
            { clave: 'periodo', titulo: 'Periodo' },
            { clave: 'asignatura_codigo', titulo: 'Código' },
            { clave: 'asignatura', titulo: 'Asignatura' },
            { clave: 'semestre', titulo: 'Sem.', className: 'text-right' },
            { clave: 'creditos', titulo: 'Créd.', className: 'text-right' },
            {
              clave: 'definitiva',
              titulo: 'Definitiva',
              className: 'text-right tabular-nums',
              render: (f) =>
                f.definitiva === null ? '—' : (
                  <span className={Number(f.definitiva) >= 3 ? 'text-emerald-600' : 'text-rose-600'}>
                    {Number(f.definitiva).toFixed(2)}
                  </span>
                ),
            },
            { clave: 'origen', titulo: 'Tabla', className: 'text-xs text-muted-foreground' },
          ]}
          filas={notas}
          vacio="Sin notas"
        />
      </Tarjeta>

      {data.ingles.length > 0 && (
        <Tarjeta titulo="Módulos de inglés completados (academico_homologacioningles)">
          <Tabla
            columnas={[
              { clave: 'centro', titulo: 'Centro', className: 'text-xs' },
              { clave: 'modulo', titulo: 'Módulo' },
              { clave: 'motivo', titulo: 'Motivo' },
              { clave: 'observacion', titulo: 'Observación', className: 'text-xs text-muted-foreground' },
            ]}
            filas={data.ingles}
          />
        </Tarjeta>
      )}
    </div>
  )
}

/** Estudiantes tal como están en el ERP (copia erp_migracion). Solo lectura. */
export default function EstudiantesErp({ getHash, onSesionExpirada }) {
  const opciones = useApi(getHash, '/erp/estudiantes/opciones/', onSesionExpirada)
  const [filtros, setFiltros] = useState({ q: '', programa: '', periodo: '', estado: '', sede: '', origen: '' })
  const [texto, setTexto] = useState('')
  const [pagina, setPagina] = useState(1)
  const [seleccion, setSeleccion] = useState(null)
  const [exportando, setExportando] = useState(false)
  const [errorExport, setErrorExport] = useState('')

  const params = new URLSearchParams({ page: String(pagina) })
  Object.entries(filtros).forEach(([k, v]) => v && params.set(k, v))
  const lista = useApi(getHash, `/erp/estudiantes/?${params}`, onSesionExpirada)
  const cambiar = (k) => (v) => {
    setFiltros((f) => ({ ...f, [k]: v }))
    setPagina(1)
  }

  async function exportar() {
    setExportando(true)
    setErrorExport('')
    try {
      const p = new URLSearchParams()
      Object.entries(filtros).forEach(([k, v]) => v && p.set(k, v))
      const res = await fetch(`${API_MIGRACION}/erp/estudiantes/exportar/?${p}`, {
        headers: { 'X-Consulta-Password': getHash() },
      })
      if (res.status === 403) throw new SesionExpirada('Sesión expirada')
      if (!res.ok) throw new Error(`Error ${res.status}`)
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a')
      a.href = url
      a.download = 'estudiantes_erp.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      if (err instanceof SesionExpirada) onSesionExpirada()
      setErrorExport(err.message)
    } finally {
      setExportando(false)
    }
  }

  const o = opciones.data
  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">
        Lo que hay hoy en el ERP (copia <code>erp_migracion</code>), leído directo de sus tablas: personas con vínculo
        Estudiante, sus matrículas, notas e inglés. Solo lectura.
      </p>

      {o && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Kpi etiqueta="Estudiantes en el ERP" valor={fmt(o.totales.estudiantes)} />
          <Kpi etiqueta="Con vínculo activo" valor={fmt(o.totales.activos)} tono="ok" />
          <Kpi etiqueta="Migrados de SWA" valor={fmt(o.totales.de_swa)} ayuda={`${fmt(o.totales.estudiantes - o.totales.de_swa)} nativos del ERP`} />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <form className="relative min-w-[240px] flex-1" onSubmit={(e) => { e.preventDefault(); cambiar('q')(texto.trim()) }}>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="h-10 pl-9" placeholder="Código, documento o nombre (Enter)" value={texto} onChange={(e) => setTexto(e.target.value)} />
        </form>
        <Select value={filtros.programa} onChange={cambiar('programa')} className="max-w-[260px]">
          <option value="">Todos los programas</option>
          {o?.programas.map((p) => (
            <option key={p.id} value={p.id}>{p.nombre} ({p.sede}) · {fmt(p.estudiantes)}</option>
          ))}
        </Select>
        <Select value={filtros.periodo} onChange={cambiar('periodo')}>
          <option value="">Cualquier periodo</option>
          {o?.periodos.filter((p) => p.matriculas > 0).map((p) => (
            <option key={p.id} value={p.id}>{p.nombre}{p.activo ? ' (activo)' : ''} · {fmt(p.matriculas)}</option>
          ))}
        </Select>
        <Select value={filtros.estado} onChange={cambiar('estado')}>
          <option value="">Activos e inactivos</option>
          <option value="activo">Con vínculo activo</option>
          <option value="inactivo">Sin vínculo activo</option>
        </Select>
        <Select value={filtros.sede} onChange={cambiar('sede')}>
          <option value="">Todas las sedes</option>
          {o?.sedes.map((s) => (
            <option key={s.id} value={s.id}>{s.nombre}</option>
          ))}
        </Select>
        <Select value={filtros.origen} onChange={cambiar('origen')}>
          <option value="">SWA y ERP</option>
          <option value="swa">Migrados de SWA</option>
          <option value="erp">Nativos del ERP</option>
        </Select>
        <Button variant="outline" size="sm" disabled={exportando} onClick={exportar}>
          {exportando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />} Exportar CSV
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        El filtro de periodo muestra a quien tiene matrícula en ese periodo (en el programa elegido, si se elige uno).
      </p>

      <MensajeError mensaje={opciones.error || lista.error || errorExport} />
      {lista.cargando && !lista.data ? (
        <Cargando />
      ) : (
        <>
          <Tabla
            onFila={(f) => setSeleccion(f.codigo)}
            columnas={[
              { clave: 'codigo', titulo: 'Código' },
              { clave: 'nro_documento', titulo: 'Documento', render: (f) => `${f.tipo_documento} ${f.nro_documento}` },
              { clave: 'nombre', titulo: 'Nombre', className: 'font-medium' },
              {
                clave: 'programas',
                titulo: 'Programa(s)',
                className: 'text-xs',
                render: (f) =>
                  (f.programas || []).map((p) => (
                    <div key={p.id}>
                      {p.nombre} <span className="text-muted-foreground">({p.sede})</span>{' '}
                      {p.activo && <Badge tono="LISTA">activo</Badge>}
                    </div>
                  )),
              },
              { clave: 'ultimo_periodo', titulo: 'Últ. periodo' },
              { clave: 'matriculas', titulo: 'Matr.', className: 'text-right' },
              { clave: 'notas', titulo: 'Notas', className: 'text-right' },
              { clave: 'modulos_ingles', titulo: 'Inglés', className: 'text-right', render: (f) => f.modulos_ingles || '—' },
              { clave: 'tiene_usuario', titulo: 'Usuario', render: (f) => (f.tiene_usuario ? 'Sí' : 'No') },
              { clave: 'de_swa', titulo: 'Origen', render: (f) => <Badge tono={f.de_swa ? 'BAJA' : 'LISTA'}>{f.de_swa ? 'SWA' : 'ERP'}</Badge> },
            ]}
            filas={lista.data?.results}
            vacio="Ningún estudiante con estos filtros"
          />
          <Paginacion data={lista.data} pagina={pagina} onPagina={setPagina} />
        </>
      )}

      <Modal abierto={!!seleccion} onCerrar={() => setSeleccion(null)} ancho="max-w-6xl">
        {seleccion && <FichaEstudiante codigo={seleccion} getHash={getHash} onSesionExpirada={onSesionExpirada} />}
      </Modal>
    </div>
  )
}
