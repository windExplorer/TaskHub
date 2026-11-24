# NSFW 检测接入统一调度队列 —— 改造说明

> 目标：让「涩图检测」请求进入 TaskHub 的优先级队列，由调度器统一排队/调度，
> 显存够就直接执行、不够就排队等待；插件 POST `/nsfw/detect/simple` 后同步拿回结论。
> 本文基于 TaskHub `f2b5b76`，逐文件列出改动点与可直接照抄的代码。
>
> 关联：消费方插件 `astrbot-comfyui-anima`（v8.13.7 起指令侧已按「排队」语义调长超时、
> 读 `X-Queue-Position`），服务端契约 `/nsfw/detect/simple` 保持 multipart 入 / JSON 出不变。

---

## 0. 为什么要改（现状问题）

当前 `/nsfw/detect/simple`（`main.py:592`）是**直连代理**：

```python
body = await request.body()
ctype = request.headers.get('content-type', 'application/octet-stream')
status, payload = await nsfw_checker.proxy_detect(body, ctype)   # 直接 POST 到 8890
return JSONResponse(payload, status_code=status)
```

`NsfwChecker.proxy_detect`（`adapters/nsfw.py:78`）直接 `httpx.post` 到 nsfw-check，**不经
scheduler、不占队列槽位**。因此：

- 检测请求与绘图/TTS 各自为政，显存紧张时三方抢卡，可能 OOM；
- 插件侧「加入到队列、显存够直接执行」的预期落空（所以之前连不上/超时回退本地）；
- 检测任务不进任务列表，WebUI 看不到、无法插队/取消/统计。

另外，NSFW 目前只是 ComfyUI 任务的**后置送检**环节（`adapters/comfy.py` 的 `nsfw`
字段），不是独立任务类型。要接入队列，需把 `nsfw` 变成一等 `task_type`。

---

## 1. 改动清单总览

| 文件 | 改动 |
|---|---|
| `scheduler.py` | ① `Task.task_type` 注释放开 `nsfw`；② **新增任务级显存准入**（不够则保持 queued）；③ 队列超时的 429 让客户端可重试 |
| `adapters/nsfw.py` | 新增 `detect_task(task)` handler，包装 `proxy_detect`，返回 `(status, payload)` 并透传 `task.status_code` |
| `main.py` | ① 新增 `_submit_json()` 同步包装；② 重写 `/nsfw/detect/simple` 改为入队；③ `tasks_enabled`/`tasks_enabled` 加 nsfw；④ 类型切换清理补 nsfw；⑤ `NsfwChecker` 构造时把 scheduler 引用注入（可选，用于任务内更新状态） |
| `config.py` | `ServerConfig.tasks_enabled` 默认值加 `nsfw: True`；加载逻辑加 nsfw |
| `middle-station.yaml` | `server.tasks_enabled.nsfw: true`；`server.queue_wait` 调大（见 §6） |
| `webui/src/types.ts`、`constants.ts` | `TaskType` / `TYPE_META` / `TYPE_COLOR` 加 `nsfw` |
| 测试（新建） | `tests/test_nsfw_queue.py`：入队 + 显存准入 + 超时映射 |

---

## 2. scheduler.py —— 任务类型与显存准入

### 2.1 放开 task_type 注释（`scheduler.py:32`）

```python
task_type: str = field(compare=False)          # 'tts' | 'comfyui' | 'nsfw'
```

### 2.2 新增「任务级显存准入」

在 `Scheduler` 类里新增方法（建议放在 `_effective_concurrent` 之后，`_worker_loop` 之前）：

```python
def _task_resource_ready(self, task: Task) -> bool:
    """任务级准入：nsfw 检测在显存不足时保持 queued，不派发。

    - 非 nsfw 任务恒 True（TTS 不需显存、ComfyUI 有自己的单飞信号量）。
    - 取不到显存数据（无 NVIDIA / total=0）时 True，退化为纯并发控制。
    - 判定口径与 _effective_concurrent 一致：可用比例 < vram_threshold
      或 可用绝对量 < vram_min_free_gb 即为「不够」。
    """
    if task.task_type != 'nsfw':
        return True
    try:
        free_gb, total_gb = self.vram_provider()
    except Exception:
        return True
    if not total_gb or total_gb <= 0:
        return True
    return not (free_gb / total_gb < self.vram_threshold
                or free_gb < self.vram_min_free_gb)
```

### 2.3 在派发前调用（`scheduler.py:356` 之后）

`_worker_loop` 弹出任务、过滤脏条目之后，**在类型切换清理之前**插入准入判断。
关键：不满足时把任务**放回堆**并短暂等待，避免忙等与队头饥饿。

```python
task = heapq.heappop(self._heap)
if task.status != 'queued':
    continue
# v8.x 新增：nsfw 显存准入——不够则放回队列，等 0.2s 再试（与满槽等待同频）
if not self._task_resource_ready(task):
    heapq.heappush(self._heap, task)
    self._wakeup.clear()
    try:
        await asyncio.wait_for(self._wakeup.wait(), timeout=0.2)
    except asyncio.TimeoutError:
        pass
    continue
```

> 注意：`queue_position` 是入队时快照（`add_task` 里算的），放回堆不会重算——这是对的，
> 排队位置对客户端应该稳定。
>
> 饥饿风险：`nsfw` 若长期等显存，会阻塞堆顶后续任务（`max_concurrent=1` 时本来就是串行，
> 影响有限）。若要避免，可改为「把不满足的任务暂存到单独列表、先派发后面的任务」，
> 但实现复杂度上升，按需再做。

### 2.4 超时语义（无需改，确认即可）

`scheduler.submit()`（`scheduler.py:234`）已经是两阶段等待：

- 排队超时 → `(None, 429, 'queue wait timeout...')`
- 执行超时 → `(None, 504, 'inference timeout')`

插件侧读 429 可退避重试，符合「排队中」语义。**不要改这段**。

---

## 3. adapters/nsfw.py —— 新增 detect_task handler

在 `NsfwChecker` 类中、`proxy_detect` 之后新增：

```python
async def detect_task(self, task) -> Tuple[int, dict]:
    """调度器 handler：把插件指令的检测请求作为任务执行。

    task.payload = {'body': bytes, 'ctype': str}
    返回 (status, payload)；同时把 status 写到 task.status_code，
    使 scheduler._run_task 将非 200 标为 failed 并把 code 回给等待方。
    """
    payload = task.payload or {}
    body = bytes(payload.get('body') or b'')
    ctype = str(payload.get('ctype') or 'application/octet-stream')
    status, result = await self.proxy_detect(body, ctype)
    task.status_code = status
    if status != 200:
        task.error = str(result.get('error') or f'upstream http {status}')
    # 顺带回写 nsfw 结论，让任务列表/WebUI 展示送检结果（可选但推荐）
    try:
        task.nsfw = dict(result) if isinstance(result, dict) else None
    except Exception:
        pass
    return status, result
```

> `scheduler._run_task`（`scheduler.py:399`）把 handler 返回值放进 `task.result`，
> 并以 `task.status_code`（默认 200，handler 可改）作为等待方结果码。
> 上面同时返回 `(status, result)` 并设置 `task.status_code` 是为了两处一致：
> `_submit_json` 可直接用 `scheduler.submit()` 返回的 code。

---

## 4. main.py —— 入队包装 + 重写检测端点

### 4.1 新增同步 JSON 包装 `_submit_json`（放在现有 `_submit` 之后，`main.py:405` 附近）

现有 `_submit` 把 result 当 bytes 塞进 `Response`，NSFW 返回的是 dict，需另做一个 JSON 版：

```python
async def _submit_json(task: Task, queue_wait=None, infer_timeout=None) -> JSONResponse:
    """入队并同步等待，结果为 JSON（NSFW 检测用）。

    与 _submit 的区别：返回 JSONResponse(content=result) 而非 Response(bytes)，
    并同样透传 X-Queue-Position 响应头。
    """
    if not cfg.server.tasks_enabled.get(task.task_type, True):
        raise HTTPException(status_code=503, detail=f'{task.task_type} 业务已被 WebUI 关闭')
    scheduler.add_task(task)
    result, code, err = await scheduler.submit(
        task, queue_wait=queue_wait, infer_timeout=infer_timeout)
    if code == 200:
        resp = JSONResponse(content=result)
        resp.headers['X-Queue-Position'] = str(task.queue_position)
        return resp
    raise HTTPException(status_code=code, detail=err or f'http {code}')
```

### 4.2 重写 `/nsfw/detect/simple`（`main.py:592-604`）

```python
@app.post('/nsfw/detect/simple')
async def nsfw_detect_proxy_api(request: Request):
    """插件指令远程检测中转：multipart 原样入队，由调度器排队后转发 nsfw-check。

    契约不变（multipart 入 / JSON 出 + X-Queue-Position），插件无感。
    """
    if nsfw_checker is None:
        raise HTTPException(status_code=404, detail='nsfw checker is disabled')
    body = await request.body()
    ctype = request.headers.get('content-type', 'application/octet-stream')
    source = ''
    try:
        # 从 multipart _body 里尽力取 source（仅用于日志/审计，不影响转发原 body）
        text = body.decode('utf-8', 'ignore')
        if 'name="source"' in text:
            seg = text.split('name="source"', 1)[1].split('\r\n', 2)
            source = seg[-1].strip() if len(seg) >= 3 else ''
    except Exception:
        pass
    task = Task(
        priority=0,
        created_at=time.time(),
        task_id=_new_id('nsfw'),
        task_type='nsfw',
        payload={'body': body, 'ctype': ctype, 'source': source},
        handler=nsfw_checker.detect_task,
        estimated_duration=8.0,
        resource_weight=1.0,
        # 排队等待：用 server.queue_wait（默认 30s）；执行超时用 nsfw.timeout+余量
        # 显式传参，避免被 server.infer_timeout=110 拉长到不该有的时长。
    )
    return await _submit_json(
        task,
        queue_wait=cfg.server.queue_wait,
        infer_timeout=max(cfg.nsfw.timeout + 15.0, 30.0),
    )
```

要点：

- `handler=nsfw_checker.detect_task` —— 复用现有代理逻辑，只是现在跑在调度器里；
- `queue_wait` 用 server 配置（排队超时回 429，插件可重试）；
- `infer_timeout` 显式用 `nsfw.timeout + 余量`，不要用 `server.infer_timeout=110`；
- 响应头 `X-Queue-Position` 由 `_submit_json` 透传，插件据此显示「排队第 N 位」。

### 4.3 任务类型切换清理补 nsfw（`main.py:244-257`）

在 `_vram_cleanup_on_type_switch` 里补两段，避免 nsfw 与 comfyui 同卡抢模型：

```python
# 原逻辑保留：切离 comfyui 时释放 ComfyUI 显存
if prev_type == 'comfyui' and cfg.comfyui.free_vram_on_type_switch:
    await comfy.free_vram()
if nsfw_checker is not None:
    # 切到 nsfw 前预热检测模型（消除卸载后的冷启动 ~4s）
    if next_type == 'nsfw':
        await nsfw_checker.warm()
    # 切离 nsfw（去 comfyui/tts）时卸载模型，把显存让给上游
    elif prev_type == 'nsfw':
        await nsfw_checker.release()
    # 原有：切离 comfyui 时顺带卸载 nsfw（可能与 comfyui 同卡）
    elif prev_type == 'comfyui':
        await nsfw_checker.release()
```

### 4.4 初始化处无需大改

`create_app`（`main.py:1033`）已有 `nsfw_checker = NsfwChecker(cfg.nsfw) ...`，
`detect_task` 是它的实例方法，handler 引用天然可用，不需要注入 scheduler。

---

## 5. 配置与前端

### 5.1 `config.py`

- `ServerConfig.tasks_enabled`（`config.py:57`）默认值：

```python
tasks_enabled: Dict[str, bool] = field(
    default_factory=lambda: {'tts': True, 'comfyui': True, 'nsfw': True})
```

- `Config.load` 的 tasks_enabled 加载（`config.py:166-170`）：

```python
cfg.server.tasks_enabled = {
    'tts': bool(te.get('tts', True)),
    'comfyui': bool(te.get('comfyui', True)),
    'nsfw': bool(te.get('nsfw', True)),
}
```

### 5.2 `middle-station.yaml`

```yaml
server:
  queue_wait: 60        # 见 §6：给排队留足时间，插件侧超时须 >= 本值 + infer_timeout
  infer_timeout: 110
  tasks_enabled:
    tts: true
    comfyui: true
    nsfw: true
```

### 5.3 前端（`webui/src/`）

- `types.ts:3`：

```typescript
export type TaskType = "tts" | "comfyui" | "nsfw";
```

- `constants.ts:25-28` 的 `TYPE_META`：

```typescript
export const TYPE_META: Record<TaskType, { label: string; cls: string }> = {
  tts: { label: "语音", cls: "bg-info-bg text-info border-info/40" },
  comfyui: { label: "绘图", cls: "bg-accent-2-bg text-accent-2 border-accent-2/40" },
  nsfw: { label: "涩图检测", cls: "bg-warn-bg text-warn border-warn/40" },
};
```

- `constants.ts` 的 `TYPE_COLOR`（约 104-106 行）同样加一种颜色；
- 如 `ConfigDialog.tsx` 有 `tasks_enabled` 的逐项开关，加一行 nsfw。

改完前端需重建：`cd webui && npm run build`（产物 `webui/dist`）。

---

## 6. 超时对齐（关键，否则插件先断连）

调用链超时关系：

```
插件 cmd_detect_timeout(240s)
  >= TaskHub queue_wait + max(nsfw.timeout + 15, 30)
```

当前插件侧默认 240s，TaskHub 若 `queue_wait=60`、`nsfw.timeout=25` → 60+40=100s，安全。
**不要把 `queue_wait` 调到 200 以上**，否则客户端先超时。

排队超时的语义：`scheduler.submit` 在排队阶段超时回 **429**，插件收到后应退避重试；
本插件 v8.13.5+ 超时已调长，正常排队不会触发 429。

---

## 7. 验证步骤

1. **单元/集成自检**（项目原先没有测试，建议新建 `tests/test_nsfw_queue.py`）：
   - 构造 `Task(task_type='nsfw', handler=...)`，`add_task` 后确认 `task_type` 入堆、
     `X-Queue-Position` 正确；
   - mock `vram_provider` 返回低显存，确认 `_task_resource_ready` 为 False、任务保持 queued；
   - mock `proxy_detect` 返回 `(200, {...})`，确认 `detect_task` 透传 status 与 result；
   - 排队超时/执行超时分别映射 429/504。
   运行：`python -m pytest tests/test_nsfw_queue.py`（需在 `pyproject.toml` 加 pytest 依赖）。

2. **真实冒烟**：
   - 确认 nsfw-check 在 8890 可达：`curl http://127.0.0.1:8890/healthz`；
   - 启动 TaskHub，看启动日志 `TaskHub up: ... nsfw=label@http://127.0.0.1:8890`（不是 `off`）；
   - 发一张图：
     ```
     curl -F "file=@test.png" -F "source=plugin-cmd" -i http://127.0.0.1:9000/nsfw/detect/simple
     ```
     期望：HTTP 200、body 含 `ok/level/action/score/labels/record_id`、响应头含
     `X-Queue-Position: 0`；
   - WebUI 任务页应出现一条 `nsfw` 类型的已完成任务。

3. **插件侧联调**：装 anima v8.13.7+，发 `/涩图检测`，回复应含
   `来源：云端检测·排队第N位` 与 `命中部位：…`。

---

## 8. 已知坑 / 注意事项

- **`nsfw_checker is None` 导致 404**：`main.py:202-209` 启动时探测 8890，不通就把
  `nsfw_checker` 置 None，所有 `/nsfw/*` 端点返回 404。所以先确保 nsfw-check 服务可达。
  另注意 `middle-station.yaml` 里 nsfw-check 的 `cwd: D:/AI/nsfw-check` 与实际部署路径
  （工作区里是 `X:/_WorkSpace/Mine/nsfw-check`）要一致，否则服务面板拉不起 8890。
- **`_submit_json` 别复用 `_submit`**：`_submit` 把 result 塞进 bytes `Response`，dict 会炸。
- **`detect_task` 必须写 `task.status_code`**：否则上游非 200 会被调度器当成功（默认 200）。
- **队列里 nsfw 任务占并发槽**：`resource_weight` 当前不参与并发决策，nsfw 执行期间
  ComfyUI/TTS 不会并行（`max_concurrent=1` 时）。这是「统一排队」的预期行为。
- **`X-Queue-Position` 是入队快照**：随队列推进实际等待会变，插件只用来展示，别当精确值。
- **不要在 nsfw handler 里再套 `asyncio.Semaphore`**：调度器已经限并发，双锁会死锁/降吞吐。
