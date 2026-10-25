# -*- coding: utf-8 -*-
"""Depose l'archive du plugin sur plugins.qgis.org, puis retire les versions
precedentes que la moderation n'a pas encore validees.

    python tools/publish.py                    # construit le ZIP, l'envoie, nettoie
    python tools/publish.py --zip chemin.zip   # envoie une archive existante
    python tools/publish.py --dry-run          # verifie tout, sans rien envoyer ni supprimer
    python tools/publish.py --keep-unapproved  # envoie sans retirer les anciennes versions
    python tools/publish.py --clean-only       # nettoie seulement (rien n'est envoye)

Le mot de passe n'est jamais passe en argument : il serait visible dans
l'historique du shell et dans la liste des processus. Il est lu dans la
variable d'environnement OSGEO_PASSWORD si elle existe (secret GitHub dans le
workflow), sinon demande au clavier sans echo.

Chaque version deposee attend la validation d'un moderateur. Tant qu'elle ne
l'est pas, une version plus recente la rend inutile : apres un envoi accepte,
les versions plus anciennes publiees par un tag vX.Y.Z et encore « non
validees » sont supprimees du site. Une version validee n'est jamais touchee.
L'API du site ne permet pas de supprimer une version : on passe par la page de
confirmation du site, apres connexion avec le meme identifiant OSGeo. Un echec
du nettoyage ne remet pas en cause l'envoi : il est signale avec l'adresse a
ouvrir pour supprimer a la main.

Rappel : une version refusee par les controles du depot ne peut pas etre
renvoyee sous le meme numero. Lancer le controle de prepublication avant.
"""

import argparse
import getpass
import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xmlrpc.client
from urllib.parse import quote

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "Box_cad"
SITE = "https://plugins.qgis.org"
ENDPOINT = "https://{0}:{1}@plugins.qgis.org/plugins/RPC2/"
USER_AGENT = "Box_cad-publish/1.0 (+https://github.com/Cartoyoyo/Box_Cad)"
TAG = re.compile(r"^v(\d+(?:\.\d+)+)$")
CSRF = re.compile(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"')


def read_version():
    path = os.path.join(PLUGIN_DIR, "metadata.txt")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("version="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("Version absente de metadata.txt")


def version_key(text):
    return tuple(int(part) for part in text.split("."))


def _scrub(text, secret):
    """Efface le secret (brut et encode) d'un message avant de l'afficher."""
    for value in (secret, quote(secret or "", safe="")):
        if value and value in text:
            text = text.replace(value, "***")
    return text


def credentials():
    user = os.environ.get("OSGEO_USER")
    if not user:
        user = input("Identifiant OSGeo : ").strip()
    password = os.environ.get("OSGEO_PASSWORD")
    if not password:
        password = getpass.getpass("Mot de passe OSGeo (saisie masquee) : ")
    if not user or not password:
        raise SystemExit("Identifiants incomplets.")
    return user, password


def upload(zip_path, user, password):
    with open(zip_path, "rb") as handle:
        payload = handle.read()
    # encodes : un caractere special (@, :, /...) casserait l'adresse du service
    server = xmlrpc.client.ServerProxy(ENDPOINT.format(quote(user, safe=""), quote(password, safe="")))
    return server.plugin.upload(xmlrpc.client.Binary(payload))


# ----------------------------------------------------------------------
# Nettoyage des versions non validees
# ----------------------------------------------------------------------

def tagged_versions():
    """Versions publiees par un tag vX.Y.Z du depot, lues dans .git (refs/tags et
    packed-refs) : le workflow recupere tous les tags (fetch-depth: 0)."""
    git = os.path.join(PLUGIN_DIR, ".git")
    names = set()
    tags_dir = os.path.join(git, "refs", "tags")
    if os.path.isdir(tags_dir):
        names.update(os.listdir(tags_dir))
    packed = os.path.join(git, "packed-refs")
    if os.path.exists(packed):
        with open(packed, encoding="utf-8") as handle:
            for line in handle:
                ref = line.strip().split(" ")[-1]
                if ref.startswith("refs/tags/"):
                    names.add(ref[len("refs/tags/"):])
    return [m.group(1) for m in (TAG.match(name) for name in names) if m]


class Site:
    """Session sur plugins.qgis.org (cookies, jeton CSRF de Django)."""

    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, path, fields=None, referer=None):
        headers = {"User-Agent": USER_AGENT}
        data = None
        if fields is not None:
            data = urllib.parse.urlencode(fields).encode("utf-8")
            headers["Referer"] = SITE + (referer or path)    # exige par Django en HTTPS
        request = urllib.request.Request(SITE + path, data=data, headers=headers)
        with self.opener.open(request, timeout=60) as response:     # adresse https fixe (SITE)
            return response.geturl(), response.read().decode("utf-8", "replace")

    def get(self, path):
        """(url finale, html), ou (None, None) si la page n'existe pas (404)."""
        try:
            return self._open(path)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None, None
            raise

    def post(self, path, fields, referer=None):
        return self._open(path, fields, referer)

    def approval(self, version):
        """'yes', 'no', ou None si la version n'est pas (ou plus) sur le site."""
        _, html = self.get("/plugins/{0}/version/{1}/".format(PACKAGE, version))
        if html is None:
            return None
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        match = re.search(r"\bApproved (yes|no)\b", text)
        return match.group(1) if match else "?"

    def login(self, user, password):
        _, html = self.get("/accounts/login/")
        token = CSRF.search(html or "")
        if not token:
            raise RuntimeError("formulaire de connexion introuvable")
        url, html = self.post("/accounts/login/", {
            "csrfmiddlewaretoken": token.group(1), "login": user, "password": password})
        if urllib.parse.urlparse(url).path.startswith("/accounts/login") or 'name="password"' in html:
            raise RuntimeError("connexion refusee")

    def delete(self, version):
        path = "/plugins/{0}/version/{1}/delete/".format(PACKAGE, version)
        _, html = self.get(path)
        token = CSRF.search(html or "")
        if not token:
            raise RuntimeError("page de confirmation introuvable")
        self.post(path, {"csrfmiddlewaretoken": token.group(1), "delete_confirm": "1"}, referer=path)


def clean_unapproved(current, user, password, dry_run=False):
    """Supprime les versions taguees plus anciennes que `current` et non validees."""
    site = Site()
    older = sorted((v for v in tagged_versions() if version_key(v) < version_key(current)), key=version_key)
    targets = []
    for version in older:
        state = site.approval(version)
        label = {"yes": "validee, conservee", "no": "non validee", None: "absente du site", "?": "etat illisible"}
        print("  {0} : {1}".format(version, label.get(state, state)))
        if state == "no":
            targets.append(version)
    if not targets:
        print("Aucune ancienne version a retirer.")
        return 0
    if dry_run:
        print("Essai a blanc : seraient supprimees {0}.".format(", ".join(targets)))
        return 0
    try:
        site.login(user, password)
    except (RuntimeError, urllib.error.URLError, OSError) as error:
        print("ATTENTION : nettoyage impossible ({0}). A supprimer a la main : {1}".format(
            _scrub(str(error), password),
            ", ".join("{0}/plugins/{1}/version/{2}/".format(SITE, PACKAGE, v) for v in targets)))
        return 1
    failed = []
    for version in targets:
        try:
            site.delete(version)
        except (RuntimeError, urllib.error.URLError, OSError) as error:
            print("  {0} : echec de la suppression ({1})".format(version, _scrub(str(error), password)))
            failed.append(version)
            continue
        if site.approval(version) is None:
            print("  {0} : supprimee".format(version))
        else:
            print("  {0} : toujours presente apres la suppression".format(version))
            failed.append(version)
    if failed:
        print("ATTENTION : a supprimer a la main : {0}".format(
            ", ".join("{0}/plugins/{1}/version/{2}/".format(SITE, PACKAGE, v) for v in failed)))
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", help="archive a envoyer ; construite si omise")
    parser.add_argument("--dry-run", action="store_true",
                        help="tout verifier sans rien envoyer ni supprimer")
    parser.add_argument("--keep-unapproved", action="store_true",
                        help="ne pas retirer les anciennes versions non validees")
    parser.add_argument("--clean-only", action="store_true",
                        help="retirer les anciennes versions non validees, sans rien envoyer")
    arguments = parser.parse_args()

    version = read_version()
    if arguments.clean_only:
        print("Nettoyage des versions anterieures a {0} :".format(version))
        if arguments.dry_run:
            return clean_unapproved(version, None, None, dry_run=True)
        user, password = credentials()
        return clean_unapproved(version, user, password)

    zip_path = arguments.zip
    if not zip_path:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import build_zip
        zip_path = build_zip.build(os.path.dirname(PLUGIN_DIR))

    if not os.path.exists(zip_path):
        raise SystemExit("Archive introuvable : {0}".format(zip_path))
    print("Plugin  : {0} {1}".format(PACKAGE, version))
    print("Archive : {0} ({1:.0f} Ko)".format(
        zip_path, os.path.getsize(zip_path) / 1024))

    if arguments.dry_run:
        print("Essai a blanc : rien n'a ete envoye.")
        if not arguments.keep_unapproved:
            print("Anciennes versions :")
            clean_unapproved(version, None, None, dry_run=True)
        return 0

    user, password = credentials()
    print("Envoi sur plugins.qgis.org en tant que {0}...".format(user))
    try:
        result = upload(zip_path, user, password)
    except xmlrpc.client.ProtocolError as error:
        # 401 : identifiants refuses. 403 : compte sans droit sur ce plugin.
        raise SystemExit(
            "Envoi refuse ({0} {1}). Verifiez vos identifiants sur "
            "https://plugins.qgis.org".format(error.errcode, error.errmsg)
        )
    except xmlrpc.client.Fault as error:
        raise SystemExit("Le depot a refuse l'archive : {0}".format(
            _scrub(error.faultString, password)))
    except Exception as error:
        # Le mot de passe fait partie de l'URL du service : le message d'une
        # exception inattendue le contiendrait en clair, et se retrouverait
        # dans un journal. On l'efface avant d'ecrire quoi que ce soit.
        raise SystemExit("Envoi impossible : {0}".format(
            _scrub("{0}: {1}".format(type(error).__name__, error), password)))
    print("Envoi accepte : {0}".format(result))
    print("La version attend la validation d'un moderateur.")

    if not arguments.keep_unapproved:
        print("Anciennes versions non validees :")
        clean_unapproved(version, user, password)   # un echec n'annule pas l'envoi
    return 0


if __name__ == "__main__":
    sys.exit(main())
