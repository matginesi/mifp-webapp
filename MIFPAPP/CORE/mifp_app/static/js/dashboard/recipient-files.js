(function () {
  'use strict';

  var MAX_FILE_BYTES = 5 * 1024 * 1024;
  var MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024;
  var MAX_ROWS = 300;

  function parseCsv(text) {
    var rows = [];
    var row = [];
    var value = '';
    var quoted = false;
    for (var index = 0; index < text.length; index += 1) {
      var character = text[index];
      if (quoted) {
        if (character === '"' && text[index + 1] === '"') {
          value += '"';
          index += 1;
        } else if (character === '"') {
          quoted = false;
        } else {
          value += character;
        }
      } else if (character === '"') {
        quoted = true;
      } else if (character === ',' || character === ';' || character === '\t') {
        row.push(value);
        value = '';
      } else if (character === '\n') {
        row.push(value.replace(/\r$/, ''));
        if (row.some(function (cell) { return cell.trim(); })) rows.push(row);
        if (rows.length > MAX_ROWS + 1) throw new Error('Recipient file has more than ' + MAX_ROWS + ' rows.');
        row = [];
        value = '';
      } else {
        value += character;
      }
    }
    if (quoted) throw new Error('CSV contains an unterminated quoted field.');
    row.push(value.replace(/\r$/, ''));
    if (row.some(function (cell) { return cell.trim(); })) rows.push(row);
    return table(rows);
  }

  function table(rows) {
    // An email-only list has no header: keep its first recipient.
    if (rows.length && rows[0].length === 1 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(rows[0][0].trim())) rows.unshift(['email']);
    if (rows.length < 2) throw new Error('Recipient file needs a header and at least one data row.');
    if (rows.length > MAX_ROWS + 1) throw new Error('Recipient file has more than ' + MAX_ROWS + ' rows.');
    var headers = rows[0].map(function (value, index) {
      return String(value || '').trim() || 'Column ' + (index + 1);
    });
    return {
      headers: headers,
      rows: rows.slice(1, MAX_ROWS + 1).map(function (cells) {
        return headers.map(function (_, index) { return String(cells[index] || '').trim(); });
      }),
    };
  }

  function uint16(view, offset) { return view.getUint16(offset, true); }
  function uint32(view, offset) { return view.getUint32(offset, true); }

  async function unzip(arrayBuffer) {
    var bytes = new Uint8Array(arrayBuffer);
    var view = new DataView(arrayBuffer);
    var eocd = -1;
    for (var position = Math.max(0, bytes.length - 65557); position <= bytes.length - 22; position += 1) {
      if (uint32(view, position) === 0x06054b50) eocd = position;
    }
    if (eocd < 0) throw new Error('XLSX ZIP directory is missing.');
    var entries = uint16(view, eocd + 10);
    var offset = uint32(view, eocd + 16);
    var decoder = new TextDecoder('utf-8');
    var files = new Map();
    var expandedBytes = 0;
    for (var index = 0; index < entries; index += 1) {
      if (uint32(view, offset) !== 0x02014b50) throw new Error('XLSX ZIP directory is malformed.');
      var method = uint16(view, offset + 10);
      var compressedSize = uint32(view, offset + 20);
      var uncompressedSize = uint32(view, offset + 24);
      var nameLength = uint16(view, offset + 28);
      var extraLength = uint16(view, offset + 30);
      var commentLength = uint16(view, offset + 32);
      var localOffset = uint32(view, offset + 42);
      var name = decoder.decode(bytes.slice(offset + 46, offset + 46 + nameLength));
      expandedBytes += uncompressedSize;
      if (uncompressedSize > MAX_UNCOMPRESSED_BYTES || expandedBytes > MAX_UNCOMPRESSED_BYTES) {
        throw new Error('XLSX expands beyond the 20 MB safety limit.');
      }
      if (uint32(view, localOffset) !== 0x04034b50) throw new Error('XLSX ZIP entry is malformed.');
      var localNameLength = uint16(view, localOffset + 26);
      var localExtraLength = uint16(view, localOffset + 28);
      var start = localOffset + 30 + localNameLength + localExtraLength;
      var compressed = bytes.slice(start, start + compressedSize);
      if (method === 0) {
        files.set(name, compressed);
      } else if (method === 8 && typeof DecompressionStream !== 'undefined') {
        var stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
        var expanded = new Uint8Array(await new Response(stream).arrayBuffer());
        if (expanded.byteLength > MAX_UNCOMPRESSED_BYTES) throw new Error('XLSX entry is too large.');
        files.set(name, expanded);
      } else {
        throw new Error('This browser cannot decompress this XLSX file. Save it as CSV and retry.');
      }
      offset += 46 + nameLength + extraLength + commentLength;
    }
    return files;
  }

  function xml(bytes, name) {
    if (!bytes) throw new Error('XLSX is missing ' + name + '.');
    var documentNode = new DOMParser().parseFromString(new TextDecoder('utf-8').decode(bytes), 'application/xml');
    if (documentNode.getElementsByTagName('parsererror').length) throw new Error('XLSX XML is malformed.');
    return documentNode;
  }

  function nodes(documentNode, localName) {
    return Array.from(documentNode.getElementsByTagNameNS('*', localName));
  }

  function columnIndex(reference) {
    var letters = String(reference || '').match(/^[A-Z]+/i);
    if (!letters) return 0;
    return letters[0].toUpperCase().split('').reduce(function (value, letter) {
      return value * 26 + letter.charCodeAt(0) - 64;
    }, 0) - 1;
  }

  async function parseXlsx(arrayBuffer) {
    var files = await unzip(arrayBuffer);
    var shared = [];
    if (files.has('xl/sharedStrings.xml')) {
      nodes(xml(files.get('xl/sharedStrings.xml'), 'shared strings'), 'si').forEach(function (item) {
        shared.push(nodes(item, 't').map(function (text) { return text.textContent || ''; }).join(''));
      });
    }
    var workbook = xml(files.get('xl/workbook.xml'), 'workbook');
    var firstSheet = nodes(workbook, 'sheet')[0];
    if (!firstSheet) throw new Error('XLSX has no worksheet.');
    var relationshipId = firstSheet.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'id') || firstSheet.getAttribute('r:id');
    var relationships = xml(files.get('xl/_rels/workbook.xml.rels'), 'workbook relationships');
    var relationship = nodes(relationships, 'Relationship').find(function (item) { return item.getAttribute('Id') === relationshipId; });
    var target = relationship ? relationship.getAttribute('Target') : 'worksheets/sheet1.xml';
    var normalized = target.replace(/^\//, '').replace(/^xl\//, '');
    var sheet = xml(files.get('xl/' + normalized), 'first worksheet');
    var result = [];
    var sheetRows = nodes(sheet, 'row');
    if (sheetRows.length > MAX_ROWS + 1) throw new Error('Recipient file has more than ' + MAX_ROWS + ' rows.');
    sheetRows.forEach(function (rowNode) {
      var row = [];
      nodes(rowNode, 'c').forEach(function (cell) {
        var index = columnIndex(cell.getAttribute('r'));
        var type = cell.getAttribute('t');
        var valueNode = nodes(cell, type === 'inlineStr' ? 't' : 'v')[0];
        var value = valueNode ? valueNode.textContent || '' : '';
        if (type === 's') value = shared[Number(value)] || '';
        if (type === 'b') value = value === '1' ? 'TRUE' : 'FALSE';
        row[index] = value;
      });
      result.push(row);
    });
    return table(result);
  }

  async function parse(file) {
    if (!file || file.size <= 0 || file.size > MAX_FILE_BYTES) throw new Error('Choose a non-empty CSV/XLSX file up to 5 MB.');
    if (/\.csv$/i.test(file.name)) return parseCsv(await file.text());
    if (/\.xlsx$/i.test(file.name)) return parseXlsx(await file.arrayBuffer());
    throw new Error('Choose a .csv or .xlsx file.');
  }

  function guess(headers, candidates) {
    var normalized = headers.map(function (value) { return value.toLowerCase().replace(/^\uFEFF/, '').replace(/[\s_-]+/g, ''); });
    var index = normalized.findIndex(function (value) { return candidates.includes(value); });
    return index < 0 ? '' : String(index);
  }

  function mapping(tableData) {
    return {
      email: guess(tableData.headers, ['email', 'emailaddress', 'mail', 'mailaddress', 'e-mail']),
      firstName: guess(tableData.headers, ['firstname', 'givenname', 'name', 'first']),
      lastName: guess(tableData.headers, ['lastname', 'surname', 'familyname', 'last']),
    };
  }

  function downloadTemplate(filename, columns) {
    var csv = '\uFEFF' + (columns || 'email,first_name,last_name') + '\r\n';
    var blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = filename || 'mifp-recipient-template.csv';
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
  }

  function variableNames(tableData) {
    if (tableData.headers.length > 64) throw new Error('Use at most 64 columns for personalized email.');
    var names = tableData.headers.map(function (header) {
      return header.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
    });
    if (names.some(function (name) { return !/^[a-z][a-z0-9_]{0,63}$/.test(name); })) throw new Error('Column names must start with a letter and use letters, numbers or underscores.');
    if (new Set(names).size !== names.length) throw new Error('Use distinct column names for personalized email.');
    return names;
  }

  function recipients(tableData, selected, personalized) {
    var names = personalized ? variableNames(tableData) : [];
    var seen = new Set();
    var valid = [];
    var invalid = 0;
    var duplicate = 0;
    tableData.rows.forEach(function (row) {
      var email = String(row[Number(selected.email)] || '').trim();
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || /[\r\n]/.test(email)) {
        invalid += 1;
        return;
      }
      var key = email.toLowerCase();
      if (seen.has(key)) {
        duplicate += 1;
        return;
      }
      seen.add(key);
      var recipient = {
        email: email,
        first_name: selected.firstName === '' ? '' : String(row[Number(selected.firstName)] || '').trim().slice(0, 120),
        last_name: selected.lastName === '' ? '' : String(row[Number(selected.lastName)] || '').trim().slice(0, 120),
      };
      if (personalized) {
        recipient.variables = Object.create(null);
        names.forEach(function (name, index) {
          var value = String(row[index] || '').trim();
          if (value.length > 1000) throw new Error('Personalization values must be at most 1000 characters.');
          recipient.variables[name] = value;
        });
        if (Object.values(recipient.variables).reduce(function (total, value) { return total + value.length; }, 0) > 16000) throw new Error('Personalization values in a row are too large.');
        recipient.variables.first_name = recipient.first_name;
        recipient.variables.last_name = recipient.last_name;
      }
      valid.push(recipient);
    });
    return { total: tableData.rows.length, valid: valid, invalid: invalid, duplicate: duplicate };
  }

  document.querySelectorAll('[data-recipient-template-download]').forEach(function (button) {
    button.addEventListener('click', function () {
      downloadTemplate(button.dataset.recipientTemplateName || 'mifp-recipient-template.csv', button.dataset.recipientTemplateColumns);
    });
  });

  window.MIFPRecipientFiles = Object.freeze({
    parse: parse,
    mapping: mapping,
    recipients: recipients,
    variableNames: variableNames,
    downloadTemplate: downloadTemplate,
  });
})();
