"""Real two-pass GPU Gaussian blur of the viewport, captured once per browser."""
import gpu
from gpu_extras.batch import batch_for_shader

class BlurredBackdrop:
    def __init__(self):
        self.output=None
        self.size=None
        self.failed=''
        self.shader=None

    def build_shader(self):
        interface=gpu.types.GPUStageInterfaceInfo('neotrix_blur_interface')
        interface.smooth('VEC2','uv')
        info=gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0,'VEC2','position')
        info.vertex_in(1,'VEC2','texCoord')
        info.vertex_out(interface)
        info.fragment_out(0,'VEC4','fragColor')
        info.sampler(0,'FLOAT_2D','sourceImage')
        info.push_constant('VEC2','direction')
        info.vertex_source('void main(){uv=texCoord;gl_Position=vec4(position,0.0,1.0);}')
        info.fragment_source('''
            void main(){
                vec4 sum=vec4(0.0); float total=0.0;
                for(int i=-12;i<=12;i++){
                    float weight=exp(-float(i*i)/50.0);
                    sum+=texture(sourceImage,clamp(uv+direction*float(i),vec2(0.0),vec2(1.0)))*weight;
                    total+=weight;
                }
                fragColor=vec4((sum/total).rgb,1.0);
            }
        ''')
        self.shader=gpu.shader.create_from_info(info)

    def capture(self):
        viewport=gpu.state.viewport_get()
        vx,vy,w,h=viewport
        if self.size==(w,h) and self.output:return True
        if self.failed:return False
        first=None
        try:
            self.free()
            self.build_shader()
            pixels=gpu.state.active_framebuffer_get().read_color(vx,vy,w,h,4,0,'FLOAT')
            original=gpu.types.GPUTexture((w,h),format='RGBA32F',data=pixels)
            bw,bh=max(1,w//3),max(1,h//3)
            first=gpu.types.GPUOffScreen(bw,bh,format='RGBA16F')
            self.output=gpu.types.GPUOffScreen(bw,bh,format='RGBA16F')
            batch=batch_for_shader(self.shader,'TRI_FAN',{'position':[(-1,-1),(1,-1),(1,1),(-1,1)],'texCoord':[(0,0),(1,0),(1,1),(0,1)]})
            gpu.state.blend_set('NONE')
            with first.bind():
                gpu.state.viewport_set(0,0,bw,bh)
                self.shader.bind()
                self.shader.uniform_sampler('sourceImage',original)
                self.shader.uniform_float('direction',(5.0/w,0))
                batch.draw(self.shader)
            with self.output.bind():
                gpu.state.viewport_set(0,0,bw,bh)
                self.shader.bind()
                self.shader.uniform_sampler('sourceImage',first.texture_color)
                self.shader.uniform_float('direction',(0,5.0/h))
                batch.draw(self.shader)
            self.size=(w,h)
            return True
        except Exception as exc:
            self.failed=str(exc)
            self.free()
            print('Neotrix: blur unavailable, using opaque cards:',self.failed)
            return False
        finally:
            if first:first.free()
            gpu.state.viewport_set(*viewport)
            gpu.state.blend_set('ALPHA')

    def draw(self,w,h):
        if not self.capture():return False
        shader=gpu.shader.from_builtin('IMAGE')
        batch=batch_for_shader(shader,'TRI_FAN',{'pos':[(0,0),(w,0),(w,h),(0,h)],'texCoord':[(0,0),(1,0),(1,1),(0,1)]})
        shader.bind()
        shader.uniform_sampler('image',self.output.texture_color)
        batch.draw(shader)
        return True

    def free(self):
        if self.output:
            self.output.free()
            self.output=None
        self.size=None
