`"""
GramAI — Offline Python Traceback Debugger
ADTC 2026 · Coding Assistants Track

Usage:
    python rungramai.py                  # interactive mode
    python rungramai.py --traceback "..."  # single-shot mode
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# ── Dependencies ──────────────────────────────────────────────────────────────
try:
    from llama_cpp import Llama
except ImportError:
    sys.exit("[GramAI] llama-cpp-python not installed. Run: pip install llama-cpp-python")

try:
    from sentence_transformers import SentenceTransformer
    import faiss
    import numpy as np
except ImportError:
    sys.exit("[GramAI] Missing deps. Run: pip install sentence-transformers faiss-cpu numpy")


# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent
MODEL_PATH  = ROOT / "model" / "qwen2.5-3b-instruct-q4_k_m.gguf"
CORPUS_PATH = ROOT / "corpus" / "errors.jsonl"

# ── System prompt ─────────────────────────────────────────────────────────────
# Tight and explicit — constrains the verbosity observed in baseline tests.
# The model tends to pad with generic step-by-step advice; this pushes it toward
# a concise diagnosis + fix format that actually helps in a 2-minute demo.
SYSTEM_PROMPT = """You are GramAI, an expert Python debugger.

When given a Python traceback, you must respond in this EXACT format and no other:

DIAGNOSIS: <one sentence explaining the root cause>
FIX: <corrected code snippet or the exact change needed, no more than 10 lines>
WHY: <one sentence explaining why this fix works>

Rules:
- You MUST base your FIX on the similar cases provided above — do not invent a different solution
- Never pad with generic advice like "ensure you have defined the variable"
- Never suggest steps the user didn't ask for
- Never write more than 3 sections
- If the traceback is ambiguous, state the most likely cause directly
- Always give a concrete fix, not a checklist of things to check
- Never suggest sys.path.append as a fix for ModuleNotFoundError — always prefer python -m or PYTHONPATH
- The FIX must be copied or directly adapted from the similar cases shown above — never invent a solution not present in the context
- For ModuleNotFoundError, the fix is always to run as 'python -m module.path' from the project root, never sys.path.append or changing the import path"""

# ── RAG layer ─────────────────────────────────────────────────────────────────
class CorpusRAG:
    """
    Two-tier offline RAG over corpus/errors.jsonl.
    Falls back gracefully if corpus doesn't exist yet (early dev).
    """

    def __init__(self, corpus_path: Path):
        self.ready = False
        self.entries = []

        if not corpus_path.exists():
            print(f"[GramAI] No corpus found at {corpus_path} — running without RAG.")
            print("[GramAI] Build corpus/errors.jsonl to enable grounded responses.")
            return

        # Load corpus
        with open(corpus_path) as f:
            self.entries = [json.loads(line) for line in f if line.strip()]

        EMBED_PATH = ROOT / "model" / "all-MiniLM-L6-v2"
        if not EMBED_PATH.exists():
            print("[GramAI] Downloading embedding model (first run only)...")
            self.embedder = SentenceTransformer("all-MiniLM-L6-v2")
            self.embedder.save(str(EMBED_PATH))
        else:
            self.embedder = SentenceTransformer(str(EMBED_PATH))

        if not self.entries:
            print("[GramAI] Corpus is empty — running without RAG.")
            return

        # Build FAISS index over traceback_pattern field
        print(f"[GramAI] Loading embedding model...")

        patterns = [e["traceback_pattern"] for e in self.entries]
        embeddings = self.embedder.encode(patterns, show_progress_bar=False)
        embeddings = np.array(embeddings, dtype="float32")

        # Normalise for cosine similarity
        faiss.normalize_L2(embeddings)
        self.index = faiss.IndexFlatIP(embeddings.shape[1])
        self.index.add(embeddings)

        self.ready = True
        print(f"[GramAI] Corpus loaded: {len(self.entries)} entries indexed.")

    def retrieve(self, traceback: str, k: int = 3) -> str:
        """Return top-k corpus entries as a formatted context string."""
        if not self.ready:
            return ""

        query_vec = self.embedder.encode([traceback], show_progress_bar=False)
        query_vec = np.array(query_vec, dtype="float32")
        faiss.normalize_L2(query_vec)

        distances, indices = self.index.search(query_vec, k)

        context_parts = []
        for rank, idx in enumerate(indices[0]):
            if idx == -1:
                continue
            entry = self.entries[idx]
            context_parts.append(
                f"[Similar case {rank+1}]\n"
                f"Error type: {entry.get('error_type', 'unknown')}\n"
                f"Diagnosis: {entry.get('diagnosis', '')}\n"
                f"Fix: {entry.get('fix', '')}"
            )

        return "\n\n".join(context_parts)


# ── Model loader ──────────────────────────────────────────────────────────────
def load_model(model_path: Path) -> Llama:
    if not model_path.exists():
        sys.exit(
            f"[GramAI] Model not found at {model_path}\n"
            f"Run: bash download_model.sh"
        )

    print(f"[GramAI] Loading model (CPU only)...")
    return Llama(
        model_path=str(model_path),
        n_ctx=2048,       # context window — enough for traceback + RAG + response
        n_threads=4,      # match ADTC standard laptop spec (4 vCPU)
        n_gpu_layers=0,   # CPU only — mirrors the target hardware, no CUDA
        verbose=False,
    )


# ── Inference ─────────────────────────────────────────────────────────────────
def diagnose(llm: Llama, rag: CorpusRAG, traceback: str) -> dict:
    """
    Run a single traceback through RAG retrieval + LLM inference.
    Returns a dict with response text and timing metadata.
    """
    # Retrieve grounding context
    context = rag.retrieve(traceback)

    # Build prompt
    user_content = f"Diagnose this Python traceback:\n\n{traceback}"
    if context:
        user_content = (
            f"Here are similar cases from the knowledge base:\n\n{context}\n\n"
            f"---\n\nNow diagnose this Python traceback:\n\n{traceback}"
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    # Generate
    t0 = time.perf_counter()
    response = llm.create_chat_completion(
        messages=messages,
        max_tokens=300,    # hard cap — keeps responses tight, protects Sperf score
        temperature=0.1,   # low temp = deterministic, grounded — right for a debugger
        top_p=0.9,
    )
    elapsed = time.perf_counter() - t0

    text = response["choices"][0]["message"]["content"].strip()
    usage = response.get("usage", {})

    return {
        "response": text,
        "elapsed_s": round(elapsed, 2),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="GramAI — Offline Python traceback debugger"
    )
    parser.add_argument(
        "--traceback", "-t",
        type=str,
        default=None,
        help="Traceback string to diagnose (wraps in quotes). Omit for interactive mode."
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=str(MODEL_PATH),
        help=f"Path to GGUF model file (default: {MODEL_PATH})"
    )
    parser.add_argument(
        "--corpus", "-c",
        type=str,
        default=str(CORPUS_PATH),
        help=f"Path to corpus JSONL file (default: {CORPUS_PATH})"
    )
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="Disable RAG retrieval (run model only)"
    )
    args = parser.parse_args()

    # Load model and RAG
    llm = load_model(Path(args.model))
    rag = CorpusRAG(Path(args.corpus)) if not args.no_rag else CorpusRAG(Path("nonexistent"))

    print("\n" + "─" * 60)
    print("  GramAI — Offline Python Debugger")
    print("  Paste a traceback. Type 'exit' to quit.")
    print("─" * 60 + "\n")

    # Single-shot mode
    if args.traceback:
        result = diagnose(llm, rag, args.traceback)
        print(result["response"])
        print(f"\n[{result['completion_tokens']} tokens · {result['elapsed_s']}s]")
        return

    # Interactive mode
    while True:
        print("Traceback (paste, then press Enter twice):")
        lines = []
        try:
            while True:
                line = input()
                if line == "" and lines and lines[-1] == "":
                    break
                if line.lower() in ("exit", "quit"):
                    print("Exiting GramAI.")
                    return
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            print("\nExiting GramAI.")
            return

        traceback_input = "\n".join(lines).strip()
        if not traceback_input:
            continue

        print("\n[GramAI] Diagnosing...\n")
        result = diagnose(llm, rag, traceback_input)

        print("─" * 60)
        print(result["response"])
        print(f"\n[{result['completion_tokens']} tokens · {result['elapsed_s']}s]")
        print("─" * 60 + "\n")


if __name__ == "__main__":
    main()