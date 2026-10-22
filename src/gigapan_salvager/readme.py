"""Explain a salvaged panorama in the repository README, using only saved metadata."""
import json
import re
from datetime import datetime

METADATA_FILES = [
    (r'gigapan\.json', 'パノラマ本体の情報（原本）', '`gigapan.com/gigapans/{id}.json`'),
    (r'normalized\.json', '上記の正規化版（文字化けの置換・寸法の補完）', '加工'),
    (r'source\.kml', 'Google Earth 用 PhotoOverlay の原本', '`gigapan.com/gigapans/{id}.kml` または `--kml`'),
    (r'page\.html', 'GigaPan ページの HTML（書き換えなし）', '`gigapan.com/gigapans/{id}`'),
    (r'page_config\.json', 'ビューア設定（寸法・タイル配信元・関連パノラマ）', '`page.html` から抽出'),
    (r'snapshots_page_\d+\.json', 'スナップショット一覧（原本）', '`.../snapshots.json?page=N&per_page=100`'),
    (r'snapshots_normalized\.json', 'スナップショット一覧の正規化版', '加工'),
    (r'snapshot_\d+_comments_\d+\.html', 'スナップショットのコメント（HTML）', '`gigapan.com/gigapans/show_snapshot_comments/ID`'),
    (r'panorama_comments_\d+\.html', 'パノラマのコメント（HTML）', '`gigapan.com/gigapans/{id}/comments`'),
    (r'retrieval_log\.json', '取得日時・URL・ETag・エラーの記録', '—'),
]
STATUS = {'complete': '全タイル検証済み', 'partial': '一部未取得', 'probe': '通信確認のみ（全画像の保存ではありません）'}


def cell(value):
    """Make arbitrary metadata text safe inside a Markdown table cell."""
    text = re.sub(r'\s+', ' ', str(value)).strip()
    return text.replace('\\', '\\\\').replace('|', '\\|').replace('<', '&lt;') or '—'


def number(value, digits=0):
    return f'{value:,.{digits}f}' if isinstance(value, (int, float)) else '—'


def date(value):
    """Keep the original timestamp (with its offset) rather than guessing a local time."""
    try:
        return datetime.fromisoformat(str(value)).strftime('%Y-%m-%d %H:%M (UTC%z)').replace('(UTC+0000)', '(UTC)')
    except ValueError:
        return cell(value) if value else '—'


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def license_note(description):
    match = re.search(r'(?im)^\s*licen[cs]e\s*[:：]\s*(.+)$', description or '')
    return match[1].strip() if match else None


def table(rows):
    rows = [(k, v) for k, v in rows if v not in (None, '', '—')]
    return '| 項目 | 内容 |\n| --- | --- |\n' + ''.join(f'| {k} | {v} |\n' for k, v in rows)


def repository_readme(job):
    from . import __version__
    m, g, r, site, gid = job.metadata, job.geometry, job.report, job.site, job.gid
    base = job.pages_base
    name = re.sub(r'\s+', ' ', str(m.get('name') or f'GigaPan {gid}')).strip()
    description = str(m.get('description') or '').replace('\r\n', '\n').strip()
    page = read_json(job.meta / 'page_config.json', {})
    page = page.get('gigapan', page) if isinstance(page, dict) else {}
    snapshots = read_json(job.meta / 'snapshots_normalized.json', [])
    kml = f'gigapan_{gid}.kml'
    out = [f'# {cell(name)}\n\n',
           f'GigaPan.com で公開されていたギガピクセルパノラマ（GigaPan ID: {gid}）のアーカイブです。'
           '公開 API・ページ・KML・タイル画像を [GigaPan Salvager](https://github.com/mapconcierge/GigaPanSalvager) で取得・検証し、'
           '`docs/` を GitHub Pages として公開できる形で保存しています。\n\n']
    if (site / 'preview.jpg').exists():
        out.append('![プレビュー](docs/preview.jpg)\n\n')
    out.append(f'- 元ページ: <https://gigapan.com/gigapans/{gid}>\n'
               f'- ビューア: <{base}>（このリポジトリ内: [`docs/index.html`](docs/index.html)）\n')
    if (site / kml).exists():
        out.append(f'- Google Earth 用 KML: <{base}{kml}>\n')
    out.append('\n## パノラマの概要\n\n')
    options = m.get('options') or {}
    out.append(table([
        ('タイトル', cell(name)),
        ('ライセンス表記', cell(license_note(description)) + '（説明文の記載）' if license_note(description) else None),
        ('撮影日時', date(m['taken_at']) if m.get('taken_at') else None),
        ('GigaPan 登録日時', date(m['created_at']) if m.get('created_at') else None),
        ('最終更新', date(m['updated_at']) if m.get('updated_at') else None),
        ('公開状態', {False: '公開（`is_private: false`）', True: '非公開'}.get(m.get('is_private'))),
        ('閲覧数', number(m.get('views')) + ' 回（取得時点）' if isinstance(m.get('views'), int) else None),
        ('Explore score', number(m.get('explore_score'), 1) if m.get('explore_score') is not None else None),
        ('スナップショット', f'{len(snapshots)} 件（[下記](#スナップショット)）' if snapshots else None),
        ('コメント', number(options.get('comment_count')) + ' 件' if options.get('comment_count') else None),
    ]))
    if description:
        out.append('\n### 説明（原文）\n\n' + ''.join('> ' + line.rstrip() + '\n' if line.strip() else '>\n'
                                                   for line in description.split('\n')))
    fov = []
    if m.get('field_of_view_w') is not None and m.get('field_of_view_h') is not None:
        w, h = float(m['field_of_view_w']), float(m['field_of_view_h'])
        bottom = float(m.get('field_of_view_b', -h / 2))
        fov = [('画角', f'水平 {w:g}°、垂直 {h:g}°（上 {bottom + h:+.1f}° / 下 {bottom:+.1f}°）')]
    out.append('\n## 画像の仕様\n\n' + table([
        ('サイズ', f'{g.width:,} × {g.height:,} px（約 {g.width * g.height / 1e6:,.0f} メガピクセル）'),
        *fov,
        ('タイル', f'{g.tile_size} px 角 JPEG、{g.levels} レベル、総数 {g.count:,} 枚'),
        ('形状', '全周（球面）パノラマ' if fov and float(m['field_of_view_w']) >= 360 else None),
    ]))
    if m.get('latitude') is not None and m.get('longitude') is not None:
        lat, lon = float(m['latitude']), float(m['longitude'])
        out.append('\n## 撮影位置\n\n座標系は WGS84（EPSG:4326）です。\n\n' + table([
            ('緯度・経度', f'{lat:.6f}, {lon:.6f}'),
            ('高度', f"{float(m['altitude']):g} m" if m.get('altitude') is not None else None),
            ('向き（heading）', f"{float(m['heading']):g}°" if m.get('heading') is not None else None),
            ('傾き（tilt）', f"{float(m['tilt']):g}°" if m.get('tilt') is not None else None),
            ('地図', f'[OpenStreetMap](https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=16/{lat:.6f}/{lon:.6f})'),
        ]))
    if m.get('stitcher_notes'):
        notes = str(m['stitcher_notes']).replace('```', "'''").rstrip()
        out.append('\n## 撮影・合成情報\n\nGigaPan Stitch が記録した合成時の情報（`stitcher_notes` 原文）です。\n\n```text\n' + notes + '\n```\n')
    if snapshots:
        out.append('\n## スナップショット\n\nパノラマ内の注目箇所です。範囲は全体画像上のピクセル座標です。\n\n'
                   '| 名前 | 説明 | 作成日時 | 閲覧数 | 範囲 (x / y) |\n| --- | --- | --- | --- | --- |\n')
        default = m.get('default_snapshot_id')
        for s in snapshots:
            area = '—'
            if all(isinstance(s.get(k), (int, float)) for k in ('xmin', 'xmax', 'ymin', 'ymax')):
                area = f"x {s['xmin']:,.0f}〜{s['xmax']:,.0f} / y {s['ymin']:,.0f}〜{s['ymax']:,.0f}"
            label = cell(s.get('name', s.get('id'))) + ('（デフォルト）' if s.get('id') == default else '')
            out.append(f"| {label} | {cell(s.get('description', ''))} | {date(s.get('created_at'))} | {number(s.get('views'))} | {area} |\n")
    related = [x for x in page.get('related_gigapans') or [] if isinstance(x, dict) and isinstance(x.get('id'), int)]
    if related:
        out.append(f'\n## 関連パノラマ\n\nGigaPan ページに表示されていた関連作品（[`page_config.json`](docs/metadata/page_config.json) に {len(related)} 件）です。\n\n')
        out += [f"- [{cell(x.get('name') or x['id'])}（{x['id']}）](https://gigapan.com/gigapans/{x['id']})\n" for x in related]
    seconds = None
    try:
        seconds = (datetime.fromisoformat(r['finished_at']) - datetime.fromisoformat(r['started_at'])).total_seconds()
    except (KeyError, ValueError):
        pass
    out.append(f"\n## アーカイブの取得結果\n\n{str(r.get('finished_at', ''))[:10]} に取得・検証した結果です（[`job-result.json`](job-result.json)、[`docs/audit.json`](docs/audit.json)）。\n\n" + table([
        ('ステータス', f"{r['status']} — {STATUS.get(r['status'], r['status'])}（終了コード {job.result['exit_code']}）"),
        ('タイル', f"期待 {r['expected_tiles']:,} 枚 / 検証済み {r['verified_tiles']:,} 枚 / 欠落 {r['missing_tiles']:,} 枚"),
        ('タイル容量', f"約 {r['tile_bytes'] / 1e6:,.1f} MB（{r['tile_bytes']:,} bytes）"),
        ('所要時間', f'約 {int(seconds // 60)} 分 {int(seconds % 60)} 秒' if seconds is not None else None),
        ('取得ツール', f'GigaPan Salvager v{__version__}'),
    ]))
    if r.get('warnings'):
        out.append('\n注意点:\n\n' + ''.join(f'- {cell(w)}\n' for w in r['warnings']))
    out.append(f'''
## GitHub Pages で公開する

`docs/` がそのまま公開サイトです。KML のタイル参照は公開 URL <{base}> を前提にした絶対パスです。

1. GitHub に `{base.rstrip('/').rsplit('/', 1)[-1]}` という名前のリポジトリを作り、このフォルダを push します。
2. Settings → Pages → Build and deployment で **Deploy from a branch**、ブランチ `main`、フォルダ **`/docs`** を選びます。
3. 数分後に <{base}> でビューア、<{base}{kml}> で Google Earth 用 KML が開けます。

`tiles/` と `ge_tiles/` は同じ画像です（Git では同一内容として1回だけ保存されます）。GitHub Pages の公開サイトは 1 GB までです。

## ディレクトリ構成

| パス | 内容 |
| --- | --- |
| [`docs/`](docs/) | 公開用ファイル一式（GitHub Pages の公開フォルダ） |
| `docs/index.html` / `docs/viewer.html` | 外部ライブラリ不要のブラウザ用ビューア（同じ内容） |
| `docs/preview.jpg` | 完全に取得できた低解像度層のプレビュー |
| `docs/tiles/` | 配信元の四分木構造のままのタイル JPEG |
| `docs/ge_tiles/` | Google Earth 用の `level/y/x.jpg` 構造（`tiles/` と同じ画像） |
| `docs/{kml}` | Google Earth 用 KML（タイル参照は公開 URL の絶対パス） |
| `docs/gigapan_{gid}_local.kml` | 同じ KML のローカル版（`ge_tiles/` を相対参照。展開したフォルダで使用） |
| `docs/thumbnails/` | パノラマ・スナップショットの公開サムネイル（取得できた場合） |
| `docs/tile_manifest.csv` | 全タイルの座標・取得 URL・バイト数・SHA-256・検証結果 |
| `docs/audit.json` / `docs/missing_tiles.json` / `docs/plan.json` | 検証結果・欠落タイル・画像寸法 |
| `docs/metadata/` | 取得した元メタデータ（下表） |
| `job-result.json` | 取得ジョブの結果 |

### `docs/metadata/` の内容

| ファイル | 内容 | 取得元 |
| --- | --- | --- |
''')
    for path in sorted(job.meta.iterdir()) if job.meta.exists() else []:
        if not path.is_file():
            continue
        for pattern, about, source in METADATA_FILES:
            if re.fullmatch(pattern, path.name):
                out.append(f'| [`{path.name}`](docs/metadata/{path.name}) | {about} | {source.format(id=gid)} |\n')
                break
    out.append(f'''
## 権利・出典

- 元データ: [GigaPan {gid}](https://gigapan.com/gigapans/{gid})
- 写真・説明・コメントの権利は各著作者に帰属します。利用条件は上記の説明文・元ページの表記に従ってください（このアーカイブで独自のライセンスを付与するものではありません）。
- 元の撮影素材・非公開画像・アカウント限定の内容は含みません。`metadata/page.html` は原本のまま保存しているため外部サイトを参照します。

---

Generated by [GigaPan Salvager](https://github.com/mapconcierge/GigaPanSalvager) v{__version__}.
''')
    return ''.join(out)
