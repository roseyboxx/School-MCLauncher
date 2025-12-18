#!/usr/bin/env python3
"""
Minecraft Launcher (Tkinter GUI) with strict version JSON library resolution,
proxy downloads, auto detection of installed versions under ~/.minecraft,
and correct classpath building to avoid NoSuchMethodError from missing libs.
"""

import os
import json
import zipfile
import subprocess
import hashlib
import shutil
import requests
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import uuid

# ----------------------------
# CONFIG
# ----------------------------
MINECRAFT_DIR = os.path.expanduser("~/.minecraft")
LIBRARIES_DIR = os.path.join(MINECRAFT_DIR, "libraries")
VERSIONS_DIR = os.path.join(MINECRAFT_DIR, "versions")
ASSETS_DIR = os.path.join(MINECRAFT_DIR, "assets")
NATIVES_DIR = os.path.join(MINECRAFT_DIR, "natives")
PROXY_PREFIX = "https://download-prx.izziefinnegan.workers.dev/?url="
JAVA_CMD = "java"
XMS = "1G"
XMX = "2G"

os.makedirs(MINECRAFT_DIR, exist_ok=True)
os.makedirs(LIBRARIES_DIR, exist_ok=True)
os.makedirs(VERSIONS_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(NATIVES_DIR, exist_ok=True)

# ----------------------------
# HELPERS
# ----------------------------
def verify_hash(path, expected_hash):
    sha1 = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            sha1.update(chunk)
    return sha1.hexdigest() == expected_hash

def proxy_download(url, dest, expected_hash=None):
    if os.path.exists(dest):
        if expected_hash and verify_hash(dest, expected_hash):
            return
        elif not expected_hash:
            return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    full_url = PROXY_PREFIX + url
    r = requests.get(full_url, stream=True)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(1024 * 1024):
            f.write(chunk)

def extract_natives(jar, target):
    with zipfile.ZipFile(jar, "r") as z:
        for name in z.namelist():
            if any(name.endswith(ext) for ext in [".dll", ".so", ".dylib"]):
                z.extract(name, target)
                src = os.path.join(target, name)
                dst = os.path.join(target, os.path.basename(name))
                shutil.move(src, dst)

def ensure_version_installed(version_id):
    version_manifest = os.path.join(MINECRAFT_DIR, "version_manifest.json")
    if not os.path.exists(version_manifest):
        proxy_download("https://launchermeta.mojang.com/mc/game/version_manifest.json", version_manifest)
    with open(version_manifest, "r") as f:
        manifest = json.load(f)
    version_info = next((v for v in manifest["versions"] if v["id"] == version_id), None)
    if version_info is None:
        raise RuntimeError(f"Version {version_id} not found")
    version_folder = os.path.join(VERSIONS_DIR, version_id)
    os.makedirs(version_folder, exist_ok=True)
    version_json_file = os.path.join(version_folder, f"{version_id}.json")
    if not os.path.exists(version_json_file):
        proxy_download(version_info["url"], version_json_file)
    with open(version_json_file, "r") as f:
        version_data = json.load(f)

    # Download client JAR
    client_info = version_data["downloads"]["client"]
    client_jar = os.path.join(version_folder, f"{version_id}.jar")
    proxy_download(client_info["url"], client_jar, client_info.get("sha1"))

    # Download libraries and natives from JSON
    for lib in version_data["libraries"]:
        downloads = lib.get("downloads", {})
        artifact = downloads.get("artifact")
        if artifact:
            path = os.path.join(LIBRARIES_DIR, artifact["path"])
            proxy_download(artifact["url"], path, artifact.get("sha1"))
        classifiers = downloads.get("classifiers", {})
        for key, info in classifiers.items():
            if "natives" in key:
                nat_path = os.path.join(LIBRARIES_DIR, info["path"])
                proxy_download(info["url"], nat_path, info.get("sha1"))
                extract_natives(nat_path, NATIVES_DIR)

    # Download assets
    asset_index_info = version_data["assetIndex"]
    index_file = os.path.join(ASSETS_DIR, "indexes", f"{version_id}.json")
    os.makedirs(os.path.dirname(index_file), exist_ok=True)
    proxy_download(asset_index_info["url"], index_file, asset_index_info.get("sha1"))
    with open(index_file, "r") as f:
        assets_index = json.load(f)
    for obj, asset in assets_index["objects"].items():
        obj_file = os.path.join(ASSETS_DIR, "objects", asset["hash"][:2], asset["hash"])
        url = f"https://resources.download.minecraft.net/{asset['hash'][:2]}/{asset['hash']}"
        proxy_download(url, obj_file, asset["hash"])

    return version_data

def build_classpath(version_data, version_id):
    cp = []
    # version JAR
    cp.append(os.path.join(VERSIONS_DIR, version_id, f"{version_id}.jar"))

    # libraries from JSON
    for lib in version_data["libraries"]:
        downloads = lib.get("downloads", {})
        artifact = downloads.get("artifact")
        if artifact:
            cp.append(os.path.join(LIBRARIES_DIR, artifact["path"]))
    # return macOS/Linux separator
    return ":".join(cp)

def launch_game(profile):
    version_id = profile["version"]
    version_data = ensure_version_installed(version_id)
    classpath = build_classpath(version_data, version_id)

    cmd = [
        JAVA_CMD,
        "-XstartOnFirstThread",
        f"-Xms{profile.get('xms', XMS)}",
        f"-Xmx{profile.get('xmx', XMX)}",
        f"-Djava.library.path={NATIVES_DIR}",
        "-cp", classpath,
        version_data["mainClass"],
        "--username", profile.get("username", "Player"),
        "--version", version_id,
        "--gameDir", MINECRAFT_DIR,
        "--assetsDir", ASSETS_DIR,
        "--assetIndex", version_id,
        "--uuid", str(uuid.uuid4()),
        "--accessToken", "0",
        "--userType", "legacy"
    ]
    subprocess.run(cmd)

# Profiles

PROFILES_FILE = os.path.join(MINECRAFT_DIR, "launcher_profiles.json")

def load_profiles():
    if not os.path.exists(PROFILES_FILE):
        data = {"clientToken": str(uuid.uuid4()), "profiles": {}}
        with open(PROFILES_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return data["profiles"]
    with open(PROFILES_FILE, "r") as f:
        return json.load(f).get("profiles", {})

def save_profiles(profiles):
    data = {"clientToken": str(uuid.uuid4()), "profiles": profiles}
    with open(PROFILES_FILE, "w") as f:
        json.dump(data, f, indent=2)

def auto_detect_versions():
    profiles = load_profiles()
    for vid in os.listdir(VERSIONS_DIR):
        if os.path.isdir(os.path.join(VERSIONS_DIR, vid)):
            if f"Offline-{vid}" not in profiles:
                profiles[f"Offline-{vid}"] = {
                    "username": "Player",
                    "version": vid,
                    "xms": XMS,
                    "xmx": XMX
                }
    save_profiles(profiles)
    return profiles

# Tkinter UI

class GUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Minecraft Launcher")
        self.profiles = auto_detect_versions()
        self.selected = tk.StringVar(value=next(iter(self.profiles), ""))

        ttk.Label(root, text="Profile").grid(row=0, column=0)
        self.combo = ttk.Combobox(root, values=list(self.profiles), textvariable=self.selected)
        self.combo.grid(row=0, column=1)
        ttk.Button(root, text="Add", command=self.add).grid(row=0, column=2)
        ttk.Button(root, text="Play", command=self.play).grid(row=1, column=1)

    def add(self):
        user = simpledialog.askstring("Username", "Enter username:")
        ver = simpledialog.askstring("Version", "Enter version (e.g., 1.20.2):")
        if not user or not ver:
            return
        ensure_version_installed(ver)
        pname = f"{user}-{ver}"
        self.profiles[pname] = {"username": user, "version": ver, "xms": XMS, "xmx": XMX}
        save_profiles(self.profiles)
        self.combo["values"] = list(self.profiles)

    def play(self):
        prof = self.profiles.get(self.selected.get())
        if prof:
            threading.Thread(target=launch_game, args=(prof,), daemon=True).start()

def main():
    root = tk.Tk()
    app = GUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
