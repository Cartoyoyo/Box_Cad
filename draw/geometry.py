"""Calculs geometriques purs des outils de dessin (sans interface, testables seuls).

Conventions AutoCAD : angles en degres, 0 = est, sens anti-horaire.
Toutes les coordonnees sont dans le SCR du canevas (unites de la carte).
"""
import math

from qgis.core import QgsGeometry, QgsPointXY

CIRCLE_SEGMENTS = 72


def polar_point(origin, length, angle_deg):
    """Point situe a `length` de `origin` dans la direction `angle_deg`."""
    a = math.radians(angle_deg)
    return QgsPointXY(origin.x() + length * math.cos(a), origin.y() + length * math.sin(a))


def angle_of(p1, p2):
    """Angle de p1 vers p2, dans [0, 360)."""
    return math.degrees(math.atan2(p2.y() - p1.y(), p2.x() - p1.x())) % 360.0


def distance(p1, p2):
    return math.hypot(p2.x() - p1.x(), p2.y() - p1.y())


def deflection(angle_deg, ref_deg):
    """Deviation par rapport a la direction `ref` : 0 = tout droit,
    positif = virage a gauche, negatif = a droite ; dans ]-180, 180]."""
    d = (angle_deg - ref_deg) % 360.0
    return d - 360.0 if d > 180.0 else d


def ortho_angle(angle_deg):
    """Mode ortho : arrondi a l'horizontale ou a la verticale la plus proche."""
    return (round(angle_deg / 90.0) * 90.0) % 360.0


def signed_offset(origin, angle_deg, p):
    """Distance signee de p a la droite (origin, angle) : positive a gauche."""
    a = math.radians(angle_deg)
    return -(p.x() - origin.x()) * math.sin(a) + (p.y() - origin.y()) * math.cos(a)


def rect_two_corners(c1, c2):
    """Rectangle aligne sur les axes a partir de deux coins opposes."""
    return [QgsPointXY(c1.x(), c1.y()), QgsPointXY(c2.x(), c1.y()),
            QgsPointXY(c2.x(), c2.y()), QgsPointXY(c1.x(), c2.y())]


def rect_oriented(p1, angle_deg, length, height):
    """Rectangle dont le premier cote part de p1 (longueur, angle) ;
    hauteur positive = a gauche du premier cote, negative = a droite."""
    p2 = polar_point(p1, length, angle_deg)
    p3 = polar_point(p2, height, angle_deg + 90.0)
    p4 = polar_point(p1, height, angle_deg + 90.0)
    return [QgsPointXY(p1), p2, p3, p4]


def square(p1, side, angle_deg):
    """Carre : premier cote depuis p1 (cote, angle), construit a gauche de ce cote."""
    return rect_oriented(p1, angle_deg, side, side)


def circle(center, radius, segments=CIRCLE_SEGMENTS):
    return [polar_point(center, radius, 360.0 * i / segments) for i in range(segments)]


def polygon_geometry(points):
    pts = [QgsPointXY(p) for p in points]
    return QgsGeometry.fromPolygonXY([pts + [QgsPointXY(pts[0])]])


def line_geometry(points, closed=False):
    pts = [QgsPointXY(p) for p in points]
    if closed:
        pts.append(QgsPointXY(pts[0]))
    return QgsGeometry.fromPolylineXY(pts)


# ----------------------------------------------------------------------
# Accrochage objet : projections, intersections, reconnaissance des cercles
# ----------------------------------------------------------------------

def project_on_segment(p, a, b):
    """Point de [a, b] le plus proche de p, et parametre t (0 = a, 1 = b) sur la droite."""
    dx, dy = b.x() - a.x(), b.y() - a.y()
    den = dx * dx + dy * dy
    if den == 0:
        return QgsPointXY(a), 0.0
    t = ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / den
    tc = min(1.0, max(0.0, t))
    return QgsPointXY(a.x() + tc * dx, a.y() + tc * dy), t


def foot_on_line(p, a, b):
    """Pied de la perpendiculaire de p sur la droite (a, b), et t correspondant."""
    dx, dy = b.x() - a.x(), b.y() - a.y()
    den = dx * dx + dy * dy
    if den == 0:
        return None, 0.0
    t = ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / den
    return QgsPointXY(a.x() + t * dx, a.y() + t * dy), t


def segment_intersection(a, b, c, d):
    """Intersection des segments [a, b] et [c, d], ou None."""
    r = (b.x() - a.x(), b.y() - a.y())
    s = (d.x() - c.x(), d.y() - c.y())
    den = r[0] * s[1] - r[1] * s[0]
    if den == 0:
        return None
    qp = (c.x() - a.x(), c.y() - a.y())
    t = (qp[0] * s[1] - qp[1] * s[0]) / den
    u = (qp[0] * r[1] - qp[1] * r[0]) / den
    if -1e-12 <= t <= 1 + 1e-12 and -1e-12 <= u <= 1 + 1e-12:
        return QgsPointXY(a.x() + t * r[0], a.y() + t * r[1])
    return None


def segment_circle_intersections(a, b, center, radius):
    """Points d'intersection exacts du segment [a, b] et du cercle."""
    dx, dy = b.x() - a.x(), b.y() - a.y()
    fx, fy = a.x() - center.x(), a.y() - center.y()
    qa = dx * dx + dy * dy
    qb = 2 * (fx * dx + fy * dy)
    qc = fx * fx + fy * fy - radius * radius
    disc = qb * qb - 4 * qa * qc
    if qa == 0 or disc < 0:
        return []
    root = math.sqrt(disc)
    points = []
    for t in ((-qb - root) / (2 * qa), (-qb + root) / (2 * qa)):
        if -1e-12 <= t <= 1 + 1e-12:
            points.append(QgsPointXY(a.x() + t * dx, a.y() + t * dy))
    return points


def circle_circle_intersections(c1, r1, c2, r2):
    d = distance(c1, c2)
    if d == 0 or d > r1 + r2 or d < abs(r1 - r2):
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0.0))
    mx = c1.x() + a * (c2.x() - c1.x()) / d
    my = c1.y() + a * (c2.y() - c1.y()) / d
    ox, oy = h * (c2.y() - c1.y()) / d, -h * (c2.x() - c1.x()) / d
    return [QgsPointXY(mx + ox, my + oy), QgsPointXY(mx - ox, my - oy)]


def circle_of_ring(ring, min_vertices=24, tolerance=1e-3):
    """(centre, rayon) si l'anneau ferme est un cercle (sommets equidistants du
    barycentre a `tolerance` pres en relatif), sinon None."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    if len(pts) < min_vertices:
        return None
    cx = sum(p.x() for p in pts) / len(pts)
    cy = sum(p.y() for p in pts) / len(pts)
    center = QgsPointXY(cx, cy)
    dists = [distance(center, p) for p in pts]
    r = sum(dists) / len(dists)
    if r == 0 or max(abs(d - r) for d in dists) > tolerance * r:
        return None
    return center, r


# ----------------------------------------------------------------------
# Transformations affines (QTransform : x' = m11 x + m21 y + dx ; y' = m12 x + m22 y + dy)
# ----------------------------------------------------------------------

def scale_transform(base, factor):
    """Mise a l'echelle de `factor` autour du point `base`."""
    from qgis.PyQt.QtGui import QTransform
    return QTransform(factor, 0.0, 0.0, factor,
                      (1.0 - factor) * base.x(), (1.0 - factor) * base.y())


def mirror_transform(p, angle_deg):
    """Symetrie par rapport a l'axe passant par `p` avec l'angle `angle_deg`."""
    from qgis.PyQt.QtGui import QTransform
    a = math.radians(2.0 * angle_deg)
    # residus trigonometriques (1e-16) annules : coordonnees nettes pour les axes droits
    c, s = (0.0 if abs(v) < 1e-12 else v for v in (math.cos(a), math.sin(a)))
    return QTransform(c, s, s, -c,
                      p.x() - c * p.x() - s * p.y(),
                      p.y() - s * p.x() + c * p.y())


def translate_transform(dx, dy):
    from qgis.PyQt.QtGui import QTransform
    return QTransform(1.0, 0.0, 0.0, 1.0, dx, dy)


def rotate_transform(base, angle_deg):
    """Rotation anti-horaire de `angle_deg` autour de `base`."""
    from qgis.PyQt.QtGui import QTransform
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return QTransform(c, s, -s, c,
                      base.x() - c * base.x() + s * base.y(),
                      base.y() - s * base.x() - c * base.y())


def map_point(qtransform, p):
    x, y = qtransform.map(p.x(), p.y())
    return QgsPointXY(x, y)


def circumcenter(a, b, c):
    """Centre du cercle passant par trois points (None s'ils sont alignes).
    Calcul relatif a `a` : en Lambert 93, les carres des coordonnees absolues
    (~1e13) feraient perdre le millimetre."""
    bx, by = b.x() - a.x(), b.y() - a.y()
    cx, cy = c.x() - a.x(), c.y() - a.y()
    d = 2.0 * (bx * cy - by * cx)
    if abs(d) < 1e-12:
        return None
    b2, c2 = bx * bx + by * by, cx * cx + cy * cy
    return QgsPointXY(a.x() + (cy * b2 - by * c2) / d, a.y() + (bx * c2 - cx * b2) / d)


def transformed(geom, qtransform):
    """Copie de `geom` transformee par une QTransform."""
    g = QgsGeometry(geom)
    g.transform(qtransform)
    return g
