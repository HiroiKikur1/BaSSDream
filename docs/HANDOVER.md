# BaSSDream（原 BassStation）项目交接文档

> 更新时间：2026-09-26 早。本文档取代旧的 `AI_HANDOVER.md`（已删除，指标和架构描述已过时），并吸收了 `docs/扒谱架构方案.md` 的全部内容。接手的 AI 请先通读本文，再看 `AGENTS.md`（UI 文案规则）。

---

## 0. 一句话现状

- **UI**：WPF 原生客户端已按 BanG Dream! 少女乐团派对（GBP）风格全面重做，并更名为 **BaSSDream**，已部署到根目录 `BassStation.exe`。
- **数据**：修复了软件改坏原版谱的严重 bug，并加了写保护。
- **伴奏**：伴奏补齐和对齐改成了后台的"对齐 + 质量门控 + 派生文件"方案。
- **扒谱**：神经网络 BassNet（v3+v4 集成）。⚠️ **以记谱级指标衡量，测试集只有约 25% 的小节与原谱完全一致**（记谱音符准确率约 55%）；§10 里的"全对 67%"是音频时间指标，不代表谱面可用性。**最新情况以 §11 为准。**

---

## 1. 用户偏好与硬性规则（务必遵守）

1. **WPF 客户端（`src-native/`）才是正式 UI**；React Web UI（`frontend/`）、`desktop/` 壳和 `start_bass_station.*` 已于 2026-09-30 删除（git 历史可找回）。
2. **UI 文案遵守 `AGENTS.md` 零冗余准则**：不写说教提示，不加英文副标题，Toast 要短。
3. **扒谱质量优先**：单首新歌分析耗时 1 小时以内都可以接受。目标是持续逼近 100%，数字要如实报告。
4. **GPU 与用户玩游戏共用**：用户说暂停就暂停重负载任务（分离、训练），等用户通知再开始。
5. **原版谱不可改动**：用户购买的原版谱在 `C:\Users\hongw\Desktop\曲谱`（文件夹名与 `tabs/` 一一对应）。任何写 `.gp` 的代码都必须先调用 `gp_guard.is_protected()`。
6. **乐队标识只用官方素材**：不要自己画乐队文字或 logo（用户明确反感）。本项目纯自用。
7. 下载文件前必须征得用户同意（写明文件名、来源、大小）。已获批并下载的素材见 §3.4。
8. 用户期望：休息期间自主推进；完成后给出清晰、如实的数字总结。

---

## 2. 环境与目录

| 项 | 说明 |
|---|---|
| 项目根 | `E:\BassStation\` |
| WPF 客户端 | `src-native/`（.NET 10，`dotnet build -c Release`），产物复制到根目录 `BassStation.exe/.dll/...` 即部署 |
| 后端 Python（扫描器、tab_cli） | `C:\Python314\python.exe`（PATH 中的 `python` 就是它；**没有** librosa/torch） |
| 机器学习 Python | `C:\Users\hongw\AppData\Local\Programs\Python\Python311\python.exe`（torch 2.6 cu124、librosa、audio_separator 0.47、PIL） |
| GPU | RTX 3060 Ti 8GB |
| ffmpeg | `E:\BassStation\tools\Ultimate Vocal Remover\ffmpeg.exe` |
| 曲库 | `tabs/`（每首一个文件夹：`.gp` + PDF + 可选 `backing.mp3`） |
| 数据库 | `backend/data.db`（`song_cache`、`favorites` 等表；WPF 直接读） |
| 扒谱引擎新代码 | `backend/bassnet/` |
| 扒谱缓存 | `cache/bassnet/`（见 §5.2） |
| 文档 | `docs/HANDOVER.md`（本文）、`docs/扒谱架构方案.md` |
| 记忆 | `C:\Users\hongw\.claude\projects\E--BassStation\memory\`（三条：偏好、bassnet 管线、原谱保护） |

### 常用命令
```bash
# 构建 + 部署 WPF（部署前确认没有 BassStation.exe 进程在运行）
cd /e/BassStation/src-native && dotnet build -c Release
cp bin/Release/net10.0-windows/BassStation.{dll,exe,pdb,deps.json,runtimeconfig.json} /e/BassStation/

# WPF 调试渲染（只截客户区 PNG，不弹窗口；用于检查 UI）
./BassStation.exe --render-test out.png
./BassStation.exe --render-filter out.png        # 打开筛选面板
./BassStation.exe --render-fx out.png            # 点击特效
./BassStation.exe --render-sheet-cal|eval|add out.png   # 内嵌浮层
./BassStation.exe --render-song out.png "BWV 996"      # 选中某曲

# 真实窗口截图（PrintWindow，可以看到窗口边框级别的问题）
powershell -ExecutionPolicy Bypass -File E:\BassStation\cache\capture_window.ps1 -exe <exe路径> -out <png>

# 库重扫（Python314）
cd backend && python -c "from tab_scanner import scan_all_tabs; scan_all_tabs(force_rescan=True)"
```

---

## 3. UI（WPF）——已完成

### 3.1 视觉与交互（仿 GBP 选曲界面）
- **窗口**：客户区固定 1440×810（16:9，和海报比例一致），`WindowStyle=None`，自带 GBP 风格的最小化/关闭按钮，拖动空白处移动窗口。根元素是 `rootGrid`，窗口 `SizeToContent`。
- **标题栏**：左上角是 BaSSDream 标识，旁边粉色胶囊"自由练习"叠在白色胶囊"选择乐曲"上；右侧依次是搜索、日历、添加、粉色"筛选"。
- **左栏**：GBP 直角白条，左侧带色条，选中项整条变粉。分类为所有 / 收藏 / BanG Dream! / Project SEKAI / 5弦 / 古典练习曲 / 其他。
- **曲目列表**：深灰半透明条，选中项变成更高的粉色条，显示曲名、艺术家和等级；5弦标签；收藏的歌带心形徽章；圆形"切换"按钮用于排序。
- **舞台区**：
  - 390px 封面，白框加红色错位衬底，难度标签贴在左上角；
  - 下方白条显示曲名和艺术家；
  - 右侧依次是乐曲等级卡、BPM/弦数/时长深色条、最高分卡（点击打开演奏评测）、收藏 1/2/3、"复制PDF谱面"、删除；
  - 底部是"随机选曲"和大号"开始练习"。
- **按钮样式**：`App.xaml` 中的 `GbpFlat`、`GbpFlatPink`、`GbpAction`。扁平，按下时变浅色，没有位移。旧的 `GbpButton`、`GbpButtonWhite` 也已改为扁平。
- **筛选面板**：在窗口内从右侧滑出，点击空白处关闭。包括官方队标图块（多选）、难易度单选、等级区间双滑块（原版样式）、恢复默认值、关闭。实现在 `MainWindow.Filter.cs`。
- **添加曲目 / 演奏评测 / 练琴日历**：已改成**主界面内的浮层**，不再弹出系统窗口，点空白返回（`MainWindow.Sheet.cs` 的 `ShowSheet`）。原 Window 类仍保留，通过 `RequestClose` 事件关闭，`Loaded` 改挂在 Content 上。
- **点击/拖动特效**：随机弹出白色线描乐队徽记，同时散出彩色星星（`MainWindow.Effects.cs`，读取 `assets/band_icons_line/*.png`）。
- **背景**：
  - 有乐队海报时，海报以窗口中心为中心、等比完整显示，两侧用模糊层补满；
  - 没有乐队海报时，固定显示 `src-native/assets/stage_default.jpg`（迷幻舞台背景，由 `backend/stage_backdrop.py` 生成）；
  - 两条倾斜约 20° 的镂空"BaSSDream"字带沿自身方向滑动（素材 `src-native/assets/bg_outline_text.png`）。
- **细节**：
  - 滚动条是原版样式：细灰轨道加白色胶囊滑块；
  - 全局去掉了按 Alt 后出现的虚线焦点框（`App.OnStartup` 里的 `RegisterClassHandler`）；
  - "!"图标是原版闪电竖笔加圆点（`App.xaml` 的 `ExclaimBoltGeometry`）。
- **收藏**：`favorites(song_id, slot)` 表（`DatabaseService.LoadFavoritesAsync/SetFavoriteAsync`），支持 1/2/3 三个收藏夹。
- **古典练习曲封面**：`backend/classical_cover.py` 生成统一封面（`assets/classical_cover.jpg`，谱面是 BWV1007 开头的动机），已接入 `cover_generator`。
- **节拍对齐面板和伴奏状态条已移除**，这两项改为后台自动运行（§4.3）。

### 3.2 更名 BaSSDream
- 标识 = 原版 BanG Dream! 标识，只把"nG"重绘为同风格的"SS"。
  - 生成脚本：`cache/logo/make_bassdream.py`（S 字形构造在 `cache/logo/make_logo.py` 的 `glyph_S`）；
  - 字形参数（从原版测得）：斜度 tan=0.268、竖笔 10.1、横笔 8.0、字腔 3.2、x 字高 35.2、大写字高 44.1；
  - 下方片假名仍是原版"バンドリ"（用户要求"只改 nG"）。如果以后想改成"バスドリ"，问用户。
- 产物：`assets/bassdream_logo.png`（带白描边）、`assets/bassdream_plain.svg`、`src-native/assets/bassdream_logo.png`，程序图标 `src-native/BassStation.ico`（旧图标备份在 `cache/logo/BassStation_old.ico`）。
- 背景镂空字：`cache/logo/make_outline_strip.py`，生成 `src-native/assets/bg_outline_text.png`（1214×186）。
- 窗口标题、csproj 的 Product/ApplicationTitle、GP 写出器的 Tabber 都已改名。
- **未改**：程序集名、exe 文件名、命名空间、目录名仍是 BassStation（有意保留，避免破坏路径）；Web 前端未改。
- 旧的 BasS Station 标识（`bassstation_logo_official.png`）仍在 assets 中，但已不被界面引用。

### 3.3 官方乐队素材（已获用户批准下载）
- `assets/band_logos/{popipa,afterglow,hhw,pasupare,roselia,ras,morfonica,mygo,avemujica}.png`：
  - 前 8 个来自 bestdori.com 的 `assets/jp/band/logo/NNN_rip/logoL.png`；
  - Ave Mujica 来自维基共享资源 `Ave-mujica-original-logo.svg`（原图为白字），在图块上用的是改色为深红的版本。
- `assets/band_icons/*.png`：bestdori `res/icon/band_N.svg` 转成的彩色徽记。
- `assets/band_icons_line/*.png`：从彩色徽记提取的白色单线版，用于点击特效。
- 转换脚本：`cache/band_svg/convert.py`（用 Edge 无头模式把 SVG 转成 PNG）。

### 3.4 UI 待办
- [ ] 下载时顺带看到的"筛选"按钮在某些截图里颜色偏浅，可能是鼠标悬停状态，需在真机上确认。
- [ ] 真机长时间使用测试：后台伴奏队列、点击特效的性能、拖动窗口。
- Web 前端已删除（见 §24）。

---

## 4. 数据完整性、伴奏补齐（已完成）

### 4.1 原版谱被改写事件（已修复）
- 旧代码（`auto_ensure_backing_track`、`tab_scanner` 自动注入、`audio_aligner`、`gp_audio_linker`）会把下载的音频注入到**文件夹里所有 GP**，并把 FramePadding 清零。63 个 GP 因此失去同步，其中包括回層浮、BLACK SHOUT、天体観測等。
- 已从桌面原版恢复（335 个文件逐字节一致）。改动前的版本备份在 `cache/tabs_backup_20260925/`，代码修改前的备份在 `cache/code_backup_20260925/`。
- 有 7 个 GP 在原版目录中没有对应文件（巴赫 3 首、万青、又三郎、言って、春日影 MyGO），是软件生成或导入的，也有内嵌音频。
- 差异检查脚本：`cache/diff_tabs.py`，语义对比脚本：`cache/diff_semantic.py`。

### 4.2 写保护 `backend/gp_guard.py`
- `is_protected(path)`：与原版逐字节一致，或者内嵌了音频且不是派生文件，满足任一即受保护。
- 派生文件的标记：`meta.json` 里写 `bassstationDerived: true`。
- 已在 `gp_audio_linker`、`audio_aligner.update_gp_frame_padding`、`backing_track_enhancer.switch_backing_track_mode`、`tab_fetcher`（Songsterr 指法同步）这几处写入前做了检查。
- 扫描器 `tab_scanner.py` 的改动：
  - 不再自动注入音频；
  - 多个 GP 时优先选内嵌音频的，再优先派生文件，最后避开 `_N.gp`；
  - 时长改用 `bassnet.gpif_parser`，考虑变速和同步点。

### 4.3 新的伴奏补齐（后台）
- `backing_track_enhancer.auto_ensure_backing_track`：
  1. 文件夹里有内嵌音频的 GP → 直接就绪，什么都不写；
  2. 否则依次尝试候选音源：本地音频 → 网易云指定 ID → 按曲名搜索 → 按"乐队+曲名"搜索；
  3. 每个候选都用 `bassnet/score_align.py` 对齐（在 Python 3.11 子进程里运行，因为 WPF 调的是 Python 3.14，没有 librosa）；
  4. 只有**音高特异性质量 ≥ 1.8** 的音源才会写入派生文件 `xxx [伴奏].gp`，其中逐小节写同步点；原谱不动。
  5. 文件夹里还没有 GP 时（新歌转录流程），只下载音源。
- `score_align.py` 的做法（2026-09-27 重写，见 §15；旧 CQT 版保留为 `_align_cqt`，仅在模型不可用时回退）：
  - 质量指标：贝斯频段线性功率色度在谱面音级上的比值（匹配时 2.1–3.5，不匹配时 ≤ 1.6）。
- WPF 启动后会跑 `RunBackingQueueAsync`，对没有伴奏的歌逐首静默执行 `tab_cli ensure-backing`。
- 现状：春日影 CRYCHIC 已生成派生伴奏谱；独創収差的本地音频质量 1.79，被拒（谱是 Live 版，音频可能是录音室版）；还有约 4 首需要联网下载后再验证。

### 4.4 遗留 bug / 待办
- [ ] **演奏评测是假的**：`backend/performance_evaluator.py` 不看乐谱也不看音高，只算音量和到"从 0 秒开始的八分网格"的距离，分数被限定在 60–99，点评是拼接的模板句。需要用 `score_align` 加 BassNet 重建（见 §7 的 P4）。
- [ ] 旧的 `audio_aligner.auto_align_backing_track`、`tab_cli auto-align/adjust-offset` 仍然存在（写入会被守卫拦截），可以考虑删除。
- [ ] Original Call game size：测试中有 26% 的音符恰好低 2 个半音，可能是录音与谱面的调不一致（例如 game size 版本转了调），尚未核实。

---

## 5. 扒谱引擎 BassNet

### 5.1 模块一览（`backend/bassnet/`）
| 文件 | 作用 |
|---|---|
| `gpif_parser.py` | GP6/7/8 解析：选贝斯轨，展开反复和交替结尾，处理倚音、连音线、闷音；`score_anchors` 是纯谱面速度时钟。**SyncPoint 语义（已实测验证）**：同步段内音频以 ModifiedTempo 匀速运行；audio_time = FrameOffset/44100 − FramePadding/44100（`PAD_SIGN=-1`）。 |
| `build_stems.py` | 用 BS-Roformer-SW（overlap 2，autocast）分离全库贝斯，输出 `cache/bassnet/stems/<md5>.flac`（22050Hz 单声道）；索引写在 `index.json`。 |
| `dataset.py` | CQT 特征（A0 起，3 bin/半音，264 bin，hop 256 ≈ 86fps）、混音 log-mel（128 维）、首轮标签对齐（CQT 互相关，已被 `align_labels` 取代）、拍点/强拍标签。同一音频对应多份谱时的选择规则：**5 弦 > 非简化版 > 音符最多**。 |
| `lab/align_labels.py` | **当前标签对齐方法**：短窗 STFT 起音包络 × 音高能量，依次做全局偏移、5 秒窗局部精修（中值平滑）、逐音 ±25ms 吸附；原始时间保存在 `time_gp/end_gp`。 |
| `model.py` | BassNet：谐波堆叠 CQT（½f…6f）→ 卷积 → 双向 GRU(256)，混音 mel 分支，`stem_branch`（v2 起新增：分离音轨宽频加差分分支）。输出 49 类帧音高（0 表示休止，MIDI 23–70）、起音、闷音、拍、强拍。`load_checkpoint()` 会按检查点里的 `arch` 构建对应结构。 |
| `train.py` | 10 秒随机切片；增广有 ±3 半音移调、±10% 时间伸缩、增益、频谱倾斜、噪声；AMP；OneCycle。**固定划分**：测试集按歌名分组，基于完整索引（`test_split.json`）；验证集从训练歌里另外划出（种子 11）。 |
| `decode.py` | `decode_notes`（默认参数：起音阈值 0.5、最小间隔 4 帧、音高判定窗口为起音后 2–10 帧；可选网格约束，默认关闭）；`decode_beats`（Ellis 式 DP，局部速度连续、允许速度突变、全局周期先验；强拍取激活峰并按拍号补齐）；`note_metrics`。 |
| `quantize.py` | 节拍网格量化：逐拍选择细分（简单拍 4/3/8/6，复合拍 6/3/12）；逐小节判定复合拍（证据累积 + ±2 小节平滑）；小节线由强拍决定（支持变拍号）；`legato_steps=1` 最优。 |
| `gp_writer.py` | QScore → .gp：复制模板容器，时值拆分（直拍、三连音、复合拍），逐小节写同步点和 FramePadding，内嵌音频。回环测试（原谱 → 量化 → 写出 → 解析）：音高 99.7%、时值 99.3%。 |
| `fretboard.py` / `fingering_learn.py` | 学习式指法（从库中统计发射和转移代价，Viterbi）。在固定测试集上和手调规则持平（86.3% 对 86.2%），另一组随机集上 88% 对 80%。 |
| `pipeline.py` | 端到端：`posteriors`（所有 `cache/bassnet/bassnet*.pt` 集成 × 移调 TTA {0,±1}）→ 解码 → 节拍 → 量化 → 指法 → 写 GP。 |
| `score_eval.py` | 乐谱级指标：起音 ±60ms 匹配，时值用真值拍点换算，误差 < 1/12 拍算对。 |
| `eval_e2e.py` | 在测试集上跑完整链路（含节拍/强拍 F1）。 |
| `eval_cached.py` | 把测试集后验缓存到 `eval/post_cache*`，之后离线快速评测各种变体；`--combine` 可以平均多个缓存做集成。 |
| `tune_decode.py` | 在验证集上扫解码参数。 |
| `lab/realign.py` | EM 标签精修（用模型输出微调训练标签；测试集不动）。**注意**：它会让验证 F1 虚高；而 `align_labels` 会从 `time_gp` 重新计算，覆盖掉 EM 的结果。 |
| `consensus.py` / `lm.py` | 重复一致性、音程语言模型：受控测试有效，真实数据无效，默认不启用。 |
| `score_align.py` | 录音 ↔ 乐谱对齐（用于伴奏补齐，见 §4.3）。 |
| `lab/bench_legacy.py` | 旧引擎基线，`--test` 只跑测试集。 |
| `lab/run_p1.py` | 通宵编排脚本（已执行完毕）。 |
| `lab/test_roundtrip.py` | 量化和写出的保真度测试。 |

### 5.2 缓存 `cache/bassnet/`
- `index.json`：320 份谱 → 296 段唯一音频（按 md5）。
- `stems/`：299 个 flac（有 3 个来自旧版音频，无用）。
- `feats/<md5>.npz|json`：json 里有 notes（time/end/midi/time_gp…）、beats、downbeats、`rate_final`（训练要求 ≥ 0.85）。共 296 首。
- `test_split.json`：42 份谱（35 首通过质量门控，用于评测）。
- `bassnet.pt`（v1，无 stem_branch）、`bassnet_v2.pt`（v2，有 stem_branch，用 EM 标签训练）。
- `staging/bassnet_v3.pt`：**v3 训练中**（在暂存目录，是为了不被 `bassnet*.pt` 通配符提前加载）。
- `fingering.json`（指法统计）、`interval_lm.json`（语言模型，未启用）。
- `eval/`：`results_*.json`、`post_cache`（v1）、`post_cache_v2`、`post_cache_v2snap`、`post_cache_v2e13`。
- 日志：`cache/p1_*.log`、`cache/bassnet_stems.log`、`cache/bassnet_feats.log`。

### 5.3 已接入主程序的方式
- `ai_transcriber.build_complete_tab_project`：如果 `bassnet.pipeline.available()`（存在 `bassnet*.pt`），走 `build_complete_tab_project_bassnet`：
  1. 用 BS-Roformer overlap=8 分离；
  2. 调性检测；
  3. BassNet 推理；
  4. 写 GP（内嵌无贝斯伴奏，逐小节同步）；
  出错时回退到旧引擎（旧代码备份为 `ai_transcriber.py.pre_bassnet`）。
- 调用链：WPF 添加曲目 → `tab_cli transcribe-audio` → `tab_fetcher.transcribe_audio_to_library` → Python 3.11 子进程运行 `ai_transcriber.py`（`__PROGRESS__` 进度 + JSON 结果）。
- ⚠️ **这条完整流程还没有用一首真实新歌实际跑过**，这是最优先的待验证项。

---

## 6. 评测结果与实验记录

### 6.1 指标定义
- 测试集：35 首从未参与训练的歌，标签已用 `align_labels` 做 STFT 对齐。
- 比较对象是最终写出并解析回来的 GP 与原谱。
- 起音 ±60ms 匹配；音高准确率 = 起音匹配且音高正确的音 / 全部真值音；时值准确率只统计已匹配的音；全对 = 音高、位置、时值都正确。
- 35 首的测试集样本量有限，**不同检查点之间 ±2% 属于正常波动**。

### 6.2 结果汇总
| 系统 | 音高 | 起音召回 | 时值 | 全对 | 误报 |
|---|---|---|---|---|---|
| 旧引擎（原始音符，未量化） | 54.6% | 72.1% | 30.2% | 18.8% | – |
| 旧引擎（最终 GP） | 14.9% | 48.8% | 28.1% | 4.4% | – |
| v1（STFT 对齐标签） | 71.4% | 82.8% | 83.7% | 60.7% | 17.0% |
| v2 最终 | 71.8% | 83.4% | 83.4% | 60.6% | 18.9% |
| v1+v2（v2 快照，集成） | **73.2%** | **84.3%** | **84.6%** | **62.7%** | 13.5% |
| v1+v2 最终（集成） | 71.2% | 82.4% | 84.4% | 60.9% | 15.6% |

- 节拍 F1 约 0.83、强拍 F1 约 0.69（测试集）；验证集分别为 0.92 / 0.81。
- v3（修正后的标签 + stem_branch）第 7–13 轮的验证 F1 为 0.76–0.78（v1/v2 同期约 0.52–0.57）。**测试集成绩尚未评测**。

### 6.3 关键发现（按重要性排序）
1. **标签质量是最大的杠杆**。
   - 4 弦/5 弦选错导致低音八度标签错误（涉及 19 首 Ave Mujica）；
   - CQT 对齐在低音区时间模糊，部分歌偏差 >100ms。
   两项修正后，同一模型的测试得分就上升了，v3 的验证成绩也大幅提升。
2. 解码参数：起音阈值 0.5，音高窗口改为起音后 2–10 帧（起音匹配时的音高准确率 +5%）。
3. 给定真实起音时，音高准确率约 87%。错误构成：八度 1335 > 半音 904 > 其他 517 > 四五度 453。八度错误集中在 Ave Mujica 5 弦曲（v1/v2 受 4 弦标签污染）和 Morfonica（合成贝斯八度叠加）。
4. 起音召回的主要损失：同音重复拨弦（召回约 74%）和休止后的第一个音（约 62%）。
5. 经验证无效的做法：重复一致性（−3%）、音程语言模型（0 到 −1%）、放宽连奏合并、网格约束起音（F1 −0.6%）、节拍 DP 参数（差异 < 0.01）。

---

## 7. 未完成事项（按优先级）

### P0 — 立即可做
1. **v3 训练收尾**：后台命令为 `python -m bassnet.train --epochs 70 --out cache\bassnet\staging\bassnet_v3.pt`，日志在 `cache/p1_train_v3.log`，每轮约 145 秒，截至交接时在第 13/70 轮。如果进程已被中断，从头重跑即可（`--resume` 读的是 `--out` 路径）。完成后：
   ```bash
   cd /e/BassStation/backend
   C:/Users/hongw/AppData/Local/Programs/Python/Python311/python.exe -m bassnet.eval_cached --build --models "E:\BassStation\cache\bassnet\staging\bassnet_v3.pt" --cache "E:\BassStation\cache\bassnet\eval\post_cache_v3"
   C:/Users/hongw/AppData/Local/Programs/Python/Python311/python.exe -m bassnet.eval_cached --variant base --cache "E:\BassStation\cache\bassnet\eval\post_cache_v3"
   # 集成对比
   C:/Users/hongw/AppData/Local/Programs/Python/Python311/python.exe -m bassnet.eval_cached --variant base --combine <v1缓存> <v2缓存> <v3缓存>
   ```
   然后选定正式使用的模型组合：把要用的检查点放进 `cache/bassnet/`，命名为 `bassnet*.pt`；不用的移到 `cache/bassnet/archive/`。
2. **端到端实测**：拿一首曲库里没有的歌（或者一首测试集歌曲的原始 mp3），走 `ai_transcriber.py --audio ... --output ...`，确认以下几点：
   - 分离 overlap=8；
   - `pipeline.posteriors` 能正常加载混合结构的模型；
   - 生成的 GP 能在 Guitar Pro 8 里打开，伴奏同步；
   - 进度行和 JSON 结果能被 WPF 正确解析。
3. 考虑在 v3 上再做一轮 EM 精修（`lab/realign.py`），然后微调；评测时注意验证集会虚高，只看测试集。

### P1 — 扒谱精度（架构方向）
- **起音**：同音重复是最大短板。可以尝试：
  - 起音头改用更局部的高时间分辨率特征（hop 128 或原始波形前端）；
  - 用"起音 + 偏移"回归代替二分类；
  - 按类别（同音重复、换音、休止后）分别调阈值。
- **八度**：在 v3 结果基础上再看剩余的八度错误；可以试更强的时序模型（Conformer / Transformer），或加入 MERT 等基础模型特征（需要下载权重，先问用户）。
- **数据**：
  - 把"5 弦版和 4 弦版"都作为训练样本（4 弦版的音高按规则映射）不合适；应该坚持用与录音一致的版本；
  - 可以加入混音重组增广：其他歌的鼓和吉他叠加到分离残留上。
- **分离**：推理时试 BS-Roformer 与 htdemucs_ft 的贝斯轨融合，并用测试集验证是否提升。
- **节拍/强拍**：测试集强拍 F1 只有 0.69，会影响小节线位置。可以评估 `beat_this`（需要 pip 安装和下载权重，先问用户），或给强拍头增加训练权重。
- **技巧头**：滑音、击勾弦、闷音、Slap（GP 标签里都有，目前只训练了闷音）。

### P2 — 功能
- **演奏评测重建**（`performance_evaluator.py`）：录音 → BassNet → 与 GP 逐音符对齐 → 按小节给出音准、节奏偏差（ms）、漏音和错音。
- **低置信度小节标注**：在 GP 里用文字或颜色标出，配合人工校对回流，把修改后的谱加入训练集（主动学习）。
- 清理旧代码：`audio_aligner` 的写入逻辑、tab_cli 的 `auto-align`/`adjust-offset`、`gp_builder` 的旧量化（新管线已不使用）。

---

## 8. 架构计划（原 `docs/扒谱架构方案.md`，保留作为方向）

```
原曲 ──► ① 分离集成 ──► ② 声学模型 ──► ③ 节拍/拍号 ──► ④ 联合解码 ──► ⑤ 记谱与指法 ──► GP
             │              ▲   ▲            ▲               ▲   ▲             │
             │              │   └──混音分支──┘               │   └ 乐句重复一致性（实测无效，已关闭）
             └── 残差/置信度 ┘                 记谱语言模型（实测无效，已关闭）
                                                                          ▼
                                                           ⑥ 置信度标注 + 人工校对回流训练
```

| 模块 | 状态 |
|---|---|
| ① 分离：BS-Roformer-SW（推理 overlap 8） | 已实现；集成未做 |
| ② 声学模型 BassNet（谐波 CQT + GRU + 混音分支 + 分离音轨分支） | v1/v2 完成，v3 训练中 |
| ③ 节拍/强拍联合训练 + DP 解码 | 已实现 |
| ④ 联合解码（语言模型、重复一致性） | 已实现，实测无效，已关闭 |
| ⑤ 节拍网格量化 + GP 写出 + 学习式指法 | 已实现（量化和写出基本无损） |
| ⑥ 置信度与人工闭环 | 未做 |
| ⑦ 演奏评测重建 | 未做 |
| 标签工程（选对谱版本 + STFT 对齐 + EM） | 已实现，**效果最大** |

原则：
- 所有改动都在固定测试集上用同一套指标做回归对比；
- 验证集用于调参和选检查点；测试集只做最终报告；
- 任何依赖模型输出的标签修改（EM）都不能碰测试集。

---

## 9. 踩坑记录（给下一个 AI）

1. **Bash heredoc 里的 Python 字符串**：`r"E:\BassStation\..."` 在 `<<'EOF'` 里没问题，但在普通三引号字符串中 `\b`、`\t`、`\U` 会被转义，已经多次写坏路径。**改代码请用 Edit/Write 工具**，或者用 `os.path.join`。
2. Edge 无头截图：必须指定 `--user-data-dir=E:\BassStation\cache\edge_headless`，偶尔会失败，用 `cache/logo/shot.py`（带重试）。
3. 调试渲染偶尔会留下没有窗口的 BassStation.exe 进程，导致部署时"文件被占用"。先用 `Get-Process BassStation` 查看启动时间，确认不是用户在用再结束。
4. GP 资源文件名可能带全角字符，扩展名需要白名单判断（已在 `build_stems.py` 和 `dataset.py` 中修复）。
5. librosa DTW 只在目标格计代价，使用 (2,1) 步时要加权 2，否则路径会被压缩。
6. 质量指标要用线性功率，对数压缩后区分度不够（见 `score_align`）。
7. 5 弦低 B 在分离音轨中基频往往弱于二次谐波，不能用能量去自动挑选"4 弦版还是 5 弦版"的标签。
8. `pipeline.model_paths()` 会读取 `cache/bassnet/bassnet*.pt` 的全部文件并做集成。训练中的检查点不要放在这个目录。
9. WPF 调用的 `python` 是 3.14（没有 librosa、torch），需要机器学习的步骤一律用 Python 3.11 子进程。
10. 用户把 GPU 用于游戏时，训练和分离每轮会慢 2–3 倍，属于正常现象。

---

## 10. 2026-09-26 续（本节为最新进展，与前文冲突时以本节为准）

### 10.1 端到端实测（P0.2 完成）
- 新脚本 `bassnet/lab/e2e_real.py`：从测试集 GP 抽出原混音 → 以子进程跑 `ai_transcriber.py`（与 WPF 相同路径）→ 解析写出的 GP（按其自身同步点换算音频时间）→ 与原谱比较。结果在 `cache/bassnet/e2e/`。
- ALIVE（Morfonica）：音高 48.6%，与离线评测 48.3% 一致；写出 GP 的全局同步误差 +6ms；5 条进度行、JSON 结果均正常。**离线指标可代表真实流程。**
- 修复：
  - `ai_transcriber` 写 GP 前调用 `gp_guard.is_protected()`，冲突时改写 `[BASS TAB] xxx [AI].gp`；`gp_writer` 写出的 meta.json 带 `bassstationDerived: true`（AI 谱可被重新扒谱覆盖，原谱不会）。
  - BassNet 出错时回退旧引擎原本是静默的（旧引擎 GP 音高仅约 4–15%）；现在打印 traceback，JSON 带 `engine: "legacy"` 与 `fallback_reason`。
- 未验证：Guitar Pro 8 实际打开（本机无法自动化）；WPF 端完整添加流程。

### 10.2 解码/后处理改进（均在验证集选定，测试集确认）
| 改动 | 测试集效果 |
|---|---|
| 强拍后验用 v1+v2snap+v3 集成（音符/拍点仍只用 v3）— `cache/bassnet/models.json` 按角色配置模型 | 强拍 F1 0.675 → 0.749，音符指标不变 |
| 强拍解码改为 HMM（`decode.downbeats_hmm`，拍号 {4,3}、换拍号/相位跳变惩罚），已设为默认 | 再到 **0.849**（验证集 0.842 → 0.918） |
| 低置信音符标记：`min(conf, onset峰值) < 0.5` 的音符在 GP 里加 "?" 文字（`pipeline.LOW_CONF`） | 标出约 6% 音符，其中 68% 确实是错的（验证集） |

- `pipeline.posteriors` 支持 `models.json`：`{"notes": [...], "beats": [...], "downbeats": [...]}`（文件名，位于 `cache/bassnet/`）；没有该文件时所有 `bassnet*.pt` 用于全部输出头（旧行为）。
- `eval_cached.py` 新增：`--split val`、`--beats`/`--downs`（从其他缓存取拍点/强拍后验）、beatF1/downF1 输出、若干变体（end*、rep*、hmm*、sf*）。验证集缓存：`eval/val_*`。

### 10.3 已验证无效（别再试）
- 音符结束阈值 `end_thr` 0.4–0.8：时值准确率变化 < 0.3%。
- 同音重复弱起音合并 `rep_thr`：0.6 时误报 −3.5% 但召回 −1.5%，全对 +0.2%，基本中性；更高阈值明显变差。v3 起音峰值无法区分真假重复。
- 节拍周期选择 `strong_frac`：验证集最优 0.8（现值），测试集 0.9 略好 —— 不改。
- EM 精修（`lab/realign.py`）：v3 起音与标签中位偏差已仅 11.6ms，且 realign 以 `time_gp` 为基准会丢掉 STFT 对齐，跳过。

### 10.4 误差结构（v3 ep16，验证集）
- 时值误差主要来自起音错误而非结束判定：同音假重复把一个音切成两个（6.8%）、休止/结尾（6.7%）、漏掉下一个起音（3%）。
- 音高（起音匹配时约 90%）：其他 558、八度 456、四五度 322、半音 281；最差为 Morfonica（合成贝斯）与部分 Roselia。
- 节拍：测试集 4 首被判成双倍速（See you! ×2、Safe and Sound、Sanctuary），1 首 3:2（Georgette）。这是记谱习惯歧义；尝试用"拍强弱交替"判别，4 个样本里误判 1 个、漏 1 个，未采用。可考虑训练速度/节拍层级分类头。

### 10.5 v3 最终检查点测试集结果（另一会话补充，2026-09-26 09:55）
- v3 已训练完 70 轮，最佳为第 52 轮（验证 F1 0.785），文件在 `cache/bassnet/staging/bassnet_v3.pt`（未移动）；测试集后验缓存在 `eval/post_cache_v3`。
- 测试集 35 首，默认解码（base 变体，旧的强拍解码）：

| 模型 | 音高 | 起音召回 | 时值 | 全对 | 误报 | 拍 F1 | 强拍 F1 |
|---|---|---|---|---|---|---|---|
| v3 单模型 | **79.6%** | **90.2%** | 82.9% | **66.6%** | 12.3% | 0.878 | 0.881 |
| v2 + v3 集成（全部输出头） | 65.6% | 75.5% | 77.9% | 53.2% | 8.5% | 0.879 | 0.890 |
| v1 + v2 + v3 集成（全部输出头） | 71.4% | 82.1% | 83.6% | 60.8% | 10.9% | 0.868 | 0.882 |

- 结论：音符头只用 v3（与 10.2 的 models.json 方案一致）；v1/v2 参与音符集成会明显拉低。
- 注意：目前 `cache/bassnet/` 下的 `bassnet*.pt` 仍是 v1、v2（`models.json` 尚不存在），若不做配置，`pipeline` 会用 v1+v2 做推理。上线前需按 10.2 创建 `models.json` 或把 v3 放入该目录。

### 10.5 v3 完成并已部署（2026-09-26 10:00）
- 训练 70 轮，最佳为第 52 轮（验证 F1 0.785）；第 7 轮后验证基本平台期，之后主要靠退火小幅提升。**以后同配方 40 轮足够。**
- 测试集（35 首，最终 GP 解析回来比较，强拍用集成 + HMM）：

| 系统 | 音高 | 起音召回 | 时值 | 全对 | 误报 | 拍 F1 | 强拍 F1 |
|---|---|---|---|---|---|---|---|
| 旧正式（v1+v2 最终集成） | 71.2% | 82.4% | 84.4% | 60.9% | 15.6% | ~0.83 | ~0.69 |
| v3 最终 | 79.6% | 90.2% | 82.9% | 66.6% | 12.3% | 0.878 | 0.892 |
| **v3 + v3ep16（已部署）** | **79.6%** | 89.7% | **83.1%** | **66.9%** | **11.5%** | 0.875 | 0.890 |

- 选择依据是验证集（v3+ep16 全对 69.4% vs v3 单模 68.5%）。
- 部署：`cache/bassnet/` 里现有 `bassnet.pt`(v1)、`bassnet_v2snap.pt`、`bassnet_v3.pt`、`bassnet_v3e16.pt` + `models.json`（音符/拍点：v3+v3e16；强拍：四个全用）。v2 最终版移到 `archive/`。
- 正式配置真实端到端（5 首测试歌，含分离）：音高 80.4%、起音召回 91.2%、全对 68.7%；ALIVE 从 48.6% 升到 62.8%；5 弦自动识别正确；同步偏差 −12…0ms（略偏早，可忽略但可留意）；每首 3–7 分钟。

### 10.6 v4 实验（已完成并部署）
- 命令：`python -m bassnet.train --epochs 40 --seed 1 --rep-w 2.0 --sus-w 1.5 --out cache\bassnet\staging\bassnet_v4.pt`，日志 `cache/p1_train_v4.log`。
- `train.py` 新参数：`--seed`；`--rep-w`（同音重复起音的正样本权重）；`--sus-w`（持续音内部的起音负样本权重，压制假重复）。检查点里记录 `recipe`。
- 目的：①检验起音加权对"同音重复"短板的作用；②得到一个与 v3 同水平但不同种子的模型，用于集成（v1/v2 太弱，与 v3 集成反而拉低音高）。
- 完成后：`eval_cached --build` 到 `eval/post_cache_v4` 与 `eval/val_v4`，在验证集比较 v3 / v4 / v3+v4，按验证集决定是否更新 `models.json`。

- **结果**：v4 最佳在第 13 轮（验证 F1 0.792）。单模略弱于 v3（测试集全对 65.8% vs 66.6%），即起音加权本身**没有**带来明显提升；但 v3+v4 集成是验证集最优，已部署：

| 系统（测试集 35 首） | 音高 | 起音召回 | 时值 | 全对 | 误报 | 拍 F1 | 强拍 F1 |
|---|---|---|---|---|---|---|---|
| v3 + v3ep16（上一版） | 79.6% | 89.7% | 83.1% | 66.9% | 11.5% | 0.875 | 0.890 |
| v4 单模 | 78.9% | 89.3% | 82.5% | 65.8% | 11.3% | 0.845 | 0.853 |
| **v3 + v4（当前正式）** | **79.6%** | **89.8%** | **84.2%** | **67.6%** | **10.6%** | **0.883** | **0.897** |

- 验证集：v3+v4 全对 70.5%（v3+ep16 69.4%，v3+ep16+v4 69.9%）。
- `models.json`：音符/拍点 = v3+v4；强拍 = v1+v2snap+v3+v4。`bassnet_v3e16.pt` 移入 `archive/`。
- 低置信标记阈值在 v3+v4 下复核：标出 4.4% 音符，其中 69% 有错，阈值 0.5 保持。
- 正式配置真实端到端（5 首）：音高 80.8%、起音召回 91.3%、全对 69.1%、误报 12.4%。
- 下一步建议：再训一个不同种子的模型（可用原配方 `--rep-w 1 --sus-w 1`，30–40 轮）加入集成；预计收益每个模型 +0.5–1% 全对，边际递减。

### 10.7 接入软件（已完成，已部署到根目录 exe）
- 网易云搜索 →「扒谱」：`tab_cli transcribe-audio` → `ai_transcriber.py` → BassNet（按 `models.json`），v3+v4 已生效，无需改 WPF。
- **新增本地音频扒谱**：添加曲目浮层的「选择本地文件」（原「选择本地乐谱」）与拖放现在接受 mp3/wav/flac/m4a/ogg/aac/opus；
  - WPF：`AddSongWindow.ImportLocalFile` 判定音频后显示「扒谱中...」，超时 3 小时；
  - 后端：`tab_cli import-local <音频>` → `transcribe_audio_to_library(local_audio=...)`（音频复制为曲目文件夹里的 `source.<ext>`，跳过在线下载），之后与在线扒谱同一流程（GP + PDF + 封面 + 重扫曲库）。
- 实测（用 Python 3.14 按 WPF 同样方式调用）：ALIVE 混音 168 秒完成，入库字段完整（难度/BPM/弦数等），GP 与离线评测一致（音高 62.8%）；测试曲目已删除并重扫（曲库 325 首）。
- 备份：`cache/code_backup_20260925/` 下 `tab_fetcher_pre_local.py`、`tab_cli_pre_local.py`、`AddSongWindow.xaml.cs.pre_local`、`train_pre_v4.py`。

### 10.8 待办（更新后的优先级）
1. 再训一个不同种子的模型加入集成（见 10.6）。
2. 起音：同音重复仍是最大短板（加权损失无效），需要结构改动：更高时间分辨率的起音分支（hop 128，需要重算特征），或原始波形前端。
3. 节拍层级：4/35 首测试歌被判成双倍速，1 首 3:2；可以训练一个速度/节拍层级分类头。
4. Guitar Pro 8 实际打开生成的 GP 并确认「?」文字显示正常（本机无法自动化，需用户确认）。
5. 同步偏差 −12…0ms（略偏早），可以考虑常量校准 +5ms，需先在更多歌上确认。
6. P2：演奏评测重建；旧代码清理（`audio_aligner` 写入、`auto-align`/`adjust-offset`、`gp_builder` 旧量化）。


---

## 11. 2026-09-26 下午：用户实测"完全不可用"后的排查与修复（最新，优先阅读）

### 11.1 用户反馈
在 Guitar Pro 8 里打开生成的谱：没有「?」、没有内嵌伴奏、音符混乱、节奏与拍号混乱，"达不到 80% 全对"。反馈完全属实。另：ALIVE（Morfonica）是**电贝司加强力过载**，不是合成贝斯（§10.4 的说法错误）。

### 11.2 根因（按影响排序）
1. **GP 写出格式错误，GP8 实际读不对**（此前只用自己的宽松解析器做回环测试，测不出来）：
   - 升号写成 `<Accidental>Sharp</Accidental>`，GP8 要求 `#` → **所有升号音被 GP8 丢弃显示为休止**（B 大调歌约一半音符消失）；
   - GP8 要求文本字段用 CDATA，ElementTree 会丢掉 CDATA → **BackingTrack 整个被忽略**（无伴奏），「?」不显示；
   - BackingTrack 放在 MasterTrack 之前、缺 ChannelStrip；Assets 在 ScoreViews 之后；
   - ConcertPitch 八度差 1；`InstrumentSet/LineCount` 被改成 4（那是五线谱线数，应为 5）；
   - 模板用了《Ether》：失真音色 + 残留原曲自动化。
2. **拍号**：量化器逐小节判断复合拍，阈值极小 → 4/4 歌里 27% 小节被写成 12/8（Tomorrow's Door 45/172 小节）。
3. **评估指标与"谱能不能用"脱节**：`score_eval` 只看音频时间上的音符（起音±60ms、音高、以拍计的时值），对拍号、小节线、节奏记法完全不敏感。
4. 谱从第一个贝斯音开始，前奏被切掉（与原谱小节号错开、FramePadding 达 −11 秒）。

### 11.3 修复（均已生效于正式流程 `ai_transcriber → pipeline.build_gp`）
- `gp_writer.py`：`#`/空 Accidental、八度=midi//12、Beat 子元素顺序（Dynamic Rhythm StemOrientation×2 FreeText Notes Properties）、Note 属性按名字母序、BackingTrack 在 MasterTrack 之后并带 ChannelStrip、Assets 在 ScoreViews 之前、文本字段 CDATA（InstrumentSet 内除外，与原版一致）、zip 条目按名排序；固定模板 `cache/bassnet/template.gp`（购买原版《Swear》game 版拷贝，干净贝斯音色），清空模板音轨自动化。
- **新 `bassnet/gp_lint.py`**：从 `Desktop\曲谱` 购买原版学习元素顺序/必需子元素/枚举取值（档案 `cache/bassnet/gp_profile.json`，`--rebuild` 重建；**不要用 tabs/，里面有旧代码写坏的文件**）。`pipeline.build_gp` 每次写出后自动校验，结果 JSON 带 `lint_problems`。
- **GP8 实机截图验证**：`cache/gp8_shot.ps1 -file <gp> -out <png>`（通过文件关联打开，截图后只结束自己启动的 GP8；GP8 已在运行时自动跳过）。用 Python 调用以避免路径转义问题。已验证：伴奏波形「Audio Track」、「?」、升号音、标题都正常。
- `quantize.py`：`meter_mode="simple"` 为默认（曲库 278 首里 261 首纯单拍子；按起音位置判断复合拍的判据在快歌里被噪声主导，不可靠），每拍内三连音细分保留；`lead_in=True` 在第一个音前补整小节休止直到录音开头。
- **新 `bassnet/notation_eval.py`（主指标）**：原谱与生成谱都切成小节放到音频时间轴上配对，比较小节内 (位置, 音高, 时值, 闷音)。原谱时间映射 `label_time_map` = 原始 GP 时间 + `global_lag` + align_labels 曲线（注意 `time_gp` 已含 global_lag）。`eval_cached` 与 `e2e_real` 都输出 NOTATION 行；`--per-song` 看逐首。

### 11.4 真实成绩（记谱级，当前默认配置）
| | 小节线对齐 | 拍号一致 | 小节完全一致 | 节奏一致 | 记谱音符准确率 |
|---|---|---|---|---|---|
| 旧 v1+v2（修正前量化） | ~70% | 85% | 19% | 29% | 41% |
| v3+v4、逐小节拍号（今早交付给用户的状态，另有格式 bug） | 80% | 78% | 21% | 32% | 45% |
| **v3+v4、单拍子（当前）测试集** | 80% | **99.9%** | **24.6%** | **36.5%** | **55.1%** |
| 同上 验证集 | 82% | 94% | 27.5% | 37.4% | 59.8% |
- 5 首真实端到端（分离→GP，GP8 可正常打开）：小节完全一致 26.6%、节奏 42%、拍号 100%、记谱音符 62%；Tomorrow's Door 记谱音符 73%、小节 37%。

### 11.5 剩余误差结构（验证集小节）
- 完全一致 28%；**小节内只多一个音 15.5%**（其中 72% 是"原谱一个长音，我们在中间多拆出同音高的音"——音频能量特征 AUC≈0.5，即录音里确实像重新拨弦；属于扒谱者记谱习惯/Live 版差异，不能简单删音）；多处起音不同 13%；小节线错位（真实）约 18%；只少一个音 7%；仅音高 9%；仅时值 5%。
- 小节线错位主因：节拍模型在部分段落锁到反拍（全部原谱拍点中 5% 反相）；DP 参数（jump/tight/bias）无效，需改进节拍模型本身。复合拍歌（Georgette 6/8、Imprisoned XII 9/8）目前处理不了。
- 指法：音高对但把位与原谱不同（如 B1 用 E 弦 7 品而原谱 A 弦 2 品）——记谱指标不计弦/品，但影响可读性。
- 技巧：滑音、击勾弦未识别（slide 终点被当成新音）。

### 11.6 路线反思与建议
- 声学部分（起音/音高）大致可用；**瓶颈在"音符→记谱"**：拍层级/相位、节奏写法、重复音记法、指法、技巧——目前全是手写规则，且训练数据里现成的记谱信息（qpos/qdur/弦/品/技巧）没用上。
- 建议方向：
  1. 以 `bar_exact`/`notation_note_acc` + GP8 实机截图为验收标准，任何改动先在验证集比较。
  2. 让模型直接学记谱：在拍网格上预测"该拍内起音位置/细分"与"是否延续上一音"（学习扒谱者对重复音的写法）；训练标签直接来自原谱 qpos/qdur。
  3. 节拍/强拍：专用节拍模型（如 beat_this，需下载权重，先问用户），或加大节拍头训练权重、加入反相惩罚。
  4. 指法：用原谱弦/品统计学习把位偏好（已有 `fingering_learn`，需以"与原谱同弦同品率"评估）。
  5. 技巧头：滑音/击勾弦（GP 标签里都有）。

### 11.7 伴奏派生谱（`[伴奏].gp`）同样有格式问题 —— 已修
- `score_align.write_derived_gp` 原本也用 ElementTree 重写整份原谱：BackingTrack 顺序/ChannelStrip 错误，且**原谱里全部 CDATA（标题、段落、歌词…）被丢掉**，GP8 不加载伴奏。
- 现改用 `gp_writer.attach_backing` + `save_gp`（与扒谱写出器共用）；`gp_writer.repair_gp()` 只处理带派生标记的文件。
- 已就地修复曲库中 7 个 `[伴奏].gp`（备份 `cache/derived_backup_20260926/`），lint 26–27 → 0；GP8 实测春日影派生谱：伴奏波形、同步点、段落标记正常。
- `gp_lint` 新增原始 XML 层面的 CDATA 检查（解析后的树看不出 CDATA）。

### 11.8 核对版（2026-09-26 下午，用户需求）
- GP8 **一个文件只能有一条音频轨**（官方文档确认），所以用配套文件：`[BASS TAB] <曲名> [核对版].gp`，谱面与正式版相同，音频 = 原曲 + 1.5× 分离贝斯（低频比原曲高约 5 dB，峰值归一化 0.97）。由 `ai_transcriber.make_check_audio` + `pipeline.build_gp(check_audio=...)` 在每次扒谱时生成（音频 `check_mix.mp3`）。
- WPF：「开始练习」右侧 78×78「核对」按钮（与左侧「随机选曲」对称），仅当存在核对版时显示（`MainWindow.Check.cs`）。打开核对版同样计入练习时间，按钮显示「核对中 mm:ss」。
- 同步：会话结束时若核对版在会话期间被保存（修改时间变化），调用 `tab_cli sync-check <核对版>` → `check_version.sync_check_to_practice`：正式版 = 核对版的完整谱面（含用户改的同步点），换回正式版自己的无贝斯伴奏；旧正式版备份到 `cache/check_sync_backup/`；只改 BaSSDream 自己生成的文件（购买原版一律拒绝，已测试）。
- `gp_guard.is_derived` 现在也认 `Tabber = BaSSDream`（GP8 保存时会重写 meta.json、丢掉派生标记）。
- 扫描器：`[核对版]` 文件永远不会被选为练习文件。
- 已验证：Python 侧同步（模拟 GP8 保存：改音符、改资源名、去掉派生标记）→ 正式版拿到修改、伴奏正确、lint OK；WPF 按钮显示/隐藏（--render-song）。**未验证**：在真实 GP8 里编辑保存后由 WPF 自动触发同步的完整流程（GUI 自动化不可靠，需用户实测）。
- 曲库里保留了一首测试曲「核对测试」（ALIVE 混音）供用户试用该流程，可用删除按钮删掉。

### 11.9 v5：技法头 + 效果器增广（进行中）与 beat_this 评估
- **技法标签**：`gpif_parser` 现在解析 Slide 标志位（1 换把 / 2 连音 / 4 向下滑出 / 8 向上滑出 / 16、32 滑入）、HopoOrigin/Destination、Beat 级 Slapped/Popped；已补进全部 296 个特征 json（`cache/add_tech_labels.py`，逐音校验 0 不匹配）。曲库频率：滑音 20%（264 首）、击勾弦 2.6%、闷音 2.1%、Slap/Pop 2.0%/1.3%（36 首）。
- **模型**：`BassNet(tech=7)` 新增技法头（`model.TECH_NAMES`：slide_arr, slide_out_down, slide_out_up, slide_in, hopo_arr, slap, pop），标签在音符起音帧；旧检查点照常加载（5 路输出）。
- **效果器增广**：`bassnet/lab/build_fx.py`（tanh/硬削波/非对称/fuzz，驱动 1.5–25×，可选中频提升，音箱低通 1.5–5 kHz，干湿 0.3–1），每首 2 个变体 → `cache/bassnet/feats_fx/`（592 个，按真实录音高频能量分布校准：曲库中位 0.036、90% 0.13、最高 0.30）。训练 `--fx-p 0.3`。训练缓存 CQT 改为 float16。
- **推理端**：`pipeline.posteriors(with_tech=True)`、`attach_techniques`（阈值 `cache/bassnet/tech_thr.json`，由 `python -m bassnet.lab.eval_tech --split val --cache ... --tune` 生成）；写出器支持 Slapped/Popped（滑音/击勾弦已有），GP8 实测滑音线、H、S、P 均正常显示。
- v5 命令：`python -m bassnet.train --epochs 40 --seed 2 --tech --fx-p 0.3 --out cache\bassnet\staging\bassnet_v5.pt`，日志 `cache/p1_train_v5.log`。
- **beat_this（CPJKU，MIT，权重 final0 81 MB 已下载到 ~/.cache/torch/hub/checkpoints，`pip install beat-this --no-deps`，torch 未变）**：在本库上**比我们自己的节拍头差**（验证集拍 F1 0.86 vs 0.93，小节完全一致 17% vs 27%）：系统性早 18 ms，55 首里 7 首选了半速（我们的节拍头学的是这些谱的速度记法习惯）。**不采用**，代码 `bassnet/lab/beat_external.py`、`eval_cached` 变体 bt/bt_hmm 保留。
- **八度错误分析**：出错时模型很确定（正确八度的后验只有所选的 2–10%），且相邻音也大多在错误八度（仅 8–17% 支持正确八度）→ 整段八度偏移，解码层面无法修复；需要模型/数据层面（可能是录音与谱的八度记法不一致、或某些音色整体误判）。

## 12. 2026-09-26：演奏评测重写（`backend/performance_evaluator.py`）
- 旧版只统计音量/过零率并设保底分，与谱面无关；每次评测覆盖最高分；WPF 用 Python314（无 librosa）调用、stderr 不读可能卡死。已全部替换。
- 新版：录音 ↔ GP 贝斯轨逐音符比对。音高感知互相关求整体偏移（录音可从任意位置开始，可只录一段；不匹配直接报“录音与曲谱不匹配”）→ 中位数对齐（恒定延迟不扣分，抢拍/拖拍扣分）→ 每个音判 PERFECT ≤40ms / GREAT ≤75 / GOOD ≤115 / BAD（超时或错音）/ MISS。维度：节奏、音准、完整、干净（多余起音）。按小节输出热力图与点评（抢拍/拖拍/错音/漏音小节）。
- 关键坑：① GP 同步点与真实伴奏音频局部偏差 p90≈50ms、最大≈100ms → 参照时间用 bassnet 训练缓存 `cache/bassnet/feats/<md5>.json` 里已对齐到音频的音符时间（按 qpos 插值，覆盖 318/326 首）；无缓存且有内嵌音频时用录音做 3s 窗局部校正；无音频的谱直接信 GP 时间。② 低音区 CQT 时间分辨率极差（E1 窗约 1s），起音必须用短窗 mel 谱通量；音高用 2–4 次谐波和（±2 半音局部比），失败的音再用 pYIN 复核。③ librosa `onset_detect` 默认在线峰值拾取会报上升沿，需 `post_max=3`。
- 运行环境 Python311；CLI：`performance_evaluator.py <录音> <song_id> <gp> [--no-save]`，最后一行 JSON；只在超过历史最高时写库（旧启发式记录无 judgments_json，会被直接替换）。WPF 端 `Views/EvaluationWindow` 只读库，不再写。
- 测试：`cache/eval_test/`（`synth_cases.py` 由谱合成带已知错误的演奏 + `score_synth.py` 出混淆矩阵；`run_cases.py` 用分离贝斯轨）。当前：干净合成 100/ALL PERFECT；漏音 23/23；错音 27/31；70ms 拖拍段正确定位。真实入口验证：`BassStation.exe --render-eval-run out.png <录音> <曲名>`。

## 13. 2026-09-26：歌曲评级重写（`backend/difficulty_evaluator.py`）
- 旧版问题：总音符数占 24% 权重（short/完整版差 2–4 级）；时间轴不处理连音/反复/变速/延音线（Ether 被算出 13 音/秒的假峰值）；“反拍”按音符序号奇偶判定；死音当幽灵音；5 弦硬 +0.8；按乐队名加标签。
- 新版：用 `bassnet.gpif_parser` 取音符 → 同时发声合并为事件 → 每事件负荷 = 速度系数 ×（1 + 左手换把〔一指一品把位模型 × 时间压力〕+ 跨弦 + 节奏位置〔8 分/16 分反拍/连音、休止后反拍〕+ 技巧〔Slap/Pop、击勾、滑音、倚音、和弦〕）→ 4 s 窗口负荷 → 取最难 10% 均值 → 耐力对数加成（≤ ~+15%）→ 固定分段对数映射到 Lv6–31（`LEVEL_KNOTS`，按本库分位数一次性标定后冻结，新歌不影响旧歌等级）。.gp5 走旧解析器兜底。
- 结果：同曲多版本等级差中位数 1.3 → 0.45（大差异组均为不同编配：Live/原声/5 弦不同指法）；“簡單版”均低于正常版；Tier 分布 EASY 22 / NORMAL 91 / HARD 161 / EXPERT 46 / SPECIAL 6。
- 已重评：`python backend/tab_scanner.py --rerate`（只更新评级列）。评级模型改动后需重跑（扫描器按 mtime 跳过缓存）。验证：`python cache/rating_test/validate.py [--groups]`。重评前数据库备份 `cache/code_backup_20260926/data.db.before_rating`。

### 11.10 v5 结果（2026-09-26 晚）
- 训练 40 轮，最佳第 28 轮（验证 F1 0.786，与 v3/v4 相当）。
- **音符/记谱**：验证集上 v3+v4 仍最好（小节完全一致 27.5%）；加入 v5 的任何音符组合都略降。节拍改用 v4+v5：小节线 82.2→83.6%，但小节完全一致不变、记谱音符略降 → 不改。
- **效果器增广**：对失真歌无明显作用（ALIVE 音高 64→66%，FIRE BIRD 68→71%，仮死化 73→68%）。
- **技法**（`eval_tech`，音符级精确率/召回）：滑音各类 F1 0.1–0.46、精确率 0.1–0.5；击勾弦 ≈0；Slap 验证集 P 0.62 / R 0.76（仅 21 个样本，测试集无 Slap 歌）。按"精确率 ≥ 0.6 才启用"只保留 **Slap**（`tech_thr.json` = [1.01×5, 0.15, 1.01]，1.01 表示关闭）。
- **部署**：`models.json` 新增 `"tech": ["bassnet_v5.pt"]`，音符/拍点/强拍不变；5 首端到端结果与之前完全相同，lint 0。
- 下一步想法：
  1. 滑音改用信号方法（CQT/帧后验中两音之间的连续音高滑移）而非从不一致的谱面标记学习；
  2. **核对版修正回流训练**：用户在核对版里改过并同步的谱，是最贴近录音的标签，可累积为高权重训练数据（主动学习）；
  3. 记谱学习（拍网格上的细分/重复音写法）、节拍反相、整段八度偏移仍是主要瓶颈。

## 14. 2026-09-26：软件内录制 + 邦多利风结算页 + 逐音符报告
- **录制**：评测浮层底部 = 选择录音 / 输入设备（`cache/eval_session/input_device.txt`）/ 开始录制。点击后 `backend/eval_session.py`（Py311）生成会话 wav：4 拍预备拍 + 去贝斯伴奏 + 节拍器，缓存于 `cache/eval_session/<key>.wav/.json`。去贝斯 = GP 内嵌混音 − `cache/bassnet/stems/<音频md5>.flac`（拟合延迟/增益）；无缓存分轨的现场用 BS-Roformer 分离（约 90 s，存 `cache/eval_session/stems`，失败不缓存）。原谱无音频但有 `<名> [伴奏].gp` 时，会话与评测都用派生谱。节拍器为 2.8/3.4 kHz 纯音，在评测起音通量（≤2 kHz）与音高频段之外。
- **播放/录音**：`Services/TakeRecorder.cs`（NAudio.Core + NAudio.Wasapi 2.2.1，部署时需一起复制这两个 dll）。先开录音、再播放，`RecordOffset` = 播放开始时已录秒数；评测器收 `--lag-hint (RecordOffset − lead_s)`，只在提示 ±0.6 s 内搜整体偏移（设备延迟由中位数对齐吸收）。录音存 `cache/evaluations/takes/`。
- **评测器输出**新增 `fast/slow/wrong/prev_best/report_path`；报告 JSON（`cache/evaluations/reports/`，`backend/eval_report.py`）= 按演奏顺序展开的小节 → 拍（节奏值/附点/连音/休止）→ 音（弦/品/判定/dt/错音），附小节评级（PERFECT/GREAT/GOOD/BAD + 快慢倾向 >40 ms）、段落评级（GP Section 标记，无标记按 8 小节分组）和 `summary`。
- **WPF**：`Views/EvaluationResultView`（全窗结算页，背景 `assets/live_stage.jpg` 由 `cache/stage_bg/make_stage.py` 自绘）→ 详细报告 `Views/ReportView.cs`（DrawingContext 绘制彩色 TAB，复制 = 图片 + 文本）。调试：`BassStation.exe --render-result out.png <report.json> <曲名> [--report [滚动]]`；真实录制 `--debug-take out.png <秒> <曲名>`（会外放并录音）。
- **已知限制**：外放伴奏 + 麦克风时，伴奏里的鼓/吉他会被判成贝斯音（去贝斯伴奏本身送评可得 ~69 分）→ 麦克风须戴耳机；声卡直插不受影响。
- **2026-09-26 晚 UI 修订**：结算页按原版逐项复刻（Canvas 绝对布局；背景 = Bestdori `bg/result/skin00/rhythmBG.png` 裁切为 `assets/result_stage.jpg`；中文用资源圆体 Resource Han Rounded CN（OFL，`E:\BassStation\assets\fonts`，App.xaml `RoundFont`），数字/英文用 Arial；描边渐变字 `Views/GbpText.cs`）。得分改为游戏分值：`OverallScore`（0–100，库内保留 4 位小数）× 25000，AP = 2,500,000（`PerformanceScoreDetailModel.ToPoints`，主界面/评测浮层/结算/报告统一）。报告页改为工业风（直角/切角/细线框/Bahnschrift），报告页隐藏评级徽章，按钮为「复制报告」（白）/「返回」（粉，主按钮）。

### 11.11 对照实验：通用大模型 vs 自训专用模型（2026-09-26 晚）
- **标签上限**：同一录音的两份人工谱，4 弦改编版 vs 5 弦版音高一致率只有 27–94%（整段八度改编）；忠实重复版之间 95–100%；Easy/短版 61–87%。测试集里没有按名字可识别的改编版（选谱时优先 5 弦/非简化），训练+验证里有 9 首 + 31 首 Live 版。
- **YourMT3+**（YPTF.MoE+Multi noPS，GPL-3.0，检查点 561 MB 在 `tools/ymt/`，独立环境 `tools/ymt_env`（继承系统包，另装 lightning/transformers 4.45.1/torchvision 0.21 cu124——系统里的 torchvision 0.29 与 torch 2.6 不匹配，系统环境未改）；脚本 `tools/ymt/bench_bass.py`、对比 `bassnet/lab/bench_compare.py`）：

| 测试集 35 首（音频层面） | 音高 | 忽略八度 | 起音召回 |
|---|---|---|---|
| **BassNet v3+v4** | **80.1%** | **84.2%** | **90.3%** |
| YourMT3+ 原曲混音、贝斯乐器 | 12.5% | 17.6% | 20.3% |
| YourMT3+ 同一贝斯分轨 | 18.2% | 21.8% | 28.2% |

  时间对得上（中位偏早 22–37 ms），但严重漏音（只出约 40–60% 的音），只有 5% 的音被分到贝斯乐器。结论：**通用大模型整体替换/精调路线不可取**。
- **MERT 特征探针**：MERT-v1-330M（CC BY-NC 4.0，1.26 GB 在 `cache/models/MERT-v1-330M/`），取第 6/12/18 层 → PCA 384（解释方差 85.7%，`cache/bassnet/mert_pca.npz`），特征 `cache/bassnet/feats_mert/`（3.2 GB，`python -m bassnet.lab.build_mert`，用 ymt_env 运行）。模型 `BassNet(mert=384)` 新分支；训练时移调样本置零 MERT（MERT 无法随 CQT 移调），另 10% 随机丢弃。训练 v6m：`--epochs 40 --seed 3 --tech --mert 384`，日志 `cache/p1_train_v6m.log`。
- **数据扩充**：清单与渠道见 `docs/训练数据扩充清单.md`（Songsterr Plus 官方可下 GP；mySongBook 不可导出；雪鹽子/ぷりんと楽譜/Piascore 只有 PDF）。
- **MERT 探针结果（v6m，最佳第 35 轮，验证 F1 0.783）**：同配方单模型对比 v3，验证集音高 82.6→82.8%、记谱音符 58.9→59.5%、小节完全一致 24.5→24.7%；测试集音高 80.1→80.2%、记谱音符 55.0→54.4%、小节完全一致 22.9→23.6%——**均在 ±2% 噪声内，无实质提升**。加入集成（v3+v4+v6m）验证集略降、测试集略升，按规则不采用。未部署（部署还需在正式流程里跑 MERT，另需 transformers 环境）。
- **总结论**：v3/v4/v5/v6m 无论改损失、加技法头、加效果器增广、加预训练特征，验证集都收敛到 note F1≈0.78、音高≈82%、小节完全一致≈25–27%；通用大模型（YourMT3+）远差于我们。→ 瓶颈是**数据量与标签（记谱）本身**，不是模型容量或特征。路线定为：**自训专用模型 + 扩充数据（见 docs/训练数据扩充清单.md）+ 记谱层建模（节拍网格）+ 核对版修正回流**。

### 11.12 数据扩充工具（用户决定：暂不使用 Songsterr 与社区谱）
- Songsterr：robots.txt 对所有程序禁止 `/api/`，条款禁止自动化绕过 Plus 付费下载 → 不做抓取。**注意**：现有 `tab_fetcher.fetch_songsterr_gp_url`（「添加曲目」的 Songsterr 导入）调用 `/api/` 并直接取修订里的 GP 原文件，不经 Plus；是否保留待用户决定。**用户 09-30 决定：Songsterr 导入保留，继续修缮维护**（上面“暂不使用”只指训练数据扩充）。
- 用户曲库 = 雪鹽子在爱发电出售的全部 GP 谱（已全部购买）。社区谱质量堪忧，用户决定先放弃。
- 保留的工具：`backend/ingest_incoming.py`（`E:\BassStation\incoming\` 收件夹 → 读谱元数据、拒绝无贝斯轨/AI 谱 → 建曲目 → 取音源 → 对齐质量门控 → 通过的生成 [伴奏].gp 进入训练索引；.gp5/.gpx 放 `需要转换/`，用 GP8「文件 > 批量转换」）。模拟测试（去掉音频的 Tomorrow's Door）：自动取音源、对齐质量 3.41 → 收录；测试产物已删除。
- **训练索引排除 AI 谱**：`build_stems.is_ai_transcription`（Tabber=BaSSDream 或文件名含 [核对版]）。
- **固定划分**：`cache/bassnet/split_keys.json`（测试 31 个歌曲键 / 验证 17 个，与此前完全一致：训练+验证 243、测试 35、验证 20）；以后新增的歌一律进训练集，`train.split_songs` 与 `tune_decode.val_metas` 均读取该文件。

### 11.13 标签八度噪声与"谱质量"客观评分（2026-09-27）
- `bassnet/audio_verify.py`：纯信号的逐音证据。`d_down(L)` = 在 L-12、L+7、L+16（低八度音的奇次谐波）处的峰值度，表示"实际比谱低一个八度"。
- 结果：模型判为"低一个八度"的 848 个音里，**95% 录音里确有低八度证据**（中位 +2.1，正确音 −0.07）；判为"高一个八度"的则没有（模型真错）。→ 低八度"错误"多数是谱面写高八度（4 弦改编 / 音区选择），模型是对的。全部训练标签里约 3% 的音有强证据（>1.5），集中在 16 首（4 弦版、部分 Project SEKAI 合成贝斯叠低八度）。
- 注意：物理上"只弹低八度"与"贝斯原八度 + 合成器叠低八度"频谱无法区分 → 八度部分是记谱选择，不直接改标签；可考虑对这些音用八度宽容的损失，最终八度由弦数/把位规则决定。
- `bassnet/lab/tab_quality.py`：一份谱与录音的客观一致度——agree（模型同时同音高）、agree_pc（忽略八度）、coverage（模型听到的音被谱包含的比例）+ 数据集的音频命中率 rate_final。基准（55 首未训练的购买谱）10% 分位：agree 0.653、agree_pc 0.72、coverage 0.828、rate 0.956；中位 0.83/0.86/0.92/0.99。
- 验证：4 弦改编版 agree 大降而 agree_pc 不变（能识别"只是八度改编"）；版本不符（栞 短版增編）全 0；Easy 版识别不出。人为损坏：错 30% 的音 100% 被拦，删 20% 的音 100% 被拦；错 10% 的音几乎拦不住（35% vs 完好谱 24% 的误拦率）——受限于模型本身约 80% 的准确率。
- 若启用社区谱：导入 → 分离/特征 → 模型后验 → tab_quality 门控（达到购买谱 10% 分位）→ 训练时降权、永不进验证/测试集。尚未接入，等用户决定。

### 11.14 4 弦 / 5 弦双版本 + 八度宽容训练（2026-09-27，用户：不启用社区谱；4、5 弦都弹）
- **录音证据修正后的音高准确率**（谱写高八度且 d_down>1.5 时模型判低八度不算错）：验证 84.7→85.5%，测试 82.0→83.8%（此处按"优先同音高/低八度"匹配，数值口径略高于 compare_notes）。八度改编噪声真实存在但只占 1–2 个百分点。
- **标签标注**：`cache/mark_oct_amb.py` 给 6,435 个音（3.0%）标 `oct_amb`（d_down>1.5 且低八度 ≥ B0）。
- **八度宽容训练**：`train.py --oct-tol`，歧义帧的损失 = −log(p(写的八度)+p(低八度))，移调增强时备选八度同步移动。v7o 训练中：`--epochs 40 --seed 4 --tech --oct-tol --out cache\bassnet\staging\bassnet_v7o.pt`，日志 `cache/p1_train_v7o.log`。
- **4 弦版自动生成**：`pipeline.four_string_arrangement`——5 弦转录（有低于 E1 的音）时额外写 `[BASS TAB] <曲名> [4弦版].gp`：含 E1 以下音的小节**整小节升八度**（超出 67 时只升越界音），标准 4 弦重新分配把位。验证（测试集墮天，与雪鹽子手工版比较）：我们的 4 弦版 vs 他的 4 弦版音高一致 **88.1%**（用 5 弦版比只有 66.2%）；5 弦版 vs 他的 5 弦版 89.5%；他自己两版之间 73%。
- **UI**：舞台区 BPM/时长信息条右侧新增「4弦 | 5弦」分段切换（仅当存在 [4弦版] 时显示，此时文字不再重复弦数），「开始练习」打开所选版本；核对版始终对应正式（5 弦）版。已部署。扫描器不会把 [4弦版] 选为正式练习文件；训练索引也排除（Tabber=BaSSDream）。


## 15. 2026-09-27：伴奏对齐重写（`bassnet/score_align.py`）
- 用户反馈：Master of Puppets 的 [伴奏].gp 同步点速度混乱（逐小节 180/353/4321 BPM 乱跳），起音偏 0.8 s。根因：旧版用原始 CQT 做 DTW + 逐小节平移，每小节速度直接取自 DTW 路径抖动；且谱面开头休止段的 DTW 起点不受约束。
- 新流程：分离贝斯（`eval_session._separate`，按音频 md5 缓存）→ BassNet 后验（帧音高/起音/拍/强拍，缓存 `cache/bassnet/align_post/<md5>.npz`）→ 粗 DTW（93 ms，只信谱面有音符处的路径点，偏移 ±3 s 中值滤波）→ **拍级 DP**：状态 = 相邻两拍的帧，证据 = 起音×音高后验 + 拍/强拍激活，代价 = 二阶速度变化（`LAM`）+ 偏离 ±16 拍滑动中值速度（`LAM1`，防长程漂移）；两遍（第二遍参考速度来自第一遍）。
- 写出：同步点**稀疏化**（`sync_bars`：直线插值偏差 ≤ 10 ms 的小节不写；谱面变速处必写），ModifiedTempo 按段算，OriginalTempo 写该处谱面速度（旧版恒写首速度）。
- 基准 `cache/align_test/bench.py`（测试集 35 首，参照 = 训练标签且与作者 GP 同步相差 ≤ 40 ms 的音）：

| | 中位误差 | p90 | ≤50 ms | 最差一首 p90 |
|---|---|---|---|---|
| 旧 CQT 版 | 51.5 ms | 140 ms | 48.2% | 23 s（整段错位） |
| 新版 | 11.6 ms | 31 ms | 97.4% | 134 ms |

- 仍存疑：墮天 4 弦等个别段落与作者同步差一个八分音符（~150 ms），但模型拍/起音证据与原始起音通量都支持新结果，无法判定谁对（需人耳）。
- Master of Puppets：谱是 BanG Dream 版编配，录音是 Metallica 原版，bar 244–258 处录音似乎多一遍 riff，该处速度被摊到几个小节（70 BPM / 430 BPM），其余段 205–215、中段 103–105。
- 已重新生成曲库 8 个 [伴奏].gp（旧文件备份 `cache/derived_backup_20260927/`），片头首音与检测起音逐一核对（`cache/align_test/intro_check.py`），GP8 实机打开正常。脚本：`cache/align_test/regen_derived.py`。
- 耗时：新歌首次约 3–4 分钟（分离 + 后验，占 GPU），之后命中缓存 5–60 s。
- **v7o 结果（八度宽容训练，最佳验证 F1≈0.785）**：单模型 vs v3——验证：音高 82.6/82.5%、小节完全一致 24.5/25.7%、记谱音符 58.9/58.4%；测试：音高 80.1/79.6%、记谱音符 55.0/52.4%。录音证据修正后音高：验证 85.3/85.2%、测试 83.8/83.5%；低八度错误比例不变（验证 1.4/1.3%，测试 2.5/2.8%）。集成 v3+v4+v7o：验证小节完全一致 +0.3、记谱音符 −0.2 → **不采用，未部署**。
- 至此又一条"改训练目标"的路线确认无效；验证集依旧收敛在同一水平（音高≈82–85%）。

## 16. 2026-09-27：曲库命名 / 乐队分类 / 4·5 弦合并 / 封面校验
- 用户反馈：同一首歌出现多个看起来一样的条目；命名混乱；ヨルシカ「又三郎」被归到 BanG Dream!；封面错乱（「僕は...」显示 MyGO 1st LIVE 视频截图）。
- 根因：`tab_scanner.extract_metadata` 以文件夹名为准（卖家用 `_` 代替非法字符，且几乎每个文件夹都带 "BanG Dream!" 后缀），未识别的一律默认 BanG Dream!；艺术家按列表顺序而非出现位置匹配（焚音打 → Ave Mujica）；弦数检测读错 gpif 结构（`Tuning/Pitches`），全部靠文件夹名里的"5弦"；封面批量抓取取网易云搜索第一条，不做校验。
- 新模块 `backend/library_meta.py`：标题/艺术家优先取 GP 内嵌 Title/Artist（清洗后），乐队按别名表识别，**franchise 只由乐队/角色归属决定**（其余为空 → "其他"）；版本标签 Live/Acoustic/Easy/先行/Cover/TV/Game/Short/Full/#N（#N 仅在同曲有多版本时保留；同名同标签再冲突时追加演出名，如 R 的 Live Rose / Live Farbe）；弦数取 GP 调弦；`group_key` = 标题+乐队+版本，4/5 弦成对。个别修正放 `backend/library_overrides.json`（按文件夹名）。
- `tab_scanner`：新增列 `version`、`group_key`、`alt_gp_path`（同文件夹内另一弦数的谱，如 天体観測 4st/5st、AI 的 [4弦版]）；每次扫描都会重算身份信息（`apply_identity`，约 8 s）。数据库改动前备份 `cache/code_backup_20260927_data.db`。
- WPF：4/5 弦两版合并为一个列表条目（标签"4/5弦"），舞台 BPM 条右侧「4弦 | 5弦」切换：跨文件夹的两版切换整首歌（等级/时长/成绩各自独立），同文件夹的两版切换打开的文件；最后一次选择存 `cache/ui_prefs.json` 作为默认；"5弦"分类默认显示 5 弦版。标题旁显示版本标签。
- 封面：`backend/cover_match.py` 只接受"歌名一致 + 署名含本乐队/艺术家"的网易云结果；导入流程（tab_fetcher 三处）已改用 `fetch_cover`。无可信结果时用新的兜底封面（`cover_generator`：官方乐队 logo + 乐队色斜纹，无文字）。已人工核对全库封面（联系表 `cache/cover_audit/sheet_*.jpg`），9 首明显错误且无可信来源的已换成兜底封面，旧图备份 `cache/covers_backup_20260927/`。GBP 游戏版曲目的"4th Anniversary"图是网易云该专辑的官方图，未改。

## 17. 2026-09-27：记谱层诊断、评测修正、标签重对齐、分离上限

### 17.1 评测修正（`bassnet/notation_eval.py`）
- 旧评测用 `label_time_map`（缓存里的 `time_gp` + 对齐曲线）放置原谱小节，但 `time_gp` 来自旧版 GP 时间映射，与当前解析器相差中位 40–80 ms（Touring 0.85 s）→ 小节匹配（容差 80 ms）大量失败，谱面指标被系统性低估。
- 现在默认 `fixed_q_map`：用**固定**的 v3+v4 后验跑 `score_align.align_posteriors`（节拍级 DP），结果缓存 `cache/bassnet/eval/gt_align/<md5>.json`，保证不同模型 A/B 用同一条小节时间线。`eval_cached`、`e2e_real`、`notation_oracle` 都走 `gt_score_bars()`。
- `score_align.align()` 拆出 `align_posteriors(info, fr, on, be, do)`；伴奏对齐基准不变（中位 11.6 ms，97.4% ≤50 ms）。
- 新增：`compare_bars` 输出 `note_pos_pitch`、`note_pos`；`error_breakdown()` 逐音归因；`lab/notation_oracle.py`（PP/PG/GP/GG 神谕对照）；`lab/notation_sweep.py`（多进程扫解码/量化/HMM 参数，`--beats` 可指定节拍后验来源）。
- 正式配置（v3+v4）修正后：验证 notation_note_acc 58.9% → **68.8%**，测试 → **67.2%**，小节线 94%/98%。真音符+真节拍喂量化器 ≈ 98%（量化器本身几乎不丢分）。
- 逐音错误（验证）：音高 7.9%、多余音 7.4%（不计入召回，但使前一音时值变短）、小节错位 5.1%（Henceforth 强拍相位差 2 拍、The Whole Blue World 3/4 判成 4/4、Imprisoned XII 5 拍）、漏音 4.3%、时值 8.5%（其中约 3/4 是"时值=到下一个音"，但下一个音多了或漏了 → 本质是起音问题）。
- 试过无效：起音阈值扫描（0.5 已最优）、`grid_beats` 弱起音（变差）、强拍 HMM 加"贝斯换根音"证据 `bass_change_evidence`（+0.3–0.6，不稳定，默认关闭 `chg_w=0`）。

### 17.2 标签时间错位与重对齐（`bassnet/lab/relabel.py` → `cache/bassnet/labels_v2/`）
- 旧标签（作者 GP 同步 + 局部 CQT 修正）在 Live 版和快速八分音符段整段偏 60–120 ms（常恰好一个八分）。纯信号频谱通量裁判：分歧音符中支持新对齐 1308 vs 旧标签 526（另 473 两边都有起音）。
- 做法：两折模型 `staging/fold0.pt`/`fold1.pt`（`train.py --fold 0/1`，各用一半训练歌 20 轮；验证 F1 都约 0.776，与全量模型相当）为另一半歌出后验；val/test 用缓存 v3+v4 后验 → `align_posteriors` → 音符时间 = 对齐网格，±35 ms 吸附到起音峰；拍/强拍也从对齐网格重建；只有频谱通量支持度不下降才采用新标签。
- 结果：296 首里 293 首采用；通量支持 0.839 → 0.901（仅网格不吸附 0.877）；平均 9.8% 音符移动 >60 ms；7 首原先因 rate<0.85 被排除的歌重新可用（285 首）。
- 使用：`train.py --labels <dir>` 或环境变量 `BASSNET_LABELS`（`load_metas`、`eval_e2e.test_metas` 都读取）。
- 新标签下同一 v3+v4（验证）：音高 82.4 → 84.9%，起音召回 92.3 → 94.5%，拍 F1 0.929 → 0.971，强拍 0.896 → 0.938 → 旧评测低估了模型。

### 17.3 v8（v4 配方 + labels_v2）—— 不采用，未部署
- `train.py --epochs 40 --seed 1 --rep-w 2.0 --sus-w 1.5 --labels labels_v2`，`staging/bassnet_v8.pt`，最佳 ep31 验证 F1 0.840（同尺子 v4 0.835、v3 0.832）。缓存 `eval/val_v8`、`eval/post_cache_v8`。
- 单模型：音高 +1–1.5、起音召回 +1.5，但多余音 +1–2、拍 F1 变差；谱面 验证 +0.3 / 测试 −3.4。v3+v4+v8：验证 +0.8 / 测试 −0.8。换阈值、节拍改用 v3+v4 都在 ±1 噪声内。

### 17.4 错误审计（`bassnet/lab/error_audit.py`、`bassnet/lab/audit_list.py`）
- 起音匹配但音高不同的音（验证 8.9%），用分离轨谐波证据 `d_here` 做裁判：支持模型 74%、支持谱 19%、不确定 8%；裁判在模型与谱一致的音上判错率 2–6%。非八度错误（半音/全音/四五度）也有 65–80% 支持模型。八度类因 E1 基频弱，结论要打折。
- 含义：剩余"错误"相当一部分是谱与录音不一致；现有 GT 已接近测不出进步的程度 → 需要人工核对的金标准小集合。
- 抽查清单：`docs/音高分歧抽查清单.md`（4 首 × 12 处，含时间/小节/拍/谱/模型/裁判倾向），等用户听后回填，用于校准裁判。

### 17.5 分离上限（`bassnet/sep_ceiling.py`，结果 `cache/sep_ceiling/results.json`）
- 20 首验证歌：用谱渲染加法合成贝斯（随机拨弦位置/衰减/亮度/过载）→ 混入原伴奏（原混音 − 分离贝斯）→ BS-Roformer 再分离。
- 干净合成：F1 0.983、音高 99.3%；再分离：F1 0.972、音高 99.2%；SDR 12.8 dB。**分离只损失约 1 点 → 不调分离模型。**
- 模型从未见过合成音色，却在"与谱完全一致的干净贝斯"上接近满分 → 真实误差主要来自真实演奏/音色复杂性和谱-录音不一致，而不是听音能力或分离。

### 17.6 结论与下一步
- 正式模型不变（v3+v4）。评测代码修正已生效。
- 下一步优先：① 用户按抽查清单听 10–20 处，校准审计裁判；② 建立人工核对的金标准小集（5–10 首，经核对版流程），作为新的主评测；③ 用裁判清洗训练集音高标签（仅高置信分歧），再训；④ 无标注 JRock 录音的半监督。
- 用户问过但已回复、未执行：MuScriptor（CC BY-NC，需用户在 HF 接受许可并登录后才能实测）；租算力（现阶段不需要）；Rocksmith/CDLC（CDLC = 社区谱，暂不用；官方 DLC 解密违反 EULA）；视频 OCR 扒谱（不建议）。

### 17.7 "乐理 / 编曲 / 贝斯手" 先验（用户提议）—— 四种做法均无效
- 交叉后验：`bassnet/lab/xfold_post.py` → `cache/bassnet/eval/xfold/`（230 首训练歌，各用没见过它的 fold 模型）。逐音数据集 `bassnet/lab/context_model.py build` → `cache/bassnet/context_ds.pkl`（train 17.3 万音 / val 1.7 万 / test 2.5 万）。
- 声学模型出错时通常很自信：错选音高的中位概率 0.75，44% > 0.8；正确答案排第二约占一半。
- ① 全曲上下文 Transformer（`lab/context_model.py`，残差修正声学 log 概率 + 弦位辅助任务）：val 91.04 → 91.06（噪声）；加时间位置编码、lr 1e-3 → 训练 91.5 但 val 降到 90.0（过拟合）。
- ② "贝斯手"联合解码（`lab/play_decode.py`：top-3 音高 × 弦位 Viterbi，学到的指法发射/换把代价）：test 90.91 → 86.9（w=1）… 90.7（w=8），始终不超过基线。注意 `fingering.json` 只排除了 test，不排除 val。
- ③ 八度一致性投票（`lab/octave_vote.py`：同音名 + 前后音程 + 拍内位置分组，概率求和定八度）：val/test/train 全部持平或下降。
- 结论：声学模型（BiGRU、数秒上下文、用谱训练）已隐含了从 230 首谱能学到的规律；剩余错误无法用统计先验推翻。
- "?" 标记覆盖率（逐音音高错误）：当前 conf<0.5 标 3% 的音、抓到 19–26% 的错误（精度 60–65%）；conf<0.8 标 11–12%、抓到 52–56%（精度 40–45%）；conf<0.9 标 18–20%、抓到 68–70%。

## 18. 2026-09-27 晚：新方向（用户定）—— 谱面人性化 + 分离专攻贝斯

用户对听重合成音频后判定：模型比参考谱更贴近录音，~10% 差异主要是参考谱与原曲不符；音频→音高→谱面已可用。此后**参考谱不再是评价标准**。新评价：忠于音频（重合成相似度）+ 规整易读 + 可弹 + 用户听感。反复记号用户选 (b)：完全相同的段落用反复记号。**每次替换正式版前先快照**到 `snapshots/<日期>_<名>/`（首个：`snapshots/2026-09-27_v3v4`，77 MB，含权重、配置、后端代码与基准）。

### 18.1 重合成对比（`bassnet/resynth_compare.py`）
- 同一合成音色分别渲染模型扒谱与参考谱，和分离轨在"每行按全曲均值白化的半音 CQT"上逐帧余弦比较（任一方有声的帧都计入）。
- 55 首：相似度 谱 0.586/0.612 vs 模型 0.591/0.617（验证/测试）；2 秒窗 模型更像 20–21%、谱更像 15–17%、其余持平。该指标偏向模型（模型本就跟着分离轨），且不适合跨分离模型比较（偏好"干"的分离轨）。
- 对听文件：`cache/resynth_listen/<曲名>/`（分离轨 / 谱渲染 / 模型渲染 / 左右声道对照）。

### 18.2 分离压力测试（`bassnet/lab/sep_stress.py`、`bassnet/sep_compare.py`）
- 渲染 8 种音色（指弹、拨片、slap、过载、fuzz、合成贝斯、八度、合唱）混入真实伴奏（原混音 − 分离贝斯），可选贝斯压低 dB，比较多个分离模型；结果 `cache/sep_stress/results.json`，样例音色 `cache/sep_stress/demo/`。
- 正常音量 BS-Roformer-SW：多数音色 F1 损失 ≤1–2 点；fuzz −2~−6。
- **贝斯压低 9 dB（被鼓/吉他埋住）**：指弹 F1 0.96→0.88、拨片 0.90、fuzz 0.78（音高 91%）；htdemucs_ft 明显更差（0.58–0.67）。现有开源模型里 BS-Roformer-SW 最好 → 需精调（重点 fuzz/过载 + 被埋）。
- 真实歌曲"纯度"（分离轨能量落在参考谱音符 1–8 次谐波上的比例）：bs_sw 与 htdemucs_ft 均约 0.79，不足以区分。
- `sep_compare.run_separator(name, src, out)` 可调用 audio_separator 里任意模型（`SEPS` 表）。

### 18.3 段落识别（`bassnet/section_model.py`，权重 `cache/bassnet/section.pt`，训练副本 `staging/section.pt`）
- 数据：购买谱 338 份中 252 份有 `<Section>` 标注（A=A 旋律、B=B 旋律、C=サビ、D.. 其他、Intro/Outro/solo 文字）；归 7 类 intro/A/B/C/D/inter/outro；可用于训练的 163/13/21 首（有交叉后验或缓存后验者）。
- 特征只来自音频（按小节）：混音 mel 16 频段 + 能量、贝斯音名分布（相对主音）、起音密度、贝斯静音、位置、mel/音名自相似、Foote 新颖度、贝斯音符指纹重复度（Jaccard）。BiGRU + 注意力，输出类别与段落起点。
- 结果（±1 小节边界 F1 / 逐小节类别）：验证 0.69 / 64%，测试 0.67 / 58%。Tomorrow's Door 实测结构 Intro-A-A-B-C-A-B-C-D-C-Outro，主段落位置与原谱差 ≤2 小节。
- 可提升：加入人声分离特征（A/B/C 主要按人声旋律分），需重新分离约 300 首（~5 h GPU）。

### 18.4 反复记号 + 段落标记（`gp_writer.write_gp(sections=..., repeats=True)`，已接入正式流程）
- `find_repeats`：连续完全相同的 1–8 小节块（音符/节奏/连音/"?"/技法/拍号全同）重复 ≥2 次 → 只写一遍 + `<Repeat start/end count>`；块内不跨段落起点；全空小节块不压缩。同步点按每一遍写 `BarIndex` + `BarOccurrence`。MasterBar 子元素顺序 Key Time Repeat Section Bars。
- 回读验证：20 首验证歌展开版与反复版解析后逐音时间/音高/时值完全一致（`cache/test_repeats.py`）。GP8 截图：反复"7x/8x"、段落标记正常（`cache/rep_test/*.png`）。
- `pipeline.song_sections()` 生成 `{小节: (Letter, Text)}`（Intro / A 1 / B 1 / C 1 / D 1 / Interlude / Outro），练习版、核对版、4 弦版都带；`gp_lint` 已把 BarOccurrence 等同步字段列为自由值。
- 真实端到端（`e2e_real --names "Tomorrow's Door"`）：lint 0，11 个段落，3 处反复。
- 现状：扒出的重复段常有 1–2 个音不同 → 多数歌压缩很少（Henceforth 158→82 小节，多数只少几小节）。下一步"规整"：近似重复段按声音证据统一写法、保留真实加花。

### 18.5 规整 + 多结尾 + 解析器修复（已接入正式流程）
- `bassnet/regularize.py`：同拍号、(起音位置, 音高) Jaccard ≥ 0.7 的小节按中心点聚成"乐句家族"；每种写法放到每个成员位置上用声学后验打分（帧音高/休止对数概率 + 起音证据 + 未认领起音惩罚），全组总分最高者为标准写法；成员仅在自身位置得分损失 ≤ τ 时改写（真实加花保留）；跨小节连音的小节不动。正式流程 `pipeline.TIDY=True, TIDY_TAU=0.05`，在 quantize 之后、指法之前。
- 评估 `bassnet/lab/regularize_eval.py`（τ=0.05）：验证 改动 4.7% 音符，不同写法小节 72.4→66.5%，重合成 0.6018→0.6011，对参考谱 68.8→68.7%；测试 改动 5.7%，75.1→68.4%，重合成 −0.0008，对参考谱 −0.3。
- `find_repeats` 增加多结尾（`|: 共同部分 |1. :|2. |`，两遍，结尾 ≤ 块长一半）与 `min_saved=2`（至少省 2 小节才用反复，避免一串单小节 |: :|）。块元组现为 `(起始, L, k, m)`，`_saved()` 算节省小节数。
- `gpif_parser._playback_order` 修复：第二结尾在反复终止小节之后时原逻辑会跳过它；现在用 `cur_pass/block_end` 跟踪当前遍数。库内只有 1 份（Walking with you）展开结果变化（+1 小节），其余 9 份带反复的谱不变。
- 回读验证：20 首验证歌 反复+多结尾版与展开版逐音一致；GP8 截图正常。`gp_lint` 自由值加入 AlternateEndings、同步字段。
- 对比谱（带原曲伴奏）：`cache/tidy_demo/<曲名> [规整前|规整后].gp`（Henceforth、転生林檎、Tomorrow's Door、ALIVE）。

### 18.6 分离压力测试汇总（2 首 × 音色 × 电平，`cache/sep_stress/results.json`，键 `md5|音色|电平dB|分离器`）
| 条件 | bs_sw 分离后 F1 | htdemucs_ft |
|---|---|---|
| 正常电平：指弹/拨片/slap/过载/合成/八度/合唱 | 0.95–0.98 | 0.93–0.96 |
| 正常电平 fuzz | 0.92 | 0.92 |
| −9 dB 指弹 / 拨片 | 0.91 / 0.93 | 0.78 / 0.73 |
| −9 dB 过载 / fuzz / 合成 | 0.87 / 0.81 / 0.85 | 0.76 / 0.71 / 0.62 |
- 干净渲染在 −9 dB 时也降到 0.93–0.95（混音 mel 分支受影响）。结论：短板是"被埋 + 失真/合成音色"，bs_sw 仍是现有最佳 → 下一步精调分离（合成多音色贝斯 × 真实伴奏 × 随机电平）。注意：旧结果里 `r["sep"]` 是分离后指标，分离器名只在键里（已改为 `separator` 字段）。
- **修复（2026-09-28）**：反复版同步点的 `<Automation><Bar>` 必须填**展开后的演奏小节序号**（GP8 自己的文件如此：Bar 78 / BarIndex 46 / occ 1），`BarIndex`+`BarOccurrence` 填谱面小节与第几遍。之前填成谱面序号 → GP8 把同步点放错位置，出现 70/300 BPM 跳变、伴奏错乱（用户在 Henceforth 发现）。我们的解析器只读 BarIndex+occ，回读核对发现不了 → 反复相关改动必须用 GP8 截图核对总时长与音频轨同步点。修复后 Henceforth 总时长 3:57 = 音频 236.9 s。修复前曲库没有新生成的 AI 谱。

### 18.7 固定速度 + 稀疏同步 + 雪鹽子式反复（2026-09-28，用户要求）
- 购买谱统计：91%（307/338）全曲只有 1 个速度；反复记号仅 10/338 份使用，块长只有 2/4/8/16 小节（从不单小节），次数多为 2–4，多结尾仅 2 份，无 SimileMark。
- `bassnet/tempo_map.py`：`tempo_segments`（每小节速度的对数，稳健分段 DP，单小节偏差封顶 8%，每段 ≥ 8 小节，每多一段代价 1.5，整数 BPM，相差 ≤1 的相邻段合并）；`sparse_sync`（最少同步点，使每小节起点偏离两同步点连线 ≤ tol）。
- 校准（55 首）：全部判为单速，与购买谱"单速/多速"一致 51/55（不一致的 4 首是半速/倍速记法或 174→180 渐快）；tol 35 ms 时每首中位 6 个同步点（约 4% 小节）。
- `write_gp(sync_tol=...)`：None = 每小节同步（评测用，时间精确）；正式流程 `pipeline.SYNC_TOL = 0.035`。速度记号 = 各段整数 BPM；同步点的 ModifiedTempo = 到下一个同步点的平均速度，OriginalTempo = 该段记谱速度。
- `find_repeats` 默认风格：`REPEAT_LENGTHS = (2, 4, 8, 16)`，块起点须相对段落起点按块长对齐，`REPEAT_MIN_SAVED = 4`，`REPEAT_ALT = False`。20 首验证歌里只剩 3 首用到反复。
- GP8 核对：Henceforth ♩=158 单一速度、7 个同步点、总时长 3:57 = 音频。
- 仍存在（下一步）：强拍 HMM 会插入孤立 2/4 小节"跳相"（Henceforth 第 11 小节），导致后续段落错 2 拍；段落在前奏过碎。
- 强拍实验（2026-09-28，均未部署，正式参数不变）：跳相/换拍号代价 p_jump 20 + p_meter 12 → 小节错位 验证 5.1→4.8%、测试 2.6→2.2%；贝斯"再进入"证据 `bass_entry_evidence`（`entry_w`）无效果；全曲相位模型 `bassnet/lab/phase_model.py`（HMM 多数相位已 98–100% 正确，模型不更好）；全曲相位锁定 `decode_beats(phase_lock=...)` 验证最好 5.1→3.2% 但测试 2.7→3.2% 变差。结论：Henceforth 式"整段跑偏半小节"是局部修补解决不了的结构问题 → 见 docs/扒谱架构v2.md。

## 19. 2026-09-28：架构 v2 定稿与第 0 阶段启动（压缩对话前的状态）
- 方案文档：`docs/扒谱架构v2.md`（第 9 节为执行版，含 DadaGP 的用途与质量把关）。用户决定：不要易弹版；GPU 任务在工作日空闲时跑；DadaGP 用户认为可信、已获授权（用作符号先验、指法、写谱风格、和弦进行，不当真实录音标签）。
- 数据统一放 `D:\BassData\`（不计入 BassStation 200 GB 额度）：
  - `stems6\<md5>\{bass,drums,guitar,piano,vocals,other}.flac`：`python -m bassnet.lab.build_stems6`（BS-Roformer-SW，overlap 4，44.1 kHz 立体声，约 25–40 s/首，297 首，可续跑），日志 `D:\BassData\stems6.log`。
  - `musdb18hq.zip`：后台 curl 下载中（Zenodo 3338373 公开可下，22.66 GB，约 1.7 MB/s，可 `curl -C -` 续传），日志 `musdb18hq.log`；之后自动下载 `IDMT-SMT-BASS.zip`（Zenodo 7188892，1.58 GB）。
  - `IDMT-SMT-BASS-SINGLE-TRACKS\`：已解压（源 `D:\IDMT-SMT-BASS-SINGLE-TRACKS.zip`）。
  - `D:\moisesdb.zip`：用户浏览器仍在下载（21:40 时 19 GB 且在增长），完成后解压到 `D:\BassData\moisesdb\` 并按 moises-db README 的校验值核对。
  - DadaGP：Zenodo 记录 5624597 公开部分只有论文 PDF，数据需用户提供审批后的访问链接（邮件或 Zenodo "Requests" 页）。
- 下一步（第 0 阶段剩余）：五维评测套件、歌曲档案 JSON 格式、DadaGP 解析；然后第 1 阶段（WSL2 部署 allin1、S-KEY、lv-chordia 零样本评测，调号/升降写法接入写谱）。

## 20. 2026-09-28 夜：第 0 阶段完成、第 1 阶段（调号、和弦、allin1）
- **五维评测套件** `bassnet/lab/song_eval.py`（`--run NAME [--key tab|none|方法]`，`--compare A B`）：val+test 55 首走正式写谱路径（缓存 v3+v4 后验 → `pipeline.build_gp`）后解析回来打分：①忠于录音（对 labels_v2 的音符 F1、对谱逐小节记谱准确率）②结构（小节线 F1、首小节、拍号、速度个数、调号、段落边界 F1）③可读（调外音/100 音、离格音、同步点、反复）④可弹（品位跳动、换把比例、小节内跨度）⑤写出的 GP 留在 `cache/bassnet/song_eval/<run>/` 供试听。基线 `base_tabkey`：音符 F1 0.829、记谱 0.676、小节线 F1 0.942、首小节 0.891、拍号 0.980、段落 F1 0.575、同步点 7.0（谱 2.5）。`pipeline.build_gp` 返回值加了 `bar0_time`。
- **升降写法** `bassnet/spelling.py`：按调号拼写（调内音用调的字母，调外音随调号方向），与购买谱 24.4 万个音一致 99.85%（旧写法全用升号：82.3%）；小调导音升号规则反而更差（谱里 D 小调写 Db），已去掉。`gp_writer` 已接入。
- **调号识别** `bassnet/key_eval.py`（评测）+ `bassnet/key_detect.py`（正式）：全曲色度 + lv-chordia 和弦（原曲上跑，约 3 秒/首），两路证据按签名打分、权重在 297 首上学（`cache/bassnet/key_w.json`）。与谱调号一致 80.5%（10 折），旧检测器（前 90 秒 Krumhansl）63.6%；96% 在一个五度以内。错误多为 ±1 个五度（调式歧义）和 46 首谱内转调的歌。`ai_transcriber.build_complete_tab_project_bassnet` 已改用它（失败回退旧检测器）。lv-chordia 以 `--no-deps` 装入正式 ML Python（另加 pretty-midi、h5py、importlib_resources），numpy/torch 版本未变。
- **反复记号频率**：在谱自身内容上统计，最少省 4 小节会让 27% 的谱出反复（雪鹽子实际 3%），8 → 11.5% 且仍覆盖他 10 份反复谱中的 8 份；`REPEAT_MIN_SAVED = 8`。55 首评测：有反复的歌 10 → 3（谱 2）。
- **歌曲档案** `bassnet/song_doc.py`：`SongDoc`（节拍、小节时刻、拍数、复合拍、首小节、速度段、调号、段落[功能/能量/段内调]、和弦、每小节和弦、乐句家族、来源）+ `from_pipeline` + `validate`；和弦标签解析 `parse_chord` / `chord_pcs`。
- **allin1**：WSL 未装（需管理员+重启），改为 Windows 原生：隔离 venv `cache/venv_allin1`（`--system-site-packages`，madmom 用 MSVC 从 git 编译，allin1 `--no-deps`），NATTEN 用纯 PyTorch 替身（venv 的 `site-packages/natten/functional.py`，按 NATTEN 0.14/0.15 的边界与相对位置偏置规则）。`bassnet/allin1_run.py`（我们的 6 轨 → allin1 的 4 轨：other = guitar+piano+other）→ `cache/bassnet/allin1/`。
  - 零样本（39 首）：24 首跟成半速；强拍 F1 0.656（我们的 HMM 0.837），强拍精确率 0.83 vs 0.85，段落边界 F1 0.55（我们的段落模型 0.575）。激活值 0.3 融合进我们的 HMM 仅 +0.4 点。结论：原样不如现行，需要精调。
  - 精调 `bassnet/lab/allin1_finetune.py`：`targets`（谱的拍、小节线、段落 → `cache/bassnet/l1_targets/`，训练 223 / val 20 / test 35 首）、`train`（从 harmonix-fold0 起，60 秒随机片段，AdamW 2e-4，mask 只算谱覆盖的区间，按 val 强拍 F1 存 `staging/allin1_ft_fold0.pt`）、`infer`。`lab/layer1_eval.py` 用环境变量 `ALLIN1_DIR` 切换零样本/精调结果。
- **和弦** `bassnet/lab/chords_run.py`（venv）：lv-chordia 在 6 轨混合 / 和声轨（`_mix` / `_harm`）与原曲（`--orig`）上 → `cache/bassnet/chords/`。
- **DadaGP**：数据需作者邮件授权后的链接（Zenodo 公开部分只有 PDF，GitHub 只有编解码工具，已克隆到 `D:\BassData\dadagp_tools`）。token 格式不含拍号、调号、段落标记，所以解析直接读 GP 文件：`bassnet/lab/dadagp.py`（PyGuitarPro；贝斯轨音符、同曲吉他/键盘的半小节和弦、底鼓/军鼓、每小节拍号/调号/标记/反复；质量门槛：1 条贝斯轨、≥50 音、B0–G4、≥16 小节）；`parse <根目录>`、`stats`。示例文件测试通过。
- **后台任务**：6 轨分离续跑（22:18 因文件名含奇怪扩展名崩溃，已修，同样的修复也加到了 key_eval / chords_run）；`cache/layer1_loop.sh` 每 15 分钟把新分出的歌跑 allin1、和弦、分轨色度；`cache/overnight.sh` 在分离完成后自动：key_eval 全量 → allin1 零样本评测 → 精调 fold0 40 轮 → 推理 → 精调后评测（日志 `cache/key_eval_full.log`、`layer1_zeroshot.log`、`allin1_ft_fold0.log`、`layer1_ft.log`）。下载改为 `D:\BassData\dl.sh`（任何 curl 错误都续传重试，日志 `dl.log`）。
- **MoisesDB**：`D:\moisesdb.zip` 停在 23.7 GB（22:18 后不再增长，zip 目录缺失 = 未下完），需要用户在浏览器里恢复下载。
- **段落按乐句长度解码**（正式）：`section_model.decode_boundaries_dp`：边界证据 + 训练谱的段落长度先验（`staging/section_len.json`，8 和 16 小节各占约 22%）的半马尔可夫动态规划，取代逐峰值挑边界。55 首：段落边界 F1 0.575 → 0.609，段落长度为 4 的倍数 41% → 55%（谱 59%）。参数 `SECTION_DECODE`，可用环境变量 `BASSNET_SEC_DEC` 覆盖做扫描。
- **和弦契合度**（新指标，`song_eval`）：小节首音落在和弦根音 / 和弦音上的比例，我们 0.850 / 0.897，谱 0.845 / 0.896 —— 声学扒出来的低音已经和和声一样契合，贝斯语言模型的收益不会在小节首音上，而在经过音与含糊处。
- **和弦先验能否纠音高（第 3 阶段可行性预检）**：55 首 val/test，模型音符与 labels_v2 起音匹配 39357 个，音级不同 2242 个、八度不同 1308 个。音级分歧里"谱是和弦音而模型不是" 602 个，"模型是和弦音而谱不是" 848 个 —— 按和弦偏向选音会让与谱的一致率变差（谱里大量经过音、非和弦音）。结论：贝斯语言模型不能用"贴和弦"来改音，只能在声学真正含糊（低置信）的音上、用学到的乐句写法（DadaGP + 雪鹽子谱）做裁决；八度问题要靠演奏习惯（指法/把位、slap 八度型），不是和声。
- **真实 DI 贝斯评测** `bassnet/lab/idmt_eval.py`（IDMT-SMT-Bass-Single-Tracks，17 条带弦/品标注的真实贝斯线，只作评测）：音符 F1 0.845，匹配音的音高 96.8%，我们的指法与演奏者选同一根弦 72.4%（同弦同品 72.0%）。个别曲目 F1 偏低：017（0.56）56 个音里 32 个是泛音（HA），009（0.65）全是闷音拨弦（MU）加揉弦——技法专项的具体靶子。指法 72% 是第 5 阶段指法模型的基线。
- **歌曲档案落地**：`pipeline.build_gp` 每次写谱时把 `SongDoc` 存到 `cache/bassnet/song_docs/<gp 名>.json`（`ai_transcriber` 把 key_detect 得到的和弦一并传入）。
- **真实多轨分离评测** `bassnet/sep_real_eval.py`（MUSDB18-HQ test：SI-SDR + "分离后扒的音符 vs 真干净贝斯扒的音符"F1），`sep_compare.run_separator` 新增 `xlance_bass` / `xlance_dn_bass`（X-LANCE MSR 2025 冠军的贝斯修复模型，权重在 `D:\BassData\restoration\checkpoints`，代码 `D:\BassData\restoration\xlance-msr`）。`cache/overnight2.sh`、`overnight3.sh` 依次排队：压力测试（-9 dB、0 dB）→ MUSDB 解压 → 真实多轨评测。
- **allin1 结论（第 1/2 阶段）**：全库 278 首零样本：强拍 F1 0.736（我们的 HMM 0.874），135 首跟成半速；激活值 0.3 融合 0.888。精调 fold0（6 轮后 val 不再涨，`staging/allin1_ft_fold0.pt`）在 55 首 val/test：强拍 F1 0.858（HMM 0.948），强拍精确率 0.966（最高）但首小节只有 0.69；融合 0.3：强拍 0.954（+0.6）但首小节 0.891 → 0.855；用它的强拍给整首歌投票定相位（`layer1_eval.phase_vote`）无变化；段落边界 0.583（我们的 DP 0.609）。**不采用**：我们在本领域数据上训练的节拍模型已强于通用模型，剩下的错误是局部的（个别段落的相位、乐句起点），不是整首的相位。allin1 的环境、脚本、精调结果保留，可在以后数据更多时再试。
- **X-LANCE 贝斯修复结论（第 4 阶段第一步）**：压力测试 −9 dB 配对 15 组（3 首 × 5 种音色）：音符 F1 BS-Roformer-SW 0.865 vs X-LANCE 0.806，15 组里 12 组更差、0 组更好；音高 0.950 vs 0.935。**不采用**。修复模型必须以"扒谱准确"为目标自己训练（第 4 阶段第二步，需要 MoisesDB/MUSDB 真实分轨）。
- **网络**：凌晨约 01:50 起所有境外 HTTPS（zenodo、huggingface）握手失败，国内正常——多半是代理断了。MUSDB18-HQ 停在 17.9/22.66 GB，`D:\BassData\dl.sh` 每分钟重试（最多 3000 次），网络恢复后自动续传并在完成后自动解压、跑真实多轨评测（`cache/overnight3.sh`）。
- **最终五维评测**（`song_eval --run final_0929 --key fused`，调号用 10 折交叉的真实检测结果）vs 昨天的 `base_tabkey`：段落边界 F1 0.575 → 0.609，有反复的歌 10 → 5（均值 0.35 → 0.13，谱 0.09），调号与谱一致 87%（这 55 首），音符/节拍/首小节不变（本轮没动声学与节拍）。写出的 GP 在 `cache/bassnet/song_eval/final_0929/`（无伴奏音频，只看谱面）。

## 21. 2026-09-29 晚
- 夜里的后台任务在机器重启后全部中断；MUSDB 已用 `D:\BassData\dl.sh` 续传。MoisesDB 下载页要填姓名/邮箱/单位表单（不代填），请用户在浏览器重新点下载并把下载地址给我，用 `curl -C -` 从已有的 23.7 GB 续传。
- **首小节 / 强拍问题的定位**（`layer1_eval` 逐小节相位串）：错误主要是①整段的半小节相位翻转（Henceforth：20 小节错 2 拍 → 36 小节对 → 34 小节又错），②前奏无拍区后的弱起被当成 3 拍相位，③少量真实变拍号的歌（The Whole Blue World）。精调后的 allin1 恰好能纠正②，与我们的错误互补。
- **上线：allin1 强拍只作"小节从哪开始"的投票**（只融合强拍激活 0.3，拍子仍用我们的）：首小节正确 0.891 → 0.927，强拍 F1 0.942 → 0.946，记谱 0.676 → 0.678（`song_eval final_a1ft`）。拍子和强拍都融合时首小节反而变差，所以只融合强拍。app 走独立 venv 子进程（`allin1_run --stems4 --ckpt --out`，约 18 秒），缺环境或出错自动跳过。
- **上线：段内转调**（`bassnet/key_local.py`）：窗口 12 秒，每窗口用与整曲相同的证据（色度 + 和弦），12 个调号上的 Viterbi，换调代价 6；转调点吸附到最近的段落起点（`section_keys`），`gp_writer.write_gp(bar_keys=)` 逐小节写调号、按该调拼写，反复记号不跨调号；歌曲档案的段落带 `key`。321 份谱的窗口级：有转调的歌 49% → 76%，单调歌 84.6% → 84.4%，8.8% 的单调歌会多出一次转调。五维评测（55 首）：逐小节调号 85.5% → 90.3%，有转调的歌 62.6% → 89.3%。`song_eval` 新指标 `bar_key_ok`，环境变量 `SONG_EVAL_LOCALKEY=1`、`SONG_EVAL_ALLIN1=目录:权重`、`SONG_EVAL_HMM=json`。
- MoisesDB：用户给的签名链接（7 天有效，存于 `D:\BassData\moisesdb_url.txt`）从 23.7 GB 断点续传（已核对续传点前 1 MB 与服务器一致），总 88.8 GB；`D:\BassData\dl_moises.sh` 下完后校验 md5（README：13cf74eda129c38b914a51ea79fb1778）并解压到 `D:\BassData\moisesdb\`。
- **温度/功耗**：用户在驱动里把显卡限到 180 W，要求温度不要持续 15 分钟以上超过 85°C。`bassnet/thermal.py`：`guard()` 在训练/批处理循环里每 5 秒读 nvidia-smi，≥83°C 暂停到 77°C 以下（`BASSNET_MAX_TEMP`；功耗阈值 `BASSNET_MAX_POWER` 默认不限）；`lower_priority()` 低于正常优先级 + 4 线程。已接入 `restore_train`、`sep_stress`、`fingering_nn`。
- **分离精调（第 4 阶段）进展**：`bassnet/restore_train.py`（`cache` → `D:\BassData\restore_cache\`，MUSDB 150 首；`train`：只训 BS-Roformer-SW 的贝斯掩码头（可选最后两层），梯度检查点，6 秒片段约 1.8 GB 显存；X-LANCE 仓库里 BSRoformer 的检查点分支漏了两行，已在 `D:\BassData\restoration\xlance-msr` 本地修补）。
  - MUSDB test 上的现状：分离后扒的音符 vs 真干净贝斯扒的音符 F1 0.8275，SI-SDR 9.34 —— 真实歌曲里贝斯分离损失约 17% 音符。
  - 两轮精调（强增强 + 后两层 lr 2e-5；温和增强 + 仅掩码头 lr 5e-6）在 MUSDB test 上都从第 1000 步起变差（0.811 / 0.813）。怀疑 SW 训练时见过 MUSDB（含 test），MUSDB 不能当验证集。改为用我们自己 val 歌曲的原曲分离后扒谱、对 labels_v2 的音符 F1 作选择标准（`validate_lib`，原曲缓存 `D:\BassData\restore_libval\`）；`sep_compare` 新增 `sw_bassft` / `sw_raw`（同一分块推理，公平对比）。基线：原版 SW 在 10 首 val 歌上 `validate_lib` 音符 F1 0.8228（约 11 分钟一轮，所以 `val_every` 改为 1500）。下一步：`python -m bassnet.restore_train train --steps 9000`（日志 `cache/restore_train_lib.log`）。
  - **找到了前两轮变差的主因**：训练时模型处于 train 模式，配置里的 attn/ff dropout 0.1 在冻结层上一直开着，掩码头学的是被 dropout 扰动过的特征，推理时（无 dropout）就对不上。改为整段训练用 `model.eval()` 后：1500 步 loss 0.48 → 0.25，MUSDB 音符 F1 0.8291（原版 0.8275），自家曲库 0.8221（原版 0.8228）—— 从"变差"变成"持平"，继续训看能否超过。
  - 修正 dropout 后的第一轮（只用 MUSDB）：3000 步 MUSDB 0.8341（+0.7 点），但自家曲库 0.8216 → 4500 步 0.8203，一直没超过原版 0.8228 —— 西方曲目的伴奏和我们的日系乐队不一样。4500 步时停掉，无检查点。
  - 第二轮（凌晨 01:50 起，`restore_train_r2.log`）：训练曲 336 首（MUSDB 100 + MoisesDB 236）+ **40% 的片段用自家曲库训练曲的"去掉贝斯的乐队"做伴奏**（6 轨分离的鼓/吉他/钢琴/人声/其他，`lib_<md5>_rest.npy` 240 首，val/test 歌曲排除），15000 步，只有自家曲库 F1 超过 0.8228 才存 `staging/sw_bassft_r2.ckpt`。
  - **第二轮结果（05:01 结束）：无提升，未保存检查点。** 15000 步里自家曲库音符 F1 在 0.8197–0.8216 之间（原版 0.8228），MUSDB 0.824–0.834。结论：拿"干净分轨"为目标去精调 BS-Roformer-SW 的贝斯输出，对我们的扒谱没有帮助（曲库上 ±0.3 点属噪声范围，且一直略低）。分离路线到此为止，不再用这种方式继续投入 GPU。
  - 真正的 17% 差距（MUSDB：分离后扒 vs 干净贝斯扒）若要缩小，可能的方向是让**扒谱模型**直接学会从分离音频里还原"干净贝斯能听出的音"：用 MUSDB/MoisesDB 的 336 首真实多轨，以"干净贝斯上扒出的音符"为教师标签、以分离后的贝斯为输入训练 BassNet。但这等于用模型输出当训练标签，违反"不用 AI 输出训练"的原则，需用户决定。
  - IDMT-SMT-Bass 单音技法库已解压到 `D:\BassData\IDMT-SMT-BASS\`（5306 个 wav；文件名 `BS_<琴>_EQ_<eq>_[PS_]<拨弦>_[ES_]<表情>_<弦1-4>_<品0-12>`）。**技法评测** `bassnet/lab/idmt_tech_eval.py`（每类抽 40–60 个）：
    - 音高：指弹/拨片/闷音/slap 拇指/slap 勾弦/揉弦/滑音/推弦 0.92–1.00（快推弦 BEQ 0.68）；泛音 0.00（预期内）。
    - 闷音（DN）只检出 7%，几乎全漏。
    - 技法标记在所有类别上都是 0：`tech_thr.json` 里除 slap（0.15）外阈值全是 1.01（等于关闭），而 slap 拇指单音上 slap 后验最大只有 0.03。**技法识别目前实际上是关着的/不工作**，这是第 5 阶段"技法专项"的起点（IDMT 许可只能评测，训练标签需另找：谱里的技法标记 + 用户核对版）。
  - **技法专项分类器试验** `bassnet/lab/tech_note.py`（只看每个音的起音：以音高为 0 号格重排的 CQT 8 帧 + 频带亮度/突变，4 类 plain/slap/pop/dead，谱标签训练曲 686 slap / 379 pop / 1775 dead）：val 谱 slap P 0.35 R 0.58、pop P 0.49 R 0.78、dead P 0.23 R 0.63；IDMT 真实单音上 slap 拇指只认出 5%、勾弦 1%，指弹误判闷音 31%。**不采用**。技法样本太少（slap/pop 只有 37 首歌），且分离后的贝斯与 DI 单音差异大；要做好需要更多带技法的真实标签（用户核对版回流、DadaGP 里带 slap/pop 标记的贝斯谱可做合成）。
  - **自有谱格式已接入**：`pipeline.build_gp` 写完主 GP 后调用 `score_format.write_score`（另一个会话写的 `backend/score_format.py`），生成 `<GP名>.score.json`（核对版/4 弦版不写）；每音带 `time/end`（录音秒，80 ms 内匹配原始解码音）、`conf`（min(conf, on_peak)）、`cand`（起音处后验前三 [midi, p]），顶层 `model`（models.json 哈希、各角色权重、`audio_md5`（`transcribe_stem` 里算，混音字节 md5 前 16 位）、代码日期）。失败只打 `[score_format] skipped`。对方已在 WPF 编辑器里读写验证通过；编辑记录 `edits.jsonl` 支持 add/set/delete/set_rhythm/review（bars 左闭右开 + 录音秒 t）/undo，只有 review 过的区间才算真值。
  - **演示谱**（全部新改动，含伴奏，可直接用 GP8 打开）：`cache/bassnet/demo_0930/<Henceforth|My Dearest|Velonica|転生林檎>/[BASS TAB] *.gp`（及核对版）。四首调号都与谱一致（3♯ / 0 / 4♯ / 1♭），都只有一个速度（Henceforth 158）；Henceforth 在第 103 小节（尾奏）检测到转到 5♯，谱里没写转调——可能是最后副歌升调，也可能误判，请用户听判。
  - 与"项目功能审查与优化"会话（负责 WPF/评测）对齐了自有谱格式：它新建 `backend/score_format.py` 和 WPF 谱面视图，不碰 pipeline/gp_writer/quantize/song_doc；我在 `build_gp` 里加一行调用并提供每音的录音时间/置信度/模型版本/音频 md5；回流标签需要"已审核"标记（没改的音不等于确认过）以及小节线/速度/段落修改记录。
  - MoisesDB 下完（88.8 GB），md5 与 README 一致（13cf74eda129c38b914a51ea79fb1778），自动解压中。`cache/night_restore.sh`：第一轮结束后（有更好的检查点才）做压力测试 A/B（`sw_raw` vs `sw_bassft`，−9 dB），MoisesDB 解压后重建缓存、第二轮 15000 步（`staging/sw_bassft_r2.ckpt`，日志 `restore_train_r2.log`、汇总 `night_restore.log`）。
- **指法序列模型**（`bassnet/lab/fingering_nn.py`，双向 GRU 对每个音的可弹弦打分）：test 谱同弦率 0.8630 vs 现行 Viterbi 0.8631，IDMT 0.727 vs 0.715 —— 持平，不采用。87% 左右可能就是谱本身的一致性上限；等 DadaGP 再试。

## 22. 2026-09-30：练习闭环（段落 / 慢速录制、录制历史）+ 评测器修复 + 代码审查
- **评测器 bug（已修，`performance_evaluator.py`）**：起音强度是对数 mel 通量，与音量无关；音高检查也是相对值 → 底噪会被判成弹奏。纯底噪带 `--lag-hint` 得 65 分；"停了手但录音没停"的后半段全判 PERFECT。修复：① 电平门限 `lvl`（贝斯音区 CQT 线性峰值）：第一遍 ≥ 录音 95 分位的 5%，第二遍 ≥ 已匹配拨弦中位数的 10%（闷音减半），多余音检测同样过门限；② `_pitch_ok` 加 `TONAL_MIN=1.35`（目标音高显著度 / 音区中位数；底噪约 1.0，真实音符 1% 分位 ≥ 1.6）。回归 `cache/eval_test/baseline_20260930.txt` vs `after_20260930.txt`：只有 s_errors 一个错音 GOOD→BAD、missing 多一个 MISS（均更准），纯底噪现在报"不匹配"。
- **最高分 bug（已修）**：录一半就停 / 只弹前奏也会覆盖全曲最高分。现在每次录制都记入新表 `performance_takes`（分数、覆盖率 `coverage`、段落 `range_start/end/label`、速度 `rate`、`complete`、`new_best`、报告路径、各段落成绩 `sections_json`）；`performance_scores`（全曲最高分）只接受"全曲 + 100% + 覆盖 ≥ 90%（`FULL_COVERAGE`）"的录制；每个（段落, 速度）组合各自记最高分（`save_take`）。
- **段落 / 慢速录制**：`eval_session.py --sections <gp>` 列出段落（与报告同源：`eval_report.song_sections`，GP 段落标记，同名相邻标记合并，如 Henceforth 每 4 小节重复标的 "Intro+A+B+A2"；无标记按 8 小节分组 "9–16 小节"；< 8 音的段落不列出）；`--range A B --rate R` 生成该段的预备拍 + 去贝斯伴奏（ffmpeg atempo 变速不变调）+ 节拍器，返回时间均为变速后的秒数（`lead_s` 已除以 rate，WPF 的 `hint = RecordOffset − lead_s` 公式不变）。去贝斯伴奏缓存为 `cache/eval_session/<gp键>_nobass.flac`，之后各段落秒开。评测器 `--range A B --rate R --label 名`：只评该段音符（最少 8 音 `MIN_RANGE_NOTES`），音符时间 ÷ rate。全曲 100% 输出与旧版逐样本一致（仅 FLAC 量化 1 LSB）。
- **测试** `cache/eval_test/section_cases.py`（临时库副本）：ALIVE C 段 100/80/70% 干净合成 → 全 PERFECT、coverage 1.0；80% 伴奏按原速弹 → 37.6 分且 coverage 0.84 不计最高分；只弹 10 秒 → coverage 0.22 不计；整曲前 40 秒 → coverage 0.41 不计。WPF 真实入口：`BASSDREAM_SECTION=C BASSDREAM_RATE=0.7 BassStation.exe --render-eval-run out.png <录音> "ALIVE _ Morfonica"`。**未做**：带声卡的真实录制（会外放，夜里没测）；`TakeRecorder` 流程与全曲相同，只是参数不同。
- **WPF**：评测浮层底部 = 选择录音 / 输入 / **段落**（全曲 + 各段，右侧显示该段最近一次成绩等级）/ **速度**（100–60%）/ 开始录制；原"点评"卡改为**录制记录**（时间、段落·速度、分数、等级；未完成的半透明），点一行打开当次结算页 → 详细报告。段落录制的结算页标题带"· C · 70%"，报告只显示该段小节（`EvaluationReport.Crop`）。选择录音也按当前段落/速度评测。软件内录制的时长计入练琴日历。全局 `ContextMenu/MenuItem` 样式（白卡、粉点）。调试：`BASSDREAM_SONG=<曲名> [BASSDREAM_MENU=section] BassStation.exe --render-sheet-eval out.png`（菜单另存 `out.png.menu.png`）。
- **代码审查修复**：主界面拖入 GP 导入、PDF 导出（ensure-pdf）、后台伴奏队列（ensure-backing）都重定向了 stderr 却不读 → 日志多时管道写满卡死（添加曲目浮层早先已修过同一问题）。统一改用 `Services/TabCli.Run`（并发读 stderr、超时杀进程树、取最后一行 JSON）。主界面拖入**音频**文件 → 打开添加曲目浮层并直接扒谱（原来无反应）。
- 备份：`cache/code_backup_*_20260930.py`（评测三件套）、`cache/data.db.before_takes_20260930`、`cache/code_backup_20260930_app/`（旧 exe/dll）。
- 已知遗留（未改）：`DatabaseService.AddSongRecordAsync` 写死 franchise 'BanG Dream! & Custom'，但目前无调用方（死代码）；`tab_cli search-all` 输出无 status 字段。

## 23. 2026-09-30 下午：自有谱格式 + 软件内谱面（查看 / 播放 / 编辑）
- 用户要求"内置一个简版 GP8"，并与扒谱会话协调。结论（双方一致）：格式和排版规则本身不给训练加信息；有价值的是 ① 不经 GP 往返、每个音带录音时间的真值；② 结构化改谱记录 + **审核标记**（没改的音 ≠ 确认过，只有审核过的区间算真值）。排版是"音符 + 小节网格 → 显示"的确定性函数，不回写标签；写法风格仍以购买谱统计为准。
- **格式** `backend/score_format.py`（文档在文件头）：`scores/<song_id>/score.json` + `edits.jsonl`；960 tick/四分；小节按演奏顺序展开（反复只是显示），每小节带 `t0/t1`（录音秒，来自 `score_time_map` = bassnet 对齐标签）；拍 = 节奏（v/d/t）+ 音（s/f/midi/tie/x/slide/hopo/slap/pop/src/conf/flag）；未知字段（模型的 time/end/cand、song_doc、小节 key）在 WPF 保存时原样保留（`JsonExtensionData`）。
  - `open <id> <gp> [title artist]`：首次从 GP 导入（`eval_report.build_report` 的逐拍读法 + gpif_parser 技法；空小节补整小节休止）；GP 旁有 `<名>.score.json`（模型输出）时直接采纳。准备音轨 `cache/scores/<id>/backing_1.00.wav`（去贝斯，复用 eval_session 缓存）+ `bass_1.00.wav`（原混音 − 去贝斯伴奏）。`stretch <id> <rate>`：atempo 变速副本。
  - `write_score(out_gp, qs, doc, tuning, key, bar_keys, sections, notes_meta, model_info)`：给扒谱管线调用（由扒谱会话在 `pipeline.build_gp` 里加一行；节奏拼写直接用 `gp_writer.build_bar_events`，与 GP8 显示一致）。验证 `cache/score_test/model_score.py`：Henceforth 158 小节全部填满，1146 音与量化器一致。
- **WPF** 舞台右下「谱面」→ `Views/ScoreView`（全窗）+ `ScoreCanvas`（TAB 排版、选中格、低置信虚线框、用户改动蓝字、已审核绿底、小节未填满红底）+ `Services/ScorePlayer`（NAudio 实时混音：伴奏 / 原贝斯为变速 wav，谱面合成音与节拍器按谱实时生成，任意速度；段落循环）。
  - 键：空格 播放/暂停；Enter 从选中处播放；方向键移动；数字输入品位（800 ms 内两位数）；Del 删除；X 闷音；Ctrl+↑↓ 同音换弦；- / + 拍子减半（补休止）/ 吞并后面的休止；Ctrl+Z 撤销；Ctrl+S 保存（退出时自动保存）；Ctrl+R / 按钮「审核本段」。双击从该处播放。
  - edits.jsonl：`{seq, ts, op: add|set|delete|set_rhythm|review|undo, bar, note_id, before/after{id,s,f,midi,x,src,conf,tick,dur,time,end}}`；review 带 `bars:[a,b)` 与时间范围。
  - 验证（离线混音 `BASSDREAM_MIX="<wav>|rate|from|sec|vb,vbass,vsynth,vclick" BassStation.exe --render-score out.png <曲名>`）：原贝斯轨与源文件逐样本一致（100% / 80%）；合成音起音 vs 谱面时间中位误差 1–2 ms；节拍器间隔随速度正确缩放。调试编辑：`--render-score out.png <曲名> <bar> <beat> <string> <fret>`。
- **未做 / 待定**：导出 GP（需把 score.json 转回 QScore 走 gp_writer，与扒谱会话商量）；小节线 / 速度 / 段落编辑（op set_bar/set_tempo/set_section，扒谱会话要这些做第一层标签）；插入/删除拍、连音编辑；真机外放试听。**视觉风格用户要单独讨论**（"邦味 + 简约工业风"，同时要改报告页 TAB），ScoreView 目前只是功能骨架。
- 自动模式"安全检查无结论"：根因很可能是全局配置里的第三方 API 中转；已加项目白名单 `.claude/settings.local.json`（Python/dotnet build/调试渲染/只读命令）。
- 追记（同日）：扒谱会话已在 `pipeline.build_gp` 写完 GP 后调用 `write_score`（try 包裹，失败只打 "[score_format] skipped"）；`pipeline.notes_meta` 提供 time/end（±80 ms 内的原始解码音，否则网格插值）、conf=min(conf,on_peak)、cand（起音处前 3 音高 [[midi,p],...]）；`pipeline.model_info` = {models_json 哈希, roles, audio_md5（app 路径才有）, code}。只有主 GP 写 score.json（核对版、4 弦版不写）。用它的 smoke 输出验证 WPF 往返：改 1 音保存后其余字段逐项一致。修了 C# 序列化（值为 0 的必填字段被省略、计算属性被写出）。`open_score`：GP 旁的 score.json 比工作副本新且未编辑过时自动采纳新输出。
- 追记（2026-09-30 晚）：用户反馈谱面伴奏全是高频噪音。离线渲染干净，真实声卡回录（`BASSDREAM_LIVE=1` + `BASSDREAM_MIX`，WasapiLoopbackCapture）与原伴奏相关度只有 0.12。原因：44.1 kHz 浮点流交给 48 kHz 设备由 Windows 自动转换（NAudio WasapiOut 共享模式）。现在 ScorePlayer 与 TakeRecorder 都先用 WdlResampling 重采样到设备 MixFormat，修复后相关度 0.993（100%）/ 0.995（80% 全音轨）。演奏评测的录制播放也受同一问题影响，一并修复。

## 22. 2026-09-30 晚：小节相位按整首歌判断、滑音、老师—学生训练
- **Henceforth 开头差两拍的根因**（`bassnet/phase_audit.py` 逐小节相位串 + 逐拍证据）：主歌是 F#m–D–A–E 每两拍一换，贝斯每两拍换音，局部听第 1、3 拍完全一样；强拍网络在前 20 小节"很有把握地"押错（第 3 拍 0.85、第 1 拍 0.25），副歌（一小节一换）才转对。旧 HMM 把重复 20 遍的同一乐句当成 20 份独立证据，压过了"不该有奇数小节"的先验。
- **上线**：`decode.repeat_weights`（每拍证据权重 = 同样 4 拍贝斯乐句出现次数^-0.5）+ `BEAT_HMM = {p_jump: 12, p_meter: 12}`，入口 `pipeline.song_beats`（app 与 song_eval 共用）。allin1 融合关闭（叠加后首小节反而 0.927→0.909）。上下文强拍分类器 `bassnet/downbeat_ctx.py`（梯度提升，前后半小节/一小节的贝斯、和弦、强拍后验）试过，无效，未采用（`decode_beats(down_fn=)` 接口保留）。
- 曲库事实：谱里第一个贝斯音在第 1 拍只有 59%（不能当"歌曲起点"硬规则）；18% 的谱至少有一个奇数小节。
- 演示谱已按新代码重生成（`cache/bassnet/demo_0930/`）：Henceforth 152 条小节线全部对上，全曲 4/4、速度 158。
- **规则变更（用户 09-30）**：模型输出可以当训练标签，前提是数据可信、不会造成虚假准确；评测必须用独立标签。
- **老师—学生**：`bassnet/ext_build.py` 把 MUSDB（train 100 + test 50）与 MoisesDB 236 首做成 feats 格式（`cache/bassnet/feats_ext/`）：输入 = 正式分离模型（SW，overlap 8）分出的贝斯 CQT + 混音 mel；标签 = 现有 v3+v4 在**干净贝斯**上扒出的音符和节拍；MUSDB test 标 `ext_split: val` 不参与训练。`train.py` 新增 `--init`（从已有权重微调）、`--extra DIR`、`--extra-p`。计划：v4 微调 12 轮，对照组只用曲库、实验组加 30% 外部歌，均用 labels_v2，在曲库 val/test 上比较。
- **滑音**：`bassnet/lab/slide_note.py`，在贝斯分轨细分辨率 CQT（每半音 3 格，和声求和 + 抛物线插值）上跟踪每个音开头/主体/结尾/结尾后的音高、到下一个音的滑行、下一个音起音强度，每类滑音（连音滑、音尾下滑、音尾上滑、从下滑入）一个梯度提升分类器，用谱里的滑音标记训练。谱中频率：音尾下滑 1.17%、连音滑 0.79%、音尾上滑 0.78%、从下滑入 0.35%。

## 23. 2026-09-30 晚：找回被算法吃掉的部分
- **损耗审计** `bassnet/loss_audit.py`（55 首 val/test，参照 labels_v2，音符 F1）：解码 0.834 → 量化 0.832 → 规整 0.8315 → 写出 GP 0.829。解码之后几乎不掉分，损耗集中在解码本身：漏掉 2224 个音（约 1700 个模型其实听到了音高：起音峰 0.25–0.5，或同音重复拨弦根本没有峰）、音高错 3520 个（八度 1298、正确音是第二候选 866）、多出 3011 个。
- **上线：候选起音复核** `bassnet/onset_rescore.py`：所有候选起音（峰 ≥0.08 + 音高切换）用梯度提升判别（起音强度/突出度、前后音高及把握、休止、贝斯分轨在该音高上的能量回升、节拍网格位置、相邻候选距离），阈值 `pipeline.ONSET_RESCORE = 0.5`（环境变量 `BASSNET_ONSET_RESCORE`，0 = 关闭）。val/test 音符 F1 0.834 → 0.839（精确率 0.831 → 0.853），训练曲交叉验证 0.807 → 0.812，143 首变好 / 52 首变差。取舍：只看召回的"记谱准确率"降 0.5 分（多余音被去掉）。`decode_notes(onsets=)` 新接口；`pipeline.decode_song` 统一正式与评测。
- **上线：变拍号探测** `pipeline.song_beats`：先用宽松设置解一遍，若出现连续 ≥8 个 3 拍小节（真有 3/4 段落），改拍号代价保持 4，否则 12。278 首小节线正确率 97.15% → 97.30%（纯 4/4 98.6%、3/4 段落 93.5%、6/8 等复合拍 87.9%，都不低于改动前）。「1」恢复全对，Henceforth 仍全对。
- 剩余小节问题主要是**复合拍与少见拍号**（6/8 整首被写成 12/8 或跟成半速、5/4、6/4、9/8），以及个别 3/4 段落（雑踏）——需要支持复合二拍（6/8）与更多拍号的解码，是下一步。
- **五维评测**（`song_eval prod_1001`，全部新改动）vs 09-29 正式版：音符 F1 0.829 → 0.833、小节线 0.968 → 0.976、强拍 F1 0.946 → 0.953、首小节 0.927 不变、离格音 0.40 → 0.28 /100 音。
- **滑音**：`lab/slide_note.py` 手工轮廓特征 + 梯度提升，以及音高相对 CQT 片段的小 CNN（`slide_note.py patches|cnn|cnn_eval`），对谱面滑音标记的平均精确率仅 0.1–0.29（音尾下滑最好：阈值 0.9 时 P 0.36 R 0.40）。原因：①谱里约 80% 的"音尾下滑"紧接下一个音，声音上与直接换低音难分；②IDMT 的真实滑音（一个半音的连音滑）被解码成一个音，滑到的新音根本没写出来。**未上线**。可行方向：在细分辨率音高轨迹上检测音符中途的持续音高移动并切分成"连音滑"两音；滑出类标记可在核对版里作为建议给用户确认。
- **显卡**：用户的 180 W 驱动上限重启后失效（现在 225 W）；`thermal.py` 默认按 180 W 节流，并新增 `hook_module` 在分离模型每块计算前检查（`ext_build` 已接入）。

## 24. 2026-09-30 晚：谱面页改为只看只播 + 清理
- **需求变更（用户）**：软件内谱面**不做改谱**，改谱交给 GP；软件内只做内置看谱与播放。§23 里的编辑相关内容（数字改品位、Del / X / Ctrl+↑↓ / -+ / Ctrl+Z、审核本段、edits.jsonl、用户改动蓝字 / 已审核绿底、保存按钮）**已全部移除**，§23 "未做 / 待定" 里的小节线 / 速度 / 段落编辑、插入删除拍、连音编辑、导出 GP 都**取消**。
- **保留**：播放（伴奏 / 原贝斯 / 谱面合成 / 节拍器混音）、变速、段落或选区循环、伴奏对齐（± 步进、自动；偏移写入 `score.json` 的 `meta.audio_offset_ms`，退出时自动保存）、点击选格、双击从该处播放、低置信度虚线框。键：空格 播放/暂停 · Enter 从选中处播放 · 方向键移动 · L 循环 · Esc 返回。速度 / 对齐输入框聚焦时按键归输入框（此前会被谱面吞掉，数字会改写品位）。
- 后端 `score_format.py`：`open_score` 只要 GP 旁的 `.score.json` 比工作副本新就采纳（不再看 edits.jsonl）；`edits.jsonl` 不再产生。扒谱会话原计划用 edits 回流标签，这条路径没有了，回流改由 GP 里核对后的谱（核对版）提供。
- **已删除**：`frontend/`（React Web UI）、`desktop/`、`desktop_app.py`、`start_bass_station.bat/.vbs`、`启动BassStation.bat`、`AI_HANDOVER.md`、WPF 的 `LogoComparisonWindow` / `LogoControl` 与 `--render-logo` 调试入口、`backend/test_*.gp`、`backend/ai_transcriber_test.py`、未被引用的旧 logo 素材（根目录和 `assets/` 下的 `bangdream_*`、`bassdream_plain.svg`、`bassstation_*`）。`backend/app.py`（FastAPI）及只被它引用的 `clipboard_service` / `fretboard_service` / `gp_process_monitor` 也已删除（用户决定整体去掉 FastAPI；练琴监控、剪贴板导出由 WPF 自己做）。`gamification_service`（徽章 / 熟练度表，WPF 直接读）和 `practice_history`（练习记录表）保留，建表改由 `tab_scanner.init_db()` 调用，新库不会缺表。
- 邻近会话在本机未推送的进行中工作（云端看不到）：报告页判定彩带改版（中性数字 + 连续彩带，PERFECT 幻彩，MISS 断开虚线，错音变紫）、便签与选中小节高亮、小节重放控制条（我的演奏 / 原曲贝斯 / 交替对比）。最后停在核对"原曲贝斯"重放位置比预期早 0.38 秒（怀疑重复段落导致误判），需在本机确认。
- 云端能做的验证：`dotnet build -c Release -p:EnableWindowsTargeting=true` 可编译 WPF（Linux 上只能编译，无法运行界面和声音）。

## 25. 2026-09-30 夜：开机动画、界面准则 skill、架构整理

### 25.1 用户决定（审阅页 https://claude.ai/artifact/3mQGz2eDaiAYNaJUcZce6i）
- 界面**保持现状**，不换风格。上一轮“为什么飘”的诊断作废：用户说的“飘”是**看起来不像人做的、没有落地感**，而那一版方案本身也是 AI 网页式设计。准则见 `.claude/skills/human-made-ui/SKILL.md`（从隐喻和参照截图出发、按素材逻辑画：统一光源 / 接触影 / 材质 / 描边、稳定感、动效只说明去向、交付前自查清单）。**改任何界面前先加载这个 skill。**
- 品牌粉统一为 logo 原色 **#E50050**。
- 开机动画每次启动都播放；Songsterr 保留维护；屏幕 1920×1080 100%。

### 25.2 开机动画（`Views/BootOverlay.cs`，3.75 秒，点击或按键跳过）
- 首帧 `src-native/assets/boot/splash.png`（纯白）由 WPF `SplashScreen` 在 .NET 启动前显示，与动画第一帧一致。用户试过把 M#265 描成背景板，觉得怪，已去掉（描图文件在 git 历史里）。
- B a S S 依次从左侧抛物线飞入 → Dream 从右侧冲入撞线（1.54 s），边界线在撞击瞬间从撞击点沿 B 左边界向上下伸出（上端与 B 顶齐平，左侧向左渐浅的光晕）→ 闪电、星、バンドリ → 碎裂成约 5 千片（`WriteableBitmap`）流向主界面 logo 的真实坐标，主界面渐显。
- logo 路径数据 `assets/boot/logo_glyphs.json` 已规整为 WPF 可解析的绝对命令（`assets/boot/normalize_paths.py`，与原图逐像素一致）。资源出错时直接跳过动画。
- **未在真机验证**。检查单帧：`BassStation.exe --render-boot out.png 1.56`。需确认：SplashScreen 与主窗口位置是否完全重合、动画帧率、碎屑落点是否正好是左上角 logo。可调常数都在 `BootOverlay` 顶部（时间轴）。

### 25.3 架构（均已提交）
- **路径**：`backend/paths.py` 与 `src-native/Services/AppPaths.cs`。根目录由代码位置推算（WPF 从 exe 向上找 `backend/tab_cli.py`），项目根的 `bassdream.json`（可选，模板 `bassdream.example.json`）覆盖单项：`root / python / python_ml / originals / ffmpeg / guitar_pro`。圆体字体改为运行时从 `assets/fonts` 加载。
- **调试入口**：全部 `--render-*` 移到 `DebugTools/RenderHarness.cs`，`App.xaml.cs` 只剩启动逻辑。
- **颜色令牌**：`Themes/Palette.xaml`（XAML 用 `{StaticResource BrandBrush}` 等）+ `Services/Palette.cs`（C# 用 `Palette.Brand`、`Palette.Tier(tier)`）。XAML 里 409 处十六进制色 293 处改为色名；旧名 `PinkPrimaryBrush` 等保留为别名。字号、圆角**未收敛**（会改变外观，用户要求保持现状）。乐队代表色、判定色不在令牌里。
- **常驻 Python**：`backend/worker.py` + `Services/PyHost.cs`。`score_format / eval_session / performance_evaluator` 在常驻 Python311 里按原命令行运行（启动时预热 1 个，最多 3 个并行；超时结束该进程；起不来退回一次性进程；协议流与子进程输出隔离）。`tab_cli.py` 仍是一次性进程（Python314）。
- **实时变速**：`Services/StretchProvider.cs`（NuGet `SoundTouch.Net` 2.3.2，LGPL-2.1）。谱面页直接读 1.00 原始 wav 实时变速，不再调 `score_format.py stretch`（报告页重放仍用）。参数与提前量补偿由点击音轨实测确定：伴奏与节拍器起音差各速度平均 ≤0.3 ms，单个 ±5 ms。
- **数据库**：`backend/schema.py` 是 data.db 全部 9 张表、后加列、索引和 `PRAGMA user_version` 的唯一定义；各模块的 init 函数改为调用 `schema.ensure`。WPF 的 favorites 表与 song_cache 身份列是镜像，改表时两边一起改。
- **研究脚本**：39 个只在实验里用的模块移到 `backend/bassnet/lab/`（按软件入口的真实 import 追踪判定），运行 `python -m bassnet.lab.<名字>`；本文前面各节的路径已同步。
- **窗口**：工作区小于 1440×810 时整体等比缩小。

### 25.4 暂缓（等用户推送本机未提交的改动后再做）
- 统一谱面排版引擎（ReportView 与 ScoreCanvas/StaffDrawer 合一，报告页也有五线谱）、报告页分层渲染、拆分 MainWindow。原因：另一个会话在本机改了 ReportView / 评测结算页（判定彩带、便签、小节重放）且未推送，现在重写这些文件会和那批改动大面积冲突。

### 25.5 已知问题
- `backend/ai_transcriber.py` 约 784 行引用未定义的 `min_allowed_midi`（旧版扒谱器，此前就存在）：走到 PYIN 下 19 半音修正分支时会 NameError。

