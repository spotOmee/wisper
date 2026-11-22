#!/usr/bin/env python3
"""
wisper APK Master Build & Packaging Automation
Repository: https://github.com/spotOmee/wisper

Builds or repackages wisper APK with custom icons, branding, and signing:
1. If stock Gboard APK exists at input/gboard.apk (or via INPUT_APK), executes full
   Morphe patcher pipeline and sanitize_apk_resources.py.
2. If input APK is not provided, downloads upstream release base, injects the new
   wisper adaptive and legacy launcher icons across all densities, zipaligns,
   and signs with apksigner.
3. Places finalized, signed APK into output/ and final-apk/ for CI artifact/release delivery.
"""

import os
import sys
import glob
import shutil
import zipfile
import subprocess
import urllib.request

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
FINAL_DIR = os.path.join(BASE_DIR, "final-apk")
ICONS_ROOT = os.path.join(BASE_DIR, "pixelboard-patches/patches/src/main/resources/icons")
FALLBACK_BASE_URL = "https://github.com/Akshayykadam/PixelBoard/releases/download/1.3.6/PixelBoard-1.3.6.apk"

# Canonical obfuscated WebP mappings in PixelBoard/Gboard 18.6.3
ICON_REPLACEMENTS = {
    # Adaptive backgrounds (0x7f110001, solid #E4E3DF)
    "res/cuu.webp": f"{ICONS_ROOT}/mipmap-mdpi/mipmap_0x7f110001.webp",
    "res/Tcw.webp": f"{ICONS_ROOT}/mipmap-hdpi/mipmap_0x7f110001.webp",
    "res/3b_.webp": f"{ICONS_ROOT}/mipmap-xhdpi/mipmap_0x7f110001.webp",
    "res/JN5.webp": f"{ICONS_ROOT}/mipmap-xxhdpi/mipmap_0x7f110001.webp",
    "res/uRn.webp": f"{ICONS_ROOT}/mipmap-xxxhdpi/mipmap_0x7f110001.webp",
    # Adaptive foregrounds (0x7f110002, centered wisper logo)
    "res/eRH.webp": f"{ICONS_ROOT}/mipmap-mdpi/mipmap_0x7f110002.webp",
    "res/FKo.webp": f"{ICONS_ROOT}/mipmap-hdpi/mipmap_0x7f110002.webp",
    "res/EJy.webp": f"{ICONS_ROOT}/mipmap-xhdpi/mipmap_0x7f110002.webp",
    "res/ZCC.webp": f"{ICONS_ROOT}/mipmap-xxhdpi/mipmap_0x7f110002.webp",
    "res/aQv.webp": f"{ICONS_ROOT}/mipmap-xxxhdpi/mipmap_0x7f110002.webp",
}


def find_sdk_tool(tool_name):
    for env_var in ['ANDROID_HOME', 'ANDROID_SDK_ROOT']:
        val = os.environ.get(env_var)
        if val:
            candidates = sorted(glob.glob(os.path.join(val, 'build-tools', '*', tool_name)), reverse=True)
            if candidates:
                return candidates[0]
    default_mac = os.path.expanduser('~/Library/Android/sdk/build-tools')
    candidates = sorted(glob.glob(os.path.join(default_mac, '*', tool_name)), reverse=True)
    if candidates:
        return candidates[0]
    return shutil.which(tool_name)


def ensure_keystore():
    env_ks = os.environ.get("ANDROID_KEYSTORE_PATH")
    if env_ks and os.path.isfile(env_ks):
        return {
            "path": env_ks,
            "pass": os.environ.get("ANDROID_KEYSTORE_PASS", "pixelboard"),
            "alias": os.environ.get("ANDROID_KEY_ALIAS", "pixelboard"),
            "keypass": os.environ.get("ANDROID_KEY_PASS", "pixelboard"),
        }

    default_ks = os.path.join(BASE_DIR, "tools/patcher-data/pixelboard.keystore")
    os.makedirs(os.path.dirname(default_ks), exist_ok=True)
    if not os.path.isfile(default_ks):
        print("🔑 Generating wisper build keystore...")
        cmd = [
            "keytool", "-genkeypair",
            "-keystore", default_ks,
            "-alias", "pixelboard",
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-storepass", "pixelboard",
            "-keypass", "pixelboard",
            "-dname", "CN=wisper, OU=wisper, O=wisper, L=Unknown, ST=Unknown, C=US"
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {
        "path": default_ks,
        "pass": "pixelboard",
        "alias": "pixelboard",
        "keypass": "pixelboard",
    }


def build_from_stock_or_base():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(FINAL_DIR, exist_ok=True)

    input_apk = os.environ.get("INPUT_APK") or os.path.join(BASE_DIR, "input/gboard.apk")
    base_url = os.environ.get("BASE_APK_URL")

    # If base_url provided and input_apk not present, download it
    if base_url and not os.path.isfile(input_apk):
        print(f"📥 Downloading base APK from specified URL: {base_url}")
        os.makedirs(os.path.dirname(input_apk), exist_ok=True)
        urllib.request.urlretrieve(base_url, input_apk)

    out_final = os.path.join(OUTPUT_DIR, "wisper.apk")

    if os.path.isfile(input_apk):
        print(f"🔨 Stock Gboard APK detected at {input_apk}. Running ./build.sh...")
        res = subprocess.run(["./build.sh", input_apk], cwd=BASE_DIR)
        if res.returncode != 0:
            print("❌ ./build.sh failed.")
            sys.exit(res.returncode)
    else:
        print("📦 No stock Gboard input APK found. Packaging wisper from upstream base with new launcher icons...")
        temp_base = "/tmp/wisper_base_download.apk"
        if not os.path.exists(temp_base) or os.path.getsize(temp_base) < 50_000_000:
            print(f"📥 Fetching baseline package from: {FALLBACK_BASE_URL}")
            urllib.request.urlretrieve(FALLBACK_BASE_URL, temp_base)

        temp_injected = "/tmp/wisper_injected.apk"
        temp_aligned = "/tmp/wisper_aligned.apk"

        print("🎨 Injecting updated wisper launcher icon assets into APK...")
        with zipfile.ZipFile(temp_base, "r") as zin, zipfile.ZipFile(temp_injected, "w") as zout:
            for item in zin.infolist():
                # Strip old signatures so re-signing succeeds cleanly
                if item.filename.startswith("META-INF/") and any(item.filename.endswith(ext) for ext in [".SF", ".RSA", ".DSA", ".EC", ".MF"]):
                    continue
                if item.filename in ICON_REPLACEMENTS and os.path.exists(ICON_REPLACEMENTS[item.filename]):
                    with open(ICON_REPLACEMENTS[item.filename], "rb") as f:
                        zout.writestr(item, f.read())
                else:
                    zout.writestr(item, zin.read(item.filename))

        zipalign = find_sdk_tool("zipalign")
        apksigner = find_sdk_tool("apksigner")
        ks_info = ensure_keystore()

        if zipalign:
            print("📐 Aligning APK with zipalign...")
            subprocess.run([zipalign, "-f", "-p", "4", temp_injected, temp_aligned], check=True)
        else:
            print("⚠️ Warning: zipalign not found, skipping alignment.")
            shutil.copyfile(temp_injected, temp_aligned)

        if apksigner:
            print("✍️ Signing APK with apksigner...")
            sign_cmd = [
                apksigner, "sign",
                "--ks", ks_info["path"],
                "--ks-pass", f"pass:{ks_info['pass']}",
                "--key-pass", f"pass:{ks_info['keypass']}",
                "--ks-key-alias", ks_info["alias"],
                temp_aligned
            ]
            subprocess.run(sign_cmd, check=True)
            verify_cmd = [apksigner, "verify", temp_aligned]
            subprocess.run(verify_cmd, check=True)
            print("✅ APK signature verified successfully!")
        else:
            print("⚠️ Warning: apksigner not found, using jarsigner...")
            jarsigner = shutil.which("jarsigner")
            if jarsigner:
                subprocess.run([
                    jarsigner,
                    "-keystore", ks_info["path"],
                    "-storepass", ks_info["pass"],
                    "-keypass", ks_info["keypass"],
                    temp_aligned,
                    ks_info["alias"]
                ], check=True)

        shutil.copyfile(temp_aligned, out_final)
        # Cleanup temp build files
        for p in [temp_injected, temp_aligned, temp_base]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    # Ensure versioned copy exists
    patch_ver = "1.3.6"
    versioned_apk = os.path.join(OUTPUT_DIR, f"wisper-{patch_ver}.apk")
    if os.path.isfile(out_final):
        shutil.copyfile(out_final, versioned_apk)
        shutil.copyfile(out_final, os.path.join(FINAL_DIR, f"wisper-{patch_ver}.apk"))
        shutil.copyfile(out_final, os.path.join(FINAL_DIR, "wisper.apk"))
        sz = os.path.getsize(out_final) / (1024 * 1024)
        print(f"🎉 wisper APK built successfully: {out_final} ({sz:.2f} MB)")
        print(f"📦 Staged in {FINAL_DIR}/ for artifact & Telegram release delivery.")
    else:
        print("❌ Error: Expected output APK not found at output/wisper.apk")
        sys.exit(1)


if __name__ == "__main__":
    build_from_stock_or_base()
