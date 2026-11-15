use std::collections::VecDeque;
use std::io::{self, ErrorKind, Read, Seek, SeekFrom};
use std::thread;
use std::time::Duration;

use bytes::{Buf, Bytes};
use reqwest::Url;
use symphonia::core::io::MediaSource;

use crate::voice::source::http::{Fetcher, HttpMedia};

const LIVE_EDGE: usize = 3;
const PACKET: usize = 188;
const SYNC: u8 = 0x47;
const AUDIO_STREAMS: [u8; 3] = [0x0F, 0x03, 0x04];

pub(crate) struct HlsMedia {
    fetcher: Fetcher,
    playlist: Url,
    map: Option<Url>,
    segments: VecDeque<(u64, Url)>,
    next: u64,
    target: Duration,
    ended: bool,
    current: Bytes,
    demux: Demux,
}

struct MediaPlaylist {
    target: Duration,
    map: Option<Url>,
    segments: Vec<(u64, Url)>,
    ended: bool,
}

impl HlsMedia {
    pub(crate) fn open(mut media: HttpMedia) -> io::Result<Self> {
        let mut text = String::new();
        media.read_to_string(&mut text)?;
        let (fetcher, mut playlist) = media.into_fetcher();
        if let Some(chosen) = choose_variant(&text, &playlist)? {
            (text, playlist) = fetch_text(&fetcher, &chosen)?;
        }
        let parsed = parse_media(&text, &playlist)?;
        let skip = if parsed.ended {
            0
        } else {
            parsed.segments.len().saturating_sub(LIVE_EDGE)
        };
        let segments: VecDeque<_> = parsed.segments.into_iter().skip(skip).collect();
        Ok(Self {
            next: segments.front().map_or(0, |&(sequence, _)| sequence),
            fetcher,
            playlist,
            map: parsed.map,
            segments,
            target: parsed.target,
            ended: parsed.ended,
            current: Bytes::new(),
            demux: Demux::default(),
        })
    }

    fn refill(&mut self) -> io::Result<bool> {
        if let Some(map) = self.map.take() {
            self.current = self.fetcher.get(&map)?.0;
            return Ok(true);
        }
        loop {
            if let Some((sequence, url)) = self.segments.pop_front() {
                self.next = sequence + 1;
                let (segment, _) = self.fetcher.get(&url)?;
                self.current = self.demux.elementary(segment);
                return Ok(true);
            }
            if self.ended {
                return Ok(false);
            }
            thread::sleep(self.target / 2);
            let (text, location) = fetch_text(&self.fetcher, &self.playlist)?;
            let parsed = parse_media(&text, &location)?;
            self.playlist = location;
            self.target = parsed.target;
            self.ended = parsed.ended;
            let next = self.next;
            self.segments.extend(
                parsed
                    .segments
                    .into_iter()
                    .filter(|&(sequence, _)| sequence >= next),
            );
        }
    }
}

impl Read for HlsMedia {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        while self.current.is_empty() {
            if buf.is_empty() || !self.refill()? {
                return Ok(0);
            }
        }
        let count = buf.len().min(self.current.len());
        self.current
            .copy_to_slice(buf.get_mut(..count).unwrap_or_default());
        Ok(count)
    }
}

impl Seek for HlsMedia {
    fn seek(&mut self, _: SeekFrom) -> io::Result<u64> {
        Err(io::Error::new(
            ErrorKind::Unsupported,
            "an HLS stream cannot seek",
        ))
    }
}

impl MediaSource for HlsMedia {
    fn is_seekable(&self) -> bool {
        false
    }

    fn byte_len(&self) -> Option<u64> {
        None
    }
}

fn fetch_text(fetcher: &Fetcher, url: &Url) -> io::Result<(String, Url)> {
    let (body, location) = fetcher.get(url)?;
    let text = String::from_utf8(body.to_vec())
        .map_err(|error| io::Error::new(ErrorKind::InvalidData, error))?;
    Ok((text, location))
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(ErrorKind::InvalidData, message.to_owned())
}

fn join(base: &Url, uri: &str) -> io::Result<Url> {
    base.join(uri)
        .map_err(|error| invalid(&format!("the HLS playlist names an invalid URI: {error}")))
}

fn choose_variant(text: &str, base: &Url) -> io::Result<Option<Url>> {
    let mut audio: Option<(bool, String)> = None;
    let mut variants: Vec<(u64, bool, String)> = Vec::new();
    let mut pending: Option<(u64, bool)> = None;
    for line in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
        if let Some(attributes) = line.strip_prefix("#EXT-X-MEDIA:") {
            let attributes = attributes_of(attributes);
            let value = |name: &str| {
                attributes
                    .iter()
                    .find(|(key, _)| *key == name)
                    .map(|(_, value)| value.as_str())
            };
            if let (Some("AUDIO"), Some(uri)) = (value("TYPE"), value("URI")) {
                let default = value("DEFAULT") == Some("YES");
                if audio.as_ref().is_none_or(|(chosen, _)| default && !chosen) {
                    audio = Some((default, uri.to_owned()));
                }
            }
        } else if let Some(attributes) = line.strip_prefix("#EXT-X-STREAM-INF:") {
            let attributes = attributes_of(attributes);
            let bandwidth = attributes
                .iter()
                .find(|(key, _)| key == "BANDWIDTH")
                .and_then(|(_, value)| value.parse().ok())
                .unwrap_or(0);
            let audio_only = attributes
                .iter()
                .find(|(key, _)| key == "CODECS")
                .is_some_and(|(_, codecs)| codecs.split(',').all(|codec| !is_video(codec.trim())));
            pending = Some((bandwidth, audio_only));
        } else if !line.starts_with('#')
            && let Some((bandwidth, audio_only)) = pending.take()
        {
            variants.push((bandwidth, audio_only, line.to_owned()));
        }
    }
    if let Some((_, uri)) = audio {
        return join(base, &uri).map(Some);
    }
    let best = variants
        .iter()
        .filter(|(_, audio_only, _)| *audio_only)
        .max_by_key(|(bandwidth, _, _)| *bandwidth)
        .or_else(|| variants.iter().min_by_key(|(bandwidth, _, _)| *bandwidth));
    best.map(|(_, _, uri)| join(base, uri)).transpose()
}

fn is_video(codec: &str) -> bool {
    ["avc", "hvc", "hev", "vp0", "vp8", "vp9", "av01"]
        .iter()
        .any(|prefix| codec.starts_with(prefix))
}

fn parse_media(text: &str, base: &Url) -> io::Result<MediaPlaylist> {
    if !text.trim_start().starts_with("#EXTM3U") {
        return Err(invalid("the HLS playlist does not start with #EXTM3U"));
    }
    let mut playlist = MediaPlaylist {
        target: Duration::from_secs(6),
        map: None,
        segments: Vec::new(),
        ended: false,
    };
    let mut sequence = 0_u64;
    for line in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
        if let Some(value) = line.strip_prefix("#EXT-X-TARGETDURATION:") {
            let seconds = value.trim().parse::<f64>().unwrap_or(6.0);
            playlist.target = Duration::try_from_secs_f64(seconds).unwrap_or(playlist.target);
        } else if let Some(value) = line.strip_prefix("#EXT-X-MEDIA-SEQUENCE:") {
            sequence = value.trim().parse().unwrap_or(0);
        } else if line == "#EXT-X-ENDLIST" {
            playlist.ended = true;
        } else if let Some(attributes) = line.strip_prefix("#EXT-X-MAP:") {
            let uri = attributes_of(attributes)
                .into_iter()
                .find(|(key, _)| key == "URI")
                .map(|(_, uri)| uri)
                .ok_or_else(|| invalid("an HLS #EXT-X-MAP has no URI"))?;
            playlist.map = Some(join(base, &uri)?);
        } else if let Some(attributes) = line.strip_prefix("#EXT-X-KEY:") {
            let encrypted = attributes_of(attributes)
                .iter()
                .any(|(key, method)| key == "METHOD" && method != "NONE");
            if encrypted {
                return Err(invalid("encrypted HLS streams are not supported"));
            }
        } else if line.starts_with("#EXT-X-BYTERANGE") {
            return Err(invalid("HLS byte ranges are not supported"));
        } else if !line.starts_with('#') {
            playlist.segments.push((sequence, join(base, line)?));
            sequence += 1;
        }
    }
    Ok(playlist)
}

fn attributes_of(list: &str) -> Vec<(String, String)> {
    let mut attributes = Vec::new();
    let mut rest = list.trim();
    while let Some((key, after)) = rest.split_once('=') {
        let (value, remaining) = match after.strip_prefix('"') {
            Some(quoted) => match quoted.split_once('"') {
                Some((value, tail)) => (value, tail),
                None => (quoted, ""),
            },
            None => after.split_once(',').unwrap_or((after, "")),
        };
        attributes.push((key.trim().to_owned(), value.to_owned()));
        rest = remaining.trim_start_matches(',').trim_start();
    }
    attributes
}

#[derive(Default)]
struct Demux {
    pmt: Option<u16>,
    audio: Option<u16>,
}

impl Demux {
    fn elementary(&mut self, segment: Bytes) -> Bytes {
        if segment.first() != Some(&SYNC) {
            return skip_id3(segment);
        }
        let (packets, _) = segment.as_chunks::<PACKET>();
        let mut out = Vec::with_capacity(segment.len());
        for packet in packets {
            self.packet(packet, &mut out);
        }
        Bytes::from(out)
    }

    fn packet(&mut self, packet: &[u8; PACKET], out: &mut Vec<u8>) {
        let [SYNC, flags, low, control, ..] = *packet else {
            return;
        };
        let start = flags & 0x40 != 0;
        let pid = u16::from(flags & 0x1F) << 8 | u16::from(low);
        let mut payload = packet.get(4..).unwrap_or_default();
        if control & 0x20 != 0 {
            let skip = payload.first().map_or(0, |&length| usize::from(length) + 1);
            payload = payload.get(skip..).unwrap_or_default();
        }
        if control & 0x10 == 0 {
            return;
        }
        if pid == 0 && start {
            self.pmt = section(payload).and_then(program_map);
        } else if Some(pid) == self.pmt && start {
            self.audio = section(payload).and_then(audio_stream);
        } else if Some(pid) == self.audio {
            if start {
                let header = payload
                    .get(8)
                    .map_or(payload.len(), |&length| 9 + usize::from(length));
                payload = payload.get(header..).unwrap_or_default();
            }
            out.extend_from_slice(payload);
        }
    }
}

fn section(payload: &[u8]) -> Option<&[u8]> {
    let pointer = usize::from(*payload.first()?);
    let table = payload.get(1 + pointer..)?;
    let length = usize::from(u16::from_be_bytes([*table.get(1)?, *table.get(2)?]) & 0x0FFF);
    table.get(8..(3 + length).saturating_sub(4))
}

fn program_map(programs: &[u8]) -> Option<u16> {
    let (entries, _) = programs.as_chunks::<4>();
    entries
        .iter()
        .find(|[high, low, _, _]| u16::from_be_bytes([*high, *low]) != 0)
        .map(|[_, _, high, low]| u16::from_be_bytes([*high, *low]) & 0x1FFF)
}

fn audio_stream(table: &[u8]) -> Option<u16> {
    let info = usize::from(u16::from_be_bytes([*table.get(2)?, *table.get(3)?]) & 0x0FFF);
    let mut streams = table.get(4 + info..)?;
    while let [kind, pid_high, pid_low, info_high, info_low, rest @ ..] = streams {
        if AUDIO_STREAMS.contains(kind) {
            return Some(u16::from_be_bytes([*pid_high, *pid_low]) & 0x1FFF);
        }
        let skip = usize::from(u16::from_be_bytes([*info_high, *info_low]) & 0x0FFF);
        streams = rest.get(skip..)?;
    }
    None
}

fn skip_id3(mut segment: Bytes) -> Bytes {
    if let [b'I', b'D', b'3', _, _, flags, a, b, c, d, ..] = *segment {
        let size = [a, b, c, d]
            .iter()
            .fold(0_usize, |size, byte| size << 7 | usize::from(byte & 0x7F));
        let footer = if flags & 0x10 == 0 { 0 } else { 10 };
        segment.advance((10 + size + footer).min(segment.len()));
    }
    segment
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::io::Write;
    use std::net::{TcpListener, TcpStream};
    use std::sync::{Arc, Mutex};

    use super::*;
    use crate::voice::source::SourceKind;
    use crate::voice::source::tests::{Harness, fixture, frames, read_to_end};
    use crate::voice::types::SourceSpec;

    type Pages = Arc<Mutex<HashMap<String, VecDeque<Vec<u8>>>>>;
    type ByPath = Arc<Mutex<HashMap<String, String>>>;

    struct Site {
        base: String,
        pages: Pages,
        moved: ByPath,
        types: ByPath,
    }

    impl Site {
        fn start() -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let base = format!("http://{}", listener.local_addr().unwrap());
            let pages = Pages::default();
            let moved = ByPath::default();
            let types = ByPath::default();
            let served = Arc::clone(&pages);
            let redirects = Arc::clone(&moved);
            let typed = Arc::clone(&types);
            thread::spawn(move || {
                for stream in listener.incoming() {
                    let Ok(stream) = stream else { return };
                    let pages = Arc::clone(&served);
                    let moved = Arc::clone(&redirects);
                    let types = Arc::clone(&typed);
                    thread::spawn(move || answer(stream, &pages, &moved, &types));
                }
            });
            Self {
                base,
                pages,
                moved,
                types,
            }
        }

        fn typed(&self, path: &str, mime: &str) {
            self.types
                .lock()
                .unwrap()
                .insert(path.to_owned(), mime.to_owned());
        }

        fn redirect(&self, from: &str, to: &str) {
            self.moved
                .lock()
                .unwrap()
                .insert(from.to_owned(), to.to_owned());
        }

        fn page(&self, path: &str, bodies: &[&[u8]]) {
            let bodies = bodies.iter().map(|body| body.to_vec()).collect();
            self.pages.lock().unwrap().insert(path.to_owned(), bodies);
        }

        fn play(&self, path: &str) -> (Vec<Bytes>, Option<String>) {
            let mut harness = Harness::new();
            let source = harness.open(SourceSpec::Url {
                url: format!("{}{path}", self.base),
                headers: Vec::new(),
            });
            read_to_end(&source)
        }
    }

    fn answer(mut stream: TcpStream, pages: &Pages, moved: &ByPath, types: &ByPath) {
        let mut head = Vec::new();
        let mut byte = [0; 1];
        while !head.ends_with(b"\r\n\r\n") && stream.read(&mut byte).unwrap_or(0) == 1 {
            head.push(byte[0]);
        }
        let head = String::from_utf8_lossy(&head);
        let path = head
            .split_whitespace()
            .nth(1)
            .unwrap_or_default()
            .to_owned();
        if let Some(to) = moved.lock().unwrap().get(&path) {
            let response = format!(
                "HTTP/1.1 302 Found\r\nlocation: {to}\r\ncontent-length: 0\r\nconnection: close\r\n\r\n"
            );
            drop(stream.write_all(response.as_bytes()));
            return;
        }
        let body = pages.lock().unwrap().get_mut(&path).map(|bodies| {
            if bodies.len() > 1 {
                bodies.pop_front().unwrap()
            } else {
                bodies[0].clone()
            }
        });
        let mime = types
            .lock()
            .unwrap()
            .get(&path)
            .map(|mime| format!("content-type: {mime}\r\n"))
            .unwrap_or_default();
        let response = match body {
            Some(body) => [
                format!(
                    "HTTP/1.1 200 OK\r\n{mime}content-length: {}\r\nconnection: close\r\n\r\n",
                    body.len()
                )
                .into_bytes(),
                body,
            ]
            .concat(),
            None => b"HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\n\r\n".to_vec(),
        };
        drop(stream.write_all(&response));
    }

    fn tone_ts() -> Vec<u8> {
        std::fs::read(fixture("tone.ts")).unwrap()
    }

    fn seconds(chunks: &[Bytes]) -> f64 {
        #[expect(clippy::cast_precision_loss)]
        let frames = frames(SourceKind::Pcm, chunks) as f64;
        frames / 48_000.0
    }

    #[test]
    fn a_master_playlist_plays_its_audio_variant_segment_after_segment() {
        let site = Site::start();
        let master = "#EXTM3U\n\
            #EXT-X-STREAM-INF:BANDWIDTH=900000,CODECS=\"avc1.4d401f,mp4a.40.2\"\n\
            video.m3u8\n\
            #EXT-X-STREAM-INF:BANDWIDTH=48000,CODECS=\"mp4a.40.2\"\n\
            audio/index.m3u8\n";
        let media = "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1.0,\na.ts\n#EXTINF:1.0,\nb.ts\n\
            #EXT-X-ENDLIST\n";
        site.page("/master.m3u8", &[master.as_bytes()]);
        site.page("/audio/index.m3u8", &[media.as_bytes()]);
        site.page("/audio/a.ts", &[&tone_ts()]);
        site.page("/audio/b.ts", &[&tone_ts()]);

        let (chunks, error) = site.play("/master.m3u8");

        assert_eq!(error, None);
        assert!((seconds(&chunks) - 2.0).abs() < 0.1, "{}", seconds(&chunks));
    }

    #[test]
    fn playlists_resolve_their_uris_from_where_a_redirect_took_them() {
        let site = Site::start();
        let master =
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=48000,CODECS=\"mp4a.40.2\"\naudio.m3u8\n";
        let media = "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1.0,\na.ts\n#EXT-X-ENDLIST\n";
        site.redirect("/radio", "/hls/master.m3u8");
        site.page("/hls/master.m3u8", &[master.as_bytes()]);
        site.redirect("/hls/audio.m3u8", "/cdn/audio.m3u8");
        site.page("/cdn/audio.m3u8", &[media.as_bytes()]);
        site.page("/cdn/a.ts", &[&tone_ts()]);

        let (chunks, error) = site.play("/radio");

        assert_eq!(error, None);
        assert!((seconds(&chunks) - 1.0).abs() < 0.1, "{}", seconds(&chunks));
    }

    #[test]
    fn a_live_playlist_reloads_for_new_segments_until_it_ends() {
        let site = Site::start();
        let first = "#EXTM3U\n#EXT-X-TARGETDURATION:0\n#EXT-X-MEDIA-SEQUENCE:7\n#EXTINF:1.0,\n\
            7.ts\n";
        let later = "#EXTM3U\n#EXT-X-TARGETDURATION:0\n#EXT-X-MEDIA-SEQUENCE:7\n#EXTINF:1.0,\n\
            7.ts\n#EXTINF:1.0,\n8.ts\n#EXT-X-ENDLIST\n";
        site.page("/live.m3u8", &[first.as_bytes(), later.as_bytes()]);
        site.page("/7.ts", &[&tone_ts()]);
        site.page("/8.ts", &[&tone_ts()]);

        let (chunks, error) = site.play("/live.m3u8");

        assert_eq!(error, None);
        assert!((seconds(&chunks) - 2.0).abs() < 0.1, "{}", seconds(&chunks));
    }

    #[test]
    fn an_encrypted_stream_is_refused_by_name() {
        let site = Site::start();
        let media = "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key\"\n#EXTINF:1.0,\na.ts\n";
        site.page("/locked.m3u8", &[media.as_bytes()]);

        let (_, error) = site.play("/locked.m3u8");

        assert_eq!(
            error.as_deref(),
            Some("encrypted HLS streams are not supported")
        );
    }

    fn base() -> Url {
        Url::parse("https://cdn.example/radio/index.m3u8").unwrap()
    }

    #[test]
    fn a_master_playlist_picks_the_default_audio_then_the_best_audio_variant() {
        let audio = "#EXTM3U\n\
            #EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"a\",NAME=\"en\",DEFAULT=NO,URI=\"en.m3u8\"\n\
            #EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"a\",NAME=\"main\",DEFAULT=YES,URI=\"main.m3u8\"\n\
            #EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"a\",NAME=\"fr\",DEFAULT=NO,URI=\"fr.m3u8\"\n\
            #EXT-X-STREAM-INF:BANDWIDTH=900000,AUDIO=\"a\"\nvideo.m3u8\n";
        let undecided = "#EXTM3U\n\
            #EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"a\",NAME=\"en\",URI=\"en.m3u8\"\n\
            #EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"a\",NAME=\"fr\",URI=\"fr.m3u8\"\n";
        let muxed = "#EXTM3U\n\
            #EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"a\",NAME=\"main\",DEFAULT=YES\n\
            #EXT-X-STREAM-INF:BANDWIDTH=900000,CODECS=\"avc1.4d401f,mp4a.40.2\"\nhd.m3u8\n\
            #EXT-X-STREAM-INF:BANDWIDTH=48000,CODECS=\"mp4a.40.2\"\nlow.m3u8\n\
            #EXT-X-STREAM-INF:BANDWIDTH=96000,CODECS=\"mp4a.40.2\"\nhigh.m3u8\n";
        let video = "#EXTM3U\n\
            #EXT-X-STREAM-INF:BANDWIDTH=900000,CODECS=\"avc1.4d401f,mp4a.40.2\"\nhd.m3u8\n\
            #EXT-X-STREAM-INF:BANDWIDTH=300000,CODECS=\"avc1.4d401f,mp4a.40.2\"\nsd.m3u8\n";
        let media = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\na.ts\n";
        for (text, chosen) in [
            (audio, Some("main.m3u8")),
            (undecided, Some("en.m3u8")),
            (muxed, Some("high.m3u8")),
            (video, Some("sd.m3u8")),
            (media, None),
        ] {
            let chosen = chosen.map(|uri| base().join(uri).unwrap());
            assert_eq!(choose_variant(text, &base()).unwrap(), chosen, "{text}");
        }
    }

    #[test]
    fn a_media_playlist_reads_its_segments_init_section_and_end() {
        let vod = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:10\n\
            #EXT-X-MAP:URI=\"init.mp4\"\n#EXT-X-KEY:METHOD=NONE\n\
            #EXTINF:4.0,\na.m4s\n#EXTINF:4.0,\n/other/b.m4s\n#EXT-X-ENDLIST\n";
        let live = "#EXTM3U\n#EXTINF:6.0,\nlive.ts\n";

        let parsed = parse_media(vod, &base()).unwrap();
        let open = parse_media(live, &base()).unwrap();

        assert_eq!(parsed.target, Duration::from_secs(4));
        assert_eq!(parsed.map, Some(base().join("init.mp4").unwrap()));
        assert_eq!(
            parsed.segments,
            [
                (10, base().join("a.m4s").unwrap()),
                (11, base().join("/other/b.m4s").unwrap())
            ]
        );
        assert!(parsed.ended);
        assert_eq!(
            (open.target, open.map, open.ended),
            (Duration::from_secs(6), None, false)
        );
        assert_eq!(open.segments, [(0, base().join("live.ts").unwrap())]);
    }

    #[test]
    fn a_media_playlist_bostie_cannot_play_is_refused_by_name() {
        for (text, refusal) in [
            (
                "#EXTINF:6.0,\na.ts\n",
                "the HLS playlist does not start with #EXTM3U",
            ),
            (
                "#EXTM3U\n#EXT-X-MAP:BYTERANGE=\"720@0\"\n",
                "an HLS #EXT-X-MAP has no URI",
            ),
            (
                "#EXTM3U\n#EXTINF:6.0,\n#EXT-X-BYTERANGE:1000@0\nall.ts\n",
                "HLS byte ranges are not supported",
            ),
        ] {
            let error = parse_media(text, &base()).err().unwrap();
            assert_eq!(error.to_string(), refusal, "{text}");
        }
    }

    #[test]
    fn packed_audio_drops_its_id3_tag_and_any_footer() {
        let tag = |flags: u8| [&b"ID3\x04\x00"[..], &[flags, 0, 0, 1, 2], &[7; 130]].concat();
        let audio = b"\xff\xf1aac";
        for segment in [
            [tag(0), audio.to_vec()].concat(),
            [tag(0x10), vec![9; 10], audio.to_vec()].concat(),
            audio.to_vec(),
        ] {
            let mut demux = Demux::default();
            assert_eq!(demux.elementary(Bytes::from(segment)), &audio[..]);
        }
    }

    #[test]
    fn a_live_playlist_starts_three_segments_before_its_live_edge() {
        let site = Site::start();
        let live = "#EXTM3U\n#EXT-X-TARGETDURATION:0\n#EXTINF:1.0,\n0.ts\n#EXTINF:1.0,\n1.ts\n\
            #EXTINF:1.0,\n2.ts\n#EXTINF:1.0,\n3.ts\n#EXTINF:1.0,\n4.ts\n";
        let ended = format!("{live}#EXT-X-ENDLIST\n");
        site.page("/live.m3u8", &[live.as_bytes(), ended.as_bytes()]);
        for index in 0..5 {
            site.page(&format!("/{index}.ts"), &[&tone_ts()]);
        }

        let (chunks, error) = site.play("/live.m3u8");

        assert_eq!(error, None);
        assert!((seconds(&chunks) - 3.0).abs() < 0.1, "{}", seconds(&chunks));
    }

    #[test]
    fn a_playlist_is_spotted_by_its_content_type_alone() {
        let site = Site::start();
        let media = "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1.0,\na.ts\n#EXT-X-ENDLIST\n";
        site.page("/radio", &[media.as_bytes()]);
        site.typed("/radio", "application/vnd.apple.mpegurl");
        site.page("/a.ts", &[&tone_ts()]);

        let (chunks, error) = site.play("/radio");

        assert_eq!(error, None);
        assert!((seconds(&chunks) - 1.0).abs() < 0.1, "{}", seconds(&chunks));
    }
}
