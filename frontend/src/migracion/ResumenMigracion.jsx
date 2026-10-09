import { useState } from 'react'
import { ArrowRight, Loader2, RefreshCw, RotateCcw, Send, ShieldAlert } from 'lucide-react'
import { Button } from '../components/ui/button'
import {
  apiMigracion,
  BarraApilada,
  Badge,
  Cargando,
  MensajeError,
  etiqueta,
  fmt,
  Modal,
  pct,
  Tabla,
  Tarjeta,
  useApi,
} from './comunes'
import PreparacionErp from './PreparacionErp'

const fecha = (f) => (f ? new Date(f).toLocaleString('es-CO') : '—')

/** Tarjeta de indicador con su explicación visible. */
function Indicador({ titulo, valor, detalle, explicacion, tono = 'default', accion }) {
  const color = {
    default: 'text-foreground',
    ok: 'text-emerald-600 dark:text-emerald-400',
    alerta: 'text-amber-600 dark:text-amber-400',
  }[tono]
  return (
    <div className="flex flex-col rounded-2xl border border-border bg-card p-4 shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{titulo}</p>
      <p className={`mt-2 text-3xl font-semibold tabular-nums ${color}`}>{valor}</p>
      {detalle && <p className="text-sm font-medium text-foreground">{detalle}</p>}
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{explicacion}</p>
      {accion && <div className="mt-3">{accion}</div>}
    </div>
  )
}

/** Paso del embudo de alcance. */
function PasoAlcance({ cantidad, titulo, texto, tono }) {
  const estilos = {
    entra: 'border-emerald-500/40 bg-emerald-500/5',
    decidir: 'border-amber-500/40 bg-amber-500/5',
    fuera: 'border-border bg-secondary/40',
    base: 'border-border bg-card',
  }[tono]
  return (
    <div className={`rounded-xl border p-3 ${estilos}`}>
      <p className="text-2xl font-semibold tabular-nums text-foreground">{fmt(cantidad)}</p>
      <p className="text-sm font-semibold text-foreground">{titulo}</p>
      <p className="mt-1 text-xs text-muted-foreground">{texto}</p>
    </div>
  )
}

function ComparativoEntidad({ e }) {
  const otros = Math.max(e.antes - e.despues - e.revision - e.perdida, 0)
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <p className="font-semibold text-foreground">{e.entidad}</p>
        <p className="text-sm tabular-nums text-muted-foreground">
          {fmt(e.antes)} en SWA <ArrowRight className="inline h-3 w-3" />{' '}
          <span className="font-semibold text-foreground">{fmt(e.despues)} listos</span>
        </p>
      </div>
      <BarraApilada
        total={e.antes}
        segmentos={[
          { titulo: 'Listos para el ERP', valor: e.despues, className: 'bg-emerald-500' },
          { titulo: 'Unificados o reemplazados', valor: otros, className: 'bg-sky-400' },
          { titulo: 'Esperando decisión', valor: e.revision, className: 'bg-amber-400' },
          { titulo: 'No se migran', valor: e.perdida, className: 'bg-rose-500' },
        ]}
      />
    </div>
  )
}

export default function ResumenMigracion({ getHash, onSesionExpirada, irA }) {
  const resumen = useApi(getHash, '/resumen/', onSesionExpirada)
  const resultado = useApi(getHash, '/resultado/', onSesionExpirada)
  const [confirmar, setConfirmar] = useState(false)
  const [ocupado, setOcupado] = useState('')
  const [mensaje, setMensaje] = useState(null)

  async function accion(nombre, ruta) {
    setOcupado(nombre)
    setMensaje(null)
    try {
      const data = await apiMigracion(getHash, ruta, { method: 'POST', body: '{}' })
      setMensaje({ tipo: 'ok', data, nombre })
      await Promise.all([resumen.recargar(), resultado.recargar()])
    } catch (err) {
      setMensaje({ tipo: 'error', texto: err.message })
    } finally {
      setOcupado('')
      setConfirmar(false)
    }
  }

  if (resumen.cargando && !resumen.data) return <Cargando texto="Calculando el estado de la migración..." />
  if (resumen.error && !resumen.data) return <MensajeError mensaje={resumen.error} />
  const m = resumen.data
  const g = m.global
  const personas = m.entidades.find((e) => e.entidad === 'Personas')
  const d = personas.detalle
  const notas = m.entidades.find((e) => e.entidad === 'Notas')
  const pctListo = g.antes ? (100 * g.despues) / g.antes : 0
  const hayEnvios = m.lotes.some((l) => l.estado === 'COMPLETADO')
  const { PERIODO_EN_CURSO_SE_MIGRA_AL_CIERRE: aplazadas = 0, ...descartesNotas } = notas.detalle.descartes_por_regla
  const correcciones = { ...d.transformaciones, ...descartesNotas }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          Copia de SWA del <b>{fecha(m.ultima_copia_swa)}</b> · última depuración <b>{fecha(m.ultima_depuracion)}</b>
        </p>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" disabled={!!ocupado} onClick={() => accion('depurar', '/depurar/')}>
            {ocupado === 'depurar' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Aplicar decisiones (re-depurar)
          </Button>
          <Button size="sm" disabled={!!ocupado} onClick={() => setConfirmar(true)}>
            <Send className="h-4 w-4" /> Enviar a la copia (ensayo)
          </Button>
        </div>
      </div>

      {mensaje?.tipo === 'error' && <MensajeError mensaje={mensaje.texto} />}
      {mensaje?.tipo === 'ok' && mensaje.nombre === 'enviar' && (
        <p className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
          Lote #{mensaje.data.lote} enviado: {fmt(mensaje.data.resumen.persona_creada)} personas creadas,{' '}
          {fmt(mensaje.data.resumen.matricula)} matrículas y{' '}
          {fmt(mensaje.data.resumen.nota_historica + mensaje.data.resumen.nota_en_curso)} notas.
        </p>
      )}
      {mensaje?.tipo === 'ok' && mensaje.nombre === 'depurar' && (
        <p className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
          Decisiones aplicadas. Las cifras ya están actualizadas.
        </p>
      )}

      <Tarjeta titulo="¿Quiénes entran a la migración?">
        <p className="mb-4 text-sm text-muted-foreground">
          Se migra a toda persona que tuvo una matrícula en SWA en <b>cualquier periodo</b>, hoy activa o no. Aspirantes,
          terceros y proveedores no están en esta cuenta porque no tienen código de estudiante.
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <PasoAlcance tono="base" cantidad={d.personas_swa_total} titulo="Personas con código de estudiante en SWA" texto="Punto de partida." />
          <PasoAlcance tono="entra" cantidad={d.con_matricula} titulo="Tuvieron matrícula" texto="Al menos un periodo matriculados (M). Entran." />
          <PasoAlcance tono="decidir" cantidad={d.solo_prematricula} titulo="Solo prematrícula" texto="Se les liquidó pero nunca legalizaron ni tienen notas. Decidir en casos." />
          <PasoAlcance tono="decidir" cantidad={d.solo_notas_sin_matricula} titulo="Solo notas" texto="Tienen notas pero ninguna matrícula registrada. Decidir en casos." />
          <PasoAlcance tono="fuera" cantidad={d.solo_codigo_fuera_de_alcance} titulo="Solo código" texto="Inscritos/admitidos que nunca se matricularon. No entran (ver Información apartada)." />
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          Los {fmt(personas.antes)} registros de persona que entran o están por decidir corresponden a{' '}
          <b className="text-foreground">{fmt(d.personas_unicas)} personas reales</b>: {fmt(d.unificadas)} registros eran
          duplicados de la misma persona y se unificaron.
        </p>
      </Tarjeta>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Indicador
          titulo="Listo para enviar"
          valor={pct(pctListo)}
          detalle={`${fmt(g.despues)} de ${fmt(g.antes)} registros`}
          explicacion="Personas, matrículas y notas de SWA que ya están depuradas y viajarían al ERP si se envía ahora."
          tono="ok"
        />
        <Indicador
          titulo="Corregido automáticamente"
          valor={pct(g.pct_depuracion)}
          detalle="de los registros"
          explicacion="Registros que el sistema arregló solo: duplicados unificados, documentos y nombres normalizados, notas de habilitación que reemplazan a la original, etc."
        />
        <Indicador
          titulo="Pendiente de decisión"
          valor={fmt(g.casos_pendientes)}
          detalle="casos"
          explicacion="Situaciones que necesitan que una persona decida. Mientras no se decidan, esa información no se envía."
          tono={g.casos_pendientes ? 'alerta' : 'ok'}
          accion={
            <Button size="sm" variant="outline" onClick={() => irA('casos')}>
              Ir a decidir <ArrowRight className="h-4 w-4" />
            </Button>
          }
        />
        <Indicador
          titulo="Riesgo de pérdida"
          valor={pct(g.pct_probabilidad_perdida)}
          detalle={`Pérdida ya confirmada: ${pct(g.pct_perdida_confirmada)}`}
          explicacion="Si todo lo pendiente se excluyera, este porcentaje de la información no llegaría al ERP. Baja a medida que se deciden casos."
          tono="alerta"
        />
      </div>

      {aplazadas > 0 && (
        <p className="rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-2 text-sm text-sky-700 dark:text-sky-300">
          {fmt(aplazadas)} notas del periodo 2026-2 están aplazadas: el periodo sigue abierto en SWA y se migran cuando cierre.
          No se pierden ni cuentan como riesgo.
        </p>
      )}

      <PreparacionErp getHash={getHash} onSesionExpirada={onSesionExpirada} />

      <Tarjeta titulo="Qué se crea y qué se vincula en el ERP">
        <Tabla
          columnas={[
            { clave: 'item', titulo: 'Qué', className: 'font-medium whitespace-nowrap' },
            { clave: 'cantidad', titulo: 'Cantidad', className: 'text-right tabular-nums', render: (f) => fmt(f.cantidad) },
            { clave: 'tabla', titulo: 'Tabla del ERP', render: (f) => <code className="text-xs">{f.tabla}</code> },
            { clave: 'como', titulo: 'Cómo se enlaza', className: 'text-muted-foreground' },
          ]}
          filas={m.vinculacion}
        />
        <p className="mt-2 text-xs text-muted-foreground">Cantidades de lo que está listo hoy; cambian al decidir casos.</p>
      </Tarjeta>

      <div className="grid gap-6 lg:grid-cols-2">
        <Tarjeta titulo="Antes (SWA) y después (listo)">
          <div className="space-y-5">
            {m.entidades.map((e) => (
              <ComparativoEntidad key={e.entidad} e={e} />
            ))}
          </div>
          <div className="mt-4 flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-full bg-emerald-500" /> Listos</span>
            <span className="inline-flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-full bg-sky-400" /> Unificados o reemplazados</span>
            <span className="inline-flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-full bg-amber-400" /> Esperando decisión</span>
            <span className="inline-flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-full bg-rose-500" /> No se migran</span>
          </div>
        </Tarjeta>

        <Tarjeta titulo="Correcciones automáticas más frecuentes">
          <ul className="divide-y divide-border text-sm">
            {Object.entries(correcciones)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 8)
              .map(([k, v]) => (
                <li key={k} className="flex items-center justify-between gap-3 py-1.5">
                  <span className="text-muted-foreground">{etiqueta(k)}</span>
                  <span className="font-semibold tabular-nums text-foreground">{fmt(v)}</span>
                </li>
              ))}
          </ul>
          <p className="mt-3 text-xs text-muted-foreground">
            Datos que faltan en SWA (no se inventan):{' '}
            {Object.entries(d.alertas_calidad || {})
              .map(([k, v]) => `${etiqueta(k).toLowerCase()} ${fmt(v)}`)
              .join(' · ')}
            .
          </p>
        </Tarjeta>
      </div>

      {(hayEnvios || m.lotes.length > 0) && (
        <Tarjeta titulo="Envíos al ERP">
          <Tabla
            columnas={[
              { clave: 'id', titulo: 'Lote' },
              { clave: 'estado', titulo: 'Estado', render: (f) => <Badge>{f.estado}</Badge> },
              { clave: 'inicio', titulo: 'Fecha', render: (f) => fecha(f.inicio) },
              {
                clave: 'resumen',
                titulo: 'Qué se envió',
                render: (f) =>
                  f.error ? (
                    <span className="text-rose-600">No se aplicó ningún cambio: {f.error}</span>
                  ) : f.resumen ? (
                    `${fmt(f.resumen.persona_creada)} personas · ${fmt(f.resumen.matricula)} matrículas · ${fmt(
                      (f.resumen.nota_historica || 0) + (f.resumen.nota_en_curso || 0),
                    )} notas · ${fmt(f.resumen.homologacion_ingles)} módulos de inglés`
                  ) : '—',
              },
              {
                clave: 'acciones',
                titulo: '',
                render: (f) =>
                  f.estado === 'COMPLETADO' ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!!ocupado}
                      onClick={() => {
                        if (window.confirm(`¿Revertir el lote #${f.id}? Se borrará del ERP todo lo que creó.`)) {
                          accion('revertir', `/lotes/${f.id}/revertir/`)
                        }
                      }}
                    >
                      <RotateCcw className="h-4 w-4" /> Revertir
                    </Button>
                  ) : null,
              },
            ]}
            filas={m.lotes}
          />
          {hayEnvios && (
            <div className="mt-4">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Resultado en el ERP</h4>
              <Tabla
                columnas={[
                  { clave: 'entidad', titulo: 'Tabla', render: (f) => etiqueta(f.entidad) },
                  { clave: 'total_erp', titulo: 'Total en ERP', className: 'text-right tabular-nums', render: (f) => fmt(f.total_erp) },
                  { clave: 'creados_migracion', titulo: 'Creados por la migración', className: 'text-right tabular-nums', render: (f) => fmt(f.creados_migracion) },
                  { clave: 'enlazados_existentes', titulo: 'Ya existían (enlazados)', className: 'text-right tabular-nums', render: (f) => fmt(f.enlazados_existentes) },
                ]}
                filas={resultado.data?.entidades}
              />
            </div>
          )}
        </Tarjeta>
      )}

      <Modal abierto={confirmar} onCerrar={() => !ocupado && setConfirmar(false)} titulo="Enviar a la copia del ERP (ensayo)">
        <div className="space-y-3 text-sm text-muted-foreground">
          <p>
            Se aplican las decisiones tomadas y se envía a la <b className="text-foreground">copia local</b> del ERP todo lo que esté{' '}
            <b className="text-foreground">listo</b>, en una sola operación: si algo falla no se aplica ningún cambio. El envío se
            puede revertir. El ERP de producción no se toca: el envío real se hace en la ventana de corte con{' '}
            <code>migra_corte</code>.
          </p>
          {g.casos_pendientes > 0 && (
            <p className="flex gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-amber-700 dark:text-amber-300">
              <ShieldAlert className="h-5 w-5 shrink-0" />
              Hay {fmt(g.casos_pendientes)} casos sin decidir. Esa información no se envía ahora; podrá enviarse en otro lote
              cuando se decida.
            </p>
          )}
          <p>SWA no se modifica: solo se lee desde la copia local.</p>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" disabled={!!ocupado} onClick={() => setConfirmar(false)}>
            Cancelar
          </Button>
          <Button disabled={!!ocupado} onClick={() => accion('enviar', '/enviar/')}>
            {ocupado === 'enviar' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            {ocupado === 'enviar' ? 'Enviando...' : 'Sí, enviar'}
          </Button>
        </div>
      </Modal>
    </div>
  )
}
