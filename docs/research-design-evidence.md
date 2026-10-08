# Research design: what the published evidence says (checked 2026-10-07)

| Plan item | Evidence | Verdict |
|---|---|---|
| Small single-task steps | RULER (arXiv 2404.06654): many models fall short of their claimed context; small models degrade fastest. Least-to-most prompting (arXiv 2205.10625): 99% vs 16% on SCAN. | Keep |
| Parallel section writing (current code) | LangChain Open Deep Research blog: parallel section writers gave "disjoint" reports; fixed by writing once after all research. | Change |
| Layered merging for small models | BooookScore (arXiv 2310.00785): hierarchical merging more coherent than incremental, less detail; closed models more coherent than open. | Keep for small context only |
| Majority voting for judgement | arXiv 2608.11403: majority vote lowered accuracy on 56.6% (Qwen2.5-7B) and 65.7% (Llama-3-8B) of hard GPQA problems. | Drop |
| LLM judging page relevance | Cross-encoder rerankers beat LLM relevance judging (vendor benchmark, zeroentropy.dev; not independent). | Use reranker, optional |
| LLM checking claims | MiniCheck (arXiv 2404.10774): 770M checker reaches GPT-4 accuracy on LLM-AggreFact at 400x lower cost. | Use small dedicated checker, optional |
| Spend on search | Anthropic multi-agent blog: token usage explains 80% of BrowseComp variance; a better model beat doubling tokens. | Depth + stronger model for plan/verdict |
| Web-first planning | STORM (arXiv 2402.14207): perspectives from related articles, +10 pts coverage, +25 organisation vs RAG baseline. No direct head-to-head found. | Keep, measure |
| Citation baseline | Liu et al. (arXiv 2304.09848): commercial generative search engines, 51.5% sentences fully supported, 74.5% citations support their sentence. | Benchmark to beat |
