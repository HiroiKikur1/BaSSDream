> ⚠️ 本文档已过时（2026-09-26）。最新交接请看 `docs/HANDOVER.md`。

# BassStation 贝斯工作站 — 项目交接与技术白皮书 (AI Handover Document)

> 本文档专为后续接手的 AI / 开发者编写，凝聚了本项目完整的系统架构、目录拓扑、算法演进、关键成就、规范准则与待办蓝图。接手后请优先通读本文档。

---

## 一、 项目定位与核心价值

**BassStation** 是一套面向专业/进阶贝斯乐手（特别是 BanG Dream! / J-Rock / J-Metal / 古典乐手）量身打造的**全功能本地化智能练琴与扒谱工作站**。

### 核心功能矩阵
1. **本地曲库与乐谱资产管理**：支持 `.gp` / `.gp5` / `.gpx`、伴侣 `.pdf`、高清曲绘、伴奏音频一站式秒级检索与分类（5弦专区、古典练习曲、各乐队分类、难度分级）。
2. **AI 全自动音频扒谱引擎**：输入普通音频/MP3，全自动调用 BS-Roformer / Demucs 分离贝斯轨道，利用物理声学 CQT + HPS 谐波乘积谱 + Viterbi 动态规划解码，全自动生成符合 Gould 记谱规范的 Guitar Pro 8 工程。
3. **伴奏与乐谱精准时钟对齐**：智能解析 GP 乐谱与音频前导静音/弱起小节，全自动计算并修改 `score.gpif` 中的 `FramePadding`，实现乐谱光标与伴奏毫秒级同步。
4. **伴奏增强与无贝斯模式切换**：支持原曲与已分离无贝斯伴奏（Backing Track / Isolated Stems）的一键无缝热切换。
5. **Guitar Pro 8 实时练琴监控与足迹打卡**：后台守护进程监听 Guitar Pro 进程生命周期，自动记录练琴时长、生成日历热力图（Heatmap）与练习统计。
6. **虚拟指板生理工效学计算**：基于弦规与指板生理代价动态规划，分析曲目把位跨度与指法热力分布。
7. **平板一键导出**：一键调用 Win32 剪贴板 API 注入 PDF/GP 文件，乐手可在微信/QQ直接按 Ctrl+V 发送至平板乐谱架。
8. **演奏录音测评与段位系统**：麦克风/声卡录音对比，音高与节拍容差打分，积分段位与徽章解锁。

---

## 二、 关键路径与目录拓扑

| 路径 | 说明 |
|---|---|
| `E:\BassStation\` | **项目实际代码根目录** |
| `E:\BassStation\backend\` | Python FastAPI 后端服务（端口 8080），核心业务逻辑与算法引擎 |
| `E:\BassStation\frontend\` | React 18 + TypeScript + Vite + Tailwind 前端工程 |
| `E:\BassStation\frontend\dist\` | 前端生产编译产物（由 FastAPI 静态托管） |
| `E:\BassStation\src-native\` | WPF (.NET 8/9 C#) 原生桌面客户端工程 |
| `E:\BassStation\tabs\` | 本地乐谱与曲库资产目录（每首歌一个独立文件夹） |
| `E:\BassStation\cache\` | 运行缓存（人声/贝斯分离音频、临时切片、录音评测） |
| `E:\BassStation\start_bass_station.bat` | 生产启动脚本（自动杀占用的 8080 端口 -> 后台启动 app.py -> 调起 Edge App 模式窗口） |
| `C:\Users\hongw\.gemini\antigravity\brain\faafc47d-6ef1-4942-8f4a-60e9106a152a\scratch\` | **算法测试基准实验室**（含 25 首跨风格测试集、消融实验、GT 比较器） |
| `c:\Users\hongw\Documents\antigravity\silly-bardeen\` | 当前 IDE 打开的工作区根目录（含全局规范文件 `AGENTS.md` / `GEMINI.md`） |

---

## 三、 核心架构与核心模块解析

### 1. 后端核心模块 (`E:\BassStation\backend\`)

- **`app.py`**：FastAPI 入口，提供 RESTful 接口体系（`/api/songs`, `/api/practice/*`, `/api/add-song/*`, `/api/audio/*`, `/api/gamification/*` 等）。
- **`ai_transcriber.py`** (53KB)：**核心 AI 扒谱引擎**。
  - Librosa 54-bin CQT 频段扩展（MIDI 23 $B_0$ 至 MIDI 77 $F_5$，全面兼容 5 弦低音 B、Drop-D、Drop-C#）。
  - 四次谐波乘积谱（HPS, Harmonic Product Spectrum）八度辨析。
  - Viterbi 全局动态规划音高序列解码器（消除跳跃振荡与异常半音噪声）。
  - 动态曲速自适应同音合并门限 (`min_distinct_gap = min(0.105, max(0.078, 0.60 * (15.0 / tempo)))`)。
  - Gould 记谱规则与三连音/连音（Tuplet）量化生成。
- **`gp_builder.py`** (37KB)：底层生成标准 Guitar Pro 8 压缩包（`.gp`），内部精准生成 `Content/score.gpif` XML 描述文件，包含音轨、调弦、小节线、节拍标记与内嵌音频参数。
- **`audio_aligner.py`** (21KB)：基于瞬态音头（Onset）与色度互相关的乐谱与伴奏对齐算法，计算 `FramePadding` 并直接热补丁到 `.gp` 内部。
- **`backing_track_enhancer.py`** (9KB)：伴奏音轨管理、Demucs 分离脚本调用、伴奏模式热切换（原版 vs 无贝斯）。
- **`tab_scanner.py`** (27KB)：本地目录扫描引擎，提取乐谱调性、五弦标记、难度等级、伴奏状态并存入 SQLite `data.db`。
- **`gp_process_monitor.py`** (7KB)：监控 `GuitarPro.exe` 进程状态，判定练琴开始与结束，提供实时练琴心跳与防抖机制。
- **`tab_fetcher.py`** (57KB)：网易云音乐搜索、NCM 格式无损解密、Songsterr 乐谱解析导入。
- **`performance_evaluator.py`** (14KB)：练琴录音比对评分引擎。
- **`fretboard_service.py`** (5KB)：虚拟指板把位计算与指法热力分布计算。

### 2. 双端展现层架构

1. **Web App 模式（主力推荐）**：
   - 架构：React 18 + TS + Vite。
   - 启动：通过 `start_bass_station.bat` 以 Microsoft Edge 独立应用窗（`--app=http://localhost:8080 --window-size=1360,820`）运行，免安装、启动极速。
2. **Native WPF 桌面客户端（备用/原生）**：
   - 路径：`E:\BassStation\src-native\BassStation.csproj`。
   - 界面定义：`MainWindow.xaml`，现代化深色/明色沉浸式设计，与 SQLite `data.db` 深度打通。

---

## 四、 核心算法突破与当前指标 (25 首跨风格实测基准)

在最新一轮研发（V9 架构）中，我们建立了包含 **25 首跨风格代表性曲目** 的全量基准测试集（涵盖 Ave Mujica 五弦降B、Roselia 降D/降C#速度金属、MyGO 流行朋克、万青民谣摇滚、Yorushika、Project Sekai、巴赫大提琴等）：

### 全局综合指标
- **音高准确率 (Pitch Acc)**：从基准 46.2% 飙升至 **73.2%**（累计提升 +27.0%）。
- **八度准确率 (Octave Acc)**：达到 **75.6%**。
- **时值准确率 (Duration Acc)**：达到 **83.5%**（16分音符网格槽位匹配）。
- **官方 PASS 曲目数量**：从 3 首跃升至 **16 首**（Drinkeries 100%、Sing Alive 89.5%、BLACK SHOUT 79.6%、Break your desire 79.4%、Shoumei Sanka 86.7%、Tentai Kansoku 85.4% 等）。
- **处理速度**：GPU 单曲耗时稳定在 **2.4s ~ 2.7s** 极速。

### 核心算法机制要点
1. **BS-Roformer `overlap=4` 与 PCM 规整化**：重叠率提升至 75%，消除了分块边界频谱拼接伪影；所有 MP3 输入预先规整为 44.1kHz 16-bit PCM WAV，彻底根治 libsndfile MPEG3 异常。
2. **动态自适应同音门限**：根据 BPM 动态缩放释放检测窗口，慢歌防止双触发切碎长音，快歌完整保留 16 分轮指。
3. **四次谐波乘积谱 (HPS)**：以 $S(b) = e_1(b) + 0.85e_2(b+12) + 0.5e_3(b+19) + 0.25e_4(b+24)$ 压制低频二次谐波虚假峰值，解除了低音区“八度乱跳”难题。
4. **Viterbi 全局音高平滑**：建立状态转移时代价矩阵（同音复奏代价 0、级进 0.05、大跳/异常跳跃惩罚增大），反向回溯全局最优音高线。

---

## 五、 必须坚决遵守的设计与开发准则

无论后续进行何种功能迭代或界面重构，**必须 100% 严格执行以下全局准则**（见工作区 `AGENTS.md` 及 `E:\BassStation\.agents\rules\ui_principles.md`）：

### 1. UI 极致精简与零冗余文案准则 (Zero Redundant Information Rule)
- **杜绝一切说教与指示性文案**：
  - 严禁任何“点击这里可以...”、“按此按钮进行...”、“点击完成结算”等啰嗦提示。
  - 用户是专业乐手，主按钮状态只保留核心动词或即时状态（例如：“开始练习”、“练习中 02:15”，不要写“进入练习 STAGE START”或“练琴中 · 点击完成结算”）。
  - 严禁在按钮或卡片下方增加副标题解释（如“实时监测时长”、“一键找谱归档”、“自动提取贝斯轨道”等 AI 宣传废话）。
- **状态与提示（Toast / MessageBox）极致精炼**：
  - Toast 通知必须直击事实结果，严禁过度修辞与长句：
    - 正确：`已导入: Ether` | 错误：`曲目「Ether」已成功导入，伴奏与乐谱均已就绪！`
    - 正确：`已打开: Ether` | 错误：`已启动 GP8: Ether · 练琴计时开始...`
    - 正确：`练习未满10秒` | 错误：`练习时间过短 (<10秒)，未记录足迹。`
- **严禁堆叠冗余英文副标题与标签**：
  - 严禁在清晰中文标题后附带冗余大写英文（如“难易度选择 DIFFICULTY”、“伴奏节拍对齐 TIMING SYNC”）。
  - 直接使用干净精炼的中文词汇：`难度`、`节拍对齐`、`演奏评测`。
- **空即是多（Zero Visual Noise）**：
  - 不在同一视口内重复表达相同信息；曲绘封面上严禁堆砌过多浮动标签。

### 2. 视觉设计风格 (BanG Dream! Arcade White/Bright 风格)
- 遵循 `.agents/skills/visual-communication-ui/SKILL.md`：
- 明亮清爽的偏白色系背景（`#f4f6fb`、`#ffffff`），搭配高对比度文本（`#0f172a`、`#334155`）。
- 标志性高饱和度点缀色：BanG Dream 荧光粉（`#ff2d75`）、青蓝（`#00b4d8`）、星空琥珀金（`#f59e0b`）、律动紫（`#7c3aed`）。
- 街机风格斜角药丸微标、精密渐变难度徽章。

---

## 六、 运行、调试与验证指令

### 1. 启动全套系统
在项目根目录下双击 `start_bass_station.bat`，或在命令行运行：
```powershell
cd E:\BassStation
.\start_bass_station.bat
```
*(该脚本会自动清理 8080 端口，拉起 `pythonw backend/app.py`，并调起 Edge App 窗口)*

### 2. 调试后端 FastAPI 服务（看实时日志）
```powershell
C:\Python314\python.exe E:\BassStation\backend\app.py
```
*(日志亦实时追加在 `E:\BassStation\backend\server.log` 中)*

### 3. 运行 25 首跨风格 AI 扒谱全量基准测试
```powershell
C:\Users\hongw\AppData\Local\Programs\Python\Python311\python.exe C:\Users\hongw\.gemini\antigravity\brain\faafc47d-6ef1-4942-8f4a-60e9106a152a\scratch\batch_25_round_tuner.py
```

### 4. 运行 4 首生产级 AI 扒谱验证
```powershell
C:\Users\hongw\AppData\Local\Programs\Python\Python311\python.exe C:\Users\hongw\.gemini\antigravity\brain\faafc47d-6ef1-4942-8f4a-60e9106a152a\scratch\verify_production_transcriber.py
```

### 5. 前端编译（若修改了 `frontend/src`）
```powershell
cd E:\BassStation\frontend
npm run build
```
*(产物将输出至 `E:\BassStation\frontend\dist`，FastAPI 会自动免缓存提供服务)*

### 6. WPF 原生端构建（若修改了 `src-native`）
```powershell
cd E:\BassStation\src-native
dotnet build -c Release
```

---

## 七、 后续 AI 重点攻坚方向与待办事项 (Next Steps)

1. **AI 扒谱算法收敛（目标：25 首全集突破 80%+ 综合音高准确率）**：
   - 攻坚 TUNED 状态的剩余 9 首曲目（如《Masquerade 5st/4st》、《Kao 4st》、《Sugar Rush》、《Daremo》等）。
   - 针对《Daremo》（极高动态 Math Rock 变拍）：优化非 4/4 拍与连续离散切分音的音头灵敏度。
   - 针对《Kao 4st》（降 D 强失真重低音）：优化失真低频饱和泛音的基频识别。
2. **前端交互与伴奏对齐体验打磨**：
   - 校验前端所有新建、对齐、切换界面的文案，确保符合“零冗余说教”准则。
   - 增加伴奏对齐波形微调的可视化滑块或快捷热键支持（$\pm 10\text{ms}$ 步进）。
3. **WPF 端功能对齐**：
   - 确保 Web 端新增的伴奏模式切换、虚拟指板把位分析在 WPF 端具备对应的界面触发入口。
4. **乐谱导出扩展**：
   - 完善 MusicXML 导出转换链路，提升向 MuseScore / Sibelius 的乐谱导入兼容性。
