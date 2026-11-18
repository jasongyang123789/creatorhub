# Remotion / 动效代码规范（内嵌版）

来源：remotion-skills 的 markup 规则，蒸馏为本仓库生成 Remotion/动效代码时必须遵守的清单。
CreatorHub 的 Agent（runtime.py SYSTEM 提示词引用本文件）在生成 Remotion 代码、
评审动效方案、或写 HTML/CSS 动画时按此执行。PIL 逐帧渲染（packaging/explainer）
同样遵守"时长/帧数数学"与"无副作用"两条。

## 1. 时长与帧数数学
- 一律用帧（frame）思考：`seconds * fps = frames`；组件内取 `useVideoConfig()` 的
  `fps / durationInFrames / width / height`，不硬编码 30fps 或 1080 宽。
- 插值用 `interpolate(frame, [a, b], [from, to], {extrapolateLeft/Right: "clamp"})`，
  不手写百分比；边界必须 clamp，防止溢出区间。

## 2. 渲染纯函数
- 渲染路径上无副作用：不 fetch、不写文件、不 `Math.random()`（随机用 seeded 值，
  如 `random(\`seed-${id}\`)`），保证同输入同画面。
- 入场/出场用 `<Sequence from={} durationInFrames={}>` 切分，不嵌套修改全局时间。

## 3. 布局与安全区
- 容器一律 `position:absolute; inset:0` 全幅铺满；竖屏 9:16 时左右留 8% 安全区，
  大字标题不压边。
- 字号随画幅缩放（`width * 比例`），长标题按字数降档 + `text-wrap: balance`。

## 4. 动效节奏
- 单镜头 2-4 秒；标题砸入 0.3-0.5s（scale 1.5→1.0 + 透明度）；信息条 0.3s 滑入。
- 相邻镜头衔接：前镜出 0.15s 内后镜入，不留黑场（转场显式声明才允许）。

## 5. 与本仓库管线的对应
- packaging.py（PIL 逐帧）= 本规范第 1/2/4 条的确定性实现：帧数由
  `int(duration * fps)` 计算、每帧纯函数（进度 → 画面）、hero/kinetic 版式遵守
  安全区与字号降档。
- explainer.py 场景时长 = 音频时长驱动（有 TTS 时），镜头时长数学同第 1 条。
- 生成 Remotion 项目（a2e 管线）时：composition id、useVideoConfig、interpolate clamp、
  Sequence 切分四条是硬约束。
