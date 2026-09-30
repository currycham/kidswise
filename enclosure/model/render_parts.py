"""Inside views of the printed shells with every part in place (for checking the layout)."""
import os,json,numpy as np,trimesh
from PIL import Image,ImageDraw,ImageFont
from build_shell import box,cyl_y,OUT,GROUND,PARTS,LENS_X,I
L=json.load(open(os.path.join(OUT,'layout.json')))
def load(n):
    m=trimesh.load(os.path.join(OUT,n+'.stl')); m.apply_translation([0,0,GROUND]); return m
front,back=load('mumi_front_shell'),load('mumi_back_shell')
SHELL=(236,226,206); COL={'board':(40,70,120),'board_back':(90,110,140),'lens':(20,20,20),'battery':(170,180,195),
 'speaker':(60,60,65),'charger':(40,110,190),'switch':(230,230,230),'magnet':(200,205,212)}
def part_meshes():
    out=[]
    for k,(x0,x1,y0,y1,z0,z1) in PARTS.items():
        m=cyl_y(LENS_X,0,5.0,y0,y1) if k=='lens' else box(x0,x1,y0,y1,z0,z1)
        out.append((m,COL[k]))
    for x in (-9.0,9.0): out.append((cyl_y(x,8.0,5.0,17.1,20.3),COL['magnet']))
    return out
def scene(items):
    ms=[m for m,_ in items]; cols=np.vstack([np.tile(c,(len(m.faces),1)) for m,c in items])
    return trimesh.util.concatenate(ms),cols
def render(items,d,up=(0,0,1),R=560,zoom=1.0,centre=None):
    m,cols=scene(items); d=np.asarray(d,float); d/=np.linalg.norm(d); up=np.asarray(up,float)
    right=np.cross(d,up); right/=np.linalg.norm(right); u=np.cross(right,d)
    c=np.array(centre) if centre is not None else m.bounds.mean(0); r=np.linalg.norm(m.extents)/2*0.9/zoom
    s=np.linspace(-r,r,R*2); X,Y=np.meshgrid(s,-s)
    O=c-d*400+X.reshape(-1,1)*right+Y.reshape(-1,1)*u
    loc,ri,ti=m.ray.intersects_location(O,np.tile(d,(len(O),1)),multiple_hits=False)
    n=m.face_normals[ti]; n[(n*d).sum(1)>0]*=-1
    Lg=-d+0.8*u+0.5*right; Lg/=np.linalg.norm(Lg)
    sh=0.45+0.55*np.clip((n*Lg).sum(1),0,1)
    img=np.full((4*R*R,3),250.0); img[ri]=cols[ti]*sh[:,None]
    return Image.fromarray(img.reshape(2*R,2*R,3).astype(np.uint8)).resize((R,R),Image.LANCZOS)
pm=part_meshes()
fparts=[p for p in pm if p[0].bounds[1][1]<1.3]; bparts=[p for p in pm if p[0].bounds[1][1]>=1.3]
cut=lambda m,x: trimesh.boolean.intersection([m,box(-60,x,-60,60,-60,60)],engine='manifold')
views=[
 ('前壳内部（从后往前看）',render([(front,SHELL)]+fparts,(0,-1,0.0))),
 ('后壳内部（从前往后看）',render([(back,SHELL)]+bparts,(0,1,0.0))),
 ('侧面剖开（沿身体中线）',render([(cut(front,0.0),SHELL),(cut(back,0.0),(222,208,182))]+[(cut(m,0.0),c) for m,c in pm if m.bounds[0][0]<0],(-1,0,0))),
 ('分开的前后壳',render([(front.copy().apply_translation([0,-22,0]),SHELL),(back.copy().apply_translation([0,22,0]),(222,208,182))]+
     [(m.copy().apply_translation([0,-22 if m.bounds[1][1]<1.3 else 22,0]),c) for m,c in pm],(1.0,0.55,-0.35),zoom=0.8)),
]
W=Image.new('RGB',(560*4,610),(250,250,250)); dr=ImageDraw.Draw(W)
font=None
for f in ('/System/Library/Fonts/Hiragino Sans GB.ttc','/System/Library/Fonts/STHeiti Medium.ttc','/System/Library/Fonts/PingFang.ttc'):
    try: font=ImageFont.truetype(f,22); break
    except Exception: pass
for i,(t,im) in enumerate(views): W.paste(im,(i*560,50)); dr.text((i*560+20,12),t,fill=(40,40,40),font=font)
W.save(os.path.join(OUT,'layout_preview.png'))
