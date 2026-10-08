# Stage 1 scope (run 2026-10-07, written by Claude Opus 5.5)

## Restated question
What is the best hardware a UK buyer can get for under GBP 3,000 (new or used, whole system) to run 100B+ parameter language models locally, as of October 2026? "Best" means usable speed (generation and prompt processing) on 100B+ models at 4-bit or similar, weighed against price, power, noise and software support.

## Unclear points
- Dense vs MoE: most 100B+ open models in 2026 are MoE (gpt-oss-120b, Qwen3 235B-A22B, GLM-4.5-Air, Qwen3.5-122B-A10B). Reading: MoE first, dense 100B+ noted where data exists.
- New vs used: both allowed, used prices flagged as volatile.
- Single user, home use, not serving many users.

## Sub-questions
1. Unified-memory machines (AMD Strix Halo 128GB mini PCs, Apple Mac Studio / Mac mini, NVIDIA DGX Spark / GB10 boxes): UK prices, memory size, bandwidth.
2. Discrete GPU and server builds under GBP 3,000 (multi used RTX 3090, RTX 5090, Intel Arc Pro B60, AMD Radeon AI PRO R9700, EPYC/Xeon with lots of DDR5 RAM): parts, prices, total VRAM/RAM, bandwidth.
3. Measured speed: tokens per second (generation and prompt processing) for 100B+ MoE models on these platforms, from benchmarks.
4. Software and running costs: llama.cpp / vLLM / MLX / ROCm / CUDA support, power draw, noise, reliability, resale.
5. Landscape and timing: what the local-LLM community recommends now, and hardware due in the next 6 months that changes the answer.

## Landscape
r/LocalLLaMA, llama.cpp benchmark threads, Level1Techs, ServeTheHome, Apple MLX community, Framework Desktop owners.

## Done means
- At least 4 platforms compared on price, memory, bandwidth, measured tok/s.
- UK prices with dates.
- A clear pick plus runner-up, and who should choose differently.
