# A 128GB Strix Halo box is the £3,000 answer

*Question: What is the best hardware a UK buyer can get for under £3,000 to run 100B+ parameter language models locally, as of October 2026?*
*Depth: Standard. Run: 7 October 2026. Sources: 17. Claims checked: 17 (5 confirmed, 6 corrected, 3 weakened, 3 dropped).*

## Summary

A mini PC built on AMD's Ryzen AI Max+ 395 chip ("Strix Halo") with 128GB of memory is the best way to run 100B+ models for under £3,000 in October 2026. It is the only machine found in budget where a 4-bit 120B model fits entirely in memory the graphics chip can use, and it has a measured speed of about 46 tokens (word pieces) per second on gpt-oss-120b [1]. European presale prices of €1,499 to €1,799 [13] and a US list price of $1,999 to $2,599 [14] put it at roughly £1,500 to £2,000, which leaves room for storage and a spare.

The alternatives each fail one test. A Mac with enough memory starts at £3,799 for 64GB, which is still too little, so 128GB Macs are well over budget [5][6]. NVIDIA's DGX Spark has the right memory size but cost £3,699.97 in the UK a year ago and has risen since [3][4]. An RTX 5090 now costs about £2,019 [7], but its 32GB cannot hold a 120B model, and the one measured test of it with the rest of the model in system memory gave 8 tokens per second [11].

The biggest caveat: the 46 tokens per second figure comes from one pre-production machine tested in September 2025 [1], and no UK retailer price for a 128GB Strix Halo box was confirmed. Check both before buying.

## Key numbers

| What | Number | Source |
|---|---|---|
| Strix Halo, gpt-oss-120b generation speed | 46 tokens/s (MXFP4, ROCm, pre-production unit) | [1] |
| 128GB Strix Halo mini PC price | €1,499-€1,799 presale; $1,999-$2,599 list | [13][14] |
| Cheapest RTX 5090 in the UK | £2,019 (5 Oct 2026) | [7] |
| Cheapest Mac Studio with 64GB | £3,799 | [6] |
| DGX Spark UK price, Oct 2025 | £3,699.97 (since raised in US and EU) | [3][4] |
| DGX Spark memory bandwidth | 273 GB/s | [2] |
| Memory needed for gpt-oss-120b at 4-bit | about 61GB | [11] |
| UK electricity unit rate, Oct-Dec 2026 | 26.32p per kWh | [12] |

## Background

A language model's size is counted in parameters. At 4-bit precision, the usual way to run large models at home, each billion parameters needs a little over half a gigabyte of memory. A 120B model therefore needs about 61 to 70GB before any working space for the conversation itself [11].

Most current large open models are "mixture of experts" (MoE) models. gpt-oss-120b, Qwen3.5-122B-A10B and Qwen3-235B-A22B hold all their parameters in memory but use only a small slice of them for each word. That makes them much faster than a dense model of the same size, provided the whole model fits in fast memory. The LMSYS test of DGX Spark shows the difference: a dense 70B model ran at 2.7 tokens per second [2], which is too slow for conversation.

Speed when generating text depends mostly on memory bandwidth, the rate at which the chip can read the model. So the buying question has two parts: does the model fit, and how fast can the memory be read.

## Only unified-memory machines fit a 120B model in budget

There are three ways to get 64GB or more of memory a graphics chip can use. One is a unified-memory machine (Strix Halo, Apple Silicon, DGX Spark), where the processor and graphics share one large pool. Another is several graphics cards whose memory adds up. The last is one graphics card with the rest of the model in ordinary system memory.

Within £3,000, only Strix Halo offers 128GB of unified memory. Apple's current Mac Studio range starts with 36GB on the M5 Max at £2,499, then 64GB at £3,799, and 96GB on the M5 Ultra at £5,499 [5][6]. The 36GB model cannot hold a 100B model at all. DGX Spark has 128GB but sat at £3,699.97 in the UK in October 2025 [4], and NVIDIA raised the US price from $3,999 to $4,699 in February 2026, citing memory supply [3].

| Machine | Usable memory | UK price found | Fits 120B at 4-bit? |
|---|---|---|---|
| Strix Halo mini PC (128GB) | 128GB shared | ~£1,500-£2,000 (converted) [13][14] | Yes |
| Mac Studio M5 Max (36GB) | 36GB | £2,499 [6] | No |
| Mac Studio M5 Max (64GB) | 64GB | £3,799 [6] | Barely, over budget |
| DGX Spark | 128GB shared | £3,699.97 (2025) [4] | Yes, over budget |
| RTX 5090 desktop | 32GB on card | £2,019 card only [7] | No, needs system RAM |
| 2 x used RTX 3090 | 48GB across cards | from £900 each [8] | No, needs system RAM |

## Strix Halo runs gpt-oss-120b at about 46 tokens per second

The only measured result for a 100B+ model on Strix Halo that survived checking is 46.08 tokens per second for gpt-oss-120b in its native 4-bit format, posted on the Framework community forum on 21 September 2025 [1]. That is comfortable reading speed. The test used a pre-production Framework Desktop and a release-candidate version of AMD's ROCm software, so a shipping machine on current software may differ in either direction.

A second claim, 18 to 19 tokens per second for Qwen3.5-122B-A10B on the Level1Techs forum, was dropped: the thread, when reopened, contains no result for that model. No checked figure exists for Qwen3-235B on this chip. At 4-bit that model needs around 130GB, which is more than the 128GB in the machine, so it would need a smaller 3-bit or 2-bit version.

Software choice matters on this chip. Phoronix testing found AMD's open Vulkan driver faster for normal conversation lengths, with ROCm pulling ahead only at very long contexts [15]. This was not re-checked for this report.

## A single RTX 5090 is fast for small models and slow for large ones

UK RTX 5090 prices have fallen back. One researcher's note gave £3,799 to £3,999, but the price tracker it cited shows £2,019 for a Gigabyte Gaming OC on 5 October 2026 [7]. A complete desktop around it, with 128GB of system memory and a 1,000W power supply, would land close to £3,000.

The card has 32GB, so a 120B model must split, with the experts in system memory and the rest on the card (llama.cpp's `--n-cpu-moe` option). The one checked test of this, from November 2025, measured gpt-oss-120b rising from 3.4 to just over 8 tokens per second on an RTX 5090 with 64GB of DDR4 [11]. That test machine had barely enough system memory to hold the model, and a separate user report in the same source quoted about 15 tokens per second on an older RTX 3090 with a similar setup [11]. Faster DDR5 memory should help, but no measurement was found. Treat the RTX 5090 as the better choice only if most of the work is on models of 32B or smaller, where it is several times faster than any unified-memory machine.

## Used RTX 3090s are cheap but do not solve the size problem

A used RTX 3090 has 24GB and lists from £900 on PriceSpy [8]. Two of them give 48GB, which is still short of a 4-bit 120B model, so they face the same split-memory slowdown as the RTX 5090 [11]. They also draw far more power and need a large case, a motherboard with two full-length slots and a power supply of 1,200W or more. One researcher's claim of 267 to 455 tokens per second for gpt-oss-120b on a single 3090 was dropped as impossible: the model is 61GB and the card holds 24GB, and the cited post predates the model's release.

## Apple is the fastest option for 122B models, at more than double the budget

Apple machines run these models well when they have the memory. A Mac Studio M3 Ultra with 512GB ran Qwen3.5-122B-A10B at 8-bit at 41.5 tokens per second in a March 2026 community benchmark [9]. A widely cited figure of 40 to 55 tokens per second on an M4 Max 128GB is labelled on its own page as an estimate, not a measurement [10]. The new Mac Studio launched in August 2026 with up to 512GB [17], but the configurations with enough memory for these models start well above £3,000 [6]. A used 128GB M4 Max Mac Studio may come close to budget; no UK price was checked.

## Who else is doing this

Four other approaches compete for the same buyer.

| Approach | Strength | Weakness | Fit for this question |
|---|---|---|---|
| NVIDIA DGX Spark | 128GB, CUDA software, compact | £3,700+ and rising [3][4]; 273 GB/s [2] | Over budget |
| Apple Mac Studio M5 | Fast, quiet, mature MLX software | 128GB configurations far above £3,000 [6] | Over budget |
| Gaming GPU desktop (RTX 5090) | Very fast on models up to 32B | Large models spill into system RAM, ~8 tok/s measured [11] | Runner-up |
| Workstation cards (Intel Arc Pro B60, 24GB) | Low price, low power | Bandwidth 456 GB/s; software fragmented [16] | Not recommended |
| Renting cloud GPUs | No upfront cost | Ongoing cost, data leaves the house | Outside scope |

## The case against

The main conclusion rests on one measured speed from one pre-production machine more than a year old [1]. If shipping machines or current software are slower, the margin over the RTX 5090 shrinks. The Strix Halo price is converted from euro and dollar prices [13][14], and memory prices have risen through 2026 [3], so a UK box may now cost more than £2,000.

Strix Halo is also slow at reading long prompts compared with a dedicated graphics card, which matters for work like summarising long documents. An RTX 5090 owner who mostly runs 30B-class models gets several times the speed, and can still run a 120B model slowly when needed. For that user the RTX 5090 is the better buy.

## Risks and unknowns

- No UK retail price for a 128GB Strix Halo mini PC was confirmed.
- No measured speed for Qwen3.5-122B-A10B or Qwen3-235B on Strix Halo survived checking.
- The RTX 5090 split-memory speed was measured on DDR4 with only 64GB [11]. A DDR5 system with 128GB could be much faster. This is the gap most likely to change the runner-up.
- DGX Spark's current UK price was not found. It may have risen further.
- Running cost: every 100W drawn all year costs about £231 at 26.32p per kWh [12]. Power draw figures for these machines were not re-checked, so this report does not compare running costs.

## Recommendations

1. Buy a 128GB Ryzen AI Max+ 395 mini PC if the main goal is running 100B+ MoE models. Confirm the UK price and that the memory is the full 128GB before ordering.
2. Before buying, look for a recent measured result for the exact model you plan to run on that chip. If none exists, ask on the Framework or Level1Techs forums.
3. Choose an RTX 5090 desktop with 128GB DDR5 instead only if most daily work uses models of 32B or smaller.
4. Skip new Apple and DGX Spark hardware at this budget. Check used 128GB M4 Max Mac Studio prices if Apple's software matters to you.
5. Re-run this research in six months. GPU and memory prices moved by hundreds of pounds during 2026 [3][7].

## How this was researched

The question was split into five sub-questions: unified-memory machines, GPU and server builds, measured speeds, software and running costs, and the wider market including what is coming next. Five researchers worked in parallel, making 86 searches and page opens between them. Their notes produced 17 claims.

A separate check reopened the cited source for every claim with a number in it. Five claims were confirmed, six corrected, three weakened and three dropped. Two of the dropped claims were physically impossible: a 130GB model said to run on a 32GB card, and a 61GB model said to run on a 24GB card. One extra search round was run to measure the RTX 5090 with system-memory offload, the gap that most affected the runner-up.

## Verification log

| Claim | Result | What was checked | Change |
|---|---|---|---|
| Strix Halo 128GB price | weakened | presale and US prices; no UK listing found | stated as a conversion |
| Strix Halo gpt-oss-120b speed | corrected | forum post reopened | 53.4 to 46 tok/s; pre-production noted |
| Strix Halo Qwen3.5-122B 18-19 tok/s | dropped | forum thread reopened, no such result | removed |
| RTX 5090 UK price | corrected | price tracker reopened | £3,799 to £2,019 |
| Mac Studio for 100B+ from £2,499 | corrected | Apple UK store and price article | 36GB too small; 64GB is £3,799 |
| M4 Max 122B 40-55 tok/s | weakened | page says figures are estimates | labelled as estimate |
| M3 Ultra 122B speed | corrected | benchmark reopened | 41.5 tok/s, 8-bit, 512GB |
| DGX Spark gpt-oss-120b 60 tok/s | corrected | review reopened, figure not present | speed removed; bandwidth kept |
| DGX Spark over budget | confirmed | forum and price check | none |
| Used RTX 3090 price | confirmed | price comparison site | none |
| RTX 5090 runs Qwen3-235B at 118 tok/s | dropped | does not fit in memory | removed |
| Single GPU plus system RAM speed | corrected | test reopened | 8 tok/s on 5090; conflict noted |
| UK electricity rate | confirmed | Ofgem | none |
| 120B memory need | confirmed | several sources agree | wording fixed |
| Arc Pro B60 price and software | weakened | not reopened | caveat |
| 2026 GPU and memory price rises | confirmed | NVIDIA forum | none |
| Apple cannot run 100B models | dropped | contradicted by M3 Ultra benchmark | removed |

## Sources

1. AMD Strix Halo (Ryzen AI Max+ 395) GPU LLM performance tests. Framework Community. https://community.frame.work/t/amd-strix-halo-ryzen-ai-max-395-gpu-llm-performance-tests/72521. Post dated 21 Sep 2025. Read 7 Oct 2026. Forum.
2. NVIDIA DGX Spark in-depth review. LMSYS. https://www.lmsys.org/blog/2025-10-13-nvidia-dgx-spark. 13 Oct 2025. Read 7 Oct 2026. Secondary (independent test).
3. DGX Spark price increase. NVIDIA Developer Forums. https://forums.developer.nvidia.com/t/dgx-spark-price-increase/361640. 24 Feb 2026. Read 7 Oct 2026. Forum.
4. DGX Spark vs Mac Studio: price-checked look. glukhov.org. https://glukhov.org/hardware/ai/nvidia-dgx-spark-prices/. Undated (prices as of Oct 2025). Read 7 Oct 2026. Secondary.
5. Buy Mac Studio. Apple UK. https://www.apple.com/uk/shop/buy-mac/mac-studio. Live page. Read 7 Oct 2026. Primary.
6. Mac Studio M5 release date, UK prices, Max vs Ultra. lewislovelock.com. https://lewislovelock.com/blog/mac-studio-m5. 16 Jul 2026. Read 7 Oct 2026. Secondary.
7. RTX 5090 UK price history. GPU Price History. https://gpupricehistory.com/uk/rtx-5090. Updated 5 Oct 2026. Read 7 Oct 2026. Secondary (price tracker).
8. Gigabyte GeForce RTX 3090 Gaming OC 24GB. PriceSpy UK. https://pricespy.co.uk/gigabyte-geforce-rtx-3090-gaming-oc-2xhdmi-3xdp-24gb--p5509028. Live page. Read 7 Oct 2026. Secondary (price comparison).
9. Qwen3.5-122B-A10B on M3 Ultra. oMLX benchmarks. https://omlx.ai/benchmarks/c9cwjmc1. 31 Mar 2026. Read 7 Oct 2026. Forum (community benchmark).
10. Qwen 3.5 122B-A10B on M4 Max. LLMCheck. https://llmcheck.net/models/qwen-35-122b-a10b-on-m4-max/. Undated. Read 7 Oct 2026. Secondary (estimate).
11. GPT-OSS 120B: offloading MoE layers to CPU. Hardware Corner. https://www.hardware-corner.net/guides/gpt-oss-offloading-moe-layers/. 10 Nov 2025. Read 7 Oct 2026. Secondary.
12. Changes to energy price cap between 1 October and 31 December 2026. Ofgem. https://ofgem.gov.uk/news/changes-energy-price-cap-between-1-october-and-31-december-2026. Aug 2026. Read 7 Oct 2026. Primary.
13. GMKtec EVO-X2 is officially the cheapest PC with AMD's most powerful AI CPU. TechRadar. https://www.techradar.com/pro/at-eur1-499-gmktec-evo-x2-is-officially-the-cheapest-pc-with-the-most-powerful-amd-ai-cpu-ever-and-it-will-come-with-windows-11. 2025. Read 7 Oct 2026. Secondary.
14. Mini PC with AMD's fastest AI processor and 128GB RAM gets $800 discount. TechRadar. https://www.techradar.com/pro/hurry-mini-pc-with-amds-fastest-ai-processor-and-128gb-ram-gets-whopping-usd800-discount-for-just-a-week. Undated. Read 7 Oct 2026. Secondary.
15. AMD Ryzen AI Max+ "Strix Halo" performance with ROCm 7.0. Phoronix. https://www.phoronix.com/review/amd-rocm-7-strix-halo/3. 2025. Not reopened. Secondary.
16. Intel Arc Pro B60 workstation test. Igor's Lab. https://www.igorslab.de/en/intel-arc-pro-b60-workstation-test-with-technical-analysis-and-teardown-battle-of-the-small-workhorses-under-1000-euros/. 2026. Not reopened. Secondary.
17. Apple's new Mac Studio supports up to 512GB of unified memory. PetaPixel. https://petapixel.com/2026/08/25/apples-new-mac-studio-supports-up-to-512gb-of-unified-memory/. 25 Aug 2026. Read via search. Secondary.
