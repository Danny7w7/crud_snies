import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  Moon,
  Sun,
  ShieldCheck,
  IdCard,
  Hash,
  User,
  GraduationCap,
  Save,
  X,
  FileText,
  ClipboardList,
  Search,
  Phone,
  Mail,
  Lock,
  BookOpen,
  MapPin,
  Globe,
  Calendar,
  Locate,
} from 'lucide-react'
import countryList from 'react-select-country-list'
import { Button } from './components/ui/button'
import { Input } from './components/ui/input'
import { Label } from './components/ui/label'
import { Checkbox } from './components/ui/checkbox'
import { SearchableSelect } from './components/SearchableSelect'
import { ThemeProvider, useTheme } from './contexts/ThemeContext'
import { useDarkMode } from './hooks/useDarkMode'
import AtmosphereBackground from './components/AtmosphereBackground'

const API_URL = '/api/estudiantes/'
const API_PERSONAS_URL = '/api/personas/'
const API_UBICACIONES_URL = '/api/ubicaciones/'

const CONSULTA_HASH_KEY = 'consulta_password_hash'
const CONSULTA_ACTIVITY_KEY = 'consulta_password_last_activity'
const CONSULTA_INACTIVITY_MS = 15 * 60 * 1000

const TIPOS_IDENTIFICACION = [
  { value: 'CC', label: 'Cédula de Ciudadanía' },
  { value: 'CE', label: 'Cédula de Extranjería' },
  { value: 'TI', label: 'Tarjeta de Identidad' },
  { value: 'PA', label: 'Pasaporte' },
  { value: 'RC', label: 'Registro Civil' },
]

const INITIAL_FORM = {
  tipo_identificacion: 'CC',
  numero_identificacion: '',
  fecha_expedicion_documento: '',
  primer_nombre: '',
  segundo_nombre: '',
  primer_apellido: '',
  segundo_apellido: '',
  codigo_estudiante: '',
  programa: '',
  municipio: '',
  pais_nacimiento: '',
  municipio_nacimiento: '',
  periodo_anio: '',
  periodo_semestre: '',
}

const CAMPOS_OBLIGATORIOS = [
  ['numero_identificacion', 'Número de identificación'],
  ['fecha_expedicion_documento', 'Fecha de expedición del documento'],
  ['primer_nombre', 'Primer nombre'],
  ['primer_apellido', 'Primer apellido'],
  ['segundo_apellido', 'Segundo apellido'],
  ['codigo_estudiante', 'Código de estudiante'],
  ['programa', 'Programa'],
  ['municipio', 'Municipio'],
  ['pais_nacimiento', 'País de nacimiento'],
  ['municipio_nacimiento', 'Municipio de nacimiento'],
  ['periodo_anio', 'Periodo'],
  ['periodo_semestre', 'Semestre'],
]

function etiquetaCampo(clave) {
  const found = CAMPOS_OBLIGATORIOS.find(([key]) => key === clave)
  return found ? found[1] : clave
}

const ANIO_ACTUAL = new Date().getFullYear()
const aniosOptions = Array.from(
  { length: ANIO_ACTUAL + 1 - 2000 + 1 },
  (_, i) => 2000 + i,
)

const TIPO_DOCUMENTO_LABELS = {
  0: 'Por definir',
  1: 'CC',
  2: 'Pasaporte',
  3: 'CE',
  4: 'TI',
  5: 'Cabildo',
  6: 'NIP',
  7: 'NUIP',
  8: 'RC',
  9: 'Sec. Educación',
}

const TIPO_DOCUMENTO_FILTER_OPTIONS = Object.entries(TIPO_DOCUMENTO_LABELS).map(
  ([value, label]) => ({ value, label }),
)

const TIPO_DOCUMENTO_TO_CODE = {
  1: 'CC',
  2: 'PA',
  3: 'CE',
  4: 'TI',
  8: 'RC',
}

function splitNameParts(value) {
  const [first, ...rest] = (value || '').trim().split(/\s+/)
  return { first: first || '', rest: rest.join(' ') }
}

function parsePeriodoIngreso(value) {
  if (value === null || value === undefined || value === '') return { anio: '', semestre: '' }
  const str = String(value).trim()
  if (!/^\d{5}$/.test(str)) return { anio: '', semestre: '' }
  const anio = str.slice(0, 4)
  const semestre = str.slice(4)
  if (semestre !== '1' && semestre !== '2') return { anio: '', semestre: '' }
  return { anio, semestre }
}

function formatPeriodoIngreso(value) {
  const { anio, semestre } = parsePeriodoIngreso(value)
  return anio ? `${anio}-${semestre}` : null
}

function personaToForm(persona) {
  const nombres = splitNameParts(persona.nombre)
  const apellidos = splitNameParts(persona.apellido)
  const periodo = parsePeriodoIngreso(persona.periodo_ingreso)
  return {
    tipo_identificacion: TIPO_DOCUMENTO_TO_CODE[persona.tipo_identificacion] || 'CC',
    numero_identificacion: persona.identificacion || '',
    fecha_expedicion_documento: '',
    primer_nombre: nombres.first,
    segundo_nombre: nombres.rest,
    primer_apellido: apellidos.first,
    segundo_apellido: apellidos.rest,
    codigo_estudiante: persona.codigo_estudiante || '',
    programa: persona.programa != null ? String(persona.programa) : '',
    municipio: '',
    pais_nacimiento: '',
    municipio_nacimiento: '',
    periodo_anio: periodo.anio,
    periodo_semestre: periodo.semestre,
  }
}

async function sha256Hex(text) {
  const data = new TextEncoder().encode(text)
  const digest = await crypto.subtle.digest('SHA-256', data)
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

function getConsultaHash() {
  const hash = sessionStorage.getItem(CONSULTA_HASH_KEY)
  if (!hash) return ''
  const lastActivity = Number(sessionStorage.getItem(CONSULTA_ACTIVITY_KEY) || 0)
  if (Date.now() - lastActivity > CONSULTA_INACTIVITY_MS) {
    sessionStorage.removeItem(CONSULTA_HASH_KEY)
    sessionStorage.removeItem(CONSULTA_ACTIVITY_KEY)
    return ''
  }
  return hash
}

function touchActivity() {
  sessionStorage.setItem(CONSULTA_ACTIVITY_KEY, String(Date.now()))
}

function ThemeToggle() {
  const isDarkMode = useDarkMode()
  const { setTheme } = useTheme()

  return (
    <button
      type="button"
      onClick={() => setTheme(isDarkMode ? 'light' : 'dark')}
      className="absolute right-4 top-4 z-20 inline-flex h-11 w-11 items-center justify-center rounded-xl border border-white/40 bg-card/80 text-foreground shadow-lg backdrop-blur-xl transition hover:-translate-y-0.5 hover:bg-card focus:outline-none focus:ring-2 focus:ring-accent sm:right-6 sm:top-6"
      aria-label="Cambiar tema"
    >
      {isDarkMode ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
    </button>
  )
}

function PageShell({ active, children, wide = false }) {
  const navClass = (name) =>
    `inline-flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
      active === name
        ? 'bg-primary text-primary-foreground shadow-sm'
        : 'text-muted-foreground hover:text-foreground'
    }`

  const nav = (
    <nav className="mb-8 inline-flex w-full rounded-2xl border border-white/40 bg-secondary/60 p-1.5 dark:border-white/10">
      <Link to="/ubicar" className={navClass('ubicar')}>
        <Locate className="h-4 w-4" />
        Ubicar
      </Link>
      <Link to="/consulta" className={navClass('consulta')}>
        <Search className="h-4 w-4" />
        Consultar
      </Link>
    </nav>
  )

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#eef1f5] text-foreground dark:bg-[#080d17]">
      <AtmosphereBackground />

      <ThemeToggle />

      <main className="relative z-10 flex min-h-screen items-center justify-center p-4 py-16 sm:p-8 sm:py-20">
        {wide ? (
          <div className="w-full max-w-7xl overflow-hidden rounded-[2rem] border border-white/55 bg-card/95 shadow-[0_32px_90px_-28px_rgba(15,31,58,0.55)] dark:border-white/10 dark:bg-card/90">
            <section className="flex items-start bg-card px-6 py-9 sm:px-10 lg:px-12 xl:px-16">
              <div className="mx-auto w-full">
                {nav}
                {children}
              </div>
            </section>
          </div>
        ) : (
        <div className="grid w-full max-w-6xl overflow-hidden rounded-[2rem] border border-white/55 bg-card/95 shadow-[0_32px_90px_-28px_rgba(15,31,58,0.55)] dark:border-white/10 dark:bg-card/90 lg:min-h-[700px] lg:grid-cols-[0.72fr_1.28fr]">
          <section className="relative hidden overflow-hidden bg-brand-ink p-10 text-white lg:flex lg:flex-col lg:justify-between xl:p-14">
            <div className="pointer-events-none absolute inset-0" aria-hidden="true">
              <div className="absolute -right-24 -top-20 h-72 w-72 rounded-full border border-white/10" />
              <div className="absolute -right-6 top-12 h-44 w-44 rounded-full border border-white/10" />
              <div className="absolute -bottom-32 -left-24 h-80 w-80 rounded-full bg-accent/20 blur-3xl" />
              <div className="absolute inset-0 bg-[linear-gradient(145deg,transparent_30%,rgba(255,255,255,0.035)_100%)]" />
            </div>

            <div className="relative">
              <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.16em] text-white/75">
                <ShieldCheck className="h-4 w-4 text-accent" />
                CRUD SNIES
              </div>
              <p className="text-sm font-semibold uppercase tracking-[0.22em] text-white/55">
                Institución Universitaria
              </p>
              <h1 className="mt-4 text-4xl font-semibold leading-tight xl:text-5xl">
                Gestión de Estudiantes
              </h1>
              <div className="mt-8 h-1 w-20 rounded-full bg-accent" />
            </div>

            <div className="relative space-y-5">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/5">
                  <FileText className="h-5 w-5 text-accent" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-white/90">Actualización</p>
                  <p className="text-xs text-white/45">Corrección de datos de identificación</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/5">
                  <ClipboardList className="h-5 w-5 text-accent" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-white/90">Consulta</p>
                  <p className="text-xs text-white/45">Búsqueda y seguimiento de registros</p>
                </div>
              </div>
            </div>

            <p className="relative text-xs text-white/45">
              SNIES · Sistema Nacional de Información de la Educación Superior
            </p>
          </section>

          <section className="flex items-start bg-card px-6 py-9 sm:px-10 lg:px-12 xl:px-16 lg:max-h-[700px] lg:overflow-y-auto">
            <div className="mx-auto w-full max-w-xl">
              {nav}

              {children}
            </div>
          </section>
        </div>
        )}
      </main>
    </div>
  )
}

function ConsultaPasswordGate({ onSuccess }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const hash = await sha256Hex(password)
      const res = await fetch(API_PERSONAS_URL, {
        headers: { 'X-Consulta-Password': hash },
      })
      if (res.status === 403) {
        setError('Contraseña de consulta incorrecta')
        return
      }
      if (!res.ok) throw new Error('Error al validar la contraseña')
      sessionStorage.setItem(CONSULTA_HASH_KEY, hash)
      touchActivity()
      onSuccess()
    } catch (err) {
      setError(err.message || 'No se pudo validar la contraseña')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="mx-auto w-full max-w-md">
      <div className="mb-8 text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-white/40 bg-secondary/70 dark:border-white/10">
          <Lock className="h-6 w-6 text-accent" />
        </div>
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-accent">
          Acceso restringido
        </p>
        <h2 className="mt-3 text-3xl font-semibold tracking-tight text-foreground">
          Consulta de personas
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Esta sección está protegida. Ingresa la contraseña de consulta para continuar.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="consulta_password" className="text-sm font-medium text-foreground">
            Contraseña
          </Label>
          <div className="group relative">
            <Lock className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground transition-colors z-20 group-focus-within:text-accent" />
            <Input
              id="consulta_password"
              name="consulta_password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoFocus
              className="pl-12 h-12"
            />
          </div>
        </div>

        {error && (
          <p className="rounded-lg border border-destructive/40 bg-destructive/20 px-3 py-2 text-sm text-destructive-foreground">
            {error}
          </p>
        )}

        <Button type="submit" size="lg" className="h-12 w-full" disabled={loading}>
          <Lock className="h-4 w-4" />
          {loading ? 'Validando...' : 'Ingresar'}
        </Button>
      </form>
    </div>
  )
}

function ConsultarPersonas() {
  const [query, setQuery] = useState('')
  const [reintegro, setReintegro] = useState('')
  const [tipoIdentificacion, setTipoIdentificacion] = useState('')
  const [programa, setPrograma] = useState('')
  const [programas, setProgramas] = useState([])
  const [results, setResults] = useState([])
  const [count, setCount] = useState(0)
  const [page, setPage] = useState(1)
  const [hasNext, setHasNext] = useState(false)
  const [hasPrev, setHasPrev] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const navigate = useNavigate()
  const debounceRef = useRef(null)
  const controllerRef = useRef(null)

  const headers = useMemo(
    () => ({ 'X-Consulta-Password': getConsultaHash() }),
    [],
  )

  const programaOptions = useMemo(
    () => programas.map((p) => ({ value: String(p.codigo), label: p.nombre })),
    [programas],
  )

  const runSearch = useCallback(
    async (q, pageNumber = 1, filters = {}) => {
      const { reintegro: reintegroValue, tipoIdentificacion: tipoValue, programa: programaValue } = filters
      controllerRef.current?.abort()
      const controller = new AbortController()
      controllerRef.current = controller
      setLoading(true)
      setError('')
      try {
        const params = new URLSearchParams({ page: String(pageNumber) })
        if (q) params.set('q', q)
        if (reintegroValue) params.set('reintegro', reintegroValue)
        if (tipoValue) params.set('tipo_identificacion', tipoValue)
        if (programaValue) params.set('programa', programaValue)
        const res = await fetch(`${API_PERSONAS_URL}?${params}`, {
          headers,
          signal: controller.signal,
        })
        if (res.status === 403) {
          throw new Error('Contraseña de consulta inválida. Vuelve a ingresarla.')
        }
        if (!res.ok) throw new Error('Error al consultar los datos')
        const data = await res.json()
        setResults(data.results)
        setCount(data.count)
        setPage(pageNumber)
        setHasNext(Boolean(data.next))
        setHasPrev(Boolean(data.previous))
      } catch (err) {
        if (err.name === 'AbortError') return
        setError(err.message)
        setResults([])
        setCount(0)
      } finally {
        setLoading(false)
      }
    },
    [headers],
  )

  useEffect(() => {
    let cancelled = false
    async function loadProgramas() {
      try {
        const res = await fetch('/api/programas/')
        if (!res.ok) throw new Error('Error al cargar programas')
        const data = await res.json()
        if (!cancelled) setProgramas(data)
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
    }
    loadProgramas()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const onActivity = () => touchActivity()
    document.addEventListener('pointerdown', onActivity)
    document.addEventListener('keydown', onActivity)
    return () => {
      document.removeEventListener('pointerdown', onActivity)
      document.removeEventListener('keydown', onActivity)
    }
  }, [])

  useEffect(() => {
    debounceRef.current = setTimeout(() => {
      runSearch(query.trim(), 1, {
        reintegro,
        tipoIdentificacion,
        programa,
      })
    }, 400)
    return () => clearTimeout(debounceRef.current)
  }, [query, reintegro, tipoIdentificacion, programa, runSearch])

  function handleSubmit(e) {
    e.preventDefault()
    clearTimeout(debounceRef.current)
    runSearch(query.trim(), 1, { reintegro, tipoIdentificacion, programa })
  }

  function goToPage(pageNumber) {
    clearTimeout(debounceRef.current)
    runSearch(query.trim(), pageNumber, { reintegro, tipoIdentificacion, programa })
  }

  return (
    <div className="mx-auto w-full">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-accent">
          CRUD SNIES
        </p>
        <h2 className="mt-3 text-3xl font-semibold tracking-tight text-foreground">
          Consultar personas
        </h2>
      </div>

      <form onSubmit={handleSubmit} className="mb-6 space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="busqueda" className="text-xs font-medium text-foreground">
            Buscar por nombre, documento o código de estudiante
          </Label>
          <div className="group relative">
            <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground transition-colors z-20 group-focus-within:text-accent" />
            <Input
              id="busqueda"
              name="busqueda"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ej: María, 1044431897, 2026001234"
              className="pl-10 h-10"
            />
          </div>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="filtro_reintegro" className="text-xs font-medium text-foreground">
              Reintegro
            </Label>
            <SearchableSelect
              id="filtro_reintegro"
              value={reintegro}
              onValueChange={setReintegro}
              options={[
                { value: '', label: 'Todos' },
                { value: '1', label: 'Sí' },
                { value: '0', label: 'No' },
              ]}
              placeholder="Todos"
              className="h-9"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="filtro_tipo" className="text-xs font-medium text-foreground">
              Tipo de documento
            </Label>
            <SearchableSelect
              id="filtro_tipo"
              value={tipoIdentificacion}
              onValueChange={setTipoIdentificacion}
              options={TIPO_DOCUMENTO_FILTER_OPTIONS}
              placeholder="Todos"
              searchPlaceholder="Buscar tipo..."
              className="h-9"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="filtro_programa" className="text-xs font-medium text-foreground">
              Programa
            </Label>
            <SearchableSelect
              id="filtro_programa"
              value={programa}
              onValueChange={setPrograma}
              options={programaOptions}
              placeholder="Todos"
              searchPlaceholder="Buscar programa..."
              className="h-9"
            />
          </div>
        </div>

        <div className="flex items-center gap-2 pt-1">
          <Button type="submit" size="sm" className="h-9 flex-1">
            <Search className="h-4 w-4" />
            Buscar
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-9"
            onClick={() => {
              setQuery('')
              setReintegro('')
              setTipoIdentificacion('')
              setPrograma('')
            }}
          >
            <X className="h-4 w-4" />
            Limpiar
          </Button>
        </div>
      </form>

      {error && (
        <p className="mb-6 rounded-lg border border-destructive/40 bg-destructive/20 px-3 py-2 text-sm text-destructive-foreground">
          {error}
        </p>
      )}

      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {loading
            ? 'Consultando...'
            : `${count} registro${count === 1 ? '' : 's'} encontrado${count === 1 ? '' : 's'}`}
        </p>
        {!loading && count > 0 && (
          <p className="text-xs text-muted-foreground">
            Página {page}
          </p>
        )}
      </div>

      {loading ? (
        <div className="space-y-3">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="erp-skeleton h-14 rounded-xl bg-card/60" />
          ))}
        </div>
      ) : results.length === 0 ? (
        <p className="rounded-xl border border-white/40 bg-card/80 px-4 py-10 text-center text-sm text-muted-foreground dark:border-white/10">
          No se encontraron resultados para la búsqueda
        </p>
      ) : (
        <div className="erp-table-frame">
          <table className="w-full text-sm">
            <thead className="bg-primary text-primary-foreground">
              <tr>
                <th className="px-4 py-3 text-left font-medium">Código</th>
                <th className="px-4 py-3 text-left font-medium">Periodo</th>
                <th className="px-4 py-3 text-left font-medium">Documento</th>
                <th className="px-4 py-3 text-left font-medium">Nombre</th>
                <th className="px-4 py-3 text-left font-medium">Apellido</th>
                <th className="px-4 py-3 text-left font-medium">Reintegro</th>
                <th className="hidden px-4 py-3 text-left font-medium md:table-cell">
                  Teléfono
                </th>
                <th className="hidden px-4 py-3 text-left font-medium lg:table-cell">
                  Email
                </th>
                <th className="px-4 py-3 text-right font-medium">Acción</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {results.map((p) => (
                <tr
                  key={p.id}
                  className="transition-colors hover:bg-secondary/60"
                >
                  <td className="px-4 py-3 font-medium text-foreground">
                    {p.codigo_estudiante || (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {formatPeriodoIngreso(p.periodo_ingreso) || (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-foreground">
                    <span className="mr-1.5 inline-block rounded-md bg-secondary px-1.5 py-0.5 text-xs text-muted-foreground">
                      {TIPO_DOCUMENTO_LABELS[p.tipo_identificacion] ?? ''}
                    </span>
                    {p.identificacion}
                  </td>
                  <td className="px-4 py-3 text-foreground">{p.nombre}</td>
                  <td className="px-4 py-3 text-foreground">{p.apellido}</td>
                  <td className="px-4 py-3">
                    {p.es_reintegro ? (
                      <span className="inline-flex items-center gap-1.5 rounded-md bg-accent/20 px-2 py-0.5 text-xs font-medium text-accent">
                        Sí
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 rounded-md bg-secondary px-2 py-0.5 text-xs text-muted-foreground">
                        No
                      </span>
                    )}
                  </td>
                  <td className="hidden px-4 py-3 text-muted-foreground md:table-cell">
                    <span className="inline-flex items-center gap-1.5">
                      <Phone className="h-3.5 w-3.5" />
                      {p.telefono || '—'}
                    </span>
                  </td>
                  <td className="hidden max-w-[220px] truncate px-4 py-3 text-muted-foreground lg:table-cell">
                    <span className="inline-flex items-center gap-1.5">
                      <Mail className="h-3.5 w-3.5" />
                      {p.email || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          navigate('/ubicar', { state: { persona: p } })
                        }
                      >
                        <Locate className="h-3.5 w-3.5" />
                        Ubicar
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && count > 0 && (
        <div className="mt-4 flex items-center justify-between gap-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!hasPrev}
            onClick={() => goToPage(page - 1)}
          >
            Anterior
          </Button>
          <p className="text-xs text-muted-foreground">
            Mostrando {results.length} de {count} · Página {page}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!hasNext}
            onClick={() => goToPage(page + 1)}
          >
            Siguiente
          </Button>
        </div>
      )}
    </div>
  )
}

function ConsultaPage() {
  const [authed, setAuthed] = useState(() => Boolean(getConsultaHash()))

  useEffect(() => {
    if (!authed) return
    const interval = setInterval(() => {
      if (!getConsultaHash()) setAuthed(false)
    }, 30 * 1000)
    return () => clearInterval(interval)
  }, [authed])

  return (
    <PageShell active="consulta" wide>
      {authed ? (
        <ConsultarPersonas />
      ) : (
        <ConsultaPasswordGate onSuccess={() => setAuthed(true)} />
      )}
    </PageShell>
  )
}

function UbicarPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const persona = location.state?.persona
  const [form, setForm] = useState(() =>
    persona ? personaToForm(persona) : INITIAL_FORM,
  )
  const [editingId, setEditingId] = useState(null)
  const [aceptaTratamiento, setAceptaTratamiento] = useState(false)
  const [error, setError] = useState('')
  const [resultadoModal, setResultadoModal] = useState(null)
  const [yaUbicado, setYaUbicado] = useState(null)
  const [fechaUbicacion, setFechaUbicacion] = useState('')
  const [municipios, setMunicipios] = useState([])
  const [loadingMunicipios, setLoadingMunicipios] = useState(true)
  const [programas, setProgramas] = useState([])
  const [loadingProgramas, setLoadingProgramas] = useState(true)

  const municipioOptions = useMemo(
    () => municipios.map((m) => ({ value: m.id, label: `${m.nombre} - ${m.codigo}` })),
    [municipios],
  )
  const programaOptions = useMemo(
    () => programas.map((p) => ({ value: String(p.codigo), label: p.nombre })),
    [programas],
  )
  const paisOptions = useMemo(
    () =>
      countryList()
        .getData()
        .map((p) => ({ value: p.value.toUpperCase(), label: p.label })),
    [],
  )

  useEffect(() => {
    async function loadMunicipios() {
      try {
        const res = await fetch('/api/municipios/')
        if (!res.ok) throw new Error('Error al cargar municipios')
        setMunicipios(await res.json())
      } catch (err) {
        setError(err.message)
      } finally {
        setLoadingMunicipios(false)
      }
    }
    loadMunicipios()

    async function loadProgramas() {
      try {
        const res = await fetch('/api/programas/')
        if (!res.ok) throw new Error('Error al cargar programas')
        setProgramas(await res.json())
      } catch (err) {
        setError(err.message)
      } finally {
        setLoadingProgramas(false)
      }
    }
    loadProgramas()
  }, [])

  useEffect(() => {
    if (!persona?.codigo_estudiante) return
    let cancelled = false
    async function buscarRegistroLocal() {
      try {
        const res = await fetch(API_URL)
        if (!res.ok) throw new Error('Error al consultar los registros locales')
        const lista = await res.json()
        if (cancelled) return
        const local = lista.find(
          (e) => e.codigo_estudiante === persona.codigo_estudiante,
        )
        if (local) {
          setEditingId(local.id)
          setForm({
            ...personaToForm(persona),
            fecha_expedicion_documento: local.fecha_expedicion_documento ?? '',
            programa: local.programa || '',
            municipio: local.municipio ?? '',
            municipio_nacimiento: local.municipio_nacimiento ?? '',
            periodo_anio: local.periodo_anio ?? '',
            periodo_semestre: local.periodo_semestre ?? '',
          })
        }
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
    }
    buscarRegistroLocal()
    return () => {
      cancelled = true
    }
  }, [persona])

  useEffect(() => {
    if (!persona?.codigo_estudiante) return
    let cancelled = false
    async function verificarUbicacion() {
      try {
        const res = await fetch(
          `${API_UBICACIONES_URL}?codigo=${encodeURIComponent(persona.codigo_estudiante)}`,
        )
        if (!res.ok) throw new Error('Error al verificar la ubicación')
        const rows = await res.json()
        if (cancelled) return
        if (rows.length > 0) {
          setYaUbicado(true)
          setFechaUbicacion(rows[0].fecha_ubicacion || '')
        } else {
          setYaUbicado(false)
        }
      } catch (err) {
        if (!cancelled) {
          setYaUbicado(false)
          setError(err.message)
        }
      }
    }
    verificarUbicacion()
    return () => {
      cancelled = true
    }
  }, [persona])

  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value })
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setResultadoModal(null)

    if (!aceptaTratamiento) {
      setResultadoModal({
        type: 'error',
        title: 'Autorización requerida',
        message: 'Debes aceptar la autorización para el tratamiento de datos personales.',
        fields: ['Autorización de datos personales'],
      })
      return
    }

    const faltantes = CAMPOS_OBLIGATORIOS.filter(
      ([key]) => !String(form[key] ?? '').trim(),
    )
    if (faltantes.length > 0) {
      setResultadoModal({
        type: 'error',
        title: 'Campos incompletos',
        message: 'Completa los siguientes campos obligatorios:',
        fields: faltantes.map(([, label]) => label),
      })
      return
    }

    const method = editingId ? 'PUT' : 'POST'
    const url = editingId ? `${API_URL}${editingId}/` : API_URL
    try {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      if (!res.ok) {
        const data = await res.json()
        if (data.programa) {
          setResultadoModal({
            type: 'error',
            title: 'No se encontró el programa',
            message: 'El programa seleccionado no está disponible. Comunícate con el administrador.',
            fields: ['Programa'],
          })
          return
        }
        const entradas = Object.entries(data)
        const fields = entradas.map(([clave]) => etiquetaCampo(clave))
        const msg = entradas
          .map(([, valores]) => (Array.isArray(valores) ? valores.join(', ') : String(valores)))
          .join(', ')
        setResultadoModal({
          type: 'error',
          title: 'No se pudo guardar',
          message: msg || 'Error al guardar la información del estudiante.',
          fields,
        })
        return
      }
      setAceptaTratamiento(false)
      setResultadoModal({
        type: 'success',
        title: 'Información guardada',
        message: 'La información del estudiante se guardó correctamente.',
        fields: [],
      })
    } catch (err) {
      setResultadoModal({
        type: 'error',
        title: 'Error al guardar',
        message: err.message || 'No se pudo guardar la información.',
        fields: [],
      })
    }
  }

  useEffect(() => {
    if (resultadoModal?.type !== 'success') return
    const timer = setTimeout(() => {
      navigate('/ubicar', { replace: true, state: null })
    }, 4000)
    return () => clearTimeout(timer)
  }, [resultadoModal, navigate])

  const inputIconClass =
    'pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground transition-colors z-20'
  const inputWithIconClass = 'pl-12 h-12'

  return (
    <PageShell active="ubicar">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-accent">
          CRUD SNIES
        </p>
        <h2 className="mt-3 text-3xl font-semibold tracking-tight text-foreground">
          Ubicar estudiante
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Completa la información del estudiante ubicado en la consulta. Solo se
          puede guardar si el estudiante existe en la base institucional.
        </p>
      </div>

      {!persona ? (
        <div className="flex flex-col items-center gap-3 rounded-2xl border border-white/40 bg-secondary/40 px-6 py-14 text-center dark:border-white/10">
          <Locate className="h-10 w-10 text-muted-foreground" />
          <p className="font-medium text-foreground">Aún no has ubicado un estudiante</p>
          <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
            Primero busca al estudiante en la pestaña <span className="font-semibold">Consultar</span>{' '}
            y pulsa el botón <span className="font-semibold">Ubicar</span> en la fila del
            resultado. No es posible ingresar estudiantes nuevos: solo se guarda
            información de quienes existen en la base institucional.
          </p>
          <Button asChild variant="outline" size="lg" className="mt-2 h-12">
            <Link to="/consulta">
              <Search className="h-4 w-4" />
              Ir a Consultar
            </Link>
          </Button>
        </div>
      ) : yaUbicado ? (
        <div className="flex flex-col items-center gap-3 rounded-2xl border border-white/40 bg-secondary/40 px-6 py-14 text-center dark:border-white/10">
          <ShieldCheck className="h-10 w-10 text-emerald-500" />
          <p className="font-medium text-foreground">Este estudiante ya fue ubicado</p>
          {fechaUbicacion && (
            <p className="text-sm text-muted-foreground">
              Registrado el{' '}
              {new Date(fechaUbicacion).toLocaleDateString('es-CO', {
                day: 'numeric',
                month: 'long',
                year: 'numeric',
              })}
            </p>
          )}
          <Button asChild variant="outline" size="lg" className="mt-2 h-12">
            <Link to="/consulta">
              <Search className="h-4 w-4" />
              Volver a consultar
            </Link>
          </Button>
        </div>
      ) : yaUbicado === null ? (
        <div className="flex flex-col items-center gap-3 rounded-2xl border border-white/40 bg-secondary/40 px-6 py-14 text-center dark:border-white/10">
          <div className="h-10 w-10 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          <p className="text-sm text-muted-foreground">Verificando si el estudiante ya fue ubicado...</p>
        </div>
      ) : (
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-5 gap-3">
          <div className="col-span-2 space-y-2">
            <Label htmlFor="tipo_identificacion" className="text-sm font-medium text-foreground">
              Tipo de identificación <span className="text-accent">*</span>
            </Label>
            <div className="group relative">
              <IdCard className={`${inputIconClass} group-focus-within:text-accent`} />
              <SearchableSelect
                id="tipo_identificacion"
                value={form.tipo_identificacion}
                onValueChange={(value) =>
                  setForm({ ...form, tipo_identificacion: value })
                }
                options={TIPOS_IDENTIFICACION}
                placeholder="Selecciona un tipo"
                className={inputWithIconClass}
              />
            </div>
          </div>

          <div className="col-span-3 space-y-2">
            <Label htmlFor="numero_identificacion" className="text-sm font-medium text-foreground">
              Número de identificación <span className="text-accent">*</span>
            </Label>
            <div className="group relative">
              <Hash className={`${inputIconClass} group-focus-within:text-accent`} />
              <Input
                id="numero_identificacion"
                name="numero_identificacion"
                value={form.numero_identificacion}
                onChange={handleChange}
                maxLength={20}
                required
                placeholder="Ej: 1023456789"
                className={inputWithIconClass}
              />
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="fecha_expedicion_documento" className="text-sm font-medium text-foreground">
            Fecha de expedición del documento <span className="text-accent">*</span>
          </Label>
          <div className="group relative">
            <Calendar className={`${inputIconClass} group-focus-within:text-accent`} />
            <Input
              id="fecha_expedicion_documento"
              name="fecha_expedicion_documento"
              type="date"
              value={form.fecha_expedicion_documento}
              onChange={handleChange}
              required
              className={inputWithIconClass}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <Label htmlFor="primer_nombre" className="text-sm font-medium text-foreground">
              Primer nombre <span className="text-accent">*</span>
            </Label>
            <div className="group relative">
              <User className={`${inputIconClass} group-focus-within:text-accent`} />
              <Input
                id="primer_nombre"
                name="primer_nombre"
                value={form.primer_nombre}
                onChange={handleChange}
                maxLength={50}
                required
                placeholder="Ej: Ana"
                className={inputWithIconClass}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="segundo_nombre" className="text-sm font-medium text-foreground">
              Segundo nombre
            </Label>
            <div className="group relative">
              <User className={`${inputIconClass} group-focus-within:text-accent`} />
              <Input
                id="segundo_nombre"
                name="segundo_nombre"
                value={form.segundo_nombre}
                onChange={handleChange}
                maxLength={50}
                placeholder="Ej: María"
                className={inputWithIconClass}
              />
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <Label htmlFor="primer_apellido" className="text-sm font-medium text-foreground">
              Primer apellido <span className="text-accent">*</span>
            </Label>
            <div className="group relative">
              <User className={`${inputIconClass} group-focus-within:text-accent`} />
              <Input
                id="primer_apellido"
                name="primer_apellido"
                value={form.primer_apellido}
                onChange={handleChange}
                maxLength={50}
                required
                placeholder="Ej: López"
                className={inputWithIconClass}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="segundo_apellido" className="text-sm font-medium text-foreground">
              Segundo apellido <span className="text-accent">*</span>
            </Label>
            <div className="group relative">
              <User className={`${inputIconClass} group-focus-within:text-accent`} />
              <Input
                id="segundo_apellido"
                name="segundo_apellido"
                value={form.segundo_apellido}
                onChange={handleChange}
                maxLength={50}
                required
                placeholder="Ej: Pérez"
                className={inputWithIconClass}
              />
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="codigo_estudiante" className="text-sm font-medium text-foreground">
            Código de estudiante <span className="text-accent">*</span>
          </Label>
          <div className="group relative">
            <GraduationCap className={`${inputIconClass} group-focus-within:text-accent`} />
            <Input
              id="codigo_estudiante"
              name="codigo_estudiante"
              value={form.codigo_estudiante}
              onChange={handleChange}
              maxLength={20}
              required
              placeholder="Ej: 2026001234"
              className={inputWithIconClass}
            />
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="programa" className="text-sm font-medium text-foreground">
            Programa al que pertenece <span className="text-accent">*</span>
          </Label>
          <div className="group relative">
            <BookOpen className={`${inputIconClass} group-focus-within:text-accent`} />
            <SearchableSelect
              id="programa"
              value={form.programa}
              onValueChange={(value) => setForm({ ...form, programa: value })}
              options={programaOptions}
              placeholder="Selecciona un programa"
              searchPlaceholder="Buscar programa..."
              emptyLabel="No hay programas disponibles."
              isLoading={loadingProgramas}
              className={inputWithIconClass}
            />
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-2">
            <Label htmlFor="municipio" className="text-sm font-medium text-foreground">
              Municipio <span className="text-accent">*</span>
            </Label>
            <div className="group relative">
              <MapPin className={`${inputIconClass} group-focus-within:text-accent`} />
              <SearchableSelect
                id="municipio"
                value={form.municipio}
                onValueChange={(value) => setForm({ ...form, municipio: value })}
                options={municipioOptions}
                placeholder="Selecciona un municipio"
                searchPlaceholder="Buscar municipio..."
                emptyLabel="No hay municipios."
                isLoading={loadingMunicipios}
                className={inputWithIconClass}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="periodo_anio" className="text-sm font-medium text-foreground">
              Periodo - 1er semestre <span className="text-accent">*</span>
            </Label>
            <div className="group relative">
              <Calendar className={`${inputIconClass} group-focus-within:text-accent`} />
              <SearchableSelect
                id="periodo_anio"
                value={form.periodo_anio}
                onValueChange={(value) =>
                  setForm({ ...form, periodo_anio: value })
                }
                options={aniosOptions.map((anio) => ({
                  value: String(anio),
                  label: String(anio),
                }))}
                placeholder="Año..."
                searchPlaceholder="Buscar año..."
                className={inputWithIconClass}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="periodo_semestre" className="text-sm font-medium text-foreground">
              Semestre <span className="text-accent">*</span>
            </Label>
            <div className="group relative">
              <Calendar className={`${inputIconClass} group-focus-within:text-accent`} />
              <SearchableSelect
                id="periodo_semestre"
                value={form.periodo_semestre}
                onValueChange={(value) =>
                  setForm({ ...form, periodo_semestre: value })
                }
                options={[
                  { value: '1', label: '1' },
                  { value: '2', label: '2' },
                ]}
                placeholder="Semestre..."
                className={inputWithIconClass}
              />
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <Label htmlFor="pais_nacimiento" className="text-sm font-medium text-foreground">
              País de nacimiento <span className="text-accent">*</span>
            </Label>
            <div className="group relative">
              <Globe className={`${inputIconClass} group-focus-within:text-accent`} />
              <SearchableSelect
                id="pais_nacimiento"
                value={form.pais_nacimiento}
                onValueChange={(value) => setForm({ ...form, pais_nacimiento: value })}
                options={paisOptions}
                placeholder="Selecciona un país"
                searchPlaceholder="Buscar país..."
                emptyLabel="No hay países."
                className={inputWithIconClass}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="municipio_nacimiento" className="text-sm font-medium text-foreground">
              Municipio de nacimiento <span className="text-accent">*</span>
            </Label>
            <div className="group relative">
              <MapPin className={`${inputIconClass} group-focus-within:text-accent`} />
              <SearchableSelect
                id="municipio_nacimiento"
                value={form.municipio_nacimiento}
                onValueChange={(value) =>
                  setForm({ ...form, municipio_nacimiento: value })
                }
                options={municipioOptions}
                placeholder="Selecciona un municipio"
                searchPlaceholder="Buscar municipio..."
                emptyLabel="No hay municipios."
                isLoading={loadingMunicipios}
                className={inputWithIconClass}
              />
            </div>
          </div>
        </div>

        <div className="flex items-start gap-2 pt-1">
          <Checkbox
            id="aceptaTratamiento"
            checked={aceptaTratamiento}
            onCheckedChange={setAceptaTratamiento}
            className="mt-0.5"
          />
          <Label
            htmlFor="aceptaTratamiento"
            className="text-sm leading-relaxed text-muted-foreground"
          >
            Autorizo el tratamiento de datos personales conforme a la política de
            privacidad institucional
          </Label>
        </div>

        {error && (
          <p className="rounded-lg border border-destructive/40 bg-destructive/20 px-3 py-2 text-sm text-destructive-foreground">
            {error}
          </p>
        )}

        <div className="flex gap-3 pt-1">
          <Button
            type="submit"
            size="lg"
            className="h-12 flex-1"
          >
            <Save className="h-4 w-4" />
            Guardar información
          </Button>
        </div>
      </form>
      )}

      {resultadoModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setResultadoModal(null)}
          />
          <div className="relative w-full max-w-md rounded-2xl border border-white/40 bg-secondary p-6 shadow-xl dark:border-white/10">
            <button
              type="button"
              onClick={() => setResultadoModal(null)}
              className="absolute right-4 top-4 text-muted-foreground transition-colors hover:text-foreground"
              aria-label="Cerrar"
            >
              <X className="h-5 w-5" />
            </button>
            <div className="flex flex-col items-center text-center">
              {resultadoModal.type === 'success' ? (
                <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-500/20">
                  <ShieldCheck className="h-7 w-7 text-emerald-600 dark:text-emerald-400" />
                </div>
              ) : (
                <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-destructive/20">
                  <X className="h-7 w-7 text-destructive" />
                </div>
              )}
              <h3 className="text-lg font-semibold text-foreground">
                {resultadoModal.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                {resultadoModal.message}
              </p>
              {resultadoModal.fields.length > 0 && (
                <div className="mt-4 w-full rounded-xl border border-border/60 bg-input-background p-3 text-left">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Campos relacionados
                  </p>
                  <ul className="space-y-1.5">
                    {resultadoModal.fields.map((campo) => (
                      <li
                        key={campo}
                        className="flex items-center gap-2 text-sm text-foreground"
                      >
                        <X className="h-3.5 w-3.5 shrink-0 text-destructive" />
                        {campo}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <Button
                type="button"
                size="lg"
                className="mt-5 h-11 w-full"
                onClick={() => {
                  if (resultadoModal.type === 'success') {
                    navigate('/ubicar', { replace: true, state: null })
                  } else {
                    setResultadoModal(null)
                  }
                }}
              >
                {resultadoModal.type === 'success' ? 'Volver a Ubicar' : 'Entendido'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </PageShell>
  )
}

function App() {
  return (
    <ThemeProvider>
      <Routes>
        <Route path="/" element={<UbicarPage />} />
        <Route path="/ubicar" element={<UbicarPage />} />
        <Route path="/registrar" element={<Navigate to="/ubicar" replace />} />
        <Route path="/consulta" element={<ConsultaPage />} />
        <Route path="*" element={<UbicarPage />} />
      </Routes>
    </ThemeProvider>
  )
}

export default App
