// FrameLink online installer. GPL-3.0-or-later; see the accompanying source archive.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class FrameLinkInstaller
{
    private const string Revision = "325f0bf4e20a5d1ac12b15842d0ff1e4d82c535d";
    private const string PythonUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip";
    private const string PipUrl = "https://raw.githubusercontent.com/pypa/get-pip/f6f644156f23dfe9acc06e7b9ca75eee311f2e37/public/get-pip.py";
    // Filled from pinned HTTPS input hashes by the build script.
    private const string PythonHash = "@PYTHON_SHA256@";
    private const string PipHash = "@PIP_SHA256@";
    private static string root;
    private static string logPath;
    private static Action<string> progress = delegate { };

    [STAThread]
    private static int Main(string[] args)
    {
        root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FrameLinkDesktop");
        bool installOnly = args.Length == 2 && args[0] == "--install-only";
        if (installOnly) root = Path.GetFullPath(args[1]);
        Directory.CreateDirectory(root);
        logPath = Path.Combine(root, "setup.log");
        if (installOnly)
        {
            try { Install().GetAwaiter().GetResult(); return 0; }
            catch (Exception e) { File.AppendAllText(logPath, e.ToString() + Environment.NewLine); return 1; }
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (Installed())
        {
            try { Launch(); return 0; }
            catch (Exception e) { MessageBox.Show(e.Message, "FrameLink", MessageBoxButtons.OK, MessageBoxIcon.Error); return 1; }
        }
        using (var form = new Form())
        {
            form.Text = "FrameLink v0.1.2 — Setup";
            form.ClientSize = new Size(620, 320);
            form.StartPosition = FormStartPosition.CenterScreen;
            form.FormBorderStyle = FormBorderStyle.FixedDialog;
            form.MaximizeBox = false;
            form.Font = new Font("Segoe UI", 10);
            var title = new Label { Text = "Install FrameLink", Left = 28, Top = 24, Width = 550, Height = 40, Font = new Font("Segoe UI", 20) };
            var description = new Label { Text = "Experimental Windows x64 build.\nFirst installation downloads Python and dependencies over HTTPS.\nRecording works locally after setup; internet is not required to record.\nPhysical iPhone Safari verification is still pending.", Left = 28, Top = 80, Width = 564, Height = 105 };
            var status = new Label { Text = "Installs in your user profile. No administrator access required.", Left = 28, Top = 204, Width = 564, Height = 50 };
            var install = new Button { Text = "Install and launch", Left = 400, Top = 263, Width = 190, Height = 34 };
            var license = new Button { Text = "License", Left = 28, Top = 263, Width = 100, Height = 34 };
            license.Click += delegate {
                using (var reader = new StreamReader(Assembly.GetExecutingAssembly().GetManifestResourceStream("FrameLink.LICENSE")))
                using (var dialog = new Form()) {
                    dialog.Text = "FrameLink — GPL-3.0-or-later"; dialog.Size = new Size(680, 500);
                    dialog.Controls.Add(new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill, Text = reader.ReadToEnd() });
                    dialog.ShowDialog(form);
                }
            };
            bool installing = false;
            form.FormClosing += delegate(object sender, FormClosingEventArgs e) {
                if (installing) { e.Cancel = true; status.Text = "Setup is still running. Please wait for completion or an error."; }
            };
            progress = text => form.BeginInvoke((Action)(() => status.Text = text));
            install.Click += async delegate {
                install.Enabled = false; installing = true;
                try {
                    await Install();
                    Launch(); installing = false; form.Close();
                } catch (Exception e) {
                    installing = false; install.Enabled = true;
                    status.Text = "Installation failed. Your recordings were not changed.";
                    File.AppendAllText(logPath, e.ToString() + Environment.NewLine);
                    MessageBox.Show("Setup failed: " + e.Message + "\n\nLog: " + logPath, "FrameLink setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            };
            form.Controls.AddRange(new Control[] { title, description, status, install, license });
            Application.Run(form);
        }
        return 0;
    }

    private static bool Installed()
    {
        string marker = Path.Combine(root, "installed-revision.txt");
        return File.Exists(marker) && File.ReadAllText(marker).Trim() == Revision && File.Exists(PythonExe());
    }
    private static string PythonExe() { return Path.Combine(root, "Runtime312", "python.exe"); }
    private static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"").TrimEnd('\\') + "\""; }
    private static async Task Download(string url, string destination, string expected)
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        using (var client = new WebClient()) await client.DownloadFileTaskAsync(new Uri(url), destination);
        using (var hash = SHA256.Create())
        using (var file = File.OpenRead(destination)) {
            string actual = BitConverter.ToString(hash.ComputeHash(file)).Replace("-", "").ToLowerInvariant();
            if (actual != expected) throw new InvalidDataException("Download hash mismatch: " + new Uri(url).Host);
        }
    }
    private static async Task Run(string executable, string arguments, int timeoutMilliseconds)
    {
        var info = new ProcessStartInfo(executable, arguments) {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
            WorkingDirectory = root
        };
        if (arguments.Contains("--smoke-test")) info.EnvironmentVariables["QT_QPA_PLATFORM"] = "offscreen";
        using (var process = new Process { StartInfo = info }) {
            process.Start();
            Task<string> output = process.StandardOutput.ReadToEndAsync();
            Task<string> error = process.StandardError.ReadToEndAsync();
            bool finished = await Task.Run(() => process.WaitForExit(timeoutMilliseconds));
            if (!finished) { process.Kill(); throw new TimeoutException("Setup step timed out; retry on a working internet connection."); }
            string text = await output + await error;
            File.AppendAllText(logPath, text + Environment.NewLine);
            if (process.ExitCode != 0) throw new InvalidOperationException("Setup step failed (" + process.ExitCode + "). See setup.log.");
        }
    }
    private static async Task Install()
    {
        if (Installed()) return;
        string temporary = Path.Combine(root, "setup-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(temporary);
        try {
            progress("Downloading and verifying Python 3.12.10…");
            string archive = Path.Combine(temporary, "python.zip");
            await Download(PythonUrl, archive, PythonHash);
            string runtime = Path.Combine(root, "Runtime312");
            if (!Directory.Exists(runtime)) Directory.CreateDirectory(runtime);
            // Extract into a fresh directory, then copy to support interrupted-install retries.
            string extracted = Path.Combine(temporary, "python");
            ZipFile.ExtractToDirectory(archive, extracted);
            foreach (string source in Directory.GetFiles(extracted)) File.Copy(source, Path.Combine(runtime, Path.GetFileName(source)), true);
            Directory.CreateDirectory(Path.Combine(runtime, "Lib", "site-packages"));
            File.WriteAllText(Path.Combine(runtime, "python312._pth"), "python312.zip\n.\nLib/site-packages\nimport site\n");
            progress("Downloading and verifying pip…");
            string bootstrap = Path.Combine(temporary, "get-pip.py");
            await Download(PipUrl, bootstrap, PipHash);
            await Run(PythonExe(), Quote(bootstrap) + " --isolated --index-url https://pypi.org/simple --disable-pip-version-check", 600000);
            progress("Installing build tools from PyPI…");
            await Run(PythonExe(), "-m pip --isolated --disable-pip-version-check install --index-url https://pypi.org/simple --only-binary=:all: setuptools wheel", 600000);
            progress("Installing FrameLink and Windows media dependencies…");
            string codeArchive = Path.Combine(temporary, "framelink.zip");
            using (var resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("FrameLink.SOURCE"))
            using (var output = File.Create(codeArchive)) resource.CopyTo(output);
            string code = Path.Combine(temporary, "source");
            ZipFile.ExtractToDirectory(codeArchive, code);
            await Run(PythonExe(), "-m pip --isolated --disable-pip-version-check install --index-url https://pypi.org/simple --no-build-isolation --only-binary=:all: " + Quote(Path.Combine(code, "FrameLink")), 1200000);
            progress("Checking the installed recording engine…");
            await Run(PythonExe(), "-m framelink.app --smoke-test --data-dir " + Quote(Path.Combine(temporary, "smoke-data")), 120000);
            string launcher = Path.Combine(root, "FrameLink.exe");
            string current = Assembly.GetExecutingAssembly().Location;
            if (!string.Equals(current, launcher, StringComparison.OrdinalIgnoreCase)) File.Copy(current, launcher, true);
            File.WriteAllText(Path.Combine(root, "installed-revision.txt"), Revision + "\n");
            progress("FrameLink is installed. Creating the desktop shortcut…");
            string shortcut = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "FrameLink.lnk");
            string script = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('" + shortcut.Replace("'", "''") + "');$s.TargetPath='" + launcher.Replace("'", "''") + "';$s.WorkingDirectory='" + root.Replace("'", "''") + "';$s.Save()";
            await Run("powershell.exe", "-NoProfile -NonInteractive -Command " + Quote(script), 30000);
        } finally { try { Directory.Delete(temporary, true); } catch (IOException) { } catch (UnauthorizedAccessException) { } }
    }
    private static void Launch()
    {
        string pythonw = Path.Combine(root, "Runtime312", "pythonw.exe");
        Process.Start(new ProcessStartInfo(pythonw, "-m framelink.app") { UseShellExecute = false, WorkingDirectory = root });
    }
}
