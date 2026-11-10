using System;
using System.Collections.Generic;
using System.Collections;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

[assembly: System.Reflection.AssemblyTitle("TEMU 工作台")]
[assembly: System.Reflection.AssemblyVersion("1.2.198.0")]
[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8", FrameworkDisplayName=".NET Framework 4.8")]

namespace TemuWorkbench {
    static class Program {
        internal const string Origin = "http://127.0.0.1:8787";
        internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 131072 };
        internal static string Root, Profile, Identity;
        internal static Icon AppIcon;
        static Mutex clientMutex;
        static EventWaitHandle activate;
        static RegisteredWaitHandle activationRegistration;

        [STAThread]
        static void Main(string[] args) {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Root = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));
            string mode = "choose", profileOverride = null;
            for (int i = 0; i < args.Length; i++) {
                if (args[i] == "--root" && i + 1 < args.Length) Root = Path.GetFullPath(args[++i]);
                else if (args[i] == "--mode" && i + 1 < args.Length) mode = args[++i];
                else if (args[i] == "--profile-dir" && i + 1 < args.Length) profileOverride = Path.GetFullPath(args[++i]);
                else { MessageBox.Show("启动参数无效。", "TEMU 工作台"); return; }
            }
            if (!File.Exists(Path.Combine(Root, "server.py")) || (mode != "choose" && mode != "client" && mode != "web")) {
                MessageBox.Show("请从完整程序目录启动工作台。", "TEMU 工作台"); return;
            }
            using (var sha = SHA256.Create()) Identity = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Root.ToLowerInvariant()))).Replace("-", "").Substring(0, 20);
            Profile = profileOverride ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TemuWorkbench", Identity);
            Directory.CreateDirectory(Profile);
            string iconFile=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"TemuWorkbench.ico");
            AppIcon = File.Exists(iconFile) ? new Icon(iconFile,new Size(64,64)) : SystemIcons.Application;
            Application.ThreadException += (s, e) => ShowError(e.Exception);
            try {
                using (var chooser = new ModeDialog(mode)) {
                    if (chooser.ShowDialog() != DialogResult.OK) return;
                    var health = chooser.Health;
                    if (chooser.SelectedMode == "web") { OpenBrowser("workbench"); return; }
                    bool owner;
                    clientMutex = new Mutex(true, "Local\\TemuClient-ui1-" + Identity, out owner);
                    if (!owner) {
                        try { EventWaitHandle.OpenExisting("Local\\TemuActivate-ui1-" + Identity).Set(); }
                        catch (WaitHandleCannotBeOpenedException) { MessageBox.Show("客户端正在启动，请稍后再打开。", "TEMU 工作台"); }
                        return;
                    }
                    activate = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\TemuActivate-ui1-" + Identity);
                    using (var window = new WorkbenchWindow(health, Origin + "/", true)) {
                        activationRegistration = ThreadPool.RegisterWaitForSingleObject(activate, (s, timedOut) => {
                            try { if (!window.IsDisposed && window.IsHandleCreated) window.BeginInvoke(new Action(window.RestoreWindow)); } catch (InvalidOperationException) { }
                        }, null, Timeout.Infinite, false);
                        Application.Run(window);
                    }
                }
            } catch (Exception error) { ShowError(error); }
            finally {
                if (activationRegistration != null) activationRegistration.Unregister(null);
                if (activate != null) activate.Dispose();
                if (clientMutex != null) { try { clientMutex.ReleaseMutex(); } catch (ApplicationException) {} clientMutex.Dispose(); }
            }
        }

        internal static void ShowError(Exception error) {
            MessageBox.Show(error.GetBaseException().Message + "\n\n后台任务不会因关闭此窗口而停止。可使用 start-web.cmd 打开网页端。", "TEMU 工作台", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        internal static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"") + "\""; }

        internal static async Task<Dictionary<string, object>> EnsureBackend() {
            string python = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python", "Python312", "python.exe");
            string prefix = "";
            if (!File.Exists(python)) { python = "py.exe"; prefix = "-3 "; }
            string helper = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "launcher.py"));
            var start = new ProcessStartInfo(python, prefix + Quote(helper) + " --root " + Quote(Root)) {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true,
                RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
                WorkingDirectory = Root
            };
            using (var process = Process.Start(start)) {
                var stdout = process.StandardOutput.ReadToEndAsync();
                var stderr = process.StandardError.ReadToEndAsync();
                var exited = Task.Run(() => process.WaitForExit());
                if (await Task.WhenAny(exited, Task.Delay(95000)) != exited)
                    throw new Exception("后台仍在初始化；请稍后重试。已启动的后台会继续运行，不会重复创建。");
                await exited;
                string resultText = await stdout;
                string errorText = await stderr;
                Dictionary<string, object> result;
                try { result = Json.Deserialize<Dictionary<string, object>>(resultText.Trim()); }
                catch { throw new Exception("启动器未能读取后台状态，请检查 Python 3.12 和 logs 目录。" + (errorText.Length > 0 ? "\n" + errorText.Substring(0, Math.Min(700, errorText.Length)) : "")); }
                if (result.ContainsKey("error")) throw new Exception(Convert.ToString(result["error"]));
                if (process.ExitCode != 0) throw new Exception("后台尚未准备完成，请查看本机日志。");
                return result;
            }
        }

        internal static bool IsLocal(string value) {
            Uri uri;
            return Uri.TryCreate(value, UriKind.Absolute, out uri) && uri.Scheme == "http" && uri.Host == "127.0.0.1" && uri.Port == 8787;
        }
        internal static string PageUrl(string page) {
            string[] allowed = { "workbench", "automation", "tasks", "settings", "pricing", "zhihou", "orders" };
            if (Array.IndexOf(allowed, page) < 0) page = "workbench";
            return Origin + "/?page=" + Uri.EscapeDataString(page);
        }
        internal static void OpenBrowser(string page) { OpenExternal(PageUrl(page)); }
        internal static void OpenExternal(string url) {
            Uri uri;
            if (!Uri.TryCreate(url, UriKind.Absolute, out uri) || (uri.Scheme != "http" && uri.Scheme != "https")) return;
            // Chrome keeps the installed pricing/1688 extensions and its
            // existing profile. Do not copy profiles or remote-debug Chrome.
            string[] candidates = {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Google", "Chrome", "Application", "chrome.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Google", "Chrome", "Application", "chrome.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Google", "Chrome", "Application", "chrome.exe")
            };
            foreach (string chrome in candidates) if (File.Exists(chrome)) { Process.Start(new ProcessStartInfo(chrome, Quote(url)) { UseShellExecute = false }); return; }
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }
    }

    sealed class ModeDialog : Form {
        internal string SelectedMode;
        internal Dictionary<string, object> Health;
        Label status;
        Button client, web;
        string initialMode;
        Dictionary<Control,Rectangle> logicalBounds=new Dictionary<Control,Rectangle>();
        internal ModeDialog(string mode) {
            SuspendLayout();AutoScaleMode = AutoScaleMode.None;
            initialMode = mode;
            Text = "打开 TEMU 工作台"; Icon = Program.AppIcon;
            StartPosition = FormStartPosition.CenterScreen; ClientSize = new Size(590, 315);
            FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
            Font = new Font("Microsoft YaHei UI", 10); BackColor = Color.FromArgb(245, 247, 252);
            var title = new Label { Text = "选择打开方式", Font = new Font(Font.FontFamily, 21, FontStyle.Bold), AutoSize = true, Location = new Point(30, 27) };
            var note = new Label { Text = "共用同一个本地后台、商品和任务队列", AutoSize = true, ForeColor = Color.SlateGray, Location = new Point(33, 79) };
            client = Choice("本地客户端", "独立窗口 · 持久缓存 · 托盘", new Point(32, 120), true);
            web = Choice("网页端", "Chrome 浏览器 · 保留扩展联动", new Point(307, 120), false);
            client.Click += async (s, e) => await Choose("client");
            web.Click += async (s, e) => await Choose("web");
            status = new Label { Text = "已运行的后台会直接复用，关闭窗口不停止任务。", Location = new Point(32, 245), Size = new Size(525, 55), ForeColor = Color.SlateGray };
            Controls.AddRange(new Control[] {title, note, client, web, status});
            foreach(Control child in Controls)logicalBounds[child]=child.Bounds;
            Shown += async (s, e) => { ScaleChoices();if (initialMode != "choose") await Choose(initialMode); };
            DpiChanged+=(s,e)=>BeginInvoke(new Action(ScaleChoices));
            ResumeLayout(true);
        }
        void ScaleChoices(){
            float scale=DeviceDpi/96F;SuspendLayout();ClientSize=new Size((int)(590*scale),(int)(315*scale));
            foreach(var item in logicalBounds){var r=item.Value;item.Key.Location=new Point((int)(r.X*scale),(int)(r.Y*scale));if(!item.Key.AutoSize)item.Key.Size=new Size((int)(r.Width*scale),(int)(r.Height*scale));}
            ResumeLayout(true);
        }
        Button Choice(string title, string note, Point at, bool primary) {
            return new Button { Text = title + "\n\n" + note, Location = at, Size = new Size(250, 104),
                FlatStyle = FlatStyle.Flat, BackColor = primary ? Color.FromArgb(34, 101, 246) : Color.White,
                ForeColor = primary ? Color.White : Color.FromArgb(30, 48, 75), Cursor = Cursors.Hand };
        }
        async Task Choose(string mode) {
            if (!client.Enabled) return;
            client.Enabled = web.Enabled = false; status.Text = "正在连接本地后台；首次启动可能需要稍候…";
            try {
                Health = await Program.EnsureBackend();
                if (IsDisposed) return;
                SelectedMode = mode; DialogResult = DialogResult.OK; Close();
            } catch (Exception error) { if (!IsDisposed) { status.Text = error.GetBaseException().Message; client.Enabled = web.Enabled = true; } }
        }
    }

    sealed class RoundedButton : Button {
        bool hover, pressed;
        internal Color Fill = Color.White;
        internal RoundedButton() {
            FlatStyle=FlatStyle.Flat;FlatAppearance.BorderSize=0;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw,true);
            Cursor=Cursors.Hand;
        }
        protected override void OnMouseEnter(EventArgs e){hover=true;Invalidate();base.OnMouseEnter(e);}
        protected override void OnMouseLeave(EventArgs e){hover=pressed=false;Invalidate();base.OnMouseLeave(e);}
        protected override void OnMouseDown(MouseEventArgs e){pressed=true;Invalidate();base.OnMouseDown(e);}
        protected override void OnMouseUp(MouseEventArgs e){pressed=false;Invalidate();base.OnMouseUp(e);}
        protected override void OnPaint(PaintEventArgs e) {
            e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;
            e.Graphics.Clear(Parent == null ? Color.White : Parent.BackColor);
            float radius=Math.Max(6,7*DeviceDpi/96F),width=ClientSize.Width-1,height=ClientSize.Height-1;
            using(var path=new GraphicsPath()) {
                path.AddArc(0,0,radius*2,radius*2,180,90);path.AddArc(width-radius*2,0,radius*2,radius*2,270,90);
                path.AddArc(width-radius*2,height-radius*2,radius*2,radius*2,0,90);path.AddArc(0,height-radius*2,radius*2,radius*2,90,90);path.CloseFigure();
                var fill=pressed ? Color.FromArgb(220,231,251) : hover ? Color.FromArgb(235,242,253) : Fill;
                using(var brush=new SolidBrush(fill))e.Graphics.FillPath(brush,path);
            }
            TextRenderer.DrawText(e.Graphics,Text,Font,ClientRectangle,Enabled ? ForeColor : Color.SlateGray,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis);
            if(Focused && ShowFocusCues)ControlPaint.DrawFocusRectangle(e.Graphics,Rectangle.Inflate(ClientRectangle,-5,-5));
        }
    }

    sealed class WorkbenchWindow : Form {
        readonly WebView2 view = new WebView2();
        readonly Dictionary<string, object> health;
        readonly string initialUrl;
        readonly bool main;
        NotifyIcon tray;
        Label status;
        TableLayoutPanel nativeToolbar;
        Button menuButton;
        FlowLayoutPanel nativeActions;
        FlowLayoutPanel pageTabs;
        ContextMenuStrip tabsOverflowMenu;
        readonly List<Dictionary<string, object>> openedPages = new List<Dictionary<string, object>>();
        string activePageKey = "";
        static readonly string[] TabPages = {"workbench","collectionAccount","dataStatistics","orders","zhihou","pricing","collectionAutomation","automation","automaticTasks","tasks","publishRecords","settings","products","templates"};
        internal WorkbenchWindow(Dictionary<string, object> info, string url, bool isMain) {
            SuspendLayout();AutoScaleMode = AutoScaleMode.None;
            health = info; initialUrl = url; main = isMain;
            Text = "TEMU 工作台 · 客户端"; Icon = Program.AppIcon;
            Font = new Font("Microsoft YaHei UI", 9); StartPosition = FormStartPosition.CenterScreen;
            var area = Screen.PrimaryScreen.WorkingArea;
            Size = new Size(Math.Min(1480, area.Width - 45), Math.Min(960, area.Height - 45));
            MinimumSize = new Size(980, 640);
            var toolbar = new TableLayoutPanel { Dock=DockStyle.Top,Height=44,Padding=new Padding(10,6,12,6),BackColor=Color.White,ColumnCount=3,RowCount=1 };
            toolbar.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));toolbar.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100F));toolbar.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            var actions=new FlowLayoutPanel {AutoSize=true,Dock=DockStyle.Fill,BackColor=Color.White,WrapContents=false,Margin=Padding.Empty,Padding=Padding.Empty};
            var tips=new ToolTip();
            var back=Action("‹",()=>{if(view.CanGoBack)view.GoBack();});back.Tag=32;back.Font=new Font("Segoe UI",17);back.AccessibleName="返回上一页";tips.SetToolTip(back,"返回上一页");actions.Controls.Add(back);
            var refresh=Action("↻",()=>{if(view.CoreWebView2!=null)view.Reload();});refresh.Tag=32;refresh.Font=new Font("Segoe UI Symbol",13);refresh.AccessibleName="刷新";tips.SetToolTip(refresh,"刷新界面，后台任务继续运行");actions.Controls.Add(refresh);
            nativeToolbar=toolbar;nativeActions=actions;
            var more=Action("⋯",()=>{});more.Tag=34;more.Font=new Font("Segoe UI",15);more.AccessibleName="更多操作";tips.SetToolTip(more,"更多操作");
            var optionsMenu=new ContextMenuStrip {Font=Font,ShowImageMargin=false};
            optionsMenu.Items.Add("Chrome 打开",null,(s,e)=>Program.OpenBrowser("workbench"));
            optionsMenu.Items.Add("切换打开方式",null,(s,e)=>{using(var choose=new ModeDialog("choose"))if(choose.ShowDialog(this)==DialogResult.OK && choose.SelectedMode=="web")Program.OpenBrowser("workbench");});
            optionsMenu.Items.Add("最小化到托盘",null,(s,e)=>{if(tray!=null)Hide();else WindowState=FormWindowState.Minimized;});
            more.Click+=(s,e)=>optionsMenu.Show(more,new Point(0,more.Height));actions.Controls.Add(more);menuButton=more;
            status = new Label {Text="●  后台已连接",AutoSize=true,Anchor=AnchorStyles.Right,ForeColor=Color.FromArgb(24,128,101),BackColor=Color.FromArgb(238,248,243),Padding=new Padding(11,5,11,5),Margin=Padding.Empty};
            pageTabs=new FlowLayoutPanel {Dock=DockStyle.Fill,WrapContents=false,Margin=new Padding(8,0,10,0),Padding=Padding.Empty,AccessibleName="已打开页面"};
            pageTabs.SizeChanged+=(s,e)=>RenderPageTabs();
            toolbar.Controls.Add(actions,0,0);toolbar.Controls.Add(pageTabs,1,0);toolbar.Controls.Add(status,2,0);
            ReadPageTabs();
            toolbar.Paint+=(s,e)=>{using(var pen=new Pen(Color.FromArgb(230,235,243)))e.Graphics.DrawLine(pen,0,toolbar.Height-1,toolbar.Width,toolbar.Height-1);};
            view.Dock = DockStyle.Fill;
            Controls.Add(view); Controls.Add(toolbar);
            if (main) {
                var menu = new ContextMenuStrip();
                menu.Items.Add("显示工作台", null, (s, e) => RestoreWindow());
                menu.Items.Add("Chrome 打开", null, (s, e) => Program.OpenBrowser("workbench"));
                menu.Items.Add("退出客户端（后台继续运行）", null, (s, e) => Close());
                tray = new NotifyIcon { Icon = Icon, Text = "TEMU 工作台 · 后台独立运行", ContextMenuStrip = menu, Visible = true };
                tray.DoubleClick += (s, e) => RestoreWindow();
            }
            Shown += async (s, e) => {
                var workingArea=Screen.FromHandle(Handle).WorkingArea;
                if(Width>workingArea.Width-20 || Height>workingArea.Height-20)
                    Bounds=new Rectangle(workingArea.Left+10,workingArea.Top+10,Math.Min(Width,workingArea.Width-20),Math.Min(Height,workingArea.Height-20));
                ScaleChrome();
                await Initialize();
            };
            DpiChanged+=(s,e)=>BeginInvoke(new Action(ScaleChrome));
            FormClosed += (s, e) => { if (tray != null) { tray.Visible = false; tray.Dispose(); } if(tabsOverflowMenu!=null)tabsOverflowMenu.Dispose();view.Dispose(); };
            AutoScaleDimensions=new SizeF(96F,96F);AutoScaleMode=AutoScaleMode.Dpi;ResumeLayout(true);
        }
        Button Action(string text, Action handler) {
            var button = new RoundedButton { Text=text,Width=96,Height=30,ForeColor=Color.FromArgb(67,82,106),Margin=new Padding(0,0,5,0) };
            button.Click += (s, e) => { try { handler(); } catch (Exception error) { Program.ShowError(error); } };
            return button;
        }
        void ScaleChrome(){
            if(nativeToolbar==null)return;
            float scale=DeviceDpi/96F;nativeToolbar.SuspendLayout();nativeActions.SuspendLayout();
            nativeToolbar.Height=(int)Math.Round(44*scale);nativeToolbar.Padding=new Padding((int)(10*scale),(int)(6*scale),(int)(12*scale),(int)(6*scale));
            foreach(Control child in nativeActions.Controls)if(child is Button){
                int width=child.Tag is int ? (int)child.Tag : 96;child.Size=new Size((int)Math.Round(width*scale),(int)Math.Round(30*scale));
                child.Margin=new Padding(0,0,(int)(5*scale),0);
            }
            status.Padding=new Padding((int)(11*scale),(int)(5*scale),(int)(11*scale),(int)(5*scale));
            nativeActions.ResumeLayout(true);nativeToolbar.ResumeLayout(true);
            RenderPageTabs();
        }
        static Dictionary<string, object> PageRoute(Dictionary<string, object> value) {
            try {
                string page=Convert.ToString(value["page"]);
                int collection=Convert.ToInt32(value["collectionAccountId"]),order=Convert.ToInt32(value["orderAccountId"]);
                if(Array.IndexOf(TabPages,page)<0 || collection<1 || collection>20 || order<1 || order>20)return null;
                string title=value.ContainsKey("title") ? Convert.ToString(value["title"]) : page;
                if(title.Length>64)title=title.Substring(0,64);
                string key=(page=="collectionAccount" ? "workbench" : page);
                if(page=="orders")key+=":"+order;
                else if(page=="workbench" || page=="collectionAccount")key+=":"+collection;
                return new Dictionary<string,object>{{"page",page},{"collectionAccountId",collection},{"orderAccountId",order},{"title",title},{"key",key}};
            } catch {return null;}
        }
        string PageAddress(Dictionary<string,object> route) {
            string url=Program.Origin+"/?page="+Uri.EscapeDataString(Convert.ToString(route["page"]))+"&collectionAccount="+route["collectionAccountId"];
            if(Convert.ToString(route["page"])=="orders")url+="&orderAccount="+route["orderAccountId"];
            return url;
        }
        void ReadPageTabs() {
            if(!main)return;
            try {
                string path=Path.Combine(Program.Profile,"navigation-tabs.json");
                if(!File.Exists(path) || new FileInfo(path).Length>32768)return;
                var state=Program.Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path));
                var items=state["pages"] as IEnumerable;
                if(items==null)return;
                foreach(object item in items) {
                    var value=item as Dictionary<string,object>;if(value==null)continue;
                    var route=PageRoute(value);if(route==null || openedPages.Exists(p=>Convert.ToString(p["key"])==Convert.ToString(route["key"])))continue;
                    openedPages.Add(route);if(openedPages.Count>=16)break;
                }
                string key=Convert.ToString(state["activeKey"]);
                activePageKey=openedPages.Exists(p=>Convert.ToString(p["key"])==key) ? key : openedPages.Count>0 ? Convert.ToString(openedPages[0]["key"]) : "";
            } catch {openedPages.Clear();activePageKey="";}
        }
        void SavePageTabs() {
            if(!main)return;
            try {File.WriteAllText(Path.Combine(Program.Profile,"navigation-tabs.json"),Program.Json.Serialize(new Dictionary<string,object>{{"pages",openedPages},{"activeKey",activePageKey}}));}catch(IOException){}catch(UnauthorizedAccessException){}
        }
        void RecordPage(Dictionary<string,object> value) {
            var route=PageRoute(value);if(route==null)return;
            string key=Convert.ToString(route["key"]);
            int index=openedPages.FindIndex(p=>Convert.ToString(p["key"])==key);
            if(index>=0)openedPages[index]=route;
            else {if(openedPages.Count>=16)openedPages.RemoveAt(0);openedPages.Add(route);}
            activePageKey=key;SavePageTabs();RenderPageTabs();
        }
        async Task<bool> SwitchPage(Dictionary<string,object> route) {
            if(view.CoreWebView2==null)return false;
            try {
                string json=Program.Json.Serialize(route);
                string result=await view.CoreWebView2.ExecuteScriptAsync("window.workbenchNavigation ? window.workbenchNavigation.open("+json+") : false");
                return result=="true";
            } catch(Exception error){Program.ShowError(error);return false;}
        }
        async void ClosePage(Dictionary<string,object> route) {
            if(openedPages.Count<=1)return;
            int index=openedPages.IndexOf(route);if(index<0)return;
            bool wasActive=Convert.ToString(route["key"])==activePageKey;
            if(wasActive){var next=openedPages[index>0 ? index-1 : 1];if(!await SwitchPage(next))return;activePageKey=Convert.ToString(next["key"]);}
            openedPages.RemoveAll(p=>Convert.ToString(p["key"])==Convert.ToString(route["key"]));
            SavePageTabs();RenderPageTabs();
        }
        void RenderPageTabs() {
            if(pageTabs==null)return;
            pageTabs.SuspendLayout();
            while(pageTabs.Controls.Count>0){var item=pageTabs.Controls[0];pageTabs.Controls.RemoveAt(0);item.Dispose();}
            float scale=DeviceDpi/96F;
            int available=Math.Max((int)(120*scale),pageTabs.ClientSize.Width),capacity=Math.Max(1,(int)(available/(140*scale)));
            bool overflow=openedPages.Count>capacity;
            if(overflow)capacity=Math.Max(1,(int)((available-32*scale)/(140*scale)));
            int activeIndex=Math.Max(0,openedPages.FindIndex(p=>Convert.ToString(p["key"])==activePageKey));
            int start=Math.Max(0,activeIndex-capacity+1),finish=Math.Min(openedPages.Count,start+capacity);
            if(tabsOverflowMenu!=null)tabsOverflowMenu.Dispose();tabsOverflowMenu=null;
            for(int pageIndex=start;pageIndex<finish;pageIndex++){
                var route=openedPages[pageIndex];
                bool selected=Convert.ToString(route["key"])==activePageKey;
                string title=Convert.ToString(route["title"]);
                int slot=Math.Max((int)(100*scale),(available-(overflow ? (int)(32*scale) : 0))/Math.Max(1,finish-start)-(int)(5*scale));
                int width=Math.Min(slot,Math.Max((int)(120*scale),Math.Min((int)(220*scale),TextRenderer.MeasureText(title,Font).Width+(int)(46*scale)))),height=(int)(30*scale),closeWidth=(int)(26*scale);
                var group=new Panel {Width=width,Height=height,Margin=new Padding(0,0,(int)(5*scale),0)};
                var color=selected ? Color.FromArgb(232,240,255) : Color.FromArgb(246,248,252);
                var tab=new RoundedButton {Text=title,Font=Font,Fill=color,ForeColor=selected ? Color.FromArgb(30,91,206) : Color.FromArgb(83,100,125),Bounds=new Rectangle(0,0,width-closeWidth,height),AccessibleName="切换页面 "+title};
                tab.Click+=async (s,e)=>await SwitchPage(route);
                var close=new RoundedButton {Text="×",Font=Font,Fill=color,ForeColor=Color.SlateGray,Bounds=new Rectangle(width-closeWidth,0,closeWidth,height),Enabled=openedPages.Count>1,AccessibleName="关闭标签 "+title};
                close.Click+=(s,e)=>ClosePage(route);
                group.Controls.Add(tab);group.Controls.Add(close);pageTabs.Controls.Add(group);
            }
            if(overflow){
                tabsOverflowMenu=new ContextMenuStrip {Font=Font};
                foreach(var route in openedPages){var selectedRoute=route;var entry=tabsOverflowMenu.Items.Add(Convert.ToString(route["title"]),null,async(s,e)=>await SwitchPage(selectedRoute));((ToolStripMenuItem)entry).Checked=Convert.ToString(route["key"])==activePageKey;}
                var extra=new RoundedButton {Text="⌄",Font=Font,Width=(int)(28*scale),Height=(int)(30*scale),Margin=Padding.Empty,AccessibleName="全部已打开页面"};
                extra.Click+=(s,e)=>tabsOverflowMenu.Show(extra,new Point(0,extra.Height));pageTabs.Controls.Add(extra);
            }
            pageTabs.ResumeLayout(true);
        }
        internal void RestoreWindow() { Show(); if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal; Activate(); }
        async Task Initialize() {
            try {
                var options = new CoreWebView2EnvironmentOptions("--disk-cache-size=268435456");
                var environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Program.Profile, "WebView2"), options);
                await view.EnsureCoreWebView2Async(environment);
                view.ZoomFactor=1.0;
                ScaleChrome();
                File.WriteAllText(Path.Combine(Program.Profile,"display-diagnostics.json"),Program.Json.Serialize(new Dictionary<string,object>{
                    {"deviceDpi",DeviceDpi},{"autoScaleMode",AutoScaleMode.ToString()},
                    {"targetFramework",AppDomain.CurrentDomain.SetupInformation.TargetFrameworkName ?? ".NETFramework,Version=v4.8"},
                    {"zoomFactor",view.ZoomFactor},{"toolbarHeight",nativeToolbar.Height},
                    {"menuButtonWidth",menuButton.Width},{"menuButtonHeight",menuButton.Height},
                    {"buttonFontPoints",menuButton.Font.SizeInPoints},{"version","1.2.198"}}));
                view.CoreWebView2.Settings.IsStatusBarEnabled = false;
                view.CoreWebView2.Settings.IsPasswordAutosaveEnabled = false;
                view.CoreWebView2.Settings.IsGeneralAutofillEnabled = false;
                await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                    "if(window===window.top && location.origin==='" + Program.Origin + "'){Object.defineProperty(window,'temuDesktop',{value:Object.freeze({openBrowser:function(page){window.chrome.webview.postMessage({action:'open-browser',page:page});},navigationChanged:function(route){window.chrome.webview.postMessage({action:'navigation-state',route:route});}})});}");
                view.CoreWebView2.WebMessageReceived += (s, e) => {
                    if (!Program.IsLocal(e.Source)) return;
                    try {
                        if (e.WebMessageAsJson.Length > 2048) return;
                        var message = Program.Json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson);
                        if(message.ContainsKey("action") && Convert.ToString(message["action"])=="navigation-state" && message.ContainsKey("route")) {
                            var route=message["route"] as Dictionary<string,object>;if(route!=null)RecordPage(route);return;
                        }
                        if (message.ContainsKey("action") && Convert.ToString(message["action"]) == "open-browser")
                            Program.OpenBrowser(message.ContainsKey("page") ? Convert.ToString(message["page"]) : "workbench");
                    } catch (Exception) { /* Ignore malformed/untrusted messages. */ }
                };
                view.CoreWebView2.NavigationStarting += (s, e) => {
                    if (Program.IsLocal(e.Uri) || e.Uri == "about:blank" || e.Uri.StartsWith("blob:" + Program.Origin + "/", StringComparison.Ordinal)) return;
                    e.Cancel = true;
                    if (e.IsUserInitiated) Program.OpenExternal(e.Uri);
                };
                view.CoreWebView2.NewWindowRequested += (s, e) => {
                    e.Handled = true;
                    if (Program.IsLocal(e.Uri)) new WorkbenchWindow(health, e.Uri, false).Show();
                    else if (e.IsUserInitiated) Program.OpenExternal(e.Uri);
                };
                view.CoreWebView2.PermissionRequested += (s, e) => { e.State = CoreWebView2PermissionState.Deny; };
                view.CoreWebView2.ProcessFailed += (s, e) => { status.Text = "界面需刷新"; };
                var restored=main ? openedPages.Find(p=>Convert.ToString(p["key"])==activePageKey) : null;
                view.Source = new Uri(restored==null ? initialUrl : PageAddress(restored));
            } catch (Exception error) { status.Text = "界面未连接"; Program.ShowError(error); }
        }
    }
}
