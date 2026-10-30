import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, symlinkSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const script = fileURLToPath(new URL('./sync-writing-skill.mjs', import.meta.url));
const sourcePrefix = 'agent-integrations/shared/skills/vammo-work-tracker-writing';
const targetPrefix = 'skills/vammo-work-tracker-writing';
const files = ['SKILL.md', 'references/english-ste.md', 'references/portuguese-writing.md'];
const fixtureText = [
  '---\nname: vammo-work-tracker-writing\ndescription: Draft cards\n---\n[English](references/english-ste.md)\n[Portuguese](references/portuguese-writing.md)\n',
  '# English\nSynthetic reference.\n',
  '# Portuguese\nSynthetic reference.\n',
];

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'vammo-writing-sync-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const source = join(root, 'source');
  const plugin = join(root, 'plugin');
  for (const [i, file] of files.entries()) {
    const path = join(source, sourcePrefix, file);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, fixtureText[i]);
  }
  mkdirSync(plugin);
  const run = (...args) => spawnSync(process.execPath, [script, '--plugin-root', plugin, ...args], { encoding: 'utf8' });
  const sync = () => run('--source', source);
  const manifestPath = join(plugin, 'writing-source.json');
  const manifest = () => JSON.parse(readFileSync(manifestPath, 'utf8'));
  const changeManifest = (edit) => {
    const value = manifest(); edit(value);
    writeFileSync(manifestPath, `${JSON.stringify(value, null, 2)}\n`);
  };
  return { root, source, plugin, run, sync, manifestPath, manifest, changeManifest };
}

test('copies exactly three canonical files and produces stable development provenance', (t) => {
  const f = fixture(t);
  assert.equal(f.sync().status, 0);
  const original = readFileSync(f.manifestPath);
  const m = f.manifest();
  assert.equal(m.contractId, 'vammo.work-tracker-writing');
  assert.equal(m.sourceRepository, 'leopardaelectric/vammo-work-tracker');
  assert.equal(m.sourceRevision, 'WORKTREE');
  assert.equal(m.sourceStatus, 'development');
  assert.deepEqual(m.files, files.map((file, i) => ({
    sourcePath: `${sourcePrefix}/${file}`,
    targetPath: `${targetPrefix}/${file}`,
    sha256: createHash('sha256').update(fixtureText[i]).digest('hex'),
  })));
  for (const [i, file] of files.entries()) assert.equal(readFileSync(join(f.plugin, targetPrefix, file), 'utf8'), fixtureText[i]);
  assert.equal(f.sync().status, 0);
  assert.deepEqual(readFileSync(f.manifestPath), original);
  assert.equal(f.run('--source', f.source, '--check').status, 0);
  assert.equal(f.run('--verify-package').status, 0);
});

test('source drift fails check without modifying the distributed files', (t) => {
  const f = fixture(t); assert.equal(f.sync().status, 0);
  writeFileSync(join(f.source, sourcePrefix, files[1]), `${fixtureText[1]}x`);
  assert.notEqual(f.run('--source', f.source, '--check').status, 0);
  assert.equal(readFileSync(join(f.plugin, targetPrefix, files[1]), 'utf8'), fixtureText[1]);
});

test('a one-byte package drift fails both checks', (t) => {
  const f = fixture(t); assert.equal(f.sync().status, 0);
  writeFileSync(join(f.plugin, targetPrefix, files[2]), `${fixtureText[2]}x`);
  assert.notEqual(f.run('--source', f.source, '--check').status, 0);
  assert.notEqual(f.run('--verify-package').status, 0);
});

test('missing source reference fails before copying any file', (t) => {
  const f = fixture(t);
  rmSync(join(f.source, sourcePrefix, files[2]));
  assert.notEqual(f.sync().status, 0);
  assert.equal(existsSync(join(f.plugin, targetPrefix, files[0])), false);
  assert.equal(existsSync(f.manifestPath), false);
});

test('sync leaves MCP configuration and unrelated files unchanged', (t) => {
  const f = fixture(t);
  writeFileSync(join(f.plugin, '.mcp.json'), 'synthetic MCP sentinel\n');
  writeFileSync(join(f.plugin, 'unrelated.txt'), 'keep\n');
  writeFileSync(join(f.source, sourcePrefix, 'private.txt'), 'must not copy\n');
  assert.equal(f.sync().status, 0);
  assert.equal(readFileSync(join(f.plugin, '.mcp.json'), 'utf8'), 'synthetic MCP sentinel\n');
  assert.equal(readFileSync(join(f.plugin, 'unrelated.txt'), 'utf8'), 'keep\n');
  assert.equal(existsSync(join(f.plugin, targetPrefix, 'private.txt')), false);
});

for (const [label, edit] of [
  ['contract', (m) => { m.contractId = 'wrong'; }],
  ['repository', (m) => { m.sourceRepository = 'other/repository'; }],
  ['published WORKTREE', (m) => { m.sourceStatus = 'published'; }],
  ['invalid revision', (m) => { m.sourceRevision = 'main'; }],
  ['hash', (m) => { m.files[0].sha256 = '0'.repeat(64); }],
  ['path traversal', (m) => { m.files[0].targetPath = '../outside'; }],
  ['missing file', (m) => { m.files.pop(); }],
  ['extra manifest metadata', (m) => { m.personalPath = '/private/path'; }],
]) {
  test(`rejects inconsistent manifest: ${label}`, (t) => {
    const f = fixture(t); assert.equal(f.sync().status, 0);
    f.changeManifest(edit);
    assert.notEqual(f.run('--verify-package').status, 0);
    assert.notEqual(f.run('--source', f.source, '--check').status, 0);
  });
}

test('package verification rejects unmanifested skill files without deleting them', (t) => {
  const f = fixture(t); assert.equal(f.sync().status, 0);
  writeFileSync(join(f.plugin, targetPrefix, 'unexpected.txt'), 'retain\n');
  assert.notEqual(f.run('--verify-package').status, 0);
  assert.equal(readFileSync(join(f.plugin, targetPrefix, 'unexpected.txt'), 'utf8'), 'retain\n');
});

test('symlinked source and target directories cannot read or overwrite outside the allowlist', (t) => {
  const f = fixture(t);
  const outside = join(f.root, 'outside'); mkdirSync(outside);
  writeFileSync(join(outside, 'english-ste.md'), 'outside\n');
  const sourceRef = join(f.source, sourcePrefix, 'references');
  rmSync(sourceRef, { recursive: true }); symlinkSync(outside, sourceRef, 'dir');
  assert.notEqual(f.sync().status, 0);
  rmSync(sourceRef); mkdirSync(sourceRef);
  for (const [i, file] of files.entries()) if (i) writeFileSync(join(f.source, sourcePrefix, file), fixtureText[i]);
  mkdirSync(join(f.plugin, 'skills'));
  symlinkSync(outside, join(f.plugin, targetPrefix), 'dir');
  assert.notEqual(f.sync().status, 0);
  assert.equal(readFileSync(join(outside, 'english-ste.md'), 'utf8'), 'outside\n');
});

test('release gate rejects development provenance and sync cannot self-claim published origin', (t) => {
  const f = fixture(t); assert.equal(f.sync().status, 0);
  assert.notEqual(f.run('--verify-package', '--require-published').status, 0);
  assert.notEqual(f.run('--source', f.source, '--published').status, 0);
});

test('source check rejects a declared revision that cannot supply the canonical bytes', (t) => {
  const f = fixture(t); assert.equal(f.sync().status, 0);
  f.changeManifest((m) => { m.sourceStatus = 'published'; m.sourceRevision = 'a'.repeat(40); });
  assert.notEqual(f.run('--source', f.source, '--check').status, 0);
});

for (const collision of ['references/english-ste.md', 'writing-source.json']) {
  test(`directory collision at ${collision} fails before changing existing bytes`, (t) => {
    const f = fixture(t); assert.equal(f.sync().status, 0);
    const priorSkill = readFileSync(join(f.plugin, targetPrefix, 'SKILL.md'));
    writeFileSync(join(f.source, sourcePrefix, 'SKILL.md'), `${fixtureText[0]}new source\n`);
    const path = collision === 'writing-source.json' ? f.manifestPath : join(f.plugin, targetPrefix, collision);
    rmSync(path); mkdirSync(path);
    assert.notEqual(f.sync().status, 0);
    assert.deepEqual(readFileSync(join(f.plugin, targetPrefix, 'SKILL.md')), priorSkill);
  });
}
