using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace AppPilot
{
    public class PlanStep
    {
        public string Text;
        public string Status; // pending | in_progress | done
    }

    /// <summary>对话里的一句话:谁说的、什么类型(提议/同意/反对/决定/执行…)、内容。</summary>
    public class DialogEntry
    {
        public DateTime Time;
        public string Who;   // xiaoke | ds
        public string Name;
        public string Kind;  // say | propose | agree | object | decide | exec | ask
        public string Text;
    }

    /// <summary>
    /// What the agents say they are thinking and doing; shown in the side panel.
    /// 支持两个 Agent 同台:小克(xiaoke,主决策)和 DS(ds,识别与执行),各自有气泡里的当前发言和桌宠表情,
    /// 另有一条共享的对话记录。不带 agent 参数时一切照旧(按小克处理)。
    /// </summary>
    public class AgentState
    {
        public const string Main = "xiaoke";
        public const string Partner = "ds";
        public const int MaxSpeakers = 4;
        /// <summary>4 个发言位的标识:1 号位小克、2 号位 DS、3/4 号位 p3/p4。</summary>
        public static readonly string[] Ids = { Main, Partner, "p3", "p4" };

        class Speaker
        {
            public string Name;
            public string Thought = "";
            public string Mood = "idle";
            public DateTime MoodTime;
        }

        readonly object sync = new object();
        readonly Dictionary<string, Speaker> speakers = new Dictionary<string, Speaker>
        {
            { Main, new Speaker { Name = "小克" } }, { Partner, new Speaker { Name = "DS" } },
            { "p3", new Speaker { Name = "3号" } }, { "p4", new Speaker { Name = "4号" } }
        };

        /// <summary>发言位序号(0 起);未知时为 0。</summary>
        public static int SlotOf(string who) { return Math.Max(0, Array.IndexOf(Ids, Normalize(who))); }
        List<PlanStep> plan = new List<PlanStep>();
        List<string> goals = new List<string>();
        readonly List<string> history = new List<string>();
        readonly List<DialogEntry> dialog = new List<DialogEntry>();

        public event Action Changed;

        public static string Normalize(string who)
        {
            string w = (who ?? "").Trim().ToLowerInvariant();
            if (w == "ds" || w == "deepseek" || w == "partner" || w == "workbuddy" || w == "dsh" || w == "2" || w == "p2") return Partner;
            if (w == "p3" || w == "3") return "p3";
            if (w == "p4" || w == "4") return "p4";
            return Main;
        }

        /// <summary>agent 参数也可以直接写显示名(如「ChatGPT」),按当前各位的名字匹配;匹配不到再按标识解析。</summary>
        public string Resolve(string who)
        {
            string w = (who ?? "").Trim();
            lock (sync)
                foreach (KeyValuePair<string, Speaker> kv in speakers)
                    if (w != "" && string.Equals(kv.Value.Name, w, StringComparison.OrdinalIgnoreCase)) return kv.Key;
            return Normalize(w);
        }

        public string NameOf(string who) { lock (sync) return Get(who).Name; }

        public void SetDisplayName(string who, string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return;
            lock (sync) Get(who).Name = name;
            Fire();
        }

        public static string KindLabel(string kind)
        {
            switch ((kind ?? "").ToLowerInvariant())
            {
                case "propose": return "提议";
                case "agree": return "同意";
                case "object": return "反对";
                case "decide": return "决定";
                case "exec": return "执行";
                case "ask": return "提问";
                default: return "";
            }
        }

        static string MoodForKind(string kind)
        {
            switch ((kind ?? "").ToLowerInvariant())
            {
                case "propose": return "writing";
                case "agree": return "happy";
                case "object": return "oops";
                case "exec": return "running";
                default: return "thinking";
            }
        }

        Speaker Get(string who) { return speakers[Normalize(who)]; }

        /// <summary>小克当前的话(兼容旧调用)。</summary>
        public string Thought { get { return ThoughtOf(Main); } }
        public string ThoughtOf(string who) { lock (sync) return Get(who).Thought; }

        /// <summary>idle | thinking | writing | running | happy | oops. Falls back to idle when the agent goes quiet.</summary>
        public string Mood { get { return MoodOf(Main); } }
        public string MoodOf(string who)
        {
            lock (sync)
            {
                Speaker s = Get(who);
                double age = (DateTime.Now - s.MoodTime).TotalSeconds;
                if (age > ((s.Mood == "happy" || s.Mood == "oops") ? 6 : 25)) s.Mood = "idle";
                return s.Mood;
            }
        }

        void SetMood(string who, string m) { Speaker s = Get(who); s.Mood = m; s.MoodTime = DateTime.Now; }
        public List<PlanStep> Plan { get { lock (sync) return plan.ToList(); } }
        public List<string> History { get { lock (sync) return history.ToList(); } }
        /// <summary>当前游戏目标(如「升 6 搜流萤」),显示在面板顶部。</summary>
        public List<string> Goals { get { lock (sync) return goals.ToList(); } }
        /// <summary>对话记录,旧的在前。</summary>
        public List<DialogEntry> Dialog { get { lock (sync) return dialog.ToList(); } }
        /// <summary>小克的思考记录(兼容旧调用)。</summary>
        public List<KeyValuePair<DateTime, string>> Thoughts
        {
            get { lock (sync) return dialog.Where(d => d.Who == Main).Select(d => new KeyValuePair<DateTime, string>(d.Time, d.Text)).ToList(); }
        }

        public void SetGoals(List<string> g)
        {
            lock (sync) { goals = g.Where(x => !string.IsNullOrWhiteSpace(x)).Select(x => x.Trim()).ToList(); Append("目标: " + string.Join(" / ", goals.ToArray())); }
            Fire();
        }

        public void SetThought(string t) { Say(Main, t, "say"); }

        /// <summary>某个 Agent 说一句话:更新它的气泡和表情,并记入对话。</summary>
        public void Say(string who, string text, string kind)
        {
            who = Normalize(who);
            lock (sync)
            {
                Speaker s = Get(who);
                s.Thought = text ?? "";
                if (s.Thought != "")
                {
                    dialog.Add(new DialogEntry { Time = DateTime.Now, Who = who, Name = s.Name, Kind = (kind ?? "say").ToLowerInvariant(), Text = s.Thought });
                    if (dialog.Count > 400) dialog.RemoveRange(0, 80);
                }
                string label = KindLabel(kind);
                Append(NameOf(who) + (label != "" ? "【" + label + "】" : "") + ": " + s.Thought);
                SetMood(who, MoodForKind(kind));
            }
            Fire();
        }

        public void SetPlan(List<PlanStep> p)
        {
            lock (sync) plan = p;
            Fire();
        }

        /// <summary>Marks 1-based step n as in progress and everything before it as done.</summary>
        public void SetStep(int n)
        {
            lock (sync)
            {
                for (int i = 0; i < plan.Count; i++)
                    plan[i].Status = i < n - 1 ? "done" : (i == n - 1 ? "in_progress" : "pending");
                if (plan.Count > 0 && n > plan.Count) { Append("计划全部完成"); SetMood(Main, "happy"); }
            }
            Fire();
        }

        public void Record(string what, string reason, string mood) { Record(Main, what, reason, mood); }

        public void Record(string who, string what, string reason, string mood)
        {
            lock (sync)
            {
                string prefix = NameOf(who) + " 操作: ";
                Append(prefix + what + (string.IsNullOrEmpty(reason) ? "" : "  —— " + reason));
                SetMood(who, mood);
            }
            Fire();
        }

        public void Fail(string message) { Fail(Main, message); }

        public void Fail(string who, string message)
        {
            lock (sync) { Append(NameOf(who) + " 出错: " + message); SetMood(who, "oops"); }
            Fire();
        }

        public void Clear()
        {
            lock (sync)
            {
                foreach (Speaker s in speakers.Values) { s.Thought = ""; s.Mood = "idle"; }
                plan = new List<PlanStep>(); goals = new List<string>(); history.Clear(); dialog.Clear();
            }
            Fire();
        }

        void Append(string line)
        {
            history.Add(DateTime.Now.ToString("HH:mm:ss") + "  " + line);
            if (history.Count > 500) history.RemoveRange(0, 100);
        }

        void Fire()
        {
            Action a = Changed;
            if (a != null) a();
        }

        public Dictionary<string, object> Describe()
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            lock (sync)
            {
                d["thought"] = speakers[Main].Thought;
                d["ds_thought"] = speakers[Partner].Thought;
                d["main_name"] = speakers[Main].Name;
                d["partner_name"] = speakers[Partner].Name;
                d["speakers"] = Ids.Select(id => new Dictionary<string, object>
                {
                    { "agent", id }, { "name", speakers[id].Name }, { "thought", speakers[id].Thought }
                }).ToList();
                d["goal"] = goals.ToList();
                d["plan"] = plan.Select(s => new Dictionary<string, object> { { "text", s.Text }, { "status", s.Status } }).ToList();
                d["dialog"] = dialog.Skip(Math.Max(0, dialog.Count - 10)).Select(e => new Dictionary<string, object>
                {
                    { "time", e.Time.ToString("HH:mm:ss") }, { "who", e.Who }, { "name", e.Name }, { "kind", e.Kind }, { "text", e.Text }
                }).ToList();
            }
            return d;
        }
    }

    /// <summary>Loopback-only HTTP API. Every request must carry the token, which also blocks calls from web pages.</summary>
    public class ApiServer
    {
        readonly WindowManager mgr;
        readonly AgentState agent;
        public Recorder Recorder;
        public Func<string, Dictionary<string, object>> Cloud;
        readonly string token;
        readonly int port;
        TcpListener listener;

        public Action<string> Log;

        public ApiServer(WindowManager mgr, AgentState agent, int port, string token)
        {
            this.mgr = mgr; this.agent = agent; this.port = port; this.token = token;
        }

        public void Start()
        {
            listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start();
            Thread t = new Thread(AcceptLoop);
            t.IsBackground = true;
            t.Start();
        }

        void AcceptLoop()
        {
            while (true)
            {
                TcpClient c;
                try { c = listener.AcceptTcpClient(); } catch { return; }
                ThreadPool.QueueUserWorkItem(delegate { Serve(c); });
            }
        }

        void Serve(TcpClient client)
        {
            try
            {
                using (client)
                using (NetworkStream ns = client.GetStream())
                {
                    ns.ReadTimeout = 15000;
                    string method, target;
                    Dictionary<string, string> headers;
                    byte[] body;
                    if (!ReadRequest(ns, out method, out target, out headers, out body)) return;

                    int status = 200;
                    string contentType = "application/json; charset=utf-8";
                    byte[] payload;
                    try
                    {
                        object result = Handle(method, target, headers, body);
                        if (result is byte[]) { payload = (byte[])result; contentType = "image/png"; }
                        else if (result is string) { payload = Encoding.UTF8.GetBytes((string)result); contentType = "text/plain; charset=utf-8"; }
                        else payload = Encoding.UTF8.GetBytes(Json().Serialize(result));
                    }
                    catch (ApiException ex)
                    {
                        status = ex.Status; payload = Error(ex.Message);
                        if (ex.Status != 401 && ex.Status != 403) agent.Fail(ex.Message);
                    }
                    catch (Exception ex) { status = 500; payload = Error(ex.GetType().Name + ": " + ex.Message); agent.Fail(ex.Message); }

                    string head = "HTTP/1.1 " + status + " " + (status == 200 ? "OK" : "Error") + "\r\n" +
                        "Content-Type: " + contentType + "\r\nContent-Length: " + payload.Length + "\r\nConnection: close\r\n\r\n";
                    byte[] hb = Encoding.ASCII.GetBytes(head);
                    ns.Write(hb, 0, hb.Length);
                    ns.Write(payload, 0, payload.Length);
                }
            }
            catch { }
        }

        static JavaScriptSerializer Json()
        {
            JavaScriptSerializer js = new JavaScriptSerializer();
            js.MaxJsonLength = int.MaxValue;
            return js;
        }

        static byte[] Error(string message)
        {
            return Encoding.UTF8.GetBytes(Json().Serialize(new Dictionary<string, object> { { "error", message } }));
        }

        static bool ReadRequest(NetworkStream ns, out string method, out string target, out Dictionary<string, string> headers, out byte[] body)
        {
            method = target = null; headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase); body = new byte[0];
            MemoryStream head = new MemoryStream();
            int state = 0;
            while (state < 4)
            {
                int b = ns.ReadByte();
                if (b < 0 || head.Length > 65536) return false;
                head.WriteByte((byte)b);
                if (b == (state % 2 == 0 ? '\r' : '\n')) state++; else state = b == '\r' ? 1 : 0;
            }
            string[] lines = Encoding.ASCII.GetString(head.ToArray()).Split(new[] { "\r\n" }, StringSplitOptions.RemoveEmptyEntries);
            if (lines.Length == 0) return false;
            string[] first = lines[0].Split(' ');
            if (first.Length < 2) return false;
            method = first[0].ToUpperInvariant();
            target = first[1];
            for (int i = 1; i < lines.Length; i++)
            {
                int k = lines[i].IndexOf(':');
                if (k > 0) headers[lines[i].Substring(0, k).Trim()] = lines[i].Substring(k + 1).Trim();
            }
            string cl;
            int len;
            if (headers.TryGetValue("Content-Length", out cl) && int.TryParse(cl, out len) && len > 0)
            {
                if (len > 16 * 1024 * 1024) return false;
                body = new byte[len];
                int got = 0;
                while (got < len)
                {
                    int n = ns.Read(body, got, len - got);
                    if (n <= 0) return false;
                    got += n;
                }
            }
            return true;
        }

        object Handle(string method, string target, Dictionary<string, string> headers, byte[] body)
        {
            string path = target, query = "";
            int q = target.IndexOf('?');
            if (q >= 0) { path = target.Substring(0, q); query = target.Substring(q + 1); }
            path = path.TrimEnd('/');
            if (method == "OPTIONS") throw new ApiException(403, "不接受跨域请求");
            if (headers.ContainsKey("Origin")) throw new ApiException(403, "不接受来自网页的请求");

            string given = null, auth;
            if (headers.TryGetValue("Authorization", out auth) && auth.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase)) given = auth.Substring(7).Trim();
            else headers.TryGetValue("X-Token", out given);
            if (given != token) throw new ApiException(401, "令牌缺失或错误:请带上请求头 Authorization: Bearer <token.txt 的内容>");

            Dictionary<string, object> a = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            foreach (string pair in query.Split(new[] { '&' }, StringSplitOptions.RemoveEmptyEntries))
            {
                int e = pair.IndexOf('=');
                string k = e < 0 ? pair : pair.Substring(0, e), v = e < 0 ? "" : pair.Substring(e + 1);
                a[Uri.UnescapeDataString(k)] = Uri.UnescapeDataString(v.Replace('+', ' '));
            }
            if (body.Length > 0)
            {
                Dictionary<string, object> parsed;
                try { parsed = Json().DeserializeObject(Encoding.UTF8.GetString(body)) as Dictionary<string, object>; }
                catch (Exception ex) { throw new ApiException(400, "请求体不是合法 JSON: " + ex.Message); }
                if (parsed == null) throw new ApiException(400, "请求体必须是 JSON 对象");
                foreach (KeyValuePair<string, object> kv in parsed) a[kv.Key] = kv.Value;
            }
            return Route(path.ToLowerInvariant(), a);
        }

        // ---------- argument helpers ----------

        static string Str(Dictionary<string, object> a, string key, string def)
        {
            object v;
            return a.TryGetValue(key, out v) && v != null ? Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture) : def;
        }

        static int Int(Dictionary<string, object> a, string key, int def)
        {
            object v;
            if (!a.TryGetValue(key, out v) || v == null) return def;
            try { return (int)Math.Round(Convert.ToDouble(v, System.Globalization.CultureInfo.InvariantCulture)); }
            catch { throw new ApiException(400, key + " 必须是数字"); }
        }

        static int Need(Dictionary<string, object> a, string key)
        {
            if (!a.ContainsKey(key)) throw new ApiException(400, "缺少参数 " + key);
            return Int(a, key, 0);
        }

        static bool Bool(Dictionary<string, object> a, string key, bool def)
        {
            object v;
            if (!a.TryGetValue(key, out v) || v == null) return def;
            if (v is bool) return (bool)v;
            string s = v.ToString().ToLowerInvariant();
            return s == "true" || s == "1" || s == "yes";
        }

        ManagedWindow Win(Dictionary<string, object> a) { return mgr.Get(Need(a, "id")); }

        static bool Real(Dictionary<string, object> a)
        {
            string mode = Str(a, "mode", "background").ToLowerInvariant();
            if (mode != "background" && mode != "real") throw new ApiException(400, "mode 只能是 background 或 real");
            return mode == "real";
        }

        static Dictionary<string, object> Ok()
        {
            return new Dictionary<string, object> { { "ok", true } };
        }

        void Note(Dictionary<string, object> a, string what, string mood = "running")
        {
            agent.Record(what, Str(a, "reason", ""), mood);
            if (Log != null) Log(what);
        }

        // ---------- routes ----------

        object Route(string path, Dictionary<string, object> a)
        {
            ManagedWindow w;
            switch (path)
            {
                case "/api/help":
                    return HelpText;

                case "/api/windows":
                    mgr.Refresh();
                    return mgr.List().Select(x => mgr.Describe(x)).ToList();

                case "/api/all_windows":
                    return mgr.EnumTopLevel().Where(h => Native.Title(h) != "").Select(h => mgr.Describe(h)).ToList();

                case "/api/launch":
                {
                    string p = Str(a, "path", null), args = Str(a, "args", "");
                    Note(a, "启动 " + p + (args == "" ? "" : " " + args));
                    List<ManagedWindow> added = mgr.Launch(p, args, Bool(a, "background", true), Int(a, "wait_ms", 8000));
                    Dictionary<string, object> r = Ok();
                    r["windows"] = added.Select(x => mgr.Describe(x)).ToList();
                    if (added.Count == 0) r["note"] = "程序已启动,但等待时间内没有出现新窗口;稍后可调用 /api/all_windows 再 /api/attach";
                    return r;
                }

                case "/api/attach":
                {
                    IntPtr h = IntPtr.Zero;
                    if (a.ContainsKey("hwnd")) h = new IntPtr(Convert.ToInt64(a["hwnd"]));
                    else
                    {
                        string title = Str(a, "title", null);
                        if (string.IsNullOrEmpty(title)) throw new ApiException(400, "需要 hwnd 或 title");
                        h = mgr.EnumTopLevel().FirstOrDefault(x => Native.Title(x).IndexOf(title, StringComparison.OrdinalIgnoreCase) >= 0);
                        if (h == IntPtr.Zero) throw new ApiException(404, "没有标题包含 \"" + title + "\" 的窗口");
                    }
                    w = mgr.Add(h, Bool(a, "background", false));
                    Note(a, "接管窗口 #" + w.Id + " " + Native.Title(h));
                    return mgr.Describe(w);
                }

                case "/api/screenshot":
                {
                    w = Win(a);
                    byte[] png = mgr.CapturePng(w);
                    if (Str(a, "format", "png") != "json") return png;
                    Dictionary<string, object> r = mgr.Describe(w);
                    r["png_base64"] = Convert.ToBase64String(png);
                    return r;
                }

                case "/api/click":
                {
                    w = Win(a);
                    int x = Need(a, "x"), y = Need(a, "y");
                    string button = Str(a, "button", "left");
                    bool dbl = Bool(a, "double", false);
                    Note(a, "#" + w.Id + " " + (dbl ? "双击" : "点击") + (button == "left" ? "" : "(" + button + ")") + " (" + x + "," + y + ")");
                    if (Real(a)) mgr.ClickReal(w, x, y, button, dbl); else mgr.Click(w, x, y, button, dbl);
                    return Ok();
                }

                case "/api/drag":
                {
                    w = Win(a);
                    int x1 = Need(a, "x1"), y1 = Need(a, "y1"), x2 = Need(a, "x2"), y2 = Need(a, "y2");
                    Note(a, "#" + w.Id + " 拖动 (" + x1 + "," + y1 + ") → (" + x2 + "," + y2 + ")");
                    if (Real(a)) mgr.DragReal(w, x1, y1, x2, y2, Str(a, "button", "left"), Int(a, "steps", 20), Int(a, "duration_ms", 400));
                    else mgr.Drag(w, x1, y1, x2, y2, Str(a, "button", "left"), Int(a, "steps", 20), Int(a, "duration_ms", 400));
                    return Ok();
                }

                case "/api/type":
                {
                    w = Win(a);
                    string text = Str(a, "text", "");
                    Note(a, "#" + w.Id + " 输入文字 \"" + (text.Length > 40 ? text.Substring(0, 40) + "…" : text).Replace("\n", "⏎") + "\"", "writing");
                    if (Real(a)) mgr.TypeReal(w, text); else mgr.TypeText(w, text);
                    return Ok();
                }

                case "/api/key":
                {
                    w = Win(a);
                    string keys = Str(a, "keys", null);
                    Note(a, "#" + w.Id + " 按键 " + keys, "writing");
                    if (Real(a)) mgr.KeysReal(w, keys); else mgr.PressKeys(w, keys);
                    return Ok();
                }

                case "/api/scroll":
                {
                    w = Win(a);
                    int x = Need(a, "x"), y = Need(a, "y"), d = Int(a, "delta", -3);
                    Note(a, "#" + w.Id + " 滚动 " + d + " 格 (" + x + "," + y + ")");
                    if (Real(a)) mgr.ScrollReal(w, x, y, d); else mgr.Scroll(w, x, y, d);
                    return Ok();
                }

                case "/api/window":
                {
                    w = Win(a);
                    string action = Str(a, "action", "").ToLowerInvariant();
                    Note(a, "#" + w.Id + " 窗口操作 " + action);
                    switch (action)
                    {
                        case "background": mgr.SendToBackground(w); break;
                        case "foreground": mgr.BringToForeground(w, true); break;
                        case "resize": mgr.Resize(w, Need(a, "width"), Need(a, "height")); break;
                        case "close": mgr.Close(w); break;
                        case "release": mgr.Remove(w); break;
                        default: throw new ApiException(400, "action 只能是 background/foreground/resize/close/release");
                    }
                    return Ok();
                }

                case "/api/ui_tree":
                    w = Win(a);
                    return mgr.UiTree(w, Int(a, "max", 300));

                case "/api/ui_action":
                {
                    w = Win(a);
                    string action = Str(a, "action", "invoke");
                    string name = Str(a, "name", null), aid = Str(a, "automation_id", null);
                    int el = Int(a, "element", -1);
                    Note(a, "#" + w.Id + " 界面元素 " + action + " " + (el >= 0 ? "[" + el + "]" : (aid ?? name)));
                    Dictionary<string, object> r = Ok();
                    r["result"] = mgr.UiAction(w, el, name, aid, action, Str(a, "value", null));
                    return r;
                }

                case "/api/cloud":
                    if (Cloud == null) throw new ApiException(500, "内嵌云游戏不可用");
                    return Cloud(Str(a, "action", "status"));

                case "/api/record":
                {
                    if (Recorder == null) throw new ApiException(500, "录像不可用");
                    string act = Str(a, "action", "status");
                    if (act == "start") return Recorder.Start(Int(a, "fps", 60));
                    if (act == "stop") return Recorder.Stop();
                    return Recorder.Describe();
                }

                case "/api/status":
                {
                    if (Bool(a, "clear", false)) agent.Clear();
                    object planObj;
                    if (a.TryGetValue("plan", out planObj) && planObj != null)
                    {
                        IEnumerable items = planObj as IEnumerable;
                        if (items == null || planObj is string) throw new ApiException(400, "plan 必须是数组");
                        List<PlanStep> plan = new List<PlanStep>();
                        foreach (object item in items)
                        {
                            PlanStep s = new PlanStep();
                            Dictionary<string, object> d = item as Dictionary<string, object>;
                            if (d != null)
                            {
                                Dictionary<string, object> ci = new Dictionary<string, object>(d, StringComparer.OrdinalIgnoreCase);
                                s.Text = Str(ci, "text", "");
                                s.Status = Str(ci, "status", "pending").ToLowerInvariant();
                                if (s.Status != "done" && s.Status != "in_progress") s.Status = "pending";
                            }
                            else { s.Text = Convert.ToString(item); s.Status = "pending"; }
                            plan.Add(s);
                        }
                        agent.SetPlan(plan);
                    }
                    object goalObj;
                    if (a.TryGetValue("goal", out goalObj) && goalObj != null)
                    {
                        List<string> goals = new List<string>();
                        if (goalObj is string) goals.AddRange(((string)goalObj).Split(new[] { '\n', '|' },StringSplitOptions.RemoveEmptyEntries));
                        else if (goalObj is IEnumerable) foreach (object item in (IEnumerable)goalObj) goals.Add(Convert.ToString(item));
                        agent.SetGoals(goals);
                    }
                    if (a.ContainsKey("step")) agent.SetStep(Int(a, "step", 0));
                    string who = agent.Resolve(Str(a, "agent", AgentState.Main));
                    string thought = Str(a, "thought", null) ?? Str(a, "text", null);
                    if (thought != null) agent.Say(who, thought, Str(a, "kind", "say"));
                    string note = Str(a, "note", null);
                    if (note != null)
                    {
                        string mood = Str(a, "mood", "running");
                        if (mood == "oops") agent.Fail(who, note); else agent.Record(who, note, Str(a, "reason", null), mood);
                    }
                    return agent.Describe();
                }
            }
            throw new ApiException(404, "未知接口 " + path + ",GET /api/help 查看全部接口");
        }

        public const string HelpText =
@"小克工作台 接口(仅本机 127.0.0.1;每个请求需带请求头 Authorization: Bearer <token>)
参数可放在 JSON 请求体或查询串中。坐标均为相对窗口左上角的像素,与截图像素一一对应。
所有操作类接口都可附带 reason(这一步的原因),会显示在程序右侧的 AI 面板里。

GET  /api/windows                      受管窗口列表(id、标题、大小、是否在后台)
GET  /api/all_windows                  桌面上所有可接管的窗口
POST /api/launch   {path,args?,background?=true,wait_ms?}   启动程序并接管它的窗口
POST /api/attach   {hwnd|title,background?=false}            接管一个已打开的窗口
GET  /api/screenshot?id=1[&format=json] 窗口截图(PNG;json 时返回 base64)
POST /api/click    {id,x,y,button?=left|right|middle,double?}
POST /api/drag     {id,x1,y1,x2,y2,button?,steps?,duration_ms?}
POST /api/type     {id,text}           输入文字(\n 表示回车)
POST /api/key      {id,keys}           按键或组合键:enter、tab、esc、f5、ctrl+s、alt+f4、shift+tab …
POST /api/scroll   {id,x,y,delta}      滚轮,delta 为格数,负数向下
POST /api/window   {id,action}         background | foreground | resize(width,height) | close | release
GET  /api/ui_tree?id=1[&max=300]       界面元素树(名称、类型、位置、可用动作)
POST /api/ui_action {id,element|name|automation_id,action?=invoke|set_value|toggle|select|expand|collapse,value?}
POST /api/cloud    {action=open|hide|reload|status}   内嵌云·星穹铁道(WebView2,带 CDP 调试端口)
POST /api/record   {action=start|stop|status, fps?=60}   录制整个 小克工作台 窗口,存到 录像\*.mp4
POST /api/status   {agent?=xiaoke|ds|p3|p4 或 1-4 或显示名,kind?=say|propose|agree|object|decide|exec|ask,thought?,goal?:文字或[文字](当前游戏目标,| 或换行分隔),plan?:[文字或{text,status}],step?,clear?,note?,reason?,mood?}   上报 AI 的思考与计划(step 为当前进行到第几步,从 1 开始);note 记一条操作日志,mood=oops 记为出错

建议流程:status 上报计划 → launch → screenshot 看界面 → click/type/key 操作 → screenshot 验证。
click/drag/type/key/scroll 都可带 mode:
  background(默认)  纯后台投递消息,不动真实鼠标键盘、不抢焦点。适用于大多数传统桌面程序。
  real              兜底:把窗口短暂调到前台,用真实鼠标键盘操作,完成后自动放回后台并还原鼠标位置和原前台窗口。
                    适用于不响应后台消息的程序(WinUI/UWP 新式界面,如新版画图、计算器;以及部分游戏)。
若操作后截图没有变化:按钮类先试 ui_tree + ui_action(仍是纯后台),否则改用 mode=real。";
    }
}
