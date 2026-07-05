# Technical Report: GramAI

- **Team ID:** gram-f6954y
- **Domain:** coding_assistants
- **Model:** Qwen2.5-3B-Instruct-Q4_K_M

---

## Problem

Nigerian developers and students, particularly those trained through programs like 3MTT and coding bootcamps routinely encounter Python errors during development without reliable access to debugging tools. GitHub Copilot and ChatGPT require stable internet connections and recurring API costs that are prohibitive on variable Nigerian data plans. When connectivity fails or budgets run out, developers are left with no interactive debugging assistance.

GramAI addresses this directly: 
- paste a Python traceback, get a plain-English root-cause diagnosis and a concrete fix, entirely offline. No API key, no internet connection, no recurring cost. The system runs on the ~budget laptops already in use across Nigeria's developer community not on cloud infrastructure.

The offline constraint is not a technical novelty for its own sake. It reflects a real deployment reality, a developer working in Abuja during a NEPA outage, a bootcamp student in a low-connectivity area, or a solo engineer who cannot afford to send proprietary code to a third-party API. For these users, on-device inference is not a preference, it is the only viable option.

---

## Design Decisions

### Base model: Qwen2.5-3B-Instruct
Qwen2.5-3B-Instruct was selected after benchmarking three candidates against the same set of Python traceback test cases:

| Model | Params | Coding benchmark | Notes |
|---|---|---|---|
| **Qwen2.5-3B-Instruct** (selected) | 3B | strongest at this size | Apache 2.0, best coding/math relative to parameter count |
| Phi-3.5-mini-instruct | 3.8B | strong on reasoning | loses to Qwen2.5-3B on coding-specific tasks |
| Llama-3.2-3B-Instruct | 3B | solid generalist | slightly behind Qwen2.5-3B on code-related outputs |
| Gemma-2-2B-it | 2B | weakest of the four | retained as fallback if RAM ceiling becomes an issue |

Qwen2.5-3B-Instruct was the clear choice: strongest coding performance at this parameter count, Apache 2.0 license (clean for a public competition repo), and well-supported in llama.cpp's GGUF ecosystem.

### Quantization: Q4_K_M
Q4_K_M was chosen as the primary quantization after evaluating the tradeoffs at this model size:

- **Q8_0**: better quality but ~6GB for weights alone, leaving almost no headroom for RAG context and embedding model within the 7GB ceiling
- **Q4_K_M**: peak RSS of 3,454 MB including the full RAG stack, leaving 3.5GB headroom; quality degradation on Python debugging tasks is negligible compared to Q8_0
- **Q4_K_S / Q3_K_M**: tested and rejected; output quality degraded noticeably on the harder Tier 2 debugging cases where precise diagnosis matters

### Product scope: debugging-only, not general chat
A deliberate decision was made to constrain GramAI to a single task, traceback diagnosis and fix. General-purpose coding assistants spread a 3B model's capacity across code generation, explanation, documentation, and debugging. By narrowing to one task, the system prompt, RAG corpus, and retrieval design can all be optimized for that task specifically and a 3B model can be genuinely good at it rather than mediocre across many things.

### RAG architecture: two-tier offline corpus
The core engineering contribution is the RAG layer a structured offline corpus of verified Python error patterns that grounds the model's responses rather than letting it improvise.

The corpus is organized in two tiers:

**Tier 1: Core canonical errors (~60% of corpus)**
Covers the bread-and-butter Python exceptions every developer hits: `ModuleNotFoundError`, `TypeError`, `AttributeError`, `IndexError`, `KeyError`, `ValueError`, `ZeroDivisionError`, `SyntaxError`/`IndentationError`, and common pandas/numpy shape errors. This tier is designed for breadth ensuring robust performance across the range of errors a judge or real user is likely to test.

**Tier 2: Real-world differentiation cases (~40% of corpus)**
Drawn from actual debugging history across real engineering projects: serial port permission errors, Docker DNS resolution failures, config-loading type mismatches (YAML values parsed as wrong types), environment/dependency conflicts, and relative path resolution bugs. These entries are written from first-hand debugging experience, not synthesized from documentation, the traceback patterns, diagnoses, and fixes are all verified against real reproducible cases.

**Why RAG rather than fine-tuning?**
Fine-tuning a 3B model on a custom debugging corpus would require significant compute and risks degrading general Python knowledge the base model already has. RAG allows the model to retain its pretrained coding knowledge while grounding its responses in verified, domain-specific patterns, a better tradeoff at this scale.

**Empirical validation:**
Baseline testing (5 tracebacks, no RAG) showed the model gave a completely incorrect answer for the TypeError/config pattern (Test 4),suggesting treating both operands as strings rather than casting the config value to float. With RAG enabled, the correct diagnosis and fix (`float(config['fraud_threshold']) + 0.05`) was produced consistently across multiple runs. This is the clearest demonstration that the RAG layer is load-bearing, not decorative.

### African language bonus: deliberately not pursued
The African Alpha Bonus (+15%) was evaluated and rejected for this submission. Python tracebacks, error messages, and code are inherently English/symbol-based artifacts, a `TypeError` is a `TypeError` in any language, and no working Nigerian developer wants their debugging tool's diagnosis delivered in Igbo or Yoruba when the error message itself is in English. Pursuing language support here would have been cosmetic rather than meaningful, and the judges' own FAQ explicitly discounts cosmetic language additions. The decision is documented here as a considered tradeoff, not an oversight.

---

## Constraints

- **Hardware target:** 4 vCPU, 8GB RAM, integrated GPU only (Intel UHD/Iris Xe), Ubuntu 22.04 with no discrete GPU
- **Memory ceiling:** hard 7GB RAM limit; GramAI peaks at 3,454 MB including model weights, FAISS index, and sentence-transformers embedding model leaving 3.5GB headroom
- **Inference:** CPU-only via llama.cpp (`n_gpu_layers=0` hardcoded); the GTX 1650 on the development machine was explicitly disabled during all benchmarking to reflect target hardware
- **Offline:** zero network calls during inference; the sentence-transformers embedding model (`all-MiniLM-L6-v2`) is cached locally after first run
- **Context window:** `n_ctx=2048`, `max_tokens=300` hard cap, keeps responses concise and protects throughput score; sufficient for traceback + RAG context + structured response
- **Small model capacity:** a 3B model has genuine limitations on harder debugging cases without grounding, the RAG layer compensates for the model's weakest areas (config type errors, module path resolution) where pretrained knowledge alone produces wrong answers

---

## Benchmarks

Benchmarks measured on development machine using the official ADTC profiler (`adtc-profiler 0.1.0`) in participant mode.

| Metric | Value |
|---|---|
| Machine | HP Victus 15, 12th Gen Intel Core i7-12700H |
| RAM (peak RSS) | 3,454.9 MB |
| RAM (steady state) | 3,374.5 MB |
| Generation speed | 21.08 tokens/sec |
| Time to first token | 6,726 ms |
| Thermal peak | 96°C (throttling observed) |
| OS | Ubuntu 22.04.5 LTS |

**Thermal note:** the profiler run that produced these numbers triggered thermal throttling at 99°C. The throughput and memory numbers are stable across runs; the thermal result is a function of ambient conditions and laptop cooling, not model size or inference implementation.

**Baseline vs RAG comparison (5 test tracebacks):**

| Test | Error type | No RAG | With RAG |
|---|---|---|---|
| 1 | NameError: undefined variable |  Correct |  Correct |
| 2 | AttributeError: NoneType from dict.get() |  Correct |  Correct |
| 3 | ModuleNotFoundError: wrong run method | Incomplete |  Correct |
| 4 | TypeError: config value loaded as string | Wrong fix |  Correct |
| 5 | SerialException: permission denied |  Correct |  Correct |

Test 4 is the most significant result: without RAG the model suggested concatenating both operands as strings (factually wrong); with RAG it correctly diagnosed the config loading issue and produced `float(config['fraud_threshold']) + 0.05`. This directly demonstrates the RAG layer's value on the error types that matter most in real production Python work.