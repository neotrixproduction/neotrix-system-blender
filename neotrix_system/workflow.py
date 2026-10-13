"""Create/version-save and discussion operators used by both interfaces."""
import os
import re
import getpass
import importlib
import textwrap
from datetime import datetime
import bpy
from bpy.props import StringProperty, EnumProperty, BoolProperty
from . import storage
from .indexer import parse, STAGES

def owner():
    return importlib.import_module(__package__)

def root():
    return bpy.path.abspath(owner().prefs().root)

def selected(context):
    s=context.window_manager.neotrix_picker
    return s.files[s.active_index].path if s.files and s.active_index<len(s.files) else ''

def load_discussion(context, filepath=None):
    s=context.window_manager.neotrix_picker
    filepath=filepath if filepath is not None else selected(context)
    s.comments.clear()
    s.comments_path=filepath
    s.comment_error=''
    if not filepath:
        return
    try:
        records, errors=storage.load_comments(root(),filepath)
        for item in records:
            c=s.comments.add()
            c.name=item['id']
            c.author=item['author']
            c.message=item['text']
            c.created=datetime.fromisoformat(item['created']).astimezone().strftime('%d %b %Y · %H:%M')
        s.comment_error='{} comment record(s) could not be read'.format(len(errors)) if errors else ''
    except (OSError,ValueError) as exc:
        s.comment_error=str(exc)
    owner().redraw()

class NEOTRIX_OT_save(bpy.types.Operator):
    bl_idname='relay.save_version'
    bl_label='Create / Save As'
    bl_description='Save the current scene to a managed project/variant/shot, creating folders automatically'
    mode: EnumProperty(items=[('NEW','Create / Save As','Save current scene under a project and shot'),('NEXT','Save Next Version','Version the currently opened Relay scene')],default='NEW')
    project: StringProperty(name='Project',description='Existing or new project name')
    code: StringProperty(name='Project code',default='PRJ')
    variant: StringProperty(name='Variant / Film',default='Main')
    shot: StringProperty(name='Shot number',default='0010')
    stage: EnumProperty(name='Production stage',items=[(s[0],s[0],'') for s in STAGES],default='Layout')
    note: StringProperty(name='Save comment',description='Optional note attached to the new file')
    compress: BoolProperty(name='Compress Blender file',default=True)

    def target(self):
        if self.mode=='NEXT':return storage.current_destination(root(),bpy.data.filepath,self.stage)
        return storage.destination(root(),self.project,self.code,self.variant,self.shot,self.stage)

    def invoke(self,context,event):
        ns=owner();s=context.window_manager.neotrix_picker
        self.project=s.project if s.project not in {'__ALL__',None,'Root files'} else ''
        self.variant=s.variant if s.variant not in {'__ALL__',None} else 'Main'
        if bpy.data.filepath and storage.contained(root(),bpy.data.filepath):
            record=parse(bpy.data.filepath,root())
            self.project=record['project'] if not self.project else self.project
            self.variant=record['variant'] if self.variant=='Main' else self.variant
            m=re.match(r'([A-Za-z][A-Za-z0-9]*)_',record['name'])
            if m:self.code=m.group(1).upper()
            if record['shot']!='Unspecified':self.shot=record['shot'].split()[-1]
            inferred=ns.file_stage(record)
            if inferred in {x[0] for x in STAGES}:self.stage=inferred
        elif self.project:
            letters=''.join(word[0] for word in self.project.split() if word[0].isalpha()).upper()
            self.code=(letters+'PRJ')[:3]
        return context.window_manager.invoke_props_dialog(self,width=520,confirm_text='Save new version',title='Neotrix · '+('Save next version' if self.mode=='NEXT' else 'Create / Save As'))

    def draw(self,context):
        layout=self.layout
        layout.label(text='CURRENT SCENE → NEW SAVED VERSION',icon='FILE_BLEND')
        layout.label(text=os.path.basename(bpy.data.filepath) if bpy.data.filepath else 'Unsaved scene')
        if self.mode=='NEW':
            state=context.window_manager.neotrix_picker
            layout.prop_search(self,'project',state,'projects',text='Choose existing')
            layout.prop(self,'project',text='Project / new name')
            row=layout.row();row.prop(self,'code');row.prop(self,'variant')
            layout.prop(self,'shot')
        layout.prop(self,'stage')
        box=layout.box()
        try:
            path=self.target()
            box.label(text=os.path.basename(path),icon='FILE_NEW')
            for line in textwrap.wrap(os.path.relpath(os.path.dirname(path),root()),width=67):box.label(text=line)
            box.label(text='Folders are created automatically. Earlier versions stay intact.')
        except (OSError,ValueError) as exc:
            box.label(text=str(exc),icon='ERROR')
        layout.prop(self,'note')
        layout.prop(self,'compress')

    def execute(self,context):
        ns=owner()
        try:
            reservation=storage.reserve_current(root(),bpy.data.filepath,self.stage) if self.mode=='NEXT' else storage.reserve(root(),self.project,self.code,self.variant,self.shot,self.stage)
            with reservation as path:
                context.scene['neotrix_stage']=self.stage
                result=bpy.ops.wm.save_as_mainfile(filepath=path,check_existing=True,compress=self.compress,relative_remap=True)
                if result!={'FINISHED'} or not os.path.isfile(path):raise OSError('Blender did not finish saving the file')
            context.scene['neotrix_stage']=self.stage
            ns.TAGS[os.path.normcase(path)]=self.stage
            try:
                with open(ns.config_file(),'w',encoding='utf8') as handle:
                    __import__('json').dump(ns.TAGS,handle,indent=2)
            except OSError as exc: self.report({'WARNING'},'Saved file; local stage tag unavailable: '+str(exc))
            ns.PENDING_SELECTION=path
            # Clear filters before refreshing so the new save always appears.
            bpy.ops.relay.reset_picker()
            if self.note.strip():
                try:storage.add_comment(root(),path,ns.prefs().author,self.note)
                except (OSError,ValueError) as exc:self.report({'WARNING'},'File saved; comment could not be saved: '+str(exc))
            ns.start_scan()
            self.report({'INFO'},'Saved '+os.path.basename(path))
        except (OSError,ValueError,RuntimeError) as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        return {'FINISHED'}

class NEOTRIX_OT_comment(bpy.types.Operator):
    bl_idname='relay.add_comment'
    bl_label='Add file comment'
    filepath: StringProperty()
    author: StringProperty(name='Your name')
    message: StringProperty(name='Comment',maxlen=12000)
    def invoke(self,context,event):
        if not self.filepath:self.filepath=selected(context)
        self.author=owner().prefs().author or getpass.getuser()
        return context.window_manager.invoke_props_dialog(self,width=520,confirm_text='Add comment')
    def draw(self,context):
        self.layout.label(text=os.path.basename(self.filepath),icon='FILE_BLEND')
        self.layout.prop(self,'author')
        self.layout.prop(self,'message')
        self.layout.label(text='Stored beside this file and visible to the team.')
    def execute(self,context):
        try:
            storage.add_comment(root(),self.filepath,self.author,self.message)
            owner().prefs().author=self.author.strip()
            load_discussion(context,self.filepath)
            owner().redraw()
        except (OSError,ValueError) as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        return {'FINISHED'}

class NEOTRIX_OT_comments(bpy.types.Operator):
    bl_idname='relay.view_comments'
    bl_label='File discussion'
    filepath: StringProperty()
    def invoke(self,context,event):
        if not self.filepath:self.filepath=selected(context)
        load_discussion(context,self.filepath)
        return context.window_manager.invoke_props_dialog(self,width=540)
    def draw(self,context):
        self.layout.label(text=os.path.basename(self.filepath),icon='FILE_BLEND')
        draw_comments(self.layout,context,self.filepath,limit=30,width=66)
    def execute(self,context):return {'FINISHED'}

class NEOTRIX_OT_refresh_comments(bpy.types.Operator):
    filepath: StringProperty()
    bl_idname='relay.refresh_comments'
    bl_label='Refresh comments'
    def execute(self,context):
        load_discussion(context,self.filepath or context.window_manager.neotrix_picker.comments_path or selected(context))
        return {'FINISHED'}

def draw_comments(layout,context,filepath,limit=4,width=35):
    state=context.window_manager.neotrix_picker
    if state.comment_error:layout.label(text=state.comment_error,icon='ERROR')
    comments=list(state.comments)[-limit:] if state.comments_path==filepath else []
    if not comments:layout.label(text='No comments yet. Start the discussion.',icon='COMMUNITY')
    for comment in comments:
        box=layout.box()
        box.label(text=comment.author,icon='USER')
        box.label(text=comment.created)
        for paragraph in comment.message.splitlines() or ['']:
            for line in textwrap.wrap(paragraph,width=width) or ['']:box.label(text=line)
    if len(state.comments)>limit:layout.label(text='Showing latest {} of {} comments'.format(limit,len(state.comments)))
    row=layout.row(align=True)
    row.operator('relay.add_comment',text='Add comment',icon='ADD').filepath=filepath
    row.operator('relay.refresh_comments',text='',icon='FILE_REFRESH').filepath=filepath

CLASSES=(NEOTRIX_OT_save,NEOTRIX_OT_comment,NEOTRIX_OT_comments,NEOTRIX_OT_refresh_comments)
