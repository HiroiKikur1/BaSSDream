# 设计工作台

做界面小样、渲染、对比、评审的地方。流程见 `.claude/skills/design-lab/SKILL.md`，原理见 `.claude/skills/design-craft/`，本项目界面准则见 `.claude/skills/human-made-ui/`。

| 路径 | 内容 |
|---|---|
| `setup.sh` / `setup.ps1` | 装工具和字体（云端 / Windows），可重复跑 |
| `fonts.txt` | 字体库清单；字体下载到 `fonts/`（不进 git） |
| `refs/` | 参考截图与锚点 |
| `seeds.md` | 用户认可的方向（从这里长） |
| `rejected.md` | 用户否掉的东西（原话） |
| `studies/` | 每次设计一个文件夹；`_template/brief.md` 是起手模板 |

小样不是产品代码。用户批准后才翻译成 WPF，并用 `BassStation.exe --render-*` 截图和小样叠比。
