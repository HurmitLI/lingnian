# 聆年家用生成节点 v2

## 新开发方向：10秒单场景（隔离验证中）

2026-09-07当前短片入口从完整采访里选择4～10秒的完整原话，生成原生10秒连续场景；不足10秒的部分保留自然停顿，不截句、不压速、不拼接。新入口为 `python -m lingnian_worker.short_scene`，与下面的正式旧节点和长片实验相互隔离。进度、权限和已知缺口见 `docs/10秒回忆片段开发记录.md`。不要使用旧的短镜头延展功能替代新目标。

离线制作包接收入口为 `python -m lingnian_worker.short_scene_bundle --bundle <ZIP路径> --expected-sha256 <独立核对的完整ZIP摘要> --output-dir <新任务目录>`。它只核验并接收四个文件（manifest.json、plan.json、recording.wav、reference.png），不调用 ComfyUI，不创建审核通过记录。重复输入不覆盖不同文件；输出 preparation-report.json 代表资料校验，不代表视觉通过。制作包含明文素材，只能走已授权的私密传输，不能作为公开下载或替代正式加密队列。

新增独立队列连接器 `python -m lingnian_worker.short_scene_service --once`，复用既有节点配置与凭据，但只领取 `native-short-scene-v1` 专用接口。默认不接入旧节点进程；后端和该连接器未部署前，不应运行于正式环境。完整队列、加密投递、取消、租约失效以及待验收回传已有本地集成测试，Windows兼容性以开发记录为准。

连接器将任务检查点放在既有工作目录的 `native-short-scene/<任务ID>/`，不把连接密钥或租约令牌写入日志。没有针对当前参考图的真实输入核对记录会回报 `awaiting_input_review`，不调用GPU。核对后只能经家庭授权继续同一个任务、同一个节点；遇到未知生成结果或断线，不自动重新提交。候选回传只进入待完整视听，媒体解码检查并非视觉质量通过。锁文件、中断任务和GPU日志须先核对，不能直接删除再跑。

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

## 隔离连贯影片路径：尚未完成产品验收

`film_contract`、`film_executor`、`film_assembly` 是另行验证的长片路径，不等于上面的正式节点已升级。该路径要求原生动态镜头、有依据的场景切换、真实旁白字幕以及逐镜复核，禁止通过循环或静帧延展凑满片长。测试通过不等于有照片/无照片的线上完整流程通过。

场景参考除人物、物品、年代和构图外，还必须针对当前方案的 `opening_state` 留下实际目视记录。参考文件对应的 `review.json` 需有 `opening_state_evidence`：

- `expected`：与该场景当前 `opening_state` 完全一致；复核仍绑定真实参考图及当前方案。
- `observed`：复核者实际看到的状态，非空文字；不能从 expected 自动复制生成。
- `matches`：严格布尔值。看不清或无法判断时不要批准，不能用数字 1 代替 true。

缺少或过期的状态依据会返回 `awaiting_scene_reference_review`，明确不匹配会返回 `scene_reference_rejected`，两者均不会提交视频生成。原参考及旧复核记录不自动覆盖，旧记录缺失此项必须重新检查。该检查只是要求复核依据齐全，**不是自动识别画面是否正确的模型**；复核者仍须实际查看原图，不得根据文件名或提示词认定“包已经闭合”。

当前实机进度、失败证据和与正式产品的接入边界以项目 `docs/60秒连贯影片自动推进任务.md` 为准。未通过的参考图或镜头不能改名后加入成片。

## 新十秒路径的场景参考准备（隔离接入中）

当前收缩后的进度以 `docs/10秒回忆片段开发记录.md` 为准，上述长片路径暂停推进。

`short_scene_reference` 使用此前已安装的 Klein 4B、qwen_3_4b、flux2-vae，只准备一张1280×704开场参考：无照片使用示意人物，有照片通过参考潜变量保留身份线索，不把竖版照片拉伸为横版场景。原文依据、问答身份、场景及原照片摘要由后端 `prepare_reference_brief` 重新验证和绑定。

默认CLI仅检查当前ComfyUI节点、模型名称选项和队列，不上传图片、不生成、不下载或安装。示例中的路径需替换为已准备、获授权的本地材料：

```powershell
python -m lingnian_worker.short_scene_reference --brief "reference-brief.json" --work-dir "reference-work"
```

有照片时加 `--photo "authorized-photo.png"`。只有可信调用方另外传入与方案相符的 `--authorized-brief-sha256` 才会请求生成；这只是精确输入绑定，不代替家庭身份验证或服务端授权事件。专属接口及加密投递已在本地实现，但尚未部署；不能把这个离线CLI当成已经可上线的授权系统。

每份方案固定种子和单图请求，保存实际图及持久请求日志；不确定结果沿用原日志，不换种子重投，不覆盖未知产物。缓存摘要不符或原进程锁存在时暂停。产物仅为 `awaiting_reference_review`，不写审核通过文件，不继续Wan。模型名称可选和PNG尺寸校验不能证明模型文件摘要、人物正确、历史准确或画面合格。

### 独立参考队列连接器（本地联调通过，未启动生产进程）

`python -m lingnian_worker.short_reference_service --once` 会领取一项已取得专属授权的参考任务，实际调用一次本机Klein并回传图片，**不是只读预检查**。使用前须先受控部署对应后端及迁移，并确认节点授权和空闲情况；不要把下面的说明当作已经启动或允许替换生产进程。

它复用已有LINGNIAN_BACKEND_URL、节点环境密钥或既有系统钥匙串、LINGNIAN_WORK_DIR与本机ComfyUI；不需要为单张参考查找ffmpeg/ffprobe，不创建凭据、不安装模型。只领取short-reference-v1，不启动视频。去掉--once才会持续等待新授权任务；发生中断或未知状态会以失败状态退出，不假装继续工作。

工作目录为short-reference/。领取前持久记录请求意图；若领取响应未知，不能自动再领取。拿到任务后保存非秘密编号和摘要；生成等待期间续租，结果回传丢响应时只查询原任务。重启时先恢复查询，不重新生成或自动重传；没有已确认完成结果则暂停，令牌只保留在内存。仍有connector.lock时先核对原进程，不能直接删锁；claim_outcome_unknown尚缺自动查回原领取编号的接口，需要受控核查，不得清空标记强行继续。

有/无照片两条本地集成测试已穿过真实HTTP路由、家庭/节点加解密、严格接收、参考执行器及结果保存；仅ComfyUI被纯色PNG模拟。该测试不是5080真实生成或视觉验收，也不是新采访产品闭环通过。
