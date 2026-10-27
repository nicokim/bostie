use std::io::{self, Write};

#[cfg(unix)]
mod platform {
    use std::io;
    use std::os::fd::{IntoRawFd, RawFd};
    use std::os::unix::net::UnixStream;

    pub type Stream = UnixStream;
    pub type RawHandle = RawFd;

    pub fn pair() -> io::Result<(Stream, Stream)> {
        UnixStream::pair()
    }

    pub fn into_raw(stream: Stream) -> RawHandle {
        stream.into_raw_fd()
    }
}

#[cfg(windows)]
mod platform {
    use std::io;
    use std::net::{Ipv4Addr, TcpListener, TcpStream};
    use std::os::windows::io::{IntoRawSocket, RawSocket};

    pub type Stream = TcpStream;
    pub type RawHandle = RawSocket;

    pub fn pair() -> io::Result<(Stream, Stream)> {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
        let ringer = TcpStream::connect(listener.local_addr()?)?;
        ringer.set_nodelay(true)?;
        loop {
            let (accepted, peer) = listener.accept()?;
            if peer == ringer.local_addr()? {
                return Ok((ringer, accepted));
            }
        }
    }

    pub fn into_raw(stream: Stream) -> RawHandle {
        stream.into_raw_socket()
    }
}

pub type RawBellHandle = platform::RawHandle;

pub(crate) struct Bell {
    ringer: platform::Stream,
}

pub struct BellReceiver {
    stream: platform::Stream,
}

pub(crate) fn pair() -> io::Result<(Bell, BellReceiver)> {
    let (ringer, stream) = platform::pair()?;
    ringer.set_nonblocking(true)?;
    Ok((Bell { ringer }, BellReceiver { stream }))
}

impl Bell {
    pub(crate) fn ring(&self) -> bool {
        loop {
            match (&self.ringer).write(&[1]) {
                Ok(_) => return true,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => return true,
                Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                Err(error) => {
                    tracing::debug!(%error, "bell ring failed");
                    return false;
                }
            }
        }
    }
}

impl BellReceiver {
    pub fn into_raw(self) -> RawBellHandle {
        platform::into_raw(self.stream)
    }

    #[cfg(test)]
    pub(crate) fn stream(&self) -> &platform::Stream {
        &self.stream
    }
}
