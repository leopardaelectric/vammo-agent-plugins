import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const hook = fileURLToPath(new URL('./session-start.sh', import.meta.url));
for (const scenario of ['included', 'excluded', 'unmatched', 'missing scope file']) {
  test(`writing reminder preserves tracking verdict: ${scenario}`, (t) => {
    const root = mkdtempSync(join(tmpdir(), 'vammo-writing-hook-'));
    t.after(() => rmSync(root, { recursive: true, force: true }));
    const project = join(root, 'work', 'project');
    const scope = join(root, 'scope');
    mkdirSync(project, { recursive: true });
    if (scenario === 'included') writeFileSync(scope, `${join(root, 'work')}\n`);
    if (scenario === 'excluded') writeFileSync(scope, `${join(root, 'work')}\n!${project}\n`);
    if (scenario === 'unmatched') writeFileSync(scope, `${join(root, 'other')}\n`);
    const result = spawnSync('sh', [hook], {
      cwd: project, encoding: 'utf8',
      env: { ...process.env, VAMMO_TRACKED_FOLDERS: scope, CLAUDE_PROJECT_DIR: project },
    });
    assert.equal(result.status, 0, result.stderr);
    const [verdict, reminder, ...extra] = result.stdout.trim().split('\n');
    const tracked = scenario === 'included' || scenario === 'missing scope file';
    assert.match(verdict, tracked ? /^Vammo Work Tracker is connected/ : /^Vammo Work Tracker: tracking is off/);
    if (!tracked) assert.match(verdict, /Do not track this session/);
    assert.match(reminder ?? '', /vammo-work-tracker-writing/);
    assert.match(reminder, /does not authorize saving or tracking/);
    assert.deepEqual(extra, []);
  });
}
