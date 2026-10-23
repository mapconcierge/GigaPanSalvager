export function parseId(value) {
  const text=String(value).trim();
  if (/^[1-9]\d{0,8}$/.test(text)) return Number(text);
  try {
    const u=new URL(text), m=u.pathname.match(/^\/gigapans\/([1-9]\d{0,8})\/?$/);
    if (['http:','https:'].includes(u.protocol)&&['gigapan.com','www.gigapan.com','gigapan.org','www.gigapan.org'].includes(u.hostname)&&m) return Number(m[1]);
  } catch {}
  throw new Error('GigaPan の数値 ID またはパノラマの URL を入力してください。');
}
export const MAX_BATCH=256;
// One GigaPan ID or URL per line. First CSV column only; blank, # and header lines are skipped; duplicates dropped.
export function parseIdList(text,limit=MAX_BATCH) {
  const ids=[];
  String(text).replace(/^\uFEFF/,'').split(/\r\n|\r|\n/).forEach((line,i)=>{
    const cell=line.split(',')[0].trim().replace(/^["']|["']$/g,'').trim();
    if(!cell||cell.startsWith('#'))return;
    let id;
    try{id=parseId(cell);}
    catch{if(!ids.length&&!/\d/.test(cell))return;throw new Error(`${i+1} 行目「${cell.slice(0,40)}」は GigaPan ID または URL ではありません。`);}
    if(!ids.includes(id))ids.push(id);
  });
  if(!ids.length)throw new Error('ID リストに GigaPan ID がありません。');
  if(ids.length>limit)throw new Error(`ID リストは ${limit} 件までです（${ids.length} 件）。分割してください。`);
  return ids;
}
export function issueUrl(repository,request) {
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('保存先リポジトリの設定が不正です。');
  const u=new URL(`https://github.com/${repository}/issues/new`);
  let title,body;
  if(request.ids){
    // Compact JSON keeps a 256-ID request well inside GitHub's URL length limit.
    const ids=request.ids.map(parseId);if(!ids.length||ids.length>MAX_BATCH||new Set(ids).size!==ids.length)throw new Error('ID リストが不正です。');
    title=`[Salvage] GigaPan 一括 ${ids.length} 件（${ids[0]} ほか）`;
    body='<!-- gigapan-salvager:v1 -->\n```json\n'+JSON.stringify({ids,mode:request.mode})+'\n```\n\n'+`公開されているタイルとメタデータの保存を、${ids.length} 件のパノラマについて1件ずつ順番に依頼します。`;
  }else{
    const id=parseId(request.id);
    title=`[Salvage] GigaPan ${id}`;
    body='<!-- gigapan-salvager:v1 -->\n```json\n'+JSON.stringify({...request,id},null,2)+'\n```\n\n公開されているタイルとメタデータの保存を依頼します。';
  }
  u.searchParams.set('template','salvage.md');u.searchParams.set('title',title);u.searchParams.set('body',body);
  return u.href;
}
export function statusLabel(run) {
  if (run.status!=='completed') return run.status==='queued'?'待機中':'実行中';
  return ({success:'完了',failure:'未完了・ログを確認',cancelled:'キャンセル',skipped:'スキップ',timed_out:'時間切れ'})[run.conclusion]||'終了';
}
