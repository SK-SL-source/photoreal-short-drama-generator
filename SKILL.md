---
name: photoreal-short-drama-generator
description: |
  Make a photoreal short drama (3D or live-action look) on local ComfyUI (MiniMax H3) through five stations: script and event table; art direction locked per project, assets, a narrative-efficient storyboard, voice assets, storyboard references and an animatic the user approves as the production plan; H3 prompts compiled from the locked cards and linted; queued generation behind a mechanically enforced production gate, with measured clip review; then sound post and the final cut. Not for single clips.
trigger-words: [仿真人短剧, 仿真人短劇, 仿真人写实3D, 仿真人寫實3D, 写实3D短剧, 寫實3D短劇, photoreal short drama, photoreal 3D drama]
---

# Photoreal Short Drama Generator

Turn a short-drama idea or script into a finished photoreal short, in a 3D or live-action look, one station at a time. This Skill says what to decide, in what order, and who approves; its rules core says how.

Talk to the user in their language. Documents for the user (brief, story, storyboard, check notes) are in their language; H3 prompts follow station 3.

## Settings and rules

- **Machine settings:** "the settings" means `scripts/設定.json` (defaults shipped with this Skill) overridden by `scripts/設定.local.json` (this machine's values, never uploaded): the ComfyUI address and folder, Python, ffmpeg, the longest clip this GPU can render, model file names, and the local files below. Run `scripts/開工檢查.py` with that Python on a new machine, after changing the settings, and at the start of each work session; fix every ❌ before going on.
- **Rules:** `references/規則核心.md` is bundled with this Skill. `local_rules` in the settings lists local rule files, such as a studio's own rules or a prompt lab's verified patterns. Where a local file covers a point it wins over the core, and earlier files in the list win over later ones. Read the core and the local files before station 1, and again before writing prompts.
- **Official H3 format:** `official_guides` in the settings points to MiniMax's H3 prompt guides (`base-en.txt`, `ref-en.txt`). Without them, follow the format summary in the core (§6).
- **Checkers:** `scripts/h3_prompt_lint.py` checks every prompt; the external lint named in the settings is used instead when it exists. `scripts/檢查鏡頭表.py` checks the shot table. `scripts/檢查量產Gate.py` checks the lock prerequisites and the G5D lock and records the lock, its invalidation and exceptions; the queue runs its per-shot preflight. Scripts and checkers only implement the rules and never set rules of their own; a script that disagrees with a rule file is a bug to report.

Use only writing that the core or a local rule file marks as verified, or that this Skill's templates carry. Anything else is a test and is labeled as one.

When a step here conflicts with those files, or cannot be done, stop and report in three parts: which shot or station; what happened, in one plain sentence; two options for the user to choose from.

## Principles

1. **Every item is decided.** Every part of the frame, every second of a clip and every sound track is written down somewhere, including "deliberately empty" or "deliberately quiet". Whatever nobody decides, the model fills with its average.
2. **A shot never spans two events.** Two logically separate events are two shots; one event may take several shots, one per narrative beat (`references/2A-分鏡敘事.md` §4). When the events outnumber the shots agreed at intake, ask the user whether to add shots or merge two events; never squeeze them into one shot yourself.
3. **Every shot has a narrative purpose**: a beat (what the audience knows or feels that they did not a second ago), or a hold, delay, orientation or callback whose premise holds. Shot size follows the expression point: the widest size that keeps it fully effective. Camera position, lens and aperture follow from the purpose.
4. **Move capability limits upstream.** Whatever AI cannot do reliably (readable text, exact counts, exact cut points, mirror detail, piece-by-piece hand work) is changed at the script or storyboard station, not forced in the prompt.
5. **Prompts are compiled** from approved storyboard cards. The prompt adds nothing the card does not say.
6. **Review by measurement plus human judgment.** Verdicts are pass, fail or not verified; missing evidence is not a pass.
7. **Only the user changes the story.** When something cannot be done as written, or a storyboard proposal would touch approved content, offer options marked "story change" and wait.
8. **Only the user locks the art direction.** Analyse the script and propose two or three directions; which one becomes the look of the whole work is the user's decision.

## Gates, authorization and rework

- Do one station at a time and stop at each gate.
- **Reviews happen in chat, item by item.** "File or item + 過" approves that item; "file + a change" is the rework instruction and the approval for that one regeneration (state the estimated time when you queue it). A reply that does not say which item, such as "ok" or "continue" after several items, approves nothing: ask which. Keep the shot table's status column current, so the list of items waiting for review can be read from it at any time.
- **Choice cards (AskUserQuestion) only for batch authorization and branch decisions:** the intake card, generation authorization, and choices between options such as rework routes, story changes or a profile change. Put a gate's questions on one card.
- Review results never authorize more generation. Every image, video, audio or music generation, and every re-edit, needs the user's approval for that operation: say which shots, what changes, how many outputs and the estimated render time.
- One approval covers one generation per listed item unless the user approves a bounded batch.
- **Redo exactly as told.** Change only what the user named; if something else seems worth changing, ask first.
- **Rework limit,** counted per shot in the shot table:
  - Technical failure (the clip does not do what its card says): the first generation plus at most two more for the same issue. Regenerating its keyframe counts; switching modes, splitting the shot or renaming the issue does not reset the count. At the limit, go back to station 2 (or 1).
  - The user gives a new direction (performance, timing, camera, mood): a new issue, counted from zero.
  - The user explicitly asks to keep trying: allowed past the limit; note it in the work order.
- Change only the affected shots; approved outputs stay locked. A new candidate is not approved until the user approves it.
- Before every generation batch: every H3 prompt passes the lint and every node graph passes `check_graph`. H3 video generation (`h3_ref`, `h3_i2v`, any profile) also needs a valid G5D lock and a passed per-shot preflight (`references/4-生成與驗片.md` §1); without them it runs only inside a narrow exception the user authorized and that is recorded in `量產關卡.json`, never as a silent bypass; the queue enforces this with a one-time permit issued only when the dispatch-time preflight passes, so `comfy.run` and direct submits cannot send H3 video. If the user wants to watch progress, open the ComfyUI page from the settings in one browser tab (`comfy.open_chrome_once()` does it with Chrome on Windows).

## Project folder

Create each project under `projects_root` from the settings (ask the user when it is empty); a test project uses the same layout in its own folder.

```text
1-劇本.md       brief, story, final lines, event table
2-鏡頭表.csv    overview, continuity, status (checked with _腳本\檢查鏡頭表.py)
2-分鏡.md       visual bible, asset list, narrative ledger, shot cards, voice assets, storyboard reference list, cue sheet
2-分鏡圖\       storyboard reference images, one per shot that goes into production
2-預覽\         animatic versions (animatic_vNN.mp4 and its .json timeline) and preview speech
資產\           cards, voice files, first frames
3-提示詞\       one prompt per shot, plus check notes
4-影片\         clips, review packets (驗片\) and review notes
5-成片\         rough cuts, fine cuts, the final cut and the no-music master
量產關卡.json   G5D lock: the approved animatic version, source fingerprints and exceptions
工單.md         project settings, progress, issues, lessons
_腳本\          a copy of this Skill's scripts\ (with 設定.json and 設定.local.json), each station's job scripts, and the queue ledger 佇列\
```

Work only from this folder and the series-state file (`series_state` in the settings, when set); never carry story, settings or one-off fixes over from other episodes.

## Station 1: Script

Follow `references/1-劇本.md`.
- G1 intake card: aspect ratio, total length and shot count (with the local 5.17-second minimum per clip), music mode, reference-image source, style preset.
- G2 brief, story, final lines, capability pre-check and the event table, approved together.

## Station 2: Storyboard

Follow `references/2A-分鏡敘事.md` to decide which shots to make and what each must deliver, then `references/2-分鏡.md`, the chosen style preset and the audio preset.
- G3A art direction lock: propose art directions from the script and settle them with the user through a few questions; then write the visual bible (the single source of truth for the look) with its fixed art paragraph for image prompts, and generate Style Master candidates for the user to approve. No cards or first frames are generated before the user approves both the visual bible and the Style Master.
- G3B assets: cards and the asset list; every batch is compared with the Style Master before approval.
- G4A narrative skeleton, for the whole episode before any full card: the narrative ledger first; for each candidate shot only the card header, the 2A block (beat, expression point, delete test, scale, verdict) and any story-change question it raises. No shot table yet. Only shots whose final verdict is keep go on to G4B; an open question blocks G4B, and an approved story change goes into `1-劇本.md` first.
- G4B text storyboard, only for the shots kept at G4A, on the same cards: narrative purpose, camera, action card, sound, first-frame source, the continuity handoff at each cut, and A/B segments where one generation cannot hold the order; the cue sheet when there is music. Then build the shot table and run its check before showing it.
- G4C narrative QC: one pass over the whole episode with the 2A tests; list only the problem shots and send each back to G4A (narrative) or G4B (camera and production), redoing only what it affects.
- G5A voice assets, after G4C: list every identity that actually speaks (dialogue, off-screen voice, inner voice, narration) and give each one approved voice file: the user's reference, the series' approved voice, or a picked `h3_voice` candidate. A voice asset decides how an approved speaker sounds, never who speaks or what is said.
- G5B storyboard references and first frames: every shot that goes into production gets one storyboard reference image in `2-分鏡圖\`, compiled from its card and the approved assets at the lowest sufficient cost (an approved image, a crop of a master, then a cheap composite). It is a previz asset for review and the animatic. The first-frame strategy stays as planned at G4B; relay and borrowed frames stay pending until their clips exist, and a storyboard reference becomes a first frame only when it passes the eligibility check.
- G5C animatic, after G5B: the approved storyboard references cut together in the shot table's order and durations, hard cuts only, with preview speech made from the approved lines and voices (`h3_vo`) and any existing sound cue that carries the story (otherwise marked unresolved); each version is `2-預覽\animatic_vNN.mp4` plus its `剪接.py` config. The user reviews the whole episode for story, pacing, fit of the lines and the order of reveals across shots before any formal generation (a still image proves nothing about timing inside a shot, which stays with the G4B action card); each problem goes back to the layer that owns it (G4A/G4C, G4B, G5B, G5A or station 1), and nothing is rewritten in the animatic.
- G5D final shot lock, after the user approves one complete animatic version: check the lock prerequisites from the project files, never from the work order (the gate record says G3A LOCKED, G4C PASS and G5C PASS for exactly this animatic version; every asset and voice the shots use is approved; each shot has one approved storyboard reference with all three checks passed; derived columns agree with their sources); when the user says lock, `檢查量產Gate.py lock` re-checks them and records the approved animatic and fingerprints of the shot table, the production plan (including `1-劇本.md`) and that animatic in `量產關卡.json`. Any later change to the plan (the script, shots, order, durations, lines, speakers, staging, camera, action cards, sound windows, splits, first-frame strategy, voices, storyboard references, cards) invalidates the lock until a new animatic is approved; runtime state (status, file names, retries, seeds, a resolved relay frame) does not.

## Station 3: Prompts

Follow `references/3-提示詞.md` once G5D is locked: route each shot to an H3 mode, compile the prompt from its card, lint it, show two prompts for a spot check, then the generation authorization card (G6).

## Station 4: Generate and review

Follow `references/4-生成與驗片.md`: queue only authorized items that pass the per-shot preflight through the queue ledger (`_腳本/佇列.py`, which also relays first frames), run the review packet on each clip, list the clips waiting for review and take the user's item-by-item replies, record the status in the shot table, and handle failures within the rework limits (G7).

## Station 5: Close

Follow `references/5-結案.md`: inner voice and narration, music cues assembled into one track, rough cut, fine cut and mix with a versioned config for each cut, and the no-music master (G8; each generation needs authorization). Then final approval (G9): update the work order, and the lessons and series-state files named in the settings.

## Presets

- Style: `presets/style/photoreal-3d.md` (style-sentence formula, visual-bible defaults, card and Style Master specs) or `presets/style/live-action.md` (live-action film look; same defaults and specs), chosen on the intake card.
- Audio: `presets/audio/dialogue-led.md`.

Only these exist. Add another only when a project needs it and it has been tested.

## Boundaries

Not for a single image, one standalone clip, a simple edit, or prompt-only help. Narration-led and 2D productions are not covered yet.

Sources: the rules core is distilled from a studio rule file and a prompt lab's controlled tests (one variable per test, three seeds, on a local RTX 3090), then checked in two end-to-end test productions. The event table, purpose-first shot design and continuity checks are adapted from DirectorSKILL (MIT License; see `THIRD_PARTY_NOTICES.md`).
