# Stage 3: claims (written from notes 2a-2e, 2026-10-07)

- C1: A 128GB Ryzen AI Max+ 395 ("Strix Halo") mini PC costs about EUR 1,499-1,799 presale / USD 1,999 list, well under GBP 3,000. Support: techradar, guru3d, gmktec store. Strength: moderate (no direct UK price).
- C2: Strix Halo runs gpt-oss-120b at ~53 tok/s generation. Support: Framework community thread. Strength: strong (measured).
- C3: Strix Halo runs Qwen3.5-122B-A10B at 18-19 tok/s. Support: Level1Techs thread. Strength: moderate.
- C4: RTX 5090 costs GBP 3,799-3,999 in the UK (landscape notes) / GBP 2,099-2,348 (GPU notes). CONFLICT.
- C5: A Mac Studio with enough memory for 100B+ starts at GBP 2,499 (M5 Max 36GB). Strength: moderate.
- C6: M4 Max 128GB runs Qwen3.5-122B-A10B at 40-55 tok/s. Support: llmcheck.net. Strength: moderate.
- C7: M3 Ultra runs Qwen3.5-122B at ~38-41 tok/s. Support: omlx.ai. Strength: moderate.
- C8: DGX Spark (128GB, 273 GB/s) runs gpt-oss-120b ~60 tok/s; dense 70B only 2.7 tok/s. Support: LMSYS. Strength: strong.
- C9: DGX Spark is over budget in the UK. Support: pi3g USD 4,699; Scan GBP 3,699. Strength: moderate.
- C10: Used RTX 3090 24GB costs GBP 600-900 in the UK. Support: PriceSpy, eBay. Strength: moderate.
- C11: RTX 5090 32GB runs Qwen3-235B-A22B Q4_K_M at 118 tok/s. Support: markaicode. Strength: weak.
- C12: With --n-cpu-moe, one GPU plus system RAM runs gpt-oss-120b. Support: hardware-corner. Strength: moderate (gap-round find).
- C13: UK electricity cap unit rate from 1 Oct 2026 is 26.32p/kWh. Support: Ofgem. Strength: strong.
- C14: gpt-oss-120b MXFP4 needs ~61GB; 120B at Q4 ~70GB. Support: several guides. Strength: moderate.
- C15: Intel Arc Pro B60 24GB ~GBP 620; IPEX-LLM archived, llama.cpp via Vulkan/SYCL. Strength: weak/moderate.
- C16: GPU and memory prices rose through 2026 on DRAM shortage. Support: techenclave, DGX Spark rise. Strength: moderate.
- C17: Apple Silicon cannot run 100B models (capped ~65B). Support: inventivehq. Strength: weak.

## Conflicts
- RTX 5090 UK price (C4).
- Strix Halo gpt-oss-120b: 53.4 (notes) vs ROCm/Vulkan figures in other posts.
- Single-card 3090 gpt-oss-120b "267-455 tok/s" (impossible) vs 1.6 tok/s offload (hardware-corner) vs ~15 tok/s user report.

## Still missing
- Speed of RTX 5090 + 128GB DDR5 with expert offload on 100B+ models (decides the runner-up).
- Direct UK prices for Strix Halo boxes.
