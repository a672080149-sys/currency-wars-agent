using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace AppPilot
{
    /// <summary>Warm, low-contrast palette in the style of Claude's interface.</summary>
    static class Theme
    {
        public static readonly Color Background = Color.FromArgb(245, 244, 237);
        public static readonly Color Card = Color.FromArgb(255, 255, 253);
        public static readonly Color Border = Color.FromArgb(226, 222, 209);
        public static readonly Color Text = Color.FromArgb(41, 38, 27);
        public static readonly Color Muted = Color.FromArgb(135, 131, 118);
        public static readonly Color Accent = Color.FromArgb(201, 100, 66);
        public static readonly Color AccentHover = Color.FromArgb(183, 88, 56);
        public static readonly Color Hover = Color.FromArgb(240, 238, 228);
        public static readonly Color Stage = Color.FromArgb(38, 38, 36);
        public static readonly Color Bubble = Color.FromArgb(245, 243, 234);
        public const string FontName = "Microsoft YaHei UI";
    }

    /// <summary>Live mirror of a managed window; mouse and keyboard on it are forwarded to the real window.</summary>
    public class PreviewPanel : Control
    {
        Bitmap image;
        public string Hint = "";

        public PreviewPanel()
        {
            SetStyle(ControlStyles.Selectable | ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            SetStyle(ControlStyles.StandardDoubleClick, false);
            TabStop = true;
            BackColor = Theme.Stage;
        }

        public void SetImage(Bitmap bmp)
        {
            Bitmap old = image;
            image = bmp;
            if (old != null) old.Dispose();
            Invalidate();
        }

        RectangleF ImageRect(out float scale)
        {
            scale = 1f;
            if (image == null) return RectangleF.Empty;
            scale = Math.Min(1f, Math.Min((float)ClientSize.Width / image.Width, (float)ClientSize.Height / image.Height));
            float w = image.Width * scale, h = image.Height * scale;
            return new RectangleF((ClientSize.Width - w) / 2f, (ClientSize.Height - h) / 2f, w, h);
        }

        /// <summary>Maps a point on the panel to window pixel coordinates.</summary>
        public bool ToWindow(Point p, out Point win)
        {
            float scale;
            RectangleF r = ImageRect(out scale);
            win = Point.Empty;
            if (image == null || scale <= 0 || !r.Contains(p)) return false;
            win = new Point((int)((p.X - r.X) / scale), (int)((p.Y - r.Y) / scale));
            return true;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            if (image == null)
            {
                TextRenderer.DrawText(e.Graphics, Hint, Font, ClientRectangle, Color.FromArgb(176, 172, 160),
                    TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.WordBreak);
                return;
            }
            float scale;
            RectangleF r = ImageRect(out scale);
            e.Graphics.InterpolationMode = scale < 1f ? InterpolationMode.HighQualityBilinear : InterpolationMode.NearestNeighbor;
            e.Graphics.DrawImage(image, r);
            if (Focused)
                using (Pen pen = new Pen(Theme.Accent, 2))
                    e.Graphics.DrawRectangle(pen, r.X, r.Y, r.Width - 1, r.Height - 1);
        }

        protected override bool IsInputKey(Keys keyData) { return true; }
        protected override void OnGotFocus(EventArgs e) { base.OnGotFocus(e); Invalidate(); }
        protected override void OnLostFocus(EventArgs e) { base.OnLostFocus(e); Invalidate(); }
    }

    /// <summary>
    /// The Claudia desktop pet (sprites from kirito1207/claudia-pet), fixed at the bottom of the thinking card.
    /// 头顶一个小气泡说当前在想什么;上方的 History 区显示当前游戏目标和思考记录。Not draggable.
    /// </summary>
    public class PetPanel : Panel
    {
        const int Columns = 8;
        public readonly RichTextBox History = new RichTextBox();
        public const int BubbleLines = 6;
        readonly Font sayFont;
        string say = "";
        readonly string dir;
        readonly float dpi;
        readonly System.Windows.Forms.Timer anim = new System.Windows.Forms.Timer();
        Bitmap sheet;
        int frames, cell, frame;
        string mood = "", wanted = "";
        Rectangle petRect, bubbleRect;

        public PetPanel(string dir, float dpi)
        {
            this.dir = dir; this.dpi = dpi;
            DoubleBuffered = true;
            ResizeRedraw = true;
            BackColor = Theme.Card;
            History.ReadOnly = true; History.BorderStyle = BorderStyle.None; History.BackColor = Theme.Card;
            History.ScrollBars = RichTextBoxScrollBars.Vertical; History.DetectUrls = false; History.TabStop = false;
            Controls.Add(History);
            sayFont = new Font(Theme.FontName, 10f);
            anim.Interval = 50;
            anim.Tick += delegate
            {
                if (sheet == null || frames == 0) return;
                frame = (frame + 1) % frames;
                Invalidate(petRect);
            };
            anim.Start();
        }

        int S(int px) { return (int)Math.Round(px * dpi); }

        /// <summary>头顶气泡里的话(当前思考)。</summary>
        public void Say(string text)
        {
            text = (text ?? "").Trim();
            if (text == say) return;
            say = text;
            PerformLayout();
            Invalidate();
        }

        const TextFormatFlags SayFlags = TextFormatFlags.WordBreak | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix | TextFormatFlags.TextBoxControl;
        int PadX { get { return S(14); } }
        int PadY { get { return S(8); } }
        int Tail { get { return S(9); } }

        /// <summary>Switches animation; the sheet is decoded off the UI thread.</summary>
        public void SetMood(string m)
        {
            if (m == wanted) return;
            wanted = m;
            string file = Path.Combine(dir, m + ".png");
            ThreadPool.QueueUserWorkItem(delegate
            {
                Bitmap bmp = null;
                int count = 0;
                try
                {
                    if (File.Exists(file))
                    {
                        bmp = new Bitmap(file);
                        count = CountFrames(bmp);
                    }
                }
                catch { if (bmp != null) { bmp.Dispose(); bmp = null; } }
                try
                {
                    BeginInvoke((Action)delegate
                    {
                        if (m != wanted || bmp == null) { if (bmp != null) bmp.Dispose(); return; }
                        Bitmap old = sheet;
                        sheet = bmp; frames = count; cell = bmp.Width / Columns; frame = 0; mood = m;
                        if (old != null) old.Dispose();
                        Invalidate(petRect);
                    });
                }
                catch { if (bmp != null) bmp.Dispose(); }
            });
        }

        /// <summary>Frames fill the sheet row by row; trailing fully transparent cells are not frames.</summary>
        static int CountFrames(Bitmap bmp)
        {
            int cell = bmp.Width / Columns, total = Columns * (bmp.Height / cell);
            System.Drawing.Imaging.BitmapData d = bmp.LockBits(new Rectangle(0, 0, bmp.Width, bmp.Height),
                System.Drawing.Imaging.ImageLockMode.ReadOnly, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
            try
            {
                for (int i = total - 1; i > 0; i--)
                {
                    int x0 = (i % Columns) * cell, y0 = (i / Columns) * cell;
                    for (int y = y0; y < y0 + cell; y += 6)
                        for (int x = x0; x < x0 + cell; x += 6)
                            if (System.Runtime.InteropServices.Marshal.ReadByte(d.Scan0, y * d.Stride + x * 4 + 3) != 0) return i + 1;
                }
                return 1;
            }
            finally { bmp.UnlockBits(d); }
        }

        protected override void OnLayout(LayoutEventArgs e)
        {
            base.OnLayout(e);
            if (sayFont == null) return;
            int size = Math.Min(S(240), Math.Max(S(120), Height * 44 / 100));
            // The figure sits in the middle ~70% of its frame, so the frame may overhang the bottom edge a little.
            petRect = new Rectangle((Width - size) / 2, Height - size + size / 8, size, size);
            int headTop = petRect.Top + size * 13 / 100;
            int top = headTop;
            if (say != "")
            {
                int maxW = Math.Max(S(120), Width - S(16));
                int lineH = TextRenderer.MeasureText("国", sayFont).Height;
                Size t = TextRenderer.MeasureText(say, sayFont, new Size(maxW - 2 * PadX, int.MaxValue), SayFlags);
                int th = Math.Min(t.Height, lineH * BubbleLines);
                int bw = Math.Min(maxW, t.Width + 2 * PadX + S(2)), bh = th + 2 * PadY;
                bubbleRect = new Rectangle((Width - bw) / 2, headTop - Tail - bh, bw, bh);
                top = bubbleRect.Top;
            }
            else bubbleRect = Rectangle.Empty;
            History.Bounds = new Rectangle(0, 0, Width, Math.Max(S(60), top - S(10)));
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            if (sheet != null && frames > 0)
            {
                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                g.PixelOffsetMode = PixelOffsetMode.Half;
                g.DrawImage(sheet, petRect, new Rectangle((frame % Columns) * cell, (frame / Columns) * cell, cell, cell), GraphicsUnit.Pixel);
                g.PixelOffsetMode = PixelOffsetMode.Default;
            }
            if (say == "" || bubbleRect.IsEmpty) return;
            Rectangle b = bubbleRect;
            int r = Math.Min(b.Height / 2, S(16)), tail = Tail, cx = Width / 2;
            using (GraphicsPath p = new GraphicsPath())
            {
                p.AddArc(b.Left, b.Top, r * 2, r * 2, 180, 90);
                p.AddArc(b.Right - r * 2, b.Top, r * 2, r * 2, 270, 90);
                p.AddArc(b.Right - r * 2, b.Bottom - r * 2, r * 2, r * 2, 0, 90);
                p.AddLine(cx + tail, b.Bottom, cx, b.Bottom + tail);
                p.AddLine(cx, b.Bottom + tail, cx - tail, b.Bottom);
                p.AddArc(b.Left, b.Bottom - r * 2, r * 2, r * 2, 90, 90);
                p.CloseFigure();
                using (SolidBrush fill = new SolidBrush(Color.White)) g.FillPath(fill, p);
                using (Pen pen = new Pen(Color.FromArgb(232, 160, 70), Math.Max(1.5f, 1.5f * dpi))) g.DrawPath(pen, p);
            }
            Rectangle tr = new Rectangle(b.Left + PadX, b.Top + PadY, b.Width - 2 * PadX, b.Height - 2 * PadY);
            TextFormatFlags f = SayFlags;
            if (tr.Height < sayFont.Height * 2) f |= TextFormatFlags.HorizontalCenter;
            TextRenderer.DrawText(g, say, sayFont, tr, Theme.Text, f);
        }

        protected override void OnPaintBackground(PaintEventArgs e)
        {
            base.OnPaintBackground(e);
            // 宠物身后一圈淡淡的暖色光晕
            if (petRect.IsEmpty) return;
            Rectangle glow = Rectangle.Inflate(petRect, -petRect.Width / 10, -petRect.Height / 10);
            using (GraphicsPath gp = new GraphicsPath())
            {
                gp.AddEllipse(glow);
                using (PathGradientBrush br = new PathGradientBrush(gp))
                {
                    br.CenterColor = Color.FromArgb(70, 250, 200, 205);
                    br.SurroundColors = new[] { Color.FromArgb(0, 250, 200, 205) };
                    e.Graphics.FillPath(br, gp);
                }
            }
        }
    }

    public class MainForm : Form
    {
        class WindowItem
        {
            public ManagedWindow Window;
            public string Label;
            public override string ToString() { return Label; }
        }

        readonly WindowManager mgr = new WindowManager();
        readonly AgentState agent = new AgentState();
        readonly int port;
        readonly CurrencyWarsLauncher cw;
        readonly Recorder recorder;
        CloudView cloud;
        IntPtr selfHwnd;
        Button recButton;
        readonly bool cwAutoStart;
        readonly string token;
        float dpi = 1f;

        PreviewPanel preview;
        Panel thinkCard, logCard, funcCard;
        RichTextBox rtbThink;
        PetPanel pet;
        TextBox txtLog;
        ComboBox cmbWindows;
        CheckBox chkReal;
        Font fontBody, fontThought, fontBold, fontSmall;
        System.Windows.Forms.Timer timer;

        ManagedWindow current;
        bool capturing, rebuilding;
        int tick;
        string listSignature = "";
        string lastPath = "", lastArgs = "";
        bool lastBackground = true;
        Point downWin; bool downValid; MouseButtons downButton;
        DateTime lastClickTime; Point lastClickWin;

        public MainForm(int port, bool cwAutoStart)
        {
            this.port = port;
            this.cwAutoStart = cwAutoStart;
            token = LoadToken();
            cw = new CurrencyWarsLauncher(mgr, agent, port, token);
            recorder = new Recorder(() => selfHwnd, agent, delegate
            {   // 云游戏录 WebView2 的声音;没开云游戏就录本地 StarRail.exe
                if (cloud != null && cloud.BrowserPid > 0) return cloud.BrowserPid;
                System.Diagnostics.Process[] ps = System.Diagnostics.Process.GetProcessesByName("StarRail");
                return ps.Length > 0 ? ps[0].Id : 0;
            });
            HandleCreated += delegate { selfHwnd = Handle; };
            BuildUi();
            cloud = new CloudView(this, agent, CurrencyWarsLauncher.LoadConfig().CdpPort);
            cw.OpenCloud = delegate
            {
                Invoke((Action)delegate { cloud.Open(); cloud.SetBounds(preview.Bounds); });
                return cloud.CdpPort;
            };

            mgr.Changed += delegate { Ui(RebuildList); };
            agent.Changed += delegate { Ui(UpdateAgentPanel); };

            ApiServer server = new ApiServer(mgr, agent, port, token);
            server.Recorder = recorder;
            server.Cloud = delegate(string action)
            {
                Dictionary<string, object> r = null;
                Invoke((Action)delegate
                {
                    if (action == "open") { cloud.Open(); cloud.SetBounds(preview.Bounds); }
                    else if (action == "hide") cloud.Hide();
                    else if (action == "reload") cloud.Reload();
                    r = cloud.Describe();
                });
                return r;
            };
            try { server.Start(); }
            catch (Exception ex)
            {
                MessageBox.Show("AI 接口启动失败(端口 " + port + " 可能被占用): " + ex.Message +
                    "\n可用 小克工作台.exe --port 其他端口 启动。", "小克工作台", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }

            timer = new System.Windows.Forms.Timer();
            timer.Interval = 300;
            timer.Tick += OnTick;
            timer.Start();
            UpdateAgentPanel();
            if (cwAutoStart) Shown += delegate { cw.Start(true); };
        }

        void ToggleRecording()
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try { if (recorder.Recording) recorder.Stop(); else recorder.Start(60); }
                catch (Exception ex) { agent.Fail("录像: " + ex.Message); }
            });
        }

        void StartCurrencyWars()
        {
            if (CurrencyWarsLauncher.LoadConfig().Mode == "local" && !CurrencyWarsLauncher.IsAdmin())
            {
                if (MessageBox.Show("星铁以管理员身份运行,小克工作台 也需要管理员身份才能操作它。\n现在以管理员身份重新打开 小克工作台 并启动星铁?",
                        "小克工作台", MessageBoxButtons.OKCancel, MessageBoxIcon.Question) != DialogResult.OK) return;
                try
                {
                    ProcessStartInfo psi = new ProcessStartInfo(Application.ExecutablePath, "--port " + port + " --cw-start");
                    psi.UseShellExecute = true;
                    psi.Verb = "runas";
                    Close();
                    Process.Start(psi);
                }
                catch (Exception ex) { agent.Fail("没有以管理员身份重启: " + ex.Message); }
                return;
            }
            cw.Start(true);
        }

        static string LoadToken()
        {
            string file = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "token.txt");
            try
            {
                if (File.Exists(file))
                {
                    string t = File.ReadAllText(file).Trim();
                    if (t.Length >= 16) return t;
                }
            }
            catch { }
            byte[] buf = new byte[16];
            using (RNGCryptoServiceProvider rng = new RNGCryptoServiceProvider()) rng.GetBytes(buf);
            string token = BitConverter.ToString(buf).Replace("-", "").ToLowerInvariant();
            try { File.WriteAllText(file, token); } catch { }
            return token;
        }

        void Ui(Action a)
        {
            if (IsDisposed || !IsHandleCreated) return;
            try { BeginInvoke(a); } catch { }
        }

        int S(int px) { return (int)Math.Round(px * dpi); }

        // ---------- layout ----------
        // Preview (16:9) top-left, thinking on its right, log underneath, controls bottom-right.

        void BuildUi()
        {
            using (Graphics g = CreateGraphics()) dpi = g.DpiX / 96f;
            fontBody = new Font(Theme.FontName, 9f);
            fontThought = new Font(Theme.FontName, 10.5f);
            fontBold = new Font(Theme.FontName, 9f, FontStyle.Bold);
            fontSmall = new Font(Theme.FontName, 8.25f);

            SuspendLayout();
            Text = "小克工作台";
            using (Stream ico = typeof(MainForm).Assembly.GetManifestResourceStream("app.ico"))
                if (ico != null) Icon = new Icon(ico);
            Font = fontBody;
            AutoScaleMode = AutoScaleMode.None;
            BackColor = Theme.Background;
            ForeColor = Theme.Text;
            ClientSize = new Size(S(1364), S(820));
            MinimumSize = new Size(S(1000), S(640));
            StartPosition = FormStartPosition.CenterScreen;
            WindowState = FormWindowState.Maximized; // 默认最大化(工作台 + 内嵌游戏画面都更大,录像也直接按整屏抓)

            preview = new PreviewPanel();
            preview.Font = new Font(Theme.FontName, 10.5f);
            preview.Hint = "点右下角「启动程序」或「接管窗口」\n\n程序在后台运行,这里是它的实时画面,可以直接在上面点击、拖动和打字";
            preview.MouseDown += PreviewMouseDown;
            preview.MouseUp += PreviewMouseUp;
            preview.MouseWheel += PreviewMouseWheel;
            preview.KeyDown += ForwardKeyDown;
            preview.KeyPress += PreviewKeyPress;

            pet = new PetPanel(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "pet"), dpi);
            rtbThink = pet.History;
            thinkCard = Card("思考", pet);

            txtLog = new TextBox();
            txtLog.Multiline = true; txtLog.ReadOnly = true; txtLog.BorderStyle = BorderStyle.None;
            txtLog.BackColor = Theme.Card; txtLog.ForeColor = Theme.Text; txtLog.ScrollBars = ScrollBars.Vertical; txtLog.TabStop = false;
            logCard = Card("日志", txtLog);

            funcCard = Card("操作", BuildControls());

            Controls.Add(preview);
            Controls.Add(thinkCard);
            Controls.Add(logCard);
            Controls.Add(funcCard);
            ResumeLayout(false);
            Arrange();
        }

        Control BuildControls()
        {
            TableLayoutPanel t = new TableLayoutPanel();
            t.BackColor = Theme.Card;
            t.ColumnCount = 2;
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
            t.RowCount = 8;
            t.RowStyles.Add(new RowStyle(SizeType.Absolute, S(32)));
            for (int i = 0; i < 4; i++) t.RowStyles.Add(new RowStyle(SizeType.Absolute, S(36)));
            t.RowStyles.Add(new RowStyle(SizeType.Absolute, S(28)));
            t.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            t.RowStyles.Add(new RowStyle(SizeType.Absolute, S(20)));

            cmbWindows = new ComboBox();
            cmbWindows.DropDownStyle = ComboBoxStyle.DropDownList; cmbWindows.FlatStyle = FlatStyle.Flat;
            cmbWindows.Dock = DockStyle.Fill; cmbWindows.BackColor = Theme.Card; cmbWindows.ForeColor = Theme.Text;
            cmbWindows.Margin = new Padding(S(3), S(2), S(3), S(4));
            cmbWindows.SelectedIndexChanged += delegate
            {
                WindowItem it = cmbWindows.SelectedItem as WindowItem;
                if (!rebuilding && it != null) current = it.Window;
            };
            t.Controls.Add(cmbWindows, 0, 0);
            t.SetColumnSpan(cmbWindows, 2);

            t.Controls.Add(MakeButton("启动程序", true, delegate { LaunchFromUi(); }), 0, 1);
            t.Controls.Add(MakeButton("接管窗口", false, delegate { AttachFromUi(); }), 1, 1);
            t.Controls.Add(MakeButton("放到后台", false, delegate { WithCurrent(delegate(ManagedWindow w) { mgr.SendToBackground(w); }); }), 0, 2);
            t.Controls.Add(MakeButton("显示到前台", false, delegate { WithCurrent(delegate(ManagedWindow w) { mgr.BringToForeground(w, true); }); }), 1, 2);
            t.Controls.Add(MakeButton("关闭窗口", false, delegate { WithCurrent(delegate(ManagedWindow w) { mgr.Close(w); }); }), 0, 3);
            t.Controls.Add(MakeButton("停止接管", false, delegate { WithCurrent(delegate(ManagedWindow w) { mgr.Remove(w); }); }), 1, 3);
            Button cwButton = MakeButton("启动星铁 · 货币战争", true, delegate { StartCurrencyWars(); });
            t.Controls.Add(cwButton, 0, 4);
            recButton = MakeButton("● 开始录像", false, delegate { ToggleRecording(); });
            t.Controls.Add(recButton, 1, 4);

            chkReal = new CheckBox();
            chkReal.Text = "真实输入(新式界面程序用)"; chkReal.AutoSize = true; chkReal.ForeColor = Theme.Muted;
            chkReal.Anchor = AnchorStyles.Left; chkReal.Margin = new Padding(S(4), S(4), 0, 0);
            t.Controls.Add(chkReal, 0, 5);
            t.SetColumnSpan(chkReal, 2);

            LinkLabel api = new LinkLabel();
            api.Text = "接口 127.0.0.1:" + port + "  ·  复制令牌";
            api.Font = fontSmall; api.AutoSize = true; api.Anchor = AnchorStyles.Left | AnchorStyles.Bottom;
            api.LinkArea = new LinkArea(api.Text.Length - 4, 4);
            api.LinkColor = Theme.Accent; api.ActiveLinkColor = Theme.AccentHover; api.ForeColor = Theme.Muted;
            api.LinkBehavior = LinkBehavior.HoverUnderline; api.Margin = new Padding(S(3), 0, 0, 0);
            api.LinkClicked += delegate
            {
                try { Clipboard.SetText(token); api.Text = "接口 127.0.0.1:" + port + "  ·  已复制 "; } catch { }
            };
            t.Controls.Add(api, 0, 7);
            t.SetColumnSpan(api, 2);
            return t;
        }

        /// <summary>A white panel with a hairline border and a small muted title.</summary>
        Panel Card(string title, Control content)
        {
            Panel outer = new Panel();
            outer.BackColor = Theme.Border;
            outer.Padding = new Padding(1);
            Panel inner = new Panel();
            inner.Dock = DockStyle.Fill; inner.BackColor = Theme.Card;
            inner.Padding = new Padding(S(14), S(10), S(12), S(12));
            Label head = new Label();
            head.Text = title; head.Dock = DockStyle.Top; head.Height = S(26);
            head.Font = fontBold; head.ForeColor = Theme.Muted;
            content.Dock = DockStyle.Fill;
            inner.Controls.Add(content);
            inner.Controls.Add(head);
            outer.Controls.Add(inner);
            return outer;
        }

        Button MakeButton(string text, bool primary, EventHandler onClick)
        {
            Button b = new Button();
            b.Text = text; b.Dock = DockStyle.Fill; b.Margin = new Padding(S(3));
            b.FlatStyle = FlatStyle.Flat; b.Cursor = Cursors.Hand; b.Font = fontBody;
            b.FlatAppearance.BorderSize = primary ? 0 : 1;
            b.FlatAppearance.BorderColor = primary ? Theme.Accent : Theme.Border;
            b.FlatAppearance.MouseOverBackColor = primary ? Theme.AccentHover : Theme.Hover;
            b.FlatAppearance.MouseDownBackColor = primary ? Theme.AccentHover : Theme.Border;
            b.BackColor = primary ? Theme.Accent : Theme.Card;
            b.ForeColor = primary ? Color.White : Theme.Text;
            b.Click += onClick;
            return b;
        }

        void Arrange()
        {
            if (preview == null) return;
            int m = S(16), gap = S(12), w = ClientSize.Width, h = ClientSize.Height;
            int side = Math.Max(S(300), Math.Min(S(420), w * 27 / 100));
            int pw = w - 2 * m - gap - side, ph = pw * 9 / 16;
            int maxPh = h - 2 * m - gap - S(282);
            if (ph > maxPh)
            {
                ph = Math.Max(S(160), maxPh);
                pw = ph * 16 / 9;
                side = w - 2 * m - gap - pw;
            }
            int bottomY = m + ph + gap, bottomH = Math.Max(S(60), h - bottomY - m);
            preview.Bounds = new Rectangle(m, m, pw, ph);
            if (cloud != null) cloud.SetBounds(preview.Bounds);
            thinkCard.Bounds = new Rectangle(m + pw + gap, m, side, ph);
            logCard.Bounds = new Rectangle(m, bottomY, pw, bottomH);
            funcCard.Bounds = new Rectangle(m + pw + gap, bottomY, side, bottomH);
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            Arrange();
        }

        // ---------- launcher ----------

        void LaunchFromUi()
        {
            string path, args;
            bool bg;
            using (Form dlg = new Form())
            {
                dlg.Text = "启动程序"; dlg.Font = fontBody; dlg.BackColor = Theme.Background; dlg.ForeColor = Theme.Text;
                dlg.AutoScaleMode = AutoScaleMode.None;
                dlg.ClientSize = new Size(S(520), S(190));
                dlg.FormBorderStyle = FormBorderStyle.FixedDialog; dlg.StartPosition = FormStartPosition.CenterParent;
                dlg.MinimizeBox = false; dlg.MaximizeBox = false; dlg.ShowInTaskbar = false;

                TableLayoutPanel t = new TableLayoutPanel();
                t.Dock = DockStyle.Fill; t.Padding = new Padding(S(16));
                t.ColumnCount = 3;
                t.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, S(48)));
                t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
                t.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, S(84)));
                t.RowCount = 4;
                for (int i = 0; i < 3; i++) t.RowStyles.Add(new RowStyle(SizeType.Absolute, S(38)));
                t.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

                TextBox txtPath = new TextBox(); txtPath.Dock = DockStyle.Fill; txtPath.Text = lastPath; txtPath.Margin = new Padding(S(3), S(6), S(3), 0);
                TextBox txtArgs = new TextBox(); txtArgs.Dock = DockStyle.Fill; txtArgs.Text = lastArgs; txtArgs.Margin = new Padding(S(3), S(6), S(3), 0);
                CheckBox chk = new CheckBox(); chk.Text = "启动后放到后台"; chk.Checked = lastBackground; chk.AutoSize = true; chk.Anchor = AnchorStyles.Left;
                Button browse = MakeButton("浏览", false, delegate
                {
                    using (OpenFileDialog ofd = new OpenFileDialog())
                    {
                        ofd.Filter = "程序 (*.exe;*.lnk;*.bat;*.cmd)|*.exe;*.lnk;*.bat;*.cmd|所有文件 (*.*)|*.*";
                        if (ofd.ShowDialog(dlg) == DialogResult.OK) txtPath.Text = ofd.FileName;
                    }
                });
                Button ok = MakeButton("启动", true, delegate { dlg.DialogResult = DialogResult.OK; });
                dlg.AcceptButton = ok;

                t.Controls.Add(DialogLabel("程序"), 0, 0);
                t.Controls.Add(txtPath, 1, 0);
                t.Controls.Add(browse, 2, 0);
                t.Controls.Add(DialogLabel("参数"), 0, 1);
                t.Controls.Add(txtArgs, 1, 1);
                t.Controls.Add(chk, 1, 2);
                t.Controls.Add(ok, 2, 2);
                Label tip = new Label();
                tip.Text = "可以填 exe 路径、命令名(如 notepad)、文档或网址"; tip.ForeColor = Theme.Muted; tip.Font = fontSmall;
                tip.AutoSize = true; tip.Anchor = AnchorStyles.Left | AnchorStyles.Bottom;
                t.Controls.Add(tip, 1, 3);
                t.SetColumnSpan(tip, 2);
                dlg.Controls.Add(t);

                if (dlg.ShowDialog(this) != DialogResult.OK) return;
                path = txtPath.Text.Trim().Trim('"'); args = txtArgs.Text; bg = chk.Checked;
            }
            if (path == "") return;
            lastPath = path; lastArgs = args; lastBackground = bg;
            Cursor = Cursors.AppStarting;
            ThreadPool.QueueUserWorkItem(delegate
            {
                string error = null;
                List<ManagedWindow> added = null;
                try { added = mgr.Launch(path, args, bg, 8000); }
                catch (Exception ex) { error = ex.Message; }
                Ui(delegate
                {
                    Cursor = Cursors.Default;
                    if (error != null) MessageBox.Show(this, error, "小克工作台", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    else if (added.Count == 0) MessageBox.Show(this, "程序已启动,但没有检测到新窗口。\n可以稍后用「接管窗口」。", "小克工作台");
                    else { RebuildList(); Select(added[0].Id); }
                });
            });
        }

        Label DialogLabel(string text)
        {
            Label l = new Label();
            l.Text = text; l.AutoSize = true; l.Anchor = AnchorStyles.Left; l.ForeColor = Theme.Muted;
            return l;
        }

        void AttachFromUi()
        {
            List<IntPtr> all = mgr.EnumTopLevel().Where(h => Native.Title(h) != "").ToList();
            using (Form dlg = new Form())
            {
                dlg.Text = "接管窗口"; dlg.Font = fontBody; dlg.BackColor = Theme.Background; dlg.ForeColor = Theme.Text;
                dlg.AutoScaleMode = AutoScaleMode.None;
                dlg.ClientSize = new Size(S(560), S(400)); dlg.Padding = new Padding(S(16));
                dlg.StartPosition = FormStartPosition.CenterParent; dlg.MinimizeBox = false; dlg.MaximizeBox = false; dlg.ShowInTaskbar = false;
                ListBox lb = new ListBox();
                lb.Dock = DockStyle.Fill; lb.IntegralHeight = false; lb.BorderStyle = BorderStyle.FixedSingle;
                lb.BackColor = Theme.Card; lb.ForeColor = Theme.Text; lb.ItemHeight = S(22);
                foreach (IntPtr h in all)
                {
                    Dictionary<string, object> d = mgr.Describe(h);
                    lb.Items.Add(d["title"] + "    —  " + d["process"]);
                }
                Panel bar = new Panel(); bar.Dock = DockStyle.Bottom; bar.Height = S(48); bar.Padding = new Padding(0, S(10), 0, 0);
                Button ok = MakeButton("接管", true, delegate { dlg.DialogResult = DialogResult.OK; });
                ok.Dock = DockStyle.Right; ok.Width = S(96);
                bar.Controls.Add(ok);
                lb.DoubleClick += delegate { dlg.DialogResult = DialogResult.OK; };
                dlg.Controls.Add(lb); dlg.Controls.Add(bar);
                if (dlg.ShowDialog(this) != DialogResult.OK || lb.SelectedIndex < 0) return;
                try
                {
                    ManagedWindow w = mgr.Add(all[lb.SelectedIndex], lastBackground);
                    RebuildList();
                    Select(w.Id);
                }
                catch (Exception ex) { MessageBox.Show(this, ex.Message, "小克工作台"); }
            }
        }

        // ---------- window list ----------

        void RebuildList()
        {
            List<ManagedWindow> list = mgr.List();
            string sig = string.Join("|", list.Select(w => w.Id + ":" + Native.Title(w.Hwnd) + ":" + w.InBackground).ToArray());
            if (sig == listSignature) return;
            listSignature = sig;
            int keep = current != null ? current.Id : -1;
            rebuilding = true;
            cmbWindows.BeginUpdate();
            cmbWindows.Items.Clear();
            int select = -1;
            foreach (ManagedWindow w in list)
            {
                string title = Native.Title(w.Hwnd);
                WindowItem it = new WindowItem();
                it.Window = w;
                it.Label = "#" + w.Id + "  " + (title == "" ? "(无标题)" : title) + (w.InBackground ? "  · 后台" : "  · 前台");
                cmbWindows.Items.Add(it);
                if (w.Id == keep) select = cmbWindows.Items.Count - 1;
            }
            if (select < 0) select = cmbWindows.Items.Count - 1; // newest window
            cmbWindows.SelectedIndex = select;
            cmbWindows.EndUpdate();
            rebuilding = false;
            if (select >= 0) current = ((WindowItem)cmbWindows.Items[select]).Window;
            else { current = null; preview.SetImage(null); }
        }

        void Select(int id)
        {
            for (int i = 0; i < cmbWindows.Items.Count; i++)
                if (((WindowItem)cmbWindows.Items[i]).Window.Id == id) cmbWindows.SelectedIndex = i;
        }

        void WithCurrent(Action<ManagedWindow> action)
        {
            ManagedWindow w = current;
            if (w == null) return;
            try { action(w); } catch (Exception ex) { MessageBox.Show(this, ex.Message, "小克工作台"); }
        }

        void OnTick(object sender, EventArgs e)
        {
            if (++tick % 4 == 0)
            {
                try { mgr.Refresh(); } catch { }
                if (!cmbWindows.DroppedDown) RebuildList();
            }
            pet.SetMood(agent.Mood); // moods expire back to idle
            bool rec = recorder.Recording;
            timer.Interval = rec ? 100 : 300; // 录像时预览刷新快一些,视频里的游戏画面更流畅
            string recText = rec ? "■ 停止录像 " + recorder.Elapsed.ToString(@"hh\:mm\:ss") : "● 开始录像";
            if (recButton != null && recButton.Text != recText) { recButton.Text = recText; recButton.ForeColor = rec ? Color.FromArgb(214, 48, 49) : Theme.Text; }
            ManagedWindow w = current;
            if (w == null || capturing) return;
            capturing = true;
            ThreadPool.QueueUserWorkItem(delegate
            {
                Bitmap bmp = null;
                try { if (Native.IsWindow(w.Hwnd)) bmp = mgr.Capture(w); } catch { }
                Ui(delegate
                {
                    if (bmp != null && current == w) preview.SetImage(bmp);
                    else if (bmp != null) bmp.Dispose();
                    capturing = false;
                });
                if (IsDisposed || !IsHandleCreated) capturing = false;
            });
        }

        // ---------- forwarding the user's own input to the background window ----------

        static string ButtonName(MouseButtons b)
        {
            return b == MouseButtons.Right ? "right" : (b == MouseButtons.Middle ? "middle" : "left");
        }

        void Send(Action action)
        {
            ThreadPool.QueueUserWorkItem(delegate { try { action(); } catch { } });
        }

        void PreviewMouseDown(object sender, MouseEventArgs e)
        {
            preview.Focus();
            downValid = current != null && preview.ToWindow(e.Location, out downWin);
            downButton = e.Button;
        }

        void PreviewMouseUp(object sender, MouseEventArgs e)
        {
            ManagedWindow w = current;
            Point up;
            if (!downValid || w == null || e.Button != downButton) return;
            downValid = false;
            if (!preview.ToWindow(e.Location, out up)) up = downWin;
            Point from = downWin;
            string button = ButtonName(e.Button);
            bool real = chkReal.Checked;
            if (Math.Abs(up.X - from.X) > 4 || Math.Abs(up.Y - from.Y) > 4)
            {
                Send(delegate
                {
                    if (real) mgr.DragReal(w, from.X, from.Y, up.X, up.Y, button, 20, 300);
                    else mgr.Drag(w, from.X, from.Y, up.X, up.Y, button, 20, 300);
                });
                return;
            }
            if (real) { Send(delegate { mgr.ClickReal(w, up.X, up.Y, button, false); }); return; }
            bool second = (DateTime.Now - lastClickTime).TotalMilliseconds < SystemInformation.DoubleClickTime &&
                          Math.Abs(up.X - lastClickWin.X) < 6 && Math.Abs(up.Y - lastClickWin.Y) < 6;
            lastClickTime = second ? DateTime.MinValue : DateTime.Now;
            lastClickWin = up;
            Send(delegate { mgr.Click(w, up.X, up.Y, button, second, second); });
        }

        void PreviewMouseWheel(object sender, MouseEventArgs e)
        {
            ManagedWindow w = current;
            Point p;
            if (w == null || !preview.ToWindow(e.Location, out p)) return;
            int clicks = e.Delta / 120;
            bool real = chkReal.Checked;
            if (clicks != 0) Send(delegate { if (real) mgr.ScrollReal(w, p.X, p.Y, clicks); else mgr.Scroll(w, p.X, p.Y, clicks); });
        }

        static string KeyName(Keys k)
        {
            switch (k)
            {
                case Keys.Enter: return "enter";
                case Keys.Tab: return "tab";
                case Keys.Escape: return "esc";
                case Keys.Back: return "backspace";
                case Keys.Delete: return "delete";
                case Keys.Insert: return "insert";
                case Keys.Home: return "home";
                case Keys.End: return "end";
                case Keys.PageUp: return "pageup";
                case Keys.PageDown: return "pagedown";
                case Keys.Left: return "left";
                case Keys.Right: return "right";
                case Keys.Up: return "up";
                case Keys.Down: return "down";
                case Keys.Space: return "space";
            }
            if (k >= Keys.F1 && k <= Keys.F24) return "f" + (k - Keys.F1 + 1);
            if (k >= Keys.A && k <= Keys.Z) return ((char)('a' + (k - Keys.A))).ToString();
            if (k >= Keys.D0 && k <= Keys.D9) return ((char)('0' + (k - Keys.D0))).ToString();
            return null;
        }

        void ForwardKeyDown(object sender, KeyEventArgs e)
        {
            ManagedWindow w = current;
            if (w == null) return;
            string name = KeyName(e.KeyCode);
            if (name == null) return;
            bool printable = (e.KeyCode >= Keys.A && e.KeyCode <= Keys.Z) || (e.KeyCode >= Keys.D0 && e.KeyCode <= Keys.D9) || e.KeyCode == Keys.Space;
            if (printable && !e.Control && !e.Alt) return; // arrives as KeyPress
            string combo = (e.Control ? "ctrl+" : "") + (e.Alt ? "alt+" : "") + (e.Shift ? "shift+" : "") + name;
            e.SuppressKeyPress = true;
            bool real = chkReal.Checked;
            Send(delegate { if (real) mgr.KeysReal(w, combo); else mgr.PressKeys(w, combo); });
        }

        void PreviewKeyPress(object sender, KeyPressEventArgs e)
        {
            ManagedWindow w = current;
            if (w == null || char.IsControl(e.KeyChar)) return;
            string s = e.KeyChar.ToString();
            e.Handled = true;
            bool real = chkReal.Checked;
            Send(delegate { if (real) mgr.TypeReal(w, s); else mgr.TypeText(w, s); });
        }

        // ---------- thinking + log ----------

        void Append(string text, Color color, Font font)
        {
            rtbThink.SelectionStart = rtbThink.TextLength;
            rtbThink.SelectionLength = 0;
            rtbThink.SelectionColor = color;
            rtbThink.SelectionFont = font;
            rtbThink.AppendText(text);
        }

        void UpdateAgentPanel()
        {
            string thought = agent.Thought;
            List<string> goals = agent.Goals;
            List<PlanStep> plan = agent.Plan;
            List<KeyValuePair<DateTime, string>> thoughts = agent.Thoughts;
            rtbThink.Clear();
            bool any = false;
            if (goals.Count > 0)
            {
                Append("当前游戏目标", Theme.Muted, fontBold);
                foreach (string goal in goals) Append("\n◎  " + goal, Theme.Accent, fontBold);
                any = true;
            }
            else if (plan.Count > 0)
            {
                Append("当前步骤", Theme.Muted, fontBold);
                int n = 1;
                foreach (PlanStep s in plan)
                {
                    string line = "\n" + (s.Status == "done" ? "✓  " : (s.Status == "in_progress" ? "●  " : "○  ")) + n++ + ". " + s.Text;
                    if (s.Status == "done") Append(line, Theme.Muted, fontBody);
                    else if (s.Status == "in_progress") Append(line, Theme.Accent, fontBold);
                    else Append(line, Theme.Text, fontBody);
                }
                any = true;
            }
            if (thoughts.Count > 0)
            {
                Append((any ? "\n\n" : "") + "思考记录", Theme.Muted, fontBold);
                // 新的在上面,不用滚动就能看到最近的
                for (int i = thoughts.Count - 1, shown = 0; i >= 0 && shown < 80; i--, shown++)
                {
                    bool latest = i == thoughts.Count - 1;
                    Append("\n" + thoughts[i].Key.ToString("HH:mm:ss") + "  ", Theme.Muted, fontSmall);
                    Append(thoughts[i].Value, latest ? Theme.Text : Theme.Muted, latest ? fontThought : fontBody);
                }
                any = true;
            }
            if (!any)
                Append("AI 开始干活后,这里会显示当前游戏目标和它的思考记录。", Theme.Muted, fontBody);
            rtbThink.SelectionStart = 0;
            rtbThink.ScrollToCaret();

            pet.Say(thought != "" ? thought : (any ? "" : "在呢～"));
            pet.SetMood(agent.Mood);

            txtLog.Text = string.Join("\r\n", agent.History.ToArray());
            txtLog.SelectionStart = txtLog.TextLength;
            txtLog.ScrollToCaret();
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            // Never leave the user's apps stranded off-screen.
            foreach (ManagedWindow w in mgr.List())
                try { if (Native.IsWindow(w.Hwnd) && w.InBackground) mgr.BringToForeground(w, false); } catch { }
            try { recorder.Stop(); } catch { }
            cw.StopBridge();
            base.OnFormClosing(e);
        }
    }

    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            int port = 8765;
            bool cwStart = false;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--port" && i + 1 < args.Length) int.TryParse(args[i + 1], out port);
                if (args[i] == "--cw-start") cwStart = true;
            }
            Native.EnableDpiAwareness();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm(port, cwStart));
        }
    }
}
