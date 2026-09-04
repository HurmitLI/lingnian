# ComfyUI 纪实空镜工作流

这里需要放从 ComfyUI 选择“保存（API 格式）”导出的工作流，默认文件名：

`documentary-scene-api.json`

执行器会递归替换下列占位符，数值占位符必须在 JSON 中保留为完整字符串值：

- `__LINGNIAN_PROMPT__`：只依据已确认原文形成的画面提示；
- `__LINGNIAN_NEGATIVE_PROMPT__`：水印、畸形人物、具体正脸和虚构事实等负面约束；
- `__LINGNIAN_WIDTH__` / `__LINGNIAN_HEIGHT__`：1280×720 或 720×1280；
- `__LINGNIAN_FPS__`：固定 24；
- `__LINGNIAN_FRAMES__`：镜头秒数乘以 24；
- `__LINGNIAN_DURATION_SECONDS__`：镜头秒数；
- `__LINGNIAN_SEED__`：同一故事可复现的种子；
- `__LINGNIAN_IMAGE__`：上传到 ComfyUI 的授权照片文件名；
- `__LINGNIAN_OUTPUT_PREFIX__`：输出文件名前缀。

为适配 16GB RTX 5080，执行器默认每个纪实空镜只生成最多 5 秒，再用克制的循环和统一转场延展到分镜时长；不会一次生成整条 45–90 秒长片。可以通过 `LINGNIAN_MAX_GENERATION_SECONDS` 调整，但应先观察显存峰值。

正式 `scene_video` 工作流至少应输出 MP4、WebM、PNG、JPEG 或 WebP 之一。推荐在 16GB RTX 5080 上使用能逐镜头生成、单镜头显存稳定的图生视频/文生视频模型；不要把整条 45–90 秒影片一次塞进显存。
