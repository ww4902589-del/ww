# Binary asset manifest

The supplied Wallpaper Engine source package contains the following binary files. They are required for runtime but are not text source code.

| Repository path | Size | SHA-256 |
|---|---:|---|
| `assets/master-keyframe.png` | 5,728,488 bytes | `d782f341a7b6801b78d902686e063135e7a48a3c10700b5ef97c3ea66221d268` |
| `assets/motion-blink.png` | 5,739,684 bytes | `92e547d91598f86bf6e7121bae58ae91722c59f0903537fa0ff531cbfa61ce8d` |
| `assets/motion-reach.png` | 5,735,215 bytes | `ab2e5300b44720c5ea34d512c53036cf6388b457633e268a5fe8977b1a323010` |
| `preview.jpg` | 57,537 bytes | Not recorded in the original handoff check. |

## Restore procedure

Copy the files from the supplied archive directory:

```text
壁纸/动态工程/liv_jimeng_dream_journey/assets/
壁纸/动态工程/liv_jimeng_dream_journey/preview.jpg
```

into this project, preserving the paths above. Then verify the three PNG checksums before visual testing.

The v6 blink milestone must not keep `motion-blink.png` as a complete face overlay. It may be used only as a temporary reference for producing local open, half-closed and closed eye assets.
