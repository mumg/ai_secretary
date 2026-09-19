# WScript.Shell loads .lnk paths through the system ANSI code page. Use the
# Unicode Shell interface so Cyrillic shortcut names work on English runners.
if (-not ('AISecretary.ShortcutReader' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

namespace AISecretary {
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int length, IntPtr findData, uint flags);
        void GetIDList(out IntPtr item);
        void SetIDList(IntPtr item);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int length);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string text);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int length);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string path);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder args, int length);
    }
    public sealed class ShortcutInfo {
        public string FullName { get; set; }
        public string TargetPath { get; set; }
        public string Arguments { get; set; }
    }
    public static class ShortcutReader {
        public static ShortcutInfo Read(string file) {
            object instance = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046")));
            try {
                ((IPersistFile)instance).Load(file, 0);
                var link = (IShellLinkW)instance;
                var target = new StringBuilder(32768);
                var args = new StringBuilder(32768);
                link.GetPath(target, target.Capacity, IntPtr.Zero, 0);
                link.GetArguments(args, args.Capacity);
                return new ShortcutInfo { FullName = file, TargetPath = target.ToString(), Arguments = args.ToString() };
            } finally { Marshal.FinalReleaseComObject(instance); }
        }
    }
}
'@
}

function Get-ShortcutInfo([string]$LiteralPath) {
    [AISecretary.ShortcutReader]::Read((Get-Item -LiteralPath $LiteralPath -ErrorAction Stop).FullName)
}
