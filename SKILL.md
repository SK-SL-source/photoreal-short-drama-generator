---
name: photoreal-short-drama-generator
description: |
  Make a photoreal short drama (3D or live-action look) on local ComfyUI (MiniMax H3) through five stations: script and event table, visual bible and storyboard with a purpose for every shot, first frames, compiled and linted H3 prompts, queued generation and measured clip review, then sound post and the final cut. Not for single clips.
trigger-words: [仿真人短剧, 仿真人短劇, 仿真人写实3D, 仿真人寫實3D, 写实3D短剧, 寫實3D短劇, photoreal short drama, photoreal 3D drama]
---

# Photoreal Short Drama Generator

Turn a short-drama idea or script into a finished photoreal short, in a 3D or live-action look, one station at a time. This Skill says what to decide, in what order, and who approves; its rules core says how.

Talk to the user in their language. Documents for the user (brief, story, storyboard, check notes) are in their language; H3 prompts follow station 3.

## Settings and rules

- **Machine settings:** "the settings" means `scripts/設定.json` (defaults shipped with this Skill) overridden by `scripts/設定.local.json` (this machine's values, never uploaded): the ComfyUI address and folder, Python, ffmpeg, the longest clip this GPU can render, model file names, and the local files below. Run `scripts/開工檢查.py` with that Python on a new machine, after changing the settings, and at the start of each work session; fix every ❌ before going on.
- **Rules:** `references/規則核心.md` is bundled with this Skill. `local_rules` in the settings lists local rule files, such as a studio's own rules or a prompt lab's verified patterns. Where a local file covers a point it wins over the core, and earlier files in the list win over later ones. Read the core and the local files before station 1, and again before writing prompts.
- **Official H3 format:** `official_guides` in the settings points to MiniMax's H3 prompt guides (`base-en.txt`, `ref-en.txt`). Without them, follow the format summary in the core (§6).
- **Checkers:** `scripts/h3_prompt_lint.py` checks every prompt; the external lint named in the settings is used instead when it exists. `scripts/檢查鏡頭表.py` checks the shot table. Scripts and checkers only implement the rules and never set rules of their own; a script that disagrees with a rule file is a bug to report.

Use only writing that the core or a local rule file marks as verified, or that this Skill's templates carry. Anything else is a test and is labeled as one.

When a step here conflicts with those files, or cannot be done, stop and report in three parts: which shot or station; what happened, in one plain sentence; two options for the user to choose from.

## Principles

1. **Every item is decided.** Every part of the frame, every second of a clip and every sound track is written down somewhere, including "deliberately empty" or "deliberately quiet". Whatever nobody decides, the model fills with its average.
2. **One event per shot.** Two logically separate events are two shots. When the events outnumber the shots agreed at intake, ask the user whether to add shots or merge two events; never squeeze them into one shot yourself.
3. **Every shot has a narrative purpose**: what the audience knows or feels that they did not a second ago. Shot size, camera position, lens and aperture follow from that purpose.
4. **Move capability limits upstream.** Whatever AI cannot do reliably (readable text, exact counts, exact cut points, mirror detail, piece-by-piece hand work) is changed at the script or storyboard station, not forced in the prompt.
5. **Prompts are compiled** from approved storyboard cards. The prompt adds nothing the card does not say.
6. **Review by measurement plus human judgment.** Verdicts are pass, fail or not verified; missing evidence is not a pass.
7. **Only the user changes the story.** When something cannot be done as written, offer options marked "story change" and wait.

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
- Before every generation batch: every H3 prompt passes the lint and every node graph passes `check_graph`. If the user wants to watch progress, open the ComfyUI page from the settings in one browser tab (`comfy.open_chrome_once()` does it with Chrome on Windows).

## Project folder

Create each project under `projects_root` from the settings (ask the user when it is empty); a test project uses the same layout in its own folder.

```text
1-劇本.md       brief, story, final lines, event table
2-鏡頭表.csv    overview, continuity, status (checked with _腳本\檢查鏡頭表.py)
2-分鏡.md       visual bible, asset list, shot cards, cue sheet
資產\           cards, voice files, first frames
3-提示詞\       one prompt per shot, plus check notes
4-影片\         clips, review packets (驗片\) and review notes
5-成片\         rough cuts, fine cuts, the final cut and the no-music master
工單.md         project settings, progress, issues, lessons
_腳本\          a copy of this Skill's scripts\ (with 設定.json and 設定.local.json), each station's job scripts, and the queue ledger 佇列\
```

Work only from this folder and the series-state file (`series_state` in the settings, when set); never carry story, settings or one-off fixes over from other episodes.

## Station 1: Script

Follow `references/1-劇本.md`.
- G1 intake card: aspect ratio, total length and shot count (with the local 5.17-second minimum per clip), music mode, reference-image source, style preset.
- G2 brief, story, final lines, capability pre-check and the event table, approved together.

## Station 2: Storyboard

Follow `references/2-分鏡.md`, the chosen style preset and the audio preset.
- G3 visual bible (every field decided) and assets: cards, asset list, voice files.
- G4 shot table and shot cards: narrative purpose, camera, action card, sound, first-frame source, the continuity handoff at each cut, and A/B segments where one generation cannot hold the order; the cue sheet when there is music. Run the shot-table check before showing it.
- G5 keyframes, optional: a shot starts from the previous shot's last frame (relay) or an adjacent approved frame; synthesize a keyframe only when neither exists, with the user's approval. Write what the first frame settles back into the cards.

## Station 3: Prompts

Follow `references/3-提示詞.md`: route each shot to an H3 mode, compile the prompt from its card, lint it, show two prompts for a spot check, then the generation authorization card (G6).

## Station 4: Generate and review

Follow `references/4-生成與驗片.md`: queue only authorized items through the queue ledger (`_腳本/佇列.py`, which also relays first frames), run the review packet on each clip, list the clips waiting for review and take the user's item-by-item replies, record the status in the shot table, and handle failures within the rework limits (G7).

## Station 5: Close

Follow `references/5-結案.md`: inner voice and narration, music cues assembled into one track, rough cut, fine cut and mix with a versioned config for each cut, and the no-music master (G8; each generation needs authorization). Then final approval (G9): update the work order, and the lessons and series-state files named in the settings.

## Presets

- Style: `presets/style/photoreal-3d.md` (style-sentence formula, visual-bible defaults, card specs) or `presets/style/live-action.md` (live-action film look; same defaults and card specs), chosen on the intake card.
- Audio: `presets/audio/dialogue-led.md`.

Only these exist. Add another only when a project needs it and it has been tested.

## Boundaries

Not for a single image, one standalone clip, a simple edit, or prompt-only help. Narration-led and 2D productions are not covered yet.

Sources: the rules core is distilled from a studio rule file and a prompt lab's controlled tests (one variable per test, three seeds, on a local RTX 3090), then checked in two end-to-end test productions. The event table, purpose-first shot design and continuity checks are adapted from DirectorSKILL (MIT License; see `THIRD_PARTY_NOTICES.md`).
