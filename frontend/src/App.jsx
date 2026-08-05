import { useEffect, useState } from 'react'
import './App.css'

const API_URL = 'http://localhost:8000/api/estudiantes/'

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
}

function App() {
  const [estudiantes, setEstudiantes] = useState([])
  const [form, setForm] = useState(INITIAL_FORM)
  const [editingId, setEditingId] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchEstudiantes()
  }, [])

  async function fetchEstudiantes() {
    setLoading(true)
    try {
      const res = await fetch(API_URL)
      if (!res.ok) throw new Error('Error al cargar los estudiantes')
      setEstudiantes(await res.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value })
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
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
      fetchEstudiantes()
    } catch (err) {
      setError(err.message)
    }
  }

  function handleEdit(estudiante) {
    setEditingId(estudiante.id)
    setForm({
      tipo_identificacion: estudiante.tipo_identificacion,
      numero_identificacion: estudiante.numero_identificacion,
      primer_nombre: estudiante.primer_nombre,
      segundo_nombre: estudiante.segundo_nombre,
      primer_apellido: estudiante.primer_apellido,
      segundo_apellido: estudiante.segundo_apellido,
      codigo_estudiante: estudiante.codigo_estudiante,
    })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function handleCancelEdit() {
    setEditingId(null)
    setForm(INITIAL_FORM)
    setError('')
  }

  async function handleDelete(id) {
    if (!window.confirm('¿Seguro que deseas eliminar este estudiante?')) return
    try {
      const res = await fetch(`${API_URL}${id}/`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Error al eliminar el estudiante')
      fetchEstudiantes()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="container">
      <h1>CRUD de Estudiantes</h1>

      <form className="form" onSubmit={handleSubmit}>
        <h2>{editingId ? 'Editar estudiante' : 'Registrar estudiante'}</h2>

        <div className="grid">
          <label>
            Tipo de identificación *
            <select
              name="tipo_identificacion"
              value={form.tipo_identificacion}
              onChange={handleChange}
              required
            >
              {TIPOS_IDENTIFICACION.map((tipo) => (
                <option key={tipo.value} value={tipo.value}>
                  {tipo.label}
                </option>
              ))}
            </select>
          </label>

          <label>
            Número de identificación *
            <input
              name="numero_identificacion"
              value={form.numero_identificacion}
              onChange={handleChange}
              maxLength={20}
              required
            />
          </label>

          <label>
            Primer nombre *
            <input
              name="primer_nombre"
              value={form.primer_nombre}
              onChange={handleChange}
              maxLength={50}
              required
            />
          </label>

          <label>
            Segundo nombre
            <input
              name="segundo_nombre"
              value={form.segundo_nombre}
              onChange={handleChange}
              maxLength={50}
            />
          </label>

          <label>
            Primer apellido *
            <input
              name="primer_apellido"
              value={form.primer_apellido}
              onChange={handleChange}
              maxLength={50}
              required
            />
          </label>

          <label>
            Segundo apellido *
            <input
              name="segundo_apellido"
              value={form.segundo_apellido}
              onChange={handleChange}
              maxLength={50}
              required
            />
          </label>

          <label>
            Código de estudiante *
            <input
              name="codigo_estudiante"
              value={form.codigo_estudiante}
              onChange={handleChange}
              maxLength={20}
              required
            />
          </label>
        </div>

        {error && <p className="error">{error}</p>}

        <div className="actions">
          <button type="submit" className="btn primary">
            {editingId ? 'Guardar cambios' : 'Registrar'}
          </button>
          {editingId && (
            <button type="button" className="btn" onClick={handleCancelEdit}>
              Cancelar
            </button>
          )}
        </div>
      </form>

      <div className="list">
        <h2>Listado de estudiantes</h2>
        {loading ? (
          <p>Cargando...</p>
        ) : estudiantes.length === 0 ? (
          <p>No hay estudiantes registrados.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Tipo ID</th>
                <th>Número ID</th>
                <th>Nombres</th>
                <th>Apellidos</th>
                <th>Código</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {estudiantes.map((est) => (
                <tr key={est.id}>
                  <td>{est.tipo_identificacion}</td>
                  <td>{est.numero_identificacion}</td>
                  <td>
                    {est.primer_nombre} {est.segundo_nombre}
                  </td>
                  <td>
                    {est.primer_apellido} {est.segundo_apellido}
                  </td>
                  <td>{est.codigo_estudiante}</td>
                  <td className="table-actions">
                    <button className="btn small" onClick={() => handleEdit(est)}>
                      Editar
                    </button>
                    <button
                      className="btn small danger"
                      onClick={() => handleDelete(est.id)}
                    >
                      Eliminar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

export default App
