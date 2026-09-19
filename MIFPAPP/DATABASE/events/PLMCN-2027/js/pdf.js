/*
 * Program PDF exporter — dependency-free, browser-only.
 *
 * This intentionally does NOT use a CDN, tracker, canvas screenshot or print
 * dialog. It writes a compact PDF 1.4 document directly from program.csv data.
 * The PDF uses the built-in Helvetica / Helvetica-Bold fonts, so no font files
 * or third-party runtime assets are required.
 */
(function () {
  'use strict';

  const PAGE_W = 595.28; // A4 points
  const PAGE_H = 841.89;
  const MARGIN_X = 46;
  const TOP = 46;
  const BOTTOM = 45;

  const asText = (value) => value == null ? '' : String(value);

  function safeFilename(value) {
    const name = asText(value || 'conference-program.pdf').replace(/[^a-zA-Z0-9._-]+/g, '-');
    return name.toLowerCase().endsWith('.pdf') ? name : `${name}.pdf`;
  }

  function hexRgb(hex, fallback = '101010') {
    const raw = asText(hex).trim().replace('#', '');
    const fallbackRaw = asText(fallback).replace('#', '');
    const valid = /^[0-9a-f]{6}$/i.test(raw) ? raw : fallbackRaw;
    return [0, 2, 4].map((i) => parseInt(valid.slice(i, i + 2), 16) / 255);
  }

  // PDF's standard Helvetica font uses WinAnsiEncoding. Convert common Unicode
  // punctuation and Western-European characters to that encoding; unsupported
  // glyphs degrade to '?' instead of corrupting the PDF file.
  const WIN_1252 = new Map([
    ['€', 128], ['‚', 130], ['ƒ', 131], ['„', 132], ['…', 133], ['†', 134],
    ['‡', 135], ['ˆ', 136], ['‰', 137], ['Š', 138], ['‹', 139], ['Œ', 140],
    ['Ž', 142], ['‘', 145], ['’', 146], ['“', 147], ['”', 148], ['•', 149],
    ['–', 150], ['—', 151], ['˜', 152], ['™', 153], ['š', 154], ['›', 155],
    ['œ', 156], ['ž', 158], ['Ÿ', 159]
  ]);

  function toWinAnsiBytes(value) {
    const bytes = [];
    for (const ch of asText(value)) {
      if (WIN_1252.has(ch)) { bytes.push(WIN_1252.get(ch)); continue; }
      const code = ch.charCodeAt(0);
      if (code <= 255) { bytes.push(code); continue; }
      const simplified = ch.normalize ? ch.normalize('NFKD').replace(/[\u0300-\u036f]/g, '') : ch;
      const fallback = simplified.charCodeAt(0);
      bytes.push(fallback <= 255 ? fallback : 63);
    }
    return bytes;
  }

  // Keep stream contents ASCII by octal-escaping non-ASCII WinAnsi bytes.
  function pdfLiteral(value) {
    let out = '';
    for (const byte of toWinAnsiBytes(value).map((b) => b === 10 || b === 13 ? 32 : b)) {
      if (byte === 40 || byte === 41 || byte === 92) out += `\\${String.fromCharCode(byte)}`;
      else if (byte < 32 || byte > 126) out += `\\${byte.toString(8).padStart(3, '0')}`;
      else out += String.fromCharCode(byte);
    }
    return out;
  }

  function approxWidth(text, size, bold) {
    return asText(text).length * size * (bold ? 0.56 : 0.51);
  }

  function wrap(text, maxWidth, size, bold) {
    const source = asText(text).replace(/\s+/g, ' ').trim();
    if (!source) return [];
    const words = source.split(' ');
    const lines = [];
    let line = '';
    for (const word of words) {
      const test = line ? `${line} ${word}` : word;
      if (line && approxWidth(test, size, bold) > maxWidth) {
        lines.push(line);
        line = word;
      } else line = test;
    }
    if (line) lines.push(line);
    return lines;
  }

  async function loadJpegAsset(source) {
    const src = asText(source).trim();
    if (!src) return null;
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.decoding = 'async';
      img.onload = () => {
        try {
          const maxDimension = 520;
          const scale = Math.min(1, maxDimension / Math.max(img.naturalWidth || 1, img.naturalHeight || 1));
          const width = Math.max(1, Math.round((img.naturalWidth || 1) * scale));
          const height = Math.max(1, Math.round((img.naturalHeight || 1) * scale));
          const canvas = document.createElement('canvas');
          canvas.width = width;
          canvas.height = height;
          const ctx = canvas.getContext('2d', { alpha: false });
          if (!ctx) throw new Error('Canvas is not available');
          ctx.fillStyle = '#ffffff';
          ctx.fillRect(0, 0, width, height);
          ctx.drawImage(img, 0, 0, width, height);
          canvas.toBlob(async (blob) => {
            if (!blob) { reject(new Error('Logo conversion failed')); return; }
            resolve({ bytes: new Uint8Array(await blob.arrayBuffer()), width, height });
          }, 'image/jpeg', 0.9);
        } catch (error) { reject(error); }
      };
      img.onerror = () => reject(new Error(`Logo could not be loaded: ${src}`));
      img.src = src;
    });
  }

  function fitImage(asset, maxWidth, maxHeight) {
    if (!asset || !asset.width || !asset.height) return { width: 0, height: 0 };
    const scale = Math.min(maxWidth / asset.width, maxHeight / asset.height);
    return { width: asset.width * scale, height: asset.height * scale };
  }

  async function makePdf(options) {
    const rows = Array.isArray(options.rows) ? options.rows : [];
    const program = options.program || {};
    const conference = options.conference || {};
    const organization = options.organization || {};
    const configuredColors = options.colors || (program.pdf && program.pdf.colors) || {};
    // MIFP PDF identity: red, dark navy and black. Values remain configurable
    // in conference.yaml, but the generated program never follows debug palettes.
    const red = hexRgb(configuredColors.red, 'b5122b');
    const navy = hexRgb(configuredColors.navy, '13213c');
    const black = hexRgb(configuredColors.black, '101010');
    const grey = [0.36, 0.39, 0.44];
    const light = [0.95, 0.955, 0.965];
    const logoConfig = options.logos || (program.pdf && program.pdf.logos) || {};
    const logoEntries = [];
    for (const [name, source] of [['organizer', logoConfig.organizer], ['conference', logoConfig.conference]]) {
      if (!source) continue;
      try {
        const asset = await loadJpegAsset(source);
        if (asset) logoEntries.push({ name, ...asset });
      } catch (error) {
        console.warn('[MIFP][PROGRAM][PDF] Logo skipped', { name, error: error.message });
      }
    }
    const logoMap = Object.fromEntries(logoEntries.map((asset) => [asset.name, asset]));
    const pages = [];
    let page = null;
    let y = TOP;

    function newPage() {
      page = [];
      pages.push(page);
      y = TOP;
    }

    function cmd(value) { page.push(value); }
    function pdfY(topY) { return PAGE_H - topY; }
    function strokeColor(rgb) { cmd(`${rgb.map((n) => n.toFixed(3)).join(' ')} RG`); }
    function fillColor(rgb) { cmd(`${rgb.map((n) => n.toFixed(3)).join(' ')} rg`); }
    function line(x1, top1, x2, top2, width, rgb) {
      strokeColor(rgb || [0.82, 0.84, 0.87]);
      cmd(`${Number(width || 0.5).toFixed(2)} w ${x1.toFixed(2)} ${pdfY(top1).toFixed(2)} m ${x2.toFixed(2)} ${pdfY(top2).toFixed(2)} l S`);
    }
    function rect(x, topY, w, h, rgb) {
      fillColor(rgb);
      cmd(`${x.toFixed(2)} ${(PAGE_H - topY - h).toFixed(2)} ${w.toFixed(2)} ${h.toFixed(2)} re f`);
    }
    function image(name, x, topY, width, height) {
      if (!logoMap[name] || width <= 0 || height <= 0) return;
      cmd(`q ${width.toFixed(2)} 0 0 ${height.toFixed(2)} ${x.toFixed(2)} ${(PAGE_H - topY - height).toFixed(2)} cm /Im_${name} Do Q`);
    }
    function text(value, x, topY, size, bold, rgb) {
      if (!asText(value)) return;
      fillColor(rgb || [0.12, 0.14, 0.17]);
      cmd(`BT /${bold ? 'F2' : 'F1'} ${size.toFixed(2)} Tf 1 0 0 1 ${x.toFixed(2)} ${pdfY(topY).toFixed(2)} Tm (${pdfLiteral(value)}) Tj ET`);
    }
    function paragraph(value, x, maxWidth, size, leading, bold, rgb) {
      const lines = wrap(value, maxWidth, size, bold);
      lines.forEach((item, index) => text(item, x, y + index * leading, size, bold, rgb));
      y += lines.length * leading;
      return lines.length;
    }
    function ensure(height) {
      if (y + height <= PAGE_H - BOTTOM) return;
      newPage();
      drawRunningHeader();
    }
    function drawRunningHeader() {
      text(conference.acronym || program.title || 'Conference Program', MARGIN_X, 31, 8.5, true, navy);
      line(MARGIN_X, 38, PAGE_W - MARGIN_X, 38, 1.0, red);
      y = 54;
    }

    newPage();

    // Compact dual-logo header: MIFP on the left, conference identity on the right.
    const organizerFit = fitImage(logoMap.organizer, 84, 42);
    const conferenceFit = fitImage(logoMap.conference, 112, 48);
    if (organizerFit.width) image('organizer', MARGIN_X, 34, organizerFit.width, organizerFit.height);
    if (conferenceFit.width) image('conference', PAGE_W - MARGIN_X - conferenceFit.width, 31, conferenceFit.width, conferenceFit.height);
    if (!organizerFit.width) text(organization.short_name || organization.name || 'MIFP', MARGIN_X, 48, 9, true, red);
    line(MARGIN_X, 84, PAGE_W - MARGIN_X, 84, 0.8, [0.84, 0.86, 0.89]);
    y = 112;

    // Cover/header on page one. Event title remains dominant.
    const title = program.pdf && program.pdf.title ? program.pdf.title : (conference.full_name || program.title || 'Conference Program');
    paragraph(title, MARGIN_X, PAGE_W - MARGIN_X * 2, 30, 33, true, navy);
    const pdfSubtitle = program.pdf && program.pdf.subtitle ? program.pdf.subtitle : '';
    if (pdfSubtitle) {
      y += 2;
      paragraph(pdfSubtitle, MARGIN_X, PAGE_W - MARGIN_X * 2, 12, 15, true, black);
    }
    y += 8;
    const subtitle = [
      conference.date_label,
      conference.venue,
      [conference.city, conference.country].filter(Boolean).join(', ')
    ].filter(Boolean);
    subtitle.forEach((item) => { paragraph(item, MARGIN_X, PAGE_W - MARGIN_X * 2, 9.4, 12, false, grey); });
    y += 10;
    line(MARGIN_X, y, PAGE_W - MARGIN_X, y, 2.0, red);
    line(MARGIN_X, y + 4, PAGE_W - MARGIN_X, y + 4, 0.8, navy);
    y += 24;

    // Keep CSV order, but nest rows whose Parent ID refers to a visible parent.
    const byId = new Map(rows.filter((row) => row.ID).map((row) => [asText(row.ID), row]));
    const children = new Map();
    rows.forEach((row) => {
      const parent = asText(row['Parent ID']).trim();
      if (!parent || !byId.has(parent)) return;
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(row);
    });

    let currentDay = '';
    rows.forEach((row) => {
      const parent = asText(row['Parent ID']).trim();
      if (parent && byId.has(parent)) return;
      const dayKey = `${asText(row.Day)}|${asText(row.Date)}`;
      if (dayKey !== currentDay) {
        ensure(66);
        if (currentDay) y += 8;
        currentDay = dayKey;
        const dayLabel = [row.Day, row.Date].filter(Boolean).join(' · ');
        rect(MARGIN_X, y, PAGE_W - MARGIN_X * 2, 25, navy);
        text(dayLabel, MARGIN_X + 10, y + 16, 10.2, true, [1, 1, 1]);
        y += 35;
      }

      const eventChildren = row.ID ? (children.get(asText(row.ID)) || []) : [];
      const titleLines = wrap(row.Title || row.Type || '', 350, 10.2, true);
      const detailParts = [];
      if (row.Speaker) detailParts.push(row.Speaker);
      if (row.Affiliation) detailParts.push(row.Affiliation);
      const details = detailParts.join(' · ');
      const detailLines = wrap(details, 350, 8.1, false);
      const meta = [row.Chair ? `${program.chair_label || 'Chair'}: ${row.Chair}` : '', row.Location, row.Notes].filter(Boolean).join(' · ');
      const metaLines = wrap(meta, 350, 7.4, false);
      const height = Math.max(34, 8 + titleLines.length * 12 + detailLines.length * 10 + metaLines.length * 9 + eventChildren.length * 31);
      ensure(height + 8);

      const time = [row['Start Time'], row['End Time']].filter(Boolean).join('–');
      // A restrained timeline rail makes the generated PDF match the web program.
      line(MARGIN_X + 72, y + 6, MARGIN_X + 72, y + Math.max(28, height - 3), 0.55, [0.82, 0.86, 0.88]);
      rect(MARGIN_X + 69.5, y + 8, 5, 5, red);
      line(MARGIN_X + 74.5, y + 10.5, MARGIN_X + 82, y + 10.5, 0.8, red);
      text(time, MARGIN_X, y + 11, 8.2, true, red);
      if (row.Type) text(asText(row.Type).toUpperCase(), MARGIN_X + 86, y + 10, 6.6, true, [0.45, 0.49, 0.55]);
      let contentY = y + 23;
      titleLines.forEach((item) => { text(item, MARGIN_X + 86, contentY, 10.2, true, black); contentY += 12; });
      detailLines.forEach((item) => { text(item, MARGIN_X + 86, contentY, 8.1, false, [0.30, 0.34, 0.40]); contentY += 10; });
      metaLines.forEach((item) => { text(item, MARGIN_X + 86, contentY, 7.4, false, [0.45, 0.49, 0.55]); contentY += 9; });

      eventChildren.forEach((talk) => {
        const talkTime = [talk['Start Time'], talk['End Time']].filter(Boolean).join('–');
        const talkTitle = wrap(talk.Title || '', 322, 8.3, true).slice(0, 2);
        const talkMeta = wrap([talk.Speaker, talk.Affiliation].filter(Boolean).join(' · '), 322, 7.2, false).slice(0, 2);
        line(MARGIN_X + 86, contentY + 2, PAGE_W - MARGIN_X, contentY + 2, 0.35, [0.89, 0.90, 0.92]);
        rect(MARGIN_X + 84, contentY, 3, 3, red);
        contentY += 12;
        text(talkTime, MARGIN_X + 96, contentY, 7.1, true, red);
        let ty = contentY;
        talkTitle.forEach((item) => { text(item, MARGIN_X + 160, ty, 8.3, true, black); ty += 10; });
        talkMeta.forEach((item) => { text(item, MARGIN_X + 160, ty, 7.2, false, [0.38, 0.42, 0.48]); ty += 9; });
        contentY = Math.max(contentY + 24, ty + 3);
      });

      y = Math.max(y + height, contentY + 4);
      line(MARGIN_X, y, PAGE_W - MARGIN_X, y, 0.45, [0.88, 0.89, 0.91]);
      y += 7;
    });

    // Page numbers and footer are applied after the page count is known.
    pages.forEach((commands, index) => {
      const footerY = PAGE_H - 24;
      const label = program.pdf && program.pdf.footer
        ? asText(program.pdf.footer)
        : `${conference.acronym || 'Conference'} · Scientific Program`;
      commands.push(`${navy.map((n) => n.toFixed(3)).join(' ')} rg`);
      commands.push(`BT /F1 7 Tf 1 0 0 1 ${MARGIN_X.toFixed(2)} ${(PAGE_H - footerY).toFixed(2)} Tm (${pdfLiteral(label)}) Tj ET`);
      commands.push(`${red.map((n) => n.toFixed(3)).join(' ')} rg`);
      commands.push(`BT /F2 7 Tf 1 0 0 1 ${(PAGE_W - MARGIN_X - 45).toFixed(2)} ${(PAGE_H - footerY).toFixed(2)} Tm (${pdfLiteral(`Page ${index + 1}/${pages.length}`)}) Tj ET`);
    });

    return buildBinary(pages, logoEntries);
  }

  function asciiBytes(value) {
    const out = new Uint8Array(value.length);
    for (let i = 0; i < value.length; i += 1) out[i] = value.charCodeAt(i) & 255;
    return out;
  }

  function concatBytes(chunks) {
    const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    chunks.forEach((chunk) => { out.set(chunk, offset); offset += chunk.length; });
    return out;
  }

  function buildBinary(pages, images) {
    const imageList = Array.isArray(images) ? images : [];
    const imageObjectIds = new Map();
    imageList.forEach((asset, index) => imageObjectIds.set(asset.name, 5 + index));
    const firstPageObject = 5 + imageList.length;
    const objectCount = 4 + imageList.length + pages.length * 2;
    const objects = new Array(objectCount + 1);
    objects[1] = asciiBytes('<< /Type /Catalog /Pages 2 0 R >>');
    const kids = pages.map((_, i) => `${firstPageObject + i * 2} 0 R`).join(' ');
    objects[2] = asciiBytes(`<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>`);
    objects[3] = asciiBytes('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>');
    objects[4] = asciiBytes('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>');

    imageList.forEach((asset, index) => {
      const id = 5 + index;
      const head = asciiBytes(`<< /Type /XObject /Subtype /Image /Width ${asset.width} /Height ${asset.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${asset.bytes.length} >>\nstream\n`);
      const tail = asciiBytes('\nendstream');
      objects[id] = concatBytes([head, asset.bytes, tail]);
    });

    const xObjects = imageList.map((asset) => `/Im_${asset.name} ${imageObjectIds.get(asset.name)} 0 R`).join(' ');
    pages.forEach((commands, i) => {
      const pageId = firstPageObject + i * 2;
      const contentId = pageId + 1;
      const stream = `${commands.join('\n')}\n`;
      const resources = `<< /Font << /F1 3 0 R /F2 4 0 R >>${xObjects ? ` /XObject << ${xObjects} >>` : ''} >>`;
      objects[pageId] = asciiBytes(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_W.toFixed(2)} ${PAGE_H.toFixed(2)}] /Resources ${resources} /Contents ${contentId} 0 R >>`);
      const streamBytes = asciiBytes(stream);
      objects[contentId] = concatBytes([asciiBytes(`<< /Length ${streamBytes.length} >>\nstream\n`), streamBytes, asciiBytes('endstream')]);
    });

    const chunks = [asciiBytes('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n')];
    const offsets = new Array(objectCount + 1).fill(0);
    let position = chunks[0].length;
    for (let id = 1; id <= objectCount; id += 1) {
      offsets[id] = position;
      const bytes = concatBytes([asciiBytes(`${id} 0 obj\n`), objects[id], asciiBytes('\nendobj\n')]);
      chunks.push(bytes);
      position += bytes.length;
    }
    const xrefOffset = position;
    let xref = `xref\n0 ${objectCount + 1}\n0000000000 65535 f \n`;
    for (let id = 1; id <= objectCount; id += 1) xref += `${String(offsets[id]).padStart(10, '0')} 00000 n \n`;
    xref += `trailer\n<< /Size ${objectCount + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
    chunks.push(asciiBytes(xref));
    return concatBytes(chunks);
  }

  async function download(options) {
    const bytes = await makePdf(options || {});
    const blob = new Blob([bytes], { type: 'application/pdf' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = safeFilename(options && options.filename);
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
    return { filename: a.download, bytes: bytes.length };
  }

  window.ProgramPdf = { makePdf, download };
})();
