"""Printable front and back shells of Mumi v2 (Jupiter), with mounts for every part inside.

Body coordinates (mm): origin at the body centre, x to the viewer's right, y front (-) to back (+), z up.
Outputs (standing orientation, ground at z=0) go to enclosure/print/.
"""
import os,json,numpy as np,trimesh
from scipy.ndimage import gaussian_filter1d
from mumi_model import *
from build_full import S,GROUND,appearance,eye_centre_y,EYE_H

HERE=os.path.dirname(os.path.abspath(__file__))
OUT=os.path.join(HERE,'../print'); os.makedirs(OUT,exist_ok=True)
U=lambda a,b: trimesh.boolean.union([a,b],engine='manifold')
D=lambda a,b: trimesh.boolean.difference([a,b],engine='manifold')
I=lambda a,b: trimesh.boolean.intersection([a,b],engine='manifold')

WALL=1.8                       # shell wall (mm)
Y_SPLIT=0.015*S                # front/back split: bottom of the macaron groove
LENS_IN=2.0                    # lens tip sits this far behind the face surface
LENS_H=9.0                     # lens tip height above the PCB front face
BOARD_BACK=6.4                 # tallest parts on the back of the board (VIN terminal)

def box(x0,x1,y0,y1,z0,z1):
    b=trimesh.creation.box(extents=[x1-x0,y1-y0,z1-z0]); b.apply_translation([(x0+x1)/2,(y0+y1)/2,(z0+z1)/2]); return b
def cyl_y(x,z,r,y0,y1,sections=64):
    c=trimesh.creation.cylinder(radius=r,height=y1-y0,sections=sections)
    c.apply_transform(trimesh.transformations.rotation_matrix(np.pi/2,[1,0,0])); c.apply_translation([x,(y0+y1)/2,z]); return c
def cyl_z(x,y,r,z0,z1,sections=48):
    c=trimesh.creation.cylinder(radius=r,height=z1-z0,sections=sections); c.apply_translation([x,y,(z0+z1)/2]); return c

def cavity(offset):
    """Inside of the body: the outer profile moved inwards by `offset` mm, revolved."""
    P=profile()*S
    # across the macaron band, follow the deepest point of the groove so the wall never gets thinner there
    band=(P[:,1]>FA*S-1)&(P[:,1]<BA*S+1); r=P[:,0].copy()
    for i in np.where(band)[0]:
        r[i]=P[band&(np.abs(P[:,1]-P[i,1])<2.5),0].min()
    P=np.c_[r,P[:,1]]
    d=np.r_[0,np.cumsum(np.linalg.norm(np.diff(P,axis=0),axis=1))]; s=np.linspace(0,d[-1],1600)
    P=np.c_[np.interp(s,d,P[:,0]),np.interp(s,d,P[:,1])]
    T=np.gradient(P,axis=0); T/=np.linalg.norm(T,axis=1)[:,None]
    Q=P+offset*np.c_[-T[:,1],T[:,0]]
    Q=np.c_[gaussian_filter1d(Q[:,0],6),gaussian_filter1d(Q[:,1],6)]
    Q=Q[Q[:,0]>0.3]; Q=np.vstack([[0,P[0,1]+offset],Q,[0,P[-1,1]-offset]])
    m=trimesh.creation.revolve(Q,sections=360)
    m.apply_transform(np.array([[1,0,0,0],[0,0,1,0],[0,1,0,0],[0,0,0,1.]]))
    if m.volume<0: m.invert()
    return m

# ---------------- where everything goes ----------------
LENS_X=-EYE_X*S                          # camera in the viewer's-left eye
FACE_AT_EYE=eye_centre_y(-EYE_X)
Y_PCB=FACE_AT_EYE+LENS_IN+LENS_H         # PCB front face
BX,BZ=LENS_X+6.3,0.0                     # board centre (board turned 90°: lens is 6.3 mm left of centre)
MIC=(BX+9.0,-14.5)                       # MEMS mic on the PCB front, after the 90° turn
HOLES=[(BX+sx*17.5,BZ+sz*17.5) for sx in (-1,1) for sz in (-1,1)]
SCREWS=[(29*np.cos(np.radians(a)),29*np.sin(np.radians(a))) for a in (68,112,240,300)]   # shell screws (x,z): clear of board and battery

PARTS={   # name: (x0,x1,y0,y1,z0,z1)  keep-out boxes of the real parts
 'board':      (BX-21,BX+21, Y_PCB,Y_PCB+1.6, -21,21),
 'board_back': (BX-19,BX+19, Y_PCB+1.6,Y_PCB+1.6+BOARD_BACK, -19,19),
 'lens':       (LENS_X-5,LENS_X+5, Y_PCB-LENS_H,Y_PCB, -5,5),
 'battery':    (-26,26, 1.6,6.8, -15,19),        # 523450: 52 × 34 × 5.2
 'speaker':    (-10,10, -11,1.0, -31.5,-25.5),
 'charger':    (-8.5,8.5, 12.0,16.0, -32.0,-3.0),
 'switch':     (11.7,20.3, 15.4,18.9, -15.75,-12.25),
}

def build():
    outer=appearance(S)
    inner=cavity(WALL)
    hull=D(outer,inner)
    front=I(hull,box(-60,60,-60,Y_SPLIT,-60,60))
    back=I(hull,box(-60,60,Y_SPLIT,60,-60,60))

    # --- seam: a lip on the front shell slides 2.2 mm into the back shell
    lip=D(cavity(WALL+0.15),cavity(WALL+1.65))
    front=U(front,I(lip,box(-60,60,Y_SPLIT-0.5,Y_SPLIT+2.2,-60,60)))

    # --- camera eye: the printed dome becomes a flat 1 mm ring with an Ø11 bore (flat 1.7 mm rim);
    #     a Ø14 clear acrylic dome is glued on top, so it still looks like the other, glossy eye
    a=EYE_D/2*S; dome_top=FACE_AT_EYE-EYE_H*a
    front=D(front,cyl_y(LENS_X,0,a+0.3,dome_top-2,FACE_AT_EYE))                  # take the printed dome off
    front=U(front,cyl_y(LENS_X,0,a,FACE_AT_EYE-1.0,FACE_AT_EYE+1.5))              # flat Ø14.4 × 1 mm platform
    front=D(front,cyl_y(LENS_X,0,5.5,dome_top-2,Y_PCB-1.0))

    # --- board standoffs (M2.5 self-tapping, from the back of the board)
    for x,z in HOLES:
        post=I(cyl_y(x,z,2.6,-60,Y_PCB),outer)
        front=D(U(front,post),cyl_y(x,z,1.05,Y_PCB-5,Y_PCB+1))

    # --- microphone: sound tube from the face to the MEMS mic, 0.6 mm gap for a foam gasket
    mx,mz=MIC
    tube=I(cyl_y(mx,mz,2.0,-60,Y_PCB-0.6),outer)
    front=D(U(front,tube),cyl_y(mx,mz,0.6,-60,Y_PCB+1))

    # --- speaker: fence on the bottom, grille holes between the front feet
    xs,xe,ys,ye,zs,ze=PARTS['speaker']
    fence=D(box(xs-1.4,xe+1.4,ys-1.4,min(ye+1.4,Y_SPLIT-0.3),-60,zs+3.0),box(xs-0.2,xe+0.2,ys-0.2,ye+0.2,-60,60))
    front=U(front,I(fence,outer))
    for gx in (-3.0,0.0,3.0):
        for gy in (-8.5,-5.5,-2.5):
            front=D(front,cyl_z(gx,gy,0.8,-60,zs-0.5))

    # --- shell screws: blind bosses in the front, through tubes with counterbores in the back
    for x,z in SCREWS:
        rib_dir=np.array([x,z])/np.hypot(x,z)
        rib=box(-0.8,0.8,Y_SPLIT-6.5,Y_SPLIT,0,12)
        ang=np.arctan2(rib_dir[0],rib_dir[1])
        rib.apply_transform(trimesh.transformations.rotation_matrix(ang,[0,1,0])); rib.apply_translation([x,0,z])   # rib runs outwards into the wall
        front=D(U(front,U(cyl_y(x,z,2.75,Y_SPLIT-6.5,Y_SPLIT),I(rib,outer))),cyl_y(x,z,0.85,Y_SPLIT-5.0,Y_SPLIT+1))
        rho=np.hypot(x,z)/S; yb=float(shell(rho,BA,YB,BR,BN,BM))*S
        tubeb=I(cyl_y(x,z,3.5,Y_SPLIT,60),outer)
        back=U(back,tubeb)
        back=D(back,cyl_y(x,z,1.15,Y_SPLIT-1,60))
        back=D(back,cyl_y(x,z,2.2,yb-3.0,60))

    # --- battery shelf: a plate behind the battery, tied to the back wall by three ribs
    bx0,bx1,by0,by1,bz0,bz1=PARTS['battery']
    shelf=box(bx0-1,bx1+1,by1+0.2,by1+1.4,bz0-1,bz1+1)
    ribs=[box(x-0.6,x+0.6,by1+1.4,40,1.0,bz1) for x in (-18.0,0.0,18.0)]
    back=U(back,I(trimesh.boolean.union([shelf]+ribs,engine='manifold'),outer))

    # --- magnets: pockets open to the inside, 0.8 mm skin to the outside
    for x in (-9.0,9.0):
        yo=float(shell(np.hypot(x,8.0)/S,BA,YB,BR,BN,BM))*S
        back=U(back,I(cyl_y(x,8.0,6.6,yo-5.0,60),outer))
        back=D(back,cyl_y(x,8.0,5.15,yo-8.0,yo-1.2))   # open to the inside so the magnet can go in; 1.2 mm skin

    # --- charger (TP4056, USB-C down): side rails on the back wall, port slot in the bottom
    cx0,cx1,cy0,cy1,cz0,cz1=PARTS['charger']
    for x in (cx0-1.4,cx1+0.2):
        back=U(back,I(box(x,x+1.2,cy0-1.5,40,cz0+3,cz1),outer))
    sx0_,sx1_,sy0_,sy1_=-4.8,4.8,cy0+0.2,cy1-0.4
    # the underside slopes up towards the back here, so a straight cut would leave a knife edge:
    # fill a small flat pad round the port (between the rear feet, out of sight) and cut through that
    px0,px1,py0,py1=-6.5,6.5,sy0_-0.7,sy1_+0.9
    def under_z(x,y):
        t=(y/S-BA)/(YB-BA); r=BR*(1-t**BM)**(1/BN)*S
        return -np.sqrt(max(r*r-x*x,0.0))
    z_pad=min(under_z(x,y) for x in (px0,0.0,px1) for y in np.linspace(py0,py1,9))
    pad=D(box(px0,px1,py0,py1,z_pad,cz0),inner)
    back=U(back,pad)
    back=D(back,box(sx0_,sx1_,sy0_,sy1_,-60,cz0+1))

    # --- slide switch: lever slot through the back
    sx0,sx1,sy0,sy1,sz0,sz1=PARTS['switch']
    back=D(back,box(sx0+1.8,sx1-1.8,sy1-1,40,(sz0+sz1)/2-0.9,(sz0+sz1)/2+0.9))
    back=U(back,I(box(sx0-1.2,sx1+1.2,sy0-1.2,40,sz1,sz1+1.2),outer))       # ledge above the switch body

    return outer,inner,front,back

def export_stl(m,path):
    """STL stores float32: merge points that land on each other so the file stays closed."""
    for digits in (5,4,3):
        k=m.copy(); k.merge_vertices(digits_vertex=digits); k.update_faces(k.nondegenerate_faces()); k.remove_unreferenced_vertices()
        k.export(path)
        if trimesh.load(path).is_watertight: return
    raise RuntimeError('could not export a closed mesh: '+path)

def check(front,back):
    """Every part must fit: no part may overlap the printed shells (volume of overlap, mm3)."""
    both=U(front,back); res={}
    for k,(x0,x1,y0,y1,z0,z1) in PARTS.items():
        shape=cyl_y(LENS_X,0,5.0,y0,y1) if k=='lens' else box(x0,x1,y0,y1,z0,z1)
        o=I(both,shape); res[k]=round(float(o.volume),2)
        if o.volume>0.01: print('  overlap',k,'at',o.bounds.round(1).tolist())
    for x in (-9.0,9.0):   # magnets sit in their pockets
        yo=float(shell(np.hypot(x,8.0)/S,BA,YB,BR,BN,BM))*S
        res['magnet%+d'%x]=round(float(I(both,cyl_y(x,8.0,5.0,yo-1.2-3.0,yo-1.2)).volume),2)
    return res

if __name__=='__main__':
    outer,inner,front,back=build()
    print('front',front.is_watertight,round(front.volume/1000,2),'cm3   back',back.is_watertight,round(back.volume/1000,2),'cm3')
    print('overlap with parts (mm3):',check(front,back))
    for name,m in (('mumi_front_shell',front),('mumi_back_shell',back)):
        m=m.copy(); m.apply_translation([0,0,-GROUND])
        path=os.path.join(OUT,name+'.stl'); export_stl(m,path)
        r=trimesh.load(path); bodies=r.split(only_watertight=False)
        print(name,'reloaded watertight:',r.is_watertight,'bodies:',len(bodies),'faces',len(r.faces))
        # print shops need one connected solid: no loose pieces, no sealed voids
        if len(bodies)!=1 or r.volume<=0:
            raise RuntimeError(f'{name}: {len(bodies)} bodies '+str([(round(b.volume,1),b.bounds.round(1).tolist()) for b in bodies]))
    from wallcheck import outer_thin
    for name,m in (('front',front),('back',back)):
        bad=outer_thin(m,outer,front=(name=='front'))
        print(name,'outer-surface points thinner than 1 mm:',len(bad))
        if len(bad): raise RuntimeError(f'{name}: thin wall near '+str(bad[:10]))
    json.dump({'S':S,'WALL':WALL,'Y_SPLIT':Y_SPLIT,'Y_PCB':Y_PCB,'board_centre':[BX,BZ],'mic':MIC,'holes':HOLES,'screws':SCREWS,'parts':PARTS,'ground':GROUND},
              open(os.path.join(OUT,'layout.json'),'w'),indent=1)
