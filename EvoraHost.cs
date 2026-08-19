using System;
using System.Diagnostics;
using System.Windows.Forms;

internal static class EvoraHost
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length != 2 || !string.Equals(args[0], "--script", StringComparison.OrdinalIgnoreCase))
        {
            MessageBox.Show("Evora could not start because its launch instructions were invalid.", "Evora", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }

        string script = args[1];
        var startInfo = new ProcessStartInfo
        {
            FileName = "powershell.exe",
            Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File \"" + script.Replace("\"", "\\\"") + "\"",
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        try
        {
            using (Process process = Process.Start(startInfo))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show("Evora could not start.\r\n\r\n" + error.Message, "Evora", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
