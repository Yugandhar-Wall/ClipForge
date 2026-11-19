"""Look of the app: a Notion-like theme and the page CSS."""
import gradio as gr

_SANS = ["ui-sans-serif", "-apple-system", "Segoe UI Variable Display", "Segoe UI", "Helvetica", "Arial", "sans-serif"]
THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.blue, secondary_hue=gr.themes.colors.stone,
    neutral_hue=gr.themes.colors.stone, radius_size=gr.themes.sizes.radius_sm,
    text_size=gr.themes.sizes.text_md, spacing_size=gr.themes.sizes.spacing_md,
    font=_SANS, font_mono=["ui-monospace", "SFMono-Regular", "Consolas", "Menlo", "monospace"],
).set(
    body_background_fill="#ffffff", body_background_fill_dark="#191919",
    body_text_color="#37352f", body_text_color_dark="#e3e2e0",
    body_text_color_subdued="#787774", body_text_color_subdued_dark="#9b9a97",
    background_fill_primary="#ffffff", background_fill_primary_dark="#191919",
    background_fill_secondary="#f7f7f5", background_fill_secondary_dark="#202020",
    block_background_fill="#ffffff", block_background_fill_dark="#191919",
    block_border_color="#e9e9e7", block_border_color_dark="#2f2f2f", block_border_width="1px",
    block_shadow="none", block_shadow_dark="none",
    block_label_background_fill="transparent", block_label_background_fill_dark="transparent",
    block_label_text_color="#787774", block_label_text_color_dark="#9b9a97",
    block_label_text_weight="500", block_title_text_weight="500",
    block_title_text_color="#5f5e5b", block_title_text_color_dark="#b4b3b0",
    input_background_fill="#fbfbfa", input_background_fill_dark="#202020",
    input_border_color="#e3e2e0", input_border_color_dark="#373737",
    input_border_color_focus="#2383e2", input_border_color_focus_dark="#2383e2",
    input_shadow="none", input_shadow_focus="0 0 0 3px rgb(35 131 226 / 0.18)",
    border_color_primary="#e9e9e7", border_color_primary_dark="#2f2f2f",
    button_primary_background_fill="#2383e2", button_primary_background_fill_dark="#2383e2",
    button_primary_background_fill_hover="#0f75d6", button_primary_background_fill_hover_dark="#3a93ea",
    button_primary_text_color="#ffffff", button_primary_text_color_dark="#ffffff",
    button_primary_border_color="#2383e2", button_primary_border_color_dark="#2383e2",
    button_secondary_background_fill="#ffffff", button_secondary_background_fill_dark="#252525",
    button_secondary_background_fill_hover="#f1f1ef", button_secondary_background_fill_hover_dark="#2f2f2f",
    button_secondary_text_color="#37352f", button_secondary_text_color_dark="#e3e2e0",
    button_secondary_border_color="#dfdfde", button_secondary_border_color_dark="#373737",
    checkbox_background_color_selected="#2383e2", checkbox_background_color_selected_dark="#2383e2",
    checkbox_border_color_selected="#2383e2", checkbox_border_color_selected_dark="#2383e2",
    slider_color="#2383e2", slider_color_dark="#2383e2",
    color_accent_soft="#f1f1ef", color_accent_soft_dark="#2f2f2f",
    input_border_width="1px", button_border_width="1px", input_radius="6px",
    button_large_radius="6px", button_small_radius="6px",
)

CSS = """
.gradio-container { max-width: 100% !important; padding: 0 !important; margin: 0 !important; }
.gradio-container > .main, .gradio-container .main.fillable { max-width: 100% !important; margin: 0 !important; padding: 0 !important; }
.gradio-container .wrap, .gradio-container .contain { padding: 0 !important; gap: 0 !important; }
#cf-shell .html-container { padding: 0 !important; }
footer, .reset-button { display: none !important; }

/* ---- shell: sidebar + page */
#cf-shell { gap: 0 !important; flex-wrap: nowrap !important; align-items: stretch !important; min-height: 100vh; }
#cf-side { flex: 0 0 244px !important; min-width: 244px !important; max-width: 244px; background: #f7f7f5;
  border-right: 1px solid #e9e9e7; padding: 14px 8px 12px !important; gap: 2px !important;
  position: sticky; top: 0; height: 100vh; align-self: flex-start; overflow-y: auto; }
.dark #cf-side { background: #202020; border-color: #2f2f2f; }
#cf-brand { padding: 4px 8px 12px !important; border: none !important; background: transparent !important; }
#cf-brand .name { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
#cf-brand .mark { width: 22px; height: 22px; border-radius: 5px; background: #37352f; color: #fff;
  display: inline-flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; }
.dark #cf-brand .mark { background: #e3e2e0; color: #191919; }
#cf-brand .sub { font-size: 12px; color: #91918e; margin: 2px 0 0 30px; }
button.cf-nav { justify-content: flex-start !important; text-align: left !important; background: transparent !important;
  border: none !important; box-shadow: none !important; color: #5f5e5b !important; font-weight: 500 !important;
  font-size: 14px !important; padding: 6px 10px !important; min-height: 32px !important; height: 32px;
  border-radius: 6px !important; width: 100%; gap: 8px; flex: none !important; }
button.cf-nav:hover { background: rgb(55 53 47 / 0.06) !important; }
button.cf-nav.active { background: rgb(55 53 47 / 0.09) !important; color: #37352f !important; font-weight: 600 !important; }
.dark button.cf-nav { color: #9b9a97 !important; }
.dark button.cf-nav:hover { background: rgb(255 255 255 / 0.055) !important; }
.dark button.cf-nav.active { background: rgb(255 255 255 / 0.09) !important; color: #e3e2e0 !important; }
#cf-side-gap { flex: 1 1 auto !important; min-height: 12px; border: none !important; background: transparent !important; padding: 0 !important; }
#cf-reset-note { padding: 0 10px !important; border: none !important; background: transparent !important; min-height: 0 !important; }
#cf-reset-note p { font-size: 12px !important; color: #91918e; margin: 4px 0 0; }
#cf-main { flex: 1 1 auto !important; min-width: 0 !important; padding: 0 !important; }
#cf-pages { max-width: 920px; margin: 0 auto !important; padding: 34px 44px 90px !important; border: none !important;
  background: transparent !important; gap: 0 !important; }
#cf-pages > .tab-wrapper, #cf-pages > .tab-nav, #cf-pages > div > .tab-container { display: none !important; }
#cf-pages .tabitem { padding: 0 !important; border: none !important; background: transparent !important; }
#cf-pages .tabitem > .column, #cf-pages .tabitem > div > .column { gap: 14px !important; }

/* ---- page head */
.cf-head { padding: 0 !important; border: none !important; background: transparent !important; margin-bottom: 6px; }
.cf-head .emoji { font-size: 40px; line-height: 1.1; }
.cf-head h1 { font-size: 32px; font-weight: 700; letter-spacing: -0.01em; margin: 8px 0 4px; color: inherit; }
.cf-head p { color: #787774; font-size: 15px; margin: 0; }

/* ---- sections */
.cf-card { border: 1px solid #e9e9e7 !important; border-radius: 8px !important; background: var(--block-background-fill) !important;
  padding: 18px 20px !important; gap: 14px !important; }
.dark .cf-card { border-color: #2f2f2f !important; }
.cf-card .form { border: none !important; background: transparent !important; box-shadow: none !important;
  gap: 14px !important; overflow: visible !important; }
.cf-card .block.padded { padding: 0 !important; background: transparent !important; box-shadow: none !important; }
.cf-card .block { box-shadow: none !important; }
.cf-card .row { gap: 14px !important; }
.cf-title { font-size: 15px; font-weight: 600; margin: 0 0 2px; }
.cf-sub { font-size: 13px; color: #787774; margin: 0 0 4px; }
.cf-note p { font-size: 13px !important; color: #787774; }
.cf-note code { font-size: 12px; }
.cf-actions { gap: 10px !important; margin: 4px 0; justify-content: flex-start !important; flex-wrap: wrap !important; }
.cf-actions button { height: 38px; font-weight: 600; flex: 0 0 auto !important; min-width: 148px !important;
  width: auto !important; padding: 0 18px !important; }
#cf-go { min-width: 200px !important; height: 42px; font-size: 15px; }
.cf-card button.sm, .cf-card button.secondary { min-height: 32px; }
.cf-card .row > button.sm { flex: 0 0 auto !important; min-width: 160px !important; width: auto !important; }
#cf-log textarea, #cf-ytlog textarea, #cf-livelog textarea { font-family: var(--font-mono); font-size: 12.5px; line-height: 1.5; }
#cf-live-status { padding: 12px 14px !important; border-radius: 6px !important; background: #f7f7f5 !important; border: none !important; }
.dark #cf-live-status { background: #202020 !important; }
#cf-live-status p { margin: 0 0 2px; font-size: 14px; }

/* ---- desktop: the sidebar is pinned to the window and never scrolls away */
@media (min-width: 821px) {
  #cf-side { position: fixed !important; top: 0; left: 0; bottom: 0; height: 100vh !important; z-index: 20; }
  #cf-main { margin-left: 244px !important; }
}

/* ---- phone: the sidebar becomes a top bar you can swipe */
@media (max-width: 820px) {
  #cf-shell { flex-direction: column !important; }
  #cf-main { padding-top: 50px !important; }
  #cf-side { position: fixed !important; top: 0; left: 0; right: 0; z-index: 30; height: auto; flex: none !important; min-width: 0 !important;
    max-width: none; width: 100% !important; flex-direction: row !important; flex-wrap: nowrap !important;
    overflow-x: auto; overflow-y: hidden; border-right: none; border-bottom: 1px solid #e9e9e7;
    padding: 8px 10px !important; gap: 4px !important; align-items: center; scrollbar-width: none; }
  #cf-side::-webkit-scrollbar { display: none; }
  #cf-brand, #cf-side-gap, #cf-reset-note { display: none !important; }
  button.cf-nav { width: auto !important; flex: 0 0 auto !important; white-space: nowrap; padding: 6px 10px !important; }
  #cf-pages { padding: 18px 14px 70px !important; }
  .cf-head .emoji { font-size: 30px; }
  .cf-head h1 { font-size: 24px; margin-top: 4px; }
  .cf-card { padding: 14px !important; }
  #cf-preview { height: auto !important; max-height: 70vh; }
}

/* ---- Clip page: Video | Live switch */
#cf-mode { padding: 0 !important; border: none !important; background: transparent !important; }
#cf-mode .wrap { display: inline-flex !important; gap: 4px !important; background: #f1f1ef; border-radius: 10px;
  padding: 4px !important; flex-wrap: nowrap !important; }
.dark #cf-mode .wrap { background: #2a2a2a; }
#cf-mode label { border: none !important; background: transparent !important; border-radius: 8px !important;
  padding: 8px 20px !important; font-weight: 600 !important; font-size: 14px !important; cursor: pointer;
  box-shadow: none !important; color: #787774 !important; min-height: 0 !important; }
#cf-mode label.selected, #cf-mode label:has(input:checked) { background: #fff !important; color: #37352f !important;
  box-shadow: 0 1px 3px rgba(0,0,0,.12) !important; }
.dark #cf-mode label.selected, .dark #cf-mode label:has(input:checked) { background: #3a3a3a !important; color: #fff !important; }
#cf-mode input[type=radio] { display: none !important; }
#cf-mode > span, #cf-mode .block-title, #cf-mode [data-testid=block-info] { display: none !important; }

/* ---- numbered steps */
.cf-title .cf-step { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px;
  border-radius: 50%; background: #37352f; color: #fff; font-size: 12px; font-weight: 700; margin-right: 8px;
  vertical-align: 1px; }
.dark .cf-title .cf-step { background: #e3e2e0; color: #191919; }

/* ---- layout choice cards */
#cf-layout .wrap { display: grid !important; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px !important; }
#cf-layout label { display: flex !important; align-items: center; justify-content: center; text-align: center;
  border: 1.5px solid #e3e2e0 !important; border-radius: 10px !important; padding: 12px 8px !important;
  background: var(--block-background-fill) !important; font-weight: 600 !important; font-size: 13.5px !important;
  min-height: 52px; cursor: pointer; box-shadow: none !important; }
#cf-layout label.selected, #cf-layout label:has(input:checked) { border-color: #2383e2 !important; background: #f2f8fe !important;
  color: #0b5cad !important; }
.dark #cf-layout label { border-color: #3a3a3a !important; }
.dark #cf-layout label.selected, .dark #cf-layout label:has(input:checked) { background: #16283b !important; color: #8cc2ff !important; }
#cf-layout input[type=radio] { display: none !important; }
#cf-frame-row { align-items: flex-start !important; }
#cf-layout-preview { border: none !important; background: transparent !important; }
#cf-layout-preview img { border-radius: 10px; box-shadow: 0 2px 14px rgba(0,0,0,.18); }
.cf-small button, button.cf-small { min-width: 0 !important; }

/* ---- accordions (Style, Campaign) */
.cf-acc { border: 1px solid #e9e9e7 !important; border-radius: 8px !important; background: var(--block-background-fill) !important; }
.dark .cf-acc { border-color: #2f2f2f !important; }
.cf-acc > button, .cf-acc .label-wrap { padding: 14px 20px !important; font-weight: 600 !important; font-size: 15px !important; }
.cf-acc .form { border: none !important; background: transparent !important; gap: 14px !important; }

/* ---- action bar: always reachable */
#cf-actionbar { position: sticky; bottom: 0; z-index: 15; background: var(--body-background-fill) !important;
  border: none !important; border-top: 1px solid #e9e9e7 !important; padding: 12px 0 14px !important; margin: 0 !important;
  gap: 10px !important; align-items: center !important; flex-wrap: wrap !important; }
.dark #cf-actionbar { border-color: #2f2f2f !important; }
#cf-actionbar button { height: 44px; font-weight: 600; flex: 0 0 auto !important; width: auto !important;
  min-width: 120px !important; padding: 0 20px !important; }
#cf-start { min-width: 190px !important; font-size: 15px !important; }
#cf-stop:not([disabled]) { background: #c4554d !important; border-color: #c4554d !important; color: #fff !important; }
#cf-status { flex: 1 1 260px !important; border: none !important; background: transparent !important; padding: 0 4px !important; min-width: 200px; }
#cf-status p { margin: 0 !important; font-size: 14px; }
@media (max-width: 820px) {
  #cf-layout .wrap { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  #cf-actionbar button { min-width: 0 !important; flex: 1 1 auto !important; }
}
"""
