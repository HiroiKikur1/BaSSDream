---
description: 杜绝一切UI冗余信息、说教文案与操作说明的强制设计准则
always_on: true
---

# UI 极致精简与零冗余文案准则 (Zero Redundant Information Rule)

在本项目的所有 UI 设计、前端界面、WPF/桌面端及文案编写中，必须严格执行以下准则：

## 1. 杜绝一切说教与指示性说明
## 2. 状态与提示（Toast / MessageBox）极致精炼
## 3. 严禁堆叠冗余英文副标题与标签
- 严禁在清晰中文标题后附带冗余大写英文（如“难易度选择 DIFFICULTY”、“伴奏节拍对齐 TIMING SYNC”、“PERFORMANCE EVALUATION & COACH FEEDBACK”）。
- 直接使用干净精炼的词汇
## 4. 空即是多（Zero Visual Noise）
- 不在同一视口内重复表达相同信息。
- 如果状态是默认或已完成，使用极简微标（如纯绿点或简洁字样），不出现冗长描述性语句。

## 5. 界面设计准则
- 设计或修改任何界面前，先读 `.claude/skills/human-made-ui/SKILL.md`。
- 做新界面、改版或探索视觉方向：按 `.claude/skills/design-lab/SKILL.md` 在 `design-lab/` 里先做小样、渲染、评审，用户批准后再改产品代码。通用设计原理和工具在 `.claude/skills/design-craft/`。

## 6. 本机与云端同步
- 按 `docs/本机与云端同步.md`：只合并、不覆盖；合并前先把未提交的改动提交并推送；交接前推送；云端会话开始时先 `git fetch`，从最新的分支起步（`main` 落后时看 HANDOVER 末节记的分支）。
