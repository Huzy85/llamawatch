"""Shared probes for the browser tests."""
from pathlib import Path

_PROBE = r"""
() => {
  const vis = el => { const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && +s.opacity > 0 && r.width > 0 && r.height > 0; };
  const inPort = el => { const vp = document.getElementById('view-port').getBoundingClientRect();
    const r = el.getBoundingClientRect(); return !el.closest('#view-port') || (r.left >= vp.left - 1 && r.right <= vp.right + 1); };
  const name = el => el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + [...el.classList].join('.');
  const texts = [...document.querySelectorAll('body *')].filter(el =>
    [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && vis(el) && inPort(el)
    && !el.closest('.swm-window, .lw-settings-overlay, .xterm, [aria-hidden="true"]'));
  let min = [99, ''];
  for (const el of texts) { const f = parseFloat(getComputedStyle(el).fontSize); if (f < min[0]) min = [f, name(el)]; }
  const clipped = texts.filter(el => { const s = getComputedStyle(el);
    if (!['hidden', 'clip'].includes(s.overflowX)) return false;
    if (s.textOverflow === 'ellipsis' && el.title) return false;
    return el.scrollWidth > el.clientWidth + 1; }).map(name);
  const overlaps = [];
  for (const el of texts) { const sibs = [...el.parentElement.children].filter(s => s !== el && texts.includes(s));
    const a = el.getBoundingClientRect();
    for (const s of sibs) { const b = s.getBoundingClientRect();
      const ix = Math.min(a.right, b.right) - Math.max(a.left, b.left), iy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (ix > 2 && iy > 2) overlaps.push(name(el) + ' x ' + name(s)); } }
  const W = innerWidth, H = innerHeight;
  const offscreen = [...document.querySelectorAll('#studio [data-panel], #rail')].filter(vis).filter(inPort)
    .filter(el => { const r = el.getBoundingClientRect();
      // Below the fold is fine inside a view that scrolls vertically (the phone layout).
      const v = el.closest('.view'); const scrolls = v && v.scrollHeight > v.clientHeight;
      return r.left < -1 || r.top < -1 || r.right > W + 1 || (r.bottom > H + 1 && !scrolls); }).map(name);
  const g = document.querySelector('#g-cpu');
  return { dial: g ? g.getBoundingClientRect().width : 0, min_font: min,
           hscroll: document.documentElement.scrollWidth > innerWidth,
           clipped: [...new Set(clipped)], overlaps: [...new Set(overlaps)], offscreen };
}
"""


def probe(page) -> dict:
    return page.evaluate(_PROBE)


def diff_ratio(a: Path, b: Path, out: Path) -> float:
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return 1.0
    d = ImageChops.difference(ia, ib)
    mask = d.convert("L").point(lambda v: 255 if v > 16 else 0)
    changed = sum(1 for v in mask.getdata() if v)
    if changed:
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.composite(Image.new("RGB", ia.size, (255, 0, 80)), ia.point(lambda v: v // 3), mask).save(out)
    return changed / (ia.size[0] * ia.size[1])
