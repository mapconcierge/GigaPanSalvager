import json
import os
from pathlib import Path
import requests

p=Path('archives/job-result.json')
r=json.loads(p.read_text()) if p.exists() else {'status':'unavailable','error':'取得処理が中断されました。'}
run=f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
status={'complete':'全タイル保存完了','partial':'一部未取得','probe':'通信確認（全画像の保存ではありません）','unavailable':'取得できませんでした'}.get(r['status'],r['status'])
body=f"### GigaPan サルベージ: {status}\n\n"
if 'expected_tiles' in r:body+=f"検証済み **{r['verified_tiles']:,} / {r['expected_tiles']:,} 枚**。\n\n"
if r.get('error'):body+=str(r['error'])[:500]+'\n\n'
body+=f"[実行ログと保存ファイル]({run}) — Artifacts からダウンロードできます。保存期限は30日です。\n\n"
if r.get('pages_url'):body+=f"展開した `gigapan_{r['id']}/` はそのまま `gigapan_{r['id']}` リポジトリとして push し、Settings → Pages で `main` ブランチの `/docs` を公開できます（公開予定 URL: {r['pages_url']}）。\n\n"
body+="未完了の場合は、配信元の復旧後に再実行してください。キャッシュが残っていれば検証済みタイルを再利用します。\n"
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(body)
event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
if os.environ['GITHUB_EVENT_NAME']=='issues':
    number=event['issue']['number']
    response=requests.post(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/issues/{number}/comments",headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json'},json={'body':body},timeout=20)
    response.raise_for_status()
size=sum(p.stat().st_size for p in Path('archives').glob('gigapan_*/docs/tiles/**/*.jpg'))
with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write(f'cache_ok={str(0<size<4*1024**3).lower()}\n')
