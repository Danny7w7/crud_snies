"""Explicacion de cada tipo de caso de revision (lo consume la vista "Analisis de casos").

Cada tipo responde: que paso en SWA, por que importa para el ERP, que recomendamos y
que hace cada opcion.
"""

CATALOGO_CASOS = {
    'PREMATRICULA_SIN_LEGALIZAR': {
        'titulo': 'Solo prematrícula, nunca legalizó',
        'que_paso': 'En SWA se le liquidó la matrícula (estado P = prematrícula) pero nunca quedó matriculado '
                    '(estado M) en ningún periodo y no tiene ninguna nota.',
        'por_que_importa': 'Técnicamente "tuvo una matrícula" en SWA, pero no hay prueba de que haya estudiado. '
                           'Si se migra, aparecerá en el ERP como estudiante con matrículas no vigentes.',
        'recomendacion': 'Incluir solo a quien tiene prematrícula del periodo en curso (2026-2): puede estar a '
                         'punto de legalizar. Los de periodos anteriores, excluirlos.',
        'opciones': {'INCLUIR': 'Se crea la persona en el ERP con sus prematrículas como matrículas no vigentes.',
                     'EXCLUIR': 'No se migra. Queda en la copia de SWA.'},
    },
    'PERSONA_SIN_MATRICULA': {
        'titulo': 'Tiene notas pero ninguna matrícula',
        'que_paso': 'Cursó asignaturas con nota en SWA, pero SWA no tiene ninguna matrícula registrada (M) a su '
                    'nombre en ningún periodo.',
        'por_que_importa': 'Sus notas prueban que estudió; si se excluye, se pierde su historial académico.',
        'recomendacion': 'Incluir.',
        'opciones': {'INCLUIR': 'Se migra como estudiante; sus periodos con notas quedan como matrículas inferidas '
                                '(cada una se confirma en "Notas en un periodo sin matrícula").',
                     'EXCLUIR': 'No se migra la persona ni sus notas.'},
    },
    'NOTAS_SIN_MATRICULA': {
        'titulo': 'Notas en un periodo sin matrícula',
        'que_paso': 'El estudiante sí tiene matrículas en SWA, pero en ESTE periodo tiene notas y no tiene la '
                    'matrícula (M) registrada. Suele ser un registro financiero que no se cerró.',
        'por_que_importa': 'En el ERP toda nota debe colgar de una matrícula. Sin decisión, esas notas no se migran.',
        'recomendacion': 'Incluir: las notas son evidencia de que cursó el periodo.',
        'opciones': {'INCLUIR': 'Se crea una matrícula (no vigente) para ese periodo y se migran sus notas.',
                     'EXCLUIR': 'Esas notas no se migran (quedan en la copia de SWA).'},
    },
    'DOCUMENTO_COMPARTIDO': {
        'titulo': 'Un documento, nombres distintos',
        'que_paso': 'Varios registros de persona en SWA tienen el mismo número de documento pero los nombres no '
                    'coinciden.',
        'por_que_importa': 'El ERP no permite dos personas con el mismo tipo y número de documento.',
        'recomendacion': 'Revisar los nombres: si es la misma persona mal digitada, unir con el nombre correcto.',
        'opciones': {'UNIR': 'Es la misma persona: se unifican sus registros con el nombre que indiques.',
                     'EXCLUIR': 'No se migra hasta aclarar la identidad.'},
    },
    'DOCUMENTO_INVALIDO': {
        'titulo': 'Número de documento sospechoso',
        'que_paso': 'El número tiene letras, menos de 6 o más de 11 dígitos (p. ej. un dígito repetido al '
                    'digitar).',
        'por_que_importa': 'El documento es la llave para encontrar a la persona en el ERP; si está mal, '
                           'admisiones no la encontrará.',
        'recomendacion': 'Corregir con el documento real (consultar hoja de vida o soporte). Aceptar solo si es '
                         'una cédula antigua válida de pocos dígitos.',
        'opciones': {'ACEPTAR': 'El número es correcto tal como está.',
                     'CORREGIR': 'Se migra con el número que indiques.',
                     'EXCLUIR': 'No se migra la persona.'},
    },
    'TIPO_DOCUMENTO_INDEFINIDO': {
        'titulo': 'Tipo de documento sin definir',
        'que_paso': 'SWA tiene el tipo "POR DEFINIR" y no hay fecha de nacimiento válida para deducir si es CC o TI.',
        'por_que_importa': 'El ERP exige el tipo de documento.',
        'recomendacion': 'Indicar el tipo correcto.',
        'opciones': {'CORREGIR': 'Se migra con el tipo indicado.', 'EXCLUIR': 'No se migra la persona.'},
    },
    'NOMBRE_INCOMPLETO': {
        'titulo': 'Falta nombre o apellido',
        'que_paso': 'En SWA el nombre o el apellido está vacío o quedó vacío al limpiarlo.',
        'por_que_importa': 'El ERP exige primer nombre y primer apellido.',
        'recomendacion': 'Completar el nombre.',
        'opciones': {'CORREGIR': 'Se migra con el nombre que indiques.', 'EXCLUIR': 'No se migra la persona.'},
    },
    'CONFLICTO_PERSONA_ERP': {
        'titulo': 'Ya existe en el ERP con otro nombre',
        'que_paso': 'El documento ya está registrado en el ERP (por admisiones) pero con un nombre distinto.',
        'por_que_importa': 'Si es la misma persona, su historial debe quedar en esa ficha y no en una nueva.',
        'recomendacion': 'Vincular si es la misma persona.',
        'opciones': {'VINCULAR': 'El historial de SWA se asocia a la persona que ya existe en el ERP.',
                     'EXCLUIR': 'No se migra hasta aclarar.'},
    },
    'DOCUMENTO_REPETIDO_NORMALIZADO': {
        'titulo': 'Dos documentos quedan iguales',
        'que_paso': 'Dos documentos distintos de SWA quedan con el mismo tipo y número al limpiarlos.',
        'por_que_importa': 'El ERP no permite documentos repetidos.',
        'recomendacion': 'Revisar en "Personas" y corregir el documento en el caso de documento sospechoso.',
        'opciones': {'EXCLUIR': 'No se migran hasta aclarar.'},
    },
    'INGLES_SECUENCIA_IRREGULAR': {
        'titulo': 'Módulos de inglés con saltos',
        'que_paso': 'Los módulos aprobados no empiezan en 1 o tienen huecos (p. ej. 1, 2, 4).',
        'por_que_importa': 'Es informativo: el ERP habilita el siguiente módulo según los aprobados. Muchos se '
                           'corrigen solos al incluir las "Notas en un periodo sin matrícula".',
        'recomendacion': 'Aceptar.',
        'opciones': {'ACEPTAR': 'Se migran los módulos aprobados tal cual.'},
    },
}
