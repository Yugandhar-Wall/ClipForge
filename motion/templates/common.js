// Shared helpers. Every template reads its variables, builds the DOM, then registers a paused
// GSAP timeline on window.__timelines.root (HyperFrames seeks it frame by frame).
window.CF = {
  vars() { return window.__hyperframes.getVariables(); },
  fit(text, sizes) {           // font size from text length
    const n = (text || "").length;
    for (const [max, px] of sizes) if (n <= max) return px;
    return sizes[sizes.length - 1][1];
  },
  words(el, text, hiIndex) {   // one span per word; returns the spans
    const out = [];
    (text || "").split(/\s+/).filter(Boolean).forEach((w, i) => {
      const s = document.createElement("span");
      s.className = "w" + (i === hiIndex ? " hi" : "");
      s.textContent = w;
      el.appendChild(s);
      el.appendChild(document.createTextNode(" "));
      out.push(s);
    });
    return out;
  },
  keyword(text) {              // the word to emphasise: longest, ignoring filler
    const stop = new Set(["THE","A","AN","AND","OF","TO","IN","ON","IS","IT","HE","SHE","WE","I","YOU","THIS","THAT","WAS","FOR","WITH"]);
    let best = -1, len = 0;
    (text || "").split(/\s+/).filter(Boolean).forEach((w, i) => {
      const c = w.replace(/[^\p{L}\p{N}]/gu, "");
      if (!stop.has(c.toUpperCase()) && c.length > len) { best = i; len = c.length; }
    });
    return best;
  },
  done(tl, dur) {              // make the timeline exactly `dur` long, register it
    tl.set({}, {}, dur);
    window.__timelines = window.__timelines || {};
    window.__timelines.root = tl;
  },
};
