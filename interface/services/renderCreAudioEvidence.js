export function renderCreAudioEvidence(root, data, freshness, documentRef = document) {
  if (!root) return;
  root.replaceChildren();
  if (!data || !data.scriptPreviews) {
    const empty = documentRef.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'Audio evidence status is unavailable until authenticated CRE status is received.';
    root.append(empty);
    return;
  }
  if (data.scriptPreviews.state !== 'AVAILABLE') {
    const empty = documentRef.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = `Audio evidence status unavailable: ${data.scriptPreviews.reason || 'backend did not report a reason'}.`;
    root.append(empty);
    return;
  }
  if (!data.scriptPreviews.items.length) {
    const empty = documentRef.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'No owner-scoped script previews are recorded.';
    root.append(empty);
    return;
  }
  data.scriptPreviews.items.forEach((preview) => {
    const evidence = preview.audioEvidence;
    const card = documentRef.createElement('article');
    card.className = 'trading-record';
    const heading = documentRef.createElement('h3');
    const state = freshness === 'AVAILABLE'
      ? evidence.status
      : `STALE · last status ${evidence.status}`;
    heading.textContent = `${state} · preview ${preview.scriptId}`;
    const detail = documentRef.createElement('p');
    const metrics = evidence.sampleCount === null
      ? 'sample/clipping metrics unavailable'
      : `${evidence.sampleCount} samples · ${evidence.clippedSamples} clipped · ratio ${evidence.clippedSampleRatio} / ${evidence.clippingThreshold}`;
    detail.textContent = `Evidence ${evidence.evidenceId || 'unavailable'} · ${evidence.algorithmVersion || 'algorithm unavailable'} · created ${evidence.createdAt || 'not recorded'} · checked ${evidence.lastCheckedAt || 'not checked'} · last verified ${evidence.lastVerifiedAt || 'never'} · source ${evidence.sourceBinding} · script ${evidence.scriptBinding} · artifact ${evidence.artifactBinding} · ${metrics} · ${evidence.failureReason || evidence.nextAction}`;
    card.append(heading, detail);
    const visuals = preview.sceneVisualEvidence;
    if (visuals) {
      const visualHeading = documentRef.createElement('h4');
      const visualState = freshness === 'AVAILABLE'
        ? visuals.state
        : `STALE · last visual status ${visuals.state}`;
      visualHeading.textContent = `Scene visuals · ${visualState}`;
      const visualDetail = documentRef.createElement('p');
      visualDetail.textContent = `${visuals.scenes.length} scene(s) · ${visuals.statusScope || 'scope unavailable'} · checked ${visuals.lastCheckedAt || 'not checked'} · renderer ${visuals.rendererVersion || 'not recorded'} · ${visuals.failureReason || visuals.nextAction}`;
      card.append(visualHeading, visualDetail);
      visuals.scenes.forEach((scene) => {
        const sceneDetail = documentRef.createElement('p');
        const asset = scene.asset;
        sceneDetail.textContent = `Scene ${scene.order} · ${scene.narrativeRole} · ${scene.visualStrategy} · ${scene.renderedComposition || 'composition unavailable'} · ${scene.state} · source ${scene.sourceOffsets.start}-${scene.sourceOffsets.endExclusive} · ${asset ? `${asset.format} ${asset.width}×${asset.height} · SHA-256 ${asset.sha256}` : scene.failureReason || 'asset unavailable'}`;
        card.append(sceneDetail);
      });
    }
    root.append(card);
  });
}
