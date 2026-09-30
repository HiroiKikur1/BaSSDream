# BaSSDream 🎸

一套专为进阶/专业贝斯乐手量身打造的**全功能本地化智能练琴与扒谱工作站**（BanG Dream! 视觉风格）。

![BaSSDream Logo](assets/bassdream_logo.png)

---

## 🌟 核心功能矩阵

1. **本地曲库与乐谱资产管理**
   - 支持 `.gp` / `.gp5` / `.gpx`、伴侣 `.pdf`、高清曲绘、伴奏音频一站式检索与快速分类（5弦专区、古典练习曲、各乐队分类、难度分级等）。
2. **AI 全自动音频扒谱引擎**
   - 音频贝斯轨道分离结合物理声学 CQT + HPS 谐波乘积谱 + Viterbi 全局动态规划解码，自动生成符合 Gould 记谱规范的 Guitar Pro 乐谱工程。
3. **伴奏与乐谱精准时钟对齐**
   - 智能解析 GP 乐谱与音频前导静音/弱起小节，全自动计算并热补丁修正乐谱内嵌音频 `FramePadding`，实现乐谱光标与伴奏毫秒级同步。
4. **Guitar Pro 实时练琴监控与打卡**
   - 后台守护进程监听 Guitar Pro 进程生命周期，自动记录练琴时长、生成练习日历热力图与统计报告。
5. **虚拟指板与人体工程学生理把位分析**
   - 基于弦规与生理代价动态规划算法，分析曲目把位跨度与指法热力分布。
6. **双端展现架构**
   - **Web App 模式（主力推荐）**：React 18 + TypeScript + Vite + Tailwind，通过 Edge 独立 App 模式运行，免安装秒级调起。
   - **WPF 原生客户端**：基于 .NET 10 WPF 打造的沉浸式深色桌面端。

---

## 📂 目录架构

```text
BaSSDream/
├── backend/          # Python (FastAPI) 后端服务、核心业务逻辑与算法引擎
│   ├── bassnet/      # AI 扒谱模型与特征工程管线
│   ├── app.py        # FastAPI 主入口 (端口 8080)
│   └── ...
├── frontend/         # React 18 + TypeScript + Vite 前端工程
│   ├── src/          # 前端核心业务组件与页面
│   └── package.json
├── src-native/       # WPF (.NET 10 C#) 原生桌面客户端工程
├── assets/           # 乐队图标、Logo、字体与视觉资源
├── desktop/          # 桌面辅助脚本与托盘工具
├── docs/             # 架构白皮书、交接文档与排查清单
└── start_bass_station.bat  # 一键启动脚本
```

---

## 🚀 快速上手

### 环境要求
- **Python**: 3.11 / 3.14（FastAPI、librosa 等基础库）
- **Node.js**: 18+（前端构建）
- **.NET SDK**: 10.0（可选，仅用于构建原生 WPF 客户端）
- **Guitar Pro**: Guitar Pro 8（用于乐谱同步与交互）

### 启动方式 (Web 模式)
双击运行根目录下的 `start_bass_station.bat`，即可一键拉起后端服务并自动唤起独立应用窗口。

---

## 📌 注意事项
- 本开源仓库仅包含核心系统工程代码、算法实现、交互前端与界面素材。
- 个人的 `.gp` 谱库与音频资产（`tabs/` 目录）以及大型 AI 预训练权重（`cache/` 目录）不随本代码库分发，按需放置在本地相应目录下即可自动识别。
