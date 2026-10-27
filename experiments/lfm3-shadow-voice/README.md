# LFM3 Shadow Voice — isolated PhoneDex research prototype

**Status: controller prototype and model benchmark harness, NOT a live GPT Live alternative.** No PhoneDex call routing is changed. No microphone capture, telephony access, model weights, or service is started automatically.

## Goal

Test the hypothesis that an **always-observing decision model** improves conversational turn timing while a separate audio/generative model speaks. Compare d1-Omni-2B to a no-shadow baseline; Jev/TypeSafe can be added as a separate backend later. The shadow **does not sit in the blocking audio path**.

```text
16 kHz mic / SIP audio -----+---> LFM3-Audio-2B (streaming voice, in future)
                            |
                            +---> d1-Omni-2B shadow (asynchronous)
                                      |
             synchronized revision/decision/probabilities
                                      |
playback controller <---------- speculation, duck/cancel, state reconciliation
            |
            +---- confirmed-played chunks vs uncertain playback ledger
```

`shadow.py` is a stdlib-only controller for VAD events, partial transcripts, speculation, independent decisions and playback acknowledgments. Predictions are bound to **immutable revision numbers**: a slow answer never cancels a newer turn. `d1_adapter.py` maps Liquid's official typed `system_one` API to controller decisions. `demo.py` runs a **deterministic simulation only**, not inference or actual audio.

**Safety of the control path:** VAD immediately ducks speech; model-driven cancellation requires a probability threshold. Speculative output must be explicitly committed, never automatically played as an authoritative answer. Cancelled generations cannot add more played chunks. Tool calls MUST be evaluated against fresh preconditions at the execution boundary; this proof of concept executes no tools. For genuine RTP, chunk delivery/transmission is not proof a caller heard audio—record only what the playback path can actually acknowledge, and preserve uncertainty.

## Offline tests (no dependencies)

From this directory:

```sh
python -m unittest discover -s tests -v
python demo.py
```

## Real model benchmarks (optional, manual, one at a time)

Read the upstream model cards and review `trust_remote_code` before use. Create a fresh environment; install a CUDA-enabled PyTorch for your hardware, then:

```sh
python -m pip install 'transformers>=5.20.2' accelerate soundfile librosa torch torchvision pillow numpy
python bench_models.py decision --output results/decision.json
python bench_models.py audio --output results/audio.json
# optional: d1-Omni with real 16-bit PCM audio, one model at a time:
python bench_models.py decision --audio-file ./sample_16k.wav --output results/decision_audio.json
```

**Benchmark limits:** Measures warm d1 `system_one` wall time or full LFM3 TTS generation + codec, **not first-audio latency, live ASR, call concurrency, or end-to-end interruptions**. A synthesized WAV is produced by the audio benchmark. No values are substituted if a model cannot load. Output reports are untracked. Model downloads/custom-code load happen only on explicit invocation. By default the benchmark refuses CPU inference and refuses to load on a GPU with less than 5.5 GiB free VRAM; it is intended for controlled idle-GPU tests, not when games are running. Changing this guard is an explicit operator choice, not a recommendation.

Liquid notes that `d1-Omni-2B` is weaker than `d1-2B` on text-only states, so the correct comparison is d1-Omni **with actual audio** versus d1-2B or Jev on transcripts. Numeric model probabilities are **not** calibrated for interruption classification by this experiment; collect labeled caller speech before setting production thresholds.

## Not done yet / gating issues

1. **Live audio plumbing:** wire streaming mic/RTP, endpointing, cancellation and playout ACKs to real audio, without touching PhoneDex's running SIP bridge.
2. **Real hardware numbers:** validate NVIDIA/CUDA health and install dependencies on an isolated Python environment; run the two benchmarks sequentially, then measure first audible sample latency and repeated short conversations.
3. **Compare decisions:** add labeled interruption/backchannel/background-talk fixtures; test text-only d1/Jev versus d1-Omni, evaluate precision/recall and p50/p95 latency and compute cost.
4. **Commercial review:** inspect Liquid's current LFM Open License and deployment/remote-code security requirements before pricing or deploying.

## Sources

- [LFM3-Audio-2B model card](https://huggingface.co/LiquidAI/LFM3-Audio-2B)
- [d1-Omni-2B model card](https://huggingface.co/LiquidAI/d1-Omni-2B)
- [LFM3 announcement](https://www.liquid.ai/blog/lfm3)
