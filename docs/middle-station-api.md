# Middle Station API 接口文档

**版本**：1.2  
**Base URL**：`http://<host>:9000`（默认 `0.0.0.0:9000`）  
**WebUI**：`/ui`　|　**API 文档网页版**：`/ui/api.html`　|　**Swagger**：`/docs`（FastAPI 自动生成）

> 文档覆盖全部 HTTP 接口、WebSocket 与状态码语义。接口分四类：
> ① TTS 标准接口（AstrBot 语音插件对接）② ComfyUI 标准接口（绘图插件对接）
> ③ 监控与任务管理 ④ WebSocket 实时推送。

---

## 0. 通用说明

### 0.1 状态码语义（严格遵守）

| 状态码 | 含义 | 插件/调用方行为 |
| --- | --- | --- |
| `200` | 成功 | 正常解析 |
| `400` | 参数错误（缺 tts_text / 缺参考音频 / 非法 JSON） | 不重试，直接报错 |
| `404` | 任务不存在 / 非排队状态 | 不重试 |
| `429` | 排队等待超时（队列满或等待超过 `server.queue_wait`） | **指数退避重试** |
| `503` | 模型未加载完成 / 服务繁忙 | 退避重试 |
| `504` | 推理超时（超过 `server.infer_timeout`） | 退避重试 |
| `500` | 内部错误 / 上游失联 / 空音频 | 不重试，进冷却 |

> 上游（真实 CosyVoice / ComfyUI）返回的非 200 状态码**原样透传**给调用方。

### 0.2 数据格式

- TTS 合成结果：**裸 int16 PCM 字节流**（`application/octet-stream`），24kHz 单声道，无 WAV 头（WAV 头由插件补）。
- ComfyUI 任务：JSON `{"prompt_id": "32位hex"}`。
- 时间戳字段：Unix 秒（float）。

---

## 1. TTS 标准接口（CosyVoice 语音插件）

> 契约来源：`docs/backend-api.md`。插件零改动对接。

### 1.1 `GET /` — 健康检查 + 采样率

插件**首次合成前**必读，用返回的 `sample_rate` 覆盖本地配置（否则音调会变）。

**响应 200**：

```json
{ "status": "ok", "model_loaded": true, "sample_rate": 24000 }
```

### 1.2 `POST /inference_zero_shot` — 零样本合成（主路径）

`multipart/form-data`：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `tts_text` | ✅ | 目标文本（插件已分段，每段一次请求） |
| `prompt_text` | 可选 | 参考音频对应的纯人声文本；**缺省时**中转站按 `prompt_wav_path` 文件名从 voices 映射自动回退 |
| `prompt_wav` | 二选一 | 上传的参考音频文件 |
| `prompt_wav_path` | 二选一 | 参考音频在**后端**的文件名/路径（推荐，免上传大文件） |

- 两者都不给 → `400`。
- `prompt_text` 含 LLM 污染标记（`<|endofprompt|>` 等）或 >150 字会被自动净化丢弃，由后端 voices 映射回退。
- **响应 200**：裸 PCM 字节流。其他状态码透传后端。

**curl**：

```bash
# 参考音频走服务端路径（推荐）
curl -X POST http://127.0.0.1:9000/inference_zero_shot \
  -F "tts_text=你好，今天天气不错。" \
  -F "prompt_wav_path=xiaoyu.wav"

# 上传参考音频（AstrBot 本地文件）
curl -X POST http://127.0.0.1:9000/inference_zero_shot \
  -F "tts_text=你好" \
  -F "prompt_text=你好，我是小宇。" \
  -F "prompt_wav=@/path/to/xiaoyu.wav"
```

**Python（httpx，带 429 退避）**：

```python
import httpx, time

def synthesize(text, voice="xiaoyu.wav", max_retry=3):
    for attempt in range(max_retry):
        r = httpx.post("http://127.0.0.1:9000/inference_zero_shot",
                       data={"tts_text": text, "prompt_wav_path": voice},
                       timeout=130)
        if r.status_code == 200:
            return r.content
        if r.status_code in (429, 503, 504):
            time.sleep(0.5 * 2 ** attempt)
            continue
        print("[错误]", r.status_code, r.text)
        return None
```

### 1.3 `POST /inference_instruct2` — 指令合成（兼容保留）

字段同 1.2，另加：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `instruct_text` | ✅ | 语气指令，如「请用开心的语气说」 |

响应格式同 1.2。

### 1.4 `GET /voices` — 参考音频列表（排错用）

**响应 200**：

```json
{
  "voices_dir": "D:/CosyVoice/voices",
  "files": ["xiaoyu.wav", "boss.wav"],
  "texts": { "xiaoyu.wav": "你好，我是小宇。" }
}
```

---

## 2. ComfyUI 标准接口（绘图插件）

> 契约来源：`docs/comfyui-backend-api.md`。`/prompt` 走**单飞调度**（同一时刻仅放行 `serialize_concurrent` 个任务），其余接口透传。

### 2.1 `POST /prompt` — 提交绘图任务（核心，单飞）

**请求体 JSON**：

```json
{
  "prompt": { "4": { "class_type": "CheckpointLoaderSimple", "inputs": {} } },
  "client_id": "astrbot-comfyui-xxxx"
}
```

- 排队中的请求**内部阻塞等待槽位**（不返回 429），提交成功后立即返回 prompt_id。
- **响应 200**：`{ "prompt_id": "e1f2...32位hex" }`
- 校验失败：透传真实 ComfyUI 的状态码与文案（常见 400）。

### 2.2 `POST /upload/image` — 上传图生图参考图

`multipart/form-data`：`image`（文件）、`type`（固定 `input`）。

**响应 200**：

```json
{ "name": "photo.png", "subfolder": "", "type": "input" }
```

> 中转站保证图片落到真实 ComfyUI 的 `input` 目录，`name` 可被 `/prompt` 引用。

### 2.3 `GET /history/{prompt_id}` — 查询单个任务结果

- 排队中/尚未提交的真实任务返回 `{}`（插件会持续轮询，不会误判完成）。
- 完成后透传真实 ComfyUI 的历史 JSON（含 `outputs.images[]`）。

### 2.4 `GET /history` — 全部历史（透传）

### 2.5 `GET /view` — 下载输出图片（透传）

Query：`filename`、`subfolder`、`type`（通常 `type=output`）。响应为图片二进制。

---

## 3. 监控与任务管理接口

### 3.1 `GET /health` — 系统状态

```json
{
  "status": "ok",
  "gpu_load": 0.11,
  "model_loaded": true,
  "queue_length": 2,
  "running": 1,
  "max_concurrent": 3,
  "sample_rate": 24000
}
```

### 3.2 `GET /monitor` — 实时资源快照

```json
{
  "cpu_percent": 13.6,
  "ram_available_gb": 12.02,
  "ram_used_gb": 19.8,
  "ram_total_gb": 31.81,
  "gpu_load": 0.11,
  "gpu_free_gb": 6.43,
  "gpu_total_gb": 8.0,
  "gpu_threshold": 0.8,
  "ts": 1786033708.8,
  "queue_length": 0,
  "running": 0,
  "max_concurrent": 3,
  "effective_concurrent": 3
}
```

> `effective_concurrent` 为 GPU 过载时降并发后的有效值；`gpu_load > gpu_threshold` 时自动降为 `max(1, max_concurrent*0.5)`。

### 3.3 `GET /stats?hours=24` — 历史统计（SQLite）

```json
{
  "window_hours": 24,
  "total": 40, "done": 35, "failed": 0,
  "success_rate": 87.5,
  "by_type": { "tts": 35, "comfyui": 5 },
  "avg_run_seconds": 0.36
}
```

### 3.4 `GET /queue?limit=100` — 队列与任务状态

```json
{
  "queue_length": 2, "running": 1,
  "max_concurrent": 3, "effective_concurrent": 3,
  "gpu_threshold": 0.8, "gpu_load": 0.11,
  "total_queued": 123, "total_completed": 120,
  "tasks": [ { "task_id": "tts_xxx", "task_type": "tts", "priority": 0,
               "status": "running", "status_code": 200, "error": "",
               "created_at": 1786033708.8, "started_at": 1786033709.0,
               "finished_at": null, "queue_seconds": 0.2, "run_seconds": 1.5,
               "resource_weight": 1.0, "estimated_duration": 30.0 } ]
}
```

任务状态：`queued`（排队中）/ `running`（运行中）/ `done`（完成）/ `failed`（失败）/ `timeout`（超时）/ `cancelled`（已取消）。

### 3.5 `GET /tasks?limit=200` — 任务列表

```json
{ "tasks": [ /* 同 /queue 的 tasks 元素 */ ] }
```

### 3.6 `POST /tasks/{task_id}/promote?priority=-1` — 插队

把排队中任务提到指定优先级（数值越小越优先）。成功：`{"ok": true, "task_id": "...", "priority": -1}`；不存在或非排队：`404`。

### 3.7 `POST /tasks/{task_id}/cancel` — 取消排队任务

成功：`{"ok": true, "task_id": "..."}`；不存在或非排队：`404`。

### 3.8 `GET /config` — 当前生效配置（只读）

返回 `server` / `tts` / `comfyui` / `monitoring` 四组配置摘要与运行时值（含探测后的真实 `sample_rate`）。修改请编辑 `middle-station.yaml` 后重启。

---

## 4. WebSocket 实时推送

### 4.1 `WS /ws/monitor` — 资源监控（每秒一帧）

```json
{ "cpu_percent": 12.7, "ram_available_gb": 11.88, "gpu_load": 0.15,
  "gpu_free_gb": 6.43, "gpu_total_gb": 8.0, "gpu_threshold": 0.8,
  "queue_length": 0, "running": 0, "max_concurrent": 3,
  "effective_concurrent": 3, "ts": 1786033736.9 }
```

### 4.2 `WS /ws/tasks` — 任务事件

连接后先发全量 `init`，之后每次状态变更推 `task_update`，空闲时每 15s 发 `ping` 保活：

```json
{ "event": "init", "tasks": [ /* 全部任务 */ ] }
{ "event": "task_update", "task": { /* 任务对象，同 /queue tasks 元素 */ } }
{ "event": "ping" }
```

### 4.3 `WS /ws/logs` — 日志流

连接后增量推送（每条日志一行），格式：

```json
{ "ts": 1786033736.9, "level": "INFO", "message": "task tts_xxx done" }
```

---

## 5. 示例：完整调用链（Python）

```python
import httpx

BASE = "http://127.0.0.1:9000"

with httpx.Client(timeout=10) as c:
    # 1. 健康检查（拿采样率）
    print(c.get(f"{BASE}/").json())

    # 2. 提交 TTS
    r = c.post(f"{BASE}/inference_zero_shot",
               data={"tts_text": "你好", "prompt_wav_path": "xiaoyu.wav"}, timeout=150)
    if r.status_code == 200:
        pcm = r.content  # 裸 int16 PCM

    # 3. 提交 ComfyUI 绘图
    r = c.post(f"{BASE}/prompt",
               json={"prompt": {"4": {"class_type": "CheckpointLoaderSimple",
                                      "inputs": {"ckpt_name": "model.safetensors"}}},
                     "client_id": "demo"}, timeout=150)
    pid = r.json()["prompt_id"]

    # 4. 轮询结果
    import time
    for _ in range(120):
        h = c.get(f"{BASE}/history/{pid}").json()
        if pid in h:
            print("done:", h[pid]["outputs"])
            break
        time.sleep(2)

    # 5. 下载图片
    img = c.get(f"{BASE}/view", params={"filename": "00001.png",
                                        "subfolder": "", "type": "output"})
    open("out.png", "wb").write(img.content)
```

---

## 6. 相关资源

| 资源 | 位置 |
| --- | --- |
| WebUI 监控面板 | `/ui` |
| API 文档网页版 | `/ui/api.html` |
| Swagger | `/docs`（FastAPI 自动生成） |
| 计划文档 | `docs/middle-station-plan.md` |
| 对接契约（TTS） | `docs/backend-api.md` |
| 对接契约（ComfyUI） | `docs/comfyui-backend-api.md` |
| 配置 | 仓库根目录 `middle-station.yaml` |
