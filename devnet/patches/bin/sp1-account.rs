//! Read-only Succinct account helper.
//!
//! `balance`               - confirm the requester account is funded before a paid run.
//! `settlement <req_id>`   - after a run, report what the auction actually charged,
//!                           so the settled price can be compared to the pre-flight ceiling.
//!
//! Both are read-only queries. Neither requests a proof or spends anything.

use std::error::Error;

use sp1_sdk::blocking::ProverClient;
use sp1_sdk::network::{NetworkMode, B256};

const WEI: u128 = 1_000_000_000_000_000_000;

fn prove_amount(wei: u128) -> String {
    format!("{}.{:018}", wei / WEI, wei % WEI)
}

fn opt_prove(v: Option<&String>) -> String {
    v.and_then(|s| s.parse::<u128>().ok())
        .map_or_else(|| "-".to_string(), prove_amount)
}

fn main() -> Result<(), Box<dyn Error>> {
    let net = ProverClient::builder().network_for(NetworkMode::Mainnet).build();
    let mut args = std::env::args().skip(1);

    match args.next().as_deref() {
        None | Some("balance") => {
            let raw: u128 = net.get_balance()?.to_string().parse()?;
            println!("balance : {} PROVE", prove_amount(raw));
            println!("raw wei : {raw}");
        }
        Some("settlement") => {
            let id: B256 = args.next().ok_or("usage: sp1-account settlement <request_id>")?.parse()?;
            let req = net.get_proof_request(id)?.ok_or("request not found")?;

            println!("request            : 0x{}", hex_of(&req.request_id));
            println!("fulfillment_status : {}", req.fulfillment_status);
            println!("settlement_status  : {}", req.settlement_status);
            println!("fulfiller          : {}", req.fulfiller.as_ref().map_or_else(|| "-".to_string(), |f| format!("0x{}", hex_of(f))));
            println!("cycles             : {}", req.cycles.map_or_else(|| "-".to_string(), |c| c.to_string()));
            println!("gas_limit          : {}", req.gas_limit);
            println!("gas_used           : {}", req.gas_used.map_or_else(|| "-".to_string(), |g| g.to_string()));
            println!("gas_price /PGU     : {}", req.gas_price.map_or_else(|| "-".to_string(), |p| prove_amount(u128::from(p))));
            println!("base_fee           : {} PROVE", opt_prove(req.base_fee.as_ref()));
            println!("--");
            println!("DEDUCTED (actual)  : {} PROVE", opt_prove(req.deduction_amount.as_ref()));
            println!("refunded           : {} PROVE", opt_prove(req.refund_amount.as_ref()));
        }
        Some(other) => return Err(format!("unknown subcommand: {other}").into()),
    }
    Ok(())
}

fn hex_of(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
