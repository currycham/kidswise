"""Soft 'vinyl toy' preview renders of the appearance model (front, side, back, three-quarter)."""
import os,sys,trimesh,numpy as np
from PIL import Image
from mumi_model import C_Z,EYE_X,EYE_D
S=78.0
m=trimesh.load(sys.argv[1]); out=sys.argv[2]
zc=C_Z*S; Rb=0.49*S
cream=np.array([248,238,221.]); stripe=np.array([232,158,88.]); black=np.array([24,20,22.])
blush=np.array([248,160,164.]); brown=np.array([88,52,40.]); footc=np.array([244,232,212.])
ss=lambda e0,e1,x: np.clip((x-e0)/(e1-e0),0,1)**2*(3-2*np.clip((x-e0)/(e1-e0),0,1))
from scipy import ndimage
from mumi_model import profile
# paint map taken from the front view of the approved three-view (ip/mumi_v2_4_jimeng_sanshi.png)
_src=np.asarray(Image.open(os.path.join(os.path.dirname(__file__),'../../ip/mumi_v2_4_jimeng_sanshi.png')).convert('RGB')).astype(float)
IMG_C=(313.0,756.9); IMG_R=267.6          # body silhouette circle in that image (pixels)
RMAX=profile()[:,0].max()*S               # body silhouette radius in the model (mm)
_r,_g,_b=_src[...,0],_src[...,1],_src[...,2]
_ws=ndimage.grey_closing(np.clip((_g-_b-22)/40,0,1),size=(9,9))
_ws=ndimage.gaussian_filter(_ws,2.0)
W_STRIPE=np.clip((_ws-0.18)/0.3,0,1)
W_BLUSH=ndimage.gaussian_filter(np.clip(((_r-_g)-(_g-_b)*1.3-12)/35,0,1),1.0)
W_DARK=ndimage.gaussian_filter((_src.sum(2)<260).astype(float),0.8)
def sample(W,x,z):
    px=IMG_C[0]+x/RMAX*IMG_R; py=IMG_C[1]-z/RMAX*IMG_R
    return ndimage.map_coordinates(W,[py,px],order=1,mode='constant')
def colour(p):
    x,y,z=p[:,0],p[:,1],p[:,2]-zc
    col=np.tile(cream,(len(p),1))
    rho=np.hypot(x,z)/RMAX
    front=ss(0.0,-0.06*S,y)*(1-ss(0.86,0.94,rho))          # paint only the face, not the rim shading
    mouth=(np.abs(x)<0.13*S)&(z<-0.04*S)&(z>-0.15*S)
    ws=sample(W_STRIPE,x,z)*front; cheek=np.minimum(np.hypot(x-0.2265*S,z+0.1115*S),np.hypot(x+0.2265*S,z+0.1115*S))<0.09*S
    wb=sample(W_BLUSH,x,z)*front*cheek; wd=sample(W_DARK,x,z)*front*mouth
    col=col*(1-ws[:,None])+stripe*ws[:,None]
    col=col*(1-wb[:,None])+blush*wb[:,None]
    col=col*(1-wd[:,None])+brown*wd[:,None]
    col[p[:,2]<(C_Z-0.49)*S*0.95]=footc
    for sx in (-1,1):
        d=np.hypot(x-sx*EYE_X*S,z); eye=(d<EYE_D/2*S+0.1)&(y<-0.2*S); col[eye]=black
        a_=EYE_D/2*S; ex,ez=x-sx*EYE_X*S,z
        for hx,hz,hr in [(0.30,0.34,0.24),(0.52,0.02,0.09)]:
            col[eye&(np.hypot(ex-hx*a_,ez-hz*a_)<hr*a_)]=[255,255,255]
    return col
def render(d,up=(0,0,1),R=520,SS=2):
    N=R*SS; d=np.asarray(d,float); d/=np.linalg.norm(d); up=np.asarray(up,float)
    right=np.cross(d,up); right/=np.linalg.norm(right); u=np.cross(right,d)
    c=m.bounds.mean(0); r=np.linalg.norm(m.extents)/2*0.95
    s=np.linspace(-r,r,N); X,Y=np.meshgrid(s,-s)
    O=c-d*3*r+X.reshape(-1,1)*right+Y.reshape(-1,1)*u
    loc,ri,ti=m.ray.intersects_location(O,np.tile(d,(len(O),1)),multiple_hits=False)
    n=trimesh.triangles.barycentric_to_points  # placeholder to keep namespace tidy
    # smooth shading: interpolate vertex normals
    tri=m.triangles[ti]; bc=trimesh.triangles.points_to_barycentric(tri,loc)
    vn=m.vertex_normals[m.faces[ti]]; n=(vn*bc[:,:,None]).sum(1); n/=np.linalg.norm(n,axis=1)[:,None]
    key=-d+0.9*u+0.6*right; key/=np.linalg.norm(key)
    wrap=np.clip(((n*key).sum(1)+0.4)/1.4,0,1)            # soft wrap-around key light
    sky=0.5+0.5*n[:,2]                                    # hemisphere fill
    fres=(1-np.clip(-(n*d).sum(1),0,1))**3                # soft rim light
    col=colour(loc); isblack=(col.sum(1)<150)|(col.sum(1)>=765)
    H=key-d; H/=np.linalg.norm(H); spec=np.clip((n*H).sum(1),0,1)
    sheen=spec**18*28*(~isblack)+spec**90*230*isblack      # matte vinyl sheen; glossy eyes
    light=0.62+0.34*wrap+0.12*sky
    sh=col*light[:,None]+(sheen+fres*22*(~isblack))[:,None]
    bg=np.array([252,250,247.]); img=np.tile(bg,(N*N,1)); img[ri]=np.clip(sh,0,255)
    im=Image.fromarray(img.reshape(N,N,3).astype(np.uint8))
    return im.resize((R,R),Image.LANCZOS)
views=[(0,1,0),(1,0,0),(0,-1,0),(0.75,1,-0.3)]
W=Image.new('RGB',(520*4,520),(252,250,247))
for i,v in enumerate(views): W.paste(render(v),(i*520,0))
W.save(out)
