use std::os::raw::c_int;

use bytes::Bytes;
use opus::{Bitrate, Channels};

use crate::error::CoreError;
use crate::voice::convert::Converter;
use crate::voice::types::{
    Application, EncoderSettings, FRAME_LEN, FRAME_SAMPLES, PcmFormat, SAMPLE_RATE,
};

pub const MAX_PACKET: usize = 1_275;

pub fn packet_samples(packet: &[u8]) -> Option<u32> {
    if !fits_libopus(packet) {
        return None;
    }
    opus::packet::parse(packet).ok()?;
    let samples = opus::packet::get_nb_samples(packet, SAMPLE_RATE).ok()?;
    u32::try_from(samples).ok().filter(|&samples| samples > 0)
}

pub fn is_stereo_20ms(packet: &[u8]) -> bool {
    fits_libopus(packet)
        && matches!(opus::packet::get_nb_channels(packet), Ok(Channels::Stereo))
        && matches!(
            opus::packet::get_nb_samples(packet, SAMPLE_RATE),
            Ok(FRAME_SAMPLES)
        )
}

fn fits_libopus(packet: &[u8]) -> bool {
    c_int::try_from(packet.len()).is_ok()
}

pub struct Encoder {
    opus: opus::Encoder,
    settings: EncoderSettings,
    packet: [u8; MAX_PACKET],
}

impl Encoder {
    pub fn new(settings: EncoderSettings) -> Result<Self, CoreError> {
        settings.validate()?;
        let application = match settings.application {
            Application::Audio => opus::Application::Audio,
            Application::Voip => opus::Application::Voip,
            Application::LowDelay => opus::Application::LowDelay,
        };
        let mut opus =
            opus::Encoder::new(SAMPLE_RATE, Channels::Stereo, application).map_err(rejected)?;
        configure(&mut opus, &settings)?;
        Ok(Self {
            opus,
            settings,
            packet: [0; MAX_PACKET],
        })
    }

    pub fn settings(&self) -> EncoderSettings {
        self.settings
    }

    pub fn set(&mut self, settings: EncoderSettings) -> Result<(), CoreError> {
        if settings.application != self.settings.application {
            *self = Self::new(settings)?;
            return Ok(());
        }
        settings.validate()?;
        configure(&mut self.opus, &settings)?;
        self.settings = settings;
        Ok(())
    }

    pub fn encode(&mut self, frame: &[f32]) -> Option<&[u8]> {
        match self.opus.encode_float(frame, &mut self.packet) {
            Ok(length) => self.packet.get(..length),
            Err(error) => {
                tracing::debug!(%error, "opus could not encode a frame");
                None
            }
        }
    }

    pub fn reset(&mut self) {
        if let Err(error) = self.opus.reset_state() {
            tracing::debug!(%error, "opus could not reset the encoder");
        }
    }
}

fn configure(opus: &mut opus::Encoder, settings: &EncoderSettings) -> Result<(), CoreError> {
    let bitrate = i32::try_from(settings.bitrate).unwrap_or(i32::MAX);
    opus.set_bitrate(Bitrate::Bits(bitrate)).map_err(rejected)?;
    opus.set_inband_fec(settings.fec).map_err(rejected)?;
    opus.set_packet_loss_perc(i32::from(settings.packet_loss))
        .map_err(rejected)?;
    opus.set_complexity(i32::from(settings.complexity))
        .map_err(rejected)
}

fn rejected(error: opus::Error) -> CoreError {
    CoreError::InvalidRequest(format!("libopus rejected the encoder settings: {error}"))
}

pub struct FrameEncoder {
    converter: Converter,
    encoder: Encoder,
    pcm: Vec<f32>,
}

impl FrameEncoder {
    pub fn new(format: PcmFormat, settings: EncoderSettings) -> Result<Self, CoreError> {
        Ok(Self {
            converter: Converter::new(format)?,
            encoder: Encoder::new(settings)?,
            pcm: Vec::new(),
        })
    }

    pub fn encode(&mut self, data: &[u8]) -> Vec<Bytes> {
        self.converter.push(data, &mut self.pcm);
        self.packets()
    }

    pub fn flush(&mut self) -> Vec<Bytes> {
        self.converter.flush(&mut self.pcm);
        let partial = self.pcm.len() % FRAME_LEN;
        if partial > 0 {
            self.pcm.resize(self.pcm.len() + FRAME_LEN - partial, 0.0);
        }
        self.packets()
    }

    fn packets(&mut self) -> Vec<Bytes> {
        let (frames, _) = self.pcm.as_chunks::<FRAME_LEN>();
        let packets = frames
            .iter()
            .filter_map(|frame| self.encoder.encode(frame).map(Bytes::copy_from_slice))
            .collect();
        let used = frames.len() * FRAME_LEN;
        self.pcm.drain(..used);
        packets
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use std::f32::consts::TAU;

    use super::*;
    use crate::voice::types::SampleFormat;

    pub(crate) fn settings() -> EncoderSettings {
        EncoderSettings {
            bitrate: 64_000,
            application: Application::Audio,
            fec: true,
            packet_loss: 10,
            complexity: 10,
        }
    }

    pub(crate) fn packet(channels: Channels, frames: usize) -> Vec<u8> {
        let mut encoder =
            opus::Encoder::new(SAMPLE_RATE, channels, opus::Application::Audio).unwrap();
        let input: Vec<f32> = (0..frames)
            .flat_map(|frame| {
                let sample = (TAU * 440.0 * frame as f32 / SAMPLE_RATE as f32).sin() * 0.5;
                let right = if channels == Channels::Stereo {
                    -sample
                } else {
                    sample
                };
                [sample, right].into_iter().take(channels as usize)
            })
            .collect();
        encoder.encode_vec_float(&input, 4_000).unwrap()
    }

    #[test]
    fn packet_samples_comes_from_libopus_parsing() {
        assert_eq!(packet_samples(&packet(Channels::Stereo, 960)), Some(960));
        assert_eq!(packet_samples(&packet(Channels::Mono, 2_880)), Some(2_880));
        assert_eq!(packet_samples(&[]), None);
        assert_eq!(packet_samples(&[0x03]), None);
        assert_eq!(
            packet_samples(&[[0x00].as_slice(), &[0; 1_276]].concat()),
            None
        );
    }

    #[test]
    fn only_stereo_20_ms_packets_are_passthrough_shaped() {
        assert!(is_stereo_20ms(&packet(Channels::Stereo, 960)));
        assert!(!is_stereo_20ms(&packet(Channels::Mono, 960)));
        assert!(!is_stereo_20ms(&packet(Channels::Stereo, 2_880)));
        assert!(!is_stereo_20ms(&packet(Channels::Stereo, 480)));
        assert!(!is_stereo_20ms(&[]));
    }

    #[test]
    fn encoder_settings_reach_libopus() {
        let settings = EncoderSettings {
            bitrate: 96_000,
            application: Application::Voip,
            fec: false,
            packet_loss: 15,
            complexity: 5,
        };
        let mut encoder = Encoder::new(settings).unwrap();

        assert_eq!(encoder.opus.get_bitrate().unwrap(), Bitrate::Bits(96_000));
        assert_eq!(
            encoder.opus.get_application().unwrap(),
            opus::Application::Voip
        );
        assert!(!encoder.opus.get_inband_fec().unwrap());
        assert_eq!(encoder.opus.get_packet_loss_perc().unwrap(), 15);
        assert_eq!(encoder.opus.get_complexity().unwrap(), 5);
    }

    #[test]
    fn changing_the_application_rebuilds_the_encoder() {
        let mut encoder = Encoder::new(settings()).unwrap();
        encoder.encode(&[0.1; FRAME_LEN]).unwrap();

        encoder
            .set(EncoderSettings {
                application: Application::LowDelay,
                bitrate: 128_000,
                ..settings()
            })
            .unwrap();

        assert_eq!(
            encoder.opus.get_application().unwrap(),
            opus::Application::LowDelay
        );
        assert_eq!(encoder.opus.get_bitrate().unwrap(), Bitrate::Bits(128_000));
        assert_eq!(encoder.settings().application, Application::LowDelay);
    }

    #[test]
    fn other_changes_keep_the_encoder_and_apply_in_place() {
        let mut encoder = Encoder::new(settings()).unwrap();

        encoder
            .set(EncoderSettings {
                bitrate: 32_000,
                complexity: 3,
                ..settings()
            })
            .unwrap();

        assert_eq!(encoder.opus.get_bitrate().unwrap(), Bitrate::Bits(32_000));
        assert_eq!(encoder.opus.get_complexity().unwrap(), 3);
        assert!(
            encoder
                .set(EncoderSettings {
                    bitrate: 1,
                    ..settings()
                })
                .is_err()
        );
        assert_eq!(encoder.settings().bitrate, 32_000);
    }

    #[test]
    fn frame_encoder_turns_24_khz_mono_into_20_ms_packets_and_pads_the_last_one() {
        let format = PcmFormat {
            rate: 24_000,
            channels: 1,
            sample: SampleFormat::S16Le,
        };
        let mut encoder = FrameEncoder::new(format, settings()).unwrap();
        let second: Vec<u8> = (0..24_000)
            .flat_map(|frame| {
                let sample = (TAU * 440.0 * frame as f32 / 24_000.0).sin() * 16_000.0;
                (sample as i16).to_le_bytes()
            })
            .collect();

        let mut packets: Vec<Bytes> = second
            .chunks(960)
            .flat_map(|chunk| encoder.encode(chunk))
            .collect();
        packets.extend(encoder.flush());

        assert_eq!(packets.len(), 50);
        assert!(
            packets
                .iter()
                .all(|packet| packet_samples(packet) == Some(960))
        );
        assert!(encoder.flush().is_empty());

        let mut tail = encoder.encode(&second[..480]);
        tail.extend(encoder.flush());
        let tail: Vec<Option<u32>> = tail.iter().map(|packet| packet_samples(packet)).collect();
        assert_eq!(tail, [Some(960)]);
    }

    #[test]
    fn frame_encoder_rejects_formats_a_sink_would_reject() {
        let format = PcmFormat {
            rate: 8_001,
            channels: 2,
            sample: SampleFormat::S16Le,
        };

        assert!(FrameEncoder::new(format, settings()).is_err());
    }
}
