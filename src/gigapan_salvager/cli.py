import argparse
import os
import re
import hashlib
import json
import sys
import tarfile
from pathlib import Path
from . import __version__
from .archive import Salvager, write, write_json, now
from .model import parse_id, parse_id_list, pages_url


def main(argv=None):
    p=argparse.ArgumentParser(description='Public GigaPan panorama preservation')
    p.add_argument('id',type=parse_id,nargs='?',help='GigaPan ID or URL')
    p.add_argument('--id-list',type=Path,metavar='CSV',help='One GigaPan ID or URL per line; salvaged one after another')
    p.add_argument('--output',type=Path,default=Path('archives'))
    p.add_argument('--kml',type=Path)
    p.add_argument('--width',type=int);p.add_argument('--height',type=int)
    p.add_argument('--workers',type=int,default=6,choices=range(1,13))
    p.add_argument('--max-tiles',type=int,default=250000)
    p.add_argument('--max-bytes',type=int,default=8*1024**3)
    p.add_argument('--offline',action='store_true',help='Verify and package already-saved files without network access')
    p.add_argument('--probe',action='store_true',help='Metadata and one tile only; not a full archive')
    p.add_argument('--github-owner',default=os.environ.get('GITHUB_REPOSITORY_OWNER') or 'mapconcierge',
                   help='GitHub user/organisation that will publish gigapan_ID on Pages (default: %(default)s)')
    p.add_argument('--pages-url',help='Published site root instead of https://OWNER.github.io/gigapan_ID/')
    p.add_argument('--version',action='version',version=f'%(prog)s {__version__}')
    args=p.parse_args(argv)
    if bool(args.width)!=bool(args.height):p.error('--width and --height must be supplied together')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})',args.github_owner):p.error('invalid --github-owner')
    if (args.id is None)==(args.id_list is None):p.error('specify either a GigaPan ID or --id-list')
    if args.id is not None:
        try:base=pages_url(args.pages_url or f'https://{args.github_owner.lower()}.github.io/gigapan_{args.id}/')
        except ValueError as exc:p.error(str(exc))
        return salvage(args,args.id,base)
    if args.kml or args.width or args.pages_url:p.error('--kml, --width/--height and --pages-url apply to a single ID only')
    try:ids=parse_id_list(args.id_list.read_text(encoding='utf-8'),limit=100_000)
    except (OSError,UnicodeDecodeError,ValueError) as exc:p.error(f'--id-list: {exc}')
    results=[]
    for n,gid in enumerate(ids,1):
        # Sequential on purpose: each panorama finishes (or is recorded as incomplete) before the next starts.
        print(json.dumps({'batch':f'{n}/{len(ids)}','id':gid}),flush=True)
        salvage(args,gid,pages_url(f'https://{args.github_owner.lower()}.github.io/gigapan_{gid}/'))
        result=json.loads((args.output/'job-result.json').read_text())
        results.append({k:result.get(k) for k in ('id','name','status','exit_code','expected_tiles','verified_tiles','missing_tiles','archive')})
        write_json(args.output/'batch-result.json',{'finished':n,'total':len(ids),'results':results})
    return max(r['exit_code'] for r in results)


def salvage(args,gid,base):
    job=Salvager(gid,args.output,workers=args.workers,offline=args.offline,max_tiles=args.max_tiles,max_bytes=args.max_bytes,pages_base=base)
    try:
        job.bootstrap(args.kml,args.width,args.height)
        job.extras()
        report=job.download(args.probe)
        archive=job.finalize();code=job.result['exit_code']
    except Exception as exc:
        report={'id':gid,'status':'unavailable','finished_at':now(),'error':str(exc),'warnings':job.warnings,'errors':job.errors}
        write_json(job.site/'audit.json',report)
        archive=job.root.with_suffix('.tar.gz')
        with tarfile.open(archive,'w:gz',dereference=False) as tar:tar.add(job.root,arcname=job.root.name)
        digest=hashlib.sha256()
        with archive.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
        write(archive.with_name(archive.name+'.sha256'),digest.hexdigest()+'  '+archive.name+'\n')
        print(str(exc),file=sys.stderr);code=1
    write_json(args.output/'job-result.json',{'archive':str(archive),'exit_code':code,**report})
    print(json.dumps({'archive':str(archive),'status':report['status'],'exit_code':code},ensure_ascii=False),flush=True)
    return code


if __name__=='__main__':sys.exit(main())
