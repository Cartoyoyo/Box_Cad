# Changelog

## 1.1.0 — 2026-10-16

### Ajouts
- Barre **Box_cad Dessin** (et menu *Extensions → Box_cad → Dessin*) : ligne, polyligne, rectangle, rectangle orienté, carré, cercle.
- Saisie dynamique façon AutoCAD : seules les valeurs suivent le curseur (longueur, angle, largeur, hauteur, côté, rayon) ; l'étape, le nom des champs (champ actif en gras), l'accrochage et les touches s'affichent dans la barre d'état QGIS ; Tab change de champ, Entrée valide, Échap annule, U ou Retour arrière annule le dernier point.
- Annulation multi-niveaux : jusqu'à 50 actions, infobulle de la prochaine action annulée ; « Tout afficher » ne retire plus que les masquages de la pile.
- Épaisseur des traits par défaut : 0,25 mm.
- Barre Dessin regroupée en 3 boutons à menu (Tracer, Modifier, Annoter, titre en tête de menu) qui affichent le dernier outil utilisé.
- Barre principale : les 4 boutons d'ordre des couches et le tri par géométrie regroupés dans un bouton à menu « Ordre des couches » (9 boutons au lieu de 13).
- Pastille dans la barre Dessin : couleur, épaisseur et couche qui recevra le dessin (brouillon ou couche active en édition) ; clic = propriétés courantes.
- Déplacer, Copier (copies multiples), Miroir (axe en 2 points) et Échelle (facteur ou référence) façon AutoCAD, sur les dessins (Annuler) et la couche active en édition (Ctrl+Z) ; copie sans conflit de clé primaire.
- Textes désignables en cliquant sur leurs lettres ; Rotation (incline le texte), Échelle (taille), Miroir (reste lisible), Déplacer, Copier, Gomme.
- Polygone (un clic par sommet, surface en direct), Texte (taille en points, angle), Propriétés des dessins (couleur, épaisseur, trait, remplissage, opacité) et propriétés courantes.
- Accrochage objet façon AutoCAD (F3, actif par défaut, modes à cocher) : extrémité, milieu, centre, quadrant, opposé, intersection, perpendiculaire, proche ; cercles reconnus exactement sur les dessins Box_cad ; index QGIS en arrière-plan pour les autres couches.
- Cote (cotation alignée) : deux points puis position ; flèches, lignes de rappel et étiquette de longueur, brouillon « Box_cad dessin – cotes ».
- Angle (cotation angulaire) : clic sur deux lignes, ou Entrée puis sommet et un point sur chaque côté ; la position de l'arc choisit l'angle, son supplémentaire ou l'angle rentrant ; arc fléché, valeur en degrés à 1 décimale, rappels si besoin, brouillon « Box_cad dessin – angles ».
- Désignation : annotations (textes, cotes, angles) prioritaires et désignables par leur texte ; formes vides désignées par leur contour ou leur centre.
- Menu Extensions → Box_cad unique, rangé comme CanaPlan : Annuler, puis sous-menus Objets, Affichage, Ordre des couches, Tracer, Modifier, Annoter, Modes de dessin, et À propos ; icône Box_cad.
- Cotes associatives : Déplacer, Rotation, Miroir et Échelle d'une forme entraînent ses cotes et angles (longueur recalculée), annulés en une fois ; Déplacer une cote ou un angle ne bouge que la ligne de cote ou l'arc, les rappels s'adaptent.
- Remplissage à la demande : bouton à bascule, formes vides par défaut (champ `rempli`, style piloté par ce champ).
- Angle relatif pour Ligne et Polyligne : dès le 2e segment, l'angle tapé est une déviation (0 = tout droit) ; ortho relatif ; touche A pour l'angle absolu.
- Rotation façon AutoCAD : objet, point de base (Entrée : centre de l'objet), angle à la souris ou tapé (anti-horaire), aimanté par l'ortho ; formes dessinées (Annuler) ou couche active en édition (Ctrl+Z).
- Gomme : clic ou lasso, n'efface que les formes des couches brouillon « Box_cad dessin » visibles ; surbrillance de la forme visée ; annulable.
- Accrochage QGIS, mode ortho (F8), cotes et surface en direct.
- Dessin dans la couche active si elle est en édition et du bon type (formulaire d'attributs QGIS, Ctrl+Z), sinon dans une couche brouillon en mémoire « Box_cad dessin » ; le bouton Annuler retire la dernière forme du brouillon.
- Fenêtre « À propos » : courte présentation du plugin à la place de la liste défilante des outils ; elle tient sans défilement sur un écran de 720 px.

## 1.0.0 — 2026-10-16

Première version publique.

### Ajouts
- Logo du plugin et jeu de 12 icônes harmonisé (couleurs SIG : vert afficher, gris masquer, rouge supprimer, violet ordre, bleu localiser).
- Menu *Extensions → Box_cad* reprenant tous les outils.
- Fenêtre « À propos » avec aide-mémoire des outils.
- Annulation du tri par géométrie.

### Corrections
- Les boutons restent synchronisés avec l'outil actif quand QGIS change d'outil.
- Supprimer : le clic vise l'objet du dessus (et non plus la seule couche active), couches en lecture seule ignorées, lasso qui se recoupe accepté.
- Supprimer : plus d'enregistrement forcé d'une édition en cours ; échec d'enregistrement annulé et signalé ; l'annulation ne recrée plus d'entités non supprimées.
- Masquer entité : le filtre existant de la couche est conservé ; clé primaire réelle sur les bases de données.
- Tout afficher : ne retire plus que les masquages faits par Box_cad.
- Tri par géométrie : les sous-groupes restent à leur place.
- Rechargement du plugin et changement de projet : plus d'actions ni d'historique orphelins.
- Compatibilité Qt6 (QGIS 4) : enums sous forme complète.
