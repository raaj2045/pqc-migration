//! Pre-flight cost estimate for a single SP1 network proof.
//!
//! Reads a `program.bin` / `stdin.bin` pair dumped by `SP1_DUMP=1` (which the SDK
//! writes just before it would submit, then exits), executes the program locally
//! to obtain the exact `gas_limit` the SDK would submit, and multiplies it by the
//! live auction price. Both inputs are free: local execution touches no network,
//! and the auction-parameter query is read-only and needs no PROVE balance.

use std::error::Error;

use sp1_sdk::blocking::{Prover, ProverClient};
use sp1_sdk::network::proto::GetProofRequestParamsResponse;
use sp1_sdk::network::NetworkMode;
use sp1_sdk::{Elf, SP1ProofMode, SP1Stdin};

const WEI: u128 = 1_000_000_000_000_000_000;

fn prove_amount(wei: u128) -> String {
    format!("{}.{:018}", wei / WEI, wei % WEI)
}

fn main() -> Result<(), Box<dyn Error>> {
    let mut args = std::env::args().skip(1);
    let elf_path = args.next().unwrap_or_else(|| "program.bin".into());
    let stdin_path = args.next().unwrap_or_else(|| "stdin.bin".into());

    let elf: Elf = std::fs::read(&elf_path)?.into();
    let stdin: SP1Stdin = bincode::deserialize(&std::fs::read(&stdin_path)?)?;

    // 1. Local execution. `get_execution_limits` in sp1-sdk submits exactly
    //    `report.gas()` as the gas limit when none is set explicitly.
    let cpu = ProverClient::builder().cpu().build();
    let (_public_values, report) = cpu.execute(elf, stdin).run()?;
    let pgu = report.gas().ok_or("execution report carried no gas value")?;
    let cycles = report.total_instruction_count();

    // 2. Live auction parameters. Read-only; requires a key to sign the request
    //    but no PROVE balance.
    let net = ProverClient::builder().network_for(NetworkMode::Mainnet).build();
    let (max_price_per_pgu, base_fee) = match net.get_proof_request_params(SP1ProofMode::Groth16)? {
        GetProofRequestParamsResponse::Auction(p) => (
            p.max_price_per_pgu.parse::<u128>()?,
            p.base_fee.parse::<u128>()?,
        ),
        GetProofRequestParamsResponse::Unsupported => {
            return Err("auction parameters unsupported for this network mode".into())
        }
    };

    let ceiling = base_fee + max_price_per_pgu * u128::from(pgu);

    println!("cycles                    : {cycles}");
    println!("prover gas (PGU)          : {pgu}");
    println!("base fee                  : {} PROVE", prove_amount(base_fee));
    println!("max price per PGU         : {} PROVE", prove_amount(max_price_per_pgu));
    println!("--");
    println!("ceiling, 1 proof          : {} PROVE", prove_amount(ceiling));
    println!("ceiling, 2 proofs (test)  : {} PROVE", prove_amount(ceiling * 2));
    println!();
    println!("Ceiling, not a quote: the descending auction settles at or below this.");
    Ok(())
}
