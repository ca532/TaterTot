"""Shared lazy loader for local Qwen GGUF models."""

from __future__ import annotations

import os
import threading


_MODELS = {}
_MODEL_LOCK = threading.Lock()


def get_qwen_model(repo_id: str, filename: str):
    """Download and load a GGUF once per process and reuse it across agents."""
    key = (str(repo_id).strip(), str(filename).strip())
    if not all(key):
        raise ValueError("Qwen repository and filename are required")

    if key in _MODELS:
        return _MODELS[key]

    with _MODEL_LOCK:
        if key in _MODELS:
            return _MODELS[key]

        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama

        model_path = hf_hub_download(repo_id=key[0], filename=key[1])
        model = Llama(
            model_path=model_path,
            n_ctx=4096,
            n_threads=max(1, (os.cpu_count() or 2) - 1),
            n_batch=256,
            verbose=False,
        )
        _MODELS[key] = model
        return model
