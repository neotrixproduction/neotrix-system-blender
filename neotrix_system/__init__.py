bl_info = {'name': 'Neotrix System', 'author': 'Neotrix', 'version': (1, 8, 4),
           'blender': (5, 2, 0), 'location': 'View3D > Sidebar > Neotrix',
           'description': 'Colorful project, variant, shot, stage and version picker for Relay', 'category': 'Interface'}

import os
import json
import queue
import threading
import time
import math
import getpass
import textwrap
from datetime import datetime
import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
from bpy.props import StringProperty, EnumProperty, PointerProperty, CollectionProperty, IntProperty, BoolProperty
import bpy.utils.previews
from .indexer import scan, parse, natural, STAGES
from . import storage, workflow, backdrop, rendering, split_render

DATA = {'projects': [], 'files': [], 'errors': []}
BUSY = False
SCAN_ID = 0
LOGO = None
LOGO_SHADER = None
STATUS = 'Ready to scan Relay'
RESULTS = queue.Queue()
ACTIVE = None
KEYMAPS = []
TAGS = {}
ICONS = None
ENUM_CACHE = {}
ENUM_CURRENT = {}
ENUM_NUMBERS = {}
UPDATING = False
PENDING_SELECTION = ''
BRAND = (.20, .20, 1.0, 1)
COLORS = [(.65,.48,1,1),(.99,.65,.38,1),(.28,.79,.94,1),(1,.48,.60,1),(.35,.87,.68,1)]
UI_ALPHA = 1.0
TEXT = (.92, .94, 1, 1)
MUTED = (.52, .58, .69, 1)
PANEL = (.016, .019, .035, .98)
CARD = (.032, .039, .072, .94)

def config_file():
    return os.path.join(bpy.utils.user_resource('CONFIG', create=True), 'relay_browser_tags.json')

def prefs(context=None):
    return (context or bpy.context).preferences.addons[__package__].preferences

def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

def start_scan():
    global BUSY, STATUS, SCAN_ID
    if BUSY:
        return
    root = bpy.path.abspath(prefs().root)
    BUSY = True
    SCAN_ID += 1
    ticket = SCAN_ID
    STATUS = 'Indexing project files…'
    def worker():
        try:
            RESULTS.put((ticket, True, scan(root)))
        except Exception as exc:
            RESULTS.put((ticket, False, str(exc)))
    threading.Thread(target=worker, daemon=True, name='Relay filesystem index').start()
    if not bpy.app.timers.is_registered(consume):
        bpy.app.timers.register(consume, first_interval=.2)

def consume():
    global DATA, BUSY, STATUS, PENDING_SELECTION
    try:
        ticket, ok, value = RESULTS.get_nowait()
    except queue.Empty:
        return .2 if BUSY else None
    if ticket != SCAN_ID:
        return .05 if BUSY else None
    BUSY = False
    if ok:
        DATA = value
        STATUS = '{} projects · {} Blender files{}'.format(len(DATA['projects']), len(DATA['files']), ' · partial scan: see sidebar' if DATA['errors'] else '')
    else:
        STATUS = value
        DATA = {'projects': [], 'files': [], 'errors': [value]}
    if ACTIVE:
        ACTIVE.selected = None
        ACTIVE.content_at=time.monotonic()
    update_sidebar(bpy.context)
    if PENDING_SELECTION and ok:
        state=bpy.context.window_manager.neotrix_picker
        match=next((i for i,f in enumerate(state.files) if f.path==PENDING_SELECTION),None)
        if match is not None:
            state.active_index=match
            workflow.load_discussion(bpy.context)
            PENDING_SELECTION=''
        else:
            start_scan()
            return .2
    redraw()
    return None

def file_stage(file):
    return TAGS.get(os.path.normcase(file['path']), file['stage'])

def root_changed(self,context):
    global BUSY, DATA, PENDING_SELECTION, UPDATING
    BUSY=False
    DATA={'projects':[],'files':[],'errors':[]}
    PENDING_SELECTION=''
    current_scene_info.cache=None
    if context and hasattr(context.window_manager,'neotrix_picker'):
        state=context.window_manager.neotrix_picker
        UPDATING=True
        try:
            for field in FIELDS: setattr(state,field,'__ALL__')
            state.search=''
        finally: UPDATING=False
        update_sidebar(context)
    if ACTIVE:
        ACTIVE.filters=['All']*5
        ACTIVE.offsets=[0]*5
        ACTIVE.query=''
        ACTIVE.selected=None
        ACTIVE.file_offset=0
    start_scan()
    redraw()

class NEOTRIX_OT_root(bpy.types.Operator):
    bl_idname='relay.change_root'
    bl_label='Change project files root'
    root: StringProperty(name='Relay root',subtype='DIR_PATH')
    def invoke(self,context,event):
        self.root=prefs(context).root
        self.origin_area=context.area
        self.origin_window=context.window
        return context.window_manager.invoke_props_dialog(self,width=520)
    def draw(self,context):
        self.layout.prop(self,'root')
        self.layout.label(text='The N-panel and browser share this location.',icon='LINKED')
    def reopen(self):
        area=self.origin_area;window=self.origin_window
        def launch():
            if area in list(window.screen.areas) and not ACTIVE:
                with bpy.context.temp_override(window=window,area=area,region=next(r for r in area.regions if r.type=='WINDOW')):
                    bpy.ops.relay.browser('INVOKE_DEFAULT')
            return None
        bpy.app.timers.register(launch,first_interval=.15)
    def execute(self,context):
        prefs(context).root=self.root
        bpy.ops.wm.save_userpref()
        if hasattr(self,'origin_area'): self.reopen()
        return {'FINISHED'}
    def cancel(self,context):
        self.reopen()

class RELAY_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    root: StringProperty(name='Relay root', subtype='DIR_PATH', default='R:\\',update=root_changed)
    author: StringProperty(name='Comment author',default=getpass.getuser())
    def draw(self, context):
        self.layout.prop(self, 'root')
        self.layout.prop(self,'author')
        self.layout.label(text='Comments are shared beside each file. Saves create new versions.')

class RELAY_OT_refresh(bpy.types.Operator):
    bl_idname = 'relay.refresh'
    bl_label = 'Refresh Relay'
    def execute(self, context):
        start_scan()
        return {'FINISHED'}

class RELAY_OT_open(bpy.types.Operator):
    bl_idname = 'relay.open_file'
    bl_label = 'Open Blender file'
    filepath: StringProperty(subtype='FILE_PATH')
    def invoke(self, context, event):
        if bpy.data.is_dirty or (not bpy.data.filepath and len(bpy.data.objects)):
            return context.window_manager.invoke_confirm(self, event, title='Open Relay file?',
                message='The current scene will close. Save it first if you need to keep it.', confirm_text='Open file', icon='WARNING')
        return self.execute(context)
    def execute(self, context):
        root = os.path.normcase(os.path.realpath(bpy.path.abspath(prefs().root)))
        path = os.path.normcase(os.path.realpath(self.filepath))
        try:
            # Windows resolves mapped drives to a UNC share; a bare UNC share
            # needs a trailing separator for commonpath to recognize its root.
            common = os.path.commonpath([root + os.sep, path]).rstrip(os.sep)
            if common != root.rstrip(os.sep) or not path.endswith('.blend') or not os.path.isfile(path):
                raise ValueError('Select an existing .blend file inside the configured Relay root')
            bpy.ops.wm.open_mainfile(filepath=self.filepath, load_ui=False, use_scripts=False)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}

class RELAY_OT_stage(bpy.types.Operator):
    bl_idname = 'relay.set_stage'
    bl_label = 'Set production stage'
    filepath: StringProperty()
    stage: EnumProperty(name='Stage', items=[(s[0], s[0], 'Local production stage tag') for s in STAGES] + [('Unspecified', 'Use folder inference', 'Remove the local override')])
    def invoke(self, context, event):
        self.stage = TAGS.get(os.path.normcase(self.filepath), 'Unspecified')
        return context.window_manager.invoke_props_dialog(self, width=350)
    def draw(self, context):
        self.layout.label(text=os.path.basename(self.filepath), icon='FILE_BLEND')
        self.layout.prop(self, 'stage')
        self.layout.label(text='Saved on this computer; the .blend stays untouched.')
    def execute(self, context):
        key = os.path.normcase(self.filepath)
        previous = TAGS.copy()
        if self.stage == 'Unspecified':
            TAGS.pop(key, None)
        else:
            TAGS[key] = self.stage
        try:
            target = config_file()
            with open(target + '.tmp', 'w', encoding='utf8') as handle:
                json.dump(TAGS, handle, indent=2)
            os.replace(target + '.tmp', target)
        except OSError as exc:
            TAGS.clear()
            TAGS.update(previous)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        redraw()
        update_sidebar(context)
        return {'FINISHED'}

def rect(x, y, w, h, color, radius=0):
    import math
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    if radius:
        radius = min(radius, w / 2, h / 2)
        vertices = []
        for cx, cy, begin in [(x+w-radius, y+h-radius, 0), (x+radius, y+h-radius, 90), (x+radius, y+radius, 180), (x+w-radius, y+radius, 270)]:
            for i in range(7):
                a = math.radians(begin + i*15)
                vertices.append((cx+radius*math.cos(a), cy+radius*math.sin(a)))
    else:
        vertices = [(x,y),(x+w,y),(x+w,y+h),(x,y+h)]
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRI_FAN', {'pos': vertices})
    shader.bind()
    shader.uniform_float('color', (*color[:3],color[3]*UI_ALPHA))
    batch.draw(shader)

def gradient(x,y,w,h,left,right):
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    shader=gpu.shader.from_builtin('SMOOTH_COLOR')
    batch=batch_for_shader(shader,'TRI_FAN',{'pos':[(x,y),(x+w,y),(x+w,y+h),(x,y+h)],'color':[( *left[:3],left[3]*UI_ALPHA),( *right[:3],right[3]*UI_ALPHA),( *right[:3],right[3]*UI_ALPHA),( *left[:3],left[3]*UI_ALPHA)]})
    shader.bind()
    batch.draw(shader)

def glass(x,y,w,h,color=CARD,radius=10):
    rect(x,y,w,h,(.40,.52,.75,.10),radius)
    rect(x+1,y+1,w-2,h-2,color,max(0,radius-1))
    gradient(x+radius,y+h-1,w-radius*2,1,(.8,.72,1,.20),(.3,.8,1,.03))

def text(x, y, value, size=13, color=TEXT, width=None):
    value = str(value)
    blf.size(0, size)
    if width is not None:
        original = value
        while value and blf.dimensions(0, value)[0] > width:
            value = value[:-1]
        if value != original:
            value = value[:-2] + '…'
    blf.color(0, *color[:3],color[3]*UI_ALPHA)
    blf.position(0, x, y, 0)
    blf.draw(0, value)

def draw_logo(x,y,w,h):
    global LOGO, LOGO_SHADER
    try:
        valid=LOGO is not None and LOGO.size[0]>0
    except ReferenceError:
        valid=False
    if not valid:
        LOGO=bpy.data.images.load(os.path.join(os.path.dirname(__file__),'assets','neotrix_wordmark.jpg'),check_existing=False)
        LOGO.name='Neotrix UI wordmark'
    if LOGO_SHADER is None:
        interface=gpu.types.GPUStageInterfaceInfo('neotrix_logo_interface')
        interface.smooth('VEC2','uv')
        info=gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0,'VEC2','pos')
        info.vertex_in(1,'VEC2','texCoord')
        info.vertex_out(interface)
        info.fragment_out(0,'VEC4','fragColor')
        info.sampler(0,'FLOAT_2D','image')
        info.push_constant('MAT4','ModelViewProjectionMatrix')
        info.push_constant('FLOAT','opacity')
        info.vertex_source('void main(){uv=texCoord;gl_Position=ModelViewProjectionMatrix*vec4(pos,0.0,1.0);}')
        info.fragment_source('void main(){vec3 c=texture(image,uv).rgb;float mask=smoothstep(0.15,0.95,min(c.r,c.g));fragColor=vec4(1.0,1.0,1.0,mask*opacity);}')
        LOGO_SHADER=gpu.shader.create_from_info(info)
    shader=LOGO_SHADER
    batch=batch_for_shader(shader,'TRI_FAN',{'pos':[(x,y),(x+w,y),(x+w,y+h),(x,y+h)],'texCoord':[(.12,.40),(.88,.40),(.88,.60),(.12,.60)]})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_sampler('image',gpu.texture.from_image(LOGO))
    shader.uniform_float('opacity',UI_ALPHA)
    shader.uniform_float('ModelViewProjectionMatrix',gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
    batch.draw(shader)

class RELAY_OT_browser(bpy.types.Operator):
    bl_idname = 'relay.browser'
    bl_label = 'Launch Neotrix System'
    bl_description = 'Browse projects, variants, shots, stages and saved versions'
    fields = ['project', 'variant', 'shot', 'stage', 'version']
    labels = ['PROJECT', 'VARIANT / FILM', 'SHOT', 'STAGE', 'SAVE VERSION']

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def records(self, until=5):
        return [f for f in DATA['files'] if all(self.filters[i] == 'All' or
                (file_stage(f) if key == 'stage' else f[key]) == self.filters[i]
                for i, key in enumerate(self.fields[:until])) and
                (not self.query or self.query.lower() in f['relative'].lower())]

    def choose(self, column, value):
        self.filters[column] = value
        for i in range(column+1, 5):
            self.filters[i] = 'All'
            self.offsets[i] = 0
        self.file_offset = 0
        self.selected = None
        self.content_at=time.monotonic()

    def tween(self,key,target,speed=14):
        now=time.monotonic()
        old=self.hover_levels.get(key,(0.0,now))
        value=old[0]+(target-old[0])*min(1,(now-old[1])*speed)
        self.hover_levels[key]=(value,now)
        return value

    def button(self, x, y, w, h, label, action, color=CARD, ink=TEXT):
        target=1.0 if x<=self.hover[0]<=x+w and y<=self.hover[1]<=y+h else 0.0
        amount=self.tween('hover:'+repr(action),target)
        primary=action[0] in {'open','saveas'}
        action_colors={'open':COLORS[0],'saveas':COLORS[4],'savenext':COLORS[4],'comments':COLORS[1],'write_comment':COLORS[1],'stage':COLORS[3],'root':COLORS[2]}
        accent=action_colors.get(action[0],COLORS[0])
        base=tuple(c*.34 for c in accent[:3])+(1,) if primary else color
        color=tuple(min(1,c+(.07 if primary else .035)*amount) for c in base[:3])+(base[3],)
        if primary: ink=(1,1,1,1)
        glass(x,y,w,h,color,10)
        if amount>.01:
            highlight=COLORS[action[1]] if action[0]=='filter' else accent
            rect(x+9,y+1,(w-18)*amount,1,(*highlight[:3],.65*amount))
        text(x+12,y+(h-13)/2+1,label,13,ink,w-24)
        self.hits.append((x,y,w,h,action))

    def draw_overlay(self):
        context = bpy.context
        if context.area != self.area or context.region.type != 'WINDOW':
            return
        old_blend=gpu.state.blend_get()
        old_depth=gpu.state.depth_test_get()
        old_mask=gpu.state.depth_mask_get()
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        try:
            blurred=self.backdrop.draw(context.region.width,context.region.height)
            self.ui_scale=min(max(1.0,context.preferences.system.ui_scale),self.region.width/840,self.region.height/560)
            with gpu.matrix.push_pop():
                gpu.matrix.scale((self.ui_scale,self.ui_scale,1))
                age=max(0,time.monotonic()-self.opened_at)
                ease=1-(1-min(1,age/.42))**3
                self.motion_shift=(1-ease)*-18
                gpu.matrix.translate((0,self.motion_shift,0))
                self.paint(context.region.width/self.ui_scale,context.region.height/self.ui_scale,blurred)
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            gpu.state.blend_set(old_blend)
            gpu.state.depth_test_set(old_depth)
            gpu.state.depth_mask_set(old_mask)

    def paint(self, rw, rh, blurred=False):
        global UI_ALPHA
        UI_ALPHA=min(1,max(0,(time.monotonic()-self.opened_at)/.28))
        base_alpha=UI_ALPHA
        self.hits = []
        self.columns = []
        w = min(1120, rw-32)
        h = min(690, rh-28)
        x = (rw-w)/2
        y = (rh-h)/2
        top = y+h
        self.bounds = (x,y,w,h)
        rect(0,0,rw,rh,(.012,.018,.03,.28))
        rect(x-3,y-5,w+6,h+10,(.015,.02,.04,.6),20)
        glass(x,y,w,h,(*PANEL[:3],.86 if blurred else 1),22)
        draw_logo(x+26,top-65,160,42)
        text(x+204,top-41,'SYSTEM',13,TEXT,100)
        text(x+204,top-60,'PROJECT FILES',10,(.80,.81,1,1),110)
        reveal=min(1,(time.monotonic()-self.opened_at)/.4)
        rect(x+24,top-109,(w-48)*(1-(1-reveal)**3),1,(.60,.62,1,.70))
        if BUSY:
            phase=(time.monotonic()* .7)%1
            lx=x+24+phase*max(1,w-148)
            gradient(lx,top-111,100,2,(.8,.8,1,0),(.8,.8,1,.85))
        self.button(x+w-448,top-57,98,32,'Save As',('saveas',))
        self.button(x+w-342,top-57,103,32,'Save next',('savenext',))
        self.button(x+w-231,top-57,102,32,'Scanning…' if BUSY else 'Refresh',('refresh',))
        self.button(x+w-121,top-57,96,32,'Close  ×',('close',))
        text(x+26,top-91,STATUS,12,(.88,.89,1,1) if not DATA['errors'] else (.99,.62,.60,1),w-310)
        self.button(x+w-306,top-100,282,28,'Root: '+bpy.path.abspath(prefs().root)+'   · Change',('root',),(.065,.075,.12,.95))
        if getattr(self,'comment_path',None):
            self.paint_comments(x,y,w,h)
            return
        self.button(x+24,top-145,w-48,36,('Search filenames and folders…' if not self.query else self.query)+(' |' if self.typing else ''),('search',),(.11,.13,.19,1))
        cw = (w-64)/5
        coltop = top-165
        ch = max(115, min(216,h-405))
        for i, key in enumerate(self.fields):
            UI_ALPHA=base_alpha*min(1,max(0,(time.monotonic()-self.opened_at-.045*i)/.28))
            cx = x+24+i*(cw+4)
            glass(cx,coltop-ch,cw,ch,CARD,14)
            rect(cx+12,coltop-26,4,12,COLORS[i],2)
            text(cx+23,coltop-25,self.labels[i],10,COLORS[i],cw-33)
            base = self.records(i)
            values = DATA['projects'] if i == 0 else sorted({file_stage(f) if key == 'stage' else f[key] for f in base},key=natural,reverse=(key=='version'))
            values = ['All'] + values
            rows = max(2,int((ch-44)/29))
            self.offsets[i] = min(self.offsets[i],max(0,len(values)-rows))
            self.columns.append((cx,coltop-ch,cw,ch,i,len(values),rows))
            for j, value in enumerate(values[self.offsets[i]:self.offsets[i]+rows]):
                chosen = self.filters[i] == value
                color = tuple(c*.23 for c in COLORS[i][:3])+(1,) if chosen else CARD
                self.button(cx+7,coltop-63-j*29,cw-14,26,'All '+('projects' if i==0 else 'versions' if i==4 else key+'s') if value=='All' else value,('filter',i,value),color,COLORS[i] if chosen else TEXT)
            if len(values)>rows:
                text(cx+cw-26,coltop-24,'↕',12,MUTED)
        UI_ALPHA=base_alpha
        filetop = coltop-ch-29
        records = self.records()
        text(x+26,filetop,'BLENDER FILES',11,TEXT)
        text(x+160,filetop,str(len(records))+' matches',11,MUTED)
        self.button(x+w-135,filetop-8,110,26,'Reset filters',('reset',))
        rows = max(1,int((filetop-(y+165))/40))
        self.file_offset = min(self.file_offset,max(0,len(records)-rows))
        self.file_bounds = (x+24,y+160,w-48,max(0,filetop-y-170),len(records),rows)
        if not records:
            rect(x+24,y+134,w-48,max(38,filetop-y-155),CARD,10)
            text(x+46,filetop-40,'No Blender files match this view.',17)
            text(x+46,filetop-64,'Refresh after adding .blend files, or clear your filters.',12,MUTED,w-100)
        for j,f in enumerate(records[self.file_offset:self.file_offset+rows]):
            fy = filetop-48-j*40
            chosen = self.selected == f['path']
            selected_mix=self.tween('selected:'+f['path'],1.0 if chosen else 0.0)
            hovered=1.0 if x+24<=self.hover[0]<=x+w-24 and fy<=self.hover[1]<=fy+35 else 0.0
            hover_mix=self.tween('filehover:'+f['path'],hovered)
            row_color=tuple(CARD[k]*(1-selected_mix)+(COLORS[0][k]*.22)*selected_mix+.025*hover_mix for k in range(3))+(1,)
            UI_ALPHA=base_alpha*min(1,max(0,(time.monotonic()-self.content_at-.025*j)/.23))
            glass(x+24,fy,w-48,35,row_color,10)
            if selected_mix>.01: rect(x+26,fy+8,3,19,(*COLORS[0][:3],selected_mix),2)
            text(x+37,fy+12,f['name'],13,TEXT,w*.45)
            text(x+w*.51,fy+12,f['shot'],12,COLORS[2],100)
            text(x+w*.66,fy+12,file_stage(f),12,COLORS[3],110)
            text(x+w*.82,fy+12,f['version'],12,COLORS[4],75)
            self.hits.append((x+24,fy,w-48,35,('select',f['path'])))
        UI_ALPHA=base_alpha
        selected = next((f for f in records if f['path']==self.selected),None)
        glass(x+24,y+42,w-48,112,(.04,.05,.10,.96),14)
        if selected:
            text(x+40,y+95,selected['name'],14,TEXT,w-530)
            text(x+40,y+74,selected['relative'],11,MUTED,w-530)
            text(x+40,y+55,'{:.1f} MB  ·  {}  ·  Stage: {}'.format(selected['size']/1048576,datetime.fromtimestamp(selected['modified']).strftime('%d %b %Y %H:%M'), 'local tag' if os.path.normcase(selected['path']) in TAGS else 'folder inference'),10,MUTED,w-530)
            state=bpy.context.window_manager.neotrix_picker
            if state.comments_path!=selected['path']:
                workflow.load_discussion(bpy.context,selected['path'])
            count=len(state.comments)
            latest=state.comments[-1] if count else None
            summary=(latest.author+': '+latest.message.replace('\n',' ')) if latest else 'No comments yet. Write the first comment for this file.'
            text(x+40,y+130,'COMMENTS  '+str(count)+'  |  '+summary,11,MUTED,w-80)
            self.button(x+w-508,y+67,108,32,'Read ('+str(count)+')',('comments',selected['path']))
            self.button(x+w-394,y+67,144,32,'Write comment',('write_comment',selected['path']))
            self.button(x+w-242,y+67,82,32,'Stage',('stage',selected['path']))
            self.button(x+w-152,y+67,112,32,'Open file →',('open',selected['path']),COLORS[0],(.07,.035,.14,1))
        else:
            text(x+42,y+84,'Choose a file to see its details and open it.',13,MUTED,w-250)
            self.button(x+w-182,y+65,140,32,'Create / Save As',('saveas',))
        text(x+26,y+18,'ESC close   ·   / search   ·   Scroll over a column to browse   ·   Double-click a file to open',10,MUTED,w-50)

    def open_comments(self, context, filepath, write=False):
        self.comment_path=filepath
        self.typing=False
        self.comment_focus='message' if write else None
        self.comment_scroll=0
        self.comment_status=''
        self.comment_select_all=False
        self.comment_drafts=getattr(self,'comment_drafts',{})
        self.comment_author=prefs().author or getpass.getuser()
        self.comment_message=self.comment_drafts.get(filepath,'')
        self.comment_cursor=len(self.comment_message)
        workflow.load_discussion(context,filepath)

    def post_comment(self, context):
        try:
            storage.add_comment(bpy.path.abspath(prefs().root),self.comment_path,self.comment_author,self.comment_message)
            prefs().author=self.comment_author.strip()
            self.comment_message=''
            self.comment_drafts.pop(self.comment_path,None)
            self.comment_cursor=0
            workflow.load_discussion(context,self.comment_path)
            self.comment_scroll=0
            self.comment_status='Comment posted. Shared with the team.'
        except (ValueError,OSError) as exc:
            self.comment_status=str(exc)

    def edit_comment(self, context, event):
        focus=self.comment_focus
        key='comment_author' if focus=='author' else 'comment_message'
        value=getattr(self,key)
        cursor=min(self.comment_cursor,len(value))
        if event.ctrl and event.type in {'RET','NUMPAD_ENTER'}:
            self.post_comment(context);return
        if event.ctrl and event.type=='A':
            self.comment_select_all=True;return
        if event.type in {'LEFT_ARROW','RIGHT_ARROW','HOME','END'}:
            cursor=0 if event.type=='HOME' else len(value) if event.type=='END' else max(0,cursor-1) if event.type=='LEFT_ARROW' else min(len(value),cursor+1)
            self.comment_select_all=False
        elif event.type=='TAB':
            self.comment_focus='message' if focus=='author' else 'author'
            self.comment_cursor=len(getattr(self,'comment_'+self.comment_focus));return
        else:
            insertion=None
            if event.ctrl and event.type=='V': insertion=context.window_manager.clipboard
            elif event.type in {'RET','NUMPAD_ENTER'}: insertion='\n' if focus=='message' else None
            elif event.unicode and event.unicode.isprintable() and not event.ctrl: insertion=event.unicode
            deleting=event.type in {'BACK_SPACE','DEL'}
            if self.comment_select_all and (insertion is not None or deleting):
                value='';cursor=0
            if insertion is not None:
                if focus=='author': insertion=insertion.replace('\n',' ').replace('\r',' ')
                value=value[:cursor]+insertion+value[cursor:];cursor+=len(insertion)
            elif event.type=='BACK_SPACE' and cursor:
                value=value[:cursor-1]+value[cursor:];cursor-=1
            elif event.type=='DEL': value=value[:cursor]+value[cursor+1:]
            self.comment_select_all=False
        value=value[:120 if focus=='author' else 12000]
        setattr(self,key,value);self.comment_cursor=min(cursor,len(value))
        self.comment_drafts[self.comment_path]=self.comment_message

    def paint_comments(self,x,y,w,h):
        top=y+h
        self.button(x+24,top-145,144,32,'Back to files',('comment_back',))
        text(x+182,top-133,os.path.basename(self.comment_path),13,TEXT,w-380)
        self.button(x+w-164,top-145,140,32,'Refresh comments',('comment_refresh',))
        state=bpy.context.window_manager.neotrix_picker
        history_top=top-164;history_bottom=y+218
        glass(x+24,history_bottom,w-48,history_top-history_bottom,CARD,14)
        lines=[]
        chars=max(25,int((w-88)/7.2))
        for c in reversed(list(state.comments)):
            lines.append((c.author+'  ·  '+c.created,COLORS[1]))
            for paragraph in c.message.splitlines() or ['']:
                lines.extend((line,TEXT) for line in textwrap.wrap(paragraph,chars) or [''])
            lines.append(('',MUTED))
        if not lines: lines=[('No comments yet. Start the discussion below.',MUTED)]
        rows=max(1,int((history_top-history_bottom-26)/21))
        self.comment_scroll=max(0,min(self.comment_scroll,max(0,len(lines)-rows)))
        self.comment_bounds=(x+24,history_bottom,w-48,history_top-history_bottom,max(0,len(lines)-rows))
        for i,(line,ink) in enumerate(lines[self.comment_scroll:self.comment_scroll+rows]):
            text(x+40,history_top-26-i*21,line,12,ink,w-80)
        text(x+26,y+198,'YOUR NAME',10,MUTED)
        self.button(x+130,y+183,w-154,30,(self.comment_author[:self.comment_cursor]+'|'+self.comment_author[self.comment_cursor:]) if self.comment_focus=='author' else self.comment_author,('comment_author',))
        glass(x+24,y+78,w-48,95,(.11,.13,.19,1),12)
        self.hits.append((x+24,y+78,w-48,95,('comment_message',)))
        value=self.comment_message
        if self.comment_focus=='message': value=value[:self.comment_cursor]+'|'+value[self.comment_cursor:]
        rendered=[]
        for paragraph in value.split('\n'):rendered.extend(textwrap.wrap(paragraph,chars) or [''])
        if not value:rendered=['Write a comment…']
        for i,line in enumerate(rendered[-3:]):text(x+40,y+148-i*23,line,13,TEXT if value else MUTED,w-80)
        self.button(x+w-178,y+38,154,32,'Post comment',('comment_post',),COLORS[1],(.10,.045,.02,1))
        status=state.comment_error or self.comment_status or str(len(state.comments))+' shared comments · Enter: new line · Ctrl+Enter: post'
        text(x+26,y+48,status,11,MUTED,w-225)
        text(x+26,y+18,'Read and write here · Scroll discussion · ESC leaves editing, then returns to files',10,MUTED,w-50)

    def invoke(self, context, event):
        global ACTIVE
        if ACTIVE:
            self.report({'INFO'}, 'Relay browser is already open')
            return {'CANCELLED'}
        region = next(r for r in context.area.regions if r.type == 'WINDOW')
        self.area = context.area
        region = next(r for r in self.area.regions if r.type == 'WINDOW')
        self.region = region
        self.window = context.window
        self.old_sidebar = self.area.spaces.active.show_region_ui
        self.old_toolbar = self.area.spaces.active.show_region_toolbar
        self.area.spaces.active.show_region_ui = False
        self.area.spaces.active.show_region_toolbar = False
        self.filters = ['All']*5
        self.offsets = [0]*5
        self.query = ''
        self.typing = False
        self.selected = None
        self.comment_path=None
        self.comment_focus=None
        self.comment_drafts={}
        self.file_offset = 0
        self.hits = []
        self.columns = []
        self.hover=(-1,-1)
        self.hover_levels={}
        self.opened_at=time.monotonic()
        self.content_at=self.opened_at
        self.motion_shift=0
        self.last_click = ('',0)
        self.ui_scale=min(max(1.0,context.preferences.system.ui_scale),self.region.width/840,self.region.height/560)
        self.backdrop=backdrop.BlurredBackdrop()
        self.handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_overlay, (), 'WINDOW', 'POST_PIXEL')
        self.timer = context.window_manager.event_timer_add(1/30,window=context.window)
        ACTIVE = self
        context.window_manager.modal_handler_add(self)
        start_scan()
        self.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def finish(self, context):
        global ACTIVE
        if self.handle:
            bpy.types.SpaceView3D.draw_handler_remove(self.handle,'WINDOW')
            self.handle = None
        if self.timer:
            context.window_manager.event_timer_remove(self.timer)
            self.timer = None
        if hasattr(self,'backdrop'):self.backdrop.free()
        ACTIVE = None
        if self.area.type == 'VIEW_3D':
            self.area.spaces.active.show_region_ui = self.old_sidebar
            self.area.spaces.active.show_region_toolbar = self.old_toolbar
        global UI_ALPHA
        UI_ALPHA=1.0
        self.area.tag_redraw()

    def modal(self, context, event):
        if self.area not in list(context.screen.areas):
            self.finish(context)
            return {'CANCELLED'}
        if getattr(self,'comment_path',None):
            if event.type=='ESC' and event.value=='PRESS':
                if self.comment_focus:self.comment_focus=None
                else:self.comment_path=None
                self.area.tag_redraw();return {'RUNNING_MODAL'}
            if event.value=='PRESS' and event.type not in {'LEFTMOUSE','MOUSEMOVE','WHEELUPMOUSE','WHEELDOWNMOUSE','TIMER'}:
                if self.comment_focus:self.edit_comment(context,event)
                self.area.tag_redraw();return {'RUNNING_MODAL'}
            if event.type in {'WHEELUPMOUSE','WHEELDOWNMOUSE'}:
                if hasattr(self,'comment_bounds'):
                    self.comment_scroll=max(0,min(self.comment_bounds[-1],self.comment_scroll+(3 if event.type=='WHEELDOWNMOUSE' else -3)))
                self.area.tag_redraw();return {'RUNNING_MODAL'}
        if event.type == 'ESC' and event.value == 'PRESS':
            if self.typing:
                self.typing = False
                self.area.tag_redraw()
                return {'RUNNING_MODAL'}
            self.finish(context)
            return {'CANCELLED'}
        if event.type=='MOUSEMOVE':
            self.hover=((event.mouse_x-self.region.x)/self.ui_scale,(event.mouse_y-self.region.y)/self.ui_scale-self.motion_shift)
        if event.type == 'TIMER':
            self.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.value == 'PRESS' and event.type == 'SLASH' and not self.typing:
            self.typing = True
        elif self.typing and event.value == 'PRESS':
            if event.type in {'RET','NUMPAD_ENTER'}:
                self.typing = False
            elif event.type == 'BACK_SPACE':
                self.query = self.query[:-1]
            elif event.ctrl and event.type == 'V':
                self.query += context.window_manager.clipboard.replace('\n',' ')
            elif event.unicode and event.unicode.isprintable():
                self.query += event.unicode
            self.selected = None
            self.file_offset = 0
            self.content_at=time.monotonic()
        if event.type in {'WHEELUPMOUSE','WHEELDOWNMOUSE'}:
            direction = 1 if event.type=='WHEELDOWNMOUSE' else -1
            mx,my = (event.mouse_x-self.region.x)/self.ui_scale,(event.mouse_y-self.region.y)/self.ui_scale-self.motion_shift
            for cx,cy,cw,ch,i,count,rows in self.columns:
                if cx<=mx<=cx+cw and cy<=my<=cy+ch:
                    self.offsets[i] = max(0,min(count-rows,self.offsets[i]+direction))
            if hasattr(self,'file_bounds'):
                cx,cy,cw,ch,count,rows = self.file_bounds
                if cx<=mx<=cx+cw and cy<=my<=cy+ch:
                    self.file_offset = max(0,min(count-rows,self.file_offset+direction))
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            mx,my = (event.mouse_x-self.region.x)/self.ui_scale,(event.mouse_y-self.region.y)/self.ui_scale-self.motion_shift
            for cx,cy,cw,ch,action in reversed(self.hits):
                if cx<=mx<=cx+cw and cy<=my<=cy+ch:
                    kind = action[0]
                    self.typing = kind=='search'
                    if kind.startswith('comment_'):
                        if kind=='comment_back':self.comment_path=None;self.comment_focus=None
                        elif kind=='comment_refresh':workflow.load_discussion(context,self.comment_path)
                        elif kind=='comment_post':self.post_comment(context)
                        elif kind in {'comment_author','comment_message'}:
                            self.comment_focus=kind[8:];self.comment_cursor=len(getattr(self,kind));self.comment_select_all=False
                        self.area.tag_redraw();return {'RUNNING_MODAL'}
                    if kind in {'comments','write_comment'}:
                        self.open_comments(context,action[1],write=kind=='write_comment')
                        self.area.tag_redraw();return {'RUNNING_MODAL'}
                    if kind=='close':
                        self.finish(context)
                        return {'FINISHED'}
                    if kind=='root':
                        self.finish(context)
                        bpy.ops.relay.change_root('INVOKE_DEFAULT')
                        return {'FINISHED'}
                    if kind=='render_preview':
                        self.finish(context)
                        bpy.ops.relay.render_preview('EXEC_DEFAULT',mode=action[1])
                        return {'FINISHED'}
                    if kind=='browse_preview':
                        bpy.ops.relay.browse_preview('INVOKE_DEFAULT',mode=action[1])
                    if kind=='refresh': start_scan()
                    elif kind in {'saveas','savenext'}:
                        self.finish(context)
                        bpy.ops.relay.save_version('INVOKE_DEFAULT',mode='NEXT' if kind=='savenext' else 'NEW')
                        return {'FINISHED'}
                    elif kind=='filter': self.choose(action[1],action[2])
                    elif kind=='reset':
                        self.filters=['All']*5
                        self.offsets=[0]*5
                        self.query=''
                        self.selected=None
                    elif kind=='select':
                        self.selected=action[1]
                        if self.last_click[0]==action[1] and time.monotonic()-self.last_click[1]<.35:
                            self.finish(context)
                            bpy.ops.relay.open_file('INVOKE_DEFAULT',filepath=action[1])
                            return {'FINISHED'}
                        self.last_click=(action[1],time.monotonic())
                    elif kind in {'open','stage'}:
                        self.finish(context)
                        if kind=='open': bpy.ops.relay.open_file('INVOKE_DEFAULT',filepath=action[1])
                        else: bpy.ops.relay.set_stage('INVOKE_DEFAULT',filepath=action[1])
                        return {'FINISHED'}
                    elif kind in {'folder','project_folder'}:
                        path = action[1] if kind=='folder' else os.path.join(bpy.path.abspath(prefs().root),self.filters[0] if self.filters[0] not in {'All','Root files'} else '')
                        try: bpy.ops.wm.path_open(filepath=path)
                        except Exception as exc: self.report({'ERROR'},str(exc))
                    break
        self.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self.finish(context)

FIELDS = ['project','variant','shot','stage','version']

def sidebar_records(state, until=5):
    # Snapshot selections once. Reading an EnumProperty invokes its items
    # callback, so callbacks must never call this filtering function themselves.
    selected = [(key, getattr(state, key)) for key in FIELDS[:until]]
    query = state.search.lower()
    return [f for f in DATA['files'] if all(value == '__ALL__' or
            (file_stage(f) if key == 'stage' else f[key]) == value
            for key, value in selected) and
            (not query or query in f['relative'].lower())]

def cache_enum_items(field, values):
    signature = (field, tuple(values))
    if signature not in ENUM_CACHE:
        numbers = ENUM_NUMBERS.setdefault(field, {})
        label = 'All ' + ('projects' if field == 'project' else
                         'save versions' if field == 'version' else field + 's')
        items = [('__ALL__', label, 'Show all', 0)]
        for value in values:
            # Stable numbers keep Blender's stored selection attached to the
            # same identifier when scan results are reordered or filtered.
            number = numbers.setdefault(value, len(numbers) + 1)
            items.append((value, value, value, number))
        # Keep references to the strings Blender receives for dynamic enums.
        ENUM_CACHE[signature] = items
    return ENUM_CACHE[signature]

def enum_items(field):
    ENUM_CURRENT.setdefault(field, cache_enum_items(field, ()))
    def items(state, context):
        # Constant-time access: no filesystem work, filtering or RNA reads.
        return ENUM_CURRENT[field]
    return items

def refresh_enum_items(state, field):
    if field == 'project':
        values = DATA['projects']
    else:
        records = sidebar_records(state, FIELDS.index(field))
        values = sorted({file_stage(f) if field == 'stage' else f[field]
                         for f in records}, key=natural, reverse=field == 'version')
    ENUM_CURRENT[field] = cache_enum_items(field, values)
    return ENUM_CURRENT[field]

def update_sidebar(context):
    global UPDATING
    if not context or not hasattr(context.window_manager, 'neotrix_picker'):
        return
    state = context.window_manager.neotrix_picker
    previous = UPDATING
    UPDATING = True
    try:
        # Read old identifiers before replacing lists; an invalid enum number
        # cannot be resolved after its item is removed from the current list.
        selected = {field: getattr(state, field) for field in FIELDS}
        for field in FIELDS:
            valid = {item[0] for item in refresh_enum_items(state, field)}
            setattr(state, field, selected[field] if selected[field] in valid else '__ALL__')
        state.projects.clear()
        for name in DATA['projects']:
            item = state.projects.add()
            item.name = name
        old_path = state.files[state.active_index].path if state.files and state.active_index < len(state.files) else ''
        state.files.clear()
        for f in sidebar_records(state):
            item = state.files.add()
            item.name = f['name']
            item.path = f['path']
            item.stage = file_stage(f)
            item.version = f['version']
        state.active_index = next((i for i, f in enumerate(state.files) if f.path == old_path), 0)
    finally:
        UPDATING = previous
    workflow.load_discussion(context)
    redraw()

def search_update(state, context):
    if not UPDATING:
        update_sidebar(context)

def field_update(field):
    def changed(state, context):
        global UPDATING
        if UPDATING: return
        UPDATING=True
        try:
            for later in FIELDS[FIELDS.index(field)+1:]: setattr(state,later,'__ALL__')
            state.active_index=0
            update_sidebar(context)
        finally: UPDATING=False
    return changed

class RELAY_File(bpy.types.PropertyGroup):
    path: StringProperty()
    stage: StringProperty()
    version: StringProperty()

class NEOTRIX_Comment(bpy.types.PropertyGroup):
    author: StringProperty()
    message: StringProperty()
    created: StringProperty()

def selection_changed(state,context):
    if not UPDATING:workflow.load_discussion(context)

class RELAY_State(bpy.types.PropertyGroup):
    show_project_details: BoolProperty(name='Current Project',description='Show or hide current project details',default=True)
    project: EnumProperty(name='Project',items=enum_items('project'),update=field_update('project'))
    variant: EnumProperty(name='Variant / Film',items=enum_items('variant'),update=field_update('variant'))
    shot: EnumProperty(name='Shot',items=enum_items('shot'),update=field_update('shot'))
    stage: EnumProperty(name='Stage',items=enum_items('stage'),update=field_update('stage'))
    version: EnumProperty(name='Save version',items=enum_items('version'),update=field_update('version'))
    search: StringProperty(name='Search',description='Search filenames and relative paths',update=search_update)
    files: CollectionProperty(type=RELAY_File)
    projects: CollectionProperty(type=RELAY_File)
    active_index: IntProperty(default=0,update=selection_changed)
    comments: CollectionProperty(type=NEOTRIX_Comment)
    comments_path: StringProperty()
    comment_error: StringProperty()

class RELAY_UL_files(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        row=layout.row(align=True)
        row.label(text=item.name,icon='FILE_BLEND')
        row.label(text=item.version)

class RELAY_OT_sidebar_reset(bpy.types.Operator):
    bl_idname='relay.reset_picker'
    bl_label='Reset filters'
    def execute(self,context):
        global UPDATING
        state=context.window_manager.neotrix_picker
        UPDATING=True
        for field in FIELDS: setattr(state,field,'__ALL__')
        state.search=''
        UPDATING=False
        update_sidebar(context)
        return {'FINISHED'}

class RELAY_OT_folder(bpy.types.Operator):
    bl_idname='relay.reveal'
    bl_label='Show in Explorer'
    filepath: StringProperty()
    def execute(self,context):
        try: bpy.ops.wm.path_open(filepath=self.filepath)
        except Exception as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        return {'FINISHED'}

def current_scene_info(context):
    """Read the opened scene, independently of the browser's selected file."""
    path=bpy.data.filepath
    root=bpy.path.abspath(prefs(context).root)
    if not path:
        return None, 'Unsaved scene'
    key=(os.path.normcase(os.path.abspath(path)),os.path.normcase(os.path.abspath(root)))
    cached=getattr(current_scene_info,'cache',None)
    if not cached or cached[0]!=key:
        info=None
        status='Outside Relay'
        if storage.display_contained(root,path):
            try:
                info=parse(path,root,metadata=False)
                status=''
            except OSError:
                status='File unavailable'
        current_scene_info.cache=(key,info,status)
    _,info,status=current_scene_info.cache
    return info,status

class RELAY_PT_panel(bpy.types.Panel):
    bl_label = 'NEOTRIX SYSTEM'
    bl_idname = 'RELAY_PT_panel'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Neotrix'
    def draw(self, context):
        layout=self.layout
        layout.label(text='Relay root',icon_value=ICONS['location'].icon_id if ICONS else 0)
        layout.prop(prefs(context),'root',text='')
        layout.separator()
        state=context.window_manager.neotrix_picker
        header=layout.row(align=True)
        header.prop(state,'show_project_details',text='',icon='TRIA_DOWN' if state.show_project_details else 'TRIA_RIGHT',emboss=False)
        header.label(text='CURRENT PROJECT',icon_value=ICONS['brand'].icon_id if ICONS else 0)
        if state.show_project_details:
            box=layout.box()
            info,status=current_scene_info(context)
            if info:
                for field,label in zip(FIELDS,['Project','Variant / Film','Shot','Stage','Save version']):
                    row=box.row()
                    split=row.split(factor=.43)
                    split.label(text=label,icon_value=ICONS[field].icon_id if ICONS else 0)
                    split.label(text=bpy.context.scene.get('neotrix_stage',file_stage(info)) if field=='stage' else info[field])
                box.separator()
            else:
                box.label(text=status,icon_value=ICONS['stage'].icon_id if ICONS else 0)
            box.label(text=os.path.basename(bpy.data.filepath) if bpy.data.filepath else 'Create your first save in the browser',icon_value=ICONS['file'].icon_id if ICONS else 0)
            location=box.column(align=True)
            location.label(text='Location',icon_value=ICONS['location'].icon_id if ICONS else 0)
            directory=info['relative'].rsplit(os.sep,1)[0] if info else os.path.dirname(bpy.data.filepath) if bpy.data.filepath else 'No saved location yet'
            for line in textwrap.wrap(directory.replace(os.sep,' / '),width=max(22,int(context.region.width/7))):
                location.label(text=line)
            if bpy.data.is_dirty:
                box.label(text='Unsaved changes',icon='RECOVER_LAST')
        layout.separator()
        row=layout.row()
        row.scale_y=1.35
        row.operator('relay.render_output_setup',text='Render Output Setup',icon='RENDERLAYERS')
        render_box=layout.box()
        header=render_box.row(align=True)
        header.label(text='QUICK RENDER',icon='RENDER_STILL')
        header.operator('relay.preview_settings',text='',icon='PREFERENCES')
        preset=rendering.settings(context.scene)
        managed=bool(bpy.data.filepath)
        for mode,label,icon_key in [('STILL','Still image preview','version'),('COLOR','Lowres video lookdev preview','stage'),('PLAY','Playblast preview','shot'),('QUICK','Quick animation preview','project')]:
            render_box.label(text=label,icon_value=ICONS[icon_key].icon_id if ICONS else 0)
            label=('Workbench · '+str(preset.play_percentage)+'%') if mode=='PLAY' else ('Cycles · '+str(preset.still_percentage if mode=='STILL' else preset.color_percentage)+'% · '+str(preset.still_samples if mode=='STILL' else preset.color_samples)+' samples')
            if mode=='QUICK':
                start,end,_=rendering.preview_range(context.scene,mode)
                render_box.label(text='Frames '+str(start)+'-'+str(end)+'  /  current +/- 6',icon='TIME')
            row=render_box.row(align=True)
            row.scale_y=1.25
            split=row.split(factor=.88,align=True)
            left=split.row(align=True)
            left.enabled=managed and rendering.JOB is None and split_render.JOB is None
            left.operator('relay.render_preview',text=label).mode=mode
            right=split.row(align=True)
            right.enabled=managed
            right.operator('relay.browse_preview',text='',icon='FILE_FOLDER').mode=mode
        if not managed: render_box.label(text='Save the Blender file to render',icon='INFO')
        layout.separator()
        row=layout.row()
        row.scale_y=1.8
        row.operator('relay.browser',text='Explore Project Files',icon_value=ICONS['project'].icon_id if ICONS else 0)
        layout.label(text='Browse projects, shots and versions')
        layout.label(text='Create saves and view file comments')
        layout.separator()
        layout.label(text='Ctrl + Alt + R opens the browser',icon='INFO')
        layout.label(text='N to show / hide this sidebar')

CLASSES = (RELAY_Preferences,NEOTRIX_OT_root,RELAY_OT_refresh,RELAY_OT_open,RELAY_OT_stage,RELAY_OT_browser,RELAY_File,NEOTRIX_Comment,RELAY_State,RELAY_UL_files,RELAY_OT_sidebar_reset,RELAY_OT_folder)+workflow.CLASSES+rendering.CLASSES+split_render.CLASSES+(RELAY_PT_panel,)

def register():
    global TAGS, ICONS, LOGO
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.neotrix_picker=PointerProperty(type=RELAY_State)
    bpy.types.Scene.neotrix_render_settings=PointerProperty(type=rendering.NEOTRIX_RenderSettings)
    bpy.types.Scene.neotrix_split_settings=PointerProperty(type=split_render.NEOTRIX_SplitSettings)
    ICONS=bpy.utils.previews.new()
    for key in ['brand','location','file']+FIELDS:
        path=os.path.join(os.path.dirname(__file__),'icons',key+'.png')
        if os.path.isfile(path): ICONS.load(key,path,'IMAGE')
    try:
        with open(config_file(),encoding='utf8') as handle:
            TAGS=json.load(handle)
        if not isinstance(TAGS,dict): TAGS={}
    except (OSError,ValueError): TAGS={}
    kc=bpy.context.window_manager.keyconfigs.addon
    if kc:
        km=kc.keymaps.new(name='3D View',space_type='VIEW_3D')
        kmi=km.keymap_items.new('relay.browser','R','PRESS',ctrl=True,alt=True)
        KEYMAPS.append((km,kmi))
    if not bpy.app.timers.is_registered(initial_scan):
        bpy.app.timers.register(initial_scan,first_interval=1)

def initial_scan():
    start_scan()
    return None

def unregister():
    global BUSY, ICONS, LOGO
    if ACTIVE: ACTIVE.finish(bpy.context)
    if bpy.app.timers.is_registered(consume): bpy.app.timers.unregister(consume)
    if bpy.app.timers.is_registered(initial_scan): bpy.app.timers.unregister(initial_scan)
    BUSY=False
    for km,kmi in KEYMAPS: km.keymap_items.remove(kmi)
    KEYMAPS.clear()
    if ICONS:
        bpy.utils.previews.remove(ICONS)
        ICONS=None
    if LOGO is not None:
        try: bpy.data.images.remove(LOGO)
        except ReferenceError: pass
        LOGO=None
    if hasattr(bpy.types.WindowManager,'neotrix_picker'):del bpy.types.WindowManager.neotrix_picker
    if hasattr(bpy.types.Scene,'neotrix_render_settings'):del bpy.types.Scene.neotrix_render_settings
    if hasattr(bpy.types.Scene,'neotrix_split_settings'):del bpy.types.Scene.neotrix_split_settings
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
