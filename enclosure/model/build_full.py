"""Appearance model of Mumi (Jupiter): smooth body of revolution + feet, eyes, cheeks, smile.

Coordinates in mm, body centre at the origin: x to the viewer's right, y from front (-) to back (+), z up.
`appearance()` returns the solid in these coordinates; run this file to export the STL standing on z=0.
"""
import os,trimesh,numpy as np
from mumi_model import *

S=78.0
GROUND=-C_Z*S                 # ground plane (z) in body coordinates

def eye_centre_y(x,S=S):      # face surface at the eye (mm)
    return face_y(abs(x))*S

def blob(a,b_,c,n=2.6):
    s=trimesh.creation.icosphere(subdivisions=5,radius=1.0); v=s.vertices
    r=(np.abs(v[:,0]/a)**n+np.abs(v[:,1]/b_)**n+np.abs(v[:,2]/c)**n)**(-1/n)
    s.vertices=v*r[:,None]; return s

def feet(S=S):
    """Soft rounded "dumpling" nubs like the approved image; positions and size from the AI model."""
    out=[]
    for x in (-0.149,0.149):
        for y in (-0.085,0.131):
            f=blob(0.086*S,0.072*S,0.085*S); f.apply_translation([x*S,y*S,GROUND+0.083*S])
            cut=trimesh.creation.box(extents=[40,40,40]); cut.apply_translation([x*S,y*S,GROUND+0.004*S+20])
            f=f.intersection(cut,engine='manifold'); f.apply_translation([0,0,-0.004*S])
            out.append(f)
    return out

EYE_H=0.38                    # dome height as a fraction of the eye radius
def eyes(S=S):
    out=[]; a=EYE_D/2*S; h=EYE_H*a; Rs=(a*a+h*h)/(2*h)
    for x in (-EYE_X,EYE_X):
        s=trimesh.creation.icosphere(subdivisions=5,radius=Rs); s.apply_translation([x*S,eye_centre_y(x,S)+(Rs-h),0])
        out.append(s)
    return out

CHEEK_X,CHEEK_Z=0.2265,-0.1115
def cheeks(S=S):
    out=[]
    for x in (-CHEEK_X,CHEEK_X):
        fy=face_y(np.hypot(x,CHEEK_Z))*S
        c=trimesh.creation.icosphere(subdivisions=4,radius=1.0); c.apply_scale([0.056*S,0.7,0.050*S]); c.apply_translation([x*S,fy+0.35,CHEEK_Z*S])
        out.append(c)
    return out

def smile(S=S):
    """Engraved arc groove (to subtract)."""
    R=0.060*S; cz=-0.034*S
    th=np.linspace(np.radians(-150),np.radians(-30),40)
    path=np.c_[R*np.cos(th),np.full_like(th,face_y(0.06)*S),cz+R*np.sin(th)]
    caps=[trimesh.creation.capsule(height=np.linalg.norm(path[i+1]-path[i]),radius=0.55,count=[12,12])
          .apply_transform(trimesh.geometry.align_vectors([0,0,1],path[i+1]-path[i]))
          .apply_translation((path[i]+path[i+1])/2) for i in range(len(path)-1)]
    return trimesh.boolean.union(caps,engine='manifold')

def appearance(S=S):
    # cheeks are painted, not raised: a raised pad leaves a crease the print shop reads as a thin wall
    solid=trimesh.boolean.union([body_mesh(S)]+feet(S)+eyes(S),engine='manifold')
    return trimesh.boolean.difference([solid,smile(S)],engine='manifold')

if __name__=='__main__':
    solid=appearance()
    solid.apply_translation([0,0,-GROUND])   # stand on z=0
    print('watertight',solid.is_watertight,'size mm',solid.extents.round(1),'volume cm3',round(solid.volume/1000,1))
    solid.export(os.path.join(os.path.dirname(os.path.abspath(__file__)),'../../ip/mumi_v2_4_clean.stl'))
