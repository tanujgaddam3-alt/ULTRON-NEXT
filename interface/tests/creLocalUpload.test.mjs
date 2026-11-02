import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);

test('CRE dashboard exposes local autonomous editing controls', async () => {
  const html = await readFile(new URL('index.html', root), 'utf8');
  const consoleSource = await readFile(new URL('operator-console.js', root), 'utf8');
  const providerSource = await readFile(new URL('services/providers/websocketProvider.js', root), 'utf8');
  const stylesheet = await readFile(new URL('operator-console.css', root), 'utf8');

  assert.match(html, /<label id="creUploadMenuItem"[^>]*data-cre-file-action="upload">[\s\S]*?<input id="creSourceVideo" class="cre-source-input" type="file"[^>]*multiple[^>]*accept="video\/mp4/);
  assert.match(html, /operator-console\.css\?v=/);
  assert.match(html, /id="creAddMenuToggle"[^>]*aria-haspopup="menu"/);
  assert.match(html, /<label id="creUploadMenuItem" role="menuitem"[^>]*data-cre-file-action="upload">/);
  assert.match(html, /data-cre-file-action="clear" hidden>Clear selection/);
  assert.doesNotMatch(html, /Add more videos|data-cre-file-action="add"/);
  assert.match(html, /id="creSourceSelectedFile"[^>]*aria-live="polite"/);
  assert.match(html, /id="creFolderScan"[^>]*>Scan folder/);
  assert.match(html, /id="creFolderRegister"[^>]*>Register selected videos/);
  assert.match(html, /<h3 id="creFolderImportTitle">Import videos<\/h3>/);
  assert.match(html, /id="creFolderPath"[^>]*value="\/home\/ultron\/Videos\/CRE_Import"/);
  assert.match(html, /id="creFolderAvailability"[^>]*role="status"/);
  assert.match(html, /id="creFolderFileList"/);
  assert.match(html, /id="creFolderFeedback"/);
  assert.match(html, /cre-folder-import-6/);
  assert.match(html, /services\/providers\/websocketProvider\.js\?v=20261102-auth-local-transport-1/);
  assert.match(html, /<details class="cre-script-preview-details">[\s\S]*?<summary>Secondary workflow/);
  assert.match(html, /This separate tool accepts original script text, not video paths/);
  assert.match(html, /id="creSourceCount"/);
  assert.match(html, /data-go="cre-highlights"/);
  assert.match(html, /Select one or more videos · up to 32 MiB each/);
  assert.match(consoleSource, /allowedExtensions/);
  assert.match(consoleSource, /selectedCreFiles\.push\(file\)/);
  assert.match(consoleSource, /selectedCreFiles\.splice\(index, 1\)/);
  assert.match(consoleSource, /const files = \[\.\.\.selectedCreFiles\]/);
  assert.match(consoleSource, /creUploadMenuItem\.addEventListener\('keydown', \(event\) => \{[\s\S]*?creSourceVideo\.showPicker\(\)/);
  assert.match(consoleSource, /creClearMenuItem\.hidden = totalSelectedCount === 0/);
  assert.match(consoleSource, /selectedCreFolderFileIds\.clear\(\)/);
  assert.match(consoleSource, /creClearMenuItem\.addEventListener\('click'/);
  assert.match(consoleSource, /!provider\?\.authenticated[\s\S]*?bridge\?\.getProvenance\(\) !== 'LIVE'/);
  assert.match(consoleSource, /selectedCreFiles\.length < 1/);
  assert.doesNotMatch(consoleSource, /selectedCreFiles\.length < 2|files\.length < 2/);
  assert.match(consoleSource, /event\.key === 'ArrowDown'/);
  assert.match(consoleSource, /event\.key === 'Escape'/);
  assert.match(consoleSource, /document\.addEventListener\('pointerdown'/);
  assert.match(consoleSource, /creRegisteredSourceFiles\.indexOf\(file\)/);
  assert.match(consoleSource, /for \(let index = 0; index < files\.length; index \+= 1\)/);
  assert.match(consoleSource, /provider\.requestCreSourceVideo/);
  assert.match(consoleSource, /provider\.scanCreImportFolder\(directory\)/);
  assert.match(consoleSource, /Only \/home\/ultron\/Videos\/CRE_Import is authorized/);
  assert.match(consoleSource, /Authentication was cancelled before a credential was sent/);
  assert.match(consoleSource, /provider\.registerCreImportVideo/);
  assert.match(providerSource, /CRE_FOLDER_IMPORT_SCAN_REQUEST/);
  assert.match(providerSource, /CRE_FOLDER_IMPORT_REGISTER_REQUEST/);
  assert.match(consoleSource, /creFolderFiles\.filter/);
  assert.match(consoleSource, /creFolderPathInput\.addEventListener\('input', updateCreFileSelection\)/);
  assert.match(consoleSource, /creImportedFolderAssets\.set\(file\.fileId, asset\)/);
  assert.match(consoleSource, /selectedCreFolderFileIds\.has\(fileId\)/);
  assert.match(consoleSource, /source videos are registered and hash-verified/);
  assert.match(consoleSource, /if \(!provider\?\.authenticated \|\| bridge\?\.getProvenance\(\) !== 'LIVE'\)/);
  assert.match(consoleSource, /No upload was sent\. Authentication is required before source videos can be registered\./);
  assert.match(html, /id="creAutonomousInstruction"/);
  assert.match(html, /id="creProjectCreate"/);
  assert.match(html, /id="creProjectCreate"[^>]*disabled/);
  assert.match(html, /id="creProjectOutputVideo"[^>]*controls/);
  assert.match(consoleSource, /provider\.createCreProject/);
  assert.match(consoleSource, /provider\.autoplanCreProject/);
  assert.match(consoleSource, /provider\.renderPrivateCreProject/);
  assert.match(consoleSource, /saveCrePrivateRender\(projectId, operationId\);[\s\S]*?provider\.renderPrivateCreProject/);
  assert.match(consoleSource, /getPrivateCreRenderStatus\(operationId\)/);
  assert.match(consoleSource, /getPrivateCreRenderArtifact\(operationId\)/);
  assert.match(consoleSource, /globalThis\.crypto\.subtle\.digest\('SHA-256', bytes\)/);
  assert.match(consoleSource, /loadedmetadata/);
  assert.match(consoleSource, /loadeddata/);
  const recoveryFunction = consoleSource.match(
    /async function recoverCrePrivateRender\(operationId, feedback\) \{[\s\S]*?\n  \}\n\n  async function resumeSavedCrePrivateRender/
  )?.[0] || '';
  assert.ok(recoveryFunction);
  assert.doesNotMatch(recoveryFunction, /renderPrivateCreProject/);
  assert.match(consoleSource, /response\.payload\?\.state !== 'queued'[\s\S]*?provider\.renderPrivateCreProject/);
  assert.match(html, /id="creProjectRender"[^>]*>Recover render<\/button>/);
  assert.match(consoleSource, /creEditorOperationInProgress/);
  assert.match(consoleSource, /selectedCreFiles\.some\(\(file\) => !isValidCreFile\(file\)\)/);
  assert.match(consoleSource, /projectCreated = true/);
  assert.match(consoleSource, /revisionButton\.hidden = false/);
  assert.match(consoleSource, /revisionButton\.disabled = true/);
  assert.match(consoleSource, /CRE \$\{phase\} failed:/);
  assert.match(consoleSource, /creProjectCreationOutcomeUnknown/);
  assert.match(consoleSource, /do not retry or create another project until the gateway state is reconciled/);
  assert.match(consoleSource, /requestOutcomeIsUnknown/);
  assert.doesNotMatch(html, /<label for="creSourceTarget"/);
  assert.doesNotMatch(html, /<label for="creSourceAccount"/);
  assert.match(stylesheet, /--color-charcoal: #171717/);
  assert.match(stylesheet, /--color-forest: #06110c/);
  assert.match(stylesheet, /--color-crimson: #a31c1b/);
  assert.match(stylesheet, /\.navigation \{[\s\S]*?background: linear-gradient\(180deg, #06110c/);
  assert.match(stylesheet, /\.page-heading h1 \{[\s\S]*?font-size: clamp\(30px/);
  assert.match(stylesheet, /\.summary-card::after/);
  assert.match(stylesheet, /\.cre-add-button/);
  assert.match(stylesheet, /\.cre-file-remove/);
  assert.match(stylesheet, /\.cre-folder-path/);
  assert.match(stylesheet, /#view-cre > \.trading-panel\[aria-labelledby="creAutonomousEditorTitle"\][\s\S]*?padding: clamp/);
  assert.match(stylesheet, /\.cre-add-menu-list\s*\{[\s\S]*?position: absolute/);
  assert.match(stylesheet, /\.cre-add-menu-list \[role="menuitem"\]\[hidden\] \{ display: none; \}/);
  assert.match(stylesheet, /\.cre-source-input\s*\{[\s\S]*?inset: 0; z-index: 1; width: 100%; height: 100%;[\s\S]*?opacity: 0; cursor: pointer;/);
});

test('CRE upload menu overlays the native multiple-file input on its label and keeps keyboard access', async () => {
  const html = await readFile(new URL('index.html', root), 'utf8');
  const consoleSource = await readFile(new URL('operator-console.js', root), 'utf8');
  const stylesheet = await readFile(new URL('operator-console.css', root), 'utf8');
  assert.match(html, /<label id="creUploadMenuItem"[^>]*>[\s\S]*?<input id="creSourceVideo" class="cre-source-input" type="file"[^>]*multiple/);
  assert.match(consoleSource, /const creUploadMenuItem = byId\('creUploadMenuItem'\);[\s\S]*?creUploadMenuItem\.addEventListener\('keydown'/);
  assert.match(consoleSource, /event\.key !== 'Enter' && event\.key !== ' '/);
  assert.match(consoleSource, /creSourceVideo\.showPicker\(\)/);
  assert.match(consoleSource, /console\.error\('\[CRE\] Keyboard video picker invocation failed:', error\)/);
  assert.match(consoleSource, /\['pointerdown', 'click'\]\.forEach\(\(eventName\) => \{\s*creSourceVideo\.addEventListener\(eventName, \(event\) => \{\s*console\.debug\(`\[CRE\] Native file input received \$\{eventName\}\.`/);
  assert.match(consoleSource, /Native file picker returned a selection/);
  assert.match(consoleSource, /Native file picker was cancelled/);
  assert.match(consoleSource, /creSourceSelectedFile\.addEventListener\('dragover'/);
  assert.match(consoleSource, /creSourceSelectedFile\.addEventListener\('drop'/);
  assert.match(consoleSource, /Not added: \$\{invalidFiles\.join\(', '\)\}/);
  assert.match(html, /Drop supported videos here or use \+ to select files/);
  assert.match(stylesheet, /\.cre-file-list\.is-drop-target\s*\{[^}]*outline: 2px dashed/);
  assert.match(stylesheet, /\.cre-folder-file-row/);
  assert.match(consoleSource, /role="menuitem"\]:not\(:disabled\):not\(\[hidden\]\)/);
  assert.doesNotMatch(consoleSource, /const openCreFilePicker|creUploadMenuItem\.addEventListener\('click'/);
});

test('CRE editor accepts either one source video or a combined multi-video selection', async () => {
  const html = await readFile(new URL('index.html', root), 'utf8');
  const consoleSource = await readFile(new URL('operator-console.js', root), 'utf8');

  assert.match(html, /Select one or more videos/);
  assert.match(consoleSource, /selectedCreFiles\.length < 1/);
  assert.match(consoleSource, /selectedCreFiles\.length < 1 && selectedImportedAssets\.length < 1/);
  assert.match(consoleSource, /selectedCreFiles\.some\(\(selected\) => selected\.name === file\.name\s*&& selected\.size === file\.size && selected\.lastModified === file\.lastModified\)/);
  assert.match(consoleSource, /if \(files\.length < 1\) \{\s*throw new Error\('Select at least one local video file before starting an edit\.'\)/);
  assert.match(consoleSource, /if \(files\.length < 1 && importedAssets\.length < 1\) \{\s*feedback\.textContent = 'Select at least one local or registered folder video before starting an edit\.'/);
  assert.match(consoleSource, /for \(let index = 0; index < files\.length; index \+= 1\)/);
  assert.match(consoleSource, /creSourceAssets = \[\.\.\.importedAssets, \.\.\.uploadedAssets\]/);
});

test('CRE Overview owns the single authenticated editor and folder import controls', async () => {
  const html = await readFile(new URL('index.html', root), 'utf8');
  const editorIds = [
    'creAutonomousEditorTitle',
    'creAddMenuToggle',
    'creAddMenuList',
    'creUploadMenuItem',
    'creSourceVideo',
    'creFolderScan',
    'creFolderRegister',
    'creFolderFileList',
    'creSourceSelectedFile',
    'creProjectCreate',
    'creProjectOutputVideo',
  ];

  for (const id of editorIds) {
    assert.equal([...html.matchAll(new RegExp(`id="${id}"`, 'g'))].length, 1, `${id} must be unique`);
  }
  assert.match(html, /id="view-cre"[\s\S]*?<section class="panel trading-panel" aria-labelledby="creAutonomousEditorTitle">/);
  assert.doesNotMatch(html, /Video editing moved to CRE Overview/);
  assert.match(html, /Registered-source transcription and highlights/);
  assert.match(html, /id="creRightsReviewDetails"[\s\S]*<\/details>\s*<button id="creSourceResume"/);
  assert.match(html, /id="creTranscript"/);
  assert.match(html, /id="creCandidates"/);
  assert.match(html, /Secondary workflow: create a narrated preview from original script text/);
  assert.match(html, /id="view-cre"[\s\S]*?creFolderScan[\s\S]*?id="creProjectCreate"/);
});

test('CRE upload keeps only confirmed registrations and warns before an ambiguous retry', async () => {
  const consoleSource = await readFile(new URL('operator-console.js', root), 'utf8');

  assert.match(consoleSource, /response\.status !== 'SOURCE_REGISTERED'/);
  assert.match(consoleSource, /creUploadOutcomeUnknown = true/);
  assert.match(consoleSource, /no automatic retry was made/);
  assert.match(consoleSource, /explicitly retry; if the backend committed it, retrying may create a duplicate source/);
  assert.match(consoleSource, /creRegisteredSourceFiles\.indexOf\(file\)/);
});

test('dashboard distinguishes gateway auth failure classes without claiming success', async () => {
  const consoleSource = await readFile(new URL('operator-console.js', root), 'utf8');

  assert.match(consoleSource, /AUTHENTICATION_TIMEOUT/);
  assert.match(consoleSource, /Gateway authentication timed out/);
  assert.match(consoleSource, /AUTHENTICATION_UPSTREAM_REJECTED/);
  assert.match(consoleSource, /Gateway rejected configured authentication/);
  assert.match(consoleSource, /AUTHENTICATION_PROTOCOL_ERROR/);
  assert.match(consoleSource, /Gateway authentication protocol error/);
  assert.match(consoleSource, /provider\?\.authenticationStatus === 'AUTHENTICATION_UPSTREAM_REJECTED'[\s\S]*?chatAuthenticated \? 'Authenticated session'/);
});
