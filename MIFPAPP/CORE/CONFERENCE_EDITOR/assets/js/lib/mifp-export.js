(function (global) {
  'use strict';

  const FORMAT = 'mifp-content';
  const FORMAT_VERSION = 1;
  const EVENT_FIELDS = new Set([
    'uid','slug','title','start_date','end_date','date_text','date_precision','location','description',
    'event_type','series_key','parent_event_id','parent_event_slug','review_status','is_featured','sort_order','remote_url'
  ]);
  const TOP_LEVEL_KEYS = new Set(['type','data','links','assets','meta']);
  const REVIEW_STATUSES = new Set(['draft','review','published','quarantined','duplicate']);
  const IMAGE_EXTENSIONS = new Set(['png','jpg','jpeg','gif','webp','svg','ico','avif']);
  const PLACEHOLDER_RE = /^(?:tbc|tbd|todo|none|n\/a|na|new-conference|placeholder)$/i;

  function cleanString(value) {
    if (value == null) return '';
    return String(value).trim();
  }

  function usable(value) {
    const text = cleanString(value);
    return Boolean(text) && !PLACEHOLDER_RE.test(text);
  }

  function slugify(value) {
    return cleanString(value)
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^A-Za-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .toLowerCase()
      .slice(0, 100);
  }

  function fileName(path) {
    const parts = cleanString(path).replace(/\\/g, '/').split('/');
    return parts[parts.length - 1] || 'asset';
  }

  function extension(path) {
    const name = fileName(path);
    const index = name.lastIndexOf('.');
    return index > 0 ? name.slice(index + 1).toLowerCase() : '';
  }

  function safeFilename(value) {
    const source = fileName(value) || 'asset';
    const dot = source.lastIndexOf('.');
    const stem = dot > 0 ? source.slice(0, dot) : source;
    const ext = dot > 0 ? source.slice(dot).toLowerCase() : '';
    const normalized = stem.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 90) || 'asset';
    return normalized + ext;
  }

  function isIsoDate(value) {
    const text = cleanString(value);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text) || text.startsWith('0000-')) return false;
    const date = new Date(text + 'T00:00:00Z');
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === text;
  }

  function isHttpUrl(value) {
    try {
      const url = new URL(cleanString(value));
      return url.protocol === 'http:' || url.protocol === 'https:';
    } catch (_) {
      return false;
    }
  }

  function resolveHttpUrl(value, base) {
    const raw = cleanString(value);
    if (!usable(raw)) return '';
    try {
      const url = base && isHttpUrl(base) ? new URL(raw, base) : new URL(raw);
      return (url.protocol === 'http:' || url.protocol === 'https:') ? url.href : '';
    } catch (_) {
      return '';
    }
  }

  function uniqueStrings(values) {
    const result = [];
    const seen = new Set();
    (values || []).forEach((value) => {
      const text = cleanString(value);
      const key = text.toLowerCase();
      if (!usable(text) || seen.has(key)) return;
      seen.add(key);
      result.push(text);
    });
    return result;
  }

  function buildDescription(config) {
    const paragraphs = config && config.home_intro && Array.isArray(config.home_intro.paragraphs)
      ? config.home_intro.paragraphs.filter(usable).map(cleanString)
      : [];
    if (paragraphs.length) return paragraphs.join('\n\n');
    const siteDescription = config && config.site ? config.site.description : '';
    return usable(siteDescription) ? cleanString(siteDescription) : '';
  }

  function buildLocation(config) {
    const conference = config && config.conference ? config.conference : {};
    const cityCountry = uniqueStrings([conference.city, conference.country]).join(', ');
    const venue = usable(conference.venue) ? cleanString(conference.venue) : '';
    if (venue && cityCountry) return venue + ' · ' + cityCountry;
    return venue || cityCountry;
  }

  function buildDates(config, warnings) {
    const conference = config && config.conference ? config.conference : {};
    const start = cleanString(conference.start_date);
    const end = cleanString(conference.end_date);
    const dateText = usable(conference.date_label) ? cleanString(conference.date_label) : '';
    const result = {};

    if (usable(start) && !isIsoDate(start)) throw new Error('conference.start_date must be a valid YYYY-MM-DD date.');
    if (usable(end) && !isIsoDate(end)) throw new Error('conference.end_date must be a valid YYYY-MM-DD date.');

    if (isIsoDate(start)) result.start_date = start;
    if (isIsoDate(end)) {
      if (isIsoDate(start) && end < start) throw new Error('conference.end_date precedes start_date.');
      else result.end_date = end;
    }
    if (dateText) result.date_text = dateText;

    if (result.start_date && result.end_date && result.start_date !== result.end_date) result.date_precision = 'range';
    else if (result.start_date) result.date_precision = 'day';
    else if (dateText) result.date_precision = 'unknown';

    return result;
  }

  function eventIdentity(input) {
    const config = input.config || {};
    const conference = config.conference || {};
    const site = config.site || {};
    const project = usable(input.projectName) ? cleanString(input.projectName) : '';
    const title = usable(conference.full_name) ? cleanString(conference.full_name)
      : usable(site.title) ? cleanString(site.title)
      : project;
    if (!title) throw new Error('MIFP export requires a real conference title. Replace TBC/TBD placeholders first.');

    const year = usable(site.year) && /^\d{4}$/.test(cleanString(site.year)) ? cleanString(site.year) : '';
    const acronym = usable(conference.acronym) ? cleanString(conference.acronym)
      : usable(site.short_name) ? cleanString(site.short_name)
      : '';
    const occurrence = project || uniqueStrings([acronym, year]).join('-') || title;
    const slug = slugify(occurrence);
    if (!slug) throw new Error('Could not derive a deterministic MIFP event slug.');

    let seriesKey = slugify(acronym || occurrence.replace(/[-_ ]?\d{4}$/i, ''));
    seriesKey = seriesKey.replace(/-\d{4}$/i, '');
    if (!seriesKey) seriesKey = slug.replace(/-\d{4}$/i, '');

    return { title, slug, uid: 'event_' + slug.replace(/-/g, '_'), seriesKey };
  }

  function buildRecordBase(input, warnings) {
    const config = input.config || {};
    const conference = config.conference || {};
    const site = config.site || {};
    const identity = eventIdentity(input);
    const data = {
      uid: identity.uid,
      slug: identity.slug,
      title: identity.title
    };

    Object.assign(data, buildDates(config, warnings));
    const location = buildLocation(config);
    if (location) data.location = location;
    const description = buildDescription(config);
    if (description) data.description = description;
    data.event_type = 'conference';
    if (identity.seriesKey) data.series_key = identity.seriesKey;

    const status = cleanString(input.reviewStatus);
    data.review_status = REVIEW_STATUSES.has(status) ? status : 'draft';

    const baseUrl = usable(site.base_url) && isHttpUrl(site.base_url) ? cleanString(site.base_url) : '';
    if (usable(site.base_url) && !baseUrl) warnings.push('site.base_url is not an absolute HTTP(S) URL; remote_url was omitted.');
    if (baseUrl) data.remote_url = baseUrl;

    const links = [];
    if (baseUrl) links.push({ url: baseUrl, role: 'primary', label: 'Conference website', is_primary: true, sort_order: 1 });
    const registrationEnabled = !(config.registration && config.registration.enabled === false);
    const registrationRaw = conference.registration_url || (config.registration && config.registration.registration_url);
    const registrationUrl = registrationEnabled ? resolveHttpUrl(registrationRaw, baseUrl) : '';
    if (registrationUrl && registrationUrl !== baseUrl) {
      links.push({ url: registrationUrl, role: 'registration', label: 'Registration', is_primary: false, sort_order: links.length + 1 });
    }

    const meta = {
      source: 'mifp-conference-editor',
      sources: [
        { kind: 'conference-editor', relative_path: 'conference.yaml' }
      ],
      export: {
        editor_version: cleanString(input.editorVersion),
        conference_version: cleanString(input.conferenceVersion)
      }
    };

    return { type: 'event', data, links, assets: [], meta };
  }

  function assetCandidate(config, path, role, caption, primary) {
    const normalized = cleanString(path).replace(/\\/g, '/').replace(/^\.\//, '');
    const parts = normalized.split('/');
    if (!usable(normalized) || !normalized.startsWith('assets/') || parts.some((part) => !part || part === '.' || part === '..') || /(?:^|\/)(?:placeholder|no_face)(?:[._-]|$)/i.test(normalized)) return null;
    return { sourcePath: normalized, role, caption, isPrimary: Boolean(primary) };
  }

  function configuredAssetCandidates(config) {
    const assets = config.assets || {};
    const program = config.program || {};
    const download = program.download && typeof program.download === 'object' ? program.download : {};
    const candidates = [];
    const cover = assetCandidate(config, assets.hero_image, 'cover', 'Conference cover', true)
      || assetCandidate(config, config.site && config.site.social_image, 'cover', 'Conference cover', true)
      || assetCandidate(config, assets.social_cover, 'cover', 'Conference cover', true);
    if (cover) candidates.push(cover);
    const logo = assetCandidate(config, assets.logo, 'logo', 'Conference logo', false);
    if (logo) candidates.push(logo);

    const documents = [
      [assets.program_pdf, 'Conference programme'],
      [assets.abstracts_pdf, 'Abstracts'],
      [download.mode === 'local' ? download.local_file : '', 'Conference programme']
    ];
    documents.forEach(([path, caption]) => {
      const item = assetCandidate(config, path, 'document', caption, false);
      if (item) candidates.push(item);
    });

    const seen = new Set();
    return candidates.filter((item) => {
      if (seen.has(item.sourcePath)) return false;
      seen.add(item.sourcePath);
      return true;
    });
  }

  async function bytesOf(value) {
    if (value instanceof Uint8Array) return value;
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (value && typeof value.arrayBuffer === 'function') return new Uint8Array(await value.arrayBuffer());
    return new TextEncoder().encode(String(value == null ? '' : value));
  }

  function rightRotate(value, amount) {
    return (value >>> amount) | (value << (32 - amount));
  }

  function sha256HexFallback(bytes) {
    const K = new Uint32Array([
      0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
      0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
      0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
      0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
      0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
    ]);
    const H = new Uint32Array([
      0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19
    ]);

    const length = bytes.length;
    const bitLength = length * 8;
    const totalLength = Math.ceil((length + 9) / 64) * 64;
    const padded = new Uint8Array(totalLength);
    padded.set(bytes);
    padded[length] = 0x80;

    const high = Math.floor(bitLength / 0x100000000);
    const low = bitLength >>> 0;
    const view = new DataView(padded.buffer);
    view.setUint32(totalLength - 8, high, false);
    view.setUint32(totalLength - 4, low, false);

    const w = new Uint32Array(64);
    for (let offset = 0; offset < totalLength; offset += 64) {
      for (let i = 0; i < 16; i += 1) w[i] = view.getUint32(offset + i * 4, false);
      for (let i = 16; i < 64; i += 1) {
        const x = w[i - 15];
        const y = w[i - 2];
        const s0 = rightRotate(x, 7) ^ rightRotate(x, 18) ^ (x >>> 3);
        const s1 = rightRotate(y, 17) ^ rightRotate(y, 19) ^ (y >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }

      let a=H[0], b=H[1], c=H[2], d=H[3], e=H[4], f=H[5], g=H[6], h=H[7];
      for (let i = 0; i < 64; i += 1) {
        const S1 = rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25);
        const ch = (e & f) ^ (~e & g);
        const temp1 = (h + S1 + ch + K[i] + w[i]) >>> 0;
        const S0 = rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const temp2 = (S0 + maj) >>> 0;
        h=g; g=f; f=e; e=(d+temp1)>>>0; d=c; c=b; b=a; a=(temp1+temp2)>>>0;
      }

      H[0]=(H[0]+a)>>>0; H[1]=(H[1]+b)>>>0; H[2]=(H[2]+c)>>>0; H[3]=(H[3]+d)>>>0;
      H[4]=(H[4]+e)>>>0; H[5]=(H[5]+f)>>>0; H[6]=(H[6]+g)>>>0; H[7]=(H[7]+h)>>>0;
    }

    return Array.from(H).map((word) => word.toString(16).padStart(8, '0')).join('');
  }

  async function sha256Hex(value) {
    const bytes = await bytesOf(value);
    if (global.crypto && global.crypto.subtle && typeof global.crypto.subtle.digest === 'function') {
      try {
        const digest = await global.crypto.subtle.digest('SHA-256', bytes);
        return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
      } catch (_) {
        // file:// and embedded browser contexts may expose crypto.subtle but reject digest().
      }
    }
    return sha256HexFallback(bytes);
  }

  function packagedDirectory(kind, ext) {
    if (kind === 'image') return 'image';
    if (kind === 'pdf' || ext === 'pdf') return 'pdf';
    return 'document';
  }

  function assetKind(path, blob) {
    const ext = extension(path);
    const mime = cleanString(blob && blob.type).toLowerCase();
    if (ext === 'pdf' || mime === 'application/pdf') return 'pdf';
    if (IMAGE_EXTENSIONS.has(ext) || mime.startsWith('image/')) return 'image';
    return 'document';
  }

  function publicAssetUrl(sourcePath, baseUrl) {
    return resolveHttpUrl(sourcePath, baseUrl);
  }

  async function packageAssets(record, input, warnings) {
    const config = input.config || {};
    const sourceAssets = new Map();
    (input.assets || []).forEach((item) => {
      if (item && item.path && item.blob) sourceAssets.set(cleanString(item.path).replace(/\\/g, '/'), item);
    });
    const baseUrl = record.data.remote_url || '';
    const manifestFiles = [];
    const zipEntries = [];
    const packagedPaths = new Map();

    for (const candidate of configuredAssetCandidates(config)) {
      const source = sourceAssets.get(candidate.sourcePath);
      if (!source || !source.blob) {
        warnings.push('Configured export asset is missing and was skipped: ' + candidate.sourcePath);
        continue;
      }
      const sha256 = await sha256Hex(source.blob);
      const kind = assetKind(candidate.sourcePath, source.blob);
      const ext = extension(candidate.sourcePath);
      const directory = packagedDirectory(kind, ext);
      const baseName = safeFilename(candidate.sourcePath);
      let prefixLength = 8;
      let outputName = sha256.slice(0, prefixLength) + '-' + baseName;
      let path = directory + '/' + outputName;
      let archivePath = 'assets/' + path;
      while (packagedPaths.has(archivePath) && packagedPaths.get(archivePath) !== sha256 && prefixLength < 64) {
        prefixLength = Math.min(64, prefixLength + 8);
        outputName = sha256.slice(0, prefixLength) + '-' + baseName;
        path = directory + '/' + outputName;
        archivePath = 'assets/' + path;
      }
      const size = Number(source.blob.size || (await bytesOf(source.blob)).byteLength);
      const mimeType = cleanString(source.blob.type) || (kind === 'pdf' ? 'application/pdf' : '');

      const asset = {
        path,
        role: candidate.role,
        kind,
        caption: candidate.caption,
        is_primary: candidate.isPrimary,
        sort_order: record.assets.length + 1,
        content_sha256: sha256,
        storage_status: 'packaged',
        is_external: false,
        filename: fileName(path),
        original_filename: fileName(candidate.sourcePath),
        size
      };
      if (mimeType) asset.mime_type = mimeType;
      const sourceUrl = publicAssetUrl(candidate.sourcePath, baseUrl);
      if (sourceUrl) asset.url = sourceUrl;

      record.assets.push(asset);
      if (!packagedPaths.has(archivePath)) {
        packagedPaths.set(archivePath, sha256);
        manifestFiles.push({ path, archive_path: archivePath, sha256, size });
        zipEntries.push({ path: archivePath, data: source.blob });
      }
    }
    return { manifestFiles, zipEntries };
  }

  function assertRecordShape(record) {
    Object.keys(record).forEach((key) => {
      if (!TOP_LEVEL_KEYS.has(key)) throw new Error('Unsupported MIFP top-level key: ' + key);
    });
    if (record.type !== 'event') throw new Error('Conference Editor may export only MIFP event records.');
    Object.keys(record.data || {}).forEach((key) => {
      if (!EVENT_FIELDS.has(key)) throw new Error('Unsupported MIFP event field: ' + key);
    });
    if (!usable(record.data && record.data.title)) throw new Error('MIFP event title is required.');
    if (record.data.end_date && record.data.start_date && record.data.end_date < record.data.start_date) throw new Error('MIFP event end_date cannot precede start_date.');
  }

  async function build(input) {
    const warnings = [];
    const record = buildRecordBase(input || {}, warnings);
    const packaged = await packageAssets(record, input || {}, warnings);
    assertRecordShape(record);

    const recordsText = JSON.stringify(record) + '\n';
    const recordsSha256 = await sha256Hex(new TextEncoder().encode(recordsText));
    const manifest = {
      format: FORMAT,
      format_version: FORMAT_VERSION,
      generated_at: new Date().toISOString(),
      scope: 'all',
      records: 1,
      records_sha256: recordsSha256,
      counts: { event: 1 },
      files: packaged.manifestFiles
    };
    const manifestText = JSON.stringify(manifest, null, 2) + '\n';
    const entries = [
      { path: 'manifest.json', data: manifestText },
      { path: 'records.jsonl', data: recordsText }
    ].concat(packaged.zipEntries);

    return {
      record,
      recordsText,
      recordsSha256,
      manifest,
      manifestText,
      entries,
      warnings,
      assets: record.assets.slice()
    };
  }

  global.MifpExport = Object.freeze({
    FORMAT,
    FORMAT_VERSION,
    build,
    slugify,
    sha256Hex
  });
})(typeof window !== 'undefined' ? window : globalThis);
