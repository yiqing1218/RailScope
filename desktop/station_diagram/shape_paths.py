"""Round only geometry corners; never move a graph endpoint or a frame cut."""
import math


def rounded_path(points, radius=3):
    def xy(p):
        return f'{p[0]:.4f},{p[1]:.4f}'
    result = ['M '+xy(points[0])]
    for a,b,c in zip(points,points[1:],points[2:]):
        ab,bc = math.dist(a,b),math.dist(b,c)
        if min(ab,bc)<1e-8:
            result.append('L '+xy(b))
            continue
        length = min(radius,ab/3,bc/3)
        before = tuple(b[i]+(a[i]-b[i])*length/ab for i in (0,1))
        after = tuple(b[i]+(c[i]-b[i])*length/bc for i in (0,1))
        result.extend(('L '+xy(before),'Q '+xy(b)+' '+xy(after)))
    result.append('L '+xy(points[-1]))
    return ' '.join(result)
