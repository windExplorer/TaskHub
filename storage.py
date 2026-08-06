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
            c.execute('CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)')

    # ---------- 写入 ----------

    async def record(self, task) -> None:
        await asyncio.to_thread(self._record_sync, task)

    def _record_sync(self, task) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    """INSERT OR REPLACE INTO tasks
                       (task_id, task_type, priority, status, status_code, error,
                        created_at, started_at, finished_at, queue_seconds, run_seconds,
                        resource_weight, estimated_duration)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    ),
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
