"""Create Blender-installable ZIP and SHA-256 manifest from the source tree."""
import ast
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'neotrix_system'
tree = ast.parse((SOURCE / '__init__.py').read_text(encoding='utf-8'))
info = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'bl_info' for t in n.targets)))
version = '.'.join(map(str, info['version']))
output = ROOT / 'dist'
output.mkdir(exist_ok=True)
target = output / f'Neotrix_System_v{version}.zip'
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    for file in sorted(SOURCE.rglob('*')):
        if file.is_file() and '__pycache__' not in file.parts and file.suffix not in ('.pyc', '.pyo'):
            if file.suffix == '.py':
                ast.parse(file.read_text(encoding='utf-8'))
            archive.write(file, file.relative_to(ROOT).as_posix())
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
    assert 'neotrix_system/__init__.py' in archive.namelist()
    assert all(name.startswith('neotrix_system/') for name in archive.namelist())
checksum = hashlib.sha256(target.read_bytes()).hexdigest()
(output / 'SHA256SUMS.txt').write_text(f'{checksum}  {target.name}\n', encoding='ascii')
print(target)
print('SHA256', checksum)
