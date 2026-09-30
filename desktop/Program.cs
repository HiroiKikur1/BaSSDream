using System;
using System.Threading;
using System.Windows.Forms;

namespace BassStation;

static class Program
{
    private const string MutexName = @"Global\BassStation2_SingleInstanceMutex";

    [STAThread]
    static void Main()
    {
        using var mutex = new Mutex(true, MutexName, out bool createdNew);
        if (!createdNew)
        {
            // Another instance is already running
            MessageBox.Show("BassStation 已经在运行中！", "BassStation 2.0", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        ApplicationConfiguration.Initialize();
        Application.Run(new FormMain());
    }
}
