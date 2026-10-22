"""Recover metadata, download every expected tile, and package an auditable archive."""
import concurrent.futures
import csv
import hashlib
import html
import io
import itertools
import json
import math
import os
import re
import shutil
import tarfile
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from PIL import Image
from . import __version__
from .model import Geometry, kml_metadata, offline_kml, pages_url, safe_xml, NS
from .readme import repository_readme
from .network import Client, ORIGINS, allowed_url


LEGACY_SITE_FILES=('tiles','ge_tiles','metadata','thumbnails','tile_manifest.csv','missing_tiles.json','audit.json','plan.json','preview.jpg','viewer.html')


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.part')
    temp.write_bytes(data if isinstance(data,bytes) else data.encode('utf-8'))
    os.replace(temp,path)


def write_json(path, data):
    write(path,json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def decode_json(raw, warnings):
    try:
        text=raw.decode('utf-8')
    except UnicodeDecodeError:
        text=raw.decode('utf-8','replace')
        warnings.append('公開 JSON に壊れた UTF-8 が含まれています。原本と文字置換した正規化版を保存しました。')
    return json.loads(text)


def inspect_jpeg(raw, tile_size=None):
    if not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
        raise ValueError('JPEG の先頭・終端が不正です。')
    with Image.open(io.BytesIO(raw)) as image:
        image.load()  # Decode all pixels; verify() alone does not detect every truncated JPEG.
        width,height=image.size
        if tile_size and not (0 < width <= tile_size and 0 < height <= tile_size):
            raise ValueError('タイルの画像寸法が不正です。')
    return width,height,hashlib.sha256(raw).hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__();self.images=[];self.links=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='img' and a.get('src'):self.images.append(a['src'])
        if tag=='meta' and a.get('property')=='og:image':self.images.append(a.get('content',''))
        if tag=='a' and a.get('href'):self.links.append(a['href'])


class Salvager:
    def __init__(self,gid,output,client=None,workers=6,offline=False,max_tiles=250000,max_bytes=8*1024**3,pages_base=None):
        # root is a ready-to-push repository; docs/ is the GitHub Pages site.
        self.gid=gid;self.root=Path(output)/f'gigapan_{gid}';self.site=self.root/'docs'
        self.pages_base=pages_url(pages_base or f'https://mapconcierge.github.io/gigapan_{gid}/')
        self.migrate_layout()
        self.meta=self.site/'metadata';self.meta.mkdir(parents=True,exist_ok=True)
        self.client=client or Client();self.workers=workers;self.offline=offline
        self.max_tiles=max_tiles;self.max_bytes=max_bytes
        self.warnings=[];self.sources=[];self.errors=[];self.stop=threading.Event();self.lock=threading.Lock()
        self.downloaded_bytes=0;self.consecutive_failures=0;self.last_error=''
        self.page_config={}

    def migrate_layout(self):
        """Move archives saved by v0.1 (files directly under root) into docs/ so retries keep verified tiles."""
        for name in LEGACY_SITE_FILES:
            old=self.root/name;new=self.site/name
            if old.exists() and not new.exists():
                new.parent.mkdir(parents=True,exist_ok=True);os.replace(old,new)
        (self.root/f'gigapan_{self.gid}_offline.kml').unlink(missing_ok=True)

    def resource(self,name,path,validator=None):
        dest=self.meta/name
        # Saved originals are deliberately reusable during retries / outages.
        if dest.exists():
            raw=dest.read_bytes()
            try:
                if validator:validator(raw)
                return raw
            except (ValueError,KeyError,TypeError):pass
        if self.offline:return None
        errors=[]
        for origin in ORIGINS:
            try:
                raw,info=self.client.get(origin+path,attempts=1)
                if validator:validator(raw)
                write(dest,raw);self.sources.append({'file':str(dest.relative_to(self.site)),'retrieved_at':now(),**info})
                return raw
            except Exception as exc:errors.append(str(exc)[:250])
        self.errors.append({'resource':name,'attempt_errors':errors})
        return None

    def bootstrap(self,kml=None,width=None,height=None):
        def valid_json(raw):
            m=decode_json(raw,self.warnings)['gigapan']
            if int(m['id'])!=self.gid:raise ValueError('API returned a different GigaPan ID')
            Geometry.from_metadata(m)
        raw_json=self.resource('gigapan.json',f'/gigapans/{self.gid}.json',valid_json)
        m=decode_json(raw_json,self.warnings)['gigapan'] if raw_json else {}
        if m.get('is_private') is True:raise ValueError('非公開の GigaPan はこのツールの対象外です。')
        if kml:
            raw_kml=Path(kml).read_bytes()
            k=kml_metadata(raw_kml,self.gid)
            write(self.meta/'source.kml',raw_kml)
        else:
            raw_kml=self.resource('source.kml',f'/gigapans/{self.gid}.kml',lambda b:kml_metadata(b,self.gid))
            k=kml_metadata(raw_kml,self.gid) if raw_kml else {}
        if m and k and (m['width'],m['height'])!=(k['width'],k['height']):
            raise ValueError('API と KML の画像寸法が異なります。取得を停止しました。')
        if not m:
            m=k or ({'width':width,'height':height} if width and height else {})
            if m:self.warnings.append('公開 JSON を取得できないため、KML または入力寸法を使用しました。')
        if not m:raise ValueError('画像寸法を取得できません。GigaPan 復旧後に再実行するか、KML を指定してください。')
        m.update({'id':self.gid,'name':m.get('name',f'GigaPan {self.gid}')})
        self.geometry=Geometry.from_metadata(m)
        if self.geometry.count>self.max_tiles:raise ValueError(f'タイル数 {self.geometry.count:,} が上限 {self.max_tiles:,} を超えます。ローカル版の --max-tiles で変更できます。')
        if m.get('tiles_total') is not None and int(m['tiles_total'])!=self.geometry.count:
            self.warnings.append(f"公開 tiles_total={m['tiles_total']} と計算値 {self.geometry.count} が異なります。")
        plan={'id':self.gid,**asdict(self.geometry)}
        previous=self.site/'plan.json'
        if previous.exists() and json.loads(previous.read_text())!=plan:
            raise ValueError('保存済みの画像寸法と異なります。新しい保存先を指定してください。')
        write_json(previous,plan);write_json(self.meta/'normalized.json',m)
        self.metadata=m;self.raw_kml=raw_kml
        self.prior={}
        manifest=self.site/'tile_manifest.csv'
        if manifest.exists():
            with manifest.open() as f:self.prior={r['relative_path']:r for r in csv.DictReader(f)}
        return m

    def extras(self):
        page=self.resource('page.html',f'/gigapans/{self.gid}',lambda b:None if b'<html' in b.lower() else (_ for _ in ()).throw(ValueError('not HTML')))
        expected=None
        if page:
            match=re.search(rb'total_entries:\s*(\d+)',page)
            if match:expected=int(match[1])
            match=re.search(rb'var gigapan = (\{[^\n]+\});',page)
            if match:
                try:
                    self.page_config=decode_json(match[1],self.warnings)
                    write_json(self.meta/'page_config.json',self.page_config)
                except ValueError:pass
        snapshots=[];seen=set();pagination_complete=False
        for p in range(1,101):
            raw=self.resource(f'snapshots_page_{p}.json',f'/gigapans/{self.gid}/snapshots.json?page={p}&per_page=100',lambda b:decode_json(b,[]))
            if raw is None:break
            data=decode_json(raw,self.warnings)
            items=data if isinstance(data,list) else data.get('items',data.get('snapshots',[]))
            if not items:pagination_complete=True;break
            added=0
            for item in items:
                snap=item.get('snapshot',item)
                sid=snap.get('id')
                if not isinstance(sid,int) or sid in seen:continue
                if snap.get('gigapan_id',self.gid)!=self.gid:continue
                seen.add(sid);snapshots.append(snap);added+=1
            if expected is not None and len(snapshots)>=expected:pagination_complete=True;break
            if not added:break
        if not pagination_complete:self.warnings.append('スナップショット一覧の全ページ取得を確認できませんでした。')
        write_json(self.meta/'snapshots_normalized.json',snapshots)
        for snap in snapshots:
            if snap.get('comment_count',0):self.comments(f'/gigapans/show_snapshot_comments/{snap["id"]}',f'snapshot_{snap["id"]}_comments')
        if self.metadata.get('options',{}).get('comment_count',0):
            self.comments(f'/gigapans/{self.gid}/comments','panorama_comments')
        urls={f'https://static.gigapan.com/gigapans0/{self.gid}/images/{self.gid}-499x186.jpg'}
        for snap in snapshots:
            if snap.get('thumbnail_uri'):urls.add(urljoin('https://static.gigapan.com',snap['thumbnail_uri']))
        if page:
            parser=Links();parser.feed(page.decode('utf-8','replace'))
            urls.update(urljoin('https://gigapan.com',u) for u in parser.images if f'/{self.gid}/images/' in u)
        for url in sorted(urls):
            if not allowed_url(url):continue
            # Store only this panorama's images, including its snapshot thumbnails.
            if not re.search(rf'/(?:gigapans|snapshots)\d+/{self.gid}/images/[A-Za-z0-9_-]+\.jpg$',urlparse(url).path):continue
            filename=Path(urlparse(url).path).name;dest=self.site/'thumbnails'/filename
            if dest.exists() or self.offline:continue
            try:
                data,info=self.client.get(url,attempts=2);inspect_jpeg(data)
                write(dest,data);self.sources.append({'file':str(dest.relative_to(self.site)),**info,'retrieved_at':now()})
            except Exception as exc:self.errors.append({'resource':url,'error':str(exc)[:250]})
        self.snapshots=snapshots

    def comments(self,path,prefix):
        pending=[path];visited=set()
        while pending and len(visited)<100:
            urlpath=pending.pop(0)
            if urlpath in visited:continue
            visited.add(urlpath)
            raw=self.resource(f'{prefix}_{len(visited)}.html',urlpath)
            if not raw:continue
            parser=Links();parser.feed(raw.decode('utf-8','replace'))
            for link in parser.links:
                u=urlparse(urljoin('https://gigapan.com'+urlpath,link))
                if u.hostname not in ('gigapan.com','www.gigapan.com'):continue
                # Pagination is constrained to this comment thread.
                same=(u.path==path or (prefix.startswith('snapshot_') and u.path==f'/snapshots/{prefix.split("_")[1]}/comments'))
                pages=parse_qs(u.query).get('page',[])
                if same and pages and re.fullmatch(r'[1-9][0-9]{0,2}',pages[0]):pending.append(u.path+'?page='+pages[0])

    def tile_bases(self):
        shard=f'{self.gid//1000:02d}'
        bases=[]
        hint=self.metadata.get('tile_server_path') or self.page_config.get('gigapan',self.page_config).get('tile_server_path')
        if hint and allowed_url(hint) and re.fullmatch(rf'/gigapans[0-9]+/{self.gid}/tiles/',urlparse(hint).path):
            bases.append(hint.replace('http:','https:',1))
        bases += [f'{scheme}://tile{shard}.gigapan.{domain}/gigapans0/{self.gid}/tiles/' for scheme,domain in [('https','com'),('https','org'),('http','com'),('http','org')]]
        return list(dict.fromkeys(bases))

    def select_origin(self):
        for base in self.tile_bases():
            try:
                raw,info=self.client.get(base+'r.jpg',attempts=1);inspect_jpeg(raw,self.geometry.tile_size)
                write(self.site/'tiles/r.jpg',raw)
                return base
            except Exception as exc:self.errors.append({'resource':base+'r.jpg','error':str(exc)[:250]})
        self.warnings.append('タイル配信元へ接続できません。保存済み画像を検証し、未取得分を記録します。')
        return None

    def one_tile(self,task,base):
        z,x,y,name=task;rel='tiles/'+name;dest=self.site/rel
        row={'level':z,'x':x,'y':y,'relative_path':rel,'source_url':(base or self.tile_bases()[0])+name,'status':'missing','bytes':0,'width':0,'height':0,'sha256':''}
        try:
            if dest.exists():
                raw=dest.read_bytes();w,h,digest=inspect_jpeg(raw,self.geometry.tile_size)
                prior=self.prior.get(rel)
                if prior and prior.get('sha256') and prior['sha256']!=digest:raise ValueError('SHA-256 mismatch')
                row.update(status='existing',bytes=len(raw),width=w,height=h,sha256=digest)
                return row
        except Exception:
            dest.unlink(missing_ok=True)
        if self.offline or not base or self.stop.is_set():return row
        try:
            raw,_=self.client.get(base+name,limit=2_000_000);w,h,digest=inspect_jpeg(raw,self.geometry.tile_size)
            with self.lock:
                if self.downloaded_bytes+len(raw)>self.max_bytes:
                    self.stop.set();raise RuntimeError('ダウンロード容量の上限に達しました。')
                self.downloaded_bytes+=len(raw);self.consecutive_failures=0
            write(dest,raw)
            row.update(status='downloaded',bytes=len(raw),width=w,height=h,sha256=digest)
        except Exception as exc:
            row['error']=str(exc)[:250]
            with self.lock:
                self.last_error=row['error'];self.consecutive_failures+=1
                if self.consecutive_failures>=30:self.stop.set()
        return row

    def download(self,probe=False):
        base=None if self.offline else self.select_origin()
        rows=[];tasks=iter(self.geometry.tiles());started=now()
        # Probe is explicit: collect metadata and one tile, never claim a full salvage.
        if probe:tasks=iter(itertools.islice(tasks,1))
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            while True:
                batch=list(itertools.islice(tasks,256))
                if not batch:break
                for row in pool.map(lambda t:self.one_tile(t,base),batch):rows.append(row)
                good=sum(r['status']!='missing' for r in rows)
                print(json.dumps({'verified':good,'processed':len(rows),'expected':self.geometry.count,'bytes_downloaded':self.downloaded_bytes}),flush=True)
        self.rows=sorted(rows,key=lambda r:(r['level'],r['y'],r['x']))
        good=[r for r in rows if r['status']!='missing']
        status='probe' if probe else ('complete' if len(good)==self.geometry.count else 'partial')
        self.report={'id':self.gid,'name':self.metadata.get('name'),'status':status,'started_at':started,'finished_at':now(),
          'geometry':asdict(self.geometry),'expected_tiles':self.geometry.count,'verified_tiles':len(good),
          'missing_tiles':self.geometry.count-len(good),'tile_bytes':sum(r['bytes'] for r in good),
          'downloaded_bytes':self.downloaded_bytes,'last_error':self.last_error,'warnings':list(dict.fromkeys(self.warnings)),
          'snapshot_count':len(getattr(self,'snapshots',[]))}
        return self.report

    def finalize(self):
        site=self.site
        fields=['level','x','y','relative_path','source_url','status','bytes','width','height','sha256']
        with (site/'tile_manifest.csv').open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(self.rows)
        write_json(site/'missing_tiles.json',[r for r in self.rows if r['status']=='missing'])
        for r in self.rows:
            dest=site/f'ge_tiles/{r["level"]}/{r["y"]}/{r["x"]}.jpg'
            if r['status']=='missing':
                dest.unlink(missing_ok=True)
                continue
            source=site/r['relative_path'];dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists() and os.path.samefile(source,dest):continue
            temp=dest.with_suffix('.part')
            if temp.exists():temp.unlink()
            try:os.link(source,temp)
            except OSError:shutil.copyfile(source,temp)
            os.replace(temp,dest)
        # Published KML points at the GitHub Pages copy; the _local variant works from an unpacked folder.
        for name,base in ((f'gigapan_{self.gid}.kml',self.pages_base),(f'gigapan_{self.gid}_local.kml','')):
            try:
                kml=offline_kml(self.raw_kml,self.metadata,base)
                if kml:write(site/name,self.thumbnail_links(kml,base) if base else kml)
                else:self.warnings.append('位置または画角情報が不足しているため KML は生成していません。');break
            except ValueError as exc:self.warnings.append(str(exc));break
        self.preview()
        if self.report['tile_bytes']*2>1024**3:
            self.warnings.append('tiles/ と ge_tiles/ の合計が GitHub Pages の公開サイト上限 1 GB を超える見込みです。公開前に容量を確認してください。')
        self.report['warnings']=list(dict.fromkeys(self.warnings))
        self.report['salvager_version']=__version__;self.report['pages_url']=self.pages_base
        write_json(site/'audit.json',self.report)
        log=self.meta/'retrieval_log.json'
        history=json.loads(log.read_text()).get('history',[]) if log.exists() else []
        history.append({'time':now(),'sources':self.sources,'errors':self.errors})
        write_json(log,{'history':history})
        template=Path(__file__).with_name('viewer.html').read_text()
        data=json.dumps({'id':self.gid,'name':self.metadata.get('name'),'width':self.geometry.width,'height':self.geometry.height,'levels':self.geometry.levels,'status':self.report['status']},ensure_ascii=False).replace('<','\\u003c')
        viewer=template.replace('__PANORAMA_JSON__',data)
        write(site/'index.html',viewer);write(site/'viewer.html',viewer)
        write(site/'.nojekyll','')  # Serve thousands of tiles as-is; skip the Jekyll build.
        code=0 if self.report['status']=='complete' or (self.report['status']=='probe' and self.report['verified_tiles']==1) else 2
        self.result={'archive':f'gigapan_{self.gid}.tar.gz','exit_code':code,**self.report}
        write_json(self.root/'job-result.json',self.result)
        write(self.root/'README.md',repository_readme(self))
        archive=self.root.with_suffix('.tar.gz')
        temp=archive.with_suffix('.tmp')
        with tarfile.open(temp,'w:gz',compresslevel=3,dereference=False) as tar:tar.add(self.root,arcname=self.root.name)
        os.replace(temp,archive)
        digest=hashlib.sha256()
        with archive.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
        write(archive.with_name(archive.name+'.sha256'),digest.hexdigest()+'  '+archive.name+'\n')
        return archive

    def thumbnail_links(self,kml,base):
        """Point KML references to thumbnails we saved at their published absolute URL."""
        thumbs=self.site/'thumbnails'
        for path in sorted(thumbs.glob('*.jpg')) if thumbs.exists() else []:
            for scheme in (b'https:',b'http:',b''):
                old=scheme+f'//static.gigapan.com/gigapans0/{self.gid}/images/{path.name}'.encode()
                kml=kml.replace(old,f'{base}thumbnails/{path.name}'.encode())
        return kml

    def preview(self):
        good={(r['level'],r['x'],r['y']):r for r in self.rows if r['status']!='missing'}
        for z in range(self.geometry.levels-1,-1,-1):
            scale=2**(self.geometry.levels-1-z)
            w=math.ceil(self.geometry.width/scale);h=math.ceil(self.geometry.height/scale)
            nx,ny=self.geometry.grid(z)
            if max(w,h)>4096 or not all((z,x,y) in good for y in range(ny) for x in range(nx)):continue
            canvas=Image.new('RGB',(w,h))
            for y in range(ny):
                for x in range(nx):
                    with Image.open(self.site/good[z,x,y]['relative_path']) as tile:canvas.paste(tile,(x*256,y*256))
            canvas.save(self.site/'preview.jpg',quality=90);break
