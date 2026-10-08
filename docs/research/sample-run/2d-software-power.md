# Stage 2 notes: software and running costs (Haiku researcher, 2026-10-07)
# Not yet verified. Key claims + flags.
Claims to check:
- Ofgem cap from Oct 2026: 26.32p/kWh (ofgem.gov.uk press release) -> verify exact figure
- Strix Halo: Vulkan (RADV) faster at normal context, ROCm better at very long context (Phoronix ROCm 7 review)
- Strix Halo idle ~11W, load 70-130W
- DGX Spark: GB10 140W TDP, system 240W peak (NVIDIA docs); vLLM + llama.cpp supported
- RTX 3090: ~155W per card during vLLM inference (arXiv 2509.08867); idle 13-50W
- Intel Arc Pro B60: IPEX-LLM abandoned/archived; llama.cpp via Vulkan/SYCL
- R9700: ROCm llama.cpp benchmarks exist (github), single-card 100B unclear
Flags:
- ARITHMETIC WRONG + self-contradicting: Strix Halo at 100W listed as GBP 52-78/yr and GBP 261/yr. Correct: 0.1kW x 8760h x GBP0.2632 = GBP 230/yr if run 24/7.
- "Apple fails on 100B, capped ~65B" is false for 128GB+ Macs; source talked about a 64GB machine. Context-stretch error.
- Calls Strix Halo "laptop form factor" -> mostly mini PCs.
Prompt lesson: models must not do money/energy maths; the app computes it from stated watts and hours-per-day.
