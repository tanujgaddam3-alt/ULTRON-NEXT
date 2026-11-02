import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const directory = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(directory, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(directory, 'operator-console.js'), 'utf8');
const css = fs.readFileSync(path.join(directory, 'operator-console.css'), 'utf8');

test('operator console preserves its existing functional destinations and labels placeholders', () => {
  for (const view of ['overview', 'chat', 'activity', 'operations', 'telemetry', 'agents', 'voice', 'tools', 'diagnostics']) {
    assert.match(html, new RegExp(`data-view="${view}"`));
    assert.match(html, new RegExp(`id="view-${view}"`));
  }
  assert.match(html, /id="view-not-implemented"/);
  assert.match(html, /No data or controls are presented here/);
  assert.doesNotMatch(html, /cognitiveCanvas|state-trigger-ribbon|8 ENVIRONMENTS/);
});

test('System Intelligence distinguishes current evidence and exposes only the manager-owned helper action', () => {
  for (const id of [
    'systemInventoryStatus', 'systemObservations', 'systemServices',
    'systemDependencies', 'systemOperationalEvents', 'systemDiagnosticRecords',
    'systemActionStatus', 'systemActionResult', 'systemRefreshButton',
    'restartAuthButton',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /Restart auth helper/);
  assert.match(html, /No operation has been requested/);
  assert.match(app, /state\.state === 'STALE'[\s\S]*'systemObservations'[\s\S]*'systemServices'/);
  assert.match(app, /response\.success/);
  assert.match(app, /requestSystemIntelligence/);
  assert.match(app, /restartAuthenticationHelper/);
  assert.match(app, /systemIntelligenceState\.state !== 'AVAILABLE'/);
  assert.match(app, /handoff\.ownershipStatus/);
  assert.match(app, /handoff\.managerPids/);
  assert.match(app, /handoff\.eligible === true/);
});

test('sidebar groups requested destinations into four collapsible workspaces', () => {
  const groups = [...html.matchAll(/<details class="nav-workspace" open>[\s\S]*?<summary>(.*?)<\/summary>([\s\S]*?)<\/details>/g)];
  assert.equal(groups.length, 4);
  assert.deepEqual(groups.map(([, name]) => name), [
    'ULTRON Core',
    'Trading',
    'Content Revenue Engine',
    'OMEGA',
  ]);
  for (const destination of [
    'overview', 'chat', 'agents', 'diagnostics', 'activity', 'core-settings',
    'trading', 'market-data', 'portfolio', 'trading-strategies', 'trading-orders',
    'trading-history', 'trading-risk', 'cre', 'cre-library', 'cre-rights',
    'cre-highlights', 'cre-production', 'cre-publishing', 'cre-analytics',
    'cre-revenue', 'cre-goals',     'omega', 'omega-intelligence', 'omega-evidence',
    'omega-diagnostics', 'omega-link', 'omega-exchanges',
  ]) {
    assert.match(html, new RegExp(`data-view="${destination}"`));
  }
  const destinations = [...html.matchAll(/data-view="([^"]+)"/g)].map(([, name]) => name);
  assert.equal(new Set(destinations).size, destinations.length, 'navigation routes must be unique');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(([, id]) => id);
  assert.equal(new Set(ids).size, ids.length, 'HTML IDs must be unique');
  for (const [, route, implemented] of html.matchAll(/<button class="nav-button[^"]*"[^>]*data-view="([^"]+)"([^>]*)>/g)) {
    const hasView = html.includes(`id="view-${route}"`);
    assert.ok(hasView || implemented.includes('data-implemented="false"'), `${route} must map to a view or be marked not implemented`);
  }
  assert.match(html, /data-view="core-settings" data-implemented="false"/);
  assert.match(html, /data-view="cre-revenue" data-implemented="false"/);
  assert.match(app, /view-not-implemented/);
});

test('CRE business dashboard labels unavailable sources without inventing platform or revenue data', () => {
  for (const id of [
    'creSuccessfulPublications', 'creTotalViews', 'creReportedRevenue',
    'creConfirmedPayouts', 'creProductionJobs', 'creAwaitingApproval',
    'creFailedUncertain', 'creGoalProgress', 'creBusinessFreshness',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  for (const platform of ['YouTube', 'Instagram', 'TikTok']) {
    assert.match(html, new RegExp(`<h2>${platform}</h2>[\\s\\S]*?Connection &amp; authorization[\\s\\S]*?Unavailable`));
  }
  assert.match(html, /no platform analytics/);
  assert.match(html, /no payout records/);
  assert.match(html, /no goal progress source/);
  assert.match(app, /summarizeCreBusinessStatus/);
  assert.match(app, /may be stale/);
  assert.match(app, /button\.dataset\.view === requestedView/);
  assert.match(app, /selectedButton\?\.closest\('\.nav-workspace'\)\?\.setAttribute\('open', ''\)/);
  assert.match(app, /history\.replaceState\(null, '', `#\$\{name\}`\)/);
});

test('CRE script preview is local-only and preserves explicit review boundaries', () => {
  for (const id of [
    'creScriptPreviewForm', 'creScriptInput', 'creScriptReferences',
    'creScriptPreviewVideo', 'creScriptPreviewManifest',
    'creScriptPreviewReviewForm', 'creScriptReviewNotes',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /local MP4 with deterministic script-derived diagrams/);
  assert.match(html, /References remain unverified/);
  assert.match(app, /requestCreScriptPreview/);
  assert.match(app, /publicationState/);
  assert.match(app, /renderCreAudioEvidence\(/);
});

test('activity filters and detail inspection use runtime events and separate receive time', () => {
  for (const id of ['eventSearch', 'eventSeverity', 'eventSource', 'eventType', 'eventTimeRange', 'resetEventFilters']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(app, /filterRuntimeEvents\(events/);
  assert.match(app, /Backend event time unavailable/);
  assert.match(app, /Event snapshot received in browser:/);
  assert.match(app, /Inspect event/);
  assert.match(app, /runtimeEventTime\(event\)/);
});

test('agent and skill inventory remains read-only and does not infer health', () => {
  assert.match(app, /getRuntime\(\)\?\.agents/);
  assert.match(app, /per-agent status and capabilities are not published/);
  assert.match(app, /channelData\.get\('device'\)\?\.capabilities/);
  assert.match(app, /Registry information only/);
  assert.match(html, /Listed actions are not invoked here/);
  assert.match(html, /does not publish per-action permissions, parameter schemas, or execution history/);
});

test('runtime values are explicitly unavailable until real channels arrive', () => {
  assert.match(html, /UNKNOWN/);
  assert.match(html, /No runtime snapshot received/);
  assert.match(html, /No event data received/);
  assert.match(app, /CHANNELS = \[/);
  assert.match(app, /bridge\.subscribe\(channel/);
  assert.match(app, /JSON\.stringify\(value, null, 2\)/);
  assert.doesNotMatch(app, /innerHTML\s*=/);
  assert.match(app, /renderMarkdownInto\(message, text\)/);
  assert.match(html, /id="overviewVoiceStatus"/);
  assert.match(app, /connected && previousProvenance !== 'LIVE'[\s\S]*channelData\.clear\(\)[\s\S]*channelTimes\.clear\(\)/);
});

test('chat preserves authenticated gateway contract and never enables offline sends', () => {
  assert.match(html, /maxlength="12000"/);
  assert.match(app, /bridge\.sendChat\(text\)/);
  assert.match(app, /connected && Boolean\(provider\?\.authenticated\)/);
  assert.match(app, /chatAuthenticated/);
  assert.match(app, /Transport: connected · Chat: \$\{provider\?\.authenticated \? 'authenticated' : 'authentication required'\}/);
  assert.match(html, /Chat requires an authenticated live backend session/);
  assert.match(app, /Transport: connected · Chat:/);
  assert.doesNotMatch(html, /From the authenticated command channel/);
});

test('operator credential supports password-manager autofill without app persistence', () => {
  assert.match(html, /id="operatorAuthToken" name="credential" type="password" autocomplete="current-password"/);
  assert.match(html, /gateway token is never sent to this page/);
  assert.match(app, /Local auth helper unavailable/);
  assert.match(app, /transportConnected === true/);
  assert.match(app, /The authentication helper is connected/);
  assert.match(app, /Local passphrase not accepted/);
  assert.match(app, /Local unlock temporarily locked/);
  assert.match(app, /Gateway authentication unavailable/);
  const credentialPrompt = app.match(
    /function requestOperatorCredential\(\) \{[\s\S]*?\n\}/
  )?.[0] || '';
  assert.ok(credentialPrompt);
  assert.doesNotMatch(credentialPrompt, /localStorage|sessionStorage/);
});

test('cancelled operator authentication is distinct from a rejected passphrase', () => {
  assert.match(app, /error\.code = 'AUTHENTICATION_CANCELLED'/);
  assert.match(app, /authenticationStatus === 'AUTHENTICATION_CANCELLED'[\s\S]*Authentication cancelled/);
  assert.match(app, /Authentication was cancelled before a credential was sent/);
});

test('voice controls use the existing voice client and playback implementation', () => {
  assert.match(html, /id="voiceEnabled"/);
  assert.match(html, /id="voiceAuto"/);
  assert.match(html, /id="stopSpeaking"/);
  assert.match(app, /import\('\.\/services\/voiceClient\.js'\)/);
  assert.match(app, /import\('\.\/services\/voicePlayback\.js'\)/);
  assert.match(app, /voicePlayback\.play\(text, context, \{ onTiming \}\)/);
  assert.match(app, /voicePlayback\.playSequence\(text, context, \{ onTiming \}\)/);
  assert.match(html, /id="voiceTestButton"/);
  assert.match(app, /voiceClient\.engines\(\)/);
  assert.match(app, /voiceClient\.styles\(\)/);
  assert.match(app, /async function playVoiceTest/);
  assert.match(app, /await playSpeech\(text\)/);
  assert.match(app, /byId\('voiceTestButton'\)\.disabled = !voiceEnabled\.checked/);
  assert.match(app, /if \(voiceEnabled\.checked && voiceAuto\.checked\) \{\s*await playSpeech\(result\.response, text, \{ timingId, sentenceSequence: true \}\);/);
  assert.equal((app.match(/sentenceSequence: true/g) || []).length, 1);
  assert.match(html, /id="voiceAuto" type="checkbox" disabled(?! checked)/);
});

test('diagnostics do not infer process health from a device snapshot', () => {
  assert.match(app, /Host \/ process telemetry/);
  assert.match(app, /per-process telemetry is not published/);
  assert.match(app, /portfolio\.error\?\.message/);
});

test('subsystem inspector starts closed and has an explicit close control', () => {
  assert.match(html, /id="inspector"[^>]*hidden/);
  assert.match(html, /id="inspectorBackdrop"[^>]*hidden/);
  assert.match(app, /event\.key === 'Escape'/);
  assert.match(app, /closeInspector\(\)/);
  assert.match(css, /@media \(max-width: 900px\)/);
});

test('the console has responsive, reduced-motion, and keyboard focus styles', () => {
  assert.match(css, /:focus-visible/);
  assert.match(css, /@media \(max-width: 430px\)/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.match(css, /overflow-wrap: anywhere/);
});

test('compact layouts keep navigation horizontal and chat controls readable', () => {
  assert.match(css, /\.navigation \{[^}]*height: calc\(100vh - 68px\)/s);
  assert.match(css, /@media \(max-width: 900px\) \{[\s\S]*?\.navigation \{[\s\S]*?height: auto;[\s\S]*?overflow-y: auto/s);
  assert.match(css, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(css, /\.nav-workspace-items \{ display: grid/);
  assert.match(css, /\.chat-history \{[^}]*max-height: min\(58vh, 680px\)/s);
  assert.match(css, /\.chat-form textarea \{ min-height: 84px; \}/);
  assert.match(css, /\.chat-form \{[^}]*background:/s);
});
