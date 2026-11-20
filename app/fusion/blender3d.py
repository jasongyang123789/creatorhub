"""Render a local title scene with Blender and encode its numbered frames."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from .paths import FUSION_DIR, ensure_dir, ffmpeg_exe, font_path, slugify

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
FPSES = (24, 30)
RENDER_TIMEOUT = 600
ENCODE_TIMEOUT = 120
NO_BLENDER_MSG = "本机未找到 Blender；便携版放在 G:/Blender 或设置 CREATORHUB_BLENDER 后即可用（无需改代码）。"


def blender_exe() -> str | None:
    """Find the configured or installed portable executable."""
    configured = os.environ.get("CREATORHUB_BLENDER", "")
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    for root, pattern in (
        (Path("G:/Blender"), "*/blender.exe"),
        (Path("G:/tools"), "blender*/blender.exe"),
        (Path("G:/VerifiedRepos/agent-skills/.cache/blender"), "*/blender.exe"),
    ):
        for candidate in sorted(root.glob(pattern), reverse=True):
            if candidate.is_file():
                return str(candidate)
    return None


def build_blender_script(title: str, width: int, height: int, fps: int, seconds: float) -> str:
    frames = max(1, round(seconds * fps))
    return f'''import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

out, font_arg = sys.argv[sys.argv.index("--") + 1:]
out = Path(out)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 2
scene.cycles.use_denoising = False
scene.render.threads_mode = "FIXED"
scene.render.threads = 6
scene.render.resolution_x = {width}
scene.render.resolution_y = {height}
scene.render.resolution_percentage = 100
scene.render.fps = {fps}
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.image_settings.compression = 15
scene.view_settings.view_transform = "Standard"
scene.world = bpy.data.worlds.new("Ink")
scene.world.color = (0.004, 0.009, 0.022)


def object_at(name, data, xyz):
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = xyz
    return obj


def point_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def material(name, color, emission=0.0, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    surface = mat.node_tree.nodes.get("Principled BSDF")
    surface.inputs["Base Color"].default_value = (*color, 1)
    surface.inputs["Metallic"].default_value = metallic
    surface.inputs["Roughness"].default_value = 0.32
    surface.inputs["Emission Color"].default_value = (*color, 1)
    surface.inputs["Emission Strength"].default_value = emission
    return mat


def text(name, body, y, size, limit, mat, font=None):
    curve = bpy.data.curves.new(name, "FONT")
    curve.body = body
    if font:
        curve.font = font
    curve.align_x = "CENTER"
    curve.align_y = "CENTER"
    curve.size = size
    curve.extrude = 0.065 if name == "Title" else 0.0
    curve.bevel_depth = 0.014 if name == "Title" else 0.0
    obj = object_at(name, curve, (0, y, 0.24))
    curve.materials.append(mat)
    bpy.context.view_layer.update()
    if obj.dimensions.x > limit:
        obj.scale *= limit / obj.dimensions.x
    return obj


main_color = material("Porcelain", (0.71, 0.91, 1.0), 0.42, 0.28)
accent = material("Glacial Cyan", (0.053, 0.58, 0.98), 1.2, 0.10)
wire = material("Blueprint", (0.035, 0.16, 0.34), 0.44)
heading = {title!r}
font = bpy.data.fonts.load(font_arg) if font_arg else None
text("Title", heading, 0.05, 0.87, 3.75, main_color, font)
text("Eyebrow", "CREATORHUB   /   STUDIO", 0.95, 0.17, 3.4, accent)
text("Footer", "M O T I O N   S T U D I O", -0.94, 0.15, 3.4, accent)


def line(name, coords, thickness, mat):
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.bevel_depth = thickness
    spline = data.splines.new("POLY")
    spline.points.add(len(coords) - 1)
    for point, (x, y, z) in zip(spline.points, coords):
        point.co = (x, y, z, 1)
    object_at(name, data, (0, 0, 0))
    data.materials.append(mat)


for i in range(-10, 11):
    q = i * 0.45
    line("Grid", [(q, -5, -0.45), (q, 5, -0.45)], 0.0025, wire)
    line("Grid", [(-5, q, -0.45), (5, q, -0.45)], 0.0025, wire)
for sign in (-1, 1):
    x = sign * 2.15
    line("Left or right bracket", [(x - sign * 0.25, -0.54, 0.05), (x, -0.54, 0.05),
                                (x, 0.54, 0.05), (x - sign * 0.25, 0.54, 0.05)], 0.014, accent)
for index in range(32):
    a = math.tau * index / 32
    line("Orbit mark", [(2.7 * math.cos(a), 2.7 * math.sin(a), -0.18),
                         (2.78 * math.cos(a), 2.78 * math.sin(a), -0.18)], 0.008, wire)

key = object_at("Key", bpy.data.lights.new("Key", "AREA"), (-3, 2, 5))
key.data.energy = 520
key.data.size = 4
point_at(key, (0, 0, 0))
cam = object_at("Camera", bpy.data.cameras.new("Camera"), (0, 0, 8))
cam.data.type = "ORTHO"
cam.data.ortho_scale = 8.65 if {height} > {width} else 7.3
scene.camera = cam
scene.frame_start = 1
scene.frame_end = {frames}

for frame in range(1, {frames} + 1):
    phase = math.tau * (frame - 1) / {frames}
    cam.location = (math.sin(phase) * 1.45, math.cos(phase) * 0.93, 8.2)
    if frame <= {frames} * 0.4:
        cam.data.ortho_scale = (8.65 if {height} > {width} else 7.3) * (1.075 - 0.075 * frame / ({frames} * 0.4))
    point_at(cam, (0, 0, 0))
    scene.frame_set(frame)
    scene.render.filepath = str(out / f"f_{{frame:04d}}.png")
    bpy.ops.render.render(write_still=True)
'''


def _checked_run(args: list[str], *, timeout: int, env: dict[str, str], cwd: Path, label: str):
    try:
        result = subprocess.run(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{label}超过 {timeout} 秒，已停止") from exc
    except OSError as exc:
        raise RuntimeError(f"无法启动{label}") from exc
    if result.returncode:
        detail = "\n".join(part for part in (result.stderr[-400:], result.stdout[-400:]) if part)
        raise RuntimeError(f"{label}失败（退出码 {result.returncode}）：{detail.strip()}")


def render_intro(title: str, seconds: float = 2.0, aspect: str = "9:16", fps: int = 24) -> dict:
    title = title.strip()
    if not 1 <= len(title) <= 40 or any(ord(char) < 32 for char in title):
        raise ValueError("标题需为 1–40 个字的单行文字")
    if aspect not in ASPECTS:
        raise ValueError("画幅须为 9:16 或 16:9")
    if fps not in FPSES or not 1 <= seconds <= 5:
        raise ValueError("帧率须为 24 或 30，时长须为 1–5 秒")
    exe = blender_exe()
    if not exe:
        raise RuntimeError(NO_BLENDER_MSG)
    font = font_path() if not title.isascii() else None
    if not title.isascii() and not font:
        raise RuntimeError("找不到中文字体，请配置 CREATORHUB_FONT 后重试")
    width, height = ASPECTS[aspect]
    frames = round(seconds * fps)
    out_dir = ensure_dir(FUSION_DIR / "intros")
    out_path = out_dir / f"{slugify(title)}-{uuid4().hex[:12]}.mp4"
    work_root = ensure_dir(FUSION_DIR / ".work" / "intro3d")
    with tempfile.TemporaryDirectory(dir=work_root) as folder:
        work = Path(folder)
        temp = ensure_dir(work / "tmp")
        env = {**os.environ, "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp),
               "BLENDER_USER_RESOURCES": str(ensure_dir(work / "user")),
               "BLENDER_USER_CONFIG": str(ensure_dir(work / "config")),
               "BLENDER_USER_DATAFILES": str(ensure_dir(work / "datafiles")),
               "BLENDER_USER_SCRIPTS": str(ensure_dir(work / "user-scripts"))}
        script = work / "intro.py"
        source = build_blender_script(title, width, height, fps, seconds)
        compile(source, str(script), "exec")
        script.write_text(source, encoding="utf-8")
        frames_dir = ensure_dir(work / "frames")
        _checked_run([exe, "-b", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                      "--python", str(script), "--", str(frames_dir), font or ""],
                     timeout=RENDER_TIMEOUT, env=env, cwd=work, label="Blender 渲染")
        expected = {f"f_{frame:04d}.png" for frame in range(1, frames + 1)}
        rendered = {path.name for path in frames_dir.glob("f_*.png") if path.stat().st_size > 0}
        if rendered != expected:
            raise RuntimeError(f"Blender 帧不完整，预期 {frames} 帧，收到 {len(rendered)} 帧")
        tmp_video = work / "intro.mp4"
        _checked_run([ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-framerate", str(fps),
                      "-i", str(frames_dir / "f_%04d.png"), "-frames:v", str(frames),
                      "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                      "-movflags", "+faststart", str(tmp_video)],
                     timeout=ENCODE_TIMEOUT, env=env, cwd=work, label="FFmpeg 编码")
        if not tmp_video.is_file() or not tmp_video.stat().st_size:
            raise RuntimeError("FFmpeg 未产出视频")
        tmp_video.replace(out_path)
    return {"path": str(out_path), "frames": frames, "fps": fps, "duration": frames / fps,
            "aspect": aspect, "width": width, "height": height, "bytes": out_path.stat().st_size,
            "note": "Blender 本地渲染 · 无声 3D 片头"}
