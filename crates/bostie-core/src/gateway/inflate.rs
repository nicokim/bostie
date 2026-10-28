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

    fn message_create(seq: u64, content: &str) -> String {
        format!(r#"{{"t":"MESSAGE_CREATE","s":{seq},"op":0,"d":{{"content":"{content}"}}}}"#)
    }

    #[test]
    fn one_stream_spans_many_messages_of_any_size() {
        let mut compressor = discord_compressor();
        let mut inflater = Inflater::new(MAX_DECOMPRESSED).unwrap();
        let frames = [
            message_create(1, "small"),
            message_create(2, &"large".repeat(60_000)),
            message_create(3, "small again"),
        ];

        for frame in &frames {
            let message = compress(&mut compressor, frame.as_bytes());
            assert_eq!(inflater.inflate(&message).unwrap(), frame.as_bytes());
        }
    }

    #[test]
    fn rejects_messages_over_the_limit_and_garbage() {
        let mut compressor = discord_compressor();
        let mut inflater = Inflater::new(MAX_DECOMPRESSED).unwrap();
        let huge = message_create(1, &"z".repeat(MAX_DECOMPRESSED));

        assert!(matches!(
            inflater.inflate(&compress(&mut compressor, huge.as_bytes())),
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
