import test from 'node:test';
import assert from 'node:assert/strict';
import {parseId,parseIdList,issueUrl,statusLabel} from '../web/core.mjs';
test('ID or public panorama URL',()=>{assert.equal(parseId(' 76564 '),76564);assert.equal(parseId('https://gigapan.com/gigapans/76564'),76564);for(const bad of ['0','42;echo bad','https://evil.test/gigapans/42'])assert.throws(()=>parseId(bad));});
test('issue request roundtrip without shell interpolation',()=>{const u=new URL(issueUrl('owner/repo',{id:42,mode:'full',width:513,height:257}));assert.equal(u.origin,'https://github.com');const body=u.searchParams.get('body');assert.match(body,/gigapan-salvager:v1/);assert.deepEqual(JSON.parse(body.match(/```json\n([\s\S]+?)\n```/)[1]),{id:42,mode:'full',width:513,height:257});});
test('failed run is visibly incomplete',()=>{assert.equal(statusLabel({status:'completed',conclusion:'failure'}),'未完了・ログを確認');});
test('ID list CSV: one per line, header/blank/comment skipped, duplicates dropped',()=>{
  assert.deepEqual(parseIdList('﻿id\r\n76564\n\n# memo\nhttps://gigapan.com/gigapans/76649, 大槌\n76564\n'),[76564,76649]);
  assert.throws(()=>parseIdList('76564\nabc1\n'),/2 行目/);
  assert.throws(()=>parseIdList('id\n'),/ID がありません/);
  assert.throws(()=>parseIdList(Array.from({length:257},(_,i)=>i+1).join('\n')),/256 件/);
});
test('batch issue carries the ID list compactly',()=>{
  const ids=Array.from({length:256},(_,i)=>100000+i);
  const u=new URL(issueUrl('owner/repo',{ids,mode:'full'}));
  assert.ok(u.href.length<8000,`URL length ${u.href.length}`);
  assert.match(u.searchParams.get('title'),/^\[Salvage\] GigaPan 一括 256 件/);
  assert.deepEqual(JSON.parse(u.searchParams.get('body').match(/```json\n([\s\S]+?)\n```/)[1]),{ids,mode:'full'});
});
