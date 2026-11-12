# Session C: Filipino speech from pre-made, spliced clips (interim: before the clips)

This session follows Kenth's handoff "Filipino audio from pre-made, spliced clips" (2026-11-12). It supersedes
Session C of the original brief: the audio tutorial is dropped, and making and downloading Filipino clips is
now part of the work.

Done so far:
- handoff steps 1–2 (§5.1–5.4), with placeholder clips;
- hardware-free tests 1–8.

Waiting on the team's Drive folder:
- preparing the real clips (§5.5);
- device tests 9–12.

Nothing here has been played through the earphones yet.

## What it does

- **Filipino is the default** (`SystemConfig.language = "fil"`; `--language {fil,en}` on `main.py`'s pre-parser).
- **The speech worker is still handed English text,** and the queue contract is unchanged.
  `core/fil_clips.py` turns that text into clips:
  - a mode announcement or alert is one sentence clip, matched on its whole English text;
  - a detection phrase is parsed as `[multiple] <label> [still | leaving to] <zone>` and spliced from word
    clips: [maraming] [tao] [pa rin] [sa kaliwa].
- **The labels come from the clip list's `word: object` rows,** not from the code.
- **All or nothing:** anything the clips can't say whole is spoken in English by espeak-ng, exactly as
  before. It is never half Filipino.
- **Playback.**
  - The joined WAV is scaled by the knob's espeak amplitude / 100 (the same dB change espeak's `-a` gives).
  - It is written to `/dev/shm` and played by **`pw-play`**.
  - The process is a `Popen` subclass, so the existing poll / cut loop, failsafe rules (D41, D42), pacing
    and head-turn mute are untouched.
  - The WAV is deleted when playback ends, is cut, or at shutdown.

## Files

| File | Change |
|---|---|
| `src/second_vision/pipeline/callbacks.py` | §5.1: `"Bicycle": "bicycle"`, `"Tricycle": "tricycle"` (were `"vehicle"`); comment above the map. As specified in the planning session, since the patch was not on the Pi |
| `src/second_vision/core/priority.py` | §5.1: `bicycle` and `tricycle` get weight 1.0 and are urgent when centred, as `vehicle` was. Only the spoken word changes |
| `src/second_vision/core/fil_clips.py` | New. `ClipBank`: load (16-bit mono, one rate; bad files recorded, not fatal; missing folder means an empty bank and one warning), `resolve()`, `render()`. `CLIP_GAP_SECONDS = 0.06`, PLACEHOLDER until tuned by ear |
| `src/second_vision/workers/tts_worker.py` | `_start_speech()` replaces the direct `_start_espeak()` call. Adds `_ClipPlayback(Popen)` and `language` passed per item. The `[TTS] '<English text>'` line now ends with ` (fil)` or ` (en)`. The latency log names the engine `fil-clips`. The bank loads at the worker's start |
| `src/second_vision/core/config.py`, `src/second_vision/main.py` | `language = "fil"`; `--language {fil,en}` |
| `scripts/prepare_fil_clips.py` | New, §5.5. Checks the set, trims silence, matches loudness to `espeak-ng -a 100 "person left"`, keeps peaks ≤ −1 dBFS, resamples to 48 kHz, writes a report. `--placeholders` makes an espeak-ng stand-in set |
| `tests/test_fil_clips.py` | New, 49 tests (below) |
| `tests/test_detection_classes.py` | §5.1: mapping list and class set updated; new test: a centred bicycle or tricycle is still urgent |
| `tests/conftest.py` | Autouse fixture: every test gets an EMPTY clip bank, so no test plays the shipped clips. A test that wants clips installs its own |
| `tests/data/fil_placeholder/clips.csv` | The team's draft clip list (34 rows), the list the tests run against |
| `results/panel-revisions/session-c/evidence/second_vision_tts_clips.csv` | The draft clip list, verbatim from the planning session |

## Tests

New tests (all in `tests/test_fil_clips.py` unless noted):

| Handoff test | What it checks |
|---|---|
| 1. Inventory guard | Every spoken sentence resolves to one clip: the 11 alert `*_TEXT` constants except `EARPHONES_MISSING_TEXT`, read from the source with `ast` (`pipeline/app.py` can't be imported without GStreamer), plus the 4 `"{mode} mode"`. Every hef_v3 label from `DETECTION_CLASS_MAP` over `labels.json`, except `pole`, resolves in every phrase form. Runs on the draft list, and on `assets/audio/fil` once it exists |
| 2. 208 detection phrases | Each gives the right clip sequence |
| 3. What the clips can't say | 13 texts resolve to None: `leaving to center`, unknown labels, `pole`, extra or missing words |
| 4. `render()` | Length = clips + gaps; amplitude 100 leaves samples unchanged; 10 and 50 scale them; no int16 wrap-around at 100, 10 or 140 |
| 5. `_start_speech()` | `fil` + resolvable → `pw-play` on a `/dev/shm` WAV holding exactly the rendered clips. `fil` + unresolvable → espeak with the same argv as before. `en` or None → espeak always |
| 6. Removing the WAV | A cut (terminate), a natural end and a shutdown each remove it. The real interrupt loop with real processes: an urgent item cuts a playing clip and leaves no file |
| 7. Fallbacks | A missing clip → English, logged once per text. No clips at all → no per-text log. No player → English, no file left |
| 8. Regression | Full suite (next table) |

Also: `language` defaults to `fil`; the latency log names the engine; `tests/test_detection_classes.py` has the
bicycle/tricycle urgency test.

**Checked against the old code:**
- The worker tests fail against the Session B version of `tts_worker.py` (15 failed or errored).
- The bicycle/tricycle urgency test fails against the old map (2 failed).
- The inventory guard fails when a sentence is reworded (`VIEW_BACK_TEXT`) or a label's clip is missing (16
  bench phrases).

| Run | Result |
|---|---|
| `tests/test_detection_classes.py` + `tests/test_priority.py` | 58 passed (the planning session also had 58) |
| `tests/test_fil_clips.py` | 49 passed |
| Speech tests (`test_tts_volume`, `test_alerts_speech`, `test_failsafe_speech`, `test_priority`, `test_detection_classes`, `test_latency_log`, `test_fil_clips`), before the worker change | 159 passed, 1 skipped |
| **Full suite, `my_hailo_env`** (GStreamer tests included) | **1260 passed, 117 skipped, exit 0** (`pytest-c-full.log`) |

**Mock-mode smoke test** (`scripts/run.sh --mock --language fil`, 10 s, audio pointed at an empty runtime
folder so nothing played):
- the flag parses;
- the bank logged once: "no Filipino clips loaded from …/assets/audio/fil … speaking English";
- utterances are logged as `[TTS] 'motorcycle center' (en)`.

**Preparation script** (`--placeholders` on the draft list, output to a scratch folder, not the repo):
- 34 clips, matched to the espeak reference (active-speech RMS −20.1 dBFS);
- none needed peak limiting;
- about 1 s in all.

## Deviations from the handoff

1. **`pw-play`, not `aplay`.**
   - On the device, `aplay -L` lists no PipeWire or Pulse device, so `aplay` plays on HDMI.
   - espeak-ng reaches the earphones through PipeWire's default sink: libpcaudio → PulseAudio API →
     pipewire-pulse → "USB Audio Analog Stereo".
   - PipeWire holds the USB card, so `aplay -D hw:…` would conflict.
   - `pw-play` plays to the same default sink as espeak-ng. `paplay` is not installed.
2. **Sentence matching ignores case, extra spaces and a final full stop.** It is otherwise exact, so a CSV
   that drops the last "." still matches.
3. **Unit tests use synthetic clips,** with known samples so `render()` can be checked exactly and the
   tests need no espeak-ng. The espeak-ng stand-in set the handoff suggests is made by
   `prepare_fil_clips.py --placeholders`, for trying the device side before the real clips come.
4. **The `[TTS]` line is printed after the speech process starts,** because the engine is known only then.
   If starting it raises, that attempt has no `[TTS]` line; the worker's error line is printed as before.
5. **Bicycle/tricycle fix** written by hand from the planning session's exact instructions; the patch file
   was not on the Pi.

## Still to do

- **When the Drive folder arrives:**
  - `my_hailo_env/bin/python scripts/prepare_fil_clips.py --link "<link>" --download-to <empty dir>`,
    after installing `gdown` in the venv; or download by hand and use `--source`;
  - commit `assets/audio/fil/` (`*.wav` is not gitignored), never the raw download;
  - device tests 9–12.
  - Rate: the adapter supports 44.1 and 48 kHz, and PipeWire's clock runs at 48 kHz, so `--rate 48000` (the
    default).
- **`CLIP_GAP_SECONDS`** is tuned by ear on the device.
- **For the team, not code:**
  - The draft says "center" = "sa harap" (in front), not the literal "sa gitna"; its notes flag this.
  - The mode sentences are meaning-based drafts.
- **`pole`** (hef_v2 only) has no clip and is spoken in English if hef_v2 is ever loaded. This is by design.
- **Thesis credit:** record the engine and voice from `voice_info.txt` when it arrives (ElevenLabs free plan:
  non-commercial use, credit required).
- **Documentation (AGENTS.md rule 8), waiting on Kenth's yes:**
  - a DECISIONS.md entry: Filipino default via spliced clips, English espeak-ng fallback, tutorial dropped,
    bicycle/tricycle by name;
  - the TTS section of ARCHITECTURE.md;
  - TASKS.md.
- **Docs that still describe Bicycle/Tricycle as "vehicle"** (the T-05 fix is done in code):
  `documentation/TASKS.md:189`, `docs/testing/DISAGREEMENTS.md:88-92`, `REQUIREMENTS.md:67-77`,
  `FIX-RANKING.md:36`, `WORKBOOK-REVISIONS.md:77-85`.
