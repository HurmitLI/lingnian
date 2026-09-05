# 5080 连续影片第二次试验

## 执行范围

用户同意修正第一段的人物年龄与冷暖跳变。仅生成虚构本机样片，不使用收费云平台，不上传真实资料，不切换生产入口。新图使用 Codex 内置生图编辑；视频仍由家用 RTX 5080 的现有官方 Wan 2.2 TI2V 5B 运行，不需重新下载模型。

## Windows 操作

1. 安全更新当前分支到包含本文件的最新提交。保留本地安装器、自定义节点和未跟踪文件。远端之前的两个 UTF-8 改动可能与新提交重叠：先比较差异，如需暂存，只暂存这两个文件并保留 stash；更新后检查是否已等价合入，不要 reset/clean 或丢弃无关修改。
2. 运行 worker 测试，确认新版 `continuity.py` 已通过当前 Python 加载。此次增加了精简整片英文描述，不走旧关键词映射。原中文故事与动作仍在 JSON 中供核对。
3. 记录并暂停唯一旧 worker（如果会竞争 GPU），不要增加第二个后台 worker；试验完恢复原服务。
4. 使用原来的 Python、ffmpeg、ffprobe，调用 `python -m lingnian_worker.continuity`，替换为：

   - `--plan generation-worker/examples/1982-departure-young-v2.json`
   - `--reference generation-worker/assets/shen-suqin-station-1982-young-v2.png`
   - `--segments 1`

   其余本机端点及输出目录保持原设置。首段仍为 5 秒、24 fps、121 原始帧，20 步、CFG 5；本次不降低分辨率、不循环、不变速。
5. 旧目录和旧成片全部保留；新参考和新方案会生成不同的摘要目录。只做第一段，不生成第二、三段，不自动批准。
6. 回报实际时间、显存、成片路径、模型、测试及是否恢复原 worker；保存并检查 review 图。优先直接用媒体查看能力展示图片和 MP4；不要为了展示而新增公网入口或关闭浏览器本地文件安全限制。

## 本轮视觉判据

- 人物必须仍像新参考图中的年轻成年人，不能逐渐变老、换脸或美颜变脸。
- 0 秒到 1.25 秒不能明显由冷灰转成金黄、棕褐，也不能突然提高对比。
- 蓝包的灰布带、轮廓、衣服、车厢保持一致；背景有自然运动，不突然换场。
- 轻微转头与握包可以发生，但不能无故说话、咀嚼或张嘴。
- 逐帧拼图只辅助检查，完整播放之后才判断运动是否自然。
- 失败就保留失败证据，不扩展为三段。此次尚不包含旁白、字幕或产品上线。

## 素材与提示词出处

新版参考 SHA256：`ecd82d4dff016f02eea7e4cd33c91fb63e99b68df8e74dd04099b13c6d8a7056`。

原图为项目已有虚构样片，不是真实家庭照片。主端已检查：新版年龄观感更接近年轻成人，包、服装和站台基本保留；具体 19 岁无法只凭图像验证，仍需作为虚构演绎看待。

内置图像生成编辑的完整提示词：

> Edit target: the attached entirely fictional, previously AI-generated station image. Use case: identity-preserve age adjustment, fictional historical film reference. Change ONLY the central fictional woman's apparent age to a believable 19-year-old young adult version of the same character, with naturally youthful facial structure, youthful neck and hands, recognizable eye shape and nose, natural unretouched skin texture, no makeup, no glamour beauty filter. Keep her mouth gently closed and calm. This is an adult aged nineteen, not a child. Preserve the exact body pose, two black braids, pale tiny-pattern long-sleeve shirt, navy trousers, exact worn blue canvas bag silhouette and gray fabric straps, hands holding it, green passenger railway carriage, wet platform, background people, camera angle and composition. Preserve original neutral cool overcast daylight, exposure and muted colors. No sepia, no golden grading, no new props, no embroidery, no leather handles, no added text or watermark. Photorealistic ordinary young worker in China in spring 1982, no contemporary fashion. Single landscape image, not a collage.

视频完整输入来自 JSON 的 `render_bible` 和每段 `render_action`，以及脚本的固定连续性/曝光约束；保留源文与逐段引用，不用渲染过程凭空增加家庭事实。350 英文词以下只是长度预检，不代表已实测编码器 token 数。
