use zstd_safe::{DCtx, InBuffer, OutBuffer};

const SCRATCH_KEEP: usize = 8 * 1024 * 1024;

#[derive(Debug, thiserror::Error)]
pub(crate) enum InflateError {
    #[error("zstd: {0}")]
    Zstd(&'static str),
    #[error("a decompressed message exceeds {0} bytes")]
    TooLarge(usize),
}

pub(crate) struct Inflater {
    context: DCtx<'static>,
    scratch: Vec<u8>,
    max_decompressed: usize,
}

impl Inflater {
    pub(crate) fn new(max_decompressed: usize) -> Option<Self> {
        Some(Self {
            context: DCtx::try_create()?,
            scratch: Vec::new(),
            max_decompressed,
        })
    }

    pub(crate) fn inflate(&mut self, message: &[u8]) -> Result<&[u8], InflateError> {
        self.scratch.clear();
        self.scratch.shrink_to(SCRATCH_KEEP);
        let mut input = InBuffer::around(message);
        loop {
            if self.scratch.len() == self.scratch.capacity() {
                self.scratch.reserve(DCtx::out_size());
            }
            let written = self.scratch.len();
            let mut output = OutBuffer::around_pos(&mut self.scratch, written);
            self.context
                .decompress_stream(&mut output, &mut input)
                .map_err(|code| InflateError::Zstd(zstd_safe::get_error_name(code)))?;
            let output_full = output.pos() == output.capacity();
            if self.scratch.len() > self.max_decompressed {
                return Err(InflateError::TooLarge(self.max_decompressed));
            }
            if input.pos() == message.len() && !output_full {
                return Ok(&self.scratch);
            }
        }
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use proptest::collection::vec;
    use proptest::prelude::*;
    use zstd_safe::zstd_sys::ZSTD_EndDirective;
    use zstd_safe::{CCtx, CParameter};

    use super::*;

    const MAX_DECOMPRESSED: usize = 1024 * 1024;

    pub(crate) fn discord_compressor() -> CCtx<'static> {
        let mut context = CCtx::try_create().unwrap();
        context
            .set_parameter(CParameter::CompressionLevel(6))
            .unwrap();
        context.set_parameter(CParameter::WindowLog(18)).unwrap();
        context
    }

    pub(crate) fn compress(context: &mut CCtx<'static>, frame: &[u8]) -> Vec<u8> {
        let mut compressed = Vec::with_capacity(zstd_safe::compress_bound(frame.len()));
        let mut input = InBuffer::around(frame);
        loop {
            let written = compressed.len();
            let mut output = OutBuffer::around_pos(&mut compressed, written);
            let unflushed = context
                .compress_stream2(&mut output, &mut input, ZSTD_EndDirective::ZSTD_e_flush)
                .unwrap();
            if unflushed == 0 && input.pos() == frame.len() {
                return compressed;
            }
            compressed.reserve(DCtx::out_size());
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(16))]

        #[test]
        fn one_stream_inflates_messages_of_any_size(
            messages in vec(vec(any::<u8>(), 0..300_000), 1..4),
        ) {
            let mut compressor = discord_compressor();
            let mut inflater = Inflater::new(MAX_DECOMPRESSED).unwrap();

            for message in &messages {
                let compressed = compress(&mut compressor, message);
                prop_assert_eq!(inflater.inflate(&compressed).unwrap(), &message[..]);
            }
        }

        #[test]
        fn garbage_is_an_error_not_a_panic(garbage in vec(any::<u8>(), 0..512)) {
            drop(Inflater::new(MAX_DECOMPRESSED).unwrap().inflate(&garbage));
        }
    }

    #[test]
    fn accepts_messages_up_to_the_limit_and_rejects_larger_or_garbage() {
        let mut compressor = discord_compressor();
        let mut inflater = Inflater::new(MAX_DECOMPRESSED).unwrap();
        let largest = vec![b'z'; MAX_DECOMPRESSED];

        let inflated = inflater.inflate(&compress(&mut compressor, &largest));
        assert_eq!(inflated.unwrap(), &largest[..]);
        let too_large = compress(&mut compressor, &[&largest[..], b"z"].concat());
        assert!(matches!(
            inflater.inflate(&too_large),
            Err(InflateError::TooLarge(MAX_DECOMPRESSED))
        ));
        assert!(matches!(
            Inflater::new(MAX_DECOMPRESSED)
                .unwrap()
                .inflate(b"definitely not zstd"),
            Err(InflateError::Zstd(_))
        ));
    }
}
