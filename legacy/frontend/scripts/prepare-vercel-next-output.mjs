import fs from 'node:fs';
import path from 'node:path';

const cwd = process.cwd();
const dirName = path.basename(cwd);
const source = path.join(cwd, '.next');
const mirrorRoot = path.join(cwd, dirName);
const target = path.join(mirrorRoot, '.next');
const requiredRuntimeFiles = [
  'BUILD_ID',
  'app-path-routes-manifest.json',
  'build-manifest.json',
  'fallback-build-manifest.json',
  'images-manifest.json',
  'package.json',
  'prerender-manifest.json',
  'required-server-files.json',
  'routes-manifest.json',
  'server/app-paths-manifest.json',
  'server/functions-config-manifest.json',
  'server/middleware-build-manifest.js',
  'server/middleware-manifest.json',
  'server/next-font-manifest.json',
  'server/pages-manifest.json',
  'server/server-reference-manifest.js',
  'server/server-reference-manifest.json',
];

if (!fs.existsSync(source)) {
  throw new Error(`Missing Next build output at ${source}`);
}

function walkFiles(root) {
  const stack = [root];
  const files = [];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
      } else if (entry.isFile()) {
        files.push(fullPath);
      }
    }
  }
  return files;
}

function toTracePath(filePath) {
  return filePath.split(path.sep).join('/');
}

const includePaths = requiredRuntimeFiles
  .map((file) => path.join(source, file))
  .filter((file) => fs.existsSync(file));

for (const traceFile of walkFiles(source).filter((file) => file.endsWith('.nft.json'))) {
  const pageDir = path.dirname(traceFile);
  const trace = JSON.parse(fs.readFileSync(traceFile, 'utf8'));
  const files = new Set(Array.isArray(trace.files) ? trace.files : []);
  for (const includePath of includePaths) {
    files.add(toTracePath(path.relative(pageDir, includePath)));
  }
  fs.writeFileSync(traceFile, JSON.stringify({ ...trace, files: [...files] }));
}

if (path.resolve(source) === path.resolve(target)) {
  process.exit(0);
}

fs.rmSync(target, { recursive: true, force: true });
fs.mkdirSync(mirrorRoot, { recursive: true });

try {
  fs.symlinkSync(path.relative(mirrorRoot, source), target, 'dir');
} catch {
  fs.cpSync(source, target, { recursive: true });
}

const vercelOutputRoot = path.join(cwd, '.vercel', 'output');
const nextStaticSource = path.join(source, 'static');
const vercelStaticRoot = path.join(vercelOutputRoot, 'static', '_next');
const vercelStaticTarget = path.join(vercelStaticRoot, 'static');

if (fs.existsSync(vercelOutputRoot) && fs.existsSync(nextStaticSource)) {
  fs.rmSync(vercelStaticTarget, { recursive: true, force: true });
  fs.mkdirSync(vercelStaticRoot, { recursive: true });
  fs.cpSync(nextStaticSource, vercelStaticTarget, { recursive: true });
}
