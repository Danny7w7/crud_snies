import { useState } from 'react'
import { CheckCircle2, Circle, Loader2 } from 'lucide-react'
import { apiMigracion, Badge, MensajeError, Tarjeta, useApi } from './comunes'

/** Checklist de lo que el ERP necesita antes del envío real. */
export default function PreparacionErp({ getHash, onSesionExpirada }) {
  const { data, error, recargar } = useApi(getHash, '/preparacion/', onSesionExpirada)
  const [guardando, setGuardando] = useState('')
  const [errorAccion, setErrorAccion] = useState('')

  async function alternar(item) {
    setGuardando(item.clave)
    setErrorAccion('')
    try {
      await apiMigracion(getHash, `/preparacion/${item.clave}/`, {
        method: 'POST',
        body: JSON.stringify({ hecho: !item.hecho, nota: item.nota }),
      })
      await recargar()
    } catch (err) {
      setErrorAccion(err.message)
    } finally {
      setGuardando('')
    }
  }

  const items = data || []
  const pendientesBloqueantes = items.filter((i) => i.bloqueante && !i.hecho).length

  return (
    <Tarjeta
      titulo="Preparación del ERP antes del envío real"
      accion={
        items.length > 0 &&
        (pendientesBloqueantes ? (
          <Badge tono="REVISION">{pendientesBloqueantes} bloqueantes pendientes</Badge>
        ) : (
          <Badge tono="LISTA">Listo para envío real</Badge>
        ))
      }
    >
      <p className="mb-3 text-sm text-muted-foreground">
        Enviar a la copia del ERP es seguro y reversible. El envío al ERP real solo debe hacerse cuando todos los puntos
        bloqueantes estén resueltos.
      </p>
      <MensajeError mensaje={error || errorAccion} />
      <ul className="divide-y divide-border">
        {items.map((item) => (
          <li key={item.clave} className="flex items-start gap-3 py-2.5">
            <button
              onClick={() => alternar(item)}
              disabled={guardando === item.clave}
              className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground"
              aria-label={item.hecho ? 'Marcar como pendiente' : 'Marcar como resuelto'}
            >
              {guardando === item.clave ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : item.hecho ? (
                <CheckCircle2 className="h-5 w-5 text-emerald-600" />
              ) : (
                <Circle className="h-5 w-5" />
              )}
            </button>
            <div className="min-w-0">
              <p className={`text-sm font-medium ${item.hecho ? 'text-muted-foreground line-through' : 'text-foreground'}`}>
                {item.clave}. {item.titulo}{' '}
                {item.bloqueante && !item.hecho && <Badge tono="ALTA">Bloqueante</Badge>}
              </p>
              <p className="text-xs text-muted-foreground">{item.detalle}</p>
              {item.nota && <p className="mt-1 text-xs font-medium text-sky-700 dark:text-sky-300">{item.nota}</p>}
            </div>
          </li>
        ))}
      </ul>
    </Tarjeta>
  )
}
