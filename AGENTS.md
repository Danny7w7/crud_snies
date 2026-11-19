# Instrucciones del proyecto

Este proyecto migra datos del ERP antiguo (SWA) al nuevo ERP.

## Regla obligatoria: SWA solo lectura

- La conexion Django `produccion` corresponde al SWA: solo consultas de lectura.
- Nunca escribir, modificar esquemas, migrar, vaciar tablas, cargar fixtures ni
  ejecutar pruebas que creen bases de datos sobre el SWA. Esto incluye SQL directo,
  ORM, administracion y scripts.
- Las escrituras de la migracion se realizan exclusivamente en el destino
  `default` (nuevo ERP). Verificar siempre el alias antes de una operacion.
- Conservar el bloqueo de escritura de `ProductionRouter` y la opcion de conexion
  `default_transaction_read_only=on` del backend `config.db_postgres`.
- Las credenciales `DB_PROD_*` deben pertenecer a un usuario con permisos de lectura
  en el servidor. El modo de sesion no sustituye estos permisos.
- No ejecutar migraciones de datos ni endpoints de migracion para explorar el
  proyecto. Validar con mocks y pruebas sin conexiones reales.
- No leer ni publicar `.env`, credenciales, reportes con datos personales o dumps
  para construir el grafo.

## Graphify

- Usar la skill Graphify para preguntas de arquitectura y relaciones del codigo.
- Si existe `graphify-out/graph.json`, consultar el grafo antes de buscar codigo;
  confirmar los detalles relevantes en los archivos fuente.
- Si falta el grafo, generarlo desde esta raiz con `/graphify .` siguiendo la skill.
- Actualizarlo con `/graphify . --update` despues de cambios relevantes.
- Respetar `.graphifyignore`. Graphify analiza archivos locales y no debe importar
  la configuracion Django ni conectarse a ninguna base de datos.
- Consultas locales: `venv/Scripts/python.exe -m graphify query "pregunta"`.
- Instalacion y limites de seguridad: ver `docs/graphify.md`.
