/** API 文档数据（自旧版 frontend/api.html 的 GROUPS 常量移植，内容 1:1） */

export interface DocParam {
  name: string;
  req: string;
  desc: string;
}

export interface DocEndpoint {
  /** HTTP 方法；缺省表示纯文字小节（path 作为标题） */
  method?: "GET" | "POST" | "WS";
  path?: string;
  title?: string;
  note?: string;
  params?: DocParam[];
  /** 状态码语义表（仅通用说明用） */
  statusTable?: Array<[string, string, string]>;
  /** 项目符号列表；反引号内容渲染为 code */
  bullets?: string[];
  footnote?: string;
  body?: string;
  curl?: string;
  py?: string;
  resp?: string;
}

export interface DocGroup {
  id: string;
  name: string;
  desc?: string;
  endpoints: DocEndpoint[];
}

export const DOC_GROUPS: DocGroup[] = [
  {
    id: "overview",
    name: "通用说明",
    endpoints: [
      {
        path: "状态码语义",
        statusTable: [
          ["200", "成功", "正常解析"],
          ["400", "参数错误（缺 tts_text / 缺参考音频 / 非法 JSON）", "不重试，直接报错"],
          ["404", "任务不存在 / 非排队状态", "不重试"],
          ["429", "排队等待超时（队列满）", "指数退避重试"],
          ["503", "模型未加载 / 服务繁忙", "退避重试"],
          ["504", "推理超时", "退避重试"],
          ["500", "内部错误 / 上游失联 / 空音频", "不重试，进冷却"],
        ],
        footnote:
          "上游（真实 CosyVoice / ComfyUI）返回的非 200 状态码会原样透传给调用方。TTS 结果返回**裸 int16 PCM 字节流**（24kHz 单声道、无 WAV 头）；ComfyUI 任务返回 JSON。",
      },
      {
        path: "数据格式",
        bullets: [
          "TTS 合成结果：`application/octet-stream`，裸 int16 PCM，WAV 头由插件补。",
          "ComfyUI 任务：`{ \"prompt_id\": \"32位hex\" }`。",
          "时间戳字段：Unix 秒（float）。",
        ],
      },
      {
        path: "运维速查",
        bullets: [
          "设备状态（本机 CPU/内存/磁盘/多卡温度功耗 + 上游 ComfyUI/CosyVoice）：`GET /device`，加 `?raw=1` 附上游 `/system_stats` 原始信息。",
          "任务是不是卡住了 / 上游是不是把 prompt 丢了：`GET /self-check`；想立刻核对不等巡检周期：`POST /self-check/run`。",
          "每个任务的真实进度（节点 x/总数、采样步数）：`GET /queue` 的 `tasks[].progress`，或订阅 `WS /ws/tasks`。",
          "三条兜底行为（上层无需处理）：上游丢弃 prompt → 自动终结并释放单飞槽位；出图有单任务硬超时；服务重启后遗留的 running/queued 历史记录自动收敛为终态。",
        ],
      },
    ],
  },
  {
    id: "tts",
    name: "TTS 标准接口（语音插件）",
    desc: "契约来源 docs/backend-api.md，插件零改动对接。",
    endpoints: [
      {
        method: "GET",
        path: "/",
        title: "健康检查 + 采样率",
        note: "插件首次合成前必读，用返回的 sample_rate 覆盖本地配置（否则音调会变）。",
        resp: `{ "status": "ok", "model_loaded": true, "sample_rate": 24000 }`,
      },
      {
        method: "POST",
        path: "/inference_zero_shot",
        title: "零样本合成（主路径）",
        note: "multipart/form-data。tts_text 必填；prompt_wav 与 prompt_wav_path 二选一。prompt_text 缺省时按文件名从 voices 映射自动回退；含 LLM 污染标记会被净化丢弃。成功响应头含 X-Queue-Position（该任务入队时刻前方任务数，含运行中）。",
        params: [
          { name: "tts_text", req: "必填", desc: "目标文本（插件已分段，每段一次请求）" },
          { name: "prompt_text", req: "可选", desc: "参考音频对应的纯人声文本" },
          { name: "prompt_wav", req: "二选一", desc: "上传的参考音频文件" },
          { name: "prompt_wav_path", req: "二选一", desc: "参考音频在服务端的文件名/路径（推荐）" },
        ],
        curl: `curl -X POST http://127.0.0.1:9000/inference_zero_shot \\
  -F "tts_text=你好，今天天气不错。" \\
  -F "prompt_wav_path=xiaoyu.wav"`,
        resp: "(裸 PCM 字节流 application/octet-stream)",
        py: `import httpx, time

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
        return None`,
      },
      {
        method: "POST",
        path: "/inference_instruct2",
        title: "指令合成（兼容保留）",
        note: "字段同 zero_shot，另需 instruct_text（如「请用开心的语气说」）。响应格式同 zero_shot。",
        params: [
          { name: "tts_text", req: "必填", desc: "目标文本" },
          { name: "instruct_text", req: "必填", desc: "语气指令" },
          { name: "prompt_wav / prompt_wav_path", req: "二选一", desc: "参考音频" },
        ],
        curl: `curl -X POST http://127.0.0.1:9000/inference_instruct2 \\
  -F "tts_text=今天太开心了！" \\
  -F "instruct_text=请用开心的语气说" \\
  -F "prompt_wav_path=xiaoyu.wav"`,
        resp: "(裸 PCM 字节流)",
      },
      {
        method: "GET",
        path: "/voices",
        title: "参考音频列表（排错用）",
        resp: `{ "voices_dir": "D:/CosyVoice/voices",
  "files": ["xiaoyu.wav", "boss.wav"],
  "texts": { "xiaoyu.wav": "你好，我是小宇。" } }`,
      },
    ],
  },
  {
    id: "comfy",
    name: "ComfyUI 标准接口（绘图插件）",
    desc: "契约来源 docs/comfyui-backend-api.md。/prompt 走单飞调度（同一时刻仅放行 serialize_concurrent 个），其余透传。",
    endpoints: [
      {
        method: "POST",
        path: "/prompt",
        title: "提交绘图任务（核心，单飞）",
        note: "排队中的请求内部阻塞等待槽位（不返回 429），提交成功后立即返回 prompt_id（中转站虚拟 pid，插件照旧用它轮询 /history）。校验失败透传真实 ComfyUI 状态码（常见 400）。成功响应头含 X-Queue-Position（前方任务数，含运行中）。转发时会改写 client_id 为中转站固定值（taskhub-xxxxxxxx，原值存 _client_id_original）——因为真实 ComfyUI 的 executing/progress 进度事件只发给「提交该 prompt 的 client」，中转站要用同一个 id 建 /ws 才收得到真实进度；在 ComfyUI 网页端队列里看到 client 是 taskhub-xxxx 属正常现象。以 _ 开头的私有字段不会被转发。",
        body: `{ "prompt": { "4": { "class_type": "CheckpointLoaderSimple", "inputs": {} } },
  "client_id": "astrbot-comfyui-xxxx" }`,
        resp: `{ "prompt_id": "e1f2...32位hex" }`,
      },
      {
        method: "POST",
        path: "/upload/image",
        title: "上传图生图参考图",
        note: "multipart/form-data：image（文件）、type（固定 input）。中转站保证图片落到真实 ComfyUI 的 input 目录。",
        resp: `{ "name": "photo.png", "subfolder": "", "type": "input" }`,
      },
      {
        method: "GET",
        path: "/history/{prompt_id}",
        title: "查询单个任务结果",
        note: "排队中/尚未提交返回 {}（插件持续轮询不会误判完成）；完成后透传真实历史 JSON（含 outputs.images[]）；任务已失败（上游执行报错 / watch_timeout 超时 / prompt 被上游丢弃）时返回合成错误条目，插件据此立即结束轮询，不会空转到自己的超时。",
        resp: `{ "e1f2...": { "outputs": {},
    "status": { "status_str": "error", "completed": false,
                "messages": [["execution_error", { "exception_message": "taskhub: ..." }]] } } }`,
      },
      { method: "GET", path: "/history", title: "全部历史（透传）" },
      {
        method: "GET",
        path: "/view",
        title: "下载输出图片（透传）",
        note: "Query：filename、subfolder、type（通常 type=output）。响应为图片二进制。",
        curl: `curl "http://127.0.0.1:9000/view?filename=00001.png&subfolder=&type=output" -o out.png`,
      },
    ],
  },
  {
    id: "monitor",
    name: "监控与任务管理",
    endpoints: [
      {
        method: "GET",
        path: "/health",
        title: "系统状态",
        resp: `{ "status": "ok", "gpu_load": 0.11, "model_loaded": true,
  "queue_length": 2, "running": 1, "max_concurrent": 3,
  "sample_rate": 24000,
  "self_check": { "level": "warn", "count": 1, "items": [ /* 见 /self-check */ ] } }`,
      },
      {
        method: "GET",
        path: "/monitor",
        title: "实时资源快照",
        note: "effective_concurrent 为 GPU 过载降并发后的有效值；gpu_load > gpu_threshold 时自动降为 max(1, max_concurrent*0.5)。同样带 self_check。",
        resp: `{ "cpu_percent": 13.6, "ram_available_gb": 12.02, "ram_used_gb": 19.8,
  "ram_total_gb": 31.81, "gpu_load": 0.11, "gpu_free_gb": 6.43,
  "gpu_total_gb": 8.0, "gpu_threshold": 0.8, "ts": 1786033708.8,
  "queue_length": 0, "running": 0, "max_concurrent": 3,
  "effective_concurrent": 3 }`,
      },
      {
        method: "GET",
        path: "/device",
        title: "当前设备状态（本机 + 上游聚合）",
        note: "即时采样，不等 /monitor 的采样周期。「上游聚合」= 中转站作为客户端把真实后端的状态一次问全并归一化：ComfyUI 用 GET /queue + GET /system_stats，CosyVoice 用 GET /（健康检查）。任一上游挂了只体现为 reachable:false，接口仍返回 200。CPU 封装温度 / 机箱风扇转速在 Windows 上没有免驱来源：装了 LibreHardwareMonitor（启用 WMI provider）就自动出现，否则相关字段为 null/[] 并带 note 说明（thermal.acpi_zones_c 里通常还有一个 ACPI 热区温度，台式机上多为主板/PCH）。",
        params: [{ name: "raw", req: "否", desc: "=1 时额外返回真实 ComfyUI /system_stats 原始 JSON（字段 raw_system_stats）" }],
        curl: `curl "http://127.0.0.1:9000/device"`,
        resp: `{
  "ts": 1790348487.9,
  "host": {
    "hostname": "ishvara",
    "os": { "name": "Windows 11", "caption": "Windows 11 专业工作站版",
            "edition": "专业工作站版", "edition_id": "ProfessionalWorkstation",
            "display_version": "25H2", "build": "26200", "ubr": 9550,
            "version": "10.0.26200.9550", "kernel": "10.0.26200", "arch": "AMD64",
            "install_time": 1748573738, "python": "3.12.13" },
    "thermal": { "cpu_package_c": null, "cpu_cores_c": {}, "other_c": {},
                 "acpi_zones_c": { "\\\\_TZ.TZ00": 27.9 }, "source": "acpi-perf",
                 "note": "CPU 封装/核心温度与机箱风扇转速需要带内核驱动的工具（LibreHardwareMonitor / HWiNFO，启用 WMI provider）；acpi_zones_c 来自 ACPI 热区，通常反映主板/PCH" },
    "fans": { "gpu": [ { "index": 0, "percent": 53 } ], "system": [],
              "note": "机箱/CPU 风扇转速需要 LibreHardwareMonitor 或 HWiNFO（启用 WMI provider）" },
    "host_uptime_s": 106000, "service_uptime_s": 4020,
    "cpu": { "model": "12th Gen Intel(R) Core(TM) i5-12400F", "percent": 8.3,
             "cores_physical": 6, "cores_logical": 12, "current_mhz": 2500 },
    "ram": { "total_gb": 31.81, "used_gb": 16.84, "available_gb": 14.97, "percent": 52.9 },
    "disk": { "path": "E:\\\\...", "total_gb": 349.9, "used_gb": 290.3, "free_gb": 59.7, "percent": 82.9 },
    "process": { "pid": 27256, "uptime_s": 4020, "threads": 31, "rss_gb": 0.4 },
    "nvidia_ok": true, "driver_version": "617.14",
    "gpus": [ { "index": 0, "name": "NVIDIA GeForce RTX 3060 Ti",
                "util_percent": 5, "mem_total_gb": 8.0, "mem_used_gb": 4.95,
                "mem_free_gb": 3.05, "mem_percent": 61.9, "temperature_c": 40,
                "power_w": 44.5, "power_limit_w": 200.0, "fan_percent": 43,
                "clock_mhz": 1665, "mem_clock_mhz": 6801 } ]
  },
  "upstreams": {
    "comfyui": { "base_url": "http://127.0.0.1:8188", "reachable": true,
                 "system_stats_ok": true, "client_id": "taskhub-1a2b3c4d",
                 "queue": { "running": [], "pending": [], "queue_remaining": 0 },
                 "progress_stream": { "connected": true, "last_event_ago": 0.3 },
                 "system": { "comfyui_version": "0.37.0", "os": "win32",
                             "python": "3.13.11", "pytorch": "2.9.1+cu130",
                             "ram_free_gb": 14.97, "ram_total_gb": 31.81, "argv": [] },
                 "devices": [ { "name": "cuda:0 NVIDIA GeForce RTX 3060 Ti", "type": "cuda",
                                "vram_total_gb": 8.0, "vram_free_gb": 6.72,
                                "torch_vram_total_gb": 0.344, "torch_vram_free_gb": 0.166 } ] },
    "cosyvoice": { "base_url": "http://127.0.0.1:50002", "reachable": true,
                   "model_loaded": true, "sample_rate": 24000, "voices": 3,
                   "voices_dir": "E:\\\\voices", "last_probe_ts": 1790348487.9,
                   "info": { "status": "ok", "model_loaded": true, "sample_rate": 24000 } }
  },
  "scheduler": { "queue_length": 0, "running": 0, "max_concurrent": 1,
                 "effective_concurrent": 1, "throttle_reason": "normal",
                 "total_queued": 275, "total_completed": 253 },
  "self_check": { "level": "ok", "count": 0, "items": [] }
}`,
      },
      {
        method: "GET",
        path: "/self-check",
        title: "上游自检详情",
        note: "异常项 items[] 的 code：lost（上游丢弃 prompt，已自动终结任务并释放槽位）/ slow（耗时超同类 P95×slow_factor）/ stalled（上游在跑但无进度事件）/ slot_wait（等槽位过久）/ upstream_external（上游有非本站任务）/ upstream_vram_low / upstream_unreachable / tts_not_ready。probe 为最近一次上游探测快照，progress_stream 为 /ws 订阅状态。",
        resp: `{
  "level": "warn", "count": 2, "cycles": 42, "last_run": 1790348487.9,
  "items": [
    { "key": "resource:vram", "level": "warn", "code": "upstream_vram_low",
      "msg": "上游显存仅剩 0.63GB (< 1.0GB)，出图/语音可能 OOM", "task_id": null, "ts": 1790348487.6 },
    { "key": "upstream:external", "level": "info", "code": "upstream_external",
      "msg": "上游有 1 个执行中 / 0 个排队的非本站已知任务", "task_id": null, "ts": 1790348487.6 }
  ],
  "probe": { "reachable": true, "running": ["eba82a3a-..."], "pending": [],
             "vram_free_gb": 0.63, "vram_total_gb": 8.0 },
  "progress_stream": { "connected": true, "last_event_ago": 0.3, "queue_remaining": 1 }
}`,
      },
      {
        method: "POST",
        path: "/self-check/run",
        title: "手动触发一次自检",
        note: "排查用，不必等下一个巡检周期（默认 10s）。返回结构与 GET /self-check 相同。",
        curl: `curl -X POST "http://127.0.0.1:9000/self-check/run"`,
      },
      {
        method: "GET",
        path: "/stats?hours=24",
        title: "历史统计（SQLite）",
        resp: `{ "window_hours": 24, "total": 40, "done": 35, "failed": 0,
  "success_rate": 87.5, "by_type": { "tts": 35, "comfyui": 5 },
  "avg_run_seconds": 0.36 }`,
      },
      {
        method: "GET",
        path: "/queue?limit=100",
        title: "队列与任务状态",
        note: "任务状态：queued / waiting（等单飞槽位）/ running / done / failed / timeout / cancelled。绘图任务带 progress（真实上游进度：{nodes_done, nodes_total, node, step, step_total, state}，需 progress_stream 在线，最多每秒推一次）；任务被自检判异常时带 anomaly（{level, code, msg, ts}），二者只走 WebSocket 实时推送、不落库。",
        resp: `{ "queue_length": 2, "running": 1, "max_concurrent": 3,
  "effective_concurrent": 3, "gpu_threshold": 0.8, "gpu_load": 0.11,
  "total_queued": 123, "total_completed": 120,
  "tasks": [ { "task_id": "tts_xxx", "task_type": "tts", "priority": 0,
               "status": "running", "status_code": 200, "error": "",
               "created_at": 1786033708.8, "started_at": 1786033709.0,
               "finished_at": null, "queue_seconds": 0.2, "run_seconds": 1.5,
               "resource_weight": 1.0, "estimated_duration": 30.0,
               "progress": { "nodes_done": 12, "nodes_total": 28, "node": "9",
                             "step": 7, "step_total": 20, "state": "running" },
               "anomaly": null } ] }`,
      },
      {
        method: "GET",
        path: "/tasks",
        title: "全部任务（分页 + 筛选，查 SQLite 历史）",
        note: "参数：page、page_size(默认20)、task_type(tts/comfyui)、status(queued/waiting/running/done/failed/timeout/cancelled)、keyword(模糊搜索)。任务记录持久化，重启不丢；请求参数存档在 task_payloads 表。",
        resp: `{ "total": 40, "page": 1, "page_size": 20, "pages": 2,
  "tasks": [ /* 任务对象 */ ] }`,
      },
      {
        method: "POST",
        path: "/tasks/{task_id}/promote?priority=-1",
        title: "插队",
        note: "把排队中任务提到指定优先级（数值越小越优先）。不存在或非排队返回 404。",
        resp: `{ "ok": true, "task_id": "tts_xxx", "priority": -1 }`,
      },
      {
        method: "POST",
        path: "/tasks/{task_id}/cancel",
        title: "取消/停止任务",
        note: "排队任务直接取消；运行/等槽位的绘图任务会中断真实 ComfyUI 正在执行的出图并释放单飞槽位（状态 cancelled / 499）。不存在或不可操作返回 404。",
        resp: `{ "ok": true, "task_id": "tts_xxx" }`,
      },
      {
        method: "GET",
        path: "/config",
        title: "当前生效配置（只读）",
        note: "返回 server / tts / comfyui / monitoring / inspector 五组配置摘要与运行时值（含探测后的真实 sample_rate、提交给真实 ComfyUI 的 client_id）。与接口行为强相关的项：queue_wait(429 阈值)、infer_timeout(504 阈值)、comfyui.watch_timeout(单任务出图硬超时)、comfyui.watch_lost_grace/confirm(丢失判定)、comfyui.progress_stream、inspector.*(自检阈值，含 auto_stop_stalled 卡住自动停止，默认关闭)。修改请编辑 middle-station.yaml 后重启。",
      },
    ],
  },
  {
    id: "ws",
    name: "WebSocket 实时推送",
    endpoints: [
      {
        method: "WS",
        path: "/ws/monitor",
        title: "资源监控（每秒一帧）",
        note: "字段同 GET /monitor（含 self_check）。更完整的设备信息（型号/温度/功耗/上游状态）请用 GET /device，不在这里每秒推送。",
        resp: `{ "cpu_percent": 12.7, "ram_available_gb": 11.88, "gpu_load": 0.15,
  "gpu_free_gb": 6.43, "gpu_total_gb": 8.0, "gpu_threshold": 0.8,
  "queue_length": 0, "running": 0, "max_concurrent": 3,
  "effective_concurrent": 3, "ts": 1786033736.9,
  "self_check": { "level": "ok", "count": 0, "items": [] } }`,
      },
      {
        method: "WS",
        path: "/ws/tasks",
        title: "任务事件",
        note: "连接后先发全量 init，之后推 task_update，空闲每 15s 发 ping 保活。task_update 有两种来源、格式相同：①状态变更（入队/开始/等待槽位/完成/失败/超时/取消，会落库）；②实时更新（真实进度 progress、自检异常 anomaly，不落库，最多每秒一次）。因此不要用事件频率推断任务是否在推进——判「卡住」看 task.anomaly 或 GET /self-check。",
        resp: `{ "event": "init", "tasks": [ /* 全部任务 */ ] }
{ "event": "task_update", "task": { /* 任务对象，含 progress / anomaly */ } }
{ "event": "ping" }`,
      },
      {
        method: "WS",
        path: "/ws/logs",
        title: "日志流（增量推送）",
        resp: `{ "ts": 1786033736.9, "level": "INFO", "message": "task tts_xxx done" }`,
      },
    ],
  },
];
