# Streaming STT: Deepgram vs OpenAI (latency first)

Researched 2026-10-28. Decision: **stay on Deepgram.** OpenAI's newest streaming model is more accurate but roughly 10× slower to a final transcript.

## Independent benchmark

Artificial Analysis AA-WER Streaming (methodology v2.2, May 2026). Audio streams in real time; latency is measured from the Silero-VAD end of speech and includes network time. The figures below come from the **AA-AgentTalk** set (voice-agent speech), the closest match to this demo.

| Model | First partial after speech end | Final transcript | Final WER | Price / min |
|---|---|---|---|---|
| Deepgram Flux (EN) | **21 ms** | **23 ms** | 6.7% | $0.0065* |
| Deepgram Nova-3 Realtime (current) | 59 ms | 61 ms | 5.1% | $0.0048* |
| OpenAI gpt-live-transcribe | 170 ms | 686 ms | **2.8%** | $0.017 |
| OpenAI gpt-realtime-whisper | 206 ms | 680 ms | 3.9% | $0.017 |

\* Deepgram's current promotional pay-as-you-go streaming rates.

The agent can only answer once it has a **final** transcript. Switching to OpenAI would add about **0.6 s to every turn**.

## End-of-turn detection

| | Deepgram Nova-3 | Deepgram Flux | OpenAI gpt-live-transcribe |
|---|---|---|---|
| Built-in turn detection | No (silence endpointing only) | Yes: acoustic + semantic | **No.** Server and semantic VAD are unsupported; the client must commit each turn |
| Vendor EoT figure | — | ~260 ms p50; p90 1 s, p95 1.5 s (Deepgram's own measurement) | — |
| Early-reply hook | — | `EagerEndOfTurn` (150–250 ms earlier) / `TurnResumed` | — |
| Latency control | `endpointing` | `eot_threshold`, `eager_eot_threshold`, `eot_timeout_ms` | `delay`: minimal → xhigh (no ms values published) |
| LiveKit | `deepgram.STT` | `deepgram.STTv2` + `turn_detection="stt"` | `inference.STT("openai/gpt-live-transcribe")`, 24 kHz PCM |

## Where the remaining latency is (live_agent.py)

1. **Turn detection** dominates. The LiveKit audio turn detector waits 0.3–2.5 s and commits anyway after about 1 s if no prediction arrives. This costs far more than the 61 ms STT final.
2. **`preemptive_generation` is off.** Turning it on starts the evidence check before the turn is committed. The cost is some discarded OpenAI calls, which only ever receive public topics.
3. **Flux option:** `deepgram.STTv2(model="flux-general-en")` with `turn_detection="stt"` replaces the separate cloud turn-detector hop. The trade-off is WER: 6.7% vs 5.1%.

None of this has been measured end to end yet; no API keys were configured. Measure *last spoken word → first agent audio* across 20+ turns per configuration before quoting a number.

## Sources

- Artificial Analysis streaming leaderboard: https://artificialanalysis.ai/speech-to-text/streaming
- Benchmark article: https://artificialanalysis.ai/articles/new-streaming-speech-to-text-benchmark-aa-wer-streaming
- Methodology: https://artificialanalysis.ai/speech-to-text/methodology
- OpenAI realtime transcription guide: https://developers.openai.com/api/docs/guides/realtime-transcription
- gpt-live-transcribe model page: https://developers.openai.com/api/docs/models/gpt-live-transcribe
- Deepgram Flux vs Nova-3: https://developers.deepgram.com/docs/flux/nova-3-migration
- Flux configuration: https://developers.deepgram.com/docs/flux/configuration
- Deepgram pricing: https://deepgram.com/pricing
- LiveKit Deepgram plugin: https://docs.livekit.io/agents/models/stt/deepgram/
- LiveKit OpenAI STT: https://docs.livekit.io/agents/models/stt/openai/
- LiveKit turn detector: https://docs.livekit.io/agents/logic/turns/turn-detector/
