const ETIQUETAS_CASO = {
  PREMATRICULA_SIN_LEGALIZAR: 'solo prematrícula',
  PERSONA_SIN_MATRICULA: 'notas sin ninguna matrícula',
  NOTAS_SIN_MATRICULA: 'notas en un periodo sin matrícula',
  DOCUMENTO_COMPARTIDO: 'documento con nombres distintos',
  DOCUMENTO_INVALIDO: 'documento sospechoso',
  TIPO_DOCUMENTO_INDEFINIDO: 'tipo de documento sin definir',
  NOMBRE_INCOMPLETO: 'nombre incompleto',
  CONFLICTO_PERSONA_ERP: 'existe en el ERP con otro nombre',
  INGLES_SECUENCIA_IRREGULAR: 'módulos de inglés con saltos',
  CODIGO_SWA_INVALIDO: 'código SWA inválido',
  CODIGO_SWA_EN_USO: 'código SWA ya usado en el ERP',
}

export const etiquetaCaso = (tipo) => ETIQUETAS_CASO[tipo] || tipo
