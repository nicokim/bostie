use std::any::Any;
use std::collections::{HashMap, HashSet, VecDeque};
use std::error::Error;
use std::fmt;
use std::mem;
use std::num::NonZeroU16;
use std::panic::{self, AssertUnwindSafe};
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use davey::errors::{
    DecryptError, DecryptorDecryptError, ProcessCommitError, ProcessProposalsError,
    SetExternalSenderError,
};
use davey::{Codec, DaveSession, MediaType, ProposalsOperationType};
use tracing::{debug, error, warn};

use crate::ids::{ChannelId, UserId};
use crate::voice::types::{
    DaveOp, MAX_DAVE_PROTOCOL_VERSION, ProposalsOp, ReceiveTuning, SILENCE_FRAME,
};
use crate::voice::{Throttle, panic_message};

pub(crate) const DAVEY_VERSION: &str = "0.2.0";

const MARKER: [u8; 2] = [0xFA, 0xFA];
const VERIFICATION_CODE_DIGITS: u32 = 45;
const VERIFICATION_CODE_GROUP: u32 = 5;

const _: () = assert!(MAX_DAVE_PROTOCOL_VERSION == davey::DAVE_PROTOCOL_VERSION);
const _: () = {
    const fn assert_send<T: Send>() {}
    assert_send::<Dave>();
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Outbound {
    KeyPackage(Vec<u8>),
    CommitWelcome(Vec<u8>),
    TransitionReady(u16),
    InvalidCommitWelcome(u16),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Executed {
    pub(crate) protocol_version: u16,
    pub(crate) epoch: Option<u64>,
    pub(crate) transition_id: u16,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DaveInfo {
    pub protocol_version: u16,
    pub epoch: Option<u64>,
    pub privacy_code: Option<String>,
}

pub(crate) const RECOVERY_LIMIT: usize = 5;
const RECOVERY_WINDOW: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub(crate) enum SendMode {
    Plain = 0,
    Encrypt = 1,
    Hold = 2,
    Discard = 3,
}

impl SendMode {
    pub(crate) const fn from_byte(byte: u8) -> Self {
        match byte {
            0 => Self::Plain,
            1 => Self::Encrypt,
            3 => Self::Discard,
            _ => Self::Hold,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct EncryptFailure;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum DecryptFailure {
    NoDecryptor,
    Unencrypted,
    Failed,
}

#[derive(Debug, thiserror::Error)]
pub enum DaveError {
    #[error("more than {limit} DAVE recoveries within {window:?}")]
    RecoveryLimit { limit: usize, window: Duration },
    #[error("the DAVE session could not be built")]
    Session(#[source] Box<dyn Error + Send + Sync>),
    #[error("no DAVE verification code")]
    Verification(#[source] Box<dyn Error + Send + Sync>),
}

#[derive(Debug, Clone, Copy)]
enum MlsCall {
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

#[derive(Debug)]
pub(crate) struct Dave {
    user: UserId,
    channel: ChannelId,
    version: NonZeroU16,
    session: Option<DaveSession>,
    protocol_version: u16,
    external_sender: Option<Bytes>,
    members: Arc<HashSet<UserId>>,
    pending: HashMap<u16, u16>,
    last_transition: u16,
    recovering: bool,
    recoveries: VecDeque<Instant>,
    grace: Duration,
    downgrade_expiry: u32,
    upgrade_passthrough: u32,
    settled: Option<Instant>,
    generation: u64,
    decrypt_panicked: Throttle,
}

impl Dave {
    pub(crate) fn new(user: UserId, channel: ChannelId, tuning: &ReceiveTuning) -> Self {
        Self {
            user,
            channel,
            version: NonZeroU16::MIN,
            session: None,
            protocol_version: 0,
            external_sender: None,
            members: Arc::default(),
            pending: HashMap::new(),
            last_transition: 0,
            recovering: false,
            recoveries: VecDeque::new(),
            grace: tuning.dave_grace,
            downgrade_expiry: tuning.downgrade_expiry,
            upgrade_passthrough: tuning.upgrade_passthrough,
            settled: None,
            generation: 0,
            decrypt_panicked: Throttle::default(),
        }
    }

    pub(crate) fn start(
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

    pub(crate) fn follow(
        &mut self,
        channel: ChannelId,
        out: &mut Vec<Outbound>,
    ) -> Result<(), DaveError> {
        if channel == self.channel {
            return Ok(());
        }
        self.channel = channel;
        if self.protocol_version == 0 {
            return Ok(());
        }
        self.rebuild(out)
    }

    pub(crate) fn clients_connect(&mut self, users: &[UserId]) {
        let own = self.user;
        Arc::make_mut(&mut self.members).extend(users.iter().copied().filter(|&user| user != own));
    }

    pub(crate) fn client_disconnect(&mut self, user: UserId) {
        Arc::make_mut(&mut self.members).remove(&user);
    }

    pub(crate) fn clear_clients(&mut self) {
        self.members = Arc::default();
    }

    pub(crate) fn members(&self) -> &Arc<HashSet<UserId>> {
        &self.members
    }

    pub(crate) fn handle(
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
                    session.set_passthrough_mode(false, Some(self.downgrade_expiry));
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

    pub(crate) fn send_mode(&self) -> SendMode {
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

    pub(crate) fn encrypt(
        &mut self,
        frame: &[u8],
        out: &mut Vec<u8>,
    ) -> Result<(), EncryptFailure> {
        if frame == SILENCE_FRAME {
            return Err(EncryptFailure);
        }
        let Some(session) = &mut self.session else {
            return Err(EncryptFailure);
        };
        let Ok(()) = session.encrypt_into(MediaType::AUDIO, Codec::OPUS, frame, out) else {
            return Err(EncryptFailure);
        };
        Ok(())
    }

    pub(crate) fn receive(
        &mut self,
        user: UserId,
        frame: &[u8],
        out: &mut Vec<u8>,
    ) -> Result<(), DecryptFailure> {
        out.clear();
        let plain = frame == SILENCE_FRAME
            || (self.protocol_version == 0 && (self.session.is_none() || !has_marker(frame)));
        if plain {
            out.extend_from_slice(frame);
            return Ok(());
        }
        out.resize(frame.len(), 0);
        let decrypted = self.decrypt(user, frame, out).and_then(|len| match len {
            0 => Err(DecryptFailure::Failed),
            len => Ok(len),
        });
        out.truncate(decrypted.unwrap_or(0));
        decrypted.map(|_| ())
    }

    pub(crate) fn decrypt(
        &mut self,
        user: UserId,
        frame: &[u8],
        out: &mut [u8],
    ) -> Result<usize, DecryptFailure> {
        let Some(session) = &mut self.session else {
            return Err(DecryptFailure::NoDecryptor);
        };
        let user = u64::from(user);
        match panic::catch_unwind(AssertUnwindSafe(|| {
            session.decrypt_into(user, MediaType::AUDIO, frame, out)
        })) {
            Ok(result) => result.map_err(|error| decrypt_failure(&error)),
            Err(payload) => {
                if self.decrypt_panicked.allows(Instant::now()) {
                    error!(
                        panic = panic_message(payload.as_ref()),
                        "a DAVE decrypt panicked"
                    );
                }
                Err(DecryptFailure::Failed)
            }
        }
    }

    pub(crate) fn generation(&self) -> u64 {
        self.generation
    }

    pub(crate) fn settling(&self, now: Instant) -> bool {
        self.recovering
            || !self.pending.is_empty()
            || self
                .settled
                .is_none_or(|at| now.saturating_duration_since(at) < self.grace)
    }

    pub(crate) fn receive_recovery(
        &mut self,
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<bool, DaveError> {
        if self.protocol_version == 0 || self.settling(now) {
            return Ok(false);
        }
        let transition_id = self.last_transition;
        warn!(
            transition_id,
            "resetting the DAVE session after repeated decrypt failures"
        );
        self.reset_session(transition_id, out)?;
        Ok(true)
    }

    pub(crate) fn info(&self) -> DaveInfo {
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

    pub(crate) fn fingerprint_pair(&self, user: UserId) -> Result<[Vec<u8>; 2], DaveError> {
        let session = self
            .session
            .as_ref()
            .ok_or_else(|| DaveError::Verification(Failure::NoSession.into()))?;
        session
            .get_key_fingerprint_pair(0, u64::from(user))
            .map_err(|error| DaveError::Verification(error.into()))
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
                return Ok(Some(self.executed(transition_id, protocol_version, now)));
            }
            Ok(()) => {
                self.generation = self.generation.wrapping_add(1);
                self.pending.insert(transition_id, protocol_version);
            }
            Err(Failure::NoSession) if transition_id == 0 => return Ok(None),
            Err(Failure::NoSession) => {}
            Err(failure) => {
                self.failed(MlsCall::Prepare, &failure, transition_id, now, out)?;
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
                    .unwrap_or_else(|| self.version.get());
                Ok(Some(self.executed(transition_id, protocol_version, now)))
            }
            Ok(false) | Err(Failure::NoSession) => {
                debug!(transition_id, "no prepared DAVE transition to execute");
                Ok(None)
            }
            Err(failure) => {
                self.failed(MlsCall::Execute, &failure, transition_id, now, out)?;
                Ok(None)
            }
        }
    }

    fn executed(&mut self, transition_id: u16, protocol_version: u16, now: Instant) -> Executed {
        let previous = mem::replace(&mut self.protocol_version, protocol_version);
        if let Some(session) = &mut self.session {
            if protocol_version == 0 {
                session.set_passthrough_mode(true, None);
            } else if previous == 0 {
                session.set_passthrough_mode(false, Some(self.upgrade_passthrough));
            }
        }
        self.recovering = false;
        self.settled = Some(now);
        self.generation = self.generation.wrapping_add(1);
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
            if self.protocol_version == 0
                && let Some(session) = &mut self.session
            {
                session.set_passthrough_mode(true, None);
            }
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
            Err(failure) => {
                let transition_id = self.last_transition;
                self.failed(MlsCall::ExternalSender, &failure, transition_id, now, out)
            }
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
            Err(failure) => {
                let transition_id = self.last_transition;
                self.failed(MlsCall::Proposals, &failure, transition_id, now, out)
            }
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
            Ok(Some(true)) => return self.prepare(transition_id, self.version.get(), now, out),
            Ok(Some(false)) => {
                warn!(
                    transition_id,
                    "a DAVE commit removed this client from its group"
                );
                self.recover(transition_id, now, out)?;
            }
            Ok(None) | Err(Failure::NoSession) => debug!(transition_id, "DAVE commit ignored"),
            Err(failure) => self.failed(MlsCall::Commit, &failure, transition_id, now, out)?,
        }
        Ok(None)
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
                self.settled = Some(now);
                self.prepare(transition_id, self.version.get(), now, out)
            }
            Err(Failure::NoSession) => Ok(None),
            Err(failure) => {
                self.failed(MlsCall::Welcome, &failure, transition_id, now, out)?;
                Ok(None)
            }
        }
    }

    fn failed(
        &mut self,
        call: MlsCall,
        failure: &Failure,
        transition_id: u16,
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<(), DaveError> {
        if let (MlsCall::ExternalSender | MlsCall::Proposals, Failure::Rejected(reason)) =
            (call, failure)
        {
            warn!(?call, %reason, "a DAVE message was refused and ignored");
            return Ok(());
        }
        warn!(?call, transition_id, %failure, "a DAVE call failed");
        self.recover(transition_id, now, out)
    }

    fn recover(
        &mut self,
        transition_id: u16,
        now: Instant,
        out: &mut Vec<Outbound>,
    ) -> Result<(), DaveError> {
        while self
            .recoveries
            .front()
            .is_some_and(|&at| now.saturating_duration_since(at) >= RECOVERY_WINDOW)
        {
            self.recoveries.pop_front();
        }
        self.recoveries.push_back(now);
        if self.recoveries.len() > RECOVERY_LIMIT {
            return Err(DaveError::RecoveryLimit {
                limit: RECOVERY_LIMIT,
                window: RECOVERY_WINDOW,
            });
        }
        warn!(transition_id, "resetting the DAVE session");
        self.reset_session(transition_id, out)
    }

    fn reset_session(
        &mut self,
        transition_id: u16,
        out: &mut Vec<Outbound>,
    ) -> Result<(), DaveError> {
        self.recovering = true;
        out.push(Outbound::InvalidCommitWelcome(transition_id));
        self.rebuild(out)
    }

    fn rebuild(&mut self, out: &mut Vec<Outbound>) -> Result<(), DaveError> {
        self.pending.clear();
        self.settled = None;
        self.generation = self.generation.wrapping_add(1);
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
                out.push(Outbound::KeyPackage(key_package));
                Ok(())
            }
            Err(failure) => Err(DaveError::Session(failure.into())),
        }
    }

    fn build(&mut self) -> Result<(), DaveError> {
        let (version, user, channel) =
            (self.version, u64::from(self.user), u64::from(self.channel));
        let mut session =
            match panic::catch_unwind(move || DaveSession::new(version, user, channel, None)) {
                Ok(Ok(session)) => session,
                Ok(Err(error)) => return Err(DaveError::Session(error.into())),
                Err(payload) => {
                    panicked(MlsCall::Create, payload.as_ref());
                    return Err(DaveError::Session(Failure::Panicked.into()));
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
        let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
            run(&mut session).map_err(|error| error.to_string())
        }));
        match outcome {
            Ok(result) => {
                self.session = Some(session);
                result.map_err(Failure::Rejected)
            }
            Err(payload) => {
                panicked(call, payload.as_ref());
                Err(Failure::Panicked)
            }
        }
    }
}

fn panicked(call: MlsCall, payload: &(dyn Any + Send)) {
    error!(
        ?call,
        panic = panic_message(payload),
        "a DAVE call panicked, its session is dropped"
    );
}

pub(crate) fn has_marker(frame: &[u8]) -> bool {
    frame.ends_with(&MARKER)
}

pub(crate) fn verification_code(fingerprints: [Vec<u8>; 2]) -> Result<String, DaveError> {
    let pairwise = davey::pairwise_fingerprints_internal(fingerprints)
        .map_err(|error| DaveError::Verification(error.into()))?;
    davey::generate_displayable_code(&pairwise, VERIFICATION_CODE_DIGITS, VERIFICATION_CODE_GROUP)
        .map_err(|error| DaveError::Verification(error.into()))
}

fn decrypt_failure(error: &DecryptError) -> DecryptFailure {
    match error {
        DecryptError::NoDecryptorForUser => DecryptFailure::NoDecryptor,
        DecryptError::DecryptionFailed(failure) => match failure {
            DecryptorDecryptError::UnencryptedWhenPassthroughDisabled => {
                DecryptFailure::Unencrypted
            }
            DecryptorDecryptError::MissingKeyRatchet
            | DecryptorDecryptError::InvalidNonce
            | DecryptorDecryptError::MissingCryptor
            | DecryptorDecryptError::DecryptionFailure => DecryptFailure::Failed,
        },
    }
}

#[cfg(test)]
mod tests;
