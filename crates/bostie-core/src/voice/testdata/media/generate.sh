#!/bin/sh
set -eu
cd "$(dirname "$0")"

tone='0.5*sin(2*PI*1000*t)'
hum='0.5*sin(2*PI*50*t)'

render() {
    source=$1
    shift
    ffmpeg -hide_banner -loglevel error -y -f lavfi -i "aevalsrc=$source:d=1" \
        -map_metadata -1 -fflags +bitexact -flags:a +bitexact "$@"
}

render "$tone|$tone:s=48000" -c:a libopus -b:a 24k tone.opus
render "$tone|$tone:s=48000" -c:a libopus -b:a 24k tone.webm
render "$tone|$tone:s=44100" -c:a libvorbis -q:a 0 tone.ogg
render "$tone:s=22050" -c:a flac -sample_fmt s16 tone.flac
render "$tone:s=16000" -c:a pcm_s16le tone-s16.wav
render "$tone:s=8000" -c:a pcm_f32le tone-f32.wav
render "$tone:s=24000" -c:a libmp3lame -b:a 32k -id3v2_version 3 -metadata title=Tone tone.mp3
render "0|0|$tone|$hum|0|0:c=5.1:s=8000" -c:a flac -sample_fmt s16 tone-5.1.flac
render "0|0|$tone|$hum|0|0:c=5.1:s=48000" -c:a libopus -b:a 24k tone-5.1.opus
render "$tone:s=8000" -c:a aac -b:a 12k tone-aac.mka
