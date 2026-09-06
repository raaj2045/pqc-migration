//! Reference tooling — NOT auto-applied by setup-eureka-checkout.sh.
//!
//! Answers one question cheaply: is the SP1 ICS07 Tendermint update-client
//! guest program's Ed25519 signature verification running SP1's hardware
//! precompiles, or plain software crypto? Runs the guest ELF in SP1 execute
//! mode (no proving — same cost as `cost-estimator`) against the repo's
//! own `update_client_happy_path` fixture, and prints the cycle count plus
//! the full `syscall_counts` map. Nonzero `ED_ADD` / `ED_DECOMPRESS` means
//! the precompiled path is active.
//!
//! Finding as of 2026-09-03 (SIBE pinned commit 604476b, sp1-sdk 6.1):
//! there is no `ed25519-consensus` entry in
//! `ibc-solidity/programs/sp1-programs/Cargo.toml`'s `[patch.crates-io]`,
//! but `curve25519-dalek-ng` (which `ed25519-consensus`'s point arithmetic
//! depends on) IS patched to `sp1-patches/curve25519-dalek-ng`. So Ed25519
//! verification is transitively accelerated despite the missing direct
//! patch entry. Execute-mode run: 359,686 total cycles, ED_ADD = 1501,
//! ED_DECOMPRESS = 4 (nonzero — confirms the precompiled path).
//!
//! To reproduce:
//!   1. Have a solidity-ibc-eureka checkout set up (setup-eureka-checkout.sh)
//!      with the update-client ELF already built (`just build-sp1-programs`,
//!      or it's already under
//!      ibc-solidity/programs/sp1-programs/target/elf-compilation/riscv64im-succinct-zkvm-elf/release/).
//!   2. Copy this file to
//!      packages/sp1-ics07-tendermint-prover/examples/execute_probe.rs
//!   3. Add to packages/sp1-ics07-tendermint-prover/Cargo.toml:
//!        [dev-dependencies]
//!        ibc-client-tendermint = { workspace = true, features = ["serde"] }
//!        serde      = { workspace = true, features = ["derive"] }
//!        serde_json = { workspace = true, features = ["std"] }
//!        hex        = { workspace = true }
//!   4. From packages/sp1-ics07-tendermint-prover/:
//!        CARGO_TARGET_DIR="$HOME/.cache/sibe/target" cargo run --example execute_probe
//!   5. Revert steps 2-3 when done (this is scratch tooling, not meant to
//!      live in the checkout permanently — it mutates Cargo.toml/Cargo.lock
//!      state that setup-eureka-checkout.sh doesn't expect).
//!
//! To get a true "unpatched" baseline for comparison, remove the
//! curve25519-dalek-ng line from
//! ibc-solidity/programs/sp1-programs/Cargo.toml's [patch.crates-io],
//! regenerate the lockfile entry, rebuild the ELF with
//! `cargo prove build -p sp1-ics07-tendermint-update-client`, and re-run
//! this probe against the new ELF. Not done as part of this investigation.

use alloy_sol_types::SolValue;
use ibc_client_tendermint::types::proto::v1::{
    ClientState as ProtoClientState, ConsensusState as ProtoConsensusState,
};
use ibc_eureka_solidity_types::msgs::IICS02ClientMsgs::Height as SolHeight;
use ibc_eureka_solidity_types::msgs::IICS07TendermintMsgs::{
    ClientState as SolClientState, ConsensusState as SolConsensusState, SupportedZkAlgorithm,
    TrustThreshold as SolTrustThreshold,
};
use prost::Message;
use serde::Deserialize;
use sp1_sdk::blocking::{Prover, ProverClient};
use sp1_sdk::{Elf, SP1Stdin};
use std::fs;

#[derive(Debug, Deserialize)]
struct UpdateClientMessageFixture {
    client_message_hex: String,
}

#[derive(Debug, Deserialize)]
struct UpdateClientFixture {
    client_state_hex: String,
    consensus_state_hex: String,
    update_client_message: UpdateClientMessageFixture,
}

fn main() {
    let fixture_path =
        "../tendermint-light-client/fixtures/update_client_happy_path.json";
    let fixture: UpdateClientFixture =
        serde_json::from_str(&fs::read_to_string(fixture_path).expect("read fixture"))
            .expect("parse fixture");

    // --- client state: proto -> Sol ABI struct -------------------------
    let cs_bytes = hex::decode(&fixture.client_state_hex).unwrap();
    let proto_cs = ProtoClientState::decode(&cs_bytes[..]).unwrap();
    let trust_level = proto_cs.trust_level.unwrap();
    let latest_height = proto_cs.latest_height.unwrap();
    let sol_client_state = SolClientState {
        chainId: proto_cs.chain_id,
        trustLevel: SolTrustThreshold {
            numerator: trust_level.numerator as u8,
            denominator: trust_level.denominator as u8,
        },
        latestHeight: SolHeight {
            revisionNumber: latest_height.revision_number,
            revisionHeight: latest_height.revision_height,
        },
        trustingPeriod: proto_cs.trusting_period.unwrap().seconds as u32,
        unbondingPeriod: proto_cs.unbonding_period.unwrap().seconds as u32,
        isFrozen: proto_cs.frozen_height.is_some(),
        zkAlgorithm: SupportedZkAlgorithm::Groth16,
    };

    // --- consensus state: proto -> Sol ABI struct -----------------------
    let cons_bytes = hex::decode(&fixture.consensus_state_hex).unwrap();
    let proto_cons = ProtoConsensusState::decode(&cons_bytes[..]).unwrap();
    let ts = proto_cons.timestamp.unwrap();
    let timestamp_ns = (ts.seconds as u128) * 1_000_000_000 + ts.nanos as u128;
    let sol_consensus_state = SolConsensusState {
        timestamp: timestamp_ns,
        root: {
            let bytes: [u8; 32] = proto_cons.root.unwrap().hash.try_into().unwrap();
            bytes.into()
        },
        nextValidatorsHash: {
            let bytes: [u8; 32] = proto_cons.next_validators_hash.try_into().unwrap();
            bytes.into()
        },
    };

    // --- header: fixture hex is already raw protobuf bytes --------------
    let header_bytes = hex::decode(&fixture.update_client_message.client_message_hex).unwrap();

    // --- time: 1 hour after trusted consensus state timestamp -----------
    let time: u128 = timestamp_ns + 3600 * 1_000_000_000;

    let mut stdin = SP1Stdin::new();
    stdin.write_vec(sol_client_state.abi_encode());
    stdin.write_vec(sol_consensus_state.abi_encode());
    stdin.write_vec(header_bytes);
    stdin.write_vec(time.to_le_bytes().to_vec());

    let elf_path = "../../ibc-solidity/programs/sp1-programs/target/elf-compilation/\
riscv64im-succinct-zkvm-elf/release/sp1-ics07-tendermint-update-client";
    let elf: Elf = fs::read(elf_path).expect("read elf").into();

    let cpu = ProverClient::builder().cpu().build();
    let (_public_values, report) = cpu.execute(elf, stdin).run().expect("execute failed");

    println!("total cycles: {}", report.total_instruction_count());
    println!("syscall_counts:");
    for (syscall, count) in report.syscall_counts.iter() {
        println!("  {:?} = {}", syscall, count);
    }
}
