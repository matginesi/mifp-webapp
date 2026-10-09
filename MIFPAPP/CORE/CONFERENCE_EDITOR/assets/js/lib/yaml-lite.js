(function (global) {
  'use strict';

  const BLOCKED_KEYS = Object.freeze({ __proto__: true, prototype: true, constructor: true });

  function safeKey(key, line) {
    const value = String(key || '').trim();
    if (!value) throw new Error('Empty YAML key at line ' + line);
    if (BLOCKED_KEYS[value]) throw new Error('Unsafe YAML key "' + value + '" at line ' + line);
    return value;
  }

  function stripComment(line) {
    let quote = null;
    let out = '';
    for (let i = 0; i < line.length; i += 1) {
      const char = line[i];
      if ((char === '"' || char === "'") && (i === 0 || line[i - 1] !== '\\')) {
        quote = quote === char ? null : (quote || char);
      }
      if (char === '#' && !quote && (i === 0 || /\s/.test(line[i - 1]))) break;
      out += char;
    }
    return out.replace(/\s+$/, '');
  }

  // Split a YAML mapping only on a colon that is outside quoted text.
  // This keeps list values such as "Contact: contact@example.org" as scalars.
  function mappingEntry(text) {
    const source = String(text || '');
    let quote = null;
    for (let i = 0; i < source.length; i += 1) {
      const ch = source[i];
      if ((ch === '"' || ch === "'") && (i === 0 || source[i - 1] !== '\\')) {
        quote = quote === ch ? null : (quote || ch);
        continue;
      }
      if (ch === ':' && !quote) {
        return [source.slice(0, i), source.slice(i + 1)];
      }
    }
    return null;
  }

  function scalar(raw) {
    const value = String(raw == null ? '' : raw).trim();
    if (!value) return '';
    if ((value[0] === '"' && value[value.length - 1] === '"') || (value[0] === "'" && value[value.length - 1] === "'")) {
      try {
        return value[0] === '"' ? JSON.parse(value) : value.slice(1, -1).replace(/''/g, "'");
      } catch (error) {
        return value.slice(1, -1);
      }
    }
    if (/^(true|false)$/i.test(value)) return value.toLowerCase() === 'true';
    if (/^(null|~)$/i.test(value)) return null;
    if (/^-?(?:\d+\.?\d*|\.\d+)$/.test(value)) return Number(value);
    return value;
  }

  function readMultiline(tokens, position, parentIndent, fold) {
    const parts = [];
    let pos = position;
    while (pos < tokens.length && tokens[pos].indent > parentIndent) {
      parts.push(tokens[pos].text);
      pos += 1;
    }
    return { value: fold ? parts.join(' ') : parts.join('\n'), pos };
  }

  function parse(text) {
    const input = String(text || '').replace(/^\uFEFF/, '');
    if (input.length > 2_000_000) throw new Error('YAML file is too large for this static template');

    const rawLines = input.split(/\r?\n/);
    const tokens = [];

    rawLines.forEach(function (rawLine, index) {
      const raw = rawLine.replace(/\t/g, '  ');
      const clean = stripComment(raw);
      if (!clean.trim()) return;
      const indent = (clean.match(/^\s*/) || [''])[0].length;
      tokens.push({ indent, text: clean.trim(), line: index + 1 });
    });

    function parseBlock(position, indent, depth) {
      if (depth > 40) throw new Error('YAML nesting is too deep');
      if (position >= tokens.length) return [Object.create(null), position];

      let pos = position;
      const isArray = tokens[pos].indent === indent && tokens[pos].text.indexOf('- ') === 0;
      const container = isArray ? [] : Object.create(null);

      while (pos < tokens.length) {
        const token = tokens[pos];
        if (token.indent < indent) break;
        if (token.indent > indent) throw new Error('Unexpected indentation at line ' + token.line);

        if (isArray) {
          if (token.text.indexOf('- ') !== 0) break;
          const body = token.text.slice(2).trim();
          if (!body) {
            if (pos + 1 < tokens.length && tokens[pos + 1].indent > indent) {
              const result = parseBlock(pos + 1, tokens[pos + 1].indent, depth + 1);
              container.push(result[0]);
              pos = result[1];
            } else {
              container.push(null);
              pos += 1;
            }
            continue;
          }

          const pair = mappingEntry(body);
          if (!pair) {
            container.push(scalar(body));
            pos += 1;
            continue;
          }

          const object = Object.create(null);
          const key = safeKey(pair[0], token.line);
          const value = (pair[1] || '').trim();

          if (value === '|' || value === '>') {
            const block = readMultiline(tokens, pos + 1, indent, value === '>');
            object[key] = block.value;
            pos = block.pos;
          } else if (value) {
            object[key] = scalar(value);
            pos += 1;
          } else if (pos + 1 < tokens.length && tokens[pos + 1].indent > indent) {
            const result = parseBlock(pos + 1, tokens[pos + 1].indent, depth + 1);
            object[key] = result[0];
            pos = result[1];
          } else {
            object[key] = Object.create(null);
            pos += 1;
          }

          while (pos < tokens.length && tokens[pos].indent > indent) {
            const child = tokens[pos];
            const childIndent = child.indent;
            if (child.text.indexOf('- ') === 0) break;
            const childPair = mappingEntry(child.text);
            if (!childPair) break;
            const childKey = safeKey(childPair[0], child.line);
            const childValue = (childPair[1] || '').trim();

            if (childValue === '|' || childValue === '>') {
              const block = readMultiline(tokens, pos + 1, childIndent, childValue === '>');
              object[childKey] = block.value;
              pos = block.pos;
            } else if (childValue) {
              object[childKey] = scalar(childValue);
              pos += 1;
            } else if (pos + 1 < tokens.length && tokens[pos + 1].indent > childIndent) {
              const result = parseBlock(pos + 1, tokens[pos + 1].indent, depth + 1);
              object[childKey] = result[0];
              pos = result[1];
            } else {
              object[childKey] = Object.create(null);
              pos += 1;
            }
          }

          container.push(object);
          continue;
        }

        const pair = mappingEntry(token.text);
        if (!pair) throw new Error('Expected key: value at line ' + token.line);
        const key = safeKey(pair[0], token.line);
        const value = (pair[1] || '').trim();

        if (value === '|' || value === '>') {
          const block = readMultiline(tokens, pos + 1, indent, value === '>');
          container[key] = block.value;
          pos = block.pos;
        } else if (value) {
          container[key] = scalar(value);
          pos += 1;
        } else if (pos + 1 < tokens.length && tokens[pos + 1].indent > indent) {
          const result = parseBlock(pos + 1, tokens[pos + 1].indent, depth + 1);
          container[key] = result[0];
          pos = result[1];
        } else {
          container[key] = Object.create(null);
          pos += 1;
        }
      }

      return [container, pos];
    }

    return tokens.length ? parseBlock(0, tokens[0].indent, 0)[0] : Object.create(null);
  }

  function stringifyScalar(value) {
    if (value === null) return 'null';
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    const text = String(value == null ? '' : value);
    if (!text) return "''";
    if (text.indexOf('\n') >= 0) return null;
    const plainSafe = /^[A-Za-z0-9_./@+\-–—· ()]+$/.test(text)
      && !/^(?:true|false|null|~|yes|no|on|off|[-+]?\d+(?:\.\d+)?)$/i.test(text)
      && !/^[-?:,\[\]{}#&*!|>'"%@`]/.test(text)
      && !/:\s|\s#/.test(text);
    if (plainSafe) return text;
    return "'" + text.replace(/'/g, "''") + "'";
  }

  function stringify(value) {
    const lines = [];

    function write(node, indent, keyPrefix, inArray) {
      const pad = ' '.repeat(indent);
      if (Array.isArray(node)) {
        if (!node.length && keyPrefix != null) {
          lines.push(pad + keyPrefix + ': []');
          return;
        }
        if (keyPrefix != null) lines.push(pad + keyPrefix + ':');
        const itemPad = ' '.repeat(indent + (keyPrefix != null ? 2 : 0));
        const childIndent = indent + (keyPrefix != null ? 2 : 0);
        node.forEach(function (item) {
          if (item && typeof item === 'object') {
            if (Array.isArray(item)) {
              lines.push(itemPad + '-');
              write(item, childIndent + 2, null, true);
            } else {
              const keys = Object.keys(item);
              if (!keys.length) {
                lines.push(itemPad + '- {}');
                return;
              }
              const first = keys[0];
              const firstValue = item[first];
              if (firstValue && typeof firstValue === 'object') {
                lines.push(itemPad + '- ' + first + ':');
                write(firstValue, childIndent + 4, null, true);
              } else {
                const scalarValue = stringifyScalar(firstValue);
                if (scalarValue === null) {
                  lines.push(itemPad + '- ' + first + ': |');
                  String(firstValue).split('\n').forEach(function (part) { lines.push(' '.repeat(childIndent + 4) + part); });
                } else lines.push(itemPad + '- ' + first + ': ' + scalarValue);
              }
              keys.slice(1).forEach(function (key) {
                const v = item[key];
                if (v && typeof v === 'object') write(v, childIndent + 2, key, false);
                else {
                  const scalarValue = stringifyScalar(v);
                  if (scalarValue === null) {
                    lines.push(' '.repeat(childIndent + 2) + key + ': |');
                    String(v).split('\n').forEach(function (part) { lines.push(' '.repeat(childIndent + 4) + part); });
                  } else lines.push(' '.repeat(childIndent + 2) + key + ': ' + scalarValue);
                }
              });
            }
          } else {
            const scalarValue = stringifyScalar(item);
            if (scalarValue === null) {
              lines.push(itemPad + '- |');
              String(item).split('\n').forEach(function (part) { lines.push(' '.repeat(childIndent + 2) + part); });
            } else lines.push(itemPad + '- ' + scalarValue);
          }
        });
        return;
      }

      if (node && typeof node === 'object') {
        if (keyPrefix != null) lines.push(pad + keyPrefix + ':');
        const childPad = ' '.repeat(indent + (keyPrefix != null ? 2 : 0));
        const childIndent = indent + (keyPrefix != null ? 2 : 0);
        Object.keys(node).forEach(function (key) {
          const v = node[key];
          if (v && typeof v === 'object') write(v, childIndent, key, false);
          else {
            const scalarValue = stringifyScalar(v);
            if (scalarValue === null) {
              lines.push(childPad + key + ': |');
              String(v).split('\n').forEach(function (part) { lines.push(' '.repeat(childIndent + 2) + part); });
            } else lines.push(childPad + key + ': ' + scalarValue);
          }
        });
        return;
      }

      const scalarValue = stringifyScalar(node);
      if (keyPrefix != null) lines.push(pad + keyPrefix + ': ' + (scalarValue == null ? "''" : scalarValue));
      else if (inArray) lines.push(pad + '- ' + (scalarValue == null ? "''" : scalarValue));
    }

    write(value, 0, null, false);
    return lines.join('\n') + '\n';
  }

  global.YamlLite = Object.freeze({ parse, stringify });
})(window);
