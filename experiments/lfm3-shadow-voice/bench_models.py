"""Sequential, explicit on-device LFM3 model microbenchmarks.

Never runs model downloads or remote custom Python unless invoked by the user.
No simulated time measurements. GPU stats are PyTorch allocated-byte peaks.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Windows embeddable Python may omit the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

def benchmark(kind: str, audio_path: str | None, output_file: str, *, allow_cpu: bool,
              min_free_vram_gb: float, revision: str | None) -> dict:
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device != "cuda" and not allow_cpu:
        raise RuntimeError("CUDA unavailable; refuse large CPU inference unless --allow-cpu")
    if device == "cuda":
        free, _total = torch.cuda.mem_get_info()
        if free < min_free_vram_gb * (1024 ** 3):
            raise RuntimeError(f"Only {free / (1024**3):.2f} GiB free CUDA VRAM. "
                               "Refusing to load a model alongside other GPU workloads.")
        torch.cuda.reset_peak_memory_stats()
    dtype = (torch.bfloat16 if device == "cuda" and torch.cuda.is_bf16_supported()
             else torch.float16 if device == "cuda" else torch.float32)

    def sync():
        if device == "cuda":
            torch.cuda.synchronize()

    started = time.perf_counter()
    common_kwargs = {"trust_remote_code": True, "dtype": dtype}
    if revision:
        common_kwargs["revision"] = revision

    with torch.inference_mode():
        if kind == "decision":
            from transformers import AutoModel
            model = AutoModel.from_pretrained("LiquidAI/d1-Omni-2B", **common_kwargs).to(device).eval()
            sync()
            loaded = time.perf_counter()
            from shadow import Snapshot
            from d1_adapter import D1OmniAdapter
            adapter = D1OmniAdapter(model)
            snapshot = Snapshot(1, "No, wait, I meant tomorrow.", True, True, "Your order is", "")
            # Warm once; benchmark same model with a fixed small test state.
            adapter.classify(snapshot, audio_path)
            sync()
            times = []
            decision = None
            for _ in range(5):
                t0 = time.perf_counter()
                decision = adapter.classify(snapshot, audio_path)
                sync()
                times.append((time.perf_counter() - t0) * 1000)
            result = {"kind": kind, "model": adapter.model_name,
                      "decision": {"interrupt_probability": decision.interrupt_probability,
                                   "end_turn_probability": decision.end_turn_probability,
                                   "ignore_probability": decision.ignore_probability},
                      "inference_ms": times, "audio_supplied": audio_path is not None}
        else:
            import soundfile as sf
            from transformers import AutoModelForMultimodalLM, AutoProcessor
            model_id = "LiquidAI/LFM3-Audio-2B"
            model = AutoModelForMultimodalLM.from_pretrained(
                model_id, device_map=device, **common_kwargs).eval()
            processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True,
                                                       **({"revision": revision} if revision else {}))
            codec = processor.audio_tokenizer.to(device=device).eval()
            sync()
            loaded = time.perf_counter()
            conversation = [
                {"role": "system", "content": "Perform TTS in American English using a female voice."},
                {"role": "user", "content": "Hello. How can I help you today?"},
                {"role": "assistant", "content": "<|audio_start|>"},
            ]
            inputs = processor.apply_chat_template(
                conversation, continue_final_message=True, tokenize=True,
                return_dict=True, return_tensors="pt").to(device)
            t0 = time.perf_counter()
            outputs = model.generate(
                **inputs, max_new_tokens=256, audio_temperature=1.0,
                audio_top_k=128, audio_repetition_penalty=1.1,
                audio_repetition_window=4096, return_dict_in_generate=True)
            sync()
            generation_ms = (time.perf_counter() - t0) * 1000
            t1 = time.perf_counter()
            waveform = codec.decode(outputs.audio_codes[:1, :, :outputs.audio_lengths[0]]).audio_values[0]
            sync()
            codec_ms = (time.perf_counter() - t1) * 1000
            wav = str(Path(output_file).with_suffix(".wav"))
            sf.write(wav, waveform.float().cpu().numpy(), 24_000)
            result = {"kind": kind, "model": model_id, "generation_ms": generation_ms,
                      "codec_ms": codec_ms, "wav": wav, "samples": int(waveform.numel()),
                      "sample_rate": 24000}
    result.update({"status": "measured", "device": device, "torch_dtype": str(dtype),
                   "load_ms": (loaded - started) * 1000,
                   "peak_torch_allocated_gib": round(torch.cuda.max_memory_allocated() / 1024**3, 3)
                        if device == "cuda" else None})
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    Path(output_file).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("kind", choices=["decision", "audio"])
    p.add_argument("--audio-file", help="16-bit PCM WAV clip for d1 audio input; omit for text decision")
    p.add_argument("--output", required=True, help="JSON report path; audio also saves a WAV alongside")
    p.add_argument("--allow-cpu", action="store_true")
    p.add_argument("--min-free-vram-gb", type=float, default=5.5)
    p.add_argument("--revision", help="Pin trusted Hugging Face code/weights to a reviewed revision")
    args = p.parse_args()
    try:
        print(json.dumps(benchmark(args.kind, args.audio_file, args.output,
                                   allow_cpu=args.allow_cpu,
                                   min_free_vram_gb=args.min_free_vram_gb,
                                   revision=args.revision), indent=2))
    except Exception as exc:
        print(f"BENCHMARK NOT RUN: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
