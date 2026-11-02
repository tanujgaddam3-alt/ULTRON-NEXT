import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  isCrePlanEligibleForRevision,
  isCreRecoveredOperationEligibleForRevision,
} from '../services/creRevisionEligibility.js';

const PROJECT_ID = 'cre-synthetic-project';
const OPERATION_ID = '123e4567-e89b-42d3-a456-426614174000';
const PLAN_HASH = 'a'.repeat(64);

function plan(projectId = PROJECT_ID, revision = 1) {
  return {
    projectId,
    planRevision: revision,
    planSha256: PLAN_HASH,
    sourceAssetIds: ['synthetic-source'],
    segments: [{ timelineOrder: 1 }],
  };
}

function operation(overrides = {}) {
  return {
    operationId: OPERATION_ID,
    projectId: PROJECT_ID,
    state: 'completed',
    result: {
      projectId: PROJECT_ID,
      planMode: 'structured',
      planVersion: 'cre-creative-plan-v2',
      planRevision: 1,
      planSha256: PLAN_HASH,
      sourceAssetIds: ['synthetic-source'],
    },
    ...overrides,
  };
}

test('new project revision eligibility requires a matching, complete structured plan', () => {
  assert.equal(isCrePlanEligibleForRevision(PROJECT_ID, plan()), true);
  assert.equal(isCrePlanEligibleForRevision(PROJECT_ID, plan('other-project')), false);
  assert.equal(isCrePlanEligibleForRevision(PROJECT_ID, { ...plan(), planRevision: 0 }), false);
  assert.equal(isCrePlanEligibleForRevision(PROJECT_ID, { ...plan(), planSha256: 'bad' }), false);
  assert.equal(isCrePlanEligibleForRevision(PROJECT_ID, { ...plan(), segments: [] }), false);
});

test('recovered revision eligibility preserves and validates the original project and operation IDs', () => {
  assert.equal(
    isCreRecoveredOperationEligibleForRevision(PROJECT_ID, OPERATION_ID, operation()),
    true,
  );
  assert.equal(
    isCreRecoveredOperationEligibleForRevision('another-project', OPERATION_ID, operation()),
    false,
  );
  assert.equal(
    isCreRecoveredOperationEligibleForRevision(PROJECT_ID, 'another-operation', operation()),
    false,
  );
  assert.equal(
    isCreRecoveredOperationEligibleForRevision(
      PROJECT_ID,
      OPERATION_ID,
      operation({ state: 'failed' }),
    ),
    false,
  );
  assert.equal(
    isCreRecoveredOperationEligibleForRevision(
      PROJECT_ID,
      OPERATION_ID,
      operation({ result: { ...operation().result, planVersion: 'legacy-v1' } }),
    ),
    false,
  );
});

test('recovery and revision actions remain bound to the saved project without creating another project', async () => {
  const source = await readFile(new URL('../operator-console.js', import.meta.url), 'utf8');
  const recovery = source.match(
    /async function recoverCrePrivateRender\(operationId, feedback\) \{[\s\S]*?\n  \}\n\n  async function resumeSavedCrePrivateRender/,
  )?.[0] || '';
  const revision = source.match(
    /byId\('creRevisionRender'\)\.addEventListener\('click', async \(\) => \{[\s\S]*?\n  \}\);/,
  )?.[0] || '';

  assert.ok(recovery.includes('isCreRecoveredOperationEligibleForRevision'));
  assert.ok(recovery.includes('crePrivateProjectId'));
  assert.ok(recovery.includes('byId(\'creRevisionRender\').hidden = !crePrivateRevisionEligible'));
  assert.doesNotMatch(recovery, /createCreProject|CRE_PROJECT_CREATE_REQUEST/);
  assert.ok(revision.includes('isCrePlanEligibleForRevision(crePrivateProjectId'));
  assert.ok(revision.includes('autoplanCreProject({'));
  assert.ok(revision.includes('submitCrePrivateRender(crePrivateProjectId, feedback)'));
});

test('dashboard declares its local favicon instead of requesting the missing default path', async () => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
  const icon = await readFile(new URL('../favicon.svg', import.meta.url), 'utf8');
  assert.match(html, /rel="icon"[^>]*href="\.\/favicon\.svg/);
  assert.match(icon, /<svg\b/);
});
