# Graphify y migracion SWA

Graphify crea un mapa consultable del codigo y la documentacion de este proyecto.
El flujo de datos es **SWA (`produccion`, lectura) -> nuevo ERP (`default`, escritura)**.
La construccion del grafo no importa la aplicacion Django, ejecuta scripts de
migracion ni abre conexiones a las bases de datos.

## Instalacion y uso

Desde la raiz del repositorio, en PowerShell:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-graphify.txt
```

En Codex, usar la skill Graphify:

```text
/graphify .
/graphify . --update
```

La skill combina extraccion estructural del codigo con extraccion semantica de
documentos. No requiere una API key; Codex puede analizar los documentos en la
sesion. Gemini es opcional mediante `GEMINI_API_KEY` o `GOOGLE_API_KEY`.

Consultar el grafo ya generado desde PowerShell:

```powershell
.\venv\Scripts\python.exe -m graphify query "migrar_personas Persona PersonaErp"
.\venv\Scripts\python.exe -m graphify query "ProductionRouter DatabaseWrapper solo lectura"
.\venv\Scripts\python.exe -m graphify query "migrar_vinculos migrar_programas" --dfs --budget 1500
```

`graphify-out/graph.html` es el mapa interactivo, `GRAPH_REPORT.md` contiene el
informe y `graph.json` los datos del grafo. Los resultados y caches son locales y
se excluyen de Git. Regenerarlos al clonar el proyecto y actualizarlos despues de
cambiar el codigo o la documentacion.

`.graphifyignore` excluye `.env`, la configuracion con secretos, fixtures, reportes,
dumps, dependencias, archivos generados y recursos visuales de la plantilla.
El grafo describe estructuras y relaciones del codigo, nunca registros del SWA.

## Proteccion de la fuente

`ProductionRouter` consulta `Persona`, `EstudianteSwa`, `Programa` e `Historico`
desde `produccion`, rechaza sus escrituras ORM y prohibe migraciones en ese alias.
El backend `config.db_postgres` y `migracion.conexiones.conectar_swa` validan el
destino explícito, activan `default_transaction_read_only=on` al abrir en
autocommit y verifican ambos indicadores con `SELECT current_setting`. Sus
cursores ejecutan los `SELECT` del proyecto, incluidos los que usan CTE de
lectura. El backend conserva la compatibilidad con PostgreSQL 9.4.

`DB_PROD_HOST`, `DB_PROD_PORT`, `DB_PROD_NAME` y `DB_PROD_USER` identifican la
fuente real. Use un rol con permisos exclusivos de lectura. Para apuntar el
alias a `swa_raw`, use `MIGRADOR_SWA_LOCAL=1` con la copia local verificada en
`default`. `MIGRADOR_SIN_DOTENV=1` permite configurar ese entorno mediante
variables del proceso.

Referencias: [routers de Django](https://docs.djangoproject.com/en/5.2/topics/db/multi-db/)
y [modo de lectura de PostgreSQL 9.4](https://www.postgresql.org/docs/9.4/runtime-config-client.html).

Pruebas de las protecciones, sin Django settings de produccion ni bases de datos:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s backend/config/tests -v
```
