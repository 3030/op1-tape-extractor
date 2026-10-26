# OP-1 storage, preset and sample limits

Reference for both hardware generations. Sources are listed per row: **TE** = official
Teenage Engineering guide or product spec page, **TE support** = statement from TE support
quoted on op-forums, **community** = user testing reported on op-forums (treat as
approximate).

## Preset / patch limits

### OP-1 (original, 2011)

| Limit | Value | Source |
|---|---|---|
| Synthesis presets (FM, cluster, digital, dr wave, phase, pulse, string, voltage) | 100 | TE support, 2011 |
| Synth **sampler** patches | 42 | TE support, 2011 |
| Drum patches / kits | 42 | TE support, 2011 |

These totals are inclusive of factory content — the shipped presets count against the
number, so usable free slots are lower. That is why users hit a wall around ~25 sampler
patches in practice.

Presets live on the 512 MB internal NAND flash, exposed over USB disk mode:

```
/synth/user/     user synth patches + sampler patches (.aif)
/drum/user/      user drum kits (.aif)
/snapshot/       auto-saved snapshots (hold sound key 1-8 for 5 s)
/tape/           tape tracks
/album/          album renders
```

Patch files are AIFF with an OP-1 specific chunk carrying both a sound preview and the
synth/sampler parameter data. Filenames: max 10 characters, avoid uncommon symbols.

### OP-1 field (2022)

| Limit | Value | Source |
|---|---|---|
| User patches (synth + drum combined) | 500 | TE product spec |
| Internal memory | 256 MB | TE product spec |
| Total user sample storage | over 160 minutes | TE product spec |

The field replaces the original's per-category caps with one shared 500-patch pool plus a
rename function. No separate synth/sampler/drum ceilings are published, and users have
reported going well past the original's 42-sampler-patch limit.

## Sample length limits

| | OP-1 original | OP-1 field |
|---|---|---|
| Synth sampler | 6 s mono | ~6 s stereo (community) |
| Drum sampler | 12 s mono (TE) | 20 s stereo (TE) |

The field is stereo end to end, so a field sample of the same duration costs roughly twice
the bytes of an original one.

## Tape and album

| | OP-1 original | OP-1 field |
|---|---|---|
| Tapes | 1 | 8 swappable |
| Tracks per tape | 4 | 4 |
| Length per track | 6 min @ 44.1 kHz / 16-bit, normal speed | 6 min |
| Total tape time | 24 track-minutes | 192 track-minutes |
| Tape styles | 1 | 4 |
| Album | side A + side B, 6 min each | same |

Exported filenames on the original: `track_1.aif` … `track_4.aif` for tape, `sideA.aif` /
`sideB.aif` for album. Tape storage is allocated separately from sample/patch storage — a
tape occupies its full 6 minutes whether or not anything is recorded on it, so tape use
never eats into patch capacity.

## Notes for this tool

- Original OP-1 mounts as USB mass storage (disk mode); the field uses MTP, so file access
  is not a plain volume mount on every platform.
- All tape and patch audio is 44.1 kHz / 16-bit AIFF. Original is mono-source per track but
  files are written as AIFF; field is stereo.
- Because the original's caps count factory presets, any "free slots" calculation has to
  count files actually present in `/synth/user/` and `/drum/user/`, not assume 100/42/42
  are all available.

## Sources

- [OP-1 guide (original)](https://teenage.engineering/guides/op-1/original) — synthesizer mode, drum mode, tape mode, song rendering sections
- [OP-1 field guide](https://teenage.engineering/guides/op-1)
- [OP-1 field product page](https://teenage.engineering/products/op-1) — 256 MB, 160 min sample storage, 500 user patches, 8 tapes
- [op-forums: OP-1 patch limit numbers](https://op-forums.com/t/op-1-patch-limit-numbers/1596) — TE support's 100 / 42 / 42
- [op-forums: OP-1 field sampling and memory limitations](https://op-forums.com/t/op-1-field-sampling-and-memory-limitations/24888)
- [Wikipedia: Teenage Engineering OP-1](https://en.wikipedia.org/wiki/Teenage_Engineering_OP-1) — 512 MB NAND on the original
