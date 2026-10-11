using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class FarFromHomeLauncher
{
    [STAThread]
    private static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string scriptPath = Path.Combine(root, "FarFromHome-Server.ps1");
        string executableName = Path.GetFileNameWithoutExtension(
            Process.GetCurrentProcess().MainModule.FileName);
        string action = executableName.StartsWith("Stop-", StringComparison.OrdinalIgnoreCase)
            ? "Stop"
            : "Start";

        if (!File.Exists(scriptPath))
        {
            MessageBox.Show(
                "FarFromHome-Server.ps1 was not found next to this launcher.",
                "FarFromHome server",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        string powershellPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.System),
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe");
        string escapedScriptPath = scriptPath.Replace("\"", "\\\"");
        var startInfo = new ProcessStartInfo
        {
            FileName = powershellPath,
            Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" +
                escapedScriptPath + "\" -Action " + action,
            CreateNoWindow = true,
            UseShellExecute = false,
            WindowStyle = ProcessWindowStyle.Hidden
        };

        try
        {
            Process.Start(startInfo);
        }
        catch (Exception exception)
        {
            MessageBox.Show(
                "Could not launch the FarFromHome server helper:\n" + exception.Message,
                "FarFromHome server",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }
}
