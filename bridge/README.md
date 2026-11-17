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

## 双人模式：小克 × DS（2026-11-17，duo.py）

DS = WorkBuddy 自带 CodeBuddy CLI 跑的 DeepSeek V4.1 Flash（`ds_client.py`，每次约 5 秒）。DS 不拿任何工具、不加载 MCP，只读文字回 JSON；操作由 `duo.py` 按白名单代她执行。人设、打法规则、可用操作和输出格式在 `duo_ds_system.md`。

每一步的流程（小克=Claude 在 bridge 目录运行）：

```bat
python -X utf8 duo.py propose [--note "补充说明"]      :: DS 读 look 局面并提议（气泡显示）
python -X utf8 duo.py reply agree  "小克的话" [--actions "[...]"]   :: 同意（可带修改后的操作）
python -X utf8 duo.py reply object "小克的意见" [--actions "[...]"] :: 反对：DS 回应，最多两轮，之后按小克方案
python -X utf8 duo.py exec                               :: DS 执行已达成一致的方案，打印结果和执行后局面
python -X utf8 duo.py status
```

DS 回复「同意」时以小克给出的操作为准（她偶尔会把旧方案原样带回）。状态存在 `duo_state.json`。
工作台发言：`/api/status` 带 `agent=xiaoke|ds`、`kind=say|propose|agree|object|decide|exec|ask`。

### 银狼执行规则更新 · 2026-11-18

`duo_lineups/silverwolf.md` 保存用户确认的过渡、站位、改件和装备规则；`silverwolf.want.json` 保存基准星级目标，真珠顺手追三星，花火在砂金前台上阵后退出自动购卡目标。第9人为开拓者CT，不追6欢愉。主C之外按费用和实际作用优先换强力角色，不能只按星级保留低费卡；用 `buy` 或 `roll.want` 指定需要的替换角色。CT与开拓者·欢愉在识别和购卡缺口中按同一角色处理。

`duo.py` 对银狼的 `exp`、`roll`、`fight` 执行经济检查：买经验保50金，先到7级；银狼三星前停买经验，按55金底线搜银狼；三星后停止专门搜牌，买经验最多到9级。真珠未三星不会延长刷新。原始桥接接口和其他阵容不自动套用此阶段规则。

出战前会检查当前商店与剩余预算。买卡、刷新或买经验后返回 `needs_review`，本轮剩余计划暂停，状态为 `review`；下一轮重新读取棋盘、安排人口与装备再出战。识别或购买失败时停止并返回错误。生成后的提示词 `duo_state_system.md` 由 `build_system()` 从当前阵容与通用模板刷新。

离线验证：`vendor\March7thAssistant\.venv\Scripts\python.exe -m unittest discover -s bridge\tests -v`。
