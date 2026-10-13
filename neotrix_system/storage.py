"""Managed save destinations and shared per-file discussion, without bpy."""
import os
import re
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

def component(value, label):
    value = value.strip()
    if not value or value in {'.', '..'} or value.startswith(('.', '#', '_')):
        raise ValueError(label + ' must be a nonempty production name')
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith(('.', ' ')):
        raise ValueError(label + ' contains characters Windows cannot use in a folder name')
    if re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value):
        raise ValueError(label + ' is a reserved Windows name')
    return value

def display_contained(root, path):
    """Lexical UI hint only: never resolve or access the filesystem.

    Actual save/render/comment operations must continue using contained().
    """
    root = os.path.normcase(os.path.abspath(root))
    path = os.path.normcase(os.path.abspath(path))
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def contained(root, path):
    root = os.path.normcase(os.path.realpath(root)).rstrip(os.sep)
    real = os.path.normcase(os.path.realpath(path))
    try:
        return os.path.commonpath([root + os.sep, real]).rstrip(os.sep) == root
    except ValueError:
        return False

def shot_folder(root, project, code, variant, shot):
    if not os.path.isdir(root):
        raise OSError('Relay root is unavailable')
    project = component(project, 'Project')
    variant = component(variant, 'Variant') if variant.strip() else 'Main'
    code = code.strip().upper()
    if not re.fullmatch(r'[A-Z]{3}', code):
        raise ValueError('Project code must be exactly 3 letters')
    if not re.fullmatch(r'\d{1,4}', shot.strip()):
        raise ValueError('Shot must use 4 digits, from 0010 to 9990')
    if int(shot)<10 or int(shot)>9990 or int(shot)%10:
        raise ValueError('Shots start at 0010 and advance by 10: 0020, 0030…')
    shot = str(int(shot)).zfill(4)
    if not re.fullmatch(r'[A-Za-z0-9]+',variant):
        raise ValueError('Optional variant must use letters and digits without spaces or punctuation')
    token = variant
    if not token:
        raise ValueError('Variant needs at least one letter or number for the filename')
    prefix = code + (('_' + token) if variant != 'Main' else '') + '_' + shot
    base = os.path.join(root, project)
    production = '03_PROD' if os.path.isdir(os.path.join(base, '03_PROD')) and not os.path.isdir(os.path.join(base, '03_PRODUCTION')) else '03_PRODUCTION'
    folder = os.path.join(base, production, 'WORK', '3D', 'SHOTS', prefix, 'PROJECTS')
    if not contained(root, folder):
        raise ValueError('Save destination is outside Relay')
    return folder, prefix

def highest_version(folder, prefix):
    maximum = 0
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.lower().startswith(prefix.lower() + '_') and name.lower().endswith('.blend'):
                match = re.search(r'(?i)_v(\d+)\.blend$', name)
                if match:
                    maximum = max(maximum, int(match.group(1)))
    return maximum

def destination(root, project, code, variant, shot, stage):
    folder, prefix = shot_folder(root, project, code, variant, shot)
    stage = component(stage, 'Stage').lower()
    stage = re.sub(r'[^a-z0-9]+', '', stage)
    version = highest_version(folder, prefix) + 1
    if version>999: raise ValueError('The guide allows save versions v001 through v999')
    return os.path.join(folder, f'{prefix}_v{version:03}.blend')

def current_identity(root, filepath):
    if not contained(root, filepath) or not filepath.lower().endswith('.blend') or not os.path.isfile(filepath):
        raise ValueError('Save this scene into Relay using Create / Save As first')
    stem = os.path.splitext(os.path.basename(filepath))[0]
    stem = re.sub(r'(?i)_v\d+$', '', stem)
    stem = re.sub(r'(?i)_(preproduction|modeling|lookdev|rigging|layout|animation|lighting|rendering|post|delivery)$', '', stem)
    if not re.fullmatch(r'[A-Z]{3}(?:_[A-Za-z0-9]+)?_\d{4}',stem):
        raise ValueError('Use Create / Save As to give this scene a guide-compliant name')
    shot=int(stem.rsplit('_',1)[-1])
    if shot<10 or shot>9990 or shot%10: raise ValueError('Shot numbers must start at 0010 and advance by 10')
    return os.path.dirname(filepath), stem

def current_destination(root, filepath, stage):
    folder, prefix = current_identity(root, filepath)
    token = re.sub(r'[^a-z0-9]+', '', component(stage, 'Stage').lower())
    version=highest_version(folder,prefix)+1
    if version>999: raise ValueError('The guide allows save versions v001 through v999')
    return os.path.join(folder, f'{prefix}_v{version:03}.blend')

@contextmanager
def reserve(root, project, code, variant, shot, stage):
    folder, prefix = shot_folder(root, project, code, variant, shot)
    with reserve_folder(folder, prefix, stage) as path:
        yield path

@contextmanager
def reserve_current(root, filepath, stage):
    folder, prefix = current_identity(root, filepath)
    with reserve_folder(folder, prefix, stage) as path:
        yield path

@contextmanager
def reserve_folder(folder, prefix, stage):
    stage = re.sub(r'[^a-z0-9]+', '', component(stage, 'Stage').lower())
    os.makedirs(folder, exist_ok=True)
    # Cooperating clients reserve versions exclusively. No existing .blend is overwritten.
    for attempt in range(100):
        version = highest_version(folder, prefix) + 1 + attempt
        if version>999: raise ValueError('The guide allows save versions v001 through v999')
        path = os.path.join(folder, f'{prefix}_v{version:03}.blend')
        lock = os.path.join(folder, f'.neotrix-save-v{version:03}.lock')
        try:
            handle = open(lock, 'x', encoding='utf8')
        except FileExistsError:
            continue
        with handle:
            json.dump({'id': str(uuid.uuid4()), 'created': datetime.now(timezone.utc).isoformat()}, handle)
        try:
            if os.path.exists(path):
                continue
            yield path
            return
        finally:
            os.unlink(lock)
    raise OSError('Could not reserve a new version; inspect save permissions or pending saves')

# Shared schema 1 works on all saved files, independent of the browser root.
from . import comments as shared_comments

def discussion_folder(root, filepath):
    return str(shared_comments.discussion_folder(filepath))

def add_comment(root, filepath, author, message):
    return shared_comments.add_comment(filepath, author, message, application='Blender')

def load_comments(root, filepath):
    return shared_comments.load_comments(filepath)
