use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::mem;
use std::time::Instant;

use bytes::Bytes;
use openmls::prelude::hash_ref::make_proposal_ref;
use openmls::prelude::tls_codec::{DeserializeBytes, Serialize};
use openmls::prelude::{
    BasicCredential, Ciphersuite, ExternalProposal, ExternalSender, GroupEpoch, GroupId,
    KeyPackage, KeyPackageIn, LeafNodeIndex, MlsMessageIn, OpenMlsProvider, ProtocolVersion,
    SenderExtensionIndex, VLBytes,
};
use openmls_basic_credential::SignatureKeyPair;
use openmls_rust_crypto::OpenMlsRustCrypto;

use crate::ids::{ChannelId, UserId};
use crate::voice::dave::{Dave, DaveError, Executed, Outbound, SendMode};
use crate::voice::types::{DaveOp, MAX_DAVE_PROTOCOL_VERSION, ProposalsOp, ReceiveTuning};

const CIPHERSUITE: Ciphersuite = Ciphersuite::MLS_128_DHKEMP256_AES128GCM_SHA256_P256;
const SENDER_IDENTITY: [u8; 4] = [0x00, 0x01, 0x01, 0x00];
const PROTOCOL_VERSION_PREFIX: usize = 2;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Signal {
    Dave(DaveOp),
    ClientsConnect(Vec<UserId>),
    ClientDisconnect(UserId),
}

pub(crate) type Message = (UserId, Signal);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Corruption {
    Commit,
    Welcome,
}

type Id = u64;

fn dave(to: Id, op: DaveOp) -> Message {
    (UserId::from(to), Signal::Dave(op))
}

struct Round {
    recipients: BTreeSet<Id>,
    committers: BTreeSet<Id>,
    removes: BTreeSet<Id>,
    adds: BTreeMap<Id, Vec<u8>>,
    commits: BTreeMap<Id, Vec<u8>>,
}

struct Transition {
    id: u16,
    waiting: BTreeSet<Id>,
    ready: BTreeSet<Id>,
}

pub(crate) struct ExternalSenderFake {
    provider: OpenMlsRustCrypto,
    signer: SignatureKeyPair,
    external_sender: Bytes,
    group: GroupId,
    protocol_version: u16,
    epoch: u64,
    connected: BTreeSet<Id>,
    leaves: Vec<Option<Id>>,
    key_packages: BTreeMap<Id, KeyPackage>,
    removals: BTreeSet<Id>,
    stale: BTreeSet<Id>,
    round: Option<Round>,
    transition: Option<Transition>,
    corruptions: Vec<(Id, Corruption)>,
    held: Vec<Message>,
    pub next_transition: u16,
    pub hold_commits: bool,
    pub hold_transitions: bool,
}

impl ExternalSenderFake {
    pub(crate) fn new(channel: ChannelId, protocol_version: u16) -> Self {
        let signer = SignatureKeyPair::new(CIPHERSUITE.signature_algorithm()).unwrap();
        let sender = ExternalSender::new(
            signer.public().into(),
            BasicCredential::new(SENDER_IDENTITY.to_vec()).into(),
        );
        Self {
            provider: OpenMlsRustCrypto::default(),
            external_sender: sender.tls_serialize_detached().unwrap().into(),
            signer,
            group: GroupId::from_slice(&u64::from(channel).to_be_bytes()),
            protocol_version,
            epoch: 0,
            connected: BTreeSet::new(),
            leaves: Vec::new(),
            key_packages: BTreeMap::new(),
            removals: BTreeSet::new(),
            stale: BTreeSet::new(),
            round: None,
            transition: None,
            corruptions: Vec::new(),
            held: Vec::new(),
            next_transition: 1,
            hold_commits: false,
            hold_transitions: false,
        }
    }

    pub(crate) fn protocol_version(&self) -> u16 {
        self.protocol_version
    }

    pub(crate) fn external_sender(&self) -> DaveOp {
        DaveOp::ExternalSender(self.external_sender.clone())
    }

    pub(crate) fn members(&self) -> Vec<UserId> {
        self.member_ids().into_iter().map(UserId::from).collect()
    }

    pub(crate) fn leaves(&self) -> Vec<(u32, UserId)> {
        (0..)
            .zip(&self.leaves)
            .filter_map(|(index, leaf)| leaf.map(|id| (index, UserId::from(id))))
            .collect()
    }

    pub(crate) fn awaiting(&self) -> Vec<UserId> {
        self.round
            .as_ref()
            .map(|round| {
                round
                    .committers
                    .iter()
                    .filter(|id| !round.commits.contains_key(id))
                    .map(|&id| UserId::from(id))
                    .collect()
            })
            .unwrap_or_default()
    }

    pub(crate) fn has_round(&self) -> bool {
        self.round.is_some()
    }

    pub(crate) fn join(&mut self, user: UserId) -> Vec<Message> {
        self.connect(u64::from(user), true)
    }

    pub(crate) fn join_unannounced(&mut self, user: UserId) -> Vec<Message> {
        self.connect(u64::from(user), false)
    }

    pub(crate) fn leave(&mut self, user: UserId) -> Vec<Message> {
        let id = u64::from(user);
        self.connected.remove(&id);
        self.key_packages.remove(&id);
        let mut out: Vec<Message> = self
            .connected
            .iter()
            .map(|&other| (UserId::from(other), Signal::ClientDisconnect(user)))
            .collect();
        if self.member_ids().contains(&id) {
            self.removals.insert(id);
        }
        let staying: Vec<Id> = self
            .member_ids()
            .into_iter()
            .filter(|&member| self.in_sync(member) && !self.removals.contains(&member))
            .collect();
        if !self.leaves.is_empty() && staying.len() <= 1 {
            self.reset_group();
            for member in staying {
                out.push(dave(
                    member,
                    DaveOp::PrepareEpoch {
                        transition_id: None,
                        epoch: 1,
                        protocol_version: self.protocol_version,
                    },
                ));
                out.push(dave(
                    member,
                    DaveOp::PrepareTransition {
                        transition_id: 0,
                        protocol_version: self.protocol_version,
                    },
                ));
            }
        }
        out.extend(self.advance());
        out
    }

    pub(crate) fn kick(&mut self, user: UserId) -> Vec<Message> {
        self.removals.insert(u64::from(user));
        self.advance()
    }

    pub(crate) fn downgrade(&mut self) -> Vec<Message> {
        let transition_id = self.allocate();
        self.protocol_version = 0;
        self.reset_group();
        self.key_packages.clear();
        let users = self.connected.clone();
        if transition_id != 0 {
            self.transition = Some(Transition {
                id: transition_id,
                waiting: users.clone(),
                ready: BTreeSet::new(),
            });
        }
        users
            .into_iter()
            .map(|id| {
                dave(
                    id,
                    DaveOp::PrepareTransition {
                        transition_id,
                        protocol_version: 0,
                    },
                )
            })
            .collect()
    }

    pub(crate) fn upgrade(&mut self) -> Vec<Message> {
        self.protocol_version = MAX_DAVE_PROTOCOL_VERSION;
        self.reset_group();
        self.key_packages.clear();
        self.connected
            .iter()
            .map(|&id| {
                dave(
                    id,
                    DaveOp::PrepareEpoch {
                        transition_id: None,
                        epoch: 1,
                        protocol_version: MAX_DAVE_PROTOCOL_VERSION,
                    },
                )
            })
            .collect()
    }

    pub(crate) fn corrupt_next(&mut self, user: UserId, corruption: Corruption) {
        self.corruptions.push((u64::from(user), corruption));
    }

    pub(crate) fn revoke(&mut self, user: UserId) -> Vec<Message> {
        let id = u64::from(user);
        let Some(round) = &mut self.round else {
            return Vec::new();
        };
        let Some(proposal) = round.adds.remove(&id) else {
            return Vec::new();
        };
        round.commits.clear();
        let reference = make_proposal_ref(
            &proposal[PROTOCOL_VERSION_PREFIX..],
            CIPHERSUITE,
            self.provider.crypto(),
        )
        .unwrap();
        let data = vl_bytes(reference.tls_serialize_detached().unwrap());
        let mut out: Vec<Message> = round
            .recipients
            .iter()
            .filter(|&&recipient| recipient != id)
            .map(|&recipient| {
                dave(
                    recipient,
                    DaveOp::Proposals {
                        operation: ProposalsOp::Revoke,
                        data: data.clone(),
                    },
                )
            })
            .collect();
        if round.adds.is_empty() && round.removes.is_empty() {
            self.round = None;
        }
        self.key_packages.remove(&id);
        out.extend(self.advance());
        out
    }

    pub(crate) fn release_transitions(&mut self) -> Vec<Message> {
        mem::take(&mut self.held)
    }

    pub(crate) fn receive(&mut self, from: UserId, message: Outbound) -> Vec<Message> {
        let from = u64::from(from);
        match message {
            Outbound::KeyPackage(bytes) => {
                if self.protocol_version > 0 {
                    let key_package = KeyPackageIn::tls_deserialize_exact_bytes(&bytes)
                        .unwrap()
                        .validate(self.provider.crypto(), ProtocolVersion::Mls10)
                        .unwrap();
                    self.key_packages.insert(from, key_package);
                }
                self.advance()
            }
            Outbound::CommitWelcome(bytes) => {
                let Some(round) = &mut self.round else {
                    return Vec::new();
                };
                if !round.committers.contains(&from) {
                    return Vec::new();
                }
                round.commits.insert(from, bytes);
                if self.hold_commits || round.commits.len() < round.committers.len() {
                    return Vec::new();
                }
                self.settle()
            }
            Outbound::TransitionReady(transition_id) => {
                if let Some(transition) = &mut self.transition
                    && transition.id == transition_id
                    && transition.waiting.remove(&from)
                {
                    transition.ready.insert(from);
                }
                self.finish_transition()
            }
            Outbound::InvalidCommitWelcome(transition_id) => {
                if let Some(transition) = &mut self.transition
                    && transition.id == transition_id
                {
                    transition.waiting.remove(&from);
                }
                if self.member_ids().contains(&from) {
                    self.removals.insert(from);
                    self.stale.insert(from);
                }
                self.finish_transition()
            }
        }
    }

    pub(crate) fn settle(&mut self) -> Vec<Message> {
        let Some(round) = self.round.take() else {
            return Vec::new();
        };
        for id in round.adds.keys() {
            self.key_packages.remove(id);
        }
        let Some((&winner, bytes)) = round.commits.first_key_value() else {
            for id in &round.removes {
                self.removals.remove(id);
            }
            return Vec::new();
        };
        let (commit, welcome) = split_commit_welcome(bytes);
        let initial = self.leaves.is_empty();
        let added: Vec<Id> = round
            .adds
            .keys()
            .copied()
            .filter(|&id| !(initial && id == winner))
            .collect();
        let mut announced: BTreeSet<Id> = self
            .member_ids()
            .into_iter()
            .filter(|&member| self.in_sync(member) && !added.contains(&member))
            .collect();
        announced.insert(winner);

        if initial {
            self.leaves.push(Some(winner));
        }
        for leaf in &mut self.leaves {
            if leaf.is_some_and(|id| round.removes.contains(&id)) {
                *leaf = None;
            }
        }
        while self.leaves.last() == Some(&None) {
            self.leaves.pop();
        }
        for &id in &added {
            match self.leaves.iter_mut().find(|leaf| leaf.is_none()) {
                Some(leaf) => *leaf = Some(id),
                None => self.leaves.push(Some(id)),
            }
            self.stale.remove(&id);
        }
        for id in &round.removes {
            self.removals.remove(id);
            self.stale.remove(id);
        }
        self.epoch += 1;

        let transition_id = self.allocate();
        let mut out = Vec::new();
        let mut recipients = BTreeSet::new();
        for id in announced {
            if self.connected.contains(&id) {
                let commit = self.corrupted(id, Corruption::Commit, &commit);
                out.push(dave(
                    id,
                    DaveOp::AnnounceCommit {
                        transition_id,
                        commit,
                    },
                ));
                recipients.insert(id);
            }
        }
        for id in added {
            if self.connected.contains(&id) {
                let welcome = self.corrupted(id, Corruption::Welcome, &welcome);
                out.push(dave(
                    id,
                    DaveOp::Welcome {
                        transition_id,
                        welcome,
                    },
                ));
                recipients.insert(id);
            }
        }
        if transition_id == 0 {
            out.extend(self.advance());
        } else {
            self.transition = Some(Transition {
                id: transition_id,
                waiting: recipients,
                ready: BTreeSet::new(),
            });
        }
        out
    }

    fn member_ids(&self) -> BTreeSet<Id> {
        self.leaves.iter().flatten().copied().collect()
    }

    fn connect(&mut self, id: Id, announce: bool) -> Vec<Message> {
        let user = UserId::from(id);
        let mut out = Vec::new();
        if announce {
            let others: Vec<UserId> = self
                .connected
                .iter()
                .map(|&other| UserId::from(other))
                .collect();
            for &other in &others {
                out.push((other, Signal::ClientsConnect(vec![user])));
            }
            if !others.is_empty() {
                out.push((user, Signal::ClientsConnect(others)));
            }
        }
        self.connected.insert(id);
        out.push((user, Signal::Dave(self.external_sender())));
        out
    }

    fn in_sync(&self, id: Id) -> bool {
        self.connected.contains(&id) && !self.stale.contains(&id)
    }

    fn reset_group(&mut self) {
        self.leaves.clear();
        self.epoch = 0;
        self.removals.clear();
        self.stale.clear();
    }

    fn allocate(&mut self) -> u16 {
        let transition_id = self.next_transition;
        self.next_transition = transition_id.wrapping_add(1).max(1);
        transition_id
    }

    fn finish_transition(&mut self) -> Vec<Message> {
        let Some(transition) = self
            .transition
            .take_if(|transition| transition.waiting.is_empty())
        else {
            return self.advance();
        };
        let executes: Vec<Message> = transition
            .ready
            .into_iter()
            .map(|id| {
                dave(
                    id,
                    DaveOp::ExecuteTransition {
                        transition_id: transition.id,
                    },
                )
            })
            .collect();
        let mut out = if self.hold_transitions {
            self.held.extend(executes);
            Vec::new()
        } else {
            executes
        };
        out.extend(self.advance());
        out
    }

    fn advance(&mut self) -> Vec<Message> {
        if self.protocol_version == 0 || self.round.is_some() || self.transition.is_some() {
            return Vec::new();
        }
        if self.leaves.is_empty() {
            self.open_initial_round()
        } else {
            self.open_round()
        }
    }

    fn open_initial_round(&mut self) -> Vec<Message> {
        let pending: BTreeSet<Id> = self
            .key_packages
            .keys()
            .copied()
            .filter(|id| self.connected.contains(id))
            .collect();
        if pending.len() < 2 {
            return Vec::new();
        }
        let adds: BTreeMap<Id, Vec<u8>> = pending
            .iter()
            .map(|&id| (id, self.propose_add(id)))
            .collect();
        let out = pending
            .iter()
            .map(|&recipient| {
                let data = proposals(
                    adds.iter()
                        .filter(|(id, _)| **id != recipient)
                        .map(|(_, proposal)| proposal.as_slice()),
                );
                dave(
                    recipient,
                    DaveOp::Proposals {
                        operation: ProposalsOp::Append,
                        data,
                    },
                )
            })
            .collect();
        self.round = Some(Round {
            recipients: pending.clone(),
            committers: pending,
            removes: BTreeSet::new(),
            adds,
            commits: BTreeMap::new(),
        });
        out
    }

    fn open_round(&mut self) -> Vec<Message> {
        let members = self.member_ids();
        let removes: BTreeSet<Id> = self.removals.intersection(&members).copied().collect();
        let adds: BTreeMap<Id, Vec<u8>> = self
            .key_packages
            .keys()
            .copied()
            .filter(|id| {
                self.connected.contains(id) && (!members.contains(id) || removes.contains(id))
            })
            .map(|id| (id, self.propose_add(id)))
            .collect();
        let recipients: BTreeSet<Id> = members
            .iter()
            .copied()
            .filter(|&member| self.in_sync(member))
            .collect();
        let committers: BTreeSet<Id> = recipients.difference(&removes).copied().collect();
        if (removes.is_empty() && adds.is_empty()) || committers.is_empty() {
            return Vec::new();
        }
        let removals: Vec<Vec<u8>> = removes.iter().map(|&id| self.propose_remove(id)).collect();
        let data = proposals(removals.iter().chain(adds.values()).map(Vec::as_slice));
        let out = recipients
            .iter()
            .map(|&recipient| {
                dave(
                    recipient,
                    DaveOp::Proposals {
                        operation: ProposalsOp::Append,
                        data: data.clone(),
                    },
                )
            })
            .collect();
        self.round = Some(Round {
            recipients,
            committers,
            removes,
            adds,
            commits: BTreeMap::new(),
        });
        out
    }

    fn propose_add(&self, id: Id) -> Vec<u8> {
        ExternalProposal::new_add::<OpenMlsRustCrypto>(
            self.key_packages[&id].clone(),
            self.group.clone(),
            GroupEpoch::from(self.epoch),
            &self.signer,
            SenderExtensionIndex::new(0),
        )
        .unwrap()
        .tls_serialize_detached()
        .unwrap()
    }

    fn propose_remove(&self, id: Id) -> Vec<u8> {
        let leaf = self
            .leaves
            .iter()
            .position(|leaf| *leaf == Some(id))
            .unwrap();
        ExternalProposal::new_remove::<OpenMlsRustCrypto>(
            LeafNodeIndex::new(u32::try_from(leaf).unwrap()),
            self.group.clone(),
            GroupEpoch::from(self.epoch),
            &self.signer,
            SenderExtensionIndex::new(0),
        )
        .unwrap()
        .tls_serialize_detached()
        .unwrap()
    }

    fn corrupted(&mut self, id: Id, corruption: Corruption, bytes: &[u8]) -> Bytes {
        let Some(index) = self
            .corruptions
            .iter()
            .position(|&target| target == (id, corruption))
        else {
            return Bytes::copy_from_slice(bytes);
        };
        self.corruptions.remove(index);
        Bytes::copy_from_slice(&bytes[..bytes.len() / 2])
    }
}

fn proposals<'a>(messages: impl Iterator<Item = &'a [u8]>) -> Bytes {
    vl_bytes(messages.flatten().copied().collect())
}

fn vl_bytes(content: Vec<u8>) -> Bytes {
    VLBytes::new(content)
        .tls_serialize_detached()
        .unwrap()
        .into()
}

fn split_commit_welcome(bytes: &[u8]) -> (Vec<u8>, Vec<u8>) {
    let (_, welcome) = MlsMessageIn::tls_deserialize_bytes(bytes).unwrap();
    let commit = &bytes[..bytes.len() - welcome.len()];
    (commit.to_vec(), welcome.to_vec())
}

enum Item {
    To(Message),
    From(UserId, Outbound),
}

pub(crate) struct Call {
    pub fake: ExternalSenderFake,
    pub now: Instant,
    pub sent: Vec<(UserId, Outbound)>,
    pub executed: Vec<(UserId, Executed)>,
    pub failures: Vec<(UserId, DaveError)>,
    pub remote: Vec<Message>,
    pub(crate) receive: ReceiveTuning,
    channel: ChannelId,
    clients: HashMap<UserId, Dave>,
    remote_users: HashSet<UserId>,
    queue: VecDeque<Item>,
}

impl Call {
    pub(crate) fn new(channel: ChannelId, protocol_version: u16) -> Self {
        Self {
            fake: ExternalSenderFake::new(channel, protocol_version),
            now: Instant::now(),
            sent: Vec::new(),
            executed: Vec::new(),
            failures: Vec::new(),
            remote: Vec::new(),
            receive: ReceiveTuning::default(),
            channel,
            clients: HashMap::new(),
            remote_users: HashSet::new(),
            queue: VecDeque::new(),
        }
    }

    pub(crate) fn join(&mut self, user: UserId) {
        let messages = self.fake.join(user);
        self.add(user, messages);
    }

    pub(crate) fn join_unannounced(&mut self, user: UserId) {
        let messages = self.fake.join_unannounced(user);
        self.add(user, messages);
    }

    pub(crate) fn join_remote(&mut self, user: UserId) {
        self.remote_users.insert(user);
        let messages = self.fake.join(user);
        self.enqueue(messages);
    }

    pub(crate) fn leave(&mut self, user: UserId) {
        self.clients.remove(&user);
        self.remote_users.remove(&user);
        let messages = self.fake.leave(user);
        self.enqueue(messages);
    }

    pub(crate) fn enqueue(&mut self, messages: Vec<Message>) {
        self.queue.extend(messages.into_iter().map(Item::To));
    }

    pub(crate) fn deliver(&mut self, user: UserId, op: DaveOp) {
        self.queue.push_back(Item::To((user, Signal::Dave(op))));
    }

    pub(crate) fn receive(&mut self, from: UserId, message: Outbound) {
        self.queue.push_back(Item::From(from, message));
    }

    pub(crate) fn run(&mut self) {
        loop {
            while let Some(item) = self.queue.pop_front() {
                self.step(item);
            }
            if !self.fake.has_round()
                || self.fake.hold_commits
                || self
                    .fake
                    .awaiting()
                    .iter()
                    .any(|user| self.remote_users.contains(user))
            {
                return;
            }
            let messages = self.fake.settle();
            self.enqueue(messages);
        }
    }

    pub(crate) fn dave(&self, user: UserId) -> &Dave {
        &self.clients[&user]
    }

    pub(crate) fn dave_mut(&mut self, user: UserId) -> &mut Dave {
        self.clients.get_mut(&user).unwrap()
    }

    pub(crate) fn speak(&mut self, peer: UserId, opus: &[u8]) -> Vec<u8> {
        let dave = self.dave_mut(peer);
        if dave.send_mode() == SendMode::Plain {
            return opus.to_vec();
        }
        let mut frame = Vec::new();
        dave.encrypt(opus, &mut frame).unwrap();
        frame
    }

    pub(crate) fn sent_by(&self, user: UserId) -> Vec<Outbound> {
        self.sent
            .iter()
            .filter(|(from, _)| *from == user)
            .map(|(_, message)| message.clone())
            .collect()
    }

    pub(crate) fn executed_by(&self, user: UserId) -> Vec<Executed> {
        self.executed
            .iter()
            .filter(|(by, _)| *by == user)
            .map(|(_, executed)| *executed)
            .collect()
    }

    fn add(&mut self, user: UserId, messages: Vec<Message>) {
        self.enqueue(messages);
        let mut dave = Dave::new(user, self.channel, &self.receive);
        let mut out = Vec::new();
        if let Err(error) = dave.start(self.fake.protocol_version(), &mut out) {
            self.failures.push((user, error));
        }
        self.clients.insert(user, dave);
        self.queue
            .extend(out.into_iter().map(|message| Item::From(user, message)));
    }

    fn step(&mut self, item: Item) {
        match item {
            Item::From(user, message) => {
                self.sent.push((user, message.clone()));
                let messages = self.fake.receive(user, message);
                self.enqueue(messages);
            }
            Item::To((user, signal)) => {
                let Some(dave) = self.clients.get_mut(&user) else {
                    if self.remote_users.contains(&user) {
                        self.remote.push((user, signal));
                    }
                    return;
                };
                let mut out = Vec::new();
                match signal {
                    Signal::Dave(op) => match dave.handle(op, self.now, &mut out) {
                        Ok(Some(executed)) => self.executed.push((user, executed)),
                        Ok(None) => {}
                        Err(error) => self.failures.push((user, error)),
                    },
                    Signal::ClientsConnect(users) => dave.clients_connect(&users),
                    Signal::ClientDisconnect(left) => dave.client_disconnect(left),
                }
                self.queue
                    .extend(out.into_iter().map(|message| Item::From(user, message)));
            }
        }
    }
}
