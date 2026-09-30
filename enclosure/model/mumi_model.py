import trimesh,numpy as np
C_Z=0.5611          # body centre height above ground (model units)
EYE_X,EYE_D=0.153,0.185
YF,YB=-0.2470,0.2708           # front / back apex
FA,FR,FN,FM=-0.075,0.4895,2.59,2.26   # front shell: ends at y=FA with radius FR
BA,BR,BN,BM=0.098,0.4942,6.99,1.78    # back shell
def shell(r,ya,ye,R,n,m):   # y of the shell surface at radius r
    return ya+(ye-ya)*(1-(np.clip(r,0,R)/R)**n)**(1/m)
def mid(y):
    g=lambda c,s: np.exp(-0.5*((y-c)/s)**2)
    t=(y-FA)/(BA-FA)
    return FR+(BR-FR)*t+0.009*g(-0.030,0.018)+0.009*g(0.060,0.018)-0.012*g(0.015,0.012)
def profile():
    rs=FR*np.sin(np.linspace(0,np.pi/2,220))          # dense near the apex
    front=np.c_[rs,shell(rs,FA,YF,FR,FN,FM)]
    ym=np.linspace(FA,BA,160)[1:-1]; midp=np.c_[mid(ym),ym]
    rb=BR*np.sin(np.linspace(0,np.pi/2,220))[::-1]; back=np.c_[rb,shell(rb,BA,YB,BR,BN,BM)]
    return np.vstack([front,midp,back])
def face_y(rho): return shell(rho,FA,YF,FR,FN,FM)
def body_mesh(S,sections=360):
    b=trimesh.creation.revolve(profile()*S,sections=sections)
    b.apply_transform(np.array([[1,0,0,0],[0,0,1,0],[0,1,0,0],[0,0,0,1.]]))
    if b.volume<0: b.invert()
    return b
