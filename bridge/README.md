# 货币战争桥接接口（cw-bridge）

让 Claude 通过 MCP 工具观察和操作《崩坏：星穹铁道》的货币战争，复用 March7thAssistant（`vendor/March7thAssistant`）的截图、OCR、页面识别、棋盘读取、策略和单步自动逻辑。

## 结构

```
Claude Code ──MCP(stdio)──> bridge/mcp_server.py   普通权限，uv 临时环境（mcp<2）
                               │ HTTP 127.0.0.1:47821 + X-CW-Token
                               ▼
                           bridge/server.py         管理员权限，M7A 的 .venv
                               │ 截图 / OCR / pyautogui 输入
                               ▼
                           游戏窗口（管理员进程，1920x1080，简体中文）
```

游戏是管理员进程，Windows 会拦截普通权限进程发来的鼠标和键盘输入，所以 `server.py` 必须以管理员身份运行。

## 使用

1. 游戏以 1920x1080 窗口或全屏运行，打开自动战斗。
2. 双击 `bridge/start_server.bat`，在 UAC 弹窗点「是」。会出现标题为 cw-bridge 的窗口，关掉它服务就停止。
3. Claude Code 在项目根目录打开时会读取 `.mcp.json`，加载 `currency-wars` MCP（第一次需要你批准）。
4. 命令行备用：`python bridge/cw.py status`、`python bridge/cw.py observe`、`python bridge/cw.py tap x=960 y=540`。

默认策略是 `s2_firefly_hanya`（寒鸦流萤·高难），可用 `cw_config` 修改模式（normal/overclock）和职级难度（lowest/current/highest）。

注意：输入使用前台模式（pyautogui），运行期间会占用鼠标，游戏窗口需要保持在前台。

## 工具

| 工具 | 作用 |
|---|---|
| `cw_status` | 是否管理员权限、窗口位置、当前模式/策略/难度 |
| `cw_observe` | 本地截图 + OCR + 页面识别，默认只返回结构化字段（页面名、节点、金币、血量、等级、经验、人口、页面专属字段）；页面认不出时自动附带全屏文字；`image=True` 才附带截图 |
| `cw_tap` / `cw_drag` / `cw_key` | 原始输入，坐标统一按 1920x1080 |
| `cw_board` | 读取完整棋盘（会点开详情识别角色），并附上 M7A 的策略建议 |
| `cw_advise` | 基于最近一次棋盘给出建议；在商店页时还会给出该买哪张卡、要不要刷新 |
| `cw_step` | 让 M7A 自动处理当前页面一步 |
| `cw_run` | 连续自动推进，直到 `stop_pages` 中的页面、节点变化、回到首页或超时 |
| `cw_abort` | 中止正在运行的 run/step |
| `cw_home` / `cw_launch` / `cw_reward` / `cw_reset` / `cw_logs` | 回首页、开局、领周奖励、清空对局缓存、查看日志 |

页面名（`page` 字段）：HOME、MODES、RANK、BOARD、SHOP、CHOICE、HALO、TEA、BATTLE、NODE_RESULT、MATCH_RESULT、GARDEN、HACK、DANCE、RANGER、REVEAL、CONFIRM_FIGHT 等，完整列表见 `vendor/March7thAssistant/tasks/weekly/currency_wars_ui.py` 的 `Page` 枚举。

## 推荐玩法

- 常规节点交给 `cw_run`，并设置 `stop_pages=["CHOICE","HALO","GARDEN","HACK","DANCE","RANGER"]`，在关键选择点停下，由 Claude 结合 `knowledge/` 图鉴自己决定，再用 `cw_tap` 选择。
- 首领节点前用 `cw_board` 检查阵容，必要时用 `cw_drag` 手动调整站位。
- 截图保存在 `bridge/shots/last.png`。

## 安全

- 服务只监听 127.0.0.1，每次启动生成新的 token 写入 `bridge/.token`。
- vendor 代码里有几段 base64 混淆代码，已解码检查：只是免责声明检查、自动更新开关和反倒卖提示，没有网络或恶意行为。

## 文字化接口（2026-11-16，textview.py + icons.py）

尽量不看截图：所有页面都能转成一段文字。CLI：`python -X utf8 cw.py <route> ...`。

| 路由 | 作用 |
|------|------|
| `look` | 任何页面输出紧凑文字。棋盘：关卡/金/血/等级/人口/开拓力/球/宝箱/按钮、羁绊、前/后/备每格「序号:名字★星/站位费用[身上装备]」、物品栏「名字@坐标」、顶部道具及数量。商店：每列卡名+费用+站位。节点选择：按卡分栏「序号@坐标【标题】说明」。光环/开拓商店/其它页：标题+说明或「文字@坐标」。 |
| `equip items=[名字] target={"name":..}` | 按名字拖装备；每件都重新定位（物品栏会挪位），并学习身上小图标。冶金炉被拒绝（会洗全身）。 |
| `wrench name=角色` | 用扳手拆该角色全部装备 |
| `furnace item=散件名` | 冶金炉只用在物品栏散件上 |
| `rail action=read\|buy names=[...]` | 开拓商店：读/按名字买，默认买完关掉 |
| `tea action=read side=left\|right` / `tea action=choose index=n` | 茶歇：先读子选项，再选并确认、开奖励 |
| `halo action=pick side=.. zone=back slot_index=i` | 光环可给后台角色 |
| `icons [action=reload]` | 查看/重载图标库 |
| `reload` | 热加载 icons/actions/textview，不用重启服务 |

`move` / `sell` / `combine` / `equip` / `wrench` / `furnace` / `tea choose` 执行后自带 `after`（棋盘文字），不用再截图确认。

- 角色名：用 M7A 读棋盘（首次会点开详情，约 10 秒；之后走缓存约 1 秒）。单纯头像猜测不准，已不再使用。
- 站位/费用来自 `knowledge/characters.json`（备战席的 M7A 站位判断不准）。
- 装备图标库在 `bridge/icons/<inv|top|card>/名字__n.png`：认不出的物品会自动点开看名字后存入；身上装备（26x26 截块，中心=卡片中心+81 像素）直接用物品栏库比对。
- 自行重启服务：杀掉 `bridge\server.py` 进程，再以 `CW_BACKEND=webview CW_CDP_PORT=9223` 后台启动（令牌从 小克工作台\token.txt 读）。
