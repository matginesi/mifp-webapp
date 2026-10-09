(function (global) {
  'use strict';

  const encoder = new TextEncoder();
  let crcTable = null;

  function table() {
    if (crcTable) return crcTable;
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
      crcTable[n] = c >>> 0;
    }
    return crcTable;
  }

  function crc32(bytes) {
    const t = table();
    let c = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i += 1) c = t[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }

  function u16(view, offset, value) { view.setUint16(offset, value, true); }
  function u32(view, offset, value) { view.setUint32(offset, value >>> 0, true); }

  function dosDateTime(date) {
    const d = date instanceof Date ? date : new Date();
    const year = Math.max(1980, d.getFullYear());
    const time = ((d.getHours() & 31) << 11) | ((d.getMinutes() & 63) << 5) | ((Math.floor(d.getSeconds() / 2)) & 31);
    const day = ((year - 1980) << 9) | (((d.getMonth() + 1) & 15) << 5) | (d.getDate() & 31);
    return { time, date: day };
  }

  async function toBytes(value) {
    if (value instanceof Uint8Array) return value;
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (value instanceof Blob) return new Uint8Array(await value.arrayBuffer());
    return encoder.encode(String(value == null ? '' : value));
  }

  async function createBlob(entries, options) {
    const settings = Object.assign({ root: '', modified: new Date() }, options || {});
    const normalized = [];
    for (const entry of entries || []) {
      if (!entry || !entry.path) continue;
      const rawPath = String(entry.path).replace(/\\/g, '/').replace(/^\/+/, '');
      if (!rawPath || rawPath.split('/').some((part) => part === '..')) throw new Error('Unsafe ZIP path: ' + entry.path);
      const path = settings.root ? String(settings.root).replace(/\/+$/, '') + '/' + rawPath : rawPath;
      const name = encoder.encode(path);
      const bytes = await toBytes(entry.data);
      normalized.push({ name, bytes, crc: crc32(bytes), modified: entry.modified || settings.modified });
    }

    let localSize = 0;
    let centralSize = 0;
    normalized.forEach((entry) => {
      localSize += 30 + entry.name.length + entry.bytes.length;
      centralSize += 46 + entry.name.length;
    });
    const output = new Uint8Array(localSize + centralSize + 22);
    const view = new DataView(output.buffer);
    let offset = 0;
    const central = [];

    normalized.forEach((entry) => {
      const start = offset;
      const dt = dosDateTime(entry.modified);
      u32(view, offset, 0x04034B50); offset += 4;
      u16(view, offset, 20); offset += 2;
      u16(view, offset, 0x0800); offset += 2;
      u16(view, offset, 0); offset += 2;
      u16(view, offset, dt.time); offset += 2;
      u16(view, offset, dt.date); offset += 2;
      u32(view, offset, entry.crc); offset += 4;
      u32(view, offset, entry.bytes.length); offset += 4;
      u32(view, offset, entry.bytes.length); offset += 4;
      u16(view, offset, entry.name.length); offset += 2;
      u16(view, offset, 0); offset += 2;
      output.set(entry.name, offset); offset += entry.name.length;
      output.set(entry.bytes, offset); offset += entry.bytes.length;
      central.push({ entry, start, dt });
    });

    const centralOffset = offset;
    central.forEach(({ entry, start, dt }) => {
      u32(view, offset, 0x02014B50); offset += 4;
      u16(view, offset, 20); offset += 2;
      u16(view, offset, 20); offset += 2;
      u16(view, offset, 0x0800); offset += 2;
      u16(view, offset, 0); offset += 2;
      u16(view, offset, dt.time); offset += 2;
      u16(view, offset, dt.date); offset += 2;
      u32(view, offset, entry.crc); offset += 4;
      u32(view, offset, entry.bytes.length); offset += 4;
      u32(view, offset, entry.bytes.length); offset += 4;
      u16(view, offset, entry.name.length); offset += 2;
      u16(view, offset, 0); offset += 2;
      u16(view, offset, 0); offset += 2;
      u16(view, offset, 0); offset += 2;
      u16(view, offset, 0); offset += 2;
      u32(view, offset, 0); offset += 4;
      u32(view, offset, start); offset += 4;
      output.set(entry.name, offset); offset += entry.name.length;
    });

    const centralLength = offset - centralOffset;
    u32(view, offset, 0x06054B50); offset += 4;
    u16(view, offset, 0); offset += 2;
    u16(view, offset, 0); offset += 2;
    u16(view, offset, normalized.length); offset += 2;
    u16(view, offset, normalized.length); offset += 2;
    u32(view, offset, centralLength); offset += 4;
    u32(view, offset, centralOffset); offset += 4;
    u16(view, offset, 0); offset += 2;

    return new Blob([output], { type: 'application/zip' });
  }

  global.ZipLite = Object.freeze({ createBlob, crc32 });
})(window);
