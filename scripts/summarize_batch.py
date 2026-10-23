"""After every panorama of a batch has run, list each result in one table."""
import json
import os
from pathlib import Path
import requests

STATUS={'complete':'✅ 完了','partial':'⚠️ 一部未取得','probe':'通信確認','unavailable':'❌ 取得できず'}


def summary(ids,results,run):
    rows=[];counts={}
    for gid in ids:
        r=results.get(gid)
        label=STATUS.get(r['status'],r['status']) if r else '❌ 未実行・中断'
        counts[label]=counts.get(label,0)+1
        tiles=f"{r['verified_tiles']:,} / {r['expected_tiles']:,}" if r and r.get('expected_tiles') else '—'
        name=' '.join(str((r or {}).get('name') or '').split()).replace('|','\\|')[:60] or '—'
        rows.append(f'| [{gid}](https://gigapan.com/gigapans/{gid}) | {name} | {label} | {tiles} |')
    head=f"### GigaPan 一括サルベージ結果（{len(ids)} 件）\n\n"+'・'.join(f'{k} {v} 件' for k,v in counts.items())+'\n\n'
    table='| ID | タイトル | 結果 | 検証済みタイル |\n| --- | --- | --- | --- |\n'+'\n'.join(rows)+'\n\n'
    return head+table+f"[実行ログと保存ファイル]({run}) — Artifacts の `gigapan-ID-…` を各パノラマごとにダウンロードできます。保存期限は30日です。未完了の ID は配信元の復旧後、その ID だけで再依頼してください。\n"


def main():
    ids=json.loads(os.environ['BATCH_IDS']);results={}
    for path in Path('results').glob('*/result.json'):
        r=json.loads(path.read_text());results[r['id']]=r
    run=f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    body=summary(ids,results,run)
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(body)
    if os.environ['GITHUB_EVENT_NAME']=='issues':
        event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        response=requests.post(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/issues/{event['issue']['number']}/comments",headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json'},json={'body':body},timeout=20)
        response.raise_for_status()


if __name__=='__main__':main()
