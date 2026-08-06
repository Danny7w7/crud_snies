import { useEffect, useMemo, useState } from 'react'
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
  UserPlus,
  FileText,
  ClipboardList,
  BookOpen,
  MapPin,
  Globe,
  Calendar,
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
  primer_nombre: '',
  segundo_nombre: '',
  primer_apellido: '',
  segundo_apellido: '',
  codigo_estudiante: '',
  programa: '',
  municipio: '',
  pais_nacimiento: '',
  municipio_nacimiento: '',
  periodo_primer_semestre: '',
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

function EstudiantesCrud() {
  const [form, setForm] = useState(INITIAL_FORM)
  const [editingId, setEditingId] = useState(null)
  const [aceptaTratamiento, setAceptaTratamiento] = useState(false)
  const [error, setError] = useState('')
  const [municipios, setMunicipios] = useState([])
  const [loadingMunicipios, setLoadingMunicipios] = useState(true)

  const municipioOptions = useMemo(
    () => municipios.map((m) => ({ value: m.id, label: `${m.nombre} - ${m.codigo}` })),
    [municipios],
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
  }, [])

  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value })
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')

    if (!aceptaTratamiento) {
      setError('Debes aceptar la autorización para el tratamiento de datos personales')
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
        const msg = Object.values(data)
          .flat()
          .join(', ')
        throw new Error(msg || 'Error al guardar el estudiante')
      }
      setForm(INITIAL_FORM)
      setEditingId(null)
      setAceptaTratamiento(false)
    } catch (err) {
      setError(err.message)
    }
  }

  function handleCancelEdit() {
    setEditingId(null)
    setForm(INITIAL_FORM)
    setAceptaTratamiento(false)
    setError('')
  }

  const inputIconClass =
    'pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground transition-colors z-20'
  const inputWithIconClass = 'pl-12 h-12'

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#eef1f5] text-foreground dark:bg-[#080d17]">
      <AtmosphereBackground />

      <ThemeToggle />

      <main className="relative z-10 flex min-h-screen items-center justify-center p-4 py-16 sm:p-8 sm:py-20">
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
              <p className="mt-4 text-sm leading-relaxed text-white/55">
                Administra la información de identificación de los estudiantes:
                registro, consulta, actualización y baja de datos.
              </p>
              <div className="mt-8 h-1 w-20 rounded-full bg-accent" />
            </div>

            <div className="relative space-y-5">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/5">
                  <UserPlus className="h-5 w-5 text-accent" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-white/90">Registro</p>
                  <p className="text-xs text-white/45">Nuevos estudiantes al sistema</p>
                </div>
              </div>
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
              <div className="mb-8">
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-accent">
                  CRUD SNIES
                </p>
                <h2 className="mt-3 text-3xl font-semibold tracking-tight text-foreground">
                  {editingId ? 'Editar estudiante' : 'Registrar estudiante'}
                </h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  {editingId
                    ? 'Actualiza los datos del estudiante y guarda los cambios'
                    : 'Completa el formulario con la información de identificación'}
                </p>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="grid grid-cols-5 gap-3">
                  <div className="col-span-2 space-y-2">
                    <Label htmlFor="tipo_identificacion" className="text-sm font-medium text-foreground">
                      Tipo de identificación <span className="text-accent">*</span>
                    </Label>
                    <div className="group relative">
                      <IdCard className={`${inputIconClass} group-focus-within:text-accent`} />
                      <select
                        id="tipo_identificacion"
                        name="tipo_identificacion"
                        value={form.tipo_identificacion}
                        onChange={handleChange}
                        required
                        className={`${inputWithIconClass} focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] flex h-12 w-full min-w-0 cursor-pointer rounded-xl border border-input bg-input-background px-3 text-base text-foreground outline-none transition-[color,box-shadow] md:text-sm dark:bg-input/30`}
                      >
                        {TIPOS_IDENTIFICACION.map((tipo) => (
                          <option key={tipo.value} value={tipo.value}>
                            {tipo.label}
                          </option>
                        ))}
                      </select>
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
                    Programa al que pertenece
                  </Label>
                  <div className="group relative">
                    <BookOpen className={`${inputIconClass} group-focus-within:text-accent`} />
                    <Input
                      id="programa"
                      name="programa"
                      value={form.programa}
                      onChange={handleChange}
                      maxLength={100}
                      placeholder="Ej: Ingeniería de Sistemas"
                      className={inputWithIconClass}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <Label htmlFor="municipio" className="text-sm font-medium text-foreground">
                      Municipio
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
                    <Label htmlFor="periodo_primer_semestre" className="text-sm font-medium text-foreground">
                      Periodo que cursó 1er semestre
                    </Label>
                    <div className="group relative">
                      <Calendar className={`${inputIconClass} group-focus-within:text-accent`} />
                      <Input
                        id="periodo_primer_semestre"
                        name="periodo_primer_semestre"
                        value={form.periodo_primer_semestre}
                        onChange={handleChange}
                        maxLength={20}
                        placeholder="Ej: 2026-1"
                        className={inputWithIconClass}
                      />
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <Label htmlFor="pais_nacimiento" className="text-sm font-medium text-foreground">
                      País de nacimiento
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
                      Municipio de nacimiento
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
                    {editingId ? 'Guardar cambios' : 'Registrar estudiante'}
                  </Button>
                  {editingId && (
                    <Button
                      type="button"
                      variant="outline"
                      size="lg"
                      className="h-12"
                      onClick={handleCancelEdit}
                    >
                      <X className="h-4 w-4" />
                      Cancelar
                    </Button>
                  )}
                </div>
              </form>
            </div>
          </section>
        </div>
      </main>
    </div>
  )
}

function App() {
  return (
    <ThemeProvider>
      <EstudiantesCrud />
    </ThemeProvider>
  )
}

export default App
