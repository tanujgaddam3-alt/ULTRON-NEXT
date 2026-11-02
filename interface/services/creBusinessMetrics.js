const ACTIVE_JOB_STATES = new Set(['queued', 'running']);
const FAILURE_JOB_STATES = new Set(['failed', 'uncertain']);
const JOB_TYPES = ['processing', 'rendering', 'publishing'];

function sumMatchingStates(counts, states) {
  if (!counts || typeof counts !== 'object' || Array.isArray(counts)) return 0;
  return Object.entries(counts).reduce((total, [state, count]) => {
    if (!states.has(state) || !Number.isSafeInteger(count) || count < 0) return total;
    return total + count;
  }, 0);
}

export function summarizeCreBusinessStatus(data) {
  if (!data || data.status !== 'AVAILABLE' || !data.counts) {
    return { available: false };
  }

  const counts = data.counts;
  const productionJobs = ['processing', 'rendering'].reduce(
    (total, type) => total + sumMatchingStates(counts[type], ACTIVE_JOB_STATES),
    0,
  );
  const failedOrUncertainJobs = JOB_TYPES.reduce(
    (total, type) => total + sumMatchingStates(counts[type], FAILURE_JOB_STATES),
    0,
  );
  const pendingApprovals = Number.isSafeInteger(counts.pendingApprovals)
    && counts.pendingApprovals >= 0
    ? counts.pendingApprovals
    : null;

  return {
    available: true,
    productionJobs,
    pendingApprovals,
    failedOrUncertainJobs,
  };
}
