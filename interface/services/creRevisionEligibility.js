const SHA256_PATTERN = /^[a-f0-9]{64}$/;

export function isCrePlanEligibleForRevision(projectId, plan) {
  return typeof projectId === 'string'
    && projectId.length > 0
    && plan?.projectId === projectId
    && Number.isSafeInteger(plan.planRevision)
    && plan.planRevision > 0
    && SHA256_PATTERN.test(plan.planSha256 || '')
    && Array.isArray(plan.sourceAssetIds)
    && plan.sourceAssetIds.length > 0
    && Array.isArray(plan.segments)
    && plan.segments.length > 0;
}

export function isCreRecoveredOperationEligibleForRevision(
  projectId,
  operationId,
  operation,
) {
  const result = operation?.result;
  return typeof projectId === 'string'
    && projectId.length > 0
    && typeof operationId === 'string'
    && operation.operationId === operationId
    && operation.projectId === projectId
    && operation.state === 'completed'
    && result?.projectId === projectId
    && result.planMode === 'structured'
    && result.planVersion === 'cre-creative-plan-v2'
    && Number.isSafeInteger(result.planRevision)
    && result.planRevision > 0
    && SHA256_PATTERN.test(result.planSha256 || '')
    && Array.isArray(result.sourceAssetIds)
    && result.sourceAssetIds.length > 0;
}
