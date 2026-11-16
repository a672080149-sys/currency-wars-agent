using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace AppPilot
{
    /// <summary>
    /// 货币战争一键启动:隐藏启动 cw-bridge(Python,继承本程序的管理员权限,不弹窗口),
    /// 启动或接管《崩坏:星穹铁道》窗口。配置在程序目录的 cw.json(首次运行自动生成)。
    /// </summary>
    public class CurrencyWarsLauncher
    {
        readonly WindowManager mgr;
        readonly AgentState agent;
        readonly int apiPort;
        readonly string apiToken;
        Process bridge;
        int busy;

        public CurrencyWarsLauncher(WindowManager mgr, AgentState agent, int apiPort, string apiToken)
        {
            this.mgr = mgr; this.agent = agent; this.apiPort = apiPort; this.apiToken = apiToken;
        }

        static string AppDir { get { return AppDomain.CurrentDomain.BaseDirectory; } }
        static string ConfigPath { get { return Path.Combine(AppDir, "cw.json"); } }

        public static bool IsAdmin()
        {
            using (WindowsIdentity id = WindowsIdentity.GetCurrent())
                return new WindowsPrincipal(id).IsInRole(WindowsBuiltInRole.Administrator);
        }

        // ---------- config ----------

        public class Config
        {
            public string Project, Python, GamePath, GameTitle;
            public string Mode; // cloud(内嵌云游戏,默认) | local(本地客户端)
            public int BridgePort, CdpPort;
        }

        static bool IsProject(string dir)
        {
            return !string.IsNullOrEmpty(dir) && File.Exists(Path.Combine(dir, "bridge", "server.py"));
        }

        static string FindProject(string configured)
        {
            List<string> cands = new List<string>();
            if (!string.IsNullOrEmpty(configured)) cands.Add(configured);
            DirectoryInfo d = new DirectoryInfo(AppDir);
            for (int i = 0; i < 3 && d != null; i++, d = d.Parent) cands.Add(d.FullName);
            return cands.FirstOrDefault(IsProject);
        }

        /// <summary>Reads "key: value # comment" from M7A's config.yaml.</summary>
        static string M7aValue(string project, string key)
        {
            try
            {
                string yaml = Path.Combine(project, "vendor", "March7thAssistant", "config.yaml");
                foreach (string line in File.ReadAllLines(yaml, Encoding.UTF8))
                {
                    if (!line.StartsWith(key + ":")) continue;
                    string v = line.Substring(key.Length + 1);
                    int hash = v.IndexOf(" #");
                    if (hash >= 0) v = v.Substring(0, hash);
                    return v.Trim().Trim('"', '\'');
                }
            }
            catch { }
            return null;
        }

        public static Config LoadConfig()
        {
            Dictionary<string, object> raw = new Dictionary<string, object>();
            try
            {
                if (File.Exists(ConfigPath))
                    raw = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(ConfigPath, Encoding.UTF8))
                          ?? new Dictionary<string, object>();
            }
            catch { }
            Func<string, string> get = k => raw.ContainsKey(k) && raw[k] != null && Convert.ToString(raw[k]) != "" ? Convert.ToString(raw[k]) : null;
            Config c = new Config();
            c.Project = FindProject(get("project"));
            if (c.Project != null)
            {
                c.Python = get("python") ?? Path.Combine(c.Project, @"vendor\March7thAssistant\.venv\Scripts\python.exe");
                c.GamePath = get("game_path") ?? M7aValue(c.Project, "game_path");
                c.GameTitle = get("game_title") ?? M7aValue(c.Project, "game_title_name");
            }
            c.GameTitle = c.GameTitle ?? "崩坏：星穹铁道";
            int port;
            c.BridgePort = int.TryParse(get("bridge_port"), out port) ? port : 47821;
            c.CdpPort = int.TryParse(get("cdp_port"), out port) ? port : 9223;
            c.Mode = (get("mode") ?? "cloud").ToLowerInvariant() == "local" ? "local" : "cloud";
            if (!File.Exists(ConfigPath))
            {
                try
                {
                    Dictionary<string, object> save = new Dictionary<string, object>
                    {
                        { "project", c.Project ?? "" }, { "python", c.Python ?? "" }, { "game_path", c.GamePath ?? "" },
                        { "game_title", c.GameTitle }, { "bridge_port", c.BridgePort }, { "mode", c.Mode }, { "cdp_port", c.CdpPort },
                    };
                    string json = new JavaScriptSerializer().Serialize(save).Replace(",\"", ",\r\n  \"").Replace("{\"", "{\r\n  \"").Replace("}", "\r\n}");
                    File.WriteAllText(ConfigPath, json, new UTF8Encoding(false));
                }
                catch { }
            }
            return c;
        }

        // ---------- start / stop ----------

        static bool PortOpen(int port)
        {
            try
            {
                using (TcpClient c = new TcpClient())
                {
                    IAsyncResult r = c.BeginConnect("127.0.0.1", port, null, null);
                    bool ok = r.AsyncWaitHandle.WaitOne(300) && c.Connected;
                    return ok;
                }
            }
            catch { return false; }
        }

        public bool BridgeRunning { get { return bridge != null && !bridge.HasExited; } }

        /// <summary>Runs on a worker thread; progress goes to the AI panel.</summary>
        public void Start(bool launchGame)
        {
            if (Interlocked.Exchange(ref busy, 1) == 1) return;
            Thread t = new Thread(delegate()
            {
                try { Run(launchGame); }
                catch (Exception ex) { agent.Fail("货币战争启动失败: " + ex.Message); }
                finally { busy = 0; }
            });
            t.IsBackground = true;
            t.Start();
        }

        /// <summary>由 MainForm 提供:在界面线程打开内嵌云游戏页,返回 CDP 端口。</summary>
        public Func<int> OpenCloud;

        void RunCloud(Config c)
        {
            agent.SetPlan(new List<PlanStep>
            {
                new PlanStep { Text = "打开内嵌云·星穹铁道", Status = "pending" },
                new PlanStep { Text = "启动 cw-bridge(后台、无窗口,通过 CDP 操作网页)", Status = "pending" },
                new PlanStep { Text = "登录并进入游戏(首次需要你在画面里登录)", Status = "pending" },
            });
            agent.SetStep(1);
            if (OpenCloud == null) throw new Exception("内嵌浏览器不可用");
            c.CdpPort = OpenCloud();
            DateTime deadline = DateTime.Now.AddSeconds(30);
            while (DateTime.Now < deadline && !PortOpen(c.CdpPort)) Thread.Sleep(300);
            if (!PortOpen(c.CdpPort)) throw new Exception("内嵌浏览器调试端口 " + c.CdpPort + " 没有打开");

            agent.SetStep(2);
            if (c.Project == null) throw new Exception("找不到货币战争项目(需要 bridge\\server.py),请在 cw.json 里填 project");
            StartBridge(c);
            agent.SetStep(3);
            agent.SetThought("云·星穹铁道和 cw-bridge 都准备好了。没登录的话请在左上画面里登录;进入游戏后 Claude 就可以通过 currency-wars MCP 操作,全程后台。");
        }

        void Run(bool launchGame)
        {
            Config c = LoadConfig();
            if (c.Mode == "cloud") { RunCloud(c); return; }
            agent.SetPlan(new List<PlanStep>
            {
                new PlanStep { Text = "启动 cw-bridge(后台、无窗口)", Status = "pending" },
                new PlanStep { Text = launchGame ? "启动星铁" : "找到星铁窗口", Status = "pending" },
                new PlanStep { Text = "接管游戏窗口", Status = "pending" },
            });
            agent.SetStep(1);
            if (c.Project == null) throw new Exception("找不到货币战争项目(需要 bridge\\server.py),请在 cw.json 里填 project");
            StartBridge(c);

            agent.SetStep(2);
            IntPtr h = FindGame(c.GameTitle);
            if (h == IntPtr.Zero && launchGame)
            {
                if (string.IsNullOrEmpty(c.GamePath) || !File.Exists(c.GamePath))
                    throw new Exception("游戏路径不存在: " + c.GamePath + "(在 cw.json 里改 game_path)");
                ProcessStartInfo psi = new ProcessStartInfo(c.GamePath);
                psi.UseShellExecute = true;
                psi.WorkingDirectory = Path.GetDirectoryName(c.GamePath);
                Process.Start(psi);
                agent.Record("启动 " + Path.GetFileName(c.GamePath), "等待游戏窗口出现(最多 3 分钟)", "running");
                DateTime deadline = DateTime.Now.AddMinutes(3);
                while (h == IntPtr.Zero && DateTime.Now < deadline)
                {
                    Thread.Sleep(1000);
                    h = FindGame(c.GameTitle);
                }
            }
            if (h == IntPtr.Zero) throw new Exception("没有找到标题为「" + c.GameTitle + "」的窗口");

            agent.SetStep(3);
            ManagedWindow w = mgr.List().FirstOrDefault(x => x.Hwnd == h) ?? mgr.Add(h, false);
            agent.Record("接管游戏窗口 #" + w.Id, "bridge 用前台真实输入操作游戏,窗口保持在桌面上", "running");
            agent.SetStep(4);
            agent.SetThought("星铁和 cw-bridge 都准备好了。Claude 可以通过 currency-wars MCP 开始操作货币战争。");
        }

        IntPtr FindGame(string title)
        {
            foreach (IntPtr h in mgr.EnumTopLevel())
            {
                string t = Native.Title(h);
                if (t == title || (t.StartsWith(title) && t.Length < title.Length + 4)) return h;
            }
            return IntPtr.Zero;
        }

        void StartBridge(Config c)
        {
            if (BridgeRunning) { agent.Record("cw-bridge 已在运行", null, "running"); return; }
            if (PortOpen(c.BridgePort))
            {
                agent.Record("端口 " + c.BridgePort + " 已有 cw-bridge 在运行,沿用", "如需让它向这里汇报,先关掉旧的 bridge 再点一次", "running");
                return;
            }
            if (!File.Exists(c.Python)) throw new Exception("找不到 Python: " + c.Python);
            string bridgeDir = Path.Combine(c.Project, "bridge");
            string log = Path.Combine(bridgeDir, "server.log");
            ProcessStartInfo psi = new ProcessStartInfo(c.Python, "-X utf8 -u \"" + Path.Combine(bridgeDir, "server.py") + "\"");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.WorkingDirectory = bridgeDir;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["XK_URL"] = "http://127.0.0.1:" + apiPort;
            psi.EnvironmentVariables["XK_TOKEN"] = apiToken;
            psi.EnvironmentVariables["CW_BRIDGE_PORT"] = c.BridgePort.ToString();
            if (c.Mode == "cloud") { psi.EnvironmentVariables["CW_BACKEND"] = "webview"; psi.EnvironmentVariables["CW_CDP_PORT"] = c.CdpPort.ToString(); }
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

            StreamWriter writer = new StreamWriter(log, false, new UTF8Encoding(false));
            writer.AutoFlush = true;
            object sync = new object();
            DataReceivedEventHandler sink = delegate(object s, DataReceivedEventArgs e)
            {
                if (e.Data == null) return;
                lock (sync) { try { writer.WriteLine(DateTime.Now.ToString("HH:mm:ss ") + e.Data); } catch { } }
            };
            Process p = new Process();
            p.StartInfo = psi;
            p.EnableRaisingEvents = true;
            p.OutputDataReceived += sink;
            p.ErrorDataReceived += sink;
            p.Exited += delegate
            {
                int code = -1;
                try { code = p.ExitCode; } catch { }
                lock (sync) { try { writer.Dispose(); } catch { } }
                if (bridge == p) agent.Fail("cw-bridge 已退出(代码 " + code + "),日志: " + log);
            };
            p.Start();
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            bridge = p;
            agent.Record("启动 cw-bridge(pid " + p.Id + ")", "日志写在 bridge\\server.log", "running");

            DateTime deadline = DateTime.Now.AddSeconds(60); // M7A 导入 OCR 模型较慢
            while (DateTime.Now < deadline && !p.HasExited && !PortOpen(c.BridgePort)) Thread.Sleep(500);
            if (p.HasExited) throw new Exception("cw-bridge 启动后立即退出,看 " + log);
            if (!PortOpen(c.BridgePort)) throw new Exception("cw-bridge 60 秒内没有开始监听,看 " + log);
        }

        public void StopBridge()
        {
            Process p = bridge;
            bridge = null;
            if (p == null) return;
            try { if (!p.HasExited) { p.Kill(); p.WaitForExit(3000); } } catch { }
        }
    }
}
