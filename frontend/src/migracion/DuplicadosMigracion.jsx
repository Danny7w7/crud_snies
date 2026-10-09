import { Download } from 'lucide-react'
import { Button } from '../components/ui/button'
import { Badge, Cargando, MensajeError, fmt, Kpi, nombrePeriodo, Tabla, Tarjeta, TIPOS_DOCUMENTO, useApi } from './comunes'
import { etiquetaCaso } from './casosEtiquetas'

const ESTADO = {
  LISTA: ['Se vincula', 'LISTA'],
  REVISION: ['Pendiente de decisión', 'REVISION'],
  EXCLUIDA: ['Excluida', 'EXCLUIDA'],
}

function queHaceLaMigracion(f) {
  if (!f.estado_migracion) {
    return 'Nada: en SWA nunca tuvo matrícula (solo código o aspirante). Su ficha del ERP queda tal cual.'
  }
  if (f.estado_migracion === 'LISTA') {
    return `Su historial de SWA (${fmt(f.matriculas_swa)} matrículas) se agrega a la ficha que ya existe en el ERP. Conserva el ID ${f.erp_id}; no se crea otra persona.`
  }
  if (f.estado_migracion === 'REVISION') {
    return `Se vinculará a la ficha del ERP cuando se decida: ${(f.casos_pendientes || []).map(etiquetaCaso).join(', ')}.`
  }
  return 'No se migra.'
}

function descargarCsv(nombre, columnas, filas) {
  const escapar = (v) => `"${String(v ?? '').replaceAll('"', '""')}"`
  const contenido = [columnas.map((c) => escapar(c.titulo)).join(';')]
    .concat(filas.map((f) => columnas.map((c) => escapar(c.csv ? c.csv(f) : f[c.clave])).join(';')))
    .join('\n')
  const url = URL.createObjectURL(new Blob(['﻿' + contenido], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = nombre
  a.click()
  URL.revokeObjectURL(url)
}

const COLUMNAS_DOCUMENTO = [
  { clave: 'erp_id', titulo: 'ID ERP' },
  {
    clave: 'nro_documento',
    titulo: 'Documento',
    render: (f) => `${TIPOS_DOCUMENTO[f.tipo_documento_id] || ''} ${f.nro_documento}`,
    csv: (f) => `${TIPOS_DOCUMENTO[f.tipo_documento_id] || ''} ${f.nro_documento}`,
  },
  { clave: 'nombre_erp', titulo: 'Nombre en el ERP', className: 'font-medium' },
  {
    clave: 'registros_swa',
    titulo: 'Nombre en SWA',
    render: (f) => [...new Set(f.registros_swa.map((r) => r.nombre))].join(' / '),
    csv: (f) => [...new Set(f.registros_swa.map((r) => r.nombre))].join(' / '),
  },
  { clave: 'vinculos_erp', titulo: 'Qué es hoy en el ERP', className: 'text-xs' },
  { clave: 'codigos_swa', titulo: 'Códigos en SWA', className: 'text-xs' },
  {
    clave: 'ultima_matricula_swa',
    titulo: 'Última matrícula SWA',
    render: (f) => (f.ultima_matricula_swa ? `${nombrePeriodo(f.ultima_matricula_swa)} (${fmt(f.matriculas_swa)} en total)` : 'Nunca'),
    csv: (f) => (f.ultima_matricula_swa ? nombrePeriodo(f.ultima_matricula_swa) : 'Nunca'),
  },
  {
    clave: 'estado_migracion',
    titulo: 'Estado',
    render: (f) => {
      const [texto, tono] = ESTADO[f.estado_migracion] || ['Fuera del alcance', 'BAJA']
      return <Badge tono={tono}>{texto}</Badge>
    },
    csv: (f) => (ESTADO[f.estado_migracion] || ['Fuera del alcance'])[0],
  },
  { clave: 'que_hace', titulo: 'Qué hace la migración', className: 'text-xs text-muted-foreground', render: queHaceLaMigracion, csv: queHaceLaMigracion },
]

const COLUMNAS_NOMBRE = [
  { clave: 'nombre', titulo: 'Nombre', className: 'font-medium' },
  { clave: 'erp_id', titulo: 'ID ERP' },
  { clave: 'documento_erp', titulo: 'Documento en el ERP' },
  { clave: 'documento_swa', titulo: 'Documento en SWA' },
  {
    clave: 'documento_swa_tambien_en_erp',
    titulo: 'Observación',
    render: (f) => (f.documento_swa_tambien_en_erp ? 'El documento de SWA también existe en el ERP: está registrada dos veces en el ERP' : 'Posible error de digitación del documento'),
    csv: (f) => (f.documento_swa_tambien_en_erp ? 'Registrada dos veces en el ERP' : 'Posible error de digitación'),
  },
]

const COLUMNAS_INTERNOS = [
  { clave: 'nombre', titulo: 'Nombre', className: 'font-medium' },
  { clave: 'erp_id_1', titulo: 'Ficha 1 (ID ERP)' },
  { clave: 'documento_1', titulo: 'Documento 1', render: (f) => `${TIPOS_DOCUMENTO[f.tipo_1] || ''} ${f.documento_1}`, csv: (f) => `${TIPOS_DOCUMENTO[f.tipo_1] || ''} ${f.documento_1}` },
  { clave: 'erp_id_2', titulo: 'Ficha 2 (ID ERP)' },
  { clave: 'documento_2', titulo: 'Documento 2', render: (f) => `${TIPOS_DOCUMENTO[f.tipo_2] || ''} ${f.documento_2}`, csv: (f) => `${TIPOS_DOCUMENTO[f.tipo_2] || ''} ${f.documento_2}` },
  { clave: 'motivo', titulo: 'Motivo' },
]

/** Reporte: personas que ya existen en el ERP y también están en SWA, y duplicados del propio ERP. */
export default function DuplicadosMigracion({ getHash, onSesionExpirada }) {
  const { data, error, cargando } = useApi(getHash, '/duplicados-erp/', onSesionExpirada)
  if (cargando && !data) return <Cargando />
  if (error) return <MensajeError mensaje={error} />
  const r = data.resumen

  const exportar = (nombre, columnas, filas) => (
    <Button size="sm" variant="outline" disabled={!filas.length} onClick={() => descargarCsv(nombre, columnas, filas)}>
      <Download className="h-4 w-4" /> Exportar CSV
    </Button>
  )

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        Cruce de las {fmt(r.personas_erp)} personas que hoy tiene el ERP contra todas las personas de SWA. La coincidencia
        se hace por número de documento normalizado; aparte se buscan personas con el mismo nombre y documento distinto.
      </p>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Kpi etiqueta="En SWA y en el ERP" valor={fmt(r.por_documento)} ayuda="Mismo documento en ambos sistemas" />
        <Kpi etiqueta="Se vinculan" valor={fmt(r.se_vinculan)} ayuda="Su historial de SWA va a su ficha del ERP" tono="ok" />
        <Kpi etiqueta="Pendientes" valor={fmt(r.en_revision)} ayuda="Se vinculan cuando se decida su caso" tono="alerta" />
        <Kpi etiqueta="Fuera del alcance" valor={fmt(r.fuera_de_alcance)} ayuda="En SWA nunca se matricularon" />
        <Kpi etiqueta="Duplicados en el ERP" valor={fmt(r.duplicados_internos_erp + r.por_nombre_otro_documento)} ayuda="Mismo nombre con otro documento" tono="peligro" />
      </div>

      <Tarjeta
        titulo={`Personas que están en SWA y ya existen en el ERP (${fmt(r.por_documento)})`}
        accion={exportar('personas_swa_y_erp.csv', COLUMNAS_DOCUMENTO, data.por_documento)}
      >
        <p className="mb-3 text-xs text-muted-foreground">
          No se crean de nuevo: la migración usa la ficha que ya tiene el ERP y le agrega el historial de SWA. Su ID sigue siendo
          el del ERP (sus códigos de SWA quedan en cada matrícula).
        </p>
        <Tabla columnas={COLUMNAS_DOCUMENTO} filas={data.por_documento} vacio="Ninguna persona del ERP está en SWA." />
      </Tarjeta>

      <Tarjeta
        titulo={`Mismo nombre, documento distinto entre SWA y el ERP (${fmt(r.por_nombre_otro_documento)})`}
        accion={exportar('mismo_nombre_otro_documento.csv', COLUMNAS_NOMBRE, data.por_nombre)}
      >
        <p className="mb-3 text-xs text-muted-foreground">
          Posibles errores de digitación del documento en alguno de los dos sistemas. La migración no los une automáticamente.
        </p>
        <Tabla columnas={COLUMNAS_NOMBRE} filas={data.por_nombre} vacio="Sin coincidencias por nombre." />
      </Tarjeta>

      <Tarjeta
        titulo={`Duplicados dentro del propio ERP (${fmt(r.duplicados_internos_erp)})`}
        accion={exportar('duplicados_internos_erp.csv', COLUMNAS_INTERNOS, data.internos_erp)}
      >
        <p className="mb-3 text-xs text-muted-foreground">
          Personas registradas dos veces en el ERP actual (no vienen de SWA). Deben corregirse en el ERP: la migración no las toca.
        </p>
        <Tabla columnas={COLUMNAS_INTERNOS} filas={data.internos_erp} vacio="Sin duplicados internos." />
      </Tarjeta>
    </div>
  )
}
