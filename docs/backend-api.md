# 插件 → 语音后端 对接文档（中转站 / 任务调度）

> 本文档面向：**想做一个「中转站」把本插件的语音合成请求接到不同后端（不同接口/协议）并做任务调度的开发者**。
> 内容全部对照插件实际代码整理：`main.py`（客户端装配）、`cosyvoice/router.py`（多节点路由）、`cosyvoice/client.py`（HTTP 客户端）、`core/tts_engine.py`（分段与请求参数组装）、`deploy/cosyvoice_api.py` / `deploy/test/cosyvoice_api_queue.py`（官方参考服务端）。

---

## 1. 架构总览

```
┌─────────────────────────── AstrBot 服务端 ───────────────────────────┐
│                                                                      │
│  astrobot_plugin_cosyvoice (插件)                                    │
│    main.py  CosyVoicePlugin                                          │
│      ├── cosyvoice/router.py   CosyVoiceRouter（多节点负载均衡）      │
│      │        └── cosyvoice/client.py  CosyVoiceClient（单节点 HTTP）│
│      └── core/tts_engine.py    TtsEngine（分段 / 音色解析 / 组请求）  │
│                                                                      │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ HTTP（multipart 表单）
                                ▼
                   ┌─────────────────────────┐
                   │   你的「中转站」          │  ← 本文档要你做的东西
                   │   - 接收不同后端/接口     │
                   │   - 统一成标准接口        │
                   │   - 任务调度 / 限流 / 排队 │
                   └────────────┬────────────┘
                                ▼
              CosyVoice 推理服务（一台或多台，可能接口各不相同）
```

**关键点**：插件只认识一套「标准接口」（见 §2）。中转站把**任意上游**（不同格式、不同协议、多台机器）翻译成这套接口，并负责排队调度。插件端的一切重试/超时/冷却/多节点分流都建立在这套接口语义上（见 §4 §5），中转站只要遵守状态码语义，插件就能正确工作。

---

## 2. 标准接口契约（插件期望你的中转站提供）

### 2.1 `GET /` — 健康检查 + 采样率

插件**首次合成前**会调用一次（`CosyVoiceClient.fetch_sample_rate`），用返回的 `sample_rate` 覆盖本地配置值，确保 WAV 封装正确。**中转站必须真实透传后端模型的采样率**，否则音调会变。

响应（JSON）：

```json
{
  "status": "ok",
  "model_loaded": true,
  "sample_rate": 24000
}
```

- `sample_rate` 缺失/获取失败时插件回退到配置值（默认 24000），不会报错。

### 2.2 `POST /inference_zero_shot` — 零样本音色克隆合成（主路径）

multipart/form-data 表单字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `tts_text` | ✅ | 要合成的目标文本（插件已分段，每次请求一段） |
| `prompt_text` | 可选 | 参考音频对应的**纯人声朗读文字**（音色的 prompt_text） |
| `prompt_wav` | 二选一 | 上传的参考音频文件（`<file>`，AstrBot 本地读后上传） |
| `prompt_wav_path` | 二选一 | 参考音频在**后端**的文件名/路径（推荐，避免大文件上传） |

注意：
- `prompt_text` **为空时插件完全不传该字段**（不是传空串），由后端从自己的 `voices.json`（文件名→文本映射）自动取。这是有意的：空串会被部分实现当作"缺失"处理产生歧义。
- `prompt_text` 带 `_looks_polluted` 净化：若含 `<|endofprompt|>` 等 LLM 标记、system prompt 片段或长度 >150 字，视为污染，丢弃该字段改由后端取干净文本。
- 两者都不给 → 插件本地报 `ValueError`，不会发请求。
- `prompt_wav` 是二进制文件；`prompt_wav_path` 是字符串。**二选一**，都传时后端实现自行决定（官方实现优先 `prompt_wav_path`）。

响应：**裸 int16 PCM 字节流**（`application/octet-stream`），24kHz、单声道、**无 WAV 头**。WAV 头由插件端补。

### 2.3 `POST /inference_instruct2` — 指令控制合成（CosyVoice2/3 可选）

字段：`tts_text`（必填）、`instruct_text`（必填，如"请用开心的语气说"）、`prompt_wav` / `prompt_wav_path`（二选一）。响应同 2.2。

> 插件目前只走 `zero_shot` 模式（`mode="zero_shot"`），`instruct2` 接口由服务端保留兼容，中转站按需实现即可。

### 2.4 `GET /voices` — 列出参考音频（可选，用于核对）

```json
{
  "voices_dir": "/path/to/voices",
  "files": ["xiaoyu.wav", "boss.wav"],
  "texts": { "xiaoyu.wav": "你好，我是小宇。" }
}
```

插件未调用它，但对人工排查有用。

### 2.5 `GET /queue` — 队列状态（仅队列版中转站建议提供）

```json
{
  "queue_maxsize": 8,
  "in_queue": 2,
  "worker": {
    "state": "busy",
    "current": "zero_shot",
    "started_at": 1722720000.1,
    "running_seconds": 3.4
  }
}
```

---

## 3. 状态码语义（中转站必须遵守，插件会据此重试）

| 状态码 | 响应体 | 含义 | 插件行为 |
| --- | --- | --- | --- |
| `200` | `application/octet-stream`（PCM） | 成功 | 正常解析播放 |
| `400` | `text/plain` | 参数错误（缺参考音频/文本、文件不存在、模型不支持） | **不重试**，直接抛错 |
| `429` | `text/plain` | **服务繁忙：队列满或入队等待超时** | **退避重试**（可重试） |
| `503` | `text/plain` | 模型未加载完成 | **退避重试** |
| `504` | `text/plain` | 单次推理超时（> 后端 INFER_TIMEOUT） | **退避重试** |
| `500` | `text/plain` | 服务端生成失败 / 空音频 | 不重试，抛错 |

> **429 是任务调度的关键信号**：你的中转站满了就回 429，插件会按指数退避稍后再试，而不是无限堆积把后端拖垮。**不要**为了"尽量接住"无限排队——那会导致客户端读超时（默认 150s）雪崩。

---

## 4. 客户端行为（插件侧，你需知道它怎么"敲门"）

### 4.1 连接与超时（`CosyVoiceClient.__init__`）

- 复用单个 `httpx.AsyncClient`（连接池），多段合成共用，避免 TIME_WAIT 堆积。
- 超时：`httpx.Timeout(timeout, connect=10.0)`，其中：
  - `connect=10.0`：连接 10s 内连不上 → `ConnectError`/`ConnectTimeout` → 判定**服务器失联**（`CosyVoiceServerError`）。
  - read = 配置 `timeout`（默认 **150s**）：必须大于「后端排队最长时间 + 单次推理最长耗时」，否则客户端先断，自己制造重试验崩。

### 4.2 重试与退避（`synthesize`）

- 重试次数 `max_retry`（配置 `tts_max_retry`，默认 **0 = 不重试**）。
- 可重试条件：`429 / 503 / 504`、`ReadTimeout`、连接中断（`RequestError`，如 ReadError）。
- **不可重试**：`400 / 500`（立即抛）。
- 退避：`backoff = tts_retry_backoff * 2**attempt`（默认 0.5s → 1s → 2s …），`asyncio.sleep` 不阻塞事件循环。
- 重试耗尽 → 抛最后一次错误；插件进熔断冷却（见 4.3）。

### 4.3 熔断冷却（插件级，`main.py`）

- 任何一次合成失败（含重试耗尽）→ 进入冷却 `tts_cooldown_sec`（默认 30s）。
- 冷却期内**不再向后端发任何请求**，直接回退文字（`both` 模式文字照发；`voice_only` 补发文字），避免服务端已坏时每条消息都去打、反复 ReadError。
- 冷却到期后再试一次，成功则解除。

### 4.4 并发限流（插件级，`TtsEngine`）

- 全局 `asyncio.Semaphore`（`tts_concurrency`，默认 1）：同一时刻最多 N 个请求打到后端，其余协程挂起等待（不占事件循环、不卡其他消息）。
- 若你的中转站自己做了调度（队列 + 429），客户端并发限制可设大些；两者叠加也不会冲突（服务端 429 仍会被客户端退避兜住）。

---

## 5. 多节点路由（`CosyVoiceRouter`，面向多台后端）

插件配置 `servers` 列表（每项 `{url, enabled, default, weight}`）时启用多节点：

- **权重分流**：按 `weight` 随机选取（weight 越大概率越高）。
- **默认节点**：勾选 `default` 的节点**优先**使用（多个默认则随机取一），失败/冷却时自动退回权重分流。
- **故障隔离**：某节点连续失败 3 次 → 临时隔离 30s（`NODE_COOLDOWN_SEC`），期间不再参与分流，其余节点照常；到期自动恢复探测。
- **单节点失败自动切换**：一次合成先试选中节点，失败立即试下一节点，最多遍历全部；全部失败才抛错（触发 4.3 冷却）。
- **回退**：`servers` 为空/全禁用 → 回退到 `base_url` 单机模式。

> 对中转站的影响：如果你部署**多台中转站**，直接把所有中转站 url 填进 `servers` 即可获得负载均衡 + 故障切换，无需自己在中转站层再写集群逻辑。

---

## 6. 服务端任务调度模型（官方队列版参考实现）

`deploy/test/cosyvoice_api_queue.py` 是官方参考实现，你的中转站调度逻辑可照搬这套模型：

### 6.1 有界队列 + 单 worker

```
请求到达
  └─> _enqueue()：asyncio.Queue.put(item)（带超时）
        ├─ 队列未满 → 入队，返回 future 等 worker 结果
        └─ 队列满 → 最多等 COSY_QUEUE_WAIT(默认30s)
              ├─ 等到了 → 入队
              └─ 超时 → 返回 (429, "service busy, queue full")
后台：_queue_worker() 单协程串行消费
  └─ 每次取一个任务，asyncio.wait_for(to_thread(_synthesize, gen), INFER_TIMEOUT)
        ├─ 完成 → future.set_result((pcm, 200, ""))
        ├─ 超时 → future.set_result(("", 504, "inference timeout"))
        └─ 异常 → future.set_result(("", 500, f"inference error: {e}"))
```

### 6.2 关键参数（环境变量可配）

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `COSY_QUEUE_SIZE` | 8 | 队列长度上限，满则 429 |
| `COSY_QUEUE_WAIT` | 30 | 入队最长等待秒数，超时 429 |
| `COSY_INFER_TIMEOUT` | 120 | 单次推理超时，超时 504 |

### 6.3 为什么用队列而不是直接并发推理

- CosyVoice 推理是同步且占 GPU，并发多个会互相争抢甚至 CUDA 上下文竞争卡死。
- 队列 + 单 worker 保证同一时刻只有一个推理任务，避免 event loop 卡死、避免无限堆积。
- **429 让上游（插件）退避，而不是把请求无限堆在自己的队列里**——这是防止雪崩的关键。

> 原版 `cosyvoice_api.py`（非队列）用全局 `asyncio.Lock` 串行化 + `INFER_TIMEOUT=120`，高并发下请求会阻塞堆积导致越堆越慢。**中转站请用队列版模型**。

---

## 7. 中转站设计建议（把不同接口统一 + 调度）

### 7.1 统一层（Adapter）

把不同上游（可能是 Gradio、HTTP API、本地进程、不同参数格式）适配成 §2 标准接口：

```text
客户端(插件) ──标准接口──> [中转站路由/鉴权/限流] ──> [Adapter A] ──> 后端 A
                                              └─> [Adapter B] ──> 后端 B
```

- 入参统一：`tts_text` / `prompt_text` / `prompt_wav | prompt_wav_path`。
- 出参统一：裸 int16 PCM（无头）或由中转站补 WAV 头。
- `prompt_wav_path` 的语义由中转站落地：要么共享音频目录 + `voices.json`，要么中转站维护 `文件名 → 真实音频` 的映射，**从请求方剥离"文件在哪"的细节**。

### 7.2 调度层（Queue + Worker）

- 参照 §6 实现有界队列 + 单/多 worker（若 GPU 够、后端支持并发可多 worker，但务必有上限）。
- 队满/入队超时 → **429**；推理超时 → **504**；模型未加载 → **503**。
- 可选：按请求大小/优先级排队、任务合并（插件已分段，每段是一个独立请求）。

### 7.3 必须遵守的约定（否则插件端表现异常）

1. `/` 返回**真实** `sample_rate`（影响音频音调，插件首次请求前必读）。
2. `prompt_text` 缺省时能按文件名回退到本地文本（插件在很多场景不传）。
3. 永远别让客户端读超时：**排队等待 + 推理总时长 < 插件 `timeout`（默认 150s）**。宁可 429 也别让客户端干等到超时。
4. 429/503/504 语义要正确（插件会退避重试，不会丢消息）；400/500 会被当作永久失败（插件进冷却）。

### 7.4 接入插件的两种形态

| 形态 | 配置 | 说明 |
| --- | --- | --- |
| 单台中转站 | `base_url` 指向中转站 | 最简单 |
| 多台中转站 | `servers` 填多台中转站 url | 插件帮你做负载均衡 + 故障切换（§5） |

### 7.5 中转站「必须做 / 可选做」清单

**必须做（不做插件会异常）：**

- [ ] 实现 §2 的 `GET /` 与 `POST /inference_zero_shot`（`/` 返回真实 `sample_rate`）。
- [ ] 遵守 §3 状态码语义，尤其 **429 = 让上游退避重试**（不要无限排队拖垮客户端）。
- [ ] 支持 `prompt_text` 缺省时按 `prompt_wav_path` 文件名回退到本地文本（插件很多场景不传）。
- [ ] 保证「排队等待 + 推理总时长」小于插件 `timeout`（见 §10 调配），否则客户端先超时、触发重试验崩。

**可选做（不做也兼容）：**

- [ ] `POST /inference_instruct2`（插件目前只走 zero_shot，仅当你要兼容其他调用方）。
- [ ] `GET /voices` / `GET /queue`（监控排错用）。
- [ ] 队列调度、优先级、任务合并、鉴权、限流（按需）。

---

## 8. 附：请求示例（curl）

---

## 8. 附：请求示例（curl）

```bash
# 健康检查（采样率）
curl http://localhost:50002/

# 参考音频走服务端路径（推荐）
curl -X POST http://localhost:50002/inference_zero_shot \
  -F "tts_text=你好，今天天气不错。" \
  -F "prompt_wav_path=xiaoyu.wav"

# 参考音频上传（AstrBot 本地文件）
curl -X POST http://localhost:50002/inference_zero_shot \
  -F "tts_text=你好，今天天气不错。" \
  -F "prompt_text=你好，我是小宇。" \
  -F "prompt_wav=@/path/to/xiaoyu.wav"

# 指令合成
curl -X POST http://localhost:50002/inference_instruct2 \
  -F "tts_text=今天太开心了！" \
  -F "instruct_text=请用开心的语气说" \
  -F "prompt_wav_path=xiaoyu.wav"

# 队列状态
curl http://localhost:50002/queue
```

Python 侧（带 429 退避）示例：

```python
import time, requests

BASE = "http://localhost:50002"

def synthesize(text: str, voice: str = "xiaoyu.wav", max_retry: int = 3) -> bytes | None:
    for attempt in range(max_retry):
        r = requests.post(f"{BASE}/inference_zero_shot",
                          data={"tts_text": text, "prompt_wav_path": voice},
                          timeout=130)  # 略大于后端推理超时
        if r.status_code == 200:
            return r.content
        if r.status_code in (429, 503, 504):
            time.sleep(0.5 * (2 ** attempt))  # 指数退避
            continue
        print(f"[错误] HTTP {r.status_code}: {r.text}")
        return None
    return None
```

---

## 9. 参考文件索引

| 文件 | 内容 |
| --- | --- |
| `cosyvoice/client.py` | 单节点 HTTP 客户端（超时/重试/退避/状态码语义） |
| `cosyvoice/router.py` | 多节点负载均衡（权重/默认/故障隔离） |
| `core/tts_engine.py` | 分段、音色解析、请求参数组装（`_wav_kwargs`） |
| `main.py` | 客户端装配、熔断冷却、并发限流 |
| `deploy/cosyvoice_api.py` | 官方单机服务端（全局锁，参考） |
| `deploy/test/cosyvoice_api_queue.py` | **官方队列版服务端（推荐照此做调度）** |
| `deploy/test/api_queue.md` | 队列版对接文档 |

---

## 10. 超时调配 + 插件侧配置（零代码改动）

### 10.1 三个超时分别在哪

| 超时 | 位置 | 默认 | 作用 |
| --- | --- | --- | --- |
| 客户端读超时 | 插件配置 `timeout` | **150s** | 插件等待「排队 + 推理」的总上限 |
| 客户端连接超时 | 插件内置 `connect=10s` | 10s | 连不上就判定服务器失联（`CosyVoiceServerError`） |
| 中转站入队等待 | 中转站 `COSY_QUEUE_WAIT` | 30s | 队满时最多等多久，超时回 429 |
| 中转站推理超时 | 中转站 `COSY_INFER_TIMEOUT` | 120s | 单次推理上限，超时回 504 |

### 10.2 调配公式（重要）

```
插件 timeout(默认150) ≥ 中转站 COSY_QUEUE_WAIT(30) + 中转站 COSY_INFER_TIMEOUT(120)
                        ≥ 排队最坏时长 + 单次推理最坏时长
```

- **默认 150 = 30 + 120，刚好覆盖**，无需改任何东西。
- 如果你把中转站排队时间或推理超时调大（例如排队 60s、推理 180s），**必须同步把插件 `timeout` 调大**（如 `timeout=250`），否则客户端先超时断连，自己制造重试验崩。
- 调小 `timeout` 也可以，但必须 ≥ 你的中转站最坏总耗时。宁可让客户端少等（快速失败）也别让它干等。

### 10.3 插件侧相关配置项一览（全部可配，改配置即可）

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:50002` | 单台中转站地址 |
| `servers` | `[]` | 多台中转站（url/enabled/default/weight） |
| `timeout` | 150 | 客户端读超时（按 §10.2 调配） |
| `tts_max_retry` | 0 | 失败重试次数；0=不重试，失败直接回退文字+进冷却 |
| `tts_retry_backoff` | 0.5 | 重试退避基数（秒） |
| `tts_cooldown_sec` | 30 | 失败后多久内不再打后端、直接回退文字 |
| `tts_concurrency` | 1 | 同时打到后端的最大请求数（中转站自己调度的话可调大） |

### 10.4 结论：本插件「零代码改动」

对接中转站**不需要改插件任何代码**，只需：

1. 中转站实现 §2 标准接口 + §3 状态码语义（对照 §7.5 清单）。
2. 插件配置里把 `base_url` 指向中转站（或 `servers` 填多台中转站）。
3. 若中转站排队/推理总耗时超过 150s，把插件 `timeout` 调大。

> 为什么插件侧不用动？因为插件只依赖「标准接口 + 状态码语义 + 一个可配置的超时」，这三样你都可控。中转站内部用什么协议（Gradio / gRPC / 本地进程）、怎么调度，插件完全不关心。
