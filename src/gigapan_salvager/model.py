"""Validated IDs, geometry, and lossless KML URL replacement."""
from dataclasses import dataclass
import html
import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

NS = {'k': 'http://www.opengis.net/kml/2.2'}


def parse_id(value):
    text = str(value).strip()
    if re.fullmatch(r'[1-9][0-9]{0,8}', text):
        return int(text)
    u = urlparse(text)
    if u.scheme in ('http', 'https') and u.hostname in ('gigapan.com', 'www.gigapan.com', 'gigapan.org', 'www.gigapan.org'):
        m = re.fullmatch(r'/gigapans/([1-9][0-9]{0,8})/?', u.path)
        if m:
            return int(m[1])
    raise ValueError('GigaPan の数値 ID または https://gigapan.com/gigapans/ID を指定してください。')


def safe_xml(raw):
    if len(raw) > 256_000 or re.search(br'<!\s*(DOCTYPE|ENTITY)', raw, re.I):
        raise ValueError('KML が大きすぎるか、対応していない XML 宣言を含んでいます。')
    return ET.fromstring(raw)


def kml_metadata(raw, gid):
    tree = safe_xml(raw)
    overlay = tree.find('.//k:PhotoOverlay', NS)
    if overlay is None:
        raise ValueError('KML に PhotoOverlay がありません。')
    oid = overlay.attrib.get('id', '')
    if oid.startswith('gigapan_') and oid != f'gigapan_{gid}':
        raise ValueError('KML の ID と指定 ID が異なります。')
    def text(path, default=None):
        value = overlay.findtext(path, namespaces=NS)
        return default if value is None else value
    result = {'id': gid, 'name': text('k:name', f'GigaPan {gid}'),
              'width': int(text('k:ImagePyramid/k:maxWidth')),
              'height': int(text('k:ImagePyramid/k:maxHeight')),
              'tile_size': int(text('k:ImagePyramid/k:tileSize', '256')),
              'description': text('k:description', '')}
    for key in ('latitude', 'longitude', 'altitude', 'heading', 'tilt', 'roll'):
        value = text('k:Camera/k:' + key)
        if value is not None:
            result[key] = float(value)
    return result


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int
    levels: int
    tile_size: int = 256

    @classmethod
    def from_metadata(cls, m):
        w, h = int(m['width']), int(m['height'])
        t = int(m.get('tile_size', 256))
        if not (1 <= w <= 10_000_000 and 1 <= h <= 10_000_000 and t == 256):
            raise ValueError('画像寸法またはタイルサイズが対応範囲外です。')
        levels = max(0, math.ceil(math.log2(max(w, h) / t))) + 1
        if m.get('levels') and int(m['levels']) != levels:
            raise ValueError('公開メタデータの階層数と画像寸法が一致しません。')
        return cls(w, h, levels, t)

    def grid(self, z):
        step = self.tile_size * (1 << (self.levels - 1 - z))
        return (self.width + step - 1) // step, (self.height + step - 1) // step

    def tiles(self):
        for z in range(self.levels):
            nx, ny = self.grid(z)
            for y in range(ny):
                for x in range(nx):
                    yield z, x, y, tile_path(z, x, y)

    @property
    def count(self):
        return sum(a*b for a, b in (self.grid(z) for z in range(self.levels)))


def tile_path(z, x, y):
    if not (0 <= z <= 24 and 0 <= x < 2**z and 0 <= y < 2**z):
        raise ValueError('タイル座標が範囲外です。')
    q = ''.join(str(((x >> b) & 1) | (((y >> b) & 1) << 1)) for b in range(z-1, -1, -1))
    parts = ['r'+q[:2]] if z >= 3 else []
    parts += [q[s:s+3] for s in range(2, len(q)-3, 3)]
    return '/'.join(parts + ['r'+q+'.jpg'])


def pages_url(value):
    """Validate a GitHub Pages base URL and normalise it to end with a slash."""
    u = urlparse(str(value).strip())
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('公開 URL は https://OWNER.github.io/REPOSITORY/ の形式で指定してください。')
    return u.geturl().rstrip('/') + '/'


def offline_kml(raw, metadata, base=''):
    """Keep CDATA, Japanese text and namespaces byte-for-byte when a source exists.

    `base` is the published site root (absolute URL); empty keeps paths relative.
    """
    gid = metadata['id']
    local = (base + 'ge_tiles/$[level]/$[y]/$[x].jpg').encode('utf-8')
    if raw:
        tree = safe_xml(raw)
        href = tree.find('.//k:PhotoOverlay/k:Icon/k:href', NS)
        if href is not None and href.text:
            old = html.escape(href.text.strip(), quote=False).encode('utf-8')
            token = b'>' + old + b'</'
            if raw.count(token) == 1:
                result = raw.replace(token, b'>' + html.escape(local.decode(), quote=False).encode('utf-8') + b'</')
                check = safe_xml(result).find('.//k:PhotoOverlay/k:Icon/k:href', NS)
                if check is not None and check.text == local.decode():
                    return result
        raise ValueError('元 KML の画像参照を安全に差し替えられませんでした。原本を確認してください。')
    if not all(metadata.get(k) is not None for k in ('latitude', 'longitude', 'field_of_view_w', 'field_of_view_h')):
        return None
    esc = lambda s: html.escape(str(s))
    fov_w = float(metadata['field_of_view_w'])
    bottom = float(metadata.get('field_of_view_b', -float(metadata['field_of_view_h'])/2))
    top = bottom + float(metadata['field_of_view_h'])
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<PhotoOverlay id="gigapan_{gid}"><name>{esc(metadata.get('name', gid))}</name>
<description>{esc(metadata.get('description', ''))}</description>
<Camera><longitude>{esc(metadata['longitude'])}</longitude><latitude>{esc(metadata['latitude'])}</latitude><altitude>{esc(metadata.get('altitude',0))}</altitude><heading>{esc(metadata.get('heading',0))}</heading><tilt>90</tilt></Camera>
<Icon><href>{esc(local.decode())}</href></Icon>
<ViewVolume><leftFov>{-fov_w/2}</leftFov><rightFov>{fov_w/2}</rightFov><bottomFov>{bottom}</bottomFov><topFov>{top}</topFov><near>{esc(metadata.get('distance',15))}</near></ViewVolume>
<ImagePyramid><tileSize>256</tileSize><maxWidth>{metadata['width']}</maxWidth><maxHeight>{metadata['height']}</maxHeight><gridOrigin>upperLeft</gridOrigin></ImagePyramid>
<Point><coordinates>{metadata['longitude']},{metadata['latitude']}</coordinates></Point><shape>sphere</shape>
</PhotoOverlay></Document></kml>
'''.encode('utf-8')
