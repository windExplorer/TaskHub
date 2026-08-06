# Middle Station 项目计划文档

**版本**：1.2  
**创建时间**：2026-08-06  
**更新时间**：2026-08-07  
**目标**：搭建一个 Windows 本地、中转站服务，接收 AstrBot 插件（CosyVoice3 TTS + ComfyUI 绘图）的请求，并进行任务调度、资源监控、排队限流。

---

## 1. 需求确认

### 1.1 监控范围
- **控制台实时显示**：CPU 使用率%、RAM 可用/使用量、GPU 占用率%、显存可用量
- **调度决策**：主要依赖 GPU 占用（>80% 时自动降并发或插队）
- **额外指标**：任务类型可配置显存占用量

### 1.2 支持的后端
- **当前**：TTS（CosyVoice3）和 ComfyUI
- **未来**：支持更多后端类型（通过配置驱动）
- **资源感知**：任务类型可配置显存占用量

### 1.3 WebUI 要求
- **风格**：clean modern UI（Tailwind + shadcn/ui），无 AI 风味
- **功能**：实时监控面板、任务列表（优先级调整、插队）、控制台输出、统计图表
- **接口**：/monitor（实时返回 CPU/RAM/GPU/队列长度）、/stats（历史指标）、/health（系统状态）

### 1.4 调度模型
- **队列**：asyncio.Queue + priority heapq（任务对象含 priority、task_type、estimated_duration、timestamp、resource_weight）
- **资源控制**：GPU 占用高时自动降低 MAX_CONCURRENT 或触发插队
- **状态管理**：prompt_id → 状态（queued/running/done/failed）
- **状态码**：严格遵守 200/429/503/504（429 让插件退避、504 超时、500 直接抛错）

---

## 2. 架构设计

### 2.1 整体架构
`
AstrBot 插件（CosyVoice3 + ComfyUI）
          ↓ (标准接口 /inference_zero_shot /prompt)
     中转站服务 (FastAPI + uvicorn)
          ↓
   队列调度器 (asyncio.Queue + heapq)
          ↓
   适配层 (路由 + 状态码映射)
          ↓
   真实后端 (TTS/ComfyUI)
`

### 2.2 核心模块
- **Router**：统一接口，适配 prompt_wav/PCM 字节流
- **Scheduler**：优先级队列 + GPU/CPU/RAM 监控 + 插队逻辑
- **Monitor**：实时采集资源 + WebSocket/SSE 推送
- **Stats**：持久化任务记录（SQLite 默认），支持 CSV/JSON 导出
- **Adapter**：处理 websocket 轮询、状态表

### 2.3 部署方式
- **平台**：Windows 原生（无 Docker）
- **依赖管理**：uv（fastapi、uvicorn、click、psutil、nvidia-ml-py）
- **启动命令**：
  - uv run uvicorn main:app --host 0.0.0.0 --port 9000
  - 或 	ts-station start（CLI）

---

## 3. 技能使用记录

### 3.1 已生成技能
- **webui-design**：已生成完整 Tailwind + shadcn WebUI 代码（监控面板、任务列表、控制台、图表）
- **frontend-design**：补充 shadcn/ui 组件模板
- **taste-design**：语义设计系统

### 3.2 当前状态
- 所有技能已同步到 .codex/skills/ 并更新 index
- WebUI 代码已生成，可直接集成 FastAPI 后端
- **v1.2（2026-08-07）：计划已全部进入实现阶段并落地**，见第 10 节

---

## 4. 接口对接契约

中转站需严格遵守 AstrBot 插件期望的**标准接口**（详见 docs/backend-api.md 和 docs/comfyui-backend-api.md），插件无需修改任何代码。

### 4.1 必须实现的接口
- GET / —— 健康检查 + 采样率（JSON：{" status\: \ok\, \model_loaded\: true, \sample_rate\: 24000})
- POST /inference_zero_shot —— 零样本 TTS（multipart 表单，支持 prompt_wav_path 或 prompt_wav）
- POST /inference_instruct2 —— 指令合成（可选）
- POST /prompt —— ComfyUI 绘图提交（JSON 工作流 + prompt_id 返回）
- GET /history/{prompt_id} / GET /history / GET /view —— 透传查询下载

### 4.2 状态码语义
- 200：成功返回结果
- 429：排队/限流（插件自动退避）
- 503：服务繁忙
- 504：推理超时（插件捕获）
- 500：内部错误（直接抛错）

### 4.3 响应格式
- TTS：裸 int16 PCM 字节流（无 WAV 头，由插件补全）
- ComfyUI：{\prompt_id\: \xxx...\}（32 位 hex）

---

## 5. 资源监控与调度逻辑

### 5.1 监控采集
- **工具**：psutil（CPU/RAM）、
vidia-ml-py（GPU/VRAM，需安装 NVIDIA CUDA 驱动）
- **频率**：每 1-2 秒一次，实时推送至 WebUI（WebSocket/SSE）
- **阈值**：GPU > 80% → 自动降 MAX_CONCURRENT 或插队；任务显存占用由配置驱动

### 5.2 队列调度
- 优先级队列（priority heapq）
- 任务对象包含：priority、 ask_type、estimated_duration、
esource_weight
- 资源权重计算：weight = estimated_duration * resource_weight
- 插队逻辑：高优先级任务可抢占或等待队列前

### 5.3 状态持久化
- SQLite（默认路径 ./middle-station.db），支持导出 CSV/JSON
- 每个 prompt_id 记录完整生命周期

---

## 6. 配置系统

### 6.1 核心配置示例
`yaml
# middle-station.yaml
server:
 host: 0.0.0.0
 port: 9000
 max_concurrent: 3
 queue_timeout: 30
 infer_timeout: 120
 db_path: \./middle-station.db\
tts:
 base_url: \http://127.0.0.1:50002\ # 插件配置指向
 sample_rate: 24000
comfyui:
 base_url: \http://127.0.0.1:8188\
 serialize_concurrent: 1 # 同一时刻只放行 1 个任务
monitoring:
 gpu_threshold: 80
 interval_seconds: 2
`

### 6.2 环境变量
- MIDDLE_HOST、MIDDLE_PORT、MIDDLE_MAX_CONCURRENT 等
- 支持 .env 或 uv 环境变量

---

## 7. 错误处理与重试

### 7.1 HTTP 状态码映射
- 429 → 429（客户端退避）
- 504 → 504（客户端超时）
- 500 → 500（直接抛错）

### 7.2 重试策略
- 指数退避（0.5s → 2s → 8s）
- 最大重试：3 次
- 失败后冷却：30s（插件侧 ts_cooldown_sec）

### 7.3 日志
- 使用 logging 模块，ERROR 级别记录关键失败
- WebUI 显示最近 100 条日志

---

## 8. 测试计划

- **单元测试**：队列调度、资源监控、状态码映射（pytest + asyncio）
- **集成测试**：AstrBot 插件模拟请求（使用 httpx 测试客户端）
- **性能测试**：高并发（100+ 请求）下 GPU 争抢场景
- **监控验证**：资源变化时统计准确率 > 99%
- **端到端**：插件侧零代码改动验证

---

## 9. 版本历史

### 1.2（2026-08-07）
- **实现阶段完成**，代码全部落地于仓库根目录（`middle-station/` 二级目录已并入根，`docs/` 保留计划文档）
- 重写 `scheduler.py`：heapq 优先级队列、GPU 感知动态并发（>阈值自动降并发）、插队/取消、429（排队超时）/504（推理超时）/500 状态码
- 新增 `config.py`（YAML + `MIDDLE_*` 环境变量覆盖）、`monitor.py`（psutil + NVML）、`storage.py`（SQLite 生命周期 + CSV/JSON 导出）
- 新增 `adapters/`：`tts.py`（multipart 转发、真实采样率探测覆盖、prompt_text 污染净化、文件名→文本回退）、`comfy.py`（/prompt 单飞调度 + 槽位生命周期跟踪 + 只读/上传透传）
- 重写 `main.py`：标准接口（/ 、/inference_zero_shot、/inference_instruct2、/prompt、/history、/view、/upload/image、/voices）+ 监控接口（/monitor、/stats、/queue、/tasks）+ WebSocket（/ws/monitor、/ws/tasks、/ws/logs）+ CLI（start/status/export）+ /ui 静态页
- 重写 `frontend/index.html`：Tailwind + Chart.js 实时监控面板（指标卡、CPU/GPU 曲线、队列并发曲线、任务表操作、控制台日志、24h 统计）
- 冒烟集成测试 14/14 通过（假 TTS/ComfyUI 后端 + 并发排队 + 429 背压验证）

### 1.1（2026-08-06）
- 补充接口对接契约
- 增加配置系统、错误处理、监控细节
- 修复技能路径引用（.reasonix → .codex）
- 优化测试计划

### 1.0（2026-08-06）
- 初始版本

---

## 10. 实现状态（v1.2）

计划已全部实现。启动与配置：

```bash
cd TaskHub
uv run python main.py start                    # 默认 0.0.0.0:9000
# 或 uvicorn 直跑：uv run uvicorn main:app --host 0.0.0.0 --port 9000
# 配置：middle-station.yaml（tts.base_url 指向真实 CosyVoice、comfyui.base_url 指向真实 ComfyUI）
# WebUI：http://127.0.0.1:9000/ui
```

插件侧零改动：AstrBot 插件 `base_url` / `comfyui_servers[].url` 指向本中转站即可。
超时调配注意：`server.queue_wait + server.infer_timeout <= 插件 timeout(150s)`，默认 30+120 刚好覆盖；若调大中转站超时，需同步调大插件 `timeout`。

### 10.1 接口核对清单（对照 docs/backend-api.md §7.5）

- [x] `GET /` 返回真实 `sample_rate`（启动后探测后端并覆盖）
- [x] `POST /inference_zero_shot`（支持 `prompt_wav_path` / `prompt_wav` 上传）
- [x] 429/503/504/500 状态码语义（429=排队超时退避、504=推理超时）
- [x] `prompt_text` 缺省时按文件名回退（voices 映射 + 污染净化）
- [x] `POST /inference_instruct2`（兼容）
- [x] `GET /voices`、`GET /queue`（监控排错）
- [x] ComfyUI `/prompt` 单飞 + 排队中 `/history` 返回空 + 上传/查询透传

---

**文档生成时间**：2026-08-07  
**文档位置**：docs/middle-station-plan.md