import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const startScript = readFileSync(new URL('./start_orion_local_stack.sh', import.meta.url), 'utf8');
const statusScript = readFileSync(new URL('./status_orion_local_stack.sh', import.meta.url), 'utf8');
const projectMap = readFileSync(new URL('../docs/project-map.md', import.meta.url), 'utf8');
const pendingTasks = readFileSync(new URL('../docs/pending-tasks.md', import.meta.url), 'utf8');

test('default local stack launch skips the legacy Nest backend sidecar', () => {
  assert.match(startScript, /BACKEND_PORT="\$\{BACKEND_PORT:-4000\}"/);
  assert.match(startScript, /BACKEND_MODE="\$\{BACKEND_MODE:-skip\}"/);
  assert.match(startScript, /START_BACKEND="\$\{START_BACKEND:-0\}"/);
  assert.match(startScript, /Legacy backend: removed from the active launch path/);
  assert.match(startScript, /legacy backend\/ sidecar is not an active launch target/);
});

test('status script labels the backend as legacy optional', () => {
  assert.match(statusScript, /backend \(legacy optional\)/);
  assert.match(statusScript, /legacy backend/);
});

test('launch docs de-scope the Nest backend from the active launch path', () => {
  assert.match(projectMap, /legacy Nest control-plane sidecar/);
  assert.match(projectMap, /not part of the active launch path/);
  assert.match(pendingTasks, /Nest control-plane sidecar in `\/backend` is out of the active local launch path/);
});
