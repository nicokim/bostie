# Media fixtures

The decoder tests in `voice/source` play these files. Each one is one second long and synthesised by ffmpeg's `aevalsrc` filter, so no recording or third-party work is involved. The tone is 1 kHz at half amplitude.

| File | Content |
|---|---|
| `tone.opus` | Ogg Opus, 48 kHz stereo, 20 ms packets |
| `tone.webm` | WebM Opus, 48 kHz stereo |
| `tone.ogg` | Ogg Vorbis, 44.1 kHz stereo |
| `tone.flac` | FLAC, 22.05 kHz mono, 16 bit |
| `tone-s16.wav` | WAV, 16 kHz mono, signed 16 bit |
| `tone-f32.wav` | WAV, 8 kHz mono, 32-bit float |
| `tone.mp3` | MP3, 24 kHz mono, with an ID3v2.3 title tag |
| `tone-5.1.flac` | FLAC 5.1 at 8 kHz: the tone on the centre channel, a 50 Hz hum on the LFE channel, the other four silent |
| `tone-5.1.opus` | Ogg Opus 5.1 with the same channels, which a stereo Opus decoder cannot play |
| `tone-aac.mka` | Matroska with AAC, a codec the wheel has no decoder for |

To regenerate them, run:

```sh
sh crates/bostie-core/src/voice/testdata/media/generate.sh
```

The script needs ffmpeg built with libopus, libvorbis and libmp3lame; AAC uses ffmpeg's own encoder. The files were made with ffmpeg 7.1.1, and the bitexact flags make two runs of the same ffmpeg build produce identical bytes.
