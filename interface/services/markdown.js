const INLINE_TOKEN = /`([^`\n]+)`|\[([^\]\n]+)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)|\*\*([^*\n]+)\*\*|__([^_\n]+)__|\*([^*\n]+)\*|_([^_\n]+)_/g;
const LIST_ITEM = /^\s*(?:([-+*])\s+|(\d+)\.\s+)(.*)$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/;

function appendInline(parent, text, doc) {
  INLINE_TOKEN.lastIndex = 0;
  let cursor = 0;
  let match;

  while ((match = INLINE_TOKEN.exec(text)) !== null) {
    if (match.index > cursor) {
      parent.append(doc.createTextNode(text.slice(cursor, match.index)));
    }

    let node;
    if (match[1] !== undefined) {
      node = doc.createElement('code');
      node.textContent = match[1];
    } else if (match[2] !== undefined) {
      const url = safeLink(match[3], doc.baseURI);
      if (url) {
        node = doc.createElement('a');
        node.href = url;
        node.target = '_blank';
        node.rel = 'noopener noreferrer';
        node.textContent = match[2];
      } else {
        node = doc.createTextNode(match[2]);
      }
    } else {
      node = doc.createElement(match[4] !== undefined || match[5] !== undefined ? 'strong' : 'em');
      node.textContent = match[4] ?? match[5] ?? match[6] ?? match[7];
    }

    parent.append(node);
    cursor = INLINE_TOKEN.lastIndex;
  }

  if (cursor < text.length) {
    parent.append(doc.createTextNode(text.slice(cursor)));
  }
}

function safeLink(value, baseURI) {
  try {
    const url = new URL(value, baseURI);
    return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function isBlockStart(line) {
  return /^ {0,3}```/.test(line) || HEADING.test(line) || LIST_ITEM.test(line);
}

function renderMarkdownInto(target, markdown) {
  const doc = target.ownerDocument;
  const lines = String(markdown ?? '').replace(/\r\n?/g, '\n').split('\n');
  target.replaceChildren();

  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (/^ {0,3}```/.test(line)) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^ {0,3}```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const pre = doc.createElement('pre');
      const code = doc.createElement('code');
      code.textContent = codeLines.join('\n');
      pre.append(code);
      target.append(pre);
      continue;
    }

    const heading = line.match(HEADING);
    if (heading) {
      const node = doc.createElement(`h${heading[1].length}`);
      appendInline(node, heading[2], doc);
      target.append(node);
      index += 1;
      continue;
    }

    const listMatch = line.match(LIST_ITEM);
    if (listMatch) {
      const ordered = listMatch[2] !== undefined;
      const list = doc.createElement(ordered ? 'ol' : 'ul');
      while (index < lines.length) {
        const itemMatch = lines[index].match(LIST_ITEM);
        if (!itemMatch || (itemMatch[2] !== undefined) !== ordered) break;
        const item = doc.createElement('li');
        appendInline(item, itemMatch[3], doc);
        list.append(item);
        index += 1;
      }
      target.append(list);
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index])) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    if (!paragraphLines.length) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    const paragraph = doc.createElement('p');
    appendInline(paragraph, paragraphLines.join(' '), doc);
    target.append(paragraph);
  }
}

export { renderMarkdownInto };
