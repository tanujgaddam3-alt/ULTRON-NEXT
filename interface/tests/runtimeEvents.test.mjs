import test from 'node:test';
import assert from 'node:assert/strict';
import { eventFilterOptions, filterRuntimeEvents, runtimeEventTime } from '../services/runtimeEvents.js';

const events = [
  {
    eventId: 'event-1',
    type: 'heartbeat',
    summary: 'Runtime is waiting',
    source: 'interface-gateway',
    severity: 'INFO',
    provenance: 'LIVE',
  },
  {
    eventId: 'event-2',
    type: 'failure',
    summary: 'Voice service unavailable',
    source: 'voice',
    severity: 'ERROR',
    provenance: 'LIVE',
  },
];

test('event filters search only fields supplied by runtime events', () => {
  assert.deepEqual(filterRuntimeEvents(events, { query: 'voice' }), [events[1]]);
  assert.deepEqual(filterRuntimeEvents(events, { query: 'EVENT-1' }), [events[0]]);
});

test('event filters combine severity, source, and type without mutating input', () => {
  const original = structuredClone(events);
  assert.deepEqual(filterRuntimeEvents(events, {
    severity: 'INFO',
    source: 'interface-gateway',
    type: 'heartbeat',
  }), [events[0]]);
  assert.deepEqual(events, original);
  assert.deepEqual(filterRuntimeEvents(events, { severity: 'DEBUG' }), []);
});

test('event filter options contain only distinct non-empty runtime strings', () => {
  assert.deepEqual(eventFilterOptions([...events, events[0]], 'severity'), ['ERROR', 'INFO']);
  assert.deepEqual(eventFilterOptions([{ severity: 1 }, { severity: '' }], 'severity'), []);
  assert.deepEqual(filterRuntimeEvents(null), []);
});

test('time filtering uses supplied backend or gateway timestamps and excludes undated events', () => {
  const now = Date.parse('2026-10-18T12:00:00Z');
  const timed = [
    { ...events[0], timestamp: '2026-10-18T11:30:00Z' },
    { ...events[1], at: '2026-10-17T10:00:00Z' },
    events[0],
  ];
  assert.deepEqual(filterRuntimeEvents(timed, { timeRange: '1h' }, now), [timed[0]]);
  assert.deepEqual(filterRuntimeEvents(timed, { timeRange: '24h' }, now), [timed[0]]);
  assert.equal(filterRuntimeEvents(timed, { timeRange: 'unsupported' }, now).length, 0);
  assert.equal(runtimeEventTime({ timestamp: 'invalid', at: '2026-10-18T11:30:00Z' }).label, 'Gateway observed time');
  assert.equal(runtimeEventTime(events[0]), null);
});
