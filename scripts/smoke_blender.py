"""Run in a fresh factory Blender; does not save preferences or production scenes."""
import importlib
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace, MethodType
import bpy
import addon_utils

ROOT = Path(os.environ.get('NEOTRIX_ADDON_SOURCE', Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT))
addon_utils.enable('neotrix_system', default_set=True)
addon = importlib.import_module('neotrix_system')
assert Path(addon.__file__).resolve() == ROOT / 'neotrix_system/__init__.py'
assert hasattr(bpy.types.Scene, 'neotrix_render_settings')
assert bpy.ops.relay.render_preview.poll()
assert addon.bl_info['version'] == (1, 8, 4)
results = []
try:
    with tempfile.TemporaryDirectory(prefix='neotrix-release-qa-') as directory:
        saved = Path(directory) / 'ordinary.blend'
        saved.touch()
        # Root-independent paths and filenames, without writing preview outputs.
        assert addon.rendering.preview_folder('PLAY', str(saved)) == str(saved.parent / 'RENDERS/ordinary/preview')
        shot = Path(directory) / 'SHOTS/Main/ABC_Main_0010/PROJECTS/LAYOUT/ABC_Main_0010_layout_v001.blend'
        assert addon.rendering.preview_folder('STILL', str(shot)) == str(Path(directory) / 'SHOTS/Main/ABC_Main_0010/RENDERS/v001/frames')
        results.append('ordinary and shot preview destinations')
        for extension in ('.blend', '.comp', '.aep', '.psd', '.ai'):
            path = Path(directory) / ('review' + extension)
            path.touch()
            addon.storage.add_comment('unrelated-root', str(path), 'Artist', 'Review\nUnicode ✓')
            records, errors = addon.storage.load_comments('another-root', str(path))
            assert records[0]['text'] == 'Review\nUnicode ✓' and not errors
            assert path.read_bytes() == b''
        results.append('root-independent shared comments across creative file types')
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: addon.comments.add_comment(str(saved), str(i), 'Concurrent append', 'Fusion'), range(24)))
        records, errors = addon.comments.load_comments(str(saved))
        assert len(records) == 24 and not errors
        folder = addon.comments.discussion_folder(str(saved))
        (folder / 'bad.json').write_text('null')
        records, errors = addon.comments.load_comments(str(saved))
        assert len(records) == 24 and len(errors) == 1
        results.append('concurrent append and corrupt record isolation')
        # Exercise the exact browser helpers on a temporary file, with no UI modal.
        browser = SimpleNamespace()
        for name in ('open_comments', 'edit_comment', 'post_comment'):
            setattr(browser, name, MethodType(getattr(addon.RELAY_OT_browser, name), browser))
        author = addon.prefs().author
        browser.open_comments(bpy.context, str(saved), write=True)
        def key(kind, char='', ctrl=False):
            return SimpleNamespace(type=kind, unicode=char, ctrl=ctrl)
        for char in 'Inline':
            browser.edit_comment(bpy.context, key('A', char))
        browser.edit_comment(bpy.context, key('RET'))
        for char in 'Review':
            browser.edit_comment(bpy.context, key('A', char))
        assert browser.comment_message == 'Inline\nReview'
        browser.edit_comment(bpy.context, key('RET', ctrl=True))
        assert browser.comment_message == ''
        records, errors = addon.comments.load_comments(str(saved))
        assert any(c['text'] == 'Inline\nReview' for c in records)
        browser.comment_message = 'Draft'
        browser.comment_drafts[str(saved)] = 'Draft'
        browser.open_comments(bpy.context, str(saved))
        assert browser.comment_message == 'Draft'
        addon.prefs().author = author
        results.append('inline multiline editing, posting and draft retention')
finally:
    addon_utils.disable('neotrix_system', default_set=True)
assert not hasattr(bpy.types.Scene, 'neotrix_render_settings')
results.append('fresh addon registration and clean unregistration')
print('NEOTRIX_RELEASE_QA_PASS ' + json.dumps(results))
