"""Blender 3D 片头（探测式）：本机装了 Blender 才可用，缺依赖自动降级。

探测顺序：环境变量 CREATORHUB_BLENDER → G:/Blender/*/blender.exe（便携版解压即用）→
G:/tools/blender*/blender.exe → agent-skills/mcp-for-blender 下的 blender.exe；
全部找不到时 blender_exe() 返回 None，render_intro 报 RuntimeError（文案提示装好后
无需改代码自动可用）。

有 Blender 时：生成 python 脚本（text object = 标题、相机 360° 环绕 keyframes、日光、
EEVEE 渲染 PNG 序列到临时目录）→ blender -b -P script -- args → ffmpeg 组帧 →
storage/fusion/intros/<stamp>-<slug>.mp4。生成的 blender 脚本会用 py_compile 验证
可编译后再交给 Blender。
"""
from __future__ import annotations

import glob
import math
import os
import py_compile
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from .paths import FUSION_DIR, ensure_dir, ffmpeg_exe, run, slugify

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
FPSES = (24, 30)
NO_BLENDER_MSG = ("本机未装 Blender：装好后自动可用（便携版解压到 G:/Blender 即可），"
                  "无需改代码")


def blender_exe() -> str | None:
    """探测本机 blender.exe；找不到返回 None（不影响其他功能）。"""
    env = os.environ.get("CREATORHUB_BLENDER", "")
    if env and Path(env).is_file():
        return env
    patterns = (
        r"G:/Blender/*/blender.exe",
        r"G:/tools/blender*/blender.exe",
        r"G:/VerifiedRepos/agent-skills/mcp-for-blender/**/blender.exe",
    )
    for pattern in patterns:
        for hit in sorted(glob.glob(pattern, recursive=True)):
            if Path(hit).is_file():
                return hit
    return None


def build_blender_script(title: str, width: int, height: int, fps: int, seconds: float) -> str:
    """生成 blender 后台渲染脚本源码（独立暴露，便于 py_compile 验证）。

    脚本职责：清空场景 → 文字对象（标题，挤出立体感）→ 日光 → 相机 360° 环绕
    keyframes → EEVEE 渲染 PNG 序列（f_0001.png …）到 -- 后第一个参数目录。
    """
    frames = max(1, int(round(float(seconds) * int(fps))))
    return f'''# CreatorHub 3D 片头渲染脚本（blender -b -P intro.py -- out_dir title w h fps frames）
import math
import os
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out_dir = argv[0]
title = {title!r}
width, height, fps, frames = {width}, {height}, {fps}, {frames}

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.resolution_x = width
scene.render.resolution_y = height
scene.render.fps = fps
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = os.path.join(out_dir, "f_")
try:
    scene.render.engine = "BLENDER_EEVEE_NEXT"  # Blender 4.2+
except TypeError:
    scene.render.engine = "BLENDER_EEVEE"
scene.frame_start = 1
scene.frame_end = frames

# 标题文字：挤出立体感，居中
txt = bpy.data.curves.new("title", type="FONT")
txt.body = title
txt.align_x = "CENTER"
txt.align_y = "CENTER"
txt.size = 1.0
txt.extrude = 0.12
obj = bpy.data.objects.new("title", txt)
scene.collection.objects.link(obj)
mat = bpy.data.materials.new("title-mat")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    bsdf.inputs["Base Color"].default_value = (0.94, 0.96, 1.0, 1.0)
obj.data.materials.append(mat)

# 日光
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", type="SUN"))
sun.data.energy = 3.0
sun.rotation_euler = (math.radians(50), 0.0, math.radians(30))
scene.collection.objects.link(sun)

# 相机：360° 环绕 keyframes（第 1 帧与最后一帧），Track-To 锁定标题
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
radius = 6.0
for frame, angle in ((1, 0.0), (frames, math.tau)):
    cam.location = (math.sin(angle) * radius, -math.cos(angle) * radius, 0.8)
    cam.keyframe_insert("location", frame=frame)
track = cam.constraints.new(type="TRACK_TO")
track.target = obj
track.track_axis = "TRACK_NEGATIVE_Z"
track.up_axis = "UP_Y"

# 逐帧渲染 PNG 序列
for frame in range(1, frames + 1):
    scene.frame_set(frame)
    bpy.ops.render.render(write_still=True)
'''


def render_intro(title: str, seconds: float = 2.0, aspect: str = "9:16",
                 fps: int = 24) -> dict:
    """渲染 Blender 3D 片头 MP4；无 Blender 报 RuntimeError（探测式降级）。"""
    title = (title or "").strip()
    if not 1 <= len(title) <= 40:
        raise ValueError("title 需 1-40 字")
    if aspect not in ASPECTS:
        raise ValueError(f"aspect 只支持 {', '.join(ASPECTS)}")
    fps = int(fps)
    if fps not in FPSES:
        raise ValueError(f"fps 只支持 {', '.join(map(str, FPSES))}")
    seconds = max(1.0, min(float(seconds), 5.0))
    exe = blender_exe()
    if not exe:
        raise RuntimeError(NO_BLENDER_MSG)

    width, height = ASPECTS[aspect]
    frames = max(1, int(round(seconds * fps)))
    out_dir = ensure_dir(FUSION_DIR / "intros")
    base = f"{datetime.utcnow():%Y%m%d-%H%M%S}-{slugify(title)}"
    out_path = out_dir / f"{base}.mp4"
    work = Path(tempfile.mkdtemp(prefix="fusion-intro-"))
    script_path = work / "intro.py"
    script_path.write_text(build_blender_script(title, width, height, fps, seconds),
                           encoding="utf-8")
    py_compile.compile(str(script_path), doraise=True)  # 生成脚本先验证可编译
    try:
        result = run([exe, "-b", "-P", str(script_path), "--", str(work),
                      title, str(width), str(height), str(fps), str(frames)],
                     timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"Blender 渲染失败：{result.stderr[-400:]}")
        rendered = sorted(work.glob("f_*.png"))
        if not rendered:
            raise RuntimeError("Blender 没有产出帧；检查 Blender 版本（需支持 EEVEE）")
        encode = run([ffmpeg_exe(), "-y", "-loglevel", "error",
                      "-framerate", str(fps), "-i", str(work / "f_%04d.png"),
                      "-c:v", "libx264", "-pix_fmt", "yuv420p",
                      "-movflags", "+faststart", str(out_path)], timeout=300)
        if encode.returncode != 0 or not out_path.is_file():
            raise RuntimeError(f"编码失败：{encode.stderr[-300:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"path": str(out_path), "frames": frames, "fps": fps, "duration": seconds,
            "aspect": aspect, "width": width, "height": height,
            "bytes": out_path.stat().st_size,
            "note": "Blender EEVEE 3D 片头：文字 360° 环绕；无声，与口播在剪辑轨对齐。"}
