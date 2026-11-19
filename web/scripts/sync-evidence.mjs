// Explicit source-repository operation, never required by the website build.
import {copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
await copyFile(path.join(root,'../plans/CURRENT_STATUS.md'),path.join(root,'assets/project-status.md'));
await copyFile(path.join(root,'../LICENSE'),path.join(root,'assets/LICENSE.txt'));
console.log('Publication evidence snapshots updated from the simulator repository.');
