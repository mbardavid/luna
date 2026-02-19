#!/usr/bin/env python3
from __future__ import annotations
import os, subprocess, tempfile, json
from pathlib import Path
import cv2
import numpy as np

IN_DIR = Path('/home/openclaw/.openclaw/media/inbound')
OUT_DIR = Path('/home/openclaw/.openclaw/workspace/artifacts/azulejo')
OUT_DIR.mkdir(parents=True, exist_ok=True)


def detect_grid_lines(img_gray):
    blur = cv2.GaussianBlur(img_gray, (5,5), 0)
    edges = cv2.Canny(blur, 60, 160)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=120, minLineLength=min(img_gray.shape)//3, maxLineGap=20)
    xs, ys = [], []
    if lines is None:
        return xs, ys
    for l in lines[:,0,:]:
        x1,y1,x2,y2 = map(int,l)
        dx, dy = x2-x1, y2-y1
        if abs(dx) < 8 and abs(dy) > 40:
            xs.append((x1+x2)//2)
        elif abs(dy) < 8 and abs(dx) > 40:
            ys.append((y1+y2)//2)
    def compress(vals, tol=10):
        vals = sorted(vals)
        out=[]
        for v in vals:
            if not out or abs(v-out[-1])>tol:
                out.append(v)
        return out
    return compress(xs), compress(ys)


def central_tile_crop(img):
    h,w = img.shape[:2]
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    xs, ys = detect_grid_lines(g)
    cx, cy = w//2, h//2
    if len(xs) >= 2 and len(ys) >= 2:
        lx = max([x for x in xs if x < cx], default=w//4)
        rx = min([x for x in xs if x > cx], default=3*w//4)
        ty = max([y for y in ys if y < cy], default=h//4)
        by = min([y for y in ys if y > cy], default=3*h//4)
        if rx-lx > 40 and by-ty > 40:
            return img[ty:by, lx:rx], (lx,ty,rx,by), xs, ys
    s = min(h,w)//3
    x1,y1 = cx-s//2, cy-s//2
    return img[y1:y1+s, x1:x1+s], (x1,y1,x1+s,y1+s), xs, ys


def vectorize_multicolor(tile_bgr, out_svg: Path, n_colors=4):
    tile = cv2.cvtColor(tile_bgr, cv2.COLOR_BGR2RGB)
    H,W = tile.shape[:2]
    data = tile.reshape((-1,3)).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1.0)
    _ret, labels, centers = cv2.kmeans(data, n_colors, None, criteria, 5, cv2.KMEANS_PP_CENTERS)
    labels = labels.reshape(H,W)
    centers = centers.astype(np.uint8)

    # largest cluster is background
    counts = [(i, int((labels==i).sum())) for i in range(n_colors)]
    bg = max(counts, key=lambda x:x[1])[0]

    paths = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for i in range(n_colors):
            if i == bg:
                continue
            mask = np.where(labels==i, 255, 0).astype(np.uint8)
            mask = cv2.medianBlur(mask, 3)
            kernel = np.ones((2,2), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            pbm = td / f'c{i}.pbm'
            svg = td / f'c{i}.svg'
            cv2.imwrite(str(pbm), mask)
            subprocess.run(['potrace', str(pbm), '-s', '-o', str(svg), '--turdsize', '4', '--opttolerance', '0.3'], check=True)
            txt = svg.read_text(errors='ignore')
            start = txt.find('<path ')
            if start == -1:
                continue
            color = '#%02x%02x%02x' % tuple(int(v) for v in centers[i])
            p = txt[start:]
            p = p.replace('fill="black"', f'fill="{color}"')
            paths.append(p)

    bg_color = '#%02x%02x%02x' % tuple(int(v) for v in centers[bg])
    content = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">']
    content.append(f'<rect width="100%" height="100%" fill="{bg_color}"/>')
    content.extend(paths)
    content.append('</svg>')
    out_svg.write_text('\n'.join(content))


def main():
    report=[]
    for p in sorted(IN_DIR.glob('file_*.jpg')):
        img = cv2.imread(str(p))
        if img is None:
            continue
        tile, bbox, xs, ys = central_tile_crop(img)
        stem = p.stem
        tile_png = OUT_DIR / f'{stem}.tile.png'
        cv2.imwrite(str(tile_png), tile)
        svg = OUT_DIR / f'{stem}.tile.svg'
        try:
            vectorize_multicolor(tile, svg, n_colors=4)
            ok=True
        except Exception as e:
            ok=False
            (OUT_DIR / f'{stem}.error.txt').write_text(str(e))
        report.append({
            'file': p.name,
            'bbox': bbox,
            'grid_x_count': len(xs),
            'grid_y_count': len(ys),
            'tile_png': str(tile_png),
            'tile_svg': str(svg),
            'vector_ok': ok,
        })
    (OUT_DIR / 'report.json').write_text(json.dumps(report, indent=2))
    print(f'WROTE {OUT_DIR / "report.json"}')

if __name__ == '__main__':
    main()
