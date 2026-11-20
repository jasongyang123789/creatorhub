"""有声片头 v2：页面、HTTP 和 Agent 复用的隔离渲染入口。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from uuid import uuid4

from fastapi import HTTPException

from .paths import FUSION_DIR, ROOT, ffmpeg_exe, font_path

SCRIPT = ROOT / 'scripts' / 'video-rework-v2' / 'generate_intro.py'
ASSETS = Path(__file__).resolve().parent / 'intro_v2_assets'
TIMEOUT = 240
VIDEO_NAME = 'CreatorHub-片头-v2-有声.mp4'
_render_lock = threading.Lock()


def _texts(**values: str) -> dict[str, str]:
    limits = {'title': 16, 'hook': 12, 'message': 12, 'tagline': 24, 'brand': 24}
    result = {}
    for key, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f'{key} 需为文字')
        if any(not ch.isprintable() for ch in value):
            raise ValueError(f'{key} 不支持换行或控制字符')
        value = value.strip()
        if not value or len(value) > limits[key]:
            raise ValueError(f'{key} 需为 1–{limits[key]} 个可显示的单行字符')
        result[key] = value
    return result


def render_intro_v2(title: str = '创作工作台', hook: str = '把想法',
                    message: str = '做成内容', tagline: str = '让每个想法落地',
                    brand: str = 'CREATORHUB', request_key: str = '') -> dict:
    """生成六秒竖屏视频。请求回执落盘；同键同文案复用，同键异文案拒绝。"""
    text = _texts(title=title, hook=hook, message=message, tagline=tagline, brand=brand)
    if not isinstance(request_key, str) or len(request_key) > 200:
        raise ValueError('请求编号最多 200 个字符')
    digest = hashlib.sha256(json.dumps(text, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    root = (FUSION_DIR / 'intro-v2').resolve()
    cache = (FUSION_DIR / '.work' / 'intro-v2').resolve()
    token = hashlib.sha256(request_key.encode()).hexdigest() if request_key else uuid4().hex
    folder = root / token
    receipt = folder / 'receipt.json'
    # One renderer per process: bounds memory and serializes duplicate submissions.
    with _render_lock:
        if receipt.is_file():
            try:
                stored = json.loads(receipt.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise RuntimeError('片头回执损坏，请使用新请求编号重试') from exc
            if stored['digest'] != digest:
                raise HTTPException(409, '该请求编号已用于不同文案，请创建新请求')
            result = stored['result']
            target = Path(result['path']).resolve()
            if target.parent != folder or not target.is_file():
                raise HTTPException(410, '原片头产物已移除，请创建新请求')
            return {**result, 'replayed': True}
        if not SCRIPT.is_file() or any(not (ASSETS / name).is_file()
                                       for name in ('creation.png', 'packaging.png', 'crm.png')):
            raise RuntimeError('有声片头生成器或内置素材缺失')
        font = os.environ.get('CREATORHUB_FONT', '')
        if not font:
            bold = Path('C:/Windows/Fonts/msyhbd.ttc')
            font = str(bold) if bold.is_file() else font_path()
        if not font or not Path(font).is_file():
            raise RuntimeError('找不到中文字体，请配置 CREATORHUB_FONT')
        folder.mkdir(parents=True, exist_ok=True)
        work = cache / token
        work.mkdir(parents=True, exist_ok=True)
        output = work / 'render'
        # A failed attempt may already contain a partial video. Retain it for
        # diagnosis and render the retry in a new directory.
        if output.exists():
            output = work / ('retry-' + uuid4().hex[:8])
        output.mkdir()
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
               'PYTHONIOENCODING': 'utf-8'}
        for key in ('TEMP', 'TMP', 'TMPDIR', 'XDG_CACHE_HOME', 'IMAGEIO_USERDIR', 'MPLCONFIGDIR'):
            env[key] = str(work)
        args = [sys.executable, '-IB', str(SCRIPT), '--out', str(output),
                '--assets', str(ASSETS), '--font', font, '--ffmpeg', ffmpeg_exe()]
        for key, value in text.items():
            args.append('--' + key + '=' + value)
        with subprocess.Popen(args, cwd=work, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding='utf-8', errors='replace') as process:
            try:
                stdout, stderr = process.communicate(timeout=TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                                   capture_output=True, timeout=15, check=False)
                process.kill()
                process.communicate()
                raise RuntimeError('有声片头渲染超时，已停止生成进程，请稍后重试') from exc
        (work / 'render.log').write_text(stdout + '\n' + stderr, encoding='utf-8')
        if process.returncode:
            raise RuntimeError('有声片头渲染失败，诊断日志已保存到 storage/fusion/.work/intro-v2')
        generated = output / VIDEO_NAME
        report_file = output / 'render-report.json'
        if not generated.is_file() or not report_file.is_file():
            raise RuntimeError('有声片头没有生成完整产物')
        report = json.loads(report_file.read_text(encoding='utf-8'))
        if (report.get('decoded_frames'), report.get('duration_s'), report.get('dimensions')) != (180, 6, [1080, 1920]):
            raise RuntimeError('有声片头的视频规格校验未通过')
        if report.get('text') != text or report.get('audio_samples_per_channel') != 288000:
            raise RuntimeError('有声片头的文案或声音校验未通过')
        destination = folder / VIDEO_NAME
        generated.replace(destination)
        for name in ('01-hook.png', '02-workbench.png', '03-brand.png', 'storyboard-v2.jpg', 'render-report.json'):
            source = output / name
            if source.is_file():
                source.replace(folder / name)
        result = {'path': str(destination), 'width': 1080, 'height': 1920, 'fps': 30,
                  'duration': 6.0, 'frames': 180, 'audio': True, 'style': 'editorial-v2',
                  'bytes': destination.stat().st_size, 'replayed': False}
        temporary_receipt = folder / 'receipt.pending'
        temporary_receipt.write_text(json.dumps({'digest': digest, 'result': result},
                                                ensure_ascii=False, indent=2), encoding='utf-8')
        temporary_receipt.replace(receipt)
        return result
