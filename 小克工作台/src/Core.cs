using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Automation;

namespace AppPilot
{
    public class ApiException : Exception
    {
        public int Status;
        public ApiException(int status, string message) : base(message) { Status = status; }
    }

    public class ManagedWindow
    {
        public int Id;
        public IntPtr Hwnd;
        public uint Pid;
        public bool InBackground;
        public bool HasSaved;
        public RECT Saved;
        public IntPtr LastTarget;
        public List<AutomationElement> UiCache;
    }

    /// <summary>Tracks application windows and drives them with posted messages, so they never need focus.</summary>
    public class WindowManager
    {
        readonly object sync = new object();
        readonly object inputLock = new object();
        readonly Dictionary<int, ManagedWindow> wins = new Dictionary<int, ManagedWindow>();
        readonly HashSet<IntPtr> ignored = new HashSet<IntPtr>();
        readonly uint ownPid = (uint)Process.GetCurrentProcess().Id;
        int nextId = 1;

        public event Action Changed;

        static readonly HashSet<string> SkipClasses = new HashSet<string> {
            "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "tooltips_class32",
            "IME", "MSCTFIME UI", "#32768"
        };

        // ---------- enumeration ----------

        public List<IntPtr> EnumTopLevel()
        {
            List<IntPtr> list = new List<IntPtr>();
            Native.EnumWindows(delegate(IntPtr h, IntPtr l)
            {
                if (!Native.IsWindowVisible(h) || Native.IsCloaked(h)) return true;
                if (Native.PidOf(h) == ownPid) return true;
                RECT r;
                if (!Native.GetWindowRect(h, out r) || r.Width < 40 || r.Height < 40) return true;
                if (SkipClasses.Contains(Native.ClassName(h))) return true;
                list.Add(h);
                return true;
            }, IntPtr.Zero);
            return list;
        }

        public List<ManagedWindow> List()
        {
            lock (sync) return wins.Values.OrderBy(w => w.Id).ToList();
        }

        public ManagedWindow Get(int id)
        {
            lock (sync)
            {
                ManagedWindow w;
                if (!wins.TryGetValue(id, out w)) throw new ApiException(404, "没有 id=" + id + " 的受管窗口,请先调用 /api/windows");
                if (!Native.IsWindow(w.Hwnd)) throw new ApiException(404, "窗口 id=" + id + " 已关闭");
                return w;
            }
        }

        bool IsManaged(IntPtr h)
        {
            lock (sync) return wins.Values.Any(w => w.Hwnd == h);
        }

        public Dictionary<string, object> Describe(IntPtr h)
        {
            RECT r;
            Native.GetWindowRect(h, out r);
            uint pid = Native.PidOf(h);
            string proc = "";
            try { proc = Process.GetProcessById((int)pid).ProcessName; } catch { }
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["hwnd"] = h.ToInt64();
            d["title"] = Native.Title(h);
            d["class"] = Native.ClassName(h);
            d["pid"] = pid;
            d["process"] = proc;
            d["x"] = r.Left; d["y"] = r.Top; d["width"] = r.Width; d["height"] = r.Height;
            return d;
        }

        public Dictionary<string, object> Describe(ManagedWindow w)
        {
            Dictionary<string, object> d = Describe(w.Hwnd);
            d["id"] = w.Id;
            d["background"] = w.InBackground;
            return d;
        }

        // ---------- lifecycle ----------

        public List<ManagedWindow> Launch(string path, string args, bool background, int waitMs)
        {
            if (string.IsNullOrWhiteSpace(path)) throw new ApiException(400, "缺少 path");
            HashSet<IntPtr> before = new HashSet<IntPtr>(EnumTopLevel());
            IntPtr fg = Native.GetForegroundWindow();
            uint pid = 0;
            Exception startError = null;
            Thread t = new Thread(delegate()
            {
                try
                {
                    ProcessStartInfo psi = new ProcessStartInfo(path, args ?? "");
                    psi.UseShellExecute = true;
                    using (Process p = Process.Start(psi)) { if (p != null) pid = (uint)p.Id; }
                }
                catch (Exception ex) { startError = ex; }
            });
            t.SetApartmentState(ApartmentState.STA);
            t.Start();
            t.Join();
            if (startError != null) throw new ApiException(400, "启动失败: " + startError.Message);

            List<IntPtr> fresh = new List<IntPtr>();
            DateTime deadline = DateTime.Now.AddMilliseconds(waitMs <= 0 ? 8000 : waitMs);
            while (DateTime.Now < deadline)
            {
                Thread.Sleep(250);
                fresh = EnumTopLevel().Where(h => !before.Contains(h) && !IsManaged(h)).ToList();
                if (fresh.Count > 0)
                {
                    Thread.Sleep(600); // let the app finish creating its main window
                    fresh = EnumTopLevel().Where(h => !before.Contains(h) && !IsManaged(h)).ToList();
                    break;
                }
            }
            if (pid != 0)
            {
                List<IntPtr> own = fresh.Where(h => Native.PidOf(h) == pid).ToList();
                if (own.Count > 0) fresh = own;
            }
            List<ManagedWindow> added = new List<ManagedWindow>();
            foreach (IntPtr h in fresh) added.Add(Add(h, background));
            if (background && fg != IntPtr.Zero && added.Count > 0) Native.SetForegroundWindow(fg);
            return added;
        }

        public ManagedWindow Add(IntPtr h, bool background)
        {
            if (!Native.IsWindow(h)) throw new ApiException(404, "窗口句柄无效");
            ManagedWindow w;
            lock (sync)
            {
                w = wins.Values.FirstOrDefault(x => x.Hwnd == h);
                if (w != null) return w;
                uint pid = Native.PidOf(h);
                if (!wins.Values.Any(x => x.Pid == pid))
                {
                    // Windows this process already had stay the user's; only ones created later are adopted.
                    foreach (IntPtr o in EnumTopLevel()) if (o != h && Native.PidOf(o) == pid) ignored.Add(o);
                }
                ignored.Remove(h);
                w = new ManagedWindow();
                w.Id = nextId++;
                w.Hwnd = h;
                w.Pid = pid;
                wins[w.Id] = w;
            }
            if (background) SendToBackground(w);
            Fire();
            return w;
        }

        public void Remove(ManagedWindow w)
        {
            if (Native.IsWindow(w.Hwnd) && w.InBackground) BringToForeground(w, false);
            lock (sync) { wins.Remove(w.Id); ignored.Add(w.Hwnd); }
            Fire();
        }

        /// <summary>Drops closed windows and adopts new dialogs/windows opened by managed processes.</summary>
        public void Refresh()
        {
            bool changed = false;
            List<IntPtr> adopt = new List<IntPtr>();
            Dictionary<uint, bool> pidBackground = new Dictionary<uint, bool>();
            lock (sync)
            {
                foreach (int id in wins.Keys.ToList())
                    if (!Native.IsWindow(wins[id].Hwnd)) { wins.Remove(id); changed = true; }
                ignored.RemoveWhere(h => !Native.IsWindow(h));
                foreach (ManagedWindow w in wins.Values)
                {
                    bool b;
                    pidBackground[w.Pid] = (pidBackground.TryGetValue(w.Pid, out b) && b) || w.InBackground;
                }
                if (pidBackground.Count > 0)
                    foreach (IntPtr h in EnumTopLevel())
                        if (pidBackground.ContainsKey(Native.PidOf(h)) && !ignored.Contains(h) && !wins.Values.Any(x => x.Hwnd == h))
                            adopt.Add(h);
            }
            foreach (IntPtr h in adopt) Add(h, pidBackground[Native.PidOf(h)]);
            if (changed) Fire();
        }

        void Fire()
        {
            Action a = Changed;
            if (a != null) a();
        }

        // ---------- window placement ----------

        public void SendToBackground(ManagedWindow w)
        {
            if (Native.IsIconic(w.Hwnd) || Native.IsZoomed(w.Hwnd)) Native.ShowWindow(w.Hwnd, Native.SW_SHOWNOACTIVATE);
            if (!w.InBackground)
            {
                Native.GetWindowRect(w.Hwnd, out w.Saved);
                w.HasSaved = true;
            }
            Rectangle vs = System.Windows.Forms.SystemInformation.VirtualScreen;
            // Already off-screen (e.g. left there by a previous run): restore to a visible default instead.
            if (w.Saved.Left >= vs.Right - 40 || w.Saved.Right <= vs.Left + 40) w.HasSaved = false;
            Native.SetWindowPos(w.Hwnd, IntPtr.Zero, vs.Right + 200, vs.Top + 50, 0, 0,
                Native.SWP_NOSIZE | Native.SWP_NOZORDER | Native.SWP_NOACTIVATE);
            w.InBackground = true;
            Fire();
        }

        public void BringToForeground(ManagedWindow w, bool activate)
        {
            if (Native.IsIconic(w.Hwnd)) Native.ShowWindow(w.Hwnd, Native.SW_RESTORE);
            if (w.InBackground)
            {
                int x = w.HasSaved ? w.Saved.Left : 100, y = w.HasSaved ? w.Saved.Top : 100;
                Native.SetWindowPos(w.Hwnd, IntPtr.Zero, x, y, 0, 0,
                    Native.SWP_NOSIZE | Native.SWP_NOZORDER | (activate ? 0 : Native.SWP_NOACTIVATE));
                w.InBackground = false;
            }
            if (activate) Native.SetForegroundWindow(w.Hwnd);
            Fire();
        }

        public void Resize(ManagedWindow w, int width, int height)
        {
            if (width < 100 || height < 100) throw new ApiException(400, "width/height 至少为 100");
            if (Native.IsIconic(w.Hwnd) || Native.IsZoomed(w.Hwnd)) Native.ShowWindow(w.Hwnd, Native.SW_SHOWNOACTIVATE);
            Native.SetWindowPos(w.Hwnd, IntPtr.Zero, 0, 0, width, height,
                Native.SWP_NOMOVE | Native.SWP_NOZORDER | Native.SWP_NOACTIVATE);
        }

        public void Close(ManagedWindow w)
        {
            Native.PostMessage(w.Hwnd, Native.WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
        }

        // ---------- capture ----------

        public Bitmap Capture(ManagedWindow w)
        {
            if (Native.IsIconic(w.Hwnd)) Native.ShowWindow(w.Hwnd, Native.SW_SHOWNOACTIVATE);
            RECT r;
            if (!Native.GetWindowRect(w.Hwnd, out r) || r.Width <= 0 || r.Height <= 0)
                throw new ApiException(500, "无法获取窗口大小");
            Bitmap bmp = new Bitmap(r.Width, r.Height, PixelFormat.Format32bppRgb);
            using (Graphics g = Graphics.FromImage(bmp))
            {
                IntPtr hdc = g.GetHdc();
                try { Native.PrintWindow(w.Hwnd, hdc, 2); } // PW_RENDERFULLCONTENT
                finally { g.ReleaseHdc(hdc); }
            }
            return bmp;
        }

        public byte[] CapturePng(ManagedWindow w)
        {
            using (Bitmap bmp = Capture(w))
            using (MemoryStream ms = new MemoryStream())
            {
                bmp.Save(ms, ImageFormat.Png);
                return ms.ToArray();
            }
        }

        // ---------- mouse ----------

        static POINT ToScreen(IntPtr top, int wx, int wy)
        {
            RECT r;
            Native.GetWindowRect(top, out r);
            return new POINT(r.Left + wx, r.Top + wy);
        }

        /// <summary>Finds the deepest child under a window-relative point; works for hidden/off-screen windows.</summary>
        static IntPtr Resolve(IntPtr top, POINT screen, out POINT client)
        {
            IntPtr cur = top;
            for (int i = 0; i < 32; i++)
            {
                POINT p = screen;
                Native.ScreenToClient(cur, ref p);
                IntPtr child = Native.ChildWindowFromPointEx(cur, p, 7);
                if (child == IntPtr.Zero || child == cur) break;
                cur = child;
            }
            client = screen;
            Native.ScreenToClient(cur, ref client);
            return cur;
        }

        static void ButtonMessages(string button, out uint down, out uint up, out uint dbl, out int mk)
        {
            switch ((button ?? "left").ToLowerInvariant())
            {
                case "right": down = Native.WM_RBUTTONDOWN; up = Native.WM_RBUTTONUP; dbl = Native.WM_RBUTTONDBLCLK; mk = 2; break;
                case "middle": down = Native.WM_MBUTTONDOWN; up = Native.WM_MBUTTONUP; dbl = Native.WM_MBUTTONDBLCLK; mk = 0x10; break;
                case "left": down = Native.WM_LBUTTONDOWN; up = Native.WM_LBUTTONUP; dbl = Native.WM_LBUTTONDBLCLK; mk = 1; break;
                default: throw new ApiException(400, "button 只能是 left/right/middle");
            }
        }

        public void Click(ManagedWindow w, int x, int y, string button, bool dbl)
        {
            Click(w, x, y, button, dbl, false);
        }

        /// <param name="secondOnly">Send only the second half of a double click (the first click was already sent).</param>
        public void Click(ManagedWindow w, int x, int y, string button, bool dbl, bool secondOnly)
        {
            uint down, up, dbm; int mk;
            ButtonMessages(button, out down, out up, out dbm, out mk);
            lock (inputLock)
            {
                POINT c;
                IntPtr t = Resolve(w.Hwnd, ToScreen(w.Hwnd, x, y), out c);
                IntPtr lp = Native.MakeLParam(c.X, c.Y);
                w.LastTarget = t;
                Native.PostMessage(t, Native.WM_MOUSEMOVE, IntPtr.Zero, lp);
                Thread.Sleep(20);
                if (!secondOnly)
                {
                    Native.PostMessage(t, down, new IntPtr(mk), lp);
                    Thread.Sleep(40);
                    Native.PostMessage(t, up, IntPtr.Zero, lp);
                }
                if (dbl)
                {
                    Thread.Sleep(40);
                    Native.PostMessage(t, dbm, new IntPtr(mk), lp);
                    Thread.Sleep(40);
                    Native.PostMessage(t, up, IntPtr.Zero, lp);
                }
            }
        }

        public void Drag(ManagedWindow w, int x1, int y1, int x2, int y2, string button, int steps, int durationMs)
        {
            uint down, up, dbm; int mk;
            ButtonMessages(button, out down, out up, out dbm, out mk);
            if (steps <= 0) steps = 20;
            if (durationMs <= 0) durationMs = 400;
            lock (inputLock)
            {
                POINT c;
                IntPtr t = Resolve(w.Hwnd, ToScreen(w.Hwnd, x1, y1), out c);
                w.LastTarget = t;
                IntPtr lp = Native.MakeLParam(c.X, c.Y);
                Native.PostMessage(t, Native.WM_MOUSEMOVE, IntPtr.Zero, lp);
                Thread.Sleep(20);
                Native.PostMessage(t, down, new IntPtr(mk), lp);
                Thread.Sleep(50);
                for (int i = 1; i <= steps; i++)
                {
                    POINT p = ToScreen(w.Hwnd, x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps);
                    Native.ScreenToClient(t, ref p);
                    lp = Native.MakeLParam(p.X, p.Y);
                    Native.PostMessage(t, Native.WM_MOUSEMOVE, new IntPtr(mk), lp);
                    Thread.Sleep(Math.Max(1, durationMs / steps));
                }
                Thread.Sleep(30);
                Native.PostMessage(t, up, IntPtr.Zero, lp);
            }
        }

        public void Scroll(ManagedWindow w, int x, int y, int clicks)
        {
            lock (inputLock)
            {
                POINT screen = ToScreen(w.Hwnd, x, y), c;
                IntPtr t = Resolve(w.Hwnd, screen, out c);
                int wp = unchecked((int)(((uint)(ushort)(short)(clicks * 120)) << 16));
                Native.PostMessage(t, Native.WM_MOUSEWHEEL, new IntPtr(wp), Native.MakeLParam(screen.X, screen.Y));
            }
        }

        // ---------- keyboard ----------

        IntPtr KeyTarget(ManagedWindow w)
        {
            uint pid;
            uint tid = Native.GetWindowThreadProcessId(w.Hwnd, out pid);
            GUITHREADINFO gi = new GUITHREADINFO();
            gi.cbSize = System.Runtime.InteropServices.Marshal.SizeOf(typeof(GUITHREADINFO));
            if (Native.GetGUIThreadInfo(tid, ref gi) && gi.hwndFocus != IntPtr.Zero &&
                (gi.hwndFocus == w.Hwnd || Native.IsChild(w.Hwnd, gi.hwndFocus)))
                return gi.hwndFocus;
            if (w.LastTarget != IntPtr.Zero && Native.IsWindow(w.LastTarget)) return w.LastTarget;
            return w.Hwnd;
        }

        public void TypeText(ManagedWindow w, string text)
        {
            if (text == null) return;
            text = text.Replace("\r\n", "\r").Replace('\n', '\r');
            lock (inputLock)
            {
                IntPtr t = KeyTarget(w);
                for (int i = 0; i < text.Length; i++)
                {
                    Native.PostMessage(t, Native.WM_CHAR, new IntPtr(text[i]), new IntPtr(1));
                    if (i % 20 == 19) Thread.Sleep(10); else Thread.Sleep(1);
                }
            }
        }

        static readonly Dictionary<string, int> KeyNames = new Dictionary<string, int> {
            {"enter", 0x0D}, {"return", 0x0D}, {"tab", 0x09}, {"esc", 0x1B}, {"escape", 0x1B},
            {"backspace", 0x08}, {"space", 0x20}, {"delete", 0x2E}, {"del", 0x2E}, {"insert", 0x2D},
            {"home", 0x24}, {"end", 0x23}, {"pageup", 0x21}, {"pagedown", 0x22},
            {"left", 0x25}, {"up", 0x26}, {"right", 0x27}, {"down", 0x28}
        };
        static readonly HashSet<int> ExtendedKeys = new HashSet<int> { 0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28 };

        static int VkFromName(string name)
        {
            int vk;
            if (KeyNames.TryGetValue(name, out vk)) return vk;
            int n;
            if (name.Length >= 2 && name[0] == 'f' && int.TryParse(name.Substring(1), out n) && n >= 1 && n <= 24) return 0x70 + n - 1;
            if (name.Length == 1)
            {
                char ch = name[0];
                if (ch >= 'a' && ch <= 'z') return ch - 'a' + 0x41;
                if (ch >= '0' && ch <= '9') return ch;
                short s = Native.VkKeyScan(ch);
                if (s != -1) return s & 0xFF;
            }
            throw new ApiException(400, "不认识的按键: " + name);
        }

        /// <summary>Presses a key or combo such as "enter", "ctrl+s", "alt+f4", "shift+tab".</summary>
        public void PressKeys(ManagedWindow w, string combo)
        {
            List<int> mods;
            int key;
            bool alt;
            ParseCombo(combo, out mods, out key, out alt);
            uint kd = alt ? Native.WM_SYSKEYDOWN : Native.WM_KEYDOWN, ku = alt ? Native.WM_SYSKEYUP : Native.WM_KEYUP;
            lock (inputLock)
            {
                IntPtr t = KeyTarget(w);
                uint pid;
                uint tid = Native.GetWindowThreadProcessId(t, out pid), me = Native.GetCurrentThreadId();
                // Posted keys do not change the target's modifier state, so it is set through the shared input queue.
                bool attached = mods.Count > 0 && Native.AttachThreadInput(me, tid, true);
                byte[] old = new byte[256];
                try
                {
                    if (attached)
                    {
                        Native.GetKeyboardState(old);
                        byte[] st = (byte[])old.Clone();
                        foreach (int m in mods) st[m] |= 0x80;
                        Native.SetKeyboardState(st);
                    }
                    foreach (int m in mods) Native.PostMessage(t, kd, new IntPtr(m), KeyLParam(m, false, alt));
                    Native.PostMessage(t, kd, new IntPtr(key), KeyLParam(key, false, alt));
                    Thread.Sleep(40);
                    Native.PostMessage(t, ku, new IntPtr(key), KeyLParam(key, true, alt));
                    for (int i = mods.Count - 1; i >= 0; i--) Native.PostMessage(t, ku, new IntPtr(mods[i]), KeyLParam(mods[i], true, alt));
                    Thread.Sleep(80);
                }
                finally
                {
                    if (attached)
                    {
                        Native.SetKeyboardState(old);
                        Native.AttachThreadInput(me, tid, false);
                    }
                }
            }
        }

        static void ParseCombo(string combo, out List<int> mods, out int key, out bool alt)
        {
            if (string.IsNullOrWhiteSpace(combo)) throw new ApiException(400, "缺少 keys");
            mods = new List<int>();
            key = 0;
            alt = false;
            foreach (string raw in combo.ToLowerInvariant().Split('+'))
            {
                string part = raw.Trim();
                if (part == "ctrl" || part == "control") mods.Add(0x11);
                else if (part == "shift") mods.Add(0x10);
                else if (part == "alt") { mods.Add(0x12); alt = true; }
                else if (part == "") key = VkFromName("+");
                else key = VkFromName(part);
            }
            if (key == 0) throw new ApiException(400, "组合键里缺少主键: " + combo);
        }

        // ---------- real input: fallback for apps that ignore posted messages (WinUI/UWP, games) ----------
        // The window is put on screen and focused for the action, then returned to the background.

        static void ForceForeground(IntPtr h)
        {
            uint pid;
            uint fgThread = Native.GetWindowThreadProcessId(Native.GetForegroundWindow(), out pid), me = Native.GetCurrentThreadId();
            bool attached = fgThread != 0 && fgThread != me && Native.AttachThreadInput(me, fgThread, true);
            Native.BringWindowToTop(h);
            Native.SetForegroundWindow(h);
            if (attached) Native.AttachThreadInput(me, fgThread, false);
        }

        void Real(ManagedWindow w, Action body)
        {
            lock (inputLock)
            {
                bool wasBackground = w.InBackground;
                POINT cursor;
                Native.GetCursorPos(out cursor);
                IntPtr fg = Native.GetForegroundWindow();
                if (Native.IsIconic(w.Hwnd)) Native.ShowWindow(w.Hwnd, Native.SW_RESTORE);
                if (wasBackground)
                    Native.SetWindowPos(w.Hwnd, IntPtr.Zero, w.HasSaved ? w.Saved.Left : 100, w.HasSaved ? w.Saved.Top : 100, 0, 0,
                        Native.SWP_NOSIZE | Native.SWP_NOZORDER | Native.SWP_NOACTIVATE);
                ForceForeground(w.Hwnd);
                Thread.Sleep(250);
                try
                {
                    body();
                    Thread.Sleep(250);
                }
                finally
                {
                    if (wasBackground)
                    {
                        Rectangle vs = System.Windows.Forms.SystemInformation.VirtualScreen;
                        Native.SetWindowPos(w.Hwnd, IntPtr.Zero, vs.Right + 200, vs.Top + 50, 0, 0,
                            Native.SWP_NOSIZE | Native.SWP_NOZORDER | Native.SWP_NOACTIVATE);
                        if (fg != IntPtr.Zero && fg != w.Hwnd) ForceForeground(fg);
                        Native.SetCursorPos(cursor.X, cursor.Y);
                    }
                }
            }
        }

        static void MouseInput(uint flags, int dx, int dy, int data)
        {
            INPUT[] i = new INPUT[1];
            i[0].type = 0;
            i[0].mi.dx = dx; i[0].mi.dy = dy; i[0].mi.dwFlags = flags; i[0].mi.mouseData = unchecked((uint)data);
            Native.SendInput(1, i, 40);
        }

        static void MoveTo(POINT s)
        {
            Rectangle vs = System.Windows.Forms.SystemInformation.VirtualScreen;
            int dx = (int)Math.Round((s.X - vs.Left) * 65535.0 / Math.Max(1, vs.Width - 1));
            int dy = (int)Math.Round((s.Y - vs.Top) * 65535.0 / Math.Max(1, vs.Height - 1));
            MouseInput(0x0001 | 0x8000 | 0x4000, dx, dy, 0); // MOVE | ABSOLUTE | VIRTUALDESK
        }

        static void RealButton(string button, out uint down, out uint up)
        {
            switch ((button ?? "left").ToLowerInvariant())
            {
                case "right": down = 0x0008; up = 0x0010; break;
                case "middle": down = 0x0020; up = 0x0040; break;
                case "left": down = 0x0002; up = 0x0004; break;
                default: throw new ApiException(400, "button 只能是 left/right/middle");
            }
        }

        static void KeyInput(int vk, char unicode, bool up)
        {
            INPUT[] i = new INPUT[1];
            i[0].type = 1;
            if (unicode != '\0') { i[0].ki.wScan = unicode; i[0].ki.dwFlags = 0x0004; }
            else
            {
                i[0].ki.wVk = (ushort)vk;
                i[0].ki.wScan = (ushort)Native.MapVirtualKey((uint)vk, 0);
                if (ExtendedKeys.Contains(vk)) i[0].ki.dwFlags = 0x0001;
            }
            if (up) i[0].ki.dwFlags |= 0x0002;
            Native.SendInput(1, i, 40);
        }

        public void ClickReal(ManagedWindow w, int x, int y, string button, bool dbl)
        {
            uint down, up;
            RealButton(button, out down, out up);
            Real(w, delegate
            {
                MoveTo(ToScreen(w.Hwnd, x, y));
                Thread.Sleep(60);
                for (int n = 0; n < (dbl ? 2 : 1); n++)
                {
                    MouseInput(down, 0, 0, 0);
                    Thread.Sleep(40);
                    MouseInput(up, 0, 0, 0);
                    Thread.Sleep(60);
                }
            });
        }

        public void DragReal(ManagedWindow w, int x1, int y1, int x2, int y2, string button, int steps, int durationMs)
        {
            uint down, up;
            RealButton(button, out down, out up);
            if (steps <= 0) steps = 20;
            if (durationMs <= 0) durationMs = 400;
            Real(w, delegate
            {
                MoveTo(ToScreen(w.Hwnd, x1, y1));
                Thread.Sleep(60);
                MouseInput(down, 0, 0, 0);
                Thread.Sleep(60);
                for (int i = 1; i <= steps; i++)
                {
                    MoveTo(ToScreen(w.Hwnd, x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps));
                    Thread.Sleep(Math.Max(1, durationMs / steps));
                }
                Thread.Sleep(40);
                MouseInput(up, 0, 0, 0);
            });
        }

        public void ScrollReal(ManagedWindow w, int x, int y, int clicks)
        {
            Real(w, delegate
            {
                MoveTo(ToScreen(w.Hwnd, x, y));
                Thread.Sleep(60);
                MouseInput(0x0800, 0, 0, clicks * 120);
            });
        }

        public void TypeReal(ManagedWindow w, string text)
        {
            if (string.IsNullOrEmpty(text)) return;
            text = text.Replace("\r\n", "\r").Replace('\n', '\r');
            Real(w, delegate
            {
                foreach (char ch in text)
                {
                    if (ch == '\r') { KeyInput(0x0D, '\0', false); KeyInput(0x0D, '\0', true); }
                    else { KeyInput(0, ch, false); KeyInput(0, ch, true); }
                    Thread.Sleep(5);
                }
            });
        }

        public void KeysReal(ManagedWindow w, string combo)
        {
            List<int> mods;
            int key;
            bool alt;
            ParseCombo(combo, out mods, out key, out alt);
            Real(w, delegate
            {
                foreach (int m in mods) KeyInput(m, '\0', false);
                KeyInput(key, '\0', false);
                Thread.Sleep(40);
                KeyInput(key, '\0', true);
                for (int i = mods.Count - 1; i >= 0; i--) KeyInput(mods[i], '\0', true);
            });
        }

        static IntPtr KeyLParam(int vk, bool up, bool alt)
        {
            uint scan = Native.MapVirtualKey((uint)vk, 0);
            uint lp = 1 | (scan << 16);
            if (ExtendedKeys.Contains(vk)) lp |= 1u << 24;
            if (alt) lp |= 1u << 29;
            if (up) lp |= 0xC0000000;
            return new IntPtr(unchecked((int)lp));
        }

        // ---------- UI Automation (works on apps that ignore posted input, e.g. WinUI/UWP) ----------

        public List<Dictionary<string, object>> UiTree(ManagedWindow w, int max)
        {
            if (max <= 0) max = 300;
            List<AutomationElement> cache = new List<AutomationElement>();
            List<Dictionary<string, object>> result = new List<Dictionary<string, object>>();
            RECT wr;
            Native.GetWindowRect(w.Hwnd, out wr);
            AutomationElement root;
            try { root = AutomationElement.FromHandle(w.Hwnd); }
            catch (Exception ex) { throw new ApiException(500, "UI 自动化不可用: " + ex.Message); }
            Walk(root, 0, max, wr, cache, result);
            w.UiCache = cache;
            return result;
        }

        static void Walk(AutomationElement el, int depth, int max, RECT wr, List<AutomationElement> cache, List<Dictionary<string, object>> result)
        {
            if (cache.Count >= max || depth > 40) return;
            try
            {
                AutomationElement.AutomationElementInformation cur = el.Current;
                Dictionary<string, object> d = new Dictionary<string, object>();
                d["element"] = cache.Count;
                d["depth"] = depth;
                d["type"] = cur.ControlType.ProgrammaticName.Replace("ControlType.", "");
                d["name"] = cur.Name ?? "";
                if (!string.IsNullOrEmpty(cur.AutomationId)) d["automation_id"] = cur.AutomationId;
                System.Windows.Rect r = cur.BoundingRectangle;
                if (!r.IsEmpty && !double.IsInfinity(r.Width) && r.Width > 0)
                {
                    // Window-relative, so the centre can be passed straight to /api/click.
                    d["x"] = (int)r.X - wr.Left; d["y"] = (int)r.Y - wr.Top;
                    d["width"] = (int)r.Width; d["height"] = (int)r.Height;
                }
                if (!cur.IsEnabled) d["enabled"] = false;
                List<string> pats = new List<string>();
                if (Has(el, AutomationElement.IsInvokePatternAvailableProperty)) pats.Add("invoke");
                if (Has(el, AutomationElement.IsValuePatternAvailableProperty)) pats.Add("set_value");
                if (Has(el, AutomationElement.IsTogglePatternAvailableProperty)) pats.Add("toggle");
                if (Has(el, AutomationElement.IsSelectionItemPatternAvailableProperty)) pats.Add("select");
                if (Has(el, AutomationElement.IsExpandCollapsePatternAvailableProperty)) pats.Add("expand");
                if (pats.Count > 0) d["actions"] = pats;
                cache.Add(el);
                result.Add(d);
            }
            catch { return; }
            AutomationElement child;
            try { child = TreeWalker.ControlViewWalker.GetFirstChild(el); } catch { return; }
            while (child != null && cache.Count < max)
            {
                Walk(child, depth + 1, max, wr, cache, result);
                try { child = TreeWalker.ControlViewWalker.GetNextSibling(child); } catch { break; }
            }
        }

        static bool Has(AutomationElement el, AutomationProperty p)
        {
            try { return (bool)el.GetCurrentPropertyValue(p); } catch { return false; }
        }

        public string UiAction(ManagedWindow w, int element, string name, string automationId, string action, string value)
        {
            AutomationElement el = null;
            if (element >= 0)
            {
                if (w.UiCache == null || element >= w.UiCache.Count) throw new ApiException(400, "element 无效,请先调用 /api/ui_tree");
                el = w.UiCache[element];
            }
            else
            {
                Condition cond = null;
                if (!string.IsNullOrEmpty(automationId)) cond = new PropertyCondition(AutomationElement.AutomationIdProperty, automationId);
                else if (!string.IsNullOrEmpty(name)) cond = new PropertyCondition(AutomationElement.NameProperty, name);
                else throw new ApiException(400, "需要 element、name 或 automation_id 之一");
                el = AutomationElement.FromHandle(w.Hwnd).FindFirst(TreeScope.Descendants, cond);
                if (el == null) throw new ApiException(404, "找不到匹配的界面元素");
            }
            string act = (action ?? "invoke").ToLowerInvariant();
            // Invoke can block while the app shows a modal dialog, so it runs with a timeout.
            Task task = Task.Factory.StartNew(delegate()
            {
                object p;
                switch (act)
                {
                    case "invoke":
                        if (el.TryGetCurrentPattern(InvokePattern.Pattern, out p)) ((InvokePattern)p).Invoke();
                        else if (el.TryGetCurrentPattern(TogglePattern.Pattern, out p)) ((TogglePattern)p).Toggle();
                        else if (el.TryGetCurrentPattern(SelectionItemPattern.Pattern, out p)) ((SelectionItemPattern)p).Select();
                        else if (el.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out p)) ((ExpandCollapsePattern)p).Expand();
                        else throw new ApiException(400, "该元素不支持 invoke,可改用 /api/click 点击它的坐标");
                        break;
                    case "set_value":
                        if (!el.TryGetCurrentPattern(ValuePattern.Pattern, out p)) throw new ApiException(400, "该元素不支持 set_value");
                        ((ValuePattern)p).SetValue(value ?? "");
                        break;
                    case "toggle":
                        if (!el.TryGetCurrentPattern(TogglePattern.Pattern, out p)) throw new ApiException(400, "该元素不支持 toggle");
                        ((TogglePattern)p).Toggle();
                        break;
                    case "select":
                        if (!el.TryGetCurrentPattern(SelectionItemPattern.Pattern, out p)) throw new ApiException(400, "该元素不支持 select");
                        ((SelectionItemPattern)p).Select();
                        break;
                    case "expand":
                    case "collapse":
                        if (!el.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out p)) throw new ApiException(400, "该元素不支持 expand/collapse");
                        if (act == "expand") ((ExpandCollapsePattern)p).Expand(); else ((ExpandCollapsePattern)p).Collapse();
                        break;
                    default:
                        throw new ApiException(400, "action 只能是 invoke/set_value/toggle/select/expand/collapse");
                }
            });
            try
            {
                if (!task.Wait(4000)) return "已发出,程序仍在处理(可能弹出了对话框)";
            }
            catch (AggregateException ae)
            {
                Exception inner = ae.InnerException ?? ae;
                if (inner is ApiException) throw inner;
                throw new ApiException(500, "界面操作失败: " + inner.Message);
            }
            return "ok";
        }
    }
}
