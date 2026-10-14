import { Link, useNavigate } from 'react-router-dom'
import { BarChart3, FileSearch, Archive, Users, Copy, GraduationCap, TriangleAlert } from 'lucide-react'
import ResumenMigracion from './ResumenMigracion'
import CasosMigracion from './CasosMigracion'
import PersonasMigracion from './PersonasMigracion'
import ApartadosMigracion from './ApartadosMigracion'
import DuplicadosMigracion from './DuplicadosMigracion'
import EstudiantesErp from './EstudiantesErp'
import AnomaliasMigracion from './AnomaliasMigracion'

const SECCIONES = [
  ['resumen', 'Resumen antes / después', BarChart3, ''],
  ['casos', 'Análisis de casos', FileSearch, '/casos'],
  ['personas', 'Personas', Users, '/personas'],
  ['duplicados', 'Ya existen en el ERP', Copy, '/duplicados'],
  ['erp', 'Estudiantes en el ERP', GraduationCap, '/erp'],
  ['apartados', 'Información apartada', Archive, '/apartados'],
  ['anomalias', 'Anomalías SWA', TriangleAlert, '/anomalias'],
]

/** Módulo de migración SWA → ERP (solo estudiantes con matrícula activa comprobada). */
export default function MigracionApp({ seccion, getHash, onSesionExpirada }) {
  const navigate = useNavigate()
  const props = { getHash, onSesionExpirada }
  const irA = (s) => navigate(`/migracion${SECCIONES.find(([clave]) => clave === s)?.[3] ?? ''}`)
  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-accent">Migración SWA → ERP</p>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">Estudiantes que tuvieron matrícula en SWA</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Todos los que tuvieron una matrícula en algún periodo, activa hoy o no. SWA solo se lee desde una copia local;
          aspirantes, terceros y proveedores no entran.
        </p>
      </div>
      <nav className="flex flex-wrap gap-2 border-b border-border pb-3">
        {SECCIONES.map(([clave, texto, Icono, ruta]) => (
          <Link
            key={clave}
            to={`/migracion${ruta}`}
            className={`inline-flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition ${
              seccion === clave ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-secondary hover:text-foreground'
            }`}
          >
            <Icono className="h-4 w-4" /> {texto}
          </Link>
        ))}
      </nav>
      {seccion === 'resumen' && <ResumenMigracion {...props} irA={irA} />}
      {seccion === 'casos' && <CasosMigracion {...props} />}
      {seccion === 'personas' && <PersonasMigracion {...props} />}
      {seccion === 'duplicados' && <DuplicadosMigracion {...props} />}
      {seccion === 'erp' && <EstudiantesErp {...props} />}
      {seccion === 'apartados' && <ApartadosMigracion {...props} />}
      {seccion === 'anomalias' && <AnomaliasMigracion {...props} />}
    </div>
  )
}
