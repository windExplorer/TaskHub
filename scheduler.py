"""任务调度器：优先级队列 + GPU 感知动态并发 + 插队/取消 + 状态持久化事件。

设计对照 docs/middle-station-plan.md 第 5 节：
- 队列：heapq 优先级队列（priority 越小越优先，同优先级按创建时间 FIFO）
- 资源控制：GPU 占用超过阈值时自动降低有效并发（最小不低于 gpu_scale_min_concurrent）
- 状态机：queued -> running -> done / failed / timeout / cancelled
- 状态码：429（排队超时，让上游退避）/ 503（繁忙）/ 504（推理超时）/ 500（内部错误）
"""
import asyncio
import heapq
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger('middle_station.scheduler')


@dataclass(order=True)
class Task:
    """一个待调度任务。

    priority 数值越小越优先（插队 = 赋更小的 priority）。
    handler 是 worker 实际执行的协程，签名 async handler(task) -> result。
    handler 内部可设置 task.status_code / task.error（例如透传上游非 200），
    抛 BackendError 则映射为对应状态码，抛其他异常 -> 500，超时 -> 504。
    """
    priority: int
    created_at: float
    task_id: str = field(compare=False)
    task_type: str = field(compare=False)          # 'tts' | 'comfyui'
    payload: Any = field(default=None, compare=False)
    resource_weight: float = field(default=1.0, compare=False)
    estimated_duration: float = field(default=60.0, compare=False)
    handler: Optional[Callable] = field(default=None, compare=False)

    status: str = field(default='queued', compare=False)
    status_code: int = field(default=200, compare=False)
    result: Any = field(default=None, compare=False)
    error: str = field(default='', compare=False)
    started_at: float = field(default=0.0, compare=False)
    finished_at: float = field(default=0.0, compare=False)
    future: Any = field(default=None, compare=False)
    started_event: Any = field(default=None, compare=False)

    def __post_init__(self):
        self.started_event = asyncio.Event()

    @property
    def queue_seconds(self) -> float:
        if self.started_at:
            return max(0.0, self.started_at - self.created_at)
        return max(0.0, time.time() - self.created_at)

    @property
    def run_seconds(self) -> float:
        if self.started_at and self.finished_at:
            return max(0.0, self.finished_at - self.started_at)
        if self.started_at:
            return max(0.0, time.time() - self.started_at)
        return 0.0

    def to_dict(self) -> Dict:
        return {
            'task_id': self.task_id,
            'task_type': self.task_type,
            'priority': self.priority,
            'status': self.status,
            'status_code': self.status_code,
            'error': self.error,
            'created_at': round(self.created_at, 3),
            'started_at': round(self.started_at, 3) if self.started_at else None,
            'finished_at': round(self.finished_at, 3) if self.finished_at else None,
            'queue_seconds': round(self.queue_seconds, 2),
            'run_seconds': round(self.run_seconds, 2),
            'resource_weight': self.resource_weight,
            'estimated_duration': self.estimated_duration,
        }


class Scheduler:
    """异步优先级队列调度器。"""

    def __init__(
        self,
        max_concurrent: int = 3,
        queue_timeout: float = 30.0,
        infer_timeout: float = 120.0,
        gpu_threshold: float = 0.8,
        gpu_load_provider: Optional[Callable[[], float]] = None,
        gpu_scale_min_concurrent: int = 1,
    ):
        self.max_concurrent = max_concurrent
        self.queue_timeout = queue_timeout
        self.infer_timeout = infer_timeout
        self.gpu_threshold = gpu_threshold
        self.gpu_load_provider = gpu_load_provider or (lambda: 0.0)
        self.gpu_scale_min_concurrent = max(1, gpu_scale_min_concurrent)

        self._heap: List[Task] = []
        self.tasks: Dict[str, Task] = {}
        self.running_tasks = 0
        self.total_queued = 0
        self.total_completed = 0

        self._wakeup = asyncio.Event()
        self._worker: Optional[asyncio.Task] = None
        self._stopping = False
        self._listeners: List[Callable[[Task], Any]] = []

    # ---------- 生命周期 ----------

    def start(self):
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._worker_loop())

    async def stop(self):
        self._stopping = True
        self._wakeup.set()
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass

    # ---------- 事件 ----------

    def on_task_update(self, fn: Callable[[Task], Any]):
        """注册任务状态变更监听（用于持久化 / WebSocket 广播）。fn 可为同步或协程。"""
        self._listeners.append(fn)

    def _emit_async(self, task: Task):
        try:
            asyncio.create_task(self._emit(task))
        except RuntimeError:
            pass  # 事件循环未运行（如 CLI 只读命令）

    async def _emit(self, task: Task):
        for fn in list(self._listeners):
            try:
                r = fn(task)
                if inspect.isawaitable(r):
                    await r
            except Exception:
                logger.exception('task listener error for %s', task.task_id)

    # ---------- 入队 / 提交 ----------

    def add_task(self, task: Task) -> Task:
        if task.task_id in self.tasks:
            raise ValueError(f'task id duplicated: {task.task_id}')
        loop = asyncio.get_event_loop()
        task.future = loop.create_future()
        heapq.heappush(self._heap, task)
        self.tasks[task.task_id] = task
        self.total_queued += 1
        self._wakeup.set()
        self._emit_async(task)
        self.start()
        return task

    async def submit(
        self,
        task: Task,
        queue_wait: Optional[float] = None,
        infer_timeout: Optional[float] = None,
    ) -> Tuple[Any, int, str]:
        """提交并等待完成。

        返回 (result, status_code, error_message)：
        - 排队等待超过 queue_wait       -> (None, 429, 'queue wait timeout')
        - 推理超过 infer_timeout        -> (None, 504, 'inference timeout')
        - 成功                          -> (result, task.status_code, '')
        """
        if task.future is None:
            task.future = asyncio.get_event_loop().create_future()
        qw = self.queue_timeout if queue_wait is None else queue_wait
        it = self.infer_timeout if infer_timeout is None else infer_timeout

        # 阶段一：排队（等待 worker 取出开始执行），超时 -> 429
        try:
            await asyncio.wait_for(asyncio.shield(task.started_event.wait()), timeout=qw)
        except asyncio.TimeoutError:
            if task.status == 'queued':
                task.status = 'cancelled'
                self._emit_async(task)
            return task.result, 429, 'queue wait timeout, try later'

        # 阶段二：执行（等待 handler 完成），超时 -> 504
        try:
            await asyncio.wait_for(asyncio.shield(task.future), timeout=it)
        except asyncio.TimeoutError:
            if task.status in ('running', 'queued'):
                task.status = 'timeout'
                task.status_code = 504
                task.error = 'inference timeout'
                if not task.future.done():
                    task.future.set_result(504)
                self._emit_async(task)
            return task.result, 504, 'inference timeout'

        return task.result, task.status_code, task.error

    # ---------- 插队 / 取消 ----------

    def promote(self, task_id: str, priority: int) -> bool:
        """插队：把任务优先级提到 priority（数值越小越优先）。"""
        task = self.tasks.get(task_id)
        if task and task.status == 'queued':
            task.priority = priority
            # 惰性删除：旧堆条目保持原优先级，新条目优先弹出；
            # 已运行/已完成的旧条目在 worker 弹出时被跳过。
            heapq.heappush(self._heap, task)
            self._wakeup.set()
            self._emit_async(task)
            return True
        return False

    def cancel(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if task and task.status == 'queued':
            task.status = 'cancelled'
            self._emit_async(task)
            return True
        return False

    # ---------- 调度核心 ----------

    def _effective_concurrent(self) -> int:
        """GPU 占用超过阈值时降并发，避免资源争抢。"""
        gpu = 0.0
        try:
            gpu = float(self.gpu_load_provider() or 0.0)
        except Exception:
            gpu = 0.0
        if gpu > self.gpu_threshold:
            return max(self.gpu_scale_min_concurrent, int(self.max_concurrent * 0.5))
        return self.max_concurrent

    async def _worker_loop(self):
        while not self._stopping:
            eff = self._effective_concurrent()
            if self.running_tasks >= eff:
                await asyncio.sleep(0.2)
                continue
            if not self._heap:
                self._wakeup.clear()
                try:
                    # 带超时兜底：避免 add_task 的 set() 与 clear() 竞态导致永久等待
                    await asyncio.wait_for(self._wakeup.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                continue
            task = heapq.heappop(self._heap)
            # 惰性删除的脏条目（已被插队复制 / 已取消 / 已运行）
            if task.status != 'queued':
                continue
            self.running_tasks += 1
            task.status = 'running'
            task.started_at = time.time()
            task.started_event.set()
            self._emit_async(task)
            asyncio.create_task(self._run_task(task))

    async def _run_task(self, task: Task):
        try:
            if task.handler is None:
                raise RuntimeError('task has no handler')
            result = await asyncio.wait_for(
                asyncio.shield(task.handler(task)), timeout=self.infer_timeout
            )
            task.result = result
            if task.status_code == 200:
                task.status = 'done'
            else:
                task.status = 'failed'  # handler 显式设置了非 200
        except asyncio.TimeoutError:
            task.status = 'timeout'
            task.status_code = 504
            task.error = 'inference timeout'
        except BackendError as e:
            task.status = 'failed'
            task.status_code = e.status_code
            task.error = str(e)
            logger.error('task %s backend error %s: %s', task.task_id, e.status_code, e)
        except asyncio.CancelledError:
            task.status = 'cancelled'
            task.status_code = 500
            task.error = 'cancelled by scheduler'
        except Exception as e:  # noqa: BLE001
            task.status = 'failed'
            task.status_code = 500
            task.error = str(e)
            logger.exception('task %s failed: %s', task.task_id, e)
        finally:
            task.finished_at = time.time()
            self.running_tasks = max(0, self.running_tasks - 1)
            self.total_completed += 1
            if task.future is not None and not task.future.done():
                task.future.set_result(task.status_code)
            self._emit_async(task)
            self._wakeup.set()

    # ---------- 查询 ----------

    def queued_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t.status == 'queued')

    def get_status(self, limit: int = 100) -> Dict:
        eff = self._effective_concurrent()
        recent = sorted(
            (t.to_dict() for t in self.tasks.values()),
            key=lambda d: d['created_at'], reverse=True,
        )[:limit]
        return {
            'queue_length': self.queued_count(),
            'running': self.running_tasks,
            'max_concurrent': self.max_concurrent,
            'effective_concurrent': eff,
            'gpu_threshold': self.gpu_threshold,
            'gpu_load': round(float(self.gpu_load_provider() or 0.0), 3),
            'total_queued': self.total_queued,
            'total_completed': self.total_completed,
            'tasks': recent,
        }


class BackendError(Exception):
    """上游后端返回的非 200 状态码（映射为 task.status_code）。"""

    def __init__(self, status_code: int, message: str = ''):
        super().__init__(message or f'backend error {status_code}')
        self.status_code = status_code
