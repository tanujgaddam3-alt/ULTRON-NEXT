import test from 'node:test';
import assert from 'node:assert/strict';
import { renderCreAudioEvidence } from '../services/renderCreAudioEvidence.js';

function element(tagName) {
  return {
    tagName,
    children: [],
    className: '',
    textContent: '',
    append(...nodes) { this.children.push(...nodes); },
  };
}

function render(status, freshness = 'AVAILABLE', visualState = 'VERIFIED') {
  const root = {
    children: [],
    replaceChildren() { this.children = []; },
    append(...nodes) { this.children.push(...nodes); },
  };
  const documentRef = { createElement: element };
  renderCreAudioEvidence(
    root,
    {
      scriptPreviews: {
        state: 'AVAILABLE',
        items: [{
          scriptId: 'preview-1',
          audioEvidence: {
            status,
            evidenceId: status === 'VERIFIED' ? 'a'.repeat(64) : null,
            algorithmVersion: status === 'VERIFIED'
              ? 'PCM_S16LE_MONO_24000_CLIP_32760_V1'
              : null,
            createdAt: status === 'VERIFIED' ? 'created-at' : null,
            lastCheckedAt: 'checked-at',
            lastVerifiedAt: status === 'VERIFIED' ? 'verified-at' : null,
            sourceBinding: status === 'VERIFIED' ? 'VERIFIED' : 'UNVERIFIED',
            scriptBinding: status === 'VERIFIED' ? 'VERIFIED' : 'UNVERIFIED',
            artifactBinding: status === 'VERIFIED' ? 'VERIFIED' : 'UNVERIFIED',
            sampleCount: status === 'VERIFIED' ? 10 : null,
            clippedSamples: status === 'VERIFIED' ? 0 : null,
            clippedSampleRatio: status === 'VERIFIED' ? 0 : null,
            clippingThreshold: 0.001,
            failureReason: status === 'VERIFIED' ? null : '<img src=x onerror=alert(1)>',
            nextAction: 'Regenerate the preview.',
          },
          sceneVisualEvidence: {
            state: visualState,
            rendererVersion: visualState === 'VERIFIED'
              ? 'pillow-scene-composer-v2'
              : null,
            failureReason: visualState === 'VERIFIED'
              ? null
              : 'SCENE_VISUAL_INTEGRITY_FAILURE',
            nextAction: 'Regenerate and inspect the preview.',
            scenes: [{
              sceneId: 'scene-01',
              order: 1,
              narrativeRole: 'EXPLANATION',
              visualStrategy: 'CONCEPT_MAP_DIAGRAM',
              sourceOffsets: { start: 0, endExclusive: 10 },
              excerpt: 'Source text',
              excerptSha256: 'b'.repeat(64),
              renderedComposition: 'EXPLANATORY_CONCEPT_MAP',
              state: visualState,
              failureReason: visualState === 'VERIFIED'
                ? null
                : 'SCENE_VISUAL_INTEGRITY_FAILURE',
              asset: visualState === 'VERIFIED'
                ? {
                    assetId: 'c'.repeat(64),
                    format: 'PNG',
                    width: 720,
                    height: 1280,
                    byteSize: 1024,
                    sha256: 'd'.repeat(64),
                    rendererVersion: 'pillow-scene-composer-v2',
                    generatedAt: 'generated-at',
                    validationStatus: 'TECHNICAL_CHECKS_PASSED',
                  }
                : null,
            }],
          },
        }],
      },
    },
    freshness,
    documentRef,
  );
  return root;
}

for (const status of [
  'VERIFIED',
  'UNVERIFIABLE',
  'MISSING',
  'INTEGRITY_FAILURE',
]) {
  test(`CRE audio evidence dashboard clearly renders ${status}`, () => {
    const root = render(status);
    assert.equal(root.children.length, 1);
    const [card] = root.children;
    assert.equal(card.className, 'trading-record');
    assert.match(card.children[0].textContent, new RegExp(`^${status} · preview preview-1$`));
    if (status === 'VERIFIED') {
      assert.match(card.children[1].textContent, /10 samples · 0 clipped/);
      assert.match(card.children[1].textContent, /a{64}/);
    } else {
      assert.match(card.children[1].textContent, /sample\/clipping metrics unavailable/);
      assert.match(card.children[1].textContent, /<img src=x onerror=alert\(1\)>/);
    }
    assert.match(card.children[2].textContent, /Scene visuals/);
    assert.match(card.children[4].textContent, /EXPLANATION/);
  });
}

test('CRE audio evidence dashboard never presents a stale verified snapshot as fresh', () => {
  const root = render('VERIFIED', 'STALE');
  assert.match(root.children[0].children[0].textContent, /^STALE · last status VERIFIED/);
  assert.doesNotMatch(root.children[0].children[0].textContent, /^VERIFIED ·/);
});

test('CRE dashboard distinguishes missing scene visuals from verified evidence', () => {
  const root = render('VERIFIED', 'AVAILABLE', 'MISSING');
  assert.match(root.children[0].children[2].textContent, /Scene visuals · MISSING/);
  assert.match(root.children[0].children[4].textContent, /MISSING/);
  assert.match(root.children[0].children[4].textContent, /SCENE_VISUAL_INTEGRITY_FAILURE/);
});

test('CRE dashboard renders scene visual integrity failures with the blocker', () => {
  const root = render('VERIFIED', 'AVAILABLE', 'INTEGRITY_FAILURE');
  assert.match(
    root.children[0].children[2].textContent,
    /Scene visuals · INTEGRITY_FAILURE/,
  );
  assert.match(
    root.children[0].children[4].textContent,
    /SCENE_VISUAL_INTEGRITY_FAILURE/,
  );
});

test('CRE dashboard marks retained scene visual evidence stale on disconnect', () => {
  const root = render('VERIFIED', 'STALE', 'VERIFIED');
  assert.match(root.children[0].children[2].textContent, /STALE · last visual status VERIFIED/);
});
