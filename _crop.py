"""Blow up a corner of the captured UI so the numbers on it are legible.

The full 1280x720 frame is downscaled to thumbnail size before I ever see it, so
the HP figures - the whole point of the capture - are unreadable. Cropping first
and scaling up keeps them.
"""
import sys

sys.path.insert(0, ".")

import cv2

from flyfou import vision


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "_ui.png"
    box = [int(v) for v in sys.argv[2].split(",")] if len(sys.argv) > 2 else [0, 0, 340, 110]
    scale = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    out = sys.argv[4] if len(sys.argv) > 4 else "_crop.png"

    image = cv2.imread(src, cv2.IMREAD_COLOR)
    if image is None:
        print(f"cannot read {src}")
        return 1
    x, y, w, h = box
    patch = image[y:y + h, x:x + w]
    patch = cv2.resize(patch, (patch.shape[1] * scale, patch.shape[0] * scale),
                       interpolation=cv2.INTER_NEAREST)
    vision.imwrite(out, patch)
    print(f"saved {out}  {patch.shape[1]}x{patch.shape[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
