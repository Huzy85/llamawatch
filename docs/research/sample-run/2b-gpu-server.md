# Stage 2 notes: GPU and server builds (Haiku researcher, 2026-10-07)
# Not yet verified. Key claims + flags.
Claims to check:
- RTX 5090 UK new GBP 2,099 (Palit, Overclockers) to 2,348 (ASUS Astral), Oct 2026
- RTX 3090 used GBP 600-900 (PriceSpy UK June 2026; eBay EU)
- Arc Pro B60 24GB GBP 620, 456 GB/s, measured 70-145W (Igor's Lab)
- R9700 32GB GBP 1,173-1,268 (Teqex, We Are Sync), 300W
- Used EPYC 7642 server + 64GB GBP 2,232 (one listing)
- gpt-oss-120b MXFP4 ~61GB
Flags:
- CONFLICT: RTX 5090 UK GBP 2,099-2,348 here vs GBP 3,799-3,999 in landscape notes (gpupricehistory). Must settle in Stage 4.
- RTX 3090 power "estimated ~280W": spec TDP is 350W; agent guessed instead of searching. Gap mislabelled as finding.
- "Full precision 120B needs 61GB": wrong, 61GB is the MXFP4 (4-bit) size. Mixes precision terms.
- Misses the main CPU+GPU MoE route (llama.cpp --n-cpu-moe with 128GB+ system RAM and one GPU) and DDR4 EPYC builds with 256GB+.
Prompt lesson: researcher should label estimates as estimates, never as findings; Stage 1 should name known techniques (MoE expert offload) the gatherer must cover.
