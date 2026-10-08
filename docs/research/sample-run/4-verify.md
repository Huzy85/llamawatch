# Stage 4: verification log (fresh context, sources reopened 2026-10-07)

| Claim | Result | What was checked | Change |
|---|---|---|---|
| C1 | weakened | techradar/guru3d presale EUR prices; no UK listing found | "About GBP 1,500-2,000 by currency conversion; no UK retailer price confirmed" |
| C2 | corrected | Framework thread reopened: 46.08 tok/s, MXFP4, ROCm 7 rc, pre-production unit, 21 Sep 2025 | 53.4 -> 46 tok/s; note pre-production and date |
| C3 | dropped | Level1Techs thread reopened: no Qwen3.5-122B, gpt-oss-120b, GLM or Qwen3-235B result in it | removed |
| C4 | corrected | gpupricehistory.com/uk reopened: lowest GBP 2,019 (Gigabyte Gaming OC), updated 5 Oct 2026 | GBP 3,799 figure dropped; use GBP 2,019 |
| C5 | corrected | Apple UK store: M5 Max 36GB, M5 Max 64GB, M5 Ultra 96GB configs; lewislovelock: GBP 2,499 / 3,799 / 5,499 | 36GB cannot hold a 100B model. Cheapest 64GB is GBP 3,799. No 128GB Mac under GBP 3,000 |
| C6 | weakened | llmcheck.net reopened: page states figures are "transparent estimates", not measured | kept only as an estimate, labelled |
| C7 | corrected | omlx.ai reopened: 41.5 tok/s, 8-bit, M3 Ultra 80c 512GB, 4K ctx, 31 Mar 2026 | specific single result; machine far over budget |
| C8 | corrected | LMSYS reopened: 273 GB/s confirmed; 70B is FP8 2.7 tok/s (SGLang); gpt-oss-120b decode figure not stated | 60 tok/s figure dropped; keep bandwidth and 70B result |
| C9 | confirmed | NVIDIA forum: USD 4,699 after 23 Feb 2026 rise, EU EUR 4,800; UK GBP 3,699.97 at Oct 2025 (glukhov) | UK price is a year old and likely higher now |
| C10 | confirmed | PriceSpy from GBP 900; eBay GBP 615-660 earlier in 2026 | none |
| C11 | dropped | memory check: Qwen3-235B at Q4 is ~130GB, cannot fit 32GB VRAM | removed |
| C12 | corrected | hardware-corner reopened (10 Nov 2025): 5090 + 64GB DDR4 gave 3.4 -> 8 tok/s; 3090 0.9 -> 1.6 tok/s. Separate user report: 3090 + 64GB DDR4 ~15 tok/s | conflict kept; test system had too little RAM for a 61GB model |
| C13 | confirmed | Ofgem news page via search: 26.32p/kWh, standing charge 54.83p/day, 1 Oct-31 Dec 2026 | none |
| C14 | confirmed | consistent across notes; "full precision 61GB" wording was wrong (61GB is 4-bit) | wording fixed |
| C15 | weakened | not reopened (budget) | caveat |
| C16 | confirmed | DGX Spark rise attributed to memory supply (NVIDIA forum) | none |
| C17 | dropped | source discussed a smaller-memory Mac; omlx shows 122B running on M3 Ultra | removed |

Gap round (Stage 5): ran once, for "5090 + system RAM offload speed". Found C12. Nothing else run.
Totals: 17 claims; 5 confirmed, 6 corrected, 3 weakened, 3 dropped.
