# GramAI

## Inspiration
Nigerian developers and students especially those coming out of programs like 3MTT hit Python errors constantly but can't always reach Stack Overflow, ChatGPT, or Copilot. Data costs money, NEPA cuts power, internet drops. The debugging tools everyone assumes exist don't reliably exist for a significant portion of African developers. GramAI is the tool that works when nothing else does.

## What it does
GramAI takes a Python traceback as input and returns a plain-English root-cause diagnosis and a concrete fix. Nothing else, no general chat, no code generation, no explanations nobody asked for. It runs entirely offline via llama.cpp on a standard laptop with no discrete GPU, using a two-tier RAG layer over a curated corpus of verified Python error patterns to ground the model's responses.

## How I built it
- Model: Qwen2.5-3B-Instruct quantized to Q4_K_M via llama.cpp, selected after benchmarking against Phi-3.5-mini and Llama-3.2-3B on real Python debugging tasks
- RAG layer: a two-tier corpus of 48 verified error entries. Tier 1 covers canonical Python exceptions (TypeError, AttributeError, ModuleNotFoundError etc.), Tier 2 is drawn from real engineering debugging history covering serial port conflicts, Docker networking failures, config type mismatches, and dependency version conflicts
- Retrieval: sentence-transformers (all-MiniLM-L6-v2) + FAISS flat index, bundled locally for zero network calls at runtime
- Inference: llama-cpp-python bindings, CPU-only (n_gpu_layers=0), max_tokens=300 hard cap, temperature=0.1 for deterministic diagnostic outputs
- Benchmarks: 21.56 tokens/sec generation, 3,454 MB peak RAM, well within the 7GB ceiling

## Challenges we ran into
RAG retrieval bleeding was the core technical problem. Early versions retrieved the wrong corpus entry for similar-looking tracebacks, a plain dict AttributeError was pulling in an API-response entry and hallucinating response.json() into the fix. Solving this required iterating on traceback_pattern field specificity, not just adding more entries. More corpus isn't better, more precise corpus is better.
The other real challenge: a 3B model has strong internal priors from pretraining (e.g. sys.path.append is a very common Stack Overflow answer for ModuleNotFoundError) that compete with retrieved context. The fix was a combination of more directive system prompt instructions and rewriting fix fields to use Python-flavored syntax the model would anchor to, rather than shell command blocks it would ignore.

## Accomplishments that we're proud of
The baseline test that mattered most: without RAG, the model gave a completely wrong fix for a TypeError caused by a config value loaded as a string from YAML, it suggested concatenating both operands as strings. With RAG, it correctly diagnosed the root cause and produced float(config['fraud_threshold']) + 0.05. That's the whole point of the system working, not impressive benchmark numbers, but a wrong answer becoming a right one on exactly the error type that trips up real developers.

## What we learned
Retrieval quality matters more than corpus size. 48 well-written, precisely patterned, manually verified entries outperform 200 scraped entries with generic traceback_pattern fields. The embedding distance between a query and a corpus entry is entirely determined by how precisely the traceback_pattern captures the distinctive signature of that error, getting that right is the actual engineering work, not the model selection.

## What's next for GramAI
- Optional code-snippet context alongside the traceback (v1.1), improves diagnosis accuracy on errors where the traceback alone is ambiguous
- Sandboxed fix verification, run the suggested fix against a minimal reproduction and confirm it resolves the error before returning it to the user
- Corpus expansion to cover JavaScript/Node.js and Rust compilation errors, same architecture, different error taxonomy
- IDE integration as a VS Code extension, the offline inference engine stays identical, the UX layer changes