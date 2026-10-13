"""One-click current-scene previews with settings restored after render/cancel."""
import os
import re
import uuid
import importlib
from datetime import datetime
import bpy
from bpy.props import EnumProperty, IntProperty, BoolProperty
from . import storage
MODES=[('STILL','Still image preview','Cycles current-frame preview'),('COLOR','Lowres video lookdev preview','Cycles full-range lookdev preview'),('PLAY','Playblast preview','Workbench full-range preview'),('QUICK','Quick animation preview','Cycles current frame plus six frames before and after')]
FOLDER_NAMES={'STILL':'still_image_preview','COLOR':'lowres_video_lookdev_preview','PLAY':'playblast_preview','QUICK':'quick_animation_preview'}
JOB=None
LAST=''
def owner(): return importlib.import_module(__package__)
def output_identity(path=None):
    path=bpy.data.filepath if path is None else path
    if not path:
        raise ValueError('Save the Blender file before rendering a preview.')
    stem=os.path.splitext(os.path.basename(path))[0]
    match=re.search(r'(?i)_v(\d+)$',stem)
    version='v'+match.group(1).zfill(3) if match else None
    prefix=stem[:match.start()] if match else stem
    prefix=re.sub(r'(?i)_(preproduction|modeling|lookdev|rigging|layout|animation|lighting|rendering|post|delivery)$','',prefix) if match else prefix
    return prefix,version

def preview_folder(mode='COLOR',path=None):
    path=bpy.data.filepath if path is None else path
    prefix,version=output_identity(path)
    directory=os.path.dirname(path)
    parts=directory.split(os.sep)
    positions=[i for i,p in enumerate(parts) if p.upper()=='PROJECTS']
    shot=os.sep.join(parts[:positions[-1]]) if positions and any(p.upper()=='SHOTS' for p in parts[:positions[-1]]) else None
    target=os.path.join(shot,'RENDERS') if shot else os.path.join(directory,'RENDERS',os.path.splitext(os.path.basename(path))[0])
    if version: target=os.path.join(target,version)
    return os.path.join(target,'frames' if mode=='STILL' else 'preview')

class NEOTRIX_RenderSettings(bpy.types.PropertyGroup):
    still_percentage: IntProperty(name='Resolution %',default=100,min=1,max=100,subtype='PERCENTAGE')
    still_samples: IntProperty(name='Cycles samples',default=256,min=1,max=8192)
    color_percentage: IntProperty(name='Resolution %',default=25,min=1,max=100,subtype='PERCENTAGE')
    color_samples: IntProperty(name='Cycles samples',default=5,min=1,max=8192)
    play_percentage: IntProperty(name='Resolution %',default=50,min=1,max=100,subtype='PERCENTAGE')
    denoise: BoolProperty(name='Cycles denoising',default=True)
    workbench_color: EnumProperty(name='Workbench color',items=[('MATERIAL','Material','Material viewport colors'),('OBJECT','Object','Object viewport colors'),('RANDOM','Random','Random object colors')],default='MATERIAL')

def settings(scene): return scene.neotrix_render_settings

def preview_range(scene, mode):
    if mode == 'QUICK':
        return max(0, scene.frame_current - 6), scene.frame_current + 6, 1
    return scene.frame_start, scene.frame_end, scene.frame_step

def mode_folder(mode): return os.path.join(preview_folder(mode),FOLDER_NAMES[mode])

def final_output():
    folder=preview_folder('STILL')
    prefix,version=output_identity()
    stem=prefix+'_'+version if version else prefix
    return folder,os.path.join(folder,stem+'_####')

def output_preset(scene,output):
    r=scene.render;c=scene.cycles
    r.engine='CYCLES';c.device='GPU'
    c.preview_samples=64;c.samples=256
    for key in ('max_bounces','diffuse_bounces','glossy_bounces','transmission_bounces','volume_bounces','transparent_max_bounces'):
        setattr(c,key,6)
    r.film_transparent=True;r.use_motion_blur=True;r.motion_blur_shutter=.5
    r.use_persistent_data=True
    scene.display_settings.display_device='sRGB'
    scene.view_settings.view_transform='ACES 2.0'
    for layer in scene.view_layers:
        layer.use_pass_z=True
        layer.use_pass_mist=True
        layer.use_pass_emit=True
    if hasattr(r.image_settings,'media_type'): r.image_settings.media_type='MULTI_LAYER_IMAGE'
    r.image_settings.file_format='OPEN_EXR_MULTILAYER';r.image_settings.color_mode='RGBA'
    r.image_settings.exr_codec='DWAA';r.image_settings.color_depth='16'
    r.filepath=output;r.use_file_extension=True

class NEOTRIX_OT_output_setup(bpy.types.Operator):
    bl_idname='relay.render_output_setup'
    bl_label='Render Output Setup'
    bl_description='Apply Neotrix output settings: Cycles GPU, viewport 64 / render 256 samples, all bounces 6, transparent film, motion blur 0.5, persistent data, sRGB / ACES 2.0. Enable Depth, Mist and Emission passes on all view layers. Set Multilayer EXR, DWAA, Half Float (16-bit) to the saved shot RENDERS/v###/frames/CODE_Variant_Shot_v###_####.exr. Preserve dimensions, aspect ratio and frame range. Save the Blender file first'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):
        return context.scene is not None and JOB is None and owner().split_render.JOB is None and (bpy.app.background or not bpy.app.is_job_running('RENDER'))
    def execute(self,context):
        probe=None
        try:
            folder,output=final_output()
            # Validate engine and dynamic OCIO enums before touching the working scene.
            probe=context.scene.copy()
            output_preset(probe,output)
            os.makedirs(folder,exist_ok=True)
            bpy.data.scenes.remove(probe);probe=None
            output_preset(context.scene,output)
            owner().redraw()
            self.report({'INFO'},'Neotrix setup applied. EXR frames: '+output+'.exr')
            return {'FINISHED'}
        except (ValueError,TypeError,RuntimeError,OSError) as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        finally:
            if probe is not None: bpy.data.scenes.remove(probe)

def browse_destination(mode):
    base=mode_folder(mode)
    if os.path.isdir(base):
        runs=sorted((n for n in os.listdir(base) if os.path.isdir(os.path.join(base,n))),reverse=True)
        for name in runs:
            candidate=os.path.join(base,name)
            if any(n.lower().endswith(('.mp4','.exr')) for n in os.listdir(candidate)): return candidate
    return base

class NEOTRIX_OT_render_settings(bpy.types.Operator):
    bl_idname='relay.preview_settings'
    bl_label='Quick Render Settings'
    bl_description='Edit the preview presets; Quick animation uses the lowres video settings and renders current frame +/- 6 at step 1. Edit the other presets for this scene; defaults are Cycles 100% / 256 samples, Cycles 25% / 5 samples, and Workbench 50%'
    def invoke(self,context,event): return context.window_manager.invoke_props_dialog(self,width=430)
    def draw(self,context):
        s=settings(context.scene)
        for title,prefix in [('Still image preview','still'),('Lowres video lookdev preview','color'),('Playblast preview','play')]:
            box=self.layout.box();box.label(text=title,icon='RENDER_STILL' if prefix=='still' else 'RENDER_ANIMATION')
            box.label(text='Workbench' if prefix=='play' else 'Cycles')
            box.prop(s,prefix+'_percentage')
            if prefix!='play': box.prop(s,prefix+'_samples')
        self.layout.label(text='Quick animation: lowres settings, current frame +/- 6.')
        self.layout.prop(s,'denoise')
        self.layout.prop(s,'workbench_color')
        self.layout.label(text='Preset settings are stored with this Blender scene.')
    def execute(self,context): owner().redraw();return {'FINISHED'}

def capture(scene):
    r=scene.render
    fields=['engine','resolution_percentage','filepath','use_file_extension']
    state=[(r,k,getattr(r,k)) for k in fields]
    if hasattr(r.image_settings,'media_type'): state.append((r.image_settings,'media_type',r.image_settings.media_type))
    state += [(r.image_settings,k,getattr(r.image_settings,k)) for k in ['file_format','color_mode']]
    state += [(r.ffmpeg,k,getattr(r.ffmpeg,k)) for k in ['format','codec','constant_rate_factor','audio_codec']]
    state += [(scene.cycles,k,getattr(scene.cycles,k)) for k in ['samples','use_adaptive_sampling','use_denoising']]
    state += [(scene.display.shading,k,getattr(scene.display.shading,k)) for k in ['light','color_type','show_shadows','show_cavity']]
    return state

def restore(*args):
    global JOB,LAST
    if JOB is None: return
    job=JOB;JOB=None
    # The render engine may still own the copy inside completion/cancel handlers.
    # Never alter or free its scene until the interactive render job releases it.
    def cleanup():
        if not bpy.app.background and bpy.app.is_job_running('RENDER'): return .1
        try: job['source'].frame_set(job['frame'],subframe=job['subframe'])
        except ReferenceError: pass
        for window in bpy.context.window_manager.windows:
            if window.scene==job['scene']: window.scene=job['source']
        try: bpy.data.scenes.remove(job['scene'])
        except ReferenceError: pass
        tree=job.get('tree')
        if tree is not None and tree.users==0: bpy.data.node_groups.remove(tree)
        return None
    if bpy.app.background: cleanup()
    else: bpy.app.timers.register(cleanup,first_interval=.5)
    for handlers in [bpy.app.handlers.render_complete,bpy.app.handlers.render_cancel]:
        if restore in handlers: handlers.remove(restore)
    LAST='Preview saved: '+job['output'] if os.path.isfile(job['output']) else 'Render ended; settings restored'
    owner().redraw()

def switch_to_compositing(window):
    workspace=next((w for w in bpy.data.workspaces if w.name.casefold()=='compositing'),None)
    if workspace is None:
        # Keep the current layout intact; create a separate lightweight workspace.
        with bpy.context.temp_override(window=window): bpy.ops.workspace.duplicate()
        workspace=window.workspace
        workspace.name='Compositing'
        for screen in workspace.screens:
            for area in screen.areas:
                if area.type=='VIEW_3D':
                    area.type='NODE_EDITOR'
                    area.ui_type='CompositorNodeTree'
    window.workspace=workspace

class NEOTRIX_OT_preview(bpy.types.Operator):
    bl_idname='relay.render_preview'
    bl_label='Render preview'
    bl_description='Render the current scene to its versioned preview folder; restore settings afterwards'
    mode: EnumProperty(items=MODES,default='STILL')
    @classmethod
    def description(cls,context,properties):
        scene=context.scene;s=settings(scene);mode=properties.mode
        pct={'STILL':s.still_percentage,'COLOR':s.color_percentage,'QUICK':s.color_percentage,'PLAY':s.play_percentage}[mode]
        engine='Workbench' if mode=='PLAY' else 'Cycles'
        sample='' if mode=='PLAY' else ' · '+str(s.still_samples if mode=='STILL' else s.color_samples)+' samples'
        frames='Current frame '+str(scene.frame_current) if mode=='STILL' else 'Frames '+str(scene.frame_start)+'–'+str(scene.frame_end)+' (step '+str(scene.frame_step)+')'
        if mode=='QUICK':
            start,end,step=preview_range(scene,mode)
            frames='Frames '+str(start)+'-'+str(end)+' (current +/- 6, step 1)'
        size=str(scene.render.resolution_x*pct//100)+' × '+str(scene.render.resolution_y*pct//100)
        return dict((m[0],m[1]) for m in MODES)[mode]+'\n'+engine+' · '+str(pct)+'%'+sample+' · '+size+'\n'+frames+'\nGuide-compliant output folder. Switches to Compositing and renders a temporary scene. Original dimensions, pixel aspect, camera and settings stay unchanged.'
    def execute(self,context):
        global JOB,LAST
        if JOB is not None or owner().split_render.JOB is not None or (not bpy.app.background and bpy.app.is_job_running('RENDER')):
            self.report({'WARNING'},'A render is already running.');return {'CANCELLED'}
        source=context.scene
        scene=source
        try:
            folder=preview_folder(self.mode)
            if scene.camera is None: raise ValueError('Set an active camera before rendering.')
            os.makedirs(folder,exist_ok=True)
            prefix,version=output_identity()
            stem=prefix+'_'+version if version else prefix
            stamp=datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
            folder=os.path.join(folder,FOLDER_NAMES[self.mode],stamp)
            os.makedirs(folder,exist_ok=False)
            output=os.path.join(folder,stem+('_'+str(scene.frame_current).zfill(4)+'.exr' if self.mode=='STILL' else '.mp4'))
            scene=source.copy()
            scene.name='Neotrix temporary preview'
            JOB={'scene':scene,'source':source,'settings':capture(scene),'frame':source.frame_current,'subframe':source.frame_subframe,'output':output}
            # A prepared split graph must not write its EXRs during Quick Render.
            tree=source.compositing_node_group
            if tree:
                tree=tree.copy();scene.compositing_node_group=tree;JOB['tree']=tree
                split_owner=tree.get('neotrix_split_owner')
                for node in tree.nodes:
                    if node.type=='OUTPUT_FILE': node.mute=True
                    if node.type=='R_LAYERS' and node.scene in {source,None}: node.scene=scene
                if split_owner:
                    for layer in scene.view_layers: layer.use=layer.get('neotrix_split_owner')==split_owner
            if self.mode=='PLAY':
                scene.compositing_node_group=None
                first=next((v for v in scene.view_layers if not v.get('neotrix_split_owner')),scene.view_layers[0])
                for layer in scene.view_layers: layer.use=layer==first
            scene.frame_start,scene.frame_end,scene.frame_step=preview_range(source,self.mode)
            r=scene.render
            r.save_output=True
            preset=settings(scene)
            r.resolution_percentage={'STILL':preset.still_percentage,'COLOR':preset.color_percentage,'QUICK':preset.color_percentage,'PLAY':preset.play_percentage}[self.mode]
            if self.mode!='PLAY':
                r.engine='CYCLES'
                scene.cycles.samples=preset.still_samples if self.mode=='STILL' else preset.color_samples
                scene.cycles.use_adaptive_sampling=False
                scene.cycles.use_denoising=preset.denoise
                for layer in scene.view_layers:
                    layer.samples=0;layer.cycles.use_denoising=preset.denoise
            r.filepath=output;r.use_file_extension=True
            if hasattr(r.image_settings,'media_type'): r.image_settings.media_type='IMAGE' if self.mode=='STILL' else 'VIDEO'
            r.image_settings.file_format='OPEN_EXR' if self.mode=='STILL' else 'FFMPEG'
            r.image_settings.color_mode='RGBA' if self.mode=='STILL' else 'RGB'
            if self.mode!='STILL':
                r.ffmpeg.format='MPEG4';r.ffmpeg.codec='H264';r.ffmpeg.constant_rate_factor='MEDIUM';r.ffmpeg.audio_codec='AAC'
            if self.mode=='PLAY':
                r.engine='BLENDER_WORKBENCH'
                scene.display.shading.light='STUDIO'
                scene.display.shading.color_type=preset.workbench_color
                scene.display.shading.show_shadows=True
                scene.display.shading.show_cavity=True
            LAST='Rendering '+dict((m[0],m[1]) for m in MODES)[self.mode]
            if bpy.app.background:
                try: result=bpy.ops.render.render('EXEC_DEFAULT',animation=self.mode!='STILL',write_still=self.mode=='STILL',scene=scene.name)
                finally: restore()
            else:
                bpy.app.handlers.render_complete.append(restore)
                bpy.app.handlers.render_cancel.append(restore)
                window=context.window
                mode=self.mode
                switch_to_compositing(window)
                def launch():
                    global LAST
                    try:
                        area=max(window.screen.areas,key=lambda a:a.width*a.height)
                        region=next(r for r in area.regions if r.type=='WINDOW')
                        with bpy.context.temp_override(window=window,area=area,region=region):
                            result=bpy.ops.render.render('INVOKE_DEFAULT',animation=mode!='STILL',write_still=mode=='STILL',scene=scene.name)
                        if 'CANCELLED' in result: restore()
                    except Exception as exc:
                        restore()
                        LAST='Preview failed: '+str(exc)
                        print('Neotrix:',LAST)
                    return None
                # Give Blender one UI cycle to activate the new workspace before rendering.
                bpy.app.timers.register(launch,first_interval=.1)
            self.report({'INFO'},'Preview output: '+output)
            return {'FINISHED'}
        except Exception as exc:
            restore();self.report({'ERROR'},str(exc));return {'CANCELLED'}

class NEOTRIX_OT_preview_browse(bpy.types.Operator):
    bl_idname='relay.browse_preview'
    bl_label='Browse'
    bl_description='Open this preview’s latest output location directly in Windows Explorer; opens its designated folder if no render exists'
    mode: EnumProperty(items=MODES,default='COLOR')
    def invoke(self,context,event): return self.execute(context)
    def execute(self,context):
        try:
            folder=browse_destination(self.mode);os.makedirs(folder,exist_ok=True)
            if os.name=='nt': os.startfile(folder)
            else: bpy.ops.wm.path_open(filepath=folder)
        except (OSError,ValueError) as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        return {'FINISHED'}
CLASSES=(NEOTRIX_RenderSettings,NEOTRIX_OT_output_setup,NEOTRIX_OT_render_settings,NEOTRIX_OT_preview,NEOTRIX_OT_preview_browse)
