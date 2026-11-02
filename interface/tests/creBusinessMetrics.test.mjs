import test from 'node:test';
import assert from 'node:assert/strict';
import { summarizeCreBusinessStatus } from '../services/creBusinessMetrics.js';

test('CRE business summary derives only active, pending, failed, and uncertain job counts', () => {
  const result = summarizeCreBusinessStatus({
    status: 'AVAILABLE',
    counts: {
      processing: { queued: 2, running: 1, failed: 1 },
      rendering: { queued: 1, succeeded: 4, uncertain: 2 },
      publishing: { awaiting_approval: 3, mock_accepted: 1, failed: 1 },
      pendingApprovals: 3,
    },
  });

  assert.deepEqual(result, {
    available: true,
    productionJobs: 4,
    pendingApprovals: 3,
    failedOrUncertainJobs: 4,
  });
  assert.equal('views' in result, false);
  assert.equal('revenue' in result, false);
  assert.equal('publications' in result, false);
});

test('CRE business summary preserves a genuine empty store as zero jobs', () => {
  assert.deepEqual(summarizeCreBusinessStatus({
    status: 'AVAILABLE',
    counts: {
      processing: {},
      rendering: {},
      publishing: {},
      pendingApprovals: 0,
    },
  }), {
    available: true,
    productionJobs: 0,
    pendingApprovals: 0,
    failedOrUncertainJobs: 0,
  });
});

test('CRE business summary does not turn missing or malformed counts into metrics', () => {
  assert.deepEqual(summarizeCreBusinessStatus(null), { available: false });
  assert.deepEqual(summarizeCreBusinessStatus({ status: 'UNAVAILABLE' }), { available: false });

  const result = summarizeCreBusinessStatus({
    status: 'AVAILABLE',
    counts: {
      processing: { queued: -2, running: 1.5 },
      rendering: { running: 1 },
      publishing: { uncertain: 1 },
      pendingApprovals: -1,
    },
  });

  assert.deepEqual(result, {
    available: true,
    productionJobs: 1,
    pendingApprovals: null,
    failedOrUncertainJobs: 1,
  });
});
