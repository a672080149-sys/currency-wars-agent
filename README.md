# currency-wars-agent

让 AI Agent（Claude Code 等）亲自操作《崩坏：星穹铁道》**货币战争（S2 入画奇航）**的工具集：一个 Windows 工作台程序 + 一个本地桥接服务 + 一份结构化玩法资料库。

AI 通过接口读取游戏画面（已尽量转成文字）、买卡、上阵、合装备、选节点、出战，并把每一步的想法实时显示在工作台的「思考」面板里，方便录成解说视频。

> 非官方项目，与米哈游无关。自动化操作游戏可能违反游戏用户协议，请自行评估风险并仅用于学习研究。

## 组成

| 目录 | 内容 |
|---|---|
| `小克工作台/` | C# WinForms 程序：内嵌云·星穹铁道（WebView2）或接管本地游戏窗口；AI 面板（思考记录、当前目标、桌宠）；60fps 录像（带游戏声音）；HTTP 接口 + MCP。详见 [小克工作台/README.md](小克工作台/README.md) |
| `bridge/` | Python 桥接服务：复用 March7thAssistant 的截图、OCR、页面识别，提供买卡/卖卡/上阵/装备/合成/遭遇/光环/出战等组合操作，以及把任意页面转成文字的 `look`。详见 [bridge/README.md](bridge/README.md) |
| `knowledge/` | S2 入画奇航资料库：角色、羁绊、装备合成表、投资策略、节点事件、规则（整理自 B 站 wiki） |
| `.mcp.json` | Claude Code 的 MCP 配置（`currency-wars`） |

## 快速开始

需要：Windows 10/11、[uv](https://docs.astral.sh/uv/)、Git；用 MCP 时需要 Node 18+。

1. **下载**：到 [Releases](../../releases) 下载打包好的 zip 并解压；或直接 clone 本仓库（clone 后需要先运行 `小克工作台\build.cmd` 编译 exe，并运行 `小克工作台\tools\fetch-pet.ps1` 下载桌宠素材，桌宠可选）。
2. **装依赖 March7thAssistant**（GPL-3.0，本仓库不包含）：
   ```bat
   git clone https://github.com/moesnow/March7thAssistant.git vendor\March7thAssistant
   cd vendor\March7thAssistant
   git checkout 5c3eee70ca54bb029249813d1a8242b877fa7f16
   uv sync
   ```
   （生成 `vendor\March7thAssistant\.venv`；工作台和桥接默认用这里的 Python。）
   本项目基于上面这个提交开发和测试，未修改 March7thAssistant 的代码。
3. **运行**：双击 `小克工作台\小克工作台.exe`，点「启动星穹 · 货币战争」。默认是云游戏模式（`cw.json` 里 `mode: cloud`，首次运行自动生成），第一次需要在左上画面里自己登录。登录态保存在 `小克工作台\webview-data\`，**不要分享这个目录**。
4. **让 AI 操作**：在仓库根目录打开 Claude Code，批准 `currency-wars` MCP；或者直接用命令行：
   ```bat
   vendor\March7thAssistant\.venv\Scripts\python.exe -X utf8 bridge\cw.py look
   ```

## 文字化接口（尽量不看截图）

`bridge/cw.py look` 在任何页面都输出一段紧凑文字，例如棋盘：

```
1-3 金15 血84 Lv5 经验2/20 人口5/5
开拓力- 球0 宝箱无 按钮[出战]
羁绊 持续伤害(3/5/7/9) 星间旅人(...) 仙舟舰队(1/2) ...
前: 0:海瑟音★2/前后2费[量产型装甲]  1:桂乃芬★1/前后1费  2:火花★2/前后1费
后: 0:娜塔莎★1/后2费  1:黑天鹅★1/后1费[反重力皮靴,光能电池]
备: 2:娜塔莎★1/后2费
物品: 幸运星@1844,248  生命之花@1844,325
道具: 拆装扳手x2
```

装备图标靠自学习的图标库识别（`bridge/icons/`，认不出的会自动点开看一次名字并记住）；`move`/`sell`/`equip`/`combine` 等操作执行后直接返回变化后的棋盘文字。完整路由见 [bridge/README.md](bridge/README.md)。

## 不包含的内容

- 任何凭证：`bridge/.token`、`小克工作台/token.txt`（每次运行自动生成）、`小克工作台/webview-data/`（云游戏登录态）。
- 录像、截图、日志、运行时学到的头像/图标库（都会在本地自动生成）。
- March7thAssistant 源码与 Python 环境（按上面的步骤自行拉取）。

## 许可证

- 本仓库代码：GPL-3.0（因为运行时依赖 GPL-3.0 的 March7thAssistant），见 [LICENSE](LICENSE)。
- 桌宠素材（Releases 包里的 `小克工作台/pet/*.png`）：来自 [kirito1207/claudia-pet](https://github.com/kirito1207/claudia-pet)，CC BY-NC-SA 4.0，不得商用，见 [小克工作台/pet/NOTICE.md](小克工作台/pet/NOTICE.md)。
- `knowledge/` 资料整理自 [B 站星穹铁道 wiki](https://wiki.biligame.com/sr/)，原内容按其站点协议（CC BY-NC-SA 4.0）署名使用。
- 游戏名称、角色与美术版权归米哈游所有。

## 致谢

- [March7thAssistant](https://github.com/moesnow/March7thAssistant)：截图、OCR、货币战争页面识别与自动逻辑。
- [claudia-pet](https://github.com/kirito1207/claudia-pet)：桌宠素材。
