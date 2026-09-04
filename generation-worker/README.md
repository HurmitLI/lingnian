# 聆年家用生成节点 v2

该程序运行在家庭自己的 Windows + NVIDIA 显卡电脑上。它只会领取家庭在正式网页逐项确认并授权发送的任务，不开放家庭网络端口，不保存平台账号密码，也不会启用声音克隆。

## 当前正式能力

- 读取第十五阶段的 45/60/90 秒、16:9/9:16 纪实分镜；
- 解密 `.lnpkg` 并核验照片、原声和制作说明摘要；
- 标题卡、来源卡和档案照片镜头由本地媒体工具制作；
- 纪实环境镜头逐条交给 `127.0.0.1:8188` 的 ComfyUI；
- 默认每个环境镜头只让模型生成最多 5 秒，再由本地媒体工具延展到分镜时长，避免 16GB 显存一次承担长镜头；
- 每完成一个镜头立即保存校验摘要并上报，断线重启后复用已完成镜头；
- 使用原始录音，不调用 TTS，不克隆讲述者声音；
- 原声长于目标片长时停止并提示选择更长时长，避免把一句话从中间截断；
- 合并后校验实际时长、分辨率和镜头数，再上传到家庭待验收区；
- 上传成功后清除本机任务目录；失败只保留生成镜头检查点，解密出的原始素材随临时目录立即清理。

## Windows 安装

1. 确认 NVIDIA 驱动、Python 3.11、ComfyUI 和 FFmpeg/FFprobe 可用。
2. 在 ComfyUI 中准备并导出“API 格式”工作流，按 `workflows/README.md` 放置占位符。
3. 用管理员 PowerShell 进入本目录，执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
```

4. 安装程序会询问正式后端地址、工作流路径和一次性节点密钥。密钥输入时不显示，并写入当前 Windows 用户的凭据管理器。
5. `doctor` 全部通过后创建登录自启任务 `LingnianGenerationWorker`，ComfyUI 仍只监听本机地址。

## 日常检查

```powershell
.\.venv\Scripts\python.exe -m lingnian_worker doctor
.\.venv\Scripts\python.exe -m lingnian_worker once
```

项目文件和日志禁止写入家庭故事正文、原声、节点连接密钥或云端登录信息。`.worker-data` 以及 `.env` 已被 Git 忽略。
