import {parseId,parseIdList,issueUrl,statusLabel} from './core.mjs';
const $=s=>document.querySelector(s);
let config={repository:'mapconcierge/GigaPanSalvager'},kml=null,batch=null;
function error(message){$('#error').textContent=message;$('#error').hidden=!message;}
function request(){
  if(batch){if(kml)throw new Error('KML による寸法の補助は1件ずつの依頼でのみ使えます。');return {ids:batch.ids,mode:$('#mode').value};}
  const id=parseId($('#pano-id').value),result={id,mode:$('#mode').value};
  if(kml){if(kml.id&&kml.id!==id)throw new Error('KML の ID と入力した ID が異なります。');result.width=kml.width;result.height=kml.height;}
  return result;
}
$('#kml-file').addEventListener('change',async e=>{
  kml=null;$('#kml-info').textContent='';error('');const f=e.target.files[0];if(!f)return;
  try{
    if(f.size>256000)throw new Error('KML は 256 KB までです。');
    const text=await f.text();if(/<!\s*(DOCTYPE|ENTITY)/i.test(text))throw new Error('対応していない XML 宣言です。');
    const xml=new DOMParser().parseFromString(text,'application/xml');
    if(xml.querySelector('parsererror'))throw new Error('KML を XML として読み込めませんでした。');
    const ns='http://www.opengis.net/kml/2.2',overlay=xml.getElementsByTagNameNS(ns,'PhotoOverlay')[0];
    if(!overlay)throw new Error('KML に PhotoOverlay がありません。');
    const get=tag=>overlay.getElementsByTagNameNS(ns,tag)[0]?.textContent;
    const width=Number(get('maxWidth')),height=Number(get('maxHeight')),tileSize=Number(get('tileSize'));
    if(!Number.isSafeInteger(width)||!Number.isSafeInteger(height)||width<1||height<1||Math.max(width,height)>10000000||tileSize!==256)throw new Error('KML の画像寸法またはタイルサイズが対応範囲外です。');
    const id=overlay.getAttribute('id')?.match(/^gigapan_(\d+)$/)?.[1];
    kml={width,height,id:id?Number(id):null};$('#kml-info').textContent=`読み取り済み：${width.toLocaleString()} × ${height.toLocaleString()} px`;
  }catch(ex){e.target.value='';error(ex.message);}
});
function setBatch(next){
  batch=next;const input=$('#pano-id');input.disabled=input.required=false;
  if(batch){input.disabled=true;$('#csv-info').textContent=`読み取り済み：${batch.ids.length} 件（${batch.ids.slice(0,5).join(', ')}${batch.ids.length>5?' ほか':''}）`;$('#submit-label').textContent=`GitHub で一括救出依頼を作成（${batch.ids.length} 件）`;}
  else{input.required=true;$('#csv-info').textContent='';$('#id-csv').value='';$('#submit-label').textContent='GitHub で救出依頼を作成';}
  $('#csv-clear').hidden=!batch;
}
$('#id-csv').addEventListener('change',async e=>{
  error('');const f=e.target.files[0];setBatch(null);if(!f)return;
  try{if(f.size>1_000_000)throw new Error('ID リストは 1 MB までです。');setBatch({ids:parseIdList(await f.text()),name:f.name});}
  catch(ex){error(ex.message);}
});
$('#csv-clear').addEventListener('click',()=>{setBatch(null);error('');});
$('#request-form').addEventListener('submit',e=>{
  e.preventDefault();error('');
  try{const req=request();const u=issueUrl(config.repository,req);location.assign(u);}
  catch(ex){error(ex.message);}
});
$('#copy-command').addEventListener('click',async()=>{
  try{const req=request();let cmd=req.ids?`gigapan-salvage --id-list ${JSON.stringify(batch.name)}`:`gigapan-salvage ${req.id}`;if(req.mode==='probe')cmd+=' --probe';if(req.width)cmd+=` --width ${req.width} --height ${req.height}`;await navigator.clipboard.writeText(cmd);$('#copy-status').textContent='コピーしました：'+cmd;error('');}
  catch(ex){error(ex.message);}
});
async function loadRuns(){
  const holder=$('#runs');$('#refresh').disabled=true;
  try{
    const response=await fetch(`https://api.github.com/repos/${config.repository}/actions/workflows/salvage.yml/runs?per_page=6`,{headers:{Accept:'application/vnd.github+json'}});
    if(!response.ok)throw new Error(response.status===403?'GitHub API の取得回数制限に達しました。':'実行履歴を取得できませんでした。');
    const data=await response.json();holder.replaceChildren();
    if(!data.workflow_runs.length){const p=document.createElement('p');p.className='subtle';p.textContent='まだ救出ジョブはありません。ID を入力して最初の保存を始めましょう。';holder.append(p);}
    for(const run of data.workflow_runs){
      const u=new URL(run.html_url);if(u.origin!=='https://github.com')continue;
      const a=document.createElement('a');a.className='run '+(run.conclusion||'');a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';
      const dot=document.createElement('span');dot.className='dot';dot.setAttribute('aria-hidden','true');
      const title=document.createElement('span');title.className='run-title';title.textContent=run.display_title;
      const date=document.createElement('small');date.textContent=new Date(run.created_at).toLocaleString('ja-JP',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
      const status=document.createElement('span');status.className='status';status.textContent=statusLabel(run)+' ↗';
      a.append(dot,title,date,status);holder.append(a);
    }
  }catch(ex){holder.replaceChildren();const p=document.createElement('p');p.className='subtle';p.textContent=ex.message+' ';const a=document.createElement('a');a.href=`https://github.com/${config.repository}/actions/workflows/salvage.yml`;a.textContent='GitHub で確認 ↗';p.append(a);holder.append(p);}
  finally{$('#refresh').disabled=false;}
}
$('#refresh').addEventListener('click',loadRuns);
try{const r=await fetch('config.json');if(r.ok){const c=await r.json();if(/^[\w.-]+\/[\w.-]+$/.test(c.repository))config=c;}}catch{}
$('#repo-link').href=`https://github.com/${config.repository}`;$('#manual-link').href=`https://github.com/${config.repository}/actions/workflows/salvage.yml`;
const supplied=new URLSearchParams(location.search).get('id');if(supplied){try{$('#pano-id').value=parseId(supplied);}catch{}}
loadRuns();
