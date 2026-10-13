"""Filesystem-only Relay index. Safe to execute on a worker thread."""
import os
import re

SKIP = {'node_modules', '__pycache__', '$recycle.bin', 'system volume information'}
STAGES = [
    ('Preproduction', ('preproduction', 'preprod', 'storyboard', 'animatic')),
    ('Modeling', ('modeling', 'modelling', 'model')),
    ('Lookdev', ('lookdev', 'shading', 'texture', 'texturing')),
    ('Rigging', ('rigging', 'rig')),
    ('Layout', ('layout', 'blocking')),
    ('Animation', ('animation', 'anim')),
    ('Lighting', ('lighting', 'light')),
    ('Rendering', ('rendering', 'renders', 'render')),
    ('Post', ('post', 'compositing', 'comp', 'finishing')),
    ('Delivery', ('delivery', 'final', 'approved')),
]

def natural(value):
    return tuple((1, int(p)) if p.isdigit() else (0, p.lower()) for p in re.split(r'(\d+)', value))

def parse(path, root, metadata=True):
    relative = os.path.relpath(path, root)
    parts = relative.replace('\\', '/').split('/')
    stem = os.path.splitext(parts[-1])[0]
    stage = 'Unspecified'
    # The deepest named stage wins; a generic production directory is not a shot status.
    for component in parts:
        words = re.split(r'[^a-z0-9]+', component.lower())
        for label, aliases in STAGES:
            if any(a in words for a in aliases):
                stage = label
    shot = 'Unspecified'
    for component in reversed(parts):
        m = re.search(r'(?i)(?:^|[^a-z0-9])(?:shot|sh|sc)[ _-]*(\d+)(?:[^0-9]|$)', component)
        if m:
            shot = 'Shot ' + m.group(1).zfill(3)
            break
    # Common production names such as SRL_0001_v001.
    if shot == 'Unspecified':
        m = re.search(r'(?i)^[a-z][a-z0-9]*(?:[ _-]+[a-z][a-z0-9]*)*[ _-]+(\d{3,5})(?:[ _-]|$)', stem)
        if m:
            shot = 'Shot ' + m.group(1).zfill(3)
    version = 'Unversioned'
    for component in reversed(parts):
        m = re.search(r'(?i)(?:^|[^a-z0-9])v(?:er(?:sion)?)?[ _-]*(\d+)(?:[^0-9]|$)', component)
        if m:
            version = 'v' + m.group(1).zfill(3)
            break
    variant = 'Main'
    for i, component in enumerate(parts[:-1]):
        if component.lower() == 'shots' and i + 1 < len(parts) - 1:
            candidate = parts[i + 1]
            # HVN_Vertical_0010 or a film grouping such as 'how to rejoin'.
            m = re.match(r'(?i)^[a-z0-9]+_(.+)_\d+$', candidate)
            if m:
                variant = m.group(1)
            elif not re.search(r'(?i)(?:shot|sh|sc)[ _-]*\d|^[a-z0-9]+_\d+$', candidate):
                variant = candidate
            break
    stat = os.stat(path) if metadata else None
    return dict(path=os.path.abspath(path), relative=relative, name=parts[-1],
                project=parts[0] if len(parts) > 1 else 'Root files', variant=variant, shot=shot,
                stage=stage, version=version, size=stat.st_size if stat else 0, modified=stat.st_mtime if stat else 0)

def scan(root):
    result = dict(root=root, projects=[], files=[], errors=[])
    if not os.path.isdir(root):
        raise OSError('Relay root is unavailable: ' + root)
    def error(e):
        result['errors'].append(str(e))
    visited = set()
    for directory, dirs, files in os.walk(root, onerror=error, followlinks=False):
        key = os.path.normcase(os.path.realpath(directory))
        if key in visited:
            dirs[:] = []
            continue
        visited.add(key)
        dirs[:] = sorted((d for d in dirs if not d.startswith(('#', '.')) and d.lower() not in SKIP and d.lower() != '_shot_template' and not d.lower().endswith('.blend.neotrix')), key=natural)
        if os.path.normcase(directory) == os.path.normcase(root):
            result['projects'] = list(dirs)
        for name in files:
            if name.lower().endswith('.blend'):
                try:
                    result['files'].append(parse(os.path.join(directory, name), root))
                except OSError as e:
                    error(e)
    if any(f['project'] == 'Root files' for f in result['files']):
        result['projects'].append('Root files')
    result['files'].sort(key=lambda f: (natural(f['project']), natural(f['shot']), natural(f['version']), f['modified']), reverse=True)
    return result
