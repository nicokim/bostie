use std::any::Any;
use std::collections::{HashMap, HashSet, VecDeque};
use std::fmt;
use std::mem;
use std::num::NonZeroU16;
use std::panic::{self, AssertUnwindSafe};
use std::time::{Duration, Instant};

use bytes::Bytes;
use davey::errors::{
    DecryptError, DecryptorDecryptError, EncryptError, EncryptorEncryptError, ProcessCommitError,
    ProcessProposalsError, SetExternalSenderError,
};
use davey::{Codec, DaveSession, MediaType, ProposalsOperationType};
use tracing::{debug, error, warn};

use crate::ids::{ChannelId, UserId};
use crate::voice::types::{DaveOp, MAX_DAVE_PROTOCOL_VERSION, ProposalsOp, SILENCE_FRAME};

pub const DAVEY_VERSION: &str = "0.2.0-beta.1";

#[cfg(not(test))]
const DAVE_RECOVERY_LIMIT: usize = 5;
#[cfg(not(test))]
const DAVE_RECOVERY_WINDOW: Duration = Duration::from_secs(60);
#[cfg(test)]
const DAVE_RECOVERY_LIMIT: usize = 2;
#[cfg(test)]
const DAVE_RECOVERY_WINDOW: Duration = Duration::from_secs(1);

const PASSTHROUGH_EXPIRY_SECS: u32 = 10;
const VERIFICATION_CODE_DIGITS: u32 = 45;
const VERIFICATION_CODE_GROUP: u32 = 5;

const _: () = assert!(MAX_DAVE_PROTOCOL_VERSION == davey::DAVE_PROTOCOL_VERSION);
const _: () = {
    const fn assert_send<T: Send>() {}
    assert_send::<Dave>();
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outbound {
    KeyPackage(Vec<u8>),
    CommitWelcome(Vec<u8>),
    TransitionReady(u16),
    InvalidCommitWelcome(u16),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Executed {
    pub protocol_version: u16,
    pub epoch: Option<u64>,
    pub transition_id: u16,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DaveInfo {
    pub protocol_version: u16,
    pub epoch: Option<u64>,
    pub privacy_code: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct DaveCounters {
    pub key_packages: u64,
    pub commits: u64,
    pub welcomes: u64,
    pub transitions_executed: u64,
    pub recoveries: u64,
    pub dave_panics: u64,
    pub rejected_proposals: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum SendMode {
    Plain = 0,
    Encrypt = 1,
    Hold = 2,
    Discard = 3,
}

impl SendMode {
    pub const fn from_byte(byte: u8) -> Self {
        match byte {
            0 => Self::Plain,
            1 => Self::Encrypt,
            3 => Self::Discard,
            _ => Self::Hold,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EncryptFailure {
    NotReady,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecryptFailure {
    NoDecryptor,
    Unencrypted,
    MissingKey,
    InvalidNonce,
    MissingCryptor,
    Failed,
}

#[derive(Debug, thiserror::Error)]
pub enum DaveError {
    #[error("more than {DAVE_RECOVERY_LIMIT} DAVE recoveries within {DAVE_RECOVERY_WINDOW:?}")]
    RecoveryLimit,
    #[error("the DAVE session could not be built: {0}")]
    Session(String),
    #[error("no DAVE verification code: {0}")]
    Verification(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MlsCall {
    Create,
    ExternalSender,
    KeyPackage,
    Reinit,
    Proposals,
    Commit,
    Welcome,
    Prepare,
    Execute,
}

#[derive(Debug, thiserror::Error)]
enum Failure {
    #[error("there is no DAVE session")]
    NoSession,
    #[error("{0}")]
    Rejected(String),
    #[error("the call panicked")]
    Panicked,
}

#[cfg(test)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Fault {
    Panic,
    Error,
}

#[cfg(test)]
impl Fault {
    fn trigger<T>(self) -> Result<T, String> {
        match self {
            Self::Panic => panic!("injected DAVE panic"),
            Self::Error => Err("injected DAVE error".to_owned()),
        }
    }
}

#[derive(Debug)]
pub struct Dave {
    user: UserId,
    channel: ChannelId,
    version: NonZeroU16,
    session: Option<DaveSession>,
    protocol_version: u16,
    external_sender: Option<Bytes>,
    members: HashSet<UserId>,
    pending: HashMap<u16, u16>,
    last_transition: u16,
    recovering: bool,
    recoveries: VecDeque<Instant>,
    counters: DaveCounters,
    #[cfg(test)]
    fault: Option<(MlsCall, Fault)>,
}

impl Dave {
    pub fn new(user: UserId, channel: ChannelId) -> Self {
        Self {
            user,
            channel,
            version: NonZeroU16::MIN,
            session: None,
            protocol_version: 0,
            external_sender: None,
            members: HashSet::new(),
            pending: HashMap::new(),
            last_transition: 0,
            recovering: false,
            recoveries: VecDeque::new(),
            counters: DaveCounters::default(),
            #[cfg(test)]
            fault: None,
        }
    }

    pub fn start(
        &mut self,
        protocol_version: u16,
        out: &mut Vec<Outbound>,
    ) -> Result<(), DaveError> {
        self.protocol_version = protocol_version;
        match NonZeroU16::new(protocol_version) {
            Some(version) => {
                self.version = version;
                self.rebuild(out)
            }
            None => Ok(()),
        }
    }

    pub fn clients_connect(&mut self, users: &[UserId]) {
        let own = self.user;
        self.members
            .extend(users.iter().copied().filter(|&user| user != own));
    }

    pub fn client_disconnect(&mut self, user: UserId) {
        self.members.remove(&user);
    }

    pub fn handle(
        &mut self,
        op: DaveOp,
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<Option<Executed>, DaveError> {
        match op {
            DaveOp::PrepareTransition {
                transition_id,
                protocol_version,
            } => {
                self.last_transition = transition_id;
                if protocol_version == 0
                    && let Some(session) = &mut self.session
                {
                    session.set_passthrough_mode(true, None);
                }
                self.prepare(transition_id, protocol_version, now, out)
            }
            DaveOp::ExecuteTransition { transition_id } => self.execute(transition_id, now, out),
            DaveOp::PrepareEpoch {
                transition_id,
                epoch,
                protocol_version,
            } => self.prepare_epoch(transition_id, epoch, protocol_version, now, out),
            DaveOp::ExternalSender(sender) => {
                self.set_external_sender(sender, now, out)?;
                Ok(None)
            }
            DaveOp::Proposals { operation, data } => {
                self.process_proposals(operation, &data, now, out)?;
                Ok(None)
            }
            DaveOp::AnnounceCommit {
                transition_id,
                commit,
            } => self.process_commit(transition_id, &commit, now, out),
            DaveOp::Welcome {
                transition_id,
                welcome,
            } => self.process_welcome(transition_id, &welcome, now, out),
        }
    }

    pub fn send_mode(&self) -> SendMode {
        if self.protocol_version == 0 {
            SendMode::Plain
        } else if self.members.is_empty() {
            SendMode::Discard
        } else if !self.recovering && self.session.as_ref().is_some_and(DaveSession::can_encrypt) {
            SendMode::Encrypt
        } else {
            SendMode::Hold
        }
    }

    pub fn encrypt(&mut self, frame: &[u8], out: &mut Vec<u8>) -> Result<(), EncryptFailure> {
        if frame == SILENCE_FRAME {
            return Err(EncryptFailure::Failed);
        }
        let Some(session) = &mut self.session else {
            return Err(EncryptFailure::NotReady);
        };
        session
            .encrypt_into(MediaType::AUDIO, Codec::OPUS, frame, out)
            .map_err(|error| encrypt_failure(&error))
    }

    pub fn decrypt(
        &mut self,
        user: UserId,
        frame: &[u8],
        out: &mut [u8],
    ) -> Result<usize, DecryptFailure> {
        let Some(session) = &mut self.session else {
            return Err(DecryptFailure::NoDecryptor);
        };
        session
            .decrypt_into(u64::from(user), MediaType::AUDIO, frame, out)
            .map_err(|error| decrypt_failure(&error))
    }

    pub fn info(&self) -> DaveInfo {
        DaveInfo {
            protocol_version: self.protocol_version,
            epoch: self.epoch(),
            privacy_code: self
                .session
                .as_ref()
                .filter(|_| self.protocol_version > 0)
                .and_then(DaveSession::voice_privacy_code)
                .map(str::to_owned),
        }
    }

    pub fn counters(&self) -> DaveCounters {
        self.counters
    }

    pub fn fingerprint_pair(&self, user: UserId) -> Result<[Vec<u8>; 2], DaveError> {
        let session = self
            .session
            .as_ref()
            .ok_or_else(|| DaveError::Verification(Failure::NoSession.to_string()))?;
        session
            .get_key_fingerprint_pair(0, u64::from(user))
            .map_err(|error| DaveError::Verification(error.to_string()))
    }

    #[cfg(test)]
    pub fn inject(&mut self, call: MlsCall, fault: Fault) {
        self.fault = Some((call, fault));
    }

    #[cfg(test)]
    pub fn leaves(&self) -> Vec<(u32, UserId)> {
        let group = self.session.as_ref().and_then(DaveSession::group);
        group
            .map(|group| {
                group
                    .members()
                    .map(|member| {
                        let id: [u8; 8] =
                            member.credential.serialized_content().try_into().unwrap();
                        (member.index.u32(), UserId::from(u64::from_be_bytes(id)))
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    fn epoch(&self) -> Option<u64> {
        self.session
            .as_ref()
            .and_then(DaveSession::epoch)
            .map(|epoch| epoch.as_u64())
            .filter(|&epoch| epoch > 0)
    }

    fn recognized(&self) -> Vec<u64> {
        self.members
            .iter()
            .chain([&self.user])
            .map(|&user| u64::from(user))
            .collect()
    }

    fn prepare(
        &mut self,
        transition_id: u16,
        protocol_version: u16,
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<Option<Executed>, DaveError> {
        match self.attempt(MlsCall::Prepare, |session| {
            session.prepare_transition(transition_id, protocol_version)
        }) {
            Ok(()) if transition_id == 0 => {
                return Ok(Some(self.executed(transition_id, protocol_version)));
            }
            Ok(()) => {
                self.pending.insert(transition_id, protocol_version);
            }
            Err(Failure::NoSession) if transition_id == 0 => return Ok(None),
            Err(Failure::NoSession) => {}
            Err(failure) => {
                warn!(transition_id, %failure, "preparing a DAVE transition failed");
                self.recover(transition_id, now, out)?;
                return Ok(None);
            }
        }
        out.push(Outbound::TransitionReady(transition_id));
        Ok(None)
    }

    fn execute(
        &mut self,
        transition_id: u16,
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<Option<Executed>, DaveError> {
        self.last_transition = transition_id;
        match self.attempt(MlsCall::Execute, |session| {
            session.execute_transition(transition_id)
        }) {
            Ok(true) => {
                let protocol_version = self
                    .pending
                    .remove(&transition_id)
                    .unwrap_or(self.version.get());
                Ok(Some(self.executed(transition_id, protocol_version)))
            }
            Ok(false) | Err(Failure::NoSession) => {
                debug!(transition_id, "no prepared DAVE transition to execute");
                Ok(None)
            }
            Err(failure) => {
                warn!(transition_id, %failure, "executing a DAVE transition failed");
                self.recover(transition_id, now, out)?;
                Ok(None)
            }
        }
    }

    fn executed(&mut self, transition_id: u16, protocol_version: u16) -> Executed {
        let previous = mem::replace(&mut self.protocol_version, protocol_version);
        if previous == 0
            && protocol_version > 0
            && let Some(session) = &mut self.session
        {
            session.set_passthrough_mode(false, Some(PASSTHROUGH_EXPIRY_SECS));
        }
        self.recovering = false;
        self.counters.transitions_executed += 1;
        debug!(transition_id, protocol_version, "DAVE transition executed");
        Executed {
            protocol_version,
            epoch: self.epoch(),
            transition_id,
        }
    }

    fn prepare_epoch(
        &mut self,
        transition_id: Option<u16>,
        epoch: u64,
        protocol_version: u16,
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<Option<Executed>, DaveError> {
        if let Some(transition_id) = transition_id {
            self.last_transition = transition_id;
        }
        if epoch == 1 {
            let Some(version) = NonZeroU16::new(protocol_version) else {
                warn!("ignoring a new DAVE group for protocol version 0");
                return Ok(None);
            };
            self.version = version;
            self.rebuild(out)?;
            return Ok(None);
        }
        warn!(
            epoch,
            protocol_version, "a DAVE protocol version change inside a group is not supported"
        );
        match transition_id {
            Some(transition_id) if transition_id != 0 => {
                self.prepare(transition_id, protocol_version, now, out)
            }
            _ => Ok(None),
        }
    }

    fn set_external_sender(
        &mut self,
        sender: Bytes,
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<(), DaveError> {
        let applied = self.attempt(MlsCall::ExternalSender, |session| {
            match session.set_external_sender(&sender) {
                Err(SetExternalSenderError::AlreadyInGroup) => Ok(false),
                result => result.map(|()| true),
            }
        });
        match applied {
            Ok(true) | Err(Failure::NoSession) => {
                self.external_sender = Some(sender);
                Ok(())
            }
            Ok(false) => {
                debug!("already in a DAVE group, keeping its external sender");
                Ok(())
            }
            Err(Failure::Rejected(reason)) => {
                warn!(%reason, "the DAVE external sender was refused");
                Ok(())
            }
            Err(Failure::Panicked) => self.recover(self.last_transition, now, out),
        }
    }

    fn process_proposals(
        &mut self,
        operation: ProposalsOp,
        data: &[u8],
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<(), DaveError> {
        let operation = match operation {
            ProposalsOp::Append => ProposalsOperationType::APPEND,
            ProposalsOp::Revoke => ProposalsOperationType::REVOKE,
        };
        let recognized = self.recognized();
        match self.attempt(MlsCall::Proposals, |session| {
            match session.process_proposals(operation, data, Some(&recognized)) {
                Err(ProcessProposalsError::NoGroup) => Ok(None),
                result => result,
            }
        }) {
            Ok(Some(commit_welcome)) => {
                let mut message = commit_welcome.commit;
                if let Some(welcome) = commit_welcome.welcome {
                    message.extend_from_slice(&welcome);
                }
                out.push(Outbound::CommitWelcome(message));
                Ok(())
            }
            Ok(None) | Err(Failure::NoSession) => Ok(()),
            Err(Failure::Rejected(reason)) => {
                self.counters.rejected_proposals += 1;
                warn!(%reason, "DAVE proposals were rejected");
                Ok(())
            }
            Err(Failure::Panicked) => self.recover(self.last_transition, now, out),
        }
    }

    fn process_commit(
        &mut self,
        transition_id: u16,
        commit: &[u8],
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<Option<Executed>, DaveError> {
        self.last_transition = transition_id;
        let own = u64::from(self.user);
        match self.attempt(MlsCall::Commit, |session| {
            let applied = session.process_commit(commit)?;
            Ok::<_, ProcessCommitError>(applied.then(|| {
                session
                    .get_user_ids()
                    .is_some_and(|users| users.contains(&own))
            }))
        }) {
            Ok(Some(kept)) => {
                self.counters.commits += 1;
                if kept {
                    return self.prepare(transition_id, self.version.get(), now, out);
                }
                warn!(
                    transition_id,
                    "a DAVE commit removed this client from its group"
                );
                self.recover(transition_id, now, out)?;
                Ok(None)
            }
            Ok(None) | Err(Failure::NoSession) => {
                debug!(transition_id, "DAVE commit ignored");
                Ok(None)
            }
            Err(failure) => {
                warn!(transition_id, %failure, "a DAVE commit was refused");
                self.recover(transition_id, now, out)?;
                Ok(None)
            }
        }
    }

    fn process_welcome(
        &mut self,
        transition_id: u16,
        welcome: &[u8],
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<Option<Executed>, DaveError> {
        self.last_transition = transition_id;
        let recognized = self.recognized();
        match self.attempt(MlsCall::Welcome, |session| {
            session.process_welcome(welcome, Some(&recognized))
        }) {
            Ok(()) => {
                self.counters.welcomes += 1;
                self.prepare(transition_id, self.version.get(), now, out)
            }
            Err(Failure::NoSession) => Ok(None),
            Err(failure) => {
                warn!(transition_id, %failure, "a DAVE welcome was refused");
                self.recover(transition_id, now, out)?;
                Ok(None)
            }
        }
    }

    fn recover(
        &mut self,
        transition_id: u16,
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<(), DaveError> {
        self.counters.recoveries += 1;
        while self
            .recoveries
            .front()
            .is_some_and(|&at| now.saturating_duration_since(at) >= DAVE_RECOVERY_WINDOW)
        {
            self.recoveries.pop_front();
        }
        self.recoveries.push_back(now);
        if self.recoveries.len() > DAVE_RECOVERY_LIMIT {
            return Err(DaveError::RecoveryLimit);
        }
        warn!(transition_id, "resetting the DAVE session");
        self.recovering = true;
        out.push(Outbound::InvalidCommitWelcome(transition_id));
        self.rebuild(out)
    }

    fn rebuild(&mut self, out: &mut Vec<Outbound>) -> Result<(), DaveError> {
        self.pending.clear();
        let (version, user, channel) =
            (self.version, u64::from(self.user), u64::from(self.channel));
        match self.attempt(MlsCall::Reinit, |session| {
            session.reinit(version, user, channel, None)
        }) {
            Ok(()) => {}
            Err(Failure::NoSession) => self.build()?,
            Err(failure) => {
                warn!(%failure, "building a new DAVE session");
                self.session = None;
                self.build()?;
            }
        }
        match self.attempt(MlsCall::KeyPackage, DaveSession::create_key_package) {
            Ok(key_package) => {
                self.counters.key_packages += 1;
                out.push(Outbound::KeyPackage(key_package));
                Ok(())
            }
            Err(failure) => Err(DaveError::Session(failure.to_string())),
        }
    }

    fn build(&mut self) -> Result<(), DaveError> {
        let (version, user, channel) =
            (self.version, u64::from(self.user), u64::from(self.channel));
        let mut session =
            match panic::catch_unwind(move || DaveSession::new(version, user, channel, None)) {
                Ok(Ok(session)) => session,
                Ok(Err(error)) => return Err(DaveError::Session(error.to_string())),
                Err(payload) => {
                    self.panicked(MlsCall::Create, payload.as_ref());
                    return Err(DaveError::Session(Failure::Panicked.to_string()));
                }
            };
        if self.protocol_version == 0 {
            session.set_passthrough_mode(true, None);
        }
        self.session = Some(session);
        if let Some(sender) = self.external_sender.clone()
            && let Err(failure) = self.attempt(MlsCall::ExternalSender, |session| {
                session.set_external_sender(&sender)
            })
        {
            warn!(%failure, "the cached DAVE external sender was refused");
        }
        Ok(())
    }

    fn attempt<T, E: fmt::Display>(
        &mut self,
        call: MlsCall,
        run: impl FnOnce(&mut DaveSession) -> Result<T, E>,
    ) -> Result<T, Failure> {
        let Some(mut session) = self.session.take() else {
            return Err(Failure::NoSession);
        };
        #[cfg(test)]
        let fault = self
            .fault
            .take_if(|(target, _)| *target == call)
            .map(|(_, fault)| fault);
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            #[cfg(test)]
            if let Some(fault) = fault {
                return fault.trigger();
            }
            run(&mut session).map_err(|error| error.to_string())
        }));
        match outcome {
            Ok(result) => {
                self.session = Some(session);
                result.map_err(Failure::Rejected)
            }
            Err(payload) => {
                self.panicked(call, payload.as_ref());
                Err(Failure::Panicked)
            }
        }
    }

    fn panicked(&mut self, call: MlsCall, payload: &(dyn Any + Send)) {
        self.counters.dave_panics += 1;
        let message = payload
            .downcast_ref::<&str>()
            .copied()
            .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
            .unwrap_or("unknown panic");
        error!(
            ?call,
            panic = message,
            "a DAVE call panicked, its session is dropped"
        );
    }
}

pub fn verification_code(fingerprints: [Vec<u8>; 2]) -> Result<String, DaveError> {
    let pairwise = davey::pairwise_fingerprints_internal(fingerprints)
        .map_err(|error| DaveError::Verification(error.to_string()))?;
    davey::generate_displayable_code(&pairwise, VERIFICATION_CODE_DIGITS, VERIFICATION_CODE_GROUP)
        .map_err(|error| DaveError::Verification(error.to_string()))
}

fn encrypt_failure(error: &EncryptError) -> EncryptFailure {
    match error {
        EncryptError::NotReady
        | EncryptError::EncryptionFailed(EncryptorEncryptError::MissingKeyRatchet) => {
            EncryptFailure::NotReady
        }
        EncryptError::EncryptionFailed(
            EncryptorEncryptError::MissingCryptor
            | EncryptorEncryptError::EncryptionFailure
            | EncryptorEncryptError::TooManyAttempts,
        ) => EncryptFailure::Failed,
    }
}

fn decrypt_failure(error: &DecryptError) -> DecryptFailure {
    match error {
        DecryptError::NoDecryptorForUser => DecryptFailure::NoDecryptor,
        DecryptError::DecryptionFailed(failure) => match failure {
            DecryptorDecryptError::UnencryptedWhenPassthroughDisabled => {
                DecryptFailure::Unencrypted
            }
            DecryptorDecryptError::MissingKeyRatchet => DecryptFailure::MissingKey,
            DecryptorDecryptError::InvalidNonce => DecryptFailure::InvalidNonce,
            DecryptorDecryptError::MissingCryptor => DecryptFailure::MissingCryptor,
            DecryptorDecryptError::DecryptionFailure => DecryptFailure::Failed,
        },
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;

    use proptest::collection::vec;
    use proptest::prelude::*;

    use super::*;
    use crate::voice::testkit::mls::{Call, Corruption, ExternalSenderFake, Signal};

    const CHANNEL: u64 = 900;
    const EARLY: u64 = 50;
    const BOT: u64 = 100;
    const A: u64 = 200;
    const B: u64 = 300;
    const C: u64 = 400;
    const STRANGER: u64 = 500;
    const OPUS: &[u8] = &[
        0xFC, 0xFF, 0xFE, 0x10, 0x32, 0x54, 0x76, 0x98, 0xBA, 0xDC, 0xFE,
    ];

    fn user(id: u64) -> UserId {
        UserId::from(id)
    }

    fn channel() -> ChannelId {
        ChannelId::from(CHANNEL)
    }

    fn call(protocol_version: u16) -> Call {
        crate::init_test_tracing();
        Call::new(channel(), protocol_version)
    }

    fn group(ids: &[u64]) -> Call {
        let mut call = call(1);
        for &id in ids {
            call.join(user(id));
            call.run();
        }
        let formed = if ids.len() > 1 {
            SendMode::Encrypt
        } else {
            SendMode::Discard
        };
        for &id in ids {
            assert_eq!(call.dave(user(id)).send_mode(), formed, "{id}");
        }
        assert!(call.failures.is_empty());
        call.sent.clear();
        call.executed.clear();
        call
    }

    fn release(call: &mut Call) {
        let held = call.fake.release_transitions();
        call.enqueue(held);
        call.run();
    }

    fn hear(call: &mut Call, speaker: u64, listener: u64) -> Vec<u8> {
        let mut encrypted = Vec::new();
        call.dave_mut(user(speaker))
            .encrypt(OPUS, &mut encrypted)
            .unwrap();
        assert_ne!(encrypted.as_slice(), OPUS);
        let mut frame = vec![0; encrypted.len()];
        let len = call
            .dave_mut(user(listener))
            .decrypt(user(speaker), &encrypted, &mut frame)
            .unwrap();
        frame.truncate(len);
        frame
    }

    fn started(protocol_version: u16) -> (Dave, Vec<Outbound>) {
        crate::init_test_tracing();
        let mut dave = Dave::new(user(BOT), channel());
        let mut out = Vec::new();
        dave.start(protocol_version, &mut out).unwrap();
        (dave, out)
    }

    fn ready_ids(messages: &[Outbound]) -> Vec<u16> {
        messages
            .iter()
            .filter_map(|message| match message {
                Outbound::TransitionReady(id) => Some(*id),
                _ => None,
            })
            .collect()
    }

    fn roster(dave: &Dave) -> Vec<u64> {
        let mut users: Vec<u64> = dave
            .leaves()
            .into_iter()
            .map(|(_, id)| u64::from(id))
            .collect();
        users.sort_unstable();
        users
    }

    fn recognized(dave: &Dave) -> Vec<u64> {
        let mut users = dave.recognized();
        users.sort_unstable();
        users
    }

    #[test]
    fn davey_version_matches_the_pin_and_the_lockfile() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let manifest = fs::read_to_string(root.join("Cargo.toml")).unwrap();
        let lockfile = fs::read_to_string(root.join("Cargo.lock"))
            .unwrap()
            .replace("\r\n", "\n");

        assert!(manifest.contains(&format!("davey = \"={DAVEY_VERSION}\"")));
        assert!(lockfile.contains(&format!(
            "name = \"davey\"\nversion = \"{DAVEY_VERSION}\"\n"
        )));
    }

    #[test]
    fn send_mode_survives_the_byte_round_trip() {
        for mode in [
            SendMode::Plain,
            SendMode::Encrypt,
            SendMode::Hold,
            SendMode::Discard,
        ] {
            assert_eq!(SendMode::from_byte(mode as u8), mode);
        }
        assert_eq!(SendMode::from_byte(200), SendMode::Hold);
    }

    #[test]
    fn send_mode_follows_the_protocol_version_the_members_and_the_key() {
        let (mut plain, _) = started(0);
        plain.clients_connect(&[user(A)]);
        let (alone, _) = started(1);
        let (mut waiting, _) = started(1);
        waiting.clients_connect(&[user(A)]);
        let call = group(&[BOT, A]);

        assert_eq!(plain.send_mode(), SendMode::Plain);
        assert_eq!(alone.send_mode(), SendMode::Discard);
        assert_eq!(waiting.send_mode(), SendMode::Hold);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
    }

    #[test]
    fn the_recognized_users_are_the_announced_ones_and_the_bot_itself() {
        let (mut dave, _) = started(1);
        dave.clients_connect(&[user(A), user(B), user(BOT)]);
        assert_eq!(recognized(&dave), [BOT, A, B]);
        assert_eq!(dave.send_mode(), SendMode::Hold);

        dave.client_disconnect(user(A));
        assert_eq!(recognized(&dave), [BOT, B]);
        dave.client_disconnect(user(B));
        assert_eq!(recognized(&dave), [BOT]);
        assert_eq!(dave.send_mode(), SendMode::Discard);
    }

    #[test]
    fn alone_in_a_dave_call_the_bot_sends_a_key_package_and_discards_media() {
        let mut call = call(1);
        call.join(user(BOT));
        call.run();

        assert!(matches!(
            call.sent_by(user(BOT)).as_slice(),
            [Outbound::KeyPackage(_)]
        ));
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Discard);
        assert_eq!(call.dave(user(BOT)).counters().key_packages, 1);
        assert_eq!(
            call.dave(user(BOT)).info(),
            DaveInfo {
                protocol_version: 1,
                epoch: None,
                privacy_code: None,
            }
        );
        assert!(call.failures.is_empty());
    }

    #[test]
    fn a_refused_external_sender_is_not_cached_and_changes_nothing() {
        let fake = ExternalSenderFake::new(channel(), 1);
        let (mut dave, _) = started(1);
        let mut out = Vec::new();
        let now = Instant::now();
        dave.handle(fake.external_sender(), now, &mut out).unwrap();
        let cached = dave.external_sender.clone();

        let garbage = DaveOp::ExternalSender(Bytes::from_static(b"not an external sender"));
        dave.handle(garbage, now, &mut out).unwrap();

        assert!(out.is_empty());
        assert_eq!(dave.external_sender, cached);
        assert_eq!(dave.counters().recoveries, 0);
    }

    #[test]
    fn a_peer_joining_forms_a_group_and_hears_the_exact_opus_bytes() {
        for peer in [A, EARLY] {
            let mut call = call(1);
            call.join(user(BOT));
            call.run();
            call.join(user(peer));
            call.run();

            let sent = call.sent_by(user(BOT));
            let counters = call.dave(user(BOT)).counters();
            assert_eq!(counters.commits, u64::from(peer == A));
            assert_eq!(counters.welcomes, u64::from(peer == EARLY));
            let ready = ready_ids(&sent);
            assert_eq!(ready.len(), 1);
            assert_ne!(ready[0], 0);
            assert_eq!(
                call.executed_by(user(BOT)),
                [Executed {
                    protocol_version: 1,
                    epoch: Some(1),
                    transition_id: ready[0],
                }]
            );
            assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
            assert_eq!(hear(&mut call, BOT, peer), OPUS);
            assert_eq!(hear(&mut call, peer, BOT), OPUS);
        }
    }

    #[test]
    fn joining_peers_waits_for_the_execute_before_encrypting() {
        for peers in [&[EARLY][..], &[A, B, C]] {
            let mut call = group(peers);
            call.fake.hold_transitions = true;
            call.join(user(BOT));
            call.run();

            let ready = ready_ids(&call.sent_by(user(BOT)));
            assert_eq!(ready.len(), 1);
            assert_ne!(ready[0], 0);
            assert_eq!(call.dave(user(BOT)).counters().welcomes, 1);
            assert!(call.executed_by(user(BOT)).is_empty());
            assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Hold);

            release(&mut call);
            assert_eq!(call.executed_by(user(BOT))[0].transition_id, ready[0]);
            assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
            for &peer in peers {
                assert_eq!(hear(&mut call, BOT, peer), OPUS);
            }
        }
    }

    #[test]
    fn a_transition_id_of_zero_executes_at_once_without_op_23() {
        for peer in [A, EARLY] {
            let mut call = call(1);
            call.join(user(BOT));
            call.run();
            call.fake.next_transition = 0;
            call.join(user(peer));
            call.run();

            assert!(ready_ids(&call.sent_by(user(BOT))).is_empty());
            assert!(ready_ids(&call.sent_by(user(peer))).is_empty());
            assert_eq!(call.executed_by(user(BOT))[0].transition_id, 0);
            assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
            assert_eq!(hear(&mut call, BOT, peer), OPUS);
        }
    }

    #[test]
    fn a_sole_member_reset_drops_the_old_key_and_holds_until_the_new_group() {
        let mut call = group(&[BOT, A]);
        call.leave(user(A));
        call.run();

        assert!(matches!(
            call.sent_by(user(BOT)).as_slice(),
            [Outbound::KeyPackage(_)]
        ));
        assert_eq!(call.executed_by(user(BOT))[0].transition_id, 0);
        assert!(call.dave(user(BOT)).pending.is_empty());
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Discard);
        let mut out = Vec::new();
        assert_eq!(
            call.dave_mut(user(BOT)).encrypt(OPUS, &mut out),
            Err(EncryptFailure::NotReady)
        );

        call.fake.hold_transitions = true;
        call.join(user(B));
        call.run();
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Hold);

        release(&mut call);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        assert_eq!(hear(&mut call, BOT, B), OPUS);
    }

    #[test]
    fn peers_hear_the_bot_every_time_they_join_after_a_reset() {
        let mut call = call(1);
        call.join(user(BOT));
        call.run();
        for round in 0..3 {
            call.join(user(A));
            call.run();
            assert_eq!(hear(&mut call, BOT, A), OPUS, "round {round}");
            call.leave(user(A));
            call.run();
            assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Discard);
        }

        assert_eq!(call.dave(user(BOT)).counters().key_packages, 4);
        assert!(call.failures.is_empty());
    }

    #[test]
    fn a_downgrade_keeps_encrypting_until_the_execute_then_sends_plain_opus() {
        let mut call = group(&[BOT, A]);
        call.fake.hold_transitions = true;
        let prepare = call.fake.downgrade();
        call.enqueue(prepare);
        call.run();

        let ready = ready_ids(&call.sent_by(user(BOT)));
        assert_eq!(ready.len(), 1);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        assert_eq!(hear(&mut call, BOT, A), OPUS);

        release(&mut call);
        let executed = call.executed_by(user(BOT));
        assert_eq!(executed[0].protocol_version, 0);
        assert_eq!(executed[0].transition_id, ready[0]);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);
        assert_eq!(call.dave(user(BOT)).info().privacy_code, None);
        let mut frame = [0; 64];
        let len = call
            .dave_mut(user(BOT))
            .decrypt(user(A), OPUS, &mut frame)
            .unwrap();
        assert_eq!(&frame[..len], OPUS);
    }

    #[test]
    fn an_upgrade_from_protocol_zero_sends_plain_opus_until_the_transition_executes() {
        let mut call = call(0);
        call.join(user(BOT));
        call.join(user(A));
        call.run();
        assert!(call.sent.is_empty());
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);

        call.fake.hold_transitions = true;
        let prepare = call.fake.upgrade();
        call.enqueue(prepare);
        call.run();
        assert_eq!(call.dave(user(BOT)).counters().key_packages, 1);
        assert_eq!(ready_ids(&call.sent_by(user(BOT))).len(), 1);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Plain);
        assert_eq!(call.dave(user(BOT)).info().privacy_code, None);
        let mut frame = [0; 64];
        let len = call
            .dave_mut(user(BOT))
            .decrypt(user(A), OPUS, &mut frame)
            .unwrap();
        assert_eq!(&frame[..len], OPUS);

        release(&mut call);
        assert_eq!(call.executed_by(user(BOT))[0].protocol_version, 1);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        assert!(call.dave(user(BOT)).info().privacy_code.is_some());
        assert_eq!(hear(&mut call, BOT, A), OPUS);

        call.fake.hold_transitions = false;
        call.join(user(C));
        call.run();
        assert_eq!(
            call.dave_mut(user(BOT)).decrypt(user(C), OPUS, &mut frame),
            Err(DecryptFailure::Unencrypted)
        );
    }

    #[test]
    fn a_stage_call_at_protocol_zero_is_plain_and_only_caches_the_external_sender() {
        let mut call = call(0);
        call.join(user(BOT));
        call.run();

        let dave = call.dave(user(BOT));
        assert!(call.sent.is_empty());
        assert_eq!(dave.send_mode(), SendMode::Plain);
        assert!(dave.session.is_none());
        assert!(dave.external_sender.is_some());
        assert_eq!(
            dave.info(),
            DaveInfo {
                protocol_version: 0,
                epoch: None,
                privacy_code: None,
            }
        );
    }

    #[test]
    fn a_corrupt_commit_is_flagged_and_the_bot_is_added_again() {
        let mut call = group(&[BOT, A, B]);
        call.fake.corrupt_next(user(BOT), Corruption::Commit);
        call.join(user(C));
        call.run();

        let sent = call.sent_by(user(BOT));
        let flagged = sent
            .iter()
            .position(|message| matches!(message, Outbound::InvalidCommitWelcome(_)))
            .unwrap();
        assert!(matches!(sent[flagged + 1], Outbound::KeyPackage(_)));
        assert_eq!(call.dave(user(BOT)).counters().recoveries, 1);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        for peer in [A, B, C] {
            assert_eq!(hear(&mut call, BOT, peer), OPUS);
        }
    }

    #[test]
    fn a_corrupt_welcome_is_flagged_and_the_bot_is_added_again() {
        let mut call = group(&[A, B]);
        call.fake.corrupt_next(user(BOT), Corruption::Welcome);
        call.join(user(BOT));
        call.run();

        let sent = call.sent_by(user(BOT));
        assert!(matches!(
            sent[..3],
            [
                Outbound::KeyPackage(_),
                Outbound::InvalidCommitWelcome(_),
                Outbound::KeyPackage(_)
            ]
        ));
        assert_eq!(call.dave(user(BOT)).counters().recoveries, 1);
        assert_eq!(call.dave(user(BOT)).counters().welcomes, 1);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        assert_eq!(hear(&mut call, BOT, A), OPUS);
    }

    #[test]
    fn more_recoveries_than_the_limit_within_the_window_end_the_session() {
        let (mut dave, _) = started(1);
        let now = Instant::now();
        let mut out = Vec::new();
        let corrupt = |transition_id| DaveOp::Welcome {
            transition_id,
            welcome: Bytes::from_static(b"not a welcome"),
        };

        for transition_id in 1..=u16::try_from(DAVE_RECOVERY_LIMIT).unwrap() {
            dave.handle(corrupt(transition_id), now, &mut out).unwrap();
        }
        out.clear();
        let past_limit = dave.handle(corrupt(9), now + DAVE_RECOVERY_WINDOW / 2, &mut out);

        assert!(matches!(past_limit, Err(DaveError::RecoveryLimit)));
        assert!(out.is_empty());
    }

    #[test]
    fn recoveries_older_than_the_window_stop_counting() {
        let (mut dave, _) = started(1);
        let mut now = Instant::now();
        let mut out = Vec::new();

        for transition_id in 1..=10 {
            let welcome = DaveOp::Welcome {
                transition_id,
                welcome: Bytes::from_static(b"not a welcome"),
            };
            dave.handle(welcome, now, &mut out).unwrap();
            now += DAVE_RECOVERY_WINDOW;
        }

        assert_eq!(dave.counters().recoveries, 10);
        assert!(out.contains(&Outbound::InvalidCommitWelcome(10)));
    }

    #[test]
    fn an_add_proposal_for_an_unannounced_user_is_rejected_and_the_group_stays() {
        let mut call = group(&[BOT, A]);
        call.join_unannounced(user(STRANGER));
        call.run();

        assert!(
            !call
                .sent_by(user(BOT))
                .iter()
                .any(|message| matches!(message, Outbound::CommitWelcome(_)))
        );
        let counters = call.dave(user(BOT)).counters();
        assert_eq!(counters.rejected_proposals, 1);
        assert_eq!(counters.recoveries, 0);
        assert_eq!(roster(call.dave(user(BOT))), [BOT, A]);
        assert_eq!(hear(&mut call, BOT, A), OPUS);
    }

    #[test]
    fn a_welcome_listing_a_user_the_bot_never_heard_of_is_refused_and_recovered() {
        let mut call = group(&[A, B]);
        call.join(user(BOT));
        call.enqueue(vec![(user(BOT), Signal::ClientDisconnect(user(B)))]);
        call.run();

        let sent = call.sent_by(user(BOT));
        assert!(matches!(
            sent[..3],
            [
                Outbound::KeyPackage(_),
                Outbound::InvalidCommitWelcome(_),
                Outbound::KeyPackage(_)
            ]
        ));
        assert_eq!(call.dave(user(BOT)).counters().welcomes, 0);
        assert!(matches!(
            call.failures.as_slice(),
            [(failed, DaveError::RecoveryLimit)] if *failed == user(BOT)
        ));
    }

    #[test]
    fn revoked_proposals_are_left_out_of_the_next_commit() {
        let mut call = group(&[BOT, A]);
        call.fake.hold_commits = true;
        call.join(user(B));
        call.join(user(C));
        call.run();
        let revoke = call.fake.revoke(user(B));
        call.enqueue(revoke);
        call.run();
        call.fake.hold_commits = false;
        call.run();

        assert_eq!(roster(call.dave(user(BOT))), [BOT, A, C]);
        assert_eq!(call.dave(user(BOT)).counters().rejected_proposals, 0);
        assert_eq!(hear(&mut call, BOT, C), OPUS);
        assert!(call.dave(user(BOT)).fingerprint_pair(user(B)).is_err());
    }

    #[test]
    fn a_commit_for_a_pending_group_is_ignored() {
        let fake = ExternalSenderFake::new(channel(), 1);
        let (mut dave, _) = started(1);
        let mut out = Vec::new();
        dave.handle(fake.external_sender(), Instant::now(), &mut out)
            .unwrap();
        let commit = DaveOp::AnnounceCommit {
            transition_id: 5,
            commit: Bytes::from_static(b"a commit for someone else"),
        };

        assert_eq!(dave.handle(commit, Instant::now(), &mut out).unwrap(), None);
        assert!(out.is_empty());
        assert_eq!(dave.counters().recoveries, 0);
    }

    #[test]
    fn a_commit_without_queued_proposals_in_an_established_group_recovers() {
        let mut call = group(&[BOT, A]);
        call.deliver(
            user(BOT),
            DaveOp::AnnounceCommit {
                transition_id: 9,
                commit: Bytes::from_static(b"a replayed commit"),
            },
        );
        call.run();

        let sent = call.sent_by(user(BOT));
        assert_eq!(sent[0], Outbound::InvalidCommitWelcome(9));
        assert!(matches!(sent[1], Outbound::KeyPackage(_)));
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        assert_eq!(hear(&mut call, BOT, A), OPUS);
    }

    #[test]
    fn after_a_recovery_the_bot_holds_until_its_re_add_executes() {
        let mut call = group(&[BOT, A]);
        call.fake.hold_transitions = true;
        call.deliver(
            user(BOT),
            DaveOp::AnnounceCommit {
                transition_id: 9,
                commit: Bytes::from_static(b"a replayed commit"),
            },
        );
        call.run();

        assert_eq!(call.dave(user(BOT)).counters().recoveries, 1);
        assert_eq!(ready_ids(&call.sent_by(user(BOT))).len(), 1);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Hold);
        release(&mut call);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        assert_eq!(hear(&mut call, BOT, A), OPUS);
    }

    #[test]
    fn an_execute_for_an_unknown_transition_is_ignored() {
        let mut call = group(&[BOT, A]);
        call.deliver(user(BOT), DaveOp::ExecuteTransition { transition_id: 77 });
        call.run();

        assert!(call.sent.is_empty());
        assert!(call.executed.is_empty());
        assert_eq!(hear(&mut call, BOT, A), OPUS);
    }

    #[test]
    fn frames_between_the_commit_and_the_execute_use_the_old_key_and_still_decrypt() {
        let mut call = group(&[BOT, A]);
        call.fake.hold_transitions = true;
        call.join(user(B));
        call.run();
        assert_eq!(ready_ids(&call.sent_by(user(BOT))).len(), 1);
        assert_eq!(hear(&mut call, BOT, A), OPUS);

        release(&mut call);
        assert_eq!(hear(&mut call, BOT, A), OPUS);
        assert_eq!(hear(&mut call, BOT, B), OPUS);
    }

    #[test]
    fn the_privacy_code_appears_at_the_prepare_and_matches_the_peer() {
        let mut call = call(1);
        call.join(user(BOT));
        call.run();
        assert_eq!(call.dave(user(BOT)).info().privacy_code, None);

        call.fake.hold_transitions = true;
        call.join(user(A));
        call.run();
        let prepared = call.dave(user(BOT)).info();
        assert!(prepared.privacy_code.is_some());
        assert_eq!(prepared, call.dave(user(A)).info());

        release(&mut call);
        assert_eq!(call.dave(user(BOT)).info(), prepared);
        assert_eq!(prepared.epoch, Some(1));
    }

    #[test]
    fn the_bot_and_the_peer_compute_the_same_verification_code() {
        let call = group(&[BOT, A]);
        let ours =
            verification_code(call.dave(user(BOT)).fingerprint_pair(user(A)).unwrap()).unwrap();
        let theirs =
            verification_code(call.dave(user(A)).fingerprint_pair(user(BOT)).unwrap()).unwrap();

        assert_eq!(ours, theirs);
        assert_eq!(ours.len(), 45);
        assert!(ours.bytes().all(|digit| digit.is_ascii_digit()));
        assert!(matches!(
            call.dave(user(BOT)).fingerprint_pair(user(STRANGER)),
            Err(DaveError::Verification(_))
        ));
    }

    #[test]
    fn the_silence_frame_is_never_encrypted_into_plaintext() {
        let mut call = group(&[BOT, A]);
        let mut out = Vec::new();

        assert_eq!(
            call.dave_mut(user(BOT)).encrypt(&SILENCE_FRAME, &mut out),
            Err(EncryptFailure::Failed)
        );
        assert!(out.is_empty());
    }

    #[test]
    fn encrypting_before_a_group_is_not_ready() {
        let (mut alone, _) = started(1);
        let mut stage = Dave::new(user(BOT), channel());
        let mut out = Vec::new();

        assert_eq!(alone.encrypt(OPUS, &mut out), Err(EncryptFailure::NotReady));
        assert_eq!(stage.encrypt(OPUS, &mut out), Err(EncryptFailure::NotReady));
    }

    #[test]
    fn encrypt_errors_map_to_not_ready_or_failed() {
        assert_eq!(
            encrypt_failure(&EncryptError::NotReady),
            EncryptFailure::NotReady
        );
        assert_eq!(
            encrypt_failure(&EncryptError::EncryptionFailed(
                EncryptorEncryptError::MissingKeyRatchet
            )),
            EncryptFailure::NotReady
        );
        for error in [
            EncryptorEncryptError::MissingCryptor,
            EncryptorEncryptError::EncryptionFailure,
            EncryptorEncryptError::TooManyAttempts,
        ] {
            assert_eq!(
                encrypt_failure(&EncryptError::EncryptionFailed(error)),
                EncryptFailure::Failed
            );
        }
    }

    #[test]
    fn decrypt_errors_map_to_their_own_failure() {
        assert_eq!(
            decrypt_failure(&DecryptError::NoDecryptorForUser),
            DecryptFailure::NoDecryptor
        );
        for (error, failure) in [
            (
                DecryptorDecryptError::UnencryptedWhenPassthroughDisabled,
                DecryptFailure::Unencrypted,
            ),
            (
                DecryptorDecryptError::MissingKeyRatchet,
                DecryptFailure::MissingKey,
            ),
            (
                DecryptorDecryptError::InvalidNonce,
                DecryptFailure::InvalidNonce,
            ),
            (
                DecryptorDecryptError::MissingCryptor,
                DecryptFailure::MissingCryptor,
            ),
            (
                DecryptorDecryptError::DecryptionFailure,
                DecryptFailure::Failed,
            ),
        ] {
            assert_eq!(
                decrypt_failure(&DecryptError::DecryptionFailed(error)),
                failure
            );
        }
    }

    #[test]
    fn decrypting_reports_unknown_users_and_unencrypted_frames() {
        let mut call = group(&[BOT, A]);
        let mut frame = [0; 64];

        assert_eq!(
            call.dave_mut(user(BOT))
                .decrypt(user(STRANGER), OPUS, &mut frame),
            Err(DecryptFailure::NoDecryptor)
        );
        assert_eq!(
            call.dave_mut(user(BOT)).decrypt(user(A), OPUS, &mut frame),
            Err(DecryptFailure::Unencrypted)
        );
    }

    #[test]
    fn a_commit_that_removes_the_bot_recovers_and_the_bot_is_added_again() {
        let mut call = group(&[BOT, A, B]);
        let before = call.dave(user(BOT)).counters();
        let kick = call.fake.kick(user(BOT));
        call.enqueue(kick);
        call.run();

        let sent = call.sent_by(user(BOT));
        let flagged = sent
            .iter()
            .position(|message| matches!(message, Outbound::InvalidCommitWelcome(_)))
            .unwrap();
        assert!(matches!(sent[flagged + 1], Outbound::KeyPackage(_)));
        let counters = call.dave(user(BOT)).counters();
        assert_eq!(counters.commits, before.commits + 1);
        assert_eq!(counters.recoveries, 1);
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        assert_eq!(hear(&mut call, BOT, A), OPUS);
        assert_eq!(hear(&mut call, BOT, B), OPUS);
    }

    #[test]
    fn a_protocol_change_inside_a_group_is_still_answered() {
        let (mut dave, _) = started(1);
        let mut out = Vec::new();
        let with_id = DaveOp::PrepareEpoch {
            transition_id: Some(4),
            epoch: 2,
            protocol_version: 1,
        };
        let without_id = DaveOp::PrepareEpoch {
            transition_id: None,
            epoch: 2,
            protocol_version: 1,
        };

        dave.handle(with_id, Instant::now(), &mut out).unwrap();
        assert_eq!(out, [Outbound::TransitionReady(4)]);
        out.clear();
        dave.handle(without_id, Instant::now(), &mut out).unwrap();
        assert!(out.is_empty());
    }

    #[test]
    fn a_prepare_on_a_call_without_a_session_is_still_answered() {
        let (mut dave, _) = started(0);
        let mut out = Vec::new();
        let prepare = DaveOp::PrepareTransition {
            transition_id: 6,
            protocol_version: 0,
        };

        assert_eq!(
            dave.handle(prepare, Instant::now(), &mut out).unwrap(),
            None
        );
        assert_eq!(out, [Outbound::TransitionReady(6)]);
        out.clear();
        let execute = DaveOp::ExecuteTransition { transition_id: 6 };
        assert_eq!(
            dave.handle(execute, Instant::now(), &mut out).unwrap(),
            None
        );
        let immediate = DaveOp::PrepareTransition {
            transition_id: 0,
            protocol_version: 0,
        };
        assert_eq!(
            dave.handle(immediate, Instant::now(), &mut out).unwrap(),
            None
        );
        assert!(out.is_empty());
    }

    #[test]
    fn a_failing_prepare_or_execute_takes_the_recover_path() {
        let prepare = DaveOp::PrepareTransition {
            transition_id: 3,
            protocol_version: 1,
        };
        for (call, op) in [
            (MlsCall::Prepare, prepare.clone()),
            (
                MlsCall::Execute,
                DaveOp::ExecuteTransition { transition_id: 3 },
            ),
        ] {
            let (mut dave, _) = started(1);
            let mut out = Vec::new();
            if call == MlsCall::Execute {
                dave.handle(prepare.clone(), Instant::now(), &mut out)
                    .unwrap();
                out.clear();
            }
            dave.inject(call, Fault::Error);

            assert_eq!(dave.handle(op, Instant::now(), &mut out).unwrap(), None);
            assert!(matches!(
                out.as_slice(),
                [Outbound::InvalidCommitWelcome(3), Outbound::KeyPackage(_)]
            ));
            assert_eq!(dave.counters().recoveries, 1);
            assert_eq!(dave.counters().dave_panics, 0);
        }
    }

    #[test]
    fn a_panic_inside_davey_replaces_the_session_with_one_built_from_the_cached_sender() {
        let mut call = group(&[BOT, A]);
        let last_transition = call.dave(user(BOT)).last_transition;
        assert_ne!(last_transition, 0);
        call.dave_mut(user(BOT))
            .inject(MlsCall::Proposals, Fault::Panic);
        call.join(user(B));
        call.run();

        let counters = call.dave(user(BOT)).counters();
        assert_eq!(counters.dave_panics, 1);
        assert_eq!(counters.recoveries, 1);
        let sent = call.sent_by(user(BOT));
        assert_eq!(sent[0], Outbound::InvalidCommitWelcome(last_transition));
        assert!(matches!(sent[1], Outbound::KeyPackage(_)));
        assert_eq!(call.dave(user(BOT)).send_mode(), SendMode::Encrypt);
        assert_eq!(hear(&mut call, BOT, A), OPUS);
        assert_eq!(hear(&mut call, BOT, B), OPUS);
    }

    #[test]
    fn prepared_transitions_are_forgotten_after_a_reinit_or_a_recovery() {
        let (mut dave, _) = started(1);
        let mut out = Vec::new();
        let now = Instant::now();
        let prepare = |transition_id| DaveOp::PrepareTransition {
            transition_id,
            protocol_version: 1,
        };

        dave.handle(prepare(3), now, &mut out).unwrap();
        assert!(dave.pending.contains_key(&3));
        let epoch = DaveOp::PrepareEpoch {
            transition_id: None,
            epoch: 1,
            protocol_version: 1,
        };
        dave.handle(epoch, now, &mut out).unwrap();
        assert!(dave.pending.is_empty());
        let execute = DaveOp::ExecuteTransition { transition_id: 3 };
        assert_eq!(dave.handle(execute, now, &mut out).unwrap(), None);

        dave.handle(prepare(4), now, &mut out).unwrap();
        let corrupt = DaveOp::Welcome {
            transition_id: 5,
            welcome: Bytes::from_static(b"not a welcome"),
        };
        dave.handle(corrupt, now, &mut out).unwrap();
        assert!(dave.pending.is_empty());
        let execute = DaveOp::ExecuteTransition { transition_id: 4 };
        assert_eq!(dave.handle(execute, now, &mut out).unwrap(), None);
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(48))]

        #[test]
        fn malformed_mls_bytes_never_panic_a_pending_session(
            bytes in vec(any::<u8>(), 0..300),
            transition_id in 1..u16::MAX,
        ) {
            let fake = ExternalSenderFake::new(channel(), 1);
            let (mut dave, _) = started(1);
            let mut out = Vec::new();
            let now = Instant::now();
            dave.handle(fake.external_sender(), now, &mut out).unwrap();
            let bytes = Bytes::from(bytes);

            let welcome = DaveOp::Welcome { transition_id, welcome: bytes.clone() };
            dave.handle(welcome, now, &mut out).unwrap();
            prop_assert!(out.contains(&Outbound::InvalidCommitWelcome(transition_id)));
            for op in [
                DaveOp::Proposals { operation: ProposalsOp::Append, data: bytes.clone() },
                DaveOp::Proposals { operation: ProposalsOp::Revoke, data: bytes.clone() },
                DaveOp::AnnounceCommit { transition_id, commit: bytes.clone() },
                DaveOp::ExternalSender(bytes),
            ] {
                let _ = dave.handle(op, now, &mut out);
            }
            prop_assert_eq!(dave.counters().dave_panics, 0);
        }

        #[test]
        fn malformed_mls_bytes_never_panic_an_established_group(
            bytes in vec(any::<u8>(), 0..300),
            transition_id in 1..u16::MAX,
        ) {
            let mut call = group(&[BOT, A]);
            let bytes = Bytes::from(bytes);
            let now = call.now;
            let dave = call.dave_mut(user(BOT));
            let mut out = Vec::new();

            for operation in [ProposalsOp::Append, ProposalsOp::Revoke] {
                let proposals = DaveOp::Proposals { operation, data: bytes.clone() };
                dave.handle(proposals, now, &mut out).unwrap();
            }
            let commit = DaveOp::AnnounceCommit { transition_id, commit: bytes.clone() };
            dave.handle(commit, now, &mut out).unwrap();
            prop_assert!(out.contains(&Outbound::InvalidCommitWelcome(transition_id)));
            let welcome = DaveOp::Welcome { transition_id, welcome: bytes.clone() };
            let _ = dave.handle(welcome, now, &mut out);
            let _ = dave.handle(DaveOp::ExternalSender(bytes), now, &mut out);
            prop_assert_eq!(dave.counters().dave_panics, 0);
        }
    }
}
