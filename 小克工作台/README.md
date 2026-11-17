# 小克工作台

一个 Windows 程序:在它的窗口里启动/接管任意应用,让应用在后台运行,并通过接口让 AI Agent 对它们截图、点击、拖动、键盘输入。右侧「AI 面板」实时显示 AI 的思考、任务计划和每一步操作。

## 运行

双击 `小克工作台.exe`(无需安装,依赖 Windows 自带的 .NET Framework 4.x)。

界面分四块:

- **左上:程序画面**(16:9)。显示当前窗口的实时画面;在画面上点击、拖动、滚轮、打字,都会转发给后台程序。
- **右上:思考**。桌宠 Claudia 固定站在栏底(不能拖动),头顶的小气泡说 AI 当前在想什么;上方面板显示「当前游戏目标」(如「升 6 搜流萤」)和「思考记录」(新的在上,带时间)。没有游戏目标时显示步骤清单(✓ 已完成、● 进行中、○ 未开始)。她的动作跟随 AI 状态:待机、思考、写字(输入文字/按键)、忙碌(点击/拖动/启动等)、庆祝(计划全部完成)、叹气(操作出错)。
- **左下:日志**。AI 的每一步思考和操作,带时间和原因。
- **右下:操作**。
  - 下拉框:切换当前窗口。
  - **启动程序**:填 exe 路径、命令名(如 `notepad`)、文档或网址。勾选「启动后放到后台」时,窗口会被移到屏幕外,不占桌面、不抢焦点。
  - **接管窗口**:把一个已经在运行的窗口纳入管理。
  - **放到后台 / 显示到前台 / 关闭窗口 / 停止接管**:管理当前窗口。
  - **真实输入**:见下方「两种输入方式」。
  - **复制令牌**:复制接口令牌。
- 关闭 小克工作台 时,所有后台窗口会自动回到屏幕上。

换端口:`小克工作台.exe --port 9000`。修改源码后运行 `build.cmd` 重新编译。

## 目录结构

| 路径 | 内容 |
|---|---|
| `小克工作台.exe` | 主程序,双击运行 |
| `mcp-server.js` | 给 AI Agent 用的 MCP 桥接(需要 Node 18+) |
| `src\` | C# 源代码:`Native.cs`(系统调用)、`Core.cs`(窗口管理与输入)、`ApiServer.cs`(HTTP 接口与 AI 状态)、`MainForm.cs`(界面与桌宠) |
| `app.ico` | 程序图标 |
| `build.cmd` | 用 Windows 自带的 C# 编译器重新编译,不需要安装 SDK |
| `pet\` | 默认桌宠(小克 Claudia)动画素材和授权说明 |
| `pets\` | 更多桌宠(来自 AI-Buddy 的 11 位模型娘)动画素材和授权说明 |
| `tools\fetch-pet.ps1` | 重新下载、转换默认桌宠素材 |
| `tools\fetch-ai-buddy-pets.py` | 下载并转换 AI-Buddy 11 位角色桌宠素材到 `pets\` |
| `token.txt` | 接口令牌。**不在压缩包里**,首次运行时自动生成,每台机器各不相同 |

解压到任意目录即可使用;如果 Windows 提示文件来自其他计算机,在 exe 的「属性」里勾选「解除锁定」。
## 两种输入方式

| 方式 | 做法 | 适用 |
|---|---|---|
| `background`(默认) | 直接向窗口投递鼠标/键盘消息,不动真实鼠标键盘,不抢焦点 | 大多数传统桌面程序(Win32、WinForms、命令行等) |
| `real` | 把窗口短暂调到前台,用真实鼠标键盘操作,完成后自动放回后台并还原鼠标位置和原前台窗口 | 不响应后台消息的程序:WinUI/UWP 新式界面(新版画图、计算器等)、部分游戏 |

另外还有 **界面元素操作**(`ui_tree` + `ui_action`):按名称直接按按钮、填输入框,纯后台,对新式界面的按钮同样有效。

判断方法:操作后截图,如果画面没变化,按钮类先试 `ui_action`,否则改用 `mode: "real"`。

## 给 AI Agent 的接口

### 方式一:MCP(推荐,Claude Code / Claude Desktop 等)

需要 Node 18+,小克工作台.exe 保持运行。在 MCP 配置里加入:

```json
{
  "mcpServers": {
    "xiaoke-workbench": {
      "command": "node",
      "args": ["<小克工作台 所在目录>\\mcp-server.js"]
    }
  }
}
```

工具:`report_status`、`list_windows`、`list_all_windows`、`launch_app`、`attach_window`、`screenshot`、`click`、`drag`、`type_text`、`press_key`、`scroll`、`window_action`、`ui_tree`、`ui_action`。

### 方式二:HTTP

地址 `http://127.0.0.1:8765`,只监听本机。每个请求必须带请求头 `Authorization: Bearer <令牌>`,令牌在程序目录的 `token.txt`(首次运行自动生成,也可在窗口右下角点「复制令牌」)。完整接口说明:`GET /api/help`。

| 接口 | 参数 | 说明 |
|---|---|---|
| `GET /api/windows` | | 受管窗口列表;程序新弹出的对话框会自动加入 |
| `POST /api/launch` | `path, args?, background?=true, wait_ms?` | 启动并接管 |
| `POST /api/attach` | `hwnd` 或 `title`, `background?` | 接管已打开的窗口 |
| `GET /api/screenshot?id=1` | `format=json` 返回 base64 | 窗口截图(PNG),后台也能截 |
| `POST /api/click` | `id, x, y, button?, double?, mode?` | 点击 |
| `POST /api/drag` | `id, x1, y1, x2, y2, button?, steps?, duration_ms?, mode?` | 拖动 |
| `POST /api/type` | `id, text, mode?` | 输入文字,`\n` 为回车 |
| `POST /api/key` | `id, keys, mode?` | `enter`、`ctrl+s`、`alt+f4` 等 |
| `POST /api/scroll` | `id, x, y, delta, mode?` | 滚轮,负数向下 |
| `POST /api/window` | `id, action` | `background / foreground / resize / close / release` |
| `GET /api/ui_tree?id=1` | `max?` | 界面元素树 |
| `POST /api/ui_action` | `id, element\|name\|automation_id, action?, value?` | `invoke / set_value / toggle / select / expand / collapse` |
| `POST /api/status` | `thought?, goal?, plan?, step?, clear?` | thought 显示在桌宠头顶气泡并记入思考记录;goal 为当前游戏目标(文字或数组);plan/step 为步骤清单 |

坐标是相对窗口左上角的像素,和截图像素一一对应。所有操作接口都可以带 `reason`(这一步的原因),会显示在 AI 面板的操作记录里。

PowerShell 示例:

```powershell
$h = @{ Authorization = "Bearer $((Get-Content token.txt).Trim())" }
function Api($route, $body) {
  $json = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json))
  Invoke-RestMethod "http://127.0.0.1:8765/api/$route" -Method Post -Headers $h -ContentType 'application/json' -Body $json
}
Api status @{ plan = @('打开记事本', '写一句话'); step = 1; thought = '先启动记事本' }
$w = (Api launch @{ path = 'notepad.exe' }).windows[0]
Api type @{ id = $w.id; text = "你好`n"; reason = '写入内容' }
Invoke-WebRequest "http://127.0.0.1:8765/api/screenshot?id=$($w.id)" -Headers $h -OutFile shot.png
```

## 货币战争(星铁)一键启动

操作面板里的 **启动星铁 · 货币战争** 按钮。默认用**云·星穹铁道**,游戏画面直接内嵌在工作台里:

### 云游戏模式(默认,`cw.json` 的 `mode: "cloud"`)

1. 预览区换成内嵌浏览器(WebView2),打开 https://sr.mihoyo.com/cloud 。画面由浏览器按视频流渲染,60 FPS。
2. 第一次用要在内嵌页面里**自己登录米哈游账号**;登录状态保存在程序目录 `webview-data\`(不要分享这个文件夹)。
3. 在后台无窗口启动 cw-bridge(`CW_BACKEND=webview`)。bridge 通过浏览器调试端口(`cdp_port`,默认 9223,只监听本机)截图(固定 1920x1080)、点击、按键:**完全后台,不占真实鼠标键盘,不需要管理员**,工作台被挡住或在别的屏幕上都能操作。
4. 网页首页停着时 bridge 会自动点「进入游戏」;进游戏后的「点击进入」和首次弹窗由 Claude 自己点。
5. 网页自己的新手引导浮层会被自动关闭。

### 本地模式(`mode: "local"`)

1. 星铁以管理员身份运行,所以 小克工作台 也要是管理员。没有的话会询问一次,确认后以管理员身份重开(只弹一次 UAC),并自动继续。
2. 星铁已打开就直接接管;没打开就启动 `StarRail.exe` 并等待窗口(最多 3 分钟)。游戏窗口保持在桌面上(bridge 用前台真实输入)。

### 两种模式都一样

- bridge 输出写到 `bridge\server.log`;端口 47821 已有 bridge 在跑时沿用旧的。
- bridge 每做一步(买卡、搜卡、换位、合装备、出战……)都会记到左下日志;Claude 用 `cw_say` 上报的思考(口语化、像主播边打边聊)显示在桌宠头顶气泡和思考记录里,`goal` 显示为当前游戏目标。
- 关闭 小克工作台 时,它启动的 bridge 一起结束。
- 命令行:`小克工作台.exe --cw-start` 打开后直接执行上面的流程。工作台默认最大化启动。

配置 `cw.json`(首次点击时自动生成,一般不用改):

| 键 | 默认 |
|---|---|
| `mode` | `cloud`(内嵌云游戏)或 `local`(本机客户端) |
| `cdp_port` | 9223,内嵌浏览器调试端口 |
| `project` | 自动找上级目录里有 `bridge\server.py` 的文件夹 |
| `python` | `<project>\vendor\March7thAssistant\.venv\Scripts\python.exe` |
| `game_path` / `game_title` | 本地模式用,读 M7A `config.yaml` 的 `game_path` / `game_title_name` |
| `bridge_port` | 47821 |

内嵌浏览器接口:`POST /api/cloud {action: open|hide|reload|status}`。源码在 `src\CurrencyWars.cs`、`src\CloudView.cs`、bridge 侧 `bridge\webview_game.py`。需要 WebView2 运行时(Win11 自带)。

## 录像

操作面板的 **● 开始录像** 按钮(录制中显示「■ 停止录像 时:分:秒」),或接口 `POST /api/record {action: start|stop|status, fps?: 60}`。

- 录的是 小克工作台 整个窗口(内嵌游戏画面、思考、日志、桌宠)。
- **首选窗口采集**(ffmpeg 8+ 的 gfxcapture,也就是 Windows Graphics Capture,和 OBS「窗口采集」同一套接口):只录工作台窗口自己的画面,**被别的窗口挡住、切到桌面都不会录进去**(最小化时没有画面);GPU 抓取 + 显卡 HEVC 编码,原分辨率 60 FPS。`/api/record status` 的 `method` 显示 `wgc`。
- **没有 ffmpeg 8+ 时**,工作台在主屏上用 ffmpeg ddagrab(桌面复制,GPU 抓屏 + 显卡 HEVC 编码):原分辨率 60 FPS,几乎不占 CPU,4K 约 60 MB/分钟。抓的是屏幕上那块区域,**录像期间别让其他窗口挡住工作台**,也别移动/改变窗口大小。
- 工作台不在主屏或显卡编码不可用时,退回 PrintWindow 抓窗口(被挡住也能录,但大窗口只有十几 FPS,画面缩到 1920 宽)。`/api/record status` 的 `method` 显示用的是哪种。
- **声音**:按进程录(Windows 进程回环,Win10 2004+/Win11),只录游戏的声音,别的程序的提示音不会进去:云游戏录内嵌浏览器,本地模式录 `StarRail.exe`。录制时先写 `录像\<时间>.pcm`,停止时用 ffmpeg 合进 mp4(AAC 192k)并删掉 pcm;合成失败时 pcm 会留着。`/api/record status` 的 `audio` 显示录音状态。
- 存到程序目录 `录像\<时间>.mp4`,ffmpeg 的报错写在 `录像\ffmpeg.log`。mp4 用分片格式,程序意外退出也能播放已录部分。
- 需要 ffmpeg:推荐 8.0 以上(带 gfxcapture,例如 `winget install Gyan.FFmpeg`),程序会自动在 winget 安装位置和 PATH 里找支持 gfxcapture 的版本;否则用 7.x(带 ddagrab 和 nvenc)。普通 ffmpeg 查找顺序:按顺序找 `ffmpeg.txt` 里写的路径 → 程序目录 `ffmpeg.exe` / `ffmpeg\ffmpeg.exe` → PATH → 本机已知位置(FormatFactory 的 ffmpeg 7.1)。
- 关闭 小克工作台 时会自动停止并保存录像。
- 货币战争 MCP 里对应 `cw_record`。

## 桌宠素材

桌宠默认来自 [kirito1207/claudia-pet](https://github.com/kirito1207/claudia-pet),素材放在 `pet\` 目录,授权为 CC BY-NC-SA 4.0(需署名、**不得商用**),详见 `pet\NOTICE.md`。重新下载或更新素材:运行 `tools\fetch-pet.ps1`(先关闭 小克工作台)。

额外角色桌宠来自 [lXiaoSel1/AI-Buddy](https://github.com/lXiaoSel1/AI-Buddy)(DeepSeek、Claude、GPT、Gemini、Qwen、Kimi、Grok、Seed、MiniMax、GLM、混元),素材放在 `pets\` 目录,授权为 Apache-2.0 + Commons Clause(详见 `pets\NOTICE.md`)。重新下载或更新 AI-Buddy 桌宠:运行 `python tools\fetch-ai-buddy-pets.py`。删除 `pet\` 与 `pets\` 目录则不显示桌宠,其他功能不受影响。

「思考」标题下有 **左侧人设 / 右侧人设** 两个独立下拉框，两边都能选择所有已安装角色，包括 GPT + DeepSeek，也可以选择相同角色。选择后立即更新形象、气泡名称及后续对话和操作日志的署名；已有记录保留当时的名称。左侧选择保存在 `pet-main.txt`，右侧继续使用 `pet.txt`，重启后恢复；首次使用默认 Claude + DeepSeek（兼容原来的右侧选择）。

这里切换的是人设和显示名称，实际模型调用保持原配置。接口仍用 `agent=xiaoke`（左侧，或省略）与 `agent=ds`（右侧）区分发言位置，也可以使用 `main` / `partner`，不随人设名称变化。

## 已知限制

- `background` 方式依赖目标程序处理投递的消息。少数控件会读取真实鼠标位置(例如「字符映射表」的字符格子),点击位置会不准;右键菜单、下拉菜单这类弹出层在后台也不稳定。遇到时用 `ui_action` 或 `mode: "real"`。
- `real` 方式执行的一两秒内会占用真实鼠标键盘,窗口会短暂出现在屏幕上。
- 后台窗口是被移到屏幕外,不是隐藏;任务栏上仍有图标。如果 小克工作台 被强制结束(而非正常关闭),这些窗口会留在屏幕外,重新打开 小克工作台 →「接管已打开的窗口」→「显示到前台」即可找回。
- 以管理员身份运行的程序,需要 小克工作台 也以管理员身份运行才能操作。
- 接口只监听 127.0.0.1 并校验令牌;拿到令牌的本机程序可以启动任意程序,请不要泄露 `token.txt`。
