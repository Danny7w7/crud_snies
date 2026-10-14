import { useCallback, useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-react'
import { Button } from '../components/ui/button'

export const API_MIGRACION = '/api/migracion'

export class SesionExpirada extends Error {}

/** fetch contra la API de migracion con la contraseña de consulta. */
export async function apiMigracion(getHash, ruta, opciones = {}) {
  const res = await fetch(`${API_MIGRACION}${ruta}`, {
    ...opciones,
    headers: {
      'X-Consulta-Password': getHash(),
      ...(opciones.body ? { 'Content-Type': 'application/json' } : {}),
      ...(opciones.headers || {}),
    },
  })
  if (res.status === 403) throw new SesionExpirada('La sesión expiró. Vuelve a ingresar la contraseña.')
  const texto = await res.text()
  const data = texto ? JSON.parse(texto) : null
  if (!res.ok) throw new Error(data?.detail || `Error ${res.status}`)
  return data
}

/** Carga datos de la API y expone {data, error, cargando, recargar}. */
export function useApi(getHash, ruta, onSesionExpirada) {
  const [estado, setEstado] = useState({ data: null, error: '', cargando: true })
  const recargar = useCallback(async () => {
    if (!ruta) return
    setEstado((e) => ({ ...e, cargando: true, error: '' }))
    try {
      const data = await apiMigracion(getHash, ruta)
      setEstado({ data, error: '', cargando: false })
    } catch (err) {
      if (err instanceof SesionExpirada) onSesionExpirada?.()
      setEstado((e) => ({ ...e, error: err.message, cargando: false }))
    }
  }, [getHash, ruta, onSesionExpirada])
  useEffect(() => {
    recargar()
  }, [recargar])
  return { ...estado, recargar }
}

export const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString('es-CO'))
export const pct = (n) => (n == null ? '—' : `${Number(n).toLocaleString('es-CO', { maximumFractionDigits: 2 })} %`)
export const nombrePeriodo = (p) => (p ? `${Math.floor(p / 10)}-${p % 10}` : '—')

export const TIPOS_DOCUMENTO = { 1: 'CC', 2: 'CE', 3: 'TI', 4: 'PPT', 5: 'DE', 6: 'CA', 7: 'PS' }
export const TIPOS_SWA = { 0: 'POR DEFINIR', 1: 'CC', 2: 'PASAPORTE', 3: 'CE', 4: 'TI', 5: 'CABILDO', 8: 'RC' }

export const ETIQUETAS = {
  UNIFICADA_2_REGISTROS: 'Unificada (2 registros SWA)',
  UNIFICADA_3_REGISTROS: 'Unificada (3 registros SWA)',
  REGISTRO_X_ANULADO_UNIFICADO: 'Registro anulado (X…X) unificado',
  NOMBRE_NORMALIZADO: 'Nombre normalizado',
  TI_ACTUALIZADA_A_CC_POR_EDAD: 'TI actualizada a CC por edad',
  TIPO_INDEFINIDO_RESUELTO_POR_EDAD: 'Tipo POR DEFINIR resuelto por edad',
  TIPO_DOCUMENTO_DE_OTRO_REGISTRO: 'Tipo tomado de otro registro',
  VINCULADA_A_PERSONA_ERP: 'Vinculada a persona existente del ERP',
  VINCULADA_A_PERSONA_ERP_MANUAL: 'Vinculada al ERP (decisión manual)',
  DOCUMENTO_COMPARTIDO_RESUELTO: 'Documento compartido resuelto',
  DOCUMENTO_CORREGIDO_MANUAL: 'Documento corregido manualmente',
  TIPO_DOCUMENTO_CORREGIDO_MANUAL: 'Tipo corregido manualmente',
  NOMBRE_CORREGIDO_MANUAL: 'Nombre corregido manualmente',
  VARIOS_CODIGOS_SWA: 'Varios códigos SWA (se conservan todos)',
  CODIGO_SWA_CORREGIDO_MANUAL: 'Código SWA corregido manualmente',
  SIN_EMAIL_VALIDO: 'Sin correo válido',
  SIN_TELEFONO_VALIDO: 'Sin teléfono válido',
  FECHA_NACIMIENTO_NO_PLAUSIBLE: 'Fecha de nacimiento no plausible (descartada)',
  PREINSCRIPCION_SIN_CURSAR: 'Preinscripción sin cursar',
  PERIODO_EN_CURSO_SE_MIGRA_AL_CIERRE: 'Periodo 2026-2 en curso (se migra al cierre)',
  SOLO_CODIGO_SIN_MATRICULA: 'Solo código de estudiante (nunca se matriculó)',
  ASIGNATURA_CANCELADA: 'Asignatura cancelada',
  NOTA_DUPLICADA_MISMO_PERIODO: 'Nota duplicada en el mismo periodo',
  REEMPLAZADA_POR_HABILITACION: 'Reemplazada por habilitación',
  REEMPLAZADA_POR_VALIDACION: 'Reemplazada por validación',
  REEMPLAZADA_POR_HOMOLOGACION: 'Reemplazada por homologación',
  REEMPLAZADA_POR_CURSO_ESPECIAL: 'Reemplazada por curso especial',
}
export const etiqueta = (clave) => ETIQUETAS[clave] || clave

const ESTILOS_BADGE = {
  LISTA: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  REVISION: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  EXCLUIDA: 'bg-rose-500/15 text-rose-700 dark:text-rose-300',
  ALTA: 'bg-rose-500/15 text-rose-700 dark:text-rose-300',
  MEDIA: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  BAJA: 'bg-sky-500/15 text-sky-700 dark:text-sky-300',
  INFO: 'bg-slate-500/15 text-slate-700 dark:text-slate-300',
  COMPLETADO: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  FALLIDO: 'bg-rose-500/15 text-rose-700 dark:text-rose-300',
  REVERTIDO: 'bg-slate-500/15 text-slate-700 dark:text-slate-300',
  EN_PROCESO: 'bg-sky-500/15 text-sky-700 dark:text-sky-300',
}

export function Badge({ children, tono }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${
        ESTILOS_BADGE[tono || children] || 'bg-secondary text-secondary-foreground'
      }`}
    >
      {children}
    </span>
  )
}

export function Tarjeta({ titulo, children, accion, className = '' }) {
  return (
    <section className={`rounded-2xl border border-border bg-card p-5 shadow-sm ${className}`}>
      {(titulo || accion) && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          {titulo && <h3 className="text-base font-semibold text-foreground">{titulo}</h3>}
          {accion}
        </div>
      )}
      {children}
    </section>
  )
}

export function Kpi({ etiqueta: texto, valor, ayuda, tono = 'default' }) {
  const color = {
    default: 'text-foreground',
    ok: 'text-emerald-600 dark:text-emerald-400',
    alerta: 'text-amber-600 dark:text-amber-400',
    peligro: 'text-rose-600 dark:text-rose-400',
  }[tono]
  return (
    <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{texto}</p>
      <p className={`mt-2 text-3xl font-semibold tabular-nums ${color}`}>{valor}</p>
      {ayuda && <p className="mt-1 text-xs text-muted-foreground">{ayuda}</p>}
    </div>
  )
}

/** Tabla simple: columnas = [{clave, titulo, render?, className?}] */
export function Tabla({ columnas, filas, vacio = 'Sin datos', onFila }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full text-left text-sm">
        <thead className="bg-secondary/70 text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            {columnas.map((c) => (
              <th key={c.clave} className={`whitespace-nowrap px-3 py-2 font-semibold ${c.className || ''}`}>
                {c.titulo}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {filas?.length ? (
            filas.map((f, i) => (
              <tr
                key={f.id ?? f.nid ?? i}
                onClick={onFila ? () => onFila(f) : undefined}
                className={`border-t border-border ${onFila ? 'cursor-pointer hover:bg-secondary/60' : ''}`}
              >
                {columnas.map((c) => (
                  <td key={c.clave} className={`px-3 py-2 align-top ${c.className || ''}`}>
                    {c.render ? c.render(f) : (f[c.clave] ?? '—')}
                  </td>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={columnas.length} className="px-3 py-6 text-center text-muted-foreground">
                {vacio}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export function Paginacion({ data, pagina, onPagina }) {
  if (!data) return null
  const total = Math.max(Math.ceil(data.count / data.page_size), 1)
  return (
    <div className="mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground">
      <span>
        {fmt(data.count)} resultados · página {pagina} de {total}
      </span>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={pagina <= 1} onClick={() => onPagina(pagina - 1)}>
          <ChevronLeft className="h-4 w-4" /> Anterior
        </Button>
        <Button variant="outline" size="sm" disabled={pagina >= total} onClick={() => onPagina(pagina + 1)}>
          Siguiente <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}

export function Cargando({ texto = 'Cargando...' }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" /> {texto}
    </div>
  )
}

export function MensajeError({ mensaje }) {
  if (!mensaje) return null
  return (
    <p className="rounded-lg border border-destructive/40 bg-destructive/20 px-3 py-2 text-sm text-destructive-foreground">
      {mensaje}
    </p>
  )
}

/** Barra apilada con segmentos [{valor, className, titulo}] */
export function BarraApilada({ segmentos, total }) {
  return (
    <div className="flex h-3 w-full overflow-hidden rounded-full bg-secondary">
      {segmentos.map((s) =>
        s.valor > 0 ? (
          <div
            key={s.titulo}
            title={`${s.titulo}: ${fmt(s.valor)}`}
            className={s.className}
            style={{ width: `${(100 * s.valor) / (total || 1)}%` }}
          />
        ) : null,
      )}
    </div>
  )
}

export function Modal({ abierto, onCerrar, titulo, children, ancho = 'max-w-lg' }) {
  if (!abierto) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onCerrar}>
      <div
        className={`max-h-[90vh] w-full ${ancho} overflow-y-auto rounded-2xl border border-border bg-card p-6 shadow-2xl`}
        onClick={(e) => e.stopPropagation()}
      >
        {titulo && <h3 className="mb-4 text-lg font-semibold text-foreground">{titulo}</h3>}
        {children}
      </div>
    </div>
  )
}
