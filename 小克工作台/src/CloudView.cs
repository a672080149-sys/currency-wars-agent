using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace AppPilot
{
    /// <summary>
    /// 内嵌的云·星穹铁道(WebView2)。画面由浏览器自己渲染,跟随视频流帧率(60 FPS);
    /// 打开 CDP 调试端口,cw-bridge 通过它截图、点击、按键,完全后台,不动真实鼠标键盘,不需要管理员。
    /// 页面布局固定为 1920x1080 CSS 像素(按控件大小设置缩放),和 M7A 的坐标一致。
    /// </summary>
    public class CloudView
    {
        public const string GameUrl = "https://sr.mihoyo.com/cloud";
        const string PointerLockScript = @"(() => {
            const blocked = function () {
                return Promise.reject(new DOMException('Pointer Lock is disabled in 小克工作台.', 'NotAllowedError'));
            };
            Object.defineProperty(Element.prototype, 'requestPointerLock', { configurable: true, writable: true, value: blocked });
            if (document.pointerLockElement && document.exitPointerLock) document.exitPointerLock();
        })();";

        readonly Control host;
        readonly AgentState agent;
        public WebView2 View { get; private set; }
        public int CdpPort { get; private set; }
        public bool Ready { get; private set; }
        public int BrowserPid { get; private set; } // 录音用:WebView2 浏览器主进程,声音从它的子进程出来
        public string Error { get; private set; }
        Task opening;

        public CloudView(Control host, AgentState agent, int cdpPort)
        {
            this.host = host; this.agent = agent; CdpPort = cdpPort;
        }

        public bool Visible { get { return View != null && View.Visible; } }

        static string DataDir { get { return Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "webview-data"); } }

        /// <summary>Must run on the UI thread.</summary>
        public Task Open()
        {
            if (View != null)
            {
                View.Visible = true;
                View.BringToFront();
                return opening ?? Task.FromResult(0);
            }
            View = new WebView2();
            View.DefaultBackgroundColor = Color.Black;
            View.Visible = true;
            host.Controls.Add(View);
            View.BringToFront();
            View.SizeChanged += delegate { FitZoom(); };
            opening = Init();
            return opening;
        }

        async Task Init()
        {
            try
            {
                CoreWebView2EnvironmentOptions opt = new CoreWebView2EnvironmentOptions(
                    "--remote-debugging-port=" + CdpPort +
                    " --remote-allow-origins=*" +
                    " --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling" +
                    " --autoplay-policy=no-user-gesture-required --disable-blink-features=AutomationControlled --lang=zh-CN");
                opt.Language = "zh-CN";
                CoreWebView2Environment env = await CoreWebView2Environment.CreateAsync(null, DataDir, opt);
                await View.EnsureCoreWebView2Async(env);
                CoreWebView2 core = View.CoreWebView2;
                try { BrowserPid = (int)core.BrowserProcessId; } catch { }
                core.Settings.IsStatusBarEnabled = false;
                core.Settings.AreDevToolsEnabled = true;
                core.Settings.IsZoomControlEnabled = false;
                core.Settings.IsPinchZoomEnabled = false;
                await core.AddScriptToExecuteOnDocumentCreatedAsync(PointerLockScript);
                // 登录弹窗保持 WebView2 默认行为(独立小窗,保留 window.opener),否则登录结果回传不了主页面
                core.WindowCloseRequested += delegate { };
                core.ProcessFailed += delegate(object s, CoreWebView2ProcessFailedEventArgs e)
                {
                    agent.Fail("云游戏页面进程异常: " + e.ProcessFailedKind);
                };
                FitZoom();
                core.Navigate(GameUrl);
                Ready = true;
                agent.Record("打开云·星穹铁道", "内嵌浏览器,调试端口 " + CdpPort, "running");
            }
            catch (Exception ex)
            {
                Error = ex.Message;
                agent.Fail("内嵌浏览器启动失败(需要 WebView2 运行时): " + ex.Message);
                throw;
            }
        }

        /// <summary>让页面视口始终是 1920x1080 CSS 像素:缩放 = 控件宽(DIP) / 1920。</summary>
        public void FitZoom()
        {
            if (View == null || View.CoreWebView2 == null || View.Width <= 0) return;
            float dpi;
            using (Graphics g = View.CreateGraphics()) dpi = g.DpiX / 96f;
            double zoom = View.Width / dpi / 1920.0;
            if (zoom > 0.1 && Math.Abs(View.ZoomFactor - zoom) > 0.001)
                try { View.ZoomFactor = zoom; } catch { }
        }

        public void SetBounds(Rectangle r)
        {
            if (View == null) return;
            // 16:9 居中
            int w = r.Width, h = r.Width * 9 / 16;
            if (h > r.Height) { h = r.Height; w = h * 16 / 9; }
            View.Bounds = new Rectangle(r.X + (r.Width - w) / 2, r.Y + (r.Height - h) / 2, w, h);
        }

        public void Hide()
        {
            if (View != null) View.Visible = false;
        }

        public void Reload()
        {
            if (View != null && View.CoreWebView2 != null) View.CoreWebView2.Navigate(GameUrl);
        }

        public Dictionary<string, object> Describe()
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["open"] = View != null;
            d["visible"] = Visible;
            d["ready"] = Ready;
            d["cdp_port"] = CdpPort;
            d["url"] = View != null && View.CoreWebView2 != null ? View.CoreWebView2.Source : null;
            d["zoom"] = View != null ? View.ZoomFactor : 0;
            d["size"] = View != null ? View.Width + "x" + View.Height : null;
            d["error"] = Error;
            return d;
        }
    }
}
