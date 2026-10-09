(function (global) {
  'use strict';

  const decoder = new TextDecoder('utf-8');

  function readU16(view, offset) { return view.getUint16(offset, true); }
  function readU32(view, offset) { return view.getUint32(offset, true); }

  function findEocd(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i -= 1) {
      if (readU32(view, i) === 0x06054B50) return i;
    }
    throw new Error('Invalid XLSX/ZIP: end record not found');
  }

  function zipIndex(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const eocd = findEocd(bytes);
    const count = readU16(view, eocd + 10);
    let offset = readU32(view, eocd + 16);
    const files = new Map();
    for (let i = 0; i < count; i += 1) {
      if (readU32(view, offset) !== 0x02014B50) throw new Error('Invalid XLSX/ZIP central directory');
      const method = readU16(view, offset + 10);
      const compressedSize = readU32(view, offset + 20);
      const size = readU32(view, offset + 24);
      const nameLen = readU16(view, offset + 28);
      const extraLen = readU16(view, offset + 30);
      const commentLen = readU16(view, offset + 32);
      const localOffset = readU32(view, offset + 42);
      const name = decoder.decode(bytes.subarray(offset + 46, offset + 46 + nameLen));
      files.set(name, { method, compressedSize, size, localOffset });
      offset += 46 + nameLen + extraLen + commentLen;
    }
    return files;
  }

  async function inflateRaw(bytes) {
    if (typeof DecompressionStream !== 'function') throw new Error('This browser cannot decompress XLSX files. Use a current Chromium/Firefox browser or import CSV.');
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  async function readEntry(allBytes, index, name) {
    const meta = index.get(name);
    if (!meta) return null;
    const view = new DataView(allBytes.buffer, allBytes.byteOffset, allBytes.byteLength);
    const off = meta.localOffset;
    if (readU32(view, off) !== 0x04034B50) throw new Error('Invalid XLSX local entry: ' + name);
    const nameLen = readU16(view, off + 26);
    const extraLen = readU16(view, off + 28);
    const start = off + 30 + nameLen + extraLen;
    const compressed = allBytes.subarray(start, start + meta.compressedSize);
    if (meta.method === 0) return compressed.slice();
    if (meta.method === 8) return inflateRaw(compressed);
    throw new Error('Unsupported XLSX ZIP compression method: ' + meta.method);
  }

  async function readText(allBytes, index, name) {
    const data = await readEntry(allBytes, index, name);
    return data ? decoder.decode(data) : '';
  }

  function xml(text) {
    const doc = new DOMParser().parseFromString(text, 'application/xml');
    const error = doc.querySelector('parsererror');
    if (error) throw new Error('Invalid XML inside XLSX');
    return doc;
  }

  function cellColumn(ref) {
    const match = String(ref || '').match(/^([A-Z]+)\d+$/i);
    if (!match) return 0;
    let col = 0;
    for (const char of match[1].toUpperCase()) col = col * 26 + char.charCodeAt(0) - 64;
    return col - 1;
  }

  function excelDate(serial) {
    const value = Number(serial);
    if (!Number.isFinite(value)) return String(serial || '');
    const millis = Math.round((value - 25569) * 86400 * 1000);
    const date = new Date(millis);
    if (Number.isNaN(date.getTime())) return String(serial || '');
    const iso = date.toISOString();
    return Math.abs(value - Math.round(value)) < 1e-9 ? iso.slice(0, 10) : iso.slice(0, 16).replace('T', ' ');
  }

  function dateStyles(stylesText) {
    const dateIds = new Set([14,15,16,17,18,19,20,21,22,27,30,36,45,46,47,50,57]);
    if (!stylesText) return [];
    const doc = xml(stylesText);
    Array.from(doc.getElementsByTagNameNS('*', 'numFmt')).forEach((node) => {
      const id = Number(node.getAttribute('numFmtId'));
      const code = (node.getAttribute('formatCode') || '').replace(/\[[^\]]+\]/g, '').replace(/"[^"]*"/g, '');
      if (/[ymdhis]/i.test(code)) dateIds.add(id);
    });
    const result = [];
    const cellXfs = doc.getElementsByTagNameNS('*', 'cellXfs')[0];
    const xfs = cellXfs ? Array.from(cellXfs.children).filter((node) => node.localName === 'xf') : [];
    xfs.forEach((node, index) => { if (dateIds.has(Number(node.getAttribute('numFmtId')))) result[index] = true; });
    return result;
  }

  function sharedStrings(text) {
    if (!text) return [];
    const doc = xml(text);
    return Array.from(doc.getElementsByTagNameNS('*', 'si')).map((si) => Array.from(si.getElementsByTagNameNS('*', 't')).map((t) => t.textContent || '').join(''));
  }

  function parseSheet(sheetText, shared, styleDates) {
    const doc = xml(sheetText);
    const matrix = [];
    const sheetData = doc.getElementsByTagNameNS('*', 'sheetData')[0];
    const rowNodes = sheetData ? Array.from(sheetData.getElementsByTagNameNS('*', 'row')) : [];
    rowNodes.forEach((rowNode) => {
      const rowNumber = Number(rowNode.getAttribute('r')) || (matrix.length + 1);
      const row = [];
      Array.from(rowNode.children).filter((node) => node.localName === 'c').forEach((cell) => {
        const ref = cell.getAttribute('r') || '';
        const col = cellColumn(ref);
        const type = cell.getAttribute('t') || '';
        const style = Number(cell.getAttribute('s') || 0);
        let value = '';
        if (type === 'inlineStr') value = Array.from(cell.getElementsByTagNameNS('*', 't')).map((n) => n.textContent || '').join('');
        else {
          const vNodes = cell.getElementsByTagNameNS('*', 'v');
          const raw = vNodes.length ? vNodes[0].textContent || '' : '';
          if (type === 's') value = shared[Number(raw)] == null ? '' : shared[Number(raw)];
          else if (type === 'b') value = raw === '1' ? 'true' : 'false';
          else if (styleDates[style] && raw !== '') value = excelDate(raw);
          else value = raw;
        }
        row[col] = String(value == null ? '' : value);
      });
      matrix[rowNumber - 1] = row;
    });
    while (matrix.length && (!matrix[matrix.length - 1] || matrix[matrix.length - 1].every((v) => !v))) matrix.pop();
    const headerRow = matrix.find((row) => row && row.some((v) => String(v || '').trim())) || [];
    const headerIndex = matrix.indexOf(headerRow);
    let lastHeaderColumn = -1;
    headerRow.forEach((value, index) => { if (String(value || '').trim()) lastHeaderColumn = index; });
    const compactHeaderRow = lastHeaderColumn >= 0 ? headerRow.slice(0, lastHeaderColumn + 1) : [];
    const headers = compactHeaderRow.map((v, i) => String(v || '').trim() || ('Column ' + (i + 1)));
    const rows = matrix.slice(headerIndex + 1).filter((row) => row && row.slice(0, headers.length).some((v) => String(v || '').trim())).map((values) => {
      const obj = Object.create(null);
      headers.forEach((header, i) => { obj[header] = String(values[i] == null ? '' : values[i]).trim(); });
      return obj;
    });
    return { headers, rows };
  }

  async function parse(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const index = zipIndex(bytes);
    const sheetNames = Array.from(index.keys()).filter((name) => /^xl\/worksheets\/sheet\d+\.xml$/i.test(name)).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    if (!sheetNames.length) throw new Error('No worksheet found in XLSX');
    const [sharedText, stylesText, sheetText] = await Promise.all([
      readText(bytes, index, 'xl/sharedStrings.xml'),
      readText(bytes, index, 'xl/styles.xml'),
      readText(bytes, index, sheetNames[0])
    ]);
    const parsed = parseSheet(sheetText, sharedStrings(sharedText), dateStyles(stylesText));
    parsed.sheet = sheetNames[0];
    return parsed;
  }



  function xmlEscape(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&apos;');
  }

  function columnName(index) {
    let n = Number(index) + 1;
    let out = '';
    while (n > 0) {
      const r = (n - 1) % 26;
      out = String.fromCharCode(65 + r) + out;
      n = Math.floor((n - 1) / 26);
    }
    return out;
  }

  function worksheetXml(rows, headers) {
    const all = [headers].concat((rows || []).map((row) => headers.map((h) => row[h] == null ? '' : String(row[h]))));
    const body = all.map((values, r) => {
      const cells = values.map((value, c) => {
        const ref = columnName(c) + (r + 1);
        return '<c r="' + ref + '" t="inlineStr"><is><t xml:space="preserve">' + xmlEscape(value) + '</t></is></c>';
      }).join('');
      return '<row r="' + (r + 1) + '">' + cells + '</row>';
    }).join('');
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
      '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + body + '</sheetData></worksheet>';
  }

  async function build(rows, headers, options) {
    if (!global.ZipLite || typeof global.ZipLite.createBlob !== 'function') throw new Error('ZIP support is unavailable');
    const opts = Object.assign({ sheetName: 'Sheet1' }, options || {});
    const safeName = String(opts.sheetName || 'Sheet1').replace(/[\\/*?:\[\]]/g, ' ').slice(0, 31) || 'Sheet1';
    const entries = [
      { path: '[Content_Types].xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>' },
      { path: '_rels/.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>' },
      { path: 'xl/workbook.xml', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="' + xmlEscape(safeName) + '" sheetId="1" r:id="rId1"/></sheets></workbook>' },
      { path: 'xl/_rels/workbook.xml.rels', data: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>' },
      { path: 'xl/worksheets/sheet1.xml', data: worksheetXml(rows || [], headers || []) }
    ];
    const zip = await global.ZipLite.createBlob(entries);
    return new Blob([zip], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  }

  global.XlsxLite = Object.freeze({ parse, build });
})(window);
