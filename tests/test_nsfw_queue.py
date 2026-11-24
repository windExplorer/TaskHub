"""NSFW 入队改造自检：显存准入 / 不阻塞后续任务 / handler 回写 / 超时映射。"""
import asyncio
import pytest

from scheduler import Scheduler, Task


def make_task(task_type='nsfw', handler=None, priority=0, created_at=1.0):
    return Task(
        priority=priority,
        created_at=created_at,
        task_id=f'{task_type}_{created_at}',
        task_type=task_type,
        payload={'body': b'raw', 'ctype': 'multipart/form-data'},
        handler=handler,
    )


def test_task_resource_ready_low_vram():
    s = Scheduler(vram_provider=lambda: (0.5, 8.0), vram_threshold=0.25,
                  vram_min_free_gb=1.0)
    assert s._task_resource_ready(make_task('nsfw')) is False

    s2 = Scheduler(vram_provider=lambda: (4.0, 8.0), vram_threshold=0.25,
                   vram_min_free_gb=1.0)
    assert s2._task_resource_ready(make_task('nsfw')) is True


def test_task_resource_ready_non_nsfw_and_no_vram():
    s = Scheduler(vram_provider=lambda: (0.0, 0.0))
    assert s._task_resource_ready(make_task('tts')) is True
    assert s._task_resource_ready(make_task('nsfw')) is True


@pytest.mark.asyncio
async def test_deferred_nsfw_does_not_block_other_types():
    order = []

    async def nsfw_handler(task):
        order.append('nsfw')
        return {'ok': True}

    async def tts_handler(task):
        order.append('tts')
        return b'pcm'

    free = {'gb': 0.5}
    s = Scheduler(max_concurrent=1, queue_timeout=1.0,
                  vram_provider=lambda: (free['gb'], 8.0),
                  vram_threshold=0.25, vram_min_free_gb=1.0)
    nsfw = make_task('nsfw', nsfw_handler, created_at=1.0)
    tts = make_task('tts', tts_handler, created_at=2.0)
    s.add_task(nsfw)
    s.add_task(tts)
    await asyncio.sleep(0.3)
    assert order == ['tts'], '显存不足的 nsfw 不应阻塞后续 TTS 任务'
    assert nsfw.status == 'queued' and len(s._deferred) == 1

    free['gb'] = 4.0
    await asyncio.sleep(0.5)
    assert 'nsfw' in order
    assert nsfw.status == 'done' and len(s._deferred) == 0
    await s.stop()


@pytest.mark.asyncio
async def test_detect_task_writes_status_and_verdict():
    from adapters.nsfw import NsfwChecker

    class Cfg:
        base_url = 'http://127.0.0.1:8890'
        token = ''
        mode = 'label'
        timeout = 25.0
        wait_seconds = 15.0
        output_dir = ''
        prewarm = False

    checker = NsfwChecker(Cfg())

    async def fake_proxy(body, ctype):
        assert body == b'raw'
        return 422, {'ok': False, 'error': 'bad image'}

    checker.proxy_detect = fake_proxy
    task = make_task('nsfw')
    result = await checker.detect_task(task)
    assert result['ok'] is False and result['available'] is False
    assert task.status_code == 422 and 'bad image' in task.error
    assert task.nsfw['available'] is False
    await checker.close()


@pytest.mark.asyncio
async def test_queue_timeout_maps_429_and_infer_timeout_maps_504():
    async def never_done(task):
        await asyncio.sleep(10)

    s = Scheduler(max_concurrent=1, queue_timeout=0.05, infer_timeout=0.05)
    blocked = make_task('tts', never_done, created_at=1.0)
    queued = make_task('nsfw', never_done, created_at=2.0)
    s.add_task(blocked)
    s.add_task(queued)
    _, code, err = await s.submit(queued, queue_wait=0.05, infer_timeout=0.05)
    assert code == 429 and 'queue wait' in err

    s2 = Scheduler(max_concurrent=1, queue_timeout=1.0, infer_timeout=0.05)
    slow = make_task('nsfw', never_done, created_at=1.0)
    s2.add_task(slow)
    _, code, err = await s2.submit(slow, queue_wait=1.0, infer_timeout=0.05)
    assert code == 504 and 'inference' in err
    await s.stop()
    await s2.stop()
