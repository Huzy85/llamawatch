# Stage 2 notes: measured speeds (Haiku researcher, 2026-10-07)
# Not yet verified. Key claims + flags.
Claims to check:
- Strix Halo gpt-oss-120b ~53 tok/s gen (Framework community thread 72521)
- Strix Halo Qwen3.5-122B-A10B 18-19 tok/s (Level1Techs thread)
- M4 Max 128GB Qwen3.5-122B-A10B Q4 40-55 tok/s (llmcheck.net)
- M3 Ultra Qwen3.5-122B ~38-41 tok/s (omlx.ai)
- DGX Spark gpt-oss-120b ~60 tok/s; Llama 70B dense 2.7 tok/s (LMSYS, Oct 2025)
Flags (impossible on their face):
- "RTX 5090 32GB runs Qwen3-235B-A22B Q4_K_M at 118 tok/s": model is ~130GB at Q4, cannot fit 32GB. Content-farm source (markaicode).
- "single RTX 3090 runs gpt-oss-120b at 267-455 tok/s": 61GB model on a 24GB card; cited Reddit post is Feb 2025, before gpt-oss existed (Aug 2025).
- "dual 3090 715 tok/s" is batched throughput (200 users), not one person's speed. Batch vs single-user mixed.
- "CPU-only cannot run 100B+": wrong; no benchmark found is not the same as cannot. MoE runs on CPU at low speed.
- "Llama 70B on H200 ~60 tok/s" contradicts the 387 tok/s line in the same notes.
- Blogs/content farms labelled [primary].
Prompt lesson: Stage 2 must state batch size and context for every speed; Stage 4 must run a "does it fit in memory" check.
