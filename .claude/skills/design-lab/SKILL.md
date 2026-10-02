---
name: design-lab
description: BaSSDream 设计工作台的流程与工具：做新界面、改版某个画面、探索视觉方向、做界面小样、评审设计时加载。用法是“放开风格、收紧证据”：先在 design-lab/ 里用 HTML/SVG 等任意工具自由做小样并渲染，再用审计工具、对比图和 design-critic 子代理把关，用户批准后才翻译成 WPF。与 design-craft（通用设计学）、human-made-ui（本项目界面准则）、AGENTS.md（文案）一起用。
---

# BaSSDream 设计工作台

通用原理在 design-craft，本项目的界面准则在 human-made-ui。这里只讲**在这个项目里怎么做**。

## 0. 为什么有这个工作台

同一个模型做视频好看、做应用难看，主要因为做应用时**看不到结果、第一版就交、注意力被功能占满、没有素材、被框架限制、只有禁令没有参照**（详见 design-craft `references/process.md`）。工作台把这些条件补回来：

- 有眼睛：云端和 Windows 都能把小样渲染成图，Windows 还能截真实 WPF 界面；
- 有素材：曲绘、舞台图、官方乐队素材、字体库、参考库都在手边；
- 有自由：小样用什么工具都行，不受 WPF 限制；
- 有关卡：审计、对比图、陌生眼睛的评审、用户判活死。

## 1. 准备（每台机器一次，重复跑无害）

- 云端容器：`bash design-lab/setup.sh`
- Windows：`powershell -File design-lab/setup.ps1`

装的是：numpy、pillow，用于渲染的 playwright-core，中文回退字体，`design-lab/fonts.txt` 里的字体库（下载到 `design-lab/fonts/`，不进 git）。

## 2. 硬约束：只有这几条

除下面几条外，风格、构图、字体、配色、动效、小样工具都放开。

1. **文案**：遵守 AGENTS.md。不说教，不加英文副标题，不重复信息，默认状态不显示。
2. **固定令牌**：品牌粉 `#E50050`、判定色、乐队代表色不改（`src-native/Themes/Palette.xaml`）。从曲绘取的颜色只做陪衬。
3. **官方素材**：乐队标识只用官方文件（`assets/band_logos/`、`assets/band_icons*/`），不自己画。含官方美术的小样和对比图不发布到公开页面，给用户看时直接发文件。
4. **现有界面保持现状**：用户已经决定了。study 只在用户要求时做，用户批准后才改产品代码。
5. **框架稳定**：标题栏、顶栏、侧栏、底栏换曲目时不跳（human-made-ui 第 5 节）。

## 3. 一次设计 = 一个 study

```
design-lab/studies/<yyyy-mm-dd>-<名字>/
  brief.md        从 _template 复制，先填
  a.html b.html c.html ...   三个以上方向的小样（或 .svg / .py 画的 .png）
  out/            渲染结果和审计报告（不进 git）
  sheet.png       给用户看的对比图（进 git）
  critique.md     评审意见和处理结果
```

素材：

- 舞台背景 `assets/backdrops/`，乐队 logo `assets/band_logos/`，乐队图标 `assets/band_icons/`、`assets/band_icons_line/`
- 本软件 logo `assets/bassdream_logo.svg`、`src-native/assets/`（舞台图、结算舞台）
- 字体：项目自带的思源圆体 `assets/fonts/ResourceHanRoundedCN-*.ttf`，字体库 `design-lab/fonts/`
- 曲绘：只在 Windows 本机（`tabs/` 和缓存目录），云端没有，需要时请用户提供
- 参考库 `design-lab/refs/`，种子 `design-lab/seeds.md`，反面清单 `design-lab/rejected.md`

## 4. 流程

T 指 `.claude/skills/design-craft/tools`。

**第一段：创作（放开）**

1. 填 `brief.md`：这一屏回答哪些问题，它在现实里是什么，素材和参考有哪些，以及**默认答案**（一个没想过的 AI 会怎么做）。
2. 读 `seeds.md` 和相关参考图，从种子长，不另起炉灶。这一段不读反面目录。
3. 做三个以上空间逻辑不同的方向。每版都渲染出来，自己用 Read 看：
   `node T/render.cjs studies/x/a.html studies/x/out/a.png`，细节加 `--scale 2`，动效加 `--frames 0,120,240,480`。
4. 曲绘取色：`python T/palette.py <曲绘>`。

**第二段：审查（收紧）**

5. 读审计报告。“fonts drawn”里有回退字体就先修字体。
6. 对比图：`python T/sheet.py studies/x/sheet.png <参考...> studies/x/out/*.png --notan`，自己先看。
7. 用 Agent 工具启动 `design-critic` 子代理，只给它图片路径和一句这一屏是做什么的，不给你的设计理由。
8. 读 design-craft 的诊断词典、反面目录和清单，再读 `rejected.md`。每条命中要么改，要么在 `critique.md` 写一句理由。每轮至少删一样东西。
9. 重复 3–8，直到你自己愿意把它放到参考旁边。

**第三段：交给用户**

10. 用 SendUserFile 发 `sheet.png`，一轮只问 2–4 个决定，让用户判活死。
11. 用户的原话写进 `rejected.md` 或 `seeds.md`。

**第四段：翻译成 WPF（用户批准后）**

12. 按 human-made-ui 实现。颜色、字号用令牌，自绘部分按第 10 节。
13. 在 Windows 上截真实界面：`BassStation.exe --render-song out.png` 等（全部入口见 `src-native/DebugTools/RenderHarness.cs`）。
14. 叠比：`python T/overlay.py studies/x/out/a.png out.png studies/x/out/a-vs-wpf.png`，差异逐条处理。WPF 做不到的效果，回到小样里找一个能做到的等价做法，不要悄悄删掉。
15. 气味报告：`python T/xaml_lint.py src-native/Views/<改动的文件>`。新增的命中要么改，要么写理由。存量问题不在这次范围内。

云端容器跑不了 WPF：第 13–14 步只能在 Windows 上做。在云端改了 WPF，要明确告诉用户“未截图验证”。
