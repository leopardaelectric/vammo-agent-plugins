import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const COMPLETION_POLICIES = new Set(['PRODUCTION', 'DELIVERABLE', 'REVIEWED_DELIVERABLE']);
const WORK_ITEM_TYPES = new Set(['EPIC', 'FEATURE', 'BUG', 'MAINTENANCE', 'INFRASTRUCTURE', 'RESEARCH', 'ANALYSIS', 'OPERATIONS', 'REVIEW', 'OTHER']);

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function nonempty(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (!isRecord(value)) return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

function digest(value) {
  return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
}

export function deriveWorkIntentId(sourceId, localId) {
  if (!nonempty(sourceId) || !nonempty(localId)) throw new TypeError('sourceId and localId must be nonempty strings');
  const identity = `vammo-roadmap-item\0${sourceId}\0${localId}`;
  return `wi-vammo-roadmap-${createHash('sha256').update(identity).digest('hex')}`;
}

export function topologicalOrder(manifest) {
  const items = manifest?.items;
  if (!Array.isArray(items)) throw new TypeError('manifest.items must be an array');
  const epics = items.filter((item) => item?.type === 'EPIC');
  if (epics.length !== 1) throw new TypeError('manifest must contain exactly one EPIC');
  const children = items.filter((item) => item?.type !== 'EPIC');
  const byId = new Map(children.map((item) => [item.id, item]));
  const remaining = new Map(children.map((item) => [item.id, Array.isArray(item.dependsOn) ? item.dependsOn.length : 0]));
  const dependents = new Map(children.map((item) => [item.id, []]));

  for (const item of children) {
    for (const prerequisite of Array.isArray(item.dependsOn) ? item.dependsOn : []) {
      if (dependents.has(prerequisite)) dependents.get(prerequisite).push(item.id);
    }
  }

  const ready = [...remaining.entries()].filter(([, count]) => count === 0).map(([id]) => id).sort();
  const ordered = [];
  while (ready.length > 0) {
    const id = ready.shift();
    ordered.push(id);
    for (const dependent of dependents.get(id) ?? []) {
      remaining.set(dependent, remaining.get(dependent) - 1);
      if (remaining.get(dependent) === 0) {
        ready.push(dependent);
        ready.sort();
      }
    }
  }

  if (ordered.length !== children.length || byId.size !== children.length) throw new TypeError('dependencies contain a cycle or duplicate item IDs');
  return [epics[0].id, ...ordered];
}

export function validateManifest(manifest, { allowPendingMetadata = false, reviewerlessExistingIds = new Set() } = {}) {
  if (!isRecord(manifest)) throw new TypeError('manifest must be an object');
  const hasSourceId = nonempty(manifest.sourceId);
  if (!hasSourceId && !allowPendingMetadata) throw new TypeError('sourceId is required');
  if (!nonempty(manifest.revision) && !allowPendingMetadata) throw new TypeError('revision is required for review');
  const destination = isRecord(manifest.destination) ? manifest.destination : null;
  if ((!destination || !nonempty(destination.projectId)) && !allowPendingMetadata) throw new TypeError('destination.projectId is required');
  const responsibility = destination?.responsibility;
  if (!isRecord(responsibility) && !allowPendingMetadata) throw new TypeError('destination.responsibility is required');
  if (isRecord(responsibility)) {
    if (responsibility.kind === 'team' && !nonempty(responsibility.teamId) && !allowPendingMetadata) throw new TypeError('team responsibility requires teamId');
    if (responsibility.kind === 'personal' && !nonempty(responsibility.personalOwnerId) && !allowPendingMetadata) throw new TypeError('personal responsibility requires personalOwnerId');
    if (!['team', 'personal'].includes(responsibility.kind) && !allowPendingMetadata) throw new TypeError('responsibility kind must be team or personal');
  }
  if (!Array.isArray(manifest.items) || manifest.items.length === 0) throw new TypeError('items must be a nonempty array');
  if (manifest.items.filter((item) => item?.type === 'EPIC').length !== 1) throw new TypeError('manifest must contain exactly one EPIC');

  const ids = new Set();
  const explicitKeys = new Map();
  for (const item of manifest.items) {
    if (!isRecord(item)) throw new TypeError('each item must be an object');
    if (!nonempty(item.id)) throw new TypeError('every item needs a source-local id');
    if (ids.has(item.id)) throw new TypeError(`duplicate source-local id: ${item.id}`);
    ids.add(item.id);
    const derivedIntent = hasSourceId ? deriveWorkIntentId(manifest.sourceId, item.id) : null;
    if (item.workIntentId !== undefined && hasSourceId && item.workIntentId !== derivedIntent) throw new TypeError(`item ${item.id} has a workIntentId that does not match its source identity`);
    if (item.workIntentId !== undefined && !hasSourceId && item.workIntentId !== null && !nonempty(item.workIntentId)) throw new TypeError(`item ${item.id} has an invalid persisted workIntentId`);
    if (!nonempty(item.title)) throw new TypeError(`item ${item.id} needs a title`);
    if (!WORK_ITEM_TYPES.has(item.type)) throw new TypeError(`item ${item.id} has an unsupported type`);
    if (!nonempty(item.descriptionMarkdown)) throw new TypeError(`item ${item.id} needs descriptionMarkdown`);
    if (!Array.isArray(item.acceptanceCriteria) || item.acceptanceCriteria.length === 0 || item.acceptanceCriteria.some((criterion) => !nonempty(criterion))) {
      throw new TypeError(`item ${item.id} needs nonempty acceptance criteria`);
    }
    if (!COMPLETION_POLICIES.has(item.completionPolicy)) throw new TypeError(`item ${item.id} needs a valid completionPolicy`);
    if (item.completionPolicy === 'REVIEWED_DELIVERABLE' && !nonempty(item.reviewerId) && !allowPendingMetadata && !reviewerlessExistingIds.has(item.id)) throw new TypeError(`item ${item.id} needs a named reviewer`);
    if (item.originalLocale !== undefined && !nonempty(item.originalLocale)) throw new TypeError(`item ${item.id} has an invalid originalLocale`);
    if (item.priority !== undefined && !['URGENT', 'HIGH', 'NORMAL', 'LOW'].includes(item.priority)) throw new TypeError(`item ${item.id} has an invalid priority`);
    for (const field of ['existingKey', 'resolvedKey']) {
      const key = item[field];
      if (key !== undefined && key !== null && !nonempty(key)) throw new TypeError(`item ${item.id} has an invalid ${field}`);
      if (nonempty(key)) {
        const priorItem = explicitKeys.get(key);
        if (priorItem && priorItem !== item.id) throw new TypeError(`duplicate explicit or resolved key: ${key}`);
        explicitKeys.set(key, item.id);
      }
    }
    if (nonempty(item.existingKey) && nonempty(item.resolvedKey) && item.existingKey !== item.resolvedKey) throw new TypeError(`item ${item.id} has conflicting existingKey and resolvedKey`);
    if (!Array.isArray(item.dependsOn)) throw new TypeError(`item ${item.id} needs a dependsOn array`);
    if (new Set(item.dependsOn).size !== item.dependsOn.length) throw new TypeError(`item ${item.id} has duplicate dependencies`);
    if (item.type === 'EPIC') {
      if (item.lane !== null && item.lane !== undefined) throw new TypeError('the EPIC must not have a worker lane');
      if (item.dependsOn.length > 0) throw new TypeError('the EPIC cannot depend on a child item');
    } else if (!nonempty(item.lane)) {
      throw new TypeError(`child item ${item.id} needs a worker lane`);
    }
    if (item.externalAuthorizationGates !== undefined) {
      if (!Array.isArray(item.externalAuthorizationGates)) throw new TypeError(`item ${item.id} gates must be an array`);
      const gateIds = new Set();
      for (const gate of item.externalAuthorizationGates) {
        if (!isRecord(gate) || !nonempty(gate.id) || !nonempty(gate.description)) throw new TypeError(`item ${item.id} has an invalid external authorization gate`);
        if (gateIds.has(gate.id)) throw new TypeError(`item ${item.id} has duplicate gate id ${gate.id}`);
        gateIds.add(gate.id);
      }
    }
  }

  const epic = manifest.items.find((item) => item.type === 'EPIC');
  const epics = manifest.items.filter((item) => item.type === 'EPIC');
  if (epics.length !== 1) throw new TypeError('manifest must contain exactly one EPIC');
  for (const item of manifest.items) {
    for (const prerequisite of item.dependsOn) {
      if (!ids.has(prerequisite)) throw new TypeError(`item ${item.id} depends on unknown item ${prerequisite}`);
      if (prerequisite === item.id) throw new TypeError(`item ${item.id} cannot depend on itself`);
      if (prerequisite === epic.id) throw new TypeError('children cannot use the EPIC as a blocking prerequisite');
    }
  }
  topologicalOrder(manifest);
  return true;
}

function criteriaText(item) {
  if (!Array.isArray(item.acceptanceCriteria)) return null;
  return item.acceptanceCriteria.map((criterion) => (typeof criterion === 'string' ? criterion : criterion?.text));
}

function criteriaCount(item) {
  if (Number.isInteger(item.acceptanceCriteriaCount)) return item.acceptanceCriteriaCount;
  if (Array.isArray(item.acceptanceCriteria)) return item.acceptanceCriteria.length;
  return null;
}

function sameArray(left, right) {
  return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((value, index) => value === right[index]);
}

function analyzeSnapshot(manifest, snapshot, orderedIds) {
  const readback = isRecord(snapshot) ? snapshot : {};
  const items = Array.isArray(readback.items) ? readback.items : [];
  const relations = Array.isArray(readback.relations) ? readback.relations : [];
  const blockers = [];
  const complete = readback.status === 'complete' && Array.isArray(readback.items) && Array.isArray(readback.relations);
  if (!complete) blockers.push(`readback is ${nonempty(readback.status) ? readback.status : 'missing or incomplete'}; missing records are not treated as absent`);

  const keys = new Set();
  const identities = new Set();
  for (const live of items) {
    if (!isRecord(live) || !nonempty(live.key)) {
      blockers.push('readback contains an item without a key');
      continue;
    }
    if (keys.has(live.key)) blockers.push(`duplicate live key ${live.key}`);
    keys.add(live.key);
    if (live.workIntentId !== null && live.workIntentId !== undefined) {
      if (!nonempty(live.workIntentId)) blockers.push(`live item ${live.key} has an invalid workIntentId`);
      else if (identities.has(live.workIntentId)) blockers.push(`duplicate live workIntentId ${live.workIntentId}`);
      else identities.add(live.workIntentId);
    }
  }

  const byId = new Map(manifest.items.map((item) => [item.id, item]));
  const expectedIntent = new Map();
  const resolved = new Map();
  for (const id of orderedIds) {
    const item = byId.get(id);
    const intent = nonempty(manifest.sourceId) ? deriveWorkIntentId(manifest.sourceId, id) : (nonempty(item.workIntentId) ? item.workIntentId : null);
    expectedIntent.set(id, intent);
  }

  for (const id of orderedIds) {
    const item = byId.get(id);
    const intent = expectedIntent.get(id);
    const identityMatches = intent ? items.filter((live) => live?.workIntentId === intent) : [];
    const fixedKey = item.resolvedKey ?? item.existingKey ?? null;
    const keyMatches = fixedKey === null ? [] : items.filter((live) => live?.key === fixedKey);
    if (identityMatches.length > 1 || keyMatches.length > 1) {
      blockers.push(`item ${id} has an ambiguous live identity or explicit key`);
      resolved.set(id, { decision: 'conflict', resolvedKey: null, workIntentId: intent });
      continue;
    }
    const byIntent = identityMatches[0] ?? null;
    const byExplicitKey = keyMatches[0] ?? null;
    if (fixedKey !== null && !byExplicitKey) {
      blockers.push(`persisted key ${fixedKey} for item ${id} was not found in the complete readback`);
      resolved.set(id, { decision: 'conflict', resolvedKey: null, workIntentId: intent });
      continue;
    }
    if (byIntent && byExplicitKey && byIntent.key !== byExplicitKey.key) {
      blockers.push(`persisted key ${fixedKey} conflicts with workIntentId for item ${id}`);
      resolved.set(id, { decision: 'conflict', resolvedKey: null, workIntentId: intent });
      continue;
    }
    const selected = byExplicitKey ?? byIntent;
    if (!selected) {
      resolved.set(id, { decision: complete && intent ? 'create' : 'unresolved', resolvedKey: null, workIntentId: intent });
      continue;
    }
    if (fixedKey !== null && selected.workIntentId !== null && selected.workIntentId !== undefined && selected.workIntentId !== intent) {
      blockers.push(`persisted key ${fixedKey} belongs to a different workIntentId`);
      resolved.set(id, { decision: 'conflict', resolvedKey: null, workIntentId: intent });
      continue;
    }
    if (selected.workIntentId === null || selected.workIntentId === undefined) {
      const criteria = criteriaText(selected);
      const hasFullManualSnapshot = nonempty(selected.title) && nonempty(selected.type) && criteria !== null;
      if (fixedKey === null || !hasFullManualSnapshot) {
        blockers.push(`manual item ${selected.key} requires explicit existingKey and full source-fidelity readback`);
        resolved.set(id, { decision: 'conflict', resolvedKey: null, workIntentId: intent });
        continue;
      }
      if (selected.title !== item.title || selected.type !== item.type || !sameArray(criteria, item.acceptanceCriteria)) {
        blockers.push(`manual item ${selected.key} does not match source content for item ${id}`);
        resolved.set(id, { decision: 'conflict', resolvedKey: null, workIntentId: intent });
        continue;
      }
    }

    const observedCount = criteriaCount(selected);
    if (observedCount === null || observedCount !== item.acceptanceCriteria.length) {
      blockers.push(`acceptance-criteria count mismatch for item ${id}`);
    }
    if (selected.completionPolicy !== undefined && selected.completionPolicy !== item.completionPolicy) {
      blockers.push(`completionPolicy mismatch for live item ${selected.key}`);
    }
    for (const field of ['title', 'type']) {
      if (selected[field] !== undefined && selected[field] !== item[field]) blockers.push(`${field} mismatch for live item ${selected.key}`);
    }
    const observedCriteria = criteriaText(selected);
    if (observedCriteria !== null && !sameArray(observedCriteria, item.acceptanceCriteria)) blockers.push(`acceptance-criteria text mismatch for live item ${selected.key}`);
    resolved.set(id, { decision: 'reuse', resolvedKey: selected.key, workIntentId: intent });
  }

  const epic = manifest.items.find((item) => item.type === 'EPIC');
  for (const item of manifest.items.filter((candidate) => candidate.type !== 'EPIC')) {
    const child = resolved.get(item.id);
    if (child?.decision !== 'reuse') continue;
    const live = items.find((candidate) => candidate?.key === child.resolvedKey);
    const parent = resolved.get(epic.id);
    const expectedParentKey = parent?.decision === 'reuse' ? parent.resolvedKey : null;
    if (live?.parentKey !== undefined && live.parentKey !== null && live.parentKey !== expectedParentKey) {
      blockers.push(`parent mismatch for live item ${live.key}`);
    }
  }

  return { complete, blockers: [...new Set(blockers)], items, relations, resolved, expectedIntent };
}

function expectedEdges(manifest, orderedIds) {
  const epic = manifest.items.find((item) => item.type === 'EPIC');
  const byId = new Map(manifest.items.map((item) => [item.id, item]));
  const children = orderedIds.filter((id) => id !== epic.id);
  const edges = children.map((id) => ({ type: 'PARENT_OF', fromId: epic.id, toId: id }));
  for (const id of children) {
    for (const prerequisite of byId.get(id).dependsOn) edges.push({ type: 'DEPENDS_ON', fromId: id, toId: prerequisite });
  }
  return edges;
}

function liveRelationExists(edge, state) {
  const from = state.resolved.get(edge.fromId);
  const to = state.resolved.get(edge.toId);
  if (from?.decision !== 'reuse' || to?.decision !== 'reuse') return false;
  return state.relations.some((relation) => relation?.type === edge.type && relation.fromKey === from.resolvedKey && relation.toKey === to.resolvedKey);
}

function collectPendingMetadata(manifest, state) {
  const pending = [];
  const blockers = [];
  const add = (field, reason) => {
    pending.push({ field, reason });
    blockers.push(`metadata pending: ${field} (${reason})`);
  };
  const addOnce = (field, reason) => {
    if (!pending.some((entry) => entry.field === field)) add(field, reason);
  };

  if (!nonempty(manifest.sourceId)) addOnce('sourceId', 'canonical source origin and original path are required for publication');
  if (!nonempty(manifest.revision)) addOnce('revision', 'source revision is required for publication');
  const destination = isRecord(manifest.destination) ? manifest.destination : {};
  if (!nonempty(destination.projectId)) addOnce('destination.projectId', 'destination project is required for publication');
  const responsibility = destination.responsibility;
  if (responsibility?.kind === 'team') {
    if (!nonempty(responsibility.teamId)) addOnce('destination.responsibility.teamId', 'responsible team is required for publication');
  } else if (responsibility?.kind === 'personal') {
    if (!nonempty(responsibility.personalOwnerId)) addOnce('destination.responsibility.personalOwnerId', 'personal owner is required for publication');
  } else {
    addOnce('destination.responsibility', 'team or personal responsibility is required for publication');
  }

  for (const item of manifest.items) {
    if (item.completionPolicy !== 'REVIEWED_DELIVERABLE' || nonempty(item.reviewerId)) continue;
    const resolution = state.resolved.get(item.id);
    if (resolution?.decision !== 'reuse') {
      addOnce(`items.${item.id}.reviewerId`, 'a named reviewer is required before creating a reviewed deliverable');
      continue;
    }
    const live = state.items.find((candidate) => candidate?.key === resolution.resolvedKey);
    if (!live || !Object.hasOwn(live, 'completionPolicy')) {
      addOnce(`items.${item.id}.completionPolicy`, 'live completion policy was not returned for the reused item');
      blockers.push(`live completion policy is missing for reused item ${item.id}`);
    } else if (live.completionPolicy !== item.completionPolicy) {
      blockers.push(`completionPolicy mismatch for reused item ${item.id}`);
    }
    if (!live || !Object.hasOwn(live, 'reviewerId')) {
      addOnce(`items.${item.id}.reviewerId`, 'live reviewer metadata was not returned for the reused item');
      blockers.push(`live reviewer metadata is missing for reused item ${item.id}`);
    }
  }
  return { pending, blockers };
}

export function buildPreview({ manifest, reconciliation }) {
  validateManifest(manifest, { allowPendingMetadata: true });
  const order = topologicalOrder(manifest);
  const state = analyzeSnapshot(manifest, reconciliation, order);
  const metadata = collectPendingMetadata(manifest, state);
  const byId = new Map(manifest.items.map((item) => [item.id, item]));
  const items = order.map((id) => {
    const item = byId.get(id);
    const resolution = state.resolved.get(id);
    const liveItem = resolution.decision === 'reuse' ? state.items.find((candidate) => candidate?.key === resolution.resolvedKey) : null;
    return {
      id: item.id,
      workIntentId: resolution.workIntentId,
      existingKey: item.existingKey ?? null,
      persistedResolvedKey: item.resolvedKey ?? null,
      resolvedKey: resolution.resolvedKey,
      decision: resolution.decision,
      title: item.title,
      type: item.type,
      originalLocale: item.originalLocale ?? null,
      priority: item.priority ?? null,
      descriptionMarkdown: item.descriptionMarkdown,
      acceptanceCriteria: item.acceptanceCriteria,
      lane: item.lane ?? null,
      dependsOn: item.dependsOn,
      completionPolicy: item.completionPolicy,
      reviewerId: item.reviewerId ?? null,
      existingMetadata: liveItem ? {
        completionPolicy: Object.hasOwn(liveItem, 'completionPolicy') ? liveItem.completionPolicy : null,
        reviewerId: Object.hasOwn(liveItem, 'reviewerId') ? liveItem.reviewerId : null,
      } : null,
      externalAuthorizationGates: item.externalAuthorizationGates ?? [],
    };
  });
  const edges = expectedEdges(manifest, order);
  const proposal = {
    sourceId: manifest.sourceId ?? null,
    revision: manifest.revision ?? null,
    destination: manifest.destination ?? null,
    readbackStatus: reconciliation?.status ?? 'missing',
    items,
    edges,
    edgeDecisions: edges.map((edge) => ({ ...edge, decision: liveRelationExists(edge, state) ? 'reuse' : 'create' })),
    topologicalOrder: order,
    pendingMetadata: metadata.pending,
    blockers: [...new Set([...state.blockers, ...metadata.blockers])],
    mutationOperations: [],
  };
  return { ...proposal, digest: digest(proposal) };
}

function ensureOperation(item, destination, workIntentId) {
  const operation = {
    type: 'CREATE_ITEM',
    localId: item.id,
    workIntentId,
    creationPolicy: 'CREATE_IF_NO_MATCH',
    projectId: destination.projectId,
    proposedWork: {
      title: item.title,
      type: item.type,
      descriptionMarkdown: item.descriptionMarkdown,
      acceptanceCriteria: item.acceptanceCriteria.map((text) => ({ text })),
      responsibility: destination.responsibility,
      completionPolicy: item.completionPolicy,
    },
    lane: item.lane ?? null,
    reviewerId: item.reviewerId ?? null,
    externalAuthorizationGates: item.externalAuthorizationGates ?? [],
  };
  if (item.originalLocale) operation.proposedWork.originalLocale = item.originalLocale;
  if (item.priority) operation.proposedWork.priority = item.priority;
  return operation;
}

function relationOperation(edge, state) {
  const from = state.resolved.get(edge.fromId);
  const to = state.resolved.get(edge.toId);
  return {
    type: 'CREATE_RELATION',
    relation: edge.type,
    ...(from?.decision === 'reuse' ? { fromKey: from.resolvedKey } : { fromLocalId: edge.fromId }),
    ...(to?.decision === 'reuse' ? { toKey: to.resolvedKey } : { toLocalId: edge.toId }),
  };
}

export function planApprovedOperations({ manifest, reconciliation, approvalDigest }) {
  let preview;
  try {
    preview = buildPreview({ manifest, reconciliation });
  } catch (error) {
    return { status: 'refused', operations: [], reason: error.message };
  }
  if (!nonempty(approvalDigest)) return { status: 'refused', operations: [], reason: 'approval digest is required' };
  if (approvalDigest !== preview.digest) return { status: 'refused', operations: [], reason: 'approval digest is stale or does not match this proposal' };
  if (preview.blockers.length > 0) return { status: 'refused', operations: [], reason: preview.blockers.join('; '), blockers: preview.blockers };

  const order = preview.topologicalOrder;
  const state = analyzeSnapshot(manifest, reconciliation, order);
  if (!state.complete) return { status: 'refused', operations: [], reason: 'complete readback is required' };
  const reviewerlessExistingIds = new Set(manifest.items
    .filter((item) => item.completionPolicy === 'REVIEWED_DELIVERABLE' && !nonempty(item.reviewerId))
    .filter((item) => {
      const resolution = state.resolved.get(item.id);
      const live = resolution?.decision === 'reuse' ? state.items.find((candidate) => candidate?.key === resolution.resolvedKey) : null;
      return live?.completionPolicy === 'REVIEWED_DELIVERABLE' && Object.hasOwn(live, 'reviewerId');
    })
    .map((item) => item.id));
  try {
    validateManifest(manifest, { reviewerlessExistingIds });
  } catch (error) {
    return { status: 'refused', operations: [], reason: error.message };
  }
  const byId = new Map(manifest.items.map((item) => [item.id, item]));
  const operations = [];
  for (const id of order) {
    const resolution = state.resolved.get(id);
    if (resolution.decision === 'create') operations.push(ensureOperation(byId.get(id), manifest.destination, resolution.workIntentId));
  }
  for (const edge of expectedEdges(manifest, order)) {
    if (!liveRelationExists(edge, state)) operations.push(relationOperation(edge, state));
  }
  return {
    status: 'approved',
    sourceId: manifest.sourceId,
    revision: manifest.revision,
    digest: preview.digest,
    items: preview.items,
    operations,
  };
}

function parseFlags(args) {
  const values = {};
  for (let index = 0; index < args.length; index += 1) {
    const flag = args[index];
    if (!flag.startsWith('--') || !args[index + 1] || args[index + 1].startsWith('--')) throw new Error(`expected a value after ${flag}`);
    if (Object.hasOwn(values, flag)) throw new Error(`duplicate flag ${flag}`);
    values[flag] = args[index + 1];
    index += 1;
  }
  return values;
}

function readJson(path, label) {
  if (!path) throw new Error(`--${label} is required`);
  return JSON.parse(readFileSync(path, 'utf8'));
}

function runCli(args) {
  const [command, ...rest] = args;
  if (!['preview', 'plan'].includes(command)) throw new Error('usage: roadmap.mjs preview|plan --manifest FILE --snapshot FILE [--approved-digest DIGEST]');
  const flags = parseFlags(rest);
  const allowed = command === 'preview' ? new Set(['--manifest', '--snapshot']) : new Set(['--manifest', '--snapshot', '--approved-digest']);
  for (const flag of Object.keys(flags)) if (!allowed.has(flag)) throw new Error(`unknown flag ${flag}`);
  const manifest = readJson(flags['--manifest'], 'manifest');
  const reconciliation = readJson(flags['--snapshot'], 'snapshot');
  if (command === 'preview') {
    if (flags['--approved-digest']) throw new Error('preview does not accept --approved-digest');
    return buildPreview({ manifest, reconciliation });
  }
  return planApprovedOperations({ manifest, reconciliation, approvalDigest: flags['--approved-digest'] });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    process.stdout.write(`${JSON.stringify(runCli(process.argv.slice(2)))}\n`);
  } catch (error) {
    process.stderr.write(`roadmap: ${error.message}\n`);
    process.exitCode = 2;
  }
}
