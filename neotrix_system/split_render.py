"""Collection-based Cycles splits and a single, compositor-packed EXR."""
import bpy
import os
import json
import uuid
import hashlib
from bpy.props import StringProperty, BoolProperty, IntProperty, EnumProperty, PointerProperty, CollectionProperty
from . import rendering

RULES=[('VISIBLE','Visible','Render normally'),('INDIRECT','Indirect Only','Keep reflections and indirect lighting; hide from camera'),('HOLDOUT','Holdout','Cut an alpha hole where this geometry blocks the subject'),('EXCLUDED','Excluded','Remove from this view layer')]
JOB=None

def config(scene): return scene.neotrix_split_settings
def uid(): return uuid.uuid4().hex
def descendants(collection):
    result={collection}
    for child in collection.children: result.update(descendants(child))
    return result
def scene_collections(scene): return descendants(scene.collection)-{scene.collection}
def helper(obj): return obj.type in {'LIGHT','CAMERA','EMPTY'} and not (obj.type=='EMPTY' and obj.instance_collection)
def lighting(collection):
    objects=list(collection.all_objects)
    return bool(objects) and any(o.type=='LIGHT' for o in objects) and all(helper(o) for o in objects)

class NEOTRIX_SplitRule(bpy.types.PropertyGroup):
    collection: PointerProperty(type=bpy.types.Collection)
    mode: EnumProperty(items=RULES,default='VISIBLE')
    default_mode: StringProperty()

class NEOTRIX_SplitGroup(bpy.types.PropertyGroup):
    collection: PointerProperty(type=bpy.types.Collection)
    label: StringProperty(name='Layer name')
    key: StringProperty()
    samples: IntProperty(name='Samples',description='0 inherits scene samples',default=0,min=0,max=8192)
    denoise: BoolProperty(name='Denoising',default=True)
    rules: CollectionProperty(type=NEOTRIX_SplitRule)

class NEOTRIX_SplitSettings(bpy.types.PropertyGroup):
    expanded: BoolProperty(name='Split Render',default=False)
    show_rules: BoolProperty(name='Visibility Rules',default=False)
    groups: CollectionProperty(type=NEOTRIX_SplitGroup)
    active_index: IntProperty(default=0,min=0)
    base_key: StringProperty()
    owner_key: StringProperty()
    prepared_signature: StringProperty()
    status: StringProperty(default='Choose collections, then prepare.')
    backup_tree: PointerProperty(type=bpy.types.NodeTree)
    backup_saved: BoolProperty(default=False)

def ordered(settings):
    return sorted(settings.groups,key=lambda g: g.key!=settings.base_key)

def validate(scene):
    if bpy.app.version<(5,2,0): raise ValueError('Split Render requires Blender 5.2 or later.')
    s=config(scene);groups=list(s.groups);available=scene_collections(scene)
    if not groups: raise ValueError('Add at least one split collection.')
    if s.base_key not in {g.key for g in groups}: raise ValueError('Choose a Base collection.')
    names=set();used_collections=set();used_objects=set()
    for g in groups:
        if g.collection is None or g.collection not in available: raise ValueError('A split collection is missing from this scene. Replace or remove its row.')
        if not g.label.strip() or g.label.strip().casefold() in names: raise ValueError('Each output layer needs a unique, non-empty name.')
        if any(c in g.label for c in '/\\\n\r'): raise ValueError('Output layer names cannot contain slashes or line breaks; collection names can stay unchanged.')
        if len(g.label.strip())>48: raise ValueError('Use an output layer name of 48 characters or fewer; the collection name can stay unchanged.')
        names.add(g.label.strip().casefold())
        subtree=descendants(g.collection);objects=set(g.collection.all_objects)
        if used_collections & subtree: raise ValueError('Split groups overlap. Choose a parent collection or its children, not both.')
        if used_objects & objects: raise ValueError('Objects are shared between split groups. Give each object one split-group owner.')
        used_collections.update(subtree);used_objects.update(objects)
    # A holdout cannot mask an object living directly under the scene root.
    if any(not helper(o) for o in scene.collection.objects):
        raise ValueError('Move geometry directly under Scene Collection into a collection before splitting.')
    for c in available-used_collections:
        direct=list(c.objects)
        if any(o.type=='LIGHT' for o in direct) and any(not helper(o) for o in direct):
            raise ValueError('Mixed light/geometry collection '+c.name+': assign it as a split group or move lights to a dedicated collection.')
    rendering.final_output()  # Validate saved path and naming before edits.
    return groups

def default_rule(scene,group,collection):
    s=config(scene)
    for g in s.groups:
        if g.collection and collection in descendants(g.collection):
            return 'VISIBLE' if g.key==group.key else 'INDIRECT' if group.key==s.base_key else 'HOLDOUT'
    if lighting(collection) or all(helper(o) for o in collection.objects) and not collection.children:
        return 'VISIBLE'
    return 'VISIBLE' if group.key==s.base_key else 'HOLDOUT'

def sync_rules(scene,reset=False):
    s=config(scene)
    if not s.owner_key: s.owner_key=uid()
    if not s.base_key and s.groups: s.base_key=s.groups[0].key
    collections=sorted(scene_collections(scene),key=lambda c:c.name.casefold())
    for g in s.groups:
        saved={r.collection:r.mode for r in g.rules if r.collection and r.mode!=r.default_mode} if not reset else {}
        g.rules.clear()
        for c in collections:
            r=g.rules.add();r.collection=c;r.default_mode=default_rule(scene,g,c);r.mode=saved.get(c,r.default_mode)

def signature(scene):
    s=config(scene)
    data={'base':s.base_key,'groups':[(g.key,g.label,g.collection.name if g.collection else None,g.samples,g.denoise,[(r.collection.name if r.collection else None,r.mode) for r in g.rules]) for g in ordered(s)],
          'collections':sorted((c.name,sorted(o.name for o in c.objects),sorted(x.name for x in c.children)) for c in scene_collections(scene)),
          'output':rendering.final_output()[1]}
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()

def apply_visibility(scene,view,group):
    modes={r.collection:r.mode for r in group.rules}
    def visit(lc):
        mode=modes.get(lc.collection,'VISIBLE')
        lc.exclude=mode=='EXCLUDED';lc.holdout=mode=='HOLDOUT';lc.indirect_only=mode=='INDIRECT'
        for child in lc.children: visit(child)
    for child in view.layer_collection.children: visit(child)
    # Collection restrictions propagate: refuse contradictory descendant overrides.
    def check(lc,ancestor='VISIBLE'):
        mode=modes.get(lc.collection,'VISIBLE')
        if ancestor!='VISIBLE' and mode not in {ancestor,'EXCLUDED'}:
            raise ValueError('Conflicting nested rules in '+group.label+': set parent '+lc.collection.name+' ancestors to Visible before overriding children.')
        for child in lc.children: check(child,mode if mode!='VISIBLE' else ancestor)
    for child in view.layer_collection.children: check(child)

def tagged(node,owner): node['neotrix_split_owner']=owner;return node

def build_tree(scene,layers,owner):
    current=scene.compositing_node_group
    tree=current.copy() if current else bpy.data.node_groups.new('Neotrix Split Compositor','CompositorNodeTree')
    tree.name='Neotrix Split Compositor'
    tree['neotrix_split_owner']=owner
    try:
        for node in list(tree.nodes):
            if node.get('neotrix_split_owner')==owner: tree.nodes.remove(node)
        # Keep existing artist nodes. Route the active group output through our merge.
        output=next((n for n in tree.nodes if n.type=='GROUP_OUTPUT' and n.is_active_output),None)
        if output is None: output=tagged(tree.nodes.new('NodeGroupOutput'),owner)
        if not any(x.item_type=='SOCKET' and x.in_out=='OUTPUT' and x.socket_type=='NodeSocketColor' for x in tree.interface.items_tree):
            tree.interface.new_socket(name='Image',in_out='OUTPUT',socket_type='NodeSocketColor')
        socket=next((x for x in output.inputs if x.type=='RGBA'),None)
        if socket is None: raise ValueError('Compositor needs an Image output socket.')
        file=tagged(tree.nodes.new('CompositorNodeOutputFile'),owner)
        file.label='NEOTRIX · SPLIT EXR';file.location=(640,-200)
        folder,path=rendering.final_output()
        file.directory=folder;file.file_name=os.path.basename(path)
        file.format.media_type='MULTI_LAYER_IMAGE';file.format.file_format='OPEN_EXR_MULTILAYER'
        file.format.color_depth='16';file.format.exr_codec='DWAA';file.format.color_mode='RGBA'
        file.save_as_render=False;file.use_file_extension=True
        def pack(name,source):
            file.file_output_items.new('RGBA' if source.type=='RGBA' else 'FLOAT',name)
            tree.links.new(source,file.inputs[name])
        previous=None
        for i,(g,layer) in enumerate(layers):
            node=tagged(tree.nodes.new('CompositorNodeRLayers'),owner)
            node.scene=scene;node.layer=layer.name;node.label=g.label;node.location=(-600,-i*340)
            for pass_name,out_name in [('Image','Image'),('Depth','Depth'),('Mist','Mist'),('Emission','Emission')]:
                source=node.outputs.get(out_name)
                if source is None: raise ValueError('Missing '+out_name+' pass in '+layer.name+'; available: '+str([o.name for o in node.outputs]))
                pack(g.label.strip()+'.'+pass_name,source)
            if previous is None: previous=node.outputs['Image']
            else:
                over=tagged(tree.nodes.new('CompositorNodeAlphaOver'),owner)
                over.location=(i*190-300,100);over.inputs['Factor'].default_value=1
                tree.links.new(previous,over.inputs['Background']);tree.links.new(node.outputs['Image'],over.inputs['Foreground'])
                previous=over.outputs[0]
        pack('MergedBeauty',previous)
        tree.links.new(previous,socket);output.location=(640,140)
        tree.nodes.active=file
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree);raise

def prepare(scene):
    validate(scene);sync_rules(scene)
    s=config(scene);owner=s.owner_key
    old=[v for v in scene.view_layers if v.get('neotrix_split_owner')==owner]
    created=[];tree=None;old_engine=scene.render.engine
    try:
        scene.render.engine='CYCLES'
        for g in ordered(s):
            v=scene.view_layers.new('NS · '+g.label)
            created.append((g,v));v['neotrix_split_owner']=owner;v['neotrix_split_group']=g.key
            v.samples=g.samples;v.cycles.use_denoising=g.denoise
            v.use_pass_z=True;v.use_pass_mist=True;v.use_pass_emit=True
            v.update_render_passes()
            apply_visibility(scene,v,g)
        tree=build_tree(scene,created,owner)
        folder,_=rendering.final_output();os.makedirs(folder,exist_ok=True)
    except Exception:
        if tree: bpy.data.node_groups.remove(tree)
        for _,v in created: scene.view_layers.remove(v)
        scene.render.engine=old_engine
        raise
    current=scene.compositing_node_group
    if not s.backup_saved:
        if not s.backup_tree: s.backup_tree=current
        s.backup_saved=True
        if s.backup_tree: s.backup_tree.use_fake_user=True
    scene.compositing_node_group=tree
    for v in old: scene.view_layers.remove(v)
    for g,v in created: v.name='NS · '+g.label
    # RNA node layer strings do not follow renames reliably.
    nodes=[n for n in tree.nodes if n.type=='R_LAYERS' and n.get('neotrix_split_owner')==owner]
    for node,(g,v) in zip(nodes,created): node.layer=v.name
    if current and current!=s.backup_tree and current.get('neotrix_split_owner')==owner and current.users==0:
        bpy.data.node_groups.remove(current)
    scene.render.engine='CYCLES';scene.render.film_transparent=True;scene.render.use_compositing=True
    s.prepared_signature=signature(scene)
    s.status=str(len(created))+' layers ready · one Multilayer EXR'
    return created

class NEOTRIX_OT_split_edit(bpy.types.Operator):
    bl_idname='relay.split_edit'
    bl_label='Edit split groups'
    bl_options={'REGISTER','UNDO'}
    action: EnumProperty(items=[(x,x,'') for x in ('ADD','AUTO','REMOVE','UP','DOWN','BASE','DEFAULTS')])
    collection: StringProperty(name='Collection')
    @classmethod
    def description(cls,context,properties):
        return {'AUTO':'Use top-level geometry collections as groups. Keep dedicated lights shared. First group becomes Base; nested groups can be added manually.', 'BASE':'Make the selected group Base and reset visibility rules to the Base/isolated presets.', 'DEFAULTS':'Reset visibility rules to Base/isolated presets; retain sample and denoising settings.'}.get(properties.action,'Add, remove or reorder split groups. Collection membership stays unchanged.')
    def invoke(self,context,event):
        if self.action=='ADD': return context.window_manager.invoke_props_dialog(self,width=400)
        return self.execute(context)
    def draw(self,context): self.layout.prop_search(self,'collection',bpy.data,'collections')
    def execute(self,context):
        s=config(context.scene);i=min(s.active_index,max(0,len(s.groups)-1))
        if self.action=='AUTO':
            if s.groups:
                self.report({'ERROR'},'Use Scene Collections is for an empty group list. Add individual collections to an existing setup.');return {'CANCELLED'}
            for c in context.scene.collection.children:
                if any(not helper(o) for o in c.all_objects):
                    g=s.groups.add();g.collection=c;g.label=''.join('_' if x in '/\\\n\r' else x for x in c.name)[:48];g.key=uid()
        elif self.action=='ADD':
            selected=bpy.data.collections.get(self.collection)
            if not selected or selected not in scene_collections(context.scene):
                self.report({'ERROR'},'Choose a collection in this scene.');return {'CANCELLED'}
            if any(g.collection==selected for g in s.groups):
                self.report({'ERROR'},'This collection is already a split group.');return {'CANCELLED'}
            g=s.groups.add();g.collection=selected;g.label=''.join('_' if c in '/\\\n\r' else c for c in selected.name)[:48];g.key=uid()
            s.active_index=len(s.groups)-1
        elif self.action=='REMOVE' and s.groups:
            removed=s.groups[i].key;s.groups.remove(i);s.active_index=max(0,i-1)
            if s.base_key==removed: s.base_key=s.groups[0].key if s.groups else ''
        elif self.action=='BASE' and s.groups: s.base_key=s.groups[i].key
        elif self.action in {'UP','DOWN'} and s.groups:
            j=i+(-1 if self.action=='UP' else 1)
            if 0<=j<len(s.groups): s.groups.move(i,j);s.active_index=j
        sync_rules(context.scene,reset=self.action in {'BASE','DEFAULTS'})
        s.status='Configuration changed · prepare to apply'
        return {'FINISHED'}

class NEOTRIX_UL_split_groups(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        icons=rendering.owner().ICONS
        key=('project','variant','shot','stage','version')[index%5]
        layout.label(text='',icon_value=icons[key].icon_id if icons else 0)
        layout.prop(item,'label',text='',emboss=False)
        if not item.collection: layout.label(text='Missing',icon='ERROR')
        elif item.key==data.base_key: layout.label(text='Base',icon='HOME')

class NEOTRIX_OT_split_prepare(bpy.types.Operator):
    bl_idname='relay.split_prepare'
    bl_label='Prepare Split Render'
    bl_description='Create/update managed Cycles view layers and a single packed EXR compositor. Base is bottom; selected row can be made Base. Does not render'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context): return JOB is None and rendering.JOB is None and (bpy.app.background or not bpy.app.is_job_running('RENDER'))
    def execute(self,context):
        try: prepare(context.scene)
        except Exception as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        self.report({'INFO'},config(context.scene).status);return {'FINISHED'}

def cleanup():
    global JOB
    if JOB is None: return
    job=JOB;JOB=None
    for handlers in (bpy.app.handlers.render_complete,bpy.app.handlers.render_cancel):
        for fn in (render_done,render_cancelled):
            if fn in handlers: handlers.remove(fn)
    def release():
        if not bpy.app.background and bpy.app.is_job_running('RENDER'): return .1
        source=job['source']
        try:
            for window in bpy.context.window_manager.windows:
                if window.scene==job['scene']: window.scene=source
            source.frame_set(job['frame'],subframe=job['subframe'])
            config(source).status=job.get('message','Split render finished')
        except ReferenceError: pass
        tree=job['tree']
        bpy.data.scenes.remove(job['scene'])
        if tree.users==0: bpy.data.node_groups.remove(tree)
        return None
    if bpy.app.background: release()
    else: bpy.app.timers.register(release,first_interval=.5)

def render_done(*args):
    if JOB: JOB['done']=True
def render_cancelled(*args):
    if JOB: JOB['done']=True;JOB['stop']=True;JOB['message']='Split render cancelled'

def create_job(source,animation):
    validate(source)
    if signature(source)!=config(source).prepared_signature: raise ValueError('Configuration or save version changed. Click Prepare Split Render first.')
    if not source.camera: raise ValueError('Set an active camera before rendering.')
    owner=config(source).owner_key
    if not source.compositing_node_group or source.compositing_node_group.get('neotrix_split_owner')!=owner:
        raise ValueError('Managed compositor is missing. Prepare Split Render again.')
    scene=source.copy();scene.name='Neotrix temporary split render'
    tree=source.compositing_node_group.copy();scene.compositing_node_group=tree
    for node in tree.nodes:
        if node.type=='R_LAYERS' and node.get('neotrix_split_owner')==owner: node.scene=scene
        # Existing artist outputs stay in the graph but must not write extra files.
        if node.type=='OUTPUT_FILE' and node.get('neotrix_split_owner')!=owner: node.mute=True
    for v in scene.view_layers: v.use=v.get('neotrix_split_owner')==owner
    scene.render.engine='CYCLES';scene.render.film_transparent=True;scene.render.use_compositing=True
    scene.render.use_sequencer=False;scene.render.use_single_layer=False
    scene.render.save_output=False
    scene.update_tag();tree.update_tag()
    scene.cycles.use_denoising=any(g.denoise for g in config(source).groups)
    frames=list(range(source.frame_start,source.frame_end+1,source.frame_step)) if animation else [source.frame_current]
    return {'source':source,'scene':scene,'tree':tree,'frames':frames,'next':0,'frame':source.frame_current,'subframe':source.frame_subframe,'done':True,'stop':False,'message':'Split render finished'}

class NEOTRIX_OT_split_render(bpy.types.Operator):
    bl_idname='relay.split_render'
    bl_label='Render Split'
    bl_description='Render managed split layers and merged beauty into one EXR per frame. Uses a temporary scene; switches to Compositing. Escape cancels the current render; Stop After Frame stops the queue'
    animation: BoolProperty(default=False)
    @classmethod
    def poll(cls,context): return JOB is None and rendering.JOB is None and (bpy.app.background or not bpy.app.is_job_running('RENDER'))
    def execute(self,context):
        global JOB
        try: JOB=create_job(context.scene,self.animation)
        except Exception as exc: self.report({'ERROR'},str(exc));return {'CANCELLED'}
        if bpy.app.background:
            try:
                for frame in JOB['frames']:
                    JOB['scene'].frame_set(frame)
                    with bpy.context.temp_override(scene=JOB['scene'],view_layer=next(v for v in JOB['scene'].view_layers if v.use)):
                        result=bpy.ops.render.render('EXEC_DEFAULT',scene=JOB['scene'].name,write_still=False)
                    if 'CANCELLED' in result: raise RuntimeError('Split render cancelled')
            except Exception as exc:
                JOB['message']=str(exc);self.report({'ERROR'},str(exc));cleanup();return {'CANCELLED'}
            cleanup();return {'FINISHED'}
        try:
            rendering.switch_to_compositing(context.window)
            bpy.app.handlers.render_complete.append(render_done);bpy.app.handlers.render_cancel.append(render_cancelled)
            window=context.window
            # Invoke renders between UI event cycles, rather than recursively from
            # a modal event handler. Render Complete only signals; cleanup waits.
            def step():
                if JOB is None: return None
                if not JOB['done'] or bpy.app.is_job_running('RENDER'): return .2
                if JOB['stop'] or JOB['next']>=len(JOB['frames']): cleanup();return None
                try:
                    frame=JOB['frames'][JOB['next']];JOB['next']+=1;JOB['done']=False
                    JOB['scene'].frame_set(frame)
                    config(JOB['source']).status='Rendering frame '+str(frame)+' · '+str(JOB['next'])+'/'+str(len(JOB['frames']))
                    result=bpy.ops.render.render('INVOKE_DEFAULT',scene=JOB['scene'].name,animation=True,frame_start=frame,frame_end=frame,write_still=False)
                    if 'CANCELLED' in result: render_cancelled()
                except Exception as exc:
                    JOB['message']='Split render failed: '+str(exc);cleanup();return None
                return .2
            bpy.app.timers.register(step,first_interval=.1)
            return {'FINISHED'}
        except Exception as exc:
            cleanup();self.report({'ERROR'},str(exc));return {'CANCELLED'}
class NEOTRIX_OT_split_stop(bpy.types.Operator):
    bl_idname='relay.split_stop'
    bl_label='Stop After Frame'
    bl_description='Finish the frame currently rendering, then stop the split animation queue'
    def execute(self,context):
        if JOB: JOB['stop']=True;JOB['message']='Split render stopped after frame'
        return {'FINISHED'}

class NEOTRIX_OT_split_restore(bpy.types.Operator):
    bl_idname='relay.split_restore_compositor'
    bl_label='Restore Original Compositor'
    bl_description='Restore the compositor saved before the first split preparation. Keep split configuration and layers for reuse'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context): return JOB is None and rendering.JOB is None and config(context.scene).backup_saved and (bpy.app.background or not bpy.app.is_job_running('RENDER'))
    def execute(self,context):
        s=config(context.scene);context.scene.compositing_node_group=s.backup_tree
        s.prepared_signature='';s.status='Original compositor restored · prepare to rebuild splits'
        return {'FINISHED'}

def draw(layout,context):
    s=config(context.scene);row=layout.row(align=True)
    row.prop(s,'expanded',text='',icon='TRIA_DOWN' if s.expanded else 'TRIA_RIGHT',emboss=False)
    icons=rendering.owner().ICONS
    row.label(text='SPLIT RENDER',icon_value=icons['shot'].icon_id if icons else 0)
    if not s.expanded: return
    box=layout.box();controls=box.column();controls.enabled=JOB is None
    if not s.groups: controls.operator('relay.split_edit',text='Use Scene Collections',icon='OUTLINER_COLLECTION').action='AUTO'
    controls.template_list('NEOTRIX_UL_split_groups','',s,'groups',s,'active_index',rows=3)
    row=controls.row(align=True)
    for action,icon in [('ADD','ADD'),('REMOVE','REMOVE'),('UP','TRIA_UP'),('DOWN','TRIA_DOWN')]: row.operator('relay.split_edit',text='',icon=icon).action=action
    row.operator('relay.split_edit',text='Set Base',icon='HOME').action='BASE'
    if s.groups:
        g=s.groups[min(s.active_index,len(s.groups)-1)]
        controls.prop(g,'collection',text='Collection')
        row=controls.row();row.prop(g,'samples');row.prop(g,'denoise')
        base=next((g.label for g in s.groups if g.key==s.base_key),'Not selected')
        controls.label(text='Base: '+base+' · bottom of stack',icon='HOME')
        row=controls.row(align=True)
        row.prop(s,'show_rules',text='Visibility Rules',icon='TRIA_DOWN' if s.show_rules else 'TRIA_RIGHT',emboss=False)
        row.operator('relay.split_edit',text='',icon='FILE_REFRESH').action='DEFAULTS'
        if s.show_rules:
            controls.label(text='Rules for: '+g.label)
            for rule in g.rules:
                row=controls.row(align=True);row.label(text=rule.collection.name if rule.collection else 'Missing')
                row.prop(rule,'mode',text='')
            controls.label(text='Parent restrictions affect children.',icon='INFO')
    controls.operator('relay.split_prepare',icon='CHECKMARK')
    if s.backup_saved: controls.operator('relay.split_restore_compositor',icon='LOOP_BACK')
    buttons=box.column(align=True);buttons.enabled=bool(s.prepared_signature) and JOB is None and rendering.JOB is None
    buttons.operator('relay.split_render',text='Render Split Frame',icon='RENDER_STILL').animation=False
    buttons.operator('relay.split_render',text='Render Split Animation',icon='RENDER_ANIMATION').animation=True
    if JOB: box.operator('relay.split_stop',icon='CANCEL')
    box.label(text=s.status,icon='INFO')
    box.label(text='EXR · DWAA · Half Float')
    box.label(text='Merged beauty + separate layers')

CLASSES=(NEOTRIX_SplitRule,NEOTRIX_SplitGroup,NEOTRIX_SplitSettings,NEOTRIX_OT_split_edit,NEOTRIX_UL_split_groups,NEOTRIX_OT_split_prepare,NEOTRIX_OT_split_render,NEOTRIX_OT_split_stop,NEOTRIX_OT_split_restore)
