"""SQLite 持久化：任务完整生命周期记录 + 统计 + CSV/JSON 导出。

对应 docs/middle-station-plan.md 第 5.3 节。
同步 sqlite3 通过 asyncio.to_thread 避免阻塞事件循环。
"""
import asyncio
import csv
import json
import logging
import os
import sqlite3
import time
from typing import Dict, List, Optional

logger = logging.getLogger('middle_station.storage')


class Storage:
    def __init__(self, db_path: str = './middle-station.db'):
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
        # 串行化写入：任务状态多次 record（queued->running->done）必须按序落库，
        # 否则 to_thread 并发写入可能让旧状态覆盖新状态。
        self._lock = asyncio.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    task_type TEXT,
                    priority INTEGER,
                    status TEXT,
                    status_code INTEGER,
                    error TEXT,
                    created_at REAL,
                    started_at REAL,
                    finished_at REAL,
                    queue_seconds REAL,
                    run_seconds REAL,
                    resource_weight REAL,
                    estimated_duration REAL
                )"""
            )
            # 请求参数存档（一般不看，仅作记录；含 tts_text / prompt_wav_path / 工作流等）
            c.execute(
                """CREATE TABLE IF NOT EXISTS task_payloads (
                    task_id TEXT PRIMARY KEY,
                    task_type TEXT,
                    payload TEXT,
                    created_at REAL
                )"""
            )
            c.execute('CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_payloads_created ON task_payloads(created_at)')
            # 兼容旧库：补充 text_len 列（语音任务文本字数）
            cols = [r[1] for r in c.execute('PRAGMA table_info(tasks)')]
            if 'text_len' not in cols:
                c.execute('ALTER TABLE tasks ADD COLUMN text_len INTEGER DEFAULT 0')

    # ---------- 启动对账 ----------

    def reconcile_stale(self) -> int:
        """把上一次进程遗留的未终结任务标记为中断（启动时调用一次）。

        中转站的任务状态只存在于内存：进程被杀/重启后，DB 里上次留下的
        running/waiting/queued 行永远不会再被更新，任务页会一直显示「运行中」，
        看起来就像任务跑了几个小时。这里在启动时把它们收敛成终态。
        """
        now = time.time()
        try:
            with self._conn() as c:
                cur = c.execute(
                    """UPDATE tasks
                       SET status='failed', status_code=500, finished_at=?,
                           error='station restarted, task interrupted',
                           run_seconds=CASE WHEN started_at > 0 THEN MAX(0, ? - started_at) ELSE 0 END
                       WHERE status IN ('running','waiting')""",
                    (now, now),
                )
                n_running = cur.rowcount or 0
                cur = c.execute(
                    """UPDATE tasks
                       SET status='cancelled', status_code=499, finished_at=?,
                           error='station restarted, task dropped from queue'
                       WHERE status='queued'""",
                    (now,),
                )
                n_queued = cur.rowcount or 0
        except Exception as e:  # noqa: BLE001
            logger.error('reconcile stale tasks failed: %s', e)
            return 0
        total = n_running + n_queued
        if total:
            logger.warning('reconciled %d stale task(s) left by previous run '
                           '(running/waiting=%d, queued=%d)', total, n_running, n_queued)
        return total

    # ---------- 写入 ----------

    async def record(self, task) -> None:
        async with self._lock:
            await asyncio.to_thread(self._record_sync, task)

    @staticmethod
    def _payload_json(task) -> Optional[str]:
        """把任务请求参数序列化为 JSON 字符串（排除二进制，截断防爆表）。"""
        try:
            p = dict(task.payload or {})
            if isinstance(p.get('prompt_wav'), tuple):   # 参考音频二进制不入库
                p.pop('prompt_wav', None)
            s = json.dumps(p, ensure_ascii=False, default=str)
            return s[:8000]
        except Exception:  # noqa: BLE001
            return None

    def _record_sync(self, task) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    """INSERT OR REPLACE INTO tasks
                       (task_id, task_type, priority, status, status_code, error,
                        created_at, started_at, finished_at, queue_seconds, run_seconds,
                        resource_weight, estimated_duration, text_len)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        task.task_id,
                        task.task_type,
                        task.priority,
                        task.status,
                        task.status_code,
                        (task.error or '')[:500],
                        task.created_at,
                        task.started_at or 0.0,
                        task.finished_at or 0.0,
                        round(task.queue_seconds, 3),
                        round(task.run_seconds, 3),
                        task.resource_weight,
                        task.estimated_duration,
                        getattr(task, 'text_len', 0),
                    ),
                )
                pj = self._payload_json(task)
                if pj is not None:
                    c.execute(
                        """INSERT OR REPLACE INTO task_payloads
                           (task_id, task_type, payload, created_at) VALUES (?,?,?,?)""",
                        (task.task_id, task.task_type, pj, task.created_at),
                    )
        except Exception as e:  # noqa: BLE001
            logger.error('storage record failed: %s', e)

    # ---------- 查询 ----------

    async def stats(self, hours: int = 24) -> Dict:
        return await asyncio.to_thread(self._stats_sync, hours)

    def _stats_sync(self, hours: int = 24) -> Dict:
        cutoff = time.time() - hours * 3600
        try:
            with self._conn() as c:
                total = c.execute(
                    'SELECT COUNT(*) FROM tasks WHERE created_at >= ?', (cutoff,)).fetchone()[0]
                done = c.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status='done' AND created_at >= ?",
                    (cutoff,)).fetchone()[0]
                failed = c.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status IN ('failed','timeout') AND created_at >= ?",
                    (cutoff,)).fetchone()[0]
                by_type = {
                    r[0]: r[1] for r in c.execute(
                        'SELECT task_type, COUNT(*) FROM tasks WHERE created_at >= ? GROUP BY task_type',
                        (cutoff,))
                }
                avg_run = c.execute(
                    "SELECT AVG(run_seconds) FROM tasks WHERE status='done' AND created_at >= ?",
                    (cutoff,)).fetchone()[0] or 0
                return {
                    'window_hours': hours,
                    'total': total,
                    'done': done,
                    'failed': failed,
                    'success_rate': round(done / total * 100, 1) if total else 0.0,
                    'by_type': by_type,
                    'avg_run_seconds': round(avg_run, 2),
                }
        except Exception as e:  # noqa: BLE001
            logger.error('stats failed: %s', e)
            return {}

    async def query_tasks(self, page: int = 1, page_size: int = 20,
                          task_type: Optional[str] = None, status: Optional[str] = None,
                          keyword: Optional[str] = None) -> Dict:
        """分页 + 筛选查询历史任务（全量任务页用）。"""
        return await asyncio.to_thread(
            self._query_sync, page, page_size, task_type, status, keyword)

    def _query_sync(self, page: int, page_size: int,
                    task_type: Optional[str], status: Optional[str],
                    keyword: Optional[str]) -> Dict:
        page = max(1, int(page))
        page_size = min(200, max(1, int(page_size)))
        where: List[str] = []
        args: List = []
        if task_type:
            where.append('task_type = ?')
            args.append(task_type)
        if status:
            where.append('status = ?')
            args.append(status)
        if keyword:
            where.append('(task_id LIKE ? OR error LIKE ? OR task_type LIKE ?)')
            kw = f'%{keyword}%'
            args.extend([kw, kw, kw])
        wsql = (' WHERE ' + ' AND '.join(where)) if where else ''
        try:
            with self._conn() as c:
                total = c.execute(
                    f'SELECT COUNT(*) FROM tasks{wsql}', args).fetchone()[0]
                rows = c.execute(
                    f'SELECT * FROM tasks{wsql} ORDER BY created_at DESC LIMIT ? OFFSET ?',
                    args + [page_size, (page - 1) * page_size]).fetchall()
                return {
                    'total': total, 'page': page, 'page_size': page_size,
                    'pages': max(1, (total + page_size - 1) // page_size),
                    'tasks': [dict(r) for r in rows],
                }
        except Exception as e:  # noqa: BLE001
            logger.error('query tasks failed: %s', e)
            return {'total': 0, 'page': page, 'page_size': page_size,
                    'pages': 1, 'tasks': []}

    async def stats_detail(self, hours: int = 24) -> Dict:
        """统计详情：汇总 + 类型/状态分布 + 时间序列（统计页图表用）。"""
        return await asyncio.to_thread(self._stats_detail_sync, hours)

    def _stats_detail_sync(self, hours: int = 24) -> Dict:
        hours = max(1, min(24 * 90, int(hours)))
        cutoff = time.time() - hours * 3600
        bucket_s = 3600 if hours <= 48 else 86400   # 短窗口按小时桶，长窗口按天桶
        try:
            with self._conn() as c:
                total = c.execute(
                    'SELECT COUNT(*) FROM tasks WHERE created_at >= ?', (cutoff,)).fetchone()[0]
                done = c.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status='done' AND created_at >= ?",
                    (cutoff,)).fetchone()[0]
                failed = c.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status IN ('failed','timeout') AND created_at >= ?",
                    (cutoff,)).fetchone()[0]
                avg_run = c.execute(
                    "SELECT AVG(run_seconds) FROM tasks WHERE status='done' AND created_at >= ?",
                    (cutoff,)).fetchone()[0] or 0
                by_type = {
                    r[0]: r[1] for r in c.execute(
                        'SELECT task_type, COUNT(*) FROM tasks WHERE created_at >= ? GROUP BY task_type',
                        (cutoff,))
                }
                by_status = {
                    r[0]: r[1] for r in c.execute(
                        'SELECT status, COUNT(*) FROM tasks WHERE created_at >= ? GROUP BY status',
                        (cutoff,))
                }
                series = [{
                    'ts': r[0],
                    'total': r[1],
                    'done': r[2],
                    'failed': r[3],
                    'avg_run': round(r[4] or 0, 2),
                } for r in c.execute(
                    f"""SELECT CAST(created_at / {bucket_s} AS INTEGER) * {bucket_s} AS ts,
                               COUNT(*) AS total,
                               SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                               SUM(CASE WHEN status IN ('failed','timeout') THEN 1 ELSE 0 END) AS failed,
                               AVG(CASE WHEN status='done' THEN run_seconds END) AS avg_run
                        FROM tasks WHERE created_at >= ? GROUP BY ts ORDER BY ts""",
                    (cutoff,))
                ]
                return {
                    'hours': hours,
                    'bucket': 'hour' if bucket_s == 3600 else 'day',
                    'summary': {
                        'total': total, 'done': done, 'failed': failed,
                        'success_rate': round(done / total * 100, 1) if total else 0.0,
                        'avg_run_seconds': round(avg_run, 2),
                    },
                    'by_type': by_type,
                    'by_status': by_status,
                    'series': series,
                }
        except Exception as e:  # noqa: BLE001
            logger.error('stats_detail failed: %s', e)
            return {'hours': hours, 'bucket': 'hour', 'summary': {},
                    'by_type': {}, 'by_status': {}, 'series': []}

    async def list_tasks(self, limit: int = 200) -> List[Dict]:
        return await asyncio.to_thread(self._list_sync, limit)

    def _list_sync(self, limit: int = 200) -> List[Dict]:
        with self._conn() as c:
            rows = c.execute(
                'SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
            return [dict(r) for r in rows]

    async def export(self, fmt: str = 'csv', out: Optional[str] = None) -> str:
        return await asyncio.to_thread(self._export_sync, fmt, out)

    def _export_sync(self, fmt: str, out: Optional[str]) -> str:
        rows = self._list_sync(100000)
        if fmt == 'csv':
            path = out or 'tasks_export.csv'
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                if rows:
                    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    w.writerows(rows)
        else:
            path = out or 'tasks_export.json'
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
        logger.info('exported %d tasks to %s', len(rows), path)
        return path
