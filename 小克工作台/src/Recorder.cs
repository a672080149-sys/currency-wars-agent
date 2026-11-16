using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace AppPilot
{
    /// <summary>
    /// 录制 小克工作台 整个窗口:用 PrintWindow 抓自己的窗口(被遮挡也能录),原始帧经管道交给 ffmpeg 编码成 mp4。
    /// 优先 h264_nvenc(几乎不占 CPU,不影响游戏),不行再退到 libx264 / mpeg4。
    /// </summary>
    public class Recorder
    {
        readonly Func<IntPtr> hwndOf;
        readonly AgentState agent;
        readonly object sync = new object();
        Process ff;
        Thread worker;
        volatile bool stopping;
        DateTime started;
        string file;
        int width, height, fps;
        long frames, grabbed;
        string method; // ddagrab(GPU 抓屏,60 FPS) 或 printwindow
        public const int MaxWidth = 1920; // 输出最大宽度:窗口比这大就等比缩小,60 FPS 才跑得动

        // 声音:按进程录(云游戏 = WebView2 浏览器进程树,本地 = StarRail.exe),停止时合进 mp4
        readonly Func<int> audioPid;
        AudioCapture audio;
        string audioError, ffExe;
        DateTime lastSpawn, videoStart;

        public Recorder(Func<IntPtr> hwndOf, AgentState agent, Func<int> audioPid = null)
        {
            this.hwndOf = hwndOf; this.agent = agent; this.audioPid = audioPid;
        }

        void StartAudio()
        {
            audio = null; audioError = null;
            if (audioPid == null) { audioError = "未配置声音来源"; return; }
            int pid = 0;
            try { pid = audioPid(); } catch { }
            audio = AudioCapture.TryStart(pid, Path.ChangeExtension(file, ".pcm"), out audioError);
        }

        public bool Recording { get { lock (sync) return ff != null; } }
        public TimeSpan Elapsed { get { lock (sync) return ff == null ? TimeSpan.Zero : DateTime.Now - started; } }
        public string File { get { return file; } }

        static string AppDir { get { return AppDomain.CurrentDomain.BaseDirectory; } }

        /// <summary>程序目录 ffmpeg.exe → ffmpeg\ffmpeg.exe → record.txt 里写的路径 → PATH → 本机常见位置。</summary>
        public static string FindFfmpeg()
        {
            List<string> cands = new List<string>
            {
                Path.Combine(AppDir, "ffmpeg.exe"),
                Path.Combine(AppDir, "ffmpeg", "ffmpeg.exe"),
            };
            try
            {
                string cfg = Path.Combine(AppDir, "ffmpeg.txt");
                if (System.IO.File.Exists(cfg)) cands.Insert(0, System.IO.File.ReadAllText(cfg, Encoding.UTF8).Trim().Trim('"'));
            }
            catch { }
            foreach (string dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
                if (dir.Trim() != "") cands.Add(Path.Combine(dir.Trim(), "ffmpeg.exe"));
            cands.Add(@"C:\Program Files (x86)\FormatFactory\ffmpeg.exe"); // ffmpeg 7.1,支持 h264_nvenc
            cands.Add(@"D:\AI-TTS\GPT-SoVITS-v2pro-20261006nvidia50x0\runtime\ffmpeg.exe"); // 4.3,只能退到 mpeg4
            cands.Add(@"D:\AI-Cover\RVC20260718Nvidia50x0\ffmpeg.exe");
            return cands.FirstOrDefault(p => { try { return System.IO.File.Exists(p); } catch { return false; } });
        }

        public Dictionary<string, object> Describe()
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            lock (sync)
            {
                d["recording"] = ff != null;
                d["file"] = file;
                d["seconds"] = ff == null ? 0 : (int)(DateTime.Now - started).TotalSeconds;
                d["frames"] = frames;
                d["size"] = width + "x" + height;
                d["fps"] = fps;
                d["method"] = method;
                d["audio"] = audio != null ? (object)("录制中(进程 " + (audioPid == null ? 0 : SafePid()) + ")") : (audioError ?? "无");
                double secs = ff == null ? 0 : (DateTime.Now - started).TotalSeconds;
                d["grabbed"] = grabbed;
                d["real_fps"] = secs > 0.5 ? Math.Round(grabbed / secs, 1) : 0;
            }
            return d;
        }

        public Dictionary<string, object> Start(int fps)
        {
            lock (sync)
            {
                if (ff != null) return Describe();
                string exe = FindFfmpeg();
                if (exe == null) throw new ApiException(500, "找不到 ffmpeg.exe:放到程序目录,或在程序目录 ffmpeg.txt 里写它的完整路径");
                IntPtr h = hwndOf();
                RECT r;
                if (h == IntPtr.Zero || !Native.GetWindowRect(h, out r) || r.Width < 64) throw new ApiException(500, "取不到工作台窗口大小");
                this.fps = Math.Max(5, Math.Min(60, fps <= 0 ? 30 : fps));
                string dir = Path.Combine(AppDir, "录像");
                Directory.CreateDirectory(dir);
                file = Path.Combine(dir, DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".mp4");
                ffExe = exe;
                StartAudio();

                if (StartDda(exe, h))
                {
                    videoStart = lastSpawn;
                    started = DateTime.Now; frames = 0; grabbed = 0; stopping = false;
                    worker = null;
                    agent.Record("开始录像 " + Path.GetFileName(file), width + "x" + height + " " + this.fps + "fps (GPU 抓屏)", "running");
                    return Describe();
                }
                method = "printwindow";
                double k = r.Width > MaxWidth ? (double)MaxWidth / r.Width : 1.0;
                width = (int)(r.Width * k) & ~1; height = (int)(r.Height * k) & ~1;
                string[] encoders = { "-c:v h264_nvenc -preset p4 -rc vbr -cq 28 -b:v 0","-c:v libx264 -preset veryfast -crf 26", "-c:v mpeg4 -q:v 5" };
                byte[] black = new byte[width * height * 4];
                foreach (string enc in encoders)
                {
                    // ffmpeg 收到第一帧才初始化编码器,所以先喂一帧黑画面再判断能不能用
                    Process p = Spawn(exe, enc);
                    bool ok = true;
                    try { p.StandardInput.BaseStream.Write(black, 0, black.Length); p.StandardInput.BaseStream.Flush(); }
                    catch { ok = false; }
                    if (ok) ok = !p.WaitForExit(1500);
                    if (ok) { ff = p; break; }
                    try { if (!p.HasExited) p.Kill(); } catch { }
                    try { System.IO.File.Delete(file); } catch { }
                }
                if (ff == null && audio != null) { audio.Stop(); audio = null; }
                if (ff == null) throw new ApiException(500, "ffmpeg 启动失败(三种编码器都不行),看 录像\\ffmpeg.log");
                videoStart = lastSpawn;
                started = DateTime.Now; frames = 1; grabbed = 0; stopping = false;
                worker = new Thread(Loop);
                worker.IsBackground = true;
                worker.Start();
            }
            agent.Record("开始录像 " + Path.GetFileName(file), width + "x" + height + " " + fps + "fps", "running");
            return Describe();
        }

        Process Spawn(string exe, string enc)
        {
            return SpawnArgs(exe, "-hide_banner -loglevel warning -y -f rawvideo -pix_fmt bgra -s " + width + "x" + height +
                          " -framerate " + fps + " -i - " + enc +
                          " -pix_fmt yuv420p -movflags +frag_keyframe+empty_moov+default_base_moof \"" + file + "\"");
        }

        /// <summary>
        /// 窗口在主屏上时用 ffmpeg ddagrab(桌面复制,GPU 抓屏 + nvenc 编码,稳定 60 FPS,几乎不占 CPU)。
        /// 抓的是屏幕上这块区域:录像期间别让其他窗口挡住工作台。成功返回 true。
        /// </summary>
        bool StartDda(string exe, IntPtr h)
        {
            try
            {
                Native.MONITORINFO mi = new Native.MONITORINFO();
                mi.cbSize = Marshal.SizeOf(typeof(Native.MONITORINFO));
                IntPtr mon = Native.MonitorFromWindow(h, 2);
                if (mon == IntPtr.Zero || !Native.GetMonitorInfo(mon, ref mi) || (mi.dwFlags & 1) == 0) return false; // 只处理主屏(dxgi output 0)
                RECT r;
                if (!Native.GetWindowRect(h, out r)) return false;
                RECT m = mi.rcMonitor;
                int left = Math.Max(r.Left, m.Left), top = Math.Max(r.Top, m.Top);
                int right = Math.Min(r.Right, m.Right), bottom = Math.Min(r.Bottom, m.Bottom);
                if (Native.IsZoomed(h)) { left = mi.rcWork.Left; top = mi.rcWork.Top; right = mi.rcWork.Right; bottom = mi.rcWork.Bottom; }
                int x = (left - m.Left) & ~1, y = (top - m.Top) & ~1;
                width = (right - left) & ~1; height = (bottom - top) & ~1;
                if (width < 64 || height < 64) return false;
                string dir = Path.Combine(AppDir, "录像");
                Directory.CreateDirectory(dir);
                file = Path.Combine(dir, DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".mp4");
                string src = "-hide_banner -loglevel warning -y -f lavfi -i \"ddagrab=output_idx=0:framerate=" + fps +
                             ":draw_mouse=0:offset_x=" + x + ":offset_y=" + y + ":video_size=" + width + "x" + height + "\" ";
                string tail = " -movflags +frag_keyframe+empty_moov+default_base_moof \"" + file + "\"";
                foreach (string enc in new[] { "-c:v hevc_nvenc -preset p4 -rc vbr -cq 30 -b:v 0", "-c:v h264_nvenc -preset p4 -rc vbr -cq 28 -b:v 0" })
                {
                    Process p = SpawnArgs(exe, src + enc + tail);
                    if (!p.WaitForExit(2000)) { ff = p; method = "ddagrab"; return true; }
                    try { System.IO.File.Delete(file); } catch { }
                }
            }
            catch { }
            return false;
        }

        int SafePid() { try { return audioPid(); } catch { return 0; } }

        Process SpawnArgs(string exe, string args)
        {
            lastSpawn = DateTime.Now;
            ProcessStartInfo psi = new ProcessStartInfo(exe, args);
            psi.UseShellExecute = false; psi.CreateNoWindow = true;
            psi.RedirectStandardInput = true; psi.RedirectStandardError = true;
            Process p = new Process();
            p.StartInfo = psi;
            string log = Path.Combine(Path.GetDirectoryName(file), "ffmpeg.log");
            p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
            {
                if (e.Data != null) try { System.IO.File.AppendAllText(log, DateTime.Now.ToString("HH:mm:ss ") + e.Data + "\r\n"); } catch { }
            };
            p.Start();
            p.BeginErrorReadLine();
            return p;
        }

        void Loop()
        {
            Process p = ff;
            Stream input = p.StandardInput.BaseStream;
            byte[] buf = new byte[width * height * 4];
            Stopwatch clock = Stopwatch.StartNew();
            using (Bitmap frame = new Bitmap(width, height, PixelFormat.Format32bppRgb))
            {
                while (!stopping && !p.HasExited)
                {
                    try { Grab(frame); grabbed++; } catch { }
                    BitmapData bd = frame.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.ReadOnly, PixelFormat.Format32bppRgb);
                    try
                    {
                        for (int y = 0; y < height; y++)
                            Marshal.Copy(new IntPtr(bd.Scan0.ToInt64() + (long)y * bd.Stride), buf, y * width * 4, width * 4);
                    }
                    finally { frame.UnlockBits(bd); }
                    // 按时间补帧:抓得慢时重复上一帧,保证视频时长和真实时间一致
                    long due = (long)(clock.Elapsed.TotalSeconds * fps) + 1;
                    try
                    {
                        while (frames < due) { input.Write(buf, 0, buf.Length); frames++; }
                    }
                    catch { break; }
                    long nextMs = (long)((frames) * 1000.0 / fps);
                    int wait = (int)(nextMs - clock.ElapsedMilliseconds);
                    if (wait > 0) Thread.Sleep(wait);
                }
            }
        }

        void Grab(Bitmap frame)
        {
            IntPtr h = hwndOf();
            if (h == IntPtr.Zero || Native.IsIconic(h)) return; // 最小化时保留上一帧
            RECT r;
            if (!Native.GetWindowRect(h, out r) || r.Width <= 0 || r.Height <= 0) return;
            using (Bitmap shot = new Bitmap(r.Width, r.Height, PixelFormat.Format32bppRgb))
            {
                using (Graphics g = Graphics.FromImage(shot))
                {
                    IntPtr hdc = g.GetHdc();
                    try { Native.PrintWindow(h, hdc, 2); }
                    finally { g.ReleaseHdc(hdc); }
                }
                using (Graphics g = Graphics.FromImage(frame))
                {
                    if (shot.Width == width && shot.Height == height) g.DrawImageUnscaled(shot, 0, 0);
                    else
                    {   // 窗口大小变了:等比缩放居中,保持视频尺寸不变
                        g.Clear(Color.Black);
                        double s = Math.Min((double)width / shot.Width, (double)height / shot.Height);
                        int w = (int)(shot.Width * s), hh = (int)(shot.Height * s);
                        g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.Bilinear; g.CompositingMode = System.Drawing.Drawing2D.CompositingMode.SourceCopy; g.PixelOffsetMode = System.Drawing.Drawing2D.PixelOffsetMode.HighSpeed;
                        g.DrawImage(shot, (width - w) / 2, (height - hh) / 2, w, hh);
                    }
                }
            }
        }

        public Dictionary<string, object> Stop()
        {
            Process p;
            Thread t;
            lock (sync) { p = ff; t = worker; }
            if (p == null) return Describe();
            stopping = true;
            if (t != null) t.Join(3000);
            if (method == "ddagrab") { try { p.StandardInput.Write("q"); p.StandardInput.Flush(); } catch { } }
            try { p.StandardInput.BaseStream.Flush(); p.StandardInput.Close(); } catch { }
            if (!p.WaitForExit(15000)) try { p.Kill(); } catch { }
            Dictionary<string, object> d = Describe();
            d["recording"] = false;
            lock (sync) { ff = null; worker = null; }
            if (audio != null)
            {
                audio.Stop();
                d["audio"] = MuxAudio(audio);
                audio = null;
            }
            long size = 0;
            try { size = new FileInfo(file).Length; } catch { }
            d["mb"] = Math.Round(size / 1048576.0, 1);
            agent.Record("停止录像 " + Path.GetFileName(file), d["seconds"] + " 秒, " + d["mb"] + " MB", "happy");
            return d;
        }

        /// <summary>把录好的 PCM 合进 mp4(视频直接拷贝,声音编码 AAC)。返回结果说明。</summary>
        string MuxAudio(AudioCapture a)
        {
            string pcm = Path.ChangeExtension(file, ".pcm");
            if (a.Frames <= 0) { try { System.IO.File.Delete(pcm); } catch { } return a.Error ?? "没有录到声音"; }
            // 声音比视频早开始多少秒,就跳过多少;晚开始就往后推
            double off = (videoStart - a.StartedAt).TotalSeconds;
            string shift = off >= 0 ? "-ss " + off.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture)
                                    : "-itsoffset " + (-off).ToString("0.000", System.Globalization.CultureInfo.InvariantCulture);
            string tmp = Path.ChangeExtension(file, ".mux.mp4");
            Process p = SpawnArgs(ffExe, "-nostdin -hide_banner -loglevel warning -y -i \"" + file + "\" -f s16le -ar " + AudioCapture.Rate +
                " -ac " + AudioCapture.Channels + " " + shift + " -i \"" + pcm + "\" -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k -movflags +faststart \"" + tmp + "\"");
            if (!p.WaitForExit(600000)) { try { p.Kill(); } catch { } return "合成声音超时,声音留在 " + Path.GetFileName(pcm); }
            if (p.ExitCode != 0 || !System.IO.File.Exists(tmp)) return "合成声音失败,看 录像\\ffmpeg.log";
            try
            {
                System.IO.File.Delete(file);
                System.IO.File.Move(tmp, file);
                System.IO.File.Delete(pcm);
            }
            catch (Exception ex) { return "合成后改名失败: " + ex.Message; }
            return "已合成(" + Math.Round(a.Frames / (double)AudioCapture.Rate, 1) + " 秒)";
        }
    }
}
