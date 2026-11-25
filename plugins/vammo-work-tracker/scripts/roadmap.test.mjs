import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { test } from 'node:test';

// Helper API: validateManifest, deriveWorkIntentId, topologicalOrder,
// buildPreview({ manifest, reconciliation }), and
// planApprovedOperations({ manifest, reconciliation, approvalDigest }).
// CLI: roadmap.mjs preview --manifest FILE --snapshot FILE; roadmap.mjs plan
// --manifest FILE --snapshot FILE --approved-digest DIGEST. The CLI plans only;
// it has no MCP client or publishing command.
const helperPath = fileURLToPath(new URL('../skills/vammo-roadmap-to-tickets/scripts/roadmap.mjs', import.meta.url));
const skillDir = fileURLToPath(new URL('../skills/vammo-roadmap-to-tickets/', import.meta.url));
const loadHelper = () => import(pathToFileURL(helperPath));

function manifest() {
  return {
    sourceId: 'synthetic:roadmap:fleet-health',
    revision: 'rev-1',
    destination: {
      projectId: 'project-synthetic',
      responsibility: { kind: 'team', teamId: 'team-synthetic' },
    },
    items: [
      {
        id: 'repair-alerts',
        title: 'Repair battery alert routing',
        type: 'MAINTENANCE',
        descriptionMarkdown: 'Route low-health alerts to the battery review queue.\n\nKeep the rider message unchanged.',
        acceptanceCriteria: ['Alerts with low health reach the battery review queue.', 'Rider message text remains unchanged.'],
        lane: 'B',
        dependsOn: ['inspect-health'],
        completionPolicy: 'REVIEWED_DELIVERABLE',
        reviewerId: 'reviewer-synthetic',
        externalAuthorizationGates: [],
      },
      {
        id: 'fleet-health',
        title: 'Improve fleet battery health handling',
        type: 'EPIC',
        descriptionMarkdown: 'Track safe battery handling from inspection through alert routing.',
        acceptanceCriteria: ['The child work is grouped under this epic.'],
        lane: null,
        dependsOn: [],
        completionPolicy: 'DELIVERABLE',
      },
      {
        id: 'inspect-health',
        title: 'Inspect battery health signals',
        type: 'FEATURE',
        descriptionMarkdown: 'Compare the latest health signal with the configured threshold.',
        acceptanceCriteria: ['The inspection records the health signal.', 'Threshold comparison is visible to the reviewer.'],
        lane: 'A',
        dependsOn: [],
        completionPolicy: 'DELIVERABLE',
        externalAuthorizationGates: [
          { id: 'owner-approval', description: 'Battery owner approves the threshold change.' },
        ],
      },
    ],
  };
}

function reconciliation(items = [], relations = [], status = 'complete') {
  return { status, items, relations };
}

async function itemSnapshot(api, source, id, key, acceptanceCriteriaCount, parentKey = null) {
  return {
    key,
    workIntentId: await api.deriveWorkIntentId(source.sourceId, id),
    parentKey,
    acceptanceCriteriaCount,
  };
}

test('preview includes all item content, gates and correctly directed edges without mutations', async () => {
  const api = await loadHelper();
  const source = manifest();
  const preview = api.buildPreview({ manifest: source, reconciliation: reconciliation() });

  assert.equal(preview.sourceId, source.sourceId);
  assert.equal(preview.revision, source.revision);
  assert.deepEqual(preview.destination, source.destination);
  assert.equal(preview.items.length, 3);
  for (const item of source.items) {
    const rendered = preview.items.find((candidate) => candidate.id === item.id);
    assert.equal(rendered.title, item.title);
    assert.equal(rendered.type, item.type);
    assert.equal(rendered.descriptionMarkdown, item.descriptionMarkdown);
    assert.deepEqual(rendered.acceptanceCriteria, item.acceptanceCriteria);
    assert.equal(rendered.lane, item.lane);
  }
  assert.equal(preview.items.find((item) => item.id === 'fleet-health').type, 'EPIC');
  assert.equal(preview.items.find((item) => item.id === 'inspect-health').lane, 'A');
  assert.deepEqual(preview.items.find((item) => item.id === 'inspect-health').externalAuthorizationGates, source.items[2].externalAuthorizationGates);
  assert.equal(preview.items.find((item) => item.id === 'repair-alerts').reviewerId, 'reviewer-synthetic');
  assert.deepEqual(preview.edges, [
    { type: 'PARENT_OF', fromId: 'fleet-health', toId: 'inspect-health' },
    { type: 'PARENT_OF', fromId: 'fleet-health', toId: 'repair-alerts' },
    { type: 'DEPENDS_ON', fromId: 'repair-alerts', toId: 'inspect-health' },
  ]);
  assert.equal(Object.hasOwn(preview, 'operations'), false);
  assert.equal(Object.hasOwn(preview, 'mutationOperations'), false);
  assert.equal(typeof preview.digest, 'string');
});

test('missing approval or a stale proposal digest refuses planning with zero mutations', async () => {
  const api = await loadHelper();
  const source = manifest();
  const current = reconciliation();
  const preview = api.buildPreview({ manifest: source, reconciliation: current });
  for (const approvalDigest of [undefined, 'stale-digest']) {
    const plan = api.planApprovedOperations({ manifest: source, reconciliation: current, approvalDigest });
    assert.equal(plan.status, 'refused');
    assert.deepEqual(plan.operations, []);
  }
});

test('publication validation requires destination responsibility and a named required reviewer', async () => {
  const api = await loadHelper();
  const noResponsibility = manifest();
  delete noResponsibility.destination.responsibility;
  assert.throws(() => api.validateManifest(noResponsibility), /responsibility/i);

  const noReviewer = manifest();
  delete noReviewer.items.find((item) => item.id === 'repair-alerts').reviewerId;
  assert.throws(() => api.validateManifest(noReviewer), /reviewer/i);
  const preview = api.buildPreview({ manifest: noReviewer, reconciliation: reconciliation() });
  const plan = api.planApprovedOperations({ manifest: noReviewer, reconciliation: reconciliation(), approvalDigest: preview.digest });
  assert.equal(plan.status, 'refused');
  assert.deepEqual(plan.operations, []);
});

test('work intent identity survives wording, revision and input-order changes', async () => {
  const api = await loadHelper();
  const original = manifest();
  const revised = manifest();
  revised.revision = 'rev-2';
  revised.items.reverse();
  revised.items.find((item) => item.id === 'inspect-health').title = 'Review battery condition';

  assert.equal(
    await api.deriveWorkIntentId(original.sourceId, 'inspect-health'),
    await api.deriveWorkIntentId(revised.sourceId, 'inspect-health'),
  );
  const originalPreview = api.buildPreview({ manifest: original, reconciliation: reconciliation() });
  const revisedPreview = api.buildPreview({ manifest: revised, reconciliation: reconciliation() });
  assert.equal(
    originalPreview.items.find((item) => item.id === 'inspect-health').workIntentId,
    revisedPreview.items.find((item) => item.id === 'inspect-health').workIntentId,
  );
  assert.notEqual(
    await api.deriveWorkIntentId(original.sourceId, 'inspect-health'),
    await api.deriveWorkIntentId(original.sourceId, 'repair-alerts'),
  );
  assert.notEqual(
    await api.deriveWorkIntentId(original.sourceId, 'inspect-health'),
    await api.deriveWorkIntentId('synthetic:roadmap:other', 'inspect-health'),
  );
});

test('a null manifest workIntentId is treated as absent and derives from source identity', async () => {
  const api = await loadHelper();
  const source = manifest();
  const item = source.items.find((candidate) => candidate.id === 'inspect-health');
  item.workIntentId = null;

  assert.equal(api.validateManifest(source), true);
  const preview = api.buildPreview({ manifest: source, reconciliation: reconciliation() });
  assert.equal(
    preview.items.find((candidate) => candidate.id === 'inspect-health').workIntentId,
    await api.deriveWorkIntentId(source.sourceId, 'inspect-health'),
  );

  const conflicting = structuredClone(source);
  conflicting.items.find((candidate) => candidate.id === 'inspect-health').workIntentId = 'wi-conflicting-identity';
  assert.throws(() => api.validateManifest(conflicting), /source identity/i);
});

test('dependency order places the epic first and each prerequisite before its dependent', async () => {
  const api = await loadHelper();
  assert.deepEqual(api.topologicalOrder(manifest()), ['fleet-health', 'inspect-health', 'repair-alerts']);
});

test('validation rejects unknown endpoints, self edges, cycles and duplicate IDs or keys', async () => {
  const api = await loadHelper();
  const cases = [
    ['unknown dependency', (value) => { value.items[0].dependsOn = ['missing']; }],
    ['self dependency', (value) => { value.items[2].dependsOn = ['inspect-health']; }],
    ['cycle', (value) => { value.items[2].dependsOn = ['repair-alerts']; }],
    ['duplicate local ID', (value) => { value.items[0].id = value.items[2].id; }],
    ['duplicate existing key', (value) => { value.items[0].existingKey = 'VAM-900'; value.items[2].existingKey = 'VAM-900'; }],
  ];
  for (const [label, edit] of cases) {
    const source = manifest();
    edit(source);
    assert.throws(() => api.validateManifest(source), undefined, label);
  }
});

test('approved plan creates only missing items and uses resolved keys for missing edges', async () => {
  const api = await loadHelper();
  const source = manifest();
  const snapshot = reconciliation([
    await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
    await itemSnapshot(api, source, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
    { key: 'VAM-999', workIntentId: null, parentKey: 'VAM-900', acceptanceCriteriaCount: 2 },
  ], [
    { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-901' },
  ]);
  const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });

  assert.equal(plan.status, 'approved');
  assert.equal(plan.operations.every((op) => ['CREATE_ITEM', 'CREATE_RELATION'].includes(op.type)), true);
  assert.deepEqual(plan.operations.filter((op) => op.type === 'CREATE_ITEM').map((op) => op.localId), ['repair-alerts']);
  assert.deepEqual(plan.operations.filter((op) => op.type === 'CREATE_RELATION').map((op) => [op.relation, op.fromKey ?? op.fromLocalId, op.toKey ?? op.toLocalId]), [
    ['PARENT_OF', 'VAM-900', 'repair-alerts'],
    ['DEPENDS_ON', 'repair-alerts', 'VAM-901'],
  ]);
});

test('complete second readback proves zero-write completion without approval or planning', async () => {
  const api = await loadHelper();
  const source = manifest();
  const snapshot = reconciliation([
    await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
    await itemSnapshot(api, source, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
    await itemSnapshot(api, source, 'repair-alerts', 'VAM-902', 2, 'VAM-900'),
  ], [
    { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-901' },
    { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-902' },
    { type: 'DEPENDS_ON', fromKey: 'VAM-902', toKey: 'VAM-901' },
  ]);
  const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  assert.deepEqual(preview.blockers, []);
  assert.equal(preview.items.every((item) => item.decision === 'reuse'), true);
  assert.equal(preview.edgeDecisions.every((edge) => edge.decision === 'reuse'), true);
});

test('failed or incomplete readback never turns an unresolved item into an absence', async () => {
  const api = await loadHelper();
  const source = manifest();
  const partialItems = [
    await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
    await itemSnapshot(api, source, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
  ];
  for (const status of ['incomplete', 'failed']) {
    const current = reconciliation(partialItems, [], status);
    const preview = api.buildPreview({ manifest: source, reconciliation: current });
    const plan = api.planApprovedOperations({ manifest: source, reconciliation: current, approvalDigest: preview.digest });
    assert.equal(plan.status, 'refused');
    assert.deepEqual(plan.operations, []);
  }
});

test('an explicit existing key adopts a verified manual item without creating it', async () => {
  const api = await loadHelper();
  const source = manifest();
  const manual = source.items.find((item) => item.id === 'repair-alerts');
  manual.existingKey = 'VAM-919';
  const sourceBefore = structuredClone(source);
  const snapshot = reconciliation([
    {
      key: 'VAM-919',
      workIntentId: null,
      parentKey: 'VAM-900',
      acceptanceCriteriaCount: manual.acceptanceCriteria.length,
      title: manual.title,
      type: manual.type,
      descriptionMarkdown: `${manual.descriptionMarkdown}\n\nHuman note: retain this source reference.`,
      acceptanceCriteria: manual.acceptanceCriteria,
    },
    await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
    await itemSnapshot(api, source, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
  ], [
    { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-901' },
    { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-919' },
    { type: 'DEPENDS_ON', fromKey: 'VAM-919', toKey: 'VAM-901' },
  ]);
  const snapshotBefore = structuredClone(snapshot);
  const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });

  assert.equal(plan.status, 'approved');
  assert.equal(plan.items.find((item) => item.id === 'repair-alerts').resolvedKey, 'VAM-919');
  assert.equal(plan.items.find((item) => item.id === 'repair-alerts').decision, 'reuse');
  assert.equal(plan.items.find((item) => item.id === 'repair-alerts').descriptionMarkdown, manual.descriptionMarkdown);
  assert.deepEqual(plan.operations, []);
  assert.deepEqual(source, sourceBefore);
  assert.deepEqual(snapshot, snapshotBefore);
});

test('manual adoption still blocks title, type or criterion text mismatches', async () => {
  const api = await loadHelper();
  const edits = [
    ['title', (manual) => { manual.title = 'A different title'; }],
    ['type', (manual) => { manual.type = 'FEATURE'; }],
    ['criteria', (manual) => { manual.acceptanceCriteria[0] = 'A different criterion'; }],
  ];

  for (const [label, edit] of edits) {
    const source = manifest();
    const manual = source.items.find((item) => item.id === 'repair-alerts');
    manual.existingKey = 'VAM-919';
    const live = {
      key: 'VAM-919',
      workIntentId: null,
      parentKey: 'VAM-900',
      acceptanceCriteriaCount: manual.acceptanceCriteria.length,
      title: manual.title,
      type: manual.type,
      descriptionMarkdown: `${manual.descriptionMarkdown}\n\nHuman-authored note.`,
      acceptanceCriteria: [...manual.acceptanceCriteria],
    };
    edit(live);
    const snapshot = reconciliation([
      await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
      await itemSnapshot(api, source, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
      live,
    ], [
      { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-901' },
      { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-919' },
      { type: 'DEPENDS_ON', fromKey: 'VAM-919', toKey: 'VAM-901' },
    ]);
    const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
    const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });
    assert.equal(plan.status, 'refused', `${label} mismatch`);
    assert.deepEqual(plan.operations, [], `${label} mismatch`);
  }
});

test('manual criterion fidelity removes only checkbox-container indentation', async () => {
  const api = await loadHelper();
  const source = manifest();
  const item = source.items.find((candidate) => candidate.id === 'repair-alerts');
  item.existingKey = 'VAM-919';
  item.acceptanceCriteria = ['Criterion with `literal --flag`.\n  - bullet `first`\n    - deeper `second`'];
  const live = {
    key: 'VAM-919',
    workIntentId: null,
    parentKey: 'VAM-900',
    title: item.title,
    type: item.type,
    completionPolicy: item.completionPolicy,
    reviewerId: item.reviewerId,
    acceptanceCriteriaCount: 1,
    acceptanceCriteria: [{ text: 'Criterion with `literal --flag`.\n- bullet `first`\n  - deeper `second`', status: 'TODO' }],
  };
  const snapshot = reconciliation([
    await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
    await itemSnapshot(api, source, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
    live,
  ], [
    { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-901' },
    { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-919' },
    { type: 'DEPENDS_ON', fromKey: 'VAM-919', toKey: 'VAM-901' },
  ]);

  const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });
  assert.deepEqual(preview.blockers, []);
  assert.equal(preview.items.find((candidate) => candidate.id === 'repair-alerts').decision, 'reuse');
  assert.deepEqual(preview.items.find((candidate) => candidate.id === 'repair-alerts').acceptanceCriteria, item.acceptanceCriteria);
  assert.equal(plan.status, 'approved');
  assert.deepEqual(plan.operations, []);

  live.acceptanceCriteria[0].text = 'Criterion with `literal --flag`.\n- bullet has different content\n  - deeper `second`';
  const mismatched = api.buildPreview({ manifest: source, reconciliation: snapshot });
  assert.ok(mismatched.blockers.some((blocker) => /criterion text mismatch|source content/i.test(blocker)));
  const refused = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: mismatched.digest });
  assert.equal(refused.status, 'refused');
  assert.deepEqual(refused.operations, []);
});

test('a same-title/type live candidate is shown without adoption and blocks mutation until confirmation', async () => {
  const api = await loadHelper();
  const source = manifest();
  const item = source.items.find((candidate) => candidate.id === 'repair-alerts');
  const snapshot = reconciliation([
    await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
    await itemSnapshot(api, source, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
    {
      key: 'VAM-919',
      workIntentId: null,
      parentKey: 'VAM-900',
      title: item.title,
      type: item.type,
      acceptanceCriteriaCount: item.acceptanceCriteria.length,
      acceptanceCriteria: item.acceptanceCriteria.map((text) => ({ text, status: 'TODO' })),
    },
  ]);
  const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const candidate = preview.items.find((entry) => entry.id === 'repair-alerts');
  const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });

  assert.deepEqual(candidate.candidateKeys, ['VAM-919']);
  assert.equal(candidate.resolvedKey, null);
  assert.equal(candidate.decision, 'create');
  assert.ok(preview.blockers.some((blocker) => blocker.includes('VAM-919')));
  assert.equal(plan.status, 'refused');
  assert.deepEqual(plan.operations, []);
});

test('multiple same-title/type candidates remain ambiguous and all keys appear in the blocker', async () => {
  const api = await loadHelper();
  const source = manifest();
  const item = source.items.find((candidate) => candidate.id === 'repair-alerts');
  const manualCandidate = (key) => ({
    key,
    workIntentId: null,
    parentKey: 'VAM-900',
    title: item.title,
    type: item.type,
    acceptanceCriteriaCount: item.acceptanceCriteria.length,
    acceptanceCriteria: item.acceptanceCriteria.map((text) => ({ text, status: 'TODO' })),
  });
  const snapshot = reconciliation([
    await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
    await itemSnapshot(api, source, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
    manualCandidate('VAM-919'),
    manualCandidate('VAM-920'),
  ]);
  const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const candidate = preview.items.find((entry) => entry.id === 'repair-alerts');
  const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });

  assert.deepEqual(candidate.candidateKeys, ['VAM-919', 'VAM-920']);
  assert.equal(candidate.resolvedKey, null);
  assert.equal(candidate.decision, 'create');
  assert.ok(preview.blockers.some((blocker) => blocker.includes('VAM-919') && blocker.includes('VAM-920')));
  assert.equal(plan.status, 'refused');
  assert.deepEqual(plan.operations, []);
});

test('explicit existingKey confirmation can reuse a displayed same-title/type candidate', async () => {
  const api = await loadHelper();
  const source = manifest();
  const item = source.items.find((candidate) => candidate.id === 'repair-alerts');
  item.existingKey = 'VAM-919';
  const snapshot = reconciliation([
    await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
    await itemSnapshot(api, source, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
    {
      key: 'VAM-919',
      workIntentId: null,
      parentKey: 'VAM-900',
      title: item.title,
      type: item.type,
      acceptanceCriteriaCount: item.acceptanceCriteria.length,
      acceptanceCriteria: item.acceptanceCriteria.map((text) => ({ text, status: 'TODO' })),
    },
  ], [
    { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-901' },
    { type: 'DEPENDS_ON', fromKey: 'VAM-919', toKey: 'VAM-901' },
  ]);
  const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const unapproved = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: 'stale-digest' });
  const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });

  assert.equal(preview.items.find((entry) => entry.id === 'repair-alerts').resolvedKey, 'VAM-919');
  assert.equal(preview.items.find((entry) => entry.id === 'repair-alerts').decision, 'reuse');
  assert.deepEqual(preview.blockers, []);
  assert.equal(unapproved.status, 'refused');
  assert.deepEqual(unapproved.operations, []);
  assert.equal(plan.status, 'approved');
  assert.deepEqual(plan.operations, [{
    type: 'CREATE_RELATION',
    relation: 'PARENT_OF',
    fromKey: 'VAM-900',
    toKey: 'VAM-919',
  }]);
});

test('adopted reviewed work without a reviewer preserves policy and plans zero writes', async () => {
  const api = await loadHelper();
  const source = manifest();
  const item = source.items.find((candidate) => candidate.id === 'repair-alerts');
  item.existingKey = 'VAM-920';
  delete item.reviewerId;
  const sourceBefore = structuredClone(source);
  const snapshot = reconciliation([
    await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
    await itemSnapshot(api, source, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
    {
      key: 'VAM-920',
      workIntentId: null,
      parentKey: 'VAM-900',
      acceptanceCriteriaCount: item.acceptanceCriteria.length,
      title: item.title,
      type: item.type,
      descriptionMarkdown: `${item.descriptionMarkdown}\n\nHuman-authored pilot note.`,
      acceptanceCriteria: [...item.acceptanceCriteria],
      completionPolicy: 'REVIEWED_DELIVERABLE',
      reviewerId: null,
    },
  ], [
    { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-901' },
    { type: 'PARENT_OF', fromKey: 'VAM-900', toKey: 'VAM-920' },
    { type: 'DEPENDS_ON', fromKey: 'VAM-920', toKey: 'VAM-901' },
  ]);
  const snapshotBefore = structuredClone(snapshot);

  assert.throws(() => api.validateManifest(source), /reviewer/i);
  const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });

  assert.equal(plan.status, 'approved');
  assert.deepEqual(plan.operations, []);
  const plannedItem = plan.items.find((candidate) => candidate.id === 'repair-alerts');
  assert.equal(plannedItem.completionPolicy, 'REVIEWED_DELIVERABLE');
  assert.equal(plannedItem.reviewerId, null);
  assert.deepEqual(plannedItem.existingMetadata, { completionPolicy: 'REVIEWED_DELIVERABLE', reviewerId: null });
  assert.deepEqual(source, sourceBefore);
  assert.deepEqual(snapshot, snapshotBefore);
});

test('draft preview keeps source items visible while source identity, team and reviewer are pending', async () => {
  const api = await loadHelper();
  const source = manifest();
  source.sourceId = null;
  source.destination.responsibility = { kind: 'team' };
  delete source.items.find((item) => item.id === 'repair-alerts').reviewerId;
  const sourceBefore = structuredClone(source);
  const snapshot = reconciliation();
  const snapshotBefore = structuredClone(snapshot);

  assert.throws(() => api.validateManifest(source), /sourceId/i);
  const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const pendingFields = preview.pendingMetadata.map((entry) => entry.field);
  assert.equal(preview.items.length, source.items.length);
  assert.equal(preview.items.find((item) => item.id === 'repair-alerts').title, 'Repair battery alert routing');
  assert.equal(preview.items.every((item) => item.workIntentId === null), true);
  assert.ok(pendingFields.includes('sourceId'));
  assert.ok(pendingFields.includes('destination.responsibility.teamId'));
  assert.ok(pendingFields.includes('items.repair-alerts.reviewerId'));
  const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });
  assert.equal(plan.status, 'refused');
  assert.deepEqual(plan.operations, []);
  assert.deepEqual(source, sourceBefore);
  assert.deepEqual(snapshot, snapshotBefore);
});

test('draft preview remains renderable when destination is absent or null', async () => {
  const api = await loadHelper();
  const cases = [
    ['absent', () => {
      const source = manifest();
      delete source.destination;
      return source;
    }],
    ['null', () => {
      const source = manifest();
      source.destination = null;
      return source;
    }],
  ].map(([name, makeManifest]) => [name, makeManifest()]);
  const snapshot = reconciliation([], [], 'complete');
  const previews = [];
  const errors = [];

  for (const [name, source] of cases) {
    try {
      previews.push({ name, source, preview: api.buildPreview({ manifest: source, reconciliation: snapshot }) });
    } catch (error) {
      errors.push(`${name}: ${error.message}`);
    }
  }

  assert.deepEqual(errors, [], 'both pending destination shapes must produce a preview');
  for (const { name, source, preview } of previews) {
    const pendingFields = preview.pendingMetadata.map((entry) => entry.field);
    assert.equal(preview.readbackStatus, 'complete', name);
    assert.equal(preview.items.length, source.items.length, name);
    assert.ok(preview.items.every((item) => item.workIntentId !== null), name);
    assert.ok(pendingFields.includes('destination.projectId'), name);
    assert.ok(pendingFields.includes('destination.responsibility'), name);
    assert.ok(preview.blockers.some((blocker) => blocker.includes('destination.projectId')), name);
    assert.ok(preview.blockers.some((blocker) => blocker.includes('destination.responsibility')), name);
    assert.throws(() => api.validateManifest(source), /destination\.projectId/i, name);

    const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });
    assert.equal(plan.status, 'refused', name);
    assert.deepEqual(plan.operations, [], name);
  }
});

test('approval digest becomes stale after content, destination, gate or resolved mapping changes', async () => {
  const api = await loadHelper();
  const source = manifest();
  const snapshot = reconciliation();
  const approvedPreview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const changes = [
    (value) => { value.items.find((item) => item.id === 'inspect-health').descriptionMarkdown += '\nNew scope.'; },
    (value) => { value.destination.projectId = 'project-changed'; },
    (value) => { value.items.find((item) => item.id === 'inspect-health').externalAuthorizationGates[0].description = 'A different approval is required.'; },
  ];
  for (const edit of changes) {
    const changed = structuredClone(source);
    edit(changed);
    const plan = api.planApprovedOperations({ manifest: changed, reconciliation: snapshot, approvalDigest: approvedPreview.digest });
    assert.equal(plan.status, 'refused');
    assert.deepEqual(plan.operations, []);
  }

  const mappedSnapshot = reconciliation([
    await itemSnapshot(api, source, 'fleet-health', 'VAM-900', 1),
  ]);
  const changedMapping = api.planApprovedOperations({ manifest: source, reconciliation: mappedSnapshot, approvalDigest: approvedPreview.digest });
  assert.equal(changedMapping.status, 'refused');
  assert.deepEqual(changedMapping.operations, []);

  const manifestMapping = structuredClone(source);
  manifestMapping.items.find((item) => item.id === 'fleet-health').resolvedKey = 'VAM-900';
  const changedManifestMapping = api.planApprovedOperations({ manifest: manifestMapping, reconciliation: snapshot, approvalDigest: approvedPreview.digest });
  assert.equal(changedManifestMapping.status, 'refused');
  assert.deepEqual(changedManifestMapping.operations, []);
});

test('conflicting or structurally mismatched live mappings refuse publication', async () => {
  const api = await loadHelper();
  const source = manifest();
  const repairIntent = await api.deriveWorkIntentId(source.sourceId, 'repair-alerts');
  const inspectIntent = await api.deriveWorkIntentId(source.sourceId, 'inspect-health');
  const cases = [
    ['explicit key conflicts with identity key', (candidateManifest, items) => {
      candidateManifest.items.find((item) => item.id === 'repair-alerts').existingKey = 'VAM-999';
      items.push({ key: 'VAM-999', workIntentId: 'wi-other-identity', parentKey: 'VAM-900', acceptanceCriteriaCount: 2 });
    }],
    ['duplicate live identity', (_candidateManifest, items) => {
      items.push({ key: 'VAM-998', workIntentId: repairIntent, parentKey: 'VAM-900', acceptanceCriteriaCount: 2 });
    }],
    ['duplicate live key', (_candidateManifest, items) => {
      items.push({ key: 'VAM-902', workIntentId: inspectIntent, parentKey: 'VAM-900', acceptanceCriteriaCount: 2 });
    }],
    ['criterion count mismatch', (_candidateManifest, items) => {
      items.find((item) => item.key === 'VAM-902').acceptanceCriteriaCount = 1;
    }],
    ['parent mismatch', (_candidateManifest, items) => {
      items.find((item) => item.key === 'VAM-902').parentKey = 'VAM-OTHER';
    }],
    ['explicit key maps to a different work intent', (candidateManifest, items) => {
      candidateManifest.items.find((item) => item.id === 'repair-alerts').existingKey = 'VAM-995';
      items.push({ key: 'VAM-995', workIntentId: 'wi-other-identity', parentKey: null, acceptanceCriteriaCount: 1 });
    }],
  ];

  for (const [label, addConflict] of cases) {
    const candidateManifest = structuredClone(source);
    delete candidateManifest.items.find((item) => item.id === 'repair-alerts').existingKey;
    const items = [
      await itemSnapshot(api, candidateManifest, 'fleet-health', 'VAM-900', 1),
      await itemSnapshot(api, candidateManifest, 'inspect-health', 'VAM-901', 2, 'VAM-900'),
      await itemSnapshot(api, candidateManifest, 'repair-alerts', 'VAM-902', 2, 'VAM-900'),
    ];
    addConflict(candidateManifest, items);
    const snapshot = reconciliation(items);
    const preview = api.buildPreview({ manifest: candidateManifest, reconciliation: snapshot });
    const plan = api.planApprovedOperations({ manifest: candidateManifest, reconciliation: snapshot, approvalDigest: preview.digest });
    assert.equal(plan.status, 'refused', label);
    assert.deepEqual(plan.operations, [], label);
  }
});

test('manifest requires one epic, child lanes and nonempty criteria', async () => {
  const api = await loadHelper();
  const noEpic = manifest();
  Object.assign(noEpic.items.find((item) => item.type === 'EPIC'), { type: 'FEATURE', lane: 'Z' });
  assert.throws(() => api.validateManifest(noEpic), /epic/i);

  const twoEpics = manifest();
  Object.assign(twoEpics.items.find((item) => item.id === 'inspect-health'), { type: 'EPIC', lane: null });
  assert.throws(() => api.validateManifest(twoEpics), /epic/i);

  const noLane = manifest();
  noLane.items.find((item) => item.id === 'inspect-health').lane = null;
  assert.throws(() => api.validateManifest(noLane), /lane/i);

  const noCriteria = manifest();
  noCriteria.items.find((item) => item.id === 'inspect-health').acceptanceCriteria = [];
  assert.throws(() => api.validateManifest(noCriteria), /criteria/i);
});

test('empty snapshot plan carries ensure fields and orders the epic and prerequisites first', async () => {
  const api = await loadHelper();
  const source = manifest();
  const snapshot = reconciliation();
  const preview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const plan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });
  const creates = plan.operations.filter((operation) => operation.type === 'CREATE_ITEM');

  assert.deepEqual(creates.map((operation) => operation.localId), ['fleet-health', 'inspect-health', 'repair-alerts']);
  assert.equal(creates[0].workIntentId, await api.deriveWorkIntentId(source.sourceId, 'fleet-health'));
  assert.equal(creates[0].objective, source.items.find((item) => item.id === 'fleet-health').title);
  assert.equal(creates.every((operation) => /^req-vammo-roadmap-[a-f0-9]{64}$/.test(operation.requestId)), true);
  assert.deepEqual(
    creates.map((operation) => operation.requestId),
    preview.items.filter((item) => item.decision === 'create').map((item) => item.creationRequestId),
  );
  assert.equal(creates[0].proposedWork.title, 'Improve fleet battery health handling');
  assert.equal(creates[0].proposedWork.descriptionMarkdown, source.items.find((item) => item.id === 'fleet-health').descriptionMarkdown);
  assert.deepEqual(creates[0].proposedWork.acceptanceCriteria, [{ text: 'The child work is grouped under this epic.' }]);
  assert.equal(creates[0].projectId, source.destination.projectId);
  assert.deepEqual(creates[0].proposedWork.responsibility, source.destination.responsibility);
  assert.equal(creates[0].proposedWork.completionPolicy, 'DELIVERABLE');
  assert.equal(creates[2].reviewerId, 'reviewer-synthetic');
  assert.equal(creates.every((operation) => operation.creationPolicy === 'CREATE_IF_NO_MATCH'), true);

  const repeated = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: preview.digest });
  assert.deepEqual(
    repeated.operations.filter((operation) => operation.type === 'CREATE_ITEM').map((operation) => operation.requestId),
    creates.map((operation) => operation.requestId),
  );

  const persisted = structuredClone(source);
  for (const operation of creates) {
    persisted.items.find((item) => item.id === operation.localId).creationRequestId = operation.requestId;
  }
  const persistedPreview = api.buildPreview({ manifest: persisted, reconciliation: snapshot });
  assert.equal(persistedPreview.digest, preview.digest);
  const retried = api.planApprovedOperations({ manifest: persisted, reconciliation: snapshot, approvalDigest: persistedPreview.digest });
  assert.deepEqual(
    retried.operations.filter((operation) => operation.type === 'CREATE_ITEM').map((operation) => operation.requestId),
    creates.map((operation) => operation.requestId),
  );
});

test('a persisted creationRequestId cannot be reused after the create operation changes', async () => {
  const api = await loadHelper();
  const source = manifest();
  const snapshot = reconciliation();
  const firstPreview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const firstPlan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: firstPreview.digest });
  const firstCreate = firstPlan.operations.find((operation) => operation.type === 'CREATE_ITEM' && operation.localId === 'inspect-health');
  source.items.find((item) => item.id === 'inspect-health').creationRequestId = firstCreate.requestId;
  source.items.find((item) => item.id === 'inspect-health').descriptionMarkdown += '\nChanged proposal.';

  const changedPreview = api.buildPreview({ manifest: source, reconciliation: snapshot });
  const changedItem = changedPreview.items.find((item) => item.id === 'inspect-health');
  const changedPlan = api.planApprovedOperations({ manifest: source, reconciliation: snapshot, approvalDigest: changedPreview.digest });

  assert.notEqual(changedItem.creationRequestId, firstCreate.requestId);
  assert.ok(changedPreview.blockers.some((blocker) => /creationRequestId.*different create operation/i.test(blocker)));
  assert.equal(changedPlan.status, 'refused');
  assert.deepEqual(changedPlan.operations, []);
});

test('CLI renders a synthetic preview and approved plan without importing tools', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'vammo-roadmap-cli-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const manifestPath = join(root, 'manifest.json');
  const snapshotPath = join(root, 'snapshot.json');
  writeFileSync(manifestPath, JSON.stringify(manifest()));
  writeFileSync(snapshotPath, JSON.stringify(reconciliation()));

  const result = spawnSync(process.execPath, [helperPath, 'preview', '--manifest', manifestPath, '--snapshot', snapshotPath], { encoding: 'utf8', cwd: root });
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.sourceId, 'synthetic:roadmap:fleet-health');
  assert.equal(Object.hasOwn(output, 'operations'), false);
  assert.equal(Object.hasOwn(output, 'mutationOperations'), false);

  const planned = spawnSync(process.execPath, [helperPath, 'plan', '--manifest', manifestPath, '--snapshot', snapshotPath, '--approved-digest', output.digest], { encoding: 'utf8', cwd: root });
  assert.equal(planned.status, 0, planned.stderr);
  const plan = JSON.parse(planned.stdout);
  assert.equal(plan.status, 'approved');
  assert.equal(plan.operations.filter((operation) => operation.type === 'CREATE_ITEM').length, 3);
});

test('skill and helper exist in the Work Tracker plugin package', () => {
  assert.equal(existsSync(join(skillDir, 'SKILL.md')), true, 'roadmap skill is missing from the package');
  assert.equal(existsSync(helperPath), true, 'roadmap helper is missing from the package');
  const skill = readFileSync(join(skillDir, 'SKILL.md'), 'utf8');
  assert.match(skill, /vammo-work-tracking/);
  assert.match(skill, /vammo-work-tracker-writing/);
  const frontmatter = skill.match(/^---\n([\s\S]*?)\n---\n/)?.[1] ?? '';
  const description = frontmatter.split('\n').find((line) => line.startsWith('description:')) ?? '';
  assert.match(description, /^description: Use when a person asks to turn a roadmap into Vammo Work Tracker work items\.$/);
  assert.match(skill, /loaded\s+`SKILL\.md`/i);
  assert.match(skill, /absolute resolved script path/i);
  assert.doesNotMatch(skill, /node plugins\/vammo-work-tracker\/skills\/vammo-roadmap-to-tickets\/scripts\/roadmap\.mjs/);
  assert.match(skill, /canonical origin and original source path/i);
  assert.match(skill, /staging path/i);
  assert.match(skill, /requested draft/i);
  assert.match(skill, /reviewerless explicitly adopted item may be reused only/i);
  assert.match(skill, /never change the policy or downgrade it/i);
  assert.match(skill, /publication remains blocked/i);
  assert.match(skill, /"creationRequestId": null/);
  assert.match(skill, /Treat `workIntentId: null` as absent/);
  assert.match(skill, /candidateKeys/);
  assert.match(skill, /objective[\s\S]*source\s+item title/i);
  assert.match(skill, /Save the operation's `requestId` in that item's `creationRequestId` manifest field before the MCP call\./);
  assert.match(skill, /Persist the response key as\s+that manifest item's `resolvedKey` before the next call\./);
  assert.match(skill, /Candidate confirmation only resolves that item; it does not approve other item or relation\s+writes\./);
  assert.match(skill, /remove only the checkbox-container indentation from source and live criterion text/);
  assert.match(skill, /replace it\s+with the ID shown in the current preview and rebuild the preview/);
  assert.equal((skill.match(/ROADMAP_SKILL_FILE=/g) ?? []).length, 1);

  const readme = readFileSync(new URL('../../../README.md', import.meta.url), 'utf8');
  assert.match(readme, /`\/vammo-work-tracker:vammo-roadmap-to-tickets`/);
  assert.match(readme, /`\$vammo-roadmap-to-tickets`/);
  assert.match(readme, /Node\.js/);
  assert.match(readme, /digest/i);
  assert.match(readme, /plugin cache/i);
});
