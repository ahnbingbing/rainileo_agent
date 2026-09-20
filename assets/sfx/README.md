# Meme SFX — drop-in CC0 samples

The meme grammar (`scripts/impact_edit.py`) plays short SFX stings on its punch beats
(zoom punches, freezes, the climax). By default these are **synthesized** (a decaying
sine/noise burst) — serviceable, but a real sample reads far punchier and less like a
test tone.

## How to upgrade

Drop a real **CC0 / royalty-free** sample here named exactly by its kind:

| file                | plays on            | good source sample            |
|---------------------|---------------------|-------------------------------|
| `assets/sfx/boom.wav`   | freeze / climax hit | "vine boom", deep sub-bass hit |
| `assets/sfx/ding.wav`   | reaction beat       | bright bell / "correct" ding   |
| `assets/sfx/whoosh.wav` | zoom punch / cut    | fast air whoosh / transition   |
| `assets/sfx/riser.wav`  | into the drop       | uplifter / riser sweep         |

`.wav`, `.mp3`, `.ogg`, `.m4a` all work; the engine trims each to a short sting and
applies a gentle out-fade, so a longer source is fine. If a file is present it OVERRIDES
the synthesized fallback automatically — no code or env change.

## Where to get CC0 samples
- **Pixabay Sound Effects** (pixabay.com/sound-effects/) — CC0-like Pixabay License, no attribution.
- **Mixkit Free SFX** (mixkit.co/free-sound-effects/) — free for commercial use, no attribution.

Search terms: `vine boom`, `bass hit`, `bell ding`, `whoosh transition`, `riser uplifter`.

## Note for production (VM)
`assets/bgm/` and this dir are not the git-deployed path on their own — a new audio file
must also reach the VM (the render host) the same way the existing BGM library got there.
After dropping a file locally, mirror it to the VM before the next batch renders.

The same convention applies to **grammar music**: drop `assets/bgm/velocity_music.mp3`
(or `meme_music` / `story_music`) and that grammar uses it with no code edit — this is
how a real club/EDM banger gets under `velocity` (the 93-track library has none).
