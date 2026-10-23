"""Validate event data before passing it to the downloader. Never evaluate event text.

Without PICK_ID: validate the request and output the panorama IDs for the job matrix.
With PICK_ID: write work/request.json for that one panorama of the validated request.
"""
import json
import os
import re
from pathlib import Path
import requests
from gigapan_salvager.model import parse_id, parse_id_list, kml_metadata, Geometry, MAX_BATCH


def parse_request(event,event_name,repo,check_permission=None):
    if event_name=='issues':
        issue=event['issue'];login=issue['user']['login']
        if not issue['title'].startswith('[Salvage] GigaPan '):raise ValueError('Not a salvage request')
        if login!=repo.split('/')[0] and not (check_permission and check_permission(login)):
            raise ValueError('救出を実行できるのはリポジトリ所有者・書き込み権限のある共同編集者です。')
        body=issue.get('body') or ''
        if len(body)>100_000 or '<!-- gigapan-salvager:v1 -->' not in body:raise ValueError('Invalid request marker or size')
        match=re.search(r'```json\s*\n([\s\S]+?)\n```',body)
        if not match:raise ValueError('Request JSON not found')
        value=json.loads(match[1])
    else:
        i=event.get('inputs',{})
        # Several IDs may be separated by commas, spaces or new lines.
        ids=parse_id_list('\n'.join(re.split(r'[\s,]+',str(i.get('gigapan_id','')).strip())))
        value={'id':ids[0],'mode':i.get('mode','full')} if len(ids)==1 else {'ids':ids,'mode':i.get('mode','full')}
        for key in ('width','height'):
            if i.get(key):value[key]=int(i[key])
    if not isinstance(value,dict) or set(value)-{'id','ids','mode','kml','width','height'}:raise ValueError('Unsupported request fields')
    if value.get('mode','full') not in ('full','probe'):raise ValueError('Invalid mode')
    if 'ids' in value:
        if 'id' in value or not isinstance(value['ids'],list) or not 1<=len(value['ids'])<=MAX_BATCH:raise ValueError('Invalid ID list')
        if any(value.get(k) for k in ('kml','width','height')):raise ValueError('KML・寸法の補助は1件ずつの依頼でのみ指定できます。')
        ids=[parse_id(x) for x in value['ids']]
        if len(set(ids))!=len(ids):raise ValueError('ID リストに重複があります。')
        value={'ids':ids,'mode':value.get('mode','full')}
    else:
        gid=parse_id(value.get('id',''))
        value.update(id=gid,ids=[gid],mode=value.get('mode','full'))
    if value.get('kml'):
        if not isinstance(value['kml'],str):raise ValueError('KML must be text')
        Geometry.from_metadata(kml_metadata(value['kml'].encode('utf-8'),value['id']))
    if value.get('width') or value.get('height'):
        Geometry.from_metadata({'width':value.get('width'),'height':value.get('height')})
    return value


def pick(request,gid):
    """The single-panorama request for one matrix job."""
    gid=parse_id(gid)
    if gid not in request['ids']:raise ValueError('ID is not part of this request')
    one={'id':gid,'mode':request['mode']}
    for key in ('kml','width','height'):
        if request.get(key):one[key]=request[key]
    return one


def main():
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text());repo=os.environ['GITHUB_REPOSITORY']
    def permitted(login):
        response=requests.get(f'https://api.github.com/repos/{repo}/collaborators/{login}/permission',headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json'},timeout=20)
        response.raise_for_status()
        return response.json().get('permission') in ('admin','write','maintain')
    request=parse_request(event,os.environ['GITHUB_EVENT_NAME'],repo,permitted)
    with open(os.environ['GITHUB_OUTPUT'],'a') as f:
        if os.environ.get('PICK_ID'):
            one=pick(request,os.environ['PICK_ID'])
            Path('work').mkdir(exist_ok=True);Path('work/request.json').write_text(json.dumps(one))
            f.write(f'id={one["id"]}\nmode={one["mode"]}\n')
        else:
            f.write(f'ids={json.dumps(request["ids"])}\ncount={len(request["ids"])}\nmode={request["mode"]}\n')


if __name__=='__main__':main()
