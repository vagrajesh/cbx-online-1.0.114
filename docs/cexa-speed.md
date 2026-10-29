# Why is C-EXA slow?

A healthy setup — `qwen3.8:27b` fully on an RTX 5090 — answers most of C-EXA's calls in well under 30
seconds and reads a PDF batch of a thousand invoices in an hour or two. If a chat reply takes a minute
and a batch takes a day, the model is almost always **partly on the CPU**: Ollama could not fit the whole
model plus its working memory (the *context window*) into the card's 32 GB, so it quietly put the rest
in ordinary RAM, and every call crawls.

## See it
- **Home → Needs attention** (owner/admin): a line such as *"qwen3.8:27b is 62% on the GPU — 38% is
  running on the CPU, which is why C-EXA is slow. Free VRAM: close other GPU apps, or lower CBX_NUM_CTX
  (now 32768), or use the 27b-nvfp4 tag."* appears whenever a loaded model is not 100 % on the GPU, and a
  red line when Ollama is not answering at all. Nothing shows when all is well.
- **Ask C-EXA** *"why are you slow?"* — she has a `model_health` tool that reads the same numbers and answers
  in plain words: what is loaded, GPU vs CPU share, the context window, and the average call time per task
  over the last 24 hours (from her own call log).
- **`GET /api/cexa/status`** (owner/admin) returns it as JSON: `online`, `mode`, `num_ctx`,
  `loaded[] {name, size, size_vram, gpu_pct, context}`, `advice`, `latency[] {task, calls, avg_s, max_s,
  failed}`, `slow`.
- On the server: `ollama ps` prints the same `SIZE` / `PROCESSOR` columns ("100% GPU" is what you want).

## Fix it
Pick one; re-check on Home after the next C-EXA call (Ollama loads the model on demand).

1. **Free video memory.** Close anything else using the card — a browser with hardware acceleration and a
   dozen tabs can hold 2–3 GB; so can a second model Ollama still has loaded (`ollama ps`).
2. **Lower the context window: `CBX_NUM_CTX`.** CBX asks Ollama for a 32 768-token window on every call
   (`options.num_ctx`), and a 27B model's working memory at that size is several GB. Put
   `CBX_NUM_CTX=16384` in `.env` next to `run.bat` (8192 also works for PDF reading; C-EXA's chats and
   profile authoring rarely need more than 16k) and run `server-tools\restart.bat`. The number in use is
   shown in `/api/cexa/status` and in C-EXA's answer. Minimum 2048; anything unreadable falls back to 32768.
3. **Use a smaller quantisation of the same model**: `ollama pull qwen3.8:27b-nvfp4`, then set
   `CBX_CEXA_MODEL=qwen3.8:27b-nvfp4` in `.env` and restart. Same model, roughly two-thirds of the memory,
   a small loss in precision that PDF Studio's checks absorb.
4. Still slow with everything on the GPU? Look at `latency` per task: if only `ocr` or `vision_read` is
   slow, it is scanned PDFs (Tesseract on the CPU) rather than the model — that is normal, and a batch of
   scans simply takes longer.

## What the numbers mean
| Field | Meaning |
|---|---|
| `size` / `size_vram` | bytes of the loaded model in total / in video memory. Equal = 100 % GPU. |
| `gpu_pct` | `size_vram / size`. Below 100 the difference is running on the CPU. |
| `context` | the context window Ollama actually allocated for the loaded model. |
| `num_ctx` | the window CBX asks for (`CBX_NUM_CTX`, default 32768). |
| `latency[].avg_s` | average seconds per call for that task in the last 24 h; `chat`, `profile_author`, `assess` are C-EXA's own model. |
| `slow` | true when C-EXA's tasks averaged more than 30 s. |

Nothing in this page is ever shown to users: they see "C-EXA is working"; the model names and numbers are
behind the owner/admin pages only.
