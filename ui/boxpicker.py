"""The facecam / gameplay box picker: a frame of the video with two boxes you drag, resize or
draw. Each box is locked to the shape of the space it will fill in the 9:16 clip (set by the
"facecam size" slider), so what you frame is exactly what you get - nothing stretched.

The component's value is a JSON string:
  {"img": url, "w": frame width, "h": frame height, "split": 0.25-0.55,
   "cam": [x, y, w, h], "game": [x, y, w, h], "active": "cam" | "game"}   (boxes 0-1 of the frame)
"""
import json

import gradio as gr

HTML = '<div class="bp"></div>'

CSS = """
.bp { user-select: none; -webkit-user-select: none; }
.bp-empty { border: 1.5px dashed var(--border-color-primary); border-radius: 10px; padding: 34px 16px;
  text-align: center; color: var(--body-text-color-subdued); font-size: 14px; }
.bp-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 10px; }
.bp-chip { border: 1px solid var(--border-color-primary); background: var(--background-fill-primary);
  color: var(--body-text-color); border-radius: 999px; padding: 6px 14px; font-size: 13px; font-weight: 600;
  cursor: pointer; }
.bp-chip[data-k=cam].on { background: #e8f1fd; border-color: #2383e2; color: #0b5cad; }
.bp-chip[data-k=game].on { background: #e9f8ef; border-color: #18a058; color: #0e6b3a; }
.bp-split { margin-left: auto; display: flex; align-items: center; gap: 8px; font-size: 13px;
  color: var(--body-text-color-subdued); }
.bp-split input { width: 130px; accent-color: #2383e2; }
.bp-stage { position: relative; width: 100%; touch-action: none; cursor: crosshair; border-radius: 8px;
  overflow: hidden; background: #000; line-height: 0; }
.bp-stage img { width: 100%; display: block; pointer-events: none; opacity: .92; }
.bp-box { position: absolute; border: 2.5px solid; box-sizing: border-box; cursor: move; border-radius: 3px; }
.bp-box.cam { border-color: #2383e2; background: rgba(35,131,226,.14); }
.bp-box.game { border-color: #18a058; background: rgba(24,160,88,.12); }
.bp-box.on { box-shadow: 0 0 0 9999px rgba(0,0,0,.28); z-index: 2; }
.bp-box span { position: absolute; left: 0; top: 0; transform: translateY(-100%); padding: 3px 8px;
  font: 700 11px/1.3 system-ui, sans-serif; color: #fff; border-radius: 4px 4px 0 0; white-space: nowrap; }
.bp-box.cam span { background: #2383e2; } .bp-box.game span { background: #18a058; }
.bp-box.cam span.below, .bp-box.game span.below { top: auto; bottom: 0; transform: translateY(100%); border-radius: 0 0 4px 4px; }
.bp-box i { position: absolute; right: 3px; bottom: 3px; width: 20px; height: 20px; border-radius: 50%;
  background: #fff; border: 3px solid currentColor; cursor: nwse-resize; box-sizing: border-box;
  box-shadow: 0 1px 4px rgba(0,0,0,.35); }
.bp-box.cam i { color: #2383e2; } .bp-box.game i { color: #18a058; }
.bp-hint { margin-top: 8px; font-size: 12.5px; color: var(--body-text-color-subdued); line-height: 1.5; }
.bp-hint b.warn { color: #c4554d; }
"""

JS = r"""
const root = element.querySelector('.bp');
const OW = 1080, OH = 1920;
let st = {};
let drag = null;

const parse = () => { try { return JSON.parse(props.value || '{}') || {}; } catch (e) { return {}; } };
const heights = s => { s = Math.min(0.55, Math.max(0.25, s || 0.4)); const t = Math.round(OH * s / 2) * 2; return [t, OH - t]; };
const aspect = k => { const [t, b] = heights(st.split); return k === 'cam' ? OW / t : OW / b; };   // width / height in pixels
const hOf = (k, w) => w * st.w / (aspect(k) * st.h);                                               // normalised height for a width
const wOf = (k, h) => h * aspect(k) * st.h / st.w;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

function fit(k, b) {           // keep the aspect, the centre if possible, and stay inside the frame
  let [x, y, w] = b;
  w = Math.min(w, 1, wOf(k, 1));
  w = Math.max(w, 0.03);
  const h = hOf(k, w);
  return [clamp(x, 0, 1 - w), clamp(y, 0, 1 - h), w, h];
}

function commit() {
  props.value = JSON.stringify(st);
  trigger('change');
}

function overlap() {
  const a = st.cam, b = st.game;
  if (!a || !b) return 0;
  const ix = Math.max(0, Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]));
  const iy = Math.max(0, Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]));
  return ix * iy / Math.min(a[2] * a[3], b[2] * b[3]);
}

function paint() {
  for (const k of ['cam', 'game']) {
    const el = root.querySelector('.bp-box.' + k), b = st[k];
    if (!el || !b) continue;
    el.style.left = (b[0] * 100) + '%'; el.style.top = (b[1] * 100) + '%';
    el.style.width = (b[2] * 100) + '%'; el.style.height = (b[3] * 100) + '%';
    el.classList.toggle('on', st.active === k);
    el.querySelector('span').classList.toggle('below', b[1] < 0.07);
  }
  root.querySelectorAll('.bp-chip').forEach(c => c.classList.toggle('on', c.dataset.k === st.active));
  const sv = root.querySelector('.bp-split span');
  if (sv) sv.textContent = Math.round((st.split || 0.4) * 100) + '%';
  const hint = root.querySelector('.bp-hint');
  if (hint) hint.innerHTML = 'Pick a box above, then drag it to move, drag its round corner to resize, or draw a new one on the frame. ' +
    (overlap() > 0.15 ? '<b class="warn">The boxes overlap: part of the facecam will also show in the gameplay.</b>' : '');
}

function pt(e) {
  const r = root.querySelector('.bp-stage').getBoundingClientRect();
  return [clamp((e.clientX - r.left) / r.width, 0, 1), clamp((e.clientY - r.top) / r.height, 0, 1)];
}

function build() {
  st = parse();
  if (!st.img) {
    root.innerHTML = '<div class="bp-empty">Add your video, then press <b>Load frame</b> to draw the facecam and gameplay boxes.</div>';
    return;
  }
  st.active = st.active || 'cam';
  st.split = st.split || 0.4;
  root.innerHTML =
    '<div class="bp-bar">' +
      '<button type="button" class="bp-chip" data-k="cam">🎥 Facecam box</button>' +
      '<button type="button" class="bp-chip" data-k="game">🎮 Gameplay box</button>' +
      '<label class="bp-split">Facecam size <input type="range" min="25" max="55" step="1"><span></span></label>' +
    '</div>' +
    '<div class="bp-stage"><img alt="frame" draggable="false">' +
      '<div class="bp-box game"><span>GAMEPLAY</span><i></i></div>' +
      '<div class="bp-box cam"><span>FACECAM</span><i></i></div>' +
    '</div><div class="bp-hint"></div>';
  root.querySelector('img').src = st.img;
  const range = root.querySelector('.bp-split input');
  range.value = Math.round(st.split * 100);
  range.addEventListener('input', () => {
    st.split = range.value / 100;
    for (const k of ['cam', 'game']) if (st[k]) {          // keep centre and width, new shape
      const [x, y, w, h] = st[k], cx = x + w / 2, cy = y + h / 2;
      const nh = hOf(k, w);
      st[k] = fit(k, [cx - w / 2, cy - nh / 2, w]);
    }
    paint();
  });
  range.addEventListener('change', commit);
  root.querySelectorAll('.bp-chip').forEach(c => c.addEventListener('click', () => { st.active = c.dataset.k; paint(); commit(); }));
  const stage = root.querySelector('.bp-stage');
  stage.addEventListener('pointerdown', e => {
    e.preventDefault();
    const p = pt(e), box = e.target.closest('.bp-box');
    if (box) {
      const k = box.classList.contains('cam') ? 'cam' : 'game';
      st.active = k;
      drag = { k, mode: e.target.tagName === 'I' ? 'size' : 'move', p0: p, b0: st[k].slice() };
    } else {
      drag = { k: st.active, mode: 'draw', p0: p, b0: null };
    }
    stage.setPointerCapture(e.pointerId);
    paint();
  });
  stage.addEventListener('pointermove', e => {
    if (!drag) return;
    const p = pt(e), k = drag.k, [x0, y0] = drag.p0;
    if (drag.mode === 'move') {
      const b = drag.b0;
      st[k] = fit(k, [b[0] + p[0] - x0, b[1] + p[1] - y0, b[2]]);
    } else if (drag.mode === 'size') {
      const b = drag.b0;
      let w = Math.max(0.03, p[0] - b[0], wOf(k, Math.max(0, p[1] - b[1])));
      w = Math.min(w, 1 - b[0], wOf(k, 1 - b[1]));
      st[k] = [b[0], b[1], w, hOf(k, w)];
    } else {
      let w = Math.max(Math.abs(p[0] - x0), wOf(k, Math.abs(p[1] - y0)), 0.03);
      let x = p[0] < x0 ? x0 - w : x0, h = hOf(k, w), y = p[1] < y0 ? y0 - h : y0;
      st[k] = fit(k, [x, y, w]);
    }
    paint();
  });
  const end = () => { if (drag) { drag = null; commit(); } };
  stage.addEventListener('pointerup', end);
  stage.addEventListener('pointercancel', end);
  paint();
}

build();
watch('value', () => { const v = parse(); if (v.img !== st.img || !drag) build(); });
"""


def picker(**kw):
    return gr.HTML(value="", html_template=HTML, css_template=CSS, js_on_load=JS, **kw)


def value(img_url, width, height, boxes, active="cam"):
    return json.dumps({"img": img_url, "w": width, "h": height, "split": boxes.get("split", 0.4),
                       "cam": boxes.get("cam"), "game": boxes.get("game"), "active": active})


def read(raw):
    """Picker value -> (frame info, boxes) or (None, None)."""
    try:
        v = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return None, None
    if not v.get("img") or not v.get("cam") or not v.get("game"):
        return None, None
    return v, {"split": float(v.get("split") or 0.4), "cam": v["cam"], "game": v["game"]}
