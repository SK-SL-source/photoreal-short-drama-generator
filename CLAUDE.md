# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This is a **Claude Code Skill package**, not an application. It is installed into `~/.claude/skills/photoreal-short-drama-generator` and invoked from inside a *separate* project folder (a short-drama production), where Claude follows it step by step to turn a script into a photoreal AI-generated short drama using a local ComfyUI install with MiniMax H3. There is no app to build or deploy — the "product" is the combination of `SKILL.md` (the behavioral spec for Claude), `references/*.md` (the detailed rules Claude must follow), and `scripts/*.py` (Python tools that mechanically enforce those rules and talk to ComfyUI).

Three layers, in order of authority when they conflict:
1. **`SKILL.md`** (English) / **`SKILL.cn.md`** (Traditional Chinese mirror, must stay in sync) — says what to decide, in what order, and who approves.
2. **`references/規則核心.md`** (rules core) plus any project-local `local_rules` files — says *how*; local rule files override the core on points they cover, earlier files in the local list win over later ones.
3. **`scripts/*.py`** — implement those rules mechanically and must never invent rules of their own. If a script disagrees with a rule file, that is a bug to fix in the script, not the rule.

A production's own project folder (created from the template in `SKILL.md` § "Project folder") is *not* part of this repo — it's generated at runtime under `projects_root` and contains a copy of `scripts/` plus the project's script/storyboard/clip files. When working in this repo, you are editing the Skill itself (behavior spec, rules, tooling), not a specific production.

## Commands

All scripts must be run with **ComfyUI's own bundled Python** (set via `scripts/設定.local.json` → `python`), not system Python — they rely on packages ComfyUI already ships (numpy, av, Pillow) per `requirements.txt`. From inside a production's `_腳本\` folder (a copy of `scripts/`):

```bash
# Setup check — run on a new machine, after changing settings, and at the start of every session
python 開工檢查.py

# Prompt lint — every H3 prompt must pass before submission
python h3_prompt_lint.py <prompt.txt> [--refs N|name,name] [--frames N] [--mode ref2va|i2va|fl2va|l2va|t2va]

# Shot table check (seconds, dialogue length, continuity columns, reference images)
python 檢查鏡頭表.py 2-鏡頭表.csv

# Production gate (G5D lock, invalidation, exceptions) — see scripts/檢查量產Gate.py sub-commands
python 檢查量產Gate.py check
python 檢查量產Gate.py fingerprint [--manifest <timeline.json>]
python 檢查量產Gate.py lock --animatic <path> --manifest <path> --user "<user's own words, must contain 鎖定>"
python 檢查量產Gate.py invalidate --reason "<reason>"
python 檢查量產Gate.py exception --shots <ids> --profiles <profiles> --takes <n> --reason "<reason>" --user "<user's own words, must contain 例外 and the shot ids>"

# Queue ledger — the only path by which H3 video (h3_ref, h3_i2v) can be submitted
python 佇列.py                 # status only, does not change the ledger
python 佇列.py wait [seconds]  # poll (default 30s): collects finished jobs, relays first frames, dispatches ready jobs
python 佇列.py release <id...> # release jobs held after a relay-source cut/leak check
python 佇列.py cancel <id...>  # cancel from ComfyUI

# Review-packet measurement tools (frame diff, camera drift, speech timing, loudness, cut/leak detection, etc.)
python 驗片量測.py <frame0|drift|strip|loud|tail|dup|cut|speech|motion|sheet|zoom> ...

# Edit/mix (also used to build the animatic) and music-cue assembly
python 剪接.py <cut-config.json> [--dry-run]
python 配樂組合.py <music-assembly-config.json>
```

There is no package manager, build step, linter config, or committed test suite. A local self-test script (`驗片量測_自我測試.py`) is deliberately `.gitignore`d (see `.gitignore` / CHANGELOG "本機回歸" entries) — regression counts are reported in CHANGELOG.md but the test file itself is never committed.

## Architecture: five stations, mechanically enforced gates

`SKILL.md` defines five stations, each pausing for explicit user approval before advancing:

1. **Script** (`references/1-劇本.md`) — intake card (G1), brief/story/event table (G2).
2. **Storyboard** (`references/2A-分鏡敘事.md` then `references/2-分鏡.md`) — the biggest station, with its own sub-gates: **G3A** art direction lock (visual bible + Style Master, locked only on explicit user "鎖定"), **G3B** assets (character/scene/prop cards), **G4A** narrative skeleton (decide which shots earn their place, for the whole episode, before full cards), **G4B** text storyboard (camera, action card, first-frame source, continuity handoff — only for shots kept at G4A), **G4C** narrative QC pass, **G5A** voice assets, **G5B** storyboard reference images/first frames, **G5C** animatic (hard-cut storyboard refs + preview speech), **G5D** Final Shot Lock — the user approving one animatic version writes fingerprints (shot table, production plan incl. script, animatic) into `量產關卡.json`; any later change to the plan invalidates the lock.
3. **Prompts** (`references/3-提示詞.md`) — only after G5D lock: compile H3 prompts from locked cards, lint, spot-check, generation authorization (G6).
4. **Generate and review** (`references/4-生成與驗片.md`) — queue-only submission, per-shot preflight against the lock, measured review packets, item-by-item chat approval, rework within classified limits (G7).
5. **Close** (`references/5-結案.md`) — narration, music cues, rough/fine cut, no-music master, final approval (G8/G9).

### Why formal H3 video can be blocked (the core mechanism)

This is the most load-bearing piece of logic in the repo, split across three scripts:

- **`佇列.py`**: H3 video (`h3_ref`, `h3_i2v`, any profile) can *only* be submitted through the queue ledger. `add` runs a preflight and records the job as "待送出" (ready, not yet sent) instead of sending it immediately; `wait` runs the preflight again right before dispatch and only then submits to ComfyUI. Submission also relays first frames between shots and holds a relay when the source clip fails a cut/leak check (`驗片量測.cmd_pack` / `cut_one`).
- **`檢查量產Gate.py`**: implements the gate itself — checks that `量產關卡.json` is `LOCKED` and that its three fingerprints (shot table, production plan including the script, approved animatic) still match the live files, plus per-shot checks (prompt + lint, dialogue verbatim against the locked shot table, attached references/voices, mode, frame count, segment). Without a valid lock, or a narrow user-authorized exception (recorded via `exception`, requiring the user's own words to contain 例外 and name the shots), H3 video submission is refused.
- **`comfy.py`**: the actual ComfyUI job submitter (`h3_ref`, `h3_i2v`, etc.) — it does **not** gate anything itself; `comfy.run`/direct submission cannot send H3 video because the one-time dispatch permit is only issued by the queue after the preflight passes.
- **`剪接.py`** (editing) and **`檢查鏡頭表.py`** (shot-table checker) are the downstream half of the same contract: the editor only cuts clips whose shot-table status is 過 (approved) and that have a review packet, matching the card's 成片取 (final-cut trim); the shot-table checker catches seconds exceeding generated frames, trim/duration mismatches, and dialogue windows that get cut.

The gate is enforced only on this Skill's own submission path (queue + `comfy.py`); it has no effect on external code that bypasses these scripts.

### Settings resolution

`scripts/設定.json` (shipped defaults) is overridden key-by-key by `scripts/設定.local.json` (this machine's values — ComfyUI address/folder, Python, ffmpeg, per-GPU `max_frames`, model file names, `local_rules`/`lessons`/`series_state`/`official_guides`/`projects_root` paths). `設定.local.json` is machine-specific and **never committed** (gitignored). `comfy.py` loads and merges these into `CFG`; every other script imports `CFG` from `comfy`, so settings changes only need to happen there.

## Working in this repo

- Most "code changes" here are actually **spec changes**: edits to `SKILL.md`/`SKILL.cn.md` or `references/*.md` that change what Claude does during a production. Keep `SKILL.md` and `SKILL.cn.md` semantically in sync (English/Traditional Chinese mirrors); `references/*.md` are Traditional-Chinese-only.
- When changing a rule, prefer the smallest possible diff to the prose (the project explicitly avoids adding new card fields/tables when a rule change can be expressed as a sentence edit — see CHANGELOG 1.3.1).
- Numbers labeled 【本機值】 in `references/規則核心.md` are measured on a specific RTX 3090 setup; treat them as environment-specific, not universal constants.
- Scripts must only implement rules already written in `references/規則核心.md` or project-local rule files; a script enforcing something not written in a rule file is itself a bug.
- Follow the existing commit convention: `fix:` / `docs:` for incremental changes, `Release vX.Y.Z: <one-line summary>` for version bumps (bump `meta.yaml` `version:` and add a dated section to `CHANGELOG.md` with Fixed/Changed/Verified/Known limitations subsections, per the existing entries).
