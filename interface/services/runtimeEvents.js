const FILTER_FIELDS = ['eventId', 'type', 'summary', 'source', 'severity', 'result', 'provenance'];

export function runtimeEventTime(event) {
  for (const [field, label] of [
    ['timestamp', 'Backend event timestamp (source or gateway fallback)'],
    ['at', 'Gateway observed time'],
  ]) {
    if (event?.[field] == null) continue;
    const milliseconds = new Date(event[field]).getTime();
    if (!Number.isNaN(milliseconds)) return { milliseconds, label };
  }
  return null;
}

export function eventFilterOptions(events, field) {
  return [...new Set(events
    .map((event) => event?.[field])
    .filter((value) => typeof value === 'string' && value.trim())
    .map((value) => value.trim()))]
    .sort((left, right) => left.localeCompare(right));
}

export function filterRuntimeEvents(events, filters = {}, now = Date.now()) {
  if (!Array.isArray(events)) return [];
  const query = String(filters.query || '').trim().toLocaleLowerCase();
  return events.filter((event) => {
    if (filters.severity && event?.severity !== filters.severity) return false;
    if (filters.source && event?.source !== filters.source) return false;
    if (filters.type && event?.type !== filters.type) return false;
    if (filters.timeRange) {
      const eventTime = runtimeEventTime(event)?.milliseconds ?? Number.NaN;
      const rangeMs = { '1h': 60 * 60 * 1000, '24h': 24 * 60 * 60 * 1000, '7d': 7 * 24 * 60 * 60 * 1000 }[filters.timeRange];
      if (!rangeMs || Number.isNaN(eventTime) || eventTime > now || now - eventTime > rangeMs) return false;
    }
    if (!query) return true;
    return FILTER_FIELDS.some((field) =>
      String(event?.[field] ?? '').toLocaleLowerCase().includes(query));
  });
}
