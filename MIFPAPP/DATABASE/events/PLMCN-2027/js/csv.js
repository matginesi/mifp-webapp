(function (global) {
  'use strict';

  function parse(text) {
    const rows = [];
    let row = [];
    let field = '';
    let quoted = false;
    const input = String(text || '').replace(/^\uFEFF/, '');

    for (let i = 0; i <= input.length; i += 1) {
      const char = input[i] || '\n';
      if (quoted) {
        if (char === '"' && input[i + 1] === '"') {
          field += '"';
          i += 1;
        } else if (char === '"') {
          quoted = false;
        } else {
          field += char;
        }
        continue;
      }
      if (char === '"') quoted = true;
      else if (char === ',') { row.push(field); field = ''; }
      else if (char === '\r') continue;
      else if (char === '\n') {
        row.push(field);
        field = '';
        if (row.some(function (value) { return value !== ''; })) rows.push(row);
        row = [];
      } else field += char;
    }

    if (!rows.length) return { headers: [], rows: [] };
    const headers = rows.shift().map(function (header) { return header.trim(); });
    const objects = rows.map(function (values) {
      const object = Object.create(null);
      headers.forEach(function (header, index) {
        object[header] = String(values[index] == null ? '' : values[index]).trim();
      });
      return object;
    });
    return { headers, rows: objects };
  }

  function protectSpreadsheetFormula(value) {
    const text = String(value == null ? '' : value);
    return /^[\t\r\n ]*[=+\-@]/.test(text) ? "'" + text : text;
  }

  function cell(value, protectFormulae) {
    const text = protectFormulae ? protectSpreadsheetFormula(value) : String(value == null ? '' : value);
    return /[",\n\r]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
  }

  function serialize(rows, headers, options) {
    const settings = Object.assign({ bom: true, eol: '\r\n', finalEol: true, protectFormulae: false }, options || {});
    const columns = Array.isArray(headers) ? headers.map(String) : [];
    const data = Array.isArray(rows) ? rows : [];
    const lines = [columns.map(function (header) { return cell(header, false); }).join(',')];
    data.forEach(function (row) {
      lines.push(columns.map(function (header) { return cell(row && row[header] != null ? row[header] : '', settings.protectFormulae); }).join(','));
    });
    return (settings.bom ? '\uFEFF' : '') + lines.join(settings.eol) + (settings.finalEol ? settings.eol : '');
  }

  global.CsvUtil = Object.freeze({ parse, cell, serialize, protectSpreadsheetFormula });
})(window);
