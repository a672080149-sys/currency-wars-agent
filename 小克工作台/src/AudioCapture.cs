using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

namespace AppPilot
{
    /// <summary>
    /// 按进程录声音(Windows 进程回环,Win10 2004+ / Win11):只录目标进程及其子进程播放的声音,
    /// 别的程序(聊天提示音等)不会录进去。输出 48kHz 16bit 双声道原始 PCM,没声音的时段补静音,和墙钟时间对齐。
    /// </summary>
    public class AudioCapture
    {
        public const int Rate = 48000, Channels = 2, BlockAlign = 4;

        readonly int pid;
        readonly string path;
        Thread thread;
        volatile bool stopping;
        readonly ManualResetEvent started = new ManualResetEvent(false);
        public string Error { get; private set; }
        public long Frames { get; private set; }
        public DateTime StartedAt { get; private set; }

        AudioCapture(int pid, string path) { this.pid = pid; this.path = path; }

        /// <summary>开始录;失败返回 null,error 给出原因。</summary>
        public static AudioCapture TryStart(int pid, string path, out string error)
        {
            error = null;
            if (pid <= 0) { error = "找不到要录音的进程"; return null; }
            AudioCapture a = new AudioCapture(pid, path);
            a.thread = new Thread(a.Run);
            a.thread.IsBackground = true;
            a.thread.SetApartmentState(ApartmentState.MTA);
            a.thread.Start();
            a.started.WaitOne(5000);
            if (a.Error != null || !a.thread.IsAlive) { error = a.Error ?? "录音线程退出"; return null; }
            return a;
        }

        public void Stop()
        {
            stopping = true;
            if (thread != null) thread.Join(3000);
        }

        void Run()
        {
            IntPtr fmt = IntPtr.Zero, blob = IntPtr.Zero, pv = IntPtr.Zero;
            IntPtr evt = IntPtr.Zero;
            IAudioClient client = null;
            try
            {
                // AUDIOCLIENT_ACTIVATION_PARAMS { PROCESS_LOOPBACK, pid, INCLUDE_TARGET_PROCESS_TREE }
                blob = Marshal.AllocHGlobal(12);
                Marshal.WriteInt32(blob, 0, 1);
                Marshal.WriteInt32(blob, 4, pid);
                Marshal.WriteInt32(blob, 8, 0);
                // PROPVARIANT { vt = VT_BLOB, blob = { cbSize, pBlobData } }
                pv = Marshal.AllocHGlobal(24);
                for (int i = 0; i < 24; i += 4) Marshal.WriteInt32(pv, i, 0);
                Marshal.WriteInt16(pv, 0, 65);
                Marshal.WriteInt32(pv, 8, 12);
                Marshal.WriteIntPtr(pv, 16, blob);

                Handler handler = new Handler();
                IActivateAudioInterfaceAsyncOperation op;
                ActivateAudioInterfaceAsync("VAD\\Process_Loopback", typeof(IAudioClient).GUID, pv, handler, out op);
                if (!handler.Done.WaitOne(5000)) throw new Exception("激活进程回环超时");
                int hr; object unk;
                op.GetActivateResult(out hr, out unk);
                if (hr < 0) throw new Exception("激活进程回环失败 0x" + hr.ToString("X8"));
                client = (IAudioClient)unk;

                fmt = Marshal.AllocHGlobal(18);
                Marshal.WriteInt16(fmt, 0, 1);                  // PCM
                Marshal.WriteInt16(fmt, 2, Channels);
                Marshal.WriteInt32(fmt, 4, Rate);
                Marshal.WriteInt32(fmt, 8, Rate * BlockAlign);
                Marshal.WriteInt16(fmt, 12, BlockAlign);
                Marshal.WriteInt16(fmt, 14, 16);
                Marshal.WriteInt16(fmt, 16, 0);
                const int LOOPBACK = 0x00020000, EVENTCALLBACK = 0x00040000, AUTOCONVERT = unchecked((int)0x80000000), SRC_QUALITY = 0x08000000;
                client.Initialize(0, LOOPBACK | EVENTCALLBACK | AUTOCONVERT | SRC_QUALITY, 2000000, 0, fmt, IntPtr.Zero);
                evt = CreateEvent(IntPtr.Zero, false, false, null);
                client.SetEventHandle(evt);
                object svc;
                client.GetService(typeof(IAudioCaptureClient).GUID, out svc);
                IAudioCaptureClient cap = (IAudioCaptureClient)svc;

                using (FileStream fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read, 1 << 16))
                {
                    client.Start();
                    StartedAt = DateTime.Now;
                    System.Diagnostics.Stopwatch clock = System.Diagnostics.Stopwatch.StartNew();
                    started.Set();
                    byte[] buf = new byte[Rate * BlockAlign];
                    byte[] zeros = new byte[Rate * BlockAlign / 10];
                    while (!stopping)
                    {
                        WaitForSingleObject(evt, 100);
                        uint n;
                        cap.GetNextPacketSize(out n);
                        while (n > 0)
                        {
                            IntPtr data; uint frames, flags; ulong dp, qp;
                            cap.GetBuffer(out data, out frames, out flags, out dp, out qp);
                            int bytes = (int)frames * BlockAlign;
                            if (bytes > buf.Length) buf = new byte[bytes];
                            if ((flags & 2) != 0 || data == IntPtr.Zero) Array.Clear(buf, 0, bytes);
                            else Marshal.Copy(data, buf, 0, bytes);
                            fs.Write(buf, 0, bytes);
                            Frames += frames;
                            cap.ReleaseBuffer(frames);
                            cap.GetNextPacketSize(out n);
                        }
                        // 没有声音时系统不送数据:落后墙钟 200ms 以上就补静音,保持和视频同步
                        long due = (long)(clock.Elapsed.TotalSeconds * Rate);
                        while (Frames < due - Rate / 5)
                        {
                            long gap = Math.Min(zeros.Length / BlockAlign, due - Rate / 20 - Frames);
                            if (gap <= 0) break;
                            fs.Write(zeros, 0, (int)gap * BlockAlign);
                            Frames += gap;
                        }
                    }
                    client.Stop();
                }
            }
            catch (Exception ex)
            {
                Error = ex.Message;
            }
            finally
            {
                started.Set();
                if (client != null) try { Marshal.ReleaseComObject(client); } catch { }
                if (evt != IntPtr.Zero) CloseHandle(evt);
                if (fmt != IntPtr.Zero) Marshal.FreeHGlobal(fmt);
                if (pv != IntPtr.Zero) Marshal.FreeHGlobal(pv);
                if (blob != IntPtr.Zero) Marshal.FreeHGlobal(blob);
            }
        }

        // ---------------- COM interop ----------------

        [ComImport, Guid("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IAgileObject { }

        [ComImport, Guid("72A22D78-CDE4-431D-B8CC-843A71199B6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IActivateAudioInterfaceAsyncOperation
        {
            void GetActivateResult(out int activateResult, [MarshalAs(UnmanagedType.IUnknown)] out object activatedInterface);
        }

        [ComImport, Guid("41D949AB-9862-444A-80F6-C261334DA5EB"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IActivateAudioInterfaceCompletionHandler
        {
            void ActivateCompleted(IActivateAudioInterfaceAsyncOperation op);
        }

        [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
        class Handler : IActivateAudioInterfaceCompletionHandler, IAgileObject
        {
            public readonly ManualResetEvent Done = new ManualResetEvent(false);
            public void ActivateCompleted(IActivateAudioInterfaceAsyncOperation op) { Done.Set(); }
        }

        [ComImport, Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IAudioClient
        {
            void Initialize(int shareMode, int streamFlags, long bufferDuration, long periodicity, IntPtr format, IntPtr sessionGuid);
            void GetBufferSize(out uint frames);
            void GetStreamLatency(out long latency);
            void GetCurrentPadding(out uint padding);
            [PreserveSig] int IsFormatSupported(int shareMode, IntPtr format, out IntPtr closest);
            void GetMixFormat(out IntPtr format);
            void GetDevicePeriod(out long defaultPeriod, out long minimumPeriod);
            void Start();
            void Stop();
            void Reset();
            void SetEventHandle(IntPtr handle);
            void GetService([MarshalAs(UnmanagedType.LPStruct)] Guid riid, [MarshalAs(UnmanagedType.IUnknown)] out object service);
        }

        [ComImport, Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IAudioCaptureClient
        {
            void GetBuffer(out IntPtr data, out uint frames, out uint flags, out ulong devicePosition, out ulong qpcPosition);
            void ReleaseBuffer(uint frames);
            void GetNextPacketSize(out uint frames);
        }

        [DllImport("Mmdevapi.dll", ExactSpelling = true, PreserveSig = false)]
        static extern void ActivateAudioInterfaceAsync([MarshalAs(UnmanagedType.LPWStr)] string deviceInterfacePath,
            [MarshalAs(UnmanagedType.LPStruct)] Guid riid, IntPtr activationParams,
            IActivateAudioInterfaceCompletionHandler handler, out IActivateAudioInterfaceAsyncOperation op);

        [DllImport("kernel32.dll")] static extern IntPtr CreateEvent(IntPtr attrs, bool manualReset, bool initial, string name);
        [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint ms);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    }
}
