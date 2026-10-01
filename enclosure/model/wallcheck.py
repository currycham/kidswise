"""Wall thickness of the printed shells: cast a ray inwards from surface points and measure to the opposite surface."""
import sys,numpy as np,trimesh
from build_shell import GROUND
def thickness(m,n=200000,seed=0):
    pts,fi=trimesh.sample.sample_surface_even(m,n,seed=seed)
    nrm=m.face_normals[fi]; o=pts-nrm*1e-3
    loc,ri,_=m.ray.intersects_location(o,-nrm,multiple_hits=False)
    t=np.full(len(pts),np.inf); t[ri]=np.linalg.norm(loc-o[ri],axis=1)
    return pts,t
def outer_thin(shell,outer,front,limit=1.0,n=150000,y_split=None):
    """Points on the visible outer surface whose wall is thinner than `limit` (what the print shop flags).

    Samples the outer (appearance) surface, keeps points that are still solid in this shell (not in a hole),
    and casts a ray inwards. A hit only counts when it meets the far side of a wall (its normal roughly
    along the ray), not when it grazes the edge of a hole.
    """
    from build_shell import Y_SPLIT
    ys=Y_SPLIT if y_split is None else y_split
    pts,fi=trimesh.sample.sample_surface_even(outer,n,seed=1)
    nrm=outer.face_normals[fi]
    keep=(pts[:,1]<ys-0.5) if front else (pts[:,1]>ys+0.5)
    pts,nrm=pts[keep],nrm[keep]
    o=pts-nrm*0.05
    # keep only points where this shell really has that outer surface: solid just inside, air just outside
    # (drops points in holes and on outer features the shell replaced, e.g. the camera eye's dome)
    real=shell.contains(o)&~shell.contains(pts+nrm*0.05)
    o,nrm,pts=o[real],nrm[real],pts[real]
    # and the shell's own surface there faces the same way (a ray from just outside lands on it square-on)
    lo,rj,tj=shell.ray.intersects_location(pts+nrm*0.5,-nrm,multiple_hits=False)
    same=np.zeros(len(pts),bool)
    d=np.linalg.norm(lo-(pts[rj]+nrm[rj]*0.5),axis=1)
    same[rj]=(np.abs(d-0.5)<0.05)&((shell.face_normals[tj]*nrm[rj]).sum(1)>0.95)
    o,nrm,pts=o[same],nrm[same],pts[same]
    loc,ri,ti=shell.ray.intersects_location(o,-nrm,multiple_hits=False)
    t=np.full(len(o),np.inf); t[ri]=np.linalg.norm(loc-o[ri],axis=1)+0.05
    along=np.zeros(len(o)); along[ri]=(shell.face_normals[ti]*(-nrm[ri])).sum(1)
    bad=(t<limit)&(along>0.5)
    return [p.round(1).tolist() for p in pts[bad]]

if __name__=='__main__':
    for name in sys.argv[1:] or ['mumi_front_shell','mumi_back_shell']:
        m=trimesh.load(f'../print/{name}.stl'); m.apply_translation([0,0,GROUND])
        pts,t=thickness(m)
        thin=t<1.0
        print(f'{name}: samples {len(t)}, <1.0 mm: {thin.sum()}, <0.6 mm: {(t<0.6).sum()}, min {t.min():.2f}')
        if thin.any():
            P=pts[thin]
            # cluster thin points into regions
            from scipy.cluster.hierarchy import fcluster,linkage
            idx=np.random.default_rng(0).choice(len(P),min(len(P),3000),replace=False); Q=P[idx]
            lab=fcluster(linkage(Q,'single'),3.0,'distance')
            for k in np.unique(lab):
                R=Q[lab==k]; tt=t[thin][idx][lab==k]
                print(f'   region n={len(R):4d} centre {R.mean(0).round(1).tolist()} extent {np.ptp(R,0).round(1).tolist()} min {tt.min():.2f}')
