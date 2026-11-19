# crud_snies
Solucion temporal para solucionar problema con snies, dejo esto publico porque andamos corriendo jijijaja

Migracion del ERP antiguo (SWA) al nuevo ERP. La base SWA (`produccion`) es
exclusivamente de lectura; las escrituras van al nuevo ERP (`default`).

Graphify esta integrado para consultar la arquitectura y el flujo de migracion.
Ver [instalacion, comandos y protecciones del SWA](docs/graphify.md).

La migracion conserva los IDs de persona SWA, unifica duplicados mediante
equivalencias e incluye los programas 50, 51, 52 y 60. Ingles se asocia como
programa secundario de carrera cuando corresponde. Ver las
[reglas de identidad, preparacion del destino y pruebas](docs/codigos-persona.md).

La carga de notas y pensums dispone de analisis previo y un comando independiente.
Conserva cortes y definitivas, separa el periodo actual del historico y mantiene
los IDs de los pensums existentes, actualizando sus codigos desde SWA. Ver
[reglas, comandos y limites de la migracion academica](docs/notas-swa.md).
