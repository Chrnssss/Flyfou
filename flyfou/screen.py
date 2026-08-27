"""Turning a place in the world into a place on the screen.

This exists because writing to the client turned out to be useless. Memory
writes stick and the client draws them, but no other client ever sees them: two
witnesses standing beside the character watched it not move while its own
process insisted it had. This client sends packets in response to input, not in
response to its own memory changing. Memory is a mirror, not a lever.

So the bot reads the world out of memory and acts through the mouse, and the one
thing that needs is a way to turn a monster's world position into the pixel it
is drawn at.

Searching for the client's own view-projection matrix was a dead end for a
reason worth writing down: the matrices are real and they are found easily
enough, but they live in heap the renderer allocates and frees every frame, so
an address that held one a moment ago holds a denormal now. Nothing stable
points at them.

Building the matrix is better, because the two things it needs are stable. The
camera's eye and the point it looks at sit at fixed addresses in the client's
own image - the look-at target is the character's position exactly, which is how
they were recognised. From those, the view matrix is the one Direct3D would
build, and the projection needs only a field of view.

The field of view was fitted by eye against entities whose world positions were
known: at 47 degrees a character sixty pixels from the centre of the screen
lands within three pixels of where the client drew it. That is close enough to
click on something the size of a monster, and it is a single number to revisit
if a future build disagrees.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

from .mem.process import Module, Process

#: The camera, relative to the module. Found by looking for a triple that held
#: the character's position exactly, and another twenty units above it.
EYE_AT, TARGET_AT = 0xE0EDF0, 0xE0F1F0

#: Fitted against the client's own drawing. See the note above.
FIELD_OF_VIEW = 47.0

#: Near and far planes. Only their ratio matters for where things land.
NEAR, FAR = 0.1, 5000.0

#: How far up an entity's own position to aim, in world units.
#:
#: Zero, because measurement said so. Aiming 1.4 units up put the click thirty
#: pixels above a monster's head - onto its nameplate and the grass behind it -
#: which means an entity's stored position is not its feet but somewhere around
#: the middle of the sprite. That is already the part worth clicking, so nothing
#: needs adding to it.
BODY = 0.0


@dataclass
class Camera:
    """Where the client is looking from, and at what."""

    eye: Tuple[float, float, float]
    target: Tuple[float, float, float]

    @property
    def usable(self) -> bool:
        """False on a loading screen, when both read as the origin."""
        if not self.eye or not self.target:
            return False
        span = math.dist(self.eye, self.target)
        return 0.5 < span < 5000.0

    @property
    def distance(self) -> float:
        return math.dist(self.eye, self.target)


def read_camera(process: Process, module: Module) -> Optional[Camera]:
    eye = process.vec3(module.base + EYE_AT)
    target = process.vec3(module.base + TARGET_AT)
    if eye is None or target is None:
        return None
    camera = Camera(eye, target)
    return camera if camera.usable else None


def _look_at(eye, target, up=(0.0, 1.0, 0.0)):
    """The view matrix Direct3D would build, for row vectors."""
    eye, target, up = np.array(eye), np.array(target), np.array(up)
    forward = target - eye
    length = float(np.linalg.norm(forward))
    if length < 1e-6:
        return None
    forward = forward / length
    side = np.cross(up, forward)
    if float(np.linalg.norm(side)) < 1e-6:
        return None
    side = side / np.linalg.norm(side)
    upward = np.cross(forward, side)

    view = np.eye(4)
    view[:3, 0], view[:3, 1], view[:3, 2] = side, upward, forward
    view[3, 0] = -float(np.dot(side, eye))
    view[3, 1] = -float(np.dot(upward, eye))
    view[3, 2] = -float(np.dot(forward, eye))
    return view


def _perspective(fov_degrees: float, aspect: float):
    high = 1.0 / math.tan(math.radians(fov_degrees) / 2.0)
    matrix = np.zeros((4, 4))
    matrix[0, 0] = high / aspect
    matrix[1, 1] = high
    matrix[2, 2] = FAR / (FAR - NEAR)
    matrix[2, 3] = 1.0
    matrix[3, 2] = -NEAR * FAR / (FAR - NEAR)
    return matrix


class Projector:
    """One frame's worth of "where would that be on screen".

    Built fresh each tick rather than kept: the camera moves whenever the
    character does, and a projection from a moment ago aims at where something
    used to be. Building it costs two reads and a four by four multiply.
    """

    def __init__(self, camera: Camera, width: int, height: int,
                 fov: float = FIELD_OF_VIEW):
        self.camera = camera
        self.width, self.height = width, height
        view = _look_at(camera.eye, camera.target)
        self.matrix = (None if view is None or not width or not height
                       else view @ _perspective(fov, width / float(height)))

    @property
    def usable(self) -> bool:
        return self.matrix is not None

    def at(self, spot: Sequence[float],
           lift: float = BODY) -> Optional[Tuple[int, int]]:
        """The pixel a world position is drawn at, or None if it is behind us.

        `lift` aims a little above the given point because an entity's position
        is where it stands, and the thing worth clicking is its body.
        """
        if self.matrix is None:
            return None
        clip = np.array([spot[0], spot[1] + lift, spot[2], 1.0]) @ self.matrix
        if clip[3] <= 0.01:
            return None
        x = (clip[0] / clip[3] * 0.5 + 0.5) * self.width
        y = (0.5 - clip[1] / clip[3] * 0.5) * self.height
        if not (np.isfinite(x) and np.isfinite(y)):
            return None
        return int(round(x)), int(round(y))

    def on_screen(self, spot: Sequence[float], margin: int = 4,
                  lift: float = BODY) -> Optional[Tuple[int, int]]:
        """The pixel, but only if it is somewhere a click could actually land."""
        point = self.at(spot, lift)
        if point is None:
            return None
        if not (margin <= point[0] < self.width - margin
                and margin <= point[1] < self.height - margin):
            return None
        return point


def project_world(process: Process, module: Module, width: int,
                  height: int) -> Optional[Projector]:
    """A projector for right now, or None if the client is between scenes."""
    camera = read_camera(process, module)
    if camera is None:
        return None
    projector = Projector(camera, width, height)
    return projector if projector.usable else None
