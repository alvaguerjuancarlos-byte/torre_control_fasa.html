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
const RES = { t: 'Resuelto', color: VERDE, boldFirst: true };
// Código SQL en un párrafo por línea (anexos)
const SQL = s => s.trim().split('\n').map(l => new Paragraph({
  children: [new TextRun({ text: l, font: 'Consolas', size: 16 })], spacing: { after: 0 }, indent: { left: 360 },
}));

// ═════════════════════════════════════════════════════════════════════════════
// 1. TEMAS PENDIENTES
// ═════════════════════════════════════════════════════════════════════════════
const W1 = [{ w: 500, h: '#' }, { w: 5000, h: 'Tema y qué se pide' }, { w: 3300, h: 'Criterio para darlo por resuelto' },
            { w: 1300, h: 'Responsable' }, { w: 3580, h: 'Estado al 28-oct-2026' }];
const fila = (n, tema, crit, resp, est, nota) => [String(n), tema, crit, resp, { ...est, t: est.t + (nota ? '\n' + nota : '') }];

const urgentes = [
  fila(1, '**Liga de cada ticket con su vaciado (hueco jul–oct).** Restablecer `idTicketVaciado`/`FHrVaciado` en `betamega_02_pks_ticketsorigen` desde julio de 2026, o confirmar que `betamega_aleks_idtickethistorial` la reemplaza y agregarle la fecha real de vaciado. Sin esta liga no hay puntos de control completos de julio en adelante.',
    'Tickets jul–oct con fecha de vaciado dentro de ±5% de `ticketsconvaciadodirecto` (4,343 en agosto, 3,781 en septiembre); b01/b03/b06/b08 con la misma cobertura.',
    'IT', PEND, 'El rechazo de jul–oct ya se liga por ticket con `ia_fasa.rechazo` (ver resueltos). El hueco sigue en los tickets: octubre 7,631 de 7,773 sin vaciado (98%); b03, b06 y b08 igual.'),
  fila(2, '**Reactivar `betamega_02_pks_ticketcorrida`** (detenida desde el 9-jul-2026) y corregir el deadlock de `RunDaily_betaMEGA_00_Corridas` (tabla temporal `betamega_temp_seqcorridasrechazo` que ya existe). Es la causa probable de que b01 no registre rechazo desde julio.',
    'Corridas con fecha de la semana en curso; `betamega_00_logerrores` sin errores 1213/1050.',
    'IT', PEND, 'Sin cambio: último registro 9-jul 10:09. b01 sigue con 0 rechazos registrados ago–oct; la Torre ya toma ese rechazo de `ia_fasa.rechazo`.'),
  fila(16, '**Registro de desmoldeo y limpieza por pieza.** Reactivar `FHrDESM`/`FHrLIMP` en `betamega_08_ticketrutacritica` o dar otra fuente con ticket + fecha/hora de desmoldeo y limpieza. Sin esto no se mide vaciado→desmoldeo ni desmoldeo→limpieza, ni cuántas piezas están terminadas.',
    'Desmoldeo y limpieza registrados para ≥95% de las piezas vaciadas de la semana.',
    'IT', PEND, 'Desmoldeo: 10 piezas en octubre. Desde el 12-oct solo 131 de 2,407 tickets vaciados (5%) aparecen en la tabla.'),
];
const integridad = [
  fila(4, '**Quitar el duplicado de la semana 202640** en `gamamega_01_programaperiodo` y `gamamega_01_paretopesoperiodo` (dos cargas con valores distintos).',
    'Una fila por Periodo + No_Parte; `PrcContribucion` de 202640 suma 1.0.', 'IT / CSC', PEND,
    'Sin cambio: 17 partes repetidas; el Pareto de 202640 sigue sumando 2.0.'),
  fila(5, '**Quitar duplicados por llave (2026)** en `betamega_01_rechazosbyidticket`, `ticketcorrida`, `aleks_idtickethistorial`, `corridasrechazo`, `cargasmetalicas` e `idticketsidmuestras`.',
    '0 filas duplicadas por llave en cada tabla para 2026.', 'IT', PEND,
    'Solo 2026, al 28-oct: b01 165 filas de más (19 el 27-oct; no traen fecha de vaciado ni de rechazo, así que no afectan a la Torre); aleks bajó a 26 tickets repetidos (338 el 27-oct; 10 con colada distinta); ticketcorrida 1,687; corridasrechazo 561; cargas 3 coladas (38 combinaciones, máx. ×10); idticketsidmuestras 289 tickets con dos análisis.'),
  fila(11, '**Hueco de `betamega_00_coladaprocesodemoras`** del 18-ago al 29-sep-2026: explicar y, si se puede, recuperar.',
    'Coladas de ese periodo cargadas, o confirmación de que no hay dato.', 'IT', PEND, 'Sin cambio: 8 coladas en septiembre.'),
  fila(12, '**Reanudar `gamamega_02_recomendacionescontrol`** (protocolos de acción que usa Alertas en la Torre).',
    'Período de la semana en curso.', 'IT / CSC', PEND,
    'Sin cambio: sigue en la semana 202635.'),
];
const catalogos = [
  fila(20, '**Llave estructurada análisis químico → colada.** `IdAnalisisQuimico` de `idticketsidmuestras` tiene el mismo hueco jul–oct (21 de 516 coladas de octubre). La Torre liga ahora por el texto libre del campo Note del SpectroMAX ("FINAL" + colada + clase + horno): coincide con la liga oficial en 344 de 346 coladas de mayo y cubre ~52–59% de las coladas, pero depende de lo que teclee el operador. Además la clase del Note no coincide con la de las piezas en 123 de 2,376 coladas (5%), ~25 de ellas entre gris y nodular.',
    'Columnas de colada y horno en la vista de análisis (o `IdAnalisisQuimico` poblado para ≥95% de las coladas); regla escrita de qué clase manda cuando Note y piezas difieren.', 'IT / Calidad', PEND, 'Sin cambio.'),
];
const reglas = [
  fila(10, '**Tolerancias y límites.** Tolerancia de ferroaleaciones en cargas (hoy 0 en el 100%); base de `betamega_07_cargametalicosprc`; límites de AFS (48–54) y de granulometría (% retenido malla 14, 16 y PAN) en `variablesdecontrol`.',
    '`PrcTolerancia` > 0 en ferroaleaciones; porcentajes que sumen 100% ±2%; límites AFS y de granulometría confirmados por Calidad.', 'Calidad / IT', PEND,
    'Solo 2026: 36,068 de 36,068 cargas de ferroaleación con tolerancia 0 (45% "fuera"); AFS 48–54: 129 de 130 mediciones fuera desde el 13-oct.'),
  fila(13, '**Regla de retornos ≤ 30% (PC-1):** qué materiales cuentan como retorno, si aplica a gris y nodular, y si las recetas de Corex deben respetarla. Tolerancia numérica para OPE-FUS-005.',
    'Respuesta escrita con la lista de claves de retorno y el límite por clase de hierro; tolerancia de pesaje en kg o %.', 'Calidad', PEND, 'Sin respuesta. En 2026 el retorno promedio en carga es 32%.'),
  fila(14, '**Estándar del análisis térmico ITACA** (TBL-OPE-IIP-001/002/003, citadas en OPE-FUS-010): límite por alerta (Porosity, MacroShrinkage, Cementite, Nodularization, InverseChill) y por clase de hierro. Además, una llave copa→colada: columna de colada u horno en `itaca_acqresults`, o `idItaca` poblado en `idticketsidmuestras` (absorbe el antiguo tema 9).',
    'Tablas TBL-OPE-IIP con límites numéricos; llave copa→colada para ≥95% de las coladas.', 'Calidad / Metalurgia / IT', PEND,
    'Sin respuesta. La Torre usa límites provisionales (≥70 verde, ≥50 ámbar, <50 rojo) y liga la copa por hora (~85% de coladas).'),
  fila(15, '**Ventana de la tasa del pronóstico semanal de CSC** (`gamamega_01_paretopesoperiodo`): documentarla y actualizarla. Caso 42091101: CSC espera 24.3%, la Torre 8.2%; la parte bajó de 20–65% (2025) a 4–8% (2026) tras el rediseño de DEPRESION.',
    'Ventana y método documentados; tasa recalculada con historia de 2026, o justificación de mantener la de 2025.', 'CSC', PEND,
    'Sin respuesta. Semana 43: 45 de 77 partes difieren 5 puntos o más.'),
  fila(19, '**Cómo leer la especificación química de `corex_test.clases`.** Cada elemento trae centro (`_F`) y tolerancia (`_Fl`); el fósforo es el único con tolerancia 0 (P = 0.10 en gris, 0.04 en nodular). Leído como "0.10 ± 0" marcaba fuera de especificación 2,316 de 2,376 coladas de abr–oct 2026, con valores reales de 0.02–0.03. Desde el 26-oct la Torre lo trata como máximo.',
    'Respuesta escrita de Calidad: P es máximo (o el rango correcto); de preferencia columnas de mínimo y máximo por elemento.', 'Calidad', PEND, 'Sin respuesta.'),
];
const infra = [
  fila(17, '**Índices y estabilidad del servidor de BD.** `itaca_acqresults` (8.9 millones de filas) no tiene ningún índice y `ticketsconvaciadodirecto` no tiene índice en `FHrVaciado`: consultas simples tardan de 12 a 30 s. Durante pruebas del 25-oct MySQL cortó conexiones dos veces bajo carga (errores 2003 "Can\'t connect" y 2013 "Lost connection"). Sugerencia: índices en `itaca_acqresults (aResDate, aResNum)`, `ticketsconvaciadodirecto (FHrVaciado)` e `ia_fasa.rechazo (idticket, fecha)`, y revisar límites de conexiones y timeouts.',
    'Índices creados (o explicación de por qué no); consulta de un día de ITACA o de vaciados en menos de 2 s; sin cortes de conexión en una semana de uso normal de la Torre.', 'IT', PEND, '28-oct: `ia_fasa.rechazo` tampoco tiene índices; cruzarla con `ticketsconvaciadodirecto` tardaba ~90 s y la Torre tuvo que rearmar la consulta (0.2 s).'),
];
const resueltos = [
  fila(7, '**Catálogo de modelos** con peso, cliente, rango de temperatura, clase y estatus (antes `corex_test.modelos`, congelada desde el 11-mar-2026).',
    'Las partes producidas en 2026 presentes, con peso y rango de temperatura.', 'IT', RES,
    'IT copió el catálogo a `ia_fasa.modelos` el 27-oct (2,117 modelos, con cliente, clase y estatus). Están las 353 partes de 2026; quedan 5 sin peso y 25 sin rango de temperatura (captura en Corex).'),
  fila(8, '**Pedidos/embarques** (antes `piso_ia.fdbase`, congelada el 9-mar-2026).',
    'Embarques posteriores a marzo de 2026.', 'IT', RES,
    '`ia_fasa.fdbase` con 43,359 filas y datos al 26-oct. Falta que se cargue a diario (no hay datos del 27 ni del 28).'),
  fila(18, '**Tipo de rechazo** (Producción / Maquinado / Prueba) para el costo de no-calidad.',
    'Tipo de rechazo disponible para el rechazo interno de 2026.', 'IT', RES,
    '`ia_fasa.rechazo` trae `tipo_rech` e `idticket` en el 100% de las filas y cuadra ticket por ticket con el rechazo interno de b01 (557 de 557). La Torre ya lo usa para jul–oct 2026 y para el costo de no-calidad (solo Producción). Falta que se cargue a diario (último dato 26-oct).'),
];

// Anexo: historial por tema
const WH = [{ w: 500, h: '#' }, { w: 2600, h: 'Tema' }, { w: 1180, h: 'Estado' }, { w: 9400, h: 'Historial (fechas de revisión contra la base de datos)' }];
const h = (n, tema, est, ...lineas) => [String(n), `**${tema}**`, est, lineas.join('\n')];
const historial = [
  h(1, 'Liga ticket → vaciado', PEND,
    '**13-oct:** 99% de los tickets jul–oct sin vaciado.',
    '**26-oct:** 97–99%, sin cambio. **27-oct:** octubre 6,875 de 7,063 sin vaciado.',
    '**28-oct:** el rechazo jul–oct ya se liga por ticket con `ia_fasa.rechazo`; octubre 7,631 de 7,773 tickets sin vaciado (98%).'),
  h(2, '`ticketcorrida` y deadlock', PEND,
    '**13-oct:** parada desde el 9-jul; deadlocks 1213/1050 el 2 y 3-oct.',
    '**25 al 28-oct:** sin cambio; sin errores nuevos en `logerrores` desde el 3-oct.'),
  h(4, 'Semana 202640 duplicada', PEND,
    '**13-oct:** 17 partes repetidas; Pareto suma 2.0. **25 al 28-oct:** sin cambio.'),
  h(5, 'Duplicados por llave', PEND,
    '**13-oct:** duplicados en b01, `ticketcorrida`, `aleks`, `corridasrechazo`, `cargasmetalicas` e `idticketsidmuestras` (histórico completo).',
    '**25-oct:** b01 sin duplicados (16 → 0). **26-oct:** b01 regresó a 152 filas.',
    '**27-oct (solo 2026):** b01 19; `ticketcorrida` 1,687; `aleks` 338 tickets (10 con colada distinta); `corridasrechazo` 550; cargas 3 coladas; `idticketsidmuestras` 289.',
    '**28-oct (solo 2026):** b01 165 (sin fechas); `aleks` 26 (10 con colada distinta); `corridasrechazo` 561; el resto igual.'),
  h(7, 'Catálogo de modelos', RES,
    '**13-oct:** `corex_test.modelos` congelada desde el 11-mar. **27-oct:** de 353 partes de 2026, 6 sin modelo, 19 sin peso y 37 sin rango de temperatura.',
    '**28-oct:** IT lo copió a `ia_fasa.modelos`; las 353 partes presentes, 5 sin peso y 25 sin rango. Resuelto.'),
  h(8, 'Pedidos/embarques', RES,
    '**13-oct:** `piso_ia.fdbase` congelada el 9-mar. **27-oct:** se acotó a pedidos (la química pasó al tema 20).',
    '**28-oct:** `ia_fasa.fdbase` con datos al 26-oct. Resuelto; falta la carga diaria.'),
  h(10, 'Tolerancias y límites', PEND,
    '**13-oct:** ferroaleaciones con tolerancia 0 en el 100%; AFS deja fuera casi todas las mediciones.',
    '**25-oct:** se agregó la granulometría (58–100% fuera). **27-oct:** 35,815 cargas con tolerancia 0 (44% "fuera"); AFS 110 de 110 fuera.',
    '**28-oct:** 36,068 cargas con tolerancia 0 (45% "fuera"); AFS 129 de 130 fuera.'),
  h(11, 'Hueco de demoras ago–sep', PEND,
    '**13-oct:** sin coladas del 18-ago al 29-sep. **25 al 28-oct:** sin cambio (8 coladas en septiembre).'),
  h(12, '`recomendacionescontrol`', PEND,
    '**13-oct:** detenida en la semana 202635. **27-oct:** se retiraron las fechas anteriores a 2015 de `programa3dias` (registros de 2013–2014). **28-oct:** sin cambio.'),
  h(13, 'Retornos ≤ 30% (PC-1)', PEND, '**14-oct:** tema nuevo; retorno promedio 2026 de 32%. **28-oct:** sin respuesta.'),
  h(14, 'Estándar ITACA y llave copa → colada', PEND,
    '**25-oct:** tema nuevo. **27-oct:** absorbe el antiguo tema 9 (`idItaca` en 1,123 tickets de 2026). **28-oct:** sin respuesta.'),
  h(15, 'Tasa del pronóstico CSC', PEND, '**25-oct:** tema nuevo; semana 43, 45 de 77 partes difieren 5 puntos o más. **28-oct:** sin respuesta.'),
  h(16, 'Desmoldeo y limpieza', PEND,
    '**25-oct:** tema nuevo; desde el 12-oct, 168 de 1,881 piezas vaciadas (9%) en la tabla.',
    '**26-oct:** el desmoldeo volvió el 25-oct, 7 piezas en octubre; 150 de 2,088 (7%).',
    '**28-oct:** 10 desmoldes en octubre; 131 de 2,407 tickets vaciados (5%).'),
  h(17, 'Índices y estabilidad', PEND,
    '**25-oct:** tema nuevo; cortes de conexión 2003/2013. **26-oct:** la lista de coladas de Control del Proceso tarda ~10 s.',
    '**28-oct:** `ia_fasa.rechazo` sin índices (cruce de ~90 s, rearmado a 0.2 s).'),
  h(18, 'Tipo de rechazo', RES,
    '**25-oct:** tema nuevo. **27-oct:** baja prioridad (maquinado 3.8% del rechazo interno de ene–mar).',
    '**28-oct:** `ia_fasa.rechazo` trae `tipo_rech`; la Torre ya lo usa. Resuelto; falta la carga diaria.'),
  h(19, 'Especificación química (fósforo)', PEND, '**26-oct:** tema nuevo. **28-oct:** sin respuesta.'),
  h(20, 'Llave análisis químico → colada', PEND, '**26-oct:** tema nuevo. **28-oct:** sin cambio.'),
];

const doc1 = doc([
  ...titulo('Temas pendientes con IT, Calidad y CSC', 'Torre de Control FASA · corte 28 de octubre de 2026 · solo datos de 2026 · Preparado por Juan Carlos Alvarez (MindBridge)'),
  P('De 17 temas, **3 quedaron resueltos** el 28-oct (7 catálogo de modelos, 8 pedidos y 18 tipo de rechazo) con las tablas que IT copió el 27-oct a `ia_fasa` (`rechazo`, `fdbase` y `modelos`). **Quedan 14 pendientes.** Los tres primeros siguen siendo los más urgentes: sin ellos la Torre no tiene puntos de control completos ni piezas terminadas de julio en adelante.'),
  P('**Pendiente nuevo para los resueltos:** `ia_fasa.rechazo` y `ia_fasa.fdbase` se quedaron en el 26-oct y `modelos` en la copia del 27-oct; no hay carga del 27 ni del 28. Hay que confirmar que IT programó su actualización diaria, o la Torre volverá a quedarse sin rechazo reciente.'),
  P('Se conserva la numeración original; los temas 3, 6 y 9 se retiraron el 27-oct (ver al final). Cada tema trae un criterio para darlo por resuelto y su estado verificado contra la base de datos el 28-oct-2026. El historial de cada tema está en el anexo.'),
  H2('A. Urgentes: bloquean puntos de control y avance'),
  table(W1, urgentes), SP(),
  H2('B. Integridad y duplicados'),
  table(W1, integridad), SP(),
  H2('C. Ligas faltantes'),
  table(W1, catalogos), SP(),
  H2('D. Reglas y estándares'),
  table(W1, reglas), SP(),
  H2('E. Infraestructura de la base de datos'),
  table(W1, infra), SP(),
  H2('F. Resueltos al 28-oct-2026'),
  table(W1, resueltos), SP(),
  P('**Temas retirados al acotar a 2026:** **3** (ticket heredado: en 2026 el 8% de los tickets son heredados y el rechazo da 16.2% por ticket contra 16.7% por vaciado, 0.5 puntos, no cambia decisiones); **6** (tickets con dos coladas o varios vaciados: 0 y 4 en 2026); **9** (`idItaca`, `idTension`, `idArenas`: la llave de ITACA pasó al tema 14; tensión y arenas no se usan).', { run: { size: 18 } }),
  P('**Mejoras de captura sugeridas** (sin prioridad): catálogo cerrado para `Puesto` en variables de control (hoy más de 30 variantes de "pintor"); unificar los 3 `idDefecto` con dos nombres (53, 55 y 57); agregar PERDIDA POR COMPLEMENTO y MODELOS al catálogo de defectos.', { run: { size: 18 } }),
  H1('Anexo. Historial por tema'),
  P('Lo que se midió en cada revisión, del 13 al 28-oct-2026. Sirve para ver qué avanzó, qué retrocedió y qué no se ha movido.'),
  table(WH, historial),
], true, 'Temas pendientes con IT, Calidad y CSC');

// ═════════════════════════════════════════════════════════════════════════════
// 2. AUDITORÍA DE DATOS — ACTUALIZACIÓN
// ═════════════════════════════════════════════════════════════════════════════
const MEJ = { color: VERDE, bold: true }, PEOR = { color: ROJO, bold: true }, IGUAL = { color: GRIS };
const t = (s, o) => ({ t: s, ...o });

const disp = [
  ['`ia_fasa.rechazo` (nueva, copiada el 27-oct)', 'No existía (`piso_ia.rechazo` congelada el 9-mar)', '26-oct · 26,114 filas, con `tipo_rech` e `idticket`; sin carga el 27 ni el 28', t('Nueva', MEJ)],
  ['`ia_fasa.fdbase` (nueva)', 'No existía (`piso_ia.fdbase` congelada el 9-mar)', '26-oct · 43,359 filas; sin carga el 27 ni el 28', t('Nueva', MEJ)],
  ['`ia_fasa.modelos` (nueva)', 'No existía (`corex_test.modelos` congelada el 11-mar)', 'Copia del 27-oct · 2,117 modelos', t('Nueva', MEJ)],
  ['`betamega_10_variablesproceso*` (8 tablas, nuevas)', 'No existían', 'Creadas el 27-oct · 112,434 filas la principal; aún sin evaluar', t('Nueva', IGUAL)],
  ['`betamega_02_pks_ticketsorigen`', '13-oct · 99% sin vaciado jul–oct', '28-oct · 98% sin vaciado en octubre', t('Sin cambio', IGUAL)],
  ['`betamega_01/03/06` y `ticketcoladacargametalica`', '12-oct · 14 / 31 / 79 / 151 tickets por mes (jul–oct)', '28-oct · b01: 14 / 31 / 79 / 312; b03 y b06: 14 / 31 / 79 / 148', t('Sin cambio', IGUAL)],
  ['`betamega_08_ticketrutacritica`', '12-oct · 14 / 31 / 79 / 151', 'Vaciado 27-oct; **desmoldeo 10 piezas en octubre**; limpieza 155 / 424 / 335 / 496 por mes', t('Empeoró', PEOR)],
  ['`betamega_07_carga*` (3 tablas)', '12-oct · 13 / 28 / 79 / 150', '27-oct · 13 / 28 / 79 / 147', t('Sin cambio', IGUAL)],
  ['`betamega_02_pks_ticketcorrida`', '9-jul-2026 (parada)', '9-jul-2026 (parada)', t('Sin cambio', IGUAL)],
  ['`betamega_00_ticketsconvaciadodirecto`', '13-oct · completa', '28-oct · completa: 394 / 4,343 / 3,781 / 3,636', t('Viva', MEJ)],
  ['`betamega_aleks_idtickethistorial`', '13-oct · completa por moldeo', '28-oct · 4,364 / 4,330 / 3,833 / 3,685', t('Viva', MEJ)],
  ['`betamega_00_coladaprocesodemoras`', 'Hueco 18-ago al 29-sep', '28-oct · hueco sin recuperar', t('Sin cambio', IGUAL)],
  ['`betamega_00_cargasmetalicas`', 'Colada 86199', 'Colada 86559', t('Viva', MEJ)],
  ['`betamega_00_corridasrechazo`', '13-oct · depende de b01', '28-oct · depende de b01', t('Sin cambio', IGUAL)],
  ['`betamega_09_variablesdecontrol`', '13-oct · completa', '27-oct · completa (4,367 registros en octubre)', t('Viva', MEJ)],
  ['`itaca_acqresults`', '12-oct', '27-oct · ~4,000 muestras por mes', t('Viva', MEJ)],
  ['`gamamega_01_programaperiodo` / `paretopesoperiodo`', 'Semana 202644', 'Semana 202644', t('Sin cambio', IGUAL)],
  ['`gamamega_01_programa3dias`', '3-oct', '3-oct (25 días sin carga)', t('Sin cambio', IGUAL)],
  ['`gamamega_02_recomendacionescontrol`', 'Semana 202635', 'Semana 202635', t('Sin cambio', IGUAL)],
  ['`betamega_00_logerrores`', 'Deadlocks 2–3 oct', 'Sin errores nuevos desde el 3-oct', t('Sin cambio', IGUAL)],
  ['`corex_test.*`, `piso_ia.*`', '~11-mar-2026 (congeladas)', '11-mar / 9-mar-2026 (congeladas; reemplazadas por las copias en `ia_fasa`)', t('Sin cambio', IGUAL)],
];
const cob = [
  ['Tickets en `ticketsorigen`', '5,043', '5,156', '2,887', '7,789', '7,570', '7,773'],
  ['  % sin vaciado', '32%', '34%', t('99.5%', PEOR), t('99.6%', PEOR), t('99.0%', PEOR), t('98.2%', PEOR)],
  ['Piezas vaciadas reales (`ticketsconvaciadodirecto`)', '3,431', '3,201', '394', '4,343', '3,781', '3,636'],
  ['Moldeadas (`aleks`)', '3,387', '3,963', '4,364', '4,330', '3,833', '3,685'],
  ['Tickets en derivadas (b01 / b03 y b06)', '3,506', '3,446', t('14', PEOR), t('31', PEOR), t('79', PEOR), t('312 / 148', PEOR)],
  ['Rechazo registrado en b01', '382', '524', t('1', PEOR), t('0', PEOR), t('0', PEOR), t('0', PEOR)],
  ['Rechazo de piso (`ia_fasa.rechazo`, por fecha de rechazo)', '419', '491', t('616', MEJ), t('695', MEJ), t('545', MEJ), t('552**', MEJ)],
  ['Limpieza registrada (b08)', '3,357', '3,528', '155', '424', '335', '496'],
  ['Desmoldeo registrado (b08)', '3,464', '3,560', t('1', PEOR), t('3', PEOR), t('0', PEOR), t('10', PEOR)],
  ['Coladas (`coladaprocesodemoras`)', '698', '736', '760', '411', t('8', PEOR), '615'],
  ['Variables de control (b09)', '4,192', '4,577', '4,556', '4,566', '3,890', '4,367'],
  ['Muestras ITACA', '3,300', '3,984', '4,377', '4,515', '4,266', '3,436'],
];
const integ = [
  ['Duplicados en `betamega_01_rechazosbyidticket`', '165 filas de más (26,079 tickets)', 'Sin fecha de vaciado ni de rechazo: no afectan a la Torre. 0 el 25-oct, 19 el 27-oct', '5'],
  ['Filas de más en `ticketcorrida`', '1,687 (24,452 tickets)', 'Tabla parada desde el 9-jul', '5'],
  ['Tickets repetidos en `aleks`', '26 (10 con colada distinta)', 'De 39,409 tickets moldeados; 338 el 27-oct', '5'],
  ['Duplicados en `corridasrechazo`', '561', 'Depende de b01', '5'],
  ['Duplicados en `cargasmetalicas`', '3 coladas (38 combinaciones, máx. ×10)', 'Afecta PC-1 de esas coladas', '5'],
  ['Tickets con dos análisis químicos', '289', '', '5'],
  ['Semana 202640 duplicada', '17 partes; Pareto suma 2.0', 'Plan semanal y pronóstico CSC', '4'],
  ['Temperaturas de vaciado en 0 °C', '483 de 24,161', 'La Torre las excluye de PC-4', '—'],
  ['Temperaturas > 1,550 °C (máx. capturada)', '6 (13,890 °C)', 'Captura sin validar', '—'],
  ['Rango de temperatura con mín ≥ máx', '49', 'Catálogo de modelos', '—'],
  ['Limpieza antes que desmoldeo (b08)', '1,996 de 25,868', '', '16'],
  ['Limpieza marcada sin vaciado (b08)', '1,431', '', '16'],
  ['Ferroaleaciones con tolerancia 0', '100% (36,068); 45% "fuera"', '', '10'],
  ['AFS con límite 48–54 fuera de límite', '129 de 130 desde el 13-oct', '', '10'],
  ['`idDefecto` con dos nombres', '3', 'Mejora de captura', '—'],
  ['Partes sin peso / sin rango de temp. (`ia_fasa.modelos`)', '5 / 25 de 353', 'Antes, en `corex_test.modelos`: 6 sin modelo, 19 sin peso, 37 sin rango', '7 (resuelto)'],
  ['Tickets con dos coladas (b03) / varios vaciados (b06)', '0 / 4', 'Ya no es problema en 2026', 'retirado (6)'],
  ['Tickets heredados', '8%; rechazo 16.2% por ticket vs 16.7% por vaciado', 'Diferencia menor', 'retirado (3)'],
];

const consultas = [
  ['Hueco julio–octubre: tickets sin vaciado por mes', `
SELECT AAMM, COUNT(*) AS tickets, SUM(FHrVaciado IS NULL) AS sin_vaciado
FROM ia_fasa.betamega_02_pks_ticketsorigen
WHERE AAMM BETWEEN 2601 AND 2612
GROUP BY AAMM;`],
  ['Cobertura de una fuente por mes (cambiar tabla y columna de fecha)', `
SELECT DATE_FORMAT(FHrVaciado, '%Y-%m') AS mes, COUNT(DISTINCT idTicket) AS tickets
FROM ia_fasa.betamega_00_ticketsconvaciadodirecto
WHERE FHrVaciado >= '2026-01-01'
GROUP BY mes;
-- repetir con betamega_01_rechazosbyidticket, betamega_03_coladasproceso, betamega_06_vaciado`],
  ['Frescura de las tablas nuevas de ia_fasa', `
SELECT (SELECT MAX(fecha) FROM ia_fasa.rechazo)             AS rechazo,
       (SELECT MAX(fecha) FROM ia_fasa.fdbase)              AS fdbase,
       (SELECT MAX(FechaModificacion) FROM ia_fasa.modelos) AS modelos;`],
  ['Rechazo de piso por mes y tipo', `
SELECT DATE_FORMAT(fecha, '%Y-%m') AS mes, tipo_rech, COUNT(*) AS piezas
FROM ia_fasa.rechazo
WHERE fecha >= '2026-01-01'
GROUP BY mes, tipo_rech;`],
  ['Duplicados por llave en 2026 (cambiar tabla)', `
SELECT COUNT(*) - COUNT(DISTINCT idTicket) AS filas_de_mas, COUNT(DISTINCT idTicket) AS tickets
FROM ia_fasa.betamega_01_rechazosbyidticket
WHERE anio = 2026;
-- igual con betamega_02_pks_ticketcorrida`],
  ['Semana 202640 duplicada', `
SELECT Periodo, TRIM(No_Parte), COUNT(*)
FROM ia_fasa.gamamega_01_programaperiodo
GROUP BY 1, 2 HAVING COUNT(*) > 1;
SELECT Periodo, SUM(PrcContribucion) FROM ia_fasa.gamamega_01_paretopesoperiodo GROUP BY Periodo;`],
  ['Duplicados en cargas metálicas', `
SELECT COUNT(DISTINCT IdColada) AS coladas, COUNT(*) AS combinaciones, MAX(n) AS max_repeticiones
FROM (SELECT IdColada, Tipo, Clave, COUNT(*) AS n
      FROM ia_fasa.betamega_00_cargasmetalicas
      GROUP BY 1, 2, 3 HAVING COUNT(*) > 1) z;`],
  ['Temperaturas inválidas en 2026', `
SELECT COUNT(*), SUM(vt_TemperaturaVaciado = 0), SUM(vt_TemperaturaVaciado > 1550),
       MAX(vt_TemperaturaVaciado), SUM(m_tmin >= m_tmax)
FROM ia_fasa.betamega_06_vaciado
WHERE FHrVaciado >= '2026-01-01';`],
  ['Secuencia de etapas en 2026', `
SELECT COUNT(*), SUM(FHrLIMP < FHrDESM), SUM(bLIMP = 1 AND bVACI = 0)
FROM ia_fasa.betamega_08_ticketrutacritica
WHERE anio = 2026;`],
  ['Piezas vaciadas desde el 12-oct que aparecen en la ruta crítica', `
SELECT COUNT(*) AS vaciados, SUM(b.idTicket IS NOT NULL) AS en_b08
FROM ia_fasa.betamega_00_ticketsconvaciadodirecto v
LEFT JOIN ia_fasa.betamega_08_ticketrutacritica b ON b.idTicket = v.idTicket
WHERE v.FHrVaciado >= '2026-10-12';`],
  ['Partes de 2026 sin peso o sin rango de temperatura', `
SELECT COUNT(*), SUM(m.NoParte IS NULL), SUM(IFNULL(m.PesoKgs, 0) = 0),
       SUM(IFNULL(m.TemperaturaMin, 0) = 0 OR IFNULL(m.TemperaturaMax, 0) = 0)
FROM (SELECT DISTINCT TRIM(No_Parte) AS p FROM ia_fasa.betamega_02_pks_ticketsorigen WHERE anio = 2026) o
LEFT JOIN (SELECT TRIM(NoParte) AS NoParte, MAX(PesoKgs) AS PesoKgs,
                  MAX(TemperaturaMin) AS TemperaturaMin, MAX(TemperaturaMax) AS TemperaturaMax
           FROM ia_fasa.modelos GROUP BY TRIM(NoParte)) m ON m.NoParte = o.p;`],
];

const doc2 = doc([
  ...titulo('Auditoría de calidad y disponibilidad de datos — actualización',
    'Torre de Control FASA · corte 28-oct-2026 · solo datos de 2026 (disponibilidad, cobertura e integridad al 28-oct 14:19) · Preparado por Juan Carlos Alvarez (MindBridge)'),
  H1('1. Resumen'),
  P('El 27-oct IT copió a `ia_fasa` el registro de rechazo de piso (`rechazo`), los pedidos (`fdbase`) y el catálogo de modelos (`modelos`). Con eso **quedaron resueltos 3 temas** (7, 8 y 18) y **la Torre ya tiene rechazo real de julio a octubre de 2026**: 14.0%, 12.9%, 12.1% y 12.0% mensual (octubre todavía incompleto por el retraso de captura). El hueco de tickets sigue: el **98% de los tickets de octubre no tiene liga a su vaciado**, y el desmoldeo casi no se registra (10 piezas en octubre).'),
  B('**Nuevo:** `ia_fasa.rechazo` y `fdbase` se quedaron en el 26-oct y `modelos` en la copia del 27-oct; no hay carga del 27 ni del 28. Hay que confirmar con IT que la actualización diaria está programada.'),
  B('**Sigue igual:** hueco jul–oct en tickets, `ticketcorrida` parada desde el 9-jul, hueco de coladas del 18-ago al 29-sep, semana 202640 duplicada, `programa3dias` y recomendaciones detenidas.'),
  B('**Mejoró:** rechazo jul–oct disponible; catálogo de modelos y pedidos al 26/27-oct; tickets repetidos en `aleks` bajaron de 338 a 26.'),
  B('**Empeoró:** los duplicados de `betamega_01` subieron de 19 a 165 filas en 2026 (no traen fechas, no afectan a la Torre); `corridasrechazo` llegó a 561; desmoldeo y limpieza solo registran ~5% de los tickets vaciados.'),
  B('**Lo que sí está completo y vivo:** piezas vaciadas (`ticketsconvaciadodirecto`), moldeo (`aleks`), cargas, variables de control de arenas, análisis térmico ITACA y el programa semanal.'),
  H1('2. Alcance y método'),
  P('Mismo método que el 13-oct: tablas y vistas del servidor `200.76.23.164` con el usuario de solo lectura `sapiens` (`ia_fasa`, `corex_test`, `piso_ia`, `test`). Solo se consideran datos de 2026. Corte al 28-oct 14:19. Las consultas para reproducir cada cifra están en el anexo.'),
  H1('3. Disponibilidad: qué está vivo y qué está congelado'),
  table([{ w: 2900, h: 'Tabla' }, { w: 2500, h: '13-oct' }, { w: 3300, h: '28-oct' }, { w: 1380, h: 'Cambio' }], disp), SP(),
  H2('Cobertura mensual 2026'),
  P('Mayo y junio son la referencia de un mes normal. De julio en adelante la producción existe (vaciado y moldeo completos), pero las tablas derivadas casi no la ven. El rechazo de piso ya está completo desde julio.'),
  table([{ w: 3780, h: 'Fuente' }, { w: 1050, h: 'May' }, { w: 1050, h: 'Jun' }, { w: 1050, h: 'Jul' }, { w: 1050, h: 'Ago' }, { w: 1050, h: 'Sep' }, { w: 1050, h: 'Oct*' }], cob),
  P('* Octubre hasta el 28 a las 14:19. ** Rechazo de piso hasta el 26-oct.', { run: { size: 16, color: GRIS } }),
  H1('4. Integridad 2026 (al 28-oct)'),
  table([{ w: 3800, h: 'Problema' }, { w: 2700, h: 'Solo 2026' }, { w: 2500, h: 'Nota' }, { w: 1080, h: 'Tema' }], integ), SP(),
  H1('5. Hallazgos'),
  H2('Nuevos al 28-oct'),
  B('**`ia_fasa.rechazo` cuadra con b01.** Coincide ticket por ticket con el rechazo interno de `betamega_01` en el periodo en que ambas tienen datos (557 de 557) y trae `idticket` en el 100% de las filas. Solo trae rechazo **interno**: desde julio de 2026 la Torre no tiene rechazo externo.'),
  B('**El rechazo se captura ~11 días después del vaciado** en promedio (67% dentro de 7 días). Las semanas y el mes más recientes siempre salen bajos hasta que se completa la captura.'),
  B('**Hueco de vaciado del 23-jun al 28-jul.** `ticketsconvaciadodirecto` no tiene piezas en esas fechas; la Torre las toma de `aleks` (fecha de moldeo) solo en ese periodo. Con eso julio quedó con 4,267 piezas y 14.0% de rechazo.'),
  B('**Ninguna de las tablas nuevas tiene índices.** Cruzar `ia_fasa.rechazo` con `ticketsconvaciadodirecto` tardaba ~90 s; la Torre tuvo que rearmar la consulta (0.2 s) (tema 17).'),
  H2('Del 14 al 27-oct (siguen vigentes)'),
  B('**`betamega_08_ticketrutacritica` casi dejó de registrar.** Desde el 12-oct solo 131 de 2,407 tickets vaciados (5%) aparecen; desmoldeo con 10 piezas en octubre. La Torre mide avance y ritmo con piezas vaciadas.'),
  B('**ITACA sí se puede usar, ligado por hora.** `itaca_acqresults` no trae colada ni horno; con la regla de 10–15 min antes de liberar la colada se asigna al ~85% de las coladas. Faltan los límites oficiales (tema 14).'),
  B('**Arenas: LOI fuera de límite en casi todas las líneas.** AFS (48–54) y la granulometría tienen límites que no corresponden a la arena real (tema 10).'),
  B('**El pronóstico semanal de CSC usa tasas desactualizadas** (tema 15).'),
  B('**Programa semanal contra vaciado real:** cada semana se vacía 33–61% de lo programado; hay que confirmar con Planeación qué incluye el programa.'),
  B('**Retornos en carga:** retorno promedio de 32% en 2026 contra el límite de 30% (tema 13).'),
  B('**Química:** la liga oficial análisis→colada tiene el mismo hueco; la Torre liga por el campo Note del SpectroMAX (~52–59% de las coladas) y trata el fósforo como máximo (temas 19 y 20).'),
  H1('6. Estado de los temas'),
  P('De **17 temas de 2026**, **3 resueltos** (7, 8 y 18) y **14 pendientes**. El detalle, los criterios de aceptación y el historial de cada tema están en "Temas pendientes con IT, Calidad y CSC" del 28-oct-2026.'),
  H1('Anexo. Consultas para reproducir las cifras'),
  P('Todas corren con el usuario de solo lectura. Sirven también como prueba de aceptación después de cada corrección.'),
  ...consultas.flatMap(([tit, q]) => [P(`**${tit}**`, { par: { spacing: { before: 160, after: 60 } } }), ...SQL(q)]),
], false, 'Auditoría de datos — actualización 28-oct-2026 (solo 2026)');

(async () => {
  const f1 = path.join(OUT_DIR, 'Temas_Pendientes_IT_Calidad_CSC_2026-10-28.docx');
  const f2 = path.join(OUT_DIR, 'Auditoria_Datos_Actualizacion_2026-10-28.docx');
  fs.writeFileSync(f1, await Packer.toBuffer(doc1));
  fs.writeFileSync(f2, await Packer.toBuffer(doc2));
  console.log('ok', f1, f2);
})();
