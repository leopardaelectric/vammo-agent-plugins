import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { lstatSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const contractId = 'vammo.work-tracker-writing';
const sourceRepository = 'leopardaelectric/vammo-work-tracker';
const sourcePrefix = 'agent-integrations/shared/skills/vammo-work-tracker-writing';
const targetPrefix = 'skills/vammo-work-tracker-writing';
const names = ['SKILL.md', 'references/english-ste.md', 'references/portuguese-writing.md'];
const mappings = names.map((name) => ({ sourcePath: `${sourcePrefix}/${name}`, targetPath: `${targetPrefix}/${name}` }));
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');

function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
}

// Fixed mappings are the only files sync may read or write. Reject symlink escapes before writing.
function checkedPath(root, path, required = true) {
  let current = resolve(root);
  for (const part of ['', ...path.split('/')]) {
    if (part) current = join(current, part);
    if (!existsSync(current)) {
      // lstat also detects a dangling symlink, which existsSync follows and would miss.
      try { requireCondition(!lstatSync(current).isSymbolicLink(), `Symlink refused: ${path}`); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
      requireCondition(!required, `Missing file: ${path}`);
      continue;
    }
    requireCondition(!lstatSync(current).isSymbolicLink(), `Symlink refused: ${path}`);
  }
  return current;
}

function readFile(root, path) {
  const fullPath = checkedPath(root, path);
  requireCondition(lstatSync(fullPath).isFile(), `Not a regular file: ${path}`);
  return readFileSync(fullPath);
}

function sourceFiles(sourceRoot) {
  const content = mappings.map(({ sourcePath }) => readFile(sourceRoot, sourcePath));
  for (const match of content[0].toString('utf8').matchAll(/\]\(([^)]+)\)/g)) {
    const link = match[1].split('#')[0];
    if (!link || /^[a-z]+:/i.test(link)) continue;
    requireCondition(names.includes(link), `Reference is outside the source allowlist: ${link}`);
  }
  return content;
}

function skillInventory(pluginRoot, path = targetPrefix) {
  const fullPath = checkedPath(pluginRoot, path);
  const result = [];
  for (const entry of readdirSync(fullPath, { withFileTypes: true })) {
    const child = `${path}/${entry.name}`;
    checkedPath(pluginRoot, child);
    if (entry.isDirectory()) result.push(...skillInventory(pluginRoot, child));
    else {
      requireCondition(entry.isFile(), `Not a regular file: ${child}`);
      result.push(child);
    }
  }
  return result.sort();
}

function readManifest(pluginRoot) {
  const manifest = JSON.parse(readFile(pluginRoot, 'writing-source.json').toString('utf8'));
  requireCondition(JSON.stringify(Object.keys(manifest).sort()) === JSON.stringify(['contractId', 'files', 'sourceRepository', 'sourceRevision', 'sourceStatus']), 'Unexpected manifest fields');
  requireCondition(manifest.contractId === contractId && manifest.sourceRepository === sourceRepository, 'Wrong source contract or repository');
  requireCondition(['development', 'published'].includes(manifest.sourceStatus), 'Invalid source status');
  requireCondition(manifest.sourceRevision === 'WORKTREE' || /^[a-f0-9]{40}$/.test(manifest.sourceRevision), 'Invalid source revision');
  requireCondition(manifest.sourceStatus !== 'published' || /^[a-f0-9]{40}$/.test(manifest.sourceRevision), 'Published origin requires an immutable revision');
  requireCondition(Array.isArray(manifest.files) && manifest.files.length === mappings.length, 'Wrong manifest file count');
  for (const [i, entry] of manifest.files.entries()) {
    requireCondition(JSON.stringify(Object.keys(entry).sort()) === JSON.stringify(['sha256', 'sourcePath', 'targetPath']), 'Unexpected file fields');
    requireCondition(entry.sourcePath === mappings[i].sourcePath && entry.targetPath === mappings[i].targetPath, 'Manifest paths or order differ from the allowlist');
    requireCondition(/^[a-f0-9]{64}$/.test(entry.sha256), 'Invalid file hash');
  }
  return manifest;
}

export function verifyPackage(pluginRoot, { requirePublished = false } = {}) {
  const manifest = readManifest(pluginRoot);
  requireCondition(!requirePublished || manifest.sourceStatus === 'published', 'Release blocked: development provenance');
  requireCondition(JSON.stringify(skillInventory(pluginRoot)) === JSON.stringify(mappings.map(({ targetPath }) => targetPath)), 'Unmanifested or missing skill files');
  for (const entry of manifest.files) {
    requireCondition(hash(readFile(pluginRoot, entry.targetPath)) === entry.sha256, `Package drift: ${entry.targetPath}`);
  }
  return manifest;
}

export function syncWritingSkill(sourceRoot, pluginRoot, { check = false } = {}) {
  const content = sourceFiles(sourceRoot);
  const files = mappings.map((mapping, i) => ({ ...mapping, sha256: hash(content[i]) }));
  if (check) {
    const manifest = verifyPackage(pluginRoot);
    requireCondition(JSON.stringify(manifest.files) === JSON.stringify(files), 'Source and package differ');
    if (manifest.sourceRevision !== 'WORKTREE') {
      for (const entry of manifest.files) {
        let committed;
        try {
          committed = execFileSync('git', ['-C', resolve(sourceRoot), 'show', `${manifest.sourceRevision}:${entry.sourcePath}`], { stdio: ['ignore', 'pipe', 'ignore'] });
        } catch { throw new Error('Declared source revision is unavailable in the supplied checkout'); }
        requireCondition(hash(committed) === entry.sha256, `Revision content differs: ${entry.sourcePath}`);
      }
    }
    return manifest;
  }
  // Preflight every destination before the first mutation; do not delete unknown files.
  const destinations = [...mappings.map(({ targetPath }) => targetPath), 'writing-source.json'];
  const targets = destinations.map((path) => {
    const target = checkedPath(pluginRoot, path, false);
    requireCondition(!existsSync(target) || lstatSync(target).isFile(), `Not a regular file destination: ${path}`);
    return target;
  });
  const manifestPath = targets.pop();
  for (const [i, target] of targets.entries()) {
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, content[i]);
  }
  const manifest = { contractId, sourceRepository, sourceRevision: 'WORKTREE', sourceStatus: 'development', files };
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}

function main(args) {
  let sourceRoot;
  let pluginRoot = fileURLToPath(new URL('..', import.meta.url));
  let check = false;
  let verify = false;
  let requirePublished = false;
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--source' || arg === '--plugin-root') {
      requireCondition(args[i + 1] && !args[i + 1].startsWith('--'), `Missing value for ${arg}`);
      const value = resolve(args[++i]);
      if (arg === '--source') sourceRoot = value;
      else pluginRoot = value;
    } else if (arg === '--check') check = true;
    else if (arg === '--verify-package') verify = true;
    else if (arg === '--require-published') requirePublished = true;
    else throw new Error(`Unknown option: ${arg}`);
  }
  requireCondition(verify ? !sourceRoot && !check : !!sourceRoot && !requirePublished, 'Use --source [--check] or --verify-package [--require-published]');
  const manifest = verify ? verifyPackage(pluginRoot, { requirePublished }) : syncWritingSkill(sourceRoot, pluginRoot, { check });
  const action = verify ? 'Package integrity verified' : check ? 'Source and package match' : 'Three writing skill files synchronized';
  console.log(`${action} (${manifest.sourceStatus}). Authenticated upstream provenance is not checked.`);
}

if (process.argv[1] && relative(resolve(process.argv[1]), fileURLToPath(import.meta.url)) === '') {
  try { main(process.argv.slice(2)); }
  catch (error) { console.error(`Writing skill check failed: ${error.message}`); process.exitCode = 1; }
}
