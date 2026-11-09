"""Native resource and explicitly labelled native-to-PowerShell argv seam."""
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT/'build/windows_launcher/bin/LumaFEM.exe'
ICO = ROOT/'python/lumafem/gui/assets/lumafem.ico'

class WindowsLauncherTests(unittest.TestCase):
    def test_generated_powershell_to_python_argv_receiver_seam(self):
        # Execute the actual generated serializer against independent CPython argv.
        source = (ROOT/'scripts/package.ps1').read_text(encoding='utf-8-sig')
        function = source[source.index('function ConvertTo-CrtArgument'):source.index("$start = [Diagnostics.ProcessStartInfo]::new()")]
        with tempfile.TemporaryDirectory(prefix='LumaFEM PS argv ') as folder:
            root = Path(folder)
            expected = ['', 'literal"quote', 'end\\', 'x & y', "apostrophe's", '\\"', 'space \\']
            arguments = ['-c','import json,sys; open(sys.argv[1],"w").write(json.dumps(sys.argv[2:]))',str(root/'actual.json'),*expected]
            (root/'arguments.json').write_text(json.dumps(arguments))
            python = sys.executable.replace("'", "''")
            script = "$ErrorActionPreference='Stop'\n" + function + f"\n$arguments=Get-Content -Raw -LiteralPath '{root.as_posix()}/arguments.json' | ConvertFrom-Json\n$start=[Diagnostics.ProcessStartInfo]::new()\n$start.FileName='{python}'\n$start.UseShellExecute=$false\n$start.CreateNoWindow=$true\n$start.Arguments=(($arguments | ForEach-Object {{ ConvertTo-CrtArgument $_ }}) -join ' ')\n$p=[Diagnostics.Process]::Start($start)\n$p.WaitForExit()\nexit $p.ExitCode\n"
            (root/'probe.ps1').write_text(script,encoding='utf-8-sig')
            powershell = Path(__import__('os').environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
            result = subprocess.run([str(powershell),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'probe.ps1')],capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            self.assertEqual(json.loads((root/'actual.json').read_text()),expected)

    def test_actual_x64_gui_PE_and_embedded_approved_resource_bytes(self):
        data = EXE.read_bytes()
        offset = struct.unpack_from('<I', data, 0x3c)[0]
        self.assertEqual(data[offset:offset+4], b'PE\0\0')
        self.assertEqual(struct.unpack_from('<H', data, offset+4)[0], 0x8664)
        self.assertEqual(struct.unpack_from('<H', data, offset+24+68)[0], 2)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LoadLibraryExW.argtypes = [wintypes.LPCWSTR,wintypes.HANDLE,wintypes.DWORD]
        kernel.LoadLibraryExW.restype = wintypes.HMODULE
        kernel.FindResourceW.argtypes = [wintypes.HMODULE,ctypes.c_void_p,ctypes.c_void_p]
        kernel.FindResourceW.restype = wintypes.HANDLE
        kernel.SizeofResource.argtypes = [wintypes.HMODULE,wintypes.HANDLE]
        kernel.LoadResource.argtypes = [wintypes.HMODULE,wintypes.HANDLE]
        kernel.LoadResource.restype = wintypes.HANDLE
        kernel.LockResource.argtypes = [wintypes.HANDLE]
        kernel.LockResource.restype = ctypes.c_void_p
        kernel.FreeLibrary.argtypes = [wintypes.HMODULE]
        module = kernel.LoadLibraryExW(str(EXE), None, 2)
        self.assertTrue(module)
        def resource(name, kind):
            found = kernel.FindResourceW(module, name, kind)
            self.assertTrue(found)
            length = kernel.SizeofResource(module, found)
            return ctypes.string_at(kernel.LockResource(kernel.LoadResource(module, found)), length)
        try:
            group = resource(1,14)
            count = struct.unpack_from('<H',group,4)[0]
            approved = ICO.read_bytes()
            self.assertEqual(count,9)
            for i in range(count):
                entry = group[6+14*i:20+14*i]
                width,height,_,_,planes,bits,length,identifier = struct.unpack('<BBBBHHIH',entry)
                native = resource(identifier,3)
                aw,ah,_,_,ap,ab,al,ao = struct.unpack_from('<BBBBHHII',approved,6+16*i)
                # RC normalizes an ICO's unspecified planes (0) to DIB planes (1).
                self.assertEqual((width,height,planes,bits,length),(aw,ah,ap or 1,ab,al))
                self.assertEqual(native, approved[ao:ao+al])
                header = struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',width,height,0,0,planes,bits,length,22)
                frame = Image.open(io.BytesIO(header+native)).convert('RGBA')
                self.assertEqual(frame.size,(width or 256,height or 256))
                self.assertEqual(frame.getchannel('A').getextrema(),(0,255))
        finally:
            kernel.FreeLibrary(module)

    def test_native_to_powershell_argv_receiver_seam(self):
        # Dedicated synthetic bootstrap receiver; not runtime acceptance.
        with tempfile.TemporaryDirectory(prefix='LumaFEM argv ') as folder:
            root = Path(folder)
            shutil.copy2(EXE,root/'LumaFEM.exe')
            (root/'runtime').mkdir()
            for name in ('python.exe','python310.dll'):
                (root/'runtime'/name).write_bytes(b'labelled seam only')
            (root/'Launch-LumaFEM.ps1').write_text('ConvertTo-Json -InputObject @($args) | Set-Content -LiteralPath "$PSScriptRoot/argv.json"',encoding='utf-8-sig')
            expected = ['plain','','literal"quote','trailing\\', 'a & b', "apostrophe's", 'two spaces \\', '\\"']
            result = subprocess.run([str(root/'LumaFEM.exe'),*expected],cwd=ROOT,timeout=30)
            self.assertEqual(result.returncode,0)
            self.assertEqual(json.loads((root/'argv.json').read_text(encoding='utf-8-sig')),expected)

if __name__ == '__main__':
    unittest.main(verbosity=2)
