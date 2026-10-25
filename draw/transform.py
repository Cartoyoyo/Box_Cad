"""Transformations facon DAO : Deplacer, Copier, Rotation, Miroir, Echelle, Aligner.

Etapes communes : designer l'objet (surbrillance au survol) -> point(s) -> valeur.
Plusieurs objets : cliquer-glisser entoure des objets (lasso, comme la gomme), autant de fois
que voulu ; Entree, clic droit, ou un clic sur un objet (ajoute) passent a l'etape suivante.
Objets concernes : dessins Box_cad (brouillons visibles) et couche active en edition.
  - brouillon : changement direct + bouton Annuler de Box_cad (une annulation pour tous) ;
  - couche active : commande d'edition QGIS (Ctrl+Z), rien n'est enregistre sur disque.
Calculs dans le SCR de travail des outils (metres vrais) : objet couche -> travail ->
transformation -> couche.
Cotes associatives : Deplacer, Rotation, Miroir, Echelle et Aligner d'une forme entrainent les cotes
et les angles poses sur elle (points mesures sur la forme), annules avec elle.
Deplacer une cote ou un angle designe seul ne bouge que la ligne de cote / l'arc : les rappels
s'adaptent ; dans une selection de plusieurs objets, il se deplace en bloc.
"""
from qgis.core import (
    QgsFeature, QgsFeatureRequest, QgsGeometry, QgsPointXY, QgsProject, QgsRectangle,
    QgsVectorLayerUtils,
)
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from . import geometry as G
from . import target as T
from .crs import pixel_size
from .angle_dim import angle_parts, build_angle, moved_angle
from .dimension import dimension_geometry, dimension_parts, moved_dimension
from .draw_tool_base import ACCENT, KEYS_DRAW, DrawTool
from .pick import _number, editable_layers, features_in_lasso, hit_geometry, is_text_layer, pick_feature

STEP_PICK = 0
LINKED_KINDS = ("dimension", "angle")
LINK_TOLERANCE_PX = 0.5     # point de cote considere comme pose sur la forme
LASSO_THRESHOLD = 5         # pixels avant de passer en lasso (comme la gomme)
SELECTION = QColor(37, 99, 235)
PICK_HINT = ("Cliquez l'objet : forme dessinée, ou objet de la couche active en édition. "
             "<b>Cliquer-glisser</b> : en entourer plusieurs.")


def _kind(layer):
    return layer.customProperty(T.DRAFT_PROPERTY)


def _key(hit):
    layer, feature = hit
    return layer.id(), feature.id()


def _count(n):
    return f"{n} objet" + ("s" if n > 1 else "")


class TransformTool(DrawTool):
    """Base : les sous-classes definissent NAME, after_pick(), compute_step(mouse),
    click_step(pt), finish_step() et transform_geometry(layer, feature) ; la
    transformation s'applique a tous les objets designes (self.targets)."""

    NAME = ""
    MULTI = True            # plusieurs objets au lasso
    GUIDE_KEYS = KEYS_DRAW

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.TOOL_NAME = self.NAME.lower()
        self.step = STEP_PICK
        self.targets = []       # objets designes [(couche, QgsFeature)]
        self.target = None      # le premier d'entre eux
        self.hover = None
        self.base = None        # point de base, SCR de travail
        self.rb_obj = self.rb_obj_lines = self.rb_lasso = None
        self._work = {}         # geometries des objets designes, SCR de travail
        self._press = None      # position du clic gauche enfonce (lasso)
        self._dragged = False   # la souris s'est eloignee du clic : c'est un lasso
        self._lasso = []

    def activate(self):
        super().activate()
        self.rb_obj = QgsRubberBand(self.canvas(), T.POLYGON)
        self.rb_obj.setStrokeColor(SELECTION)
        self.rb_obj.setFillColor(QColor(37, 99, 235, 40))
        self.rb_obj.setWidth(2)
        # lignes en trait epais, sans tampon : rapide meme pour des centaines de canalisations
        self.rb_obj_lines = QgsRubberBand(self.canvas(), T.LINE)
        self.rb_obj_lines.setStrokeColor(SELECTION)
        self.rb_obj_lines.setWidth(4)
        self.rb_lasso = QgsRubberBand(self.canvas(), T.POLYGON)
        self.rb_lasso.setStrokeColor(SELECTION)
        self.rb_lasso.setFillColor(QColor(37, 99, 235, 25))
        self.rb_lasso.setLineStyle(Qt.PenStyle.DashLine)
        self.rb_lasso.setWidth(1)

    def deactivate(self):
        for rb in (self.rb_obj, self.rb_obj_lines, self.rb_lasso):
            if rb is not None:
                self.canvas().scene().removeItem(rb)
        self.rb_obj = self.rb_obj_lines = self.rb_lasso = None
        self._press = None
        super().deactivate()

    def wants_osnap(self):
        return self.step != STEP_PICK      # pas d'accrochage pour designer l'objet

    # ------------------------------------------------------------------
    # Etapes communes
    # ------------------------------------------------------------------

    def start(self):
        self.step = STEP_PICK
        self.target = self.hover = self.base = None
        self.targets = []
        self._work = {}
        self.points = []
        self.configure(self.pick_prompt())

    def pick_prompt(self):
        if self.targets:
            return f"{self.NAME.upper()} · {_count(len(self.targets))} (Entrée : continuer) :"
        more = " (cliquer-glisser : plusieurs)" if self.MULTI else ""
        return f"{self.NAME.upper()} · cliquez l'objet{more} :"

    def guide_step(self):
        return self.step

    def guide_hint(self, index):
        if index == STEP_PICK and self.targets:
            return (f"<b>{_count(len(self.targets))}</b> · <b>Entrée</b> ou clic droit : continuer · "
                    "clic sur un objet : l'ajouter et continuer · glisser : en entourer d'autres")
        return self.GUIDE_HINTS[index]

    def compute(self, mouse):
        if self.step == STEP_PICK:
            self.hover = pick_feature(self.canvas(), editable_layers(self.plugin.iface), self.frame.to_map(mouse))
            return mouse, None, None, {}, ""
        return self.compute_step(mouse)

    def on_click(self, pt):
        if self.step != STEP_PICK:
            self.click_step(QgsPointXY(pt))
            return
        if self.hover is None:
            if not self.targets:
                self.plugin.warn(f"{self.NAME} : cliquez une forme dessinée, "
                                 "ou un objet de la couche active en édition.")
            return
        self.add_targets([self.hover])
        self.end_selection()

    def on_finish(self, cancel=False):
        if self.step == STEP_PICK and not cancel and self.targets:
            self.end_selection()            # Entree / clic droit : fin de la designation
        elif cancel or self.step == STEP_PICK:
            self.start()
        else:
            self.finish_step()

    def undo_point(self):
        self.start()

    def add_targets(self, hits):
        known = {_key(t) for t in self.targets}
        for hit in hits:
            if _key(hit) not in known:
                known.add(_key(hit))
                self.targets.append(hit)
        self.target = self.targets[0] if self.targets else None

    def end_selection(self):
        """Fin de la designation : geometries des objets gardees dans le SCR de travail."""
        self._work = {_key(t): self.frame.from_layer(t[1].geometry(), t[0]) for t in self.targets}
        self.after_pick()

    # ------------------------------------------------------------------
    # Lasso : cliquer-glisser a l'etape de designation
    # ------------------------------------------------------------------

    def canvasPressEvent(self, event):
        if (self.MULTI and self.step == STEP_PICK and self.dyn is not None
                and event.button() == Qt.MouseButton.LeftButton):
            self._press = event.pos()
            self._dragged = False
            self._lasso = [QgsPointXY(event.mapPoint())]

    def _dragging(self, pos):
        """Lasso en cours : une fois le seuil franchi, on le reste jusqu'au relachement."""
        if self._press is not None and not self._dragged:
            self._dragged = (pos - self._press).manhattanLength() >= LASSO_THRESHOLD
        return self._press is not None and self._dragged

    def canvasMoveEvent(self, event):
        if self._dragging(event.pos()):
            self._lasso.append(QgsPointXY(event.mapPoint()))
            self.hover = None
            if len(self._lasso) >= 3 and self.rb_lasso is not None:
                self.rb_lasso.setToGeometry(QgsGeometry.fromPolygonXY([self._lasso]), None)
            self.draw_extra()
            return
        super().canvasMoveEvent(event)

    def canvasReleaseEvent(self, event):
        dragged = event.button() == Qt.MouseButton.LeftButton and self._dragging(event.pos())
        self._press = None
        if not dragged:
            self._lasso = []
            super().canvasReleaseEvent(event)
            return
        points, self._lasso = self._lasso + [QgsPointXY(event.mapPoint())], []
        if self.rb_lasso is not None:
            self.rb_lasso.reset(T.POLYGON)
        self.select_in_lasso(points)

    def select_in_lasso(self, points):
        """Ajoute a la designation les objets touches par le lasso (SCR de la carte)."""
        if len(points) < 3:
            return
        lasso = QgsGeometry.fromPolygonXY([points + [points[0]]])
        if not lasso.isGeosValid():
            lasso = lasso.makeValid()
        found = features_in_lasso(self.canvas(), editable_layers(self.plugin.iface), lasso)
        hits = [(layer, f) for layer, feats in found.values() for f in feats]
        if not hits:
            self.plugin.warn(f"{self.NAME} : aucun objet dans le lasso.")
            return
        self.add_targets(hits)
        self.configure(self.pick_prompt())
        self.draw_extra()

    # ------------------------------------------------------------------
    # Apercu
    # ------------------------------------------------------------------

    def draw_extra(self):
        if self.rb_obj is None:
            return
        self.rb_obj.reset(T.POLYGON)
        self.rb_obj_lines.reset(T.LINE)
        if self.step == STEP_PICK:
            shown = list(self.targets)
            if self.hover is not None and _key(self.hover) not in {_key(t) for t in shown}:
                shown.append(self.hover)
            outlines = [(layer, feature, None, {}) for layer, feature in shown]
        else:
            outlines = []
            for layer, feature in self.targets:
                geom = self.preview_geometry(layer, feature)
                outlines.append((layer, feature, geom, self.transform_attrs(layer, feature) if geom is not None else {}))
        polygons, lines = [], []
        for layer, feature, geom, attrs in outlines:
            # un texte est montre par son cadre (position, angle et taille apres transformation)
            geom = hit_geometry(self.canvas(), layer, feature, geom, attrs.get("angle"), attrs.get("taille"))
            if geom is None or geom.isNull():
                continue
            if geom.type() == T.LINE:
                lines.append((geom, layer))
                continue
            if geom.type() != T.POLYGON:
                geom = geom.buffer(pixel_size(self.canvas(), layer.crs()) * 2, 4)
            polygons.append((geom, layer))
        for rb, items in ((self.rb_obj, polygons), (self.rb_obj_lines, lines)):
            for i, (geom, layer) in enumerate(items):
                rb.addGeometry(geom, layer, i == len(items) - 1)     # un seul rafraichissement

    # ------------------------------------------------------------------
    # Outils communs
    # ------------------------------------------------------------------

    def in_work(self, layer, feature):
        """Geometrie de l'objet dans le SCR de travail (metres vrais)."""
        geom = self._work.get((layer.id(), feature.id()))
        return QgsGeometry(geom) if geom is not None else self.frame.from_layer(feature.geometry(), layer)

    def transformed(self, layer, feature, q):
        """Objet transforme par `q` dans le SCR de travail, rendu dans le SCR de sa couche."""
        return self.frame.to_layer(G.transformed(self.in_work(layer, feature), q), layer)

    def center_of(self, layer, feature):
        """Centre de l'objet (d'un texte : centre de son cadre), SCR de travail."""
        c = hit_geometry(self.canvas(), layer, feature).centroid()
        return QgsPointXY(self.frame.from_layer(c, layer).asPoint())

    def selection_box(self):
        """Emprise des objets designes (d'un texte : son cadre), SCR de travail."""
        box = None
        for layer, feature in self.targets:
            bb = self.frame.from_layer(hit_geometry(self.canvas(), layer, feature), layer).boundingBox()
            if box is None:
                box = QgsRectangle(bb)
            else:
                box.combineExtentWith(bb)
        return box

    def selection_center(self):
        """Point de base par defaut (Entree) : centre de l'objet, ou de l'emprise de la selection."""
        if len(self.targets) == 1:
            return self.center_of(*self.target)
        return self.selection_box().center()

    def transform_attrs(self, layer, feature):
        """Attributs a modifier avec la geometrie (angle / taille d'un texte)."""
        return {}

    def preview_geometry(self, layer, feature):
        """Objet montre en bleu pendant l'outil : transforme, ou None = a sa place."""
        return self.transform_geometry(layer, feature)

    def qtransform(self):
        """Transformation en cours dans le SCR de travail (QTransform), ou None.
        Sert aussi a entrainer les cotes associees ; sans elle, rien n'est entraine."""
        return None

    @staticmethod
    def _change_draft(layer, feature, new_geom, attrs):
        """Modifie un objet de brouillon ; retourne l'element d'annulation ou None."""
        old_geom = QgsGeometry(feature.geometry())
        changes, old = {}, {}
        for name, value in (attrs or {}).items():
            idx = layer.fields().indexOf(name)
            if idx >= 0:
                changes[idx] = value
                old[idx] = feature.attribute(idx)
        provider = layer.dataProvider()
        if not provider.changeGeometryValues({feature.id(): new_geom}):
            return None
        if changes:
            provider.changeAttributeValues({feature.id(): changes})
        layer.triggerRepaint()
        return (layer.id(), feature.id(), old_geom, old)

    def apply_transform(self):
        """Applique la transformation en cours a tous les objets designes."""
        changes = []
        for layer, feature in self.targets:
            geom = self.transform_geometry(layer, feature)
            if geom is not None:
                changes.append((layer, feature, geom, self.transform_attrs(layer, feature)))
        if changes:
            self.replace_geometries(changes, follow=True)

    def replace_geometries(self, changes, follow=False):
        """changes : [(couche, objet, nouvelle geometrie, attributs)]. Brouillons : une seule
        annulation pour tous (bouton Annuler) ; couche active : une commande d'edition par
        couche (Ctrl+Z). `follow` : les cotes et angles poses sur les formes suivent."""
        items, refused, done, edits = [], [], [], {}
        for layer, feature, geom, attrs in changes:
            if layer.customProperty(T.DRAFT_PROPERTY):
                item = self._change_draft(layer, feature, geom, attrs)
                if item is None:
                    refused.append(layer.name())
                else:
                    items.append(item)
                    done.append((layer, feature))
            else:
                edits.setdefault(layer.id(), (layer, []))[1].append((feature, geom))
        for layer, geoms in edits.values():
            layer.beginEditCommand(f"{self.NAME} [Box_cad]")
            if all(layer.changeGeometry(feature.id(), geom) for feature, geom in geoms):
                layer.endEditCommand()
                layer.triggerRepaint()
                done += [(layer, feature) for feature, _ in geoms]
            else:
                layer.destroyEditCommand()
                refused.append(layer.name())
        if refused:
            self.plugin.warn(f"{self.NAME} refusé par « {' », « '.join(sorted(set(refused)))} ».")
        if follow:
            shapes = [hit for hit in done if _kind(hit[0]) not in LINKED_KINDS]
            moved = {(layer.id(), feature.id()) for layer, feature, _, _ in changes}
            for ann_layer, ann_feature, ann_geom, ann_attrs in self.linked_annotations(shapes, moved):
                item = self._change_draft(ann_layer, ann_feature, ann_geom, ann_attrs)
                if item is not None:
                    items.append(item)
        if len(items) == 1:
            self.plugin._set_undo(('draft_geom',) + items[0])
        elif items:
            self.plugin._set_undo(('draft_geoms', items))
        return bool(done)

    def linked_annotations(self, shapes, exclude=()):
        """Cotes et angles dont les points mesures sont sur l'une des formes `shapes`
        [(couche, objet)] : [(couche, objet, nouvelle geometrie, attributs)]. Les deux points
        sur les formes : la cote suit en bloc ; un seul : ce point suit, la cote garde son
        decalage. Angle : son sommet (ou ses deux cotes) sur les formes, il suit en bloc.
        `exclude` : (couche, objet) deja transformes eux-memes (designes avec les formes)."""
        result = []
        q = self.qtransform()
        if q is None or not shapes:
            return result
        frame = self.frame
        tol = LINK_TOLERANCE_PX * frame.pixel()
        works = [self.in_work(layer, feature) for layer, feature in shapes]   # tout en SCR de travail
        boxes = [w.boundingBox().buffered(tol) for w in works]

        def on(p):
            pt = QgsGeometry.fromPointXY(p)
            return any(box.contains(p) and w.distance(pt) <= tol for w, box in zip(works, boxes))

        zone = QgsRectangle(boxes[0])
        for box in boxes[1:]:
            zone.combineExtentWith(box)
        zone = QgsGeometry.fromRect(zone)
        for ann in QgsProject.instance().mapLayers().values():
            kind = _kind(ann)
            if kind not in LINKED_KINDS:
                continue
            request = QgsFeatureRequest().setFilterRect(frame.to_layer(zone, ann).boundingBox())
            for f in ann.getFeatures(request):
                if (ann.id(), f.id()) in exclude:
                    continue
                g = frame.from_layer(f.geometry(), ann)
                if kind == "dimension":
                    parts = dimension_parts(g)
                    if parts is None:
                        continue
                    p1, p2, offset = parts
                    on1, on2 = on(p1), on(p2)
                    if not (on1 or on2):
                        continue
                    n1 = G.map_point(q, p1) if on1 else p1
                    n2 = G.map_point(q, p2) if on2 else p2
                    if G.distance(n1, n2) == 0:
                        continue
                    new = G.transformed(g, q) if on1 and on2 else dimension_geometry(n1, n2, offset)[0]
                    result.append((ann, QgsFeature(f), frame.to_layer(new, ann),
                                   {"longueur": round(G.distance(n1, n2), 2)}))
                else:
                    parts = angle_parts(g)
                    if parts is None:
                        continue
                    vertex, start, sweep, r, reaches = parts
                    sides = [G.polar_point(vertex, reach, d) for reach, d in zip(reaches, (start, start + sweep))]
                    if not (on(vertex) or all(on(p) for p in sides)):
                        continue
                    new = _reoriented_angle(G.transformed(g, q))
                    new_parts = angle_parts(new)
                    attrs = {"longueur": round(new_parts[3], 2)} if new_parts else {}
                    result.append((ann, QgsFeature(f), frame.to_layer(new, ann), attrs))
        return result

    # a definir par les sous-classes
    def after_pick(self):
        raise NotImplementedError

    def compute_step(self, mouse):
        raise NotImplementedError

    def click_step(self, pt):
        raise NotImplementedError

    def finish_step(self):
        self.start()

    def transform_geometry(self, layer, feature):
        return None


# ----------------------------------------------------------------------
# Deplacer / Copier
# ----------------------------------------------------------------------

class MoveTool(TransformTool):
    """DEPLACER : objet -> point de base -> point d'arrivee (longueur, angle)."""
    NAME = "Déplacer"
    STEP_BASE, STEP_DEST = 1, 2
    dest = None
    GUIDE_STEPS = ("Objets", "Point de base", "Point d'arrivée")
    GUIDE_HINTS = (PICK_HINT,
                   "Cliquez le point de base : un coin, un centre… (accrochage objet).",
                   "Cliquez le point d'arrivée, ou tapez la <b>longueur</b> (Tab : <b>angle</b>) puis Entrée.")

    def after_pick(self):
        self.step = self.STEP_BASE
        self.configure("Point de base :")

    def compute_step(self, mouse):
        if self.step == self.STEP_BASE:
            self.dest = None
            return mouse, None, None, {}, ""
        pt, ang, dist = self.constrain_from(self.base, mouse)
        self.dest = pt
        return pt, [self.base, pt], "line", {"length": dist, "angle": ang}, ""

    def click_step(self, pt):
        if self.step == self.STEP_BASE:
            self.base = pt
            self.points = [pt]          # point precedent pour l'accrochage (perpendiculaire...)
            self.step = self.STEP_DEST
            self.configure("Point d'arrivée (longueur, angle) :",
                           [("length", "Longueur", "m"), ("angle", "Angle", "°")])
        else:
            self.dest = pt
            self.place()

    def finish_step(self):
        if self.step == self.STEP_DEST and self.dest is not None and self.dyn.has_input():
            self.place()
        else:
            self.start()

    def qtransform(self):
        if self.base is None or getattr(self, "dest", None) is None:
            return None
        return G.translate_transform(self.dest.x() - self.base.x(), self.dest.y() - self.base.y())

    def offset_geometry(self, layer, feature):
        q = self.qtransform()
        return self.transformed(layer, feature, q) if q is not None else None

    def moved_work(self, layer, feature):
        """Objet deplace, SCR de travail ; une cote ou un angle designe seul : seule la
        ligne de cote / l'arc se deplace (avec d'autres objets, il suit en bloc)."""
        q = self.qtransform()
        if q is None:
            return None
        geom = self.in_work(layer, feature)
        alone = len(self.targets) == 1
        if alone and _kind(layer) == "dimension":
            return moved_dimension(geom, q.dx(), q.dy())
        if alone and _kind(layer) == "angle":
            return moved_angle(geom, q.dx(), q.dy())
        return G.transformed(geom, q)

    def move_geometry(self, layer, feature):
        geom = self.moved_work(layer, feature)
        return self.frame.to_layer(geom, layer) if geom is not None else None

    def transform_geometry(self, layer, feature):
        return self.move_geometry(layer, feature) if self.step == self.STEP_DEST else None

    def transform_attrs(self, layer, feature):
        if _kind(layer) == "angle" and self.step == self.STEP_DEST:
            parts = angle_parts(self.moved_work(layer, feature))
            return {"longueur": round(parts[3], 2)} if parts else {}
        return {}

    def place(self):
        self.apply_transform()
        self.start()


class CopyTool(MoveTool):
    """COPIER : objet(s) -> point de base -> chaque clic pose une copie ; Entree/Echap termine."""
    NAME = "Copier"
    GUIDE_STEPS = ("Objets", "Point de base", "Copies")
    GUIDE_HINTS = (PICK_HINT,
                   "Cliquez le point de base : un coin, un centre… (accrochage objet).",
                   "Chaque clic pose une copie (ou tapez <b>longueur</b>, Tab : <b>angle</b>, puis Entrée). "
                   "<b>Entrée</b> ou <b>Échap</b> : terminer.")

    def transform_geometry(self, layer, feature):
        # une copie est toujours un double complet (cote comprise), pose au point d'arrivee
        return self.offset_geometry(layer, feature) if self.step == self.STEP_DEST else None

    def transform_attrs(self, layer, feature):
        return {}

    def finish_step(self):
        if self.step == self.STEP_DEST and self.dest is not None and self.dyn.has_input():
            self.place()                # valeur tapee + Entree : une copie de plus
        else:
            self.start()

    def place(self):
        """Une copie de chaque objet designe ; brouillons : une seule annulation."""
        if self.qtransform() is None:
            return
        drafts, active = {}, {}
        for layer, feature in self.targets:
            geom = self.offset_geometry(layer, feature)
            if geom is not None:
                group = drafts if layer.customProperty(T.DRAFT_PROPERTY) else active
                group.setdefault(layer.id(), (layer, []))[1].append((feature, geom))
        added = []
        for layer_id, (layer, items) in drafts.items():
            copies = []
            for feature, geom in items:
                copy = QgsFeature(feature)
                copy.setGeometry(geom)
                copies.append(copy)
            ok, new = layer.dataProvider().addFeatures(copies)
            if ok and new:
                added.append((layer_id, [f.id() for f in new]))
                layer.triggerRepaint()
        if len(added) == 1:
            self.plugin._set_undo(('add_features',) + added[0])
        elif added:
            self.plugin._set_undo(('add_features_multi', added))
        for layer, items in active.values():
            # createFeature : attributs recopies SAUF la cle primaire (fid GPKG...), valeurs
            # par defaut et contraintes d'unicite gerees ; pas de copie des objets lies par
            # relation (duplicateFeature les recopierait sans limite de profondeur)
            pk = set(layer.primaryKeyAttributes())
            context = layer.createExpressionContext()
            layer.beginEditCommand("Copier [Box_cad]")
            ok = True
            for feature, geom in items:
                attrs = {i: v for i, v in enumerate(feature.attributes()) if i not in pk}
                if not layer.addFeature(QgsVectorLayerUtils.createFeature(layer, geom, attrs, context)):
                    ok = False
                    break
            if ok:
                layer.endEditCommand()
                layer.triggerRepaint()
            else:
                layer.destroyEditCommand()
                self.plugin.warn(f"Copie refusée par « {layer.name()} ».")
        # on reste a l'etape « point d'arrivee » pour enchainer les copies
        self.configure("Copie suivante (Entrée/Échap : fin) :",
                       [("length", "Longueur", "m"), ("angle", "Angle", "°")])


# ----------------------------------------------------------------------
# Rotation
# ----------------------------------------------------------------------

class RotateTool(TransformTool):
    """ROTATION : objet -> point de base (Entree : centre de l'objet) -> angle anti-horaire."""
    NAME = "Rotation"
    STEP_BASE, STEP_ANGLE = 1, 2
    GUIDE_STEPS = ("Objets", "Point de base", "Angle")
    GUIDE_HINTS = (PICK_HINT,
                   "Cliquez le centre de rotation ; <b>Entrée</b> : centre de l'objet (ou de la sélection).",
                   "Tournez à la souris et cliquez, ou tapez l'<b>angle</b> (sens anti-horaire) puis Entrée.")

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.angle = 0.0

    def start(self):
        super().start()
        self.angle = 0.0

    def after_pick(self):
        self.step = self.STEP_BASE
        self.configure("Point de base (Entrée : centre de l'objet) :")

    def _ask_angle(self):
        self.step = self.STEP_ANGLE
        self.points = [self.base]
        self.configure("Angle de rotation (anti-horaire) :", [("angle", "Angle", "°")])

    def compute_step(self, mouse):
        if self.step == self.STEP_BASE:
            return mouse, None, None, {}, ""
        _, ang, _ = self.constrain_from(self.base, mouse, length_key="", angle_key="angle")
        self.angle = ang
        far = G.polar_point(self.base, G.distance(self.base, mouse) or self.frame.pixel() * 40, ang)
        return mouse, [self.base, far], "line", {"angle": ang}, ""

    def click_step(self, pt):
        if self.step == self.STEP_BASE:
            self.base = pt
            self._ask_angle()
        else:
            self._apply()

    def finish_step(self):
        if self.step == self.STEP_BASE:      # Entree : centre de l'objet comme point de base
            self.base = self.selection_center()
            self._ask_angle()
        else:
            self._apply()

    def undo_point(self):
        if self.step == self.STEP_ANGLE:
            self.after_pick()
        else:
            self.start()

    def qtransform(self):
        if self.step != self.STEP_ANGLE or self.base is None:
            return None
        return G.rotate_transform(self.base, self.angle)

    def transform_geometry(self, layer, feature):
        q = self.qtransform()
        return self.transformed(layer, feature, q) if q is not None else None

    def transform_attrs(self, layer, feature):
        if not is_text_layer(layer):
            return {}
        return {"angle": round((_number(feature["angle"], 0.0) + self.angle) % 360.0, 2)}

    def _apply(self):
        self.apply_transform()
        self.start()


# ----------------------------------------------------------------------
# Miroir
# ----------------------------------------------------------------------

class MirrorTool(TransformTool):
    """MIROIR : objet -> 1er point de l'axe -> 2e point (angle) ; objet retourne sur place."""
    NAME = "Miroir"
    STEP_P1, STEP_P2 = 1, 2
    GUIDE_STEPS = ("Objets", "Axe : 1er point", "Axe : 2e point")
    GUIDE_HINTS = (PICK_HINT,
                   "Cliquez un premier point de l'axe de symétrie (accrochage objet).",
                   "Cliquez le second point de l'axe, ou tapez son <b>angle</b> : l'objet est retourné sur place.")

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.axis_angle = None

    def after_pick(self):
        self.step = self.STEP_P1
        self.configure("Premier point de l'axe de symétrie :")

    def compute_step(self, mouse):
        if self.step == self.STEP_P1:
            self.axis_angle = None
            return mouse, None, None, {}, ""
        pt, ang, dist = self.constrain_from(self.base, mouse, length_key="", angle_key="angle")
        self.axis_angle = ang
        far = self.frame.pixel() * 2000
        return pt, [G.polar_point(self.base, far, ang + 180), G.polar_point(self.base, far, ang)], \
            "line", {"angle": ang}, ""

    def click_step(self, pt):
        if self.step == self.STEP_P1:
            self.base = pt
            self.points = [pt]
            self.step = self.STEP_P2
            self.configure("Second point de l'axe (angle) :", [("angle", "Angle", "°")])
        else:
            self._apply()

    def finish_step(self):
        if self.step == self.STEP_P2 and self.axis_angle is not None:
            self._apply()
        else:
            self.start()

    def qtransform(self):
        if self.step != self.STEP_P2 or self.axis_angle is None:
            return None
        return G.mirror_transform(self.base, self.axis_angle)

    def transform_geometry(self, layer, feature):
        q = self.qtransform()
        if q is None:
            return None
        geom = G.transformed(self.in_work(layer, feature), q)
        return self.frame.to_layer(_reoriented_angle(geom) if _kind(layer) == "angle" else geom, layer)

    def transform_attrs(self, layer, feature):
        """Texte : orientation symetrisee mais toujours lisible (jamais a l'envers)."""
        if not is_text_layer(layer) or self.axis_angle is None:
            return {}
        a = (2.0 * self.axis_angle - _number(feature["angle"], 0.0)) % 360.0
        if 90.0 < a <= 270.0:
            a = (a + 180.0) % 360.0
        return {"angle": round(a, 2)}

    def _apply(self):
        self.apply_transform()
        self.start()


# ----------------------------------------------------------------------
# Echelle
# ----------------------------------------------------------------------

class ScaleTool(TransformTool):
    """ECHELLE : objet -> point de base -> facteur (tape, ou souris : distance au point
    de base rapportee a la demi-taille de l'objet). R : mode Reference (longueur
    actuelle -> nouvelle longueur)."""
    NAME = "Échelle"
    STEP_BASE, STEP_FACTOR = 1, 2
    HANDLED_EXTRA = "R"
    GUIDE_STEPS = ("Objets", "Point de base", "Facteur")
    GUIDE_HINTS = (PICK_HINT,
                   "Cliquez le point fixe ; <b>Entrée</b> : centre de l'objet (ou de la sélection).",
                   "Tapez le <b>facteur</b> (2 = double) ou réglez-le à la souris, puis cliquez. "
                   "<b>R</b> : par référence.")
    REFERENCE_HINT = ("Tapez la <b>longueur actuelle</b>, Tab, puis la <b>nouvelle longueur</b> (ou à la souris). "
                      "<b>R</b> : revenir au facteur.")

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.factor = 1.0
        self.reference = False

    def start(self):
        super().start()
        self.factor = 1.0
        self.reference = False

    def after_pick(self):
        self.step = self.STEP_BASE
        self.configure("Point de base (Entrée : centre de l'objet) :")

    def guide_hint(self, index):
        if index == self.STEP_FACTOR and self.reference:
            return self.REFERENCE_HINT
        return super().guide_hint(index)

    def _size(self):
        """Distance souris qui vaut un facteur 1 : demi-diagonale de l'emprise des objets."""
        bb = self.selection_box()
        return max((bb.width() ** 2 + bb.height() ** 2) ** 0.5 / 2, self.frame.pixel() * 10)

    def _ask_factor(self):
        self.step = self.STEP_FACTOR
        self.points = [self.base]
        if self.reference:
            self.configure("Référence : longueur actuelle, puis nouvelle longueur (R : facteur) :",
                           [("ref", "Long. actuelle", "m"), ("new", "Nouvelle", "m")])
        else:
            self.configure("Facteur d'échelle (R : référence) :", [("factor", "Facteur", "")])

    def compute_step(self, mouse):
        if self.step == self.STEP_BASE:
            return mouse, None, None, {}, ""
        dist = G.distance(self.base, mouse)
        if self.reference:
            ref, new = self.dyn.locked("ref"), self.dyn.locked("new")
            new_len = new if new is not None else dist
            self.factor = (new_len / ref) if ref else 1.0
            live = {"new": new_len}
        else:
            typed = self.dyn.locked("factor")
            self.factor = typed if typed is not None else dist / self._size()
            live = {"factor": self.factor}
        info = f"Facteur {self.factor:.4f}".replace(".", ",")
        return mouse, [self.base, mouse], "line", live, info

    def keyPressEvent(self, event):
        from qgis.PyQt.QtCore import Qt
        if self.step == self.STEP_FACTOR and event.key() == Qt.Key.Key_R and self.dyn is not None:
            self.reference = not self.reference
            self._ask_factor()
            event.accept()
            self.refresh()
            return
        super().keyPressEvent(event)

    def _wants(self, event):
        from qgis.PyQt.QtCore import Qt
        return super()._wants(event) or (event.key() == Qt.Key.Key_R and self.step == self.STEP_FACTOR)

    def click_step(self, pt):
        if self.step == self.STEP_BASE:
            self.base = pt
            self._ask_factor()
        else:
            self._apply()

    def finish_step(self):
        if self.step == self.STEP_BASE:
            self.base = self.selection_center()
            self._ask_factor()
        else:
            self._apply()

    def qtransform(self):
        if self.step != self.STEP_FACTOR or self.factor <= 0 or self.base is None:
            return None
        return G.scale_transform(self.base, self.factor)

    def transform_geometry(self, layer, feature):
        q = self.qtransform()
        return self.transformed(layer, feature, q) if q is not None else None

    def transform_attrs(self, layer, feature):
        if _kind(layer) in LINKED_KINDS:              # longueur mesuree / rayon de l'arc
            return {"longueur": round(_number(feature["longueur"], 0.0) * self.factor, 2)}
        if not is_text_layer(layer):
            return {}
        return {"taille": max(1.0, round(_number(feature["taille"], 10.0) * self.factor, 1))}

    def _apply(self):
        self.apply_transform()
        self.start()


# ----------------------------------------------------------------------
# Aligner
# ----------------------------------------------------------------------

class AlignTool(TransformTool):
    """ALIGNER : objet -> 1er point source -> 1re destination -> 2e point source (Entree :
    deplacer seulement) -> 2e destination -> mise a l'echelle O/N (Entree : non).
    Le 1er point source vient sur la 1re destination, la direction source prend celle des
    destinations (rotation autour du 1er point) ; sur O, la longueur suit aussi."""
    NAME = "Aligner"
    STEP_S1, STEP_D1, STEP_S2, STEP_D2, STEP_SCALE = 1, 2, 3, 4, 5
    PROMPTS = {
        STEP_S1: "Premier point source :",
        STEP_D1: "Premier point de destination :",
        STEP_S2: "Deuxième point source (Entrée : déplacer seulement) :",
        STEP_D2: "Deuxième point de destination :",
        STEP_SCALE: "Mettre à l'échelle ? (O : oui, N ou Entrée : non)",
    }
    # bandeau d'aide : une ligne d'etapes, une ligne de consigne, une ligne de touches
    GUIDE_STEPS = ("Objets", "Point source 1", "Destination 1", "Point source 2", "Destination 2", "Échelle")
    GUIDE_HINTS = {
        STEP_PICK: PICK_HINT,
        STEP_S1: "Cliquez un point de l'objet (un coin par exemple) : il viendra sur la 1re destination.",
        STEP_D1: "Cliquez l'endroit où ce point doit arriver.",
        STEP_S2: "Cliquez un 2e point de l'objet (en bleu) : il donne la direction. <b>Entrée</b> : simple déplacement.",
        STEP_D2: "Cliquez un point dans la direction voulue : l'objet pivote autour de la 1re destination.",
        STEP_SCALE: "<b>O</b> : ajuster aussi la taille aux destinations · <b>N</b> ou <b>Entrée</b> : "
                    "garder la taille.",
    }
    GUIDE_KEYS = "U : point précédent · Échap : recommencer · F3 : accrochage objet"

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.pairs = []         # [(source, destination)] valides, SCR du canevas
        self.pending = None     # point source qui attend sa destination
        self.dest = None        # destination sous la souris
        self.scale = False
        self.rb_pairs = None

    def activate(self):
        super().activate()
        self.rb_pairs = QgsRubberBand(self.canvas(), T.LINE)
        self.rb_pairs.setStrokeColor(ACCENT)
        self.rb_pairs.setLineStyle(Qt.PenStyle.DashLine)
        self.rb_pairs.setWidth(1)

    def deactivate(self):
        if self.rb_pairs is not None:
            self.canvas().scene().removeItem(self.rb_pairs)
            self.rb_pairs = None
        super().deactivate()

    def start(self):
        super().start()
        self.pairs, self.pending, self.dest, self.scale = [], None, None, False

    def _ask(self, step):
        self.step = step
        self.configure(self.PROMPTS[step])

    def after_pick(self):
        self._ask(self.STEP_S1)

    def _points(self):
        """s1, d1[, s2, d2] en cours (destination sous la souris comprise), SCR de travail."""
        pts = [p for pair in self.pairs for p in pair]
        if self.pending is not None and self.dest is not None:
            pts += [self.pending, self.dest]
        return pts

    def _params(self):
        pts = self._points()
        return G.align_transform(*pts, scale=self.scale) if pts else None

    def _info(self):
        """Rotation et facteur des deux paires (barre d'etat)."""
        pts = self._points()
        params = G.align_transform(*pts, scale=True) if len(pts) == 4 else None
        if params is None:
            return ""
        return f"Rotation {G.deflection(params[1], 0.0):.1f}° · facteur {params[2]:.4f}".replace(".", ",")

    def _same(self, a, b):
        return G.distance(a, b) < self.frame.pixel() / 2

    def compute_step(self, mouse):
        if self.step in (self.STEP_S1, self.STEP_S2):
            self.dest = None
            return mouse, None, None, {}, ""
        if self.step == self.STEP_SCALE:
            return mouse, None, None, {}, self._info()
        # 1re destination : ortho depuis le point source (comme Deplacer) ;
        # 2e destination : ortho depuis la 1re destination (direction d'alignement)
        origin = self.pending if self.step == self.STEP_D1 else self.pairs[0][1]
        pt, _, _ = self.constrain_from(origin, mouse, length_key="", angle_key="")
        self.dest = pt
        return pt, [self.pending, pt], "line", {}, self._info()

    def click_step(self, pt):
        if self.step in (self.STEP_S1, self.STEP_S2):
            if self.step == self.STEP_S2 and self._same(pt, self.pairs[0][0]):
                self.plugin.warn("Aligner : le deuxième point source est confondu avec le premier.")
                return
            self.pending = pt
            self.points = [pt]
            self._ask(self.step + 1)
        elif self.step in (self.STEP_D1, self.STEP_D2):
            if self.step == self.STEP_D2 and self._same(pt, self.pairs[0][1]):
                self.plugin.warn("Aligner : la deuxième destination est confondue avec la première.")
                return
            self.pairs.append((self.pending, pt))
            self.pending = self.dest = None
            self.points = [pt]
            if len(self.pairs) == 1:
                self._ask(self.STEP_S2)
            else:
                self._after_second_pair()
        else:
            self._apply(scale=False)            # clic : reponse par defaut (non)

    def _after_second_pair(self):
        params = G.align_transform(*self._points(), scale=True)
        if params is not None and abs(params[2] - 1.0) < 1e-6:
            self._apply(scale=False)            # longueurs egales : rien a demander
        else:
            self._ask(self.STEP_SCALE)

    def finish_step(self):
        if self.step in (self.STEP_S2, self.STEP_D2):
            self.pairs = self.pairs[:1]         # Entree apres la 1re paire : deplacer seulement
            self.pending = self.dest = None
            self._apply(scale=False)
        elif self.step == self.STEP_SCALE:
            self._apply(scale=False)
        else:
            self.start()

    def undo_point(self):
        """U / Retour arriere : revient au point precedent."""
        if self.step in (self.STEP_D1, self.STEP_D2):
            self.pending = self.dest = None
            self._ask(self.step - 1)
        elif self.step in (self.STEP_S2, self.STEP_SCALE):
            self.pending = self.pairs.pop()[0]  # la paire redevient « source en attente »
            self._ask(self.step - 1)
        else:
            self.start()
            return
        last = self.pending or (self.pairs[-1][1] if self.pairs else None)
        self.points = [last] if last is not None else []

    def keyPressEvent(self, event):
        if self.step == self.STEP_SCALE and self.dyn is not None and event.key() in (Qt.Key.Key_O, Qt.Key.Key_N):
            event.accept()
            self._apply(scale=event.key() == Qt.Key.Key_O)
            self.refresh()
            return
        super().keyPressEvent(event)

    def _wants(self, event):
        return super()._wants(event) or (
            self.step == self.STEP_SCALE and event.key() in (Qt.Key.Key_O, Qt.Key.Key_N))

    def draw_extra(self):
        super().draw_extra()
        if self.rb_pairs is None:
            return
        self.rb_pairs.reset(T.LINE)
        if self.pairs:
            pairs = QgsGeometry.fromMultiPolylineXY([[s, d] for s, d in self.pairs])
            self.rb_pairs.setToGeometry(self.frame.geom_to_map(pairs), None)

    def qtransform(self):
        params = self._params()
        return params[0] if params else None

    def preview_geometry(self, layer, feature):
        # points source : l'objet reste montre a sa place, c'est sur lui (et non sur un
        # apercu deja deplace, ou rien ne s'accroche) qu'on les clique, comme en DAO
        if self.step in (self.STEP_S1, self.STEP_S2):
            return None
        return self.transform_geometry(layer, feature)

    def transform_geometry(self, layer, feature):
        q = self.qtransform()
        return self.transformed(layer, feature, q) if q is not None else None

    def transform_attrs(self, layer, feature):
        """Comme Rotation (angle d'un texte) et Echelle (taille, longueur d'une cote)."""
        params = self._params()
        if params is None:
            return {}
        _, angle, factor = params
        if _kind(layer) in LINKED_KINDS:
            return {"longueur": round(_number(feature["longueur"], 0.0) * factor, 2)} if factor != 1.0 else {}
        if not is_text_layer(layer):
            return {}
        attrs = {"angle": round((_number(feature["angle"], 0.0) + angle) % 360.0, 2)}
        if factor != 1.0:
            attrs["taille"] = max(1.0, round(_number(feature["taille"], 10.0) * factor, 1))
        return attrs

    def _apply(self, scale):
        self.scale = scale
        self.apply_transform()
        self.start()


def _reoriented_angle(geom):
    """Apres un miroir, l'arc tourne en sens horaire : on le reconstruit en sens
    anti-horaire (meme sommet, meme ouverture) pour qu'angle_parts le relise.
    Sans effet sur un arc deja anti-horaire (deplacement, rotation, echelle)."""
    parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
    if not parts or len(parts[0]) < 3:
        return geom
    arc = parts[0]
    m = arc[len(arc) // 2]
    ccw = angle_parts(geom)
    if ccw is not None:
        vertex, start, sweep, r, _ = ccw
        mid_expected = G.polar_point(vertex, r, start + sweep / 2.0)
        if G.distance(mid_expected, m) < r * 1e-3:
            return geom          # deja dans le bon sens
    rebuilt = angle_parts(QgsGeometry.fromMultiPolylineXY([list(reversed(arc))] + parts[1:]))
    if rebuilt is None:
        return geom
    vertex, start, sweep, r, reaches = rebuilt
    return build_angle(vertex, start, sweep, r, reaches)
