import os
from qgis.PyQt import sip
from qgis.PyQt.QtWidgets import QAction, QLabel, QMenu, QToolButton, QWidgetAction
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from qgis.PyQt.QtCore import QSettings, Qt, QTimer
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsExpression, QgsFeature, QgsLayerTreeLayer, QgsWkbTypes,
    QgsMessageLog, Qgis,
)


PLUGIN_DIR = os.path.dirname(__file__)
MENU = "&Box_cad"
MENU_DRAW = "Dessin"          # outils de la barre Dessin (ranges dans les sous-menus Box_cad)
OSNAP_MODES_KEY = "Box_cad/osnap_modes"
UNDO_DEPTH = 50
UNDO_LABELS = {
    'layer': "masquage de couche", 'feature': "masquage d'entité", 'all_layers': "Tout éteindre",
    'delete_features_multi': "suppression", 'sort': "tri des couches", 'add_features': "ajout de forme",
    'draft_erase': "gomme", 'draft_geom': "modification de forme", 'draft_attrs': "propriétés",
    'draft_geoms': "modification de forme et de ses cotes",
}


def _icon(name):
    return QIcon(os.path.join(PLUGIN_DIR, "icons", name))


def _menu_title(menu, title):
    """Titre en tete de menu (addSection n'affiche pas de texte avec le style Windows)."""
    label = QLabel(title)
    label.setStyleSheet("font-weight: bold; color: #555; padding: 3px 8px 2px 8px;")
    title_action = QWidgetAction(menu)
    title_action.setDefaultWidget(label)
    title_action.setEnabled(False)
    menu.addAction(title_action)


LINE_DASHES = {"dash": Qt.PenStyle.DashLine, "dot": Qt.PenStyle.DotLine, "dash dot": Qt.PenStyle.DashDotLine}


def _props_pixmap(color, width_mm, style):
    """Icone de la pastille : carre de couleur + echantillon de trait."""
    pix = QPixmap(40, 20)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setPen(QPen(QColor("#555555"), 1))
    p.setBrush(color)
    p.drawRect(1, 3, 13, 13)
    pen = QPen(QColor("#111111"), max(1.0, min(6.0, width_mm * 4)))
    pen.setStyle(LINE_DASHES.get(style, Qt.PenStyle.SolidLine))
    p.setPen(pen)
    p.drawLine(19, 10, 38, 10)
    p.end()
    return pix


class LayerPicker:
    def __init__(self, iface):
        self.iface = iface
        self.toolbar = None
        self.action_pick_layer = None
        self.action_hide_layer = None
        self.action_hide_feature = None
        self.action_delete_feature = None
        self.action_undo = None
        self.action_show_all = None
        self.action_hide_all = None
        self.action_sort_geom = None
        self.action_to_top = None
        self.action_move_up = None
        self.action_move_down = None
        self.action_to_bottom = None
        self.draw_toolbar = None
        self._draw_groups = []      # [(titre, [actions])] : Tracer, Modifier, Annoter
        self._submenus = []         # sous-menus de Extensions → Box_cad
        self.draw_status = None     # etape de l'outil de dessin, dans la barre d'etat
        self.props_chip = None      # pastille couleur / epaisseur / destination
        self._group_buttons = {}
        self.action_ortho = None
        # etat partage par les outils de dessin
        self.draw_ortho = False
        self.draw_fill = False          # formes vides par defaut, remplies a la demande
        from .draw.properties import load_current_props
        self.current_props = load_current_props()   # couleur, epaisseur, trait, opacite
        self.osnap_on = True            # accrochage objet Box_cad, actif par defaut
        self.osnap_cache = None
        self.action_osnap = None
        from .draw.osnap import MODES as OSNAP_MODES
        saved = QSettings().value(OSNAP_MODES_KEY, None)
        if isinstance(saved, str):
            self.osnap_modes = {m for m in saved.split(",") if m}
        else:
            self.osnap_modes = {key for key, _ in OSNAP_MODES}
        self.action_fill = None
        self._actions = []
        self._action_menu = {}   # action -> menu Extensions qui la contient
        # dict[action -> fabrique du map tool] et dict[action -> map tool actif]
        self._tools_factories = {}
        self._tools = {}
        # vrai pendant la synchro d'un bouton decoche par QGIS (pas par l'utilisateur)
        self._syncing = False
        # undo: tuple ('layer', layer_id) | ('feature', layer_id, key, prev_subset)
        #            | ('all_layers', [ids]) | ('delete_features_multi', {layer_id: [QgsFeature]})
        #            | ('sort', [(group, [layer_id, ...])]) | ('add_features', layer_id, [fid])
        #            | ('draft_erase', {layer_id: [QgsFeature]}) | ('draft_geom', layer_id, fid, old_geom)
        #            | ('draft_geoms', [(layer_id, fid, old_geom, old_attrs)])  forme + cotes associees
        self._undo = []         # pile d'annulation (la plus recente en dernier)
        # dict[layer_id] -> [cle SQL, ...] entites masquees par subsetString
        self._hidden_features = {}
        # dict[layer_id] -> filtre d'origine de la couche avant le premier masquage
        self._orig_subset = {}

    # ------------------------------------------------------------------
    # QGIS lifecycle
    # ------------------------------------------------------------------

    def _add_action(self, icon, text, tooltip=None, checkable=False, toolbar=None, menu=MENU):
        action = QAction(_icon(icon), text, self.iface.mainWindow())
        action.setObjectName("boxcad_" + os.path.splitext(icon)[0])   # memorisation des groupes
        action.setCheckable(checkable)
        if tooltip:
            action.setToolTip(tooltip)
        if toolbar is not False:
            (toolbar or self.toolbar).addAction(action)
        # menu Extensions → Box_cad : range par themes dans _build_plugin_menu
        self._actions.append(action)
        self._action_menu[action] = menu
        return action

    def initGui(self):
        self.toolbar = self.iface.mainWindow().addToolBar("Box_cad")
        self.toolbar.setObjectName("BoxCadToolBar")

        from .map_tool_pick_layer import MapToolPickLayer
        from .map_tool_delete_feature import MapToolDeleteFeature
        from .map_tool_layer import MapToolHideLayer
        from .map_tool_feature import MapToolHideFeature
        from .map_tool_reorder import MapToolReorderLayer

        # --- Localiser couche (toggle map tool) ---
        self.action_pick_layer = self._add_action(
            "pick_layer.svg", "Localiser couche (clic)",
            "Activer puis cliquer sur un objet pour le retrouver et le sélectionner dans le panneau des couches",
            checkable=True)
        self._bind_tool(self.action_pick_layer, MapToolPickLayer)

        # --- Supprimer entite (toggle map tool) ---
        self.action_delete_feature = self._add_action(
            "delete_feature.svg", "Supprimer entité (clic)",
            "Activer puis cliquer sur un objet pour le supprimer définitivement de sa couche",
            checkable=True)
        self._bind_tool(self.action_delete_feature, MapToolDeleteFeature)

        self.toolbar.addSeparator()

        # --- Masquer couche (toggle map tool) ---
        self.action_hide_layer = self._add_action(
            "hide_layer.svg", "Masquer couche (clic)",
            "Activer puis cliquer sur un objet pour masquer sa couche",
            checkable=True)
        self._bind_tool(self.action_hide_layer, MapToolHideLayer)

        # --- Masquer entite (toggle map tool) ---
        self.action_hide_feature = self._add_action(
            "hide_feature.svg", "Masquer entité (clic)",
            "Activer puis cliquer sur un objet pour le masquer individuellement",
            checkable=True)
        self._bind_tool(self.action_hide_feature, MapToolHideFeature)

        self.toolbar.addSeparator()

        # --- Annuler derniere action ---
        self.action_undo = self._add_action(
            "undo.svg", "Annuler la dernière action",
            "Annuler les dernières actions, une à une (jusqu'à 50)")
        self.action_undo.setEnabled(False)
        self.action_undo.triggered.connect(self._undo_last)

        self.toolbar.addSeparator()

        # --- Tout afficher ---
        self.action_show_all = self._add_action(
            "show_all.svg", "Tout afficher",
            "Afficher toutes les couches vecteur et réafficher les entités masquées par Box_cad")
        self.action_show_all.triggered.connect(self._show_all)

        # --- Tout eteindre ---
        self.action_hide_all = self._add_action(
            "hide_all.svg", "Tout éteindre", "Masquer toutes les couches vecteur")
        self.action_hide_all.triggered.connect(self._hide_all)

        self.toolbar.addSeparator()

        # --- Ordre des couches (toggle map tools) + tri, dans un bouton a menu ---
        reorder = [
            ("top", "to_top.svg", "Mettre au premier plan (clic)",
             "Activer puis cliquer sur un objet pour le mettre au premier plan"),
            ("up", "move_up.svg", "Monter d'un niveau (clic)",
             "Activer puis cliquer sur un objet pour monter sa couche d'un niveau"),
            ("down", "move_down.svg", "Descendre d'un niveau (clic)",
             "Activer puis cliquer sur un objet pour descendre sa couche d'un niveau"),
            ("bottom", "to_bottom.svg", "Mettre en dernier plan (clic)",
             "Activer puis cliquer sur un objet pour le mettre en dernier plan"),
        ]
        reorder_actions = []
        for position, icon, text, tip in reorder:
            action = self._add_action(icon, text, tip, checkable=True, toolbar=False)
            self._bind_tool(
                action,
                lambda canvas, plugin, act, pos=position: MapToolReorderLayer(canvas, plugin, act, pos))
            reorder_actions.append(action)
        (self.action_to_top, self.action_move_up,
         self.action_move_down, self.action_to_bottom) = reorder_actions

        # --- Trier par geometrie + alpha ---
        self.action_sort_geom = self._add_action(
            "sort_geom.svg", "Trier par type de géométrie",
            "Trier les couches : Points > Lignes > Polygones, ordre alphabétique "
            "(annulable avec le bouton Annuler)", toolbar=False)
        self.action_sort_geom.triggered.connect(self._sort_layers_by_geometry)

        self._group_buttons = {}
        self._add_group_button("ordre", "Ordre des couches", reorder_actions + [None, self.action_sort_geom],
                               QSettings(), toolbar=self.toolbar)

        self._init_status_label()
        self._init_draw_toolbar()

        # --- A propos (menu Extensions uniquement) ---
        self.action_about = QAction(
            QIcon(os.path.join(PLUGIN_DIR, "icon.png")), "À propos…", self.iface.mainWindow())
        self.action_about.triggered.connect(self._show_about)
        self._actions.append(self.action_about)
        self._build_plugin_menu()

        QgsProject.instance().cleared.connect(self._reset_state)

    def unload(self):
        try:
            QgsProject.instance().cleared.disconnect(self._reset_state)
        except TypeError:
            pass
        self._disconnect_chip()
        if self.draw_status is not None:
            self.iface.statusBarIface().removeWidget(self.draw_status)
            self.draw_status.deleteLater()
            self.draw_status = None
        if self.osnap_cache is not None:
            self.osnap_cache.shutdown()      # attend les indexations en cours, puis libere
            self.osnap_cache = None
        self._deactivate_tools()
        root = self._plugin_root_menu()
        if root is not None:
            root.clear()            # titres et sous-menus ; QGIS retire ensuite le menu vide
            self.iface.removePluginMenu(MENU, self.action_undo)
        for action in self._actions:
            action.deleteLater()
        for submenu in self._submenus:
            submenu.deleteLater()
        self._submenus = []
        self._draw_groups = []
        self._actions = []
        self._action_menu = {}
        for attr in ("toolbar", "draw_toolbar"):
            bar = getattr(self, attr)
            if bar:
                bar.deleteLater()
                setattr(self, attr, None)

    # ------------------------------------------------------------------
    # Barre Box_cad Dessin
    # ------------------------------------------------------------------

    def _init_draw_toolbar(self):
        from .draw.angle_dim import AngleTool
        from .draw.dimension import DimensionTool
        from .draw.eraser import EraserTool
        from .draw.properties import PropertiesTool
        from .draw.text_tool import TextTool
        from .draw.tools import (
            CircleTool, LineTool, PolygonTool, PolylineTool, RectangleTool, SquareTool,
        )
        from .draw.transform import CopyTool, MirrorTool, MoveTool, RotateTool, ScaleTool

        self.draw_toolbar = self.iface.mainWindow().addToolBar("Box_cad Dessin")
        self.draw_toolbar.setObjectName("BoxCadDrawToolBar")
        keys = " Tapez une valeur, Tab pour le champ suivant, Entrée pour valider, Échap pour annuler."
        pick = " Formes dessinées, ou objet de la couche active en édition."
        groups = [
            ("tracer", "Tracer", [
                ("draw_line.svg", "Ligne", "LIGNE : segments enchaînés, une entité par segment (longueur, angle)." + keys,
                 LineTool),
                ("draw_polyline.svg", "Polyligne",
                 "POLYLIGNE : une seule entité ; Entrée ou clic droit termine, C ferme." + keys, PolylineTool),
                ("draw_rectangle.svg", "Rectangle", "RECTANGLE : deux coins opposés (largeur, hauteur)." + keys,
                 RectangleTool),
                ("draw_rect_rotated.svg", "Rectangle orienté",
                 "RECTANGLE ORIENTÉ : premier côté (longueur, angle) puis hauteur." + keys,
                 lambda canvas, plugin, act: RectangleTool(canvas, plugin, act, oriented=True)),
                ("draw_square.svg", "Carré",
                 "CARRÉ : coin puis côté (longueur, angle), construit à gauche du côté." + keys, SquareTool),
                ("draw_polygon.svg", "Polygone",
                 "POLYGONE : un clic par sommet (longueur, angle) ; Entrée, clic droit ou C ferme, "
                 "U annule le dernier sommet." + keys, PolygonTool),
                ("draw_circle.svg", "Cercle", "CERCLE : centre puis rayon." + keys, CircleTool),
            ]),
            ("modifier", "Modifier", [
                ("draw_move.svg", "Déplacer", "DÉPLACER : objet, point de base, point d'arrivée (longueur, angle)."
                 + pick, MoveTool),
                ("draw_copy.svg", "Copier", "COPIER : objet, point de base, puis un clic par copie ; "
                 "Entrée ou Échap termine." + pick, CopyTool),
                ("draw_rotate.svg", "Rotation", "ROTATION : objet, point de base (Entrée : son centre), "
                 "angle à la souris ou tapé (anti-horaire)." + pick, RotateTool),
                ("draw_mirror.svg", "Miroir", "MIROIR : objet, puis deux points de l'axe ; l'objet est retourné."
                 + pick, MirrorTool),
                ("draw_scale.svg", "Échelle", "ÉCHELLE : objet, point de base (Entrée : centre), facteur tapé ou "
                 "à la souris ; R = référence (longueur actuelle → nouvelle)." + pick, ScaleTool),
                ("draw_eraser.svg", "Gomme", "GOMME : clic sur une forme dessinée, ou cliquer-glisser pour en "
                 "effacer plusieurs. Couches brouillon seulement ; Annuler restaure.", EraserTool),
                ("draw_properties.svg", "Propriétés", "PROPRIÉTÉS : clic sur une forme dessinée (couleur, épaisseur, "
                 "trait, remplissage, texte) ; Entrée sans objet : propriétés courantes.", PropertiesTool),
            ]),
            ("annoter", "Annoter", [
                ("draw_text.svg", "Texte", "TEXTE : point d'insertion, angle (Entrée : 0°), puis texte et taille "
                 "(en points, fixe à l'écran).", TextTool),
                ("draw_dimension.svg", "Cote", "COTE : premier point, second point (longueur, angle), puis position "
                 "de la ligne de cote (Entrée : sans décalage)." + keys, DimensionTool),
                ("draw_angle.svg", "Angle", "ANGLE : clic sur deux lignes (ou Entrée : sommet puis un point "
                 "sur chaque côté), puis position de l'arc ; le côté choisi donne l'angle, son "
                 "supplémentaire ou l'angle rentrant." + keys, AngleTool),
            ]),
        ]
        settings = QSettings()
        for key, title, tools in groups:
            actions = []
            for icon, text, tip, factory in tools:
                action = self._add_action(icon, text, tip, checkable=True, toolbar=False, menu=MENU_DRAW)
                self._bind_tool(action, factory)
                actions.append(action)
            self._add_group_button(key, title, actions, settings)
            self._draw_groups.append((title, actions))

        self.draw_toolbar.addSeparator()
        self.action_fill = self._add_action(
            "draw_fill.svg", "Remplissage",
            "Remplissage des rectangles, carrés et cercles dessinés : désactivé = forme vide (contour seul)",
            checkable=True, toolbar=self.draw_toolbar, menu=MENU_DRAW)
        self.action_fill.toggled.connect(self._set_fill)
        self.action_osnap = self._add_action(
            "draw_osnap.svg", "Accrochage objet (F3)",
            "Accrochage objet : centre, quadrant, opposé, extrémité, milieu, intersection, "
            "perpendiculaire, proche (flèche : choix des modes)",
            checkable=True, toolbar=self.draw_toolbar, menu=MENU_DRAW)
        self.action_osnap.setChecked(True)
        self.action_osnap.toggled.connect(self._set_osnap)
        self._init_osnap_menu()
        self._init_osnap_cache()
        self.action_ortho = self._add_action(
            "draw_ortho.svg", "Mode ortho (F8)", "Ortho : segments horizontaux ou verticaux uniquement",
            checkable=True, toolbar=self.draw_toolbar, menu=MENU_DRAW)
        self.action_ortho.toggled.connect(self._set_ortho)
        self.draw_toolbar.addSeparator()
        self._init_props_chip()

    def _add_group_button(self, key, title, actions, settings, toolbar=None):
        """Bouton a menu deroulant : affiche le dernier outil utilise du groupe (memorise),
        la fleche ouvre la liste des outils sous un titre. None = separateur."""
        toolbar = toolbar or self.draw_toolbar
        button = QToolButton(toolbar)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        menu = QMenu(button)
        menu.setTitle(title)
        _menu_title(menu, title)
        for action in actions:
            if action is None:
                menu.addSeparator()
            else:
                menu.addAction(action)
        actions = [a for a in actions if a is not None]
        button.setMenu(menu)
        last = settings.value(f"Box_cad/group_{key}", "")
        button.setDefaultAction(next((a for a in actions if a.objectName() == last), actions[0]))
        button.setMenu(menu)            # setDefaultAction ne doit pas faire perdre le menu

        def remember(action, k=key, b=button):
            b.setDefaultAction(action)
            b.setMenu(menu)
            QSettings().setValue(f"Box_cad/group_{k}", action.objectName())

        for action in actions:
            action.triggered.connect(lambda checked=False, a=action: remember(a))
        toolbar.addWidget(button)
        self._group_buttons[key] = button

    # ------------------------------------------------------------------
    # Barre d'etat (etape de l'outil de dessin) et pastille des proprietes
    # ------------------------------------------------------------------

    def _init_status_label(self):
        """Etape et champs de l'outil de dessin, a gauche de la barre d'etat
        (la saisie pres du curseur n'affiche que les valeurs)."""
        self.draw_status = QLabel()
        self.draw_status.setObjectName("BoxCadDrawStatus")
        self.draw_status.setTextFormat(Qt.TextFormat.RichText)
        # largeur minimale nulle : un texte long est coupe au lieu d'elargir la fenetre QGIS
        self.draw_status.setMinimumWidth(1)
        self.draw_status.hide()
        bar = self.iface.statusBarIface()
        anchor = getattr(getattr(type(bar), "Anchor", type(bar)), "AnchorLeft")
        bar.addPermanentWidget(self.draw_status, 0, anchor)

    def show_draw_status(self, html):
        if self.draw_status is None:
            return
        self.draw_status.setText(html or "")
        self.draw_status.setVisible(bool(html))

    def _init_props_chip(self):
        """Pastille : couleur, epaisseur et couche qui recevra le dessin ;
        clic = proprietes courantes."""
        self.props_chip = QToolButton(self.draw_toolbar)
        self.props_chip.setObjectName("BoxCadPropsChip")
        self.props_chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.props_chip.setAutoRaise(True)
        self.props_chip.clicked.connect(self.edit_current_props)
        self.draw_toolbar.addWidget(self.props_chip)
        self._chip_layer = None
        self.iface.currentLayerChanged.connect(self._on_current_layer)
        self._on_current_layer(self.iface.activeLayer())

    def _on_current_layer(self, layer):
        self._watch_layer(None)
        if isinstance(layer, QgsVectorLayer):
            self._watch_layer(layer)
        self.update_props_chip()

    def _watch_layer(self, layer):
        """Suit le passage en edition de la couche active (un seul abonnement a la fois)."""
        old = self._chip_layer
        if old is not None and not sip.isdeleted(old):
            for signal in (old.editingStarted, old.editingStopped):
                try:
                    signal.disconnect(self.update_props_chip)
                except TypeError:
                    pass
        self._chip_layer = layer
        if layer is not None:
            layer.editingStarted.connect(self.update_props_chip)
            layer.editingStopped.connect(self.update_props_chip)

    def _disconnect_chip(self):
        if getattr(self, "props_chip", None) is None:
            return
        try:
            self.iface.currentLayerChanged.disconnect(self._on_current_layer)
        except TypeError:
            pass
        self._watch_layer(None)
        self.props_chip = None

    def update_props_chip(self, *args):
        chip = getattr(self, "props_chip", None)
        if chip is None or sip.isdeleted(chip):
            return
        from .draw import target as T
        props = self.current_props
        color = QColor(props.get("couleur") or T.DEFAULT_INK)
        try:
            width = float(props.get("epaisseur") or T.DEFAULT_WIDTH)
        except (TypeError, ValueError):
            width = T.DEFAULT_WIDTH
        style = props.get("trait") or "solid"
        chip.setIcon(QIcon(_props_pixmap(color, width, style)))

        lines, polys = T.active_target(self.iface, "line"), T.active_target(self.iface, "polygon")
        layer = lines or polys
        if layer is not None:
            name = layer.name()
            target = name if len(name) <= 18 else name[:17] + "…"
        else:
            target = "brouillon"
        chip.setText(f"{width:g} mm · {target}".replace(".", ","))

        def dest(found):
            return f"« {found.name()} » (en édition)" if found else "couche brouillon Box_cad"
        chip.setToolTip(
            "Propriétés courantes et destination des dessins (clic : modifier les propriétés)\n"
            f"Couleur {color.name()}, épaisseur {width:g} mm\n".replace(".", ",")
            + f"Lignes → {dest(lines)}\nRectangles, cercles, polygones → {dest(polys)}\n"
            "Textes et cotes → leur couche brouillon")

    def edit_current_props(self):
        from .draw.properties import PropertiesDialog
        values = dict(self.current_props)
        values["rempli"] = 1 if self.draw_fill else 0
        dialog = PropertiesDialog(self.iface.mainWindow(), values, "current")
        if dialog.exec():
            self.set_current_props(dialog.values()[0])

    def set_current_props(self, values):
        """Proprietes appliquees aux prochaines formes (memorisees)."""
        from .draw import target as T
        from .draw.properties import save_current_props
        props = {k: values.get(k, self.current_props.get(k)) for k in T.PROPERTY_FIELDS}
        self.current_props = props
        save_current_props(props)
        if "rempli" in values and self.action_fill is not None:
            self.action_fill.setChecked(bool(values["rempli"]))
        self.update_props_chip()

    def _init_osnap_cache(self):
        from .draw.osnap import SnapCache
        self.osnap_cache = SnapCache(self.iface.mapCanvas())

    def _init_osnap_menu(self):
        from .draw.osnap import MODES as OSNAP_MODES
        menu = QMenu(self.iface.mainWindow())
        for key, label in OSNAP_MODES:
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(key in self.osnap_modes)
            a.toggled.connect(lambda checked, k=key: self._set_osnap_mode(k, checked))
        self.action_osnap.setMenu(menu)
        self._osnap_menu = menu
        button = self.draw_toolbar.widgetForAction(self.action_osnap)
        if isinstance(button, QToolButton):
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

    def _set_osnap(self, checked):
        self.osnap_on = checked

    def _set_osnap_mode(self, key, checked):
        if checked:
            self.osnap_modes.add(key)
        else:
            self.osnap_modes.discard(key)
        QSettings().setValue(OSNAP_MODES_KEY, ",".join(sorted(self.osnap_modes)))

    def _set_fill(self, checked):
        self.draw_fill = checked

    def _set_ortho(self, checked):
        self.draw_ortho = checked

    def _plugin_root_menu(self):
        """Sous-menu Extensions → Box_cad (ou None)."""
        for action in self.iface.pluginMenu().actions():
            if action.menu() is not None and action.text() == MENU:
                return action.menu()
        return None

    def _build_plugin_menu(self):
        """Extensions → Box_cad, comme CanaPlan : l'action principale, puis un sous-menu
        par theme, puis A propos."""
        self.iface.addPluginToMenu(MENU, self.action_undo)      # cree le sous-menu Box_cad
        root = self._plugin_root_menu()
        if root is None:
            return
        root.menuAction().setIcon(QIcon(os.path.join(PLUGIN_DIR, "icon.png")))
        root.addSeparator()
        themes = [
            ("Objets", [self.action_pick_layer, self.action_delete_feature]),
            ("Affichage", [self.action_hide_layer, self.action_hide_feature,
                           self.action_show_all, self.action_hide_all]),
            ("Ordre des couches", [self.action_to_top, self.action_move_up, self.action_move_down,
                                   self.action_to_bottom, None, self.action_sort_geom]),
        ] + self._draw_groups + [
            ("Modes de dessin", [self.action_fill, self.action_osnap, self.action_ortho]),
        ]
        for title, actions in themes:
            submenu = QMenu(title, self.iface.mainWindow())
            for action in actions:
                if action is None:
                    submenu.addSeparator()
                else:
                    submenu.addAction(action)
            root.addMenu(submenu)
            self._submenus.append(submenu)
        root.addSeparator()
        root.addAction(self.action_about)

    def _show_about(self):
        from .about_dialog import AboutDialog
        AboutDialog(self.iface.mainWindow()).exec()

    def _reset_state(self):
        """Nouveau projet / projet ferme : les identifiants memorises ne valent plus."""
        self._hidden_features.clear()
        self._orig_subset.clear()
        self._set_undo(None)

    # ------------------------------------------------------------------
    # Activation des map tools
    # ------------------------------------------------------------------

    def _all_toggle_actions(self):
        return list(self._tools_factories)

    def _bind_tool(self, action, factory):
        self._tools_factories[action] = factory
        action.toggled.connect(lambda checked, a=action: self._toggle_tool(a, checked))

    def _toggle_tool(self, action, checked):
        if self._syncing:
            return  # bouton resynchronise apres un changement d'outil fait par QGIS
        canvas = self.iface.mapCanvas()
        if checked:
            self._deactivate_others(action)
            tool = self._tools_factories[action](canvas, self, action)
            self._tools[action] = tool
            canvas.setMapTool(tool)
        else:
            tool = self._tools.pop(action, None)
            if tool is not None:
                canvas.unsetMapTool(tool)

    def _on_tool_deactivated(self, action):
        """Appele par le map tool quand QGIS lui retire la main (autre outil choisi).
        Decoche le bouton sans relancer _toggle_tool (on est deja dans setMapTool) ;
        la reference a l'outil est gardee : le canvas est encore en train de l'utiliser."""
        if action.isChecked():
            self._syncing = True
            try:
                action.setChecked(False)
            finally:
                self._syncing = False

    def _deactivate_others(self, keep):
        """Decoche tous les boutons toggle sauf celui en cours d'activation."""
        for a in self._all_toggle_actions():
            if a is not keep and a.isChecked():
                a.setChecked(False)

    def _deactivate_tools(self):
        for a in self._all_toggle_actions():
            a.setChecked(False)
        canvas = self.iface.mapCanvas()
        for tool in self._tools.values():
            canvas.unsetMapTool(tool)
        self._tools.clear()

    # ------------------------------------------------------------------
    # Messages et undo
    # ------------------------------------------------------------------

    def warn(self, msg):
        self.iface.messageBar().pushWarning("Box_cad", msg)

    @property
    def _undo_stack(self):
        """Derniere action annulable (ou None)."""
        return self._undo[-1] if self._undo else None

    def _set_undo(self, entry):
        """Empile une action annulable ; None vide toute la pile."""
        if entry is None:
            self._undo.clear()
        else:
            self._undo.append(entry)
            del self._undo[:-UNDO_DEPTH]
        self._update_undo_action()

    def _discard_undo(self, entry):
        """Retire une action de la pile (deja annulee autrement)."""
        if entry in self._undo:
            self._undo.remove(entry)
        self._update_undo_action()

    def _drop_undo(self, ops):
        """Retire de la pile les actions d'un certain type (ex. masquages apres Tout afficher)."""
        self._undo = [e for e in self._undo if e[0] not in ops]
        self._update_undo_action()

    def _update_undo_action(self):
        if not self.action_undo:
            return
        self.action_undo.setEnabled(bool(self._undo))
        if self._undo:
            label = UNDO_LABELS.get(self._undo[-1][0], "dernière action")
            self.action_undo.setToolTip(f"Annuler : {label} ({len(self._undo)} action(s) annulable(s))")
        else:
            self.action_undo.setToolTip("Rien à annuler")

    def _remap_fids(self, layer_id, mapping):
        """Formes restaurees = nouveaux identifiants : on les reporte sur les actions
        plus anciennes de la pile pour qu'elles restent annulables."""
        for i, entry in enumerate(self._undo):
            op = entry[0]
            if op == 'add_features' and entry[1] == layer_id:
                self._undo[i] = (op, layer_id, [mapping.get(f, f) for f in entry[2]])
            elif op in ('draft_geom', 'draft_attrs') and entry[1] == layer_id and entry[2] in mapping:
                self._undo[i] = (op, layer_id, mapping[entry[2]]) + tuple(entry[3:])
            elif op == 'draft_geoms':
                self._undo[i] = (op, [(lid, mapping.get(fid, fid) if lid == layer_id else fid) + tuple(rest)
                                      for lid, fid, *rest in entry[1]])

    # ------------------------------------------------------------------
    # Actions appelees par les map tools
    # ------------------------------------------------------------------

    def pick_layer(self, layer):
        """Selectionne la couche dans le panneau et la rend active. Appele par MapToolPickLayer."""
        self.iface.setActiveLayer(layer)
        view = self.iface.layerTreeView()
        view.setCurrentLayer(layer)
        index = view.currentIndex()
        if index.isValid():
            view.scrollTo(index)
        # setActiveLayer peut declencher activeLayerChanged qui reset le map tool ;
        # on le reaffirme apres que Qt ait traite tous les signaux de ce tick.
        tool = self._tools.get(self.action_pick_layer)
        if tool:
            QTimer.singleShot(0, lambda: self._reassert_tool(tool, self.action_pick_layer))

    def _reassert_tool(self, tool, action):
        """Reactive le map tool si QGIS l'a reinitialise suite a activeLayerChanged."""
        canvas = self.iface.mapCanvas()
        if sip.isdeleted(tool) or canvas.mapTool() is tool:
            return
        self._syncing = True
        try:
            action.setChecked(True)
        finally:
            self._syncing = False
        self._deactivate_others(action)
        self._tools[action] = tool
        canvas.setMapTool(tool)

    def hide_layer(self, layer):
        """Masque la couche entiere. Appele par MapToolHideLayer."""
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer.id())
        if node and node.itemVisibilityChecked():
            node.setItemVisibilityChecked(False)
            self._set_undo(('layer', layer.id()))

    def hide_feature(self, layer, fid):
        """Masque une entite via subsetString. Appele par MapToolHideFeature."""
        if layer.isEditable():
            self.warn(f"« {layer.name()} » est en mode edition : impossible de filtrer ses entites.")
            return
        key = self._feature_key(layer, fid)
        if key is None:
            self.warn(f"« {layer.name()} » n'a pas de cle primaire : entite non masquable.")
            return

        layer_id = layer.id()
        prev_subset = layer.subsetString()
        self._orig_subset.setdefault(layer_id, prev_subset)
        keys = self._hidden_features.setdefault(layer_id, [])
        if key in keys:
            return
        keys.append(key)
        if self._rebuild_subset(layer):
            self._set_undo(('feature', layer_id, key, prev_subset))
        else:
            keys.remove(key)
            layer.setSubsetString(prev_subset)
            self.warn(f"Le fournisseur de « {layer.name()} » refuse le filtre : entite non masquee.")

    def delete_features_multi(self, to_delete):
        """Supprime des entites de plusieurs couches.
        to_delete: dict[layer_id -> (layer, [QgsFeature])]
        Si l'utilisateur editait deja la couche, la suppression reste dans son tampon
        d'edition (pas d'enregistrement force de ses modifications en cours)."""
        saved = {}
        errors = []
        for layer_id, (layer, features) in to_delete.items():
            fids = [f.id() for f in features]
            was_editing = layer.isEditable()
            if not was_editing and not layer.startEditing():
                errors.append(f"{layer.name()} : edition impossible")
                continue
            layer.beginEditCommand(f"Supprimer {len(fids)} entite(s) [Box_cad]")
            if not layer.deleteFeatures(fids):
                layer.destroyEditCommand()
                if not was_editing:
                    layer.rollBack()
                errors.append(f"{layer.name()} : suppression refusee")
                continue
            layer.endEditCommand()
            if not was_editing and not layer.commitChanges():
                errors.append(f"{layer.name()} : " + " ; ".join(layer.commitErrors()))
                layer.rollBack()
                continue
            saved[layer_id] = features  # deja des copies QgsFeature

        if errors:
            self.warn("Suppression incomplete - " + " | ".join(errors))
        if saved:
            self._set_undo(('delete_features_multi', saved))

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def _undo_last(self):
        if not self._undo:
            return
        entry = self._undo.pop()

        project = QgsProject.instance()
        root = project.layerTreeRoot()
        op = entry[0]

        if op == 'layer':
            _, layer_id = entry
            node = root.findLayer(layer_id)
            if node:
                node.setItemVisibilityChecked(True)

        elif op == 'feature':
            _, layer_id, key, prev_subset = entry
            layer = project.mapLayer(layer_id)
            if layer:
                keys = self._hidden_features.get(layer_id, [])
                if key in keys:
                    keys.remove(key)
                layer.setSubsetString(prev_subset)

        elif op == 'all_layers':
            _, layer_ids = entry
            for layer_id in layer_ids:
                node = root.findLayer(layer_id)
                if node:
                    node.setItemVisibilityChecked(True)

        elif op == 'delete_features_multi':
            _, saved = entry
            errors = []
            for layer_id, saved_features in saved.items():
                layer = project.mapLayer(layer_id)
                if not layer or not saved_features:
                    continue
                was_editing = layer.isEditable()
                if not was_editing and not layer.startEditing():
                    errors.append(f"{layer.name()} : edition impossible")
                    continue
                layer.beginEditCommand(f"Restaurer {len(saved_features)} entite(s) [Box_cad]")
                layer.addFeatures(saved_features)
                layer.endEditCommand()
                if not was_editing and not layer.commitChanges():
                    errors.append(f"{layer.name()} : " + " ; ".join(layer.commitErrors()))
                    layer.rollBack()
            if errors:
                self.warn("Restauration incomplete - " + " | ".join(errors))

        elif op == 'draft_attrs':
            _, layer_id, fid, old = entry
            layer = project.mapLayer(layer_id)
            if layer:
                layer.dataProvider().changeAttributeValues({fid: old})
                layer.triggerRepaint()

        elif op in ('draft_geom', 'draft_geoms'):
            items = entry[1] if op == 'draft_geoms' else [entry[1:]]
            for layer_id, fid, old_geom, *rest in items:
                layer = project.mapLayer(layer_id)
                if layer:
                    layer.dataProvider().changeGeometryValues({fid: old_geom})
                    if rest and rest[0]:                 # angle / taille d'un texte, longueur d'une cote
                        layer.dataProvider().changeAttributeValues({fid: rest[0]})
                    layer.triggerRepaint()

        elif op == 'draft_erase':
            _, saved = entry
            for layer_id, feats in saved.items():
                layer = project.mapLayer(layer_id)
                if layer:
                    ok, added = layer.dataProvider().addFeatures([QgsFeature(f) for f in feats])
                    if ok and added:
                        self._remap_fids(layer_id, {f.id(): a.id() for f, a in zip(feats, added)})
                    layer.triggerRepaint()

        elif op == 'add_features':
            _, layer_id, fids = entry
            layer = project.mapLayer(layer_id)
            if layer:
                layer.dataProvider().deleteFeatures(fids)
                layer.triggerRepaint()

        elif op == 'sort':
            _, orders = entry
            bridge = project.layerTreeRegistryBridge()
            bridge.setEnabled(False)
            try:
                for group, layer_ids in orders:
                    if not sip.isdeleted(group):
                        self._reorder_group(group, layer_ids)
            finally:
                bridge.setEnabled(True)

        self._update_undo_action()

    # ------------------------------------------------------------------
    # Actions globales
    # ------------------------------------------------------------------

    def _show_all(self):
        """Affiche toutes les couches vecteur et retire les masquages d'entites de Box_cad.
        Les filtres poses par l'utilisateur sont conserves (restaures tels quels)."""
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        for layer in project.mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                node = root.findLayer(layer.id())
                if node:
                    node.setItemVisibilityChecked(True)
        for layer_id, orig in self._orig_subset.items():
            layer = project.mapLayer(layer_id)
            if layer and layer.subsetString() != orig:
                layer.setSubsetString(orig)
        self._hidden_features.clear()
        self._orig_subset.clear()
        self._drop_undo({'layer', 'feature', 'all_layers'})

    def _hide_all(self):
        """Masque toutes les couches vecteur (une seule action undo disponible)."""
        root = QgsProject.instance().layerTreeRoot()
        hidden_ids = []
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                node = root.findLayer(layer.id())
                if node and node.itemVisibilityChecked():
                    node.setItemVisibilityChecked(False)
                    hidden_ids.append(layer.id())
        if hidden_ids:
            self._set_undo(('all_layers', hidden_ids))

    # ------------------------------------------------------------------
    # Ordre des couches
    # ------------------------------------------------------------------

    def _reorder_layer_by_layer(self, layer, position):
        """Deplace la couche via les actions natives du panneau QGIS."""
        view = self.iface.layerTreeView()
        view.setCurrentLayer(layer)
        da = view.defaultActions()
        if position == 'top':
            action = da.actionMoveToTop(view)
        elif position == 'up':
            action = da.actionMoveUp(view)
        elif position == 'down':
            action = da.actionMoveDown(view)
        else:  # bottom
            action = da.actionMoveToBottom(view)
        action.trigger()
        action.deleteLater()  # actions creees a chaque appel, parentees a la vue

    # ------------------------------------------------------------------
    # Tri geometrique
    # ------------------------------------------------------------------

    @staticmethod
    def _reorder_group(group, layer_ids):
        """Replace les couches d'un groupe dans l'ordre layer_ids, en reutilisant
        les emplacements des couches (les sous-groupes ne bougent pas).
        A appeler avec le layerTreeRegistryBridge desactive (sinon removeChildNode
        retire aussi la couche du projet)."""
        children = group.children()
        slots = [i for i, n in enumerate(children) if isinstance(n, QgsLayerTreeLayer)]
        by_id = {children[i].layerId(): children[i] for i in slots}
        ordered = [by_id[lid] for lid in layer_ids if lid in by_id]
        ordered += [children[i] for i in slots if children[i] not in ordered]
        if [children[i] for i in slots] == ordered:
            return False
        clones = [n.clone() for n in ordered]
        for i in reversed(slots):
            group.removeChildNode(children[i])
        for idx, clone in zip(slots, clones):
            group.insertChildNode(idx, clone)
        return True

    def _sort_layers_by_geometry(self):
        """Trie les couches : Points > Lignes > Polygones, alpha dans chaque classe.
        Methode : bridge OFF global -> clone -> removeChildNode -> insertChildNode -> bridge ON.
        Le bridge doit rester OFF pour toute l'operation (pas de toggle par groupe).
        """
        def log(msg, level=Qgis.MessageLevel.Info):
            QgsMessageLog.logMessage(msg, "Box_cad", level)

        def sort_key(node):
            layer = node.layer()
            if not layer:
                return (4, '')
            name = layer.name().lower()
            if isinstance(layer, QgsVectorLayer):
                g = layer.geometryType()
                if g == QgsWkbTypes.PointGeometry:   return (1, name)
                if g == QgsWkbTypes.LineGeometry:    return (2, name)
                if g == QgsWkbTypes.PolygonGeometry: return (3, name)
            return (4, name)

        previous = []

        def sort_group(group):
            # Recursion dans les sous-groupes d'abord
            for node in group.children():
                if not isinstance(node, QgsLayerTreeLayer):
                    sort_group(node)

            layer_nodes = [n for n in group.children() if isinstance(n, QgsLayerTreeLayer)]
            if not layer_nodes:
                return
            before = [n.layerId() for n in layer_nodes]
            after = [n.layerId() for n in sorted(layer_nodes, key=sort_key)]
            if self._reorder_group(group, after):
                previous.append((group, before))
                log(f"'{group.name()}' : {len(after)} couches triees")

        bridge = QgsProject.instance().layerTreeRegistryBridge()
        bridge.setEnabled(False)
        try:
            log("=== TRI DEBUT ===")
            sort_group(QgsProject.instance().layerTreeRoot())
            log("=== TRI FIN ===")
        finally:
            bridge.setEnabled(True)
        if previous:
            self._set_undo(('sort', previous))

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------

    @staticmethod
    def _feature_key(layer, fid):
        """Valeur SQL identifiant l'entite dans le filtre, selon le fournisseur."""
        if layer.providerType() in ('postgres', 'mssql', 'oracle'):
            # le fid QGIS n'est pas forcement la valeur de la cle primaire
            pk_attrs = layer.primaryKeyAttributes()
            if not pk_attrs:
                return None
            value = layer.getFeature(fid).attribute(pk_attrs[0])
            return QgsExpression.quotedValue(value)
        return str(int(fid))

    def _rebuild_subset(self, layer):
        """Reconstruit le filtre : filtre d'origine de l'utilisateur ET entites masquees.
        Retourne False si le fournisseur refuse le filtre."""
        layer_id = layer.id()
        keys = self._hidden_features.get(layer_id, [])
        orig = self._orig_subset.get(layer_id, "")
        if not keys:
            return layer.setSubsetString(orig)

        keys_str = ", ".join(keys)
        provider = layer.providerType()

        if provider == 'ogr':
            # GeoPackage, Shapefile, GeoJSON... -> syntaxe OGR SQL
            clause = f"FID NOT IN ({keys_str})"
        elif provider in ('postgres', 'mssql', 'oracle'):
            pk_name = layer.fields()[layer.primaryKeyAttributes()[0]].name()
            clause = f'"{pk_name}" NOT IN ({keys_str})'
        elif provider == 'spatialite':
            clause = f"ROWID NOT IN ({keys_str})"
        else:
            # memory, wfs... -> expression QGIS
            clause = f"$id NOT IN ({keys_str})"

        return layer.setSubsetString(f"({orig}) AND ({clause})" if orig else clause)
