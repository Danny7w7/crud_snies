import { useMemo, useState } from 'react'
import { ArrowLeft, Search } from 'lucide-react'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import {
  Badge,
  Cargando,
  MensajeError,
  etiqueta,
  fmt,
  nombrePeriodo,
  Paginacion,
  Tabla,
  Tarjeta,
  TIPOS_DOCUMENTO,
  TIPOS_SWA,
  useApi,
} from './comunes'

const nombre = (p) => [p.primer_nombre, p.segundo_nombre, p.primer_apellido, p.segundo_apellido].filter(Boolean).join(' ')

function Pestanas({ opciones, activa, onCambio }) {
  return (
    <div className="mb-3 flex flex-wrap gap-1 rounded-xl bg-secondary/70 p-1">
      {opciones.map(([clave, texto]) => (
        <button
          key={clave}
          onClick={() => onCambio(clave)}
          className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
            activa === clave ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
          }`}
        >
          {texto}
        </button>
      ))}
    </div>
  )
}

/** Antes (SWA crudo) y después (depurado / ERP) de una persona. */
export function PersonaDetalle({ nid, getHash, onSesionExpirada }) {
  const { data, error, cargando } = useApi(getHash, `/personas/${encodeURIComponent(nid)}/`, onSesionExpirada)
  const [vistaAntes, setVistaAntes] = useState('personas')
  const [vistaDespues, setVistaDespues] = useState('persona')

  const notasPorEstado = useMemo(() => {
    const r = { LISTA: 0, REVISION: 0, EXCLUIDA: 0 }
    data?.despues.notas.forEach((n) => { r[n.estado] = (r[n.estado] || 0) + 1 })
    return r
  }, [data])

  if (cargando && !data) return <Cargando />
  if (error) return <MensajeError mensaje={error} />
  const { antes, despues, casos } = data
  const p = despues.persona

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-xl font-semibold text-foreground">{nombre(p)}</h3>
          <p className="text-sm text-muted-foreground">
            {TIPOS_DOCUMENTO[p.tipo_documento_id] || '¿?'} {p.nro_documento ?? nid}
            {' · '}Código <b className="text-foreground">{despues.erp_id || p.codigo_swa}</b>
            {p.codigos_swa.length > 1 && ` (+${p.codigos_swa.length - 1} códigos más)`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge>{p.estado}</Badge>
          <Badge tono="BAJA">{p.accion}</Badge>
        </div>
      </div>

      {(p.transformaciones.length > 0 || p.alertas.length > 0) && (
        <div className="flex flex-wrap gap-1.5">
          {p.transformaciones.map((t) => <Badge key={t} tono="LISTA">{etiqueta(t)}</Badge>)}
          {p.alertas.map((t) => <Badge key={t} tono="REVISION">{etiqueta(t)}</Badge>)}
        </div>
      )}

      {casos.length > 0 && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <p className="mb-1 font-semibold text-amber-700 dark:text-amber-300">Casos de revisión</p>
          <ul className="space-y-0.5 text-muted-foreground">
            {casos.map((c) => (
              <li key={c.id}>
                {c.titulo} — <b className="text-foreground">{c.decision || 'pendiente'}</b>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Tarjeta titulo="Antes · SWA (crudo)">
          <Pestanas
            activa={vistaAntes}
            onCambio={setVistaAntes}
            opciones={[
              ['personas', `Personas (${antes.personas.length})`],
              ['codigos', `Códigos (${antes.codigos.length})`],
              ['historico', `Histórico (${antes.historico.length})`],
              ['registros', `Notas (${antes.registros.length})`],
            ]}
          />
          {vistaAntes === 'personas' && (
            <Tabla
              columnas={[
                { clave: 'id', titulo: 'ID' },
                { clave: 'identificacion', titulo: 'Documento' },
                { clave: 'tipo_identificacion', titulo: 'Tipo', render: (f) => TIPOS_SWA[f.tipo_identificacion] ?? f.tipo_identificacion },
                { clave: 'nombre', titulo: 'Nombre' },
                { clave: 'apellido', titulo: 'Apellido' },
                { clave: 'email', titulo: 'Correo' },
                { clave: 'telefono', titulo: 'Teléfono' },
              ]}
              filas={antes.personas}
            />
          )}
          {vistaAntes === 'codigos' && (
            <Tabla
              columnas={[
                { clave: 'codigo', titulo: 'Código' },
                { clave: 'programa_nombre', titulo: 'Programa' },
                { clave: 'pensum', titulo: 'Pensum' },
                { clave: 'periodo_ingreso', titulo: 'Ingreso', render: (f) => nombrePeriodo(f.periodo_ingreso) },
                { clave: 'estado', titulo: 'Estado' },
                { clave: 'nivel', titulo: 'Nivel' },
              ]}
              filas={antes.codigos}
            />
          )}
          {vistaAntes === 'historico' && (
            <Tabla
              columnas={[
                { clave: 'periodo', titulo: 'Periodo', render: (f) => nombrePeriodo(f.periodo) },
                { clave: 'programa', titulo: 'Programa' },
                { clave: 'estudiante', titulo: 'Código' },
                { clave: 'estado', titulo: 'Estado', render: (f) => (f.estado === 'M' ? 'M (matriculado)' : f.estado) },
                { clave: 'nivel', titulo: 'Nivel' },
              ]}
              filas={antes.historico}
            />
          )}
          {vistaAntes === 'registros' && (
            <Tabla
              columnas={[
                { clave: 'periodo', titulo: 'Periodo', render: (f) => nombrePeriodo(f.periodo) },
                { clave: 'asignatura_nombre', titulo: 'Asignatura' },
                { clave: 'grupo', titulo: 'Grupo' },
                { clave: 'definitiva', titulo: 'Nota', className: 'text-right tabular-nums' },
                { clave: 'estado', titulo: 'Estado', render: (f) => `${f.estado}/${f.estado_definitiva}` },
              ]}
              filas={antes.registros}
            />
          )}
        </Tarjeta>

        <Tarjeta titulo="Después · depurado (destino ERP)">
          <Pestanas
            activa={vistaDespues}
            onCambio={setVistaDespues}
            opciones={[
              ['persona', 'Persona'],
              ['matriculas', `Matrículas (${despues.matriculas.length})`],
              ['notas', `Notas (${despues.notas.length})`],
              ['ingles', `Inglés (${despues.homologaciones_ingles.length})`],
              ['apartado', `Apartado (${despues.descartes.length})`],
            ]}
          />
          {vistaDespues === 'persona' && (
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-sm">
              {[
                ['Tipo documento', TIPOS_DOCUMENTO[p.tipo_documento_id] || '—'],
                ['Número', p.nro_documento ?? '—'],
                ['Primer nombre', p.primer_nombre],
                ['Segundo nombre', p.segundo_nombre || '—'],
                ['Primer apellido', p.primer_apellido],
                ['Segundo apellido', p.segundo_apellido || '—'],
                ['Correo', p.email || '—'],
                ['Teléfono', p.telefono ?? '—'],
                ['Fecha nacimiento', p.fecha_nacimiento || '—'],
                ['Código (ID en el ERP)', p.codigo_swa || 'Se genera uno nuevo'],
                ['Todos sus códigos SWA', p.codigos_swa.join(', ') || '—'],
                ['Registros SWA unificados', p.swa_ids.join(', ')],
                ['Vínculos', despues.vinculos.map((v) => `${v.programa}${v.activo ? ' (activo)' : ''}`).join(', ') || '—'],
              ].map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="font-medium text-foreground">{v}</dd>
                </div>
              ))}
            </dl>
          )}
          {vistaDespues === 'matriculas' && (
            <Tabla
              columnas={[
                { clave: 'periodo_nombre', titulo: 'Periodo' },
                { clave: 'programa', titulo: 'Programa' },
                { clave: 'codigo', titulo: 'Código', render: (f) => f.codigo + (f.codigos.length > 1 ? ` (+${f.codigos.length - 1})` : '') },
                { clave: 'nivel', titulo: 'Semestre' },
                { clave: 'origen', titulo: 'Origen', render: (f) => ({ HISTORICO_M: 'Matrícula (M)', PREMATRICULA: 'Prematrícula (P)', INFERIDA_NOTAS: 'Inferida de notas' })[f.origen] || f.origen },
                { clave: 'activa', titulo: 'Activa', render: (f) => (f.activa ? 'Sí' : 'No') },
                { clave: 'estado', titulo: 'Estado', render: (f) => <Badge>{f.estado}</Badge> },
              ]}
              filas={despues.matriculas}
            />
          )}
          {vistaDespues === 'notas' && (
            <>
              <p className="mb-2 text-xs text-muted-foreground">
                {fmt(notasPorEstado.LISTA)} listas · {fmt(notasPorEstado.REVISION)} en revisión · {fmt(notasPorEstado.EXCLUIDA)} excluidas
              </p>
              <Tabla
                columnas={[
                  { clave: 'periodo', titulo: 'Periodo', render: (f) => nombrePeriodo(f.periodo) },
                  { clave: 'asignatura_nombre', titulo: 'Asignatura' },
                  { clave: 'definitiva', titulo: 'Nota', className: 'text-right tabular-nums' },
                  { clave: 'en_curso', titulo: 'Tipo', render: (f) => (f.en_curso ? 'En curso' : f.grupo < 0 ? 'Especial' : 'Histórica') },
                  { clave: 'estado', titulo: 'Estado', render: (f) => <Badge>{f.estado}</Badge> },
                ]}
                filas={despues.notas}
              />
            </>
          )}
          {vistaDespues === 'ingles' && (
            <Tabla
              columnas={[
                { clave: 'programa', titulo: 'Centro' },
                { clave: 'modulo', titulo: 'Módulo' },
                { clave: 'periodo', titulo: 'Aprobado en', render: (f) => nombrePeriodo(f.periodo) },
                { clave: 'definitiva', titulo: 'Nota', className: 'text-right tabular-nums' },
                { clave: 'origen', titulo: 'Origen', render: (f) => (f.origen === 'APROBADO' ? 'Aprobado' : 'Copiado al centro actual') },
              ]}
              filas={despues.homologaciones_ingles}
              vacio="Sin módulos de inglés aprobados"
            />
          )}
          {vistaDespues === 'apartado' && (
            <Tabla
              columnas={[
                { clave: 'motivo', titulo: 'Motivo', render: (f) => etiqueta(f.motivo) },
                { clave: 'periodo', titulo: 'Periodo', render: (f) => nombrePeriodo(f.datos.periodo) },
                { clave: 'asignatura', titulo: 'Asignatura', render: (f) => f.datos.asignatura },
                { clave: 'nota', titulo: 'Nota', render: (f) => f.datos.definitiva },
                { clave: 'con_valor', titulo: 'Pierde info.', render: (f) => (f.con_valor ? 'Sí' : 'No') },
              ]}
              filas={despues.descartes}
              vacio="Nada apartado para esta persona"
            />
          )}
        </Tarjeta>
      </div>
    </div>
  )
}

export default function PersonasMigracion({ getHash, onSesionExpirada }) {
  const [q, setQ] = useState('')
  const [busqueda, setBusqueda] = useState('')
  const [estado, setEstado] = useState('')
  const [pagina, setPagina] = useState(1)
  const [seleccion, setSeleccion] = useState(null)

  const params = new URLSearchParams({ page: String(pagina) })
  if (busqueda) params.set('q', busqueda)
  if (estado) params.set('estado', estado)
  const { data, error, cargando } = useApi(getHash, `/personas/?${params}`, onSesionExpirada)

  if (seleccion) {
    return (
      <div className="space-y-4">
        <Button variant="outline" size="sm" onClick={() => setSeleccion(null)}>
          <ArrowLeft className="h-4 w-4" /> Volver al listado
        </Button>
        <PersonaDetalle nid={seleccion} getHash={getHash} onSesionExpirada={onSesionExpirada} />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <form className="relative min-w-[260px] flex-1" onSubmit={(e) => { e.preventDefault(); setPagina(1); setBusqueda(q.trim()) }}>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-9" placeholder="Código, documento o nombre (Enter para buscar)" value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
        {[['', 'Todas'], ['LISTA', 'Listas'], ['REVISION', 'En revisión'], ['EXCLUIDA', 'Excluidas']].map(([v, t]) => (
          <Button key={v} size="sm" variant={estado === v ? 'default' : 'outline'} onClick={() => { setEstado(v); setPagina(1) }}>
            {t}
          </Button>
        ))}
      </div>
      <MensajeError mensaje={error} />
      {cargando && !data ? (
        <Cargando />
      ) : (
        <>
          <Tabla
            onFila={(f) => setSeleccion(f.nid)}
            columnas={[
              { clave: 'codigo_swa', titulo: 'Código', render: (f) => f.codigo_swa || '—' },
              { clave: 'nid', titulo: 'Documento', render: (f) => `${TIPOS_DOCUMENTO[f.tipo_documento_id] || '¿?'} ${f.nro_documento ?? f.nid}` },
              { clave: 'nombre', titulo: 'Nombre', render: nombre },
              { clave: 'registros_swa', titulo: 'Reg. SWA', className: 'text-right' },
              { clave: 'matriculas', titulo: 'Matrículas', className: 'text-right' },
              { clave: 'transformaciones', titulo: 'Correcciones', render: (f) => f.transformaciones.length || '—', className: 'text-right' },
              { clave: 'accion', titulo: 'Acción' },
              { clave: 'estado', titulo: 'Estado', render: (f) => <Badge>{f.estado}</Badge> },
              { clave: 'erp_id', titulo: 'En ERP', render: (f) => (f.erp_id ? 'Enviada' : '—') },
            ]}
            filas={data?.results}
          />
          <Paginacion data={data} pagina={pagina} onPagina={setPagina} />
        </>
      )}
    </div>
  )
}
