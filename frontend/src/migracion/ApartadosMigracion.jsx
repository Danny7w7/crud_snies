import { useState } from 'react'
import { Cargando, MensajeError, etiqueta, fmt, nombrePeriodo, Paginacion, Tabla, Tarjeta, useApi } from './comunes'

/** Informacion apartada por reglas (no viaja al ERP) con su motivo. */
export default function ApartadosMigracion({ getHash, onSesionExpirada }) {
  const [motivo, setMotivo] = useState('')
  const [pagina, setPagina] = useState(1)
  const params = new URLSearchParams({ page: String(pagina) })
  if (motivo) params.set('motivo', motivo)
  const { data, error, cargando } = useApi(getHash, `/descartes/?${params}`, onSesionExpirada)

  if (cargando && !data) return <Cargando />
  return (
    <div className="space-y-4">
      <MensajeError mensaje={error} />
      <Tarjeta titulo="Motivos">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <button
            onClick={() => { setMotivo(''); setPagina(1) }}
            className={`rounded-xl border p-3 text-left ${motivo === '' ? 'border-accent bg-accent/5' : 'border-border'}`}
          >
            <p className="text-sm font-semibold">Todos</p>
            <p className="text-xs text-muted-foreground">{fmt(data?.motivos.reduce((a, m) => a + m.filas, 0))} filas</p>
          </button>
          {data?.motivos.map((m) => (
            <button
              key={m.motivo}
              onClick={() => { setMotivo(m.motivo); setPagina(1) }}
              className={`rounded-xl border p-3 text-left ${motivo === m.motivo ? 'border-accent bg-accent/5' : 'border-border'}`}
            >
              <p className="text-sm font-semibold">{etiqueta(m.motivo)}</p>
              <p className="text-xs text-muted-foreground">
                {fmt(m.filas)} filas · {fmt(m.con_valor)} con información útil
              </p>
            </button>
          ))}
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          Las notas reemplazadas por habilitación, validación u homologación no se pierden: el resultado final sí se
          migra y el intento original queda trazado aquí y en la copia de SWA.
        </p>
      </Tarjeta>
      <Tabla
        columnas={[
          { clave: 'motivo', titulo: 'Motivo', render: (f) => etiqueta(f.motivo) },
          {
            clave: 'estudiante',
            titulo: 'Código / persona',
            render: (f) =>
              f.entidad === 'persona'
                ? `${f.datos?.identificacion} · ${f.datos?.nombre} ${f.datos?.apellido} (${(f.datos?.codigos || [])
                    .map((c) => c.codigo)
                    .join(', ')})`
                : f.datos?.estudiante,
          },
          { clave: 'periodo', titulo: 'Periodo', render: (f) => nombrePeriodo(f.datos?.periodo) },
          { clave: 'programa', titulo: 'Programa', render: (f) => f.datos?.programa },
          { clave: 'asignatura', titulo: 'Asignatura', render: (f) => f.datos?.asignatura },
          { clave: 'grupo', titulo: 'Grupo', render: (f) => f.datos?.grupo },
          { clave: 'definitiva', titulo: 'Nota', render: (f) => f.datos?.definitiva },
          { clave: 'con_valor', titulo: 'Pierde info.', render: (f) => (f.con_valor ? 'Sí' : 'No') },
        ]}
        filas={data?.results}
      />
      <Paginacion data={data} pagina={pagina} onPagina={setPagina} />
    </div>
  )
}
