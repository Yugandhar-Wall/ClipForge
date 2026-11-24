# ClipForge

Long video in → finished 9:16 shorts out: AI-picked moments, word-by-word captions,
hook text, original sound effects, viral titles + post captions.

## Setup (once)
1. Python 3.10–3.13 installed (python.org, tick "Add to PATH").
2. Double-click **setup.bat**.
3. Get an OpenRouter key at https://openrouter.ai/keys

## Use
**run.bat** opens the app in your browser. Paste your OpenRouter key in *Settings* once.
Everything for a run is on the **Clip** page, top to bottom:
1. **Video | Live stream** switch at the top.
2. **Source**: a YouTube link or a file path (for big files paste the path, e.g.
   `D:\Videos\stream.mp4`, instead of uploading) - or, in Live mode, the stream's link.
   **From / To** (h:mm:ss) clips only part of a video; with a link only that part is downloaded.
3. **Layout** (see below). **Load frame** shows a frame of the video and a preview of a clip.
4. **Clips**: how many and how long (Live: how often to check and the minimum score).
5. **Style** and **Campaign** are folded away: formats, captions, sounds, motion graphics,
   sponsor logo, campaign rules.
6. **Start**. **Stop** ends a run at once, in both modes. **New session** clears everything from
   the last run: the link, description, creator, boxes, logo, rules, log and results.
   Run inputs are never saved, so the app also starts clean every time. Preferences (keys,
   style, clip lengths, layout type) are kept.

**batch.bat**: drag a folder of videos onto it; clips every video with your preferences
(`python cli.py --help` for the options: logo, rules, facecam boxes, ...).

## Layouts
| Layout | What a clip looks like |
|---|---|
| **Facecam + gameplay** | For streams. You draw two boxes on a frame: the streamer's camera and the game. The camera fills the top, the game the bottom, captions sit on the line between them; hook text, stickers and a sponsor logo go on the game, never on the face. The **Facecam size** slider sets how much of the height the camera gets (25-55%). Each box is locked to the shape of the space it fills, so nothing is ever stretched. Works in Live mode too (the frame is taken from the stream as it is live). |
| **Full frame** | The whole video, nothing cut off, on a blurred copy of itself. Zoom above 1.0 crops the sides. |
| **Fill screen** | Fills the vertical screen by cutting off the sides. |
| **Split with gameplay** | The video on top, a muted gameplay video (from the `gameplay` folder) underneath. |

A tiny facecam (e.g. 320x180 in a 1080p stream) is enlarged to the full width and looks soft:
ClipForge uses the best upscaler ffmpeg has, but it can't add detail that isn't there.

## How moments are picked
The AI is briefed on why people watch a clip: a hook in the first 3 seconds, real stakes, a
genuine emotional peak from the streamer, a payoff inside the clip, no context needed, and a
reason to share. Every clip is scored on those parts (hook 25, emotion 25, payoff 20,
context 15, share 15) and names its trigger ("near-miss escape", "rage after a betrayal"...),
written in the clip's .txt. Generic laughing, inside jokes, donation reading, greeting chat,
menus and dead air are rejected.
The streamer is the main signal: what they say (exclamations, swearing, current slang like
"ain't no way", "he's cooked", "chat is this real") and how (lines said much louder than their
normal voice, rapid panicked talk, stunned silences). Live chat is only a weak hint: greetings
are ignored, W/L and emote spam count little, copypasta bursts are discounted, and a moment is
never picked because of chat alone.

## Storage
Everything ClipForge makes is temporary: clips, downloads, transcripts, motion graphics and
live recordings older than **6 days** (Settings → Storage) are deleted automatically when the
app starts and after every run. A clip still waiting in the YouTube upload queue is kept until
it is uploaded. Settings → Storage shows how much space is used and can clean up right away.

Output: `output\<video name>\<date_time>\`
- `clip_01_<title>.mp4` … ranked best first
- `clip_01_<title>.txt` title, caption + hashtags, hook, source timestamps
- `clips.json` everything, plus the model used and estimated cost

## Formats (the AI picks one per clip and keeps them varied)
| Format | What viewers see | Best for |
|---|---|---|
| `cold_open` | The payoff first (≈3 s) with a curiosity line, rewind sound, "EARLIER...", then the build-up; ends on the payoff, so it loops | crashes, kills, screams, fails |
| `freeze_open` | Plays into the peak, freezes in black & white + record scratch + "you're probably wondering how we got here", then rewinds | chaotic / absurd disasters |
| `replay` | The full moment, then an instant replay of the peak at half speed, zoomed | skilful or crazy plays |
| `wait_for_it` | A "WAIT FOR IT..." banner until the payoff hits, riser → boom | slow build, sudden payoff |
| `spotlight` | "Watch <creator>..." hook + creator credit on screen | iconic streamer moments |
| `two_part` | Part 1 ends frozen on the cliffhanger ("PART 2 ON MY PAGE"), Part 2 starts with context | longer stories (30 s+) |
| `standard` | Mid-action start + hook text | self-explanatory moments |
| **Top-N countdown** (extra video) | #N → #1 of the video's best moments with number cards | one per long video |

On every format: word-by-word captions, progress bar at the top, punch-in zooms on the
biggest reactions, a comment-bait question at the end, and sound effects on the beats.
Caption colours vary per clip ("auto") so uploads don't all look like one template.

## Sponsor & campaign (for paid clipping campaigns)

Open **Sponsor & campaign** in the app.

**Logo**: upload once and it stays saved. It goes on every clip, in the blurred band directly
under the captions and never on the gameplay. A logo forces the blur layout, so it also applies
when "crop" is selected. Animation: `float` (gentle hover), `pulse` (quick pop every 3 s) or
`none`. A PNG with a transparent background looks best; a JPG keeps its background box. Note:
on TikTok/Reels/Shorts the post caption overlays the lower part of the screen, so check a clip
on your phone once to see how much of the logo stays visible.

**Campaign rules**: paste them or upload .txt/.md/.pdf/.docx (scanned PDFs have no text, so paste
instead). Clear the box to turn rules off. With rules on:
1. The AI turns the rules into hard limits, which the software then enforces. These are clip length (sliders are
   overridden), required hashtags/@mentions/caption text (added to every caption), banned words
   (checked in the speech and in every title/hook/caption), and forbidden edits (e.g. no
   part 1/part 2, no countdowns, no sound effects).
2. The full rules go into the clip-picking prompt; it's told to skip anything doubtful.
3. A separate AI review checks every picked clip (what's said plus all its text) against every
   rule. Clips that don't pass are dropped, and the log says why. Twice as many candidates are
   reviewed so you still get your clip count when possible. If the review can't run, nothing
   is rendered.
4. Rules the software can't check (what's on screen, posting/account rules, music...) are listed
   in each clip's .txt and in `campaign_rules.txt` as **CHECK MANUALLY BEFORE POSTING**.

The AI reads the transcript only; it does not see the video. Always do the manual checks.

Batch mode: `batch.bat D:\Videos --logo sponsor.png --rules rules.pdf` (`--logo none` /
`--rules none` to turn off; otherwise the app's saved logo and rules are used).

## Motion graphics (optional)

Tick **Motion graphics** in the app (or `--motion on` in batch mode). The plain text overlays
become GSAP animations rendered by HyperFrames (open source, Apache 2.0):

| Element | What it does |
|---|---|
| Hook & tease | words pop in on a white card (red keyword + shine), or slam in yellow with a shake |
| End question | red card bounces in with a 💬 bubble |
| Countdown numbers | "#3" slams in with a ring burst and rays |
| Reaction stickers | emoji + word ("💀 BRUH") pops on the funniest beats (AI-picked, else the reaction detector), max 3 per clip |
| Creator tag | "@name" pill slides in |

**Install once:** double-click `setup_motion.bat`. It installs everything inside
`ClipForge\motion` (portable Node.js 22 if you don't have it, HyperFrames, GSAP, ffprobe, a
headless Chrome; ~300 MB), then renders a test animation.

**Speed:** each new animation takes a few seconds (Chrome draws every frame), all rendered in
one batch before the clips. Animations are cached by content, so "#3", your creator tag etc.
render only once; unused ones are deleted after 45 days. Expect roughly +1-2 minutes per video.

Anything that fails falls back to the normal text overlay; motion never stops a run. The
templates are plain HTML + GSAP in `motion\templates\` if you want to restyle them.

## Trending meme sounds (optional)

Tick **Trending meme sounds** in the app (or `--memes on`). The AI reads the transcript and
adds 0-2 meme sounds per clip where one genuinely fits, e.g. *fahhh* the instant a ball flies
just past someone. No forced memes.

- Your sounds live in `ClipForge\sounds\`. Drop in any mp3/wav/m4a/ogg; it's picked up on
  the next run. Name files after the meme (`oh-hell-nah.mp3`).
- `sounds\sounds.txt` tells the AI when each one fits (one line per file). Add a line for
  new sounds; files without a line still work, by name.
- Every sound is loudness-matched (downloaded memes differ by 20+ dB) and soft-limited.
  ClipForge measures where each sound's punch lands (e.g. 0.24 s into fahhh, 1.1 s into
  the Among Us reveal) and lines the punch up with the moment.
- The game audio dips while a meme plays; regular sound effects near a meme are dropped so
  they don't clash. Volume slider in the app.

## Split-screen gameplay (optional)
Content in the top half, muted gameplay (Minecraft parkour, Subway Surfers, ...) in the
bottom half, captions on the line between them. Turn it on in **Style**, or
`--split on --game minecraft_parkour` in batch mode.
- Put your own gameplay videos in the `gameplay` folder, one sub-folder per game:
  `gameplay\minecraft_parkour\run1.mp4`, `gameplay\subway_surfers\run1.mp4`.
  "Any game" mixes all of them.
- Each clip gets a different video from a random point; it keeps running through freezes
  and slow-motion. Use videos of a minute or more: shorter ones loop with a visible jump.
- The gameplay is always muted. A sponsor logo goes on the gameplay half, under the captions.
- Only use this for campaigns that allow outside footage. Many don't (e.g. "only footage
  from the official folder"). Use gameplay you recorded or have the rights to.

## Live streams (Live mode)
Switch to **Live stream**, paste the link and press **Start watching**. ClipForge records the
stream while it happens and, every 5 minutes (you choose), checks the newest part:
- the speech is transcribed and the streamer's words and voice are analysed; the **live chat**
  (YouTube and Twitch) is read as a weak extra hint;
- the AI only picks moments above the **minimum score** (75-85 works best; 95 means almost
  nothing qualifies); most checks find nothing, and that's normal;
- each clip is made with your Style / Campaign settings, uploaded to YouTube as **private**
  (if YouTube is connected) and announced on Telegram. Publish it from the Publish page.
- Each check re-reads about a minute of the previous one, so a moment that crosses two checks
  is clipped whole, and never twice.
- Checked parts of the recording are deleted straight away: only a few minutes of video are on
  disk at any time. Tick **Keep the full recording** to save the whole stream instead.
- If the stream hasn't started yet, ClipForge waits for it. When the stream ends, it does a
  last check and stops by itself. **Stop** ends it at once instead (a check in progress is
  abandoned; footage not checked yet is skipped). The PC must stay on and online the whole time.
- Clips appear 1-7 minutes after the moment (the wait for the next check + rendering).
- Batch mode: `python cli.py --live https://www.youtube.com/live/...`
Only clip streams you're allowed to clip (your campaign's creators, or your own).

## YouTube upload + Telegram (optional)
**Publish** page. One-time setup: put the `client_secret...json` from Google Cloud in the
ClipForge folder, click **Connect YouTube** and sign in. The sign-in is saved in
`youtube_token.json` (keep it private). While your Google app is in *Testing*, Google ends the
sign-in after 7 days; just click Connect again.
- Tick **Upload to YouTube when the clips are done**. Every clip is uploaded **private**, not
  made for kids, with its title, caption and keywords.
- **Private: I review and publish**: tick videos under *waiting for review*, then
  **Publish selected now**.
- **Scheduled**: YouTube makes them public on its own, one every N minutes (default 10).
  A new batch is scheduled after the last one already waiting.
- Campaign clips (rules loaded) are never scheduled: they always wait for your review.
- If YouTube's daily upload limit is hit, the rest stay queued: click **Upload queued clips**
  the next day. Failed uploads are retried the same way.
- **Telegram**: bot token + chat id, then **Send test message**. You get a message for every
  upload, failure, limit hit and publish.
- Batch mode: `--youtube review` or `--youtube scheduled --interval 10`.

## How it works
| Step | Tool | Cost |
|---|---|---|
| Transcribe (word timestamps) | Whisper Large V3 via OpenRouter (default), or faster-whisper on your PC | ~$0.03–0.22 per hour of audio / free |
| Reaction detector: exclamations ("oh no", "no way", swearing), laughter, sudden pauses, fast panicked talk, loud spikes | runs on your PC | free |
| Pick moments, titles, captions, SFX cues | OpenRouter, cheapest model that works | ~$0.00–0.01 per video |
| Cut, 9:16 layout, captions, hook, SFX, loudness | ffmpeg (bundled) | free |

**Finding moments**: the AI reads the full transcript, with the detector's tags inline
(`<<exclaim:oh no>>`, `<<laugh>>`, `...(3.2s silence)...`, `<<loud>>`) plus a ranked list
of hot spots. The tags are hints; the AI picks clips by what actually happens:
setup → reaction, banter, fails, chases. A quiet funny line can beat a scream.

**Model "auto"** = the cheapest model that is still good:
1. Reads OpenRouter's live price list. Drops models that can't fit the transcript or are tiny.
2. First tries recent models (≈15 months) from proven families (Gemini Flash/Flash-Lite,
   GPT mini/nano, gpt-oss, DeepSeek, Qwen3, Llama 4, Mistral Small/Medium, Grok fast,
   GLM, Kimi, MiniMax), cheapest first. Obscure or old bargain models are fallback only.
3. Every answer must pass a quality check (valid timestamps, real titles, hooks,
   hashtags, enough clips) or the next model is tried.
The model used and the real cost are shown at the end and saved in `clips.json`.
To force a model, type its OpenRouter id (e.g. `google/gemini-3.1-flash-lite`).

**Sound effects** (now 12, incl. record scratch) are synthesized by `make_sfx.py` from scratch (no samples, nothing
to license): whoosh, swoosh_short, impact, deep_boom, bass_drop, pop, ding, glitch,
riser, rewind, tick. Edit or replace the WAVs in `sfx\` to change them.

**Transcription** defaults to cloud Whisper Large V3 on OpenRouter (same key). The audio is
cut at quiet moments into ~5-minute pieces, sent as small MP3s, 4 at a time; silent pieces
are skipped and words on silent audio are dropped (cloud Whisper sometimes invents text in
silence). OpenRouter chooses the provider (DeepInfra ~$0.03/h, Together ~$0.09/h,
Groq ~$0.11/h). If word timestamps don't come back or a request fails, it tries Turbo, then
your local fallback model, so a run never stops because of it.

## Tips
- Local transcription speed (CPU): `small` ≈ 3–6× faster than real time on a modern
  8-core; use `base` for speed or `large-v3-turbo` for accuracy. Transcripts are
  cached per engine, so re-running the same video with other settings skips this step.
- NVIDIA GPU: set environment variable `CLIPFORGE_GPU=1` (needs CUDA 12 cuBLAS/cuDNN).
- YouTube URLs use yt-dlp; if YouTube blocks it, download the video and use the file.
- "Try free models first" is off by default: free models are heavily rate-limited.

## Code map
| File | What it does |
|---|---|
| `app.py` | The web app (Gradio): the Clip page, Publish, Settings; runs in the background with Stop |
| `ui/boxpicker.py` | The facecam / gameplay box picker (drag, resize, draw; shapes locked) |
| `ui/theme.py` | Look of the app (theme + CSS) |
| `cli.py` | Batch mode / command line (`batch.bat`) |
| `cf/pipeline.py` | `process()`: one video in, finished clips out. Calls everything below, step by step |
| `cf/live.py` | Live-stream monitor: recorder, periodic checks, cleanup |
| `cf/chat.py` | Live chat readers (YouTube, Twitch) and chat-burst detection |
| `cf/download.py` | Downloading videos with yt-dlp |
| `cf/frames.py` | A frame of a file / link / live stream, and the layout preview |
| `cf/storage.py` | Deletes clips and temporary files after N days; disk usage |
| `cf/audio.py` | Audio extraction and wav reading |
| `cf/transcribe.py`, `cf/cloud_stt.py` | Speech-to-text: cloud Whisper, local fallback |
| `cf/hype.py`, `cf/signals.py` | Loud spikes and reaction signals (exclaims, laughs, pauses, fast talk) |
| `cf/llm.py` | OpenRouter client: picks the cheapest model that passes the quality check |
| `cf/rules.py` | Campaign rules: reading, AI parsing, banned words, AI review, length limits |
| `cf/formats.py` | Edit formats (cold open, replay, wait for it...) -> timelines |
| `cf/captions.py` | Word captions and text overlays (ASS subtitles) |
| `cf/render.py` | Renders a timeline with ffmpeg (layout, zooms, logo, overlays, SFX, memes) |
| `cf/motion.py` | Animated overlays (HyperFrames + GSAP) |
| `cf/memes.py` | Meme-sound library from `sounds\` |
| `cf/brand.py` | Sponsor logo |
| `cf/gameplay.py` | Split-screen gameplay library |
| `cf/publish.py` | YouTube upload queue + Telegram messages |
| `cf/remote.py` | Phone access (share link + login) |
| `cf/settings.py` | Saved settings and their defaults |
| `cf/tools.py` | Shared helpers: stopping a run, folders, ffmpeg, Node.js, JSON files, time formats |


OFF CAMPUS CODE: RKJMJQ
