const fs = require('fs');
const path = require('path');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType,
  AlignmentType, HeadingLevel, LevelFormat, PageOrientation, BorderStyle, Footer, PageNumber,
} = require('docx');

const OUT_DIR = 'C:\\Users\\Administrator\\Documents\\FASA\\Auditorias de datos';
const FONT = 'Arial';
const VERDE = '1D7A5A', ROJO = 'B42318', AMBAR = '9A6700', GRIS = '6B7280';

// ── helpers ──────────────────────────────────────────────────────────────────
// Texto con **negritas** y `código` inline
function runs(text, opts = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  const base = Object.fromEntries(Object.entries(opts).filter(([, v]) => v !== undefined));
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(new TextRun({ ...base, text: text.slice(last, m.index), font: FONT }));
    const t = m[0];
    if (t.startsWith('**')) out.push(...runs(t.slice(2, -2), { ...base, bold: true }));  // admite `código` dentro
    else out.push(new TextRun({ ...base, text: t.slice(1, -1), font: 'Consolas', size: (base.size || 20) - 2 }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(new TextRun({ ...base, text: text.slice(last), font: FONT }));
  return out;
}
const P = (t, o = {}) => new Paragraph({ children: runs(t, o.run || {}), spacing: { after: 120 }, ...o.par });
const H1 = t => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text: t, font: FONT })] });
const H2 = t => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text: t, font: FONT })] });
const B = (t, lvl = 0) => new Paragraph({ numbering: { reference: 'bullets', level: lvl }, children: runs(t), spacing: { after: 60 } });

function cell(text, width, o = {}) {
  const color = o.color, bold = o.bold;
  // boldFirst: solo la primera línea en negrita (estado), el resto es nota normal
  const paras = String(text).split('\n').map((line, i) => new Paragraph({
    children: runs(line, { size: o.size || 18, color, bold: o.boldFirst ? i === 0 : bold }), spacing: { after: 40 },
    alignment: o.align || AlignmentType.LEFT,
  }));
  return new TableCell({
    width: { size: width, type: WidthType.DXA }, children: paras,
    shading: o.fill ? { type: ShadingType.CLEAR, color: 'auto', fill: o.fill } : undefined,
    margins: { top: 60, bottom: 60, left: 90, right: 90 },
  });
}
// cols: [{w, h}], rows: [[valor | {t, color, bold, fill}]]
function table(cols, rows) {
  const total = cols.reduce((s, c) => s + c.w, 0);
  const head = new TableRow({ tableHeader: true, children: cols.map(c => cell(c.h, c.w, { bold: true, fill: 'E8EFEC' })) });
  const body = rows.map(r => new TableRow({ children: r.map((v, i) => {
    const o = typeof v === 'object' && v !== null ? v : { t: v };
    return cell(o.t, cols[i].w, o);
  }) }));
  return new Table({ width: { size: total, type: WidthType.DXA }, columnWidths: cols.map(c => c.w), rows: [head, ...body] });
}
const SP = () => new Paragraph({ children: [], spacing: { after: 80 } });

function doc(children, landscape, titulo) {
  return new Document({
    creator: 'Juan Carlos Alvarez (MindBridge)', title: titulo,
    styles: {
      default: { document: { run: { font: FONT, size: 20 } } },
      paragraphStyles: [
        { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true,
          run: { size: 28, bold: true, color: '0F4C3A', font: FONT }, paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 0 } },
        { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true,
          run: { size: 23, bold: true, color: '1F2937', font: FONT }, paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 1 } },
      ],
    },
    numbering: { config: [{ reference: 'bullets', levels: [
      { level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } },
      { level: 1, format: LevelFormat.BULLET, text: '–', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1080, hanging: 270 } } } },
    ] }] },
    sections: [{
      properties: { page: {
        size: landscape ? { width: 12240, height: 15840, orientation: PageOrientation.LANDSCAPE } : { width: 12240, height: 15840 },
        margin: { top: 1080, bottom: 1080, left: 1080, right: 1080 },
      } },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [
        new TextRun({ text: titulo + ' · pág. ', font: FONT, size: 16, color: GRIS }),
        new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16, color: GRIS }),
      ] })] }) },
      children,
    }],
  });
}
function titulo(t, sub) {
  return [
    new Paragraph({ children: [new TextRun({ text: t, bold: true, size: 36, color: '0F4C3A', font: FONT })], spacing: { after: 80 } }),
    new Paragraph({ children: [new TextRun({ text: sub, size: 20, color: GRIS, font: FONT })], spacing: { after: 240 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: 'C7D6D0', space: 6 } } }),
  ];
}
const PEND = { t: 'Pendiente', color: ROJO, boldFirst: true };
const PARC = { t: 'Avance parcial', color: AMBAR, boldFirst: true };

// ═════════════════════════════════════════════════════════════════════════════
// 1. TEMAS PENDIENTES
// ═════════════════════════════════════════════════════════════════════════════
const W1 = [{ w: 500, h: '#' }, { w: 5000, h: 'Tema y qué se pide' }, { w: 3300, h: 'Criterio para darlo por resuelto' },
            { w: 1300, h: 'Responsable' }, { w: 3580, h: 'Estado al 25-oct-2026' }];
const fila = (n, tema, crit, resp, est, nota) => [String(n), tema, crit, resp, { ...est, t: est.t + (nota ? '\n' + nota : '') }];

const urgentes = [
  fila(1, '**Liga de cada ticket con su vaciado (hueco jul–oct).** Restablecer `idTicketVaciado`/`FHrVaciado` en `betamega_02_pks_ticketsorigen` desde julio de 2026, o confirmar que `betamega_aleks_idtickethistorial` la reemplaza y agregarle la fecha real de vaciado. Sin esta liga no hay rechazo real ni puntos de control de julio en adelante.',
    'Tickets jul–oct con fecha de vaciado dentro de ±5% de `ticketsconvaciadodirecto` (4,343 en agosto, 3,781 en septiembre); b01/b03/b06/b08 con la misma cobertura.',
    'IT', PEND, 'Sin cambio: 97–99% de los tickets jul–oct siguen sin vaciado (octubre: 6,466 de 6,654).'),
  fila(2, '**Reactivar `betamega_02_pks_ticketcorrida`** (detenida desde el 9-jul-2026) y corregir el deadlock de `RunDaily_betaMEGA_00_Corridas` (tabla temporal `betamega_temp_seqcorridasrechazo` que ya existe). Es la causa probable de que el rechazo nuevo no se registre.',
    'Corridas con fecha de la semana en curso; `betamega_00_logerrores` sin errores 1213/1050.',
    'IT', PEND, 'Sin cambio: último registro 9-jul 10:09. Rechazo registrado ago–oct: 0 piezas.'),
  fila(16, '**Registro de desmoldeo y limpieza por pieza.** Reactivar `FHrDESM`/`FHrLIMP` en `betamega_08_ticketrutacritica` o dar otra fuente con ticket + fecha/hora de desmoldeo y limpieza. Sin esto no se mide vaciado→desmoldeo ni desmoldeo→limpieza, ni cuántas piezas están terminadas.',
    'Desmoldeo y limpieza registrados para ≥95% de las piezas vaciadas de la semana.',
    'IT', PEND, 'Nueva (25-oct). Desmoldeo: último registro 1-oct. Desde el 12-oct solo 168 de 1,881 piezas vaciadas (9%) aparecen en la tabla.'),
];
const integridad = [
  fila(3, '**Regla del ticket heredado.** En 2025, 50,118 tickets corresponden a 44,043 vaciados físicos: ¿el ticket hijo es la misma pieza física o una pieza distinta? Cambia el % de rechazo (15.1% por ticket vs 16.3% por vaciado).',
    'Respuesta escrita; si es la misma pieza, una columna o tabla que marque el ticket "contable".', 'IT / Calidad', PEND),
  fila(4, '**Quitar el duplicado de la semana 202640** en `gamamega_01_programaperiodo` y `gamamega_01_paretopesoperiodo` (dos cargas con valores distintos).',
    'Una fila por Periodo + No_Parte; `PrcContribucion` de 202640 suma 1.0.', 'IT / CSC', PEND,
    'Sin cambio: 17 partes repetidas; el Pareto de 202640 sigue sumando 2.0.'),
  fila(5, '**Quitar duplicados por llave** en `cargasmetalicas` (colada 84420 ×10), `corridasrechazo`, `ticketcorrida`, `aleks_idtickethistorial` e `idticketsidmuestras`.',
    '0 filas duplicadas por llave en cada tabla.', 'IT', PARC,
    'b01 quedó sin duplicados (16→0). Siguen: cargas 38 combinaciones; corridasrechazo 2026 subió de 412 a 541; ticketcorrida 5,464; aleks 1,100 tickets (314 con colada distinta); idticketsidmuestras 289.'),
  fila(6, '**Tickets con dos coladas o varios vaciados** en `betamega_03_coladasproceso`/`ticketcoladacargametalica` y `betamega_06_vaciado`.',
    'Un registro por ticket, o una regla documentada de cuál prevalece.', 'IT', PARC,
    'b06 con varios vaciados bajó de 659 a 357; b03 con dos coladas sigue en 288.'),
  fila(11, '**Hueco de `betamega_00_coladaprocesodemoras`** del 18-ago al 29-sep-2026: explicar y, si se puede, recuperar.',
    'Coladas de ese periodo cargadas, o confirmación de que no hay dato.', 'IT', PEND, 'Sin cambio: sin coladas del 18-ago al 29-sep.'),
  fila(12, '**Reanudar `programa3dias` y `recomendacionescontrol`**; limpiar las fechas anteriores a 2015 en `programa3dias`.',
    'Datos de la semana en curso; 0 fechas anteriores a 2015.', 'IT', PEND,
    'Sin cambio: programa3dias en 3-oct; recomendaciones en semana 202635; 18,062 fechas < 2015.'),
];
const catalogos = [
  fila(7, '**Catálogo de modelos en gamamega** con peso, cliente, rango de temperatura, clase y estatus (hoy sale de `corex_test.modelos`, congelada desde el 11-mar-2026).',
    'Las partes producidas en 2025–26 presentes, con peso y rango de temperatura.', 'IT', PEND,
    'Empeoró: de 444 partes de 2025–26, 6 no existen en el catálogo (antes 5), 21 sin peso (antes 13), 48 sin rango de temperatura (antes 39).'),
  fila(8, '**Análisis químico por colada y pedidos/embarques en gamamega** (hoy `corex_test`/`piso_ia`, congelados en marzo).',
    'Datos posteriores a marzo de 2026.', 'IT', PEND, 'Sin cambio: piso_ia.fdbase y piso_ia.rechazo en 9-mar-2026.'),
  fila(9, '**Llenar `idItaca`, `idTension` e `idArenas`** en `betamega_02_pks_idticketsidmuestras`.',
    'Más del 80% de los tickets de 2026 con `idItaca`.', 'IT', PARC,
    '`idItaca` pasó de 41 a 1,123 tickets en 2026 (10–18% en ago–sep; octubre 18). `idTension` e `idArenas` siguen vacíos.'),
  fila(18, '**Tipo de rechazo en `betamega_01_rechazosbyidticket`** (Producción / Maquinado / Prueba, el campo `tipo_rech` de `piso_ia.rechazo`). El costo de no-calidad se calcula solo con rechazo de Producción (maquinado y prueba ya traen costo adicional). `piso_ia.rechazo` se congeló el 9-mar-2026 y `betamega_01` no trae ese campo, así que desde 2026 se usa todo el rechazo interno (~7% más toneladas: 1,201.8 t contra 1,119.2 t en 2025). No se puede separar por defecto.',
    'Columna `tipo_rech` en `betamega_01` para todo rechazo interno desde 2025, que cuadre con `piso_ia.rechazo` en 2025 (6,308 Producción, 302 Maquinado, 4 Prueba).', 'IT', PEND, 'Nueva (25-oct).'),
];
const reglas = [
  fila(10, '**Tolerancias y límites.** Tolerancia de ferroaleaciones en cargas (hoy 0 en el 100%); base de `betamega_07_cargametalicosprc`; límites de AFS (48–54) y de granulometría (% retenido malla 14, 16 y PAN) en `variablesdecontrol`.',
    '`PrcTolerancia` > 0 en ferroaleaciones; porcentajes que sumen 100% ±2%; límites AFS y de granulometría confirmados por Calidad.', 'Calidad / IT', PEND,
    'Sin cambio: 39,120 de 39,120 cargas de ferroaleación con tolerancia 0 (43% "fuera"); AFS 48–54: 110 de 110 mediciones fuera desde el 13-oct.'),
  fila(13, '**Regla de retornos ≤ 30% (PC-1):** qué materiales cuentan como retorno, si aplica a gris y nodular, y si las recetas de Corex deben respetarla. Tolerancia numérica para OPE-FUS-005.',
    'Respuesta escrita con la lista de claves de retorno y el límite por clase de hierro; tolerancia de pesaje en kg o %.', 'Calidad', PEND, 'Nueva (14-oct). En 2026 el retorno promedio en carga es 32%.'),
  fila(14, '**Estándar del análisis térmico ITACA** (TBL-OPE-IIP-001/002/003, citadas en OPE-FUS-010): límite por alerta (Porosity, MacroShrinkage, Cementite, Nodularization, InverseChill) y por clase de hierro. Además, una llave copa→colada en `itaca_acqresults`.',
    'Tablas TBL-OPE-IIP con límites numéricos; columna de colada u horno en `itaca_acqresults` (o `idItaca` poblado).', 'Calidad / Metalurgia / IT', PEND,
    'Nueva (25-oct). La Torre usa límites provisionales (≥70 verde, ≥50 ámbar, <50 rojo) y liga la copa por hora (~85% de coladas).'),
  fila(15, '**Ventana de la tasa del pronóstico semanal de CSC** (`gamamega_01_paretopesoperiodo`): documentarla y actualizarla. Caso 42091101: CSC espera 24.3%, la Torre 8.2%; la parte bajó de 20–65% (2025) a 4–8% (2026) tras el rediseño de DEPRESION.',
    'Ventana y método documentados; tasa recalculada con historia de 2026, o justificación de mantener la de 2025.', 'CSC', PEND,
    'Nueva (25-oct). Semana 43: 45 de 77 partes difieren 5 puntos o más.'),
];

const infra = [
  fila(17, '**Índices y estabilidad del servidor de BD.** `itaca_acqresults` (8.9 millones de filas) no tiene ningún índice y `ticketsconvaciadodirecto` no tiene índice en `FHrVaciado`: consultas simples tardan de 12 a 30 s. Durante pruebas del 25-oct MySQL cortó conexiones dos veces bajo carga (errores 2003 "Can\'t connect" y 2013 "Lost connection"). Sugerencia: índices en `itaca_acqresults (aResDate, aResNum)` y en `ticketsconvaciadodirecto (FHrVaciado)`, y revisar límites de conexiones y timeouts.',
    'Índices creados (o explicación de por qué no); consulta de un día de ITACA o de vaciados en menos de 2 s; sin cortes de conexión en una semana de uso normal de la Torre.', 'IT', PEND, 'Nueva (25-oct).'),
];

const doc1 = doc([
  ...titulo('Temas pendientes con IT, Calidad y CSC', 'Torre de Control FASA · corte 25 de octubre de 2026 · Preparado por Juan Carlos Alvarez (MindBridge)'),
  P('Hay **18 temas abiertos**. Ninguno está cerrado y 3 tienen avance parcial (5, 6 y 9). Los tres primeros son los más urgentes: sin ellos la Torre no tiene rechazo real, puntos de control ni piezas terminadas de julio en adelante.'),
  P('Cada tema trae un criterio para darlo por resuelto y el estado verificado contra la base de datos el 25-oct-2026. El detalle de las mediciones está en la auditoría de datos de la misma fecha.'),
  H2('A. Urgentes: bloquean rechazo, puntos de control y avance'),
  table(W1, urgentes), SP(),
  H2('B. Integridad y duplicados'),
  table(W1, integridad), SP(),
  H2('C. Catálogos y ligas faltantes'),
  table(W1, catalogos), SP(),
  H2('D. Reglas y estándares'),
  table(W1, reglas), SP(),
  H2('E. Infraestructura de la base de datos'),
  table(W1, infra), SP(),
  P('**Mejoras de captura sugeridas** (sin prioridad): catálogo cerrado para `Puesto` en variables de control (hoy más de 30 variantes de "pintor"); unificar los 3 `idDefecto` con dos nombres (53, 55 y 57); agregar PERDIDA POR COMPLEMENTO y MODELOS al catálogo de defectos.', { run: { size: 18 } }),
], true, 'Temas pendientes con IT, Calidad y CSC');

// ═════════════════════════════════════════════════════════════════════════════
// 2. AUDITORÍA DE DATOS — ACTUALIZACIÓN
// ═════════════════════════════════════════════════════════════════════════════
const MEJ = { color: VERDE, bold: true }, PEOR = { color: ROJO, bold: true }, IGUAL = { color: GRIS };
const t = (s, o) => ({ t: s, ...o });

const disp = [
  ['`betamega_02_pks_ticketsorigen`', '13-oct · 99% sin vaciado jul–oct', '24-oct · 97–99% sin vaciado', t('Sin cambio', IGUAL)],
  ['`betamega_01/03/06` y `ticketcoladacargametalica`', '12-oct · 14 / 31 / 79 / 151 tickets por mes (jul–oct)', '24-oct · 14 / 31 / 79 / 188', t('Sin cambio', IGUAL)],
  ['`betamega_08_ticketrutacritica`', '12-oct · 14 / 31 / 79 / 151', 'Vaciado 24-oct; **desmoldeo 1-oct**; limpieza 155 / 424 / 335 / 396 por mes', t('Empeoró', PEOR)],
  ['`betamega_07_carga*` (3 tablas)', '12-oct · 13 / 28 / 79 / 150', 'Recreadas 24-oct · 13 / 28 / 79 / 181', t('Sin cambio', IGUAL)],
  ['`betamega_02_pks_ticketcorrida`', '9-jul-2026 (parada)', '9-jul-2026 (parada)', t('Sin cambio', IGUAL)],
  ['`betamega_00_ticketsconvaciadodirecto`', '13-oct · completa', '24-oct · completa: 394 / 4,343 / 3,781 / 3,116', t('Viva', MEJ)],
  ['`betamega_aleks_idtickethistorial`', '13-oct · completa por moldeo', '24-oct · 4,364 / 4,330 / 3,833 / 3,168', t('Viva', MEJ)],
  ['`betamega_00_coladaprocesodemoras`', 'Hueco 18-ago al 29-sep', '24-oct · hueco sin recuperar', t('Sin cambio', IGUAL)],
  ['`betamega_00_cargasmetalicas`', 'Colada 86199', 'Colada 86474', t('Viva', MEJ)],
  ['`betamega_00_corridasrechazo`', '13-oct · depende de b01', '24-oct · depende de b01', t('Sin cambio', IGUAL)],
  ['`betamega_09_variablesdecontrol`', '13-oct · completa', '24-oct · completa (3,724 registros en octubre)', t('Viva', MEJ)],
  ['`itaca_acqresults`', '12-oct', '24-oct · ~4,000 muestras por mes', t('Viva', MEJ)],
  ['`gamamega_01_programaperiodo` / `paretopesoperiodo`', 'Semana 202644', 'Semana 202644 (semana 44 parcial: 24 partes)', t('Sin cambio', IGUAL)],
  ['`gamamega_01_programa3dias`', '3-oct', '3-oct (22 días sin carga)', t('Sin cambio', IGUAL)],
  ['`gamamega_02_recomendacionescontrol`', 'Semana 202635', 'Semana 202635', t('Sin cambio', IGUAL)],
  ['`betamega_00_logerrores`', 'Deadlocks 2–3 oct', 'Sin errores nuevos desde el 3-oct', t('Sin cambio', IGUAL)],
  ['`corex_test.*`, `piso_ia.*`', '~11-mar-2026 (congeladas)', '11-mar / 9-mar-2026 (congeladas)', t('Sin cambio', IGUAL)],
];
const cob = [
  ['Tickets en `ticketsorigen`', '5,043', '5,156', '2,887', '7,789', '7,576', '6,654'],
  ['  % sin vaciado', '32%', '34%', t('99.5%', PEOR), t('99.6%', PEOR), t('99.0%', PEOR), t('97.2%', PEOR)],
  ['Piezas vaciadas reales (`ticketsconvaciadodirecto`)', '3,431', '3,201', '394', '4,343', '3,781', '3,116'],
  ['Moldeadas (`aleks`)', '3,387', '3,963', '4,364', '4,330', '3,833', '3,168'],
  ['Tickets en derivadas (b01/b03/b06)', '3,506', '3,446', t('14', PEOR), t('31', PEOR), t('79', PEOR), t('188', PEOR)],
  ['Rechazo registrado (b01)', '382', '524', t('1', PEOR), t('0', PEOR), t('0', PEOR), t('0', PEOR)],
  ['Limpieza registrada (b08)', '3,357', '3,528', '155', '424', '335', '396'],
  ['Desmoldeo registrado (b08)', '3,464', '3,560', t('1', PEOR), t('3', PEOR), t('0', PEOR), t('1', PEOR)],
  ['Coladas (`coladaprocesodemoras`)', '698', '736', '760', '411', t('8', PEOR), '530'],
  ['Variables de control (b09)', '4,192', '4,577', '4,556', '4,566', '3,890', '3,724'],
  ['Muestras ITACA', '3,300', '3,984', '4,377', '4,515', '4,266', '3,121'],
];
const integ = [
  ['Duplicados en `betamega_01_rechazosbyidticket`', '16 tickets', '0', t('Resuelto', MEJ)],
  ['Tickets con varios vaciados (b06)', '659', '357', t('Mejoró', MEJ)],
  ['Tickets con dos coladas (b03)', '289', '288', t('Sin cambio', IGUAL)],
  ['Duplicados en `cargasmetalicas`', 'Colada 84420 ×10', '38 combinaciones (máx. ×10)', t('Sin cambio', IGUAL)],
  ['Duplicados en `corridasrechazo` (2026)', '412', '541', t('Empeoró', PEOR)],
  ['Filas de más en `ticketcorrida`', '5,464', '5,464', t('Sin cambio', IGUAL)],
  ['Tickets repetidos en `aleks`', '993', '1,100 (314 con colada distinta)', t('Empeoró', PEOR)],
  ['Tickets con dos análisis químicos', '275', '289', t('Empeoró', PEOR)],
  ['Semana 202640 duplicada', '17 partes; Pareto suma 2.0', '17 partes; Pareto suma 2.0', t('Sin cambio', IGUAL)],
  ['Temperaturas de vaciado en 0 °C (2025–26)', '3,579', '3,578', t('Sin cambio', IGUAL)],
  ['Temperatura máxima capturada en 2026', '113,396 °C', '13,890 °C', t('Mejoró, sigue inválida', { color: AMBAR, bold: true })],
  ['Rango de temperatura con mín ≥ máx (2026)', '46', '49', t('Empeoró', PEOR)],
  ['Limpieza antes que desmoldeo (b08, 2025+)', '5,102', '5,101', t('Sin cambio', IGUAL)],
  ['Limpieza marcada sin vaciado (b08)', '1,067', '1,332', t('Empeoró', PEOR)],
  ['Ferroaleaciones con tolerancia 0', '100% (37,713)', '100% (39,120); 43% "fuera"', t('Sin cambio', IGUAL)],
  ['AFS con límite 48–54 fuera de límite', '~99%', '100% (110 de 110 desde 13-oct)', t('Sin cambio', IGUAL)],
  ['Fechas < 2015 en `programa3dias`', '18,062', '18,062', t('Sin cambio', IGUAL)],
  ['`idDefecto` con dos nombres', '3', '3', t('Sin cambio', IGUAL)],
  ['Partes 2025–26 sin modelo / sin peso / sin rango de temp.', '5 / 13 / 39', '6 / 21 / 48', t('Empeoró', PEOR)],
  ['Tickets 2026 con `idItaca`', '41', '1,123', t('Mejoró', MEJ)],
];

const doc2 = doc([
  ...titulo('Auditoría de calidad y disponibilidad de datos — actualización',
    'Torre de Control FASA · corte 25-oct-2026 (datos al 24-oct 14:00) · comparado contra la auditoría del 13-oct-2026 · Preparado por Juan Carlos Alvarez (MindBridge)'),
  H1('1. Resumen'),
  P('En 12 días **no se resolvió ninguno de los problemas críticos**. El hueco de julio a octubre sigue igual: los tickets existen pero el 97–99% no tiene liga a su vaciado, y por eso no hay rechazo real ni puntos de control completos desde julio. Además, **dejó de registrarse el desmoldeo** (último dato: 1-oct).'),
  B('**Sigue igual:** hueco jul–oct en tickets, `ticketcorrida` parada desde el 9-jul, hueco de coladas del 18-ago al 29-sep, semana 202640 duplicada, `programa3dias` y recomendaciones detenidas, Corex y piso congelados en marzo.'),
  B('**Mejoró:** b01 sin duplicados (16→0); tickets con varios vaciados en b06 (659→357); `idItaca` poblado en 1,123 tickets de 2026 (antes 41); la captura extrema de 113,396 °C ya no aparece.'),
  B('**Empeoró:** desmoldeo sin registros desde el 1-oct y limpieza con ~9% de las piezas; duplicados en `corridasrechazo` (412→541) y en `aleks` (993→1,100); partes sin peso en el catálogo (13→21) y sin rango de temperatura (39→48).'),
  B('**Lo que sí está completo y vivo:** piezas vaciadas (`ticketsconvaciadodirecto`), moldeo (`aleks`), cargas, variables de control de arenas, análisis térmico ITACA y el programa semanal. La Torre ya usa estas fuentes donde las derivadas fallan (ver sección 5).'),
  H1('2. Alcance y método'),
  P('Mismo método que el 13-oct: 871 tablas y vistas del servidor `200.76.23.164` con el usuario de solo lectura `sapiens` (`ia_fasa` 41, `corex_test` 708, `piso_ia` 118, `test` 4). Se repitieron las mismas consultas de frescura, cobertura mensual, duplicados por llave, rangos y catálogos. Las cifras de octubre llegan al 24-oct 14:00.'),
  H1('3. Disponibilidad: qué está vivo y qué está congelado'),
  table([{ w: 2900, h: 'Tabla' }, { w: 2500, h: '13-oct' }, { w: 3300, h: '25-oct' }, { w: 1380, h: 'Cambio' }], disp), SP(),
  H2('Cobertura mensual 2026'),
  P('Mayo y junio son la referencia de un mes normal. De julio en adelante la producción existe (vaciado y moldeo completos), pero las tablas derivadas casi no la ven.'),
  table([{ w: 3780, h: 'Fuente' }, { w: 1050, h: 'May' }, { w: 1050, h: 'Jun' }, { w: 1050, h: 'Jul' }, { w: 1050, h: 'Ago' }, { w: 1050, h: 'Sep' }, { w: 1050, h: 'Oct*' }], cob),
  P('* Octubre hasta el 24 a las 14:00.', { run: { size: 16, color: GRIS } }),
  H1('4. Integridad: comparación contra el 13-oct'),
  table([{ w: 3800, h: 'Problema' }, { w: 2100, h: '13-oct' }, { w: 2600, h: '25-oct' }, { w: 1580, h: 'Tendencia' }], integ), SP(),
  P('El problema de los tickets heredados (50,118 tickets de 2025 sobre 44,043 vaciados físicos; 15.1% vs 16.3% de rechazo) es histórico y no cambia: sigue esperando la regla de negocio (tema 3).'),
  H1('5. Hallazgos nuevos desde el 13-oct'),
  P('Salieron al conectar fuentes nuevas a la Torre entre el 14 y el 25 de octubre.'),
  B('**`betamega_08_ticketrutacritica` casi dejó de registrar.** Desde el 12-oct solo 168 de 1,881 piezas vaciadas (9%) aparecen; desmoldeo en cero desde el 1-oct; limpieza registra 60–190 piezas por semana contra ~900 vaciadas. La Torre ahora mide avance y ritmo con piezas vaciadas (seguimiento mensual, plan semanal, Riesgo del Plan, agentes Alfa y Beta): antes solo 13 de 144 partes del plan tenían ritmo; ahora 108.'),
  B('**ITACA sí se puede usar, ligado por hora.** `itaca_acqresults` no trae colada ni horno y `idItaca` cubre muy pocos tickets. La copa de análisis se toma 10–15 min antes de liberar la colada; con esa regla se asigna al ~85% de las coladas (~7% ambiguas con dos hornos abiertos). Faltan los límites oficiales (tema 14).'),
  B('**Arenas: LOI fuera de límite en casi todas las líneas.** Últimas mediciones: AF2 1.61, AF3 1.49, K300 1.45 contra máximo 1.35. AFS (48–54) y la granulometría tienen límites que no corresponden a la arena real (tema 10).'),
  B('**El pronóstico semanal de CSC usa tasas desactualizadas.** En la semana 43, 45 de 77 partes difieren 5 puntos o más contra la tasa de los últimos 12 meses; caso principal 42091101 (24.3% vs 8.2%) (tema 15).'),
  B('**Programa semanal contra vaciado real:** cada semana se vacía 33–61% de lo programado (semana 43: 611 de 1,123 piezas) y entre 90 y 420 piezas por semana son de partes que no estaban en el programa. Hay que confirmar con Planeación si el programa semanal incluye piezas que no se esperan vaciar esa semana.'),
  B('**Retornos en carga:** en 2026 el retorno promedio es 32% contra el límite de 30% del Plan de Control; julio llegó a 40% (tema 13).'),
  B('**Estabilidad del servidor.** Durante las pruebas MySQL cortó conexiones dos veces bajo consultas pesadas (errores 2003 y 2013). Tablas grandes sin índices útiles: `itaca_acqresults` (8.9 millones de filas, ningún índice) y `ticketsconvaciadodirecto` (sin índice en `FHrVaciado`); una consulta simple tarda de 12 a 30 s. Conviene que IT evalúe índices en esas columnas (tema 17).'),
  H1('6. Estado de los temas pendientes'),
  P('De 18 temas, **0 resueltos, 3 con avance parcial** (5 duplicados, 6 tickets con varios registros, 9 `idItaca`) y 15 sin avance, incluidos los 6 agregados después del 13-oct (13 a 18). El detalle y los criterios de aceptación están en el documento "Temas pendientes con IT, Calidad y CSC" del 25-oct-2026.'),
], false, 'Auditoría de datos — actualización 25-oct-2026');

(async () => {
  const f1 = path.join(OUT_DIR, 'Temas_Pendientes_IT_Calidad_CSC_2026-10-25.docx');
  const f2 = path.join(OUT_DIR, 'Auditoria_Datos_Actualizacion_2026-10-25.docx');
  fs.writeFileSync(f1, await Packer.toBuffer(doc1));
  fs.writeFileSync(f2, await Packer.toBuffer(doc2));
  console.log('ok', f1, f2);
})();
