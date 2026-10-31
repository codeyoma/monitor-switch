"""Package the supplied PNG as a macOS icon using Apple's native tools."""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent


def main():
    if sys.platform!='darwin':raise SystemExit('Build the macOS icon on a Mac.')
    source=ROOT/'assets/app/MonitorSwitch.png'
    iconset=ROOT/'build/MonitorSwitch.iconset'
    iconset.mkdir(parents=True,exist_ok=True)
    for points in (16,32,128,256,512):
        for scale in (1,2):
            name=f'icon_{points}x{points}'+('@2x' if scale==2 else '')+'.png'
            subprocess.run(['/usr/bin/sips','-z',str(points*scale),str(points*scale),
                            str(source),'--out',str(iconset/name)],check=True,capture_output=True)
    destination=ROOT/'assets/app/MonitorSwitch.icns'
    subprocess.run(['/usr/bin/iconutil','-c','icns','-o',str(destination),str(iconset)],check=True)
    print(destination)


if __name__=='__main__':main()
