# Paper supplement

Supplementary material for *Migrating Legacy Blockchain Assets to Post-Quantum Accounts over Light-Client Bridges: Implementation and Evaluation* (IEEE Internet Computing, manuscript IC-2026-06-0088, revision 2).

The paper links to this folder at tag `ic-2026-r2`. Each file below holds material that was shortened in the paper to meet the word limit, or points to the existing repository document that holds it in full.

| Topic | Where | Paper section |
|---|---|---|
| Threat model, guarantees and trust assumptions | [threat-model.md](threat-model.md) | VI-D |
| Formal verification, fuzzing and adversarial testing | [verification-and-testing.md](verification-and-testing.md) | III-D, VI-D |
| Threats to validity | [threats-to-validity.md](threats-to-validity.md) | VI-D |
| Experimental setup (hardware, software versions, node configuration) | [experimental-setup.md](experimental-setup.md) | V |
| Comparison with related systems | [related-work.md](related-work.md) | II-A |
| Implementation notes (light-client initialisation, relayer steps) | [implementation-notes.md](implementation-notes.md) | V-B, V-C, V-D |
| Evaluation notes (storage method, per-step latency, block size) | [evaluation-notes.md](evaluation-notes.md) | VI-B, VI-C |

Raw results, scripts and figures are under [`experiments/`](../experiments) and [`benchmarks/`](../benchmarks).
