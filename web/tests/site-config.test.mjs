import test from 'node:test';
import assert from 'node:assert/strict';
import {studioBase,publicConfig,SITE_FILES,SITE_ASSETS} from '../site-config.js';
test('website works without an application URL',()=>{assert.equal(studioBase(null),null);assert.equal(publicConfig({schema:'power-grid-env.public-site-config/v1',studio_url:null}).studio_url,null);});
test('application URL preserves a deployment subpath',()=>{assert.equal(studioBase('https://example.test/tools/studio'),'https://example.test/tools/studio/');assert.equal(new URL('editor.html',studioBase('https://example.test/tools/studio')).href,'https://example.test/tools/studio/editor.html');});
for(const value of ['javascript:alert(1)','file:///tmp/studio','/studio','https://user:secret@example.test/','https://example.test/?token=secret','https://example.test/#run',' https://example.test/'])test('unsafe or ambiguous public application base rejected: '+value,()=>assert.throws(()=>studioBase(value)));
test('environment override changes only the public navigation URL',()=>{assert.equal(publicConfig({schema:'power-grid-env.public-site-config/v1',studio_url:null},'http://localhost:5174').studio_url,'http://localhost:5174/');assert.throws(()=>publicConfig({schema:'power-grid-env.public-site-config/v1',api_key:'secret'}));});
test('website publication inventory excludes engineering runtime and recordings',()=>{for(const file of [...SITE_FILES,...SITE_ASSETS])assert.ok(!/^(editor|grid|steady|agent-lab)\.(html|js)|grid-replays|server\.mjs|^tests\//.test(file),file);});
