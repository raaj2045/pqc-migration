# Threats to validity

Section VI-D of the paper summarises this document.

- **Deployment.** The measurements describe the implemented migration path, not production Ethereum or Cosmos deployments. All components ran on one host, the Cosmos network had one validator, and Ethereum ran as a controlled devnet. Network propagation, public-network contention, heterogeneous validators and geographic distribution are therefore not represented.
- **Mainnet differences.** The devnet's slot time (6 s) and several node limits differ from mainnet (12 s slots). Mainnet timing statements are extrapolations, not measurements.
- **Node configuration.** The raised RPC and body limits and unlimited block gas used in the batching experiments expose protocol and implementation limits. They are not production configuration recommendations.
- **Microbenchmarks.** These depend on the software versions, cryptographic implementations and processor listed in [experimental-setup.md](experimental-setup.md).
- **Mock verifier.** Throughput measurements used a mock acknowledgement verifier, so they characterise voucher delivery separately from the much more expensive real SP1 acknowledgement-proving path. That path is measured and reported separately.
- **Repetitions.** Cryptographic benchmarks are medians of ten runs; throughput figures carry 95 % confidence intervals over 15 runs per configuration; no runs were excluded. Real-verifier proving was measured once per batch size on a single deployment.
